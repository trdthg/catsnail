from __future__ import annotations

import asyncio
import struct
import zlib
from pathlib import Path
from typing import cast

import pytest

from catsnail.qemu.vnc import Frame, VncClient, _png_chunk, _typing_key


class _Writer:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, _: bytes) -> None:
        self.writes.append(_)

    async def drain(self) -> None:
        return None


def test_typing_key_uses_explicit_shift_for_shell_punctuation() -> None:
    assert _typing_key(">") == (".", True)
    assert _typing_key("&") == ("7", True)
    assert _typing_key("A") == ("A", False)
    assert _typing_key("a") == ("a", False)


def test_frame_region_metrics(tmp_path: Path) -> None:
    frame = Frame(
        width=2,
        height=2,
        rgba=bytes(
            [
                255,
                255,
                255,
                0,
                0,
                0,
                0,
                0,
                250,
                250,
                250,
                0,
                10,
                20,
                30,
                0,
            ]
        ),
    )
    changed = Frame(width=2, height=2, rgba=bytes(16))

    assert frame.changed_pixels(changed) == 3

    image = tmp_path / "frame.png"
    frame.write_png(image)
    loaded = Frame.read_png(image)
    assert loaded == frame
    assert frame.mean_absolute_difference(loaded, x=0, y=0) == 0


def test_vnc_keeps_unchanged_pixels_across_partial_updates() -> None:
    async def exercise() -> Frame:
        reader = asyncio.StreamReader()
        client = VncClient(reader, cast(asyncio.StreamWriter, None))
        client.width = 2
        client.height = 1
        client._framebuffer = bytearray([255, 0, 0, 0, 255, 0, 0, 0])
        reader.feed_data(
            b"\x00"
            + struct.pack(">H", 1)
            + struct.pack(">HHHHi", 1, 0, 1, 1, 0)
            + bytes([0, 255, 0, 0])
        )
        reader.feed_eof()
        return await client._read_framebuffer_payload()

    frame = asyncio.run(exercise())
    assert frame.rgba == bytes([255, 0, 0, 0, 0, 255, 0, 0])


def test_vnc_frame_returns_the_latest_queued_update() -> None:
    async def exercise() -> Frame:
        reader = asyncio.StreamReader()
        client = VncClient(reader, cast(asyncio.StreamWriter, _Writer()))
        client.width = 1
        client.height = 1
        client._framebuffer = bytearray(4)
        reader.feed_data(
            b"\x00"
            + b"\x00"
            + struct.pack(">H", 1)
            + struct.pack(">HHHHi", 0, 0, 1, 1, 0)
            + bytes([255, 0, 0, 0])
            + b"\x00"
            + b"\x00"
            + struct.pack(">H", 1)
            + struct.pack(">HHHHi", 0, 0, 1, 1, 0)
            + bytes([0, 255, 0, 0])
        )
        return await client.frame(timeout=1)

    frame = asyncio.run(exercise())

    assert frame.rgba == bytes([0, 255, 0, 0])


def test_vnc_click_holds_the_button_until_the_next_compositor_tick() -> None:
    async def exercise() -> list[bytes]:
        writer = _Writer()
        client = VncClient(asyncio.StreamReader(), cast(asyncio.StreamWriter, writer))
        client.width = 1280
        client.height = 800
        await client.click(10, 20)
        return writer.writes

    writes = asyncio.run(exercise())

    assert writes == [
        struct.pack(">BBHH", 5, 1, 10, 20),
        struct.pack(">BBHH", 5, 0, 10, 20),
    ]


def test_vnc_press_holds_a_key_through_one_controller_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> tuple[list[bytes], list[float]]:
        writer = _Writer()
        client = VncClient(asyncio.StreamReader(), cast(asyncio.StreamWriter, writer))
        delays: list[float] = []

        async def record_delay(seconds: float) -> None:
            delays.append(seconds)

        monkeypatch.setattr("catsnail.qemu.vnc.asyncio.sleep", record_delay)
        await client.press(ord("x"))
        return writer.writes, delays

    writes, delays = asyncio.run(exercise())

    assert writes == [
        struct.pack(">BBHI", 4, 1, 0, ord("x")),
        struct.pack(">BBHI", 4, 0, 0, ord("x")),
    ]
    assert delays == [0.01]


def test_vnc_applies_a_desktop_resize_before_raw_pixels() -> None:
    async def exercise() -> Frame:
        reader = asyncio.StreamReader()
        client = VncClient(reader, cast(asyncio.StreamWriter, None))
        client.width = 1
        client.height = 1
        client._framebuffer = bytearray(4)
        reader.feed_data(
            b"\x00"
            + struct.pack(">H", 2)
            + struct.pack(">HHHHi", 0, 0, 2, 1, -223)
            + struct.pack(">HHHHi", 0, 0, 2, 1, 0)
            + bytes([255, 0, 0, 0, 0, 255, 0, 0])
        )
        reader.feed_eof()
        return await client._read_framebuffer_payload()

    frame = asyncio.run(exercise())

    assert (frame.width, frame.height) == (2, 1)
    assert frame.rgba == bytes([255, 0, 0, 0, 0, 255, 0, 0])


def test_reads_filtered_rgb_png(tmp_path: Path) -> None:
    # A two-pixel RGB row using PNG's Sub filter for its second pixel.
    header = struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0)
    image = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(b"\x01\x0a\x14\x1e\x05\x05\x05"))
        + _png_chunk(b"IEND", b"")
    )
    path = tmp_path / "filtered.png"
    path.write_bytes(image)

    assert Frame.read_png(path).rgba == bytes([10, 20, 30, 0, 15, 25, 35, 0])
