"""Debian-family guest capability adapter."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import shlex
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Awaitable, Callable
from uuid import uuid4
from pathlib import Path

from .adapter import GuestAdapter
from .controls import Guest, GuestControlError

if TYPE_CHECKING:
    from ..graph.api import Network


class DebianAdapter(GuestAdapter):
    """Debian-family guest capabilities.

    Operations use Debian's common command-line environment and can grow here
    without adding OS assumptions to the portable ``Guest`` controls.
    """

    def __init__(self, guest: Guest, *, sudo_password: str | None = None) -> None:
        super().__init__(guest)
        self._sudo_password = sudo_password
        self._serial: DebianSerial | None = None
        self.terminal = DebianTerminal(self)
        self.network = DebianNetwork(self)

    async def initialize(self, *, timeout: float = 30) -> None:
        """Configure Debian services required by Catsnail guest capabilities."""

        await self.terminal.run(
            "systemctl enable --now serial-getty@ttyS1.service",
            admin=True,
            timeout=timeout,
        )

    async def serial(self, *, timeout: float = 30) -> DebianSerial:
        """Return the serial terminal prepared by :meth:`initialize`."""

        if self._serial is None:
            self._serial = await DebianSerial.connect(
                self.guest._running.artifacts.serial_socket,
                release_log=self.guest.release_directory / "serial.log",
                timeout=timeout,
            )
            self.guest._register_close_callback(self._serial.close)
        return self._serial

    @staticmethod
    def _admin_command(command: str, *, password_required: bool) -> str:
        prefix = "sudo -S -k -p ''" if password_required else "sudo -n"
        return f"{prefix} -- sh -c {shlex.quote(command)}"


class DebianTerminal:
    """The Debian desktop terminal and its command execution conventions."""

    def __init__(self, adapter: DebianAdapter) -> None:
        self._adapter = adapter
        guest = adapter.guest
        self._keyboard = guest.keyboard
        self._control_url = guest._control_url
        self._after_step = guest.screen.record_step
        self._server_started = False
        self._password_required: bool | None = None

    async def run(
        self, command: str, *, admin: bool = False, timeout: float = 120.0
    ) -> None:
        """Run a shell command and wait for it to complete."""

        result = await self.command(command, admin=admin, timeout=timeout)
        await result.wait(timeout=timeout)

    async def launch(self, command: str, *, timeout: float = 120.0) -> None:
        """Launch a non-privileged background command."""

        await self._ensure_server(timeout=timeout)
        await self._keyboard.type(command)
        await self._keyboard.press("ENTER")
        await self._after_step("terminal-launched")

    async def command(
        self,
        command: str,
        *,
        admin: bool = False,
        timeout: float = 120.0,
        capture_output: bool = False,
    ) -> TerminalCommand:
        """Enter a command and return its handle for later completion checks."""

        if admin and self._adapter._sudo_password is not None:
            return await self._password_command(
                command, timeout=timeout, capture_output=capture_output
            )
        return await self._send(
            self._render(command, admin=admin),
            timeout=timeout,
            capture_output=capture_output,
        )

    async def output(
        self, command: str, *, admin: bool = False, timeout: float = 120.0
    ) -> str:
        """Run a shell command and return its standard output."""

        result = await self.command(
            command,
            admin=admin,
            timeout=timeout,
            capture_output=True,
        )
        await result.wait(timeout=timeout)
        return await result.output(timeout=timeout)

    async def focus(self) -> None:
        """Open a new terminal window and give it keyboard focus."""

        await self._keyboard.shortcut("CTRL", "ALT", "T")
        await asyncio.sleep(1)
        await self._after_step("terminal-focus")

    async def _password_command(
        self, command: str, *, timeout: float, capture_output: bool
    ) -> TerminalCommand:
        password = self._adapter._sudo_password
        assert password is not None
        password_required = await self._requires_password(timeout=timeout)
        result = await self._send(
            self._adapter._admin_command(
                command, password_required=password_required
            ),
            timeout=timeout,
            capture_output=capture_output,
        )
        if not password_required:
            return result
        # VNC acknowledges key delivery before the guest terminal has handed
        # stdin to sudo. Without this settle time, part of the password can
        # land at the next shell prompt instead of sudo's password reader.
        await asyncio.sleep(1)
        # sudo disables terminal echo while it reads this password. The
        # password is therefore not present in the command or shell history.
        await self._adapter.guest.keyboard.type(password)
        await self._adapter.guest.keyboard.press("ENTER")
        return result

    async def _requires_password(self, *, timeout: float) -> bool:
        if self._password_required is None:
            try:
                await self.run("sudo -n true", timeout=timeout)
            except GuestControlError:
                self._password_required = True
            else:
                self._password_required = False
        return self._password_required

    def _render(self, command: str, *, admin: bool) -> str:
        if not admin:
            return command
        return self._adapter._admin_command(command, password_required=False)

    async def _send(
        self,
        command: str,
        *,
        timeout: float,
        capture_output: bool,
    ) -> TerminalCommand:
        await self._ensure_server(timeout=timeout)
        token = f"catsnail-result-{uuid4().hex}"
        rendered = command
        output_url: str | None = None
        if capture_output:
            rendered = f"{command} > /tmp/{token}.out 2>&1"
            output_url = f"{self._control_url}/{token}.out"
        await self._keyboard.type(f"{rendered}; printf %s $? > /tmp/{token}")
        await self._keyboard.press("ENTER")
        return TerminalCommand(
            command=command,
            result_url=f"{self._control_url}/{token}",
            output_url=output_url,
            after_step=self._after_step,
        )

    async def _ensure_server(self, *, timeout: float) -> None:
        if self._server_started:
            return
        try:
            # A restored checkpoint can already contain the guest-side server.
            await _read_text(
                f"{self._control_url}/does-not-exist",
                min(timeout, 1),
                allow_not_found=True,
            )
        except GuestControlError:
            pass
        else:
            self._server_started = True
            return
        await self._keyboard.shortcut("CTRL", "ALT", "T")
        await asyncio.sleep(1)
        await self._keyboard.type(
            "python3 -m http.server 8123 --directory /tmp >/tmp/catsnail-http.log 2>&1 &"
        )
        await self._keyboard.press("ENTER")
        await _read_text(
            f"{self._control_url}/does-not-exist", timeout, allow_not_found=True
        )
        self._server_started = True


class DebianSerial:
    """Interactive serial terminal for Debian's ``ttyS1`` getty."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        release_log: Path,
    ) -> None:
        self._reader = reader
        self._writer = writer
        release_log.parent.mkdir(parents=True, exist_ok=True)
        self._transcript = release_log.open("w", encoding="utf-8")

    @classmethod
    async def connect(
        cls, path: Path, *, release_log: Path, timeout: float = 30
    ) -> DebianSerial:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(path)
                return cls(reader, writer, release_log=release_log)
            except OSError as error:
                if asyncio.get_running_loop().time() >= deadline:
                    raise GuestControlError(
                        f"timed out connecting to guest serial socket {path}: {error}"
                    ) from error
                await asyncio.sleep(0.1)

    async def send(self, text: str) -> None:
        self._writer.write(text.encode("utf-8"))
        await self._writer.drain()

    async def expect(self, pattern: str, *, timeout: float = 30) -> str:
        matcher = re.compile(pattern, re.MULTILINE)
        buffer = ""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise GuestControlError(
                    f"serial output did not match {pattern!r}: {buffer[-2000:]}"
                )
            try:
                chunk = await asyncio.wait_for(self._reader.read(4096), remaining)
            except TimeoutError as error:
                raise GuestControlError(
                    f"serial output did not match {pattern!r}: {buffer[-2000:]}"
                ) from error
            if not chunk:
                raise GuestControlError("guest serial connection closed")
            text = chunk.decode("utf-8", errors="replace")
            self._transcript.write(text)
            self._transcript.flush()
            buffer += text
            if matcher.search(buffer):
                return buffer

    async def close(self) -> None:
        self._transcript.close()
        self._writer.close()
        await self._writer.wait_closed()


class TerminalCommand:
    """A Debian terminal command whose exit status can be awaited."""

    def __init__(
        self,
        *,
        command: str,
        result_url: str,
        output_url: str | None,
        after_step: Callable[[str], Awaitable[None]],
    ) -> None:
        self._command = command
        self._result_url = result_url
        self._output_url = output_url
        self._after_step = after_step

    async def wait(self, *, timeout: float = 120.0) -> None:
        status = await _read_text(self._result_url, timeout)
        if status.strip() != "0":
            output = ""
            if self._output_url is not None:
                output = await _read_text(
                    self._output_url, timeout, allow_not_found=True
                )
            raise GuestControlError(
                f"guest command failed with status {status.strip() or 'unknown'}: {self._command}"
                f"\n{output[-2_000:]}"
            )
        await self._after_step("terminal-command-complete")

    async def output(self, *, timeout: float = 120.0) -> str:
        if self._output_url is None:
            return ""
        return await _read_text(self._output_url, timeout, allow_not_found=True)


class DebianNetwork:
    """Static IPv4 configuration through Debian's ``ip`` command."""

    def __init__(self, adapter: DebianAdapter) -> None:
        self._adapter = adapter

    async def static_address(
        self,
        network: Network,
        address: str,
    ) -> None:
        interface = self._adapter.guest.network.interface(network)
        try:
            parsed_address = ipaddress.ip_interface(address)
            subnet = ipaddress.ip_network(interface.subnet, strict=True)
        except ValueError as error:
            raise GuestControlError(
                f"invalid Debian private-network address {address!r}"
            ) from error
        if parsed_address.version != 4 or parsed_address.ip not in subnet:
            raise GuestControlError(
                f"address {address!r} is outside private network {interface.subnet}"
            )

        interface_file = f"/tmp/catsnail-{_shell_slug(interface.mac)}-interface"
        mac = interface.mac.lower()
        await self._adapter.terminal.run(
            "ip -o link | "
            f"awk '/{mac}/ {{name=$2; sub(/:$/, \"\", name); print name; exit}}' "
            f"> {interface_file} && test -s {interface_file}"
        )
        await self._adapter.terminal.run(
            "if command -v nmcli >/dev/null 2>&1; then "
            f"nmcli device set $(cat {interface_file}) managed no || true; "
            "fi; "
            f"ip link set $(cat {interface_file}) up",
            admin=True,
        )
        await self._adapter.terminal.run(
            f"ip addr replace {shlex.quote(str(parsed_address))} "
            f"dev $(cat {interface_file})",
            admin=True,
        )
        configured = await self._adapter.terminal.output(
            f"ip -o -4 addr show dev $(cat {interface_file})", timeout=30
        )
        if str(parsed_address.ip) not in configured:
            raise GuestControlError(
                f"Debian guest did not apply address {parsed_address} to private network"
            )


def _shell_slug(value: str) -> str:
    return (
        "".join(character if character.isalnum() else "-" for character in value).strip(
            "-"
        )
        or "network"
    )


async def _read_text(url: str, timeout: float, *, allow_not_found: bool = False) -> str:
    deadline = time.monotonic() + timeout
    latest_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            return await asyncio.to_thread(_urlopen_text, url)
        except urllib.error.HTTPError as error:
            if allow_not_found and error.code == 404:
                return ""
            latest_error = error
        except (OSError, urllib.error.URLError) as error:
            latest_error = error
        await asyncio.sleep(0.25)
    raise GuestControlError(
        f"timed out waiting for guest control endpoint {url}: {latest_error}"
    )


def _urlopen_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - localhost only
        return response.read().decode("utf-8", errors="replace")
