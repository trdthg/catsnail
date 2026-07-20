"""Step-boundary screenshot recording and MP4 assembly."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..qemu.vnc import Frame


@dataclass
class StepRecorder:
    """Persist visual checkpoints and assemble them into a concise video."""

    artifact_directory: Path
    release_directory: Path | None = None
    frames_per_second: int = 2
    directory: Path = field(init=False)
    frames_directory: Path = field(init=False)
    keyframes_directory: Path = field(init=False)
    _events: list[dict[str, object]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.directory = self.artifact_directory / "recording"
        self.frames_directory = self.directory / "frames"
        self.keyframes_directory = self.directory / "keyframes"
        self.frames_directory.mkdir(parents=True, exist_ok=True)
        self.keyframes_directory.mkdir(exist_ok=True)
        if self.release_directory is not None:
            self.release_directory.mkdir(parents=True, exist_ok=True)

    @property
    def video_path(self) -> Path:
        return (self.release_directory or self.directory) / "recording.mp4"

    def set_release_directory(self, directory: Path) -> None:
        """Publish subsequent screenshots and the final video to ``directory``."""

        directory.mkdir(parents=True, exist_ok=True)
        self.release_directory = directory

    def add(self, frame: Frame, label: str) -> Path:
        index = len(self._events) + 1
        frame_path = self.frames_directory / f"{index:06d}.png"
        frame.write_png(frame_path)
        keyframe_path = self.keyframes_directory / f"{index:06d}-{_slug(label)}.png"
        try:
            os.link(frame_path, keyframe_path)
        except OSError:
            shutil.copyfile(frame_path, keyframe_path)
        self._events.append({"frame": frame_path.name, "label": label})
        return keyframe_path

    async def finalize(self) -> None:
        manifest = self.directory / "manifest.json"
        manifest.write_text(
            json.dumps(
                {"fps": self.frames_per_second, "events": self._events}, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        if not self._events:
            return

        executable = shutil.which("ffmpeg")
        if executable is None:
            self._write_error("ffmpeg was not found; retained PNG keyframes only\n")
            return

        stderr_path = self.directory / "ffmpeg.stderr.log"
        with stderr_path.open("wb") as stderr:
            process = await asyncio.create_subprocess_exec(
                executable,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-framerate",
                str(self.frames_per_second),
                "-i",
                str(self.frames_directory / "%06d.png"),
                "-an",
                "-vf",
                "format=yuv420p",
                str(self.video_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=stderr,
            )
            try:
                returncode = await asyncio.wait_for(process.wait(), timeout=120)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                self._write_error("ffmpeg timed out while assembling recording.mp4\n")
                return
        if returncode != 0:
            self._write_error(
                f"ffmpeg exited with status {returncode}; see ffmpeg.stderr.log\n"
            )

    def _write_error(self, message: str) -> None:
        (self.directory / "recording-error.txt").write_text(message, encoding="utf-8")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "step"
