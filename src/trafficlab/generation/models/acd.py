"""Exponential autoregressive conditional duration traffic model."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray
from scipy import optimize as scipy_optimize  # pyright: ignore[reportMissingTypeStubs]

from trafficlab.common.config import AcdConfig, FamilyName, GeneCoordinateKind, GenerationLimits, IntegerBounds
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TrafficTrace
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

_OPTIMIZER_METHOD = "L-BFGS-B"
_OPTIMIZER_TOLERANCE = 1e-10
_OPTIMIZER_MAXIMUM_ITERATIONS = 500
_OPTIMIZER_OPTIONS: dict[str, int | float] = {
    "maxiter": _OPTIMIZER_MAXIMUM_ITERATIONS,
    "ftol": _OPTIMIZER_TOLERANCE,
    "gtol": _OPTIMIZER_TOLERANCE,
    "maxls": 20,
}
_INVALID_OBJECTIVE = 1e100
_MINIMUM_SIMPLEX_SLACK = 1e-12


class _OptimizeResult(Protocol):
    success: bool
    x: NDArray[np.float64]
    nit: int
    fun: float
    message: object


class _Minimize(Protocol):
    def __call__(
        self,
        fun: Callable[..., object],
        x0: NDArray[np.float64],
        args: tuple[object, ...],
        *,
        method: str,
        jac: bool,
        tol: float,
        options: dict[str, int | float],
    ) -> _OptimizeResult: ...


minimize = cast(_Minimize, cast(Any, scipy_optimize).minimize)


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


def _validate_reference_mean(value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0.0:
        raise ValueError("reference_mean must be a finite positive float")
    return value


def _transform_parameters(
    parameters: NDArray[np.float64], *, order: int, reference_mean: float
) -> tuple[float, tuple[float, ...], tuple[float, ...]]:
    """Map unconstrained coordinates to positive omega and one stationary coefficient simplex."""
    if type(order) is not int or not 1 <= order <= 3:
        raise ValueError("order must be an exact integer in 1..3")
    mean = _validate_reference_mean(reference_mean)
    if parameters.shape != (1 + 2 * order,):
        raise ValueError("ACD optimizer coordinates have the wrong shape")
    values = np.asarray(parameters, dtype=np.float64)
    if not np.all(np.isfinite(values)):
        raise ValueError("ACD optimizer coordinates must be finite")

    logits = values[1:]
    maximum = max(0.0, float(np.max(logits)))
    with np.errstate(over="ignore", invalid="ignore"):
        scaled = np.exp(logits - maximum)
    slack_weight = math.exp(-maximum)
    denominator = slack_weight + float(np.sum(scaled, dtype=np.float64))
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise ValueError("ACD coefficient transform denominator must be finite and positive")
    raw_coefficients = np.asarray(scaled / denominator, dtype=np.float64)
    coefficients = np.asarray((1.0 - _MINIMUM_SIMPLEX_SLACK) * raw_coefficients, dtype=np.float64)
    persistence = math.fsum(float(value) for value in coefficients)
    slack = 1.0 - persistence
    try:
        level = math.exp(float(values[0]))
    except OverflowError as error:
        raise ValueError("ACD level transform overflowed") from error
    omega = mean * level * slack
    if not math.isfinite(omega) or omega <= 0.0 or not 0.0 < slack <= 1.0:
        raise ValueError("ACD transform did not produce finite stationary parameters")
    alpha = tuple(float(value) for value in coefficients[:order])
    beta = tuple(float(value) for value in coefficients[order:])
    return (omega, alpha, beta)


def _conditional_means(
    durations: Sequence[float],
    *,
    omega: float,
    alpha: tuple[float, ...],
    beta: tuple[float, ...],
    initial_mean: float,
) -> tuple[float, ...]:
    """Evaluate the declared ACD recursion with fixed mean pre-sample histories."""
    values = tuple(durations)
    if not values or any(type(value) is not float or not math.isfinite(value) or value < 0.0 for value in values):
        raise ValueError("durations must be nonempty finite nonnegative floats")
    if type(omega) is not float or not math.isfinite(omega) or omega <= 0.0:
        raise ValueError("omega must be a finite positive float")
    if type(alpha) is not tuple or type(beta) is not tuple or len(alpha) != len(beta) or not 1 <= len(alpha) <= 3:
        raise ValueError("alpha and beta must be matching tuples with order in 1..3")
    coefficients = (*alpha, *beta)
    if any(
        type(value) is not float or not math.isfinite(value) or value < 0.0 or value >= 1.0
        for value in coefficients
    ):
        raise ValueError("ACD coefficients must be finite floats in [0, 1)")
    if math.fsum(coefficients) >= 1.0:
        raise ValueError("ACD coefficients must have sum below one")
    mean = _validate_reference_mean(initial_mean)
    order = len(alpha)
    result: list[float] = []
    for index in range(len(values)):
        duration_part = math.fsum(
            alpha[lag - 1] * (values[index - lag] if index >= lag else mean)
            for lag in range(1, order + 1)
        )
        mean_part = math.fsum(
            beta[lag - 1] * (result[index - lag] if index >= lag else mean)
            for lag in range(1, order + 1)
        )
        conditional_mean = omega + duration_part + mean_part
        if not math.isfinite(conditional_mean) or conditional_mean <= 0.0:
            raise ValueError("ACD recursion produced a nonfinite or nonpositive conditional mean")
        result.append(conditional_mean)
    return tuple(result)


def _exponential_negative_log_likelihood(
    durations: Sequence[float], conditional_means: Sequence[float]
) -> float:
    """Return the exponential ACD negative log likelihood without its zero constant."""
    values = tuple(durations)
    means = tuple(conditional_means)
    if not values or len(values) != len(means):
        raise ValueError("ACD likelihood requires equally sized nonempty vectors")
    if any(type(value) is not float or not math.isfinite(value) or value < 0.0 for value in values):
        raise ValueError("ACD likelihood durations must be finite and nonnegative")
    if any(type(value) is not float or not math.isfinite(value) or value <= 0.0 for value in means):
        raise ValueError("ACD likelihood conditional means must be finite and positive")
    result = math.fsum(math.log(mean) + duration / mean for duration, mean in zip(values, means, strict=True))
    if not math.isfinite(result):
        raise ValueError("ACD likelihood must be finite")
    return result


def _likelihood_and_gradient(
    parameters: NDArray[np.float64], durations: tuple[float, ...], order: int, reference_mean: float
) -> tuple[float, NDArray[np.float64]]:
    """Evaluate the exponential likelihood and its analytic transformed gradient."""
    try:
        omega, alpha, beta = _transform_parameters(parameters, order=order, reference_mean=reference_mean)
    except (OverflowError, ValueError):
        finite = np.nan_to_num(np.asarray(parameters, dtype=np.float64), nan=0.0, posinf=1e10, neginf=-1e10)
        bounded = np.asarray(np.clip(finite, -1e50, 1e50), dtype=np.float64)
        squared = float(bounded @ bounded)
        return (_INVALID_OBJECTIVE + min(squared, _INVALID_OBJECTIVE), np.asarray(2.0 * bounded, dtype=np.float64))

    natural_width = 1 + 2 * order
    conditional_means: list[float] = []
    derivatives: list[NDArray[np.float64]] = []
    natural_gradient = np.zeros(natural_width, dtype=np.float64)
    loss_terms: list[float] = []
    for index, duration in enumerate(durations):
        duration_lags = tuple(
            durations[index - lag] if index >= lag else reference_mean for lag in range(1, order + 1)
        )
        mean_lags = tuple(
            conditional_means[index - lag] if index >= lag else reference_mean for lag in range(1, order + 1)
        )
        conditional_mean = (
            omega
            + math.fsum(weight * value for weight, value in zip(alpha, duration_lags, strict=True))
            + math.fsum(weight * value for weight, value in zip(beta, mean_lags, strict=True))
        )
        derivative = np.zeros(natural_width, dtype=np.float64)
        derivative[0] = 1.0
        derivative[1 : 1 + order] = duration_lags
        derivative[1 + order :] = mean_lags
        for lag, weight in enumerate(beta, start=1):
            if index >= lag:
                derivative += weight * derivatives[index - lag]
        if not math.isfinite(conditional_mean) or conditional_mean <= 0.0 or not np.all(np.isfinite(derivative)):
            return (_INVALID_OBJECTIVE, np.zeros_like(parameters, dtype=np.float64))
        squared_mean = conditional_mean * conditional_mean
        if squared_mean == 0.0:
            return (_INVALID_OBJECTIVE, np.zeros_like(parameters, dtype=np.float64))
        loss_term = math.log(conditional_mean) + duration / conditional_mean
        gradient_factor = (conditional_mean - duration) / squared_mean
        if not math.isfinite(loss_term) or not math.isfinite(gradient_factor):
            return (_INVALID_OBJECTIVE, np.zeros_like(parameters, dtype=np.float64))
        conditional_means.append(conditional_mean)
        derivatives.append(derivative)
        loss_terms.append(loss_term)
        natural_gradient += gradient_factor * derivative

    loss = math.fsum(loss_terms)
    coefficients = np.asarray((*alpha, *beta), dtype=np.float64)
    raw_coefficients = coefficients / (1.0 - _MINIMUM_SIMPLEX_SLACK)
    slack_probability = max(0.0, (1.0 - float(np.sum(raw_coefficients, dtype=np.float64))))
    simplex_slack = 1.0 - math.fsum(float(value) for value in coefficients)
    coefficient_gradient = natural_gradient[1:]
    weighted_coefficient_gradient = float(coefficient_gradient @ coefficients)
    gradient = np.empty_like(parameters, dtype=np.float64)
    gradient[0] = natural_gradient[0] * omega
    gradient[1:] = coefficients * (
        coefficient_gradient - weighted_coefficient_gradient / (1.0 - _MINIMUM_SIMPLEX_SLACK)
    )
    gradient[1:] -= natural_gradient[0] * omega * slack_probability * coefficients / simplex_slack
    if not math.isfinite(loss) or not np.all(np.isfinite(gradient)):
        return (_INVALID_OBJECTIVE, np.zeros_like(parameters, dtype=np.float64))
    return (loss, gradient)


def _fit_parameters(
    durations: tuple[float, ...], *, order: int, reference_mean: float
) -> tuple[float, tuple[float, ...], tuple[float, ...]]:
    result = minimize(
        _likelihood_and_gradient,
        np.zeros(1 + 2 * order, dtype=np.float64),
        args=(durations, order, reference_mean),
        method=_OPTIMIZER_METHOD,
        jac=True,
        tol=_OPTIMIZER_TOLERANCE,
        options=dict(_OPTIMIZER_OPTIONS),
    )
    parameters = np.asarray(result.x, dtype=np.float64)
    try:
        iterations = int(result.nit)
        final_loss = float(result.fun)
    except (TypeError, ValueError, OverflowError) as error:
        raise _invalid(
            "invalid ACD optimizer result",
            corrective_action="refit with finite durations under the fixed ACD solver policy",
        ) from error
    if (
        not bool(result.success)
        or parameters.shape != (1 + 2 * order,)
        or not np.all(np.isfinite(parameters))
        or not math.isfinite(final_loss)
        or iterations < 0
        or iterations > _OPTIMIZER_MAXIMUM_ITERATIONS
    ):
        raise _invalid(
            f"invalid ACD optimizer result: {result.message}",
            corrective_action="provide identifiable finite durations or increase the fixed solver budget in architecture",
        )
    try:
        fitted = _transform_parameters(parameters, order=order, reference_mean=reference_mean)
        checked_loss = _exponential_negative_log_likelihood(
            durations,
            _conditional_means(
                durations,
                omega=fitted[0],
                alpha=fitted[1],
                beta=fitted[2],
                initial_mean=reference_mean,
            ),
        )
    except (OverflowError, ValueError) as error:
        raise _invalid(
            f"invalid ACD optimizer parameters: {error}",
            corrective_action="refit finite durations under the stationary ACD transform",
        ) from error
    if not math.isclose(final_loss, checked_loss, rel_tol=1e-8, abs_tol=1e-8):
        raise _invalid(
            "invalid ACD optimizer result: final loss does not match fitted parameters",
            corrective_action="refit under the fixed deterministic ACD solver policy",
        )
    return fitted


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
            + math.fsum(
                weight * value for weight, value in zip(checked_model.alpha, duration_history, strict=True)
            )
            + math.fsum(
                weight * value
                for weight, value in zip(checked_model.beta, conditional_mean_history, strict=True)
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
        "optimizer_tolerance": _OPTIMIZER_TOLERANCE,
        "optimizer_maximum_iterations": _OPTIMIZER_MAXIMUM_ITERATIONS,
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
