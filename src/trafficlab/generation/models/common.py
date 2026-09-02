"""Shared contracts, validation, and deterministic primitives for traffic models."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from time import monotonic
from types import MappingProxyType
from typing import Literal, Protocol, cast

import numpy as np

from trafficlab.common.config import (
    AcdConfig,
    FamilyName,
    GeneCoordinateKind,
    GenerationLimits,
    MarkovPacketTrainConfig,
    MarkovRenewalConfig,
    MmppConfig,
    NhppConfig,
    PacketHmmConfig,
    PoissonConfig,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace

type Gene = float | int
type Genes = tuple[Gene, ...]
type IncompleteReason = Literal["max_packets", "max_output_bytes", "max_wall_seconds"]
type FamilyBounds = (
    PoissonConfig
    | MarkovRenewalConfig
    | MmppConfig
    | NhppConfig
    | AcdConfig
    | MarkovPacketTrainConfig
    | PacketHmmConfig
)
type ModelDiagnostics = Mapping[str, int]
type ReferenceTrace = TrafficTrace

MARKOV_MODEL_DIAGNOSTIC_KEYS = (
    "timing_tier_transition_count",
    "timing_tier_source_count",
    "timing_tier_global_count",
    "uniform_unobserved_row_count",
)

_MINIMUM_FRAME_LENGTH = 14
_MAXIMUM_FRAME_LENGTH = 2**32 - 1
_INCOMPLETE_REASONS = frozenset(("max_packets", "max_output_bytes", "max_wall_seconds"))


def freeze_model_diagnostics(value: object) -> ModelDiagnostics:
    """Validate and freeze one finite mapping of named nonnegative counters."""
    if not isinstance(value, Mapping):
        raise TypeError("model diagnostics must be a mapping")
    mapping = cast(Mapping[object, object], value)
    items: tuple[tuple[object, object], ...] = tuple(mapping.items())
    if any(type(name) is not str or not name for name, _count in items):
        raise ValueError("model diagnostic names must be nonempty exact strings")
    if any(type(count) is not int or count < 0 for _name, count in items):
        raise ValueError("model diagnostic counts must be nonnegative exact integers")
    checked = cast(tuple[tuple[str, int], ...], items)
    return MappingProxyType(dict(sorted(checked)))


def _empty_model_diagnostics() -> dict[str, int]:
    return {}


class FittedModel(Protocol):
    """A fitted family-specific model with a common family identity."""

    @property
    def family(self) -> FamilyName:
        """Return the family that owns this fitted model."""
        ...


class ModelFamily(Protocol):
    """The strict contract each built-in traffic-model family implements."""

    @property
    def name(self) -> FamilyName:
        """Return the closed registry name."""
        ...

    @property
    def gene_names(self) -> tuple[str, ...]:
        """Return canonical chromosome coordinate names in order."""
        ...

    @property
    def gene_coordinate_kinds(self) -> tuple[GeneCoordinateKind, ...]:
        """Return one coordinate transform kind per canonical chromosome name."""
        ...

    @property
    def bounds_type(
        self,
    ) -> (
        type[PoissonConfig]
        | type[MarkovRenewalConfig]
        | type[MmppConfig]
        | type[NhppConfig]
        | type[AcdConfig]
        | type[MarkovPacketTrainConfig]
        | type[PacketHmmConfig]
    ):
        """Return the exact configured bounds type accepted by this family."""
        ...

    @property
    def estimator_choices(self) -> Mapping[str, str | int | float]:
        """Return the canonical persisted estimator policy."""
        ...

    def repair(self, genes: Sequence[Gene], bounds: FamilyBounds, reference: TrafficTrace) -> Genes:
        """Repair one family chromosome without consuming randomness."""
        ...

    def fit(self, reference: TrafficTrace, genes: Sequence[Gene], *, W: float, bounds: FamilyBounds) -> FittedModel:
        """Fit a model to one normalized reference trace."""
        ...

    def generate(
        self,
        model: FittedModel,
        seed: int,
        W: float,
        limits: GenerationLimits,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> GenerationResult:
        """Generate a diagnostic or complete trace from one fitted model."""
        ...

    def load_fitted(self, data: object, *, genes: Genes, bounds: FamilyBounds) -> FittedModel:
        """Load this family's validated fitted-model payload."""
        ...

    def dump_fitted(self, model: FittedModel) -> dict[str, object]:
        """Return this family's JSON-compatible fitted-model payload."""
        ...


def _invalid(detail: str, *, corrective_action: str) -> TrafficlabError:
    return TrafficlabError(detail, corrective_action=corrective_action)


def _validate_frame_length(frame_length: object, *, context: str) -> int:
    if type(frame_length) is not int:
        raise _invalid(
            f"invalid {context}: frame length must be an integer",
            corrective_action="provide renderer-compatible canonical Ethernet frame lengths",
        )
    if not _MINIMUM_FRAME_LENGTH <= frame_length <= _MAXIMUM_FRAME_LENGTH:
        raise _invalid(
            f"invalid {context}: frame length must be in {_MINIMUM_FRAME_LENGTH}..{_MAXIMUM_FRAME_LENGTH} (32-bit)",
            corrective_action="provide renderer-compatible canonical Ethernet frame lengths",
        )
    return frame_length


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """A complete reusable columnar trace or diagnostic prefix from a stopped generation."""

    complete: bool
    trace: TrafficTrace
    reason: IncompleteReason | None = None
    model_diagnostics: ModelDiagnostics = field(default_factory=_empty_model_diagnostics)

    def __post_init__(self) -> None:
        if type(self.complete) is not bool:
            raise TypeError("complete must be a bool")
        if type(self.trace) is not TrafficTrace:
            raise TypeError("generated trace must be a TrafficTrace")
        if self.complete and not len(self.trace):
            raise ValueError("complete generated trace must not be empty")
        if np.any(self.trace.frame_lengths < _MINIMUM_FRAME_LENGTH):
            raise ValueError(
                f"generated frame length must be in {_MINIMUM_FRAME_LENGTH}..{_MAXIMUM_FRAME_LENGTH} (32-bit)"
            )
        if self.complete:
            if self.reason is not None:
                raise ValueError("complete generation must not have an incomplete reason")
        elif self.reason not in _INCOMPLETE_REASONS:
            raise ValueError("incomplete generation requires a recognized reason")
        object.__setattr__(self, "model_diagnostics", freeze_model_diagnostics(self.model_diagnostics))

    def require_complete(self) -> TrafficTrace:
        """Return only a full-window trace, never diagnostic partial events."""
        if not self.complete:
            if self.reason == "max_packets":
                raise TrafficlabError(
                    "generation exceeded the configured packet limit",
                    corrective_action="correct limit or model",
                )
            raise TrafficlabError(
                f"generation did not complete: {self.reason}",
                corrective_action="increase generation limits and generate a complete trace",
            )
        return self.trace


def make_generation_trace(
    timestamps: Sequence[float], directions: Sequence[Direction], frame_lengths: Sequence[int]
) -> TrafficTrace:
    """Build one owned immutable generated trace directly from numeric model columns."""
    return TrafficTrace(
        np.asarray(timestamps, dtype=np.float64),
        np.asarray([0 if direction is Direction.OUTBOUND else 1 for direction in directions], dtype=np.uint8),
        np.asarray(frame_lengths, dtype=np.uint32),
    )


def validate_fit_inputs(reference: ReferenceTrace, *, W: float) -> TrafficTrace:
    """Validate the exact normalized columnar reference shared by all family fitters."""
    if type(W) is not float or not math.isfinite(W) or W <= 0.0:
        raise _invalid(
            "invalid observation window: it must be a finite positive float",
            corrective_action="provide a finite positive normalized observation window",
        )
    if type(reference) is not TrafficTrace:
        raise _invalid(
            "invalid reference trace: it must be an exact TrafficTrace",
            corrective_action="provide normalized canonical TrafficTrace columns ending at W",
        )
    if np.any(reference.frame_lengths < _MINIMUM_FRAME_LENGTH):
        raise _invalid(
            f"invalid reference trace: frame length must be in {_MINIMUM_FRAME_LENGTH}..{_MAXIMUM_FRAME_LENGTH} (32-bit)",
            corrective_action="provide renderer-compatible canonical Ethernet frame lengths",
        )
    if len(reference) < 2:
        raise _invalid(
            "invalid reference trace: at least two events are required",
            corrective_action="provide a normalized canonical reference ending at W",
        )
    if reference.timestamps[0] != 0.0:
        raise _invalid(
            "invalid reference trace: timestamps must start at zero",
            corrective_action="normalize the reference trace to start at zero and end at W",
        )
    if reference.timestamps[-1] != W:
        raise _invalid(
            "invalid reference trace: timestamps must end at W",
            corrective_action="normalize the reference trace to start at zero and end at W",
        )
    return reference


@dataclass(frozen=True, slots=True)
class MarkCount:
    """One joint empirical direction/frame-length mark and its observed count."""

    direction: Direction
    frame_length: int
    count: int

    def __post_init__(self) -> None:
        if type(self.direction) is not Direction:
            raise TypeError("mark direction must be a Direction")
        _validate_frame_length(self.frame_length, context="empirical mark")
        if type(self.count) is not int:
            raise TypeError("mark count must be an integer")
        if self.count <= 0:
            raise ValueError("mark count must be positive")


class _Choice(Protocol):
    def choice(self, a: int) -> int:
        """Return one uniform index below a positive population size."""
        ...


def make_rng(seed: int) -> np.random.Generator:
    """Construct the one production random stream explicitly owned by its caller."""
    if type(seed) is not int or seed < 0:
        raise ValueError("random seed must be a nonnegative exact integer")
    return np.random.Generator(np.random.PCG64(seed))


@dataclass(frozen=True, slots=True)
class MarkDistribution:
    """An ordered empirical joint-mark distribution sampled with integer draws."""

    entries: tuple[MarkCount, ...]

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple:
            raise TypeError("mark entries must be a tuple")
        if not self.entries:
            raise ValueError("mark distribution must not be empty")
        seen: set[tuple[Direction, int]] = set()
        for entry in self.entries:
            if type(entry) is not MarkCount:
                raise TypeError("mark entries must be MarkCount values")
            if type(entry.direction) is not Direction:
                raise TypeError("mark direction must be a Direction")
            _validate_frame_length(entry.frame_length, context="empirical mark")
            if type(entry.count) is not int:
                raise TypeError("mark count must be an integer")
            if entry.count <= 0:
                raise ValueError("mark count must be positive")
            mark = (entry.direction, entry.frame_length)
            if mark in seen:
                raise ValueError("mark distribution must not contain duplicate marks")
            seen.add(mark)

    @classmethod
    def from_trace(cls, trace: TrafficTrace) -> MarkDistribution:
        """Count joint marks with vector kernels while retaining first appearance order."""
        if type(trace) is not TrafficTrace:
            raise TypeError("reference marks must be a TrafficTrace")
        keys = (trace.directions.astype(np.uint64) << np.uint64(32)) | trace.frame_lengths.astype(np.uint64)
        unique_keys, first_indices, counts = np.unique(keys, return_index=True, return_counts=True)
        order = np.argsort(first_indices, kind="stable")
        ordered_keys = unique_keys[order]
        ordered_counts = counts[order]
        return cls(
            tuple(
                MarkCount(
                    Direction.OUTBOUND if int(key >> np.uint64(32)) == 0 else Direction.INBOUND,
                    int(key & np.uint64(_MAXIMUM_FRAME_LENGTH)),
                    int(count),
                )
                for key, count in zip(ordered_keys, ordered_counts, strict=True)
            )
        )

    @classmethod
    def from_reference(cls, reference: Sequence[TraceEvent]) -> MarkDistribution:
        """Count joint marks in their first observed order."""
        counts: dict[tuple[Direction, int], int] = {}
        for event in reference:
            if type(event) is not TraceEvent:
                raise TypeError("reference marks must be TraceEvent values")
            _validate_frame_length(event.frame_length, context="reference mark")
            mark = (event.direction, event.frame_length)
            counts[mark] = counts.get(mark, 0) + 1
        return cls(
            tuple(MarkCount(direction, frame_length, count) for (direction, frame_length), count in counts.items())
        )

    @property
    def total_count(self) -> int:
        """Return the exact integer population used by the empirical sampler."""
        return sum(entry.count for entry in self.entries)

    def sample(self, rng: _Choice) -> tuple[Direction, int]:
        """Sample one joint mark using exactly one scalar choice draw."""
        draw = rng.choice(self.total_count)
        if type(draw) is not int or not 0 <= draw < self.total_count:
            raise _invalid(
                "invalid empirical random draw",
                corrective_action="use a random generator that returns integers in the requested range",
            )
        cumulative = 0
        for entry in self.entries:
            cumulative += entry.count
            if draw < cumulative:
                return (entry.direction, entry.frame_length)
        raise AssertionError("validated empirical draw was outside its cumulative distribution")


class _Random(Protocol):
    def random(self) -> float:
        """Return one uniform continuous value in the half-open unit interval."""
        ...


def weighted_index(weights: Sequence[float], rng: _Random) -> int:
    """Choose an ordered weight index with one continuous cumulative draw."""
    values = tuple(weights)
    if not values:
        raise _invalid(
            "invalid weights: at least one weight is required", corrective_action="provide positive total weights"
        )
    if any(type(weight) is not float or not math.isfinite(weight) or weight < 0.0 for weight in values):
        raise _invalid(
            "invalid weights: values must be finite nonnegative floats",
            corrective_action="provide finite nonnegative weights with a positive total",
        )
    total = sum(values)
    if not math.isfinite(total) or total <= 0.0:
        raise _invalid(
            "invalid weights: total must be finite and positive",
            corrective_action="provide finite nonnegative weights with a positive total",
        )
    draw = rng.random()
    if type(draw) is not float or not math.isfinite(draw) or not 0.0 <= draw < 1.0:
        raise _invalid(
            "invalid weighted random draw",
            corrective_action="use a random generator that returns finite values in [0, 1)",
        )
    threshold = draw * total
    cumulative = 0.0
    for index, weight in enumerate(values[:-1]):
        cumulative += weight
        if threshold < cumulative:
            return index
    return len(values) - 1


def _validate_limits(limits: object) -> GenerationLimits:
    if type(limits) is not GenerationLimits:
        raise TypeError("limits must be a GenerationLimits")
    if type(limits.max_packets) is not int or limits.max_packets <= 0:
        raise TypeError("max_packets must be a positive exact integer")
    if type(limits.max_output_bytes) is not int or limits.max_output_bytes <= 0:
        raise TypeError("max_output_bytes must be a positive exact integer")
    if (
        type(limits.max_wall_seconds) is not float
        or not math.isfinite(limits.max_wall_seconds)
        or limits.max_wall_seconds <= 0.0
    ):
        raise TypeError("max_wall_seconds must be a finite positive exact float")
    return limits


@dataclass(frozen=True, slots=True)
class GenerationGuard:
    """Account for generation limits without turning partial output into a trace."""

    limits: GenerationLimits
    clock: Callable[[], float]
    deadline: float | None
    last_clock: float | None
    initial_wall_failure: bool = False

    @classmethod
    def start(cls, limits: GenerationLimits, *, clock: Callable[[], float] = monotonic) -> GenerationGuard:
        """Start a guard with exactly one clock read."""
        checked_limits = _validate_limits(limits)
        start = clock()
        if type(start) is not float or not math.isfinite(start):
            return cls(checked_limits, clock, None, None, initial_wall_failure=True)
        deadline = start + checked_limits.max_wall_seconds
        if not math.isfinite(deadline):
            return cls(checked_limits, clock, None, start, initial_wall_failure=True)
        return cls(checked_limits, clock, deadline, start)

    def _wall_reason(self) -> IncompleteReason | None:
        if self.initial_wall_failure:
            return "max_wall_seconds"
        now = self.clock()
        if (
            type(now) is not float
            or not math.isfinite(now)
            or self.last_clock is None
            or now < self.last_clock
            or self.deadline is None
            or now >= self.deadline
        ):
            return "max_wall_seconds"
        object.__setattr__(self, "last_clock", now)
        return None

    @staticmethod
    def _validate_accounting(count: object, byte_count: object) -> tuple[int, int]:
        if type(count) is not int or count < 0:
            raise TypeError("packet count must be a nonnegative exact integer")
        if type(byte_count) is not int or byte_count < 0:
            raise TypeError("output byte count must be a nonnegative exact integer")
        return (count, byte_count)

    def pre_draw_reason(self, count: int, byte_count: int) -> IncompleteReason | None:
        """Check wall, packet, and byte limits before a stochastic decision."""
        wall_reason = self._wall_reason()
        if wall_reason is not None:
            return wall_reason
        checked_count, checked_bytes = self._validate_accounting(count, byte_count)
        if checked_count >= self.limits.max_packets:
            return "max_packets"
        if checked_bytes >= self.limits.max_output_bytes:
            return "max_output_bytes"
        return None

    def post_draw_reason(self) -> IncompleteReason | None:
        """Check wall time immediately after a stochastic draw."""
        return self._wall_reason()

    def prospective_reason(self, count: int, byte_count: int, frame_length: int) -> IncompleteReason | None:
        """Check all limits before adding a prospective in-window packet."""
        wall_reason = self._wall_reason()
        if wall_reason is not None:
            return wall_reason
        checked_count, checked_bytes = self._validate_accounting(count, byte_count)
        checked_length = _validate_frame_length(frame_length, context="prospective event")
        if checked_count + 1 > self.limits.max_packets:
            return "max_packets"
        if checked_bytes + checked_length > self.limits.max_output_bytes:
            return "max_output_bytes"
        return None
