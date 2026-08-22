"""Behavioral tests for one Markov renewal owner."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import math
import sys

import pytest

import trafficlab.generation.models.markov_renewal.generation as markov_renewal
from tests.support.markov_renewal import (
    BOUNDS,
    FAMILY,
    LARGE_LIMITS,
    ScriptedClock,
    ScriptedMarkovRng,
    two_state_model,
)
from trafficlab.common.config import GenerationLimits
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace


def test_generation_uses_exact_draw_order_and_emits_the_closed_endpoint() -> None:
    """Changing draw order or treating W as open would break reproducibility and boundary semantics."""
    model = two_state_model(alpha=0.0, minimum_support=1.0)
    rng = ScriptedMarkovRng(random_values=[0.0, 0.9, 0.1], indices=[0, 0, 0, 0])
    result = markov_renewal.generate_with_rng(
        model,
        rng,
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0] * 20),
    )
    assert result.require_complete() == (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(1.0, Direction.INBOUND, 80),
    )
    assert rng.calls == [
        ("random", None),
        ("choice", 1),
        ("random", None),
        ("choice", 1),
        ("choice", 1),
        ("random", None),
        ("choice", 1),
    ]


def test_final_only_state_uses_uniform_row_and_global_iat() -> None:
    """A state with no outgoing observations must still transition and reach global timing fallback."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(1.0, Direction.INBOUND, 50),
        TraceEvent(3.0, Direction.INBOUND, 80),
    )
    model = FAMILY.fit(TrafficTrace.from_events(reference), (0.25, 0.75, 0.0, 2.0, 1.0), W=3.0, bounds=BOUNDS)
    rng = ScriptedMarkovRng(random_values=[0.9, 0.1], indices=[0, 0])
    result = markov_renewal.generate_with_rng(
        model,
        rng,
        W=0.5,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0] * 12),
    )
    assert result.require_complete() == (TraceEvent(0.0, Direction.INBOUND, 80),)
    assert rng.calls == [("random", None), ("choice", 1), ("random", None), ("choice", 2)]
    assert dict(result.model_diagnostics) == {
        "timing_tier_transition_count": 0,
        "timing_tier_source_count": 0,
        "timing_tier_global_count": 1,
        "uniform_unobserved_row_count": 1,
    }


@pytest.mark.parametrize(
    ("minimum_support", "expected_tier"),
    [(1.0, "transition"), (2.0, "source")],
)
def test_generation_counts_each_selected_timing_tier(
    minimum_support: float,
    expected_tier: str,
) -> None:
    """The owner must count the actual tier chosen even when the sampled next event exceeds W."""
    model = two_state_model(alpha=0.0, minimum_support=minimum_support)
    result = markov_renewal.generate_with_rng(
        model,
        ScriptedMarkovRng(random_values=[0.0, 0.9], indices=[0, 0]),
        W=0.5,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0] * 12),
    )

    assert result.require_complete() == (TraceEvent(0.0, Direction.OUTBOUND, 20),)
    assert dict(result.model_diagnostics) == {
        "timing_tier_transition_count": int(expected_tier == "transition"),
        "timing_tier_source_count": int(expected_tier == "source"),
        "timing_tier_global_count": 0,
        "uniform_unobserved_row_count": 0,
    }


def test_generation_completes_without_destination_frame_draw_after_window() -> None:
    """Drawing an unused frame for an out-of-window transition would perturb subsequent seeded trials."""
    model = two_state_model(alpha=0.0, minimum_support=1.0)
    rng = ScriptedMarkovRng(random_values=[0.0, 0.9], indices=[0, 0])
    result = markov_renewal.generate_with_rng(
        model,
        rng,
        W=0.5,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0] * 12),
    )
    assert result.require_complete() == (TraceEvent(0.0, Direction.OUTBOUND, 20),)
    assert rng.calls == [("random", None), ("choice", 1), ("random", None), ("choice", 1)]


def test_generation_allows_zero_iats_until_a_reliability_guard_stops_it() -> None:
    """Zero holding times are valid but must not permit unbounded packet generation."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(0.0, Direction.INBOUND, 80),
        TraceEvent(1.0, Direction.OUTBOUND, 20),
    )
    model = FAMILY.fit(TrafficTrace.from_events(reference), (0.25, 0.75, 0.0, 1.0, 1.0), W=1.0, bounds=BOUNDS)
    rng = ScriptedMarkovRng(random_values=[0.0, 0.9, 0.1], indices=[0, 0, 0, 0, 0])
    result = markov_renewal.generate_with_rng(
        model,
        rng,
        W=1.0,
        limits=GenerationLimits(max_packets=3, max_output_bytes=100_000, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0] * 24),
    )
    assert tuple(event.timestamp for event in result.trace) == (0.0, 0.0, 1.0)
    assert result.reason == "max_packets"


@pytest.mark.parametrize("draw", [math.nan, math.inf, -0.1, 1.0, 1])
def test_generation_rejects_invalid_continuous_rng_draws(draw: object) -> None:
    """A noncanonical uniform draw would make cumulative state selection ambiguous."""
    with pytest.raises(TrafficlabError, match="random draw"):
        markov_renewal.generate_with_rng(
            two_state_model(),
            ScriptedMarkovRng(random_values=[draw], indices=[]),  # type: ignore[list-item]
            W=1.0,
            limits=LARGE_LIMITS,
            clock=ScriptedClock([0.0] * 4),
        )


@pytest.mark.parametrize("index", [-1, 1, True, 0.0])
def test_generation_rejects_invalid_integer_rng_draws(index: object) -> None:
    """Coercing an empirical index would select a sample outside the requested population."""
    with pytest.raises(TrafficlabError, match="random draw"):
        markov_renewal.generate_with_rng(
            two_state_model(),
            ScriptedMarkovRng(random_values=[0.0], indices=[index]),  # type: ignore[list-item]
            W=1.0,
            limits=LARGE_LIMITS,
            clock=ScriptedClock([0.0] * 6),
        )


@pytest.mark.parametrize(
    ("random_values", "indices", "clock_values"),
    [
        ([math.nan], [], [0.0, 0.0, 10.0]),
        ([0.0], [99], [0.0, 0.0, 0.0, 10.0]),
        ([0.0, math.nan], [0], [0.0] * 6 + [10.0]),
        ([0.0, 0.9], [0, 99], [0.0] * 7 + [10.0]),
    ],
)
def test_generation_prioritizes_post_draw_wall_expiry_over_malformed_draws(
    random_values: list[float], indices: list[int], clock_values: list[float]
) -> None:
    """A draw made at expiry must return the wall diagnostic before inspecting malformed raw data."""
    result = markov_renewal.generate_with_rng(
        two_state_model(),
        ScriptedMarkovRng(random_values=random_values, indices=indices),
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock(clock_values),
    )
    assert result.reason == "max_wall_seconds"


def test_generation_checks_wall_after_destination_frame_draw_and_before_emission() -> None:
    """An in-window frame drawn at the deadline must not be emitted."""
    result = markov_renewal.generate_with_rng(
        two_state_model(alpha=0.0, minimum_support=1.0),
        ScriptedMarkovRng(random_values=[0.0, 0.9], indices=[0, 0, 0]),
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0] * 8 + [10.0]),
    )
    assert result.trace == (TraceEvent(0.0, Direction.OUTBOUND, 20),)
    assert result.reason == "max_wall_seconds"


def test_generation_checks_prospective_packet_and_output_limits_before_emission() -> None:
    """Prospective limit checks must retain only the valid diagnostic prefix."""
    model = two_state_model(alpha=0.0, minimum_support=1.0)
    packet_result = markov_renewal.generate_with_rng(
        model,
        ScriptedMarkovRng(random_values=[0.0], indices=[0]),
        W=1.0,
        limits=GenerationLimits(max_packets=1, max_output_bytes=100_000, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0] * 8),
    )
    byte_result = markov_renewal.generate_with_rng(
        model,
        ScriptedMarkovRng(random_values=[0.0], indices=[0]),
        W=1.0,
        limits=GenerationLimits(max_packets=100, max_output_bytes=19, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0] * 8),
    )
    assert packet_result.trace == (TraceEvent(0.0, Direction.OUTBOUND, 20),)
    assert packet_result.reason == "max_packets"
    assert byte_result.trace == ()
    assert byte_result.reason == "max_output_bytes"


def test_generation_checks_initial_wall_guard_and_later_prospective_output_limit() -> None:
    """Wall failure must precede the first draw, and later output checks must precede emission."""
    model = two_state_model(alpha=0.0, minimum_support=1.0)
    initial_rng = ScriptedMarkovRng(random_values=[], indices=[])
    initial_result = markov_renewal.generate_with_rng(
        model,
        initial_rng,
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0, 10.0]),
    )
    later_result = markov_renewal.generate_with_rng(
        model,
        ScriptedMarkovRng(random_values=[0.0, 0.9], indices=[0, 0, 0]),
        W=1.0,
        limits=GenerationLimits(max_packets=100, max_output_bytes=99, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0] * 12),
    )
    assert initial_result.reason == "max_wall_seconds"
    assert initial_rng.calls == []
    assert later_result.trace == (TraceEvent(0.0, Direction.OUTBOUND, 20),)
    assert later_result.reason == "max_output_bytes"


def test_generation_rejects_overflowed_scaled_arrival_time() -> None:
    """Treating arithmetic overflow as natural completion would hide structural corruption."""
    fitted = FAMILY.fit(
        TrafficTrace.from_events(
            (
                TraceEvent(0.0, Direction.OUTBOUND, 20),
                TraceEvent(1e308, Direction.INBOUND, 80),
            )
        ),
        (0.25, 0.75, 0.0, 1.0, 2.0),
        W=1e308,
        bounds=BOUNDS,
    )
    with pytest.raises(TrafficlabError, match="arrival time"):
        markov_renewal.generate_with_rng(
            fitted,
            ScriptedMarkovRng(random_values=[0.9, 0.1], indices=[0, 0]),
            W=sys.float_info.max,
            limits=LARGE_LIMITS,
            clock=ScriptedClock([0.0] * 12),
        )


@pytest.mark.parametrize("window", [0.0, -1.0, math.inf, math.nan, True])
def test_generation_rejects_invalid_windows(window: object) -> None:
    """A nonpositive or nonfinite window cannot define closed generation semantics."""
    with pytest.raises(TrafficlabError, match="observation window"):
        markov_renewal.generate_with_rng(
            two_state_model(),
            ScriptedMarkovRng(random_values=[], indices=[]),
            W=window,  # type: ignore[arg-type]
            limits=LARGE_LIMITS,
        )


def test_generation_rejects_a_non_markov_model() -> None:
    """Interpreting another fitted family as Markov state would corrupt generation."""
    with pytest.raises(TypeError, match="MarkovRenewalModel"):
        markov_renewal.generate_with_rng(
            object(),  # type: ignore[arg-type]
            ScriptedMarkovRng(random_values=[], indices=[]),
            W=1.0,
            limits=LARGE_LIMITS,
        )
