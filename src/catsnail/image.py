"""Private resolution and caching for remote ISO declarations."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class ImageError(RuntimeError):
    """Raised when a remote machine image cannot be fetched or verified."""


async def resolve_iso(iso: Path | str | None, sha256: str | None) -> Path | None:
    """Resolve a Machine ISO declaration to a verified local file."""

    if iso is None or isinstance(iso, Path):
        return iso
    return await asyncio.to_thread(
        _resolve_remote_iso,
        iso,
        sha256,
        iso_cache_directory(),
    )


def iso_cache_directory() -> Path:
    """Return the XDG location used for URL-addressed ISO images."""

    configured = os.environ.get("CATSNAIL_CONFIG_DIR")
    if configured:
        return Path(configured) / "iso"
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "catsnail" / "iso"
    return Path.home() / ".config" / "catsnail" / "iso"


def _resolve_remote_iso(
    url: str, sha256: str | None, cache_directory: Path
) -> Path:
    cache_directory.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    lock_path = cache_directory / f"{cache_key}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            directory = cache_directory / cache_key
            image = directory / "image.iso"
            if image.is_file():
                cached_digest = _sha256(image)
                if sha256 is None:
                    raise _missing_sha256(url, cached_digest)
                if cached_digest == sha256:
                    return image
            image.unlink(missing_ok=True)
            directory.mkdir(exist_ok=True)
            temporary = _partial_image(directory)
            try:
                digest, size = _download_iso(url, temporary)
            except OSError as error:
                raise ImageError(f"failed to download remote ISO {url}: {error}") from error
            if sha256 is None:
                os.replace(temporary, image)
                _write_manifest(directory, url, digest, size)
                raise _missing_sha256(url, digest)
            if digest != sha256:
                temporary.unlink(missing_ok=True)
                raise ImageError(
                    f"ISO sha256 mismatch for {url}: expected {sha256}, got {digest}"
                )
            os.replace(temporary, image)
            _write_manifest(directory, url, digest, size)
            return image
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _write_manifest(directory: Path, url: str, sha256: str, size: int) -> None:
    (directory / "manifest.json").write_text(
        json.dumps(
            {"url": url, "sha256": sha256, "size": size},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _download_iso(url: str, destination: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = destination.stat().st_size if destination.is_file() else 0
    if size:
        with destination.open("rb") as partial:
            while chunk := partial.read(1024 * 1024):
                digest.update(chunk)
    headers = {"User-Agent": "catsnail/0.1"}
    if size:
        headers["Range"] = f"bytes={size}-"
    request = Request(url, headers=headers)
    try:
        response = urlopen(request, timeout=60)  # noqa: S310 - user-declared ISO URL
    except HTTPError as error:
        if error.code == 416 and size:
            return digest.hexdigest(), size
        raise
    with response:
        if size and getattr(response, "status", 200) != 206:
            digest = hashlib.sha256()
            size = 0
            mode = "wb"
        else:
            mode = "ab" if size else "wb"
        with destination.open(mode) as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
                size += len(chunk)
    return digest.hexdigest(), size


def _partial_image(directory: Path) -> Path:
    """Return the stable partial download path, adopting an older PID file."""

    partial = directory / ".image.part"
    if partial.is_file():
        return partial
    legacy = sorted(directory.glob(".image.*.part"), key=lambda path: path.stat().st_mtime)
    if legacy:
        os.replace(legacy[-1], partial)
    return partial


def _missing_sha256(url: str, digest: str) -> ImageError:
    return ImageError(
        f"remote ISO {url} requires sha256; calculated sha256: {digest}\n"
        f'Add sha256="{digest}" to Machine(...) and run again.'
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
