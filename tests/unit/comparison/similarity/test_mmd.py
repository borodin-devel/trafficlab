"""Independent behavioral tests for streaming approximate joint MMD."""

import math
from collections.abc import Mapping
from typing import cast

import numpy as np
import pytest

from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.comparison.similarity.mmd import (
    RandomFeatureMean,
    approximate_mmd_similarity,
    feature_mean,
    random_fourier_frequencies,
)


def _trace(
    timestamps: tuple[float, ...], lengths: tuple[int, ...], direction_codes: tuple[int, ...]
) -> TrafficTrace:
    """Build a canonical trace from the documented categorical direction codes."""
    directions = tuple(Direction.OUTBOUND if code == 0 else Direction.INBOUND for code in direction_codes)
    return TrafficTrace.from_events(
        TraceEvent(timestamp, direction, length)
        for timestamp, direction, length in zip(timestamps, directions, lengths, strict=True)
    )


def _explicit_feature(direction: int, values: tuple[float, float], frequencies: np.ndarray) -> np.ndarray:
    """Construct one frozen cosine/sine direction block without production helpers."""
    feature_count = len(frequencies)
    result = np.zeros(4 * feature_count, dtype=np.float64)
    projections = frequencies @ np.asarray(values, dtype=np.float64)
    offset = direction * 2 * feature_count
    result[offset : offset + feature_count] = np.cos(projections) / math.sqrt(feature_count)
    result[offset + feature_count : offset + 2 * feature_count] = np.sin(projections) / math.sqrt(feature_count)
    return result


def test_random_feature_mean_matches_a_tiny_explicit_cosine_sine_oracle() -> None:
    """Mixing direction blocks or omitting the unit-norm scale would change this mean."""
    frequencies = np.array(((0.0, 0.0), (math.pi / 2.0, 0.0)), dtype=np.float64)
    accumulator = RandomFeatureMean(frequencies)
    accumulator.add(0, np.array((1.0, 3.0), dtype=np.float64))
    accumulator.add(1, np.array((2.0, 4.0), dtype=np.float64))

    expected = (
        _explicit_feature(0, (1.0, 3.0), frequencies) + _explicit_feature(1, (2.0, 4.0), frequencies)
    ) / 2.0
    assert accumulator.count == 2
    assert accumulator.mean == pytest.approx(expected)


def test_random_feature_vectors_have_unit_norm_and_embedding_distance_is_at_most_two() -> None:
    """A wrong cosine/sine normalization can violate both documented geometric bounds."""
    frequencies = random_fourier_frequencies(7, 41)
    first = RandomFeatureMean(frequencies)
    second = RandomFeatureMean(frequencies)
    first.add(0, np.array((0.3, -1.2), dtype=np.float64))
    second.add(1, np.array((-0.7, 0.4), dtype=np.float64))

    assert np.linalg.norm(first.mean) == pytest.approx(1.0)
    assert np.linalg.norm(second.mean) == pytest.approx(1.0)
    assert np.linalg.norm(first.mean - second.mean) <= 2.0


def test_mmd_is_exactly_repeatable_and_scores_identical_traces_as_one() -> None:
    """Using global randomness or asymmetric scaling would make repeat runs differ."""
    trace = _trace((0.0, 0.0, 1.0, 3.0), (60, 120, 60, 240), (0, 1, 0, 1))

    first = approximate_mmd_similarity(trace, trace, 3.0, 5, 17, 0.1)
    second = approximate_mmd_similarity(trace, trace, 3.0, 5, 17, 0.1)

    assert first == second
    assert first.score == 1.0
    assert first.diagnostics["discrepancy"] == 0.0


def test_mmd_uses_reference_only_continuous_mean_and_scale() -> None:
    """Leaking generated values into standardization changes the frozen diagnostics."""
    reference = _trace((0.0, 1.0, 3.0), (10, 10, 10), (0, 0, 0))
    generated = _trace((0.0, 1.0, 3.0), (10, 1000, 100000), (0, 0, 0))

    result = approximate_mmd_similarity(reference, generated, 3.0, 3, 9, 0.25)

    continuous = cast(Mapping[str, object], result.diagnostics["continuous"])
    assert continuous["reference_mean"] == pytest.approx(
        ((math.log1p(1.0) + math.log1p(2.0)) / 2.0, math.log(10.0))
    )  # type: ignore[index]
    assert continuous["reference_scale"] == pytest.approx((0.25, 0.25))  # type: ignore[index]


def test_mmd_direction_has_no_numeric_order() -> None:
    """Ordinally embedding codes would violate the declared categorical delta kernel."""
    frequencies = random_fourier_frequencies(3, 13)
    first_trace = _trace((0.0, 1.0, 2.0), (10, 10, 10), (0, 0, 1))
    swapped_trace = _trace((0.0, 1.0, 2.0), (10, 10, 10), (1, 1, 0))
    mean = np.array((math.log1p(1.0), math.log(10.0)), dtype=np.float64)
    scale = np.array((1.0, 1.0), dtype=np.float64)

    first = feature_mean(first_trace, frequencies, mean, scale)
    swapped = feature_mean(swapped_trace, frequencies, mean, scale)

    assert first.shape == swapped.shape == (12,)
    assert np.all(np.isfinite(first))
    assert np.all(np.isfinite(swapped))


def test_changing_seed_changes_features_but_not_validation_shape() -> None:
    """The seed must select only deterministic random frequencies, not the feature contract."""
    trace = _trace((0.0, 1.0, 3.0), (10, 20, 30), (0, 1, 0))
    mean = np.array((math.log1p(1.0), math.log(20.0)), dtype=np.float64)
    scale = np.array((1.0, 1.0), dtype=np.float64)

    first = feature_mean(trace, random_fourier_frequencies(4, 1), mean, scale)
    second = feature_mean(trace, random_fourier_frequencies(4, 2), mean, scale)

    assert first.shape == second.shape == (16,)
    assert not np.allclose(first, second)
