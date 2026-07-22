"""Pure deterministic validation for bounded local lineage graphs."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

from .errors import InvalidProvenanceError, LineageCycleError, MissingParentError
from .values import Sha256Digest


@dataclass(frozen=True, slots=True)
class LineageNode:
    """An exact artifact digest and its canonical direct parent digests."""

    digest: Sha256Digest
    parent_hashes: tuple[Sha256Digest, ...] = ()

    def __post_init__(self) -> None:
        digest = cast(object, self.digest)
        parents = cast(object, self.parent_hashes)
        if not isinstance(digest, Sha256Digest):
            raise InvalidProvenanceError("lineage node digest must be a Sha256Digest")
        if not isinstance(parents, tuple):
            raise InvalidProvenanceError(
                "lineage parents must be an immutable tuple of Sha256Digest values"
            )
        parent_values = cast(tuple[object, ...], parents)
        if not all(isinstance(parent, Sha256Digest) for parent in parent_values):
            raise InvalidProvenanceError(
                "lineage parents must be an immutable tuple of Sha256Digest values"
            )

        typed_parents = cast(tuple[Sha256Digest, ...], parents)
        if len(set(typed_parents)) != len(typed_parents):
            raise InvalidProvenanceError("duplicate lineage parent")
        object.__setattr__(
            self,
            "parent_hashes",
            tuple(sorted(typed_parents, key=lambda parent: parent.value)),
        )


def validate_lineage_graph(
    nodes: Iterable[LineageNode],
    *,
    external_roots: Iterable[Sha256Digest] = (),
) -> tuple[LineageNode, ...]:
    """Return digest-ordered nodes after deterministic parent and cycle checks."""

    ordered = tuple(sorted(nodes, key=lambda node: node.digest.value))
    by_digest: dict[Sha256Digest, LineageNode] = {}
    for node in ordered:
        if node.digest in by_digest:
            raise InvalidProvenanceError(f"duplicate lineage node: {node.digest}")
        by_digest[node.digest] = node

    roots = tuple(sorted(external_roots, key=lambda root: root.value))
    if len(set(roots)) != len(roots):
        raise InvalidProvenanceError("duplicate external lineage root")
    root_set = frozenset(roots)
    if root_set.intersection(by_digest):
        raise InvalidProvenanceError("external lineage root duplicates a local node")

    for node in ordered:
        for parent in node.parent_hashes:
            if parent == node.digest:
                raise LineageCycleError(f"lineage cycle at: {node.digest}")
            if parent not in by_digest and parent not in root_set:
                raise MissingParentError(f"missing lineage parent: {parent}")

    color: dict[Sha256Digest, int] = {}
    for start in by_digest:
        if color.get(start, 0) != 0:
            continue
        stack: list[tuple[Sha256Digest, bool]] = [(start, False)]
        while stack:
            digest, exiting = stack.pop()
            if exiting:
                color[digest] = 2
                continue
            state = color.get(digest, 0)
            if state == 2:
                continue
            if state == 1:
                raise LineageCycleError(f"lineage cycle at: {digest}")
            color[digest] = 1
            stack.append((digest, True))
            parents = by_digest[digest].parent_hashes
            for parent in reversed(parents):
                if parent in root_set:
                    continue
                if color.get(parent, 0) == 1:
                    raise LineageCycleError(f"lineage cycle at: {parent}")
                if color.get(parent, 0) == 0:
                    stack.append((parent, False))

    return ordered
