"""Concurrent scheduling for an already-collected Catsnail test graph."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypeAlias

from .api import TestNode
from .executor import TestExecutionError
from ..progress import EventSink, ProgressReporter, event


RunReport: TypeAlias = list[
    tuple[str, Path, TestNode[Any], TestExecutionError | None]
]
ScheduledTarget: TypeAlias = tuple[Path, Any, TestNode[Any]]
ScheduledNode: TypeAlias = tuple[Path, Any, TestNode[Any]]
ScheduledKey: TypeAlias = tuple[Path, str]


async def schedule_targets(
    targets: list[ScheduledTarget],
    *,
    jobs: int,
    keep_going: bool,
    record: bool,
    reporter: ProgressReporter,
    progress_nodes: Iterable[TestNode[Any]],
    emit_progress: EventSink,
) -> tuple[int, RunReport]:
    """Run graph nodes once, respecting checkpoint dependencies and job limits."""

    async def run_target(
        candidate: Path, executor: Any, target: TestNode[Any]
    ) -> tuple[Path, TestNode[Any], Any, TestExecutionError | None]:
        try:
            return candidate, target, await executor.run(target), None
        except TestExecutionError as error:
            return candidate, target, None, error

    # Each checkpoint is a first-class scheduled node, including an internal
    # setup. This prevents a shared prerequisite from being run independently
    # by each of its descendants.
    executors = {candidate: executor for candidate, executor, _ in targets}
    scheduled: dict[ScheduledKey, ScheduledNode] = {}

    def add_closure(candidate: Path, node: TestNode[Any]) -> None:
        key = (candidate, node.id)
        if key in scheduled:
            return
        for dependency in node.dependencies.values():
            if isinstance(dependency.node, TestNode):
                add_closure(candidate, dependency.node)
        scheduled[key] = (candidate, executors[candidate], node)

    for candidate, _, target in targets:
        add_closure(candidate, target)
    pending = dict(scheduled)

    def scheduled_dependencies(candidate: Path, node: TestNode[Any]) -> set[ScheduledKey]:
        return {
            (candidate, dependency.node.id)
            for dependency in node.dependencies.values()
            if isinstance(dependency.node, TestNode)
            and (candidate, dependency.node.id) in scheduled
        }

    dependencies = {
        key: scheduled_dependencies(candidate, target)
        for key, (candidate, _, target) in pending.items()
    }
    completed: set[ScheduledKey] = set()
    failed: set[ScheduledKey] = set()
    unexpected_passes: set[ScheduledKey] = set()
    running: dict[asyncio.Task[Any], ScheduledKey] = {}
    report: RunReport = []

    try:
        while pending or running:
            for key, (candidate, executor, target) in list(pending.items()):
                if len(running) == jobs:
                    break
                if not dependencies[key].issubset(completed):
                    continue
                running[asyncio.create_task(run_target(candidate, executor, target))] = key
                del pending[key]

            if not running:
                raise RuntimeError("selected tests have an unresolved dependency")

            done, _ = await asyncio.wait(
                running,
                timeout=1 if reporter.live else None,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                reporter.refresh()
                continue
            for task in done:
                key = running.pop(task)
                candidate, target, result, error = task.result()
                if error is not None:
                    if error.outcome == "xfail":
                        report.append(("XFAIL", candidate, target, error))
                        if error.failed_node is not target:
                            emit_progress(
                                event(
                                    "xfail",
                                    target,
                                    target=True,
                                    detail=target.expected_failure or str(error),
                                )
                            )
                        completed.add(key)
                        cancel_failed_descendants(
                            key,
                            error.failed_node or target,
                            pending,
                            dependencies,
                            progress_nodes,
                            emit_progress,
                            report,
                        )
                        continue
                    status = "XPASS" if error.outcome == "xpass" else "FAIL"
                    report.append((status, candidate, target, error))
                    emit_progress(
                        event(
                            "xpass" if error.outcome == "xpass" else "failed",
                            target,
                            target=True,
                            detail=str(error),
                        )
                    )
                    if error.outcome == "xpass":
                        unexpected_passes.add(key)
                    else:
                        failed.add(key)
                    if keep_going:
                        cancel_failed_descendants(
                            key,
                            error.failed_node or target,
                            pending,
                            dependencies,
                            progress_nodes,
                            emit_progress,
                            report,
                        )
                        continue
                    for other in running:
                        other.cancel()
                    cancel_unfinished_nodes(
                        progress_nodes,
                        emit_progress,
                        detail="cancelled after a test failure",
                    )
                    await asyncio.gather(*done, *running, return_exceptions=True)
                    return 1, report
                if result is None:
                    raise AssertionError(f"{target.id} completed without a result")
                completed.add(key)
                report.append(("PASS", candidate, target, None))
                emit_progress(
                    event(
                        "passed",
                        target,
                        target=True,
                        completed=(node.function.__name__ for node in result.completed),
                    )
                )
                if record and not reporter.live:
                    for artifacts in result.artifacts:
                        print(f"Recording: {artifacts / 'recording.mp4'}")
        return 1 if failed or unexpected_passes else 0, report
    finally:
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)


def cancel_failed_descendants(
    failed_key: ScheduledKey,
    failed_node: TestNode[Any],
    pending: dict[ScheduledKey, ScheduledNode],
    dependencies: dict[ScheduledKey, set[ScheduledKey]],
    graph_nodes: Iterable[TestNode[Any]],
    reporter: EventSink,
    report: RunReport,
) -> None:
    """Cancel pending targets whose checkpoint state depends on a failure."""

    blocked = {failed_key}
    while True:
        descendants = {
            key
            for key, prerequisites in dependencies.items()
            if key in pending and prerequisites & blocked
        }
        additions = descendants - blocked
        if not additions:
            break
        blocked.update(additions)
    for key in blocked - {failed_key}:
        candidate, _, target = pending.pop(key)
        report.append(("CANCEL", candidate, target, None))
    cancel_descendant_nodes(
        failed_node,
        graph_nodes,
        reporter,
        detail="depends on a failed test",
    )


def cancel_descendant_nodes(
    failed: TestNode[Any],
    nodes: Iterable[TestNode[Any]],
    reporter: EventSink,
    *,
    detail: str,
) -> None:
    """Mark direct and indirect graph consumers of ``failed`` as cancelled."""

    all_nodes = tuple(nodes)
    children: dict[str, list[TestNode[Any]]] = {}
    for node in all_nodes:
        for dependency in node.dependencies.values():
            parent = dependency.node
            if isinstance(parent, TestNode):
                children.setdefault(parent.id, []).append(node)

    cancelled: set[str] = set()
    pending = [failed.id]
    while pending:
        parent_id = pending.pop()
        for child in children.get(parent_id, ()):
            if child.id not in cancelled:
                cancelled.add(child.id)
                pending.append(child.id)
    for node in all_nodes:
        if node.id in cancelled:
            reporter(event("cancelled", node, detail=detail))


def cancel_unfinished_nodes(
    nodes: Iterable[TestNode[Any]], reporter: EventSink, *, detail: str
) -> None:
    """Mark remaining visible graph nodes cancelled after an explicit stop."""

    for node in nodes:
        reporter(event("cancelled", node, detail=detail))
