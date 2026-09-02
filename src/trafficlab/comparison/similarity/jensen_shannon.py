"""Exact Jensen--Shannon similarities for canonical traffic traces."""

import math
from bisect import bisect_right
from collections import Counter
from collections.abc import Hashable, Mapping
from typing import cast

from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TrafficTrace, validate_traffic_trace
from trafficlab.comparison.similarity.common import (
    FrozenJsonValue,
    JsonDiagnostics,
    SimilarityResult,
    validate_observation_window,
    validated_weights,
)

_ROUNDING_TOLERANCE = 1e-15


def _positive_int(value: object, *, name: str) -> int:
    """Validate one strictly positive integer setting without accepting booleans."""
    if type(value) is not int or value <= 0:
        raise TrafficlabError(
            f"invalid {name}: it must be a positive integer",
            corrective_action=f"provide a positive integer {name}",
        )
    return value


def _bounded(value: float, *, name: str) -> float:
    """Accept only documented JSD bounds, clamping harmless arithmetic roundoff."""
    if not math.isfinite(value):
        raise TrafficlabError(
            f"invalid {name}: computation produced a nonfinite value",
            corrective_action="provide finite canonical traces",
        )
    if 0.0 <= value <= 1.0:
        return value
    if -_ROUNDING_TOLERANCE <= value < 0.0:
        return 0.0
    if 1.0 < value <= 1.0 + _ROUNDING_TOLERANCE:
        return 1.0
    raise TrafficlabError(
        f"invalid {name}: computation produced a value outside [0, 1]",
        corrective_action="provide finite canonical traces",
    )


def _jsd[Category: Hashable](reference: Mapping[Category, int], generated: Mapping[Category, int], *, name: str) -> float:
    """Return base-2 JSD over the union of two exact integer count maps."""
    reference_total = sum(reference.values())
    generated_total = sum(generated.values())
    if reference_total <= 0 or generated_total <= 0:
        raise TrafficlabError(
            f"invalid {name}: at least one observation is required in each trace",
            corrective_action="provide traces with at least two canonical events",
        )
    terms: list[float] = []
    for category in set(reference) | set(generated):
        p = reference.get(category, 0) / reference_total
        q = generated.get(category, 0) / generated_total
        midpoint = (p + q) / 2.0
        if p > 0.0:
            terms.append(0.5 * p * math.log2(p / midpoint))
        if q > 0.0:
            terms.append(0.5 * q * math.log2(q / midpoint))
    return _bounded(math.fsum(terms), name=name)


def _mark_counts(trace: TrafficTrace) -> Counter[tuple[int, int]]:
    """Count exact direction/frame-length marks without an ordinal direction conversion."""
    return Counter((int(direction), int(length)) for direction, length in zip(trace.directions, trace.frame_lengths, strict=True))


def _iat_bin_edges(window: float, bin_count: int) -> tuple[float, ...]:
    """Freeze equal-width log1p-IAT bin edges from the reference window only."""
    upper = math.log1p(window)
    return tuple(upper * index / bin_count for index in range(bin_count + 1))


def _iat_bin(value: float, edges: tuple[float, ...]) -> int:
    """Assign one log1p-IAT to left-closed bins, with the final endpoint included."""
    transformed = math.log1p(value)
    index = bisect_right(edges, transformed) - 1
    return min(max(index, 0), len(edges) - 2)


def _iat_counts(trace: TrafficTrace, edges: tuple[float, ...]) -> Counter[tuple[int, int]]:
    """Count destination-direction shared-bin IAT categories."""
    return Counter(
        (int(direction), _iat_bin(float(iat), edges))
        for direction, iat in zip(trace.directions[1:], trace.iats(), strict=True)
    )


def _direction_name(code: int) -> str:
    """Render canonical direction codes in JSON diagnostics."""
    return Direction.OUTBOUND.value if code == 0 else Direction.INBOUND.value


def _mark_diagnostics(
    reference: Counter[tuple[int, int]], generated: Counter[tuple[int, int]]
) -> tuple[dict[str, FrozenJsonValue], float]:
    """Render complete exact-mark audit data in stable categorical order."""
    jsd = _jsd(reference, generated, name="mark JSD")
    diagnostics: dict[str, FrozenJsonValue] = {
        "reference_count": sum(reference.values()),
        "generated_count": sum(generated.values()),
        "categories": tuple(
            cast(
                FrozenJsonValue,
                {
                    "direction": _direction_name(direction),
                    "frame_length": length,
                    "reference_count": reference.get((direction, length), 0),
                    "generated_count": generated.get((direction, length), 0),
                },
            )
            for direction, length in sorted(set(reference) | set(generated))
        ),
        "jsd": jsd,
    }
    return diagnostics, jsd


def _iat_diagnostics(
    reference: Counter[tuple[int, int]], generated: Counter[tuple[int, int]], edges: tuple[float, ...]
) -> tuple[dict[str, FrozenJsonValue], float]:
    """Render complete shared-bin IAT audit data in stable categorical order."""
    jsd = _jsd(reference, generated, name="IAT JSD")
    diagnostics: dict[str, FrozenJsonValue] = {
        "reference_count": sum(reference.values()),
        "generated_count": sum(generated.values()),
        "bin_edges": edges,
        "categories": tuple(
            cast(
                FrozenJsonValue,
                {
                    "direction": _direction_name(direction),
                    "bin_index": bin_index,
                    "reference_count": reference.get((direction, bin_index), 0),
                    "generated_count": generated.get((direction, bin_index), 0),
                },
            )
            for direction, bin_index in sorted(set(reference) | set(generated))
        ),
        "jsd": jsd,
    }
    return diagnostics, jsd


def jensen_shannon_similarity(
    reference: TrafficTrace,
    generated: TrafficTrace,
    W: float,
    iat_bin_count: int,
    iat_weight: float,
    mark_weight: float,
) -> SimilarityResult:
    """Compare exact marks and reference-window shared IAT PMFs with base-2 JSD."""
    window = validate_observation_window(W)
    bin_count = _positive_int(iat_bin_count, name="Jensen--Shannon IAT bin count")
    weights = validated_weights((iat_weight, mark_weight), name="Jensen--Shannon feature weights")
    reference_trace = validate_traffic_trace(reference, minimum_events=2, trace_name="reference", window=window)
    generated_trace = validate_traffic_trace(generated, minimum_events=2, trace_name="generated", window=window)
    edges = _iat_bin_edges(window, bin_count)
    mark, mark_jsd = _mark_diagnostics(_mark_counts(reference_trace), _mark_counts(generated_trace))
    iat, iat_jsd = _iat_diagnostics(_iat_counts(reference_trace, edges), _iat_counts(generated_trace, edges), edges)
    discrepancy = _bounded(
        math.fsum((weights[0] * iat_jsd, weights[1] * mark_jsd)),
        name="Jensen--Shannon aggregate discrepancy",
    )
    diagnostics: JsonDiagnostics = {
        "observation_window_seconds": window,
        "feature_weights": {"iat": weights[0], "mark": weights[1]},
        "iat": iat,
        "mark": mark,
        "discrepancy": discrepancy,
    }
    return SimilarityResult(score=1.0 - discrepancy, diagnostics=diagnostics)
