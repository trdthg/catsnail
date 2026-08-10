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
    await desktop.screen.assert_screen(
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
    await desktop.screen.assert_screen(
        XFCE_PANEL,
        x=0,
        y=0,
        timeout=120,
        label="desktop",
    )
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
configuration directory). Every successful `assert_screen(...)` publishes its
matching full-screen PNG, and recordings are published under
`target/release/minimal/test_desktop_login/`.

## Fast Iteration

During GUI test authoring, rerun only the node being edited. Catsnail restores
the nearest valid prerequisite checkpoint, so it does not boot the OS or repeat
the IDE installation. `--no-record` skips the dense per-input keyframes and
MP4 work; successful `assert_screen(...)` calls still save their verified PNGs.

```bash
uv run catsnail run integration-ubuntu/ruyisdk_ide.py \
  --test '^测试RuyiSDK项目模板$' --no-record --progress plain
```

Write one `assert_screen(...)` for each stable UI state. It waits for the
fixture, validates it, and writes the same matched framebuffer to release.
When the scenario is ready for delivery, rerun it without `--no-record` to add
the step recording and MP4. A code change invalidates only that test's
checkpoint and descendants; unchanged prerequisites remain reusable.

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

## Interactive Studio

When a GUI flow is still being discovered, restore a successful checkpoint and
explore it without changing the checkpoint itself. Studio keeps a framebuffer
after every action in `target/run/studio/<session>/`, together with an
append-only `events.jsonl` log.

```bash
uv run catsnail studio start integration-ubuntu/ruyisdk_ide.py \
  --from 测试RuyiSDK自动检测与安装Ruyi
uv run catsnail studio screenshot
uv run catsnail studio click 431 69
uv run catsnail studio type demo
uv run catsnail studio key ENTER
uv run catsnail studio wait --timeout 30
uv run catsnail studio serial --lines 80
uv run catsnail studio emit --name ruyi-demo
uv run catsnail studio stop
```

The commands attach to the most recently active session; pass its id as the
first argument when more than one session is active. `screenshot`, `click`,
`type`, `key`, and `wait` also accept `--machine` for multi-machine sessions.
`serial` reads the latest QEMU serial output without interacting with the GUI.
`crop` saves a selected frame region as a PNG fixture. `emit` copies the
recorded frames and emits a reviewable Python draft plus a short Markdown
report under `target/studio/generated/`. The draft intentionally leaves the
original `add_os(...)` declaration for the user to connect, so Studio never
overwrites a test file or silently changes a checked-in scenario.

For an AI or editor integration, `studio start --serve` exposes the same
operations as newline-delimited JSON on the session's Unix socket. Requests
such as `screen.snapshot`, `screen.click`, `keyboard.type`,
`keyboard.press`, and `screen.wait_stable` return frame paths, dimensions,
revision numbers, and SHA-256 digests instead of embedding large base64 images.

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

## Guest Commands

`DebianAdapter.terminal.run(...)` requires a zero exit status.
`assert_output(...)` compares the final standard output exactly; the
conventional final newline is ignored, while all other whitespace remains
significant. `assert_run(...)` polls output while the command runs and requires
one or more fragments in their emitted order before requiring a zero exit
status.

```python
debian = DebianAdapter(desktop)
await debian.terminal.assert_output("id -un", "user")
await debian.terminal.assert_run("make", "Compiling", "Build complete")
```

Catsnail captures the command output through the guest's private `/tmp`
control endpoint. A nonzero exit status or an output mismatch includes the
command and available output in the test failure.

## Commands

```bash
catsnail doctor
catsnail run PATH [--test PATTERN] [--jobs N] [--progress auto|tree|plain] [--force] [--no-record]
catsnail run PATH --dry-run
catsnail prune PATH
catsnail studio start PATH --from TEST
catsnail studio screenshot [SESSION]
catsnail studio emit [SESSION]
catsnail studio stop [SESSION]
```

`run` discovers Python modules recursively when given a directory; only modules
that export `@add_test` declarations contribute scenarios. An explicit file can
have any name. `--dry-run` loads and validates the same
dependency graph without starting QEMU or touching `target/`; it also prints
the selected dependency tree with every node in `WAIT` state. `prune` lists
stale checkpoints scoped to the selected path and deletes them only after `y`.
`--force` skips existing checkpoint restoration, rebuilding prerequisite
environments and replacing their cache only after they succeed.
`--no-record` disables per-step keyframes and MP4 generation while retaining
the verified screenshots written by `assert_screen(...)`.
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
