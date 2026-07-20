from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from catsnail.graph.api import NetSocket, Network, add_net
from catsnail.guest.controls import Screen
from catsnail.guest import (
    DebianAdapter,
    DebianSerial,
    Guest,
    GuestControlError,
    GuestNetwork,
    NetworkInterface,
)
from catsnail.qemu.vnc import Frame, VncClient


class _Terminal:
    def __init__(self) -> None:
        self.commands: list[str] = []

    async def run(self, command: str, *, timeout: float = 120.0) -> None:
        del timeout
        self.commands.append(command)

    async def output(self, command: str, *, timeout: float) -> str:
        del timeout
        self.commands.append(command)
        return "2: enp0s8    inet 192.168.76.10/24"

    async def command(
        self,
        command: str,
        *,
        timeout: float,
        capture_output: bool = False,
    ) -> _TerminalCommand:
        del timeout
        self.commands.append(command)
        return _TerminalCommand(capture_output=capture_output)


class _PasswordTerminal(_Terminal):
    async def command(
        self,
        command: str,
        *,
        timeout: float,
        capture_output: bool = False,
    ) -> _TerminalCommand:
        if command == "sudo -n true":
            raise GuestControlError("sudo password is required")
        return await super().command(
            command, timeout=timeout, capture_output=capture_output
        )


class _TerminalCommand:
    def __init__(self, *, capture_output: bool = False) -> None:
        self._capture_output = capture_output

    async def wait(self, *, timeout: float) -> None:
        del timeout

    async def output(self, *, timeout: float) -> str:
        del timeout
        return "2: enp0s8    inet 192.168.76.10/24" if self._capture_output else ""


class _Serial:
    async def close(self) -> None:
        return None


class _SerialWriter:
    def write(self, _: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


class _Guest:
    def __init__(self, network: GuestNetwork) -> None:
        self._control_url = "http://127.0.0.1:8123"
        self._running = SimpleNamespace(
            artifacts=SimpleNamespace(serial_socket=Path("/tmp/catsnail-test-serial.sock"))
        )
        self.release_directory = Path("/tmp/catsnail-test-release")
        self._close_callbacks: list[object] = []
        self.network = network
        self.keyboard = _Keyboard()
        self.screen = _Screen()

    def _register_close_callback(self, callback: object) -> None:
        self._close_callbacks.append(callback)


class _Screen:
    async def record_step(self, _: str) -> None:
        return None


class _Keyboard:
    def __init__(self) -> None:
        self.actions: list[str] = []

    async def shortcut(self, *keys: str) -> None:
        self.actions.append("shortcut-" + "-".join(keys))

    async def type(self, value: str) -> None:
        self.actions.append(value)

    async def press(self, key: str) -> None:
        self.actions.append("key-" + key)


class _Vnc:
    async def frame(self) -> Frame:
        return Frame(width=1, height=1, rgba=bytes([255, 255, 255, 0]))


class _ResizingVnc:
    def __init__(self) -> None:
        self.frames = [
            Frame(width=1, height=1, rgba=bytes([0, 0, 0, 0])),
            Frame(width=2, height=1, rgba=bytes([255, 255, 255, 0] * 2)),
        ]

    async def frame(self, **_: object) -> Frame:
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]


def _linux_guest() -> tuple[Guest, _Terminal, Network]:
    terminal = _Terminal()
    ssh_network = add_net(NetSocket(subnet="192.168.76.0/24"))
    network = GuestNetwork(
        (NetworkInterface(ssh_network, "192.168.76.0/24", "52:54:00:12:34:56"),),
    )
    guest = cast(Guest, _Guest(network))
    return guest, terminal, ssh_network


def _debian(
    guest: Guest,
    terminal: _Terminal,
    *,
    sudo_password: str | None = None,
) -> DebianAdapter:
    adapter = DebianAdapter(guest, sudo_password=sudo_password)
    adapter.terminal._send = cast(Any, terminal.command)
    return adapter


def test_core_network_reports_the_declared_private_nic() -> None:
    guest, _, ssh_network = _linux_guest()

    interface = guest.network.interface(ssh_network)

    assert interface.subnet == "192.168.76.0/24"
    assert interface.mac == "52:54:00:12:34:56"


def test_debian_adapter_configures_the_declared_private_nic_by_mac_address() -> None:
    guest, terminal, ssh_network = _linux_guest()

    asyncio.run(
        _debian(guest, terminal, sudo_password="live").network.static_address(
            ssh_network,
            "192.168.76.10/24",
        )
    )

    assert "ip -o link" in terminal.commands[0]
    assert terminal.commands[1] == "sudo -n true"
    assert "nmcli device set" in terminal.commands[2]
    assert "ip link set" in terminal.commands[2]
    assert "192.168.76.10/24" in terminal.commands[3]
    assert "ip -o -4 addr" in terminal.commands[4]


def test_debian_adapter_runs_an_arbitrary_administrator_command() -> None:
    guest, terminal, _ = _linux_guest()

    asyncio.run(
        _debian(guest, terminal, sudo_password="live").terminal.run(
            "apt-get update && apt-get install --yes openssh-server", admin=True
        )
    )

    assert terminal.commands == [
        "sudo -n true",
        "sudo -n -- sh -c 'apt-get update && apt-get install --yes openssh-server'",
    ]


def test_debian_adapter_initializes_serial_once_in_a_checkpoint_stage() -> None:
    guest, terminal, _ = _linux_guest()
    debian = _debian(guest, terminal, sudo_password="live")

    asyncio.run(debian.initialize())

    assert terminal.commands == [
        "sudo -n true",
        "sudo -n -- sh -c 'systemctl enable --now serial-getty@ttyS1.service'",
    ]


def test_debian_adapter_connects_and_caches_its_serial_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guest, terminal, _ = _linux_guest()
    debian = _debian(guest, terminal)
    serial = cast(DebianSerial, _Serial())
    connected: list[Path] = []
    release_logs: list[Path] = []

    async def connect(
        path: Path, *, release_log: Path, timeout: float = 30
    ) -> DebianSerial:
        del timeout
        connected.append(path)
        release_logs.append(release_log)
        return serial

    monkeypatch.setattr(DebianSerial, "connect", connect)

    assert asyncio.run(debian.serial()) is serial
    assert asyncio.run(debian.serial()) is serial
    assert connected == [Path("/tmp/catsnail-test-serial.sock")]
    assert release_logs == [Path("/tmp/catsnail-test-release/serial.log")]
    assert len(cast(_Guest, guest)._close_callbacks) == 1


def test_debian_serial_publishes_guest_output_to_the_release_log(
    tmp_path: Path,
) -> None:
    async def exercise() -> str:
        reader = asyncio.StreamReader()
        serial = DebianSerial(
            reader,
            cast(asyncio.StreamWriter, _SerialWriter()),
            release_log=tmp_path / "release" / "serial.log",
        )
        reader.feed_data(b"Debian login: ")
        result = await serial.expect(r"login:")
        await serial.close()
        return result

    assert asyncio.run(exercise()) == "Debian login: "
    assert (tmp_path / "release" / "serial.log").read_text(encoding="utf-8") == (
        "Debian login: "
    )


def test_debian_adapter_waits_for_sudo_to_accept_terminal_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, ssh_network = _linux_guest()
    terminal = _PasswordTerminal()
    guest = cast(
        Guest,
        _Guest(
            GuestNetwork(
                (
                    NetworkInterface(
                        ssh_network,
                        "192.168.76.0/24",
                        "52:54:00:12:34:56",
                    ),
                )
            ),
        ),
    )
    delays: list[float] = []

    async def record_delay(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("catsnail.guest.debian.asyncio.sleep", record_delay)

    asyncio.run(
        _debian(guest, terminal, sudo_password="live").terminal.run(
            "ip link set ens4 up", admin=True
        )
    )

    assert delays == [1]
    assert cast(_Guest, guest).keyboard.actions == ["live", "key-ENTER"]


def test_terminal_reuses_the_control_server_after_a_checkpoint_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def server_is_running(*_: object, **__: object) -> str:
        return ""

    monkeypatch.setattr("catsnail.guest.debian._read_text", server_is_running)
    guest, _, _ = _linux_guest()
    terminal = DebianAdapter(guest).terminal

    asyncio.run(terminal._ensure_server(timeout=2))

    assert cast(_Guest, guest).keyboard.actions == []


def test_screen_separates_release_captures_from_framework_diagnostics(
    tmp_path: Path,
) -> None:
    debug_directory = tmp_path / "debug"
    release_directory = tmp_path / "release"
    debug_directory.mkdir()
    release_directory.mkdir()
    screen = Screen(
        cast(VncClient, _Vnc()), debug_directory, release_directory, recorder=None
    )

    asyncio.run(screen.capture("expected-state"))
    asyncio.run(screen.capture_result("failure"))

    assert (release_directory / "01-expected-state.png").is_file()
    assert (debug_directory / "02-failure.png").is_file()


def test_screen_waits_for_a_resize_before_matching_an_image(tmp_path: Path) -> None:
    template = tmp_path / "template.png"
    Frame(width=2, height=1, rgba=bytes([255, 255, 255, 0] * 2)).write_png(template)
    debug_directory = tmp_path / "debug"
    release_directory = tmp_path / "release"
    debug_directory.mkdir()
    release_directory.mkdir()
    screen = Screen(
        cast(VncClient, _ResizingVnc()),
        debug_directory,
        release_directory,
        recorder=None,
    )

    matched = asyncio.run(
        screen.wait_for_image(
            template,
            x=0,
            y=0,
            timeout=1,
        )
    )

    assert (matched.width, matched.height) == (2, 1)


def test_debian_adapter_rejects_an_address_outside_the_private_subnet() -> None:
    guest, terminal, ssh_network = _linux_guest()

    with pytest.raises(GuestControlError, match="outside"):
        asyncio.run(
            _debian(guest, terminal).network.static_address(
                ssh_network, "192.168.77.10/24"
            )
        )
