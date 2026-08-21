# Checkpoint Storage Research

Date: 2026-07-21

## Scope

Catsnail checkpoints must restore an exact running guest: desktop pixels,
processes, CPU state, virtual devices, and writable disks. The current QEMU
implementation stores a QCOW2 disk layer plus a QEMU migration state file.
The migration file is large because it contains guest RAM and device state.

This document records experiments and design decisions for reducing checkpoint
storage without sacrificing exact GUI-test restoration.

## Current Format

At checkpoint creation Catsnail:

1. Stops QEMU through QMP.
2. Flushes the writable QCOW2 layer.
3. Migrates QEMU to `file:<checkpoint>.state`.
4. Publishes the QCOW2 layer and migration state atomically.

The QCOW2 file is already a copy-on-write disk delta. The `.state` file is a
QEMU migration stream containing RAM, CPU, and virtual-device state. It is not
a QCOW2 image and cannot be replaced by one.

## Measurements

Measurements used existing local checkpoints. Timings are warm-cache results,
not a cold-storage benchmark.

| Workload | Raw state | Result | Time | Notes |
| --- | ---: | ---: | ---: | --- |
| Ubuntu 24.04 Desktop, 4 GiB RAM | 4.10 GB | zstd `-3`: 2.44 GB | compress 3.05 s, decompress 1.52 s | compression used 4 threads |
| Minimal desktop `login` state | 1.33 GB | zstd `-3`: 529 MB | compress 0.50 s, decompress 0.72 s | compression used all host cores |
| Minimal `boot` -> `login` | 1.07 GB -> 1.33 GB | zstd patch: 96.9 MB | see below | raw migration streams compared |

The Ubuntu compression ratio saves 40.4% of checkpoint storage. The decompressor
uses very little resident memory; the principal runtime cost is roughly 1.5 s
plus reading the restored QEMU migration stream.

## File Delta Experiment

`zstd --patch-from=boot.state login.state` treats the raw `boot.state` as a
reference dictionary and writes a compressed byte-stream patch for raw
`login.state`. The resulting 96.9 MB patch was verified by reconstructing a
stream with the same SHA-256 as `login.state`.

This is not a guest-memory-page delta:

- It matches variable-length byte sequences in a serialized migration stream.
- It has no knowledge of RAM page boundaries, CPU state, or device state.
- Its size demonstrates high similarity between adjacent states, but does not
  measure the number of KVM dirty pages.

It is not suitable as Catsnail's implementation:

- zstd's patch mode rejects reference files larger than 2 GB, so it cannot
  handle the 4 GiB Ubuntu state.
- The 1.33 GB experiment needed 3.5 GB RSS to generate the patch and 2.3 GB
  RSS to reconstruct it, even with `--mmap-dict`.
- Reconstruction took 1.61 s, slower and far more memory-intensive than full
  zstd decompression for the same fixture.

## Existing QEMU Features

QEMU offers several related but insufficient features:

- Internal VM snapshots can store VMState in a QCOW2 file, but VMState still
  contains a full RAM snapshot. QCOW2 sector sharing applies to disks, not RAM
  checkpoint deltas.
- `xbzrle` compresses page differences retransmitted during one live migration.
  It relies on sender/receiver caches and does not produce durable deltas
  between independently restored checkpoints.
- Migration compression can shrink one migration stream, but does not make it
  incremental. A generated `exec:` migration transport with zstd is the
  smallest practical short-term experiment.

## libvirt

libvirt manages QEMU domains, metadata trees, disk snapshots, and incremental
disk backups. It does not add incremental RAM snapshots:

- VM memory snapshots are full save images managed by `virDomainSave` or
  `virDomainManagedSave`.
- External snapshots make disks delta-based but retain a full VM memory state.
- Dirty bitmaps used by incremental backups track block devices, not guest RAM.

Moving Catsnail to libvirt would add a daemon, XML domain definitions, and
lifecycle cleanup without reducing `.state` files. It is not a solution to
checkpoint storage.

## Firecracker Model

Firecracker separates guest memory from device state:

```text
base.mem       immutable full guest memory
delta.mem      sparse dirty-memory pages
state          KVM and device state
```

On restore it maps `base.mem` with `MAP_PRIVATE`, so memory is loaded on demand
and guest writes become anonymous copy-on-write pages. With KVM dirty-page
tracking enabled, a subsequent diff snapshot stores only pages dirtied since
the prior snapshot.

This is the correct conceptual model for durable RAM deltas. It cannot be
adopted directly by Catsnail:

- Firecracker has no BIOS/UEFI ISO boot, VGA/VNC, PS/2 input, or full desktop
  device model. Catsnail's Live Debian/Ubuntu GUI tests require QEMU.
- It expects TAP-style networking; Catsnail's rootless `NetUser` and
  `NetSocket` model would need replacement.
- Its diff snapshots remain developer preview. A diff generally must be merged
  into its base memory file before restoring.
- A destructive merge is incompatible with a checkpoint graph where sibling
  tests share a parent. Branches need a non-destructive overlay reader,
  reflinked materialization, or a userfaultfd pager.
- Firecracker also warns that network connectivity and open connections are not
  guaranteed after restoring into another process.

## QEMU RAM-Delta Backend

To give QEMU Firecracker-like behavior, Catsnail would need a QEMU extension,
not a Python-only storage change:

1. Start QEMU with persistent, file-backed, private-CoW guest RAM.
2. Expose and reset KVM dirty-page tracking at every checkpoint.
3. Save CPU/device VMState without serializing RAM again.
4. Restore RAM from an immutable base plus ordered dirty-page overlays.
5. Preserve branching checkpoints without mutating shared parent layers.
6. Maintain compatibility with QEMU's VNC, virtio, serial, and network device
   migration state.

QEMU's live migration code already uses dirty-page tracking internally, but it
does not expose a stable API for persistent RAM-delta snapshots. Reusing it
would require maintaining C-level QEMU changes and tying Catsnail checkpoint
compatibility to a specific QEMU migration ABI.

## Options

| Option | Storage benefit | Restore behavior | Implementation cost | Decision |
| --- | --- | --- | --- | --- |
| Full migration stream | none | current exact restore | existing | baseline |
| Stream zstd compression | about 40% on Ubuntu fixture | about 1.5 s warm-cache decompression | small | defer until `exec:` proof of compatibility |
| zstd file patch | high on small fixtures | high memory; fails beyond 2 GB reference | small but unsuitable | reject |
| Content-addressed migration chunks | may deduplicate common stream regions | QEMU still consumes a complete reconstructed stream | medium | possible storage-only research path |
| QEMU RAM-delta backend | potentially large | can support on-demand CoW restoration | very high | long-term research only |
| libvirt migration | none for RAM | equivalent full QEMU save | medium | reject for this goal |

## Recommended Next Steps

1. Keep the current exact migration checkpoint as the supported default.
2. Prototype zstd through QEMU's `exec:` migration transport behind an internal
   feature flag. Verify save, restore, reproduction scripts, and failures with
   the current QEMU version before exposing any public option.
3. Do not implement generic file patching for `.state` files.
4. If checkpoint storage remains a practical blocker, create an isolated QEMU
   RAM-delta proof of concept before changing Catsnail: a 256 MiB KVM guest,
   one full RAM base, one KVM dirty-page delta, and one exact restore. Exclude
   ISO, VNC, networking, and graph branching from the first prototype.

## References

- [QEMU VM snapshots](https://www.qemu.org/docs/master/system/images.html#vm-snapshots)
- [QEMU migration capabilities](https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html#enum-QMP-migration.MigrationCapability)
- [libvirt snapshot format](https://libvirt.org/formatsnapshot.html)
- [libvirt incremental backup](https://libvirt.org/kbase/incrementalbackup.html)
- [Firecracker snapshot support](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md)
