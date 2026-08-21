from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from catsnail.graph.api import NetSocket, NetUser, Network, add_net
from catsnail.guest.controls import Screen, _keysym, _locate_template_x, _locate_template_y
from catsnail.guest import (
    DebianAdapter,
    DebianSerial,
    Guest,
    GuestControlError,
    GuestNetwork,
    NetworkInterface,
    NetworkLink,
    ScreenAssertionError,
    UbuntuAdapter,
)
from catsnail.guest.debian import TerminalCommand
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

    async def wait_for_output(
        self, expected: tuple[str, ...], *, deadline: float
    ) -> None:
        del deadline
        output = await self.output(timeout=0)
        assert all(fragment in output for fragment in expected)


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
            artifacts=SimpleNamespace(
                serial_socket=Path("/tmp/catsnail-test-serial.sock")
            )
        )
        self.release_directory = Path("/tmp/catsnail-test-release")
        self._close_callbacks: list[object] = []
        self._adapter_state: dict[str, Any] = {}
        self.network = network
        self.keyboard = _Keyboard()
        self.screen = _Screen()

    def _register_close_callback(self, callback: object) -> None:
        self._close_callbacks.append(callback)


class _Screen:
    def __init__(self) -> None:
        self.actions: list[str] = []

    async def record_step(self, _: str) -> None:
        return None

    async def click(self, x: int, y: int) -> None:
        self.actions.append(f"click-{x}-{y}")


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
    async def frame(self, **_: object) -> Frame:
        return Frame(width=1, height=1, rgba=bytes([255, 255, 255, 0]))

    async def click(self, x: int, y: int, *, button: int = 1) -> None:
        del x, y
        del button
        return None


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


def test_core_network_disconnects_a_declared_qemu_nic(monkeypatch: pytest.MonkeyPatch) -> None:
    egress = add_net(NetUser())
    calls: list[tuple[str, dict[str, object] | None]] = []

    class _Qmp:
        async def execute(
            self, command: str, arguments: dict[str, object] | None = None
        ) -> None:
            calls.append((command, arguments))

        async def close(self) -> None:
            return None

    async def connect(_: Path) -> _Qmp:
        return _Qmp()

    monkeypatch.setattr("catsnail.qemu.qmp.QmpClient.connect", connect)
    network = GuestNetwork(
        (),
        links=(NetworkLink(egress, "catsnail-user0"),),
        qmp_socket=Path("/tmp/catsnail-test-qmp.sock"),
    )

    asyncio.run(network.disconnect(egress))

    assert calls == [("set_link", {"name": "catsnail-user0", "up": False})]


def test_keyboard_supports_menu_navigation_keys() -> None:
    assert _keysym("SHIFT") == 0xFFE1
    assert _keysym("UP") == 0xFF52
    assert _keysym("DOWN") == 0xFF54
    assert _keysym("LEFT") == 0xFF51
    assert _keysym("RIGHT") == 0xFF53
    assert _keysym("F9") == 0xFFC6
    assert _keysym("MENU") == 0xFF67


def test_locates_a_template_horizontally_before_a_strict_full_image_match() -> None:
    template = Frame(
        width=16,
        height=10,
        rgba=bytes(
            component
            for y in range(10)
            for x in range(16)
            for component in ((0, 0, 0, 255) if (x + 3 * y) % 5 == 0 else (255, 255, 255, 255))
        ),
    )
    pixels = bytearray([240, 240, 240, 255] * 40 * 10)
    for y in range(template.height):
        start = (y * 40 + 17) * 4
        stop = start + template.width * 4
        pixels[start:stop] = template.rgba[y * template.width * 4 : (y + 1) * template.width * 4]
    frame = Frame(width=40, height=10, rgba=bytes(pixels))

    assert _locate_template_x(frame, template, y=0) == 17
    assert _locate_template_y(frame, template, x=17) == 0


def test_ubuntu_adapter_minimizes_the_active_x11_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guest, _, _ = _linux_guest()
    adapter = UbuntuAdapter(guest)
    adapter.terminal._focused = True
    commands: list[str] = []

    async def run(command: str, *, timeout: float = 120.0) -> None:
        del timeout
        commands.append(command)

    monkeypatch.setattr(adapter.terminal, "run", run)

    asyncio.run(adapter.window.minimize())

    assert commands == [
        "xdotool search --onlyvisible --name ubuntu@ubuntu windowminimize %@"
    ]
    assert not adapter.terminal._focused


def test_ubuntu_adapter_activates_a_named_x11_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guest, _, _ = _linux_guest()
    adapter = UbuntuAdapter(guest)
    actions: list[str] = []

    async def run_x11_action(action: str) -> None:
        actions.append(action)

    monkeypatch.setattr(adapter.window, "_run_x11_action", run_x11_action)

    asyncio.run(adapter.window.activate("RuyiSDK IDE"))

    assert actions == [
        "xdotool search --onlyvisible --name 'RuyiSDK IDE' "
        "windowactivate --sync %@"
    ]


def test_ubuntu_adapter_pastes_with_one_native_context_menu_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guest, _, _ = _linux_guest()
    adapter = UbuntuAdapter(guest)
    actions: list[str] = []

    async def run_x11_action(action: str) -> None:
        actions.append(action)

    monkeypatch.setattr(adapter.window, "_run_x11_action", run_x11_action)

    asyncio.run(adapter.window.context_paste(x=749, y=232))

    assert actions == [
        "xdotool mousemove 749 232 click 3; sleep 0.2; xdotool key p"
    ]


def test_ubuntu_adapter_rejects_negative_context_paste_coordinates() -> None:
    guest, _, _ = _linux_guest()

    with pytest.raises(ValueError, match="coordinates"):
        asyncio.run(UbuntuAdapter(guest).window.context_paste(x=-1, y=0))


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


def test_debian_terminal_asserts_captured_command_output() -> None:
    guest, terminal, _ = _linux_guest()

    asyncio.run(
        _debian(guest, terminal).terminal.assert_output(
            "ip -o -4 addr show",
            "2: enp0s8    inet 192.168.76.10/24\n",
        )
    )

    assert terminal.commands == ["ip -o -4 addr show"]


def test_debian_terminal_reports_output_assertion_mismatches() -> None:
    guest, terminal, _ = _linux_guest()

    with pytest.raises(
        GuestControlError,
        match=re.compile("expected: 'missing'.*actual: '2: enp0s8", re.DOTALL),
    ):
        asyncio.run(
            _debian(guest, terminal).terminal.assert_output(
                "ip -o -4 addr show",
                "missing",
            )
        )


def test_debian_terminal_asserts_streamed_output() -> None:
    guest, terminal, _ = _linux_guest()

    asyncio.run(
        _debian(guest, terminal).terminal.assert_run(
            "ip -o -4 addr show",
            "enp0s8",
            "192.168.76.10",
        )
    )

    assert terminal.commands == ["ip -o -4 addr show"]


def test_debian_terminal_captures_a_whole_compound_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guest, _, _ = _linux_guest()
    terminal = DebianAdapter(guest).terminal
    terminal._server_started = True
    terminal._focused = True

    async def started(*_: object, **__: object) -> str:
        return "1"

    monkeypatch.setattr("catsnail.guest.debian._read_text", started)
    asyncio.run(terminal._send("cd project && make", timeout=30, capture_output=True))

    entered = cast(_Guest, guest).keyboard.actions[0]
    assert entered.startswith("if test -e /tmp/catsnail-result-")
    assert "(cd project && make) > /tmp/catsnail-result-" in entered
    assert ".out 2>&1; printf %s $? > /tmp/catsnail-result-" in entered
    assert entered.endswith("; fi")
    assert "{" not in entered and "}" not in entered


def test_debian_terminal_envelope_handles_a_semicolon_separated_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guest, _, _ = _linux_guest()
    terminal = DebianAdapter(guest).terminal
    terminal._server_started = True
    terminal._focused = True

    async def started(*_: object, **__: object) -> str:
        return "1"

    monkeypatch.setattr("catsnail.guest.debian._read_text", started)
    asyncio.run(
        terminal._send(
            "xdotool mousemove 749 232 click 3; sleep 0.2; xdotool key p",
            timeout=30,
            capture_output=False,
        )
    )

    entered = cast(_Guest, guest).keyboard.actions[0]
    assert "xdotool mousemove 749 232 click 3; sleep 0.2; xdotool key p" in entered
    assert entered.startswith("if test -e ")
    assert entered.endswith("; fi")


def test_debian_terminal_waits_for_the_started_marker_instead_of_accepting_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guest, _, _ = _linux_guest()
    terminal = DebianAdapter(guest).terminal
    calls: list[tuple[str, float, bool]] = []

    async def read_text(
        url: str, timeout: float, *, allow_not_found: bool = False
    ) -> str:
        calls.append((url, timeout, allow_not_found))
        return "1"

    monkeypatch.setattr("catsnail.guest.debian._read_text", read_text)

    assert asyncio.run(terminal._command_started("http://guest/started", timeout=5))
    assert calls == [("http://guest/started", 5, False)]


def test_debian_terminal_retries_input_after_a_lost_terminal_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guest, _, _ = _linux_guest()
    terminal = DebianAdapter(guest).terminal
    terminal._server_started = True
    terminal._focused = True
    starts = iter(["", "1"])

    async def read_started(*_: object, **__: object) -> str:
        return next(starts)

    async def no_delay(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr("catsnail.guest.debian._read_text", read_started)
    monkeypatch.setattr("catsnail.guest.debian.asyncio.sleep", no_delay)

    asyncio.run(terminal._send("make", timeout=30, capture_output=False))

    actions = cast(_Guest, guest).keyboard.actions
    assert actions[0] == actions[3]
    assert actions[1] == actions[4] == "key-ENTER"
    assert actions == [
        actions[0],
        "key-ENTER",
        "shortcut-CTRL-ALT-T",
        actions[0],
        "key-ENTER",
    ]


def test_terminal_command_waits_for_output_fragments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = iter(["fetching\n", "fetching\ncomplete\n"])
    statuses = iter(["", ""])
    steps: list[str] = []

    async def read_text(url: str, *_: object, **__: object) -> str:
        if url == "output":
            return next(outputs, "fetching\ncomplete\n")
        return next(statuses, "")

    async def no_delay(_: float) -> None:
        return None

    async def record_step(label: str) -> None:
        steps.append(label)

    monkeypatch.setattr("catsnail.guest.debian._read_text", read_text)
    monkeypatch.setattr("catsnail.guest.debian.asyncio.sleep", no_delay)
    command = TerminalCommand(
        command="build",
        result_url="result",
        output_url="output",
        after_step=record_step,
    )

    asyncio.run(
        command.wait_for_output(("fetching", "complete"), deadline=time.monotonic() + 1)
    )

    assert steps == []


def test_terminal_command_records_after_the_terminal_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def read_text(*_: object, **__: object) -> str:
        return "0"

    async def settle(delay: float) -> None:
        assert delay == 2
        events.append("terminal-refreshed")

    async def record_step(label: str) -> None:
        events.append(label)

    monkeypatch.setattr("catsnail.guest.debian._read_text", read_text)
    monkeypatch.setattr("catsnail.guest.debian.asyncio.sleep", settle)
    command = TerminalCommand(
        command="build",
        result_url="result",
        output_url=None,
        after_step=record_step,
    )

    asyncio.run(command.wait())

    assert events == ["terminal-refreshed", "terminal-command-complete"]


def test_terminal_command_fails_when_output_is_missing_at_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps: list[str] = []

    async def read_text(url: str, *_: object, **__: object) -> str:
        return "ready\n" if url == "output" else "0"

    async def record_step(label: str) -> None:
        steps.append(label)

    monkeypatch.setattr("catsnail.guest.debian._read_text", read_text)
    command = TerminalCommand(
        command="build",
        result_url="result",
        output_url="output",
        after_step=record_step,
    )

    with pytest.raises(GuestControlError, match="completed without expected output"):
        asyncio.run(
            command.wait_for_output(("complete",), deadline=time.monotonic() + 1)
        )

    assert steps == ["terminal-command-complete"]


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

    async def no_delay(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr("catsnail.guest.debian._read_text", server_is_running)
    monkeypatch.setattr("catsnail.guest.debian.asyncio.sleep", no_delay)
    guest, _, _ = _linux_guest()
    terminal = DebianAdapter(guest).terminal

    asyncio.run(terminal._ensure_server(timeout=2))

    assert cast(_Guest, guest).keyboard.actions == ["shortcut-CTRL-ALT-T"]


def test_debian_adapters_share_one_guest_terminal_session() -> None:
    guest, _, _ = _linux_guest()

    first = DebianAdapter(guest).terminal
    first._server_started = True
    first._focused = True
    second = DebianAdapter(guest).terminal

    assert second._server_started
    assert second._focused


def test_screen_exposes_non_primary_mouse_clicks(
    tmp_path: Path,
) -> None:
    class _ClickVnc(_Vnc):
        def __init__(self) -> None:
            self.clicks: list[tuple[int, int, int]] = []

        async def click(self, x: int, y: int, *, button: int = 1) -> None:
            self.clicks.append((x, y, button))

    vnc = _ClickVnc()
    screen = Screen(cast(VncClient, vnc), tmp_path, tmp_path, recorder=None)

    asyncio.run(screen.middle_click(10, 20))
    asyncio.run(screen.right_click(30, 40))

    assert vnc.clicks == [(10, 20, 2), (30, 40, 3)]


def test_screen_exposes_pointer_wheel_scrolling(tmp_path: Path) -> None:
    class _ScrollVnc(_Vnc):
        def __init__(self) -> None:
            self.scrolls: list[tuple[int, int, int]] = []

        async def scroll(self, x: int, y: int, amount: int) -> None:
            self.scrolls.append((x, y, amount))

    vnc = _ScrollVnc()
    screen = Screen(cast(VncClient, vnc), tmp_path, tmp_path, recorder=None)

    asyncio.run(screen.scroll(10, 20, 3))

    assert vnc.scrolls == [(10, 20, 3)]


def test_screen_asserts_and_publishes_an_image_after_a_resize(tmp_path: Path) -> None:
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
        screen.assert_screen(
            template,
            x=0,
            y=0,
            timeout=1,
        )
    )

    assert (matched.width, matched.height) == (2, 1)
    assert (release_directory / "01-template.png").is_file()


def test_screen_snapshot_does_not_publish_an_artifact(tmp_path: Path) -> None:
    debug_directory = tmp_path / "debug"
    release_directory = tmp_path / "release"
    debug_directory.mkdir()
    release_directory.mkdir()
    screen = Screen(
        cast(VncClient, _Vnc()), debug_directory, release_directory, recorder=None
    )

    snapshot = asyncio.run(screen.snapshot())

    assert snapshot.width == 1
    assert not tuple(release_directory.iterdir())


def test_screen_waits_for_a_stable_frame_without_publishing_an_artifact(
    tmp_path: Path,
) -> None:
    debug_directory = tmp_path / "debug"
    release_directory = tmp_path / "release"
    debug_directory.mkdir()
    release_directory.mkdir()
    screen = Screen(
        cast(VncClient, _Vnc()), debug_directory, release_directory, recorder=None
    )

    stable = asyncio.run(screen.wait_for_stable(timeout=1))

    assert stable.width == 1
    assert not tuple(release_directory.iterdir())


def test_screen_can_wait_for_a_local_control_change(tmp_path: Path) -> None:
    baseline = Frame(width=8, height=1, rgba=bytes([0, 0, 0, 0] * 8))
    changed = Frame(
        width=8,
        height=1,
        rgba=bytes([255, 255, 255, 0] * 5 + [0, 0, 0, 0] * 3),
    )

    class _ChangingVnc(_Vnc):
        async def frame(self, **_: object) -> Frame:
            return changed

    debug_directory = tmp_path / "debug"
    release_directory = tmp_path / "release"
    debug_directory.mkdir()
    release_directory.mkdir()
    screen = Screen(
        cast(VncClient, _ChangingVnc()), debug_directory, release_directory, recorder=None
    )

    result = asyncio.run(
        screen.wait_for_change(
            baseline,
            timeout=1,
            minimum_changed_pixels=4,
        )
    )

    assert result == changed


def test_screen_retains_a_failed_assertion_frame_for_debugging(tmp_path: Path) -> None:
    expected = Frame(width=2, height=1, rgba=bytes([0, 0, 0, 0] * 2))
    template = tmp_path / "template.png"
    expected.write_png(template)
    debug_directory = tmp_path / "debug"
    release_directory = tmp_path / "release"
    debug_directory.mkdir()
    release_directory.mkdir()
    screen = Screen(
        cast(VncClient, _Vnc()), debug_directory, release_directory, recorder=None
    )

    with pytest.raises(ScreenAssertionError, match="screen assertion failed"):
        asyncio.run(
            screen.assert_screen(
                template,
                x=0,
                y=0,
                timeout=0,
            )
        )

    assert (debug_directory / "01-screen-assertion-failed.png").is_file()


def test_screen_exposes_a_single_publishing_visual_assertion_api() -> None:
    assert callable(Screen.assert_screen)
    assert callable(Screen.snapshot)
    assert not hasattr(Screen, "assert_image")
    assert not hasattr(Screen, "wait_for_image")
    assert not hasattr(Screen, "capture")


def test_debian_adapter_rejects_an_address_outside_the_private_subnet() -> None:
    guest, terminal, ssh_network = _linux_guest()

    with pytest.raises(GuestControlError, match="outside"):
        asyncio.run(
            _debian(guest, terminal).network.static_address(
                ssh_network, "192.168.77.10/24"
            )
        )
