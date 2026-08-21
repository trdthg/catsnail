"""MCP bridge that exposes one Catsnail Studio session as visual tools."""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .studio import StudioError, StudioSession

_PROTOCOL_VERSION = "2025-06-18"
_SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", _PROTOCOL_VERSION}
_INSTRUCTIONS = (
    "This server controls one restored Catsnail GUI checkpoint. Call "
    "studio_snapshot before every route, inspect its image, then make exactly "
    "one input action. Input tools require the revision from the immediately "
    "preceding image and return a new image. Do not use shell, serial, "
    "clipboard, or filesystem shortcuts to drive the guest. Reset after an "
    "unexpected frame rather than guessing coordinates."
)


class McpRequestError(StudioError):
    """A JSON-RPC request could not be fulfilled by the Studio MCP server."""

    def __init__(self, message: str, *, code: int = -32602) -> None:
        super().__init__(message)
        self.code = code


class StudioMcpServer:
    """Expose a restored Studio session with image-returning MCP tools."""

    def __init__(
        self,
        session: StudioSession,
        *,
        path: Path,
        checkpoint: str,
        target_dir: Path,
    ) -> None:
        self.session = session
        self.path = path.resolve()
        self.checkpoint = checkpoint
        self.target_dir = target_dir.resolve()
        self.stopped = False

    async def serve_forever(self) -> None:
        """Serve JSON-RPC messages on stdio until the client closes or stops."""

        try:
            while not self.stopped:
                line = await asyncio.to_thread(sys.stdin.buffer.readline)
                if not line:
                    return
                self._write(await self.handle_json_line(line))
        finally:
            await self.session.stop()

    async def handle_json_line(self, line: bytes) -> dict[str, Any] | None:
        """Handle one JSON-RPC message; notifications intentionally have no reply."""

        try:
            request = json.loads(line)
        except json.JSONDecodeError as error:
            return _error(None, -32700, f"invalid JSON: {error.msg}")
        if not isinstance(request, dict):
            return _error(None, -32600, "JSON-RPC request must be an object")
        return await self.handle_request(request)

    async def handle_request(self, request: Mapping[str, Any]) -> dict[str, Any] | None:
        """Dispatch one MCP request without requiring a live stdio transport."""

        request_id = request.get("id")
        method = request.get("method")
        if not isinstance(method, str):
            return _error(request_id, -32600, "request requires a string method")
        if method == "notifications/initialized":
            return None
        try:
            if method == "initialize":
                requested_version = _params(request).get("protocolVersion")
                protocol_version = (
                    requested_version
                    if isinstance(requested_version, str)
                    and requested_version in _SUPPORTED_PROTOCOL_VERSIONS
                    else _PROTOCOL_VERSION
                )
                result = {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "catsnail-studio", "version": "0.1.0"},
                    "instructions": _INSTRUCTIONS,
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": _tools()}
            elif method == "tools/call":
                result = await self._call_tool(_params(request))
            else:
                raise McpRequestError(f"method not found: {method}", code=-32601)
        except McpRequestError as error:
            if method == "tools/call":
                result = _tool_error(str(error))
            else:
                return _error(request_id, error.code, str(error))
        except (OSError, StudioError, ValueError, TypeError) as error:
            if method == "tools/call":
                result = _tool_error(str(error))
            else:
                return _error(request_id, -32602, str(error))
        if "id" not in request:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    async def _call_tool(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self.stopped:
            raise McpRequestError("Studio session has stopped", code=-32000)
        name = arguments.get("name")
        if not isinstance(name, str):
            raise McpRequestError("tools/call requires a string tool name")
        raw = arguments.get("arguments", {})
        if not isinstance(raw, dict):
            raise McpRequestError("tool arguments must be an object")
        values: Mapping[str, Any] = raw

        if name == "studio_snapshot":
            result = await self.session.snapshot(machine=_machine(values))
        elif name == "studio_click":
            result = await self.session.click(
                _integer(values, "x"),
                _integer(values, "y"),
                machine=_machine(values),
                expected_revision=_revision(values),
            )
        elif name == "studio_right_click":
            result = await self.session.right_click(
                _integer(values, "x"),
                _integer(values, "y"),
                machine=_machine(values),
                expected_revision=_revision(values),
            )
        elif name == "studio_middle_click":
            result = await self.session.middle_click(
                _integer(values, "x"),
                _integer(values, "y"),
                machine=_machine(values),
                expected_revision=_revision(values),
            )
        elif name == "studio_move":
            result = await self.session.move(
                _integer(values, "x"),
                _integer(values, "y"),
                machine=_machine(values),
                expected_revision=_revision(values),
            )
        elif name == "studio_type":
            result = await self.session.type(
                _string(values, "text"),
                machine=_machine(values),
                expected_revision=_revision(values),
            )
        elif name == "studio_paste":
            result = await self.session.paste(
                _string(values, "text"),
                machine=_machine(values),
                expected_revision=_revision(values),
            )
        elif name == "studio_key":
            result = await self.session.key(
                _string(values, "key"),
                machine=_machine(values),
                expected_revision=_revision(values),
            )
        elif name == "studio_shortcut":
            result = await self.session.shortcut(
                *_strings(values, "keys"),
                machine=_machine(values),
                expected_revision=_revision(values),
            )
        elif name == "studio_crop":
            result = await self.session.crop(
                _integer(values, "frame_id"),
                _integer(values, "x"),
                _integer(values, "y"),
                _integer(values, "width"),
                _integer(values, "height"),
                label=_optional_string(values, "label", "fixture"),
            )
        elif name == "studio_serial":
            result = await self.session.serial(
                machine=_machine(values),
                lines=_optional_integer(values, "lines", 100),
            )
        elif name == "studio_emit":
            result = self.session.emit(_optional_string(values, "name", "explore"))
        elif name == "studio_reset":
            result = await self._reset()
        elif name == "studio_stop":
            await self.session.stop()
            self.stopped = True
            result = {"session": self.session.session_id, "status": "stopped"}
        else:
            raise McpRequestError(f"unknown Studio tool: {name}")
        return _tool_result(result)

    async def _reset(self) -> dict[str, Any]:
        """Restore the original content-addressed checkpoint for this session."""

        session_id = self.session.session_id
        await self.session.stop()
        shutil.rmtree(self.session.directory, ignore_errors=True)
        self.session = await StudioSession.start(
            self.path,
            self.checkpoint,
            target_dir=self.target_dir,
            session_id=session_id,
        )
        return await self.session.snapshot(machine="desktop", label="reset")

    @staticmethod
    def _write(response: Mapping[str, Any] | None) -> None:
        if response is None:
            return
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _tools() -> list[dict[str, Any]]:
    machine = {
        "machine": {
            "type": "string",
            "description": "Guest name; defaults to desktop.",
        }
    }
    revision = {
        "revision": {
            "type": "integer",
            "description": "Revision returned by the immediately preceding Studio image.",
        }
    }
    point = {
        "x": {"type": "integer", "description": "Framebuffer X coordinate."},
        "y": {"type": "integer", "description": "Framebuffer Y coordinate."},
    }
    return [
        _tool("studio_snapshot", "Capture the current guest framebuffer before choosing a GUI action.", machine),
        _tool("studio_click", "Click one visible target and return the resulting framebuffer.", {**point, **revision, **machine}, ("x", "y", "revision")),
        _tool("studio_right_click", "Right-click one visible target and return the resulting framebuffer.", {**point, **revision, **machine}, ("x", "y", "revision")),
        _tool("studio_middle_click", "Middle-click one visible target and return the resulting framebuffer.", {**point, **revision, **machine}, ("x", "y", "revision")),
        _tool("studio_move", "Move the pointer once and return the resulting framebuffer.", {**point, **revision, **machine}, ("x", "y", "revision")),
        _tool("studio_type", "Type text into the focused visible control and return the resulting framebuffer.", {"text": {"type": "string", "description": "Text to enter."}, **revision, **machine}, ("text", "revision")),
        _tool("studio_paste", "Use the remote clipboard and Ctrl+V in the focused visible control, then return the resulting framebuffer.", {"text": {"type": "string", "description": "Text to paste."}, **revision, **machine}, ("text", "revision")),
        _tool("studio_key", "Press one key and return the resulting framebuffer.", {"key": {"type": "string", "description": "Key name such as ENTER or ESC."}, **revision, **machine}, ("key", "revision")),
        _tool("studio_shortcut", "Press one keyboard shortcut and return the resulting framebuffer.", {"keys": {"type": "array", "items": {"type": "string"}, "minItems": 2, "description": "Keys such as [CTRL, S]."}, **revision, **machine}, ("keys", "revision")),
        _tool("studio_crop", "Save a reviewed region from a recorded frame as a candidate PNG fixture.", {"frame_id": {"type": "integer"}, **point, "width": {"type": "integer"}, "height": {"type": "integer"}, "label": {"type": "string"}}, ("frame_id", "x", "y", "width", "height")),
        _tool("studio_serial", "Read serial output for diagnosis only; it is not evidence for a GUI action or visual acceptance.", {"lines": {"type": "integer", "minimum": 1}, **machine}),
        _tool("studio_emit", "Write a reviewable draft from the recorded Studio controls and frames.", {"name": {"type": "string"}}),
        _tool("studio_reset", "Discard exploratory changes and restore the supplied checkpoint. Returns its new framebuffer."),
        _tool("studio_stop", "Stop QEMU and close this Studio session after artifacts are emitted."),
    ]


def _tool(
    name: str,
    description: str,
    properties: Mapping[str, Any] | None = None,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": dict(properties or {})}
    if required:
        schema["required"] = list(required)
    return {"name": name, "description": description, "inputSchema": schema}


def _params(request: Mapping[str, Any]) -> Mapping[str, Any]:
    params = request.get("params", {})
    if not isinstance(params, dict):
        raise McpRequestError("request params must be an object")
    return params


def _machine(values: Mapping[str, Any]) -> str:
    return _optional_string(values, "machine", "desktop")


def _revision(values: Mapping[str, Any]) -> int:
    return _integer(values, "revision")


def _integer(values: Mapping[str, Any], name: str) -> int:
    value = values.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise McpRequestError(f"{name} must be an integer")
    return value


def _optional_integer(values: Mapping[str, Any], name: str, default: int) -> int:
    value = values.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise McpRequestError(f"{name} must be a positive integer")
    return value


def _string(values: Mapping[str, Any], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value:
        raise McpRequestError(f"{name} must be a non-empty string")
    return value


def _optional_string(values: Mapping[str, Any], name: str, default: str) -> str:
    value = values.get(name, default)
    if not isinstance(value, str) or not value:
        raise McpRequestError(f"{name} must be a non-empty string")
    return value


def _strings(values: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = values.get(name)
    if not isinstance(value, list) or len(value) < 2 or not all(
        isinstance(item, str) and item for item in value
    ):
        raise McpRequestError(f"{name} must contain at least two non-empty strings")
    return tuple(value)


def _tool_result(result: Mapping[str, Any]) -> dict[str, Any]:
    metadata = dict(result)
    image = metadata.pop("image", metadata.get("fixture"))
    content: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(metadata, ensure_ascii=False)}
    ]
    if isinstance(image, str):
        try:
            content.append(
                {
                    "type": "image",
                    "data": base64.b64encode(Path(image).read_bytes()).decode("ascii"),
                    "mimeType": "image/png",
                }
            )
        except OSError as error:
            raise McpRequestError(f"cannot read Studio screenshot {image}: {error}") from error
    return {"content": content}


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
