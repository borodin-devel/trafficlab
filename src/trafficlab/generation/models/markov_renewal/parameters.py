"""Empirical Markov renewal traffic model with observable direction-size states."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import cast

import numpy as np
from numpy.typing import NDArray

from trafficlab.common.config import FloatBounds, IntegerBounds, MarkovRenewalConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import TrafficTrace
from trafficlab.generation.models.common import Gene

ROW_TOLERANCE = 1e-12
MINIMUM_FRAME_LENGTH = 14
MAXIMUM_FRAME_LENGTH = 2**32 - 1


def invalid_markov(detail: str, *, corrective_action: str) -> TrafficlabError:
    return TrafficlabError(detail, corrective_action=corrective_action)


def type7_quantile(values: Sequence[int | float], q: float) -> float:
    """Return the Hyndman--Fan Type 7 quantile of one nonempty finite sample."""
    try:
        sample = tuple(values)
    except TypeError as error:
        raise invalid_markov(
            "invalid quantile sample",
            corrective_action="provide a nonempty finite numerical sample and a quantile in [0, 1]",
        ) from error
    if (
        not sample
        or type(q) is not float
        or not math.isfinite(q)
        or not 0.0 <= q <= 1.0
        or any(type(value) not in (int, float) or not math.isfinite(value) for value in sample)
    ):
        raise invalid_markov(
            "invalid quantile sample or level",
            corrective_action="provide a nonempty finite numerical sample and a quantile in [0, 1]",
        )
    return float(np.quantile(np.asarray(sample, dtype=np.float64), q, method="linear"))


def type7_boundaries(frame_lengths: NDArray[np.uint32], quantiles: tuple[float, float]) -> NDArray[np.float64]:
    """Return the two Type 7 frame-length boundaries as a float64 vector."""
    if type(frame_lengths) is not np.ndarray or frame_lengths.dtype != np.dtype(np.uint32):
        raise ValueError("frame lengths must be a uint32 NumPy array")
    if frame_lengths.ndim != 1 or len(frame_lengths) == 0 or np.any(frame_lengths == 0):
        raise ValueError("frame lengths must be a nonempty one-dimensional array of positive values")
    if type(quantiles) is not tuple or len(quantiles) != 2:
        raise ValueError("quantiles must be exactly two finite increasing floats in (0, 1)")
    q1, q2 = quantiles
    if (
        type(q1) is not float
        or type(q2) is not float
        or not math.isfinite(q1)
        or not math.isfinite(q2)
        or not 0.0 < q1 < q2 < 1.0
    ):
        raise ValueError("quantiles must be exactly two finite increasing floats in (0, 1)")
    return np.asarray(np.quantile(frame_lengths, quantiles, method="linear"), dtype=np.float64)


def size_bin(frame_length: int, lower_threshold: float, upper_threshold: float) -> int:
    """Map a frame length to one of three bins using inclusive upper comparisons."""
    if type(frame_length) is not int:
        raise TypeError("frame_length must be an exact integer")
    if (
        type(lower_threshold) is not float
        or type(upper_threshold) is not float
        or not math.isfinite(lower_threshold)
        or not math.isfinite(upper_threshold)
        or lower_threshold >= upper_threshold
    ):
        raise ValueError("size thresholds must be finite increasing floats")
    if frame_length <= lower_threshold:
        return 0
    if frame_length <= upper_threshold:
        return 1
    return 2


def _validate_float_bounds(value: object, *, name: str, lower_limit: float | None = None) -> FloatBounds:
    if (
        type(value) is not FloatBounds
        or type(value.lower) is not float
        or type(value.upper) is not float
        or not math.isfinite(value.lower)
        or not math.isfinite(value.upper)
        or value.lower >= value.upper
        or (lower_limit is not None and value.lower < lower_limit)
    ):
        raise invalid_markov(
            f"invalid Markov renewal {name} bounds",
            corrective_action="provide finite ordered bounds satisfying the Markov renewal chromosome domain",
        )
    return value


def _validate_bounds(bounds: object) -> MarkovRenewalConfig:
    if type(bounds) is not MarkovRenewalConfig:
        raise invalid_markov(
            "invalid Markov renewal bounds",
            corrective_action="provide configured q1, q2, alpha, r, and c_t bounds",
        )
    q1 = _validate_float_bounds(bounds.q1, name="q1")
    q2 = _validate_float_bounds(bounds.q2, name="q2")
    _validate_float_bounds(bounds.alpha, name="alpha", lower_limit=0.0)
    c_t = _validate_float_bounds(bounds.c_t, name="c_t")
    if q1.lower <= 0.0 or q1.upper >= 1.0 or q2.lower <= 0.0 or q2.upper >= 1.0 or c_t.lower <= 0.0:
        raise invalid_markov(
            "invalid Markov renewal bounds",
            corrective_action="keep quantiles in (0, 1), alpha nonnegative, and c_t positive",
        )
    r = bounds.r
    if (
        type(r) is not IntegerBounds
        or type(r.lower) is not int
        or type(r.upper) is not int
        or r.lower < 1
        or r.lower >= r.upper
    ):
        raise invalid_markov(
            "invalid Markov renewal r bounds",
            corrective_action="provide inclusive ordered integer r bounds starting at one or greater",
        )
    return bounds


def canonical_genes(genes: Sequence[Gene], bounds: object) -> tuple[float, float, float, int, float]:
    checked_bounds = _validate_bounds(bounds)
    try:
        values = tuple(genes)
    except TypeError as error:
        raise invalid_markov(
            "invalid Markov renewal genes",
            corrective_action="provide exactly q1, q2, alpha, r, and c_t finite numerical genes",
        ) from error
    if (
        len(values) != 5
        or any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
        or any(type(values[index]) is not float for index in (0, 1, 2, 4))
    ):
        raise invalid_markov(
            "invalid Markov renewal genes",
            corrective_action="provide exact finite float q1, q2, alpha, c_t genes and a numerical r gene",
        )
    q1_raw, q2_raw = sorted((cast(float, values[0]), cast(float, values[1])))
    q1 = min(max(q1_raw, checked_bounds.q1.lower), checked_bounds.q1.upper)
    q2 = min(max(q2_raw, checked_bounds.q2.lower), checked_bounds.q2.upper)
    alpha_raw = cast(float, values[2])
    alpha = min(max(alpha_raw, checked_bounds.alpha.lower), checked_bounds.alpha.upper)
    r_raw = values[3]
    rounded_r = math.floor(r_raw + 0.5)
    minimum_support = min(max(rounded_r, checked_bounds.r.lower), checked_bounds.r.upper)
    c_t_raw = cast(float, values[4])
    time_scale = min(max(c_t_raw, checked_bounds.c_t.lower), checked_bounds.c_t.upper)
    if not 0.0 < q1 < q2 < 1.0:
        raise invalid_markov(
            "invalid repaired Markov renewal quantiles",
            corrective_action="use named quantile bounds that preserve strict q1 less than q2 order",
        )
    return (q1, q2, alpha, minimum_support, time_scale)


def repair_with_trace(
    genes: Sequence[Gene], bounds: object, trace: TrafficTrace
) -> tuple[float, float, float, int, float]:
    repaired = canonical_genes(genes, bounds)
    thresholds = type7_boundaries(trace.frame_lengths, (repaired[0], repaired[1]))
    if thresholds[0] >= thresholds[1]:
        raise invalid_markov(
            "invalid Markov renewal thresholds: repaired quantiles produce duplicate thresholds",
            corrective_action="provide a reference with enough distinct frame lengths for three bins",
        )
    return repaired
