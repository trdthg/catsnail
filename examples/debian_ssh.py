"""A two-machine Debian Live SSH scenario for ``catsnail run``.

Run it with:

    uv run catsnail run examples/debian_ssh.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from catsnail import (
    DebianAdapter,
    Guest,
    Machine,
    NetSocket,
    add_net,
    add_os,
    add_test,
    use,
)


ROOT = Path(__file__).resolve().parents[1]
XFCE_PANEL = ROOT / "examples" / "assets" / "xfce-panel.png"

DEBIAN_XFCE_URL = (
    "https://cdimage.debian.org/debian-cd/current-live/amd64/iso-hybrid/"
    "debian-live-13.6.0-amd64-xfce.iso"
)
DEBIAN_XFCE_SHA256 = "55970efe1bfe6455ce9d662a034d123cbfc16f9ac7a4a9db89f8e61b09de3faf"
LIVE_PASSWORD = "live"

SSH_NET = add_net(NetSocket(subnet="192.168.76.0/24"))

SERVER = add_os(
    Machine(
        iso=DEBIAN_XFCE_URL,
        sha256=DEBIAN_XFCE_SHA256,
        memory="2G",
        vcpus=2,
        networks=(SSH_NET,),
    )
)
CLIENT = add_os(
    Machine(
        iso=DEBIAN_XFCE_URL,
        sha256=DEBIAN_XFCE_SHA256,
        memory="2G",
        vcpus=2,
        networks=(SSH_NET,),
    )
)


@add_test
async def test_ssh_pod(
    server: Guest = use(SERVER),
    client: Guest = use(CLIENT),
):
    await asyncio.gather(
        server.screen.assert_screen(
            XFCE_PANEL,
            x=0,
            y=0,
            timeout=120,
        ),
        client.screen.assert_screen(
            XFCE_PANEL,
            x=0,
            y=0,
            timeout=120,
        ),
    )
    debian_server = DebianAdapter(server)
    debian_client = DebianAdapter(client)
    await asyncio.gather(
        debian_server.network.static_address(SSH_NET, "192.168.76.10/24"),
        debian_client.network.static_address(SSH_NET, "192.168.76.20/24"),
    )
    await debian_client.terminal.run("ping -c 3 -W 2 192.168.76.10", timeout=30)

    await debian_server.terminal.run(
        "apt-get update && "
        "env DEBIAN_FRONTEND=noninteractive apt-get install --yes openssh-server && "
        "systemctl enable --now ssh",
        admin=True,
        timeout=600,
    )
    await debian_server.terminal.run(
        "systemctl is-active --quiet ssh", admin=True, timeout=30
    )
    await debian_client.terminal.run(
        "apt-get update && "
        "env DEBIAN_FRONTEND=noninteractive apt-get install --yes openssh-client",
        admin=True,
        timeout=600,
    )
    await debian_client.terminal.run("ping -c 3 -W 2 192.168.76.10", timeout=30)
    command = await debian_client.terminal.command(
        "ssh -v -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new user@192.168.76.10 "
        "'printf CATSNAIL_SSH_OK'",
        timeout=60,
        capture_output=True,
    )
    await asyncio.sleep(3)
    password_prompt = await client.screen.snapshot()
    await client.keyboard.type(LIVE_PASSWORD)
    await client.keyboard.press("ENTER")
    await command.wait(timeout=90)
    output = await command.output(timeout=30)
    if "CATSNAIL_SSH_OK" not in output:
        raise RuntimeError(f"SSH command output did not contain success marker: {output!r}")
    await client.screen.wait_for_change(password_prompt, timeout=15)
    await debian_client.terminal.run("printf 'CATSNAIL_SSH_OK\\n'", timeout=30)
    await asyncio.gather(
        server.screen.assert_screen(
            XFCE_PANEL, x=0, y=0, timeout=30, label="ssh-server-ready"
        ),
        client.screen.assert_screen(
            XFCE_PANEL, x=0, y=0, timeout=30, label="ssh-client-complete"
        ),
    )
