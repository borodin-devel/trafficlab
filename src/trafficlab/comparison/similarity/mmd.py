"""Streaming random-Fourier approximate joint MMD for canonical traces."""

import math

import numpy as np
from numpy.typing import NDArray

from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import TrafficTrace, validate_traffic_trace
from trafficlab.comparison.similarity.common import JsonDiagnostics, SimilarityResult, validate_observation_window

_ROUNDING_TOLERANCE = 1e-12


def _positive_int(value: object, *, name: str) -> int:
    """Validate one strictly positive integer setting without accepting booleans."""
    if type(value) is not int or value <= 0:
        raise TrafficlabError(
            f"invalid {name}: it must be a positive integer",
            corrective_action=f"provide a positive integer {name}",
        )
    return value


def _nonnegative_int(value: object, *, name: str) -> int:
    """Validate one deterministic PCG64 seed without accepting booleans."""
    if type(value) is not int or value < 0:
        raise TrafficlabError(
            f"invalid {name}: it must be a nonnegative integer",
            corrective_action=f"provide a nonnegative integer {name}",
        )
    return value


def _positive_float(value: object, *, name: str) -> float:
    """Validate one finite positive continuous scaling floor."""
    if type(value) is not float or not math.isfinite(value) or value <= 0.0:
        raise TrafficlabError(
            f"invalid {name}: it must be a finite positive float",
            corrective_action=f"provide a finite positive float {name}",
        )
    return value


def _bounded_discrepancy(value: float) -> float:
    """Return the geometric MMD discrepancy, accepting only floating-point overshoot."""
    if not math.isfinite(value) or value < 0.0:
        raise TrafficlabError(
            "invalid approximate MMD discrepancy: computation produced a nonfinite value",
            corrective_action="provide finite canonical traces and valid MMD settings",
        )
    if value <= 1.0:
        return value
    if value <= 1.0 + _ROUNDING_TOLERANCE:
        return 1.0
    raise TrafficlabError(
        "invalid approximate MMD discrepancy: computation produced a value outside [0, 1]",
        corrective_action="provide finite canonical traces and valid MMD settings",
    )


class RandomFeatureMean:
    """Accumulate the mean of unit-norm direction-block Fourier features in one pass."""

    def __init__(self, frequencies: NDArray[np.float64]) -> None:
        raw = np.asarray(frequencies)
        if raw.ndim != 2 or raw.shape[0] == 0 or raw.shape[1] != 2:
            raise ValueError("frequencies must have shape (feature_count, 2)")
        try:
            typed = np.asarray(raw, dtype=np.float64)
        except (OverflowError, TypeError, ValueError) as error:
            raise ValueError("frequencies must be finite") from error
        if not np.all(np.isfinite(typed)):
            raise ValueError("frequencies must be finite")
        self._frequencies: NDArray[np.float64] = np.array(typed, dtype=np.float64, copy=True, order="C")
        self._sum: NDArray[np.float64] = np.zeros(4 * len(self._frequencies), dtype=np.float64)
        self.count = 0

    @property
    def feature_count(self) -> int:
        """Return the number of frequency pairs in each categorical direction block."""
        return len(self._frequencies)

    @property
    def mean(self) -> NDArray[np.float64]:
        """Return an owned mean embedding after at least one streamed observation."""
        if self.count == 0:
            raise ValueError("at least one feature observation is required")
        return self._sum / self.count

    def add(self, direction: int, values: NDArray[np.float64]) -> None:
        """Add one standardized continuous point to its unordered direction block."""
        if type(direction) is not int or direction not in (0, 1):
            raise ValueError("direction must be canonical code 0 or 1")
        raw = np.asarray(values)
        if raw.shape != (2,):
            raise ValueError("continuous values must have shape (2,)")
        try:
            point = np.asarray(raw, dtype=np.float64)
        except (OverflowError, TypeError, ValueError) as error:
            raise ValueError("continuous values must be finite") from error
        if not np.all(np.isfinite(point)):
            raise ValueError("continuous values must be finite")
        projections = self._frequencies @ point
        block_width = 2 * self.feature_count
        offset = direction * block_width
        normalization = math.sqrt(self.feature_count)
        self._sum[offset : offset + self.feature_count] += np.cos(projections) / normalization
        self._sum[offset + self.feature_count : offset + block_width] += np.sin(projections) / normalization
        self.count += 1


def random_fourier_frequencies(feature_count: int, seed: int) -> NDArray[np.float64]:
    """Draw the dedicated deterministic standard-normal frequency matrix."""
    count = _positive_int(feature_count, name="approximate MMD feature count")
    validated_seed = _nonnegative_int(seed, name="approximate MMD seed")
    return np.random.Generator(np.random.PCG64(validated_seed)).standard_normal((count, 2), dtype=np.float64)


def _continuous_values(trace: TrafficTrace) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Return paired noninitial log-IAT and log-frame-length continuous coordinates."""
    return np.log1p(trace.iats()), np.log(trace.frame_lengths[1:])


def _continuous_parameters(trace: TrafficTrace, scale_floor: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Freeze reference-only coordinate means and population scales."""
    iats, lengths = _continuous_values(trace)
    mean = np.array((np.mean(iats, dtype=np.float64), np.mean(lengths, dtype=np.float64)), dtype=np.float64)
    scale = np.maximum(
        np.array((np.std(iats, dtype=np.float64), np.std(lengths, dtype=np.float64)), dtype=np.float64), scale_floor
    )
    return np.asarray(mean, dtype=np.float64), np.asarray(scale, dtype=np.float64)


def feature_mean(
    trace: TrafficTrace,
    frequencies: NDArray[np.float64],
    continuous_mean: NDArray[np.float64],
    continuous_scale: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Build one streamed embedding without ever allocating a packet-by-feature matrix."""
    validated_trace = validate_traffic_trace(trace, minimum_events=2, trace_name="trace")
    accumulator = RandomFeatureMean(frequencies)
    mean = np.asarray(continuous_mean, dtype=np.float64)
    scale = np.asarray(continuous_scale, dtype=np.float64)
    if mean.shape != (2,) or scale.shape != (2,) or not np.all(np.isfinite(mean)) or not np.all(np.isfinite(scale)):
        raise ValueError("continuous mean and scale must be finite shape-(2,) arrays")
    if np.any(scale <= 0.0):
        raise ValueError("continuous scale must be positive")
    iats, lengths = _continuous_values(validated_trace)
    for direction, iat, length in zip(validated_trace.directions[1:], iats, lengths, strict=True):
        point = np.array(((iat - mean[0]) / scale[0], (length - mean[1]) / scale[1]), dtype=np.float64)
        accumulator.add(int(direction), point)
    return accumulator.mean


def approximate_mmd_similarity(
    reference: TrafficTrace,
    generated: TrafficTrace,
    W: float,
    feature_count: int,
    seed: int,
    scale_floor: float,
) -> SimilarityResult:
    """Compare joint timing, size, and unordered direction with streaming random features."""
    window = validate_observation_window(W)
    floor = _positive_float(scale_floor, name="approximate MMD scale floor")
    reference_trace = validate_traffic_trace(reference, minimum_events=2, trace_name="reference", window=window)
    generated_trace = validate_traffic_trace(generated, minimum_events=2, trace_name="generated", window=window)
    frequencies = random_fourier_frequencies(feature_count, seed)
    mean, scale = _continuous_parameters(reference_trace, floor)
    reference_embedding = feature_mean(reference_trace, frequencies, mean, scale)
    generated_embedding = feature_mean(generated_trace, frequencies, mean, scale)
    discrepancy = _bounded_discrepancy(float(np.linalg.norm(reference_embedding - generated_embedding)) / 2.0)
    diagnostics: JsonDiagnostics = {
        "observation_window_seconds": window,
        "feature_count": len(frequencies),
        "embedding_dimension": len(reference_embedding),
        "seed": _nonnegative_int(seed, name="approximate MMD seed"),
        "continuous": {
            "reference_mean": tuple(float(value) for value in mean),
            "reference_scale": tuple(float(value) for value in scale),
            "scale_floor": floor,
        },
        "reference_sample_count": len(reference_trace) - 1,
        "generated_sample_count": len(generated_trace) - 1,
        "discrepancy": discrepancy,
    }
    return SimilarityResult(score=1.0 - discrepancy, diagnostics=diagnostics)
