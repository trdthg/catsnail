"""Live guest controls used by executed Catsnail tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .recording import StepRecorder
from ..qemu.vnc import Frame, VncClient

if TYPE_CHECKING:
    from ..graph.api import Network
    from ..qemu.runner import QemuProcess


class GuestControlError(RuntimeError):
    """Raised when a guest GUI or its local control endpoint is unavailable."""


class ScreenAssertionError(GuestControlError):
    """Raised when a requested visual state is not present on screen."""


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

    async def paste(self, value: str) -> None:
        await self._vnc.paste_text(value)
        await self._after_step("keyboard-paste")

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

    async def snapshot(self) -> Frame:
        """Read the current framebuffer without publishing an artifact."""

        return await self._vnc.frame()

    async def _capture_diagnostic(self, label: str) -> Frame:
        """Keep a framework-only diagnostic frame out of the release output."""

        frame = await self._vnc.frame()
        self._save(frame, label)
        self._record(frame, label)
        return frame

    async def click(self, x: int, y: int) -> None:
        await self._vnc.click(x, y)
        await self.record_step("screen-click")

    async def middle_click(self, x: int, y: int) -> None:
        """Click the middle mouse button at a screen coordinate."""

        await self._vnc.click(x, y, button=2)
        await self.record_step("screen-middle-click")

    async def right_click(self, x: int, y: int) -> None:
        """Open the context menu at a screen coordinate."""

        await self._vnc.click(x, y, button=3)
        await self.record_step("screen-right-click")

    async def move(self, x: int, y: int) -> None:
        """Move the pointer over a guest UI control without clicking it."""

        await self._vnc.move(x, y)
        await self.record_step("screen-move")

    async def scroll(self, x: int, y: int, amount: int) -> None:
        """Scroll a guest control at ``(x, y)``; positive values scroll up."""

        await self._vnc.scroll(x, y, amount)
        await self.record_step("screen-scroll")

    async def record_step(self, label: str) -> None:
        if self._recorder is None:
            return
        self._record(await self._vnc.frame(), label)

    async def wait_for_change(
        self,
        baseline: Frame,
        *,
        timeout: float,
        minimum_changed_pixels: int | None = None,
    ) -> Frame:
        """Wait for a material framebuffer change, retaining diagnostics on timeout.

        The default ignores incidental full-screen repaint noise. Pass a small
        explicit threshold for a local control such as an SWT list selection.
        """

        deadline = asyncio.get_running_loop().time() + timeout
        threshold = (
            (baseline.width * baseline.height) // 20
            if minimum_changed_pixels is None
            else minimum_changed_pixels
        )
        if threshold < 0:
            raise ValueError("minimum_changed_pixels must not be negative")
        while True:
            current = await self._vnc.frame(timeout=min(10, max(1, timeout)))
            if current.changed_pixels(baseline) > threshold:
                self._record(current, "screen-change")
                return current
            if asyncio.get_running_loop().time() >= deadline:
                path = self._save(current, "screen-change-timeout")
                raise GuestControlError(
                    f"timed out waiting for screen change; screenshot: {path}"
                )
            await asyncio.sleep(0.5)

    async def wait_for_stable(self, *, timeout: float = 30) -> Frame:
        """Wait until the framebuffer is unchanged across two polls."""

        deadline = asyncio.get_running_loop().time() + timeout
        previous: Frame | None = None
        stable = 0
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise GuestControlError("timed out waiting for a stable screen")
            current = await self._vnc.frame(timeout=min(10, max(1, remaining)))
            if previous is not None and current.changed_pixels(previous) == 0:
                stable += 1
                if stable >= 2:
                    return current
            else:
                stable = 0
            previous = current
            await asyncio.sleep(0.25)

    async def assert_screen(
        self,
        template_path: Path,
        *,
        x: int | None,
        y: int | None,
        maximum_mean_difference: float = 12.0,
        timeout: float,
        label: str | None = None,
    ) -> Frame:
        """Wait for a fixture region, then publish the matching full screen.

        Every successful visual assertion writes its exact matching framebuffer
        to the release directory. ``label`` controls that artifact name; the
        fixture filename is used when it is omitted.
        """

        template = Frame.read_png(template_path)
        if x is None and y is None:
            raise ValueError("assert_screen needs an x or y anchor")
        deadline = asyncio.get_running_loop().time() + timeout
        latest: Frame | None = None
        difference = float("inf")
        while True:
            latest = await self._vnc.frame(timeout=min(10, max(1, timeout)))
            matched_x = x if x is not None else _locate_template_x(latest, template, y=y)
            matched_y = y if y is not None else _locate_template_y(latest, template, x=x)
            difference = _image_difference(latest, template, x=matched_x, y=matched_y)
            if difference <= maximum_mean_difference:
                self._publish(latest, label or template_path.stem)
                return latest
            if asyncio.get_running_loop().time() >= deadline:
                path = self._save(latest, "screen-assertion-failed")
                raise ScreenAssertionError(
                    f"screen assertion failed for {template_path} at ({matched_x}, {matched_y}); "
                    f"mean difference {difference:.1f}; frame {latest.width}x{latest.height}; "
                    f"template {template.width}x{template.height}; screenshot: {path}"
                )
            await asyncio.sleep(0.5)

    def _save(self, frame: Frame, label: str) -> Path:
        self._capture_index += 1
        path = self._debug_directory / f"{self._capture_index:02d}-{label}.png"
        frame.write_png(path)
        return path

    def _publish(self, frame: Frame, label: str) -> Path:
        self._capture_index += 1
        path = self._release_directory / f"{self._capture_index:02d}-{label}.png"
        frame.write_png(path)
        self._record(frame, label)
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


def _image_difference(frame: Frame, template: Frame, *, x: int, y: int) -> float:
    if not _contains_region(frame, template, x=x, y=y):
        return float("inf")
    return frame.mean_absolute_difference(template, x=x, y=y)


def _locate_template_x(frame: Frame, template: Frame, *, y: int | None) -> int:
    """Find a template's most likely horizontal origin before full matching."""

    if y is None or y < 0 or y + template.height > frame.height:
        return 0
    limit = frame.width - template.width
    if limit < 0:
        return 0
    anchors = _template_anchors(template)
    best_x, best_difference = 0, float("inf")
    for x in range(limit + 1):
        difference = 0
        for anchor_x, anchor_y, red, green, blue in anchors:
            offset = ((y + anchor_y) * frame.width + x + anchor_x) * 4
            actual_red, actual_green, actual_blue = frame.rgba[offset : offset + 3]
            difference += (
                abs(actual_red - red)
                + abs(actual_green - green)
                + abs(actual_blue - blue)
            )
        if difference < best_difference:
            best_x, best_difference = x, difference
    return best_x


def _locate_template_y(frame: Frame, template: Frame, *, x: int | None) -> int:
    """Find a template's most likely vertical origin before full matching."""

    if x is None or x < 0 or x + template.width > frame.width:
        return 0
    limit = frame.height - template.height
    if limit < 0:
        return 0
    anchors = _template_anchors(template)
    best_y, best_difference = 0, float("inf")
    for y in range(limit + 1):
        difference = 0
        for anchor_x, anchor_y, red, green, blue in anchors:
            offset = ((y + anchor_y) * frame.width + x + anchor_x) * 4
            actual_red, actual_green, actual_blue = frame.rgba[offset : offset + 3]
            difference += (
                abs(actual_red - red)
                + abs(actual_green - green)
                + abs(actual_blue - blue)
            )
        if difference < best_difference:
            best_y, best_difference = y, difference
    return best_y


def _template_anchors(template: Frame) -> tuple[tuple[int, int, int, int, int], ...]:
    """Pick dark visual details from a grid as inexpensive location anchors."""

    columns, rows = 8, 10
    anchors: list[tuple[int, int, int, int, int]] = []
    for row in range(rows):
        top = row * template.height // rows
        bottom = max(top + 1, (row + 1) * template.height // rows)
        for column in range(columns):
            left = column * template.width // columns
            right = max(left + 1, (column + 1) * template.width // columns)
            candidate: tuple[int, int, int, int, int] | None = None
            candidate_darkness = -1
            for anchor_y in range(top, bottom):
                for anchor_x in range(left, right):
                    offset = (anchor_y * template.width + anchor_x) * 4
                    red, green, blue = template.rgba[offset : offset + 3]
                    darkness = 765 - red - green - blue
                    if darkness > candidate_darkness:
                        candidate = (anchor_x, anchor_y, red, green, blue)
                        candidate_darkness = darkness
            assert candidate is not None
            anchors.append(candidate)
    return tuple(anchors)


@dataclass(frozen=True)
class NetworkInterface:
    """A private data NIC declared by a machine's ``networks`` tuple."""

    network: Network
    subnet: str
    mac: str


@dataclass(frozen=True)
class NetworkLink:
    """An emulated NIC that Catsnail can connect or disconnect through QMP."""

    network: Network
    device_id: str


class GuestNetwork:
    """Private NIC metadata reported by the active QEMU backend."""

    def __init__(
        self,
        interfaces: tuple[NetworkInterface, ...],
        *,
        links: tuple[NetworkLink, ...] = (),
        qmp_socket: Path | None = None,
    ) -> None:
        self._interfaces = {interface.network: interface for interface in interfaces}
        self._links = {link.network: link for link in links}
        self._qmp_socket = qmp_socket

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

    async def disconnect(self, network: Network) -> None:
        """Disconnect a declared QEMU NIC without altering the host network."""

        link = self._links.get(network)
        if link is None:
            raise GuestControlError("guest is not attached to the declared network")
        if self._qmp_socket is None:
            raise GuestControlError("guest QMP control is unavailable")
        from ..qemu.qmp import QmpClient, QmpError

        try:
            qmp = await QmpClient.connect(self._qmp_socket)
            try:
                await qmp.execute("set_link", {"name": link.device_id, "up": False})
            finally:
                await qmp.close()
        except (OSError, ConnectionError, QmpError) as error:
            raise GuestControlError(
                f"could not disconnect guest network {link.device_id}: {error}"
            ) from error


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
        links: tuple[NetworkLink, ...] = (),
        record: bool = False,
    ) -> None:
        self.source_id = source_id
        self._running = running
        self._vnc = vnc
        self._close_callbacks: list[Callable[[], Awaitable[None]]] = []
        # Concrete adapters may be reconstructed by independent test helpers.
        # Keep physical guest-session state here so those wrappers do not open
        # competing terminals or duplicate guest-side services.
        self._adapter_state: dict[str, Any] = {}
        self._release_directory = running.artifacts.release_directory
        self._recorder = (
            StepRecorder(running.artifacts.debug_directory) if record else None
        )
        self.screen = Screen(
            vnc,
            running.artifacts.debug_directory,
            self._release_directory,
            self._recorder,
        )
        self.keyboard = Keyboard(vnc, self.screen.record_step)
        self._control_url = f"http://127.0.0.1:{control_port}"
        self.network = GuestNetwork(
            interfaces,
            links=links,
            qmp_socket=running.artifacts.qmp_socket,
        )

    def _register_close_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._close_callbacks.append(callback)

    @property
    def artifacts(self) -> Path:
        return self._running.artifacts.directory

    @property
    def vnc_socket(self) -> Path:
        """Return the local VNC endpoint for read-only observers."""

        return self._running.artifacts.vnc_socket

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
        "SHIFT": 0xFFE1,
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
        "MENU": 0xFF67,
        "HOME": 0xFF50,
        "END": 0xFF57,
        "PAGEUP": 0xFF55,
        "PAGEDOWN": 0xFF56,
    }
    upper = name.upper()
    if upper in keys:
        return keys[upper]
    if upper.startswith("F") and upper[1:].isdigit():
        number = int(upper[1:])
        if 1 <= number <= 12:
            return 0xFFBD + number
    if len(name) == 1:
        return ord(name)
    raise ValueError(f"unsupported VNC key name: {name}")
