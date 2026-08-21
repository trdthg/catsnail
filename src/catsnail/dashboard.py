"""Small local dashboard for a running Catsnail test graph."""

from __future__ import annotations

import asyncio
import json
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .graph.api import TestNode
from .guest.controls import Guest
from .progress import RunEvent
from .qemu.vnc import VncClient


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Catsnail</title>
<style>
:root { --guest-columns: 2; color-scheme: dark; font: 14px system-ui, sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; background: #111827; color: #e5e7eb; }
header { padding: 16px 20px; border-bottom: 1px solid #374151; display: flex; justify-content: space-between; gap: 16px; align-items: center; }
h1 { font-size: 18px; margin: 0; }
#summary { color: #9ca3af; }
.header-actions { display: flex; align-items: center; gap: 14px; }
.layout { display: flex; border: 1px solid #4b5563; border-radius: 4px; overflow: hidden; }
.layout button { width: 30px; height: 28px; border: 0; border-right: 1px solid #4b5563; background: #1f2937; color: #9ca3af; cursor: pointer; }
.layout button:last-child { border-right: 0; }
.layout button.active { background: #0e7490; color: #ecfeff; }
main { padding: 16px 20px; display: grid; gap: 20px; }
section { min-width: 0; }
h2 { font-size: 14px; margin: 0 0 10px; color: #9ca3af; text-transform: uppercase; letter-spacing: .08em; }
#guests { display: grid; grid-template-columns: repeat(var(--guest-columns), minmax(0, 1fr)); gap: 12px; }
.guest { background: #1f2937; border: 1px solid #374151; border-radius: 6px; overflow: hidden; }
.guest-head { padding: 9px 12px; display: flex; justify-content: space-between; color: #d1d5db; }
.guest-head small { color: #9ca3af; }
.screen-slot { position: relative; aspect-ratio: 16 / 10; display: grid; place-items: center; background: #030712; }
.screen { display: block; width: 100%; height: 100%; object-fit: contain; background: #030712; }
.offline { aspect-ratio: 16 / 10; display: grid; place-items: center; color: #6b7280; }
.expand { position: absolute; right: 10px; bottom: 10px; width: 30px; height: 30px; border: 1px solid #6b7280; border-radius: 4px; background: #111827d9; color: #e5e7eb; cursor: pointer; font-size: 18px; line-height: 1; }
.expand:disabled { opacity: .35; cursor: default; }
dialog { width: min(96vw, 1800px); max-width: none; margin: auto; padding: 0; border: 1px solid #4b5563; border-radius: 6px; background: #030712; }
dialog::backdrop { background: #030712cc; }
#viewer-image { display: block; width: 100%; max-height: 92vh; object-fit: contain; }
#viewer-close { position: fixed; top: 14px; right: 16px; width: 36px; height: 36px; border: 1px solid #9ca3af; border-radius: 4px; background: #111827e6; color: #f9fafb; font-size: 24px; cursor: pointer; }
table { width: 100%; border-collapse: collapse; background: #1f2937; border: 1px solid #374151; }
td { padding: 8px 10px; border-bottom: 1px solid #374151; vertical-align: top; }
tr:last-child td { border-bottom: 0; }
.status { font-weight: 650; white-space: nowrap; }
tr.setup td:not(.status) { opacity: .62; }
.PASS { color: #4ade80; } .FAIL { color: #f87171; } .XFAIL { color: #facc15; } .XPASS { color: #e879f9; } .RUN { color: #67e8f9; }
.WAIT { color: #facc15; } .CACHE { color: #22d3ee; } .CANCEL { color: #9ca3af; }
.detail { color: #9ca3af; font-size: 12px; }
@media (max-width: 720px) { :root { --guest-columns: 1 !important; } header { align-items: flex-start; } .header-actions { gap: 8px; flex-wrap: wrap; justify-content: flex-end; } main { padding: 12px; } }
</style>
</head>
<body>
<header><h1>Catsnail</h1><div class="header-actions"><div class="layout" role="group" aria-label="Guest columns"><button data-columns="1" title="One column">1</button><button data-columns="2" title="Two columns">2</button><button data-columns="3" title="Three columns">3</button></div><div id="summary">connecting...</div></div></header>
<main><section><h2>Guests</h2><div id="guests"></div></section>
<section><h2>Tests</h2><table><tbody id="tests"></tbody></table></section></main>
<dialog id="viewer"><button id="viewer-close" title="Close" aria-label="Close">x</button><img id="viewer-image" alt="Guest screen"></dialog>
<script>
const viewer = document.querySelector('#viewer');
const viewerImage = document.querySelector('#viewer-image');
function setColumns(value) {
  document.documentElement.style.setProperty('--guest-columns', value);
  document.querySelectorAll('[data-columns]').forEach(button => button.classList.toggle('active', button.dataset.columns === value));
  localStorage.setItem('catsnail-guest-columns', value);
}
const storedColumns = localStorage.getItem('catsnail-guest-columns');
setColumns(['1', '2', '3'].includes(storedColumns) ? storedColumns : '2');
document.querySelectorAll('[data-columns]').forEach(button => button.addEventListener('click', () => setColumns(button.dataset.columns)));
document.querySelector('#viewer-close').addEventListener('click', () => viewer.close());
viewer.addEventListener('click', event => { if (event.target === viewer) viewer.close(); });
function openViewer(image, name) { viewerImage.src = image.src; viewerImage.alt = name; viewer.showModal(); }
const esc = value => String(value).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));
function render(data) {
  const counts = {};
  data.tests.forEach(test => counts[test.state] = (counts[test.state] || 0) + 1);
  document.querySelector('#summary').textContent = Object.entries(counts).map(([key, value]) => `${value} ${key}`).join('  ') || 'no tests';
  const guestRoot = document.querySelector('#guests');
  const empty = guestRoot.querySelector(':scope > .detail');
  if (empty && data.guests.length) empty.remove();
  const visible = new Set();
  data.guests.forEach(guest => {
    visible.add(guest.id);
    let card = guestRoot.querySelector(`[data-guest-id="${guest.id}"]`);
    if (!card) {
      card = document.createElement('article');
      card.className = 'guest';
      card.dataset.guestId = guest.id;
      card.innerHTML = '<div class="guest-head"><span></span><small></small></div><div class="screen-slot"><div class="offline"></div><button class="expand" title="Expand" aria-label="Expand guest screen" disabled>⛶</button></div>';
      guestRoot.appendChild(card);
    }
    card.querySelector('.guest-head span').textContent = guest.name;
    card.querySelector('.guest-head small').textContent = guest.active ? 'RUNNING' : 'STOPPED';
    const slot = card.querySelector('.screen-slot');
    const expand = slot.querySelector('.expand');
    if (!guest.frame) {
      let offline = slot.querySelector('.offline');
      if (!offline) { offline = document.createElement('div'); offline.className = 'offline'; slot.prepend(offline); }
      offline.textContent = guest.error || 'waiting for frame';
      expand.disabled = true;
      delete slot.dataset.updated;
      return;
    }
    const current = slot.querySelector('img.screen');
    if (slot.dataset.updated === String(guest.updated)) return;
    slot.dataset.updated = String(guest.updated);
    const next = new Image();
    next.className = 'screen';
    next.alt = guest.name;
    next.dataset.updated = String(guest.updated);
    next.onload = () => {
      if (slot.dataset.updated !== next.dataset.updated) return;
      current?.remove();
      slot.querySelector('.offline')?.remove();
      slot.prepend(next);
      expand.disabled = false;
      expand.onclick = () => openViewer(next, guest.name);
    };
    next.src = `${guest.frame}?t=${guest.updated}`;
  });
  guestRoot.querySelectorAll('.guest').forEach(card => {
    if (!visible.has(card.dataset.guestId)) card.remove();
  });
  if (!data.guests.length && !guestRoot.children.length) guestRoot.innerHTML = '<div class="detail">No active guests</div>';
  document.querySelector('#tests').innerHTML = data.tests.map(test => `<tr class="${test.setup ? 'setup' : ''}"><td class="status ${test.state}">${esc(test.state)}</td><td>${esc(test.name)}${test.setup ? '<span class="detail"> (setup)</span>' : ''}<div class="detail">${test.duration == null ? '' : `${test.duration.toFixed(1)}s`} ${esc(test.detail || '')}</div></td></tr>`).join('');
}
async function refresh() {
  try { const response = await fetch('/api/state', {cache: 'no-store'}); render(await response.json()); }
  catch (error) { document.querySelector('#summary').textContent = 'dashboard disconnected'; }
}
refresh(); setInterval(refresh, 500);
</script>
</body></html>
"""


@dataclass
class _GuestView:
    id: str
    name: str
    socket: Path
    active: bool = True
    frame: bytes | None = None
    updated: int = 0
    error: str = ""
    task: asyncio.Task[None] | None = None


class Dashboard:
    """Serve a local read-only view of test status and guest framebuffers."""

    def __init__(self, nodes: list[TestNode[Any]], *, port: int = 8765) -> None:
        self._nodes = nodes
        self._states = {
            node.id: {
                "id": node.id,
                "name": node.function.__name__,
                "setup": node.internal,
                "state": "WAIT",
                "detail": "",
                "started": None,
                "duration": None,
            }
            for node in nodes
        }
        self._views: dict[str, _GuestView] = {}
        self._server: asyncio.AbstractServer | None = None
        self._requested_port = port
        self._port = port
        self._next_guest = 1

    @property
    def url(self) -> str:
        return f"http://{_local_address()}:{self._port}/"

    async def start(self) -> None:
        """Start on localhost, choosing an available port if necessary."""

        try:
            self._server = await asyncio.start_server(
                self._handle_client, "0.0.0.0", self._requested_port
            )
        except OSError:
            self._server = await asyncio.start_server(
                self._handle_client, "0.0.0.0", 0
            )
        sockets = self._server.sockets or []
        if not sockets:
            raise RuntimeError("dashboard server did not expose a socket")
        self._port = int(sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        tasks = [view.task for view in self._views.values() if view.task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._views.clear()

    def emit(self, update: RunEvent) -> None:
        state = self._states.get(update.node_id)
        if state is None:
            return
        now = time.monotonic()
        kind = update.kind
        if kind == "started":
            state["state"] = "RUN"
            state["detail"] = ""
            state["started"] = now
        elif kind == "checkpoint_saved":
            state["state"] = "PASS"
            state["duration"] = _duration(state["started"], now)
            state["detail"] = "checkpoint saved"
        elif kind == "checkpoint_restored":
            if state["state"] == "WAIT":
                state["state"] = "CACHE"
            if state["state"] not in {"PASS", "FAIL", "XFAIL", "XPASS", "CANCEL"}:
                state["detail"] = "checkpoint restored"
        elif kind in {"passed", "failed"}:
            checkpoint_completed = state["state"] == "PASS" and state["duration"] is not None
            state["state"] = "PASS" if kind == "passed" else "FAIL"
            if not checkpoint_completed:
                state["duration"] = _duration(state["started"], now)
            if update.detail:
                state["detail"] = update.detail
        elif kind in {"xfail", "xpass"}:
            state["state"] = kind.upper()
            state["duration"] = _duration(state["started"], now)
            state["detail"] = update.detail
        elif kind == "cancelled" and state["state"] not in {"PASS", "FAIL", "XFAIL", "XPASS"}:
            state["state"] = "CANCEL"
            state["detail"] = update.detail

    def register_guest(self, guest: Guest) -> None:
        key = str(guest.artifacts)
        if key in self._views:
            return
        view = _GuestView(
            id=f"guest-{self._next_guest}",
            name=guest.source_id,
            socket=guest.vnc_socket,
        )
        self._next_guest += 1
        self._views[key] = view
        view.task = asyncio.create_task(self._poll(view))

    def unregister_guest(self, guest: Guest) -> None:
        view = self._views.get(str(guest.artifacts))
        if view is not None:
            view.active = False
            if view.task is not None and not view.task.done():
                view.task.cancel()

    async def _poll(self, view: _GuestView) -> None:
        client: VncClient | None = None
        try:
            while view.active:
                try:
                    client = await VncClient.connect(view.socket, timeout=3)
                    while view.active:
                        frame = await client.frame(timeout=5)
                        if (view.frame is None or frame.non_black_pixels() > 100):
                            view.frame = frame.to_png()
                            view.updated = int(time.time() * 1000)
                        view.error = ""
                        await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    view.error = str(error) or type(error).__name__
                    await asyncio.sleep(1)
                finally:
                    if client is not None:
                        try:
                            await client.close()
                        except (OSError, ConnectionError):
                            pass
                        client = None
        except asyncio.CancelledError:
            return

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = await reader.readline()
            if not request.startswith(b"GET "):
                await self._respond(writer, 405, b"method not allowed", "text/plain")
                return
            target = request.split()[1].decode("ascii", errors="ignore")
            while await reader.readline() not in {b"\r\n", b"\n", b""}:
                pass
            path = urlsplit(target).path
            if path == "/":
                await self._respond(writer, 200, _PAGE.encode(), "text/html; charset=utf-8")
            elif path == "/api/state":
                await self._respond(writer, 200, self._state_json(), "application/json")
            elif path.startswith("/api/guest/") and path.endswith(".png"):
                guest_id = path[len("/api/guest/") : -4]
                view = next((item for item in self._views.values() if item.id == guest_id), None)
                if view is None or view.frame is None:
                    await self._respond(writer, 404, b"frame unavailable", "text/plain")
                else:
                    await self._respond(writer, 200, view.frame, "image/png")
            else:
                await self._respond(writer, 404, b"not found", "text/plain")
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def _respond(
        self, writer: asyncio.StreamWriter, status: int, body: bytes, content_type: str
    ) -> None:
        reason = {200: "OK", 404: "Not Found", 405: "Method Not Allowed"}[status]
        header = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        writer.write(header + body)
        await writer.drain()

    def _state_json(self) -> bytes:
        now = time.monotonic()
        tests = []
        for state in self._states.values():
            item = dict(state)
            if item["state"] == "RUN":
                item["duration"] = _duration(item["started"], now)
            item.pop("started", None)
            tests.append(item)
        guests = [
            {
                "id": view.id,
                "name": view.name,
                "active": view.active,
                "frame": f"/api/guest/{view.id}.png" if view.frame else None,
                "updated": view.updated,
                "error": view.error,
            }
            for view in self._views.values()
        ]
        return json.dumps(
            {"tests": tests, "guests": guests}, ensure_ascii=False
        ).encode("utf-8")


def _duration(started: float | None, now: float) -> float | None:
    return None if started is None else max(0.0, now - started)


def _local_address() -> str:
    """Find the host address peers on the local network can use."""

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 80))
        address = probe.getsockname()[0]
        return address if address != "0.0.0.0" else "127.0.0.1"
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()
