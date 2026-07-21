"""Live guest controls used by executed Catsnail tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from .recording import StepRecorder
from ..qemu.vnc import Frame, VncClient

if TYPE_CHECKING:
    from ..graph.api import Network
    from ..qemu.runner import QemuProcess


class GuestControlError(RuntimeError):
    """Raised when a guest GUI or its local control endpoint is unavailable."""


class Keyboard:
    """Keyboard input directed at the guest's active VNC window."""

    def __init__(
        self, vnc: VncClient, after_step: Callable[[str], Awaitable[None]]
    ) -> None:
        self._vnc = vnc
        self._after_step = after_step

    async def type(self, value: str) -> None:
        await self._vnc.type_text(value)
        await self._after_step("keyboard-type")

    async def press(self, key: str) -> None:
        await self._vnc.press(_keysym(key))
        await self._after_step(f"key-{key}")

    async def shortcut(self, *keys: str) -> None:
        if len(keys) < 2:
            raise ValueError("a keyboard shortcut needs at least two keys")
        modifiers, key = keys[:-1], keys[-1]
        for modifier in modifiers:
            await self._vnc.key(_keysym(modifier), True)
        try:
            await self._vnc.press(_keysym(key))
        finally:
            for modifier in reversed(modifiers):
                await self._vnc.key(_keysym(modifier), False)
        await self._after_step("shortcut-" + "-".join(keys))


class Screen:
    """Screenshot, region assertion, and pointer controls for a guest."""

    def __init__(
        self,
        vnc: VncClient,
        debug_directory: Path,
        release_directory: Path,
        recorder: StepRecorder | None,
    ) -> None:
        self._vnc = vnc
        self._debug_directory = debug_directory
        self._release_directory = release_directory
        self._recorder = recorder
        self._capture_index = 0

    def set_release_directory(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._release_directory = directory

    async def capture(self, label: str = "screen") -> Frame:
        frame = await self._vnc.frame()
        self._capture_index += 1
        frame.write_png(
            self._release_directory / f"{self._capture_index:02d}-{label}.png"
        )
        self._record(frame, label)
        return frame

    async def capture_result(self, label: str) -> Frame:
        """Save a framework diagnostic screenshot outside the release output."""

        frame = await self._vnc.frame()
        self._save(frame, label)
        self._record(frame, label)
        return frame

    async def click(self, x: int, y: int) -> None:
        await self._vnc.click(x, y)
        await self.record_step("screen-click")

    async def record_step(self, label: str) -> None:
        if self._recorder is None:
            return
        self._record(await self._vnc.frame(), label)

    async def wait_for_change(self, baseline: Frame, *, timeout: float) -> Frame:
        """Wait for a materially different frame, retaining diagnostics on timeout."""

        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            current = await self._vnc.frame(timeout=min(10, max(1, timeout)))
            if (
                current.changed_pixels(baseline)
                > (current.width * current.height) // 20
            ):
                self._record(current, "screen-change")
                return current
            if asyncio.get_running_loop().time() >= deadline:
                path = self._save(current, "screen-change-timeout")
                raise GuestControlError(
                    f"timed out waiting for screen change; screenshot: {path}"
                )
            await asyncio.sleep(0.5)

    async def wait_for_image(
        self,
        template_path: Path,
        *,
        x: int,
        y: int,
        maximum_mean_difference: float = 12.0,
        timeout: float,
    ) -> Frame:
        """Wait for a screenshot region to match a fixture PNG image."""

        template = Frame.read_png(template_path)
        deadline = asyncio.get_running_loop().time() + timeout
        latest: Frame | None = None
        difference = float("inf")
        while True:
            latest = await self._vnc.frame(timeout=min(10, max(1, timeout)))
            if _contains_region(latest, template, x=x, y=y):
                difference = latest.mean_absolute_difference(template, x=x, y=y)
                if difference <= maximum_mean_difference:
                    self._record(latest, "image-match")
                    return latest
            if asyncio.get_running_loop().time() >= deadline:
                path = self._save(latest, "image-match-timeout")
                raise GuestControlError(
                    f"timed out matching {template_path} at ({x}, {y}); "
                    f"mean difference {difference:.1f}; frame {latest.width}x{latest.height}; "
                    f"template {template.width}x{template.height}; screenshot: {path}"
                )
            await asyncio.sleep(0.5)

    def _save(self, frame: Frame, label: str) -> Path:
        self._capture_index += 1
        path = self._debug_directory / f"{self._capture_index:02d}-{label}.png"
        frame.write_png(path)
        return path

    def _record(self, frame: Frame, label: str) -> None:
        if self._recorder is not None:
            self._recorder.add(frame, label)


def _contains_region(frame: Frame, template: Frame, *, x: int, y: int) -> bool:
    return (
        x >= 0
        and y >= 0
        and x + template.width <= frame.width
        and y + template.height <= frame.height
    )


@dataclass(frozen=True)
class NetworkInterface:
    """A private data NIC declared by a machine's ``networks`` tuple."""

    network: Network
    subnet: str
    mac: str


class GuestNetwork:
    """Private NIC metadata reported by the active QEMU backend."""

    def __init__(self, interfaces: tuple[NetworkInterface, ...]) -> None:
        self._interfaces = {interface.network: interface for interface in interfaces}

    @property
    def interfaces(self) -> tuple[NetworkInterface, ...]:
        return tuple(self._interfaces.values())

    def interface(self, network: Network) -> NetworkInterface:
        interface = self._interfaces.get(network)
        if interface is None:
            raise GuestControlError(
                "guest is not attached to the declared private network"
            )
        return interface


class Guest:
    """The runtime handle supplied to an executed ``@add_test`` function."""

    def __init__(
        self,
        *,
        source_id: str,
        running: QemuProcess,
        vnc: VncClient,
        control_port: int,
        interfaces: tuple[NetworkInterface, ...] = (),
        record: bool = False,
    ) -> None:
        self.source_id = source_id
        self._running = running
        self._vnc = vnc
        self._close_callbacks: list[Callable[[], Awaitable[None]]] = []
        self._release_directory = running.artifacts.release_directory
        self._recorder = (
            StepRecorder(running.artifacts.debug_directory)
            if record
            else None
        )
        self.screen = Screen(
            vnc,
            running.artifacts.debug_directory,
            self._release_directory,
            self._recorder,
        )
        self.keyboard = Keyboard(vnc, self.screen.record_step)
        self._control_url = f"http://127.0.0.1:{control_port}"
        self.network = GuestNetwork(interfaces)

    def _register_close_callback(
        self, callback: Callable[[], Awaitable[None]]
    ) -> None:
        self._close_callbacks.append(callback)

    @property
    def artifacts(self) -> Path:
        return self._running.artifacts.directory

    @property
    def debug_directory(self) -> Path:
        return self._running.artifacts.debug_directory

    @property
    def release_directory(self) -> Path:
        return self._release_directory

    def set_release_directory(self, directory: Path) -> None:
        """Route user-facing captures for the current test function."""

        directory.mkdir(parents=True, exist_ok=True)
        self._release_directory = directory
        self.screen.set_release_directory(directory)
        if self._recorder is not None:
            self._recorder.set_release_directory(directory)

    @property
    def recording_directory(self) -> Path | None:
        return self._recorder.directory if self._recorder is not None else None

    async def close(self) -> None:
        for callback in reversed(self._close_callbacks):
            await callback()
        await self._vnc.close()
        if self._recorder is not None:
            await self._recorder.finalize()


def _keysym(name: str) -> int:
    keys = {
        "ALT": 0xFFE9,
        "CTRL": 0xFFE3,
        "ENTER": 0xFF0D,
        "ESC": 0xFF1B,
        "SUPER": 0xFFEB,
        "TAB": 0xFF09,
        "BACKSPACE": 0xFF08,
        "SPACE": 0x20,
        "UP": 0xFF52,
        "DOWN": 0xFF54,
        "LEFT": 0xFF51,
        "RIGHT": 0xFF53,
    }
    upper = name.upper()
    if upper in keys:
        return keys[upper]
    if len(name) == 1:
        return ord(name)
    raise ValueError(f"unsupported VNC key name: {name}")
