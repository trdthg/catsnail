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
from dataclasses import dataclass, replace
from pathlib import Path

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
class QemuNetwork:
    """QEMU network devices for one live guest.

    The localhost control endpoint is forwarded through a declared ``NetUser``
    NIC when one is available. Otherwise Catsnail adds a private SLIRP NIC
    solely for that endpoint.
    """

    control_port: int


@dataclass
class QemuProcess:
    process: asyncio.subprocess.Process
    artifacts: RunArtifacts
    command: list[str]
    state_disk: Path | None = None


class QemuRunner:
    def __init__(self, executable: str = "qemu-system-x86_64") -> None:
        self.executable = executable

    def build_command(
        self,
        machine: Machine,
        artifacts: RunArtifacts,
        *,
        guest_name: str | None = None,
        vnc: bool = False,
        network: QemuNetwork | None = None,
        network_attachments: tuple[NetworkAttachment, ...] = (),
        state_disk: Path | None = None,
        incoming_state: Path | None = None,
    ) -> list[str]:
        command = [
            self.executable,
            "-name",
            guest_name or _random_guest_name(),
            "-machine",
            "accel=kvm:tcg",
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
        control_on_user_nic = network is not None and any(
            isinstance(attachment, UserAttachment)
            for attachment in network_attachments
        )
        if network is not None and not control_on_user_nic:
            host_forwards = [f"hostfwd=tcp:127.0.0.1:{network.control_port}-:8123"]
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
                if network is not None and not control_forwarded:
                    options.append(
                        f"hostfwd=tcp:127.0.0.1:{network.control_port}-:8123"
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
            command.extend(["-vnc", f"unix:{artifacts.vnc_socket}"])
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
        network: QemuNetwork | None = None,
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
                network=network,
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
            network=network,
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
        stderr_handle = artifacts.stderr_log.open("wb")
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=stderr_handle,
                start_new_session=True,
            )
        except OSError as error:
            stderr_handle.close()
            raise QemuRunError(f"failed to start QEMU: {error}", artifacts) from error
        stderr_handle.close()
        return QemuProcess(
            process=process,
            artifacts=artifacts,
            command=command,
            state_disk=state_disk,
        )

    async def create_overlay(
        self,
        destination: Path,
        artifacts: RunArtifacts,
        *,
        backing: Path | None = None,
        size: str = "8G",
    ) -> Path:
        """Create a sparse QCOW2 layer, optionally backed by a prior layer."""

        executable = shutil.which("qemu-img")
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
        executable = shutil.which("qemu-img")
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

    async def stop(self, running: QemuProcess) -> int:
        if running.process.returncode is not None:
            returncode = running.process.returncode
        else:
            try:
                os.killpg(running.process.pid, 15)
            except ProcessLookupError:
                pass
            try:
                returncode = await asyncio.wait_for(running.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    os.killpg(running.process.pid, 9)
                except ProcessLookupError:
                    pass
                returncode = await running.process.wait()
        running.artifacts.qmp_socket.unlink(missing_ok=True)
        running.artifacts.vnc_socket.unlink(missing_ok=True)
        return returncode

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
            "set -eu\n"
            + shlex.join(command)
            + " &\n"
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
            "    request = {\"execute\": command}\n"
            "    if arguments is not None:\n"
            "        request[\"arguments\"] = arguments\n"
            "    connection.sendall(json.dumps(request).encode() + b'\\r\\n')\n"
            "    file = connection.makefile(\"rb\")\n"
            "    while True:\n"
            "        response = json.loads(file.readline())\n"
            "        if \"return\" in response:\n"
            "            return response[\"return\"]\n"
            "        if \"error\" in response:\n"
            "            raise RuntimeError(response[\"error\"])\n"
            "\n"
            "execute(\"qmp_capabilities\")\n"
            "execute(\"migrate-set-capabilities\", {\"capabilities\": [\n"
            "    {\"capability\": \"compress\", \"state\": True}\n"
            "]})\n"
            "execute(\"migrate-incoming\", {\"uri\": incoming})\n"
            "while True:\n"
            "    status = execute(\"query-status\")\n"
            "    if status.get(\"status\") == \"paused\":\n"
            "        break\n"
            "    if time.monotonic() >= deadline:\n"
            "        raise RuntimeError(f\"QEMU did not restore: {status}\")\n"
            "    time.sleep(0.1)\n"
            "execute(\"cont\")\n"
            "connection.close()\n"
            "PY\n"
            "wait \"$qemu_pid\"\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return script


def _random_guest_name() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "catsnail-" + "".join(secrets.choice(alphabet) for _ in range(6))
