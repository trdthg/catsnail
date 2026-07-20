from __future__ import annotations

import asyncio
import struct
import zlib
from pathlib import Path
from typing import cast

from catsnail.qemu.vnc import Frame, VncClient, _png_chunk, _typing_key


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
