from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from catsnail.studio_mcp import StudioMcpServer


class FakeSession:
    def __init__(self, image: Path) -> None:
        self.session_id = "studio-mcp-test"
        self.directory = image.parent
        self.image = image
        self.calls: list[tuple[str, object]] = []
        self.stopped = False

    async def snapshot(self, *, machine: str = "desktop", label: str = "snapshot"):
        self.calls.append(("snapshot", (machine, label)))
        return _frame(self.image, revision=len(self.calls))

    async def click(self, x: int, y: int, *, machine: str, expected_revision: int):
        self.calls.append(("click", (x, y, machine, expected_revision)))
        return _frame(self.image, revision=len(self.calls))

    async def stop(self) -> None:
        self.stopped = True


def _frame(image: Path, *, revision: int) -> dict[str, object]:
    return {
        "session": "studio-mcp-test",
        "frame_id": revision,
        "image": str(image),
        "width": 1,
        "height": 1,
        "sha256": "frame",
        "revision": revision,
    }


def _server(tmp_path: Path) -> tuple[StudioMcpServer, FakeSession, bytes]:
    image = tmp_path / "screen.png"
    raw = b"png-bytes"
    image.write_bytes(raw)
    session = FakeSession(image)
    return (
        StudioMcpServer(
            session,  # type: ignore[arg-type]
            path=tmp_path / "scenario.py",
            checkpoint="test_ready",
            target_dir=tmp_path / "target",
        ),
        session,
        raw,
    )


def test_mcp_initialization_declares_visual_revision_protocol(tmp_path: Path) -> None:
    server, _, _ = _server(tmp_path)

    response = asyncio.run(
        server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    )

    assert response is not None
    assert response["result"]["serverInfo"]["name"] == "catsnail-studio"
    assert "revision" in response["result"]["instructions"]

    tools = asyncio.run(
        server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    )
    assert tools is not None
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert {"studio_snapshot", "studio_click", "studio_paste", "studio_reset", "studio_stop"} <= names


def test_mcp_input_returns_a_png_and_requires_the_current_revision(tmp_path: Path) -> None:
    server, session, raw = _server(tmp_path)

    response = asyncio.run(
        server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "studio_click",
                    "arguments": {"x": 40, "y": 50, "revision": 7},
                },
            }
        )
    )

    assert response is not None
    content = response["result"]["content"]
    assert json.loads(content[0]["text"])["revision"] == 1
    assert base64.b64decode(content[1]["data"]) == raw
    assert session.calls == [("click", (40, 50, "desktop", 7))]

    invalid = asyncio.run(
        server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "studio_click", "arguments": {"x": 40, "y": 50}},
            }
        )
    )
    assert invalid is not None
    assert invalid["result"]["isError"] is True
    assert invalid["result"]["content"][0]["text"] == "revision must be an integer"


def test_mcp_stop_terminates_the_owned_session(tmp_path: Path) -> None:
    server, session, _ = _server(tmp_path)

    response = asyncio.run(
        server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "studio_stop", "arguments": {}},
            }
        )
    )

    assert response is not None
    assert server.stopped is True
    assert session.stopped is True
