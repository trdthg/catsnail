"""Interactive VM exploration sessions used to author Catsnail tests.

Studio deliberately sits beside the graph executor.  It restores an existing
checkpoint, records every control operation and framebuffer, and emits a
reviewable test draft.  The recorded session is the durable source of truth;
the generated Python file is only a convenient starting point for a test.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import signal
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Mapping

from .graph.api import TestNode, collect_module_tests
from .graph.checkpoint import CheckpointStore, checkpoint_key
from .graph.executor import GraphExecutor, _source_dependencies
from .guest.controls import Guest
from .qemu.artifacts import RunArtifacts
from .qemu.network import NetworkPool
from .qemu.vnc import Frame, VncClient


class StudioError(RuntimeError):
    """Raised for an invalid or unavailable interactive session."""


@dataclass(frozen=True)
class StudioEvent:
    id: int
    machine: str
    action: str
    args: dict[str, Any]
    before: int | None
    after: int | None
    duration: float
    result: str = "ok"
    error: str | None = None

    def as_json(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "machine": self.machine,
            "action": self.action,
            "args": self.args,
            "before": self.before,
            "after": self.after,
            "duration": round(self.duration, 4),
            "result": self.result,
        }
        if self.error is not None:
            value["error"] = self.error
        return value


@dataclass
class _StudioMachine:
    name: str
    source_id: str
    artifacts: RunArtifacts
    guest: Guest
    pid: int | None = None


class StudioSessionStore:
    """Filesystem-backed index for sessions that outlive a CLI invocation."""

    def __init__(self, target_dir: Path = Path("target")) -> None:
        self.target_dir = target_dir
        self.root = target_dir / "run" / "studio"
        self.root.mkdir(parents=True, exist_ok=True)

    def create_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def directory(self, session_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", session_id):
            raise StudioError(f"invalid studio session id: {session_id!r}")
        return self.root / session_id

    def manifest_path(self, session_id: str) -> Path:
        return self.directory(session_id) / "session.json"

    def read(self, session_id: str) -> dict[str, Any]:
        path = self.manifest_path(session_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise StudioError(f"studio session not found: {session_id}") from error
        except (OSError, json.JSONDecodeError) as error:
            raise StudioError(f"invalid studio session manifest: {path}") from error
        if not isinstance(payload, dict):
            raise StudioError(f"invalid studio session manifest: {path}")
        return payload

    def write(self, session_id: str, payload: Mapping[str, Any]) -> None:
        directory = self.directory(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / ".session.json.tmp"
        temporary.write_text(
            json.dumps(dict(payload), indent=2, ensure_ascii=False, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.manifest_path(session_id))

    def active(self, session_id: str | None = None) -> str:
        if session_id is not None:
            return session_id
        candidates: list[tuple[float, str]] = []
        for manifest in self.root.glob("*/session.json"):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("status") == "active":
                if not _manifest_is_live(payload):
                    payload["status"] = "stale"
                    try:
                        self.write(manifest.parent.name, payload)
                    except OSError:
                        pass
                    continue
                candidates.append((manifest.stat().st_mtime, manifest.parent.name))
        if not candidates:
            raise StudioError("no active studio session")
        return max(candidates)[1]


class StudioSession:
    """A live, serialised control surface for one restored graph checkpoint."""

    def __init__(
        self,
        *,
        store: StudioSessionStore,
        session_id: str,
        manifest: dict[str, Any],
        machines: dict[str, _StudioMachine],
        executor: GraphExecutor | None = None,
        runtimes: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.session_id = session_id
        self.directory = store.directory(session_id)
        self.frames_directory = self.directory / "frames"
        self.fixtures_directory = self.directory / "fixtures"
        self.serial_directory = self.directory / "serial"
        self.events_path = self.directory / "events.jsonl"
        self.replay_path = self.directory / "replay.sh"
        self.manifest = manifest
        self.machines = machines
        self._executor = executor
        self._runtimes = runtimes or {}
        self._lock = asyncio.Lock()
        self._frame_id = int(manifest.get("frame_id", 0))
        self._event_id = int(manifest.get("event_id", 0))

    @classmethod
    async def start(
        cls,
        path: Path,
        checkpoint_name: str,
        *,
        target_dir: Path = Path("target"),
        session_id: str | None = None,
    ) -> StudioSession:
        """Restore ``checkpoint_name`` and leave its guests running."""

        from .cli import _load_module

        module = _load_module(path)
        graph = collect_module_tests(module)
        candidates = [
            node
            for node in graph.validate()
            if isinstance(node, TestNode)
            and (node.function.__name__ == checkpoint_name or node.id == checkpoint_name)
        ]
        if len(candidates) != 1:
            raise StudioError(
                f"expected one @add_test checkpoint named {checkpoint_name!r}, "
                f"found {len(candidates)}"
            )
        node = candidates[0]
        store = StudioSessionStore(target_dir)
        sid = session_id or store.create_id()
        directory = store.directory(sid)
        if directory.exists():
            raise StudioError(f"studio session already exists: {sid}")
        checkpoint = CheckpointStore(target_dir).load(
            checkpoint_key(node), name=node.function.__name__
        )
        if checkpoint is None:
            raise StudioError(
                f"checkpoint {checkpoint_name!r} is not available; run the test first"
            )
        directory.mkdir(parents=True)
        (directory / "frames").mkdir()
        (directory / "fixtures").mkdir()
        (directory / "serial").mkdir()
        executor = GraphExecutor(
            graph,
            target_dir=target_dir,
            record=False,
            artifact_prefix=Path("studio") / sid,
        )
        runtimes: dict[str, Any] = {}
        output, guests = await executor._restore_checkpoint(
            node,
            checkpoint,
            runtimes,
            [],
            NetworkPool(),
            target=node,
        )
        del output
        machines: dict[str, _StudioMachine] = {}
        for index, source in enumerate(_source_dependencies(node), start=1):
            guest = guests.get(source.id)
            runtime = runtimes.get(source.id)
            if guest is None or runtime is None:
                continue
            name = _machine_name(node, source, index)
            machines[name] = _StudioMachine(
                name=name,
                source_id=source.id,
                artifacts=guest._running.artifacts,
                guest=guest,
                pid=guest._running.process.pid,
            )
            _link_serial_log(machines[name], directory / "serial")
        manifest = {
            "format": 1,
            "id": sid,
            "status": "active",
            "path": str(path.resolve()),
            "checkpoint": checkpoint_name,
            "checkpoint_key": checkpoint_key(node),
            "created": time.time(),
            "frame_id": 0,
            "event_id": 0,
            "machines": {
                name: _machine_manifest(machine) for name, machine in machines.items()
            },
        }
        session = cls(
            store=store,
            session_id=sid,
            manifest=manifest,
            machines=machines,
            executor=executor,
            runtimes=runtimes,
        )
        store.write(sid, session._manifest_payload())
        try:
            for name in machines:
                await session.snapshot(machine=name, label="restored")
        except BaseException:
            for runtime in list(runtimes.values()):
                try:
                    await executor.session.dispose(runtime, retain=False)
                except BaseException:
                    pass
            shutil.rmtree(directory, ignore_errors=True)
            raise
        session._write_replay_header()
        return session

    @classmethod
    async def attach(
        cls, session_id: str | None = None, *, target_dir: Path = Path("target")
    ) -> StudioSession:
        store = StudioSessionStore(target_dir)
        sid = store.active(session_id)
        manifest = store.read(sid)
        if manifest.get("status") != "active":
            raise StudioError(f"studio session is not active: {sid}")
        machines: dict[str, _StudioMachine] = {}
        raw_machines = manifest.get("machines")
        if not isinstance(raw_machines, dict):
            raise StudioError(f"studio session has no machines: {sid}")
        for name, raw in raw_machines.items():
            if not isinstance(name, str) or not isinstance(raw, dict):
                continue
            artifacts = _artifacts_from_manifest(target_dir, raw)
            vnc = await VncClient.connect(artifacts.vnc_socket, timeout=10)
            dummy = SimpleNamespace(artifacts=artifacts, process=None)
            guest = Guest(
                source_id=str(raw.get("source_id", name)),
                running=dummy,  # type: ignore[arg-type]
                vnc=vnc,
                control_port=0,
                record=False,
            )
            machines[name] = _StudioMachine(
                name=name,
                source_id=guest.source_id,
                artifacts=artifacts,
                guest=guest,
                pid=int(raw["pid"]) if isinstance(raw.get("pid"), int) else None,
            )
            _link_serial_log(machines[name], store.directory(sid) / "serial")
        return cls(store=store, session_id=sid, manifest=manifest, machines=machines)

    async def close_connections(self) -> None:
        for machine in self.machines.values():
            try:
                await machine.guest.close()
            except (OSError, ConnectionError):
                pass

    async def stop(self) -> None:
        async with self._lock:
            for machine in self.machines.values():
                if machine.pid is None:
                    continue
                try:
                    os.killpg(machine.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            self.manifest["status"] = "stopped"
            self.manifest["stopped"] = time.time()
            self.store.write(self.session_id, self._manifest_payload())
        await self.close_connections()

    async def snapshot(
        self, *, machine: str = "desktop", label: str = "snapshot",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            self._check_revision(expected_revision)
            selected = self._machine(machine)
            frame = await selected.guest.screen.snapshot()
            frame_id, path = self._save_frame(selected.name, label, frame)
            self._append_event(
                StudioEvent(self._next_event(), selected.name, "snapshot", {"label": label},
                            None, frame_id, 0.0)
            )
            return self._frame_response(frame_id, path, frame)

    async def click(self, x: int, y: int, *, machine: str = "desktop",
                    expected_revision: int | None = None) -> dict[str, Any]:
        return await self._control(machine, "click", {"x": x, "y": y},
                                   lambda guest: guest.screen.click(x, y),
                                   expected_revision=expected_revision)

    async def type(self, text: str, *, machine: str = "desktop",
                   expected_revision: int | None = None) -> dict[str, Any]:
        return await self._control(machine, "type", {"text": text},
                                   lambda guest: guest.keyboard.type(text),
                                   expected_revision=expected_revision)

    async def key(self, key: str, *, machine: str = "desktop",
                  expected_revision: int | None = None) -> dict[str, Any]:
        return await self._control(machine, "key", {"key": key},
                                   lambda guest: guest.keyboard.press(key),
                                   expected_revision=expected_revision)

    async def wait_stable(
        self, *, timeout: float = 30, machine: str = "desktop",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            self._check_revision(expected_revision)
            selected = self._machine(machine)
            started = time.monotonic()
            previous: Frame | None = None
            stable = 0
            while time.monotonic() - started < timeout:
                current = await selected.guest.screen.snapshot()
                if previous is not None and current.changed_pixels(previous) == 0:
                    stable += 1
                    if stable >= 2:
                        frame_id, path = self._save_frame(selected.name, "stable", current)
                        self._append_event(StudioEvent(
                            self._next_event(), selected.name, "wait_stable",
                            {"timeout": timeout}, None, frame_id,
                            time.monotonic() - started,
                        ))
                        return self._frame_response(frame_id, path, current)
                else:
                    stable = 0
                previous = current
                await asyncio.sleep(0.25)
            raise StudioError(f"timed out waiting for a stable screen after {timeout:g}s")

    async def serial(self, *, machine: str = "desktop", lines: int = 100) -> dict[str, Any]:
        async with self._lock:
            selected = self._machine(machine)
            if lines < 1:
                raise StudioError("serial lines must be positive")
            path = selected.artifacts.serial_log
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                raise StudioError(f"cannot read serial log {path}: {error}") from error
            result = "\n".join(content.splitlines()[-lines:])
            self._append_event(StudioEvent(
                self._next_event(), selected.name, "serial", {"lines": lines},
                None, None, 0.0,
            ))
            return {"session": self.session_id, "machine": selected.name,
                    "path": str(path), "text": result, "revision": self._event_id}

    async def crop(
        self, frame_id: int, x: int, y: int, width: int, height: int, *, label: str = "fixture"
    ) -> dict[str, Any]:
        matches = list(self.frames_directory.glob(f"{frame_id:06d}-*.png"))
        if not matches:
            raise StudioError(f"frame not found: {frame_id}")
        frame = Frame.read_png(matches[0]).crop(x, y, width, height)
        fixture = self.fixtures_directory / f"{_safe(label)}.png"
        frame.write_png(fixture)
        self._append_event(StudioEvent(
            self._next_event(), "", "crop",
            {"frame_id": frame_id, "x": x, "y": y, "width": width, "height": height,
             "label": label}, None, frame_id, 0.0,
        ))
        return {"frame_id": frame_id, "fixture": str(fixture), "width": width, "height": height}

    def emit(self, name: str = "explore") -> dict[str, str]:
        output_root = self.store.target_dir / "studio" / "generated"
        output_root.mkdir(parents=True, exist_ok=True)
        output = output_root / f"{_safe(name)}.py"
        assets = output_root / "assets" / _safe(self.session_id)
        assets.mkdir(parents=True, exist_ok=True)
        events = self._events()
        lines = [
            "from pathlib import Path",
            "from catsnail import Guest, add_os, add_test, use",
            "",
            "# Replace this declaration with the source used by the original test file.",
            "SOURCE = add_os(...)",
            "",
            "@add_test",
            "async def test_studio_exploration(guest: Guest = use(SOURCE)) -> None:",
        ]
        body = 0
        for event in events:
            if event.get("machine") != "desktop" or event.get("result") != "ok":
                continue
            action = event.get("action")
            raw_args = event.get("args")
            args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
            if action == "click":
                lines.append(f"    await guest.screen.click({int(args.get('x', 0))}, {int(args.get('y', 0))})")
                body += 1
            elif action == "type":
                lines.append(f"    await guest.keyboard.type({str(args.get('text', '')).__repr__()})")
                body += 1
            elif action == "key":
                lines.append(f"    await guest.keyboard.press({str(args.get('key', ''))!r})")
                body += 1
            after = event.get("after")
            if isinstance(after, int):
                matches = list(self.frames_directory.glob(f"{after:06d}-*.png"))
                if matches:
                    asset_name = f"{event.get('id', after):04d}-{_safe(str(action))}.png"
                    asset = assets / asset_name
                    asset.write_bytes(matches[0].read_bytes())
                    lines.append(
                        "    await guest.screen.assert_screen("
                        f"Path('assets/{_safe(self.session_id)}/{asset_name}'), "
                        "x=0, y=0, timeout=30, label="
                        f"{str(action)!r})"
                    )
                    body += 1
        if body == 0:
            lines.append("    pass")
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report = output_root / f"{_safe(name)}.md"
        report.write_text(
            f"# Catsnail studio session {self.session_id}\n\n"
            f"- Checkpoint: `{self.manifest.get('checkpoint')}`\n"
            f"- Events: {len(events)}\n- Generated: `{output}`\n",
            encoding="utf-8",
        )
        return {"test": str(output), "report": str(report), "assets": str(assets)}

    async def _control(
        self,
        machine: str,
        action: str,
        args: dict[str, Any],
        operation: Callable[[Guest], Awaitable[None]],
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            self._check_revision(expected_revision)
            selected = self._machine(machine)
            started = time.monotonic()
            before_frame = await selected.guest.screen.snapshot()
            before, _ = self._save_frame(selected.name, f"before-{action}", before_frame)
            try:
                await operation(selected.guest)
                after_frame = await selected.guest.screen.snapshot()
            except BaseException as error:
                event = StudioEvent(self._next_event(), selected.name, action, args,
                                    before, None, time.monotonic() - started,
                                    result="error", error=str(error))
                self._append_event(event)
                raise
            after, path = self._save_frame(selected.name, action, after_frame)
            self._append_event(StudioEvent(
                self._next_event(), selected.name, action, args, before, after,
                time.monotonic() - started,
            ))
            self._write_replay_line(action, selected.name, args)
            return self._frame_response(after, path, after_frame)

    def _check_revision(self, expected: int | None) -> None:
        if expected is not None and expected != self._event_id:
            raise StudioError(
                f"studio session revision changed: expected {expected}, "
                f"current {self._event_id}"
            )

    def _machine(self, name: str) -> _StudioMachine:
        try:
            return self.machines[name]
        except KeyError as error:
            raise StudioError(
                f"unknown machine {name!r}; choose one of {', '.join(self.machines)}"
            ) from error

    def _save_frame(self, machine: str, label: str, frame: Frame) -> tuple[int, Path]:
        self._frame_id += 1
        path = self.frames_directory / f"{self._frame_id:06d}-{_safe(machine)}-{_safe(label)}.png"
        frame.write_png(path)
        self.manifest["frame_id"] = self._frame_id
        self.store.write(self.session_id, self._manifest_payload())
        return self._frame_id, path

    def _frame_response(self, frame_id: int, path: Path, frame: Frame) -> dict[str, Any]:
        return {
            "session": self.session_id,
            "frame_id": frame_id,
            "image": str(path),
            "width": frame.width,
            "height": frame.height,
            "sha256": hashlib.sha256(frame.rgba).hexdigest(),
            "revision": self._event_id,
        }

    def _next_event(self) -> int:
        self._event_id += 1
        self.manifest["event_id"] = self._event_id
        return self._event_id

    def _append_event(self, event: StudioEvent) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event.as_json(), ensure_ascii=False) + "\n")
        self.store.write(self.session_id, self._manifest_payload())

    def _events(self) -> list[dict[str, Any]]:
        if not self.events_path.is_file():
            return []
        events: list[dict[str, Any]] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    def _manifest_payload(self) -> dict[str, Any]:
        return {
            **self.manifest,
            "frame_id": self._frame_id,
            "event_id": self._event_id,
            "machines": {
                name: _machine_manifest(machine) for name, machine in self.machines.items()
            },
        }

    def _write_replay_header(self) -> None:
        self.replay_path.write_text(
            "#!/bin/sh\nset -eu\n"
            f"# Catsnail studio session {self.session_id}\n"
            f"# Events are stored in {shlex.quote(str(self.events_path))}\n",
            encoding="utf-8",
        )
        self.replay_path.chmod(0o755)

    def _write_replay_line(self, action: str, machine: str, args: Mapping[str, Any]) -> None:
        with self.replay_path.open("a", encoding="utf-8") as stream:
            if action == "click":
                stream.write(f"# {machine}: click {args.get('x')} {args.get('y')}\n")
            elif action == "type":
                stream.write(f"# {machine}: type {args.get('text')!r}\n")
            elif action == "key":
                stream.write(f"# {machine}: key {args.get('key')}\n")


class StudioRpcServer:
    """Small newline-delimited JSON RPC server for an attached session."""

    def __init__(self, session: StudioSession) -> None:
        self.session = session
        self.socket_path = session.directory / "studio.sock"
        self.session.manifest["rpc_socket"] = str(self.socket_path)
        self.session.store.write(self.session.session_id, self.session._manifest_payload())
        self._server: asyncio.AbstractServer | None = None

    async def serve_forever(self) -> None:
        self.socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle, path=self.socket_path
        )
        async with self._server:
            await self._server.serve_forever()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                try:
                    request = json.loads(line)
                    result = await self.dispatch(request)
                    response = {"ok": True, "result": result}
                except (Exception,) as error:
                    response = {"ok": False, "error": str(error)}
                writer.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def dispatch(self, request: Mapping[str, Any]) -> Any:
        method = request.get("method")
        params = request.get("params")
        values = params if isinstance(params, dict) else {}
        if method == "screen.snapshot":
            return await self.session.snapshot(
                machine=str(values.get("machine", "desktop")),
                expected_revision=_optional_int(values.get("revision")),
            )
        if method == "screen.click":
            return await self.session.click(
                int(values["x"]), int(values["y"]),
                machine=str(values.get("machine", "desktop")),
                expected_revision=_optional_int(values.get("revision")),
            )
        if method == "keyboard.type":
            return await self.session.type(
                str(values["text"]), machine=str(values.get("machine", "desktop")),
                expected_revision=_optional_int(values.get("revision")),
            )
        if method == "keyboard.press":
            return await self.session.key(
                str(values["key"]), machine=str(values.get("machine", "desktop")),
                expected_revision=_optional_int(values.get("revision")),
            )
        if method == "screen.wait_stable":
            return await self.session.wait_stable(
                timeout=float(values.get("timeout", 30)),
                machine=str(values.get("machine", "desktop")),
                expected_revision=_optional_int(values.get("revision")),
            )
        if method == "serial.read":
            return await self.session.serial(
                machine=str(values.get("machine", "desktop")),
                lines=int(values.get("lines", 100)),
            )
        if method == "screen.crop":
            return await self.session.crop(
                int(values["frame_id"]), int(values["x"]), int(values["y"]),
                int(values["width"]), int(values["height"]),
                label=str(values.get("label", "fixture")),
            )
        if method == "session.emit":
            return self.session.emit(str(values.get("name", "explore")))
        raise StudioError(f"unknown studio RPC method: {method!r}")


def _machine_name(node: TestNode[Any], source: Any, index: int) -> str:
    from .graph.executor import _source_parameter

    parameter = _source_parameter(node, source)
    return parameter if parameter != "guest" or index == 1 else f"machine-{index}"


def _machine_manifest(machine: _StudioMachine) -> dict[str, Any]:
    return {
        "source_id": machine.source_id,
        "pid": machine.pid,
        "artifacts": str(machine.artifacts.directory),
        "debug": str(machine.artifacts.debug_directory),
        "release": str(machine.artifacts.release_directory),
        "vnc_socket": str(machine.artifacts.vnc_socket),
        "qmp_socket": str(machine.artifacts.qmp_socket),
    }


def _link_serial_log(machine: _StudioMachine, serial_directory: Path) -> None:
    serial_directory.mkdir(parents=True, exist_ok=True)
    link = serial_directory / f"{_safe(machine.name)}.log"
    link.unlink(missing_ok=True)
    try:
        link.symlink_to(machine.artifacts.serial_log.resolve())
    except OSError:
        # Some filesystems do not allow symlinks; retain a small copy instead.
        if machine.artifacts.serial_log.is_file():
            link.write_bytes(machine.artifacts.serial_log.read_bytes())


def _artifacts_from_manifest(target_dir: Path, value: Mapping[str, Any]) -> RunArtifacts:
    required = ["artifacts", "debug", "release", "vnc_socket", "qmp_socket"]
    if any(not isinstance(value.get(item), str) for item in required):
        raise StudioError("studio machine manifest is incomplete")
    directory = Path(str(value["artifacts"]))
    return RunArtifacts(
        target_dir=target_dir,
        run_id=hashlib.sha256(os.fsencode(str(directory))).hexdigest()[:16],
        directory=directory,
        debug_directory=Path(str(value["debug"])),
        release_directory=Path(str(value["release"])),
        serial_log=directory / "serial.log",
        serial_socket=directory / "serial.sock",
        stderr_log=directory / "qemu.stderr.log",
        qmp_socket=Path(str(value["qmp_socket"])),
        vnc_socket=Path(str(value["vnc_socket"])),
        command_json=directory / "qemu-command.json",
        reproduce_script=directory / "reproduce.sh",
    )


def _manifest_is_live(payload: Mapping[str, Any]) -> bool:
    machines = payload.get("machines")
    if not isinstance(machines, dict) or not machines:
        return True
    for raw in machines.values():
        if not isinstance(raw, dict):
            return False
        socket_path = raw.get("vnc_socket")
        pid = raw.get("pid")
        if not isinstance(socket_path, str) or not Path(socket_path).exists():
            return False
        if isinstance(pid, int):
            try:
                os.kill(pid, 0)
            except OSError:
                return False
    return True


def _safe(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", value).strip(" .")
    return value or "item"


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise StudioError("revision must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise StudioError("revision must be an integer") from error
