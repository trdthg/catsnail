from __future__ import annotations

from io import StringIO
import os

from catsnail import Guest, Machine, add_os, add_test, use
from catsnail.progress import ProgressReporter, collect_test_nodes, event
from pytest import MonkeyPatch


def test_renders_a_live_dependency_tree_with_checkpoint_state(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    source = add_os(Machine())

    @add_test
    async def test_boot(guest: Guest = use(source)) -> None:
        del guest

    @add_test
    async def test_browser(guest: Guest = use(test_boot)) -> None:
        del guest

    stream = StringIO()
    reporter = ProgressReporter(
        collect_test_nodes([test_browser]), mode="tree", stream=stream
    )
    reporter.emit(event("started", test_boot, target=True))
    reporter.emit(event("checkpoint_saved", test_boot))
    reporter.refresh()
    reporter.emit(event("checkpoint_restored", test_boot))
    reporter.emit(event("passed", test_boot, target=True))
    reporter.emit(event("started", test_browser, target=True))

    output = stream.getvalue()
    assert "Catsnail run (2 tests)" in output
    assert "`- [PASS] test_boot" in output
    assert "checkpoint saved" in output
    assert "checkpoint saved" in output
    assert "   `- [RUN] test_browser" in output
    assert "1 passed, 1 running" in output
    assert "\x1b[4A\r" in output


def test_colors_live_statuses_and_reserves_a_terminal_column(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    source = add_os(Machine())

    @add_test
    async def test_boot(guest: Guest = use(source)) -> None:
        del guest

    stream = StringIO()
    reporter = ProgressReporter([test_boot], mode="tree", stream=stream)
    reporter.emit(event("started", test_boot, target=True))
    reporter.emit(event("passed", test_boot, target=True))

    output = stream.getvalue()
    assert "\x1b[33m[WAIT]\x1b[0m" in output
    assert "\x1b[96m[RUN]\x1b[0m" in output
    assert "\x1b[32m[PASS]\x1b[0m" in output


def test_marks_expected_failures_and_unexpected_passes(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    source = add_os(Machine())

    @add_test
    async def test_expected_failure(guest: Guest = use(source)) -> None:
        del guest

    stream = StringIO()
    reporter = ProgressReporter([test_expected_failure], mode="tree", stream=stream)
    reporter.emit(event("started", test_expected_failure, target=True))
    reporter.emit(event("xfail", test_expected_failure, detail="upstream issue"))
    assert "`- [XFAIL] test_expected_failure" in stream.getvalue()
    assert "1 expected failures" in stream.getvalue()

    reporter.emit(event("xpass", test_expected_failure, detail="fixed upstream"))
    assert "`- [XPASS] test_expected_failure" in stream.getvalue()
    assert "1 unexpected passes" in stream.getvalue()


def test_live_tree_refreshes_a_running_duration(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    timestamps = iter((0.0, 0.0, 0.0, 2.4))
    monkeypatch.setattr("catsnail.progress.time.monotonic", lambda: next(timestamps))
    source = add_os(Machine())

    @add_test
    async def test_boot(guest: Guest = use(source)) -> None:
        del guest

    stream = StringIO()
    reporter = ProgressReporter([test_boot], mode="tree", stream=stream)
    reporter.emit(event("started", test_boot, target=True))
    reporter.refresh()

    assert "`- [RUN] test_boot  2.4s" in stream.getvalue()


def test_checkpoint_saved_freezes_duration_before_restore(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    timestamps = iter((0.0, 0.0, 0.0, 3.0, 3.0, 9.0, 9.0, 12.0, 12.0))
    monkeypatch.setattr("catsnail.progress.time.monotonic", lambda: next(timestamps))
    source = add_os(Machine())

    @add_test
    async def test_boot(guest: Guest = use(source)) -> None:
        del guest

    stream = StringIO()
    reporter = ProgressReporter([test_boot], mode="tree", stream=stream)
    reporter.emit(event("started", test_boot))
    reporter.emit(event("checkpoint_saved", test_boot))
    reporter.emit(event("checkpoint_restored", test_boot))
    reporter.emit(event("passed", test_boot))

    output = stream.getvalue()
    assert "`- [PASS] test_boot  3.0s  checkpoint saved" in output


def test_marks_a_second_dag_parent_as_a_shared_reference() -> None:
    left_source = add_os(Machine())
    right_source = add_os(Machine())

    @add_test
    async def test_left(guest: Guest = use(left_source)) -> None:
        del guest

    @add_test
    async def test_right(guest: Guest = use(right_source)) -> None:
        del guest

    @add_test
    async def test_join(
        left: Guest = use(test_left), right: Guest = use(test_right)
    ) -> None:
        del left, right

    stream = StringIO()
    ProgressReporter(collect_test_nodes([test_join]), mode="tree", stream=stream)

    assert "-> test_join (shared)" in stream.getvalue()


def test_renders_internal_checkpoints_without_flattening_dependencies(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    source = add_os(Machine())

    @add_test
    async def test_boot(guest: Guest = use(source)) -> None:
        del guest

    @add_test(internal=True)
    async def prepare_workbench(guest: Guest = use(test_boot)) -> None:
        del guest

    @add_test
    async def test_browser(guest: Guest = use(prepare_workbench)) -> None:
        del guest

    stream = StringIO()
    ProgressReporter(
        collect_test_nodes([test_browser], include_internal=True),
        mode="tree",
        stream=stream,
    )

    output = stream.getvalue()
    assert "Catsnail run (2 tests) (1 setup steps)" in output
    assert "`- [WAIT] test_boot" in output
    assert "   `- [WAIT] prepare_workbench (setup)" in output
    assert "      `- [WAIT] test_browser" in output
    assert "2 waiting" in output


def test_dims_setup_text_without_dimming_its_state_badge(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    source = add_os(Machine())

    @add_test(internal=True)
    async def prepare(guest: Guest = use(source)) -> None:
        del guest

    stream = StringIO()
    ProgressReporter([prepare], mode="tree", stream=stream)

    output = stream.getvalue()
    assert "\x1b[33m[WAIT]\x1b[0m\x1b[2m prepare (setup)\x1b[0m" in output


def test_live_tree_compacts_a_graph_taller_than_the_terminal(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(
        "catsnail.progress.shutil.get_terminal_size",
        lambda fallback: os.terminal_size((80, 8)),
    )
    source = add_os(Machine())
    previous = None
    tests = []
    for index in range(8):
        dependency = source if previous is None else previous

        async def test_step(guest: Guest = use(dependency)) -> None:
            del guest

        test_step.__name__ = f"test_step_{index}"
        test_step.__qualname__ = test_step.__name__
        previous = add_test(test_step)
        tests.append(previous)

    stream = StringIO()
    reporter = ProgressReporter(tests, mode="tree", stream=stream)
    reporter.emit(event("started", tests[0], target=True))

    output = stream.getvalue()
    latest_frame = output.rsplit("\x1b[2K", 1)[-1]
    assert "Catsnail run (8 tests)" in output
    assert "[RUN] test_step_0" in output
    assert "unchanged nodes hidden" in output
    assert latest_frame.count("\n") <= 7


def test_plain_progress_remains_append_only() -> None:
    source = add_os(Machine())

    @add_test
    async def test_boot(guest: Guest = use(source)) -> None:
        del guest

    stream = StringIO()
    reporter = ProgressReporter([test_boot], mode="plain", stream=stream)
    reporter.emit(event("started", test_boot, target=True))
    reporter.emit(event("passed", test_boot, target=True, completed=("test_boot",)))

    assert stream.getvalue() == (f"RUN {test_boot.id}\nPASS test_boot (test_boot)\n")


def test_auto_mode_uses_plain_output_without_terminal_redraw_support(
    monkeypatch: MonkeyPatch,
) -> None:
    class _Tty(StringIO):
        def isatty(self) -> bool:
            return True

        def fileno(self) -> int:
            return 1

    source = add_os(Machine())

    @add_test
    async def test_boot(guest: Guest = use(source)) -> None:
        del guest

    monkeypatch.setattr("catsnail.progress._supports_live_tree", lambda _: False)
    stream = _Tty()
    reporter = ProgressReporter([test_boot], mode="auto", stream=stream)
    reporter.emit(event("started", test_boot, target=True))

    assert not reporter.live
    assert stream.getvalue() == f"RUN {test_boot.id}\n"


def test_live_tree_uses_an_alternate_screen_and_leaves_a_final_tree(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(
        "catsnail.progress._alternate_screen_sequences",
        lambda _: ("<enter>", "<leave>"),
    )
    source = add_os(Machine())

    @add_test
    async def test_boot(guest: Guest = use(source)) -> None:
        del guest

    stream = StringIO()
    reporter = ProgressReporter(
        [test_boot], mode="tree", stream=stream, header="Dashboard: http://test"
    )
    reporter.emit(event("started", test_boot, target=True))
    reporter.close()

    output = stream.getvalue()
    assert output.startswith("<enter>\x1b[?25l")
    assert "Dashboard: http://test" in output
    assert "<leave>Dashboard: http://test" in output
    assert "`- [RUN] test_boot" in output
