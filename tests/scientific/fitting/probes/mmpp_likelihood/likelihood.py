"""Likelihood owner for Validation Study tooling."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray
from scipy import linalg as scipy_linalg  # pyright: ignore[reportMissingTypeStubs]

from tests.scientific.fitting.probes.mmpp_likelihood.schema import OPTIMIZER_STARTS, PROBE_BOUNDS, Coordinates

if TYPE_CHECKING:
    from tests.scientific.fitting.probes.mmpp_likelihood.schema import (
        LikelihoodEvaluation,
        ProbeRateBounds,
        Rates,
        SimulationGenerationHistory,
    )


class _MatrixExponential(Protocol):
    def __call__(self, matrix: NDArray[np.float64]) -> NDArray[np.float64]: ...


expm = cast(_MatrixExponential, cast(Any, scipy_linalg).expm)


def rate_tuple(value: Sequence[float]) -> Rates:
    rates = tuple(value)
    if len(rates) != 4 or any(type(rate) is not float or not math.isfinite(rate) or rate <= 0.0 for rate in rates):
        raise ValueError("MMPP rates must contain four finite positive floats")
    q01, q10, lambda0, lambda1 = rates
    if lambda0 >= lambda1:
        raise ValueError("MMPP arrival rates must satisfy lambda0 < lambda1")
    return (q01, q10, lambda0, lambda1)


def _arrival_epoch(rates: Rates) -> NDArray[np.float64]:
    q01, q10, lambda0, lambda1 = rates
    log_weight0 = math.log(q10) + math.log(lambda0)
    log_weight1 = math.log(q01) + math.log(lambda1)
    maximum = max(log_weight0, log_weight1)
    weight0 = math.exp(log_weight0 - maximum)
    weight1 = math.exp(log_weight1 - maximum)
    total = weight0 + weight1
    return np.array((weight0 / total, weight1 / total), dtype=np.float64)


def build_observations(iats: Iterable[float], terminal_silence: float) -> tuple[tuple[float, ...], float]:
    values = tuple(iats)
    if any(type(value) is not float or not math.isfinite(value) or value < 0.0 for value in values):
        raise ValueError("MMPP IATs must be finite nonnegative floats")
    if type(terminal_silence) is not float or not math.isfinite(terminal_silence) or terminal_silence < 0.0:
        raise ValueError("MMPP terminal silence must be a finite nonnegative float")
    return (values, terminal_silence)


def mmpp_log_likelihood(iats: Iterable[float], terminal_silence: float, rates: Sequence[float]) -> float:
    """Return the scaled arrival-epoch likelihood with explicit terminal survival."""
    intervals, terminal = build_observations(iats, terminal_silence)
    q01, q10, lambda0, lambda1 = rate_tuple(rates)
    q = np.array(((-q01, q01), (q10, -q10)), dtype=np.float64)
    d1 = np.diag(np.array((lambda0, lambda1), dtype=np.float64))
    d0 = q - d1
    forward = _arrival_epoch((q01, q10, lambda0, lambda1))
    accumulated = 0.0
    for interval in intervals:
        forward = forward @ expm(d0 * interval) @ d1
        scale = float(np.sum(forward))
        if not np.all(np.isfinite(forward)) or not math.isfinite(scale) or scale <= 0.0:
            raise ValueError("MMPP forward scale must be finite and positive")
        forward = forward / scale
        accumulated += math.log(scale)
    survival = float(forward @ expm(d0 * terminal) @ np.ones(2, dtype=np.float64))
    if not math.isfinite(survival) or survival <= 0.0:
        raise ValueError("MMPP terminal survival must be finite and positive")
    result = accumulated + math.log(survival)
    if not math.isfinite(result):
        raise ValueError("MMPP log-likelihood must be finite")
    return result


def _log_interpolate(bounds: tuple[float, float], coordinate: float) -> float:
    lower, upper = bounds
    if coordinate == 0.0:
        return lower
    if coordinate == 1.0:
        return upper
    return math.exp(math.log(lower) + coordinate * (math.log(upper) - math.log(lower)))


def decode_rates(coordinates: Sequence[float], bounds: ProbeRateBounds) -> Rates:
    """Decode bounded log coordinates and a positive dynamic arrival-rate gap."""
    values = tuple(coordinates)
    if len(values) != 4 or any(
        type(value) is not float or not math.isfinite(value) or (not 0.0 <= value <= 1.0) for value in values
    ):
        raise ValueError("optimizer coordinates must be four finite floats in [0, 1]")
    first, second, third, fourth = values
    q01 = _log_interpolate(bounds.q01, first)
    q10 = _log_interpolate(bounds.q10, second)
    lambda0 = _log_interpolate(bounds.lambda0, third)
    gap = _log_interpolate((bounds.lambda1[0] - lambda0, bounds.lambda1[1] - lambda0), fourth)
    decoded = (q01, q10, lambda0, lambda0 + gap)
    if not bounds.lambda1[0] <= decoded[3] <= bounds.lambda1[1]:
        raise AssertionError("validated gap transform escaped the named lambda1 bounds")
    return decoded


_SCIPY_EFFECTIVE_STARTS = tuple(
    cast(Coordinates, tuple(0.5 + (coordinate - 0.5) for coordinate in start)) for start in OPTIMIZER_STARTS
)

COMMON_START_RATES = tuple(decode_rates(start, PROBE_BOUNDS) for start in _SCIPY_EFFECTIVE_STARTS)


def likelihood_evaluation_count(history: Sequence[LikelihoodEvaluation]) -> int:
    """Derive a validated objective count from a complete likelihood history."""
    indexes = tuple(item.evaluation_index for item in history)
    if not indexes:
        raise ValueError("likelihood evaluation history must be nonempty")
    if indexes != tuple(range(1, len(indexes) + 1)):
        raise ValueError("likelihood evaluation indexes must be contiguous from one")
    return len(indexes)


def simulation_evaluation_count(history: Sequence[SimulationGenerationHistory]) -> int:
    """Derive a validated objective count from production evaluation events."""
    if not history:
        raise ValueError("simulation evaluation history must be nonempty")
    indexes = tuple(
        candidate.evaluation_index
        for generation in history
        for candidate in generation.candidates
        if candidate.evaluation_index is not None
    )
    if not indexes:
        raise ValueError("simulation evaluation history must contain evaluation events")
    if indexes != tuple(range(1, len(indexes) + 1)):
        raise ValueError("simulation evaluation indexes must be contiguous from one")
    return len(indexes)
