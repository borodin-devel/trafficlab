from itertools import permutations

import pytest

from trafficlab.libs.lineage import (
    InvalidProvenanceError,
    LineageCycleError,
    LineageNode,
    MissingParentError,
    Sha256Digest,
    validate_lineage_graph,
)


def _digest(number: int) -> Sha256Digest:
    return Sha256Digest(f"{number:064x}")


def _node(number: int, *parents: int) -> LineageNode:
    return LineageNode(_digest(number), tuple(_digest(parent) for parent in parents))


@pytest.mark.unit
def test_one_root_is_valid() -> None:
    root = _node(1)

    assert validate_lineage_graph((root,)) == (root,)


@pytest.mark.unit
def test_one_external_root_is_valid() -> None:
    external = _digest(1)
    node = _node(2, 1)

    assert validate_lineage_graph(iter((node,)), external_roots=iter((external,))) == (
        node,
    )


@pytest.mark.unit
def test_lineage_node_canonicalizes_parent_permutations() -> None:
    parents = (_digest(3), _digest(1), _digest(2))
    expected = tuple(sorted(parents, key=lambda parent: parent.value))

    for parent_order in permutations(parents):
        assert LineageNode(_digest(4), parent_order).parent_hashes == expected


@pytest.mark.unit
def test_unordered_acyclic_nodes_return_the_same_sorted_tuple() -> None:
    nodes = (_node(3, 2), _node(0), _node(2, 1, 0), _node(1))
    expected = tuple(sorted(nodes, key=lambda node: node.digest.value))

    for node_order in permutations(nodes):
        assert validate_lineage_graph(node_order) == expected


@pytest.mark.unit
def test_duplicate_nodes_raise_the_same_error_for_every_permutation() -> None:
    duplicate = _digest(1)
    nodes = (LineageNode(duplicate), LineageNode(duplicate, (_digest(9),)))
    observed: list[tuple[type[BaseException], str]] = []

    for node_order in permutations(nodes):
        with pytest.raises(InvalidProvenanceError) as caught:
            validate_lineage_graph(node_order)
        observed.append((type(caught.value), str(caught.value)))

    assert observed == [
        (InvalidProvenanceError, f"duplicate lineage node: {duplicate}"),
        (InvalidProvenanceError, f"duplicate lineage node: {duplicate}"),
    ]


@pytest.mark.unit
def test_lineage_node_rejects_duplicate_parents() -> None:
    parent = _digest(1)

    with pytest.raises(InvalidProvenanceError):
        LineageNode(_digest(2), (parent, parent))


@pytest.mark.unit
def test_duplicate_external_roots_are_rejected() -> None:
    root = _digest(1)

    with pytest.raises(
        InvalidProvenanceError, match=r"^duplicate external lineage root$"
    ):
        validate_lineage_graph((), external_roots=(root, root))


@pytest.mark.unit
def test_external_root_duplicating_local_node_is_deterministic() -> None:
    nodes = (_node(2), _node(1))
    roots = (_digest(3), _digest(1))
    observed: set[tuple[type[BaseException], str]] = set()

    for node_order in permutations(nodes):
        for root_order in permutations(roots):
            with pytest.raises(InvalidProvenanceError) as caught:
                validate_lineage_graph(node_order, external_roots=root_order)
            observed.add((type(caught.value), str(caught.value)))

    assert observed == {
        (
            InvalidProvenanceError,
            "external lineage root duplicates a local node",
        )
    }


@pytest.mark.unit
def test_missing_parent_selection_is_deterministic() -> None:
    first_missing = _digest(6)
    nodes = (_node(2, 8, 7), _node(0), _node(1, 6))
    observed: set[tuple[type[BaseException], str]] = set()

    for node_order in permutations(nodes):
        with pytest.raises(MissingParentError) as caught:
            validate_lineage_graph(node_order, external_roots=(_digest(9),))
        observed.add((type(caught.value), str(caught.value)))

    assert observed == {
        (MissingParentError, f"missing lineage parent: {first_missing}")
    }


@pytest.mark.unit
def test_self_cycle_selection_is_deterministic() -> None:
    first_cycle = _digest(1)
    nodes = (_node(2, 2), _node(1, 1))
    observed: set[tuple[type[BaseException], str]] = set()

    for node_order in permutations(nodes):
        with pytest.raises(LineageCycleError) as caught:
            validate_lineage_graph(node_order)
        observed.add((type(caught.value), str(caught.value)))

    assert observed == {(LineageCycleError, f"lineage cycle at: {first_cycle}")}


@pytest.mark.unit
def test_multi_node_cycle_selection_is_deterministic() -> None:
    first_cycle = _digest(1)
    nodes = (_node(4, 3), _node(2, 1), _node(3, 4), _node(1, 2))
    observed: set[tuple[type[BaseException], str]] = set()

    for node_order in permutations(nodes):
        with pytest.raises(LineageCycleError) as caught:
            validate_lineage_graph(node_order)
        observed.add((type(caught.value), str(caught.value)))

    assert observed == {(LineageCycleError, f"lineage cycle at: {first_cycle}")}


@pytest.mark.unit
def test_deep_untrusted_graph_is_validated_iteratively() -> None:
    node_count = 1_500
    nodes = tuple(
        _node(number, number + 1) if number + 1 < node_count else _node(number)
        for number in range(node_count)
    )

    assert validate_lineage_graph(reversed(nodes)) == nodes
