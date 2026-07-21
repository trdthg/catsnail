import pytest

from catsnail.graph.api import GraphDefinitionError, NetSocket, NetUser, add_net
from catsnail.qemu.network import NetworkPool, SocketAttachment, UserAttachment


def test_network_pool_creates_an_isolated_loopback_link() -> None:
    pool = NetworkPool()
    network = add_net(NetSocket(subnet="192.168.76.0/24"))
    server = pool.attachments_for("machine:server", (network,))[0]
    client = pool.attachments_for("machine:client", (network,))[0]

    assert isinstance(server, SocketAttachment)
    assert isinstance(client, SocketAttachment)
    assert server.endpoint == "listen"
    assert client.endpoint == "connect"
    assert 1 <= server.port <= 65_535
    assert server.port == client.port
    assert server.mac != client.mac


def test_network_pool_isolates_distinct_declared_networks() -> None:
    pool = NetworkPool()
    first = pool.attachments_for(
        "machine:first", (add_net(NetSocket(subnet="192.168.10.0/24")),)
    )[0]
    second = pool.attachments_for(
        "machine:second", (add_net(NetSocket(subnet="192.168.20.0/24")),)
    )[0]

    assert isinstance(first, SocketAttachment)
    assert isinstance(second, SocketAttachment)
    assert first.port != second.port


def test_network_pool_rejects_a_third_machine_on_one_socket_link() -> None:
    pool = NetworkPool()
    network = add_net(NetSocket(subnet="192.168.76.0/24"))
    pool.attachments_for("machine:first", (network,))
    pool.attachments_for("machine:second", (network,))

    with pytest.raises(GraphDefinitionError, match="two attached machines"):
        pool.attachments_for("machine:third", (network,))


def test_network_pool_allocates_an_isolated_user_egress_nic() -> None:
    pool = NetworkPool()
    attachment = pool.attachments_for("machine:desktop", (add_net(NetUser()),))[0]

    assert isinstance(attachment, UserAttachment)
    assert attachment.subnet.startswith("10.")
    assert attachment.mac.startswith("52:54:")
    assert not isinstance(attachment, SocketAttachment)


def test_network_pool_keeps_a_user_subnet_stable_across_executions() -> None:
    network = add_net(NetUser())
    first = NetworkPool().attachments_for("machine:desktop", (network,))[0]
    second = NetworkPool().attachments_for("machine:desktop", (network,))[0]

    assert isinstance(first, UserAttachment)
    assert isinstance(second, UserAttachment)
    assert first.subnet == second.subnet
