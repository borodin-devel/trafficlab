"""Behavioral tests for one Markov renewal owner."""

from __future__ import annotations

import math

import pytest

from tests.support.markov_renewal import (
    BOUNDS,
    DISTINCT_REFERENCE,
    FAMILY,
    two_state_model,
)
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.markov_renewal import (
    MarkovState,
)


def test_fit_builds_active_states_in_first_appearance_order_with_reference_lengths() -> None:
    """Sorting active states or lengths would alter every stored transition index and RNG sequence."""
    model = FAMILY.fit(DISTINCT_REFERENCE, (0.25, 0.75, 0.0, 1.0, 1.0), W=3.0, bounds=BOUNDS)
    assert tuple((state.direction, state.size_bin, state.frame_lengths) for state in model.states) == (
        (Direction.INBOUND, 0, (20,)),
        (Direction.OUTBOUND, 2, (80,)),
        (Direction.INBOUND, 1, (40,)),
        (Direction.OUTBOUND, 1, (60,)),
    )
    assert len({state.size_bin for state in model.states if state.direction is Direction.INBOUND}) <= 3
    assert len({state.size_bin for state in model.states if state.direction is Direction.OUTBOUND}) <= 3


def test_complete_additive_transition_estimator_and_ordered_iat_samples() -> None:
    """Dropping smoothing or misaligning samples would change both the kernel and holding times."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(1.0, Direction.INBOUND, 80),
        TraceEvent(2.0, Direction.OUTBOUND, 20),
        TraceEvent(4.0, Direction.INBOUND, 80),
        TraceEvent(5.0, Direction.INBOUND, 80),
    )
    model = FAMILY.fit(TrafficTrace.from_events(reference), (0.25, 0.75, 1.0, 2.0, 1.0), W=5.0, bounds=BOUNDS)

    assert model.transition_rows == ((0.25, 0.75), (0.5, 0.5))
    assert model.conditional_iats == ((((), (1.0, 2.0))), (((1.0,), (1.0,))))
    assert tuple(state.source_iats for state in model.states) == ((1.0, 2.0), (1.0, 1.0))
    assert model.global_iats == (1.0, 1.0, 2.0, 1.0)


def test_final_only_zero_smoothed_row_is_uniform() -> None:
    """Dividing an unobserved zero-smoothed row by zero would leave generation undefined."""
    model = two_state_model(alpha=0.0)
    assert model.transition_rows == ((0.0, 1.0), (0.5, 0.5))


def test_positive_smoothing_empty_row_uses_the_ordinary_uniform_formula() -> None:
    """Special-casing every empty row could accidentally bypass additive smoothing."""
    model = two_state_model(alpha=2.0)
    assert model.transition_rows[1] == (0.5, 0.5)
    assert model.transition_rows[0] == (0.4, 0.6)


def test_nonempty_zero_smoothed_row_equals_empirical_frequencies() -> None:
    """Applying smoothing at alpha zero would corrupt empirical transition frequencies."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(1.0, Direction.OUTBOUND, 20),
        TraceEvent(2.0, Direction.INBOUND, 80),
        TraceEvent(3.0, Direction.OUTBOUND, 20),
    )
    model = FAMILY.fit(TrafficTrace.from_events(reference), (0.25, 0.75, 0.0, 1.0, 1.0), W=3.0, bounds=BOUNDS)
    assert model.transition_rows[0] == (0.5, 0.5)
    assert model.transition_rows[1] == (1.0, 0.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"direction": "outbound"},
        {"size_bin": 3},
        {"frame_lengths": ()},
        {"frame_lengths": (13,)},
        {"source_iats": (-0.1,)},
    ],
)
def test_markov_state_rejects_values_fit_cannot_produce(kwargs: dict[str, object]) -> None:
    """Direct construction must enforce the same state invariants as fit and loading."""
    values: dict[str, object] = {
        "direction": Direction.OUTBOUND,
        "size_bin": 0,
        "frame_lengths": (20,),
        "source_iats": (),
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError)):
        MarkovState(**values)  # type: ignore[arg-type]


def test_zero_iats_remain_valid_in_every_fitted_sample() -> None:
    """Rejecting simultaneous packet observations would lose valid canonical trace data."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(0.0, Direction.INBOUND, 80),
        TraceEvent(1.0, Direction.OUTBOUND, 20),
    )
    model = FAMILY.fit(TrafficTrace.from_events(reference), (0.25, 0.75, 0.0, 1.0, 1.0), W=1.0, bounds=BOUNDS)
    assert model.global_iats == (0.0, 1.0)
    assert model.conditional_iats[0][1] == (0.0,)


def test_every_fitted_transition_row_has_k_entries_and_probability_invariants() -> None:
    """A malformed row would make cumulative transition sampling ambiguous."""
    model = FAMILY.fit(DISTINCT_REFERENCE, (0.25, 0.75, 0.5, 1.0, 1.0), W=3.0, bounds=BOUNDS)
    state_count = len(model.states)
    assert state_count >= 1
    assert len(model.transition_rows) == state_count
    assert len(model.conditional_iats) == state_count
    for row in model.transition_rows:
        assert len(row) == state_count
        assert all(math.isfinite(value) and value >= 0.0 for value in row)
        assert math.isclose(sum(row), 1.0, rel_tol=0.0, abs_tol=1e-12)
    assert all(len(row) == state_count for row in model.conditional_iats)
