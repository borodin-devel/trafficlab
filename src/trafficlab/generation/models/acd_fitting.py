"""Deterministic fitting mechanics for the exponential ACD family."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any, Protocol, cast

import numpy as np
from numpy.typing import NDArray
from scipy import optimize as scipy_optimize  # pyright: ignore[reportMissingTypeStubs]

from trafficlab.common.errors import TrafficlabError

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

optimizer_maximum_iterations = _OPTIMIZER_MAXIMUM_ITERATIONS
optimizer_tolerance = _OPTIMIZER_TOLERANCE


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
        type(value) is not float or not math.isfinite(value) or value < 0.0 or value >= 1.0 for value in coefficients
    ):
        raise ValueError("ACD coefficients must be finite floats in [0, 1)")
    if math.fsum(coefficients) >= 1.0:
        raise ValueError("ACD coefficients must have sum below one")
    mean = _validate_reference_mean(initial_mean)
    order = len(alpha)
    result: list[float] = []
    for index in range(len(values)):
        duration_part = math.fsum(
            alpha[lag - 1] * (values[index - lag] if index >= lag else mean) for lag in range(1, order + 1)
        )
        mean_part = math.fsum(
            beta[lag - 1] * (result[index - lag] if index >= lag else mean) for lag in range(1, order + 1)
        )
        conditional_mean = omega + duration_part + mean_part
        if not math.isfinite(conditional_mean) or conditional_mean <= 0.0:
            raise ValueError("ACD recursion produced a nonfinite or nonpositive conditional mean")
        result.append(conditional_mean)
    return tuple(result)


def _exponential_negative_log_likelihood(durations: Sequence[float], conditional_means: Sequence[float]) -> float:
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
        duration_lags = tuple(durations[index - lag] if index >= lag else reference_mean for lag in range(1, order + 1))
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


conditional_means = _conditional_means
exponential_negative_log_likelihood = _exponential_negative_log_likelihood
fit_parameters = _fit_parameters
likelihood_and_gradient = _likelihood_and_gradient
transform_parameters = _transform_parameters
