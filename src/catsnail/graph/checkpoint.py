"""Durable content-addressed QEMU checkpoint metadata and coordination."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .api import Machine, Node, Source, TestNode


# QEMU device topology is part of a migration-state ABI. Increment this when
# Catsnail changes the command line in a way that prevents old VM snapshots
# from being resumed safely.
CHECKPOINT_FORMAT = 3


class CheckpointError(RuntimeError):
    """Raised when a durable checkpoint is missing or malformed."""


class CheckpointCoordinator:
    """In-process locks that make concurrent targets publish a node only once."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock


class CheckpointStore:
    """Read and atomically publish persistent checkpoint directories."""

    def __init__(self, target_dir: Path) -> None:
        self.root = target_dir / "run" / "checkpoints"
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self, key: str, *, name: str) -> dict[str, Any] | None:
        """Load ``key`` from its deterministic, human-readable directory."""

        directory = self._directory(key, name)
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            return None
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CheckpointError(
                f"invalid checkpoint manifest {manifest_path}: {error}"
            ) from error
        if not isinstance(payload, dict) or payload.get("key") != key:
            raise CheckpointError(f"invalid checkpoint manifest {manifest_path}")
        # Result-only entries were produced by a short-lived pre-1.0 design.
        # They cannot restore a VM and are rebuilt as ordinary checkpoints.
        if payload.get("kind") == "result":
            return None
        machines = payload.get("machines")
        if not isinstance(machines, list) or not machines:
            raise CheckpointError(f"checkpoint {directory} has no guest states")
        for machine in machines:
            if not isinstance(machine, dict):
                raise CheckpointError(
                    f"checkpoint {directory} has an invalid guest state"
                )
            disk = machine.get("disk")
            state = machine.get("state")
            if not isinstance(disk, str) or not isinstance(state, str):
                raise CheckpointError(
                    f"checkpoint {directory} has an invalid guest state"
                )
            if not (directory / disk).is_file() or not (directory / state).is_file():
                raise CheckpointError(f"checkpoint {directory} is incomplete")
        payload["directory"] = directory
        return payload

    def staging_directory(self, key: str, *, name: str) -> Path:
        directory_name = self._directory_name(key, name)
        directory = self.root / f".{directory_name}.{uuid.uuid4().hex}.tmp"
        directory.mkdir()
        return directory

    def publish(
        self,
        key: str,
        staging: Path,
        manifest: Mapping[str, Any],
        *,
        name: str,
    ) -> dict[str, Any]:
        target = self._directory(key, name)
        manifest_path = staging / "manifest.json"
        payload = {**dict(manifest), "key": key}
        manifest_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        previous: Path | None = None
        if target.exists():
            previous = self.root / f".{target.name}.{uuid.uuid4().hex}.previous"
            os.replace(target, previous)
        try:
            os.replace(staging, target)
        except BaseException:
            if previous is not None:
                os.replace(previous, target)
            raise
        if previous is not None:
            _remove_cache_path(previous)
        loaded = self.load(key, name=name)
        if loaded is None:
            raise CheckpointError(f"checkpoint publication did not create {target}")
        return loaded

    @staticmethod
    def discard(staging: Path) -> None:
        shutil.rmtree(staging, ignore_errors=True)

    def stale_directories(
        self, current_checkpoints: Mapping[str, str]
    ) -> tuple[Path, ...]:
        """Return stale checkpoints created by files in the current graph.

        Checkpoints from other test files remain reusable when a user selects
        one file or subdirectory. Interrupted publications and malformed
        entries are always stale because they cannot be restored safely.
        """

        current_origins = set(current_checkpoints.values())
        stale: list[Path] = []
        for directory in sorted(self.root.iterdir()):
            if directory.name.startswith("."):
                stale.append(directory)
                continue
            if not directory.is_dir():
                stale.append(directory)
                continue
            try:
                manifest = json.loads(
                    (directory / "manifest.json").read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                manifest = None
            if not isinstance(manifest, dict):
                stale.append(directory)
                continue
            key = manifest.get("key")
            origin = manifest.get("origin")
            if (
                not isinstance(key, str)
                or not isinstance(origin, str)
                or manifest.get("kind") == "result"
            ):
                stale.append(directory)
            elif origin in current_origins and key not in current_checkpoints:
                stale.append(directory)
        return tuple(stale)

    def prune(self, current_checkpoints: Mapping[str, str]) -> tuple[Path, ...]:
        """Remove and return stale checkpoint directories."""

        stale = self.stale_directories(current_checkpoints)
        self.remove(stale)
        return stale

    def remove(self, directories: Iterable[Path]) -> None:
        """Remove checkpoint entries returned by ``stale_directories``."""

        for directory in directories:
            if directory.parent != self.root:
                raise ValueError(
                    f"checkpoint directory is outside {self.root}: {directory}"
                )
            _remove_cache_path(directory)

    def _directory(self, key: str, name: str) -> Path:
        return self.root / self._directory_name(key, name)

    @staticmethod
    def _directory_name(key: str, name: str) -> str:
        label = "".join(
            character
            if character.isascii() and (character.isalnum() or character in "-_")
            else "_"
            for character in name
        ).strip("_")
        if not label:
            label = "checkpoint"
        return f"{label}-{key[:12]}"


def _remove_cache_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def checkpoint_key(node: TestNode[Any]) -> str:
    """Compute a reproducible cache key without persisting input values."""

    memo: dict[str, dict[str, Any]] = {}

    def describe(current: Node) -> dict[str, Any]:
        cached = memo.get(current.id)
        if cached is not None:
            return cached
        if isinstance(current, Source):
            payload = {
                "kind": "source",
                "id": current.id,
                "machine": _machine_payload(current.machine),
            }
        else:
            payload = {
                "kind": "test",
                "id": current.id,
                "code": _function_payload(current.function),
                "inputs": _normalise(current.inputs),
                "dependencies": {
                    name: describe(dependency.node)
                    for name, dependency in sorted(current.dependencies.items())
                },
            }
        memo[current.id] = payload
        return payload

    encoded = json.dumps(
        {"format": CHECKPOINT_FORMAT, "node": describe(node)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def checkpoint_origin(node: TestNode[Any]) -> str:
    """Return the source file that owns a checkpoint declaration."""

    return str(_function_file(node.function))


def source_file_name(source_id: str, suffix: str) -> str:
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:16]
    return f"{digest}.{suffix}"


def _machine_payload(machine: Machine) -> dict[str, Any]:
    payload = asdict(machine)
    for name in ("iso", "disk"):
        value = getattr(machine, name)
        payload[name] = _image_identity(value) if value is not None else None
    return _normalise(payload)


def _function_payload(function: Any) -> dict[str, Any]:
    try:
        source = inspect.getsource(function)
    except (OSError, TypeError):
        source = None
    filename = _function_file(function)
    return {
        "source": source,
        "module_file": str(filename),
    }


def _function_file(function: Any) -> Path:
    filename = Path(function.__code__.co_filename)
    return filename.resolve() if filename.is_file() else filename


def _path_identity(path: Path) -> dict[str, Any]:
    stat_result = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
    }


def _image_identity(image: Path | str) -> dict[str, Any]:
    if isinstance(image, Path):
        return _path_identity(image)
    return {"url": image}


def _normalise(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return _path_identity(value)
    if isinstance(value, Mapping):
        return {
            str(key): _normalise(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_normalise(item) for item in value]
    return {
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
        "repr": repr(value),
    }
