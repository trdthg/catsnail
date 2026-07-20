"""Base protocol for explicit guest capability adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .controls import Guest


class GuestAdapter:
    """A non-serializable, OS-specific capability wrapper for one guest."""

    def __init__(self, guest: Guest) -> None:
        self.guest = guest
