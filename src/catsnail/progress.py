"""Progress reporting for Catsnail's declarative test graph."""

from __future__ import annotations

import curses
import os
import shutil
import sys
import time
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TextIO

from .graph.api import TestNode


EventKind = Literal[
    "started",
    "checkpoint_saved",
    "checkpoint_restored",
    "passed",
    "failed",
    "xfail",
    "xpass",
    "cancelled",
]
ProgressMode = Literal["auto", "tree", "plain"]
EventSink = Callable[["RunEvent"], None]


@dataclass(frozen=True)
class RunEvent:
    """One execution-state update emitted by the scheduler or executor."""

    kind: EventKind
    node_id: str
    name: str
    target: bool = False
    detail: str = ""
    completed: tuple[str, ...] = ()
    duration: float | None = None


def event(
    kind: EventKind,
    node: TestNode[Any],
    *,
    target: bool = False,
    detail: str = "",
    completed: Iterable[str] = (),
    duration: float | None = None,
) -> RunEvent:
    """Build an event without exposing the presentation layer to graph internals."""

    return RunEvent(
        kind=kind,
        node_id=node.id,
        name=node.function.__name__,
        target=target,
        detail=detail,
        completed=tuple(completed),
        duration=duration,
    )


def collect_test_nodes(
    targets: Iterable[TestNode[Any]], *, include_internal: bool = False
) -> list[TestNode[Any]]:
    """Return selected targets and dependencies in stable order.

    ``internal`` checkpoints normally remain absent from the executable test
    count. The progress tree can opt in to them so its indentation represents
    the actual checkpoint graph instead of flattening hidden setup nodes.
    """

    nodes: list[TestNode[Any]] = []
    seen: set[str] = set()

    def visit(node: TestNode[Any]) -> None:
        if node.id in seen:
            return
        for dependency in node.dependencies.values():
            if isinstance(dependency.node, TestNode):
                visit(dependency.node)
        seen.add(node.id)
        if include_internal or not node.internal:
            nodes.append(node)

    for target in targets:
        visit(target)
    return nodes


@dataclass
class _NodeState:
    state: str = "WAIT"
    detail: str = ""
    started: float | None = None
    duration: float | None = None


@dataclass(frozen=True)
class _TreeEntry:
    node_id: str
    prefix: str
    reference: bool = False


class ProgressReporter:
    """Render graph progress as a live terminal tree or durable plain logs."""

    def __init__(
        self,
        nodes: Sequence[TestNode[Any]],
        *,
        mode: ProgressMode = "auto",
        stream: TextIO | None = None,
        header: str | None = None,
    ) -> None:
        if mode not in {"auto", "tree", "plain"}:
            raise ValueError(f"unsupported progress mode {mode!r}")
        self._stream = stream or sys.stdout
        self._live = mode == "tree" or (
            mode == "auto" and _supports_live_tree(self._stream)
        )
        self._color = self._live and "NO_COLOR" not in os.environ
        self._nodes = _unique_nodes(nodes)
        self._nodes_by_id = {node.id: node for node in self._nodes}
        self._test_count = sum(not node.internal for node in self._nodes)
        self._setup_count = len(self._nodes) - self._test_count
        self._labels = _labels(self._nodes)
        self._tree = _tree_entries(self._nodes)
        self._states = {node.id: _NodeState() for node in self._nodes}
        self._rendered_lines = 0
        self._header_lines = (header,) if header else ()
        self._alternate_screen = (
            _alternate_screen_sequences(self._stream) if self._live else None
        )
        self._closed = False
        if self._live:
            self._enter_alternate_screen()
            self._render_tree()

    def emit(self, update: RunEvent) -> None:
        """Record one update and redraw the tree or append a plain progress line."""

        state = self._states.get(update.node_id)
        if state is None:
            return
        now = time.monotonic()
        if update.kind == "started":
            if state.state != "RUN":
                state.started = now
            state.state = "RUN"
            state.detail = ""
        elif update.kind == "checkpoint_saved":
            # Publishing a checkpoint happens only after the test function has
            # completed. A later QEMU restore prepares a child test and must
            # not keep adding time to this test's completed duration.
            state.state = "PASS"
            state.duration = _duration_since(state.started, now)
            state.detail = "checkpoint saved"
        elif update.kind == "checkpoint_restored":
            if state.state == "WAIT":
                state.state = "CACHE"
            if state.state not in {"PASS", "FAIL", "XFAIL", "XPASS", "CANCEL"}:
                state.detail = "checkpoint restored"
        elif update.kind == "passed":
            checkpoint_completed = state.state == "PASS" and state.duration is not None
            state.state = "PASS"
            if not checkpoint_completed:
                state.duration = (
                    update.duration
                    if update.duration is not None
                    else _duration_since(state.started, now)
                )
            if update.detail:
                state.detail = update.detail
        elif update.kind == "failed":
            state.state = "FAIL"
            state.duration = _duration_since(state.started, now)
            state.detail = update.detail
        elif update.kind == "xfail":
            state.state = "XFAIL"
            state.duration = _duration_since(state.started, now)
            state.detail = update.detail
        elif update.kind == "xpass":
            state.state = "XPASS"
            state.duration = _duration_since(state.started, now)
            state.detail = update.detail
        elif update.kind == "cancelled":
            if state.state not in {"PASS", "FAIL", "XFAIL", "XPASS"}:
                state.state = "CANCEL"
                state.detail = update.detail

        if self._live and not self._closed:
            self._render_tree()
        elif update.target:
            self._write_plain(update)

    def refresh(self) -> None:
        """Redraw live progress so running durations continue to advance."""

        if self._live and not self._closed:
            self._render_tree()

    def close(self) -> None:
        """Finish a live display and leave one durable final tree behind."""

        if self._closed:
            return
        self._closed = True
        if not self._live:
            return
        if self._alternate_screen is None:
            return
        _, leave = self._alternate_screen
        self._stream.write("\x1b[?25h" + leave)
        self._stream.flush()
        self._rendered_lines = 0
        for line in self._tree_lines():
            self._write(line + "\n")

    def render_graph(self) -> None:
        """Write the current dependency tree once without terminal redraws."""

        for line in self._tree_lines():
            self._write(line + "\n")

    @property
    def live(self) -> bool:
        """Whether this reporter is actively redrawing an interactive tree."""

        return self._live

    def _write_plain(self, update: RunEvent) -> None:
        if update.kind == "started":
            self._write(f"RUN {update.node_id}\n")
        elif update.kind == "passed":
            completed = ", ".join(update.completed)
            self._write(f"PASS {update.name} ({completed})\n")
        elif update.kind == "failed":
            self._write(f"FAIL {update.name}: {update.detail}\n")
        elif update.kind == "xfail":
            self._write(f"XFAIL {update.name}: {update.detail}\n")
        elif update.kind == "xpass":
            self._write(f"XPASS {update.name}: {update.detail}\n")

    def _render_tree(self) -> None:
        lines = self._live_tree_lines()

        if self._alternate_screen is not None:
            # A full-screen redraw does not depend on the terminal retaining
            # the previous cursor row. This keeps the view intact even when a
            # subprocess writes diagnostics or the graph changes height.
            self._stream.write("\x1b[H\x1b[2J")
            for line in lines:
                self._stream.write(line + "\n")
            self._stream.write("\x1b[J")
            self._stream.flush()
            self._rendered_lines = len(lines)
            return

        if self._rendered_lines:
            # ``CSI A`` is supported by more terminal emulators and output
            # panes than the less common cursor-previous-line ``CSI F``.
            self._stream.write(f"\x1b[{self._rendered_lines}A\r")
        for index in range(max(self._rendered_lines, len(lines))):
            line = lines[index] if index < len(lines) else ""
            self._stream.write("\x1b[2K" + line + "\n")
        self._stream.flush()
        self._rendered_lines = len(lines)

    def _live_tree_lines(self) -> list[str]:
        """Return a redrawable view that never scrolls the terminal.

        A cursor-up redraw only works while every prior line remains on screen.
        Large test graphs therefore show their active path and collapse idle
        branches instead of allowing the terminal to scroll and corrupt the
        next refresh.
        """

        lines = self._tree_lines()
        rows = shutil.get_terminal_size(fallback=(80, 24)).lines
        # The trailing newline also occupies a terminal row in common
        # emulators, so leave one row free before a following redraw.
        if len(lines) < rows:
            return lines

        active = {
            node.id
            for node in self._nodes
            if self._states[node.id].state in {"RUN", "CACHE", "FAIL", "XFAIL", "XPASS"}
        }
        if not active:
            active.update(
                entry.node_id
                for entry in self._tree
                if not entry.prefix.startswith(" ")
            )

        # Keep the entire dependency path to each visible node so indentation
        # continues to describe the real graph even in its compact form.
        by_id = self._nodes_by_id
        pending = list(active)
        while pending:
            node = by_id[pending.pop()]
            for dependency in node.dependencies.values():
                parent = dependency.node
                if isinstance(parent, TestNode) and parent.id not in active:
                    active.add(parent.id)
                    pending.append(parent.id)

        compact_nodes = [node for node in self._nodes if node.id in active]
        entries = _tree_entries(compact_nodes)
        # Header, title, collapsed-branch marker and summary need three rows
        # plus any caller-provided header rows. Retain a further blank-safe row
        # for the final newline.
        entry_budget = max(1, rows - len(self._header_lines) - 4)
        displayed = entries[:entry_budget]
        hidden = len(self._nodes) - len(displayed)

        compact_lines = [*self._header_lines, self._title_line()]
        for entry in displayed:
            compact_lines.append(self._entry_line(entry))
        if hidden:
            compact_lines.append(_fit_line(f"... {hidden} unchanged nodes hidden"))
        compact_lines.append(self._summary_line())
        return compact_lines

    def _tree_lines(self) -> list[str]:
        now = time.monotonic()
        lines = [*self._header_lines, self._title_line()]
        for entry in self._tree:
            lines.append(self._entry_line(entry, now))
        lines.append(self._summary_line())
        return lines

    def _title_line(self) -> str:
        title = f"Catsnail run ({self._test_count} tests)"
        if self._setup_count:
            title += f" ({self._setup_count} setup steps)"
        return _fit_line(title)

    def _entry_line(self, entry: _TreeEntry, now: float | None = None) -> str:
        node = self._nodes_by_id[entry.node_id]
        label = self._labels[entry.node_id]
        if node.internal:
            label += " (setup)"
        if entry.reference:
            line = _fit_line(f"{entry.prefix}-> {label} (shared)")
            return self._dim_setup(line, node)
        state = self._states[entry.node_id]
        details = _state_details(state, time.monotonic() if now is None else now)
        suffix = f"  {details}" if details else ""
        status = f"[{state.state}]"
        prefix = f"{entry.prefix}{status}"
        text = _fit_line(f"{prefix} {label}{suffix}")[len(prefix) :]
        badge = self._color_status(status, state.state)
        if node.internal:
            # Preserve the state badge's color. The rest of a setup node is
            # dimmed to distinguish framework prerequisites from @add_test.
            return (
                f"{entry.prefix}{badge}\x1b[2m{text}\x1b[0m"
                if self._color
                else f"{entry.prefix}{badge}{text}"
            )
        return f"{entry.prefix}{badge}{text}"

    def _dim_setup(self, line: str, node: TestNode[Any]) -> str:
        return f"\x1b[2m{line}\x1b[0m" if self._color and node.internal else line

    def _summary_line(self) -> str:
        return _fit_line(
            _summary(self._states[node.id] for node in self._nodes if not node.internal)
        )

    def _color_status(self, line: str, state: str) -> str:
        line = _fit_line(line)
        if not self._color:
            return line
        color = {
            "PASS": "32",
            "FAIL": "31",
            "XFAIL": "33",
            "XPASS": "35",
            "WAIT": "33",
            "RUN": "96",
            "CACHE": "36",
            "CANCEL": "90",
        }[state]
        token = f"[{state}]"
        return line.replace(token, f"\x1b[{color}m{token}\x1b[0m", 1)

    def _write(self, message: str) -> None:
        self._stream.write(message)
        self._stream.flush()

    def _enter_alternate_screen(self) -> None:
        if self._alternate_screen is None:
            return
        enter, _ = self._alternate_screen
        self._stream.write(enter + "\x1b[?25l")
        self._stream.flush()


def _supports_live_tree(stream: TextIO) -> bool:
    """Return whether the active terminal can redraw an ANSI tree in place."""

    if not stream.isatty() or os.environ.get("TERM", "") in {"", "dumb"}:
        return False
    try:
        curses.setupterm(fd=stream.fileno())
        return curses.tigetstr("cuu1") is not None and curses.tigetstr("el") is not None
    except (AttributeError, OSError, ValueError, curses.error):
        return False


def _alternate_screen_sequences(stream: TextIO) -> tuple[str, str] | None:
    """Return portable alternate-screen controls for a real terminal."""

    if not _is_terminal(stream):
        return None
    return "\x1b[?1049h\x1b[H\x1b[2J", "\x1b[?1049l"


def _is_terminal(stream: TextIO) -> bool:
    try:
        return stream.isatty() and stream.fileno() >= 0
    except (AttributeError, OSError, ValueError):
        return False


def _unique_nodes(nodes: Sequence[TestNode[Any]]) -> list[TestNode[Any]]:
    unique: list[TestNode[Any]] = []
    seen: set[str] = set()
    for node in nodes:
        if node.id not in seen:
            seen.add(node.id)
            unique.append(node)
    return unique


def _labels(nodes: Sequence[TestNode[Any]]) -> dict[str, str]:
    count: dict[str, int] = {}
    for node in nodes:
        name = node.function.__name__
        count[name] = count.get(name, 0) + 1
    return {
        node.id: (
            node.function.__name__
            if count[node.function.__name__] == 1
            else f"{_module_label(node)}::{node.function.__name__}"
        )
        for node in nodes
    }


def _module_label(node: TestNode[Any]) -> str:
    module = node.function.__module__
    if module.startswith("catsnail_user_"):
        return module[len("catsnail_user_") :].rsplit("_", 1)[0]
    return module


def _tree_entries(nodes: Sequence[TestNode[Any]]) -> list[_TreeEntry]:
    known = {node.id for node in nodes}
    dependencies: dict[str, list[str]] = {}
    for node in nodes:
        direct: list[str] = []
        for dependency in node.dependencies.values():
            candidate = dependency.node
            if isinstance(candidate, TestNode) and candidate.id in known:
                if candidate.id not in direct:
                    direct.append(candidate.id)
        dependencies[node.id] = direct

    children: dict[str, list[tuple[str, bool]]] = {node.id: [] for node in nodes}
    roots: list[str] = []
    for node in nodes:
        parents = dependencies[node.id]
        if not parents:
            roots.append(node.id)
            continue
        children[parents[0]].append((node.id, False))
        for parent in parents[1:]:
            children[parent].append((node.id, True))

    entries: list[_TreeEntry] = []

    def append(node_id: str, prefix: str, last: bool, reference: bool) -> None:
        branch = "`- " if last else "+- "
        entries.append(_TreeEntry(node_id, prefix + branch, reference))
        if reference:
            return
        descendants = children[node_id]
        child_prefix = prefix + ("   " if last else "|  ")
        for index, (child, child_reference) in enumerate(descendants):
            append(
                child,
                child_prefix,
                index == len(descendants) - 1,
                child_reference,
            )

    for index, root in enumerate(roots):
        append(root, "", index == len(roots) - 1, False)
    return entries


def _duration_since(started: float | None, now: float) -> float | None:
    return None if started is None else now - started


def _state_details(state: _NodeState, now: float) -> str:
    duration = state.duration
    if state.state == "RUN":
        duration = _duration_since(state.started, now)
    duration_text = "" if duration is None else f"{duration:.1f}s"
    return "  ".join(part for part in (duration_text, state.detail) if part)


def _summary(states: Iterable[_NodeState]) -> str:
    counts: dict[str, int] = {}
    for state in states:
        counts[state.state] = counts.get(state.state, 0) + 1
    labels = {
        "WAIT": "waiting",
        "RUN": "running",
        "PASS": "passed",
        "FAIL": "failed",
        "XFAIL": "expected failures",
        "XPASS": "unexpected passes",
        "CACHE": "cached",
        "CANCEL": "cancelled",
    }
    return (
        ", ".join(
            f"{counts[state]} {labels[state]}"
            for state in (
                "PASS",
                "FAIL",
                "XFAIL",
                "XPASS",
                "RUN",
                "WAIT",
                "CACHE",
                "CANCEL",
            )
            if counts.get(state)
        )
        or "no tests"
    )


def _fit_line(line: str) -> str:
    """Keep ANSI redraws inside one physical terminal row.

    A line exactly as wide as the terminal still wraps in many emulators,
    causing the next cursor-up redraw to leave stale headers and summaries.
    """

    width = max(1, shutil.get_terminal_size(fallback=(80, 24)).columns - 1)
    result: list[str] = []
    used = 0
    for character in line:
        character_width = _display_width(character)
        if used + character_width > width:
            break
        result.append(character)
        used += character_width
    return "".join(result)


def _display_width(character: str) -> int:
    """Return a terminal column width without adding a third-party package."""

    if unicodedata.combining(character):
        return 0
    return 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
