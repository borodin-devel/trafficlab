"""Bounded pooled-ECDF similarities for canonical traffic traces."""

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import numpy as np

from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TrafficTrace, validate_traffic_trace
from trafficlab.comparison.similarity.common import (
    FrozenJsonValue,
    JsonDiagnostics,
    SimilarityResult,
    validate_observation_window,
    validated_numeric_sample,
    validated_weights,
)

_ROUNDING_TOLERANCE = 1e-15
type _EcdfKind = Literal["cvm", "ad"]


@dataclass(frozen=True, slots=True)
class EcdfSampleResult:
    """One bounded pooled-support ECDF discrepancy and its audit values."""

    raw_sum: float
    normalization_weight: float
    discrepancy: float
    reference_sample_count: int
    generated_sample_count: int
    reference_tie_count: int
    generated_tie_count: int


def _bounded(value: float, *, name: str) -> float:
    """Return a finite documented unit-interval value, allowing arithmetic roundoff."""
    if not math.isfinite(value):
        raise TrafficlabError(
            f"invalid {name}: computation produced a nonfinite value",
            corrective_action="provide finite nonempty numeric samples",
        )
    if 0.0 <= value <= 1.0:
        return value
    if -_ROUNDING_TOLERANCE <= value < 0.0:
        return 0.0
    if 1.0 < value <= 1.0 + _ROUNDING_TOLERANCE:
        return 1.0
    raise TrafficlabError(
        f"invalid {name}: computation produced a value outside [0, 1]",
        corrective_action="provide finite nonempty numeric samples",
    )


def _sample(values: Iterable[object], *, name: str) -> tuple[int | float, ...]:
    """Materialize one finite nonempty sample without converting exact integer support."""
    return validated_numeric_sample(
        values,
        error_name=f"{name} sample",
        corrective_action="provide a nonempty iterable of finite numeric values",
        require_nonempty=True,
    )


def _pooled_ecdf(reference: Iterable[object], generated: Iterable[object], *, kind: _EcdfKind) -> EcdfSampleResult:
    """Scan tied pooled support once for a bounded CvM or AD discrepancy."""
    reference_values = _sample(reference, name="reference")
    generated_values = _sample(generated, name="generated")
    try:
        reference_counts = Counter(reference_values)
        generated_counts = Counter(generated_values)
        support = sorted(set(reference_counts) | set(generated_counts))
    except (TypeError, ValueError) as error:
        raise TrafficlabError(
            "invalid ECDF samples: values cannot be ordered safely",
            corrective_action="provide finite numeric samples with an orderable shared support",
        ) from error

    reference_seen = 0
    generated_seen = 0
    raw_terms: list[float] = []
    weights: list[float] = []
    total_sample_count = len(reference_values) + len(generated_values)
    for value in support:
        reference_count = reference_counts.get(value, 0)
        generated_count = generated_counts.get(value, 0)
        reference_seen += reference_count
        generated_seen += generated_count
        difference = reference_seen / len(reference_values) - generated_seen / len(generated_values)
        if kind == "cvm":
            weight = (reference_count + generated_count) / total_sample_count
        else:
            pooled_cdf = (reference_seen + generated_seen) / total_sample_count
            if pooled_cdf == 1.0:
                continue
            weight = 1.0 / (pooled_cdf * (1.0 - pooled_cdf))
        weights.append(weight)
        raw_terms.append(weight * difference**2)

    raw_sum = math.fsum(raw_terms)
    normalization_weight = math.fsum(weights)
    discrepancy = _bounded(raw_sum / normalization_weight if normalization_weight else 0.0, name=f"{kind} discrepancy")
    return EcdfSampleResult(
        raw_sum=raw_sum,
        normalization_weight=normalization_weight,
        discrepancy=discrepancy,
        reference_sample_count=len(reference_values),
        generated_sample_count=len(generated_values),
        reference_tie_count=len(reference_values) - len(reference_counts),
        generated_tie_count=len(generated_values) - len(generated_counts),
    )


def bounded_cvm_sample(reference: Iterable[object], generated: Iterable[object]) -> EcdfSampleResult:
    """Return the bounded pooled-mass Cramér--von Mises sample discrepancy."""
    return _pooled_ecdf(reference, generated, kind="cvm")


def bounded_ad_sample(reference: Iterable[object], generated: Iterable[object]) -> EcdfSampleResult:
    """Return the bounded endpoint-normalized Anderson--Darling sample discrepancy."""
    return _pooled_ecdf(reference, generated, kind="ad")


def _feature_diagnostics(result: EcdfSampleResult) -> dict[str, FrozenJsonValue]:
    """Render the trace-independent audit payload for one feature sample."""
    return {
        "reference_sample_count": result.reference_sample_count,
        "generated_sample_count": result.generated_sample_count,
        "reference_tie_count": result.reference_tie_count,
        "generated_tie_count": result.generated_tie_count,
        "raw_sum": result.raw_sum,
        "normalization_weight": result.normalization_weight,
        "discrepancy": result.discrepancy,
    }


def _direction_availability(reference: np.ndarray, generated: np.ndarray) -> dict[str, FrozenJsonValue]:
    """Retain whether each trace has values in every future direction stratum."""
    return {
        direction.value: {
            "reference_available": bool(np.any(reference == index)),
            "generated_available": bool(np.any(generated == index)),
            "both_available": bool(np.any(reference == index) and np.any(generated == index)),
        }
        for index, direction in enumerate((Direction.OUTBOUND, Direction.INBOUND))
    }


def _trace_similarity(
    reference: TrafficTrace,
    generated: TrafficTrace,
    W: object,
    iat_weight: object,
    size_weight: object,
    *,
    kind: _EcdfKind,
) -> SimilarityResult:
    """Aggregate documented IAT and complete-frame samples for one ECDF method."""
    window = validate_observation_window(W)
    feature_weights = validated_weights((iat_weight, size_weight), name=f"{kind} feature weights")
    reference_trace = validate_traffic_trace(reference, minimum_events=2, trace_name="reference")
    generated_trace = validate_traffic_trace(generated, minimum_events=2, trace_name="generated")
    sample_method = bounded_cvm_sample if kind == "cvm" else bounded_ad_sample
    iat_result = sample_method(
        (float(value) for value in reference_trace.iats()), (float(value) for value in generated_trace.iats())
    )
    size_result = sample_method(
        (int(value) for value in reference_trace.frame_lengths), (int(value) for value in generated_trace.frame_lengths)
    )
    discrepancy = _bounded(
        math.fsum((feature_weights[0] * iat_result.discrepancy, feature_weights[1] * size_result.discrepancy)),
        name=f"{kind} aggregate discrepancy",
    )
    diagnostics: JsonDiagnostics = {
        "observation_window_seconds": window,
        "feature_weights": {"iat": feature_weights[0], "size": feature_weights[1]},
        "iat": _feature_diagnostics(iat_result),
        "size": _feature_diagnostics(size_result),
        "direction_strata": {
            "iat": _direction_availability(reference_trace.directions[1:], generated_trace.directions[1:]),
            "size": _direction_availability(reference_trace.directions, generated_trace.directions),
        },
        "discrepancy": discrepancy,
    }
    return SimilarityResult(score=1.0 - discrepancy, diagnostics=diagnostics)


def cramer_von_mises_similarity(
    reference: TrafficTrace, generated: TrafficTrace, W: float, iat_weight: float, size_weight: float
) -> SimilarityResult:
    """Compare IAT and frame-size ECDFs with bounded pooled-mass CvM distance."""
    return _trace_similarity(reference, generated, W, iat_weight, size_weight, kind="cvm")


def anderson_darling_similarity(
    reference: TrafficTrace, generated: TrafficTrace, W: float, iat_weight: float, size_weight: float
) -> SimilarityResult:
    """Compare IAT and frame-size ECDFs with bounded tail-weighted AD distance."""
    return _trace_similarity(reference, generated, W, iat_weight, size_weight, kind="ad")
