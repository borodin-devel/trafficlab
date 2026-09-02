"""Independent hand checks for final-only Fano/Allan dispersion curves."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import cast

import pytest

from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.comparison.postfit.dispersion import fano_allan_diagnostic


def _trace(events: tuple[tuple[float, Direction], ...]) -> TrafficTrace:
    return TrafficTrace.from_events(TraceEvent(timestamp, direction, 100) for timestamp, direction in events)


def _curve(counts: list[int]) -> tuple[float, float]:
    """Hand oracle using population variance and adjacent count differences."""
    mean = sum(counts) / len(counts)
    if mean == 0.0:
        return (0.0, 0.0)
    fano = sum((count - mean) ** 2 for count in counts) / len(counts) / mean
    allan = sum((right - left) ** 2 for left, right in zip(counts, counts[1:], strict=False)) / (len(counts) - 1)
    return (fano, allan / (2.0 * mean))


def _log_difference(reference: float, generated: float) -> float:
    """Hand oracle for the documented bounded log1p curve discrepancy."""
    left = math.log1p(reference)
    right = math.log1p(generated)
    return 0.0 if left + right == 0.0 else abs(left - right) / (left + right)


def test_constant_one_packet_windows_keep_all_fano_and_allan_curves_at_zero() -> None:
    """Dividing a zero-variance or all-zero direction channel by its mean must not create NaN."""
    trace = _trace(
        (
            (0.0, Direction.OUTBOUND),
            (1.0, Direction.OUTBOUND),
            (2.0, Direction.OUTBOUND),
            (3.0, Direction.OUTBOUND),
        )
    )

    result = fano_allan_diagnostic(trace, trace, 4.0, (1.0,), (1.0,), 0.5, 0.5)

    scales = cast(tuple[Mapping[str, object], ...], result.diagnostics["scales"])
    scale = scales[0]
    reference_counts = cast(Mapping[str, object], scale["reference_counts"])
    reference_fano = cast(Mapping[str, object], scale["reference_fano"])
    reference_allan = cast(Mapping[str, object], scale["reference_allan"])
    component_differences = cast(Mapping[str, object], scale["component_differences"])
    assert result.score == 1.0
    assert reference_counts == {"total": (1, 1, 1, 1), "outbound": (1, 1, 1, 1), "inbound": (0, 0, 0, 0)}
    assert reference_fano == {"total": 0.0, "outbound": 0.0, "inbound": 0.0}
    assert reference_allan == {"total": 0.0, "outbound": 0.0, "inbound": 0.0}
    assert component_differences == {"fano": 0.0, "allan": 0.0}


def test_alternating_counts_endpoint_and_scale_weights_match_the_hand_oracle() -> None:
    """Wrong endpoint assignment, variance convention, or scale aggregation changes these literals."""
    reference = _trace(
        (
            (0.0, Direction.OUTBOUND),
            (0.1, Direction.OUTBOUND),
            (2.0, Direction.OUTBOUND),
            (2.1, Direction.OUTBOUND),
            (2.2, Direction.OUTBOUND),
            (4.0, Direction.OUTBOUND),
        )
    )
    generated = _trace(
        (
            (0.0, Direction.OUTBOUND),
            (1.0, Direction.OUTBOUND),
            (2.0, Direction.OUTBOUND),
            (3.0, Direction.OUTBOUND),
            (4.0, Direction.OUTBOUND),
        )
    )

    result = fano_allan_diagnostic(reference, generated, 4.0, (1.0, 2.0), (0.25, 0.75), 0.25, 0.75)

    reference_one = [2, 0, 3, 1]
    generated_one = [1, 1, 1, 2]
    reference_two = [2, 4]
    generated_two = [2, 3]
    fano_one, allan_one = _curve(reference_one)
    generated_fano_one, generated_allan_one = _curve(generated_one)
    fano_two, allan_two = _curve(reference_two)
    generated_fano_two, generated_allan_two = _curve(generated_two)
    one_fano = _log_difference(fano_one, generated_fano_one)
    one_allan = _log_difference(allan_one, generated_allan_one)
    two_fano = _log_difference(fano_two, generated_fano_two)
    two_allan = _log_difference(allan_two, generated_allan_two)
    # Total and outbound carry these nonzero curves; the required inbound
    # channel is all zero, so each unweighted component mean has factor 2/3.
    expected = 1.0 - (0.25 * (0.25 * (2.0 * one_fano / 3.0) + 0.75 * (2.0 * one_allan / 3.0)) + 0.75 * (0.25 * (2.0 * two_fano / 3.0) + 0.75 * (2.0 * two_allan / 3.0)))

    assert result.score == pytest.approx(expected)
    scales = cast(tuple[Mapping[str, object], ...], result.diagnostics["scales"])
    first_reference_counts = cast(Mapping[str, object], scales[0]["reference_counts"])
    first_generated_counts = cast(Mapping[str, object], scales[0]["generated_counts"])
    second_reference_counts = cast(Mapping[str, object], scales[1]["reference_counts"])
    assert scales[0]["window_count"] == 4
    assert first_reference_counts["total"] == tuple(reference_one)
    assert first_generated_counts["total"] == tuple(generated_one)
    assert second_reference_counts["total"] == tuple(reference_two)
    assert result.diagnostics["component_differences"] == {  # type: ignore[comparison-overlap]
        "fano": pytest.approx(2.0 * (0.25 * one_fano + 0.75 * two_fano) / 3.0),
        "allan": pytest.approx(2.0 * (0.25 * one_allan + 0.75 * two_allan) / 3.0),
    }


@pytest.mark.parametrize("widths", [(4.0,)])
def test_dispersion_rejects_scales_without_two_complete_windows(widths: tuple[float, ...]) -> None:
    """Allan variance is undefined for a single configured window."""
    trace = _trace(((0.0, Direction.OUTBOUND), (4.0, Direction.OUTBOUND)))

    with pytest.raises(TrafficlabError, match="at least two windows"):
        fano_allan_diagnostic(trace, trace, 4.0, widths, tuple(1.0 for _ in widths), 0.5, 0.5)


def test_dispersion_rejects_excessive_direction_window_cells_before_binning() -> None:
    """An unchecked tiny width could allocate an unbounded final-only count vector."""
    trace = _trace(((0.0, Direction.OUTBOUND), (40_000.0, Direction.OUTBOUND)))

    with pytest.raises(TrafficlabError, match="cell count exceeds the cap"):
        fano_allan_diagnostic(trace, trace, 40_000.0, (1.0,), (1.0,), 0.5, 0.5)


def test_dispersion_rejects_a_finite_scale_with_a_nonfinite_window_quotient() -> None:
    """Calling ceil on an overflowing W/width quotient leaks a built-in OverflowError."""
    trace = _trace(((0.0, Direction.OUTBOUND), (1.0, Direction.OUTBOUND)))

    with pytest.raises(TrafficlabError, match="W divided by a width must be finite"):
        fano_allan_diagnostic(trace, trace, 1e308, (1e-308,), (1.0,), 0.5, 0.5)


@pytest.mark.parametrize("widths", [(), (1.0, 1.0), (float("inf"),)])
def test_dispersion_rejects_empty_duplicate_or_nonfinite_scales(widths: tuple[float, ...]) -> None:
    """Loose scale validation could create ambiguous curves or nonfinite bins."""
    trace = _trace(((0.0, Direction.OUTBOUND), (2.0, Direction.OUTBOUND)))

    with pytest.raises(TrafficlabError, match="Fano/Allan widths"):
        fano_allan_diagnostic(trace, trace, 2.0, widths, tuple(1.0 for _ in widths), 0.5, 0.5)
