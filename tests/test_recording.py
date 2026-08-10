from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from catsnail.guest.recording import StepRecorder
from catsnail.qemu.vnc import Frame


def test_recorder_keeps_keyframes_and_manifest_without_ffmpeg(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr("catsnail.guest.recording.shutil.which", lambda _: None)
    recorder = StepRecorder(tmp_path)
    frame = Frame(width=2, height=2, rgba=bytes([255, 0, 0, 0] * 4))

    keyframe = recorder.add(frame, "Keyboard Type")
    asyncio.run(recorder.finalize())

    assert keyframe.name == "000001-keyboard-type.png"
    assert keyframe.exists()
    manifest = json.loads(
        (recorder.directory / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["events"] == [{"frame": "000001.png", "label": "Keyboard Type"}]
    assert (recorder.directory / "recording-error.txt").exists()


def test_recorder_preserves_unicode_keyframe_labels(tmp_path: Path) -> None:
    recorder = StepRecorder(tmp_path)
    frame = Frame(width=1, height=1, rgba=bytes([255, 255, 255, 0]))

    keyframe = recorder.add(frame, "打开新闻")

    assert keyframe.name == "000001-打开新闻.png"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_recorder_assembles_an_mp4_when_ffmpeg_is_available(tmp_path: Path) -> None:
    debug_directory = tmp_path / "debug"
    release_directory = tmp_path / "release"
    recorder = StepRecorder(debug_directory, release_directory)
    frame = Frame(width=2, height=2, rgba=bytes([255, 0, 0, 0] * 4))

    recorder.add(frame, "terminal-command-complete")
    asyncio.run(recorder.finalize())

    assert recorder.video_path.is_file()
    assert recorder.video_path.stat().st_size > 0
    assert recorder.frames_directory.is_relative_to(debug_directory)
    assert recorder.video_path == release_directory / "recording.mp4"


def test_recorder_can_publish_to_a_logical_test_directory(tmp_path: Path) -> None:
    recorder = StepRecorder(tmp_path / "debug", tmp_path / "release" / "boot")
    logical_directory = tmp_path / "release" / "test_desktop_login"

    recorder.set_release_directory(logical_directory)

    assert recorder.video_path == logical_directory / "recording.mp4"
