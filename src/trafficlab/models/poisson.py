"""Empirical-mark homogeneous Poisson traffic model."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from random import Random
from time import monotonic
from typing import Protocol, cast

from trafficlab.config import FamilyName, FloatBounds, GenerationLimits, PoissonConfig
from trafficlab.errors import TrafficlabError
from trafficlab.models.common import (
    FamilyBounds,
    FittedModel,
    Gene,
    GenerationGuard,
    GenerationResult,
    Genes,
    MarkCount,
    MarkDistribution,
    validate_fit_inputs,
)
from trafficlab.trace import Direction, TraceEvent


def _invalid(detail: str, *, corrective_action: str) -> TrafficlabError:
    return TrafficlabError(detail, corrective_action=corrective_action)


def _validate_window(window: object) -> float:
    if type(window) is not float or not math.isfinite(window) or window <= 0.0:
        raise _invalid(
            "invalid observation window: it must be a finite positive float",
            corrective_action="provide a finite positive normalized observation window",
        )
    return window


def _validate_bounds(bounds: object) -> PoissonConfig:
    if type(bounds) is not PoissonConfig:
        raise _invalid(
            "invalid Poisson bounds",
            corrective_action="provide the configured Poisson c_lambda bounds",
        )
    c_lambda = bounds.c_lambda
    if (
        type(c_lambda) is not FloatBounds
        or type(c_lambda.lower) is not float
        or type(c_lambda.upper) is not float
        or not math.isfinite(c_lambda.lower)
        or not math.isfinite(c_lambda.upper)
        or c_lambda.lower <= 0.0
        or c_lambda.lower >= c_lambda.upper
    ):
        raise _invalid(
            "invalid Poisson c_lambda bounds",
            corrective_action="provide finite positive c_lambda bounds with lower less than upper",
        )
    return bounds


def _repair_genes(genes: Sequence[Gene], bounds: object) -> tuple[float]:
    checked_bounds = _validate_bounds(bounds)
    try:
        values = tuple(genes)
    except TypeError as error:
        raise _invalid(
            "invalid Poisson genes: exactly one c_lambda value is required",
            corrective_action="provide one finite floating-point c_lambda gene",
        ) from error
    if len(values) != 1 or type(values[0]) is not float or not math.isfinite(values[0]):
        raise _invalid(
            "invalid Poisson genes: exactly one finite floating-point c_lambda value is required",
            corrective_action="provide one finite floating-point c_lambda gene",
        )
    return (min(max(values[0], checked_bounds.c_lambda.lower), checked_bounds.c_lambda.upper),)


@dataclass(frozen=True, slots=True)
class PoissonModel:
    """A fitted constant arrival rate with an ordered joint-mark distribution."""

    base_rate: float
    rate: float
    marks: MarkDistribution

    def __post_init__(self) -> None:
        if type(self.base_rate) is not float or not math.isfinite(self.base_rate) or self.base_rate <= 0.0:
            raise ValueError("base_rate must be a finite positive float")
        if type(self.rate) is not float or not math.isfinite(self.rate) or self.rate <= 0.0:
            raise ValueError("rate must be a finite positive float")
        if type(self.marks) is not MarkDistribution:
            raise TypeError("marks must be a MarkDistribution")
        MarkDistribution(self.marks.entries)

    @property
    def family(self) -> FamilyName:
        """Return the family identifier required by the common fitted-model contract."""
        return "poisson_empirical"


def _validate_model(model: object) -> PoissonModel:
    if type(model) is not PoissonModel:
        raise TypeError("model must be a PoissonModel")
    try:
        return PoissonModel(model.base_rate, model.rate, model.marks)
    except (TypeError, ValueError) as error:
        raise _invalid(
            f"invalid fitted Poisson model: {error}",
            corrective_action="load or fit a finite positive Poisson model with empirical marks",
        ) from error


class _PoissonRng(Protocol):
    def expovariate(self, lambd: float) -> float:
        """Return one exponential delay with the supplied positive rate."""
        ...

    def randrange(self, stop: int) -> int:
        """Return one integer mark draw below the supplied population total."""
        ...


@dataclass(frozen=True, slots=True)
class _RandomPoissonRng:
    """Adapt Random's general randrange signature to empirical-mark sampling's one-stop draw."""

    random: Random

    def expovariate(self, lambd: float) -> float:
        """Draw one standard-library exponential variate."""
        return self.random.expovariate(lambd)

    def randrange(self, stop: int) -> int:
        """Draw one standard-library empirical-mark index."""
        return self.random.randrange(stop)


def _validate_delay(delay: object) -> float:
    if type(delay) is not float or not math.isfinite(delay) or delay < 0.0:
        raise _invalid(
            "invalid Poisson random delay",
            corrective_action="use a random generator that returns finite nonnegative exponential delays",
        )
    return delay


def _generate_with_rng(
    model: PoissonModel,
    rng: _PoissonRng,
    *,
    W: float,
    limits: GenerationLimits,
    clock: Callable[[], float] = monotonic,
) -> GenerationResult:
    """Generate with an injected RNG so unit tests can prove stochastic draw order."""
    checked_model = _validate_model(model)
    window = _validate_window(W)
    guard = GenerationGuard.start(limits, clock=clock)
    events: list[TraceEvent] = []
    output_bytes = 0

    reason = guard.pre_draw_reason(0, 0)
    if reason is not None:
        return GenerationResult(complete=False, events=(), reason=reason)
    direction, frame_length = checked_model.marks.sample(rng)
    reason = guard.post_draw_reason()
    if reason is not None:
        return GenerationResult(complete=False, events=(), reason=reason)
    reason = guard.prospective_reason(0, 0, frame_length)
    if reason is not None:
        return GenerationResult(complete=False, events=(), reason=reason)
    events.append(TraceEvent(0.0, direction, frame_length))
    output_bytes += frame_length
    current_time = 0.0

    while True:
        reason = guard.pre_draw_reason(len(events), output_bytes)
        if reason is not None:
            return GenerationResult(complete=False, events=tuple(events), reason=reason)
        raw_delay = rng.expovariate(checked_model.rate)
        reason = guard.post_draw_reason()
        if reason is not None:
            return GenerationResult(complete=False, events=tuple(events), reason=reason)
        delay = _validate_delay(raw_delay)
        next_time = current_time + delay
        if not math.isfinite(next_time):
            raise _invalid(
                "invalid Poisson arrival time",
                corrective_action="use finite generation parameters and random delays",
            )
        if next_time > window:
            return GenerationResult(complete=True, events=tuple(events))

        direction, frame_length = checked_model.marks.sample(rng)
        reason = guard.post_draw_reason()
        if reason is not None:
            return GenerationResult(complete=False, events=tuple(events), reason=reason)
        reason = guard.prospective_reason(len(events), output_bytes, frame_length)
        if reason is not None:
            return GenerationResult(complete=False, events=tuple(events), reason=reason)
        events.append(TraceEvent(next_time, direction, frame_length))
        output_bytes += frame_length
        current_time = next_time


class PoissonFamily:
    """Fit, serialize, and generate the empirical-mark homogeneous Poisson family."""

    name: FamilyName = "poisson_empirical"
    gene_names: tuple[str, ...] = ("c_lambda",)
    bounds_type = PoissonConfig
    estimator_choices: Mapping[str, str | int | float] = {
        "first_event": "zero",
        "marks": "joint_empirical_first_appearance",
        "rate": "interval_count_over_window",
    }

    def repair(self, genes: Sequence[Gene], bounds: FamilyBounds, reference: Sequence[TraceEvent]) -> Genes:
        """Return the one-value canonical, finite, in-bounds rate-scale chromosome."""
        del reference
        return _repair_genes(genes, bounds)

    def fit(
        self, reference: Sequence[TraceEvent], genes: Sequence[Gene], *, W: float, bounds: FamilyBounds
    ) -> PoissonModel:
        """Estimate intervals per full window and retain ordered joint empirical marks."""
        events = validate_fit_inputs(reference, W=W)
        repaired_genes = self.repair(genes, bounds, events)
        base_rate = (len(events) - 1) / W
        rate = base_rate * repaired_genes[0]
        if not math.isfinite(rate) or rate <= 0.0:
            raise _invalid(
                "invalid fitted Poisson rate",
                corrective_action="provide a valid trace window and finite positive c_lambda gene",
            )
        return PoissonModel(base_rate=base_rate, rate=rate, marks=MarkDistribution.from_reference(events))

    def generate(
        self,
        model: FittedModel,
        seed: int,
        W: float,
        limits: GenerationLimits,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> GenerationResult:
        """Generate reproducibly with one locally owned exact nonnegative integer seed."""
        if type(seed) is not int or seed < 0:
            raise _invalid(
                "invalid Poisson seed: it must be a nonnegative exact integer",
                corrective_action="provide a nonnegative integer generation seed",
            )
        return _generate_with_rng(
            cast(PoissonModel, model), _RandomPoissonRng(Random(seed)), W=W, limits=limits, clock=clock
        )

    def dump_fitted(self, model: FittedModel) -> dict[str, object]:
        """Return exactly the JSON primitives that define a fitted Poisson model."""
        checked_model = _validate_model(model)
        return {
            "base_rate": checked_model.base_rate,
            "rate": checked_model.rate,
            "marks": [
                {
                    "direction": entry.direction.value,
                    "frame_length": entry.frame_length,
                    "count": entry.count,
                }
                for entry in checked_model.marks.entries
            ],
        }

    def load_fitted(self, data: object, *, genes: Genes, bounds: FamilyBounds) -> PoissonModel:
        """Load one strict fitted payload and bind it to canonical caller-supplied genes."""
        repaired_genes = _repair_genes(genes, bounds)
        if type(data) is not dict:
            raise _invalid(
                "invalid fitted Poisson payload",
                corrective_action="provide exactly base_rate, rate, and marks JSON fields",
            )
        payload = cast(dict[str, object], data)
        if set(payload) != {"base_rate", "rate", "marks"}:
            raise _invalid(
                "invalid fitted Poisson payload",
                corrective_action="provide exactly base_rate, rate, and marks JSON fields",
            )
        base_rate = payload["base_rate"]
        rate = payload["rate"]
        mark_data = payload["marks"]
        if (
            type(base_rate) is not float
            or type(rate) is not float
            or not math.isfinite(base_rate)
            or not math.isfinite(rate)
            or base_rate <= 0.0
            or rate <= 0.0
            or type(mark_data) is not list
        ):
            raise _invalid(
                "invalid fitted Poisson payload",
                corrective_action="provide finite positive float rates and a list of empirical marks",
            )
        entries: list[MarkCount] = []
        for mark in cast(list[object], mark_data):
            if type(mark) is not dict:
                raise _invalid(
                    "invalid fitted Poisson mark",
                    corrective_action="provide exactly direction, frame_length, and count fields for every mark",
                )
            value = cast(dict[str, object], mark)
            if set(value) != {"direction", "frame_length", "count"}:
                raise _invalid(
                    "invalid fitted Poisson mark",
                    corrective_action="provide exactly direction, frame_length, and count fields for every mark",
                )
            direction = value["direction"]
            frame_length = value["frame_length"]
            count = value["count"]
            if type(direction) is not str or type(frame_length) is not int or type(count) is not int:
                raise _invalid(
                    "invalid fitted Poisson mark",
                    corrective_action="provide exact direction, frame_length, and count mark fields",
                )
            try:
                entries.append(MarkCount(Direction(direction), frame_length, count))
            except (TypeError, ValueError) as error:
                raise _invalid(
                    f"invalid fitted Poisson mark: {error}",
                    corrective_action="provide valid empirical mark directions, Ethernet lengths, and positive counts",
                ) from error
        expected_rate = base_rate * repaired_genes[0]
        if not math.isfinite(expected_rate) or expected_rate <= 0.0 or rate != expected_rate:
            raise _invalid(
                "invalid fitted Poisson rate",
                corrective_action="make rate equal base_rate times the repaired c_lambda gene",
            )
        try:
            return PoissonModel(base_rate, rate, MarkDistribution(tuple(entries)))
        except (TypeError, ValueError) as error:
            raise _invalid(
                f"invalid fitted Poisson payload: {error}",
                corrective_action="provide a nonempty unique empirical mark distribution",
            ) from error
