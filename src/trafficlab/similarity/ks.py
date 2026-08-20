"""Exact frame-size and inter-arrival-time Kolmogorov-Smirnov metrics."""

import math
from collections.abc import Iterable
from typing import Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray
from scipy import stats as scipy_stats  # pyright: ignore[reportMissingTypeStubs]

from trafficlab.errors import TrafficlabError
from trafficlab.similarity.common import (
    JsonDiagnostics,
    SimilarityResult,
    validate_observation_window,
    validated_numeric_array,
    validated_numeric_sample,
)
from trafficlab.trace import TraceEvent, TrafficTrace, validate_traffic_trace


class _KsResult(Protocol):
    """The descriptive part of SciPy's two-sample KS result."""

    @property
    def statistic(self) -> float: ...


class _Ks2Samp(Protocol):
    """Typed boundary around SciPy's untyped two-sample KS callable."""

    def __call__(self, left: object, right: object) -> _KsResult: ...


_ks_2samp = cast(_Ks2Samp, cast(Any, scipy_stats).ks_2samp)


def _ks_statistic(left: Iterable[object], right: Iterable[object]) -> float:
    """Return SciPy's descriptive two-sample KS statistic after local validation."""
    left_raw: object = left
    right_raw: object = right
    if isinstance(left_raw, np.ndarray) and isinstance(right_raw, np.ndarray):
        left_values = validated_numeric_array(
            cast(NDArray[np.generic], left_raw),
            error_name="left sample",
            corrective_action="provide a nonempty iterable of finite numeric values",
            require_nonempty=True,
            as_float64=False,
        )
        right_values = validated_numeric_array(
            cast(NDArray[np.generic], right_raw),
            error_name="right sample",
            corrective_action="provide a nonempty iterable of finite numeric values",
            require_nonempty=True,
            as_float64=False,
        )
        scipy_left: object = left_values
        scipy_right: object = right_values
    else:
        left_values_tuple = validated_numeric_sample(
            left,
            error_name="left sample",
            corrective_action="provide a nonempty iterable of finite numeric values",
            require_nonempty=True,
        )
        right_values_tuple = validated_numeric_sample(
            right,
            error_name="right sample",
            corrective_action="provide a nonempty iterable of finite numeric values",
            require_nonempty=True,
        )
        ordered_values = sorted((*left_values_tuple, *right_values_tuple))
        ranks: dict[int | float, int] = {}
        for value in ordered_values:
            if value not in ranks:
                ranks[value] = len(ranks)
        scipy_left = tuple(ranks[value] for value in left_values_tuple)
        scipy_right = tuple(ranks[value] for value in right_values_tuple)
    try:
        statistic = float(_ks_2samp(scipy_left, scipy_right).statistic)
    except (TypeError, ValueError) as error:
        raise TrafficlabError(
            "invalid KS sample: values cannot be evaluated safely",
            corrective_action="provide nonempty finite numeric samples",
        ) from error
    if not math.isfinite(statistic) or not 0.0 <= statistic <= 1.0:
        raise TrafficlabError(
            "invalid KS statistic: computation produced a value outside [0, 1]",
            corrective_action="provide nonempty finite numeric samples",
        )
    return statistic


def _frame_lengths(events: Iterable[TraceEvent] | TrafficTrace, *, trace_name: str) -> NDArray[np.uint32]:
    """Validate one canonical trace and return its strictly positive frame lengths."""
    trace = validate_traffic_trace(events, minimum_events=1, trace_name=trace_name)
    return trace.frame_lengths


def frame_size_ks(
    reference: Iterable[TraceEvent] | TrafficTrace,
    generated: Iterable[TraceEvent] | TrafficTrace,
    W: object,
) -> SimilarityResult:
    """Compare complete frame-size samples with the exact two-sample KS distance."""
    window = validate_observation_window(W)
    reference_lengths = _frame_lengths(reference, trace_name="reference")
    generated_lengths = _frame_lengths(generated, trace_name="generated")
    distance = _ks_statistic(reference_lengths, generated_lengths)
    diagnostics: JsonDiagnostics = {
        "observation_window_seconds": window,
        "distance": distance,
        "reference_count": len(reference_lengths),
        "generated_count": len(generated_lengths),
        "reference_minimum_length": int(np.min(reference_lengths)),
        "reference_maximum_length": int(np.max(reference_lengths)),
        "generated_minimum_length": int(np.min(generated_lengths)),
        "generated_maximum_length": int(np.max(generated_lengths)),
    }
    return SimilarityResult(score=1.0 - distance, diagnostics=diagnostics)


def _inter_arrival_times(events: Iterable[TraceEvent] | TrafficTrace, *, trace_name: str) -> NDArray[np.float64]:
    """Validate a canonical trace and derive its complete IAT sample, retaining zeros."""
    trace = validate_traffic_trace(events, minimum_events=2, trace_name=trace_name)
    return trace.iats()


def _nearest_rank(values: NDArray[np.float64], quantile: float) -> float:
    """Return the documented one-based ceil(q*n) order statistic from one sample."""
    return float(np.sort(values)[math.ceil(quantile * len(values)) - 1])


def _median(values: NDArray[np.float64]) -> float:
    """Return the conventional middle value or mean of two middle values."""
    ordered = np.sort(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return float((ordered[middle - 1] + ordered[middle]) / 2.0)


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
    reference: Iterable[TraceEvent] | TrafficTrace,
    generated: Iterable[TraceEvent] | TrafficTrace,
    W: object,
    diagnostic_quantile: object,
) -> SimilarityResult:
    """Compare IAT samples with exact KS distance and explicit descriptive diagnostics."""
    window = validate_observation_window(W)
    quantile = _validate_diagnostic_quantile(diagnostic_quantile)
    reference_iats = _inter_arrival_times(reference, trace_name="reference")
    generated_iats = _inter_arrival_times(generated, trace_name="generated")
    distance = _ks_statistic(reference_iats, generated_iats)
    diagnostics: JsonDiagnostics = {
        "observation_window_seconds": window,
        "distance": distance,
        "diagnostic_quantile": quantile,
        "reference_iat_count": len(reference_iats),
        "generated_iat_count": len(generated_iats),
        "reference_zero_iat_count": int(np.count_nonzero(reference_iats == 0.0)),
        "generated_zero_iat_count": int(np.count_nonzero(generated_iats == 0.0)),
        "reference_median_iat_seconds": _median(reference_iats),
        "generated_median_iat_seconds": _median(generated_iats),
        "reference_quantile_iat_seconds": _nearest_rank(reference_iats, quantile),
        "generated_quantile_iat_seconds": _nearest_rank(generated_iats, quantile),
    }
    return SimilarityResult(score=1.0 - distance, diagnostics=diagnostics)
