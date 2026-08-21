# Catsnail Development Guide

Catsnail is a local, rootless QEMU test framework written in Python and
managed with `uv`. Its public surface should feel like idiomatic Python, while
QEMU lifecycle, snapshots, VNC, serial, and network details remain internal.

## API Contract

- Declare cold guests with `DESKTOP = add_os(Machine(...))`; do not add names
  that Catsnail can generate safely.
- Declare networks as variables with `add_net(NetUser() | NetSocket(...))`.
- Tests use `@add_test` and typed `use(node)` parameters. A test body does not
  return a value. On success, Catsnail checkpoints all of its input states:
  one input stays that type; multiple inputs become an ordered fixed tuple.
- The first `@add_test` is the root readiness assertion. Downstream tests use
  its function name through `use(test_name)`, not ad-hoc state classes.
- Keep generic guest controls in `Guest`. Put distribution-specific behavior
  in a concrete adapter such as `DebianAdapter`; do not introduce a speculative
  cross-distribution abstraction.
- Express every visible acceptance state with `screen.assert_screen(...)`.
  It publishes the exact matching full-screen PNG automatically; do not add a
  separate capture step. Use `screen.snapshot()` only when a control flow must
  compare a later framebuffer with its starting point.
- Preserve Pylance/Pyright inference. Dynamic decorator behavior needs typed
  overloads and collection-time validation, not `Any` or ignored errors.

## Style Preferences

- Optimize for a small, coherent public API. Prefer removing an abstraction or
  argument over adding a compatibility layer, decorator option, or wrapper.
- Make state flow explicit in function parameters and graph edges, even when
  the checkpoint output is implicit. Avoid hidden global state and magic names.
- Use standard-library facilities first. Add a dependency only when it removes
  substantial complexity or provides a capability that the standard library
  cannot reasonably supply. Do not add libvirt or a guest agent by default.
- Match existing module boundaries and names. Use concrete, completable names
  such as `NetUser`, `NetSocket`, and `DebianAdapter`.
- Favor straightforward functions and data classes over class hierarchies.
  Do not create state wrapper classes merely to name a checkpoint.
- Use `async` for socket I/O, subprocesses, blocking work moved to a thread,
  polling, or intentional concurrency. Sequential test steps remain sequential;
  use `asyncio.gather` only for independent operations.
- Keep comments to decisions and non-obvious invariants. Keep examples minimal,
  runnable, and free of internal tuning unless it is needed for correctness.

## Runtime Layout

- Cache downloaded ISO images by URL under `~/.config/catsnail/iso/` (respect
  `CATSNAIL_CONFIG_DIR` and `XDG_CONFIG_HOME`).
- Keep ephemeral QEMU disks, sockets, logs, and checkpoints under `target/run`.
  Keep debug captures under `target/debug` and user-facing screenshots, serial
  logs, and recordings under `target/release`.
- A failed run must print a concise Catsnail reproduction command and the QEMU
  reproduction/resume scripts. Do not silently discard failure artifacts.

## Verification

Run the narrowest relevant checks first, then use the full baseline for changes
that affect graph execution or public APIs:

```bash
uv run ruff check src tests examples
uv run pyright
uv run pytest -q
uv run catsnail run examples --dry-run
```

For a GUI scenario under active development, select just that node and skip
per-input recording. Its prerequisite checkpoints still restore, while every
`assert_screen` result remains in `target/release`:

```bash
uv run catsnail run integration-ubuntu/ruyisdk_ide.py \
  --test '^测试RuyiSDK项目模板$' --no-record --progress plain
```

For a new GUI scenario or a failing GUI test, first restore the nearest useful
checkpoint with `catsnail studio start ... --stdio`. Keep that single process
open while inspecting frames and issuing JSON Lines control requests; finish
with `session.emit`, stop it, write only the verified actions and fixtures back
to the source scenario, then validate that source change with ordinary
`catsnail run`. Studio is for fast discovery; `run` is the repeatable delivery
path and the only place that produces the final release recording.
