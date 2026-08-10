from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, cast

import pytest

from catsnail import DebianAdapter, Guest, Machine, add_os, add_test, use
from catsnail.graph.api import (
    Dependency,
    Source,
    TestGraph as CatsnailTestGraph,
    TestNode as CatsnailTestNode,
)
from catsnail.graph.checkpoint import CheckpointStore, checkpoint_key, checkpoint_origin
from catsnail.graph.executor import (
    GraphExecutor,
    TestExecutionError as CatsnailTestExecutionError,
    _artifact_directory,
    _safe_component,
)
from catsnail.qemu.artifacts import RunArtifacts
from catsnail.qemu.runner import QemuProcess, QemuRunError
from catsnail.qemu.session import SourceRuntime
from catsnail.qemu.vnc import VncClient


class _ClosedVnc:
    async def close(self) -> None:
        return None


class _CheckpointExecutor(GraphExecutor):
    """Exercise graph-level cache selection without launching QEMU."""

    def __init__(self, *args: Any, restored: list[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.restored = restored
        self._start_count = 0

    async def _start_source(self, source: Source[Guest], **_: Any) -> SourceRuntime:
        self._start_count += 1
        directory = self.target_dir / "fake-runs" / str(self._start_count)
        directory.mkdir(parents=True, exist_ok=True)
        artifacts = SimpleNamespace(
            directory=directory,
            debug_directory=directory / "debug",
            release_directory=directory / "release",
        )
        artifacts.debug_directory.mkdir(exist_ok=True)
        artifacts.release_directory.mkdir(exist_ok=True)
        running = cast(
            QemuProcess,
            SimpleNamespace(artifacts=artifacts, state_disk=directory / "state.qcow2"),
        )
        guest = Guest(
            source_id=source.id,
            running=running,
            vnc=cast(VncClient, _ClosedVnc()),
            control_port=0,
        )
        return SourceRuntime(source=source, guest=guest, running=running)

    async def _publish_checkpoint(
        self,
        node: CatsnailTestNode[Any],
        result: Any,
        runtimes: dict[str, SourceRuntime],
        recordings: list[Path],
    ) -> dict[str, Any]:
        del runtimes, recordings
        assert isinstance(result, Guest)
        key = checkpoint_key(node)
        staging = self.checkpoints.staging_directory(key, name=node.function.__name__)
        (staging / "guest.qcow2").write_bytes(b"disk")
        (staging / "guest.state").write_bytes(b"state")
        return self.checkpoints.publish(
            key,
            staging,
            {
                "origin": checkpoint_origin(node),
                "machines": [
                    {
                        "source": result.source_id,
                        "disk": "guest.qcow2",
                        "state": "guest.state",
                    }
                ],
                "output": {"kind": "guest", "source": result.source_id},
            },
            name=node.function.__name__,
        )

    async def _restore_checkpoint(
        self,
        node: CatsnailTestNode[Any],
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Any, dict[str, Guest]]:
        self.restored.append(node.function.__name__)
        return await super()._restore_checkpoint(node, *args, **kwargs)

    async def _dispose_runtime(self, runtime: SourceRuntime, *, retain: bool) -> None:
        del retain
        runtime.closed = True

    async def _save_failure_states(self, runtimes: Iterable[SourceRuntime]) -> None:
        del runtimes


class _FailedStartExecutor(GraphExecutor):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.failed_artifacts: RunArtifacts | None = None

    async def _start_source(self, *_: Any, **__: Any) -> SourceRuntime:
        artifacts = RunArtifacts.create(self.target_dir)
        self.failed_artifacts = artifacts
        raise QemuRunError("expected QEMU start failure", artifacts)


def test_publishes_and_loads_a_complete_checkpoint(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "target")
    key = "a" * 64
    name = "test_desktop_login"
    staging = store.staging_directory(key, name=name)
    (staging / "desktop.qcow2").write_bytes(b"disk")
    (staging / "desktop.state").write_bytes(b"memory")

    published = store.publish(
        key,
        staging,
        {
            "machines": [
                {
                    "source": "machine:desktop",
                    "disk": "desktop.qcow2",
                    "state": "desktop.state",
                }
            ],
            "output": {"kind": "guest", "source": "machine:desktop"},
        },
        name=name,
    )

    assert published["directory"] == store.root / "test_desktop_login-aaaaaaaaaaaa"
    assert store.load(key, name=name) == published


def test_checkpoint_and_artifact_names_preserve_unicode_identifiers(
    tmp_path: Path,
) -> None:
    store = CheckpointStore(tmp_path / "target")
    key = "f" * 64
    name = "test_打开新闻"
    staging = store.staging_directory(key, name=name)
    (staging / "desktop.qcow2").write_bytes(b"disk")
    (staging / "desktop.state").write_bytes(b"state")
    published = store.publish(
        key,
        staging,
        {
            "machines": [
                {
                    "source": "machine:desktop",
                    "disk": "desktop.qcow2",
                    "state": "desktop.state",
                }
            ],
            "output": {"kind": "guest", "source": "machine:desktop"},
        },
        name=name,
    )

    assert published["directory"] == store.root / "test_打开新闻-ffffffffffff"
    assert _safe_component("test_打开新闻") == "test_打开新闻"


def test_prunes_legacy_result_only_entries(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "target")
    key = "b" * 64
    directory = store.root / f"test_browser-{key[:12]}"
    directory.mkdir()
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "key": key,
                "kind": "result",
                "origin": str(tmp_path / "test_scenario.py"),
            }
        ),
        encoding="utf-8",
    )

    assert store.load(key, name="test_browser") is None
    assert store.stale_directories({key: str(tmp_path / "test_scenario.py")}) == (
        directory,
    )


def test_prunes_checkpoints_not_reachable_from_the_current_graph(
    tmp_path: Path,
) -> None:
    store = CheckpointStore(tmp_path / "target")
    current = "c" * 64
    stale = "d" * 64

    current_origin = str(tmp_path / "test_current.py")
    other_origin = str(tmp_path / "test_other.py")
    foreign = "e" * 64
    for key, name, origin in (
        (current, "test_current", current_origin),
        (stale, "test_stale", current_origin),
        (foreign, "test_foreign", other_origin),
    ):
        staging = store.staging_directory(key, name=name)
        (staging / "desktop.qcow2").write_bytes(b"disk")
        (staging / "desktop.state").write_bytes(b"memory")
        store.publish(
            key,
            staging,
            {
                "origin": origin,
                "machines": [
                    {
                        "source": "machine:desktop",
                        "disk": "desktop.qcow2",
                        "state": "desktop.state",
                    }
                ],
                "output": {"kind": "guest", "source": "machine:desktop"},
            },
            name=name,
        )
    abandoned = store.root / ".interrupted-publication.tmp"
    abandoned.mkdir()

    assert store.stale_directories({current: current_origin}) == (
        abandoned,
        store.root / "test_stale-dddddddddddd",
    )
    store.prune({current: current_origin})

    assert store.load(current, name="test_current") is not None
    assert store.load(stale, name="test_stale") is None
    assert store.load(foreign, name="test_foreign") is not None
    assert not abandoned.exists()


def test_checkpoint_keys_invalidate_a_and_b_without_invalidating_boot() -> None:
    source = add_os(Machine())

    @add_test
    async def boot(guest: Guest = use(source)) -> None:
        del guest

    @add_test
    async def a(guest: Guest = use(boot)) -> None:
        del guest

    @add_test
    async def b(guest: Guest = use(a)) -> None:
        del guest

    before_boot = checkpoint_key(boot)
    before_a = checkpoint_key(a)
    before_b = checkpoint_key(b)

    async def changed_a(guest: Guest = use(boot)) -> None:
        await DebianAdapter(guest).terminal.run("true")

    changed = replace(a, function=changed_a)
    changed_b = replace(
        b,
        dependencies={"guest": Dependency(changed)},
    )

    assert checkpoint_key(boot) == before_boot
    assert checkpoint_key(changed) != before_a
    assert checkpoint_key(changed_b) != before_b


def test_checkpoint_release_uses_the_test_function_directory(tmp_path: Path) -> None:
    source = add_os(Machine())
    published: list[Path] = []

    @add_test
    async def test_desktop_login(guest: Guest = use(source)) -> None:
        published.append(guest.release_directory)

    executor = _CheckpointExecutor(
        CatsnailTestGraph(roots=[test_desktop_login]),
        target_dir=tmp_path / "target",
        restored=[],
    )

    asyncio.run(executor.run(test_desktop_login))

    assert published == [
        tmp_path
        / "target"
        / "release"
        / "tests"
        / "test_checkpoint"
        / "test_desktop_login"
    ]
    assert executor._start_count == 1


def test_artifact_directories_include_the_test_file_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source = add_os(Machine())
    first_path = tmp_path / "aaa" / "bbb" / "examples" / "minimal.py"
    second_path = tmp_path / "other" / "examples" / "minimal.py"
    for path in (first_path, second_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# test module\n", encoding="utf-8")

    @add_test
    async def test_desktop_boot(guest: Guest = use(source)) -> None:
        del guest

    test_desktop_boot.function.__code__ = test_desktop_boot.function.__code__.replace(
        co_filename=str(first_path)
    )
    first = _artifact_directory(test_desktop_boot, source, source)

    test_desktop_boot.function.__code__ = test_desktop_boot.function.__code__.replace(
        co_filename=str(second_path)
    )
    second = _artifact_directory(test_desktop_boot, source, source)

    assert first.parts[:4] == ("aaa", "bbb", "examples", "minimal")
    assert second.parts[:3] == ("other", "examples", "minimal")
    assert first != second


def test_failed_qemu_start_surfaces_its_reproduction_artifacts(tmp_path: Path) -> None:
    source = add_os(Machine())

    @add_test
    async def terminal(guest: Guest = use(source)) -> None:
        del guest

    executor = _FailedStartExecutor(
        CatsnailTestGraph(roots=[terminal]), target_dir=tmp_path / "target"
    )

    with pytest.raises(CatsnailTestExecutionError, match="expected QEMU") as raised:
        asyncio.run(executor.run(terminal))

    assert executor.failed_artifacts is not None
    assert raised.value.artifacts == (executor.failed_artifacts.directory,)
    assert raised.value.debug == (executor.failed_artifacts.debug_directory,)


def test_failed_terminal_rerun_restores_a_then_rebuilds_it_after_a_changes(
    tmp_path: Path,
) -> None:
    source = add_os(Machine())
    calls: list[str] = []

    @add_test
    async def boot(guest: Guest = use(source)) -> None:
        calls.append("boot")

    @add_test
    async def a(guest: Guest = use(boot)) -> None:
        calls.append("a")

    @add_test
    async def b(guest: Guest = use(a)) -> None:
        del guest
        calls.append("b")
        raise RuntimeError("expected terminal failure")

    target_dir = tmp_path / "target"
    checkpoints = CheckpointStore(target_dir)

    def run_failure(target: CatsnailTestNode[Any]) -> list[str]:
        restored: list[str] = []
        executor = _CheckpointExecutor(
            CatsnailTestGraph(roots=[target]),
            target_dir=target_dir,
            checkpoints=checkpoints,
            restored=restored,
        )
        with pytest.raises(
            CatsnailTestExecutionError, match="expected terminal failure"
        ):
            asyncio.run(executor.run(target))
        return restored

    assert run_failure(b) == ["boot", "a"]
    assert calls == ["boot", "a", "b"]

    # The failed test is re-run from a's durable checkpoint.
    assert run_failure(b) == ["a"]
    assert calls == ["boot", "a", "b", "b"]

    async def changed_a(guest: Guest = use(boot)) -> None:
        calls.append("changed_a")
        del guest

    changed = replace(a, function=changed_a)
    changed_b = replace(b, dependencies={"guest": Dependency(changed)})

    # Changing a makes its checkpoint stale, while boot remains reusable.
    assert run_failure(changed_b) == ["boot", "changed_a"]
    assert calls == ["boot", "a", "b", "b", "changed_a", "b"]


def test_force_rebuilds_a_checkpoint_instead_of_restoring_it(tmp_path: Path) -> None:
    source = add_os(Machine())
    calls: list[str] = []

    @add_test
    async def boot(guest: Guest = use(source)) -> None:
        calls.append("boot")

    @add_test
    async def terminal(guest: Guest = use(boot)) -> None:
        calls.append("terminal")
        del guest

    target_dir = tmp_path / "target"
    checkpoints = CheckpointStore(target_dir)
    graph = CatsnailTestGraph(roots=[terminal])
    asyncio.run(
        _CheckpointExecutor(
            graph,
            target_dir=target_dir,
            checkpoints=checkpoints,
            restored=[],
        ).run(boot)
    )

    restored: list[str] = []
    asyncio.run(
        _CheckpointExecutor(
            graph,
            target_dir=target_dir,
            checkpoints=checkpoints,
            restored=restored,
            force=True,
        ).run(terminal)
    )

    assert calls == ["boot", "boot", "terminal"]
    # The second restoration is the fresh checkpoint's QEMU hand-off to terminal.
    assert restored == ["boot"]
    assert checkpoints.load(checkpoint_key(boot), name="boot") is not None
