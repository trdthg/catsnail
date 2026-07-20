"""A complete one-machine Catsnail desktop smoke test.

Run it with:

    uv run catsnail run examples/minimal.py
"""

from pathlib import Path

from catsnail import Guest, Machine, add_os, add_test, use


ISO_URL = (
    "https://cdimage.debian.org/debian-cd/current-live/amd64/iso-hybrid/"
    "debian-live-13.6.0-amd64-xfce.iso"
)
ISO_SHA256 = "55970efe1bfe6455ce9d662a034d123cbfc16f9ac7a4a9db89f8e61b09de3faf"

DEBIAN_DESKTOP = add_os(
    Machine(
        iso=ISO_URL,
        sha256=ISO_SHA256,
        memory="2G",
        vcpus=2,
        boot_args=("live-config.noautologin",),
    )
)
LIGHTDM_CREDENTIALS = Path(__file__).parent / "assets" / "lightdm-credentials.png"
XFCE_PANEL = Path(__file__).parent / "assets" / "xfce-panel.png"


@add_test
async def test_desktop_boot(desktop: Guest = use(DEBIAN_DESKTOP)):
    """Wait for the visible username and password fields."""

    await desktop.screen.wait_for_image(
        LIGHTDM_CREDENTIALS,
        x=589,
        y=343,
        timeout=120,
    )


@add_test
async def test_desktop_login(desktop: Guest = use(test_desktop_boot)):
    """Log in from the boot checkpoint and wait for Xfce's desktop panel."""

    await desktop.keyboard.type("user")
    await desktop.keyboard.press("TAB")
    await desktop.keyboard.type("live")
    await desktop.keyboard.press("ENTER")
    await desktop.screen.wait_for_image(
        XFCE_PANEL,
        x=0,
        y=0,
        timeout=120,
    )
    await desktop.screen.capture("desktop")
