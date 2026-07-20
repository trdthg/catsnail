# Catsnail

Catsnail runs QEMU desktop tests from ordinary typed Python functions. Tests use
the visible guest through VNC, record screenshots by default, and reuse
successful prerequisite states as QEMU checkpoints.

## Quick Start

Install the development environment and check the host tools:

```bash
uv sync --group dev
uv run catsnail doctor
```

`doctor` requires `qemu-system-x86_64` and `qemu-img`. KVM access and `ffmpeg`
are optional: without KVM QEMU uses TCG, and without ffmpeg Catsnail keeps PNG
keyframes rather than producing an MP4.

## One Desktop

[examples/minimal.py](examples/minimal.py) is a complete two-step test. It
boots a pinned Debian Xfce Live ISO to its login form, then logs in using the
checkpoint produced by the first step.

```python
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
    await desktop.screen.wait_for_image(
        LIGHTDM_CREDENTIALS,
        x=589,
        y=343,
        timeout=120,
    )


@add_test
async def test_desktop_login(desktop: Guest = use(test_desktop_boot)):
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
```

`add_os(...)` declares a cold machine. Every `@add_test` publishes its input
state as a checkpoint; `use(test_desktop_boot)` injects a restored `Guest` into the next test with the
same static type. If a downstream test fails, running it again restores the boot
checkpoint instead of booting Debian again. Changing `test_desktop_boot`
invalidates that checkpoint and all of its descendants.

```bash
uv run catsnail run examples/minimal.py --dry-run
uv run catsnail run examples/minimal.py --test test_desktop_login
uv run catsnail run examples/debian_live.py --test test_browser_can_open_baidu
uv run catsnail run examples/debian_ssh.py
```

The first run downloads the ISO once to `~/.config/catsnail/iso/` (or the XDG
configuration directory). Screenshots and recordings are published under
`target/release/minimal/test_desktop_login/`.

## Multiple Machines

Declare one `NetSocket` and attach it to each machine. Catsnail creates an
isolated, rootless QEMU LAN for each graph execution; it uses a random
loopback TCP link and does not create a TAP device, bridge, route, or `ip link`
entry. A `NetSocket` link connects two machines; declare another link for a
third machine. Socket LANs do not provide DHCP, so the guest adapter configures
static addresses.

```python
import asyncio

from catsnail import DebianAdapter, Guest, Machine, NetSocket, add_net, add_os, add_test, use


SSH_NET = add_net(NetSocket(subnet="192.168.76.0/24"))
SERVER = add_os(Machine(iso=ISO_URL, sha256=ISO_SHA256, networks=(SSH_NET,)))
CLIENT = add_os(Machine(iso=ISO_URL, sha256=ISO_SHA256, networks=(SSH_NET,)))


@add_test
async def test_ssh_pod(
    server: Guest = use(SERVER), client: Guest = use(CLIENT)
):
    server_os = DebianAdapter(server)
    client_os = DebianAdapter(client)
    await asyncio.gather(
        server_os.network.static_address(SSH_NET, "192.168.76.10/24"),
        client_os.network.static_address(SSH_NET, "192.168.76.20/24"),
    )
    await client_os.terminal.run("ping -c 3 -W 2 192.168.76.10")
```

This section focuses on the topology and guest network API. The full,
executable SSH flow, including desktop readiness, service startup, interactive
password entry, and remote-command assertion, is in
[examples/debian_ssh.py](examples/debian_ssh.py).

`NetUser()` is separate: it gives one guest DHCP/NAT egress, not a shared LAN.
Attach both `NetSocket` and `NetUser` when a test needs a private machine LAN
and outbound internet access.

## Serial Terminal

`DebianAdapter` can enable an interactive `ttyS1` login on demand. The session
uses QEMU's private Unix socket, while the original `ttyS0` output remains in
`serial.log`.

```python
debian = DebianAdapter(desktop)
await debian.initialize()
serial = await debian.serial()
await serial.expect(r"login:")
await serial.send("user\n")
await serial.expect(r"Password:")
await serial.send("live\n")
await serial.expect(r"\$ ")
await serial.send("uname -s\n")
await serial.expect("Linux")
```

## Commands

```bash
catsnail doctor
catsnail run PATH [--test PATTERN] [--jobs N] [--progress auto|tree|plain] [--force]
catsnail run PATH --dry-run
catsnail prune PATH
```

`run` discovers Python modules recursively when given a directory; only modules
that export `@add_test` declarations contribute scenarios. An explicit file can
have any name. `--dry-run` loads and validates the same
dependency graph without starting QEMU or touching `target/`; it also prints
the selected dependency tree with every node in `WAIT` state. `prune` lists
stale checkpoints scoped to the selected path and deletes them only after `y`.
`--force` skips existing checkpoint restoration, rebuilding prerequisite
environments and replacing their cache only after they succeed.
`--test` is a Python regular expression matched against the function name and
collected ID: `test_desktop_login` works as before, while
`'^test_(browser|ssh).*'` selects both matching scenarios. Use `^...$` for an
exact name match.

Progress defaults to `auto`: an interactive terminal redraws the selected test
DAG as a live tree, while redirected output and CI receive append-only
`RUN`/`PASS`/`FAIL` lines. Use `--progress tree` to force the live view or
`--progress plain` for line-oriented logs. Independent branches honor
`--jobs`; dependent checkpoint tests stay ordered in the tree.

The live tree colors `PASS` green, `FAIL` red, `WAIT` yellow, and `RUN` bright
cyan. Set `NO_COLOR` to retain the live layout without ANSI color codes.

## State And Failures

Catsnail stores test-local state in `target/`:

- `run/`: live QEMU logs, writable QCOW2 overlays, and durable checkpoints.
- `debug/`: diagnostic frames, failure details, and recording manifests.
- `release/`: explicit screenshots and `recording.mp4` when ffmpeg is present.

Within each of these directories, Catsnail mirrors the test file path relative
to the directory where `catsnail run` was invoked. For example,
`./aaa/bbb/examples/login.py` writes under
`target/release/aaa/bbb/examples/login/...`.

On failure Catsnail prints a graph-level reproduce command. The matching
`target/run/.../` directory also keeps QEMU logs, `reproduce.sh`, and, when
available, `failure.state` plus `resume.sh` for the exact VM state.
