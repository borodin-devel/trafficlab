"""Independent integer/Fraction checks for final-only transition fidelity."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from fractions import Fraction
from math import log1p, log2
from typing import cast

import pytest

from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.comparison.postfit.transitions import transition_matrix_diagnostic

type State = tuple[str, int | str, int | str]


def _trace(events: tuple[tuple[float, Direction, int], ...]) -> TrafficTrace:
    return TrafficTrace.from_events(TraceEvent(timestamp, direction, length) for timestamp, direction, length in events)


def _jsd(reference: Counter[object], generated: Counter[object], alpha: int, vocabulary: tuple[object, ...]) -> float:
    """A Fraction PMF oracle independent of production state construction and JSD code."""
    reference_mass = {key: Fraction(reference[key] + alpha, sum(reference.values()) + alpha * len(vocabulary)) for key in vocabulary}
    generated_mass = {key: Fraction(generated[key] + alpha, sum(generated.values()) + alpha * len(vocabulary)) for key in vocabulary}
    total = 0.0
    for key in vocabulary:
        p = reference_mass[key]
        q = generated_mass[key]
        midpoint = (p + q) / 2
        total += 0.5 * float(p) * log2(float(p / midpoint)) + 0.5 * float(q) * log2(float(q / midpoint))
    return total


def test_reference_type7_thresholds_and_directional_states_are_frozen_for_generated_edges() -> None:
    """Recomputing thresholds from generated values or merging directions changes these state counts."""
    reference = _trace(
        (
            (0.0, Direction.OUTBOUND, 10),
            (1.0, Direction.INBOUND, 20),
            (3.0, Direction.OUTBOUND, 30),
            (6.0, Direction.INBOUND, 40),
        )
    )
    generated = _trace(
        (
            (0.0, Direction.INBOUND, 1),
            (4.0, Direction.INBOUND, 100),
            (9.0, Direction.OUTBOUND, 200),
            (10.0, Direction.OUTBOUND, 300),
        )
    )

    result = transition_matrix_diagnostic(reference, generated, 10.0, 2, 2, 1.0, (1.0, 0.0, 0.0))

    assert result.diagnostics["log_size_thresholds"] == pytest.approx((log1p(10.0), (log1p(20.0) + log1p(30.0)) / 2.0, log1p(40.0)))
    assert result.diagnostics["log_iat_thresholds"] == pytest.approx((log1p(1.0), log1p(2.0), log1p(3.0)))
    assert result.diagnostics["reference_states"] == (
        ("outbound", 0, "initial"),
        ("inbound", 0, 0),
        ("outbound", 1, 1),
        ("inbound", 1, 1),
    )
    assert result.diagnostics["generated_states"] == (
        ("inbound", "below", "initial"),
        ("inbound", "above", "above"),
        ("outbound", "above", "above"),
        ("outbound", "above", 0),
    )
    vocabulary = cast(tuple[State, ...], result.diagnostics["vocabulary"])
    occupancy = cast(Mapping[str, object], result.diagnostics["occupancy"])
    reference_counts = dict(zip(vocabulary, cast(tuple[int, ...], occupancy["reference_counts"]), strict=True))
    assert reference_counts == {
        ("outbound", 0, "initial"): 1,
        ("inbound", 0, 0): 1,
        ("outbound", 1, 1): 1,
        ("inbound", 1, 1): 1,
        **{state: 0 for state in vocabulary if state not in {("outbound", 0, "initial"), ("inbound", 0, 0), ("outbound", 1, 1), ("inbound", 1, 1)}},
    }
    assert result.diagnostics["active_state_count"] == 40


def test_hand_counted_occupancy_rows_empty_rows_and_run_pmf_use_declared_smoothing() -> None:
    """Changing smoothing support, transition row normalization, or run segmentation changes every oracle."""
    reference = _trace(
        (
            (0.0, Direction.OUTBOUND, 10),
            (1.0, Direction.OUTBOUND, 10),
            (2.0, Direction.OUTBOUND, 10),
            (3.0, Direction.INBOUND, 20),
        )
    )
    generated = _trace(
        (
            (0.0, Direction.OUTBOUND, 10),
            (1.0, Direction.INBOUND, 20),
            (2.0, Direction.OUTBOUND, 10),
            (3.0, Direction.INBOUND, 20),
        )
    )

    result = transition_matrix_diagnostic(reference, generated, 3.0, 1, 1, 1.0, (0.0, 0.0, 1.0))

    occupancy = cast(Mapping[str, object], result.diagnostics["occupancy"])
    transitions = cast(Mapping[str, object], result.diagnostics["transitions"])
    runs = cast(Mapping[str, object], result.diagnostics["runs"])
    vocabulary = cast(tuple[State, ...], result.diagnostics["vocabulary"])
    run_vocabulary = cast(tuple[int | str, ...], runs["vocabulary"])
    state_index = {state: index for index, state in enumerate(vocabulary)}
    reference_counts = cast(tuple[int, ...], occupancy["reference_counts"])
    generated_counts = cast(tuple[int, ...], occupancy["generated_counts"])
    reference_transitions = cast(tuple[tuple[int, ...], ...], transitions["reference_counts"])
    generated_transitions = cast(tuple[tuple[int, ...], ...], transitions["generated_counts"])
    rows = cast(tuple[Mapping[str, object], ...], transitions["rows"])
    assert reference_counts[state_index[("outbound", 0, "initial")]] == 1
    assert reference_counts[state_index[("outbound", 0, 0)]] == 2
    assert reference_counts[state_index[("inbound", 0, 0)]] == 1
    assert generated_counts[state_index[("outbound", 0, "initial")]] == 1
    assert generated_counts[state_index[("outbound", 0, 0)]] == 1
    assert generated_counts[state_index[("inbound", 0, 0)]] == 2
    assert reference_transitions[state_index[("outbound", 0, 0)]][state_index[("outbound", 0, 0)]] == 1
    assert reference_transitions[state_index[("outbound", 0, 0)]][state_index[("inbound", 0, 0)]] == 1
    assert generated_transitions[state_index[("outbound", 0, 0)]][state_index[("inbound", 0, 0)]] == 1
    empty_row = rows[state_index[("inbound", "below", "below")]]
    assert empty_row["reference_probabilities"] == pytest.approx((Fraction(1, len(vocabulary)),) * len(vocabulary))
    assert runs["reference_counts"] == (2, 1, 0)
    assert runs["generated_counts"] == (4, 0, 0)
    expected_runs = _jsd(Counter({1: 2, 2: 1}), Counter({1: 4}), 1, run_vocabulary)
    assert runs["jsd"] == pytest.approx(expected_runs)
    assert result.score == pytest.approx(1.0 - expected_runs)
    assert len(vocabulary) == 24


def test_identical_traces_score_one() -> None:
    """Any positive component divergence would make equal final traces non-identical."""
    trace = _trace(
        (
            (0.0, Direction.OUTBOUND, 10),
            (0.0, Direction.OUTBOUND, 20),
            (2.0, Direction.INBOUND, 30),
            (4.0, Direction.OUTBOUND, 40),
        )
    )

    result = transition_matrix_diagnostic(trace, trace, 4.0, 2, 2, 0.5, (0.2, 0.3, 0.5))

    assert result.score == 1.0
    assert result.diagnostics["component_jsd"] == {"occupancy": 0.0, "transition_rows": 0.0, "runs": 0.0}


def test_transition_rejects_a_declared_vocabulary_outside_the_fixed_state_cap() -> None:
    """Large bin products must fail before a quadratic transition matrix is allocated."""
    trace = _trace(((0.0, Direction.OUTBOUND, 10), (1.0, Direction.INBOUND, 20)))

    with pytest.raises(TrafficlabError, match="state or transition-cell count exceeds the cap"):
        transition_matrix_diagnostic(trace, trace, 1.0, 10, 10, 1.0, (1.0, 0.0, 0.0))


@pytest.mark.parametrize(
    ("size_bins", "iat_bins", "pseudocount"),
    [(0, 1, 1.0), (1, 0, 1.0), (1, 1, 0.0), (1, 1, float("inf"))],
)
def test_transition_rejects_invalid_bins_and_smoothing(
    size_bins: int, iat_bins: int, pseudocount: float
) -> None:
    """Invalid dimensions or smoothing would make declared PMFs undefined."""
    trace = _trace(((0.0, Direction.OUTBOUND, 10), (1.0, Direction.INBOUND, 20)))

    with pytest.raises(TrafficlabError, match="transition"):
        transition_matrix_diagnostic(trace, trace, 1.0, size_bins, iat_bins, pseudocount, (1.0, 0.0, 0.0))
