from __future__ import annotations

from io import StringIO

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
    reporter.emit(event("checkpoint_restored", test_boot))
    reporter.emit(event("passed", test_boot, target=True))
    reporter.emit(event("started", test_browser, target=True))

    output = stream.getvalue()
    assert "Catsnail run (2 tests)" in output
    assert "`- [PASS] test_boot" in output
    assert "checkpoint restored" in output
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
