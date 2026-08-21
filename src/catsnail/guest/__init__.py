"""Core guest controls and optional OS capability adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .adapter import GuestAdapter
from .controls import (
    Guest,
    GuestControlError,
    GuestNetwork,
    NetworkInterface,
    NetworkLink,
    ScreenAssertionError,
)

if TYPE_CHECKING:
    from .debian import DebianAdapter, DebianSerial
    from .ubuntu import UbuntuAdapter, UbuntuWindow

__all__ = [
    "Guest",
    "GuestAdapter",
    "GuestControlError",
    "ScreenAssertionError",
    "DebianAdapter",
    "GuestNetwork",
    "NetworkInterface",
    "NetworkLink",
    "DebianSerial",
    "UbuntuAdapter",
    "UbuntuWindow",
]


def __getattr__(name: str) -> Any:
    if name in {"DebianAdapter", "DebianSerial"}:
        from .debian import DebianAdapter, DebianSerial

        return {"DebianAdapter": DebianAdapter, "DebianSerial": DebianSerial}[name]
    if name in {"UbuntuAdapter", "UbuntuWindow"}:
        from .ubuntu import UbuntuAdapter, UbuntuWindow

        return {"UbuntuAdapter": UbuntuAdapter, "UbuntuWindow": UbuntuWindow}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
