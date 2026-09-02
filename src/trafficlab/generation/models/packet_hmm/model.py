"""Observation vocabulary, raw reservoirs, and fitted categorical packet HMM."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from trafficlab.common.config import FamilyName
from trafficlab.common.trace import Direction, TrafficTrace
from trafficlab.generation.models.common import MarkCount, MarkDistribution
from trafficlab.generation.models.markov_renewal.parameters import type7_quantile
from trafficlab.generation.models.packet_hmm.inference import (
    BaumWelchDiagnostics,
    HmmParameters,
    canonicalize_states,
    fit_baum_welch,
)

IAT_QUANTILES = (1.0 / 3.0, 2.0 / 3.0)
SIZE_QUANTILES = (1.0 / 3.0, 2.0 / 3.0)
ADDITIVE_SMOOTHING = 0.001
MAXIMUM_ITERATIONS = 100
CONVERGENCE_TOLERANCE = 1e-8
INITIALIZATION = "fixed_cyclic_v1"
_PROBABILITY_TOLERANCE = 1e-12
_THRESHOLD_TOLERANCE = 1e-12
_MINIMUM_FRAME_LENGTH = 14
_MAXIMUM_FRAME_LENGTH = 2**32 - 1


@dataclass(frozen=True, slots=True)
class PacketCategory:
    """One observed categorical IAT/direction/size identity."""

    iat_bin: int
    direction: Direction
    size_bin: int

    def __post_init__(self) -> None:
        if type(self.iat_bin) is not int or not 0 <= self.iat_bin <= 3:
            raise ValueError("iat_bin must be an exact integer in 0..3")
        if type(self.direction) is not Direction:
            raise TypeError("category direction must be a Direction")
        if type(self.size_bin) is not int or not 0 <= self.size_bin <= 2:
            raise ValueError("size_bin must be an exact integer in 0..2")


@dataclass(frozen=True, slots=True)
class PacketSample:
    """One raw post-t0 packet member retained inside a category reservoir."""

    iat: float
    frame_length: int

    def __post_init__(self) -> None:
        if type(self.iat) is not float or not math.isfinite(self.iat):
            raise ValueError("sample IAT must be a finite exact float")
        if self.iat < 0.0:
            raise ValueError("sample IAT must be nonnegative")
        if (
            type(self.frame_length) is not int
            or not _MINIMUM_FRAME_LENGTH <= self.frame_length <= _MAXIMUM_FRAME_LENGTH
        ):
            raise ValueError("sample frame_length must be an exact integer in the renderer range")


@dataclass(frozen=True, slots=True)
class EncodedObservations:
    """Observed vocabulary and aligned raw individual-member reservoirs."""

    iat_thresholds: tuple[float, ...]
    observation_indices: tuple[int, ...]
    reservoirs: tuple[tuple[PacketSample, ...], ...]
    size_thresholds: tuple[float, float]
    vocabulary: tuple[PacketCategory, ...]


def _thresholds(values: Sequence[int | float], quantiles: tuple[float, float]) -> tuple[float, float]:
    return (type7_quantile(values, quantiles[0]), type7_quantile(values, quantiles[1]))


def iat_bin(iat: float, thresholds: tuple[float, ...]) -> int:
    """Map an IAT to explicit zero or one of three positive Type-7 bins."""
    if type(iat) is not float or not math.isfinite(iat) or iat < 0.0:
        raise ValueError("IAT must be a finite nonnegative exact float")
    if iat == 0.0:
        return 0
    if type(thresholds) is not tuple or len(thresholds) != 2:
        raise ValueError("positive IATs require two Type-7 thresholds")
    lower, upper = thresholds
    if (
        any(type(value) is not float or not math.isfinite(value) or value <= 0.0 for value in thresholds)
        or lower > upper
    ):
        raise ValueError("IAT thresholds must be finite positive nondecreasing exact floats")
    if iat <= lower:
        return 1
    if iat <= upper:
        return 2
    return 3


def size_bin(frame_length: int, thresholds: tuple[float, float]) -> int:
    """Map a frame length to three inclusive-upper Type-7 bins."""
    if type(frame_length) is not int or not _MINIMUM_FRAME_LENGTH <= frame_length <= _MAXIMUM_FRAME_LENGTH:
        raise ValueError("frame_length must be an exact integer in the renderer range")
    if type(thresholds) is not tuple or len(thresholds) != 2:
        raise ValueError("size thresholds must contain two values")
    lower, upper = thresholds
    if any(type(value) is not float or not math.isfinite(value) for value in thresholds) or lower > upper:
        raise ValueError("size thresholds must be finite nondecreasing exact floats")
    if frame_length <= lower:
        return 0
    if frame_length <= upper:
        return 1
    return 2


def build_observations(trace: TrafficTrace) -> EncodedObservations:
    """Encode only observed post-t0 categories in first-appearance order."""
    if type(trace) is not TrafficTrace or len(trace) < 2:
        raise ValueError("packet-HMM observations require a TrafficTrace with at least two packets")
    iats = tuple(float(value) for value in trace.iats())
    positive_iats = tuple(value for value in iats if value > 0.0)
    iat_thresholds: tuple[float, ...] = _thresholds(positive_iats, IAT_QUANTILES) if positive_iats else ()
    frame_lengths = tuple(int(value) for value in trace.frame_lengths[1:])
    size_thresholds = _thresholds(frame_lengths, SIZE_QUANTILES)
    vocabulary: list[PacketCategory] = []
    indices: dict[PacketCategory, int] = {}
    reservoirs: list[list[PacketSample]] = []
    observation_indices: list[int] = []
    for iat, raw_direction, frame_length in zip(iats, trace.directions[1:], frame_lengths, strict=True):
        category = PacketCategory(
            iat_bin(iat, iat_thresholds),
            Direction.OUTBOUND if int(raw_direction) == 0 else Direction.INBOUND,
            size_bin(frame_length, size_thresholds),
        )
        index = indices.get(category)
        if index is None:
            index = len(vocabulary)
            indices[category] = index
            vocabulary.append(category)
            reservoirs.append([])
        observation_indices.append(index)
        reservoirs[index].append(PacketSample(iat, frame_length))
    return EncodedObservations(
        iat_thresholds,
        tuple(observation_indices),
        tuple(tuple(reservoir) for reservoir in reservoirs),
        size_thresholds,
        tuple(vocabulary),
    )


def _validate_probability_vector(value: object, *, length: int, context: str) -> tuple[float, ...]:
    if type(value) is not tuple or len(cast(tuple[object, ...], value)) != length:
        raise ValueError(f"{context} must contain {length} probabilities")
    items = cast(tuple[object, ...], value)
    if any(type(item) is not float or not math.isfinite(item) or item <= 0.0 for item in items):
        raise ValueError(f"{context} must contain finite positive exact floats")
    result = cast(tuple[float, ...], items)
    if not math.isclose(math.fsum(result), 1.0, rel_tol=0.0, abs_tol=_PROBABILITY_TOLERANCE):
        raise ValueError(f"{context} must sum to one")
    return result


def _validate_thresholds(
    actual: object,
    values: tuple[int | float, ...],
    quantiles: tuple[float, float],
    *,
    context: str,
    allow_empty: bool,
) -> tuple[float, ...]:
    if type(actual) is not tuple:
        raise ValueError(f"{context} thresholds must be a tuple")
    thresholds = cast(tuple[object, ...], actual)
    if allow_empty and not values:
        if thresholds:
            raise ValueError(f"{context} thresholds must be empty without positive observations")
        return ()
    if len(thresholds) != 2 or any(type(value) is not float or not math.isfinite(value) for value in thresholds):
        raise ValueError(f"{context} thresholds must contain two finite exact floats")
    checked = cast(tuple[float, float], thresholds)
    if checked[0] > checked[1] or (context == "IAT" and checked[0] <= 0.0):
        raise ValueError(f"{context} thresholds must be nondecreasing")
    expected = _thresholds(values, quantiles)
    if any(
        not math.isclose(left, right, rel_tol=0.0, abs_tol=_THRESHOLD_TOLERANCE)
        for left, right in zip(checked, expected, strict=True)
    ):
        raise ValueError(f"{context} thresholds must equal reference Type-7 terciles")
    return checked


@dataclass(frozen=True, slots=True)
class PacketHmmModel:
    """A complete finite categorical HMM and raw category-member corpus."""

    additive_smoothing: float
    convergence_tolerance: float
    diagnostics: BaumWelchDiagnostics
    emission_rows: tuple[tuple[float, ...], ...]
    iat_quantiles: tuple[float, float]
    iat_thresholds: tuple[float, ...]
    initial_marks: MarkDistribution
    initial_probabilities: tuple[float, ...]
    initialization: str
    maximum_iterations: int
    reservoirs: tuple[tuple[PacketSample, ...], ...]
    size_quantiles: tuple[float, float]
    size_thresholds: tuple[float, float]
    state_count: int
    transition_rows: tuple[tuple[float, ...], ...]
    vocabulary: tuple[PacketCategory, ...]

    def __post_init__(self) -> None:
        if type(self.state_count) is not int or not 2 <= self.state_count <= 4:
            raise ValueError("state_count must be an exact integer in 2..4")
        if type(self.additive_smoothing) is not float or self.additive_smoothing != ADDITIVE_SMOOTHING:
            raise ValueError("additive smoothing must equal the fixed value 0.001")
        if type(self.convergence_tolerance) is not float or self.convergence_tolerance != CONVERGENCE_TOLERANCE:
            raise ValueError("convergence tolerance must equal the fixed value 1e-8")
        if type(self.maximum_iterations) is not int or self.maximum_iterations != MAXIMUM_ITERATIONS:
            raise ValueError("maximum_iterations must equal the fixed value 100")
        if type(self.initialization) is not str or self.initialization != INITIALIZATION:
            raise ValueError("initialization must equal fixed_cyclic_v1")
        if self.iat_quantiles != IAT_QUANTILES or any(type(value) is not float for value in self.iat_quantiles):
            raise ValueError("IAT quantiles must equal the fixed Type-7 terciles")
        if self.size_quantiles != SIZE_QUANTILES or any(type(value) is not float for value in self.size_quantiles):
            raise ValueError("size quantiles must equal the fixed Type-7 terciles")
        if type(self.diagnostics) is not BaumWelchDiagnostics:
            raise TypeError("diagnostics must be BaumWelchDiagnostics")
        BaumWelchDiagnostics(
            self.diagnostics.converged,
            self.diagnostics.iterations,
            self.diagnostics.log_likelihoods,
        )
        if self.diagnostics.iterations > self.maximum_iterations:
            raise ValueError("diagnostic iterations exceed maximum_iterations")
        if not self.diagnostics.converged and self.diagnostics.iterations != self.maximum_iterations:
            raise ValueError("nonconverged diagnostics must reach maximum_iterations")
        if type(self.initial_marks) is not MarkDistribution or self.initial_marks.total_count != 1:
            raise ValueError("initial_marks must contain exactly the observed t0 mark")
        MarkDistribution(self.initial_marks.entries)
        if type(self.vocabulary) is not tuple or not self.vocabulary:
            raise ValueError("vocabulary must be a nonempty tuple")
        if any(type(category) is not PacketCategory for category in self.vocabulary):
            raise TypeError("vocabulary entries must be PacketCategory values")
        if len(set(self.vocabulary)) != len(self.vocabulary):
            raise ValueError("vocabulary entries must be unique")
        symbol_count = len(self.vocabulary)
        if type(self.reservoirs) is not tuple or len(self.reservoirs) != symbol_count:
            raise ValueError("reservoirs must contain one sample tuple per vocabulary category")
        all_samples: list[PacketSample] = []
        for _category, reservoir in zip(self.vocabulary, self.reservoirs, strict=True):
            if (
                type(reservoir) is not tuple
                or not reservoir
                or any(type(sample) is not PacketSample for sample in reservoir)
            ):
                raise ValueError("each category reservoir must be a nonempty tuple of PacketSample values")
            for sample in reservoir:
                PacketSample(sample.iat, sample.frame_length)
                all_samples.append(sample)
        positive_iats = tuple(sample.iat for sample in all_samples if sample.iat > 0.0)
        checked_iat_thresholds = _validate_thresholds(
            self.iat_thresholds,
            positive_iats,
            IAT_QUANTILES,
            context="IAT",
            allow_empty=True,
        )
        checked_size_thresholds = cast(
            tuple[float, float],
            _validate_thresholds(
                self.size_thresholds,
                tuple(sample.frame_length for sample in all_samples),
                SIZE_QUANTILES,
                context="size",
                allow_empty=False,
            ),
        )
        for category, reservoir in zip(self.vocabulary, self.reservoirs, strict=True):
            for sample in reservoir:
                if iat_bin(sample.iat, checked_iat_thresholds) != category.iat_bin:
                    raise ValueError("reservoir sample does not belong to its IAT bin")
                if size_bin(sample.frame_length, checked_size_thresholds) != category.size_bin:
                    raise ValueError("reservoir sample does not belong to its size bin")
        if type(self.initial_probabilities) is not tuple or len(self.initial_probabilities) != self.state_count:
            raise ValueError("state_count must match initial probabilities")
        initial = _validate_probability_vector(
            self.initial_probabilities, length=self.state_count, context="initial probabilities"
        )
        if (
            type(self.transition_rows) is not tuple
            or len(self.transition_rows) != self.state_count
            or any(type(row) is not tuple or len(row) != self.state_count for row in self.transition_rows)
        ):
            raise ValueError("transition_rows must be a K x K tuple")
        transitions = tuple(
            _validate_probability_vector(row, length=self.state_count, context="transition row")
            for row in self.transition_rows
        )
        if type(self.emission_rows) is not tuple or len(self.emission_rows) != self.state_count:
            raise ValueError("emission_rows must be a K x M tuple")
        emissions = tuple(
            _validate_probability_vector(row, length=symbol_count, context="emission row") for row in self.emission_rows
        )
        means = tuple(math.fsum(sample.iat for sample in reservoir) / len(reservoir) for reservoir in self.reservoirs)
        canonical = canonicalize_states(initial, transitions, emissions, symbol_iat_means=means)
        if canonical != HmmParameters(initial, transitions, emissions):
            raise ValueError("state rows must use canonical expected-IAT, emission, and transition order")

    @property
    def family(self) -> FamilyName:
        """Return the closed fitted-model family identifier."""
        return "packet_hmm"


def fit_trace(trace: TrafficTrace, *, state_count: int) -> PacketHmmModel:
    """Fit a deterministic categorical HMM to post-t0 packet observations."""
    if type(state_count) is not int or not 2 <= state_count <= 4:
        raise ValueError("state_count must be an exact integer in 2..4")
    encoded = build_observations(trace)
    means = tuple(math.fsum(sample.iat for sample in reservoir) / len(reservoir) for reservoir in encoded.reservoirs)
    fitted = fit_baum_welch(
        encoded.observation_indices,
        state_count=state_count,
        symbol_count=len(encoded.vocabulary),
        symbol_iat_means=means,
        maximum_iterations=MAXIMUM_ITERATIONS,
        tolerance=CONVERGENCE_TOLERANCE,
        smoothing=ADDITIVE_SMOOTHING,
    )
    first_direction = Direction.OUTBOUND if int(trace.directions[0]) == 0 else Direction.INBOUND
    initial_marks = MarkDistribution((MarkCount(first_direction, int(trace.frame_lengths[0]), 1),))
    return PacketHmmModel(
        additive_smoothing=ADDITIVE_SMOOTHING,
        convergence_tolerance=CONVERGENCE_TOLERANCE,
        diagnostics=fitted.diagnostics,
        emission_rows=fitted.parameters.emission_rows,
        iat_quantiles=IAT_QUANTILES,
        iat_thresholds=encoded.iat_thresholds,
        initial_marks=initial_marks,
        initial_probabilities=fitted.parameters.initial_probabilities,
        initialization=INITIALIZATION,
        maximum_iterations=MAXIMUM_ITERATIONS,
        reservoirs=encoded.reservoirs,
        size_quantiles=SIZE_QUANTILES,
        size_thresholds=encoded.size_thresholds,
        state_count=state_count,
        transition_rows=fitted.parameters.transition_rows,
        vocabulary=encoded.vocabulary,
    )


__all__ = [
    "ADDITIVE_SMOOTHING",
    "BaumWelchDiagnostics",
    "CONVERGENCE_TOLERANCE",
    "EncodedObservations",
    "IAT_QUANTILES",
    "INITIALIZATION",
    "MAXIMUM_ITERATIONS",
    "PacketCategory",
    "PacketHmmModel",
    "PacketSample",
    "SIZE_QUANTILES",
    "build_observations",
    "fit_trace",
    "iat_bin",
    "size_bin",
]
