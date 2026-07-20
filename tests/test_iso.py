from __future__ import annotations

import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest

from catsnail import Guest, Machine, add_os, add_test, use
from catsnail.graph.checkpoint import checkpoint_key
from catsnail.image import ImageError, iso_cache_directory, resolve_iso


class _Response(BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def test_uses_the_xdg_config_directory_for_iso_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    assert iso_cache_directory() == tmp_path / "config" / "catsnail" / "iso"


def test_downloads_a_verified_url_once_then_reuses_its_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"catsnail test ISO"
    checksum = hashlib.sha256(payload).hexdigest()
    requested: list[str] = []

    def download(request: object, *, timeout: float) -> _Response:
        del timeout
        requested.append(getattr(request, "full_url"))
        return _Response(payload)

    monkeypatch.setattr("catsnail.image.urlopen", download)
    monkeypatch.setenv("CATSNAIL_CONFIG_DIR", str(tmp_path / "cache"))
    machine = Machine(
        iso="https://images.example.test/debian.iso", sha256=checksum
    )

    first = asyncio.run(resolve_iso(machine.iso, machine.sha256))
    second = asyncio.run(resolve_iso(machine.iso, machine.sha256))

    assert first is not None
    assert second is not None
    assert first == second
    assert first.read_bytes() == payload
    assert requested == [str(machine.iso)]
    manifest = json.loads((first.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == {"sha256": checksum, "size": len(payload), "url": machine.iso}


def test_reports_the_calculated_checksum_when_a_remote_iso_is_unpinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"catsnail unpinned ISO"
    checksum = hashlib.sha256(payload).hexdigest()
    requested: list[str] = []

    def download(request: object, *, timeout: float) -> _Response:
        del timeout
        requested.append(getattr(request, "full_url"))
        return _Response(payload)

    monkeypatch.setattr("catsnail.image.urlopen", download)
    monkeypatch.setenv("CATSNAIL_CONFIG_DIR", str(tmp_path / "cache"))
    url = "https://images.example.test/debian.iso"

    with pytest.raises(ImageError, match=checksum):
        asyncio.run(resolve_iso(url, None))

    pinned = Machine(iso=url, sha256=checksum)
    cached = asyncio.run(resolve_iso(pinned.iso, pinned.sha256))

    assert cached is not None
    assert cached.read_bytes() == payload
    assert requested == [url]


def test_rejects_a_download_with_the_wrong_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "catsnail.image.urlopen",
        lambda *_args, **_kwargs: _Response(b"unexpected"),
    )
    monkeypatch.setenv("CATSNAIL_CONFIG_DIR", str(tmp_path / "cache"))
    machine = Machine(
        iso="https://images.example.test/debian.iso", sha256="0" * 64
    )

    with pytest.raises(ImageError, match="sha256 mismatch"):
        asyncio.run(resolve_iso(machine.iso, machine.sha256))

    assert not list((tmp_path / "cache" / "iso").rglob("image.iso"))


def test_remote_iso_identity_participates_in_checkpoint_keys() -> None:
    first = add_os(
        Machine(
            iso="https://images.example.test/first.iso", sha256="1" * 64
        )
    )
    second = add_os(
        Machine(
            iso="https://images.example.test/second.iso", sha256="2" * 64
        )
    )

    @add_test
    async def boot_first(guest: Guest = use(first)) -> None:
        del guest

    @add_test
    async def boot_second(guest: Guest = use(second)) -> None:
        del guest

    assert checkpoint_key(boot_first) != checkpoint_key(boot_second)
