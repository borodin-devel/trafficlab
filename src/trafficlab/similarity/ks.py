"""Exact frame-size and inter-arrival-time Kolmogorov-Smirnov metrics."""

import math
from collections.abc import Iterable

from trafficlab.errors import TrafficlabError
from trafficlab.similarity.common import JsonDiagnostics, SimilarityResult, validate_observation_window
from trafficlab.trace import Direction, TraceEvent


def _validated_numeric_sample(values: Iterable[object], *, sample_name: str) -> tuple[int | float, ...]:
    """Materialize one nonempty finite numeric sample for an ECDF scan."""
    try:
        sample = tuple(values)
    except TypeError as error:
        raise TrafficlabError(
            f"invalid {sample_name} sample: values must be iterable",
            corrective_action="provide a nonempty iterable of finite numeric values",
        ) from error
    if not sample:
        raise TrafficlabError(
            f"invalid {sample_name} sample: at least one value is required",
            corrective_action="provide a nonempty iterable of finite numeric values",
        )

    numeric: list[int | float] = []
    for value in sample:
        if type(value) is int:
            numeric.append(value)
        elif type(value) is float and math.isfinite(value):
            numeric.append(value)
        else:
            raise TrafficlabError(
                f"invalid {sample_name} sample: values must be finite numbers",
                corrective_action="provide a nonempty iterable of finite numeric values",
            )
    return tuple(numeric)


def exact_ecdf_distance(left: Iterable[object], right: Iterable[object]) -> float:
    """Return the exact ECDF sup distance using one merged scan that consumes ties."""
    left_values = sorted(_validated_numeric_sample(left, sample_name="left"))
    right_values = sorted(_validated_numeric_sample(right, sample_name="right"))
    left_count = len(left_values)
    right_count = len(right_values)
    left_index = 0
    right_index = 0
    distance = 0.0

    while left_index < left_count or right_index < right_count:
        if right_index == right_count or (
            left_index < left_count and left_values[left_index] < right_values[right_index]
        ):
            value = left_values[left_index]
        elif left_index == left_count or right_values[right_index] < left_values[left_index]:
            value = right_values[right_index]
        else:
            value = left_values[left_index]

        while left_index < left_count and left_values[left_index] == value:
            left_index += 1
        while right_index < right_count and right_values[right_index] == value:
            right_index += 1
        distance = max(distance, abs(left_index / left_count - right_index / right_count))

    return distance


def _validated_trace(events: Iterable[TraceEvent], *, minimum_events: int, trace_name: str) -> tuple[TraceEvent, ...]:
    """Validate metric inputs as complete finite nondecreasing canonical traces."""
    corrective_action = f"provide finite nondecreasing canonical {trace_name} events"
    try:
        trace = tuple(events)
    except TypeError as error:
        raise TrafficlabError(
            f"invalid {trace_name} trace: events must be an iterable of canonical events",
            corrective_action=corrective_action,
        ) from error
    if len(trace) < minimum_events:
        minimum_label = "one" if minimum_events == 1 else "two"
        raise TrafficlabError(
            f"invalid {trace_name} trace: at least {minimum_label} events are required",
            corrective_action=corrective_action,
        )

    previous_timestamp: float | None = None
    for event in trace:
        if type(event) is not TraceEvent:
            raise TrafficlabError(
                f"invalid {trace_name} trace: every event must be a TraceEvent",
                corrective_action=corrective_action,
            )
        try:
            timestamp = event.timestamp
            direction = event.direction
            frame_length = event.frame_length
        except AttributeError as error:
            raise TrafficlabError(
                f"invalid {trace_name} trace: event data is incomplete",
                corrective_action=corrective_action,
            ) from error
        if (
            type(timestamp) is not float
            or not math.isfinite(timestamp)
            or timestamp < 0.0
            or type(direction) is not Direction
            or type(frame_length) is not int
            or frame_length <= 0
        ):
            raise TrafficlabError(
                f"invalid {trace_name} trace: events must have finite timestamps, directions, and positive lengths",
                corrective_action=corrective_action,
            )
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise TrafficlabError(
                f"invalid {trace_name} trace: timestamps must be nondecreasing",
                corrective_action=corrective_action,
            )
        previous_timestamp = timestamp
    return trace


def _frame_lengths(events: Iterable[TraceEvent], *, trace_name: str) -> tuple[int, ...]:
    """Validate one canonical trace and return its strictly positive frame lengths."""
    trace = _validated_trace(events, minimum_events=1, trace_name=trace_name)
    return tuple(event.frame_length for event in trace)


def frame_size_ks(reference: Iterable[TraceEvent], generated: Iterable[TraceEvent], W: object) -> SimilarityResult:
    """Compare complete frame-size samples with the exact two-sample KS distance."""
    window = validate_observation_window(W)
    reference_lengths = _frame_lengths(reference, trace_name="reference")
    generated_lengths = _frame_lengths(generated, trace_name="generated")
    distance = exact_ecdf_distance(reference_lengths, generated_lengths)
    diagnostics: JsonDiagnostics = {
        "observation_window_seconds": window,
        "distance": distance,
        "reference_count": len(reference_lengths),
        "generated_count": len(generated_lengths),
        "reference_minimum_length": min(reference_lengths),
        "reference_maximum_length": max(reference_lengths),
        "generated_minimum_length": min(generated_lengths),
        "generated_maximum_length": max(generated_lengths),
    }
    return SimilarityResult(score=1.0 - distance, diagnostics=diagnostics)


def _inter_arrival_times(events: Iterable[TraceEvent], *, trace_name: str) -> tuple[float, ...]:
    """Validate a canonical trace and derive its complete IAT sample, retaining zeros."""
    trace = _validated_trace(events, minimum_events=2, trace_name=trace_name)
    return tuple(current.timestamp - previous.timestamp for previous, current in zip(trace, trace[1:], strict=False))


def _nearest_rank(values: tuple[float, ...], quantile: float) -> float:
    """Return the documented one-based ceil(q*n) order statistic from one sample."""
    return sorted(values)[math.ceil(quantile * len(values)) - 1]


def _median(values: tuple[float, ...]) -> float:
    """Return the conventional middle value or mean of two middle values."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _validate_diagnostic_quantile(diagnostic_quantile: object) -> float:
    """Validate the strict open-interval diagnostic quantile."""
    if (
        type(diagnostic_quantile) is not float
        or not math.isfinite(diagnostic_quantile)
        or not 0.0 < diagnostic_quantile < 1.0
    ):
        raise TrafficlabError(
            "invalid diagnostic quantile: it must be a finite float strictly between zero and one",
            corrective_action="provide a diagnostic quantile q with 0 < q < 1",
        )
    return diagnostic_quantile


def iat_ks(
    reference: Iterable[TraceEvent], generated: Iterable[TraceEvent], W: object, diagnostic_quantile: object
) -> SimilarityResult:
    """Compare IAT samples with exact KS distance and explicit descriptive diagnostics."""
    window = validate_observation_window(W)
    quantile = _validate_diagnostic_quantile(diagnostic_quantile)
    reference_iats = _inter_arrival_times(reference, trace_name="reference")
    generated_iats = _inter_arrival_times(generated, trace_name="generated")
    distance = exact_ecdf_distance(reference_iats, generated_iats)
    diagnostics: JsonDiagnostics = {
        "observation_window_seconds": window,
        "distance": distance,
        "diagnostic_quantile": quantile,
        "reference_iat_count": len(reference_iats),
        "generated_iat_count": len(generated_iats),
        "reference_zero_iat_count": sum(iat == 0.0 for iat in reference_iats),
        "generated_zero_iat_count": sum(iat == 0.0 for iat in generated_iats),
        "reference_median_iat_seconds": _median(reference_iats),
        "generated_median_iat_seconds": _median(generated_iats),
        "reference_quantile_iat_seconds": _nearest_rank(reference_iats, quantile),
        "generated_quantile_iat_seconds": _nearest_rank(generated_iats, quantile),
    }
    return SimilarityResult(score=1.0 - distance, diagnostics=diagnostics)
