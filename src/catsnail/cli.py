"""Command-line entry points for the Python vertical slice."""

from __future__ import annotations

import argparse
import asyncio
import ast
import hashlib
import importlib.util
import json
import os
import shutil
import shlex
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
from .graph.scheduler import schedule_targets
from .dashboard import Dashboard
from .image import iso_cache_directory
from .progress import (
    ProgressMode,
    ProgressReporter,
    collect_test_nodes,
)
from .qemu.artifacts import RunArtifacts
from .qemu.runner import QemuLaunchOptions, QemuRunner


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "doctor":
        return _doctor()
    if arguments.command == "prune":
        return _prune(arguments.path, arguments.target_dir)
    if arguments.command == "explore":
        try:
            return _explore(arguments)
        except KeyboardInterrupt:
            print("Catsnail explore cancelled.", file=sys.stderr)
            return 130
    if arguments.command == "run":
        try:
            qemu_options = QemuLaunchOptions(
                executable=arguments.qemu,
                acceleration=arguments.accel,
                tcg_thread=arguments.tcg_thread,
                tcg_tb_size=arguments.tcg_tb_size,
                hugepage_path=arguments.hugepages,
            )
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
                    arguments.keep_going,
                    arguments.web,
                    arguments.web_port,
                    qemu_options,
                )
            )
        except ValueError as error:
            parser.error(str(error))
        except KeyboardInterrupt:
            print("Catsnail run cancelled.", file=sys.stderr)
            return 130
    if arguments.command == "studio":
        return asyncio.run(_studio_command(arguments))
    parser.error(f"unknown command: {arguments.command}")
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="catsnail")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("doctor", help="check local QEMU test prerequisites")

    explore = subcommands.add_parser(
        "explore", help="ask the local Codex CLI to author a test through Studio"
    )
    explore.add_argument("task", type=Path, help="Markdown or text test specification")
    explore.add_argument("scenario", type=Path, help="Catsnail Python scenario to extend")
    explore.add_argument(
        "--from",
        dest="checkpoint",
        required=True,
        metavar="TEST",
        help="successful @add_test checkpoint restored for exploration",
    )
    explore.add_argument("--target-dir", type=Path, default=Path("target"))
    explore.add_argument(
        "--codex",
        default="codex",
        help="Codex executable to invoke (default: codex from PATH)",
    )
    explore.add_argument(
        "--dry-run",
        action="store_true",
        help="validate inputs and print the Codex command without starting it",
    )

    studio = subcommands.add_parser(
        "studio", help="interactively explore a restored VM checkpoint"
    )
    studio_commands = studio.add_subparsers(dest="studio_command", required=True)
    studio_start = studio_commands.add_parser(
        "start", help="restore a checkpoint and create an interactive session"
    )
    studio_start.add_argument("path", type=Path)
    studio_start.add_argument(
        "--from",
        dest="checkpoint",
        required=True,
        metavar="TEST",
        help="checkpoint test function or collected id to restore",
    )
    studio_start.add_argument("--target-dir", type=Path, default=Path("target"))
    studio_start.add_argument("--session", dest="session_id")
    studio_transport = studio_start.add_mutually_exclusive_group()
    studio_transport.add_argument(
        "--serve",
        action="store_true",
        help="keep the restored VM open and serve JSON requests on its Unix socket",
    )
    studio_transport.add_argument(
        "--stdio",
        action="store_true",
        help="keep the restored VM open and serve JSON Lines over standard input/output",
    )
    studio_start.add_argument("--json", action="store_true", dest="as_json")

    studio_mcp = studio_commands.add_parser(
        "mcp", help="restore a checkpoint and expose it as MCP visual tools"
    )
    studio_mcp.add_argument("path", type=Path)
    studio_mcp.add_argument(
        "--from",
        dest="checkpoint",
        required=True,
        metavar="TEST",
        help="checkpoint test function or collected id to restore",
    )
    studio_mcp.add_argument("--target-dir", type=Path, default=Path("target"))
    studio_mcp.add_argument("--session", dest="session_id")

    studio_status = studio_commands.add_parser("status", help="show a session manifest")
    studio_status.add_argument("session_id", nargs="?")
    studio_status.add_argument("--target-dir", type=Path, default=Path("target"))
    studio_status.add_argument("--json", action="store_true", dest="as_json")

    for command, help_text in (
        ("screenshot", "capture the current framebuffer"),
        ("wait", "wait until the framebuffer is stable"),
        ("serial", "read the current serial log"),
    ):
        item = studio_commands.add_parser(command, help=help_text)
        item.add_argument("session_id", nargs="?")
        item.add_argument("--machine", default="desktop")
        item.add_argument("--timeout", type=float, default=30.0)
        item.add_argument("--lines", type=int, default=100)
        item.add_argument("--target-dir", type=Path, default=Path("target"))
        item.add_argument("--json", action="store_true", dest="as_json")

    click = studio_commands.add_parser("click", help="click a guest framebuffer")
    click.add_argument("values", nargs="+", metavar="VALUE")
    click.add_argument("--session", dest="session_id")
    click.add_argument("--machine", default="desktop")
    click.add_argument("--target-dir", type=Path, default=Path("target"))
    click.add_argument("--json", action="store_true", dest="as_json")

    right_click = studio_commands.add_parser(
        "right-click", help="open a guest context menu"
    )
    right_click.add_argument("x", type=int)
    right_click.add_argument("y", type=int)
    right_click.add_argument("--session", dest="session_id")
    right_click.add_argument("--machine", default="desktop")
    right_click.add_argument("--target-dir", type=Path, default=Path("target"))
    right_click.add_argument("--json", action="store_true", dest="as_json")

    middle_click = studio_commands.add_parser(
        "middle-click", help="paste the X11 primary selection into a guest control"
    )
    middle_click.add_argument("x", type=int)
    middle_click.add_argument("y", type=int)
    middle_click.add_argument("--session", dest="session_id")
    middle_click.add_argument("--machine", default="desktop")
    middle_click.add_argument("--target-dir", type=Path, default=Path("target"))
    middle_click.add_argument("--json", action="store_true", dest="as_json")

    move = studio_commands.add_parser("move", help="move the guest pointer")
    move.add_argument("x", type=int)
    move.add_argument("y", type=int)
    move.add_argument("--session", dest="session_id")
    move.add_argument("--machine", default="desktop")
    move.add_argument("--target-dir", type=Path, default=Path("target"))
    move.add_argument("--json", action="store_true", dest="as_json")

    type_command = studio_commands.add_parser("type", help="type text into a guest")
    type_command.add_argument("values", nargs="+", metavar="TEXT")
    type_command.add_argument("--session", dest="session_id")
    type_command.add_argument("--machine", default="desktop")
    type_command.add_argument("--target-dir", type=Path, default=Path("target"))
    type_command.add_argument("--json", action="store_true", dest="as_json")

    paste_command = studio_commands.add_parser(
        "paste", help="paste text through the guest remote clipboard"
    )
    paste_command.add_argument("values", nargs="+", metavar="TEXT")
    paste_command.add_argument("--session", dest="session_id")
    paste_command.add_argument("--machine", default="desktop")
    paste_command.add_argument("--target-dir", type=Path, default=Path("target"))
    paste_command.add_argument("--json", action="store_true", dest="as_json")

    key = studio_commands.add_parser("key", help="press a guest key")
    key.add_argument("values", nargs="+", metavar="KEY")
    key.add_argument("--session", dest="session_id")
    key.add_argument("--machine", default="desktop")
    key.add_argument("--target-dir", type=Path, default=Path("target"))
    key.add_argument("--json", action="store_true", dest="as_json")

    shortcut = studio_commands.add_parser(
        "shortcut", help="send a guest keyboard shortcut"
    )
    shortcut.add_argument("values", nargs="+", metavar="KEY")
    shortcut.add_argument("--session", dest="session_id")
    shortcut.add_argument("--machine", default="desktop")
    shortcut.add_argument("--target-dir", type=Path, default=Path("target"))
    shortcut.add_argument("--json", action="store_true", dest="as_json")

    crop = studio_commands.add_parser("crop", help="save a frame region as a fixture")
    crop.add_argument("values", nargs="+", metavar="VALUE")
    crop.add_argument("--session", dest="session_id")
    crop.add_argument("--label", default="fixture")
    crop.add_argument("--target-dir", type=Path, default=Path("target"))
    crop.add_argument("--json", action="store_true", dest="as_json")

    emit = studio_commands.add_parser("emit", help="generate a reviewable test draft")
    emit.add_argument("session_id", nargs="?")
    emit.add_argument("--name", default="explore")
    emit.add_argument("--target-dir", type=Path, default=Path("target"))
    emit.add_argument("--json", action="store_true", dest="as_json")

    finish = studio_commands.add_parser("finish", help="alias for studio emit")
    finish.add_argument("session_id", nargs="?")
    finish.add_argument("--name", default="explore")
    finish.add_argument("--target-dir", type=Path, default=Path("target"))
    finish.add_argument("--json", action="store_true", dest="as_json")

    reset = studio_commands.add_parser(
        "reset", help="restore the session checkpoint again"
    )
    reset.add_argument("path", type=Path)
    reset.add_argument("--from", dest="checkpoint", required=True, metavar="TEST")
    reset.add_argument("--session", dest="session_id")
    reset.add_argument("--target-dir", type=Path, default=Path("target"))
    reset.add_argument("--json", action="store_true", dest="as_json")

    stop = studio_commands.add_parser("stop", help="stop an interactive session")
    stop.add_argument("session_id", nargs="?")
    stop.add_argument("--target-dir", type=Path, default=Path("target"))
    stop.add_argument("--json", action="store_true", dest="as_json")

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
    execution = run.add_mutually_exclusive_group()
    execution.add_argument(
        "--keep-going",
        dest="keep_going",
        action="store_true",
        help="continue independent tests after a failure (default)",
    )
    execution.add_argument(
        "--fail-fast",
        dest="keep_going",
        action="store_false",
        help="stop all remaining tests after the first failure",
    )
    recording = run.add_mutually_exclusive_group()
    recording.add_argument(
        "--record",
        dest="record",
        action="store_true",
        help="save step screenshots and recording.mp4 for executed tests (default)",
    )
    recording.add_argument(
        "--no-record",
        dest="record",
        action="store_false",
        help="do not save step screenshots or a recording.mp4",
    )
    web = run.add_mutually_exclusive_group()
    web.add_argument(
        "--web",
        dest="web",
        action="store_true",
        help="serve the live local dashboard (default)",
    )
    web.add_argument(
        "--no-web",
        dest="web",
        action="store_false",
        help="disable the live local dashboard",
    )
    run.add_argument(
        "--web-port",
        type=int,
        default=8765,
        help="localhost dashboard port (uses a free port if occupied)",
    )
    run.add_argument(
        "--qemu",
        metavar="PATH",
        default="qemu-system-x86_64",
        help="QEMU system executable (default: qemu-system-x86_64)",
    )
    run.add_argument(
        "--accel",
        choices=("kvm", "tcg"),
        default="kvm",
        help="accelerator selection; KVM is the default and TCG is explicit",
    )
    run.add_argument(
        "--tcg-thread",
        choices=("single", "multi"),
        help="TCG worker mode; requires --accel tcg",
    )
    run.add_argument(
        "--tcg-tb-size",
        type=int,
        metavar="MIB",
        help="TCG translation-block cache size in MiB; requires --accel tcg",
    )
    run.add_argument(
        "--hugepages",
        type=Path,
        metavar="DIR",
        help="allocate guest RAM from a mounted hugetlbfs directory",
    )
    run.set_defaults(record=True, web=True, keep_going=True)
    run.add_argument(
        "--jobs",
        type=int,
        default=max(1, min(4, os.cpu_count() or 1)),
        help="maximum number of independent tests to run at once",
    )
    run.add_argument(
        "--progress",
        choices=("auto", "tree", "plain"),
        default="tree",
        help="progress output: live tree for terminals, or append-only plain logs",
    )

    return parser


def _explore(arguments: Any) -> int:
    """Run a bounded Codex test-authoring session from a text specification."""

    from .explore import ExploreError, author_test_with_codex

    try:
        result = author_test_with_codex(
            arguments.task,
            arguments.scenario,
            arguments.checkpoint,
            target_dir=arguments.target_dir,
            workspace=Path.cwd(),
            executable=arguments.codex,
            dry_run=arguments.dry_run,
        )
    except ExploreError as error:
        print(f"catsnail explore failed: {error}", file=sys.stderr)
        return 2
    if arguments.dry_run:
        print("Codex command:")
        print(shlex.join(result.command))
        return 0
    print(f"Studio session: {result.session}")
    print(f"Explore prompt: {result.prompt}")
    print(f"Codex report: {result.transcript}")
    return result.returncode or 0


async def _studio_command(arguments: Any) -> int:
    from .studio import StudioError, StudioSession, StudioSessionStore

    try:
        if arguments.studio_command == "start":
            session = await StudioSession.start(
                arguments.path,
                arguments.checkpoint,
                target_dir=arguments.target_dir,
                session_id=arguments.session_id,
            )
            result: Any = {
                "session": session.session_id,
                "manifest": str(session.store.manifest_path(session.session_id)),
                "machines": sorted(session.machines),
            }
            if arguments.serve:
                from .studio import StudioRpcServer

                print(json.dumps(result, ensure_ascii=False, indent=2))
                await StudioRpcServer(session).serve_forever()
                return 0
            if arguments.stdio:
                from .studio import StudioStdioServer

                await StudioStdioServer(session, ready=result).serve_forever()
                return 0
            await session.close_connections()
        elif arguments.studio_command == "mcp":
            from .studio_mcp import StudioMcpServer

            session = await StudioSession.start(
                arguments.path,
                arguments.checkpoint,
                target_dir=arguments.target_dir,
                session_id=arguments.session_id,
            )
            await StudioMcpServer(
                session,
                path=arguments.path,
                checkpoint=arguments.checkpoint,
                target_dir=arguments.target_dir,
            ).serve_forever()
            return 0
        elif arguments.studio_command == "status":
            store = StudioSessionStore(arguments.target_dir)
            session_id = store.active(arguments.session_id)
            result = store.read(session_id)
        elif arguments.studio_command == "reset":
            from .studio import StudioSessionStore

            store = StudioSessionStore(arguments.target_dir)
            session_id = store.active(arguments.session_id)
            existing = await StudioSession.attach(
                session_id, target_dir=arguments.target_dir
            )
            await existing.stop()
            shutil.rmtree(existing.directory, ignore_errors=True)
            session = await StudioSession.start(
                arguments.path,
                arguments.checkpoint,
                target_dir=arguments.target_dir,
                session_id=session_id,
            )
            result = {"session": session.session_id, "status": "active"}
            await session.close_connections()
        else:
            command = arguments.studio_command
            if (
                command == "click"
                and arguments.session_id is None
                and len(arguments.values) == 3
            ):
                arguments.session_id = arguments.values[0]
                arguments.values = arguments.values[1:]
            elif (
                command in {"type", "paste", "key", "shortcut"}
                and arguments.session_id is None
                and len(arguments.values) > 1
            ):
                arguments.session_id = arguments.values[0]
                arguments.values = arguments.values[1:]
            elif (
                command == "crop"
                and arguments.session_id is None
                and len(arguments.values) == 6
            ):
                arguments.session_id = arguments.values[0]
                arguments.values = arguments.values[1:]
            session = await StudioSession.attach(
                arguments.session_id, target_dir=arguments.target_dir
            )
            if arguments.studio_command == "screenshot":
                result = await session.snapshot(machine=arguments.machine)
            elif arguments.studio_command == "click":
                values = list(arguments.values)
                if len(values) != 2:
                    raise StudioError("studio click expects X Y")
                result = await session.click(
                    int(values[0]), int(values[1]), machine=arguments.machine
                )
            elif arguments.studio_command == "right-click":
                result = await session.right_click(
                    arguments.x, arguments.y, machine=arguments.machine
                )
            elif arguments.studio_command == "middle-click":
                result = await session.middle_click(
                    arguments.x, arguments.y, machine=arguments.machine
                )
            elif arguments.studio_command == "move":
                result = await session.move(
                    arguments.x, arguments.y, machine=arguments.machine
                )
            elif arguments.studio_command == "type":
                values = list(arguments.values)
                result = await session.type(" ".join(values), machine=arguments.machine)
            elif arguments.studio_command == "paste":
                values = list(arguments.values)
                result = await session.paste(" ".join(values), machine=arguments.machine)
            elif arguments.studio_command == "key":
                values = list(arguments.values)
                if len(values) != 1:
                    raise StudioError("studio key expects one key")
                result = await session.key(values[0], machine=arguments.machine)
            elif arguments.studio_command == "shortcut":
                values = list(arguments.values)
                if len(values) < 2:
                    raise StudioError("studio shortcut expects at least two keys")
                result = await session.shortcut(*values, machine=arguments.machine)
            elif arguments.studio_command == "wait":
                result = await session.wait_stable(
                    timeout=arguments.timeout, machine=arguments.machine
                )
            elif arguments.studio_command == "serial":
                result = await session.serial(
                    machine=arguments.machine, lines=arguments.lines
                )
            elif arguments.studio_command == "crop":
                values = list(arguments.values)
                if len(values) != 5:
                    raise StudioError("studio crop expects FRAME X Y WIDTH HEIGHT")
                result = await session.crop(
                    int(values[0]),
                    int(values[1]),
                    int(values[2]),
                    int(values[3]),
                    int(values[4]),
                    label=arguments.label,
                )
            elif arguments.studio_command in {"emit", "finish"}:
                result = session.emit(arguments.name)
            elif arguments.studio_command == "stop":
                await session.stop()
                result = {"session": session.session_id, "status": "stopped"}
            else:
                raise StudioError(f"unknown studio command: {arguments.studio_command}")
            if arguments.studio_command != "stop":
                await session.close_connections()
        if getattr(arguments, "as_json", False):
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        elif isinstance(result, dict):
            for key, value in result.items():
                print(f"{key}: {value}")
        else:
            print(result)
        return 0
    except (StudioError, OSError, RuntimeError) as error:
        print(f"catsnail studio failed: {error}", file=sys.stderr)
        return 1


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
        print("ERROR /dev/kvm: not accessible; default runs require KVM")
        failures += 1
    else:
        print("ERROR /dev/kvm: not found; default runs require KVM")
        failures += 1

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print("WARN ffmpeg: not found; recordings will retain PNG keyframes only")
    else:
        version = _tool_version(ffmpeg, argument="-version")
        if version is None:
            print(f"WARN ffmpeg: could not run {ffmpeg} --version")
        else:
            print(f"OK ffmpeg: {version} ({ffmpeg})")

    hugepage_path = Path("/dev/hugepages")
    if not hugepage_path.is_dir():
        print("WARN /dev/hugepages: hugetlbfs is not mounted")
    elif not os.access(hugepage_path, os.R_OK | os.W_OK | os.X_OK):
        print("WARN /dev/hugepages: hugetlbfs is not accessible")
    else:
        hugepage_stats = _hugepage_stats()
        if hugepage_stats is None:
            print("WARN /dev/hugepages: HugeTLB pool size is unavailable")
        else:
            total, free = hugepage_stats
            if total == 0 or free == 0:
                print(
                    f"WARN /dev/hugepages: {total} pages reserved, {free} free; "
                    "--hugepages may fail"
                )
            else:
                print(f"OK /dev/hugepages: {free}/{total} pages free")

    return 1 if failures else 0


def _hugepage_stats() -> tuple[int, int] | None:
    """Read the system HugeTLB pool without requiring a procfs dependency."""

    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            name, separator, value = line.partition(":")
            if separator and name in {"HugePages_Total", "HugePages_Free"}:
                values[name] = int(value.strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    if "HugePages_Total" not in values or "HugePages_Free" not in values:
        return None
    return values["HugePages_Total"], values["HugePages_Free"]


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
    keep_going: bool = True,
    web: bool = False,
    web_port: int = 8765,
    qemu_options: QemuLaunchOptions | None = None,
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
            test_count += sum(
                isinstance(node, TestNode) and not node.internal for node in nodes
            )
            source_count += sum(isinstance(node, Source) for node in nodes)
            current_checkpoints.update(_checkpoint_hashes(nodes, qemu_options))
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
            collect_test_nodes(
                (target for _, _, target in collected), include_internal=True
            ),
            mode="plain",
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
    # Show durable setup checkpoints in the live tree. Hiding them flattens
    # real dependency paths and makes unrelated-looking branches misleading.
    progress_nodes = collect_test_nodes(
        (target for _, _, target in collected), include_internal=True
    )
    dashboard = Dashboard(progress_nodes, port=web_port) if web else None
    if dashboard is not None:
        await dashboard.start()
    # Tree mode owns an alternate terminal screen for the duration of a run.
    # Keep the dashboard URL inside that screen rather than printing beside a
    # cursor-redrawn tree, where a later failure would corrupt the redraw.
    reporter = ProgressReporter(
        progress_nodes,
        mode=progress,
        header=f"Dashboard: {dashboard.url}" if dashboard is not None else None,
    )
    if dashboard is not None and not reporter.live:
        print(f"Dashboard: {dashboard.url}", file=sys.stderr)

    def emit_progress(update: Any) -> None:
        reporter.emit(update)
        if dashboard is not None:
            dashboard.emit(update)

    def observe_guest(action: str, guest: Any) -> None:
        if dashboard is None:
            return
        if action == "started":
            dashboard.register_guest(guest)
        elif action == "stopped":
            dashboard.unregister_guest(guest)

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
                reporter=emit_progress,
                guest_observer=observe_guest,
                qemu_options=qemu_options,
            ),
            target,
        )
        for candidate, graph, target in collected
    ]
    checkpoints.prune(current_checkpoints)
    RunArtifacts.prepare(target_dir)
    try:
        exit_code, report = await schedule_targets(
            targets,
            jobs=jobs,
            keep_going=keep_going,
            record=record,
            reporter=reporter,
            progress_nodes=progress_nodes,
            emit_progress=emit_progress,
        )
    finally:
        if dashboard is not None:
            await dashboard.close()
        reporter.close()
        await QemuRunner.stop_all_instances()
    _write_report(target_dir, report)
    _print_run_diagnostics(report)
    return exit_code


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


def _checkpoint_hashes(
    nodes: Iterable[Node], qemu_options: QemuLaunchOptions | None = None
) -> dict[str, str]:
    runtime = qemu_options.checkpoint_identity() if qemu_options is not None else None
    return {
        checkpoint_key(node, runtime=runtime): checkpoint_origin(node)
        for node in nodes
        if isinstance(node, TestNode)
    }


def _print_test_failure(
    path: Path, error: TestExecutionError, *, expected: bool = False
) -> None:
    heading = "catsnail expected failure" if expected else "catsnail run failed"
    print(f"{heading}: {error}", file=sys.stderr)
    print(
        f"Reproduce: catsnail run {path} --test {error.target.function.__name__}",
        file=sys.stderr,
    )
    for artifact_directory, debug_directory, release_directory in zip(
        error.artifacts, error.debug, error.release
    ):
        script = artifact_directory / "reproduce.sh"
        print(f"Artifacts: {artifact_directory}", file=sys.stderr)
        print(f"Release: {release_directory}", file=sys.stderr)
        print(f"Debug: {debug_directory}", file=sys.stderr)
        print(f"QEMU reproduce: sh {script}", file=sys.stderr)
        resume = artifact_directory / "resume.sh"
        if resume.is_file():
            print(f"VM snapshot reproduce: sh {resume}", file=sys.stderr)


def _print_run_diagnostics(
    results: Iterable[tuple[str, Path, TestNode[Any], TestExecutionError | None]],
) -> None:
    """Print diagnostics only after the live progress surface has closed."""

    for status, path, _, error in results:
        if error is not None:
            _print_test_failure(path, error, expected=status == "XFAIL")


def _write_report(
    target_dir: Path,
    results: Iterable[tuple[str, Path, TestNode[Any], TestExecutionError | None]],
) -> None:
    """Write a compact, linkable summary for the completed run."""

    rows = list(results)
    passed = sum(status == "PASS" for status, _, _, _ in rows)
    failed = sum(status == "FAIL" for status, _, _, _ in rows)
    expected_failures = sum(status == "XFAIL" for status, _, _, _ in rows)
    unexpected_passes = sum(status == "XPASS" for status, _, _, _ in rows)
    lines = [
        "# Catsnail Report",
        "",
        (
            f"{passed} passed, {failed} failed, {expected_failures} expected "
            f"failures, {unexpected_passes} unexpected passes."
        ),
        "",
        "| Status | Test | Details |",
        "| --- | --- | --- |",
    ]
    for status, path, target, error in rows:
        details = ""
        if error is not None:
            screenshot = next(
                (
                    directory / "failure.png"
                    for directory in error.release
                    if (directory / "failure.png").is_file()
                ),
                None,
            )
            reproduce = f"catsnail run {path} --test {target.function.__name__}"
            details = f"`{reproduce}`"
            if screenshot is not None:
                details += f"; [failure screenshot]({screenshot.relative_to(target_dir.parent)})"
        lines.append(f"| {status} | `{target.function.__name__}` | {details} |")
    (target_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
