"""Core guest controls and optional OS capability adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .adapter import GuestAdapter
from .controls import Guest, GuestControlError, GuestNetwork, NetworkInterface

if TYPE_CHECKING:
    from .debian import DebianAdapter, DebianSerial

__all__ = [
    "Guest",
    "GuestAdapter",
    "GuestControlError",
    "DebianAdapter",
    "GuestNetwork",
    "NetworkInterface",
    "DebianSerial",
]


def __getattr__(name: str) -> Any:
    if name in {"DebianAdapter", "DebianSerial"}:
        from .debian import DebianAdapter, DebianSerial

        return {"DebianAdapter": DebianAdapter, "DebianSerial": DebianSerial}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
