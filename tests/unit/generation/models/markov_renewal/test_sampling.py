"""Behavioral tests for one Markov renewal owner."""

from __future__ import annotations

import math

import pytest

import trafficlab.generation.models.markov_renewal.sampling as markov_renewal
from tests.support.markov_renewal import (
    ScriptedMarkovRng,
    two_state_model,
)
from trafficlab.common.errors import TrafficlabError


@pytest.mark.parametrize(
    ("conditional", "source", "expected"),
    [
        ((0.1, 0.2), (0.1, 0.2, 0.3), (0.1, 0.2)),
        ((0.1,), (0.1, 0.3), (0.1, 0.3)),
        ((), (), (0.1, 0.2, 0.3)),
    ],
)
def test_timing_fallback_precedence(
    conditional: tuple[float, ...], source: tuple[float, ...], expected: tuple[float, ...]
) -> None:
    """Changing fallback order would sample a different empirical holding-time distribution."""
    assert (
        markov_renewal.choose_holding_sample(
            conditional,
            source,
            (0.1, 0.2, 0.3),
            minimum_support=2,
        )
        == expected
    )


@pytest.mark.parametrize("alpha", [0.0, 2.0])
def test_fitted_diagnostics_classify_sparse_tiers_counts_and_unobserved_rows(alpha: float) -> None:
    """Both uniform-row formulas and every sparse timing path must remain explicit fitted evidence."""
    model = two_state_model(alpha=alpha, minimum_support=2.0)

    assert model.timing_diagnostics.transition_tiers == (
        ("source", "source"),
        ("global", "global"),
    )
    assert model.timing_diagnostics.reference_transition_count == 0
    assert model.timing_diagnostics.reference_source_count == 1
    assert model.timing_diagnostics.reference_global_count == 0
    assert model.timing_diagnostics.unobserved_rows == (1,)


@pytest.mark.parametrize(
    ("conditional", "source", "global_iats", "minimum_support"),
    [
        ((math.nan,), (), (0.1,), 1),
        ((), (), (), 1),
        ((), (), (-0.1,), 1),
        ((), (), (0.1,), True),
        ((), (), (0.1,), 0),
    ],
)
def test_timing_fallback_rejects_invalid_samples_and_support(
    conditional: tuple[float, ...],
    source: tuple[float, ...],
    global_iats: tuple[float, ...],
    minimum_support: object,
) -> None:
    """Fallback must never return absent, invalid, or ambiguously supported timing data."""
    with pytest.raises(TrafficlabError):
        markov_renewal.choose_holding_sample(
            conditional,
            source,
            global_iats,
            minimum_support=minimum_support,  # type: ignore[arg-type]
        )


def test_public_transition_and_empirical_samplers_preserve_ties_and_order() -> None:
    """A cumulative tie belongs to the next interval, while numerical tails belong to the final interval."""
    tie_rng = ScriptedMarkovRng(random_values=[0.25], indices=[])
    tail_rng = ScriptedMarkovRng(random_values=[0.999999999999], indices=[])
    empirical_rng = ScriptedMarkovRng(random_values=[], indices=[1])
    assert markov_renewal.sample_transition((0.25, 0.75), tie_rng) == 1
    assert markov_renewal.sample_transition((0.25, 0.75), tail_rng) == 1
    assert markov_renewal.sample_empirical((10, 20), empirical_rng) == 20


def test_transition_sampler_compares_raw_draw_at_tie_for_tolerance_valid_nonunit_row() -> None:
    """Rescaling a transition draw by a near-one row sum would move an exact cumulative tie."""
    rng = ScriptedMarkovRng(random_values=[0.25], indices=[])

    assert markov_renewal.sample_transition((0.25, 0.7499999999995), rng) == 1


def test_transition_sampler_rejects_an_invalid_probability_row() -> None:
    """A direct transition sampler call must not bypass the fitted row invariant."""
    rng = ScriptedMarkovRng(random_values=[0.5], indices=[])

    with pytest.raises(TrafficlabError, match="probability row"):
        markov_renewal.sample_transition((), rng)
