"""QEMU subprocess lifecycle and command construction."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shlex
import shutil
import stat
import string
import weakref
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from ..graph.api import Machine
from ..image import ImageError, resolve_iso
from .artifacts import RunArtifacts
from .network import NetworkAttachment, SocketAttachment, UserAttachment


class QemuRunError(RuntimeError):
    def __init__(self, message: str, artifacts: RunArtifacts):
        super().__init__(message)
        self.artifacts = artifacts

    @property
    def reproduce_command(self) -> str:
        return f"sh {self.artifacts.reproduce_script}"


@dataclass(frozen=True)
class QemuLaunchOptions:
    """Optional host-side QEMU settings used for reproducible benchmarks.

    Catsnail requires KVM by default. TCG is an explicit opt-in for a
    deliberate emulation run, such as a future cross-architecture backend.
    Its tuning settings cannot silently affect a KVM run.
    """

    executable: str = "qemu-system-x86_64"
    acceleration: Literal["kvm", "tcg"] = "kvm"
    tcg_thread: Literal["single", "multi"] | None = None
    tcg_tb_size: int | None = None
    hugepage_path: Path | None = None
    qemu_img_executable: str | None = None

    def __post_init__(self) -> None:
        if not self.executable.strip():
            raise ValueError("QEMU executable must not be empty")
        if self.acceleration not in {"kvm", "tcg"}:
            raise ValueError(f"unsupported QEMU acceleration: {self.acceleration}")
        if self.acceleration != "tcg" and (
            self.tcg_thread is not None or self.tcg_tb_size is not None
        ):
            raise ValueError("TCG options require acceleration='tcg'")
        if self.tcg_tb_size is not None and self.tcg_tb_size < 1:
            raise ValueError("TCG translation cache size must be positive")

    def checkpoint_identity(self) -> dict[str, str | int] | None:
        """Return launch settings that must isolate VM checkpoints."""

        if self == QemuLaunchOptions():
            return None
        identity: dict[str, str | int] = {
            "executable": str(Path(self.executable).expanduser().resolve()),
            "acceleration": self.acceleration,
        }
        try:
            executable_stat = Path(self.executable).expanduser().resolve().stat()
        except OSError:
            executable_stat = None
        if executable_stat is not None:
            identity["executable_size"] = executable_stat.st_size
            identity["executable_mtime_ns"] = executable_stat.st_mtime_ns
        if self.tcg_thread is not None:
            identity["tcg_thread"] = self.tcg_thread
        if self.tcg_tb_size is not None:
            identity["tcg_tb_size_mib"] = self.tcg_tb_size
        if self.hugepage_path is not None:
            identity["hugepage_path"] = str(self.hugepage_path.resolve())
        if self.qemu_img_executable is not None:
            identity["qemu_img_executable"] = str(
                Path(self.qemu_img_executable).expanduser().resolve()
            )
        return identity


@dataclass
class QemuProcess:
    process: asyncio.subprocess.Process
    artifacts: RunArtifacts
    command: list[str]
    state_disk: Path | None = None


class QemuRunner:
    _instances: weakref.WeakSet[QemuRunner] = weakref.WeakSet()

    def __init__(
        self,
        executable: str = "qemu-system-x86_64",
        *,
        options: QemuLaunchOptions | None = None,
    ) -> None:
        if options is not None and executable != "qemu-system-x86_64":
            raise ValueError("pass the QEMU executable through options or executable, not both")
        self.options = options or QemuLaunchOptions(executable=executable)
        self.executable = self.options.executable
        self._active: dict[int, QemuProcess] = {}
        self._instances.add(self)

    def build_command(
        self,
        machine: Machine,
        artifacts: RunArtifacts,
        *,
        guest_name: str | None = None,
        vnc: bool = False,
        guest_control_port: int | None = None,
        network_attachments: tuple[NetworkAttachment, ...] = (),
        state_disk: Path | None = None,
        incoming_state: Path | None = None,
    ) -> list[str]:
        command = [self.executable, "-name", guest_name or _random_guest_name()]
        if self.options.acceleration == "kvm":
            command.extend(["-machine", "accel=kvm"])
        else:
            # ``-accel`` cannot be combined with ``-machine accel=...``.
            # The default x86 machine is explicit here for stable repro scripts.
            command.extend(["-machine", "pc", "-accel", "tcg"])
            if self.options.tcg_thread is not None:
                command[-1] += f",thread={self.options.tcg_thread}"
            if self.options.tcg_tb_size is not None:
                command[-1] += f",tb-size={self.options.tcg_tb_size}"
        command.extend(
            [
            "-m",
            machine.memory,
            "-smp",
            str(machine.vcpus),
            "-display",
            machine.display,
            "-monitor",
            "none",
            "-serial",
            f"file:{artifacts.serial_log}",
            "-chardev",
            f"socket,id=catsnail-serial,path={artifacts.serial_socket},server=on,wait=off",
            "-device",
            "isa-serial,chardev=catsnail-serial",
            "-qmp",
            f"unix:{artifacts.qmp_socket},server=on,wait=off",
            ]
        )
        if self.options.hugepage_path is not None:
            command.extend(
                [
                    "-mem-path",
                    str(self.options.hugepage_path),
                    "-mem-prealloc",
                ]
            )
        if machine.iso is not None:
            command.extend(["-cdrom", str(machine.iso), "-boot", "order=d"])
        if state_disk is not None:
            command.extend(
                [
                    "-drive",
                    f"file={state_disk},if=virtio,format=qcow2,id=catsnail-state,cache=none",
                ]
            )
        elif machine.disk is not None:
            command.extend(["-drive", f"file={machine.disk},if=virtio"])
        control_on_user_nic = guest_control_port is not None and any(
            isinstance(attachment, UserAttachment) for attachment in network_attachments
        )
        if guest_control_port is not None and not control_on_user_nic:
            host_forwards = [
                f"hostfwd=tcp:127.0.0.1:{guest_control_port}-:8123"
            ]
            command.extend(
                [
                    "-netdev",
                    "user,id=control," + ",".join(host_forwards),
                    "-device",
                    "e1000,netdev=control,mac=52:54:00:52:00:01",
                ]
            )
        socket_index = 0
        user_index = 0
        control_forwarded = False
        for attachment in network_attachments:
            if isinstance(attachment, SocketAttachment):
                netdev_id = f"socket{socket_index}"
                socket_index += 1
                command.extend(
                    [
                        "-netdev",
                        "socket,"
                        f"id={netdev_id},"
                        f"{attachment.endpoint}=127.0.0.1:{attachment.port}",
                        "-device",
                        f"virtio-net-pci,id=catsnail-{netdev_id},netdev={netdev_id},mac={attachment.mac}",
                    ]
                )
            else:
                netdev_id = f"user{user_index}"
                user_index += 1
                options = [f"id={netdev_id}", f"net={attachment.subnet}"]
                if guest_control_port is not None and not control_forwarded:
                    options.append(
                        f"hostfwd=tcp:127.0.0.1:{guest_control_port}-:8123"
                    )
                    control_forwarded = True
                command.extend(
                    [
                        "-netdev",
                        "user," + ",".join(options),
                        "-device",
                        f"virtio-net-pci,id=catsnail-{netdev_id},netdev={netdev_id},mac={attachment.mac}",
                    ]
                )
        if vnc:
            # An absolute USB tablet keeps VNC coordinates stable in desktop
            # guests. Without it QEMU exposes the legacy PS/2 mouse path,
            # whose relative-motion translation can miss SWT table controls.
            command.extend(
                [
                    "-device",
                    "piix3-usb-uhci,id=catsnail-usb",
                    "-device",
                    "usb-tablet,bus=catsnail-usb.0",
                    "-vnc",
                    f"unix:{artifacts.vnc_socket}",
                ]
            )
        if incoming_state is not None:
            # A compressed migration stream requires the destination to
            # advertise decompression before it begins reading the file.
            command.extend(["-incoming", "defer"])
        return command

    async def start(
        self,
        machine: Machine,
        artifacts: RunArtifacts,
        *,
        guest_name: str | None = None,
        vnc: bool = False,
        guest_control_port: int | None = None,
        network_attachments: tuple[NetworkAttachment, ...] = (),
        state_disk: Path | None = None,
        incoming_state: Path | None = None,
    ) -> QemuProcess:
        try:
            resolved_iso = await resolve_iso(machine.iso, machine.sha256)
        except ImageError as error:
            command = self.build_command(
                machine,
                artifacts,
                guest_name=guest_name,
                vnc=vnc,
                guest_control_port=guest_control_port,
                network_attachments=network_attachments,
                state_disk=state_disk,
                incoming_state=incoming_state,
            )
            self._write_reproduction(command, artifacts)
            raise QemuRunError(str(error), artifacts) from error
        runtime_machine = replace(machine, iso=resolved_iso, sha256=None)
        executable = shutil.which(self.executable)
        command = self.build_command(
            runtime_machine,
            artifacts,
            guest_name=guest_name,
            vnc=vnc,
            guest_control_port=guest_control_port,
            network_attachments=network_attachments,
            state_disk=state_disk,
            incoming_state=incoming_state,
        )
        self._write_reproduction(command, artifacts)

        if executable is None:
            raise QemuRunError(
                f"QEMU executable not found: {self.executable}", artifacts
            )
        if resolved_iso is not None and not resolved_iso.is_file():
            raise QemuRunError(f"ISO image not found: {resolved_iso}", artifacts)
        if runtime_machine.disk is not None and not runtime_machine.disk.is_file():
            raise QemuRunError(
                f"disk image not found: {runtime_machine.disk}", artifacts
            )
        if state_disk is not None and not state_disk.is_file():
            raise QemuRunError(f"runtime disk image not found: {state_disk}", artifacts)
        if incoming_state is not None and not incoming_state.is_file():
            raise QemuRunError(
                f"QEMU state image not found: {incoming_state}", artifacts
            )
        if self.options.hugepage_path is not None:
            hugepage_path = self.options.hugepage_path
            if not hugepage_path.is_dir():
                raise QemuRunError(
                    f"HugeTLB filesystem is not a directory: {hugepage_path}",
                    artifacts,
                )
            if not os.access(hugepage_path, os.R_OK | os.W_OK | os.X_OK):
                raise QemuRunError(
                    f"HugeTLB filesystem is not accessible: {hugepage_path}",
                    artifacts,
                )
        stderr_handle = artifacts.stderr_log.open("wb")
        start_task = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=stderr_handle,
                start_new_session=True,
            )
        )
        try:
            # Shield the spawn itself so Ctrl-C cannot leave an untracked
            # QEMU between fork and the return of create_subprocess_exec.
            process = await asyncio.shield(start_task)
        except asyncio.CancelledError:
            try:
                process = await start_task
            except BaseException:
                stderr_handle.close()
                raise
            stderr_handle.close()
            running = QemuProcess(
                process=process,
                artifacts=artifacts,
                command=command,
                state_disk=state_disk,
            )
            self._active[process.pid] = running
            await asyncio.shield(self.stop(running))
            raise
        except OSError as error:
            stderr_handle.close()
            raise QemuRunError(f"failed to start QEMU: {error}", artifacts) from error
        stderr_handle.close()
        running = QemuProcess(
            process=process,
            artifacts=artifacts,
            command=command,
            state_disk=state_disk,
        )
        self._active[process.pid] = running
        return running

    async def create_overlay(
        self,
        destination: Path,
        artifacts: RunArtifacts,
        *,
        backing: Path | None = None,
        size: str = "8G",
    ) -> Path:
        """Create a sparse QCOW2 layer, optionally backed by a prior layer."""

        executable = self._qemu_img_executable()
        if executable is None:
            raise QemuRunError("qemu-img executable not found", artifacts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [executable, "create", "-f", "qcow2"]
        if backing is not None:
            resolved_backing = backing.resolve()
            if not resolved_backing.is_file():
                raise QemuRunError(
                    f"backing disk image not found: {backing}", artifacts
                )
            command.extend(
                [
                    "-F",
                    await self._image_format(resolved_backing, artifacts),
                    "-b",
                    str(resolved_backing),
                ]
            )
        command.extend([str(destination), size])
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise QemuRunError(
                f"qemu-img failed creating {destination}: {detail}", artifacts
            )
        return destination

    async def _image_format(self, image: Path, artifacts: RunArtifacts) -> str:
        executable = self._qemu_img_executable()
        if executable is None:
            raise QemuRunError("qemu-img executable not found", artifacts)
        process = await asyncio.create_subprocess_exec(
            executable,
            "info",
            "--output=json",
            str(image),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise QemuRunError(
                f"qemu-img could not inspect {image}: {detail}", artifacts
            )
        try:
            payload = json.loads(stdout)
            image_format = payload["format"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise QemuRunError(
                f"qemu-img returned invalid image info for {image}", artifacts
            ) from error
        if not isinstance(image_format, str):
            raise QemuRunError(
                f"qemu-img returned invalid image format for {image}", artifacts
            )
        return image_format

    def _qemu_img_executable(self) -> str | None:
        """Resolve qemu-img beside a custom QEMU binary, then PATH."""

        configured = self.options.qemu_img_executable
        if configured is not None:
            return shutil.which(configured)
        qemu_path = Path(self.executable)
        if qemu_path.parent != Path("."):
            sibling = qemu_path.parent / "qemu-img"
            if sibling.is_file() and os.access(sibling, os.X_OK):
                return str(sibling)
        return shutil.which("qemu-img")

    async def stop(self, running: QemuProcess) -> int:
        stop_task = asyncio.create_task(self._stop_process(running))
        try:
            return await asyncio.shield(stop_task)
        except asyncio.CancelledError:
            # A second Ctrl-C must not interrupt cleanup between TERM and the
            # process reaping step. Force the process group down, then finish
            # waiting for the shielded task before propagating cancellation.
            try:
                os.killpg(running.process.pid, 9)
            except ProcessLookupError:
                pass
            try:
                await asyncio.shield(stop_task)
            except BaseException:
                stop_task.cancel()
                await asyncio.gather(stop_task, return_exceptions=True)
                raise
            raise
        finally:
            if stop_task.done():
                self._active.pop(running.process.pid, None)
                running.artifacts.qmp_socket.unlink(missing_ok=True)
                running.artifacts.vnc_socket.unlink(missing_ok=True)

    async def _stop_process(self, running: QemuProcess) -> int:
        if running.process.returncode is not None:
            return running.process.returncode
        try:
            os.killpg(running.process.pid, 15)
        except ProcessLookupError:
            pass
        try:
            return await asyncio.wait_for(running.process.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(running.process.pid, 9)
            except ProcessLookupError:
                pass
            return await running.process.wait()

    async def stop_all(self) -> None:
        """Stop every QEMU process started by this runner instance."""

        await asyncio.gather(
            *(self.stop(running) for running in tuple(self._active.values())),
            return_exceptions=True,
        )

    @classmethod
    async def stop_all_instances(cls) -> None:
        """Stop QEMU processes owned by the current Catsnail process."""

        await asyncio.gather(
            *(runner.stop_all() for runner in tuple(cls._instances)),
            return_exceptions=True,
        )

    @staticmethod
    def _write_reproduction(command: list[str], artifacts: RunArtifacts) -> None:
        artifacts.command_json.write_text(
            json.dumps(command, indent=2) + "\n", encoding="utf-8"
        )
        artifacts.reproduce_script.write_text(
            "#!/bin/sh\nset -eu\nexec " + shlex.join(command) + "\n",
            encoding="utf-8",
        )
        artifacts.reproduce_script.chmod(
            artifacts.reproduce_script.stat().st_mode | stat.S_IXUSR
        )

    @staticmethod
    def write_resume_script(running: QemuProcess, state_path: Path) -> Path:
        """Write a command that resumes an exact saved VM state."""

        command: list[str] = []
        iterator = iter(running.command)
        for argument in iterator:
            if argument == "-incoming":
                next(iterator, None)
                continue
            command.append(argument)
        command.extend(["-incoming", "defer"])
        script = running.artifacts.directory / "resume.sh"
        script.write_text(
            "#!/bin/sh\n"
            "set -eu\n" + shlex.join(command) + " &\n"
            "qemu_pid=$!\n"
            "trap 'kill \"$qemu_pid\" 2>/dev/null || true' EXIT INT TERM\n"
            "python3 - "
            + shlex.quote(str(running.artifacts.qmp_socket))
            + " "
            + shlex.quote(f"file:{state_path}")
            + " <<'PY'\n"
            "import json\n"
            "import socket\n"
            "import sys\n"
            "import time\n"
            "\n"
            "path, incoming = sys.argv[1:]\n"
            "deadline = time.monotonic() + 60\n"
            "while True:\n"
            "    try:\n"
            "        connection = socket.socket(socket.AF_UNIX)\n"
            "        connection.connect(path)\n"
            "        break\n"
            "    except OSError:\n"
            "        if time.monotonic() >= deadline:\n"
            "            raise\n"
            "        time.sleep(0.1)\n"
            "def execute(command, arguments=None):\n"
            '    request = {"execute": command}\n'
            "    if arguments is not None:\n"
            '        request["arguments"] = arguments\n'
            "    connection.sendall(json.dumps(request).encode() + b'\\r\\n')\n"
            '    file = connection.makefile("rb")\n'
            "    while True:\n"
            "        response = json.loads(file.readline())\n"
            '        if "return" in response:\n'
            '            return response["return"]\n'
            '        if "error" in response:\n'
            '            raise RuntimeError(response["error"])\n'
            "\n"
            'execute("qmp_capabilities")\n'
            'execute("migrate-set-capabilities", {"capabilities": [\n'
            '    {"capability": "compress", "state": True}\n'
            "]})\n"
            'execute("migrate-incoming", {"uri": incoming})\n'
            "while True:\n"
            '    status = execute("query-status")\n'
            '    if status.get("status") == "paused":\n'
            "        break\n"
            "    if time.monotonic() >= deadline:\n"
            '        raise RuntimeError(f"QEMU did not restore: {status}")\n'
            "    time.sleep(0.1)\n"
            'execute("cont")\n'
            "connection.close()\n"
            "PY\n"
            'wait "$qemu_pid"\n',
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return script


def _random_guest_name() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "catsnail-" + "".join(secrets.choice(alphabet) for _ in range(6))
