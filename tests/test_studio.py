from __future__ import annotations

import asyncio
import json
from pathlib import Path

from catsnail.qemu.vnc import Frame
from catsnail.studio import (
    StudioEvent,
    StudioSession,
    StudioSessionStore,
    _dispatch_studio_request,
)


def _session(tmp_path: Path) -> StudioSession:
    store = StudioSessionStore(tmp_path / "target")
    session_id = "session-test"
    directory = store.directory(session_id)
    directory.mkdir(parents=True)
    (directory / "frames").mkdir()
    (directory / "fixtures").mkdir()
    manifest = {"id": session_id, "status": "active", "machines": {}}
    store.write(session_id, manifest)
    return StudioSession(
        store=store,
        session_id=session_id,
        manifest=manifest,
        machines={},
    )


def test_studio_event_is_jsonl_and_crop_creates_fixture(tmp_path: Path) -> None:
    session = _session(tmp_path)
    frame = Frame(
        width=2,
        height=1,
        rgba=bytes([255, 0, 0, 0, 0, 255, 0, 0]),
    )
    frame.write_png(session.frames_directory / "000001-desktop-action.png")
    session._frame_id = 1
    session._append_event(
        StudioEvent(
            id=1,
            machine="desktop",
            action="click",
            args={"x": 1, "y": 2},
            before=None,
            after=1,
            duration=0.1,
        )
    )

    result = asyncio.run(session.crop(1, 1, 0, 1, 1, label="green"))

    assert Path(result["fixture"]).is_file()
    first_event = json.loads(session.events_path.read_text(encoding="utf-8").splitlines()[0])
    assert first_event["action"] == "click"


def test_studio_emit_exports_a_reviewable_python_draft(tmp_path: Path) -> None:
    session = _session(tmp_path)
    frame = Frame(width=1, height=1, rgba=bytes([1, 2, 3, 0]))
    frame.write_png(session.frames_directory / "000002-desktop-click.png")
    session._frame_id = 2
    session._append_event(
        StudioEvent(
            id=2,
            machine="desktop",
            action="click",
            args={"x": 3, "y": 4},
            before=1,
            after=2,
            duration=0.1,
        )
    )

    result = session.emit("draft")
    generated = Path(result["test"]).read_text(encoding="utf-8")

    assert "@add_test" in generated
    assert "guest.screen.click(3, 4)" in generated
    assert "assert_screen" in generated
    assert Path(result["report"]).is_file()


def test_studio_request_dispatch_supports_emit_and_stop(tmp_path: Path) -> None:
    session = _session(tmp_path)
    stopped = False

    async def stop() -> None:
        nonlocal stopped
        stopped = True

    session.stop = stop  # type: ignore[method-assign]

    emitted = asyncio.run(
        _dispatch_studio_request(
            session, {"method": "session.emit", "params": {"name": "draft"}}
        )
    )
    asyncio.run(_dispatch_studio_request(session, {"method": "session.stop"}))

    assert Path(emitted["test"]).is_file()
    assert stopped


def test_studio_emit_preserves_right_clicks(tmp_path: Path) -> None:
    session = _session(tmp_path)
    frame = Frame(width=1, height=1, rgba=bytes([1, 2, 3, 0]))
    frame.write_png(session.frames_directory / "000002-desktop-right-click.png")
    session._append_event(
        StudioEvent(
            id=2,
            machine="desktop",
            action="right-click",
            args={"x": 3, "y": 4},
            before=1,
            after=2,
            duration=0.1,
        )
    )

    result = session.emit("context-menu")
    generated = Path(result["test"]).read_text(encoding="utf-8")

    assert "guest.screen.right_click(3, 4)" in generated


def test_studio_emit_preserves_middle_clicks(tmp_path: Path) -> None:
    session = _session(tmp_path)
    frame = Frame(width=1, height=1, rgba=bytes([1, 2, 3, 0]))
    frame.write_png(session.frames_directory / "000002-desktop-middle-click.png")
    session._append_event(
        StudioEvent(
            id=2,
            machine="desktop",
            action="middle-click",
            args={"x": 3, "y": 4},
            before=1,
            after=2,
            duration=0.1,
        )
    )

    result = session.emit("primary-selection")
    generated = Path(result["test"]).read_text(encoding="utf-8")

    assert "guest.screen.middle_click(3, 4)" in generated


def test_studio_emit_keeps_assets_for_each_draft(tmp_path: Path) -> None:
    session = _session(tmp_path)
    first = Frame(width=1, height=1, rgba=bytes([1, 2, 3, 0]))
    first.write_png(session.frames_directory / "000001-desktop-click.png")
    session._append_event(
        StudioEvent(1, "desktop", "click", {"x": 1, "y": 1}, None, 1, 0.1)
    )
    first_result = session.emit("open-news")

    second = Frame(width=1, height=1, rgba=bytes([4, 5, 6, 0]))
    second.write_png(session.frames_directory / "000001-desktop-click.png")
    second_result = session.emit("unread-news")

    first_assets = Path(first_result["assets"])
    second_assets = Path(second_result["assets"])
    assert first_assets != second_assets
    assert (first_assets / "0001-click.png").read_bytes() != (
        second_assets / "0001-click.png"
    ).read_bytes()


def test_studio_emit_preserves_keyboard_shortcuts(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session._append_event(
        StudioEvent(1, "desktop", "shortcut", {"keys": ["ALT", "F4"]}, None, None, 0.1)
    )

    result = session.emit("restart")

    generated = Path(result["test"]).read_text(encoding="utf-8")
    assert "await guest.keyboard.shortcut('ALT', 'F4')" in generated
