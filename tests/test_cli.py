from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from pytest import CaptureFixture, MonkeyPatch

from catsnail.graph.api import TestNode as CatsnailTestNode, collect_module_tests
from catsnail.graph.checkpoint import CheckpointStore, checkpoint_origin
from catsnail.cli import (
    _discover_test_paths,
    _load_module,
    _module_location,
    _parser,
    _run,
    main,
)
from catsnail.graph.executor import (
    ExpectedFailure,
    UnexpectedPassError,
    TestExecutionError as CatsnailTestExecutionError,
    TestResult as CatsnailTestResult,
)


def test_discovers_python_modules_and_skips_runtime_directories(
    tmp_path: Path,
) -> None:
    discovered = [
        tmp_path / "test_boot.py",
        tmp_path / "browser_test.py",
        tmp_path / "nested" / "test_ssh.py",
        tmp_path / "scenario.py",
    ]
    ignored = [
        tmp_path / "__init__.py",
        tmp_path / ".venv" / "test_dependency.py",
        tmp_path / "target" / "test_artifact.py",
        tmp_path / "nested" / "__pycache__" / "test_cached.py",
    ]
    for path in [*discovered, *ignored]:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "@add_test\nasync def test_scenario() -> None:\n    return None\n"
            if path in discovered
            else "# support fixture\n"
        )
        path.write_text(content, encoding="utf-8")

    assert _discover_test_paths(tmp_path) == sorted(discovered)


def test_uses_an_explicit_python_file_without_name_filter(tmp_path: Path) -> None:
    path = tmp_path / "scenario.py"
    path.write_text("# test fixture\n", encoding="utf-8")

    assert _discover_test_paths(path) == [path]


def test_uses_a_stable_module_name_for_non_package_test_files(tmp_path: Path) -> None:
    path = tmp_path / "test_scenario.py"

    first, first_root = _module_location(path)
    second, second_root = _module_location(path)

    assert first == second
    assert first_root is None
    assert second_root is None


def test_run_defaults_to_recording_and_accepts_dry_run() -> None:
    parser = _parser()

    assert parser.parse_args(["run", "scenario.py"]).record is True
    assert parser.parse_args(["run", "scenario.py", "--record"]).record is True
    assert parser.parse_args(["run", "scenario.py", "--no-record"]).record is False
    assert parser.parse_args(["run", "scenario.py"]).dry_run is False
    assert parser.parse_args(["run", "scenario.py", "--dry-run"]).dry_run is True
    assert parser.parse_args(["run", "scenario.py"]).force is False
    assert parser.parse_args(["run", "scenario.py", "--force"]).force is True
    assert parser.parse_args(["run", "scenario.py"]).keep_going is True
    assert parser.parse_args(["run", "scenario.py", "--keep-going"]).keep_going
    assert parser.parse_args(["run", "scenario.py", "--fail-fast"]).keep_going is False
    assert parser.parse_args(["run", "scenario.py"]).progress == "tree"
    assert parser.parse_args(["run", "scenario.py"]).web is True
    assert parser.parse_args(["run", "scenario.py", "--no-web"]).web is False
    assert (
        parser.parse_args(["run", "scenario.py", "--progress", "plain"]).progress
        == "plain"
    )
    assert parser.parse_args(["run", "scenario.py"]).qemu == "qemu-system-x86_64"
    assert parser.parse_args(["run", "scenario.py"]).accel == "kvm"
    options = parser.parse_args(
        [
            "run",
            "scenario.py",
            "--qemu",
            "/opt/qemu/bin/qemu-system-x86_64",
            "--accel",
            "tcg",
            "--tcg-thread",
            "multi",
            "--tcg-tb-size",
            "1024",
            "--hugepages",
            "/dev/hugepages",
        ]
    )
    assert options.accel == "tcg"
    assert options.tcg_thread == "multi"
    assert options.tcg_tb_size == 1024
    assert options.hugepages == Path("/dev/hugepages")


def test_main_returns_a_standard_interrupt_exit_code(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    async def interrupted(*_: object, **__: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr("catsnail.cli._run", interrupted)

    assert main(["run", "scenario.py"]) == 130
    assert "Catsnail run cancelled." in capsys.readouterr().err


def test_studio_stdio_is_an_explicit_alternative_to_socket_serving() -> None:
    parser = _parser()

    arguments = parser.parse_args(
        ["studio", "start", "scenario.py", "--from", "test_boot", "--stdio"]
    )

    assert arguments.stdio is True
    assert arguments.serve is False
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "studio",
                "start",
                "scenario.py",
                "--from",
                "test_boot",
                "--stdio",
                "--serve",
            ]
        )


def test_studio_mcp_requires_a_checkpoint() -> None:
    parser = _parser()

    arguments = parser.parse_args(
        ["studio", "mcp", "scenario.py", "--from", "test_boot"]
    )

    assert arguments.studio_command == "mcp"
    assert arguments.checkpoint == "test_boot"


def test_explore_accepts_a_task_file_scenario_and_checkpoint() -> None:
    parser = _parser()

    arguments = parser.parse_args(
        [
            "explore",
            "requirements.md",
            "scenario.py",
            "--from",
            "test_desktop_login",
        ]
    )

    assert arguments.task == Path("requirements.md")
    assert arguments.scenario == Path("scenario.py")
    assert arguments.checkpoint == "test_desktop_login"
    assert arguments.codex == "codex"


def test_internal_add_test_nodes_are_dependencies_but_not_targets(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    path = tmp_path / "test_internal.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use, xfail\n"
        "SOURCE = add_os(Machine())\n"
        "@add_test(internal=True)\n"
        "async def setup(guest: Guest = use(SOURCE)) -> None:\n"
        "    pass\n"
        "@add_test\n"
        "async def test_public(guest: Guest = use(setup)) -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )

    assert main(["run", str(path), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Catsnail run (1 tests) (1 setup steps)" in output
    assert "`- [WAIT] setup (setup)" in output
    assert "   `- [WAIT] test_public" in output
    assert "Validated 1 tests" in output


def test_doctor_reports_required_tools_and_optional_warnings(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    paths = {
        "qemu-system-x86_64": "/tools/qemu-system-x86_64",
        "qemu-img": "/tools/qemu-img",
        "ffmpeg": None,
    }
    monkeypatch.setattr("catsnail.cli.shutil.which", paths.get)
    monkeypatch.setattr(
        "catsnail.cli._tool_version",
        lambda executable, **_: f"{executable} version",
    )
    monkeypatch.setattr("catsnail.cli.Path.exists", lambda _: False)

    assert main(["doctor"]) == 1
    output = capsys.readouterr().out
    assert "OK qemu-system-x86_64" in output
    assert "OK qemu-img" in output
    assert "ERROR /dev/kvm: not found; default runs require KVM" in output
    assert "WARN ffmpeg: not found" in output


def test_doctor_fails_only_when_a_required_tool_is_missing(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    paths = {
        "qemu-system-x86_64": None,
        "qemu-img": "/tools/qemu-img",
        "ffmpeg": None,
    }
    monkeypatch.setattr("catsnail.cli.shutil.which", paths.get)
    monkeypatch.setattr("catsnail.cli._tool_version", lambda _, **__: "version")

    assert main(["doctor"]) == 1
    assert "ERROR qemu-system-x86_64: not found on PATH" in capsys.readouterr().out


def test_dry_run_collects_catsnail_tests_from_a_directory(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    path = tmp_path / "test_scenario.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use\n"
        "SOURCE = add_os(Machine())\n"
        "@add_test\n"
        "async def test_boot(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )

    target_dir = tmp_path / "target"

    assert (
        main(["run", str(tmp_path), "--dry-run", "--target-dir", str(target_dir)]) == 0
    )
    assert not target_dir.exists()
    output = capsys.readouterr().out
    assert "Catsnail run (1 tests)" in output
    assert "`- [WAIT] test_boot" in output
    assert "1 waiting" in output
    assert f"Validated 1 tests and 1 machine sources from {path}" in output


def test_dry_run_validates_a_graph_with_only_checkpoints(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    path = tmp_path / "test_environment.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use\n"
        "SOURCE = add_os(Machine())\n"
        "@add_test\n"
        "async def test_boot(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )

    assert main(["run", str(path), "--dry-run"]) == 0
    assert (
        f"Validated 1 tests and 1 machine sources from {path}"
        in capsys.readouterr().out
    )


def test_dry_run_accepts_a_regular_expression_test_pattern(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    path = tmp_path / "test_targets.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use\n"
        "SOURCE = add_os(Machine())\n"
        "@add_test\n"
        "async def test_browser(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n"
        "@add_test\n"
        "async def test_ssh(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )

    assert main(["run", str(path), "--dry-run", "--test", r"^test_(browser|ssh)$"]) == 0
    assert (
        f"Validated 2 tests and 1 machine sources from {path}"
        in capsys.readouterr().out
    )


def test_dry_run_supports_relative_imports_in_a_test_package(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    package = tmp_path / "catsnail_discovery_package"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "support.py").write_text(
        "from catsnail import Machine, add_os\nSOURCE = add_os(Machine())\n",
        encoding="utf-8",
    )
    test_file = package / "test_relative.py"
    test_file.write_text(
        "from catsnail import Guest, add_test, use\n"
        "from .support import SOURCE\n"
        "@add_test\n"
        "async def test_boot(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )

    assert main(["run", str(package), "--dry-run"]) == 0
    assert (
        f"Validated 1 tests and 1 machine sources from {test_file}"
        in capsys.readouterr().out
    )


def test_prune_deletes_stale_checkpoints_after_explicit_confirmation(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    path = tmp_path / "test_scenario.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use\n"
        "SOURCE = add_os(Machine())\n"
        "@add_test\n"
        "async def test_boot(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n"
        "@add_test\n"
        "async def test_terminal(guest: Guest = use(test_boot)) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )
    module = _load_module(path)
    checkpoint = next(
        node
        for node in collect_module_tests(module).validate()
        if isinstance(node, CatsnailTestNode) and node.function.__name__ == "test_boot"
    )
    store = CheckpointStore(tmp_path / "target")
    key = "0" * 64
    staging = store.staging_directory(key, name="test_boot")
    (staging / "guest.qcow2").write_bytes(b"disk")
    (staging / "guest.state").write_bytes(b"state")
    published = store.publish(
        key,
        staging,
        {
            "origin": checkpoint_origin(checkpoint),
            "machines": [
                {
                    "source": "machine:old",
                    "disk": "guest.qcow2",
                    "state": "guest.state",
                }
            ],
            "output": {"kind": "guest", "source": "machine:old"},
        },
        name="test_boot",
    )
    monkeypatch.setattr("builtins.input", lambda _: "y")

    assert main(["prune", str(path), "--target-dir", str(tmp_path / "target")]) == 0
    assert not published["directory"].exists()
    assert "Found 1 stale checkpoint(s):" in capsys.readouterr().out


def test_runs_independent_tests_up_to_the_job_limit(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "test_parallel.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use\n"
        "SOURCE = add_os(Machine())\n"
        "@add_test\n"
        "async def test_one(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n"
        "@add_test\n"
        "async def test_two(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )
    active = 0
    maximum = 0

    class FakeExecutor:
        def __init__(self, graph: object, **_: object) -> None:
            self.graph = graph

        async def run(self, target: CatsnailTestNode[object]) -> CatsnailTestResult:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(0.05)
            finally:
                active -= 1
            return CatsnailTestResult(completed=(), artifacts=())

    monkeypatch.setattr("catsnail.cli.GraphExecutor", FakeExecutor)

    assert (
        asyncio.run(
            _run(
                path,
                tmp_path / "target",
                None,
                False,
                jobs=2,
                dry_run=False,
            )
        )
        == 0
    )
    assert maximum == 2


def test_runs_checkpoint_tests_before_selected_consumers(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "test_dependencies.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use\n"
        "SOURCE = add_os(Machine())\n"
        "@add_test\n"
        "async def test_boot(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n"
        "@add_test\n"
        "async def test_browser(guest: Guest = use(test_boot)) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )
    executed: list[str] = []

    class FakeExecutor:
        def __init__(self, graph: object, **_: object) -> None:
            del graph

        async def run(self, target: CatsnailTestNode[object]) -> CatsnailTestResult:
            executed.append(target.function.__name__)
            return CatsnailTestResult(completed=(target,), artifacts=())

    monkeypatch.setattr("catsnail.cli.GraphExecutor", FakeExecutor)

    assert (
        asyncio.run(_run(path, tmp_path / "target", None, False, jobs=2, dry_run=False))
        == 0
    )
    assert executed == ["test_boot", "test_browser"]


def test_keep_going_runs_independent_tests_and_cancels_failed_descendants(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "test_keep_going.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use\n"
        "SOURCE = add_os(Machine())\n"
        "@add_test\n"
        "async def test_failure(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n"
        "@add_test\n"
        "async def test_cancelled(guest: Guest = use(test_failure)) -> None:\n"
        "    return None\n"
        "@add_test\n"
        "async def test_independent(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )
    executed: list[str] = []

    class FakeExecutor:
        def __init__(self, graph: object, **_: object) -> None:
            del graph

        async def run(self, target: CatsnailTestNode[object]) -> CatsnailTestResult:
            name = target.function.__name__
            executed.append(name)
            if name == "test_failure":
                raise CatsnailTestExecutionError(
                    target, RuntimeError("expected failure"), ()
                )
            return CatsnailTestResult(completed=(target,), artifacts=())

    monkeypatch.setattr("catsnail.cli.GraphExecutor", FakeExecutor)

    assert (
        asyncio.run(
            _run(
                path,
                tmp_path / "target",
                None,
                False,
                jobs=1,
                dry_run=False,
                keep_going=True,
            )
        )
        == 1
    )
    assert executed == ["test_failure", "test_independent"]
    report = (tmp_path / "target" / "report.md").read_text(encoding="utf-8")
    assert "| FAIL | `test_failure` |" in report
    assert "| CANCEL | `test_cancelled` |" in report
    assert "| PASS | `test_independent` |" in report


def test_keep_going_cancels_targets_behind_an_internal_checkpoint(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "test_internal_dependency.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use\n"
        "SOURCE = add_os(Machine())\n"
        "@add_test\n"
        "async def test_failure(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n"
        "@add_test(internal=True)\n"
        "async def prepare(guest: Guest = use(test_failure)) -> None:\n"
        "    return None\n"
        "@add_test\n"
        "async def test_descendant(guest: Guest = use(prepare)) -> None:\n"
        "    return None\n"
        "@add_test\n"
        "async def test_independent(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )
    executed: list[str] = []

    class FakeExecutor:
        def __init__(self, graph: object, **_: object) -> None:
            del graph

        async def run(self, target: CatsnailTestNode[object]) -> CatsnailTestResult:
            name = target.function.__name__
            executed.append(name)
            if name == "test_failure":
                raise CatsnailTestExecutionError(
                    target, RuntimeError("expected failure"), ()
                )
            return CatsnailTestResult(completed=(target,), artifacts=())

    monkeypatch.setattr("catsnail.cli.GraphExecutor", FakeExecutor)

    assert (
        asyncio.run(
            _run(
                path,
                tmp_path / "target",
                None,
                False,
                jobs=2,
                dry_run=False,
                keep_going=True,
            )
        )
        == 1
    )
    assert executed == ["test_failure", "test_independent"]
    report = (tmp_path / "target" / "report.md").read_text(encoding="utf-8")
    assert "| CANCEL | `test_descendant` |" in report


def test_default_run_continues_independent_tests_after_a_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "test_default_continue.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use\n"
        "SOURCE = add_os(Machine())\n"
        "@add_test\n"
        "async def test_failure(guest: Guest = use(SOURCE)) -> None:\n"
        "    pass\n"
        "@add_test\n"
        "async def test_independent(guest: Guest = use(SOURCE)) -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    executed: list[str] = []

    class FakeExecutor:
        def __init__(self, graph: object, **_: object) -> None:
            del graph

        async def run(self, target: CatsnailTestNode[object]) -> CatsnailTestResult:
            name = target.function.__name__
            executed.append(name)
            if name == "test_failure":
                raise CatsnailTestExecutionError(
                    target, RuntimeError("expected failure"), ()
                )
            return CatsnailTestResult(completed=(target,), artifacts=())

    monkeypatch.setattr("catsnail.cli.GraphExecutor", FakeExecutor)

    assert (
        asyncio.run(_run(path, tmp_path / "target", None, False, jobs=1, dry_run=False))
        == 1
    )
    assert executed == ["test_failure", "test_independent"]


def test_failure_cancels_visible_descendants_of_an_internal_setup(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    path = tmp_path / "test_internal_setup_failure.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use\n"
        "SOURCE = add_os(Machine())\n"
        "@add_test\n"
        "async def test_root(guest: Guest = use(SOURCE)) -> None:\n"
        "    pass\n"
        "@add_test(internal=True)\n"
        "async def setup(guest: Guest = use(test_root)) -> None:\n"
        "    pass\n"
        "@add_test\n"
        "async def test_descendant(guest: Guest = use(setup)) -> None:\n"
        "    pass\n"
        "@add_test\n"
        "async def test_after(guest: Guest = use(test_descendant)) -> None:\n"
        "    pass\n"
        "@add_test\n"
        "async def test_independent(guest: Guest = use(SOURCE)) -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )

    class FakeExecutor:
        def __init__(self, graph: object, **_: object) -> None:
            del graph

        async def run(self, target: CatsnailTestNode[object]) -> CatsnailTestResult:
            if target.function.__name__ == "test_descendant":
                setup = next(
                    dependency.node
                    for dependency in target.dependencies.values()
                    if isinstance(dependency.node, CatsnailTestNode)
                )
                raise CatsnailTestExecutionError(
                    target, RuntimeError("setup failed"), (), failed_node=setup
                )
            return CatsnailTestResult(completed=(target,), artifacts=())

    monkeypatch.setattr("catsnail.cli.GraphExecutor", FakeExecutor)

    assert (
        asyncio.run(_run(path, tmp_path / "target", None, False, jobs=1, dry_run=False))
        == 1
    )
    del capsys
    report = (tmp_path / "target" / "report.md").read_text(encoding="utf-8")
    assert "| FAIL | `test_descendant` |" in report
    assert "| CANCEL | `test_after` |" in report
    assert "| PASS | `test_independent` |" in report


@pytest.mark.parametrize(
    ("cause", "status", "exit_code"),
    [
        (ExpectedFailure("documented upstream defect"), "XFAIL", 0),
        (RuntimeError("terminal did not receive the command"), "FAIL", 1),
        (UnexpectedPassError("documented upstream defect"), "XPASS", 1),
    ],
)
def test_expected_failure_reports_real_failures_and_unexpected_passes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    cause: BaseException,
    status: str,
    exit_code: int,
) -> None:
    path = tmp_path / "test_expected_failure.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use, xfail\n"
        "SOURCE = add_os(Machine())\n"
        "@add_test(expected_failure='documented upstream defect')\n"
        "async def test_documented_defect(guest: Guest = use(SOURCE)) -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )

    class FakeExecutor:
        def __init__(self, graph: object, **_: object) -> None:
            del graph

        async def run(self, target: CatsnailTestNode[object]) -> CatsnailTestResult:
            raise CatsnailTestExecutionError(target, cause, (), failed_node=target)

    monkeypatch.setattr("catsnail.cli.GraphExecutor", FakeExecutor)

    assert (
        asyncio.run(_run(path, tmp_path / "target", None, False, jobs=1, dry_run=False))
        == exit_code
    )
    report = (tmp_path / "target" / "report.md").read_text(encoding="utf-8")
    assert f"| {status} | `test_documented_defect` |" in report


def test_expected_failure_in_a_real_test_body_is_not_lost_after_dependencies(
    tmp_path: Path,
) -> None:
    path = tmp_path / "test_expected_failure_body.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use, xfail\n"
        "SOURCE = add_os(Machine())\n"
        "@add_test\n"
        "async def test_setup(guest: Guest = use(SOURCE)) -> None:\n"
        "    pass\n"
        "@add_test(expected_failure='documented upstream defect')\n"
        "async def test_documented_defect(guest: Guest = use(test_setup)) -> None:\n"
        "    xfail('documented upstream defect')\n",
        encoding="utf-8",
    )

    assert (
        asyncio.run(_run(path, tmp_path / "target", None, False, jobs=1, dry_run=False))
        == 0
    )
    report = (tmp_path / "target" / "report.md").read_text(encoding="utf-8")
    assert "| XFAIL | `test_documented_defect` |" in report


@pytest.mark.skipif(
    shutil.which("qemu-system-x86_64") is None or shutil.which("qemu-img") is None,
    reason="QEMU and qemu-img are required for failure snapshot integration",
)
def test_failed_terminal_test_keeps_a_resumable_vm_state(tmp_path: Path) -> None:
    path = tmp_path / "test_failure.py"
    path.write_text(
        "from catsnail import Guest, Machine, add_os, add_test, use\n"
        "SOURCE = add_os(Machine(memory='32M'))\n"
        "@add_test\n"
        "async def test_boot(guest: Guest = use(SOURCE)) -> None:\n"
        "    return None\n"
        "@add_test\n"
        "async def test_failure(guest: Guest = use(test_boot)) -> None:\n"
        "    raise RuntimeError('expected failure')\n",
        encoding="utf-8",
    )
    target_dir = tmp_path / "target"

    assert asyncio.run(_run(path, target_dir, None, False, jobs=1, dry_run=False)) == 1

    failures = list((target_dir / "run").rglob("failure.state"))
    resumes = list((target_dir / "run").rglob("resume.sh"))
    screenshots = list((target_dir / "debug").rglob("last-vnc.png"))
    released_screenshots = list((target_dir / "release").rglob("failure.png"))
    failure_logs = list((target_dir / "release").rglob("failure.txt"))
    serial_logs = list((target_dir / "release").rglob("serial.log"))
    qemu_logs = list((target_dir / "release").rglob("qemu.stderr.log"))
    assert len(failures) == 1
    assert failures[0].stat().st_size > 0
    assert len(resumes) == 1
    assert len(screenshots) == 1
    assert len(released_screenshots) == 1
    assert len(failure_logs) == 1
    assert len(serial_logs) == 1
    assert len(qemu_logs) == 1
    report = (target_dir / "report.md").read_text(encoding="utf-8")
    assert "| FAIL | `test_failure` |" in report
    assert "failure screenshot" in report
