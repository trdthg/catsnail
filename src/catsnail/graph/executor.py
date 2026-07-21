"""Execute a typed graph with durable QEMU checkpoints and isolated branches."""

from __future__ import annotations

import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .api import (
    GraphDefinitionError,
    Node,
    Source,
    TestGraph,
    TestNode,
    select_test_targets,
)
from .checkpoint import (
    CheckpointCoordinator,
    CheckpointError,
    CheckpointStore,
    checkpoint_key,
    checkpoint_origin,
    source_file_name,
)
from ..guest import Guest
from ..progress import EventSink, RunEvent, event
from ..qemu.network import NetworkPool
from ..qemu.runner import QemuRunError
from ..qemu.session import QemuSession, SourceRuntime


class TestExecutionError(RuntimeError):
    def __init__(
        self,
        target: TestNode[Any],
        cause: BaseException,
        guests: Iterable[Guest],
    ) -> None:
        detail = str(cause) or type(cause).__name__
        super().__init__(f"{target.id} failed: {detail}")
        self.target = target
        self.cause = cause
        artifacts = [guest.artifacts for guest in guests]
        debug = [guest.debug_directory for guest in guests]
        if isinstance(cause, QemuRunError):
            artifacts.append(cause.artifacts.directory)
            debug.append(cause.artifacts.debug_directory)
        self.artifacts = tuple(artifacts)
        self.debug = tuple(debug)


@dataclass(frozen=True)
class TestResult:
    completed: tuple[TestNode[Any], ...]
    artifacts: tuple[Path, ...]


class GraphExecutor:
    """Run one decorated graph target from restored or freshly-created layers."""

    def __init__(
        self,
        graph: TestGraph,
        *,
        target_dir: Path = Path("target"),
        record: bool = True,
        force: bool = False,
        checkpoints: CheckpointStore | None = None,
        coordinator: CheckpointCoordinator | None = None,
        reporter: EventSink | None = None,
    ) -> None:
        self.graph = graph
        self.target_dir = target_dir
        self.record = record
        self.force = force
        self.session = QemuSession(target_dir=target_dir, record=record)
        self.checkpoints = checkpoints or CheckpointStore(target_dir)
        self.coordinator = coordinator or CheckpointCoordinator()
        self.reporter = reporter

    def targets(self, selection: str | None = None) -> list[TestNode[Any]]:
        return select_test_targets(self.graph, selection)

    async def run(self, target: TestNode[Any]) -> TestResult:
        runtimes: dict[str, SourceRuntime] = {}
        values: dict[str, Any] = {}
        completed: list[TestNode[Any]] = []
        recordings: list[Path] = []
        network_pool = NetworkPool()
        failed = False
        active_node: TestNode[Any] | None = None
        try:

            async def resolve_dependency(dependency: Any) -> Any:
                value = await resolve(dependency.node)
                for index in dependency.path:
                    value = value[index]
                return value

            async def resolve(node: Node) -> Any:
                nonlocal active_node
                if node.id in values:
                    return values[node.id]
                if isinstance(node, Source):
                    runtime = await self._start_source(
                        node,
                        network_pool=network_pool,
                        target=target,
                        stage=node,
                    )
                    runtimes[node.id] = runtime
                    values[node.id] = runtime.guest
                    return runtime.guest
                return await resolve_checkpoint(node)

            async def resolve_checkpoint(node: TestNode[Any]) -> Any:
                nonlocal active_node
                key = checkpoint_key(node)
                executed = False
                result: Any = None
                async with self.coordinator.lock_for(key):
                    cached = (
                        None
                        if self.force
                        else self.checkpoints.load(key, name=node.function.__name__)
                    )
                    if cached is None:
                        active_node = node
                        self._emit(event("started", node))
                        kwargs = {
                            parameter: await resolve_dependency(dependency)
                            for parameter, dependency in node.dependencies.items()
                        }
                        self._set_release_directories(node, kwargs)
                        returned = await node.function(**kwargs)
                        if returned is not None:
                            raise GraphDefinitionError(
                                f"{node.id} returned a value; @add_test functions "
                                "must use implicit checkpoint state"
                            )
                        result = _implicit_output(kwargs)
                        completed.append(node)
                        cached = await self._publish_checkpoint(
                            node, result, runtimes, recordings
                        )
                        executed = True
                        self._emit(event("checkpoint_saved", node))
                    if node is target:
                        if not executed:
                            self._emit(event("checkpoint_restored", node))
                            active_node = None
                            return None
                        self._emit(event("passed", node))
                        active_node = None
                        return result
                    active_node = node
                    restored = await self._restore_checkpoint(
                        node,
                        cached,
                        runtimes,
                        recordings,
                        network_pool,
                        target=target,
                    )
                    values.update(_replace_guest_values(values, restored[1]))
                    values[node.id] = restored[0]
                    self._emit(event("checkpoint_restored", node))
                    if executed:
                        self._emit(event("passed", node))
                    active_node = None
                    return restored[0]

            await resolve(target)
            artifacts = tuple(recordings)
            if self.record:
                artifacts += tuple(
                    runtime.guest.release_directory for runtime in runtimes.values()
                )
            return TestResult(
                completed=tuple(completed),
                artifacts=artifacts,
            )
        except BaseException as error:
            failed = True
            if active_node is not None:
                self._emit(event("failed", active_node, detail=str(error)))
            guests = [runtime.guest for runtime in runtimes.values()]
            await _capture_failures(guests)
            _write_failure_details(guests, error)
            await self._save_failure_states(runtimes.values())
            raise TestExecutionError(target, error, guests) from error
        finally:
            for runtime in reversed(list(runtimes.values())):
                await self._dispose_runtime(runtime, retain=failed or self.record)

    def _emit(self, update: RunEvent) -> None:
        if self.reporter is not None:
            self.reporter(update)

    def _set_release_directories(
        self, node: TestNode[Any], values: Mapping[str, Any]
    ) -> None:
        inputs = [
            (parameter, _guest_values(value))
            for parameter, value in values.items()
            if _guest_values(value)
        ]
        machine_count = len(
            {
                guest.source_id
                for _, guests in inputs
                for guest in guests
            }
        )
        directory = (
            self.target_dir
            / "release"
            / _test_file_directory(node)
            / _safe_component(node.function.__name__)
        )
        shutil.rmtree(directory, ignore_errors=True)
        for parameter, guests in inputs:
            for index, guest in enumerate(guests, start=1):
                if machine_count == 1:
                    guest.set_release_directory(directory)
                    continue
                suffix = parameter if len(guests) == 1 else f"{parameter}-{index}"
                guest.set_release_directory(directory / _safe_component(suffix))

    async def _publish_checkpoint(
        self,
        node: TestNode[Any],
        result: Any,
        runtimes: dict[str, SourceRuntime],
        recordings: list[Path],
    ) -> dict[str, Any]:
        descriptor = _describe_output(result)
        source_ids = list(dict.fromkeys(_output_sources(descriptor)))
        if not source_ids:
            raise GraphDefinitionError(
                f"{node.id} returned a checkpoint without any Guest values"
            )
        missing = [source_id for source_id in source_ids if source_id not in runtimes]
        if missing:
            raise GraphDefinitionError(
                f"{node.id} returned guests not owned by this test: {missing}"
            )

        key = checkpoint_key(node)
        staging = self.checkpoints.staging_directory(key, name=node.function.__name__)
        machines: list[dict[str, str]] = []
        try:
            for source_id in source_ids:
                runtime = runtimes[source_id]
                disk_name = source_file_name(source_id, "qcow2")
                state_name = source_file_name(source_id, "state")
                state_path = staging / state_name
                await self.session.save_state(runtime, state_path)
                await self._dispose_runtime(runtime, retain=True)
                if runtime.running.state_disk is None:
                    raise CheckpointError(f"{node.id} has no writable runtime disk")
                shutil.move(str(runtime.running.state_disk), staging / disk_name)
                if not self.record:
                    shutil.rmtree(
                        runtime.running.artifacts.directory, ignore_errors=True
                    )
                runtimes.pop(source_id)
                machines.append(
                    {"source": source_id, "disk": disk_name, "state": state_name}
                )
                if self.record:
                    recordings.append(runtime.guest.release_directory)
            manifest = {
                "node": node.id,
                "origin": checkpoint_origin(node),
                "output": descriptor,
                "machines": machines,
            }
            return self.checkpoints.publish(
                key, staging, manifest, name=node.function.__name__
            )
        except BaseException:
            self.checkpoints.discard(staging)
            raise

    async def _restore_checkpoint(
        self,
        node: TestNode[Any],
        cached: Mapping[str, Any],
        runtimes: dict[str, SourceRuntime],
        recordings: list[Path],
        network_pool: NetworkPool,
        *,
        target: TestNode[Any],
    ) -> tuple[Any, dict[str, Guest]]:
        directory = cached.get("directory")
        machines = cached.get("machines")
        descriptor = cached.get("output")
        if (
            not isinstance(directory, Path)
            or not isinstance(machines, list)
            or not isinstance(descriptor, dict)
        ):
            raise CheckpointError(f"checkpoint for {node.id} has invalid metadata")
        sources = {source.id: source for source in _source_dependencies(node)}
        restored: dict[str, Guest] = {}
        for machine in machines:
            if not isinstance(machine, dict):
                raise CheckpointError(
                    f"checkpoint for {node.id} has invalid guest metadata"
                )
            source_id = machine.get("source")
            disk_name = machine.get("disk")
            state_name = machine.get("state")
            if (
                not isinstance(source_id, str)
                or not isinstance(disk_name, str)
                or not isinstance(state_name, str)
            ):
                raise CheckpointError(
                    f"checkpoint for {node.id} has invalid guest metadata"
                )
            source = sources.get(source_id)
            if source is None:
                raise CheckpointError(
                    f"checkpoint for {node.id} references unknown source {source_id}"
                )
            old_runtime = runtimes.pop(source_id, None)
            if old_runtime is not None:
                await self._dispose_runtime(old_runtime, retain=self.record)
                if self.record:
                    recordings.append(old_runtime.guest.release_directory)
            runtime = await self._start_source(
                source,
                network_pool=network_pool,
                backing=directory / disk_name,
                incoming_state=directory / state_name,
                target=target,
                stage=node,
            )
            runtimes[source_id] = runtime
            restored[source_id] = runtime.guest
        return _restore_output(descriptor, node.result_annotation, restored), restored

    async def _start_source(
        self,
        source: Source[Guest],
        *,
        network_pool: NetworkPool,
        backing: Path | None = None,
        incoming_state: Path | None = None,
        target: TestNode[Any],
        stage: Node,
    ) -> SourceRuntime:
        return await self.session.start(
            source,
            network_pool=network_pool,
            relative_directory=_artifact_directory(target, source, stage),
            backing=backing,
            incoming_state=incoming_state,
        )

    async def _dispose_runtime(self, runtime: SourceRuntime, *, retain: bool) -> None:
        await self.session.dispose(runtime, retain=retain)

    async def _save_failure_states(self, runtimes: Iterable[SourceRuntime]) -> None:
        await self.session.save_failure_states(runtimes)


def _source_dependencies(target: TestNode[Any]) -> list[Source[Guest]]:
    sources: dict[str, Source[Guest]] = {}

    def walk(node: Node) -> None:
        if isinstance(node, Source):
            sources[node.id] = node
            return
        for dependency in node.dependencies.values():
            walk(dependency.node)

    walk(target)
    return list(sources.values())


def _describe_output(value: Any) -> dict[str, Any]:
    if isinstance(value, Guest):
        return {"kind": "guest", "source": value.source_id}
    if isinstance(value, tuple):
        return {
            "kind": "named-tuple" if hasattr(value, "_fields") else "tuple",
            "items": [_describe_output(item) for item in value],
        }
    raise GraphDefinitionError(
        "implicit checkpoint state must contain Guest values"
    )


def _implicit_output(values: Mapping[str, Any]) -> Any:
    """Expose one input directly and multiple inputs as a fixed tuple."""

    output = tuple(values.values())
    if len(output) == 1:
        return output[0]
    if not output:
        raise GraphDefinitionError("a test cannot publish state without inputs")
    return output


def _output_sources(descriptor: Mapping[str, Any]) -> list[str]:
    kind = descriptor.get("kind")
    if kind == "guest":
        source = descriptor.get("source")
        return [source] if isinstance(source, str) else []
    items = descriptor.get("items")
    if kind in {"tuple", "named-tuple"} and isinstance(items, list):
        return [
            source
            for item in items
            if isinstance(item, dict)
            for source in _output_sources(item)
        ]
    return []


def _restore_output(
    descriptor: Mapping[str, Any], result_annotation: Any, guests: Mapping[str, Guest]
) -> Any:
    kind = descriptor.get("kind")
    if kind == "guest":
        source = descriptor.get("source")
        if not isinstance(source, str) or source not in guests:
            raise CheckpointError("checkpoint output references a missing guest")
        return guests[source]
    items = descriptor.get("items")
    if kind not in {"tuple", "named-tuple"} or not isinstance(items, list):
        raise CheckpointError("checkpoint output has an invalid shape")
    values = [
        _restore_output(item, Guest, guests) for item in items if isinstance(item, dict)
    ]
    if len(values) != len(items):
        raise CheckpointError("checkpoint output has an invalid tuple value")
    if kind == "named-tuple":
        try:
            return result_annotation(*values)
        except TypeError as error:
            raise CheckpointError(
                "checkpoint output no longer matches its NamedTuple type"
            ) from error
    return tuple(values)


def _replace_guest_values(
    values: Mapping[str, Any], guests: Mapping[str, Guest]
) -> dict[str, Any]:
    return {key: _replace_guest_value(value, guests) for key, value in values.items()}


def _replace_guest_value(value: Any, guests: Mapping[str, Guest]) -> Any:
    if isinstance(value, Guest):
        return guests.get(value.source_id, value)
    if isinstance(value, tuple):
        items = [_replace_guest_value(item, guests) for item in value]
        return type(value)(*items) if hasattr(value, "_fields") else tuple(items)
    return value


def _guest_values(value: Any) -> list[Guest]:
    if isinstance(value, Guest):
        return [value]
    if isinstance(value, tuple):
        return [guest for item in value for guest in _guest_values(item)]
    return []


async def _capture_failures(guests: Iterable[Guest]) -> None:
    for guest in guests:
        try:
            frame = await guest.screen.capture_result("failure")
            frame.write_png(guest.debug_directory / "last-vnc.png")
        except BaseException:
            pass


def _write_failure_details(guests: Iterable[Guest], error: BaseException) -> None:
    detail = str(error) or type(error).__name__
    for guest in guests:
        try:
            (guest.debug_directory / "failure.txt").write_text(
                detail + "\n", encoding="utf-8"
            )
        except OSError:
            pass


def _artifact_directory(
    target: TestNode[Any], source: Source[Guest], stage: Node
) -> Path:
    """Name artifacts by test, parameter, and logical guest stage."""

    return (
        _test_file_directory(target)
        / _safe_component(target.function.__name__)
        / _source_parameter(target, source)
        / _safe_component(_node_name(stage))
    )


def _test_file_directory(node: TestNode[Any]) -> Path:
    """Return the test file path relative to the current project directory."""

    filename = Path(node.function.__code__.co_filename)
    try:
        if filename.is_file():
            relative = filename.resolve().relative_to(Path.cwd().resolve())
            return Path(
                *(
                    _safe_component(component)
                    for component in relative.with_suffix("").parts
                )
            )
    except (OSError, ValueError):
        pass
    return Path(_module_component(node))


def _module_component(node: TestNode[Any]) -> str:
    """Provide a stable fallback for dynamically-created test functions."""

    module = node.function.__module__
    if module.startswith("catsnail_user_"):
        module = module[len("catsnail_user_") :].rsplit("_", 1)[0]
    return _safe_component(module)


def _source_parameter(target: TestNode[Any], source: Source[Guest]) -> str:
    for parameter, dependency in target.dependencies.items():
        if dependency.node is source:
            return _safe_component(parameter)
        if not isinstance(dependency.node, TestNode):
            continue
        for candidate in _source_dependencies(dependency.node):
            if candidate is source:
                return _safe_component(parameter)
    return "guest"


def _node_name(node: Node) -> str:
    if isinstance(node, Source):
        return "boot"
    return node.function.__name__


def _safe_component(value: str) -> str:
    return (
        "".join(
            character
            if character.isascii() and (character.isalnum() or character in "-_")
            else "_"
            for character in value
        )
        or "guest"
    )
