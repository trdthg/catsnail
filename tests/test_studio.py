from __future__ import annotations

import asyncio
import json
from pathlib import Path

from catsnail.qemu.vnc import Frame
from catsnail.studio import StudioEvent, StudioSession, StudioSessionStore


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
