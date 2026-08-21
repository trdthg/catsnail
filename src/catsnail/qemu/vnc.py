"""Small RFB/VNC client for deterministic local QEMU tests.

It deliberately supports only QEMU's unauthenticated local Unix-socket VNC
server and raw framebuffer updates. This is enough for screenshots and basic
keyboard input without adding a GUI automation dependency.
"""

from __future__ import annotations

import asyncio
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


class VncError(RuntimeError):
    pass


@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    rgba: bytes

    def non_black_pixels(self) -> int:
        return sum(
            1
            for index in range(0, len(self.rgba), 4)
            if self.rgba[index] or self.rgba[index + 1] or self.rgba[index + 2]
        )

    def changed_pixels(self, other: Frame) -> int:
        if (self.width, self.height) != (other.width, other.height):
            return self.width * self.height
        return sum(
            1
            for index in range(0, len(self.rgba), 4)
            if self.rgba[index : index + 3] != other.rgba[index : index + 3]
        )

    def crop(self, x: int, y: int, width: int, height: int) -> Frame:
        """Return an RGB-compatible framebuffer region."""

        if width <= 0 or height <= 0:
            raise ValueError("crop width and height must be positive")
        if x < 0 or y < 0 or x + width > self.width or y + height > self.height:
            raise ValueError(
                f"crop ({x}, {y}, {width}, {height}) is outside "
                f"frame {self.width}x{self.height}"
            )
        rows = bytearray()
        for row in range(y, y + height):
            start = (row * self.width + x) * 4
            rows.extend(self.rgba[start : start + width * 4])
        return Frame(width=width, height=height, rgba=bytes(rows))

    def write_png(self, path: Path) -> None:
        """Write an 8-bit RGB PNG without requiring a third-party image library."""

        path.write_bytes(self.to_png())

    def to_png(self) -> bytes:
        """Encode the framebuffer as an 8-bit RGB PNG in memory."""

        rows = bytearray()
        for row in range(self.height):
            rows.append(0)  # PNG filter type: None.
            start = row * self.width * 4
            for pixel in range(start, start + self.width * 4, 4):
                rows.extend(self.rgba[pixel : pixel + 3])
        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", header)
            + _png_chunk(b"IDAT", zlib.compress(rows, level=6))
            + _png_chunk(b"IEND", b"")
        )

    @classmethod
    def read_png(cls, path: Path) -> Frame:
        """Load a standard non-interlaced 8-bit RGB or RGBA PNG asset."""

        raw = path.read_bytes()
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise VncError(f"invalid PNG image: {path}")
        position = 8
        header: bytes | None = None
        compressed = bytearray()
        while position < len(raw):
            if position + 12 > len(raw):
                raise VncError(f"truncated PNG image: {path}")
            length = struct.unpack(">I", raw[position : position + 4])[0]
            chunk_type = raw[position + 4 : position + 8]
            data_start = position + 8
            data_end = data_start + length
            if data_end + 4 > len(raw):
                raise VncError(f"truncated PNG image: {path}")
            data = raw[data_start:data_end]
            if chunk_type == b"IHDR":
                header = data
            elif chunk_type == b"IDAT":
                compressed.extend(data)
            elif chunk_type == b"IEND":
                break
            position = data_end + 4
        if header is None or len(header) != 13:
            raise VncError(f"missing PNG header: {path}")
        width, height, bit_depth, color_type, compression, filtering, interlace = (
            struct.unpack(">IIBBBBB", header)
        )
        if (
            bit_depth != 8
            or color_type not in (2, 6)
            or (compression, filtering, interlace) != (0, 0, 0)
        ):
            raise VncError(f"unsupported PNG encoding: {path}")
        try:
            filtered_rgb = zlib.decompress(compressed)
        except zlib.error as error:
            raise VncError(f"invalid PNG data: {path}") from error
        channels = 3 if color_type == 2 else 4
        row_size = width * channels
        if len(filtered_rgb) != height * (row_size + 1):
            raise VncError(f"unexpected PNG payload size: {path}")
        rgba = bytearray(width * height * 4)
        previous = bytearray(row_size)
        for row in range(height):
            offset = row * (row_size + 1)
            filter_type = filtered_rgb[offset]
            if filter_type > 4:
                raise VncError(f"unsupported PNG row filter: {path}")
            decoded = bytearray(row_size)
            source_row = filtered_rgb[offset + 1 : offset + 1 + row_size]
            for index, value in enumerate(source_row):
                left = decoded[index - channels] if index >= channels else 0
                up = previous[index]
                upper_left = previous[index - channels] if index >= channels else 0
                if filter_type == 0:
                    decoded[index] = value
                elif filter_type == 1:
                    decoded[index] = (value + left) & 0xFF
                elif filter_type == 2:
                    decoded[index] = (value + up) & 0xFF
                elif filter_type == 3:
                    decoded[index] = (value + ((left + up) // 2)) & 0xFF
                else:
                    decoded[index] = (value + _paeth(left, up, upper_left)) & 0xFF
            for source, destination in zip(
                range(0, row_size, channels),
                range(row * width * 4, (row + 1) * width * 4, 4),
            ):
                rgba[destination : destination + 3] = decoded[source : source + 3]
            previous = decoded
        return cls(width=width, height=height, rgba=bytes(rgba))

    def mean_absolute_difference(self, template: Frame, *, x: int, y: int) -> float:
        """Return the average per-channel RGB difference for a positioned image."""

        if (
            x < 0
            or y < 0
            or x + template.width > self.width
            or y + template.height > self.height
        ):
            raise ValueError("template is outside the framebuffer")
        difference = 0
        channel_count = template.width * template.height * 3
        for row in range(template.height):
            source = ((y + row) * self.width + x) * 4
            expected = row * template.width * 4
            for column in range(template.width):
                offset = column * 4
                difference += abs(
                    self.rgba[source + offset] - template.rgba[expected + offset]
                )
                difference += abs(
                    self.rgba[source + offset + 1]
                    - template.rgba[expected + offset + 1]
                )
                difference += abs(
                    self.rgba[source + offset + 2]
                    - template.rgba[expected + offset + 2]
                )
        return difference / channel_count


class VncClient:
    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.width = 0
        self.height = 0
        self._framebuffer = bytearray()
        self._extended_key_events = False
        self._last_update_had_pixels = False

    @classmethod
    async def connect(cls, path: Path, timeout: float = 30.0) -> VncClient:
        async def open_client() -> VncClient:
            reader, writer = await asyncio.open_unix_connection(path)
            client = cls(reader, writer)
            await client._handshake()
            return client

        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            try:
                return await open_client()
            except (FileNotFoundError, ConnectionRefusedError, VncError):
                if asyncio.get_running_loop().time() >= deadline:
                    raise VncError(f"timed out waiting for VNC socket: {path}")
                await asyncio.sleep(0.25)

    async def close(self) -> None:
        self.writer.close()
        await self.writer.wait_closed()

    async def frame(self, timeout: float = 20.0) -> Frame:
        deadline = asyncio.get_running_loop().time() + timeout
        latest: Frame | None = None
        had_pixels = False
        # QEMU confirms Extended Key Event support with a framebuffer update
        # containing only encoding -258. Ask again in that case: that reply is
        # protocol negotiation, not a screenshot, and the framebuffer remains
        # black until a subsequent request carries raw pixels.
        for _ in range(2):
            self.writer.write(
                struct.pack(">BBHHHH", 3, 0, 0, 0, self.width, self.height)
            )
            await self.writer.drain()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for VNC framebuffer update")
            latest = await asyncio.wait_for(
                self._read_framebuffer_update(), timeout=remaining
            )
            had_pixels = had_pixels or self._last_update_had_pixels
            # Keyboard and pointer events can leave several incremental updates
            # in flight. Consume the queued tail so captures show the current
            # desktop, rather than the first update after a request.
            while cast(Any, self.reader)._buffer:
                latest = await self._read_framebuffer_update()
                had_pixels = had_pixels or self._last_update_had_pixels
            if had_pixels:
                return latest
        assert latest is not None
        return latest

    async def key(self, keysym: int, down: bool = True) -> None:
        keycode = _qnum_for_keysym(keysym) if self._extended_key_events else None
        if keycode is not None:
            self.writer.write(
                struct.pack(
                    ">BBHII",
                    255,
                    0,
                    int(down),
                    keysym,
                    keycode,
                )
            )
            await self.writer.drain()
            return
        self.writer.write(struct.pack(">BBHI", 4, int(down), 0, keysym))
        await self.writer.drain()

    async def press(self, keysym: int) -> None:
        await self.key(keysym, True)
        # QEMU's VNC server accepts both events immediately, while the guest
        # still models them through a PS/2 controller. Holding a key through
        # one controller tick prevents intermittent missing characters in
        # long terminal commands.
        await asyncio.sleep(0.01)
        await self.key(keysym, False)

    async def click(self, x: int, y: int, *, button: int = 1) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise VncError(f"pointer coordinates outside framebuffer: ({x}, {y})")
        button_masks = {1: 1, 2: 2, 3: 4}
        mask = button_masks.get(button)
        if mask is None:
            raise ValueError(f"unsupported VNC mouse button: {button}")
        # Some guest desktops do not dispatch a button event reliably when
        # the first event also changes the absolute pointer position. Send a
        # separate motion event and let the compositor consume it first.
        await self.move(x, y)
        await asyncio.sleep(0.05)
        self.writer.write(struct.pack(">BBHH", 5, mask, x, y))
        await self.writer.drain()
        # SWT/XWayland and Flutter/Wayland clients can drop a press-release
        # pair delivered in the same VNC write. Keep the button down across
        # several compositor ticks so a focused button also receives its
        # activation event.
        await asyncio.sleep(0.15)
        self.writer.write(struct.pack(">BBHH", 5, 0, x, y))
        await self.writer.drain()

    async def move(self, x: int, y: int) -> None:
        """Move the pointer without pressing a button."""

        if not (0 <= x < self.width and 0 <= y < self.height):
            raise VncError(f"pointer coordinates outside framebuffer: ({x}, {y})")
        self.writer.write(struct.pack(">BBHH", 5, 0, x, y))
        await self.writer.drain()

    async def scroll(self, x: int, y: int, amount: int) -> None:
        """Scroll at ``(x, y)`` using standard RFB wheel button events.

        Positive values scroll up and negative values scroll down.  RFB
        represents each wheel notch as a temporary pointer button rather than
        a delta, so send one event per requested notch.
        """

        if not (0 <= x < self.width and 0 <= y < self.height):
            raise VncError(f"pointer coordinates outside framebuffer: ({x}, {y})")
        if amount == 0:
            return
        mask = 8 if amount > 0 else 16
        await self.move(x, y)
        for _ in range(abs(amount)):
            self.writer.write(struct.pack(">BBHH", 5, mask, x, y))
            await self.writer.drain()
            # Leave the widget one compositor turn per wheel notch.  In
            # particular, SWT tables coalesce wheel events received together.
            await asyncio.sleep(0.02)

    async def type_text(self, value: str) -> None:
        for character in value:
            key, shifted = _typing_key(character)
            if shifted:
                await self.key(0xFFE1, True)
            try:
                await self.press(ord(key))
            finally:
                if shifted:
                    await self.key(0xFFE1, False)
            # QEMU accepts VNC key events faster than its PS/2 emulation and
            # guest compositor can consume them.  A tiny pace prevents long
            # shell commands from losing their tail under load.
            await asyncio.sleep(0.01)
        if value:
            # ``drain()`` confirms delivery to the VNC server, not that QEMU's
            # PS/2 queue has consumed the final key.  In particular, pressing
            # Enter immediately can replace the last character of a long
            # command. Leave the guest one input tick before the next action.
            await asyncio.sleep(0.1)

    async def paste_text(self, value: str) -> None:
        """Offer text through RFB's clipboard channel for a normal Ctrl+V."""

        payload = value.encode("utf-8")
        if len(payload) > 1 << 20:
            raise VncError("VNC clipboard text exceeds the 1 MiB limit")
        self.writer.write(struct.pack(">BxxxI", 6, len(payload)) + payload)
        await self.writer.drain()
        await self.key(0xFFE3, True)
        try:
            await self.press(ord("v"))
        finally:
            await self.key(0xFFE3, False)
        await asyncio.sleep(0.1)

    async def _handshake(self) -> None:
        version = await self.reader.readexactly(12)
        if not version.startswith(b"RFB "):
            raise VncError(f"unexpected VNC server banner: {version!r}")
        self.writer.write(b"RFB 003.008\n")
        await self.writer.drain()

        count = (await self.reader.readexactly(1))[0]
        if count == 0:
            length = struct.unpack(">I", await self.reader.readexactly(4))[0]
            raise VncError(
                (await self.reader.readexactly(length)).decode(errors="replace")
            )
        security_types = await self.reader.readexactly(count)
        if 1 not in security_types:
            raise VncError(
                f"local VNC requires unsupported security: {list(security_types)}"
            )
        self.writer.write(b"\x01")
        await self.writer.drain()
        result = struct.unpack(">I", await self.reader.readexactly(4))[0]
        if result:
            length = struct.unpack(">I", await self.reader.readexactly(4))[0]
            raise VncError(
                (await self.reader.readexactly(length)).decode(errors="replace")
            )

        self.writer.write(b"\x01")
        await self.writer.drain()
        header = await self.reader.readexactly(24)
        self.width, self.height = struct.unpack(">HH", header[:4])
        self._framebuffer = bytearray(self.width * self.height * 4)
        name_length = struct.unpack(">I", header[20:24])[0]
        await self.reader.readexactly(name_length)
        # 32-bit little-endian RGBX makes QEMU raw updates straightforward.
        self.writer.write(
            struct.pack(
                ">BxxxBBBBHHHBBBxxx",
                0,
                32,
                24,
                0,
                1,
                255,
                255,
                255,
                16,
                8,
                0,
            )
        )
        # Request raw pixels, DesktopSize, and QEMU's extended-key protocol.
        # The latter carries physical key codes, which SWT uses to construct
        # text input on recent desktop sessions.
        self.writer.write(struct.pack(">BBHiii", 2, 0, 3, 0, -223, -258))
        await self.writer.drain()

    async def _read_framebuffer_update(self) -> Frame:
        while True:
            message_type = (await self.reader.readexactly(1))[0]
            if message_type == 0:
                return await self._read_framebuffer_payload()
            if message_type == 2:
                continue
            if message_type == 3:
                await self.reader.readexactly(3)
                length = struct.unpack(">I", await self.reader.readexactly(4))[0]
                await self.reader.readexactly(length)
                continue
            raise VncError(f"unsupported VNC server message: {message_type}")

    async def _read_framebuffer_payload(self) -> Frame:
        await self.reader.readexactly(1)
        rectangle_count = struct.unpack(">H", await self.reader.readexactly(2))[0]
        # QEMU may return only changed rectangles even after a non-incremental
        # request. Preserve pixels from the prior update so recordings always
        # contain a complete desktop rather than a top-left update rectangle.
        rgba = bytearray(self._framebuffer)
        self._last_update_had_pixels = False
        for _ in range(rectangle_count):
            x, y, width, height, encoding = struct.unpack(
                ">HHHHi", await self.reader.readexactly(12)
            )
            if encoding == -223:
                self.width, self.height = width, height
                rgba = bytearray(width * height * 4)
                continue
            if encoding == -258:
                self._extended_key_events = True
                continue
            if encoding != 0:
                raise VncError(f"unsupported framebuffer encoding: {encoding}")
            self._last_update_had_pixels = True
            payload = await self.reader.readexactly(width * height * 4)
            for row in range(height):
                source = row * width * 4
                destination = ((y + row) * self.width + x) * 4
                rgba[destination : destination + width * 4] = payload[
                    source : source + width * 4
                ]
        self._framebuffer = rgba
        return Frame(self.width, self.height, bytes(rgba))


def _typing_key(character: str) -> tuple[str, bool]:
    """Translate US-layout text into physical VNC key presses.

    QEMU's VNC keyboard mapping accepts printable letters directly, but sends
    several shifted punctuation keysyms (notably ``>`` and ``&``) as their
    unshifted physical keys.  Holding Shift explicitly keeps shell commands
    correct on the Debian Live US layout used by the fixture.
    """

    shifted = {
        "~": "`",
        "!": "1",
        "@": "2",
        "#": "3",
        "$": "4",
        "%": "5",
        "^": "6",
        "&": "7",
        "*": "8",
        "(": "9",
        ")": "0",
        "_": "-",
        "+": "=",
        "{": "[",
        "}": "]",
        "|": "\\",
        ":": ";",
        '"': "'",
        "<": ",",
        ">": ".",
        "?": "/",
    }
    if character in shifted:
        return shifted[character], True
    # QEMU's VNC endpoint handles alphabetic upper-case keysyms directly.
    # Unlike punctuation, synthesising Shift plus a lower-case keysym loses
    # the modifier on the Debian guest keyboard map.
    if "A" <= character <= "Z":
        return character, False
    if ord(character) < 128:
        return character, False
    raise VncError(f"VNC text input only supports ASCII: {character!r}")


def _qnum_for_keysym(keysym: int) -> int | None:
    """Return QEMU's XT key number for common US-layout VNC keysyms."""

    if 0 <= keysym < 128:
        return _US_QNUM.get(chr(keysym))
    special = {
        0xFF08: 0x0E,  # Backspace
        0xFF09: 0x0F,  # Tab
        0xFF0D: 0x1C,  # Enter
        0xFF1B: 0x01,  # Escape
        0xFFE1: 0x2A,  # Left Shift
        0xFFE3: 0x1D,  # Left Control
        0xFFE9: 0x38,  # Left Alt
        0xFF51: 0xCB,  # Left
        0xFF52: 0xC8,  # Up
        0xFF53: 0xCD,  # Right
        0xFF54: 0xD0,  # Down
        0xFF67: 0xDD,  # Menu
    }
    if 0xFFBE <= keysym <= 0xFFC9:
        return 0x3B + keysym - 0xFFBE
    return special.get(keysym)


_US_QNUM = {
    **dict(zip("1234567890-=", range(0x02, 0x0E))),
    **dict(zip("qwertyuiop[]", range(0x10, 0x1C))),
    **dict(zip("asdfghjkl;'", range(0x1E, 0x29))),
    "`": 0x29,
    "\\": 0x2B,
    **dict(zip("zxcvbnm,./", range(0x2C, 0x36))),
    " ": 0x39,
}


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def _paeth(left: int, up: int, upper_left: int) -> int:
    prediction = left + up - upper_left
    left_distance = abs(prediction - left)
    up_distance = abs(prediction - up)
    upper_left_distance = abs(prediction - upper_left)
    if left_distance <= up_distance and left_distance <= upper_left_distance:
        return left
    if up_distance <= upper_left_distance:
        return up
    return upper_left
