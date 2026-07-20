from __future__ import annotations

from pathlib import Path
import asyncio
import os
import re
import shutil
from typing import cast

import pytest

from catsnail.graph.api import Machine
from catsnail.image import ImageError
from catsnail.qemu.artifacts import RunArtifacts
from catsnail.qemu.network import SocketAttachment, UserAttachment
from catsnail.qemu.qmp import QmpClient
from catsnail.qemu.runner import (
    QemuProcess,
    QemuRunError,
    QemuRunner,
)


def test_writes_reproduction_script_before_start_failure(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(tmp_path / "target")
    machine = Machine(iso=tmp_path / "missing.iso")
    runner = QemuRunner(executable="qemu-system-x86_64")

    with pytest.raises(QemuRunError) as raised:
        import asyncio

        asyncio.run(runner.start(machine, artifacts))

    assert raised.value.reproduce_command == f"sh {artifacts.reproduce_script}"
    assert artifacts.reproduce_script.exists()
    assert "missing.iso" in artifacts.reproduce_script.read_text(encoding="utf-8")


def test_build_command_uses_qmp_and_serial_artifacts(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(tmp_path / "target")
    runner = QemuRunner()
    command = runner.build_command(Machine(memory="2G", vcpus=2), artifacts)

    assert "-qmp" in command
    assert f"file:{artifacts.serial_log}" in command
    assert "accel=kvm:tcg" in command


def test_start_resolves_a_url_iso_before_building_qemu_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = RunArtifacts.create(tmp_path / "target")
    cached = tmp_path / "cache" / "image.iso"
    cached.parent.mkdir()
    cached.write_bytes(b"image")
    machine = Machine(
        iso="https://images.example.test/debian.iso", sha256="1" * 64
    )
    resolved: list[Machine] = []

    async def resolve(iso: Path | str | None, sha256: str | None) -> Path:
        assert iso == machine.iso
        assert sha256 == machine.sha256
        resolved.append(machine)
        return cached

    def build(machine: Machine, *_: object, **__: object) -> list[str]:
        assert machine.iso == cached
        return ["/bin/true"]

    monkeypatch.setattr("catsnail.qemu.runner.resolve_iso", resolve)
    runner = QemuRunner(executable="/bin/true")
    monkeypatch.setattr(runner, "build_command", build)

    async def exercise() -> int:
        running = await runner.start(machine, artifacts)
        await running.process.wait()
        return await runner.stop(running)

    assert asyncio.run(exercise()) == 0
    assert resolved == [machine]


def test_start_reports_an_unpinned_remote_iso_with_a_reproduction_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = RunArtifacts.create(tmp_path / "target")
    machine = Machine(iso="https://images.example.test/debian.iso")
    digest = "a" * 64

    async def resolve(_: Path | str | None, __: str | None) -> Path:
        raise ImageError(
            f"remote ISO requires sha256; calculated sha256: {digest}"
        )

    monkeypatch.setattr("catsnail.qemu.runner.resolve_iso", resolve)
    runner = QemuRunner(executable="/bin/true")

    with pytest.raises(QemuRunError, match=digest) as raised:
        asyncio.run(runner.start(machine, artifacts))

    assert artifacts.reproduce_script.is_file()
    assert raised.value.reproduce_command == f"sh {artifacts.reproduce_script}"


def test_artifacts_use_one_target_root_for_output_and_runtime_sockets(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "target"
    artifacts = RunArtifacts.create(target_dir)

    assert artifacts.directory == target_dir / "run" / "machine"
    expected_socket_parent = target_dir / "run" / "sockets"
    assert (
        artifacts.qmp_socket.parent == expected_socket_parent
        or artifacts.qmp_socket.parent.name.startswith(f"catsnail-{os.getuid()}-")
    )
    assert artifacts.vnc_socket.parent == artifacts.qmp_socket.parent


def test_prepare_discards_artifacts_from_the_previous_cli_run(tmp_path: Path) -> None:
    target_dir = tmp_path / "target"
    stale = RunArtifacts.create(target_dir)
    stale.serial_log.write_text("stale", encoding="utf-8")
    stale.release_directory.mkdir(parents=True)
    released = stale.release_directory / "screenshot.png"
    released.write_bytes(b"release")

    RunArtifacts.prepare(target_dir)
    assert not stale.serial_log.exists()
    assert not stale.debug_directory.exists()
    assert released.read_bytes() == b"release"
    current = RunArtifacts.create(target_dir)

    assert current.run_id == stale.run_id
    assert current.directory == target_dir / "run" / "machine"
    assert current.debug_directory == target_dir / "debug" / "machine"
    assert current.release_directory == target_dir / "release" / "machine"
    assert current.release_directory.is_dir()


def test_artifact_directory_rejects_paths_outside_the_current_run(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="relative"):
        RunArtifacts.create(tmp_path / "target", relative_directory=Path("../outside"))


def test_build_command_uses_a_short_random_guest_name(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(tmp_path / "target")
    command = QemuRunner().build_command(Machine(), artifacts)

    guest_name = command[command.index("-name") + 1]
    assert re.fullmatch(r"catsnail-[a-z0-9]{6}", guest_name)


def test_build_command_adds_an_isolated_socket_nic(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(tmp_path / "target")
    command = QemuRunner().build_command(
        Machine(),
        artifacts,
        network_attachments=(
            SocketAttachment("listen", 24567, "52:54:00:12:34:56"),
        ),
    )

    assert "socket,id=socket0,listen=127.0.0.1:24567" in command
    assert (
        "virtio-net-pci,id=catsnail-socket0,netdev=socket0,mac=52:54:00:12:34:56"
        in command
    )


def test_build_command_adds_a_user_egress_nic(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(tmp_path / "target")
    command = QemuRunner().build_command(
        Machine(),
        artifacts,
        network_attachments=(UserAttachment("10.66.12.0/24", "52:54:00:12:34:56"),),
    )

    assert "user,id=user0,net=10.66.12.0/24" in command
    assert (
        "virtio-net-pci,id=catsnail-user0,netdev=user0,mac=52:54:00:12:34:56" in command
    )


@pytest.mark.skipif(
    shutil.which("qemu-system-x86_64") is None,
    reason="QEMU is required for the process lifecycle integration test",
)
def test_starts_and_stops_qemu_without_boot_media(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(tmp_path / "target")
    runner = QemuRunner()

    async def exercise() -> int:
        running = await runner.start(Machine(), artifacts)
        await asyncio.sleep(0.1)
        return await runner.stop(running)

    returncode = asyncio.run(exercise())
    assert returncode < 0 or returncode == 0
    assert not artifacts.qmp_socket.exists()


@pytest.mark.skipif(
    shutil.which("qemu-system-x86_64") is None,
    reason="QEMU is required for the process lifecycle integration test",
)
def test_starts_and_stops_qemu_with_a_user_network(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(tmp_path / "target")
    runner = QemuRunner()

    async def exercise() -> int:
        running = await runner.start(
            Machine(),
            artifacts,
            network_attachments=(UserAttachment("10.66.12.0/24", "52:54:00:12:34:56"),),
        )
        await asyncio.sleep(0.1)
        return await runner.stop(running)

    returncode = asyncio.run(exercise())
    assert returncode < 0 or returncode == 0


def test_uses_short_qmp_path_for_deep_run_directories(tmp_path: Path) -> None:
    long_root = tmp_path / ("very-long-directory-" * 8)
    artifacts = RunArtifacts.create(long_root)

    assert len(str(artifacts.qmp_socket).encode()) < 100
    assert artifacts.qmp_socket.parent.name.startswith("catsnail-")


def test_stop_cleans_qmp_socket_after_qemu_already_exited(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(tmp_path / "target")
    artifacts.qmp_socket.touch()

    async def exercise() -> int:
        running = await QemuRunner(executable="/bin/false").start(Machine(), artifacts)
        await asyncio.wait_for(running.process.wait(), timeout=2)
        return await QemuRunner().stop(running)

    # A failed executable simulates a process that exited before cleanup.
    returncode = asyncio.run(exercise())
    assert returncode != 0
    assert not artifacts.qmp_socket.exists()


def test_reproduction_command_quotes_paths(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(tmp_path / "target with spaces")
    runner = QemuRunner()
    command = runner.build_command(
        Machine(iso=tmp_path / "image with spaces.iso"), artifacts
    )

    runner._write_reproduction(command, artifacts)
    script = artifacts.reproduce_script.read_text(encoding="utf-8")
    assert "image with spaces.iso'" in script
    assert "'qmp.sock'" not in script


def test_resume_command_replaces_an_existing_incoming_state(tmp_path: Path) -> None:
    artifacts = RunArtifacts.create(tmp_path / "target with spaces")
    runner = QemuRunner()
    command = runner.build_command(Machine(), artifacts)
    command.extend(["-incoming", "file:/tmp/old.state"])
    running = QemuProcess(
        process=cast(asyncio.subprocess.Process, object()),
        artifacts=artifacts,
        command=command,
    )

    script = runner.write_resume_script(running, tmp_path / "new state")

    content = script.read_text(encoding="utf-8")
    assert "old.state" not in content
    assert "new state" in content


@pytest.mark.skipif(
    shutil.which("qemu-img") is None,
    reason="qemu-img is required for the overlay integration test",
)
def test_overlay_resolves_a_relative_backing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    artifacts = RunArtifacts.create(Path("target"))
    backing = artifacts.directory / "base.qcow2"
    runner = QemuRunner()

    async def exercise() -> None:
        await runner.create_overlay(backing, artifacts)
        child = await runner.create_overlay(
            artifacts.directory / "child.qcow2", artifacts, backing=backing
        )
        process = await asyncio.create_subprocess_exec(
            "qemu-img",
            "info",
            "--output=json",
            str(child),
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        assert process.returncode == 0
        assert str(backing.resolve()) in stdout.decode("utf-8")

    asyncio.run(exercise())


@pytest.mark.skipif(
    shutil.which("qemu-system-x86_64") is None or shutil.which("qemu-img") is None,
    reason="QEMU and qemu-img are required for checkpoint integration",
)
def test_saves_and_restores_qemu_migration_state(tmp_path: Path) -> None:
    runner = QemuRunner()

    async def exercise() -> None:
        first = RunArtifacts.create(
            tmp_path / "target", relative_directory=Path("first")
        )
        first_disk = await runner.create_overlay(first.directory / "state.qcow2", first)
        running = await runner.start(
            Machine(),
            first,
            state_disk=first_disk,
        )
        state = tmp_path / "saved.state"
        qmp = await QmpClient.connect(first.qmp_socket)
        try:
            await qmp.pause_and_save(state, drive_id="catsnail-state")
        finally:
            await qmp.close()
        await runner.stop(running)

        second = RunArtifacts.create(
            tmp_path / "target", relative_directory=Path("second")
        )
        second_disk = await runner.create_overlay(
            second.directory / "state.qcow2", second, backing=first_disk
        )
        restored = await runner.start(
            Machine(),
            second,
            state_disk=second_disk,
            incoming_state=state,
        )
        qmp = await QmpClient.connect(second.qmp_socket)
        try:
            await qmp.resume()
            status = await qmp.execute("query-status")
            assert status["status"] == "running"
        finally:
            await qmp.close()
        await runner.stop(restored)

    asyncio.run(exercise())
