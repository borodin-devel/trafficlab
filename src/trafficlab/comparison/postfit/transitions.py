"""Final-only reference-frozen categorical transition fidelity."""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable
from typing import NoReturn, cast

import numpy as np

from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TrafficTrace, validate_traffic_trace
from trafficlab.comparison.similarity.common import (
    JsonDiagnostics,
    SimilarityResult,
    validate_observation_window,
    validated_weights,
)

_MAXIMUM_ACTIVE_STATES = 256
_MAXIMUM_TRANSITION_CELLS = 65_536
_ROUNDING_TOLERANCE = 1e-15
type BinCategory = int | str
type State = tuple[str, BinCategory, BinCategory]


def _bounded(value: float, *, name: str) -> float:
    """Return one finite base-two JSD result within its mathematical bounds."""
    if not math.isfinite(value):
        raise TrafficlabError(
            f"invalid {name}: computation produced a nonfinite value",
            corrective_action="provide finite canonical traces and a positive smoothing pseudocount",
        )
    if 0.0 <= value <= 1.0:
        return value
    if -_ROUNDING_TOLERANCE <= value < 0.0:
        return 0.0
    if 1.0 < value <= 1.0 + _ROUNDING_TOLERANCE:
        return 1.0
    raise TrafficlabError(
        f"invalid {name}: computation produced a value outside [0, 1]",
        corrective_action="provide finite canonical traces and a positive smoothing pseudocount",
    )


def _positive_int(value: object, *, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise TrafficlabError(
            f"invalid {name}: it must be a positive integer",
            corrective_action=f"provide a positive integer {name}",
        )
    return value


def _pseudocount(value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0.0:
        raise TrafficlabError(
            "invalid transition pseudocount: it must be a finite positive float",
            corrective_action="provide a finite positive transition pseudocount",
        )
    return value


def _thresholds(values: Iterable[float], *, bin_count: int, name: str) -> tuple[float, ...]:
    """Return Type-7 extrema and interior equal-quantile thresholds in log space."""
    sample = tuple(values)
    if not sample or any(not math.isfinite(value) or value < 0.0 for value in sample):
        raise TrafficlabError(
            f"invalid {name} threshold sample",
            corrective_action="provide finite nonnegative canonical reference values",
        )
    quantiles = tuple(index / bin_count for index in range(bin_count + 1))
    return tuple(
        float(value) for value in np.quantile(np.asarray(sample, dtype=np.float64), quantiles, method="linear")
    )


def _bin(value: float, thresholds: tuple[float, ...]) -> BinCategory:
    """Assign one log value to frozen interior bins or an explicit exterior edge."""
    if value < thresholds[0]:
        return "below"
    if value > thresholds[-1]:
        return "above"
    return bisect_right(thresholds[1:-1], value)


def _vocabulary(size_bin_count: int, iat_bin_count: int) -> tuple[State, ...]:
    """Declare every smoothed state before looking at generated data."""
    size_categories: tuple[BinCategory, ...] = ("below", *range(size_bin_count), "above")
    iat_categories: tuple[BinCategory, ...] = ("initial", "below", *range(iat_bin_count), "above")
    states = tuple(
        (direction, size_category, iat_category)
        for direction in (Direction.OUTBOUND.value, Direction.INBOUND.value)
        for size_category in size_categories
        for iat_category in iat_categories
    )
    cell_count = len(states) * len(states)
    if len(states) > _MAXIMUM_ACTIVE_STATES or cell_count > _MAXIMUM_TRANSITION_CELLS:
        raise TrafficlabError(
            "invalid transition state bins: declared state or transition-cell count exceeds the cap",
            corrective_action="configure bins within the 256-state and 65536-transition-cell caps",
        )
    return states


def _states(
    trace: TrafficTrace, *, size_thresholds: tuple[float, ...], iat_thresholds: tuple[float, ...]
) -> tuple[State, ...]:
    """Encode one event sequence without changing reference-frozen categories."""
    values: list[State] = []
    previous: float | None = None
    for timestamp, direction, frame_length in zip(trace.timestamps, trace.directions, trace.frame_lengths, strict=True):
        iat_category: BinCategory = (
            "initial" if previous is None else _bin(math.log1p(float(timestamp - previous)), iat_thresholds)
        )
        values.append(
            (
                Direction.OUTBOUND.value if direction == 0 else Direction.INBOUND.value,
                _bin(math.log1p(float(frame_length)), size_thresholds),
                iat_category,
            )
        )
        previous = float(timestamp)
    return tuple(values)


def _counts(values: Iterable[State], vocabulary: tuple[State, ...]) -> tuple[int, ...]:
    count_map = Counter(values)
    return tuple(count_map[state] for state in vocabulary)


def _raise_unsafe_smoothing() -> NoReturn:
    raise TrafficlabError(
        "invalid transition pseudocount: values cannot be evaluated safely",
        corrective_action="provide a finite positive pseudocount within the supported arithmetic range",
    )


def _smoothed_pmf(counts: tuple[int, ...], *, pseudocount: float) -> tuple[float, ...]:
    try:
        total = float(sum(counts))
        numeric_counts = tuple(float(count) for count in counts)
    except (OverflowError, ValueError):
        _raise_unsafe_smoothing()
    pseudocount_mass = pseudocount * len(counts)
    denominator = total + pseudocount_mass
    if not math.isfinite(denominator) or denominator <= 0.0:
        _raise_unsafe_smoothing()
    numerators = tuple(count + pseudocount for count in numeric_counts)
    if any(not math.isfinite(numerator) or numerator <= 0.0 for numerator in numerators):
        _raise_unsafe_smoothing()
    probabilities = tuple(numerator / denominator for numerator in numerators)
    if any(not math.isfinite(probability) or probability <= 0.0 for probability in probabilities):
        _raise_unsafe_smoothing()
    return probabilities


def _jsd(reference: tuple[int, ...], generated: tuple[int, ...], *, pseudocount: float, name: str) -> float:
    """Return a smoothed base-two JSD over exactly the already declared support."""
    reference_pmf = _smoothed_pmf(reference, pseudocount=pseudocount)
    generated_pmf = _smoothed_pmf(generated, pseudocount=pseudocount)
    terms: list[float] = []
    for left, right in zip(reference_pmf, generated_pmf, strict=True):
        midpoint = (left + right) / 2.0
        terms.append(0.5 * left * math.log2(left / midpoint))
        terms.append(0.5 * right * math.log2(right / midpoint))
    return _bounded(math.fsum(terms), name=name)


def _transition_counts(states: tuple[State, ...], vocabulary: tuple[State, ...]) -> tuple[tuple[int, ...], ...]:
    indexes = {state: index for index, state in enumerate(vocabulary)}
    rows = [[0] * len(vocabulary) for _ in vocabulary]
    for source, destination in zip(states, states[1:], strict=False):
        rows[indexes[source]][indexes[destination]] += 1
    return tuple(tuple(row) for row in rows)


def _run_counts(states: tuple[State, ...], *, reference_maximum: int) -> tuple[int, ...]:
    runs: Counter[int | str] = Counter()
    run_length = 1
    for source, destination in zip(states, states[1:], strict=False):
        if destination == source:
            run_length += 1
        else:
            runs[run_length if run_length <= reference_maximum else "overflow"] += 1
            run_length = 1
    runs[run_length if run_length <= reference_maximum else "overflow"] += 1
    return tuple(runs[index] for index in (*range(1, reference_maximum + 1), "overflow"))


def _maximum_run(states: tuple[State, ...]) -> int:
    maximum = 1
    current = 1
    for source, destination in zip(states, states[1:], strict=False):
        current = current + 1 if source == destination else 1
        maximum = max(maximum, current)
    return maximum


def transition_matrix_diagnostic(
    reference: TrafficTrace,
    generated: TrafficTrace,
    W: float,
    size_bin_count: int,
    iat_bin_count: int,
    pseudocount: float,
    component_weights: tuple[float, float, float],
) -> SimilarityResult:
    """Compare occupancy, conditional transitions, and run lengths in a frozen categorical vocabulary."""
    window = validate_observation_window(W)
    size_count = _positive_int(size_bin_count, name="transition size bin count")
    iat_count = _positive_int(iat_bin_count, name="transition IAT bin count")
    alpha = _pseudocount(pseudocount)
    weights = validated_weights(
        component_weights, name="transition component weights", expected_length=3, count_name="component"
    )
    vocabulary = _vocabulary(size_count, iat_count)
    reference_trace = validate_traffic_trace(reference, minimum_events=2, trace_name="reference", window=window)
    generated_trace = validate_traffic_trace(generated, minimum_events=2, trace_name="generated", window=window)
    size_thresholds = _thresholds(
        (math.log1p(float(value)) for value in reference_trace.frame_lengths), bin_count=size_count, name="size"
    )
    iat_thresholds = _thresholds(
        (math.log1p(float(value)) for value in reference_trace.iats()), bin_count=iat_count, name="IAT"
    )
    reference_states = _states(reference_trace, size_thresholds=size_thresholds, iat_thresholds=iat_thresholds)
    generated_states = _states(generated_trace, size_thresholds=size_thresholds, iat_thresholds=iat_thresholds)
    reference_occupancy = _counts(reference_states, vocabulary)
    generated_occupancy = _counts(generated_states, vocabulary)
    occupancy_jsd = _jsd(reference_occupancy, generated_occupancy, pseudocount=alpha, name="transition occupancy JSD")
    reference_transitions = _transition_counts(reference_states, vocabulary)
    generated_transitions = _transition_counts(generated_states, vocabulary)
    rows: list[dict[str, object]] = []
    row_jsds: list[float] = []
    for state, reference_row, generated_row in zip(
        vocabulary, reference_transitions, generated_transitions, strict=True
    ):
        row_jsd = _jsd(reference_row, generated_row, pseudocount=alpha, name="transition row JSD")
        row_jsds.append(row_jsd)
        rows.append(
            {
                "source": state,
                "reference_probabilities": _smoothed_pmf(reference_row, pseudocount=alpha),
                "generated_probabilities": _smoothed_pmf(generated_row, pseudocount=alpha),
                "jsd": row_jsd,
            }
        )
    transition_jsd = _bounded(math.fsum(row_jsds) / len(row_jsds), name="transition-row JSD")
    reference_maximum_run = _maximum_run(reference_states)
    run_vocabulary: tuple[int | str, ...] = (*range(1, reference_maximum_run + 1), "overflow")
    reference_runs = _run_counts(reference_states, reference_maximum=reference_maximum_run)
    generated_runs = _run_counts(generated_states, reference_maximum=reference_maximum_run)
    run_jsd = _jsd(reference_runs, generated_runs, pseudocount=alpha, name="transition run-length JSD")
    discrepancy = _bounded(
        math.fsum((weights[0] * occupancy_jsd, weights[1] * transition_jsd, weights[2] * run_jsd)),
        name="transition fidelity discrepancy",
    )
    diagnostics: JsonDiagnostics = cast(
        JsonDiagnostics,
        {
            "observation_window_seconds": window,
            "size_bin_count": size_count,
            "iat_bin_count": iat_count,
            "log_size_thresholds": size_thresholds,
            "log_iat_thresholds": iat_thresholds,
            "pseudocount": alpha,
            "component_weights": {"occupancy": weights[0], "transition_rows": weights[1], "runs": weights[2]},
            "active_state_count": len(vocabulary),
            "transition_cell_count": len(vocabulary) * len(vocabulary),
            "vocabulary": vocabulary,
            "reference_states": reference_states,
            "generated_states": generated_states,
            "occupancy": {
                "reference_counts": reference_occupancy,
                "generated_counts": generated_occupancy,
                "reference_probabilities": _smoothed_pmf(reference_occupancy, pseudocount=alpha),
                "generated_probabilities": _smoothed_pmf(generated_occupancy, pseudocount=alpha),
                "jsd": occupancy_jsd,
            },
            "transitions": {
                "reference_counts": reference_transitions,
                "generated_counts": generated_transitions,
                "rows": tuple(rows),
                "jsd": transition_jsd,
            },
            "runs": {
                "vocabulary": run_vocabulary,
                "reference_counts": reference_runs,
                "generated_counts": generated_runs,
                "reference_probabilities": _smoothed_pmf(reference_runs, pseudocount=alpha),
                "generated_probabilities": _smoothed_pmf(generated_runs, pseudocount=alpha),
                "jsd": run_jsd,
            },
            "component_jsd": {"occupancy": occupancy_jsd, "transition_rows": transition_jsd, "runs": run_jsd},
            "discrepancy": discrepancy,
        },
    )
    return SimilarityResult(score=1.0 - discrepancy, diagnostics=diagnostics)
