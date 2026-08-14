"""Unit tests for direction-separated multiscale packet and byte rates."""

import json
import math
from collections.abc import Iterable
from typing import cast

import pytest

import trafficlab.similarity.multiscale as multiscale_module
from trafficlab.errors import TrafficlabError
from trafficlab.similarity.multiscale import (
    _exact_l1_totals,  # pyright: ignore[reportPrivateUsage]
    _snap_near_integer,  # pyright: ignore[reportPrivateUsage]
    multiscale_rate_similarity,
    normalized_l1,
)
from trafficlab.trace import Direction, TraceEvent


def _event(
    timestamp: float,
    *,
    direction: Direction = Direction.OUTBOUND,
    frame_length: int = 100,
) -> TraceEvent:
    return TraceEvent(timestamp=timestamp, direction=direction, frame_length=frame_length)


def _invalid_event(
    *,
    timestamp: object = 0.0,
    direction: object = Direction.OUTBOUND,
    frame_length: object = 100,
) -> TraceEvent:
    """Construct malformed canonical data to prove the metric validates its boundary."""
    event = object.__new__(TraceEvent)
    object.__setattr__(event, "timestamp", timestamp)
    object.__setattr__(event, "direction", direction)
    object.__setattr__(event, "frame_length", frame_length)
    return event


def _nextafter_steps(value: float, toward: float, steps: int) -> float:
    for _ in range(steps):
        value = math.nextafter(value, toward)
    return value


@pytest.mark.parametrize(
    ("reference", "generated", "expected"),
    [
        ((2, 0, 3), (2, 0, 3), 0.0),
        ((0, 0), (0, 0), 0.0),
        ((1, 0), (0, 1), 1.0),
        ((1, 1), (1, 0), 1.0 / 3.0),
    ],
)
def test_normalized_l1_matches_hand_calculated_vectors(
    reference: tuple[int, ...], generated: tuple[int, ...], expected: float
) -> None:
    assert normalized_l1(reference, generated) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("reference", "generated"),
    [
        ((1,), (1, 2)),
        ((-1,), (0,)),
        ((math.inf,), (0,)),
        ((True,), (0,)),
    ],
)
def test_normalized_l1_rejects_unaligned_or_invalid_cells(
    reference: tuple[object, ...], generated: tuple[object, ...]
) -> None:
    with pytest.raises(TrafficlabError):
        normalized_l1(reference, generated)


def test_normalized_l1_rejects_noniterable_cells() -> None:
    with pytest.raises(TrafficlabError):
        normalized_l1(cast(Iterable[object], 1), (0,))


def test_normalized_l1_preserves_a_ratio_when_finite_float_sums_would_overflow() -> None:
    assert normalized_l1((1e308,), (9e307,)) == pytest.approx(1.0 / 19.0)


def test_normalized_l1_preserves_a_ratio_for_huge_integer_cells() -> None:
    assert normalized_l1((10**308,), (9 * 10**307,)) == pytest.approx(1.0 / 19.0)


def test_normalized_l1_preserves_a_small_exact_gap_between_huge_integer_cells() -> None:
    reference = 2**2000
    generated = reference - 2**1900
    expected = (2**1900) / (2**2001 - 2**1900)

    assert normalized_l1((reference,), (generated,)) == expected


def test_exact_l1_accumulation_retains_a_tiny_float_alongside_a_huge_integer() -> None:
    small = 8.7098e-295
    small_numerator, small_denominator = small.as_integer_ratio()
    huge = 2**2000

    assert _exact_l1_totals((huge, small), (huge, 0.0)) == (
        small_numerator,
        2 * huge * small_denominator + small_numerator,
    )


def test_normalized_l1_accumulates_the_reviewed_mixed_binary_rational_vector() -> None:
    huge = 2**2000
    gap = 2**1900
    small = 8.7098e-295
    small_numerator, small_denominator = small.as_integer_ratio()
    expected = (gap * small_denominator + small_numerator) / ((2 * huge - gap) * small_denominator + small_numerator)

    assert normalized_l1((huge, small), (huge - gap, 0.0)) == expected


def test_normalized_l1_scales_mixed_huge_integer_and_finite_float_cells() -> None:
    assert normalized_l1((10**10_000, 5e-324), (9 * 10**9_999, 0.0)) == pytest.approx(1.0 / 19.0)


def test_normalized_l1_scales_zero_integer_cells_with_a_subnormal_float() -> None:
    assert normalized_l1((5e-324, 0), (0.0, 0)) == 1.0


def test_normalized_l1_translates_an_arithmetic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_overflow(_reference: tuple[int | float, ...], _generated: tuple[int | float, ...]) -> tuple[int, int]:
        raise OverflowError("controlled arithmetic failure")

    monkeypatch.setattr(multiscale_module, "_exact_l1_totals", raise_overflow)

    with pytest.raises(TrafficlabError, match="evaluated safely"):
        normalized_l1((1.0,), (0.5,))


@pytest.mark.parametrize("integer", [8.0, float(2**40)])
@pytest.mark.parametrize("toward", [0.0, math.inf])
def test_snap_near_integer_includes_exactly_four_ulps_but_not_the_next_float(integer: float, toward: float) -> None:
    four_ulps_away = _nextafter_steps(integer, toward, 4)
    immediately_outside = math.nextafter(four_ulps_away, toward)

    assert _snap_near_integer(four_ulps_away) == integer
    assert _snap_near_integer(immediately_outside) == immediately_outside


def test_snap_near_integer_handles_zero_and_the_subnormal_boundary() -> None:
    subnormal_ulp = math.ulp(0.0)
    four_ulps_from_zero = 4.0 * subnormal_ulp
    immediately_outside = math.nextafter(four_ulps_from_zero, math.inf)

    assert _snap_near_integer(0.0) == 0.0
    assert _snap_near_integer(four_ulps_from_zero) == 0.0
    assert _snap_near_integer(immediately_outside) == immediately_outside


def test_multiscale_returns_every_hand_calculated_multiscale_diagnostic() -> None:
    reference = (
        _event(0.0, frame_length=2),
        _event(0.75, direction=Direction.INBOUND, frame_length=3),
        _event(2.0, frame_length=5),
    )
    generated = (
        _event(0.0, direction=Direction.INBOUND, frame_length=2),
        _event(1.25, direction=Direction.INBOUND, frame_length=4),
        _event(2.0, frame_length=5),
    )

    result = multiscale_rate_similarity(reference, generated, 2.0, (1.0, 2.0), (0.25, 0.75), 0.4, 0.6, 6)

    assert result.score == pytest.approx(149.0 / 210.0)
    assert result.diagnostics == {
        "observation_window_seconds": 2.0,
        "widths": (1.0, 2.0),
        "scale_weights": (0.25, 0.75),
        "feature_weights": {"packet": 0.4, "byte": 0.6},
        "direction_bin_cell_counts": (4, 2),
        "total_direction_bin_cells": 6,
        "scales": (
            {
                "width_seconds": 1.0,
                "bins_per_direction": 2,
                "direction_bin_cell_count": 4,
                "reference_totals": {
                    "packet": {"outbound": 2, "inbound": 1},
                    "byte": {"outbound": 7, "inbound": 3},
                },
                "generated_totals": {
                    "packet": {"outbound": 1, "inbound": 2},
                    "byte": {"outbound": 5, "inbound": 6},
                },
                "feature_discrepancies": {
                    "packet": pytest.approx(1.0 / 3.0),
                    "byte": pytest.approx(1.0 / 3.0),
                },
                "discrepancy": pytest.approx(1.0 / 3.0),
            },
            {
                "width_seconds": 2.0,
                "bins_per_direction": 1,
                "direction_bin_cell_count": 2,
                "reference_totals": {
                    "packet": {"outbound": 2, "inbound": 1},
                    "byte": {"outbound": 7, "inbound": 3},
                },
                "generated_totals": {
                    "packet": {"outbound": 1, "inbound": 2},
                    "byte": {"outbound": 5, "inbound": 6},
                },
                "feature_discrepancies": {
                    "packet": pytest.approx(1.0 / 3.0),
                    "byte": pytest.approx(5.0 / 21.0),
                },
                "discrepancy": pytest.approx(29.0 / 105.0),
            },
        ),
        "scale_discrepancies": pytest.approx((1.0 / 3.0, 29.0 / 105.0)),
        "feature_discrepancies": {
            "packet": pytest.approx(1.0 / 3.0),
            "byte": pytest.approx(11.0 / 42.0),
        },
        "discrepancy": pytest.approx(61.0 / 210.0),
    }


def test_multiscale_identical_traces_score_one_with_json_safe_immutable_diagnostics() -> None:
    trace = (_event(0.0), _event(1.0, direction=Direction.INBOUND, frame_length=200))

    result = multiscale_rate_similarity(trace, trace, 1.0, (0.5, 1.0), (0.5, 0.5), 0.5, 0.5, 6)

    assert result.score == 1.0
    assert result.diagnostics["discrepancy"] == 0.0
    json.dumps(result.as_dict())
    with pytest.raises(TypeError):
        cast(dict[str, object], result.diagnostics["feature_weights"])["packet"] = 1.0


def test_reversed_one_bin_direction_has_maximum_discrepancy() -> None:
    reference = (_event(0.0), _event(0.5), _event(1.0))
    generated = tuple(TraceEvent(event.timestamp, Direction.INBOUND, event.frame_length) for event in reference)

    result = multiscale_rate_similarity(reference, generated, 1.0, (1.0,), (1.0,), 1.0, 0.0, 2)

    assert result.score == 0.0
    assert result.diagnostics["discrepancy"] == 1.0
    assert result.diagnostics["scales"][0]["feature_discrepancies"]["packet"] == 1.0  # type: ignore[index]


def test_multiscale_preserves_trailing_zero_bins() -> None:
    trace = (_event(0.0),)

    result = multiscale_rate_similarity(trace, trace, 3.0, (1.0,), (1.0,), 1.0, 0.0, 6)

    assert result.diagnostics["direction_bin_cell_counts"] == (6,)
    assert result.diagnostics["scales"][0]["bins_per_direction"] == 3  # type: ignore[index]


def test_multiscale_includes_an_event_exactly_at_the_closed_window_endpoint() -> None:
    trace = (_event(1.0, direction=Direction.INBOUND, frame_length=17),)

    result = multiscale_rate_similarity(trace, trace, 1.0, (0.25,), (1.0,), 0.5, 0.5, 8)

    scale = result.diagnostics["scales"][0]  # type: ignore[index]
    assert scale["reference_totals"] == {  # type: ignore[index]
        "packet": {"outbound": 0, "inbound": 1},
        "byte": {"outbound": 0, "inbound": 17},
    }
    assert result.score == 1.0


def test_multiscale_byte_only_score_handles_very_large_valid_frame_lengths() -> None:
    reference = (_event(0.0, frame_length=10**308),)
    generated = (_event(0.0, frame_length=9 * 10**307),)

    result = multiscale_rate_similarity(reference, generated, 1.0, (1.0,), (1.0,), 0.0, 1.0, 2)

    assert result.diagnostics["feature_discrepancies"] == {
        "packet": 0.0,
        "byte": pytest.approx(1.0 / 19.0),
    }
    assert result.score == pytest.approx(18.0 / 19.0)


def test_multiscale_byte_only_diagnostic_preserves_a_small_exact_gap_between_huge_lengths() -> None:
    reference_length = 2**2000
    generated_length = reference_length - 2**1900
    expected = (2**1900) / (2**2001 - 2**1900)

    result = multiscale_rate_similarity(
        (_event(0.0, frame_length=reference_length),),
        (_event(0.0, frame_length=generated_length),),
        1.0,
        (1.0,),
        (1.0,),
        0.0,
        1.0,
        2,
    )

    assert result.diagnostics["feature_discrepancies"] == {"packet": 0.0, "byte": expected}


def test_multiscale_decimal_window_quotient_snaps_to_seven_bins() -> None:
    trace = (_event(0.0),)

    result = multiscale_rate_similarity(trace, trace, 2.1, (0.3,), (1.0,), 1.0, 0.0, 14)

    assert result.diagnostics["direction_bin_cell_counts"] == (14,)
    assert result.diagnostics["scales"][0]["bins_per_direction"] == 7  # type: ignore[index]


def test_multiscale_decimal_event_quotient_snaps_to_bin_index_three() -> None:
    reference = (_event(0.3),)
    generated = (_event(0.31),)

    result = multiscale_rate_similarity(reference, generated, 0.4, (0.1,), (1.0,), 1.0, 0.0, 8)

    assert result.score == 1.0


def test_multiscale_direction_reversal_changes_an_asymmetric_trace() -> None:
    reference = (
        _event(0.0),
        _event(0.5),
        _event(1.0, direction=Direction.INBOUND),
    )
    generated = tuple(
        TraceEvent(
            timestamp=event.timestamp,
            direction=Direction.INBOUND if event.direction is Direction.OUTBOUND else Direction.OUTBOUND,
            frame_length=event.frame_length,
        )
        for event in reference
    )

    result = multiscale_rate_similarity(reference, generated, 1.0, (0.5, 1.0), (0.5, 0.5), 1.0, 0.0, 6)

    assert result.score < 1.0


def test_multiscale_direction_reversal_preserves_a_direction_symmetric_trace() -> None:
    reference = (
        _event(0.25, frame_length=10),
        _event(0.25, direction=Direction.INBOUND, frame_length=10),
        _event(0.75, frame_length=20),
        _event(0.75, direction=Direction.INBOUND, frame_length=20),
    )
    generated = tuple(
        TraceEvent(
            timestamp=event.timestamp,
            direction=Direction.INBOUND if event.direction is Direction.OUTBOUND else Direction.OUTBOUND,
            frame_length=event.frame_length,
        )
        for event in reference
    )

    result = multiscale_rate_similarity(reference, generated, 1.0, (0.5,), (1.0,), 0.5, 0.5, 4)

    assert result.score == 1.0


@pytest.mark.parametrize("W", [0.0, -1.0, math.nan, math.inf, 1])
def test_multiscale_rejects_an_invalid_observation_window(W: object) -> None:
    trace = (_event(0.0),)

    with pytest.raises(TrafficlabError):
        multiscale_rate_similarity(trace, trace, W, (1.0,), (1.0,), 0.5, 0.5, 2)


@pytest.mark.parametrize(
    "widths",
    [(), (0.0,), (-1.0,), (math.nan,), (math.inf,), (1,), (1.1,), (0.5, 0.5), (0.75, 0.5)],
)
def test_multiscale_rejects_invalid_duplicate_too_large_or_unsorted_widths(widths: tuple[object, ...]) -> None:
    trace = (_event(0.0),)

    with pytest.raises(TrafficlabError):
        multiscale_rate_similarity(trace, trace, 1.0, widths, (1.0,) * len(widths), 0.5, 0.5, 100)


def test_multiscale_rejects_noniterable_widths() -> None:
    trace = (_event(0.0),)

    with pytest.raises(TrafficlabError):
        multiscale_rate_similarity(trace, trace, 1.0, cast(Iterable[object], 1), (1.0,), 0.5, 0.5, 2)


@pytest.mark.parametrize(
    ("scale_weights", "packet_weight", "byte_weight"),
    [
        ((), 0.5, 0.5),
        ((1.0, 0.0), 0.5, 0.5),
        ((0.9,), 0.5, 0.5),
        ((-1.0,), 0.5, 0.5),
        ((math.inf,), 0.5, 0.5),
        ((1,), 0.5, 0.5),
        ((1.0,), 0.6, 0.5),
        ((1.0,), -0.1, 1.1),
        ((1.0,), math.nan, 1.0),
        ((1.0,), 1, 0.0),
    ],
)
def test_multiscale_rejects_invalid_scale_or_feature_weights(
    scale_weights: tuple[object, ...], packet_weight: object, byte_weight: object
) -> None:
    trace = (_event(0.0),)

    with pytest.raises(TrafficlabError):
        multiscale_rate_similarity(trace, trace, 1.0, (1.0,), scale_weights, packet_weight, byte_weight, 2)


def test_multiscale_rejects_noniterable_scale_weights() -> None:
    trace = (_event(0.0),)

    with pytest.raises(TrafficlabError):
        multiscale_rate_similarity(trace, trace, 1.0, (1.0,), cast(Iterable[object], 1), 0.5, 0.5, 2)


@pytest.mark.parametrize(
    ("weight", "accepted"),
    [
        (math.nextafter(1.0 - 1e-12, math.inf), True),
        (math.nextafter(1.0 - 1e-12, -math.inf), False),
        (math.nextafter(1.0 + 1e-12, -math.inf), True),
        (math.nextafter(1.0 + 1e-12, math.inf), False),
    ],
)
def test_multiscale_uses_only_absolute_tolerance_for_scale_weights(weight: float, accepted: bool) -> None:
    trace = (_event(0.0),)

    if accepted:
        assert multiscale_rate_similarity(trace, trace, 1.0, (1.0,), (weight,), 1.0, 0.0, 2).score == 1.0
    else:
        with pytest.raises(TrafficlabError):
            multiscale_rate_similarity(trace, trace, 1.0, (1.0,), (weight,), 1.0, 0.0, 2)


@pytest.mark.parametrize(
    ("weight", "accepted"),
    [
        (math.nextafter(1.0 - 1e-12, math.inf), True),
        (math.nextafter(1.0 - 1e-12, -math.inf), False),
        (math.nextafter(1.0 + 1e-12, -math.inf), True),
        (math.nextafter(1.0 + 1e-12, math.inf), False),
    ],
)
def test_multiscale_uses_only_absolute_tolerance_for_feature_weights(weight: float, accepted: bool) -> None:
    trace = (_event(0.0),)

    if accepted:
        assert multiscale_rate_similarity(trace, trace, 1.0, (1.0,), (1.0,), weight, 0.0, 2).score == 1.0
    else:
        with pytest.raises(TrafficlabError):
            multiscale_rate_similarity(trace, trace, 1.0, (1.0,), (1.0,), weight, 0.0, 2)


@pytest.mark.parametrize("cell_cap", [True, 1, 2.0, 0, -2])
def test_multiscale_rejects_an_invalid_direction_bin_cell_cap(cell_cap: object) -> None:
    trace = (_event(0.0),)

    with pytest.raises(TrafficlabError):
        multiscale_rate_similarity(trace, trace, 1.0, (1.0,), (1.0,), 0.5, 0.5, cell_cap)


def test_multiscale_rejects_the_total_direction_bin_cell_count_above_the_cap() -> None:
    trace = (_event(0.0),)

    with pytest.raises(TrafficlabError, match="cell"):
        multiscale_rate_similarity(trace, trace, 2.0, (0.5, 1.0), (0.5, 0.5), 0.5, 0.5, 11)


def test_multiscale_rejects_a_nonfinite_bin_quotient_before_allocation() -> None:
    trace = (_event(0.0),)

    with pytest.raises(TrafficlabError):
        multiscale_rate_similarity(trace, trace, 1.0, (5e-324,), (1.0,), 0.5, 0.5, 10)


def test_multiscale_rejects_a_cell_count_above_the_platform_allocation_range() -> None:
    trace = (_event(0.0),)

    with pytest.raises(TrafficlabError, match="platform allocation range"):
        multiscale_rate_similarity(trace, trace, 1.0, (1e-308,), (1.0,), 0.5, 0.5, 10**309)


@pytest.mark.parametrize(
    "trace",
    [
        (),
        cast(Iterable[TraceEvent], 1),
        cast(Iterable[TraceEvent], [object()]),
        (_invalid_event(timestamp=math.nan),),
        (_invalid_event(timestamp=-1.0),),
        (_invalid_event(direction="outbound"),),
        (_invalid_event(frame_length=0),),
        (_invalid_event(frame_length=1.0),),
        (_invalid_event(timestamp=0.5), _invalid_event(timestamp=0.25)),
    ],
)
def test_multiscale_rejects_empty_or_invalid_canonical_traces(trace: Iterable[TraceEvent]) -> None:
    valid = (_event(0.0),)

    with pytest.raises(TrafficlabError):
        multiscale_rate_similarity(trace, valid, 1.0, (1.0,), (1.0,), 0.5, 0.5, 2)


def test_multiscale_translates_an_incomplete_event_to_trafficlab_error() -> None:
    incomplete = object.__new__(TraceEvent)
    object.__setattr__(incomplete, "timestamp", 0.0)

    with pytest.raises(TrafficlabError):
        multiscale_rate_similarity((incomplete,), (_event(0.0),), 1.0, (1.0,), (1.0,), 0.5, 0.5, 2)


@pytest.mark.parametrize("timestamp", [1.0000000000000002, 2.0])
def test_multiscale_rejects_events_outside_the_shared_closed_window(timestamp: float) -> None:
    outside = (_event(timestamp),)

    with pytest.raises(TrafficlabError, match=r"\[0, W\]"):
        multiscale_rate_similarity(outside, (_event(0.0),), 1.0, (1.0,), (1.0,), 0.5, 0.5, 2)
