"""Executable one-machine Debian Live scenarios for ``catsnail run``.

The ISO is fetched once into Catsnail's URL cache. Debian Live's documented
default account is ``user`` / ``live``. The desktop source edits the Live boot
entry with ``live-config.noautologin`` so the login scenario really enters both
fields.

Run one terminal scenario, including all of its prerequisites:

    uv run catsnail run examples/debian_live.py --test test_browser_can_open_baidu
"""

from __future__ import annotations

from pathlib import Path

from catsnail import (
    DebianAdapter,
    Guest,
    Machine,
    add_os,
    add_test,
    use,
)


ROOT = Path(__file__).resolve().parents[1]
XFCE_PANEL = ROOT / "examples" / "assets" / "xfce-panel.png"
LIGHTDM_CREDENTIALS = ROOT / "examples" / "assets" / "lightdm-credentials.png"
XFCE_SETTINGS_WINDOW = ROOT / "examples" / "assets" / "xfce-settings-window.png"

DEBIAN_XFCE_URL = (
    "https://cdimage.debian.org/debian-cd/current-live/amd64/iso-hybrid/"
    "debian-live-13.6.0-amd64-xfce.iso"
)
DEBIAN_XFCE_SHA256 = "55970efe1bfe6455ce9d662a034d123cbfc16f9ac7a4a9db89f8e61b09de3faf"
LIVE_USER = "user"
LIVE_PASSWORD = "live"

DESKTOP = add_os(
    Machine(
        iso=DEBIAN_XFCE_URL,
        sha256=DEBIAN_XFCE_SHA256,
        memory="2G",
        vcpus=2,
        boot_args=("live-config.noautologin",),
    )
)


@add_test
async def test_desktop_login(desktop: Guest = use(DESKTOP)):
    await desktop.screen.assert_screen(
        LIGHTDM_CREDENTIALS,
        x=589,
        y=343,
        timeout=120,
    )

    # The username field has focus when Debian Live's LightDM greeter appears.
    await desktop.keyboard.type(LIVE_USER)
    await desktop.keyboard.press("TAB")
    await desktop.keyboard.type(LIVE_PASSWORD)
    await desktop.keyboard.press("ENTER")
    await desktop.screen.assert_screen(
        XFCE_PANEL,
        x=0,
        y=0,
        timeout=120,
        label="desktop",
    )

    # Initial serial
    debian = DebianAdapter(desktop)
    await debian.terminal.assert_run("id -un", LIVE_USER, timeout=60)
    await debian.initialize(timeout=60)
    await desktop.screen.assert_screen(XFCE_PANEL, x=0, y=0, timeout=30)


@add_test
async def test_browser_can_open_baidu(
    desktop: Guest = use(test_desktop_login),
):
    BAIDU = "https://www.baidu.com"
    BAIDU_NAVIGATION = ROOT / "examples" / "assets" / "baidu-navigation.png"

    before_browser = await desktop.screen.snapshot()
    await DebianAdapter(desktop).terminal.launch(
        "firefox --no-remote about:blank >/tmp/catsnail-firefox.log 2>&1 &",
        timeout=60,
    )
    await desktop.screen.wait_for_change(before_browser, timeout=30)
    # This is a real pointer click in Firefox's address bar, followed by input.
    await desktop.screen.click(420, 92)
    await desktop.keyboard.shortcut("CTRL", "A")
    await desktop.keyboard.type(BAIDU)
    await desktop.keyboard.press("ENTER")
    await desktop.screen.assert_screen(
        BAIDU_NAVIGATION,
        x=16,
        y=126,
        timeout=60,
        label="baidu-open",
    )


@add_test
async def test_can_open_system_setting(
    desktop: Guest = use(test_desktop_login),
):
    debian = DebianAdapter(desktop)
    await debian.terminal.launch(
        "xfce4-settings-manager >/tmp/catsnail-settings.log 2>&1 &",
        timeout=60,
    )
    await desktop.screen.assert_screen(
        XFCE_SETTINGS_WINDOW,
        x=316,
        y=121,
        timeout=60,
        label="system-settings-open",
    )


@add_test
async def test_debian_serial_terminal(
    desktop: Guest = use(test_desktop_login),
):
    serial = await DebianAdapter(desktop).serial(timeout=60)
    await serial.send("\n")
    await serial.expect(r"login:", timeout=60)
    await serial.send(f"{LIVE_USER}\n")
    await serial.expect(r"Password:", timeout=30)
    await serial.send(f"{LIVE_PASSWORD}\n")
    await serial.expect(r"\$ ", timeout=60)
    await serial.send("printf 'CATSNAIL_SERIAL_OK\\n'\n")
    await serial.expect(r"CATSNAIL_SERIAL_OK", timeout=30)
    await desktop.screen.assert_screen(XFCE_PANEL, x=0, y=0, timeout=30)
