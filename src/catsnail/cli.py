"""Command-line entry points for the Python vertical slice."""

from __future__ import annotations

import argparse
import asyncio
import ast
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

from .graph.api import (
    GraphDefinitionError,
    Node,
    Source,
    TestGraph,
    TestNode,
    collect_module_tests,
    select_test_targets,
)
from .graph.checkpoint import (
    CheckpointCoordinator,
    CheckpointStore,
    checkpoint_key,
    checkpoint_origin,
)
from .graph.executor import GraphExecutor, TestExecutionError
from .image import iso_cache_directory
from .progress import ProgressMode, ProgressReporter, collect_test_nodes, event
from .qemu.artifacts import RunArtifacts


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "doctor":
        return _doctor()
    if arguments.command == "prune":
        return _prune(arguments.path, arguments.target_dir)
    if arguments.command == "run":
        return asyncio.run(
            _run(
                arguments.path,
                arguments.target_dir,
                arguments.test,
                arguments.record,
                arguments.jobs,
                arguments.dry_run,
                arguments.progress,
                arguments.force,
            )
        )
    parser.error(f"unknown command: {arguments.command}")
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="catsnail")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("doctor", help="check local QEMU test prerequisites")

    prune = subcommands.add_parser(
        "prune", help="interactively remove stale checkpoints for a test path"
    )
    prune.add_argument("path", type=Path)
    prune.add_argument(
        "--target-dir",
        type=Path,
        default=Path("target"),
        help="checkpoint cache directory",
    )

    run = subcommands.add_parser(
        "run", help="execute collected @add_test tests in QEMU"
    )
    run.add_argument("path", type=Path)
    run.add_argument(
        "--test",
        metavar="PATTERN",
        help="run tests whose function name or collected id matches this Python regex",
    )
    run.add_argument(
        "--target-dir",
        type=Path,
        default=Path("target"),
        help="run artifact output directory",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="validate discovery and graph dependencies without starting QEMU",
    )
    run.add_argument(
        "--force",
        action="store_true",
        help="rebuild prerequisites instead of restoring existing checkpoints",
    )
    recording = run.add_mutually_exclusive_group()
    recording.add_argument(
        "--record",
        dest="record",
        action="store_true",
        help="save step screenshots and recording.mp4 (default)",
    )
    recording.add_argument(
        "--no-record",
        dest="record",
        action="store_false",
        help="do not save step screenshots or a recording.mp4",
    )
    run.set_defaults(record=True)
    run.add_argument(
        "--jobs",
        type=int,
        default=max(1, min(4, os.cpu_count() or 1)),
        help="maximum number of independent tests to run at once",
    )
    run.add_argument(
        "--progress",
        choices=("auto", "tree", "plain"),
        default="auto",
        help="progress output: live tree for terminals, or append-only plain logs",
    )

    return parser


def _doctor() -> int:
    """Report local QEMU prerequisites without creating any test state."""

    print("Catsnail doctor")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    print(f"ISO cache: {iso_cache_directory()}")
    print(f"Default target: {Path('target').resolve()}")

    failures = 0
    for name in ("qemu-system-x86_64", "qemu-img"):
        executable = shutil.which(name)
        if executable is None:
            print(f"ERROR {name}: not found on PATH")
            failures += 1
            continue
        version = _tool_version(executable)
        if version is None:
            print(f"ERROR {name}: could not run {executable} --version")
            failures += 1
            continue
        print(f"OK {name}: {version} ({executable})")

    kvm = Path("/dev/kvm")
    if kvm.exists() and os.access(kvm, os.R_OK | os.W_OK):
        print("OK /dev/kvm: accessible")
    elif kvm.exists():
        print("WARN /dev/kvm: not accessible; QEMU will fall back to TCG")
    else:
        print("WARN /dev/kvm: not found; QEMU will use TCG")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print("WARN ffmpeg: not found; recordings will retain PNG keyframes only")
    else:
        version = _tool_version(ffmpeg, argument="-version")
        if version is None:
            print(f"WARN ffmpeg: could not run {ffmpeg} --version")
        else:
            print(f"OK ffmpeg: {version} ({ffmpeg})")

    return 1 if failures else 0


def _tool_version(executable: str, *, argument: str = "--version") -> str | None:
    try:
        completed = subprocess.run(
            [executable, argument],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = completed.stdout or completed.stderr
    if completed.returncode != 0 or not output.strip():
        return None
    return output.splitlines()[0]


def _load_module(path: Path) -> ModuleType:
    resolved = path.resolve()
    module_name, import_root = _module_location(resolved)
    if import_root is not None and str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))
    specification = importlib.util.spec_from_file_location(module_name, resolved)
    if specification is None or specification.loader is None:
        raise GraphDefinitionError(f"cannot load Python module from {resolved}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    specification.loader.exec_module(module)
    return module


def _module_location(path: Path) -> tuple[str, Path | None]:
    """Use a dotted module name for package tests so relative imports work."""

    package_parts = [path.stem]
    directory = path.parent
    import_root: Path | None = None
    while (directory / "__init__.py").is_file():
        package_parts.insert(0, directory.name)
        import_root = directory.parent
        directory = directory.parent
    if import_root is not None:
        return ".".join(package_parts), import_root
    digest = hashlib.sha256(os.fsencode(path)).hexdigest()[:16]
    return f"catsnail_user_{path.stem}_{digest}", None


def _prune(path: Path, target_dir: Path) -> int:
    """Interactively discard checkpoints invalidated by the selected tests."""

    try:
        current_checkpoints: dict[str, str] = {}
        for candidate in _discover_test_paths(path):
            graph = collect_module_tests(_load_module(candidate))
            current_checkpoints.update(_checkpoint_hashes(graph.validate()))
    except (GraphDefinitionError, OSError, RuntimeError) as error:
        print(f"catsnail prune failed: {error}", file=sys.stderr)
        print(f"Reproduce: catsnail prune {path}", file=sys.stderr)
        return 1

    checkpoints = CheckpointStore(target_dir)
    stale = checkpoints.stale_directories(current_checkpoints)
    if not stale:
        print("No stale checkpoints found.")
        return 0

    print(f"Found {len(stale)} stale checkpoint(s):")
    for directory in stale:
        print(f"  {directory}")
    try:
        response = input("Delete these checkpoints? [y/N] ")
    except EOFError:
        response = ""
    if response.strip().lower() != "y":
        print("Prune cancelled.")
        return 0

    checkpoints.remove(stale)
    print(f"Deleted {len(stale)} checkpoint(s).")
    return 0


async def _run(
    path: Path,
    target_dir: Path,
    selection: str | None,
    record: bool,
    jobs: int,
    dry_run: bool,
    progress: ProgressMode = "auto",
    force: bool = False,
) -> int:
    if jobs < 1:
        print("--jobs must be at least 1", file=sys.stderr)
        return 2
    try:
        paths = _discover_test_paths(path)
    except (GraphDefinitionError, OSError, RuntimeError) as error:
        print(f"catsnail run failed before QEMU started: {error}", file=sys.stderr)
        print(f"Reproduce: catsnail run {path}", file=sys.stderr)
        return 1

    collected: list[tuple[Path, TestGraph, TestNode[Any]]] = []
    current_checkpoints: dict[str, str] = {}
    test_count = 0
    source_count = 0
    try:
        for candidate in paths:
            module = _load_module(candidate)
            graph = collect_module_tests(module)
            nodes = graph.validate()
            test_count += sum(isinstance(node, TestNode) for node in nodes)
            source_count += sum(isinstance(node, Source) for node in nodes)
            current_checkpoints.update(_checkpoint_hashes(nodes))
            try:
                targets = select_test_targets(graph, selection)
            except GraphDefinitionError as error:
                if selection is not None and str(error).startswith(
                    "no collected test matches"
                ):
                    continue
                raise
            collected.extend((candidate, graph, target) for target in targets)
    except (GraphDefinitionError, OSError, RuntimeError) as error:
        print(f"catsnail run failed before QEMU started: {error}", file=sys.stderr)
        print(f"Reproduce: catsnail run {path}", file=sys.stderr)
        return 1

    if dry_run:
        if selection is not None and not collected:
            print(f"No collected test matches {selection!r}", file=sys.stderr)
            return 1
        ProgressReporter(
            collect_test_nodes(target for _, _, target in collected), mode="plain"
        ).render_graph()
        if len(paths) == 1:
            print(
                f"Validated {test_count} tests and {source_count} machine sources "
                f"from {paths[0]}"
            )
        else:
            print(
                f"Validated {test_count} tests and {source_count} machine sources "
                f"from {len(paths)} files in {path}"
            )
        return 0

    if not collected:
        message = (
            f"No collected test matches {selection!r}"
            if selection
            else "No @add_test tests were collected"
        )
        print(message, file=sys.stderr)
        return 1

    checkpoints = CheckpointStore(target_dir)
    coordinator = CheckpointCoordinator()
    reporter = ProgressReporter(
        collect_test_nodes(target for _, _, target in collected), mode=progress
    )
    targets = [
        (
            candidate,
            GraphExecutor(
                graph,
                target_dir=target_dir,
                record=record,
                force=force,
                checkpoints=checkpoints,
                coordinator=coordinator,
                reporter=reporter.emit,
            ),
            target,
        )
        for candidate, graph, target in collected
    ]
    checkpoints.prune(current_checkpoints)
    RunArtifacts.prepare(target_dir)

    async def run_target(
        candidate: Path, executor: GraphExecutor, target: TestNode[Any]
    ) -> tuple[Path, TestNode[Any], Any, TestExecutionError | None]:
        reporter.emit(event("started", target, target=True))
        try:
            return candidate, target, await executor.run(target), None
        except TestExecutionError as error:
            return candidate, target, None, error

    # Run all selected tests, but never race a checkpoint producer with a test
    # that consumes it. Dependencies not selected by --test remain resolved by
    # GraphExecutor as normal setup work for their selected consumer.
    scheduled = {
        (candidate, target.id): (candidate, executor, target)
        for candidate, executor, target in targets
    }
    pending = dict(scheduled)
    dependencies = {
        key: {
            (candidate, dependency.node.id)
            for dependency in target.dependencies.values()
            if isinstance(dependency.node, TestNode)
            and (candidate, dependency.node.id) in pending
        }
        for key, (candidate, _, target) in pending.items()
    }
    completed: set[tuple[Path, str]] = set()
    running: dict[asyncio.Task[Any], tuple[Path, str]] = {}
    report: list[tuple[str, Path, TestNode[Any], TestExecutionError | None]] = []

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
                report.append(("FAIL", candidate, target, error))
                reporter.emit(event("failed", target, target=True, detail=str(error)))
                for other in running:
                    other.cancel()
                for _, cancelled_key in running.items():
                    reporter.emit(
                        event(
                            "cancelled",
                            scheduled[cancelled_key][2],
                            detail="cancelled after a test failure",
                        )
                    )
                for cancelled_key in pending:
                    reporter.emit(
                        event(
                            "cancelled",
                            scheduled[cancelled_key][2],
                            detail="cancelled after a test failure",
                        )
                    )
                await asyncio.gather(*done, *running, return_exceptions=True)
                _write_report(target_dir, report)
                _print_test_failure(candidate, error)
                return 1
            if result is None:
                raise AssertionError(f"{target.id} completed without a result")
            completed.add(key)
            report.append(("PASS", candidate, target, None))
            reporter.emit(
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
    _write_report(target_dir, report)
    return 0


def _discover_test_paths(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise OSError(f"test path does not exist: {path}")

    ignored = {".git", ".venv", "__pycache__", "target"}
    paths = [
        candidate
        for candidate in path.rglob("*.py")
        if candidate.name != "__init__.py"
        and not any(part in ignored for part in candidate.relative_to(path).parts)
        and _declares_tests(candidate)
    ]
    return sorted(paths)


def _declares_tests(path: Path) -> bool:
    """Return whether a module declares at least one ``@add_test`` function."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        # Let imports surface useful errors for a likely Catsnail module.
        return "add_test" in path.name
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(_is_add_test(decorator) for decorator in node.decorator_list)
        for node in ast.walk(tree)
    )


def _is_add_test(decorator: ast.expr) -> bool:
    candidate = decorator.func if isinstance(decorator, ast.Call) else decorator
    return (isinstance(candidate, ast.Name) and candidate.id == "add_test") or (
        isinstance(candidate, ast.Attribute) and candidate.attr == "add_test"
    )


def _checkpoint_hashes(nodes: Iterable[Node]) -> dict[str, str]:
    return {
        checkpoint_key(node): checkpoint_origin(node)
        for node in nodes
        if isinstance(node, TestNode)
    }


def _print_test_failure(path: Path, error: TestExecutionError) -> None:
    print(f"catsnail run failed: {error}", file=sys.stderr)
    print(
        f"Reproduce: catsnail run {path} --test {error.target.function.__name__}",
        file=sys.stderr,
    )
    for artifact_directory, debug_directory in zip(error.artifacts, error.debug):
        script = artifact_directory / "reproduce.sh"
        print(f"Artifacts: {artifact_directory}", file=sys.stderr)
        print(f"Debug: {debug_directory}", file=sys.stderr)
        print(f"QEMU reproduce: sh {script}", file=sys.stderr)
        resume = artifact_directory / "resume.sh"
        if resume.is_file():
            print(f"VM snapshot reproduce: sh {resume}", file=sys.stderr)


def _write_report(
    target_dir: Path,
    results: Iterable[tuple[str, Path, TestNode[Any], TestExecutionError | None]],
) -> None:
    """Write a compact, linkable summary for the completed run."""

    rows = list(results)
    passed = sum(status == "PASS" for status, _, _, _ in rows)
    failed = sum(status == "FAIL" for status, _, _, _ in rows)
    lines = [
        "# Catsnail Report",
        "",
        f"{passed} passed, {failed} failed.",
        "",
        "| Status | Test | Details |",
        "| --- | --- | --- |",
    ]
    for status, path, target, error in rows:
        details = ""
        if error is not None:
            screenshot = next(
                (directory / "last-vnc.png" for directory in error.debug if (directory / "last-vnc.png").is_file()),
                None,
            )
            reproduce = f"catsnail run {path} --test {target.function.__name__}"
            details = f"`{reproduce}`"
            if screenshot is not None:
                details += f"; [last VNC screenshot]({screenshot.relative_to(target_dir.parent)})"
        lines.append(f"| {status} | `{target.function.__name__}` | {details} |")
    (target_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
