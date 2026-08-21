"""Direction-separated multiscale packet-count and captured-byte similarity."""

import math
import sys
from collections.abc import Iterable

import numpy as np

from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import TraceEvent, TrafficTrace, validate_traffic_trace
from trafficlab.comparison.similarity.common import (
    FrozenJsonValue,
    JsonDiagnostics,
    SimilarityResult,
    validate_observation_window,
    validated_weights,
)

_DISCREPANCY_TOLERANCE = 3e-12


def _validated_cells(values: Iterable[object], *, name: str) -> tuple[int | float, ...]:
    """Materialize one finite nonnegative vector for normalized L1."""
    try:
        cells = tuple(values)
    except TypeError as error:
        raise TrafficlabError(
            f"invalid {name} cells: values must be iterable",
            corrective_action="provide aligned finite nonnegative cell vectors",
        ) from error
    validated: list[int | float] = []
    for value in cells:
        if type(value) is int and value >= 0:
            validated.append(value)
        elif type(value) is float and math.isfinite(value) and value >= 0.0:
            validated.append(value)
        else:
            raise TrafficlabError(
                f"invalid {name} cells: values must be finite nonnegative numbers",
                corrective_action="provide aligned finite nonnegative cell vectors",
            )
    return tuple(validated)


def _bounded_discrepancy(value: float, *, name: str) -> float:
    """Retain the documented range while clamping only negligible weighted roundoff."""
    if not math.isfinite(value):
        raise TrafficlabError(
            f"invalid {name}: computation produced a nonfinite value",
            corrective_action="provide finite nonnegative cells and normalized weights",
        )
    if 0.0 <= value <= 1.0:
        return value
    if -_DISCREPANCY_TOLERANCE <= value < 0.0:
        return 0.0
    if 1.0 < value <= 1.0 + _DISCREPANCY_TOLERANCE:
        return 1.0
    raise TrafficlabError(
        f"invalid {name}: computation produced a value outside [0, 1]",
        corrective_action="provide finite nonnegative cells and normalized weights",
    )


def _exact_l1_totals(reference: tuple[int | float, ...], generated: tuple[int | float, ...]) -> tuple[int, int]:
    """Return exact binary-rational numerator and denominator totals."""
    # ``float.as_integer_ratio`` always yields a power-of-two denominator.
    # The largest denominator is therefore divisible by every smaller one, so
    # both vectors can be lifted to integers without introducing new rounding.
    reference_ratios = tuple((cell, 1) if type(cell) is int else cell.as_integer_ratio() for cell in reference)
    generated_ratios = tuple((cell, 1) if type(cell) is int else cell.as_integer_ratio() for cell in generated)
    common_denominator = max((denominator for _, denominator in (*reference_ratios, *generated_ratios)), default=1)
    reference_numerators = tuple(
        numerator * (common_denominator // denominator) for numerator, denominator in reference_ratios
    )
    generated_numerators = tuple(
        numerator * (common_denominator // denominator) for numerator, denominator in generated_ratios
    )
    numerator_total = sum(
        abs(left - right) for left, right in zip(reference_numerators, generated_numerators, strict=True)
    )
    denominator_total = sum(reference_numerators) + sum(generated_numerators)
    return numerator_total, denominator_total


def normalized_l1(reference_cells: Iterable[object], generated_cells: Iterable[object]) -> float:
    """Return the documented normalized L1 discrepancy for two aligned vectors."""
    reference = _validated_cells(reference_cells, name="reference")
    generated = _validated_cells(generated_cells, name="generated")
    if len(reference) != len(generated):
        raise TrafficlabError(
            "invalid normalized L1 cells: vectors must have equal lengths",
            corrective_action="provide aligned finite nonnegative cell vectors",
        )
    try:
        numerator, denominator = _exact_l1_totals(reference, generated)
        discrepancy = 0.0 if denominator == 0 else numerator / denominator
    except (ArithmeticError, ValueError) as error:
        raise TrafficlabError(
            "invalid normalized L1 cells: values cannot be evaluated safely",
            corrective_action="provide finite nonnegative cell values within the supported arithmetic range",
        ) from error
    return _bounded_discrepancy(discrepancy, name="normalized L1 discrepancy")


def _snap_near_integer(quotient: float) -> float:
    """Snap a finite quotient only within four ULPs of its nearest integer."""
    # Values such as ``(k * width) / width`` can land a few representable
    # floats either side of k.  Snapping only within an ULP-scale tolerance
    # stabilizes exact bin boundaries without treating nearby real times as
    # equal or creating a user-visible fuzzy boundary.
    nearest = round(quotient)
    if abs(quotient - nearest) <= 4.0 * math.ulp(quotient):
        return float(nearest)
    return quotient


def _validated_widths_and_bin_counts(
    widths: Iterable[object], *, window: float, max_direction_bin_cells: object
) -> tuple[tuple[float, ...], tuple[int, ...], tuple[int, ...], int]:
    """Validate all scale sizes and the aggregate cap before any cell allocation."""
    if type(max_direction_bin_cells) is not int or max_direction_bin_cells < 2:
        raise TrafficlabError(
            "invalid direction-bin cell cap: it must be an integer of at least two",
            corrective_action="provide an integer cap large enough for every configured scale",
        )
    try:
        configured_widths = tuple(widths)
    except TypeError as error:
        raise TrafficlabError(
            "invalid multiscale widths: widths must be iterable",
            corrective_action="provide unique strictly increasing finite positive float widths",
        ) from error
    if not configured_widths:
        raise TrafficlabError(
            "invalid multiscale widths: at least one width is required",
            corrective_action="provide unique strictly increasing finite positive float widths",
        )

    validated_widths: list[float] = []
    bin_counts: list[int] = []
    previous_width: float | None = None
    for width in configured_widths:
        if type(width) is not float or not math.isfinite(width) or width <= 0.0 or width > window:
            raise TrafficlabError(
                "invalid multiscale widths: widths must be finite positive floats no larger than W",
                corrective_action="provide unique strictly increasing finite positive float widths",
            )
        if previous_width is not None and width <= previous_width:
            raise TrafficlabError(
                "invalid multiscale widths: widths must be unique and strictly increasing",
                corrective_action="provide unique strictly increasing finite positive float widths",
            )
        quotient = window / width
        if not math.isfinite(quotient):
            raise TrafficlabError(
                "invalid multiscale widths: W divided by a width must be finite",
                corrective_action="increase the width so its bin count is finite and within the configured cap",
            )
        validated_widths.append(width)
        bin_counts.append(math.ceil(_snap_near_integer(quotient)))
        previous_width = width

    # Account for both directions before allocating any lists.  This makes the
    # configured cap a bound on actual feature cells rather than merely on the
    # number of time bins requested for one direction.
    direction_bin_cell_counts = tuple(2 * count for count in bin_counts)
    total_direction_bin_cells = sum(direction_bin_cell_counts)
    if (
        any(direction_cell_count > sys.maxsize for direction_cell_count in direction_bin_cell_counts)
        or total_direction_bin_cells > sys.maxsize
    ):
        raise TrafficlabError(
            "invalid multiscale widths: direction-bin cell count exceeds the platform allocation range",
            corrective_action="configure fewer or wider scales whose total cell count fits the platform",
        )
    if total_direction_bin_cells > max_direction_bin_cells:
        raise TrafficlabError(
            "invalid multiscale widths: total direction-bin cell count exceeds the configured cap",
            corrective_action="increase the cap or configure fewer or wider scales",
        )
    return (
        tuple(validated_widths),
        tuple(bin_counts),
        direction_bin_cell_counts,
        total_direction_bin_cells,
    )


def _binned_trace_features(
    trace: TrafficTrace, *, width: float, bins_per_direction: int
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Build exact cells directly from one validated columnar trace."""
    quotients = trace.timestamps / width
    nearest = np.rint(quotients)
    ulps = np.abs(np.spacing(quotients))
    snapped = np.where(np.abs(quotients - nearest) <= 4.0 * ulps, nearest, quotients)
    indices = np.minimum(np.floor(snapped).astype(np.intp), bins_per_direction - 1)
    flat_indices = indices + trace.directions.astype(np.intp) * bins_per_direction
    packets = np.bincount(flat_indices, minlength=2 * bins_per_direction)

    maximum_length = int(np.max(trace.frame_lengths))
    if len(trace) <= np.iinfo(np.uint64).max // maximum_length:
        exact_bytes = np.zeros(2 * bins_per_direction, dtype=np.uint64)
        np.add.at(exact_bytes, flat_indices, trace.frame_lengths.astype(np.uint64))
        return tuple(int(value) for value in packets), tuple(int(value) for value in exact_bytes)

    fallback = [0] * (2 * bins_per_direction)
    for index, frame_length in zip(flat_indices, trace.frame_lengths, strict=True):
        fallback[int(index)] += int(frame_length)
    return tuple(int(value) for value in packets), tuple(fallback)


def _binned_features(
    trace: TrafficTrace, *, width: float, bins_per_direction: int
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Build outbound-then-inbound cells from one validated columnar trace."""
    return _binned_trace_features(trace, width=width, bins_per_direction=bins_per_direction)


def _direction_totals(
    packet_cells: tuple[int, ...], byte_cells: tuple[int, ...], *, bins_per_direction: int
) -> dict[str, FrozenJsonValue]:
    """Return explicit packet and byte totals for the two documented directions."""
    return {
        "packet": {
            "outbound": sum(packet_cells[:bins_per_direction]),
            "inbound": sum(packet_cells[bins_per_direction:]),
        },
        "byte": {
            "outbound": sum(byte_cells[:bins_per_direction]),
            "inbound": sum(byte_cells[bins_per_direction:]),
        },
    }


def multiscale_rate_similarity(
    reference: Iterable[TraceEvent] | TrafficTrace,
    generated: Iterable[TraceEvent] | TrafficTrace,
    W: object,
    widths: Iterable[object],
    scale_weights: Iterable[object],
    packet_weight: object,
    byte_weight: object,
    max_direction_bin_cells: object,
) -> SimilarityResult:
    """Compare direction-separated packet and byte volume at configured time scales."""
    window = validate_observation_window(W)
    validated_widths, bin_counts, direction_cell_counts, total_direction_cells = _validated_widths_and_bin_counts(
        widths,
        window=window,
        max_direction_bin_cells=max_direction_bin_cells,
    )
    validated_scale_weights = validated_weights(
        scale_weights,
        name="multiscale scale weights",
        expected_length=len(validated_widths),
        count_name="width",
    )
    feature_weights = validated_weights(
        (packet_weight, byte_weight),
        name="multiscale feature weights",
    )
    reference_trace = validate_traffic_trace(reference, minimum_events=1, trace_name="reference", window=window)
    generated_trace = validate_traffic_trace(generated, minimum_events=1, trace_name="generated", window=window)

    scale_diagnostics: list[dict[str, FrozenJsonValue]] = []
    packet_discrepancies: list[float] = []
    byte_discrepancies: list[float] = []
    scale_discrepancies: list[float] = []
    for width, bins_per_direction, direction_cell_count in zip(
        validated_widths, bin_counts, direction_cell_counts, strict=True
    ):
        reference_packets, reference_bytes = _binned_features(
            reference_trace,
            width=width,
            bins_per_direction=bins_per_direction,
        )
        generated_packets, generated_bytes = _binned_features(
            generated_trace,
            width=width,
            bins_per_direction=bins_per_direction,
        )
        packet_discrepancy = normalized_l1(reference_packets, generated_packets)
        byte_discrepancy = normalized_l1(reference_bytes, generated_bytes)
        scale_discrepancy = _bounded_discrepancy(
            math.fsum(
                (
                    feature_weights[0] * packet_discrepancy,
                    feature_weights[1] * byte_discrepancy,
                )
            ),
            name="multiscale scale discrepancy",
        )
        packet_discrepancies.append(packet_discrepancy)
        byte_discrepancies.append(byte_discrepancy)
        scale_discrepancies.append(scale_discrepancy)
        scale_diagnostics.append(
            {
                "width_seconds": width,
                "bins_per_direction": bins_per_direction,
                "direction_bin_cell_count": direction_cell_count,
                "reference_totals": _direction_totals(
                    reference_packets,
                    reference_bytes,
                    bins_per_direction=bins_per_direction,
                ),
                "generated_totals": _direction_totals(
                    generated_packets,
                    generated_bytes,
                    bins_per_direction=bins_per_direction,
                ),
                "feature_discrepancies": {
                    "packet": packet_discrepancy,
                    "byte": byte_discrepancy,
                },
                "discrepancy": scale_discrepancy,
            }
        )

    # Aggregate each feature across scales before applying feature weights.
    # Keeping this decomposition explicit makes the diagnostic feature totals
    # the exact operands used by the final score, including floating-point
    # summation order.
    packet_total = _bounded_discrepancy(
        math.fsum(
            weight * discrepancy
            for weight, discrepancy in zip(validated_scale_weights, packet_discrepancies, strict=True)
        ),
        name="multiscale packet discrepancy",
    )
    byte_total = _bounded_discrepancy(
        math.fsum(
            weight * discrepancy
            for weight, discrepancy in zip(validated_scale_weights, byte_discrepancies, strict=True)
        ),
        name="multiscale byte discrepancy",
    )
    discrepancy = _bounded_discrepancy(
        math.fsum((feature_weights[0] * packet_total, feature_weights[1] * byte_total)),
        name="multiscale discrepancy",
    )
    diagnostics: JsonDiagnostics = {
        "observation_window_seconds": window,
        "widths": validated_widths,
        "scale_weights": validated_scale_weights,
        "feature_weights": {"packet": feature_weights[0], "byte": feature_weights[1]},
        "direction_bin_cell_counts": direction_cell_counts,
        "total_direction_bin_cells": total_direction_cells,
        "scales": tuple(scale_diagnostics),
        "scale_discrepancies": tuple(scale_discrepancies),
        "feature_discrepancies": {"packet": packet_total, "byte": byte_total},
        "discrepancy": discrepancy,
    }
    return SimilarityResult(score=1.0 - discrepancy, diagnostics=diagnostics)
