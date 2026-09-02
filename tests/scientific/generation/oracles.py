"""Independent analytical helpers for the bounded scientific test matrix.

This module deliberately imports no traffic-model implementation. The MMPP
moments follow the two-state MAP representation documented in
``architecture/traffic_models/mmpp.md``: ``B = Lambda - Q``, arrival density
``exp(-B t) Lambda``, and first-moment kernel ``B^-2 Lambda``.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

type Matrix2 = tuple[tuple[Fraction, Fraction], tuple[Fraction, Fraction]]
type Vector2 = tuple[Fraction, Fraction]
type PacketMark = tuple[str, int]
type PacketPosition = Literal["first", "interior", "last"]


@dataclass(frozen=True, slots=True)
class PacketTrainOracle:
    """Independent fitted quantities for one hand-sized packet-train trace."""

    actual_lengths: tuple[tuple[int, tuple[int, ...]], ...]
    conditional_inter_gaps: tuple[tuple[tuple[float, ...], ...], ...]
    gap_threshold: float
    initial_probabilities: tuple[float, ...]
    inter_gaps: tuple[float, ...]
    position_mark_counts: tuple[tuple[int, PacketPosition, tuple[tuple[PacketMark, int], ...]], ...]
    state_order: tuple[int, ...]
    train_bounds: tuple[tuple[int, int], ...]
    transition_rows: tuple[tuple[float, ...], ...]
    within_gaps: tuple[tuple[int, PacketPosition, tuple[float, ...]], ...]


def _type7(values: tuple[float, ...], quantile: float) -> float:
    ordered = tuple(sorted(values))
    coordinate = (len(ordered) - 1) * quantile
    lower = math.floor(coordinate)
    upper = math.ceil(coordinate)
    fraction = coordinate - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def packet_train_oracle(
    timestamps: tuple[float, ...],
    marks: tuple[PacketMark, ...],
    *,
    length_cap: int,
) -> PacketTrainOracle:
    """Calculate segmentation and fitted tables without production model helpers."""
    if len(timestamps) != len(marks) or len(timestamps) < 2 or not 3 <= length_cap <= 8:
        raise ValueError("packet-train oracle requires aligned events and a cap in 3..8")
    gaps = tuple(right - left for left, right in zip(timestamps, timestamps[1:], strict=False))
    threshold = _type7(gaps, 0.9)
    starts = [0]
    for destination, gap in enumerate(gaps, start=1):
        if gap > threshold:
            starts.append(destination)
    stops = (*starts[1:], len(timestamps))
    bounds = tuple(zip(starts, stops, strict=True))
    lengths = tuple(stop - start for start, stop in bounds)
    state_values = tuple(min(length, length_cap) for length in lengths)
    state_order = tuple(dict.fromkeys(state_values))
    index_by_state = {state: index for index, state in enumerate(state_order)}
    state_indices = tuple(index_by_state[state] for state in state_values)
    state_count = len(state_order)

    occupancy = tuple(state_indices.count(index) / len(state_indices) for index in range(state_count))
    counts = [[0 for _ in range(state_count)] for _ in range(state_count)]
    conditional: list[list[list[float]]] = [[[] for _ in range(state_count)] for _ in range(state_count)]
    inter_gaps: list[float] = []
    for train_index, (source, destination) in enumerate(zip(state_indices, state_indices[1:], strict=False)):
        gap = timestamps[bounds[train_index + 1][0]] - timestamps[bounds[train_index][1] - 1]
        counts[source][destination] += 1
        conditional[source][destination].append(gap)
        inter_gaps.append(gap)
    rows = tuple(tuple((count + 1.0) / (sum(row) + state_count) for count in row) for row in counts)

    actual: dict[int, list[int]] = {state: [] for state in state_order}
    within: dict[tuple[int, PacketPosition], list[float]] = {
        (state, position): [] for state in state_order for position in ("first", "interior", "last")
    }
    position_marks: dict[tuple[int, PacketPosition], Counter[PacketMark]] = {
        (state, position): Counter() for state in state_order for position in ("first", "interior", "last")
    }
    for (start, stop), state in zip(bounds, state_values, strict=True):
        length = stop - start
        actual[state].append(length)
        for offset, event_index in enumerate(range(start, stop)):
            position: PacketPosition
            if offset == 0:
                position = "first"
            elif offset == length - 1:
                position = "last"
            else:
                position = "interior"
            position_marks[state, position][marks[event_index]] += 1
            if offset > 0:
                within[state, position].append(timestamps[event_index] - timestamps[event_index - 1])

    return PacketTrainOracle(
        actual_lengths=tuple((state, tuple(actual[state])) for state in state_order),
        conditional_inter_gaps=tuple(tuple(tuple(cell) for cell in row) for row in conditional),
        gap_threshold=threshold,
        initial_probabilities=occupancy,
        inter_gaps=tuple(inter_gaps),
        position_mark_counts=tuple(
            (state, position, tuple(counter.items())) for (state, position), counter in position_marks.items()
        ),
        state_order=state_order,
        train_bounds=bounds,
        transition_rows=rows,
        within_gaps=tuple((state, position, tuple(sample)) for (state, position), sample in within.items()),
    )


def empirical_mean(values: tuple[float, ...] | list[float]) -> float:
    """Return the arithmetic mean of one nonempty finite sample."""
    if not values:
        raise ValueError("empirical mean requires a nonempty sample")
    return math.fsum(values) / len(values)


def nhpp_bin_mean(rate: float, width: float) -> float:
    """Return the analytical count mean for one constant-intensity NHPP bin."""
    if not math.isfinite(rate) or rate < 0.0 or not math.isfinite(width) or width <= 0.0:
        raise ValueError("NHPP oracle requires a finite nonnegative rate and positive width")
    return rate * width


def nhpp_integrated_intensity(rates: tuple[float, ...], width: float) -> float:
    """Return the analytical total mean by integrating equal-bin intensity."""
    return math.fsum(nhpp_bin_mean(rate, width) for rate in rates)


def acd_stationary_mean(omega: float, alpha: tuple[float, ...], beta: tuple[float, ...]) -> float:
    """Return the analytical stationary mean of one ACD recursion."""
    persistence = math.fsum((*alpha, *beta))
    if not math.isfinite(omega) or omega <= 0.0 or not 0.0 <= persistence < 1.0:
        raise ValueError("ACD oracle requires positive omega and stationary nonnegative coefficients")
    mean = omega / (1.0 - persistence)
    if not math.isfinite(mean):
        raise ValueError("ACD oracle stationary mean must be finite")
    return mean


def acd_conditional_means(
    durations: tuple[float, ...],
    *,
    omega: float,
    alpha: tuple[float, ...],
    beta: tuple[float, ...],
    initial_mean: float,
) -> tuple[float, ...]:
    """Evaluate the ACD recursion independently with fixed mean prehistory."""
    if len(alpha) != len(beta) or not 1 <= len(alpha) <= 3:
        raise ValueError("ACD oracle requires matching orders in 1..3")
    if any(not math.isfinite(value) or value < 0.0 for value in durations):
        raise ValueError("ACD oracle durations must be finite and nonnegative")
    if not math.isfinite(initial_mean) or initial_mean <= 0.0:
        raise ValueError("ACD oracle initial mean must be finite and positive")
    result: list[float] = []
    order = len(alpha)
    for index in range(len(durations)):
        duration_lags = tuple(durations[index - lag] if index >= lag else initial_mean for lag in range(1, order + 1))
        mean_lags = tuple(result[index - lag] if index >= lag else initial_mean for lag in range(1, order + 1))
        result.append(
            omega
            + math.fsum(weight * value for weight, value in zip(alpha, duration_lags, strict=True))
            + math.fsum(weight * value for weight, value in zip(beta, mean_lags, strict=True))
        )
    return tuple(result)


def acd_unit_innovations(durations: tuple[float, ...], conditional_means: tuple[float, ...]) -> tuple[float, ...]:
    """Recover dimensionless ACD innovations from independently recursed means."""
    if len(durations) != len(conditional_means) or not durations:
        raise ValueError("ACD oracle requires equally sized nonempty duration and mean vectors")
    if any(not math.isfinite(value) or value < 0.0 for value in durations):
        raise ValueError("ACD oracle durations must be finite and nonnegative")
    if any(not math.isfinite(value) or value <= 0.0 for value in conditional_means):
        raise ValueError("ACD oracle conditional means must be finite and positive")
    return tuple(duration / mean for duration, mean in zip(durations, conditional_means, strict=True))


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
