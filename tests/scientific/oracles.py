"""Independent analytical helpers for the bounded scientific test matrix.

This module deliberately imports no traffic-model implementation. The MMPP
moments follow the two-state MAP representation documented in
``architecture/traffic_models/mmpp.md``: ``B = Lambda - Q``, arrival density
``exp(-B t) Lambda``, and first-moment kernel ``B^-2 Lambda``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

type Matrix2 = tuple[tuple[Fraction, Fraction], tuple[Fraction, Fraction]]
type Vector2 = tuple[Fraction, Fraction]


def empirical_mean(values: tuple[float, ...] | list[float]) -> float:
    """Return the arithmetic mean of one nonempty finite sample."""
    if not values:
        raise ValueError("empirical mean requires a nonempty sample")
    return math.fsum(values) / len(values)


def empirical_cdf(values: tuple[float, ...], threshold: float) -> float:
    """Return the empirical CDF using the closed ``x <= threshold`` event."""
    if not values:
        raise ValueError("empirical CDF requires a nonempty sample")
    return sum(value <= threshold for value in values) / len(values)


def lag_one_covariance(values: tuple[float, ...]) -> float:
    """Return the stationary lag-one covariance estimate about the sample mean."""
    if len(values) < 2:
        raise ValueError("lag-one covariance requires at least two values")
    mean = empirical_mean(values)
    return math.fsum((left - mean) * (right - mean) for left, right in zip(values, values[1:], strict=False)) / (
        len(values) - 1
    )


def markov_stationary_distribution(
    kernel: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[float, float]:
    """Solve the stationary law of an irreducible two-state transition matrix."""
    p01 = kernel[0][1]
    p10 = kernel[1][0]
    normalizer = p01 + p10
    if not math.isfinite(normalizer) or normalizer <= 0.0:
        raise ValueError("two-state kernel must have positive finite cross-transition mass")
    return (p10 / normalizer, p01 / normalizer)


def _matrix_product(left: Matrix2, right: Matrix2) -> Matrix2:
    return (
        (
            left[0][0] * right[0][0] + left[0][1] * right[1][0],
            left[0][0] * right[0][1] + left[0][1] * right[1][1],
        ),
        (
            left[1][0] * right[0][0] + left[1][1] * right[1][0],
            left[1][0] * right[0][1] + left[1][1] * right[1][1],
        ),
    )


def _matrix_vector_product(matrix: Matrix2, vector: Vector2) -> Vector2:
    return (
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1],
    )


def _row_vector_product(vector: Vector2, matrix: Matrix2) -> Vector2:
    return (
        vector[0] * matrix[0][0] + vector[1] * matrix[1][0],
        vector[0] * matrix[0][1] + vector[1] * matrix[1][1],
    )


def _inverse(matrix: Matrix2) -> Matrix2:
    determinant = matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
    if determinant == 0:
        raise ValueError("two-state matrix must be invertible")
    return (
        (matrix[1][1] / determinant, -matrix[0][1] / determinant),
        (-matrix[1][0] / determinant, matrix[0][0] / determinant),
    )


def _dot(left: Vector2, right: Vector2) -> Fraction:
    return left[0] * right[0] + left[1] * right[1]


@dataclass(frozen=True, slots=True)
class MmppMoments:
    """Exact two-state MMPP expectations used by the acceptance tests."""

    time_stationary: Vector2
    arrival_epoch: Vector2
    mean_rate: Fraction
    mean_iat: Fraction
    adjacent_iat_covariance: Fraction


def mmpp_moments(*, q01: int, q10: int, lambda0: int, lambda1: int) -> MmppMoments:
    """Derive exact CTMC, arrival-epoch, rate, and adjacent-IAT moments.

    With ``B = Lambda - Q``, ``B^-1 1`` gives the conditional mean
    interarrival vector and ``B^-2 Lambda`` is the first-moment transition
    kernel. Therefore ``E[T_n T_(n+1)] = a B^-2 Lambda B^-1 1``.
    """
    rates = tuple(Fraction(value) for value in (q01, q10, lambda0, lambda1))
    if any(value <= 0 for value in rates):
        raise ValueError("MMPP oracle rates must be positive")
    q01_value, q10_value, lambda0_value, lambda1_value = rates
    transition_total = q01_value + q10_value
    time_stationary = (q10_value / transition_total, q01_value / transition_total)
    mean_rate = time_stationary[0] * lambda0_value + time_stationary[1] * lambda1_value
    arrival_epoch = (
        time_stationary[0] * lambda0_value / mean_rate,
        time_stationary[1] * lambda1_value / mean_rate,
    )

    b_matrix: Matrix2 = (
        (lambda0_value + q01_value, -q01_value),
        (-q10_value, lambda1_value + q10_value),
    )
    arrival_matrix: Matrix2 = ((lambda0_value, Fraction(0)), (Fraction(0), lambda1_value))
    b_inverse = _inverse(b_matrix)
    ones: Vector2 = (Fraction(1), Fraction(1))
    conditional_mean = _matrix_vector_product(b_inverse, ones)
    mean_iat = _dot(arrival_epoch, conditional_mean)
    first_moment_kernel = _matrix_product(_matrix_product(b_inverse, b_inverse), arrival_matrix)
    joint_moment = _dot(_row_vector_product(arrival_epoch, first_moment_kernel), conditional_mean)
    covariance = joint_moment - mean_iat * mean_iat
    return MmppMoments(
        time_stationary=time_stationary,
        arrival_epoch=arrival_epoch,
        mean_rate=mean_rate,
        mean_iat=mean_iat,
        adjacent_iat_covariance=covariance,
    )
