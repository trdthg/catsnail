from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from catsnail.graph.api import (
    Guest,
    GraphDefinitionError,
    Machine,
    NetSocket,
    NetUser,
    Network,
    add_net,
    add_os,
    add_test,
    collect_module_tests,
    select_test_targets,
    use,
)


def test_collects_checkpoint_dependency() -> None:
    source = add_os(Machine())

    @add_test
    async def boot(desktop: Guest = use(source)):
        del desktop

    @add_test
    async def browser(desktop: Guest = use(boot)) -> None:
        assert isinstance(desktop, Guest)

    graph = collect_module_tests(SimpleNamespace(boot=boot, browser=browser))
    assert [node.id for node in graph.validate()] == [source.id, boot.id, browser.id]
    assert boot.result_annotation is Guest
    assert browser.result_annotation is Guest


def test_selects_targets_with_a_regular_expression() -> None:
    source = add_os(Machine())

    @add_test
    async def test_browser(desktop: Guest = use(source)) -> None:
        del desktop

    @add_test
    async def test_ssh(desktop: Guest = use(source)) -> None:
        del desktop

    graph = collect_module_tests(
        SimpleNamespace(test_browser=test_browser, test_ssh=test_ssh)
    )

    assert [
        node.function.__name__
        for node in select_test_targets(graph, r"^test_(browser|ssh)$")
    ] == ["test_browser", "test_ssh"]


def test_selects_every_decorated_test_by_default() -> None:
    source = add_os(Machine())

    @add_test
    async def test_desktop_boot(desktop: Guest = use(source)) -> None:
        del desktop

    @add_test
    async def test_browser_opens(desktop: Guest = use(test_desktop_boot)) -> None:
        del desktop

    graph = collect_module_tests(
        SimpleNamespace(
            test_desktop_boot=test_desktop_boot,
            test_browser_opens=test_browser_opens,
        )
    )

    assert [node.function.__name__ for node in select_test_targets(graph)] == [
        "test_desktop_boot",
        "test_browser_opens",
    ]


def test_rejects_an_invalid_target_regular_expression() -> None:
    source = add_os(Machine())

    @add_test
    async def test_browser(desktop: Guest = use(source)) -> None:
        del desktop

    graph = collect_module_tests(SimpleNamespace(test_browser=test_browser))

    with pytest.raises(GraphDefinitionError, match="invalid test pattern"):
        select_test_targets(graph, "[")


def test_rejects_removed_network_decorator_option() -> None:
    with pytest.raises(TypeError, match="network"):
        add_test(network="external")  # type: ignore[call-arg]


def test_rejects_invalid_private_network_declarations() -> None:
    with pytest.raises(GraphDefinitionError, match="invalid subnet"):
        add_net(NetSocket(subnet="not-a-subnet"))

    network = add_net(NetSocket(subnet="192.168.76.0/24"))
    with pytest.raises(GraphDefinitionError, match="more than once"):
        Machine(networks=(network, network))
    with pytest.raises(TypeError, match="transport"):
        Network(NetSocket(subnet="192.168.76.0/24"), transport="socket")  # type: ignore[call-arg]


def test_rejects_an_empty_runtime_disk_size() -> None:
    with pytest.raises(GraphDefinitionError, match="disk_size"):
        Machine(disk_size="   ")


def test_add_net_validates_user_and_socket_backends() -> None:
    socket_network = add_net(NetSocket(subnet="192.168.76.0/24"))
    user_network = add_net(NetUser())

    assert isinstance(socket_network.backend, NetSocket)
    assert socket_network.subnet == "192.168.76.0/24"
    assert isinstance(user_network.backend, NetUser)
    assert user_network.subnet is None
    assert user_network.declaration.endswith(":user_network")

    with pytest.raises(GraphDefinitionError, match="unsupported network backend"):
        add_net(cast(Any, object()))


def test_os_source_identity_uses_its_assignment_name() -> None:
    first = add_os(Machine())
    second = add_os(Machine())

    assert first.declaration.endswith(":first")
    assert second.declaration.endswith(":second")


def test_projects_a_tuple_checkpoint_into_separate_guest_parameters() -> None:
    left_source = add_os(Machine())
    right_source = add_os(Machine())

    @add_test
    async def pod(
        left: Guest = use(left_source), right: Guest = use(right_source)
    ) -> None:
        del left, right

    @add_test
    async def consumer(
        server: Guest = use(pod)[0],
        client: Guest = use(pod)[1],
    ) -> None:
        assert isinstance(server, Guest)
        assert isinstance(client, Guest)

    graph = collect_module_tests(SimpleNamespace(consumer=consumer))
    assert [node.id for node in graph.validate()] == [
        left_source.id,
        right_source.id,
        pod.id,
        consumer.id,
    ]
    assert pod.result_annotation == tuple[Guest, Guest]
    assert consumer.result_annotation == tuple[Guest, Guest]


def test_rejects_sources_created_from_the_same_helper_call_site() -> None:
    def create_source() -> object:
        return add_os(Machine())

    first = create_source()
    second = create_source()

    @add_test
    async def consumer(
        left: Guest = use(first),  # type: ignore[arg-type]
        right: Guest = use(second),  # type: ignore[arg-type]
    ) -> None:
        del left, right

    with pytest.raises(GraphDefinitionError, match="duplicate node identity"):
        collect_module_tests(SimpleNamespace(consumer=consumer))


def test_rejects_a_test_with_a_value_return_type() -> None:
    source = add_os(Machine())

    with pytest.raises(GraphDefinitionError, match="must not return a value"):

        @add_test  # type: ignore[arg-type]
        async def invalid(desktop: Guest = use(source)) -> Guest:
            return desktop


def test_rejects_untyped_parameter() -> None:
    source = add_os(Machine())

    with pytest.raises(GraphDefinitionError, match="explicit type annotation"):

        @add_test
        async def invalid(desktop=use(source)) -> None:
            del desktop


def test_test_node_is_not_callable() -> None:
    source = add_os(Machine())

    @add_test
    async def boot(desktop: Guest = use(source)) -> None:
        del desktop

    assert not callable(boot)


def test_rejects_dependency_type_mismatch() -> None:
    source = add_os(Machine())

    @add_test
    async def make_pair(
        left: Guest = use(source), right: Guest = use(source)
    ) -> None:
        del left, right

    @add_test
    async def invalid(desktop: Guest = use(make_pair)) -> None:  # type: ignore[reportArgumentType]
        del desktop

    with pytest.raises(GraphDefinitionError, match="expects Guest"):
        collect_module_tests(SimpleNamespace(invalid=invalid))
