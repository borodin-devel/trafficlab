"""Unit tests for documented autocorrelation similarity."""

import math
from collections.abc import Callable, Iterable
from typing import cast

import numpy as np
import pytest
from numpy.typing import NDArray

import trafficlab.similarity.autocorrelation as autocorrelation_module
from trafficlab.errors import TrafficlabError
from trafficlab.similarity.autocorrelation import (
    autocorrelation_similarity,
    sample_autocorrelation,
    weighted_acf_discrepancy,
)
from trafficlab.trace import Direction, TraceEvent


def _events(*timestamps: float, lengths: tuple[int, ...] | None = None) -> tuple[TraceEvent, ...]:
    """Create outbound canonical events for compact hand calculations."""
    event_lengths = lengths if lengths is not None else tuple(100 for _ in timestamps)
    return tuple(
        TraceEvent(timestamp=timestamp, direction=Direction.OUTBOUND, frame_length=length)
        for timestamp, length in zip(timestamps, event_lengths, strict=True)
    )


def _invalid_event(*, timestamp: object = 0.0, frame_length: object = 100) -> TraceEvent:
    """Construct a malformed event to prove metric trace validation."""
    event = object.__new__(TraceEvent)
    object.__setattr__(event, "timestamp", timestamp)
    object.__setattr__(event, "direction", Direction.OUTBOUND)
    object.__setattr__(event, "frame_length", frame_length)
    return event


def test_sample_autocorrelation_uses_documented_whole_series_mean_estimator() -> None:
    assert sample_autocorrelation([1, 2, 1], 1) == pytest.approx(-2.0 / 3.0)


def test_sample_autocorrelation_returns_zero_for_a_constant_series() -> None:
    assert sample_autocorrelation([4, 4, 4], 1) == 0.0


def test_sample_autocorrelation_returns_zero_for_documented_three_value_example() -> None:
    assert sample_autocorrelation([1, 2, 3], 1) == 0.0


def test_sample_autocorrelation_matches_scalar_value_through_centered_numpy_dot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    dot = cast(Callable[[NDArray[np.float64], NDArray[np.float64]], np.float64], autocorrelation_module.np.dot)

    def counted_dot(left: NDArray[np.float64], right: NDArray[np.float64]) -> np.float64:
        nonlocal calls
        calls += 1
        return dot(left, right)

    monkeypatch.setattr(autocorrelation_module.np, "dot", counted_dot)

    assert sample_autocorrelation([1.0, 2.0, 1.0, 3.0], 1) == pytest.approx(-21.0 / 44.0)
    assert calls == 2


def test_sample_autocorrelation_preserves_subnormal_serial_dependence() -> None:
    assert sample_autocorrelation([5e-324, 0.0, 5e-324], 1) == pytest.approx(-2.0 / 3.0)


def test_sample_autocorrelation_returns_zero_for_a_large_constant_series() -> None:
    assert sample_autocorrelation([1e308, 1e308, 1e308], 1) == 0.0


def test_sample_autocorrelation_returns_zero_for_an_exact_large_binary_constant_series() -> None:
    assert sample_autocorrelation([2.7112132528354864e255] * 3, 1) == 0.0


def test_sample_autocorrelation_handles_a_large_finite_nonconstant_series() -> None:
    assert sample_autocorrelation([1e308, 0.0, 1e308], 1) == pytest.approx(-2.0 / 3.0)


def test_sample_autocorrelation_translates_huge_integer_conversion_overflow() -> None:
    with pytest.raises(TrafficlabError):
        sample_autocorrelation([10**10000, 0, 1], 1)


@pytest.mark.parametrize(
    ("values", "lag"),
    [([1, math.inf, 2], 1), ([1, 2], 0), ([1, 2], 2), ([1, 2], 1.0)],
)
def test_sample_autocorrelation_rejects_nonfinite_values_and_invalid_lags(values: list[object], lag: object) -> None:
    with pytest.raises(TrafficlabError):
        sample_autocorrelation(values, lag)


def test_sample_autocorrelation_rejects_a_noniterable_sample() -> None:
    with pytest.raises(TrafficlabError):
        sample_autocorrelation(cast(Iterable[object], 1), 1)


def test_sample_autocorrelation_rejects_a_nonfinite_numpy_column() -> None:
    """The direct array path must retain finite-value validation."""
    with pytest.raises(TrafficlabError, match="finite numbers"):
        sample_autocorrelation(np.array([1.0, math.inf], dtype=np.float64), 1)


def test_sample_autocorrelation_translates_numpy_conversion_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A numeric-library conversion failure must retain the stable Trafficlab boundary."""

    def fail_asarray(_values: object, *, dtype: object) -> NDArray[np.float64]:
        raise OverflowError(dtype)

    monkeypatch.setattr(autocorrelation_module.np, "asarray", fail_asarray)
    with pytest.raises(TrafficlabError, match="evaluated safely"):
        sample_autocorrelation(np.array([1, 2], dtype=np.uint32), 1)


def test_sample_autocorrelation_translates_numpy_dot_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed centered dot product must not escape as a raw NumPy exception."""

    def fail_dot(_left: NDArray[np.float64], _right: NDArray[np.float64]) -> np.float64:
        raise ValueError("dot failed")

    monkeypatch.setattr(autocorrelation_module.np, "dot", fail_dot)
    with pytest.raises(TrafficlabError, match="evaluated safely"):
        sample_autocorrelation([1.0, 2.0, 1.0], 1)


def test_sample_autocorrelation_retains_zero_denominator_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero numerical denominator retains the documented constant-series convention."""

    def zero_dot(_left: NDArray[np.float64], _right: NDArray[np.float64]) -> np.float64:
        return np.float64(0.0)

    monkeypatch.setattr(autocorrelation_module.np, "dot", zero_dot)
    assert sample_autocorrelation([1.0, 2.0, 1.0], 1) == 0.0


def test_autocorrelation_similarity_returns_identical_score_and_complete_nested_diagnostics() -> None:
    reference = _events(0.0, 1.0, 3.0, 6.0, lengths=(10, 20, 10, 20))
    generated = _events(5.0, 6.0, 8.0, 11.0, lengths=(10, 20, 10, 20))

    result = autocorrelation_similarity(reference, generated, 8.0, (1,), (1.0,), 0.5, 0.5)

    assert result.score == 1.0
    assert result.diagnostics == {
        "observation_window_seconds": 8.0,
        "lags": (1,),
        "lag_weights": (1.0,),
        "feature_weights": {"iat": 0.5, "size": 0.5},
        "iat": {
            "reference_sample_count": 3,
            "generated_sample_count": 3,
            "reference_acf": (0.0,),
            "generated_acf": (0.0,),
            "absolute_differences": (0.0,),
            "discrepancy": 0.0,
        },
        "size": {
            "reference_sample_count": 4,
            "generated_sample_count": 4,
            "reference_acf": (-0.75,),
            "generated_acf": (-0.75,),
            "absolute_differences": (0.0,),
            "discrepancy": 0.0,
        },
        "discrepancy": 0.0,
    }


def test_autocorrelation_similarity_returns_every_hand_calculated_two_lag_diagnostic() -> None:
    reference = _events(0.0, 1.0, 3.0, 6.0, 10.0, lengths=(1, 2, 1, 2, 1))
    generated = _events(0.0, 1.0, 4.0, 6.0, 10.0, lengths=(1, 1, 2, 2, 1))

    result = autocorrelation_similarity(reference, generated, 10.0, (1, 2), (0.25, 0.75), 0.4, 0.6)

    assert result.score == pytest.approx(111.0 / 200.0)
    assert result.diagnostics["observation_window_seconds"] == 10.0
    assert result.diagnostics["lags"] == (1, 2)
    assert result.diagnostics["lag_weights"] == (0.25, 0.75)
    assert result.diagnostics["feature_weights"] == {"iat": 0.4, "size": 0.6}
    assert result.diagnostics["iat"] == {
        "reference_sample_count": 4,
        "generated_sample_count": 4,
        "reference_acf": pytest.approx((1.0 / 4.0, -3.0 / 10.0)),
        "generated_acf": pytest.approx((-7.0 / 20.0, 3.0 / 10.0)),
        "absolute_differences": pytest.approx((3.0 / 5.0, 3.0 / 5.0)),
        "discrepancy": pytest.approx(3.0 / 10.0),
    }
    assert result.diagnostics["size"] == {
        "reference_sample_count": 5,
        "generated_sample_count": 5,
        "reference_acf": pytest.approx((-4.0 / 5.0, 17.0 / 30.0)),
        "generated_acf": pytest.approx((1.0 / 30.0, -3.0 / 5.0)),
        "absolute_differences": pytest.approx((5.0 / 6.0, 7.0 / 6.0)),
        "discrepancy": pytest.approx(13.0 / 24.0),
    }
    assert result.diagnostics["discrepancy"] == pytest.approx(89.0 / 200.0)


def test_autocorrelation_similarity_compares_zero_constant_acf_to_nonconstant_acf() -> None:
    result = autocorrelation_similarity(
        _events(0.0, 1.0, 2.0, 3.0, lengths=(7, 7, 7, 7)),
        _events(0.0, 1.0, 3.0, 6.0, lengths=(10, 20, 10, 20)),
        6.0,
        (1,),
        (1.0,),
        0.0,
        1.0,
    )

    assert result.diagnostics["size"] == {
        "reference_sample_count": 4,
        "generated_sample_count": 4,
        "reference_acf": (0.0,),
        "generated_acf": (-0.75,),
        "absolute_differences": (0.75,),
        "discrepancy": 0.375,
    }
    assert result.diagnostics["discrepancy"] == 0.375
    assert result.score == 0.625


def test_weighted_acf_discrepancy_reaches_documented_maximum_for_synthetic_opposites() -> None:
    assert weighted_acf_discrepancy((-1.0,), (1.0,), (1.0,)) == 1.0


def test_weighted_acf_discrepancy_translates_arithmetic_overflow() -> None:
    with pytest.raises(TrafficlabError):
        weighted_acf_discrepancy((1.0, 1.0), (-1.0, -1.0), (1e308, 1e308))


@pytest.mark.parametrize(
    "reference_acf",
    [(math.nan,), (-2.0,)],
)
def test_weighted_acf_discrepancy_rejects_nonfinite_or_materially_out_of_range_results(
    reference_acf: tuple[float, ...],
) -> None:
    with pytest.raises(TrafficlabError):
        weighted_acf_discrepancy(reference_acf, (1.0,), (1.0,))


@pytest.mark.parametrize("lags", [(0,), (1, 1), (3,)])
def test_autocorrelation_similarity_rejects_nonpositive_duplicate_or_too_large_lags(lags: tuple[int, ...]) -> None:
    trace = _events(0.0, 1.0, 3.0, 6.0)

    with pytest.raises(TrafficlabError):
        autocorrelation_similarity(trace, trace, 6.0, lags, tuple(1.0 for _ in lags), 0.5, 0.5)


def test_autocorrelation_similarity_requires_lag_and_weight_vectors_of_equal_length() -> None:
    trace = _events(0.0, 1.0, 3.0, 6.0)

    with pytest.raises(TrafficlabError):
        autocorrelation_similarity(trace, trace, 6.0, (1, 2), (1.0,), 0.5, 0.5)


def test_autocorrelation_similarity_translates_lag_weight_summation_overflow() -> None:
    trace = _events(0.0, 1.0, 3.0, 6.0)

    with pytest.raises(TrafficlabError):
        autocorrelation_similarity(trace, trace, 6.0, (1, 2), (1e308, 1e308), 0.5, 0.5)


def test_autocorrelation_similarity_rejects_noniterable_lags_or_weights() -> None:
    trace = _events(0.0, 1.0, 3.0, 6.0)

    with pytest.raises(TrafficlabError):
        autocorrelation_similarity(trace, trace, 6.0, cast(Iterable[object], 1), (1.0,), 0.5, 0.5)
    with pytest.raises(TrafficlabError):
        autocorrelation_similarity(trace, trace, 6.0, (1,), cast(Iterable[object], 1), 0.5, 0.5)


@pytest.mark.parametrize(
    ("lag_weights", "iat_weight", "size_weight"),
    [((0.9,), 0.5, 0.5), ((-1.0,), 0.5, 0.5), ((1.0,), 0.6, 0.5), ((math.inf,), 0.5, 0.5)],
)
def test_autocorrelation_similarity_rejects_invalid_normalized_weights(
    lag_weights: tuple[float, ...], iat_weight: float, size_weight: float
) -> None:
    trace = _events(0.0, 1.0, 3.0, 6.0)

    with pytest.raises(TrafficlabError):
        autocorrelation_similarity(trace, trace, 6.0, (1,), lag_weights, iat_weight, size_weight)


@pytest.mark.parametrize(
    ("weight", "accepted"),
    [
        (math.nextafter(1.0 - 1e-12, math.inf), True),
        (math.nextafter(1.0 - 1e-12, -math.inf), False),
        (math.nextafter(1.0 + 1e-12, -math.inf), True),
        (math.nextafter(1.0 + 1e-12, math.inf), False),
    ],
)
def test_autocorrelation_similarity_uses_absolute_tolerance_for_lag_weights(weight: float, accepted: bool) -> None:
    trace = _events(0.0, 1.0, 3.0, 6.0)

    if accepted:
        assert autocorrelation_similarity(trace, trace, 6.0, (1,), (weight,), 0.5, 0.5).score == 1.0
    else:
        with pytest.raises(TrafficlabError):
            autocorrelation_similarity(trace, trace, 6.0, (1,), (weight,), 0.5, 0.5)


@pytest.mark.parametrize(
    ("weight", "accepted"),
    [
        (math.nextafter(1.0 - 1e-12, math.inf), True),
        (math.nextafter(1.0 - 1e-12, -math.inf), False),
        (math.nextafter(1.0 + 1e-12, -math.inf), True),
        (math.nextafter(1.0 + 1e-12, math.inf), False),
    ],
)
def test_autocorrelation_similarity_uses_absolute_tolerance_for_feature_weights(weight: float, accepted: bool) -> None:
    trace = _events(0.0, 1.0, 3.0, 6.0)

    if accepted:
        assert autocorrelation_similarity(trace, trace, 6.0, (1,), (1.0,), weight, 0.0).score == 1.0
    else:
        with pytest.raises(TrafficlabError):
            autocorrelation_similarity(trace, trace, 6.0, (1,), (1.0,), weight, 0.0)


def test_autocorrelation_similarity_rejects_lag_one_for_two_packets_due_to_one_iat() -> None:
    trace = _events(0.0, 1.0)

    with pytest.raises(TrafficlabError):
        autocorrelation_similarity(trace, trace, 1.0, (1,), (1.0,), 0.5, 0.5)


@pytest.mark.parametrize(
    "reference",
    [
        _events(0.0),
        cast(Iterable[TraceEvent], 1),
        cast(Iterable[TraceEvent], [object()]),
        [_invalid_event(timestamp=math.nan), _invalid_event(timestamp=1.0)],
        [_invalid_event(timestamp=1.0), _invalid_event(timestamp=0.0)],
        [_invalid_event(frame_length=0), _invalid_event()],
    ],
)
def test_autocorrelation_similarity_rejects_invalid_canonical_traces(reference: Iterable[TraceEvent]) -> None:
    with pytest.raises(TrafficlabError):
        autocorrelation_similarity(reference, _events(0.0, 1.0, 3.0), 3.0, (1,), (1.0,), 0.5, 0.5)


def test_autocorrelation_similarity_translates_incomplete_trace_events_to_trafficlab_error() -> None:
    event = object.__new__(TraceEvent)
    object.__setattr__(event, "timestamp", 0.0)

    with pytest.raises(TrafficlabError):
        autocorrelation_similarity([event, _events(1.0)[0]], _events(0.0, 1.0, 3.0), 3.0, (1,), (1.0,), 0.5, 0.5)


def test_autocorrelation_similarity_allows_unequal_trace_lengths_when_lags_fit_all_four_samples() -> None:
    reference = _events(0.0, 1.0, 3.0, 6.0, lengths=(1, 2, 1, 2))
    generated = _events(0.0, 1.0, 3.0, 6.0, 10.0, lengths=(1, 2, 1, 2, 1))

    result = autocorrelation_similarity(reference, generated, 10.0, (1, 2), (0.5, 0.5), 0.5, 0.5)

    assert result.diagnostics["iat"]["reference_sample_count"] == 3  # type: ignore[index]
    assert result.diagnostics["iat"]["generated_sample_count"] == 4  # type: ignore[index]
    assert result.diagnostics["size"]["reference_sample_count"] == 4  # type: ignore[index]
    assert result.diagnostics["size"]["generated_sample_count"] == 5  # type: ignore[index]
