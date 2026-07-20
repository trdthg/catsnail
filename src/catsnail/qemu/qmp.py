"""Minimal client for QEMU's machine protocol."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class QmpError(RuntimeError):
    """Raised when QEMU's machine protocol rejects or loses a command."""


class QmpClient:
    """Serial QMP client for pausing and migrating a local QEMU guest."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._reader = reader
        self._writer = writer

    @classmethod
    async def connect(cls, socket_path: Path, *, timeout: float = 30) -> QmpClient:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(socket_path)
                break
            except OSError as error:
                if asyncio.get_running_loop().time() >= deadline:
                    raise QmpError(
                        f"timed out connecting to QMP at {socket_path}: {error}"
                    ) from error
                await asyncio.sleep(0.1)

        client = cls(reader, writer)
        try:
            greeting = await client._read_message(timeout=timeout)
            if "QMP" not in greeting:
                raise QmpError(f"invalid QMP greeting from {socket_path}: {greeting}")
            await client.execute("qmp_capabilities", timeout=timeout)
            return client
        except BaseException:
            await client.close()
            raise

    async def close(self) -> None:
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    async def execute(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = 30,
    ) -> Any:
        payload: dict[str, Any] = {"execute": command}
        if arguments is not None:
            payload["arguments"] = arguments
        self._writer.write(json.dumps(payload).encode("utf-8") + b"\r\n")
        await self._writer.drain()

        while True:
            message = await self._read_message(timeout=timeout)
            if "return" in message:
                return message["return"]
            if "error" in message:
                detail = message["error"].get("desc", message["error"])
                raise QmpError(f"QMP {command} failed: {detail}")

    async def pause_and_save(self, state_path: Path, *, drive_id: str) -> None:
        """Persist RAM/device state after flushing the matching QCOW2 layer."""

        await self.execute("stop")
        await self._wait_for_status({"paused"})
        await self.execute(
            "human-monitor-command",
            {"command-line": f"flush {drive_id}"},
        )
        await self.execute("migrate", {"uri": f"file:{state_path}"})

        deadline = asyncio.get_running_loop().time() + 300
        while True:
            migration = await self.execute("query-migrate")
            status = migration.get("status") if isinstance(migration, dict) else None
            if status == "completed":
                return
            if status in {"failed", "cancelled"}:
                raise QmpError(f"QEMU migration to {state_path} {status}: {migration}")
            if asyncio.get_running_loop().time() >= deadline:
                raise QmpError(
                    f"timed out saving QEMU state to {state_path}: {migration}"
                )
            await asyncio.sleep(0.1)

    async def resume(self) -> None:
        """Continue a guest restored from an incoming migration stream."""

        await self._wait_for_status({"paused", "running"})
        status = await self.execute("query-status")
        if isinstance(status, dict) and status.get("status") == "paused":
            await self.execute("cont")

    async def _wait_for_status(self, expected: set[str]) -> None:
        deadline = asyncio.get_running_loop().time() + 60
        while True:
            status = await self.execute("query-status")
            value = status.get("status") if isinstance(status, dict) else None
            if value in expected:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise QmpError(
                    f"QEMU did not reach one of {sorted(expected)}: {status}"
                )
            await asyncio.sleep(0.1)

    async def _read_message(self, *, timeout: float) -> dict[str, Any]:
        try:
            line = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
        except asyncio.TimeoutError as error:
            raise QmpError("timed out waiting for QMP response") from error
        if not line:
            raise QmpError("QMP connection closed")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise QmpError(f"invalid QMP JSON: {line!r}") from error
        if not isinstance(payload, dict):
            raise QmpError(f"invalid QMP message: {payload!r}")
        return payload
