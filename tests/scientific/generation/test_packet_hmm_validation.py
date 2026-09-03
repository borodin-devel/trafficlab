"""Independent scientific validation for the categorical packet HMM."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import math

import numpy as np
import pytest

from tests.scientific.generation.oracles import enumerate_hmm_paths, markov_stationary_distribution
from tests.scientific.generation.test_model_validation import _assert_close, _assert_complete_trace
from trafficlab.common.config import GenerationLimits
from trafficlab.common.trace import Direction
from trafficlab.generation.models.common import MarkCount, MarkDistribution
from trafficlab.generation.models.packet_hmm import (
    BaumWelchDiagnostics,
    PacketCategory,
    PacketHmmFamily,
    PacketHmmModel,
    PacketSample,
    forward_backward,
)

_PACKET_HMM_SEED = 104729
_PACKET_HMM_SAMPLE_SIZE = 40_000
_PACKET_HMM_FREQUENCY_TOLERANCE = 0.015


def test_packet_hmm_forward_backward_matches_independent_hidden_path_oracle() -> None:
    """Scaled recursions must agree with exhaustive hidden-path likelihood and posterior sums."""
    observations = (0, 1, 0)
    initial = (0.4, 0.6)
    transitions = ((0.7, 0.3), (0.2, 0.8))
    emissions = ((0.2, 0.8), (0.85, 0.15))
    oracle = enumerate_hmm_paths(observations, initial, transitions, emissions)

    fitted = forward_backward(observations, initial, transitions, emissions)

    _assert_close(
        "packet-hmm-tiny-likelihood",
        seed=0,
        sample_size=len(observations),
        expected=oracle.likelihood,
        observed=math.exp(fitted.log_likelihood),
        tolerance=1e-14,
    )
    assert fitted.gamma == pytest.approx(np.asarray(oracle.state_posteriors), abs=1e-13)


def _frequency_packet_hmm_model() -> PacketHmmModel:
    return PacketHmmModel(
        additive_smoothing=0.001,
        convergence_tolerance=1e-8,
        diagnostics=BaumWelchDiagnostics(True, 1, (-2.0, -2.0)),
        emission_rows=((0.2, 0.8), (0.85, 0.15)),
        iat_quantiles=(1.0 / 3.0, 2.0 / 3.0),
        iat_thresholds=(1.0, 1.0),
        initial_marks=MarkDistribution((MarkCount(Direction.OUTBOUND, 80, 1),)),
        initial_probabilities=(0.4, 0.6),
        initialization="fixed_cyclic_v1",
        maximum_iterations=100,
        reservoirs=((PacketSample(1.0, 60),), (PacketSample(1.0, 120),)),
        size_quantiles=(1.0 / 3.0, 2.0 / 3.0),
        size_thresholds=(80.0, 100.0),
        state_count=2,
        transition_rows=((0.7, 0.3), (0.2, 0.8)),
        vocabulary=(
            PacketCategory(1, Direction.OUTBOUND, 0),
            PacketCategory(1, Direction.INBOUND, 2),
        ),
    )


def test_packet_hmm_long_run_state_and_emission_frequencies_match_stationary_oracles() -> None:
    """The transition then emission sampler must reproduce independent stationary and mixture frequencies."""
    model = _frequency_packet_hmm_model()
    stationary = markov_stationary_distribution(((0.7, 0.3), (0.2, 0.8)))
    expected_categories = (
        stationary[0] * 0.2 + stationary[1] * 0.85,
        stationary[0] * 0.8 + stationary[1] * 0.15,
    )
    limits = GenerationLimits(
        max_packets=_PACKET_HMM_SAMPLE_SIZE + 2,
        max_output_bytes=10_000_000,
        max_wall_seconds=30.0,
    )

    result = PacketHmmFamily().generate(
        model,
        _PACKET_HMM_SEED,
        float(_PACKET_HMM_SAMPLE_SIZE),
        limits,
        clock=lambda: 0.0,
    )
    events = _assert_complete_trace(result, window=float(_PACKET_HMM_SAMPLE_SIZE))
    diagnostics = dict(result.model_diagnostics)

    assert len(events) == _PACKET_HMM_SAMPLE_SIZE + 1
    for state, expected in enumerate(stationary):
        observed = diagnostics[f"hidden_state_{state}_count"] / _PACKET_HMM_SAMPLE_SIZE
        _assert_close(
            f"packet-hmm-state-{state}",
            seed=_PACKET_HMM_SEED,
            sample_size=_PACKET_HMM_SAMPLE_SIZE,
            expected=expected,
            observed=observed,
            tolerance=_PACKET_HMM_FREQUENCY_TOLERANCE,
        )
    for category, expected in enumerate(expected_categories):
        observed = diagnostics[f"category_{category}_count"] / _PACKET_HMM_SAMPLE_SIZE
        _assert_close(
            f"packet-hmm-category-{category}",
            seed=_PACKET_HMM_SEED,
            sample_size=_PACKET_HMM_SAMPLE_SIZE,
            expected=expected,
            observed=observed,
            tolerance=_PACKET_HMM_FREQUENCY_TOLERANCE,
        )
