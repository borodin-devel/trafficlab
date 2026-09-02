"""Exact packet-HMM stochastic-order, boundary, and guard tests."""

from __future__ import annotations

import copy
import math

import pytest

from tests.unit.generation.models.packet_hmm._support import ScriptedHmmRng, two_state_model
from trafficlab.common.config import GenerationLimits
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction
from trafficlab.generation.models.packet_hmm.family import PacketHmmFamily
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


def test_corrupt_nested_category_direction_is_rejected_before_dump_or_generation() -> None:
    """A post-construction string direction must not serialize or silently become inbound output."""
    model = copy.copy(two_state_model())
    object.__setattr__(model.vocabulary[0], "direction", "outbound")

    with pytest.raises(TrafficlabError, match="Direction"):
        PacketHmmFamily().dump_fitted(model)
    with pytest.raises(TrafficlabError, match="Direction"):
        generate_with_rng(
            model,
            ScriptedHmmRng(randoms=(0.0,), choices=(0,)),
            W=4.0,
            limits=LIMITS,
            clock=lambda: 0.0,
        )


def test_mid_stream_byte_limit_stops_before_appending_the_drawn_member() -> None:
    """Checking bytes only before draws would retain an over-budget in-window member."""
    result = generate_with_rng(
        two_state_model(),
        ScriptedHmmRng(randoms=(0.0, 0.0), choices=(0, 0)),
        W=4.0,
        limits=GenerationLimits(max_packets=20, max_output_bytes=100, max_wall_seconds=10.0),
        clock=lambda: 0.0,
    )

    assert result.complete is False
    assert result.reason == "max_output_bytes"
    assert result.trace.frame_lengths.tolist() == [60]


def test_post_member_wall_guard_rejects_an_out_of_window_proposal_before_completion() -> None:
    """A clock expiry after choosing the raw member must not be masked by natural window completion."""
    clock_values = iter((0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0))
    result = generate_with_rng(
        two_state_model(),
        ScriptedHmmRng(randoms=(0.0, 0.9), choices=(0, 0)),
        W=2.0,
        limits=GenerationLimits(max_packets=20, max_output_bytes=10_000, max_wall_seconds=1.0),
        clock=clock_values.__next__,
    )

    assert result.complete is False
    assert result.reason == "max_wall_seconds"
    assert result.trace.timestamps.tolist() == [0.0]


def test_cumulative_finite_iats_that_overflow_are_rejected() -> None:
    """Finite raw members can still overflow only after an earlier emitted timestamp is accumulated."""
    model = copy.copy(two_state_model())
    object.__setattr__(model.reservoirs[0][0], "iat", 1e308)
    object.__setattr__(model, "iat_thresholds", (3.333333333333333e307, 6.666666666666666e307))
    object.__setattr__(
        model,
        "vocabulary",
        (
            PacketCategory(3, Direction.INBOUND, 0),
            PacketCategory(1, Direction.OUTBOUND, 2),
        ),
    )
    object.__setattr__(model, "emission_rows", ((0.1, 0.9), (0.8, 0.2)))

    with pytest.raises(TrafficlabError, match="arrival time"):
        generate_with_rng(
            model,
            ScriptedHmmRng(randoms=(0.0, 0.0, 0.0, 0.0), choices=(0, 0, 0)),
            W=1.5e308,
            limits=LIMITS,
            clock=lambda: 0.0,
        )


def test_corrupt_raw_member_is_rejected_before_dump_or_generation() -> None:
    """Directly mutating a nested raw member must be caught before any serialization or draw."""
    model = copy.copy(two_state_model())
    object.__setattr__(model.reservoirs[0][0], "iat", math.nan)

    with pytest.raises(TrafficlabError, match="finite"):
        PacketHmmFamily().dump_fitted(model)
    with pytest.raises(TrafficlabError, match="finite"):
        generate_with_rng(
            model,
            ScriptedHmmRng(randoms=(0.0,), choices=(0,)),
            W=4.0,
            limits=LIMITS,
            clock=lambda: 0.0,
        )


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
