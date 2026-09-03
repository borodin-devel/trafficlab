"""Independent integer/Fraction checks for final-only transition fidelity."""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Mapping
from fractions import Fraction
from math import log1p, log2
from typing import cast

import pytest

import trafficlab.comparison.postfit.transitions as transitions
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.comparison.postfit.transitions import transition_matrix_diagnostic

_smoothed_pmf = transitions._smoothed_pmf  # pyright: ignore[reportPrivateUsage]

type State = tuple[str, int | str, int | str]


def _trace(events: tuple[tuple[float, Direction, int], ...]) -> TrafficTrace:
    return TrafficTrace.from_events(TraceEvent(timestamp, direction, length) for timestamp, direction, length in events)


def _pmf[Category: Hashable](
    counts: Counter[Category], alpha: int, vocabulary: tuple[Category, ...]
) -> dict[Category, Fraction]:
    """Build one hand-derived smoothed PMF using exact rational arithmetic."""
    denominator = sum(counts.values()) + alpha * len(vocabulary)
    return {key: Fraction(counts[key] + alpha, denominator) for key in vocabulary}


def _jsd[Category: Hashable](
    reference: Counter[Category], generated: Counter[Category], alpha: int, vocabulary: tuple[Category, ...]
) -> float:
    """A Fraction PMF oracle independent of production state construction and JSD code."""
    reference_mass = _pmf(reference, alpha, vocabulary)
    generated_mass = _pmf(generated, alpha, vocabulary)
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

    assert result.diagnostics["log_size_thresholds"] == pytest.approx(
        (log1p(10.0), (log1p(20.0) + log1p(30.0)) / 2.0, log1p(40.0))
    )
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
        **{
            state: 0
            for state in vocabulary
            if state not in {("outbound", 0, "initial"), ("inbound", 0, 0), ("outbound", 1, 1), ("inbound", 1, 1)}
        },
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

    result = transition_matrix_diagnostic(reference, generated, 3.0, 1, 1, 1.0, (0.2, 0.3, 0.5))

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
    reference_occupancy: Counter[State] = Counter(
        {("outbound", 0, "initial"): 1, ("outbound", 0, 0): 2, ("inbound", 0, 0): 1}
    )
    generated_occupancy: Counter[State] = Counter(
        {("outbound", 0, "initial"): 1, ("outbound", 0, 0): 1, ("inbound", 0, 0): 2}
    )
    expected_reference_occupancy = _pmf(reference_occupancy, 1, vocabulary)
    expected_generated_occupancy = _pmf(generated_occupancy, 1, vocabulary)
    expected_occupancy_jsd = _jsd(reference_occupancy, generated_occupancy, 1, vocabulary)
    assert occupancy["reference_probabilities"] == pytest.approx(
        tuple(expected_reference_occupancy[state] for state in vocabulary)
    )
    assert occupancy["generated_probabilities"] == pytest.approx(
        tuple(expected_generated_occupancy[state] for state in vocabulary)
    )
    assert occupancy["jsd"] == pytest.approx(expected_occupancy_jsd)
    reference_rows: dict[State, Counter[State]] = {
        ("outbound", 0, "initial"): Counter({("outbound", 0, 0): 1}),
        ("outbound", 0, 0): Counter({("outbound", 0, 0): 1, ("inbound", 0, 0): 1}),
    }
    generated_rows: dict[State, Counter[State]] = {
        ("outbound", 0, "initial"): Counter({("inbound", 0, 0): 1}),
        ("outbound", 0, 0): Counter({("inbound", 0, 0): 1}),
        ("inbound", 0, 0): Counter({("outbound", 0, 0): 1}),
    }
    expected_row_jsds = tuple(
        _jsd(reference_rows.get(state, Counter[State]()), generated_rows.get(state, Counter[State]()), 1, vocabulary)
        for state in vocabulary
    )
    expected_transition_jsd = sum(expected_row_jsds) / len(expected_row_jsds)
    for source, row in zip(vocabulary, rows, strict=True):
        expected_reference_row = _pmf(reference_rows.get(source, Counter[State]()), 1, vocabulary)
        expected_generated_row = _pmf(generated_rows.get(source, Counter[State]()), 1, vocabulary)
        assert row["reference_probabilities"] == pytest.approx(
            tuple(expected_reference_row[destination] for destination in vocabulary)
        )
        assert row["generated_probabilities"] == pytest.approx(
            tuple(expected_generated_row[destination] for destination in vocabulary)
        )
    assert empty_row["reference_probabilities"] == pytest.approx(
        tuple(Fraction(1, len(vocabulary)) for _ in vocabulary)
    )
    assert tuple(row["jsd"] for row in rows) == pytest.approx(expected_row_jsds)
    assert transitions["jsd"] == pytest.approx(expected_transition_jsd)
    assert runs["reference_counts"] == (2, 1, 0)
    assert runs["generated_counts"] == (4, 0, 0)
    reference_runs: Counter[int | str] = Counter({1: 2, 2: 1})
    generated_runs: Counter[int | str] = Counter({1: 4})
    expected_runs = _jsd(reference_runs, generated_runs, 1, run_vocabulary)
    assert runs["jsd"] == pytest.approx(expected_runs)
    expected_discrepancy = 0.2 * expected_occupancy_jsd + 0.3 * expected_transition_jsd + 0.5 * expected_runs
    assert result.diagnostics["component_jsd"] == {
        "occupancy": pytest.approx(expected_occupancy_jsd),
        "transition_rows": pytest.approx(expected_transition_jsd),
        "runs": pytest.approx(expected_runs),
    }
    assert result.diagnostics["discrepancy"] == pytest.approx(expected_discrepancy)
    assert result.score == pytest.approx(1.0 - expected_discrepancy)
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


def test_transition_rejects_a_finite_pseudocount_that_overflows_its_pmf_denominator() -> None:
    """A finite but extreme pseudocount must report a domain error, never divide by zero."""
    trace = _trace(((0.0, Direction.OUTBOUND, 10), (1.0, Direction.INBOUND, 20)))

    with pytest.raises(TrafficlabError, match="pseudocount.*evaluated safely"):
        transition_matrix_diagnostic(trace, trace, 1.0, 1, 1, 1e308, (1.0, 0.0, 0.0))


def test_transition_smoothing_rejects_a_huge_integer_count_before_float_conversion() -> None:
    """An exact Python count above binary64 range must remain a stable production-domain error."""
    with pytest.raises(TrafficlabError, match="pseudocount.*evaluated safely"):
        _smoothed_pmf((10**400, 0), pseudocount=0.1)


@pytest.mark.parametrize(
    ("size_bins", "iat_bins", "pseudocount"),
    [(0, 1, 1.0), (1, 0, 1.0), (1, 1, 0.0), (1, 1, float("inf"))],
)
def test_transition_rejects_invalid_bins_and_smoothing(size_bins: int, iat_bins: int, pseudocount: float) -> None:
    """Invalid dimensions or smoothing would make declared PMFs undefined."""
    trace = _trace(((0.0, Direction.OUTBOUND, 10), (1.0, Direction.INBOUND, 20)))

    with pytest.raises(TrafficlabError, match="transition"):
        transition_matrix_diagnostic(trace, trace, 1.0, size_bins, iat_bins, pseudocount, (1.0, 0.0, 0.0))
