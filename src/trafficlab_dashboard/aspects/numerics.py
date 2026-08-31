from __future__ import annotations

import math
from typing import cast

import numpy as np
from numpy.typing import NDArray

from trafficlab.comparison.similarity.multiscale import snap_near_integer
from trafficlab_dashboard.aspects.base import LineSeries


def _require_positive_float(value: float, *, name: str) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be a finite positive float")
    return value


def _require_positive_int(value: int, *, name: str, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")
    return value


def _validated_float_array(values: object, *, name: str) -> NDArray[np.float64]:
    if not isinstance(values, np.ndarray):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError(f"{name} must be a NumPy array")
    array = np.asarray(cast(NDArray[np.float64], values), dtype=np.float64)
    if array.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain only finite values")
    return array


def _immutable_float_array(values: NDArray[np.float64]) -> NDArray[np.float64]:
    return np.frombuffer(np.array(values, dtype=np.float64, copy=True, order="C").tobytes(), dtype=np.float64)


def choose_time_bin_width(window: float, minimum_bins: int, maximum_bins: int) -> float:
    validated_window = _require_positive_float(window, name="window")
    validated_minimum = _require_positive_int(minimum_bins, name="minimum_bins")
    validated_maximum = _require_positive_int(maximum_bins, name="maximum_bins")
    if validated_minimum > validated_maximum:
        raise ValueError("minimum_bins must be no greater than maximum_bins")

    exponent = math.floor(math.log10(validated_window / validated_maximum)) - 1
    while True:
        scale = math.pow(10.0, exponent)
        for multiplier in (1.0, 2.0, 5.0):
            width = float(multiplier * scale)
            if width <= 0.0:
                continue
            bin_count = math.ceil(snap_near_integer(validated_window / width))
            if bin_count <= validated_maximum:
                return width
        exponent += 1


def shared_time_edges(window: float, width: float) -> NDArray[np.float64]:
    validated_window = _require_positive_float(window, name="window")
    validated_width = _require_positive_float(width, name="width")

    quotient = snap_near_integer(validated_window / validated_width)
    whole_bins = math.floor(quotient)
    has_partial_bin = quotient != float(whole_bins)
    edges = [float(index) * validated_width for index in range(whole_bins + 1)]
    if not edges:
        edges = [0.0]
    if has_partial_bin:
        edges.append(validated_window)
    else:
        edges[-1] = validated_window
    return _immutable_float_array(np.array(edges, dtype=np.float64))


def shared_histogram_edges(
    reference: object,
    generated: object,
    *,
    logarithmic: bool = False,
) -> NDArray[np.float64]:
    reference_sample = _validated_float_array(reference, name="reference")
    generated_sample = _validated_float_array(generated, name="generated")
    combined = np.concatenate((reference_sample, generated_sample), dtype=np.float64)
    if len(combined) == 0:
        raise ValueError("combined histogram sample must be non-empty")
    if logarithmic:
        positive = combined[combined > 0.0]
        if len(positive) == 0:
            return _immutable_float_array(np.array([], dtype=np.float64))
        return _immutable_float_array(np.exp(np.histogram_bin_edges(np.log(positive), bins="fd")).astype(np.float64))
    return _immutable_float_array(np.histogram_bin_edges(combined.astype(np.float64), bins="fd").astype(np.float64))


def ecdf_points(sample: object, *, maximum_points: int) -> LineSeries:
    validated_sample = _validated_float_array(sample, name="sample")
    limit = _require_positive_int(maximum_points, name="maximum_points", minimum=2)
    if len(validated_sample) == 0:
        raise ValueError("sample must be non-empty")

    sorted_sample = np.sort(validated_sample, kind="mergesort")
    cumulative = np.arange(1, len(sorted_sample) + 1, dtype=np.float64) / float(len(sorted_sample))
    if len(sorted_sample) <= limit:
        x = sorted_sample
        y = cumulative
    else:
        indices = np.unique(np.linspace(0, len(sorted_sample) - 1, num=limit, dtype=np.int64))
        indices[0] = 0
        indices[-1] = len(sorted_sample) - 1
        x = sorted_sample[indices]
        y = cumulative[indices]
    return LineSeries(label="ECDF", x=x, y=y, sample_count=len(sorted_sample))


def minmax_envelope(x: object, y: object, *, maximum_points: int) -> LineSeries:
    x_values = _validated_float_array(x, name="x")
    y_values = _validated_float_array(y, name="y")
    limit = _require_positive_int(maximum_points, name="maximum_points", minimum=2)
    if len(x_values) != len(y_values):
        raise ValueError("x and y must have equal lengths")
    if len(x_values) == 0:
        raise ValueError("x and y must be non-empty")
    if np.any(np.diff(x_values) < 0.0):
        raise ValueError("x must be nondecreasing")
    if len(x_values) <= limit:
        return LineSeries(label="Envelope", x=x_values, y=y_values, sample_count=len(x_values))
    if limit == 2:
        indices = np.array([0, len(x_values) - 1], dtype=np.int64)
    else:
        interior_budget = limit - 2
        if interior_budget == 1:
            interior = y_values[1:-1]
            candidate = int(np.argmax(np.abs(interior - interior.mean()))) + 1 if len(interior) else 0
            indices = np.array([0, candidate, len(x_values) - 1], dtype=np.int64)
        else:
            interior_indices = np.arange(1, len(x_values) - 1, dtype=np.int64)
            bucket_count = max(1, interior_budget // 2)
            boundaries = np.linspace(0, len(interior_indices), num=bucket_count + 1, dtype=np.int64)
            selected: list[int] = [0]
            for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
                bucket = interior_indices[start:stop]
                if len(bucket) == 0:
                    continue
                bucket_y = y_values[bucket]
                low = int(bucket[np.argmin(bucket_y)])
                high = int(bucket[np.argmax(bucket_y)])
                selected.extend(sorted({low, high}, key=lambda index: x_values[index]))
            selected.append(len(x_values) - 1)
            indices = np.array(selected, dtype=np.int64)
    unique_indices = np.unique(indices)
    return LineSeries(
        label="Envelope", x=x_values[unique_indices], y=y_values[unique_indices], sample_count=len(x_values)
    )
