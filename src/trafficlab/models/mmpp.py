"""Two-state Markov-modulated Poisson process traffic model."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from random import Random
from time import monotonic
from typing import Protocol, cast

from trafficlab.config import FamilyName, FloatBounds, GenerationLimits, MmppConfig
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
    ReferenceTrace,
    validate_fit_inputs,
)
from trafficlab.trace import Direction, TraceEvent

_PROBABILITY_TOLERANCE = 1e-12


def _invalid(detail: str, *, corrective_action: str) -> TrafficlabError:
    return TrafficlabError(detail, corrective_action=corrective_action)


def _validate_window(window: object) -> float:
    if type(window) is not float or not math.isfinite(window) or window <= 0.0:
        raise _invalid(
            "invalid observation window: it must be a finite positive float",
            corrective_action="provide a finite positive normalized observation window",
        )
    return window


def _validate_rate_bounds(value: object, *, name: str) -> FloatBounds:
    if (
        type(value) is not FloatBounds
        or type(value.lower) is not float
        or type(value.upper) is not float
        or not math.isfinite(value.lower)
        or not math.isfinite(value.upper)
        or value.lower <= 0.0
        or value.lower >= value.upper
    ):
        raise _invalid(
            f"invalid MMPP {name} bounds",
            corrective_action="provide finite positive ordered bounds for every MMPP rate gene",
        )
    return value


def _validate_bounds(bounds: object) -> MmppConfig:
    if type(bounds) is not MmppConfig:
        raise _invalid(
            "invalid MMPP bounds",
            corrective_action="provide configured q01, q10, lambda0, and lambda1 bounds",
        )
    _validate_rate_bounds(bounds.q01, name="q01")
    _validate_rate_bounds(bounds.q10, name="q10")
    _validate_rate_bounds(bounds.lambda0, name="lambda0")
    _validate_rate_bounds(bounds.lambda1, name="lambda1")
    return bounds


def _repair_genes(genes: Sequence[Gene], bounds: object) -> tuple[float, float, float, float]:
    checked_bounds = _validate_bounds(bounds)
    try:
        values = tuple(genes)
    except TypeError as error:
        raise _invalid(
            "invalid MMPP genes",
            corrective_action="provide exactly four finite positive float q01, q10, lambda0, and lambda1 genes",
        ) from error
    if len(values) != 4 or any(
        type(value) is not float or not math.isfinite(value) or value <= 0.0 for value in values
    ):
        raise _invalid(
            "invalid MMPP genes",
            corrective_action="provide exactly four finite positive float q01, q10, lambda0, and lambda1 genes",
        )
    q01_raw, q10_raw, lambda_a, lambda_b = cast(tuple[float, float, float, float], values)
    lambda0_raw, lambda1_raw = sorted((lambda_a, lambda_b))
    q01 = min(max(q01_raw, checked_bounds.q01.lower), checked_bounds.q01.upper)
    q10 = min(max(q10_raw, checked_bounds.q10.lower), checked_bounds.q10.upper)
    lambda0 = min(max(lambda0_raw, checked_bounds.lambda0.lower), checked_bounds.lambda0.upper)
    lambda1 = min(max(lambda1_raw, checked_bounds.lambda1.lower), checked_bounds.lambda1.upper)
    if not lambda0 < lambda1:
        raise _invalid(
            "invalid repaired MMPP arrival rates",
            corrective_action="use named lambda bounds that preserve strict lambda0 less than lambda1 order",
        )
    return (q01, q10, lambda0, lambda1)


def _stationary_probabilities(q01: object, q10: object) -> tuple[float, float]:
    if (
        type(q01) is not float
        or type(q10) is not float
        or not math.isfinite(q01)
        or not math.isfinite(q10)
        or q01 <= 0.0
        or q10 <= 0.0
    ):
        raise ValueError("transition rates must be finite positive floats")
    # Scaling both rates before normalization preserves their ratio while
    # avoiding overflow when valid rates sit near the floating-point limit.
    scale = max(q01, q10)
    q01_scaled = q01 / scale
    q10_scaled = q10 / scale
    total = q01_scaled + q10_scaled
    pi0 = q10_scaled / total
    pi1 = q01_scaled / total
    if (
        not math.isfinite(pi0)
        or not math.isfinite(pi1)
        or not 0.0 <= pi0 <= 1.0
        or not 0.0 <= pi1 <= 1.0
        or not math.isclose(pi0 + pi1, 1.0, rel_tol=0.0, abs_tol=_PROBABILITY_TOLERANCE)
    ):
        raise ValueError("derived stationary probabilities must be finite and normalized")
    return (pi0, pi1)


def _arrival_epoch_probabilities(
    q01: object,
    q10: object,
    lambda0: object,
    lambda1: object,
) -> tuple[float, float]:
    """Return the stationary regime law conditioned on an arrival at time zero."""
    rates = (q01, q10, lambda0, lambda1)
    if any(type(rate) is not float or not math.isfinite(rate) or rate <= 0.0 for rate in rates):
        raise ValueError("arrival-epoch rates must be finite positive floats")

    checked_q01 = cast(float, q01)
    checked_q10 = cast(float, q10)
    checked_lambda0 = cast(float, lambda0)
    checked_lambda1 = cast(float, lambda1)
    # Conditioning on an arrival size-biases the time-stationary regime by its
    # Poisson rate.  Log weights keep that product stable across wide rate
    # ranges before the final two-term normalization.
    log_weight0 = math.log(checked_q10) + math.log(checked_lambda0)
    log_weight1 = math.log(checked_q01) + math.log(checked_lambda1)
    maximum_log_weight = max(log_weight0, log_weight1)
    weight0 = math.exp(log_weight0 - maximum_log_weight)
    weight1 = math.exp(log_weight1 - maximum_log_weight)
    total_weight = weight0 + weight1
    a0 = weight0 / total_weight
    return (a0, weight1 / total_weight)


@dataclass(frozen=True, slots=True)
class MmppModel:
    """A validated two-regime CTMC, ordered arrival rates, and joint marks."""

    q01: float
    q10: float
    lambda0: float
    lambda1: float
    marks: MarkDistribution

    def __post_init__(self) -> None:
        if any(
            type(value) is not float or not math.isfinite(value) or value <= 0.0
            for value in (self.q01, self.q10, self.lambda0, self.lambda1)
        ):
            raise ValueError("all MMPP rates must be finite positive floats")
        if not self.lambda0 < self.lambda1:
            raise ValueError("lambda0 must be strictly less than lambda1")
        if type(self.marks) is not MarkDistribution:
            raise TypeError("marks must be a MarkDistribution")
        MarkDistribution(self.marks.entries)
        _stationary_probabilities(self.q01, self.q10)

    @property
    def family(self) -> FamilyName:
        """Return the built-in family identifier."""
        return "mmpp"

    @property
    def pi0(self) -> float:
        """Return the stationary probability of the low-rate regime."""
        return _stationary_probabilities(self.q01, self.q10)[0]

    @property
    def pi1(self) -> float:
        """Return the stationary probability of the high-rate regime."""
        return _stationary_probabilities(self.q01, self.q10)[1]


def _validate_model(model: object) -> MmppModel:
    if type(model) is not MmppModel:
        raise TypeError("model must be an MmppModel")
    try:
        return MmppModel(model.q01, model.q10, model.lambda0, model.lambda1, model.marks)
    except (TypeError, ValueError) as error:
        raise _invalid(
            f"invalid fitted MMPP model: {error}",
            corrective_action="load or fit finite positive MMPP rates and empirical marks",
        ) from error


class _MmppRng(Protocol):
    def random(self) -> float:
        """Return a uniform draw in [0, 1)."""
        ...

    def randrange(self, stop: int) -> int:
        """Return an empirical-mark index below stop."""
        ...

    def expovariate(self, lambd: float) -> float:
        """Return a finite nonnegative exponential delay."""
        ...


@dataclass(frozen=True, slots=True)
class _RandomMmppRng:
    """Adapt one local standard-library RNG to the narrow MMPP protocol."""

    random_source: Random

    def random(self) -> float:
        """Draw the initial arrival-epoch regime variate."""
        return self.random_source.random()

    def randrange(self, stop: int) -> int:
        """Draw one empirical-mark index."""
        return self.random_source.randrange(stop)

    def expovariate(self, lambd: float) -> float:
        """Draw one exponential arrival or transition clock."""
        return self.random_source.expovariate(lambd)


def _validate_unit_draw(draw: object) -> float:
    if type(draw) is not float or not math.isfinite(draw) or not 0.0 <= draw < 1.0:
        raise _invalid(
            "invalid MMPP random draw",
            corrective_action="use a random generator that returns finite floats in [0, 1)",
        )
    return draw


def _validate_delay(delay: object) -> float:
    if type(delay) is not float or not math.isfinite(delay) or delay < 0.0:
        raise _invalid(
            "invalid MMPP random delay",
            corrective_action="use a random generator that returns finite nonnegative exponential delays",
        )
    return delay


def _mark_from_draw(marks: MarkDistribution, draw: object) -> tuple[Direction, int]:
    if type(draw) is not int or not 0 <= draw < marks.total_count:
        raise _invalid(
            "invalid empirical random draw",
            corrective_action="use a random generator that returns integers in the requested range",
        )
    cumulative = 0
    for entry in marks.entries:
        cumulative += entry.count
        if draw < cumulative:
            return (entry.direction, entry.frame_length)
    raise AssertionError("validated empirical draw was outside its cumulative distribution")


def _sample_mark(marks: MarkDistribution, rng: _MmppRng, guard: GenerationGuard) -> tuple[Direction, int] | None:
    raw_draw = rng.randrange(marks.total_count)
    if guard.post_draw_reason() is not None:
        return None
    return _mark_from_draw(marks, raw_draw)


def _next_time(current_time: float, delay: float, *, clock_name: str) -> float:
    next_time = current_time + delay
    if not math.isfinite(next_time):
        raise _invalid(
            f"invalid MMPP {clock_name} time",
            corrective_action="use finite generation parameters and random delays that do not overflow",
        )
    return next_time


def _generate_with_rng(
    model: MmppModel,
    rng: _MmppRng,
    *,
    W: float,
    limits: GenerationLimits,
    clock: Callable[[], float] = monotonic,
) -> GenerationResult:
    """Generate from a scripted or local RNG, preserving the documented race draw order."""
    checked_model = _validate_model(model)
    window = _validate_window(W)
    guard = GenerationGuard.start(limits, clock=clock)
    reason = guard.pre_draw_reason(0, 0)
    if reason is not None:
        return GenerationResult(complete=False, events=(), reason=reason)
    raw_regime_draw = rng.random()
    reason = guard.post_draw_reason()
    if reason is not None:
        return GenerationResult(complete=False, events=(), reason=reason)
    arrival_epoch_a0, _ = _arrival_epoch_probabilities(
        checked_model.q01,
        checked_model.q10,
        checked_model.lambda0,
        checked_model.lambda1,
    )
    regime = 0 if _validate_unit_draw(raw_regime_draw) < arrival_epoch_a0 else 1
    sampled_mark = _sample_mark(checked_model.marks, rng, guard)
    if sampled_mark is None:
        return GenerationResult(complete=False, events=(), reason="max_wall_seconds")
    direction, frame_length = sampled_mark
    reason = guard.prospective_reason(0, 0, frame_length)
    if reason is not None:
        return GenerationResult(complete=False, events=(), reason=reason)
    events = [TraceEvent(0.0, direction, frame_length)]
    output_bytes = frame_length
    current_time = 0.0

    while True:
        reason = guard.pre_draw_reason(len(events), output_bytes)
        if reason is not None:
            return GenerationResult(complete=False, events=tuple(events), reason=reason)
        arrival_rate = checked_model.lambda0 if regime == 0 else checked_model.lambda1
        transition_rate = checked_model.q01 if regime == 0 else checked_model.q10
        raw_arrival_delay = rng.expovariate(arrival_rate)
        reason = guard.post_draw_reason()
        if reason is not None:
            return GenerationResult(complete=False, events=tuple(events), reason=reason)
        arrival_time = _next_time(current_time, _validate_delay(raw_arrival_delay), clock_name="arrival")
        raw_transition_delay = rng.expovariate(transition_rate)
        reason = guard.post_draw_reason()
        if reason is not None:
            return GenerationResult(complete=False, events=tuple(events), reason=reason)
        transition_time = _next_time(current_time, _validate_delay(raw_transition_delay), clock_name="transition")
        arrival_wins = arrival_time < transition_time
        selected_time = arrival_time if arrival_wins else transition_time
        if selected_time > window:
            return GenerationResult(complete=True, events=tuple(events))
        current_time = selected_time
        if not arrival_wins:
            regime = 1 - regime
            continue
        sampled_mark = _sample_mark(checked_model.marks, rng, guard)
        if sampled_mark is None:
            return GenerationResult(complete=False, events=tuple(events), reason="max_wall_seconds")
        direction, frame_length = sampled_mark
        reason = guard.prospective_reason(len(events), output_bytes, frame_length)
        if reason is not None:
            return GenerationResult(complete=False, events=tuple(events), reason=reason)
        events.append(TraceEvent(current_time, direction, frame_length))
        output_bytes += frame_length


def _load_marks(value: object) -> MarkDistribution:
    if type(value) is not list:
        raise ValueError("marks must be a list")
    entries: list[MarkCount] = []
    for mark in cast(list[object], value):
        if type(mark) is not dict:
            raise ValueError("every mark must be an object")
        item = cast(dict[str, object], mark)
        if set(item) != {"direction", "frame_length", "count"}:
            raise ValueError("every mark must have exactly direction, frame_length, and count")
        direction, frame_length, count = item["direction"], item["frame_length"], item["count"]
        if type(direction) is not str or type(frame_length) is not int or type(count) is not int:
            raise ValueError("mark fields must use exact JSON scalar types")
        entries.append(MarkCount(Direction(direction), frame_length, count))
    return MarkDistribution(tuple(entries))


class MmppFamily:
    """Fit, serialize, and simulate the two-state MMPP family."""

    name: FamilyName = "mmpp"
    gene_names: tuple[str, ...] = ("q01", "q10", "lambda0", "lambda1")
    bounds_type = MmppConfig
    estimator_choices: Mapping[str, str | int | float] = {
        "rates": "direct_genes",
        "initial_regime": "arrival_epoch",
        "marks": "joint_empirical_first_appearance",
        "tie": "regime_change",
        "first_event": "zero",
    }

    def repair(self, genes: Sequence[Gene], bounds: FamilyBounds, reference: ReferenceTrace) -> Genes:
        """Return the canonical named q rates and strictly ordered arrival rates."""
        del reference
        return _repair_genes(genes, bounds)

    def fit(self, reference: ReferenceTrace, genes: Sequence[Gene], *, W: float, bounds: FamilyBounds) -> MmppModel:
        """Retain repaired timing genes and the reference's joint empirical marks."""
        trace = validate_fit_inputs(reference, W=W)
        q01, q10, lambda0, lambda1 = _repair_genes(genes, bounds)
        return MmppModel(q01, q10, lambda0, lambda1, MarkDistribution.from_trace(trace))

    def generate(
        self,
        model: FittedModel,
        seed: int,
        W: float,
        limits: GenerationLimits,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> GenerationResult:
        """Generate reproducibly from a locally owned exact nonnegative seed."""
        if type(seed) is not int or seed < 0:
            raise _invalid(
                "invalid MMPP seed: it must be a nonnegative exact integer",
                corrective_action="provide a nonnegative integer generation seed",
            )
        return _generate_with_rng(cast(MmppModel, model), _RandomMmppRng(Random(seed)), W=W, limits=limits, clock=clock)

    def dump_fitted(self, model: FittedModel) -> dict[str, object]:
        """Return exactly the persisted MMPP rates and empirical marks."""
        checked_model = _validate_model(model)
        return {
            "q01": checked_model.q01,
            "q10": checked_model.q10,
            "lambda0": checked_model.lambda0,
            "lambda1": checked_model.lambda1,
            "marks": [
                {"direction": entry.direction.value, "frame_length": entry.frame_length, "count": entry.count}
                for entry in checked_model.marks.entries
            ],
        }

    def load_fitted(self, data: object, *, genes: Genes, bounds: FamilyBounds) -> MmppModel:
        """Load strict persisted state while binding it to repaired outer genes."""
        repaired = _repair_genes(genes, bounds)
        if type(data) is not dict:
            raise _invalid(
                "invalid fitted MMPP payload",
                corrective_action="provide exactly q01, q10, lambda0, lambda1, and marks JSON fields",
            )
        payload = cast(dict[str, object], data)
        expected_fields = {"q01", "q10", "lambda0", "lambda1", "marks"}
        if set(payload) != expected_fields:
            raise _invalid(
                "invalid fitted MMPP payload",
                corrective_action="provide exactly q01, q10, lambda0, lambda1, and marks JSON fields",
            )
        q01, q10, lambda0, lambda1 = (payload[name] for name in self.gene_names)
        if (
            any(
                type(value) is not float or not math.isfinite(value) or value <= 0.0
                for value in (q01, q10, lambda0, lambda1)
            )
            or (q01, q10, lambda0, lambda1) != repaired
        ):
            raise _invalid(
                "invalid fitted MMPP rates",
                corrective_action="make payload rates equal the repaired outer MMPP genes",
            )
        try:
            return MmppModel(
                cast(float, q01),
                cast(float, q10),
                cast(float, lambda0),
                cast(float, lambda1),
                _load_marks(payload["marks"]),
            )
        except (TypeError, ValueError) as error:
            raise _invalid(
                f"invalid fitted MMPP payload: {error}",
                corrective_action="provide valid finite MMPP rates, stationary probabilities, and empirical marks",
            ) from error
