from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TypedDict, cast

import pytest

from catsnail import Machine, Guest, add_os, add_test, use
from catsnail.dashboard import Dashboard, _GuestView, _PAGE
from catsnail.progress import event
from catsnail.qemu.vnc import Frame


class _DashboardPayload(TypedDict):
    tests: list[dict[str, str]]
    guests: list[dict[str, str]]


async def _get(url: str) -> tuple[int, bytes]:
    host, port = url.removeprefix("http://").removesuffix("/").split(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    writer.write(b"GET /api/state HTTP/1.1\r\nHost: localhost\r\n\r\n")
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    header, body = response.split(b"\r\n\r\n", 1)
    return int(header.split()[1]), body


def test_dashboard_serves_test_state_and_guest_frame() -> None:
    source = add_os(Machine())

    @add_test
    async def test_boot(guest: Guest = use(source)) -> None:
        del guest

    async def exercise() -> tuple[int, _DashboardPayload]:
        dashboard = Dashboard([test_boot], port=0)
        await dashboard.start()
        try:
            view = _GuestView(
                id="guest-1",
                name="desktop",
                socket=Path("/tmp/catsnail-dashboard-vnc.sock"),
                frame=Frame(1, 1, bytes([255, 0, 0, 0])).to_png(),
                updated=1,
            )
            dashboard._views["desktop"] = view
            status, body = await _get(dashboard.url)
            return status, cast(_DashboardPayload, json.loads(body))
        finally:
            await dashboard.close()

    status, payload = asyncio.run(exercise())
    assert status == 200
    assert payload["tests"][0]["name"] == "test_boot"
    assert payload["tests"][0]["setup"] is False
    assert payload["guests"][0]["frame"] == "/api/guest/guest-1.png"


def test_dashboard_page_supports_guest_grid_and_expanded_frame() -> None:
    assert 'data-columns="1"' in _PAGE
    assert 'data-columns="3"' in _PAGE
    assert 'class="expand"' in _PAGE
    assert "openViewer" in _PAGE
    assert "test.setup" in _PAGE


def test_dashboard_freezes_a_checkpointed_test_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = add_os(Machine())

    @add_test
    async def test_boot(guest: Guest = use(source)) -> None:
        del guest

    times = iter((0.0, 4.0, 9.0, 12.0))
    monkeypatch.setattr("catsnail.dashboard.time.monotonic", lambda: next(times))
    dashboard = Dashboard([test_boot], port=0)
    dashboard.emit(event("started", test_boot))
    dashboard.emit(event("checkpoint_saved", test_boot))
    dashboard.emit(event("checkpoint_restored", test_boot))
    dashboard.emit(event("passed", test_boot))

    state = dashboard._states[test_boot.id]
    assert state["state"] == "PASS"
    assert state["duration"] == 4.0
    assert state["detail"] == "checkpoint saved"
