"""Packet-by-packet generation, fallback, boundary, and guard tests."""

from __future__ import annotations

import copy

import pytest

from tests.unit.generation.models.markov_packet_train._support import ScriptedTrainRng, two_state_model
from trafficlab.common.config import GenerationLimits
from trafficlab.common.errors import TrafficlabError
from trafficlab.generation.models.markov_packet_train.generation import generate_with_rng
from trafficlab.generation.models.markov_packet_train.model import inter_train_gap_selection

LIMITS = GenerationLimits(max_packets=20, max_output_bytes=10_000, max_wall_seconds=10.0)


def test_generation_uses_scalar_draw_order_and_includes_packet_at_window_endpoint() -> None:
    """Reordering train/packet draws or treating t=W as outside changes PCG64 reproduction."""
    rng = ScriptedTrainRng(
        randoms=(0.0, 0.9, 0.0),
        choices=(0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    )

    result = generate_with_rng(two_state_model(), rng, W=8.0, limits=LIMITS, clock=lambda: 0.0)

    assert result.complete is True
    assert result.reason is None
    assert result.trace.timestamps.tolist() == [0.0, 5.0, 6.0, 8.0]
    assert result.trace.frame_lengths.tolist() == [60, 70, 80, 90]
    assert rng.calls == [
        ("random", None),
        ("choice", 1),
        ("choice", 1),
        ("random", None),
        ("choice", 1),
        ("choice", 1),
        ("choice", 1),
        ("choice", 1),
        ("choice", 1),
        ("choice", 1),
        ("choice", 1),
        ("random", None),
        ("choice", 1),
    ]
    assert dict(result.model_diagnostics) == {
        "timing_tier_global_count": 1,
        "timing_tier_source_count": 0,
        "timing_tier_transition_count": 1,
        "uniform_unobserved_row_count": 1,
    }


@pytest.mark.parametrize(
    ("conditional", "source", "global_gaps", "tier", "sample"),
    (
        ((7.0,), (8.0,), (9.0,), "transition", (7.0,)),
        ((), (8.0,), (9.0,), "source", (8.0,)),
        ((), (), (9.0,), "global", (9.0,)),
    ),
)
def test_inter_train_gap_fallback_uses_transition_then_source_then_global(
    conditional: tuple[float, ...],
    source: tuple[float, ...],
    global_gaps: tuple[float, ...],
    tier: str,
    sample: tuple[float, ...],
) -> None:
    """Skipping a fallback tier would invent timing or reject a supported smoothed transition."""
    assert inter_train_gap_selection(conditional, source, global_gaps) == (tier, sample)


def test_source_fallback_is_counted_for_an_unobserved_transition() -> None:
    """An empty transition cell with an observed source row must not jump directly to global timing."""
    rng = ScriptedTrainRng(randoms=(0.0, 0.0), choices=(0, 0, 0, 0))

    result = generate_with_rng(two_state_model(), rng, W=4.0, limits=LIMITS, clock=lambda: 0.0)

    assert result.complete is True
    assert result.trace.timestamps.tolist() == [0.0]
    assert dict(result.model_diagnostics) == {
        "timing_tier_global_count": 0,
        "timing_tier_source_count": 1,
        "timing_tier_transition_count": 0,
        "uniform_unobserved_row_count": 0,
    }


def test_packet_guard_exhaustion_mid_train_returns_only_diagnostic_prefix() -> None:
    """Checking limits only between trains would overrun the packet bound by the remaining train length."""
    rng = ScriptedTrainRng(randoms=(0.75,), choices=(0, 0, 0, 0))
    limits = GenerationLimits(max_packets=2, max_output_bytes=10_000, max_wall_seconds=10.0)

    result = generate_with_rng(two_state_model(), rng, W=20.0, limits=limits, clock=lambda: 0.0)

    assert result.complete is False
    assert result.reason == "max_packets"
    assert result.trace.timestamps.tolist() == [0.0, 1.0]
    with pytest.raises(TrafficlabError, match="packet limit"):
        result.require_complete()


@pytest.mark.parametrize(
    ("randoms", "choices", "message"),
    (((1.0,), (), "random draw"), ((0.0,), (1,), "empirical random draw")),
)
def test_generation_rejects_rng_endpoint_violations(
    randoms: tuple[float, ...], choices: tuple[int, ...], message: str
) -> None:
    """PCG64 scalar endpoint semantics are part of the scientific contract."""
    rng = ScriptedTrainRng(randoms=randoms, choices=choices)
    with pytest.raises(TrafficlabError, match=message):
        generate_with_rng(two_state_model(), rng, W=8.0, limits=LIMITS, clock=lambda: 0.0)


def test_generation_revalidates_a_finite_non_q90_direct_model() -> None:
    """Generation cannot consume a directly corrupted threshold that would redefine every gap pool."""
    model = copy.copy(two_state_model())
    object.__setattr__(model, "gap_threshold", 4.3)

    with pytest.raises(TrafficlabError, match="Type-7 q90"):
        generate_with_rng(
            model,
            ScriptedTrainRng(randoms=(), choices=()),
            W=8.0,
            limits=LIMITS,
            clock=lambda: 0.0,
        )
