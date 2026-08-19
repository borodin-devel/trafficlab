"""Properties for genetic coordinate and checkpoint serialization boundaries."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from tests.unit.genetic.test_checkpoint import COMPATIBILITY, VALID_STATE, replace
from trafficlab.config import FloatBounds
from trafficlab.genetic.checkpoint import CheckpointState, encode_rng_state, parse_checkpoint, render_checkpoint
from trafficlab.genetic.coordinates import GeneCoordinate, decode_gene, encode_gene
from trafficlab.models.common import make_rng


@given(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
def test_linear_gene_coordinates_round_trip_within_literal_error_bound(coordinate: float) -> None:
    gene = GeneCoordinate("value", "linear", FloatBounds(lower=2.0, upper=6.0))

    assert decode_gene(gene, encode_gene(gene, decode_gene(gene, coordinate))) == pytest.approx(
        decode_gene(gene, coordinate), abs=1e-12
    )


def checkpoint_states() -> SearchStrategy[CheckpointState]:
    """Return bounded valid states with independent PCG64 continuation inputs."""
    return st.integers(min_value=0, max_value=2**32 - 1).map(
        lambda seed: replace(VALID_STATE, rng_state=encode_rng_state(make_rng(seed)))
    )


@given(checkpoint_states())
def test_checkpoint_render_parse_round_trip_is_exact(state: CheckpointState) -> None:
    assert parse_checkpoint(render_checkpoint(state), COMPATIBILITY) == state
