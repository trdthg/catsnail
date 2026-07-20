"""Runtime allocation for rootless QEMU socket and user networks."""

from __future__ import annotations

import hashlib
import socket
import uuid
from dataclasses import dataclass

from ..graph.api import GraphDefinitionError, NetSocket, Network


@dataclass(frozen=True)
class SocketAttachment:
    """One source's endpoint on a loopback TCP Ethernet link."""

    endpoint: str
    port: int
    mac: str


@dataclass(frozen=True)
class UserAttachment:
    """One guest's independent QEMU SLIRP egress NIC."""

    subnet: str
    mac: str


@dataclass
class SocketSegment:
    """One graph-private, two-machine QEMU socket segment.

    QEMU's TCP socket backend binds only to 127.0.0.1. It therefore needs no
    TAP device, bridge, multicast route, or packets on the host LAN.
    """

    network: Network
    port: int
    source_ids: list[str]

    @classmethod
    def create(cls, network: Network) -> SocketSegment:
        return cls(network=network, port=_free_tcp_port(), source_ids=[])

    def attachment_for(self, source_id: str, index: int) -> SocketAttachment:
        if source_id not in self.source_ids:
            if len(self.source_ids) == 2:
                raise GraphDefinitionError(
                    "NetSocket supports two attached machines; declare a second "
                    "NetSocket link for an additional machine"
                )
            self.source_ids.append(source_id)
        return SocketAttachment(
            endpoint="listen" if source_id == self.source_ids[0] else "connect",
            port=self.port,
            mac=_network_mac(source_id, self.network, index),
        )


@dataclass(frozen=True)
class UserSegment:
    """SLIRP subnet allocation for one declared user network."""

    network: Network
    subnet: str

    def attachment_for(self, source_id: str, index: int) -> UserAttachment:
        return UserAttachment(
            subnet=self.subnet,
            mac=_network_mac(source_id, self.network, index),
        )


NetworkAttachment = SocketAttachment | UserAttachment
NetworkSegment = SocketSegment | UserSegment


class NetworkPool:
    """Allocate network-runtime details once for one graph execution."""

    def __init__(self) -> None:
        self._segments: dict[Network, NetworkSegment] = {}
        self._user_subnets: set[str] = set()

    def attachments_for(
        self, source_id: str, networks: tuple[Network, ...]
    ) -> tuple[NetworkAttachment, ...]:
        return tuple(
            self._segment_for(network).attachment_for(source_id, index)
            for index, network in enumerate(networks)
        )

    def _segment_for(self, network: Network) -> NetworkSegment:
        segment = self._segments.get(network)
        if segment is None:
            segment = (
                SocketSegment.create(network)
                if isinstance(network.backend, NetSocket)
                else UserSegment(network, self._allocate_user_subnet())
            )
            self._segments[network] = segment
        return segment

    def _allocate_user_subnet(self) -> str:
        while True:
            token = uuid.uuid4().bytes
            subnet = f"10.{64 + token[0] % 128}.{token[1]}.0/24"
            if subnet not in self._user_subnets:
                self._user_subnets.add(subnet)
                return subnet


def _network_mac(source_id: str, network: Network, index: int) -> str:
    digest = hashlib.sha256(
        f"{source_id}:{index}:{type(network.backend).__name__}:{network.subnet}".encode(
            "utf-8"
        )
    ).digest()
    return ":".join(["52", "54", *(f"{part:02x}" for part in digest[:4])])


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
