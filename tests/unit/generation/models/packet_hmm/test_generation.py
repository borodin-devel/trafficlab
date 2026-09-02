"""Exact packet-HMM stochastic-order, boundary, and guard tests."""

from __future__ import annotations

import copy

import pytest

from tests.unit.generation.models.packet_hmm._support import ScriptedHmmRng, two_state_model
from trafficlab.common.config import GenerationLimits
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction
from trafficlab.generation.models.packet_hmm.generation import generate_with_rng
from trafficlab.generation.models.packet_hmm.model import PacketCategory, PacketSample

LIMITS = GenerationLimits(max_packets=20, max_output_bytes=10_000, max_wall_seconds=10.0)


def test_generation_uses_initial_mark_then_hidden_category_member_order_and_includes_endpoint() -> None:
    """Any reordered PCG64 primitive or strict-open window endpoint changes deterministic output."""
    rng = ScriptedHmmRng(
        randoms=(0.0, 0.0, 0.9, 0.9, 0.9, 0.9),
        choices=(0, 0, 0, 0),
    )

    result = generate_with_rng(two_state_model(), rng, W=4.0, limits=LIMITS, clock=lambda: 0.0)

    assert result.complete is True
    assert result.trace.timestamps.tolist() == [0.0, 1.0, 4.0]
    assert result.trace.directions.tolist() == [0, 1, 0]
    assert result.trace.frame_lengths.tolist() == [60, 70, 130]
    assert rng.calls == [
        ("choice", 1),
        ("random", None),
        ("random", None),
        ("choice", 1),
        ("random", None),
        ("random", None),
        ("choice", 1),
        ("random", None),
        ("random", None),
        ("choice", 1),
    ]
    assert dict(result.model_diagnostics) == {
        "category_0_count": 1,
        "category_1_count": 1,
        "hidden_state_0_count": 1,
        "hidden_state_1_count": 1,
    }


def test_zero_iat_generation_is_bounded_by_packet_guard() -> None:
    """Repeated zero gaps must not bypass per-packet guards and loop forever."""
    zero_member_model = copy.copy(two_state_model())
    object.__setattr__(zero_member_model, "emission_rows", ((1.0,), (1.0,)))
    object.__setattr__(zero_member_model, "iat_thresholds", ())
    object.__setattr__(zero_member_model, "initial_probabilities", (0.25, 0.75))
    object.__setattr__(zero_member_model, "reservoirs", ((PacketSample(0.0, 70),),))
    object.__setattr__(zero_member_model, "size_thresholds", (70.0, 70.0))
    object.__setattr__(
        zero_member_model,
        "vocabulary",
        (PacketCategory(0, Direction.INBOUND, 0),),
    )
    rng = ScriptedHmmRng(randoms=(0.0,) * 10, choices=(0,) * 10)
    limits = GenerationLimits(max_packets=2, max_output_bytes=10_000, max_wall_seconds=10.0)

    result = generate_with_rng(zero_member_model, rng, W=1.0, limits=limits, clock=lambda: 0.0)

    assert result.complete is False
    assert result.reason == "max_packets"
    assert result.trace.timestamps.tolist() == [0.0, 0.0]


@pytest.mark.parametrize(
    ("randoms", "choices", "message"),
    (
        ((1.0,), (0,), "random draw"),
        ((0.0, 0.0), (0, 1), "empirical random draw"),
    ),
)
def test_generation_rejects_rng_endpoint_violations(
    randoms: tuple[float, ...], choices: tuple[int, ...], message: str
) -> None:
    """Continuous draws are [0,1) and integer draws are [0,n), never silently clamped."""
    with pytest.raises(TrafficlabError, match=message):
        generate_with_rng(
            two_state_model(),
            ScriptedHmmRng(randoms=randoms, choices=choices),
            W=4.0,
            limits=LIMITS,
            clock=lambda: 0.0,
        )
