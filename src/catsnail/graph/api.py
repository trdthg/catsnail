"""Typed test graph declarations.

The graph is intentionally declarative. Execution and VM snapshotting live in
separate layers so graph collection remains deterministic and easy to test.
"""

from __future__ import annotations

import ast
import inspect
import re
from dataclasses import dataclass, field
from ipaddress import IPv4Network, ip_network
from inspect import Parameter, Signature, signature
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Generic,
    Mapping,
    Protocol,
    TypeVar,
    Union,
    cast,
    get_args,
    get_type_hints,
    overload,
)
from urllib.parse import urlparse

from ..guest.controls import Guest


T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")
W = TypeVar("W")


class GraphDefinitionError(ValueError):
    """Raised when a test node cannot be collected safely."""


@dataclass(frozen=True)
class NetSocket:
    """Configure a graph-private, rootless QEMU socket LAN."""

    subnet: str


@dataclass(frozen=True)
class NetUser:
    """Configure an independent QEMU SLIRP DHCP/NAT egress NIC."""


NetworkBackend = NetSocket | NetUser


@dataclass(frozen=True, eq=False)
class Network:
    """A declared QEMU network attachment.

    ``NetSocket`` is a graph-private IPv4 LAN shared by every machine that
    declares it. ``NetUser`` is an independent SLIRP egress NIC for each
    machine; it is not a multi-machine LAN.
    """

    backend: NetworkBackend
    declaration: str = "<unknown>"

    def __post_init__(self) -> None:
        if isinstance(self.backend, NetUser):
            return
        if not isinstance(self.backend, NetSocket):
            raise GraphDefinitionError(f"unsupported network backend {self.backend!r}")
        try:
            parsed = ip_network(self.backend.subnet, strict=True)
        except ValueError as error:
            raise GraphDefinitionError(
                f"NetSocket has invalid subnet {self.backend.subnet!r}"
            ) from error
        if not isinstance(parsed, IPv4Network):
            raise GraphDefinitionError("NetSocket must use an IPv4 subnet")

    @property
    def subnet(self) -> str | None:
        return self.backend.subnet if isinstance(self.backend, NetSocket) else None


@dataclass(frozen=True)
class Machine:
    """A declarative QEMU machine definition for the first vertical slice."""

    iso: Path | str | None = None
    sha256: str | None = None
    disk: Path | None = None
    disk_size: str = "8G"
    memory: str = "1G"
    vcpus: int = 1
    display: str = "none"
    boot_args: tuple[str, ...] = ()
    networks: tuple[Network, ...] = ()

    def __post_init__(self) -> None:
        if self.vcpus < 1:
            raise GraphDefinitionError("machine vcpus must be at least 1")
        if not self.disk_size.strip():
            raise GraphDefinitionError("machine disk_size must not be empty")
        if self.iso is not None and self.disk is not None:
            raise GraphDefinitionError("machine may use either iso or disk, not both")
        if isinstance(self.iso, str):
            _validate_remote_iso(self.iso, self.sha256)
            if self.sha256 is not None:
                object.__setattr__(self, "sha256", self.sha256.lower())
        elif self.sha256 is not None:
            raise GraphDefinitionError("sha256 requires an HTTPS or HTTP ISO URL")
        if len(self.networks) != len(set(self.networks)):
            raise GraphDefinitionError("machine declares a network more than once")

@dataclass(frozen=True)
class Source(Generic[T]):
    machine: Machine
    declaration: str
    output_type: Any = Guest

    @property
    def id(self) -> str:
        return f"machine:{self.declaration}"


@dataclass(frozen=True)
class Dependency(Generic[T]):
    node: Source[T] | TestNode[T]
    path: tuple[int, ...] = ()

    def __getitem__(self, index: int) -> Any:
        return Dependency(node=self.node, path=(*self.path, index))


@dataclass(frozen=True)
class TestNode(Generic[T]):
    function: Callable[..., Awaitable[None]]
    dependencies: Mapping[str, Dependency[Any]]
    parameter_annotations: Mapping[str, Any]
    inputs: Mapping[str, Any]
    result_annotation: Any

    @property
    def id(self) -> str:
        return f"{self.function.__module__}:{self.function.__qualname__}"

Node = Union[Source[Any], TestNode[Any]]


def add_net(backend: NetworkBackend) -> Network:
    """Declare a typed QEMU network.

    ``NetSocket(subnet=...)`` creates a rootless private LAN. ``NetUser()`` is
    a DHCP-configured SLIRP egress NIC.
    """

    return Network(backend=backend, declaration=_declaration_location("add_net"))


def add_os(machine: Machine) -> Source[Guest]:
    """Register a cold VM source for use in a test function's ``use`` default."""

    return Source(machine=machine, declaration=_declaration_location("add_os"))


def _declaration_location(call_name: str) -> str:
    """Return a stable identity for an ``add_os(...)`` declaration."""

    frame = inspect.currentframe()
    try:
        caller = frame.f_back.f_back if frame is not None and frame.f_back else None
        if caller is None:
            return "<unknown>"
        path = Path(caller.f_code.co_filename).resolve()
        name = _declaration_name(path, caller.f_lineno, call_name)
        if name is not None:
            return f"{path}:{name}"
        return f"{path}:{caller.f_lineno}:{caller.f_lasti}"
    finally:
        del frame


def _declaration_name(path: Path, line: int, call_name: str) -> str | None:
    """Find the assignment target wrapping the current ``add_os`` call."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in ast.walk(tree):
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if not isinstance(target, ast.Name) or value is None:
            continue
        if _contains_declaration_call(value, line, call_name):
            return target.id
    return None


def _contains_declaration_call(value: ast.expr, line: int, call_name: str) -> bool:
    for node in ast.walk(value):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != call_name:
            continue
        if node.lineno <= line <= getattr(node, "end_lineno", node.lineno):
            return True
    return False


def use(node: Source[T] | TestNode[T]) -> T:
    """Declare a typed graph dependency in a test function default value.

    The return annotation is intentionally the node result type for static type
    checkers. At runtime the decorator receives a ``Dependency`` marker.
    """

    return cast(T, Dependency(node=node))


class _TestDecorator(Protocol):
    """Typed ``@add_test(inputs=...)`` form for common pod sizes."""

    @overload
    def __call__(
        self, function: Callable[[T, U, V, W], Awaitable[None]], /
    ) -> TestNode[tuple[T, U, V, W]]: ...

    @overload
    def __call__(
        self, function: Callable[[T, U, V], Awaitable[None]], /
    ) -> TestNode[tuple[T, U, V]]: ...

    @overload
    def __call__(
        self, function: Callable[[T, U], Awaitable[None]], /
    ) -> TestNode[tuple[T, U]]: ...

    @overload
    def __call__(self, function: Callable[[T], Awaitable[None]], /) -> TestNode[T]: ...


@overload
def add_test(
    function: Callable[[T, U, V, W], Awaitable[None]], /
) -> TestNode[tuple[T, U, V, W]]: ...


@overload
def add_test(
    function: Callable[[T, U, V], Awaitable[None]], /
) -> TestNode[tuple[T, U, V]]: ...


@overload
def add_test(function: Callable[[T, U], Awaitable[None]], /) -> TestNode[tuple[T, U]]: ...


@overload
def add_test(function: Callable[[T], Awaitable[None]], /) -> TestNode[T]: ...


@overload
def add_test(
    *,
    inputs: Mapping[str, Any] | None = None,
) -> _TestDecorator: ...


def add_test(
    function: Callable[..., Awaitable[Any]] | None = None,
    *,
    inputs: Mapping[str, Any] | None = None,
) -> Any:
    """Decorate an async test function and collect typed ``use`` dependencies."""

    def decorate(target: Callable[..., Awaitable[None]]) -> TestNode[Any]:
        if not callable(target):
            raise GraphDefinitionError("add_test can only decorate a callable")

        test_signature = signature(target)
        try:
            type_hints = get_type_hints(target)
        except (NameError, TypeError) as error:
            raise GraphDefinitionError(
                f"{target.__qualname__} has unresolved type annotations: {error}"
            ) from error
        declared_return = type_hints.get("return", None)
        if declared_return not in (None, type(None)):
            raise GraphDefinitionError(
                f"{target.__qualname__} must not return a value; Catsnail returns "
                "the test inputs as its checkpoint state"
            )
        dependencies = _collect_dependencies(target, test_signature)
        parameter_annotations = {
            name: type_hints[name] for name in dependencies if name in type_hints
        }
        result_annotation = _implicit_output_annotation(
            target, parameter_annotations
        )

        return TestNode(
            function=target,
            dependencies=dependencies,
            parameter_annotations=parameter_annotations,
            inputs=dict(inputs or {}),
            result_annotation=result_annotation,
        )

    if function is None:
        return decorate
    return decorate(function)


def _collect_dependencies(
    target: Callable[..., Awaitable[None]], test_signature: Signature
) -> dict[str, Dependency[Any]]:
    dependencies: dict[str, Dependency[Any]] = {}
    for parameter in test_signature.parameters.values():
        if parameter.kind in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD):
            raise GraphDefinitionError(
                f"{target.__qualname__} cannot use variadic parameter {parameter.name!r}"
            )
        if parameter.annotation is Parameter.empty:
            raise GraphDefinitionError(
                f"{target.__qualname__}.{parameter.name} needs an explicit type annotation"
            )
        if not isinstance(parameter.default, Dependency):
            raise GraphDefinitionError(
                f"{target.__qualname__}.{parameter.name} must use use(node) as its default"
            )
        dependencies[parameter.name] = parameter.default
    return dependencies


def _implicit_output_annotation(
    target: Callable[..., Awaitable[None]], parameter_annotations: Mapping[str, Any]
) -> Any:
    output_types = tuple(parameter_annotations.values())
    if not output_types:
        raise GraphDefinitionError(
            f"{target.__qualname__} needs at least one use(...) dependency"
        )
    if len(output_types) == 1:
        return output_types[0]
    return tuple[output_types]


@dataclass
class TestGraph:
    """Collected test graph with cycle and dependency-type validation."""

    roots: list[TestNode[Any]] = field(default_factory=list)

    def validate(self) -> list[Node]:
        ordered: list[Node] = []
        visited: set[str] = set()
        active: list[str] = []
        identities: dict[str, Node] = {}

        def visit(node: Node) -> None:
            node_id = node.id
            existing = identities.get(node_id)
            if existing is not None and existing is not node:
                raise GraphDefinitionError(
                    f"duplicate node identity {node_id}; declare each source separately"
                )
            identities[node_id] = node
            if node_id in active:
                start = active.index(node_id)
                cycle = " -> ".join([*active[start:], node_id])
                raise GraphDefinitionError(f"dependency cycle: {cycle}")
            if node_id in visited:
                return

            active.append(node_id)
            if isinstance(node, TestNode):
                for parameter_name, dependency in node.dependencies.items():
                    expected = node.parameter_annotations[parameter_name]
                    actual = _dependency_output_type(dependency)
                    if expected is Any:
                        raise GraphDefinitionError(
                            f"{node.id}.{parameter_name} must not use typing.Any"
                        )
                    if expected != actual:
                        raise GraphDefinitionError(
                            f"{node.id}.{parameter_name} expects {_type_name(expected)} "
                            f"but dependency {dependency.node.id} returns {_type_name(actual)}"
                        )
                    visit(dependency.node)
            active.pop()
            visited.add(node_id)
            ordered.append(node)

        for root in self.roots:
            visit(root)
        return ordered


def collect_module_tests(module: Any) -> TestGraph:
    """Collect decorated test nodes exported by an imported Python module."""

    nodes = [value for value in vars(module).values() if isinstance(value, TestNode)]
    graph = TestGraph(roots=nodes)
    graph.validate()
    return graph


def select_test_targets(
    graph: TestGraph, selection: str | None = None
) -> list[TestNode[Any]]:
    """Return decorated tests selected by an optional Python regular expression.

    A return value makes a test reusable as a checkpoint dependency; it does
    not make that test an implementation detail. Every ``@add_test`` is an
    executable test target unless narrowed with ``--test``.
    """

    nodes = [node for node in graph.validate() if isinstance(node, TestNode)]
    if selection is not None:
        try:
            pattern = re.compile(selection)
        except re.error as error:
            raise GraphDefinitionError(
                f"invalid test pattern {selection!r}: {error}"
            ) from error
        selected = [
            node
            for node in nodes
            if pattern.search(node.function.__name__) or pattern.search(node.id)
        ]
        if not selected:
            raise GraphDefinitionError(f"no collected test matches {selection!r}")
        return selected
    return nodes


def _node_output_type(node: Node) -> Any:
    if isinstance(node, Source):
        return node.output_type
    return node.result_annotation


def _dependency_output_type(dependency: Dependency[Any]) -> Any:
    output_type = _node_output_type(dependency.node)
    for index in dependency.path:
        items = _fixed_tuple_items(output_type)
        if index < 0 or index >= len(items):
            raise GraphDefinitionError(
                f"{dependency.node.id} has no tuple output at index {index}"
            )
        output_type = items[index]
    return output_type


def _fixed_tuple_items(output_type: Any) -> tuple[Any, ...]:
    items = get_args(output_type)
    if not items or items[-1] is Ellipsis:
        raise GraphDefinitionError(
            f"{_type_name(output_type)} is not a fixed-length tuple checkpoint output"
        )
    return items


def _type_name(annotation: Any) -> str:
    return getattr(annotation, "__name__", str(annotation))


def _validate_remote_iso(url: str, sha256: str | None) -> None:
    if urlparse(url).scheme not in {"http", "https"}:
        raise GraphDefinitionError(f"ISO URL must use http or https, got {url!r}")
    if sha256 is None:
        return
    if len(sha256) != 64 or any(
        character not in "0123456789abcdef" for character in sha256.lower()
    ):
        raise GraphDefinitionError("sha256 must be a 64-character hexadecimal digest")
