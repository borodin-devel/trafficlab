"""Stable deterministic inference for a small categorical hidden Markov model."""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray

_ROW_TOLERANCE = 1e-12
_MONOTONE_TOLERANCE = 1e-10


def _object_tuple(value: object, *, context: str) -> tuple[object, ...]:
    if not isinstance(value, Iterable):
        raise ValueError(f"{context} must be a finite sequence")
    return tuple(cast(Iterable[object], value))


@dataclass(frozen=True, slots=True)
class HmmParameters:
    """Canonical immutable HMM probability tables."""

    initial_probabilities: tuple[float, ...]
    transition_rows: tuple[tuple[float, ...], ...]
    emission_rows: tuple[tuple[float, ...], ...]


@dataclass(frozen=True, slots=True)
class ForwardBackwardResult:
    """Scaled forward/backward state and transition posteriors."""

    log_likelihood: float
    alpha: NDArray[np.float64]
    beta: NDArray[np.float64]
    gamma: NDArray[np.float64]
    xi: NDArray[np.float64]
    scales: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class BaumWelchDiagnostics:
    """Bounded convergence evidence retained with every fitted model."""

    converged: bool
    iterations: int
    log_likelihoods: tuple[float, ...]

    def __post_init__(self) -> None:
        if type(self.converged) is not bool:
            raise TypeError("converged must be a bool")
        if type(self.iterations) is not int or self.iterations < 0:
            raise ValueError("iterations must be a nonnegative exact integer")
        if type(self.log_likelihoods) is not tuple or len(self.log_likelihoods) != self.iterations + 1:
            raise ValueError("log_likelihoods must contain iterations plus one values")
        if any(type(value) is not float or not math.isfinite(value) for value in self.log_likelihoods):
            raise ValueError("log_likelihoods must contain finite exact floats")
        if any(
            right + _MONOTONE_TOLERANCE < left
            for left, right in zip(self.log_likelihoods, self.log_likelihoods[1:], strict=False)
        ):
            raise ValueError("log_likelihoods must be nondecreasing within tolerance")


@dataclass(frozen=True, slots=True)
class BaumWelchResult:
    """One deterministic bounded EM result."""

    parameters: HmmParameters
    diagnostics: BaumWelchDiagnostics


def _as_probability_vector(value: object, *, length: int, context: str) -> NDArray[np.float64]:
    items = _object_tuple(value, context=context)
    if len(items) != length:
        raise ValueError(f"{context} must contain {length} probabilities")
    if any(type(item) is not float or not math.isfinite(item) or item < 0.0 for item in items):
        raise ValueError(f"{context} must contain finite nonnegative exact floats")
    vector = np.asarray(items, dtype=np.float64)
    if not math.isclose(float(vector.sum()), 1.0, rel_tol=0.0, abs_tol=_ROW_TOLERANCE):
        raise ValueError(f"{context} must sum to one")
    return vector


def _validated_inputs(
    observations: object,
    initial_probabilities: object,
    transition_rows: object,
    emission_rows: object,
) -> tuple[NDArray[np.intp], NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    raw_observations = _object_tuple(observations, context="observations")
    raw_initial = _object_tuple(initial_probabilities, context="initial probabilities")
    raw_transitions = _object_tuple(transition_rows, context="transition rows")
    raw_emissions = _object_tuple(emission_rows, context="emission rows")
    if not raw_observations or any(type(symbol) is not int or symbol < 0 for symbol in raw_observations):
        raise ValueError("observations must be a nonempty sequence of nonnegative exact symbol indices")
    checked_observations = cast(tuple[int, ...], raw_observations)
    state_count = len(raw_initial)
    if state_count == 0:
        raise ValueError("initial probabilities must not be empty")
    initial = _as_probability_vector(raw_initial, length=state_count, context="initial probabilities")
    if len(raw_transitions) != state_count:
        raise ValueError("transition rows must be a K x K matrix")
    try:
        transitions = np.vstack(
            [_as_probability_vector(row, length=state_count, context="transition row") for row in raw_transitions]
        )
    except ValueError as error:
        if "contain" in str(error):
            raise ValueError("transition rows must be a K x K matrix") from error
        raise
    if len(raw_emissions) != state_count:
        raise ValueError("emission rows must contain K rows")
    first_emission = _object_tuple(raw_emissions[0], context="emission row")
    symbol_count = len(first_emission)
    if symbol_count == 0:
        raise ValueError("emission rows must not be empty")
    emissions = np.vstack(
        [_as_probability_vector(row, length=symbol_count, context="emission row") for row in raw_emissions]
    )
    if any(symbol >= symbol_count for symbol in checked_observations):
        raise ValueError("observation symbol is outside the emission vocabulary")
    return (np.asarray(checked_observations, dtype=np.intp), initial, transitions, emissions)


def forward_backward(
    observations: object,
    initial_probabilities: object,
    transition_rows: object,
    emission_rows: object,
) -> ForwardBackwardResult:
    """Evaluate one finite categorical HMM using per-time scaling."""
    symbols, initial, transitions, emissions = _validated_inputs(
        observations, initial_probabilities, transition_rows, emission_rows
    )
    time_count = len(symbols)
    state_count = len(initial)
    alpha = np.empty((time_count, state_count), dtype=np.float64)
    scales = np.empty(time_count, dtype=np.float64)
    alpha[0] = initial * emissions[:, symbols[0]]
    scales[0] = alpha[0].sum()
    if not math.isfinite(float(scales[0])) or scales[0] <= 0.0:
        raise ValueError("observation sequence has zero or nonfinite probability")
    alpha[0] /= scales[0]
    for time in range(1, time_count):
        alpha[time] = (alpha[time - 1] @ transitions) * emissions[:, symbols[time]]
        scales[time] = alpha[time].sum()
        if not math.isfinite(float(scales[time])) or scales[time] <= 0.0:
            raise ValueError("observation sequence has zero or nonfinite probability")
        alpha[time] /= scales[time]

    beta = np.empty_like(alpha)
    beta[-1] = 1.0
    for time in range(time_count - 2, -1, -1):
        beta[time] = transitions @ (emissions[:, symbols[time + 1]] * beta[time + 1])
        beta[time] /= scales[time + 1]
    gamma = alpha * beta
    gamma_sums = gamma.sum(axis=1)
    if np.any(~np.isfinite(gamma_sums)) or np.any(gamma_sums <= 0.0):
        raise ValueError("state posteriors are zero or nonfinite")
    gamma /= gamma_sums[:, np.newaxis]

    xi = np.empty((max(0, time_count - 1), state_count, state_count), dtype=np.float64)
    for time in range(time_count - 1):
        xi[time] = (
            alpha[time, :, np.newaxis] * transitions * (emissions[:, symbols[time + 1]] * beta[time + 1])[np.newaxis, :]
        )
        denominator = xi[time].sum()
        if not math.isfinite(float(denominator)) or denominator <= 0.0:
            raise ValueError("transition posteriors are zero or nonfinite")
        xi[time] /= denominator
    log_likelihood = float(np.log(scales).sum())
    if not math.isfinite(log_likelihood):
        raise ValueError("HMM log likelihood must be finite")
    return ForwardBackwardResult(log_likelihood, alpha, beta, gamma, xi, scales)


def fixed_initial_parameters(*, state_count: int, symbol_count: int) -> HmmParameters:
    """Build the fixed positive cyclic initialization used by every fit."""
    if type(state_count) is not int or not 2 <= state_count <= 4:
        raise ValueError("state_count must be an exact integer in 2..4")
    if type(symbol_count) is not int or symbol_count < 1:
        raise ValueError("symbol_count must be a positive exact integer")
    initial_total = state_count * (state_count + 1) / 2.0
    initial = tuple((state + 1) / initial_total for state in range(state_count))
    transitions = tuple(
        tuple(
            (state_count + 1.0 if source == destination else 1.0) / (2.0 * state_count)
            for destination in range(state_count)
        )
        for source in range(state_count)
    )
    emission_rows: list[tuple[float, ...]] = []
    for state in range(state_count):
        weights = tuple(1.0 + state_count if symbol % state_count == state else 1.0 for symbol in range(symbol_count))
        total = math.fsum(weights)
        emission_rows.append(tuple(weight / total for weight in weights))
    return HmmParameters(initial, transitions, tuple(emission_rows))


def _parameter_arrays(
    parameters: HmmParameters,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    return (
        np.asarray(parameters.initial_probabilities, dtype=np.float64),
        np.asarray(parameters.transition_rows, dtype=np.float64),
        np.asarray(parameters.emission_rows, dtype=np.float64),
    )


def _as_parameters(
    initial: NDArray[np.float64], transitions: NDArray[np.float64], emissions: NDArray[np.float64]
) -> HmmParameters:
    return HmmParameters(
        tuple(float(value) for value in initial),
        tuple(tuple(float(value) for value in row) for row in transitions),
        tuple(tuple(float(value) for value in row) for row in emissions),
    )


def canonicalize_states(
    initial_probabilities: object,
    transition_rows: object,
    emission_rows: object,
    *,
    symbol_iat_means: object,
) -> HmmParameters:
    """Choose the permutation ordered by expected IAT, emission, then transition values."""
    # One arbitrary in-range symbol is sufficient to reuse the complete table validator.
    _, initial, transitions, emissions = _validated_inputs((0,), initial_probabilities, transition_rows, emission_rows)
    raw_means = _object_tuple(symbol_iat_means, context="symbol IAT means")
    if len(raw_means) != emissions.shape[1] or any(
        type(value) is not float or not math.isfinite(value) or value < 0.0 for value in raw_means
    ):
        raise ValueError("symbol IAT means must contain one finite nonnegative exact float per symbol")
    means = np.asarray(raw_means, dtype=np.float64)
    expected_iats = emissions @ means
    state_count = len(initial)
    sorted_expectations = tuple(sorted(float(value) for value in expected_iats))
    candidates: list[tuple[tuple[float, ...], tuple[int, ...]]] = []
    for permutation in itertools.permutations(range(state_count)):
        if tuple(float(expected_iats[index]) for index in permutation) != sorted_expectations:
            continue
        emission_key = tuple(float(emissions[index, symbol]) for index in permutation for symbol in range(len(means)))
        transition_key = tuple(
            float(transitions[source, destination]) for source in permutation for destination in permutation
        )
        initial_key = tuple(float(initial[index]) for index in permutation)
        candidates.append(((*sorted_expectations, *emission_key, *transition_key, *initial_key), permutation))
    permutation = min(candidates)[1]
    ordered = np.asarray(permutation, dtype=np.intp)
    return _as_parameters(initial[ordered], transitions[np.ix_(ordered, ordered)], emissions[ordered])


def _blend_parameters(current: HmmParameters, candidate: HmmParameters, fraction: float) -> HmmParameters:
    current_initial, current_transitions, current_emissions = _parameter_arrays(current)
    next_initial, next_transitions, next_emissions = _parameter_arrays(candidate)
    return _as_parameters(
        current_initial + fraction * (next_initial - current_initial),
        current_transitions + fraction * (next_transitions - current_transitions),
        current_emissions + fraction * (next_emissions - current_emissions),
    )


def fit_baum_welch(
    observations: object,
    *,
    state_count: int,
    symbol_count: int,
    symbol_iat_means: object,
    maximum_iterations: int,
    tolerance: float,
    smoothing: float,
) -> BaumWelchResult:
    """Fit positive categorical tables with bounded monotone generalized EM updates."""
    if type(maximum_iterations) is not int or maximum_iterations < 1:
        raise ValueError("maximum_iterations must be a positive exact integer")
    if type(tolerance) is not float or not math.isfinite(tolerance) or tolerance < 0.0:
        raise ValueError("tolerance must be a finite nonnegative exact float")
    if type(smoothing) is not float or not math.isfinite(smoothing) or smoothing <= 0.0:
        raise ValueError("smoothing must be a finite positive exact float")
    parameters = fixed_initial_parameters(state_count=state_count, symbol_count=symbol_count)
    # This validates observations and means before any iterative work.
    current = forward_backward(
        observations,
        parameters.initial_probabilities,
        parameters.transition_rows,
        parameters.emission_rows,
    )
    symbols = cast(tuple[int, ...], _object_tuple(observations, context="observations"))
    canonicalize_states(
        parameters.initial_probabilities,
        parameters.transition_rows,
        parameters.emission_rows,
        symbol_iat_means=symbol_iat_means,
    )
    symbol_array = np.asarray(symbols, dtype=np.intp)
    history = [current.log_likelihood]
    converged = False

    for _iteration in range(maximum_iterations):
        initial = (current.gamma[0] + smoothing) / (1.0 + smoothing * state_count)
        transition_counts = current.xi.sum(axis=0)
        transition_denominators = current.gamma[:-1].sum(axis=0) + smoothing * state_count
        transitions = (transition_counts + smoothing) / transition_denominators[:, np.newaxis]
        emission_counts = np.zeros((state_count, symbol_count), dtype=np.float64)
        for symbol in range(symbol_count):
            emission_counts[:, symbol] = current.gamma[symbol_array == symbol].sum(axis=0)
        emission_denominators = current.gamma.sum(axis=0) + smoothing * symbol_count
        emissions = (emission_counts + smoothing) / emission_denominators[:, np.newaxis]
        candidate = _as_parameters(initial, transitions, emissions)
        candidate_result = forward_backward(
            symbols, candidate.initial_probabilities, candidate.transition_rows, candidate.emission_rows
        )
        fraction = 1.0
        while candidate_result.log_likelihood + _MONOTONE_TOLERANCE < current.log_likelihood:
            fraction *= 0.5
            if fraction < 2.0**-52:
                candidate = parameters
                candidate_result = current
                break
            candidate = _blend_parameters(parameters, candidate, fraction)
            candidate_result = forward_backward(
                symbols, candidate.initial_probabilities, candidate.transition_rows, candidate.emission_rows
            )
        improvement = candidate_result.log_likelihood - current.log_likelihood
        parameters = candidate
        current = candidate_result
        history.append(current.log_likelihood)
        if 0.0 <= improvement <= tolerance:
            converged = True
            break

    canonical = canonicalize_states(
        parameters.initial_probabilities,
        parameters.transition_rows,
        parameters.emission_rows,
        symbol_iat_means=symbol_iat_means,
    )
    diagnostics = BaumWelchDiagnostics(converged, len(history) - 1, tuple(history))
    return BaumWelchResult(canonical, diagnostics)
