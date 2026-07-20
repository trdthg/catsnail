"""Filesystem layout for one Catsnail test invocation."""

from __future__ import annotations

import hashlib
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
        for child in run_root.iterdir():
            if child.name in {"checkpoints", "sockets"}:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        sockets = run_root / "sockets"
        shutil.rmtree(sockets, ignore_errors=True)
        sockets.mkdir()
        # ``result`` was the diagnostic directory before Catsnail 0.1.0.
        # Release output is retained until its owning test actually runs again.
        for name in ("debug", "result"):
            shutil.rmtree(target_dir / name, ignore_errors=True)

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
