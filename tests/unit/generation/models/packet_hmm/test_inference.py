"""Independent tiny-case tests for stable categorical-HMM inference."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

import trafficlab.generation.models.packet_hmm.inference as inference_module
from trafficlab.generation.models.packet_hmm.inference import (
    ForwardBackwardResult,
    canonicalize_states,
    fit_baum_welch,
    fixed_initial_parameters,
    forward_backward,
)


def _enumerated_likelihood(
    observations: tuple[int, ...],
    initial: tuple[float, ...],
    transitions: tuple[tuple[float, ...], ...],
    emissions: tuple[tuple[float, ...], ...],
) -> float:
    total = 0.0
    for path in itertools.product(range(len(initial)), repeat=len(observations)):
        probability = initial[path[0]] * emissions[path[0]][observations[0]]
        for index in range(1, len(path)):
            probability *= transitions[path[index - 1]][path[index]]
            probability *= emissions[path[index]][observations[index]]
        total += probability
    return total


def test_scaled_forward_likelihood_and_posteriors_match_enumeration() -> None:
    """Dropping a transition/emission factor or using the wrong scaling index breaks this literal path sum."""
    observations = (0, 1, 0)
    initial = (0.6, 0.4)
    transitions = ((0.7, 0.3), (0.2, 0.8))
    emissions = ((0.9, 0.1), (0.25, 0.75))

    result = forward_backward(observations, initial, transitions, emissions)

    expected = _enumerated_likelihood(observations, initial, transitions, emissions)
    assert math.exp(result.log_likelihood) == pytest.approx(expected, abs=1e-15)
    assert result.gamma.sum(axis=1) == pytest.approx((1.0, 1.0, 1.0), abs=1e-14)
    assert result.xi.sum(axis=(1, 2)) == pytest.approx((1.0, 1.0), abs=1e-14)
    assert result.xi.sum(axis=2) == pytest.approx(result.gamma[:-1], abs=1e-14)
    assert result.xi.sum(axis=1) == pytest.approx(result.gamma[1:], abs=1e-14)


def test_fixed_initialization_is_repeatable_positive_and_normalized() -> None:
    """Random or zero initialization would make EM irreproducible or create impossible observed symbols."""
    first = fixed_initial_parameters(state_count=3, symbol_count=5)
    second = fixed_initial_parameters(state_count=3, symbol_count=5)

    assert first == second
    assert first.initial_probabilities == pytest.approx((1.0 / 6.0, 2.0 / 6.0, 3.0 / 6.0))
    assert all(sum(row) == pytest.approx(1.0) for row in first.transition_rows)
    assert all(sum(row) == pytest.approx(1.0) for row in first.emission_rows)
    assert min(value for row in first.transition_rows for value in row) > 0.0
    assert min(value for row in first.emission_rows for value in row) > 0.0


def test_baum_welch_is_bounded_smoothed_monotone_and_repeatable() -> None:
    """An unbounded, unsmoothed, likelihood-decreasing, or random EM update violates the estimator contract."""
    observations = (0, 0, 1, 1, 0, 2, 2, 2, 1, 0)
    first = fit_baum_welch(
        observations,
        state_count=2,
        symbol_count=3,
        symbol_iat_means=(0.0, 1.0, 4.0),
        maximum_iterations=7,
        tolerance=1e-12,
        smoothing=0.001,
    )
    second = fit_baum_welch(
        observations,
        state_count=2,
        symbol_count=3,
        symbol_iat_means=(0.0, 1.0, 4.0),
        maximum_iterations=7,
        tolerance=1e-12,
        smoothing=0.001,
    )

    assert first == second
    assert first.diagnostics.iterations <= 7
    assert len(first.diagnostics.log_likelihoods) == first.diagnostics.iterations + 1
    assert all(
        right + 1e-10 >= left
        for left, right in zip(
            first.diagnostics.log_likelihoods,
            first.diagnostics.log_likelihoods[1:],
            strict=False,
        )
    )
    assert min(first.parameters.initial_probabilities) > 0.0
    assert min(value for row in first.parameters.transition_rows for value in row) > 0.0
    assert min(value for row in first.parameters.emission_rows for value in row) > 0.0


def test_iteration_cap_persists_explicit_nonconvergence() -> None:
    """Silently claiming convergence at the cap would misstate fitted-model evidence."""
    result = fit_baum_welch(
        (0, 1, 0, 1, 1, 0),
        state_count=2,
        symbol_count=2,
        symbol_iat_means=(1.0, 2.0),
        maximum_iterations=1,
        tolerance=0.0,
        smoothing=0.001,
    )

    assert result.diagnostics.iterations == 1
    assert result.diagnostics.converged is False


def test_backtracking_uses_exact_halves_of_the_original_m_step_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blending an already blended candidate would skip the required quarter-step acceptance point."""
    gamma = np.asarray(((0.8, 0.2), (0.2, 0.8)), dtype=np.float64)
    xi = np.asarray((((0.1, 0.7), (0.1, 0.1)),), dtype=np.float64)
    seen_emission_00: list[float] = []
    proposed_emission_00 = 0.801 / 1.002
    accepted_emission_00 = 0.75 + 0.125 * (proposed_emission_00 - 0.75)

    def controlled_forward(
        observations: object,
        initial_probabilities: object,
        transition_rows: object,
        emission_rows: object,
    ) -> ForwardBackwardResult:
        del observations, initial_probabilities, transition_rows
        emission_00 = float(tuple(tuple(row) for row in emission_rows)[0][0])  # type: ignore[arg-type]
        seen_emission_00.append(emission_00)
        fraction = (emission_00 - 0.75) / (proposed_emission_00 - 0.75)
        log_likelihood = 0.0 if len(seen_emission_00) == 1 or math.isclose(fraction, 0.125, abs_tol=1e-14) else -1.0
        return ForwardBackwardResult(
            log_likelihood=log_likelihood,
            alpha=gamma.copy(),
            beta=np.ones_like(gamma),
            gamma=gamma.copy(),
            xi=xi.copy(),
            scales=np.ones(2, dtype=np.float64),
        )

    monkeypatch.setattr(inference_module, "forward_backward", controlled_forward)

    result = fit_baum_welch(
        (0, 1),
        state_count=2,
        symbol_count=2,
        symbol_iat_means=(1.0, 2.0),
        maximum_iterations=1,
        tolerance=0.0,
        smoothing=0.001,
    )

    assert result.parameters.emission_rows[0][0] == pytest.approx(accepted_emission_00, abs=1e-15)
    assert seen_emission_00[:5] == pytest.approx(
        (0.75, proposed_emission_00, 0.7747005988023952, 0.7623502994011976, accepted_emission_00)
    )
    assert result.diagnostics == inference_module.BaumWelchDiagnostics(True, 1, (0.0, 0.0))


def test_canonicalization_is_invariant_to_input_label_permutation() -> None:
    """Swapping latent labels must not change the serialized fitted parameters."""
    canonical = canonicalize_states(
        (0.4, 0.6),
        ((0.8, 0.2), (0.3, 0.7)),
        ((0.9, 0.1), (0.2, 0.8)),
        symbol_iat_means=(1.0, 5.0),
    )
    permuted = canonicalize_states(
        (0.6, 0.4),
        ((0.7, 0.3), (0.2, 0.8)),
        ((0.2, 0.8), (0.9, 0.1)),
        symbol_iat_means=(1.0, 5.0),
    )

    assert canonical == permuted
    expected_iats = tuple(
        sum(probability * mean for probability, mean in zip(row, (1.0, 5.0), strict=True))
        for row in canonical.emission_rows
    )
    assert expected_iats == tuple(sorted(expected_iats))


@pytest.mark.parametrize(
    ("observations", "initial", "transitions", "emissions", "message"),
    (
        ((), (0.5, 0.5), ((0.5, 0.5), (0.5, 0.5)), ((0.5, 0.5), (0.5, 0.5)), "observation"),
        ((2,), (0.5, 0.5), ((0.5, 0.5), (0.5, 0.5)), ((0.5, 0.5), (0.5, 0.5)), "symbol"),
        ((0,), (0.4, 0.4), ((0.5, 0.5), (0.5, 0.5)), ((0.5, 0.5), (0.5, 0.5)), "sum to one"),
        ((0,), (0.5, 0.5), ((1.0,), (0.5, 0.5)), ((0.5, 0.5), (0.5, 0.5)), "K x K"),
        ((0,), (0.5, 0.5), ((0.5, 0.5), (0.5, 0.5)), ((np.nan, 1.0), (0.5, 0.5)), "finite"),
    ),
)
def test_forward_backward_rejects_malformed_inputs(
    observations: tuple[int, ...],
    initial: tuple[float, ...],
    transitions: tuple[tuple[float, ...], ...],
    emissions: tuple[tuple[float, ...], ...],
    message: str,
) -> None:
    """Malformed categorical tables must fail before scaled recursion produces misleading numbers."""
    with pytest.raises(ValueError, match=message):
        forward_backward(observations, initial, transitions, emissions)
