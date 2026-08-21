"""Filesystem layout for one Catsnail test invocation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunArtifacts:
    """Paths for one guest's runtime, debug, and release artifacts."""

    target_dir: Path
    run_id: str
    directory: Path
    debug_directory: Path
    release_directory: Path
    serial_log: Path
    serial_socket: Path
    stderr_log: Path
    qmp_socket: Path
    vnc_socket: Path
    command_json: Path
    reproduce_script: Path

    @classmethod
    def prepare(cls, target_dir: Path = Path("target")) -> None:
        """Discard prior test output while preserving durable runtime caches."""

        run_root = target_dir / "run"
        run_root.mkdir(parents=True, exist_ok=True)
        studio_sockets = _active_studio_sockets(run_root)
        for child in run_root.iterdir():
            if child.name in {"checkpoints", "sockets"}:
                continue
            if child.name == "studio":
                _discard_inactive_studios(child, studio_sockets)
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        sockets = run_root / "sockets"
        sockets.mkdir(exist_ok=True)
        for socket in sockets.iterdir():
            if socket not in studio_sockets:
                socket.unlink(missing_ok=True)
        # ``result`` was the diagnostic directory before Catsnail 0.1.0.
        # Release output is retained until its owning test actually runs again.
        debug = target_dir / "debug"
        if debug.is_dir():
            for child in debug.iterdir():
                if child.name != "studio":
                    shutil.rmtree(child, ignore_errors=True)
        shutil.rmtree(target_dir / "result", ignore_errors=True)
    @classmethod
    def create(
        cls,
        target_dir: Path = Path("target"),
        *,
        relative_directory: Path = Path("machine"),
    ) -> RunArtifacts:
        """Create one named artifact directory for the current CLI invocation."""

        run_root = target_dir / "run"
        if relative_directory.is_absolute() or ".." in relative_directory.parts:
            raise ValueError("artifact directory must be relative to target/run")
        directory = run_root / relative_directory
        debug_directory = target_dir / "debug" / relative_directory
        release_directory = target_dir / "release" / relative_directory
        directory.mkdir(parents=True, exist_ok=False)
        debug_directory.mkdir(parents=True, exist_ok=True)
        run_id = hashlib.sha256(os.fsencode(relative_directory)).hexdigest()[:16]
        qmp_socket = _runtime_socket_path(run_root.resolve(), run_id, "qmp")
        vnc_socket = _runtime_socket_path(run_root.resolve(), run_id, "vnc")
        qmp_socket.parent.mkdir(parents=True, exist_ok=True)
        vnc_socket.parent.mkdir(parents=True, exist_ok=True)
        return cls(
            target_dir=target_dir,
            run_id=run_id,
            directory=directory,
            debug_directory=debug_directory,
            release_directory=release_directory,
            serial_log=directory / "serial.log",
            serial_socket=_runtime_socket_path(run_root.resolve(), run_id, "serial"),
            stderr_log=directory / "qemu.stderr.log",
            qmp_socket=qmp_socket,
            vnc_socket=vnc_socket,
            command_json=directory / "qemu-command.json",
            reproduce_script=directory / "reproduce.sh",
        )


def _active_studio_sockets(run_root: Path) -> set[Path]:
    """Return Unix sockets still owned by a live Studio process group."""

    active: set[Path] = set()
    studio_root = run_root / "studio"
    if not studio_root.is_dir():
        return active
    socket_root = (run_root / "sockets").resolve()
    for manifest_path in studio_root.glob("*/session.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict) or manifest.get("status") != "active":
            continue
        machines = manifest.get("machines")
        if not isinstance(machines, dict) or not _has_live_studio_machine(machines):
            continue
        for machine in machines.values():
            if not isinstance(machine, dict):
                continue
            for name in ("serial_socket", "qmp_socket", "vnc_socket"):
                value = machine.get(name)
                if not isinstance(value, str):
                    continue
                path = Path(value)
                try:
                    if path.resolve().parent == socket_root:
                        active.add(path)
                except OSError:
                    continue
    return active


def _has_live_studio_machine(machines: dict[object, object]) -> bool:
    for machine in machines.values():
        if not isinstance(machine, dict):
            continue
        pid = machine.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            return True
        return True
    return False


def _discard_inactive_studios(studio_root: Path, active_sockets: set[Path]) -> None:
    for directory in studio_root.iterdir():
        if not directory.is_dir():
            directory.unlink(missing_ok=True)
            continue
        manifest = directory / "session.json"
        if not manifest.is_file():
            shutil.rmtree(directory, ignore_errors=True)
            continue
        if _studio_has_live_process(manifest):
            continue
        # An active session is represented by at least one socket retained by
        # _active_studio_sockets. Sessions that exited without ``studio stop``
        # are safe to discard with ordinary stale runtime output.
        if not active_sockets.intersection(
            {Path(value) for value in _studio_socket_values(manifest)}
        ):
            shutil.rmtree(directory, ignore_errors=True)


def _studio_has_live_process(manifest_path: Path) -> bool:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    machines = manifest.get("machines") if isinstance(manifest, dict) else None
    return isinstance(machines, dict) and _has_live_studio_machine(machines)


def _studio_socket_values(manifest_path: Path) -> tuple[str, ...]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(manifest, dict) or not isinstance(manifest.get("machines"), dict):
        return ()
    values: list[str] = []
    for machine in manifest["machines"].values():
        if not isinstance(machine, dict):
            continue
        for name in ("serial_socket", "qmp_socket", "vnc_socket"):
            value = machine.get(name)
            if isinstance(value, str):
                values.append(value)
    return tuple(values)


def _runtime_socket_path(root: Path, run_id: str, kind: str) -> Path:
    """Choose a Unix path below Linux's 108-byte sockaddr limit."""

    candidate = root / "sockets" / f"{run_id}-{kind}.sock"
    if len(os.fsencode(candidate)) < 100:
        return candidate

    root_hash = hashlib.sha256(os.fsencode(root)).hexdigest()[:12]
    return (
        Path(tempfile.gettempdir())
        / f"catsnail-{os.getuid()}-{root_hash}"
        / f"{run_id}-{kind}.sock"
    )
