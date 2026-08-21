"""Public API for Catsnail's initial Python implementation."""

from .graph.api import (
    Guest,
    Machine,
    NetSocket,
    NetUser,
    Network,
    Source,
    TestNode,
    add_net,
    add_os,
    add_test,
    use,
)
from .guest.adapter import GuestAdapter
from .guest.debian import DebianAdapter, DebianSerial
from .guest.ubuntu import UbuntuAdapter, UbuntuWindow
from .graph.executor import xfail

__all__ = [
    "Guest",
    "DebianAdapter",
    "GuestAdapter",
    "DebianSerial",
    "UbuntuAdapter",
    "UbuntuWindow",
    "Machine",
    "NetSocket",
    "NetUser",
    "Network",
    "Source",
    "TestNode",
    "add_net",
    "add_os",
    "add_test",
    "use",
    "xfail",
]
