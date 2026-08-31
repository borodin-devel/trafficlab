from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from trafficlab_dashboard.aspects.base import LineSeries
from trafficlab_dashboard.aspects.numerics import (
    choose_time_bin_width,
    ecdf_points,
    minmax_envelope,
    shared_histogram_edges,
    shared_time_edges,
)


def _ordered_coordinates(values: Sequence[float]) -> np.ndarray:
    total = 0.0
    coordinates: list[float] = []
    for value in values:
        total += value
        coordinates.append(total)
    return np.array(coordinates, dtype=np.float64)


_finite_nonnegative = st.floats(min_value=0.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)

_exact_time_width = st.sampled_from((0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0))


@st.composite
def _ordered_xy(draw: st.DrawFn) -> tuple[np.ndarray, np.ndarray]:
    size = draw(st.integers(min_value=1, max_value=60))
    increments = draw(st.lists(_finite_nonnegative, min_size=size, max_size=size))
    y_values = draw(
        st.lists(
            st.floats(min_value=-10_000.0, max_value=10_000.0, allow_nan=False, allow_infinity=False),
            min_size=size,
            max_size=size,
        )
    )
    return _ordered_coordinates(increments), np.array(y_values, dtype=np.float64)


def test_choose_time_bin_width_uses_125_sequence_and_target_range() -> None:
    width = choose_time_bin_width(window=53.975692, minimum_bins=500, maximum_bins=1500)

    assert width == 0.05
    assert 500 <= math.ceil(53.975692 / width) <= 1500


def test_shared_time_edges_preserve_a_closed_window_and_snap_boundary_drift() -> None:
    drifted_window = float(np.nextafter(0.3, 1.0))

    drifted_edges = shared_time_edges(drifted_window, 0.1)
    uneven_edges = shared_time_edges(2.3, 1.0)

    assert drifted_edges.tolist() == pytest.approx([0.0, 0.1, 0.2, drifted_window])
    assert uneven_edges.tolist() == pytest.approx([0.0, 1.0, 2.0, 2.3])


def test_shared_time_edges_replaces_a_snapped_terminal_edge_for_exact_float_boundaries() -> None:
    window = 0.3
    width = 0.1

    edges = shared_time_edges(window, width)
    histogram, returned_edges = np.histogram(np.array([0.0, 0.1, window], dtype=np.float64), bins=edges)

    assert edges.tolist() == pytest.approx([0.0, 0.1, 0.2, window])
    assert np.all(np.diff(edges) > 0.0)
    assert np.all(edges >= 0.0)
    assert np.all(edges <= window)
    assert edges[-1] == window
    assert np.array_equal(returned_edges, edges)
    assert histogram.tolist() == [1, 1, 1]


def test_shared_histogram_edges_use_both_loaded_samples() -> None:
    reference = np.array([64.0, 64.0, 128.0], dtype=np.float64)
    generated = np.array([256.0, 512.0], dtype=np.float64)

    edges = shared_histogram_edges(reference, generated)

    assert np.array_equal(
        edges,
        np.histogram_bin_edges(np.array([64.0, 64.0, 128.0, 256.0, 512.0], dtype=np.float64), bins="fd"),
    )


def test_ecdf_points_sort_stably_and_retain_ties() -> None:
    sample = np.array([2.0, 1.0, 1.0, 3.0], dtype=np.float64)

    points = ecdf_points(sample, maximum_points=10)

    assert isinstance(points, LineSeries)
    assert points.sample_count == 4
    assert points.x.tolist() == [1.0, 1.0, 2.0, 3.0]
    assert points.y.tolist() == pytest.approx([0.25, 0.5, 0.75, 1.0])


def test_minmax_envelope_preserves_bucket_extrema_and_endpoints() -> None:
    x = np.arange(8, dtype=np.float64)
    y = np.array([0, 5, 1, 4, 2, 9, 3, 8], dtype=np.float64)

    reduced = minmax_envelope(x, y, maximum_points=4)

    assert reduced.x[[0, -1]].tolist() == [0.0, 7.0]
    assert set(reduced.y.tolist()) >= {0.0, 9.0}
    assert len(reduced.x) <= 4


@given(
    sample=st.lists(_finite_nonnegative, min_size=1, max_size=60),
    maximum_points=st.integers(min_value=2, max_value=25),
)
def test_ecdf_reduction_stays_monotone_and_retains_endpoints(sample: list[float], maximum_points: int) -> None:
    sample_array = np.array(sample, dtype=np.float64)

    points = ecdf_points(sample_array, maximum_points=maximum_points)

    assert points.sample_count == len(sample)
    assert len(points.x) <= maximum_points
    assert points.x[0] == np.sort(sample_array, kind="mergesort")[0]
    assert points.x[-1] == np.sort(sample_array, kind="mergesort")[-1]
    assert points.y[0] == pytest.approx(1.0 / len(sample))
    assert points.y[-1] == 1.0
    assert np.all(np.diff(points.x) >= 0.0)
    assert np.all(np.diff(points.y) >= 0.0)


@given(width=_exact_time_width, bins=st.integers(min_value=1, max_value=100))
def test_shared_time_edges_exact_multiple_windows_stay_monotone_bounded_and_histogram_safe(
    width: float, bins: int
) -> None:
    window = float(width * bins)
    sample = np.array([0.0, window / 2.0, window], dtype=np.float64)

    edges = shared_time_edges(window, width)
    counts, returned_edges = np.histogram(sample, bins=edges)

    assert np.all(np.diff(edges) > 0.0)
    assert np.all(edges >= 0.0)
    assert np.all(edges <= window)
    assert edges[-1] == window
    assert np.array_equal(returned_edges, edges)
    assert int(np.sum(counts)) == len(sample)


@given(data=_ordered_xy(), maximum_points=st.integers(min_value=4, max_value=24))
def test_envelope_reduction_preserves_order_limits_and_extrema(
    data: tuple[np.ndarray, np.ndarray], maximum_points: int
) -> None:
    x, y = data

    reduced = minmax_envelope(x, y, maximum_points=maximum_points)

    assert reduced.sample_count == len(x)
    assert len(reduced.x) <= maximum_points
    assert reduced.x[0] == x[0]
    assert reduced.x[-1] == x[-1]
    assert np.all(np.diff(reduced.x) >= 0.0)
    assert np.min(reduced.y) == np.min(y)
    assert np.max(reduced.y) == np.max(y)
