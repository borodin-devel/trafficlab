"""Final-only direction-aware Fano and Allan dispersion diagnostics."""

from __future__ import annotations

import math
import sys
from collections.abc import Iterable
from typing import cast

from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import TrafficTrace, validate_traffic_trace
from trafficlab.comparison.similarity.common import (
    JsonDiagnostics,
    SimilarityResult,
    validate_observation_window,
    validated_weights,
)
from trafficlab.comparison.similarity.multiscale import binned_direction_features, snap_near_integer

_MAXIMUM_DIRECTION_WINDOW_CELLS = 65_536
_ROUNDING_TOLERANCE = 3e-12


def _bounded(value: float, *, name: str) -> float:
    """Return one finite unit-interval result, clamping only arithmetic dust."""
    if not math.isfinite(value):
        raise TrafficlabError(
            f"invalid {name}: computation produced a nonfinite value",
            corrective_action="provide finite canonical traces and normalized diagnostic weights",
        )
    if 0.0 <= value <= 1.0:
        return value
    if -_ROUNDING_TOLERANCE <= value < 0.0:
        return 0.0
    if 1.0 < value <= 1.0 + _ROUNDING_TOLERANCE:
        return 1.0
    raise TrafficlabError(
        f"invalid {name}: computation produced a value outside [0, 1]",
        corrective_action="provide finite canonical traces and normalized diagnostic weights",
    )


def _validated_scales(widths: Iterable[object], *, window: float) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Validate strictly increasing scales before allocating direction cells."""
    try:
        configured = tuple(widths)
    except TypeError as error:
        raise TrafficlabError(
            "invalid Fano/Allan widths: widths must be iterable",
            corrective_action="provide strictly increasing finite positive float widths with at least two windows",
        ) from error
    if not configured:
        raise TrafficlabError(
            "invalid Fano/Allan widths: at least one width is required",
            corrective_action="provide strictly increasing finite positive float widths with at least two windows",
        )
    validated: list[float] = []
    window_counts: list[int] = []
    previous: float | None = None
    for width in configured:
        if type(width) is not float or not math.isfinite(width) or width <= 0.0 or width > window:
            raise TrafficlabError(
                "invalid Fano/Allan widths: widths must be finite positive floats no larger than W",
                corrective_action="provide strictly increasing finite positive float widths with at least two windows",
            )
        if previous is not None and width <= previous:
            raise TrafficlabError(
                "invalid Fano/Allan widths: widths must be unique and strictly increasing",
                corrective_action="provide strictly increasing finite positive float widths with at least two windows",
            )
        quotient = window / width
        if not math.isfinite(quotient):
            raise TrafficlabError(
                "invalid Fano/Allan widths: W divided by a width must be finite",
                corrective_action="increase the width so its window count is finite and within the configured cap",
            )
        count = math.ceil(snap_near_integer(quotient))
        if count < 2:
            raise TrafficlabError(
                "invalid Fano/Allan widths: every scale requires at least two windows",
                corrective_action="configure widths that create at least two windows in W",
            )
        validated.append(width)
        window_counts.append(count)
        previous = width
    direction_cells = 2 * sum(window_counts)
    if direction_cells > sys.maxsize or direction_cells > _MAXIMUM_DIRECTION_WINDOW_CELLS:
        raise TrafficlabError(
            "invalid Fano/Allan widths: total direction-window cell count exceeds the cap",
            corrective_action="configure fewer or wider scales within the 65536 direction-window cell cap",
        )
    return tuple(validated), tuple(window_counts)


def _curve(counts: tuple[int, ...]) -> tuple[float, float]:
    """Return population-Fano and adjacent-window Allan factors with zero-mean convention."""
    mean = math.fsum(counts) / len(counts)
    if mean == 0.0:
        return (0.0, 0.0)
    fano = math.fsum((count - mean) ** 2 for count in counts) / len(counts) / mean
    allan = math.fsum((right - left) ** 2 for left, right in zip(counts, counts[1:], strict=False)) / (len(counts) - 1)
    return (fano, allan / (2.0 * mean))


def _log_difference(reference: float, generated: float) -> float:
    """Return a symmetric bounded difference of nonnegative dispersion factors."""
    reference_log = math.log1p(reference)
    generated_log = math.log1p(generated)
    denominator = reference_log + generated_log
    return (
        0.0
        if denominator == 0.0
        else _bounded(abs(reference_log - generated_log) / denominator, name="log1p dispersion difference")
    )


def _counts_by_channel(trace: TrafficTrace, *, width: float, window_count: int) -> dict[str, tuple[int, ...]]:
    """Reuse the canonical four-ULP endpoint binning for total and directional counts."""
    packets, _bytes = binned_direction_features(trace, width=width, bins_per_direction=window_count)
    outbound = packets[:window_count]
    inbound = packets[window_count:]
    return {
        "total": tuple(left + right for left, right in zip(outbound, inbound, strict=True)),
        "outbound": outbound,
        "inbound": inbound,
    }


def fano_allan_diagnostic(
    reference: TrafficTrace,
    generated: TrafficTrace,
    W: float,
    widths: tuple[float, ...],
    scale_weights: tuple[float, ...],
    fano_weight: float,
    allan_weight: float,
) -> SimilarityResult:
    """Compare total/outbound/inbound Fano and Allan curves only after final generation."""
    window = validate_observation_window(W)
    validated_widths, window_counts = _validated_scales(widths, window=window)
    validated_scale_weights = validated_weights(
        scale_weights,
        name="Fano/Allan scale weights",
        expected_length=len(validated_widths),
        count_name="width",
    )
    component_weights = validated_weights((fano_weight, allan_weight), name="Fano/Allan component weights")
    reference_trace = validate_traffic_trace(reference, minimum_events=1, trace_name="reference", window=window)
    generated_trace = validate_traffic_trace(generated, minimum_events=1, trace_name="generated", window=window)

    scales: list[dict[str, object]] = []
    fano_differences: list[float] = []
    allan_differences: list[float] = []
    scale_differences: list[float] = []
    for width, count in zip(validated_widths, window_counts, strict=True):
        reference_counts = _counts_by_channel(reference_trace, width=width, window_count=count)
        generated_counts = _counts_by_channel(generated_trace, width=width, window_count=count)
        reference_fano = {channel: _curve(counts)[0] for channel, counts in reference_counts.items()}
        generated_fano = {channel: _curve(counts)[0] for channel, counts in generated_counts.items()}
        reference_allan = {channel: _curve(counts)[1] for channel, counts in reference_counts.items()}
        generated_allan = {channel: _curve(counts)[1] for channel, counts in generated_counts.items()}
        fano_difference = (
            math.fsum(
                _log_difference(reference_fano[channel], generated_fano[channel])
                for channel in ("total", "outbound", "inbound")
            )
            / 3.0
        )
        allan_difference = (
            math.fsum(
                _log_difference(reference_allan[channel], generated_allan[channel])
                for channel in ("total", "outbound", "inbound")
            )
            / 3.0
        )
        scale_difference = _bounded(
            math.fsum((component_weights[0] * fano_difference, component_weights[1] * allan_difference)),
            name="Fano/Allan scale discrepancy",
        )
        fano_differences.append(fano_difference)
        allan_differences.append(allan_difference)
        scale_differences.append(scale_difference)
        scales.append(
            {
                "width_seconds": width,
                "window_count": count,
                "reference_counts": reference_counts,
                "generated_counts": generated_counts,
                "reference_fano": reference_fano,
                "generated_fano": generated_fano,
                "reference_allan": reference_allan,
                "generated_allan": generated_allan,
                "component_differences": {"fano": fano_difference, "allan": allan_difference},
                "discrepancy": scale_difference,
            }
        )
    fano_difference = _bounded(
        math.fsum(weight * value for weight, value in zip(validated_scale_weights, fano_differences, strict=True)),
        name="Fano discrepancy",
    )
    allan_difference = _bounded(
        math.fsum(weight * value for weight, value in zip(validated_scale_weights, allan_differences, strict=True)),
        name="Allan discrepancy",
    )
    discrepancy = _bounded(
        math.fsum((component_weights[0] * fano_difference, component_weights[1] * allan_difference)),
        name="Fano/Allan discrepancy",
    )
    diagnostics: JsonDiagnostics = cast(
        JsonDiagnostics,
        {
            "observation_window_seconds": window,
            "widths": validated_widths,
            "scale_weights": validated_scale_weights,
            "component_weights": {"fano": component_weights[0], "allan": component_weights[1]},
            "total_direction_window_cells": 2 * sum(window_counts),
            "scales": tuple(scales),
            "component_differences": {"fano": fano_difference, "allan": allan_difference},
            "scale_differences": tuple(scale_differences),
            "discrepancy": discrepancy,
        },
    )
    return SimilarityResult(score=1.0 - discrepancy, diagnostics=diagnostics)
