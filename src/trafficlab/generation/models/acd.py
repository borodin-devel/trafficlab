"""Exponential autoregressive conditional duration traffic model."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Protocol, cast

import numpy as np
from numpy.typing import NDArray

from trafficlab.common.config import AcdConfig, FamilyName, GeneCoordinateKind, GenerationLimits, IntegerBounds
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TrafficTrace
from trafficlab.generation.models import acd_fitting
from trafficlab.generation.models.acd_fitting import (
    optimizer_maximum_iterations,
    optimizer_tolerance,
)
from trafficlab.generation.models.common import (
    FamilyBounds,
    FittedModel,
    Gene,
    GenerationGuard,
    GenerationResult,
    Genes,
    IncompleteReason,
    MarkCount,
    MarkDistribution,
    make_generation_trace,
    make_rng,
    validate_fit_inputs,
)


def _invalid(detail: str, *, corrective_action: str) -> TrafficlabError:
    return TrafficlabError(detail, corrective_action=corrective_action)


def _validate_window(window: object) -> float:
    if type(window) is not float or not math.isfinite(window) or window <= 0.0:
        raise _invalid(
            "invalid observation window: it must be a finite positive float",
            corrective_action="provide a finite positive normalized observation window",
        )
    return window


def _validate_bounds(bounds: object) -> AcdConfig:
    if type(bounds) is not AcdConfig:
        raise _invalid("invalid ACD bounds", corrective_action="provide configured ACD order bounds")
    order = bounds.order
    if type(order) is not IntegerBounds or type(order.lower) is not int or type(order.upper) is not int:
        raise _invalid("invalid ACD order bounds", corrective_action="provide exact integer order bounds")
    if order.lower < 1 or order.upper > 3 or order.lower > order.upper:
        raise _invalid("invalid ACD order bounds", corrective_action="provide order bounds within 1..3")
    return bounds


def _repair_genes(genes: Sequence[Gene], bounds: object) -> tuple[int]:
    checked_bounds = _validate_bounds(bounds)
    try:
        values = tuple(genes)
    except TypeError as error:
        raise _invalid(
            "invalid ACD genes: exactly one order value is required",
            corrective_action="provide one exact integer order gene",
        ) from error
    if len(values) != 1 or type(values[0]) is not int:
        raise _invalid(
            "invalid ACD genes: exactly one exact integer order value is required",
            corrective_action="provide one exact integer order gene",
        )
    return (min(max(values[0], checked_bounds.order.lower), checked_bounds.order.upper),)


minimize = acd_fitting.minimize

__all__ = (
    "AcdFamily",
    "AcdModel",
    "_conditional_means",
    "_exponential_negative_log_likelihood",
    "_likelihood_and_gradient",
    "_transform_parameters",
)


def _transform_parameters(
    parameters: NDArray[np.float64], *, order: int, reference_mean: float
) -> tuple[float, tuple[float, ...], tuple[float, ...]]:
    return acd_fitting.transform_parameters(parameters, order=order, reference_mean=reference_mean)


def _conditional_means(
    durations: Sequence[float],
    *,
    omega: float,
    alpha: tuple[float, ...],
    beta: tuple[float, ...],
    initial_mean: float,
) -> tuple[float, ...]:
    return acd_fitting.conditional_means(durations, omega=omega, alpha=alpha, beta=beta, initial_mean=initial_mean)


def _exponential_negative_log_likelihood(durations: Sequence[float], conditional_means: Sequence[float]) -> float:
    return acd_fitting.exponential_negative_log_likelihood(durations, conditional_means)


def _likelihood_and_gradient(
    parameters: NDArray[np.float64], durations: tuple[float, ...], order: int, reference_mean: float
) -> tuple[float, NDArray[np.float64]]:
    return acd_fitting.likelihood_and_gradient(parameters, durations, order, reference_mean)


def _fit_parameters(
    durations: tuple[float, ...], *, order: int, reference_mean: float
) -> tuple[float, tuple[float, ...], tuple[float, ...]]:
    """Retain the historical solver seam while delegating fitting mechanics."""
    acd_fitting.minimize = minimize
    return acd_fitting.fit_parameters(durations, order=order, reference_mean=reference_mean)


@dataclass(frozen=True, slots=True)
class AcdModel:
    """One fitted stationary exponential ACD recursion and ordered joint marks."""

    omega: float
    alpha: tuple[float, ...]
    beta: tuple[float, ...]
    marks: MarkDistribution

    def __post_init__(self) -> None:
        if type(self.omega) is not float or not math.isfinite(self.omega) or self.omega <= 0.0:
            raise ValueError("omega must be a finite positive float")
        if type(self.alpha) is not tuple or type(self.beta) is not tuple:
            raise TypeError("alpha and beta must be tuples")
        if len(self.alpha) != len(self.beta) or not 1 <= len(self.alpha) <= 3:
            raise ValueError("alpha and beta must have matching order in 1..3")
        coefficients = (*self.alpha, *self.beta)
        if any(
            type(value) is not float or not math.isfinite(value) or value < 0.0 or value >= 1.0
            for value in coefficients
        ):
            raise ValueError("ACD coefficients must be finite floats in [0, 1)")
        persistence = math.fsum(coefficients)
        if persistence >= 1.0:
            raise ValueError("ACD coefficient sum must be below one")
        stationary_mean = self.omega / (1.0 - persistence)
        if not math.isfinite(stationary_mean) or stationary_mean <= 0.0:
            raise ValueError("ACD stationary mean must be finite and positive")
        if type(self.marks) is not MarkDistribution:
            raise TypeError("marks must be a MarkDistribution")
        MarkDistribution(self.marks.entries)

    @property
    def family(self) -> FamilyName:
        return "acd"


def _validate_model(model: object) -> AcdModel:
    if type(model) is not AcdModel:
        raise TypeError("model must be an AcdModel")
    try:
        return AcdModel(model.omega, model.alpha, model.beta, model.marks)
    except (TypeError, ValueError) as error:
        raise _invalid(
            f"invalid fitted ACD model: {error}",
            corrective_action="load or fit finite stationary ACD parameters and empirical marks",
        ) from error


class _AcdRng(Protocol):
    def exponential(self, scale: float) -> float:
        """Return one scalar unit-mean exponential innovation."""
        ...

    def choice(self, a: int) -> int:
        """Return one empirical-mark index below the supplied population total."""
        ...


def _validate_innovation(value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or value < 0.0:
        raise _invalid(
            "invalid ACD unit exponential innovation",
            corrective_action="use a random generator that returns finite nonnegative scalar innovations",
        )
    return value


def _stationary_mean(model: AcdModel) -> float:
    persistence = math.fsum((*model.alpha, *model.beta))
    mean = model.omega / (1.0 - persistence)
    if not math.isfinite(mean) or mean <= 0.0:
        raise _invalid(
            "invalid ACD stationary mean",
            corrective_action="use finite positive omega and stationary nonnegative coefficients",
        )
    return mean


def _generate_with_rng(
    model: AcdModel,
    rng: _AcdRng,
    *,
    W: float,
    limits: GenerationLimits,
    clock: Callable[[], float] = monotonic,
) -> GenerationResult:
    """Generate one complete closed window with fixed ACD prehistory and scalar draw order."""
    checked_model = _validate_model(model)
    window = _validate_window(W)
    guard = GenerationGuard.start(limits, clock=clock)
    timestamps: list[float] = []
    directions: list[Direction] = []
    frame_lengths: list[int] = []
    output_bytes = 0

    def result(complete: bool, reason: IncompleteReason | None = None) -> GenerationResult:
        return GenerationResult(
            complete=complete,
            trace=make_generation_trace(timestamps, directions, frame_lengths),
            reason=reason,
        )

    reason = guard.pre_draw_reason(0, 0)
    if reason is not None:
        return result(False, reason)
    direction, frame_length = checked_model.marks.sample(rng)
    reason = guard.post_draw_reason()
    if reason is not None:
        return result(False, reason)
    reason = guard.prospective_reason(0, 0, frame_length)
    if reason is not None:
        return result(False, reason)
    timestamps.append(0.0)
    directions.append(direction)
    frame_lengths.append(frame_length)
    output_bytes = frame_length

    order = len(checked_model.alpha)
    mean = _stationary_mean(checked_model)
    duration_history = [mean] * order
    conditional_mean_history = [mean] * order
    current_time = 0.0
    while True:
        reason = guard.pre_draw_reason(len(timestamps), output_bytes)
        if reason is not None:
            return result(False, reason)
        conditional_mean = (
            checked_model.omega
            + math.fsum(weight * value for weight, value in zip(checked_model.alpha, duration_history, strict=True))
            + math.fsum(
                weight * value for weight, value in zip(checked_model.beta, conditional_mean_history, strict=True)
            )
        )
        if not math.isfinite(conditional_mean) or conditional_mean <= 0.0:
            raise _invalid(
                "invalid ACD conditional mean",
                corrective_action="use finite stationary fitted ACD parameters and duration history",
            )
        raw_innovation = rng.exponential(1.0)
        reason = guard.post_draw_reason()
        if reason is not None:
            return result(False, reason)
        innovation = _validate_innovation(raw_innovation)
        duration = conditional_mean * innovation
        if not math.isfinite(duration) or duration < 0.0:
            raise _invalid(
                "invalid ACD duration",
                corrective_action="use finite fitted parameters and unit exponential innovations",
            )
        next_time = current_time + duration
        if not math.isfinite(next_time):
            raise _invalid(
                "invalid ACD arrival time",
                corrective_action="use finite fitted parameters and unit exponential innovations",
            )
        if next_time > window:
            return result(True)

        direction, frame_length = checked_model.marks.sample(rng)
        reason = guard.post_draw_reason()
        if reason is not None:
            return result(False, reason)
        reason = guard.prospective_reason(len(timestamps), output_bytes, frame_length)
        if reason is not None:
            return result(False, reason)
        timestamps.append(next_time)
        directions.append(direction)
        frame_lengths.append(frame_length)
        output_bytes += frame_length
        current_time = next_time
        duration_history.insert(0, duration)
        duration_history.pop()
        conditional_mean_history.insert(0, conditional_mean)
        conditional_mean_history.pop()


def _mark_payload(marks: MarkDistribution) -> list[dict[str, object]]:
    return [
        {"direction": entry.direction.value, "frame_length": entry.frame_length, "count": entry.count}
        for entry in marks.entries
    ]


def _load_marks(value: object) -> MarkDistribution:
    if type(value) is not list:
        raise _invalid("invalid ACD marks", corrective_action="provide a list of strict empirical marks")
    entries: list[MarkCount] = []
    for raw in cast(list[object], value):
        if type(raw) is not dict:
            raise _invalid(
                "invalid ACD marks",
                corrective_action="provide strict direction, frame_length, and count marks",
            )
        mark = cast(dict[str, object], raw)
        if set(mark) != {"direction", "frame_length", "count"}:
            raise _invalid(
                "invalid ACD marks",
                corrective_action="provide strict direction, frame_length, and count marks",
            )
        direction, frame_length, count = mark["direction"], mark["frame_length"], mark["count"]
        if type(direction) is not str or type(frame_length) is not int or type(count) is not int:
            raise _invalid("invalid ACD marks", corrective_action="provide exact empirical mark primitives")
        try:
            entries.append(MarkCount(Direction(direction), frame_length, count))
        except (TypeError, ValueError) as error:
            raise _invalid(f"invalid ACD marks: {error}", corrective_action="provide valid empirical marks") from error
    try:
        return MarkDistribution(tuple(entries))
    except (TypeError, ValueError) as error:
        raise _invalid(
            f"invalid ACD marks: {error}",
            corrective_action="provide nonempty unique empirical marks",
        ) from error


class AcdFamily:
    """Fit, serialize, and generate the stationary exponential ACD(p,p) family."""

    name: FamilyName = "acd"
    gene_names: tuple[str, ...] = ("order",)
    gene_coordinate_kinds: tuple[GeneCoordinateKind, ...] = ("integer",)
    bounds_type = AcdConfig
    estimator_choices: Mapping[str, str | int | float] = {
        "first_event": "zero",
        "duration": "exponential_acd_mle",
        "initialization": "sample_mean_presample_durations_and_conditional_means",
        "marks": "joint_empirical_first_appearance",
        "optimizer": "scipy.optimize.minimize/L-BFGS-B",
        "optimizer_start": "zero_unconstrained_sample_mean_scale",
        "optimizer_tolerance": optimizer_tolerance,
        "optimizer_maximum_iterations": optimizer_maximum_iterations,
        "parameter_transform": "scaled_exponential_simplex_slack",
    }

    def repair(self, genes: Sequence[Gene], bounds: FamilyBounds, reference: TrafficTrace) -> Genes:
        del reference
        return _repair_genes(genes, bounds)

    def fit(self, reference: TrafficTrace, genes: Sequence[Gene], *, W: float, bounds: FamilyBounds) -> AcdModel:
        trace = validate_fit_inputs(reference, W=W)
        order = _repair_genes(genes, bounds)[0]
        durations = tuple(float(value) for value in np.diff(trace.timestamps))
        reference_mean = math.fsum(durations) / len(durations)
        if not math.isfinite(reference_mean) or reference_mean <= 0.0:
            raise _invalid(
                "invalid ACD reference mean",
                corrective_action="provide a finite positive observation window with at least one positive duration",
            )
        omega, alpha, beta = _fit_parameters(durations, order=order, reference_mean=reference_mean)
        return AcdModel(omega=omega, alpha=alpha, beta=beta, marks=MarkDistribution.from_trace(trace))

    def generate(
        self,
        model: FittedModel,
        seed: int,
        W: float,
        limits: GenerationLimits,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> GenerationResult:
        if type(seed) is not int or seed < 0:
            raise _invalid(
                "invalid ACD seed: it must be a nonnegative exact integer",
                corrective_action="provide a nonnegative integer generation seed",
            )
        return _generate_with_rng(cast(AcdModel, model), make_rng(seed), W=W, limits=limits, clock=clock)

    def dump_fitted(self, model: FittedModel) -> dict[str, object]:
        checked_model = _validate_model(model)
        return {
            "omega": checked_model.omega,
            "alpha": list(checked_model.alpha),
            "beta": list(checked_model.beta),
            "marks": _mark_payload(checked_model.marks),
        }

    def load_fitted(self, data: object, *, genes: Genes, bounds: FamilyBounds) -> AcdModel:
        order = _repair_genes(genes, bounds)[0]
        if type(data) is not dict:
            raise _invalid(
                "invalid fitted ACD payload",
                corrective_action="provide exactly omega, alpha, beta, and marks",
            )
        payload = cast(dict[str, object], data)
        if set(payload) != {"omega", "alpha", "beta", "marks"}:
            raise _invalid(
                "invalid fitted ACD payload",
                corrective_action="provide exactly omega, alpha, beta, and marks",
            )
        omega, raw_alpha, raw_beta = payload["omega"], payload["alpha"], payload["beta"]
        if type(omega) is not float or type(raw_alpha) is not list or type(raw_beta) is not list:
            raise _invalid(
                "invalid fitted ACD payload",
                corrective_action="provide one positive float omega and list coefficient vectors",
            )
        alpha_values = cast(list[object], raw_alpha)
        beta_values = cast(list[object], raw_beta)
        if len(alpha_values) != order or len(beta_values) != order:
            raise _invalid(
                "invalid fitted ACD order",
                corrective_action="match alpha and beta lengths to the repaired outer order gene",
            )
        if any(type(value) is not float for value in (*alpha_values, *beta_values)):
            raise _invalid(
                "invalid fitted ACD coefficients",
                corrective_action="provide exact finite nonnegative float coefficients",
            )
        marks = _load_marks(payload["marks"])
        try:
            return AcdModel(
                omega,
                tuple(cast(float, value) for value in alpha_values),
                tuple(cast(float, value) for value in beta_values),
                marks,
            )
        except (TypeError, ValueError) as error:
            raise _invalid(
                f"invalid fitted ACD payload: {error}",
                corrective_action="provide finite positive stationary ACD parameters and unique empirical marks",
            ) from error
