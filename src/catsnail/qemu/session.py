"""One QEMU guest's runtime lifecycle within a graph execution."""

from __future__ import annotations

import asyncio
import shutil
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..graph.api import GraphDefinitionError, Network, Source
from ..guest import Guest, NetworkInterface, NetworkLink
from .artifacts import RunArtifacts
from .network import NetworkPool, SocketAttachment, UserAttachment
from .qmp import QmpClient
from .runner import (
    QemuLaunchOptions,
    QemuProcess,
    QemuRunError,
    QemuRunner,
)
from .vnc import VncClient


@dataclass
class SourceRuntime:
    """The live QEMU process and guest controls for one graph source."""

    source: Source[Guest]
    guest: Guest
    running: QemuProcess
    closed: bool = False


class QemuSession:
    """Create, checkpoint, and dispose source guests for one executor."""

    def __init__(
        self,
        *,
        target_dir: Path,
        record: bool,
        runner: QemuRunner | None = None,
        qemu_options: QemuLaunchOptions | None = None,
    ) -> None:
        if runner is not None and qemu_options is not None:
            raise ValueError("pass a runner or QEMU launch options, not both")
        self._target_dir = target_dir
        self._record = record
        self._runner = runner or QemuRunner(options=qemu_options)

    async def start(
        self,
        source: Source[Guest],
        *,
        network_pool: NetworkPool,
        relative_directory: Path,
        backing: Path | None = None,
        incoming_state: Path | None = None,
    ) -> SourceRuntime:
        """Start a source from a disk layer or restore its saved memory state."""

        guest_control_port = _free_port()
        artifacts = RunArtifacts.create(
            self._target_dir,
            relative_directory=relative_directory,
        )
        state_disk = artifacts.directory / "state.qcow2"
        await self._runner.create_overlay(
            state_disk,
            artifacts,
            backing=backing if backing is not None else source.machine.disk,
            size=source.machine.disk_size,
        )
        network_attachments = network_pool.attachments_for(
            source.id, source.machine.networks
        )
        running = await self._runner.start(
            source.machine,
            artifacts,
            vnc=True,
            guest_control_port=guest_control_port,
            network_attachments=network_attachments,
            state_disk=state_disk,
            incoming_state=incoming_state,
        )
        try:
            if incoming_state is not None:
                await self._resume(restored=running, incoming_state=incoming_state)
            vnc = await VncClient.connect(artifacts.vnc_socket, timeout=60)
            if source.machine.iso is not None and incoming_state is None:
                await _apply_boot_args(vnc, source.machine.boot_args)
            elif incoming_state is not None:
                # QEMU can drop the first input while the migrated display
                # catches up. Read one complete frame before exposing controls.
                await vnc.frame(timeout=20)
            guest = Guest(
                source_id=source.id,
                running=running,
                vnc=vnc,
                control_port=guest_control_port,
                interfaces=tuple(
                    _private_interface(network, attachment)
                    for network, attachment in zip(
                        source.machine.networks, network_attachments
                    )
                    if isinstance(attachment, SocketAttachment)
                ),
                links=_network_links(source.machine.networks, network_attachments),
                record=self._record,
            )
            await guest.screen.record_step("guest-ready")
            return SourceRuntime(source=source, guest=guest, running=running)
        except BaseException as error:
            await self._runner.stop(running)
            if isinstance(error, QemuRunError):
                raise
            raise QemuRunError(
                f"failed to initialise QEMU guest: {error}", artifacts
            ) from error

    async def save_state(self, runtime: SourceRuntime, state_path: Path) -> None:
        """Pause a guest and write a durable QEMU migration state."""

        qmp = await QmpClient.connect(runtime.running.artifacts.qmp_socket)
        try:
            await qmp.pause_and_save(state_path.resolve(), drive_id="catsnail-state")
        finally:
            await qmp.close()

    async def dispose(self, runtime: SourceRuntime, *, retain: bool) -> None:
        """Close controls, stop QEMU, and optionally discard runtime artifacts."""

        try:
            if not runtime.closed:
                try:
                    await runtime.guest.close()
                finally:
                    runtime.closed = True
        finally:
            # Guest controls (VNC, recorder, and close callbacks) are useful
            # but must never be able to orphan their owning QEMU process.
            try:
                await self._runner.stop(runtime.running)
            finally:
                if not retain:
                    shutil.rmtree(
                        runtime.running.artifacts.directory, ignore_errors=True
                    )

    async def save_failure_states(self, runtimes: Iterable[SourceRuntime]) -> None:
        """Keep exact per-guest resume points beside a failed test's artifacts."""

        for runtime in runtimes:
            if runtime.running.state_disk is None:
                continue
            state_path = runtime.running.artifacts.directory / "failure.state"
            try:
                await self.save_state(runtime, state_path)
                self._runner.write_resume_script(runtime.running, state_path.resolve())
            except BaseException:
                # The original test error is more valuable than a QEMU capture
                # failure. Existing screenshots and logs still remain useful.
                continue

    async def _resume(
        self, *, restored: QemuProcess, incoming_state: Path | None = None
    ) -> None:
        qmp = await QmpClient.connect(restored.artifacts.qmp_socket, timeout=60)
        try:
            await qmp.resume(incoming_state)
        finally:
            await qmp.close()


async def _apply_boot_args(vnc: VncClient, boot_args: tuple[str, ...]) -> None:
    """Edit Debian Live's BIOS boot-menu entry before the kernel starts."""

    deadline = asyncio.get_running_loop().time() + 25
    while True:
        frame = await vnc.frame(timeout=10)
        if frame.non_black_pixels() > 100:
            break
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError("timed out waiting for the ISO boot menu")
        await asyncio.sleep(0.25)
    if boot_args:
        await vnc.press(0xFF09)
        await vnc.type_text(" " + " ".join(boot_args))
    await vnc.press(0xFF0D)


def _private_interface(
    network: Network, attachment: SocketAttachment
) -> NetworkInterface:
    if network.subnet is None:
        raise GraphDefinitionError("socket attachment has no declared subnet")
    return NetworkInterface(
        network=network,
        subnet=network.subnet,
        mac=attachment.mac,
    )


def _network_links(
    networks: tuple[Network, ...],
    attachments: tuple[SocketAttachment | UserAttachment, ...],
) -> tuple[NetworkLink, ...]:
    """Name QEMU NIC devices exactly as :class:`QemuRunner` creates them."""

    socket_index = 0
    user_index = 0
    links: list[NetworkLink] = []
    for network, attachment in zip(networks, attachments):
        if isinstance(attachment, SocketAttachment):
            device_id = f"catsnail-socket{socket_index}"
            socket_index += 1
        else:
            device_id = f"catsnail-user{user_index}"
            user_index += 1
        links.append(NetworkLink(network=network, device_id=device_id))
    return tuple(links)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
