"""Fitted packet-train state tests with literal hand-derived expectations."""

from __future__ import annotations

from collections import Counter
from dataclasses import fields

import pytest

from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.markov_packet_train.model import fit_trace


def _three_train_trace() -> TrafficTrace:
    # Train lengths 3, 4, and 6.  Ten within gaps are 1 and two separating
    # gaps are 10, so Type-7 q90 is 9.1 and the capped states for cap=4 are
    # 3, 4, 4.
    times = (0.0, 1.0, 2.0, 12.0, 13.0, 14.0, 15.0, 25.0, 26.0, 27.0, 28.0, 29.0, 30.0)
    marks = (
        (Direction.OUTBOUND, 60),
        (Direction.INBOUND, 70),
        (Direction.OUTBOUND, 80),
        (Direction.INBOUND, 90),
        (Direction.OUTBOUND, 100),
        (Direction.OUTBOUND, 100),
        (Direction.INBOUND, 110),
        (Direction.OUTBOUND, 60),
        (Direction.OUTBOUND, 100),
        (Direction.INBOUND, 70),
        (Direction.OUTBOUND, 100),
        (Direction.OUTBOUND, 100),
        (Direction.INBOUND, 120),
    )
    return TrafficTrace.from_events(
        tuple(
            TraceEvent(timestamp, direction, length)
            for timestamp, (direction, length) in zip(times, marks, strict=True)
        )
    )


def _mark_counts(entries: object) -> Counter[tuple[Direction, int]]:
    return Counter({(entry.direction, entry.frame_length): entry.count for entry in entries})  # type: ignore[attr-defined]


def test_fit_builds_capped_states_actual_lengths_transitions_and_position_pools() -> None:
    """Using capped lengths as emitted lengths or whole trains would fail these independent count identities."""
    model = fit_trace(_three_train_trace(), length_cap=4)

    assert model.gap_quantile == 0.9
    assert model.gap_threshold == pytest.approx(9.1)
    assert model.inside_train_endpoint == "less_than_or_equal"
    assert model.length_cap == 4
    assert tuple(state.length_state for state in model.states) == (3, 4)
    assert model.initial_probabilities == pytest.approx((1.0 / 3.0, 2.0 / 3.0))
    assert model.transition_rows[0] == pytest.approx((1.0 / 3.0, 2.0 / 3.0))
    assert model.transition_rows[1] == pytest.approx((1.0 / 3.0, 2.0 / 3.0))

    state3, state4 = model.states
    assert state3.actual_lengths == (3,)
    assert state4.actual_lengths == (4, 6)
    assert state3.within_gaps.interior == (1.0,)
    assert state3.within_gaps.last == (1.0,)
    assert state4.within_gaps.interior == (1.0,) * 6
    assert state4.within_gaps.last == (1.0, 1.0)
    assert state3.marks.interior is not None
    assert state3.marks.last is not None
    assert state4.marks.interior is not None
    assert state4.marks.last is not None
    assert _mark_counts(state3.marks.first.entries) == Counter({(Direction.OUTBOUND, 60): 1})
    assert _mark_counts(state3.marks.interior.entries) == Counter({(Direction.INBOUND, 70): 1})
    assert _mark_counts(state3.marks.last.entries) == Counter({(Direction.OUTBOUND, 80): 1})
    assert sum(entry.count for entry in state4.marks.first.entries) == 2
    assert sum(entry.count for entry in state4.marks.interior.entries) == 6
    assert sum(entry.count for entry in state4.marks.last.entries) == 2

    assert model.conditional_inter_train_gaps == (((), (10.0,)), ((), (10.0,)))
    assert state3.source_inter_train_gaps == (10.0,)
    assert state4.source_inter_train_gaps == (10.0,)
    assert model.global_inter_train_gaps == (10.0, 10.0)


def test_fitted_model_has_no_whole_trace_or_train_template_field() -> None:
    """A template field could reproduce the source subsequence instead of individual packet reservoirs."""
    model = fit_trace(_three_train_trace(), length_cap=4)
    forbidden_fragments = ("template", "trace", "train_samples", "packet_sequence")

    assert all(not any(fragment in field.name for fragment in forbidden_fragments) for field in fields(type(model)))
    assert all(
        not any(fragment in field.name for fragment in forbidden_fragments) for field in fields(type(model.states[0]))
    )


def test_capped_state_keeps_actual_lengths_above_cap() -> None:
    """Replacing the actual-length reservoir with the cap would truncate long generated trains."""
    model = fit_trace(_three_train_trace(), length_cap=3)

    assert tuple(state.length_state for state in model.states) == (3,)
    assert model.states[0].actual_lengths == (3, 4, 6)
    assert model.transition_rows == ((1.0,),)


def test_fit_requires_an_observed_inter_train_gap_for_complete_window_simulation() -> None:
    """Without one boundary gap the model cannot prove that a next train starts after W."""
    trace = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(1.0, Direction.INBOUND, 70),
        )
    )

    with pytest.raises(ValueError, match="global inter-train gaps"):
        fit_trace(trace, length_cap=3)
