"""Behavioral tests for the piecewise-constant NHPP traffic model."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

from trafficlab.common.config import GenerationLimits, IntegerBounds, NhppConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.common import MarkCount, MarkDistribution
from trafficlab.generation.models.nhpp import NhppFamily, NhppModel, _generate_with_rng

FAMILY = NhppFamily()
BOUNDS = NhppConfig(bin_count=IntegerBounds(lower=2, upper=4))
LIMITS = GenerationLimits(max_packets=20, max_output_bytes=10_000, max_wall_seconds=10.0)
REFERENCE = TrafficTrace.from_events(
    (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(0.5, Direction.OUTBOUND, 60),
        TraceEvent(1.0, Direction.INBOUND, 100),
        TraceEvent(1.5, Direction.INBOUND, 100),
        TraceEvent(2.0, Direction.INBOUND, 100),
    )
)


class ScriptedNhppRng:
    """Expose the scalar empirical and exponential calls in their exact order."""

    def __init__(self, *, indices: Sequence[int], exponentials: Sequence[float]) -> None:
        self._indices = iter(indices)
        self._exponentials = iter(exponentials)
        self.calls: list[tuple[str, float | int]] = []

    def choice(self, a: int) -> int:
        self.calls.append(("choice", a))
        return next(self._indices)

    def exponential(self, scale: float) -> float:
        self.calls.append(("exponential", scale))
        return next(self._exponentials)


class ScriptedClock:
    def __init__(self, values: Sequence[float]) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def test_family_declares_integer_bin_chromosome_and_estimators() -> None:
    assert FAMILY.name == "nhpp"
    assert FAMILY.gene_names == ("bin_count",)
    assert FAMILY.gene_coordinate_kinds == ("integer",)
    assert FAMILY.bounds_type is NhppConfig
    assert FAMILY.estimator_choices == {
        "first_event": "zero",
        "rate": "equal_width_bin_interval_count_over_width",
        "marks": "bin_joint_empirical_first_appearance_global_fallback",
    }


def test_repair_clamps_exact_integer_bin_count_at_inclusive_endpoints() -> None:
    assert FAMILY.repair((1,), BOUNDS, REFERENCE) == (2,)
    assert FAMILY.repair((2,), BOUNDS, REFERENCE) == (2,)
    assert FAMILY.repair((4,), BOUNDS, REFERENCE) == (4,)
    assert FAMILY.repair((99,), BOUNDS, REFERENCE) == (4,)


@pytest.mark.parametrize("genes", [(), (2, 3), (2.0,), (True,), (math.inf,)])
def test_repair_rejects_noncanonical_integer_chromosomes(genes: tuple[object, ...]) -> None:
    with pytest.raises(TrafficlabError, match="bin_count"):
        FAMILY.repair(genes, BOUNDS, REFERENCE)  # type: ignore[arg-type]


def test_repair_rejects_non_nhpp_bounds() -> None:
    with pytest.raises(TrafficlabError, match="NHPP bounds"):
        FAMILY.repair((2,), object(), REFERENCE)  # type: ignore[arg-type]


def test_fit_uses_equal_bins_excludes_conditioned_zero_from_rates_and_keeps_bin_marks() -> None:
    model = FAMILY.fit(REFERENCE, (2,), W=2.0, bounds=BOUNDS)

    assert model.rates == (1.0, 3.0)
    assert model.global_marks.entries == (
        MarkCount(Direction.OUTBOUND, 60, 2),
        MarkCount(Direction.INBOUND, 100, 3),
    )
    assert model.bin_marks[0] == MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 2),))
    assert model.bin_marks[1] == MarkDistribution((MarkCount(Direction.INBOUND, 100, 3),))


def test_fit_assigns_window_endpoint_to_final_bin() -> None:
    model = FAMILY.fit(REFERENCE, (2,), W=2.0, bounds=BOUNDS)
    assert model.rates[1] == 3.0


def test_fit_snaps_four_ulp_scaled_boundaries_before_assigning_rates_and_marks() -> None:
    reference = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(0.1, Direction.OUTBOUND, 61),
            TraceEvent(0.2, Direction.INBOUND, 62),
            TraceEvent(0.3, Direction.INBOUND, 63),
            TraceEvent(0.4, Direction.OUTBOUND, 64),
        )
    )

    model = FAMILY.fit(reference, (4,), W=0.4, bounds=BOUNDS)

    assert model.rates == (0.0, 10.0, 10.0, 20.0)
    assert model.bin_marks[3] == MarkDistribution(
        (MarkCount(Direction.INBOUND, 63, 1), MarkCount(Direction.OUTBOUND, 64, 1))
    )


def test_fit_keeps_a_value_more_than_four_ulps_below_a_boundary_in_the_prior_bin() -> None:
    width = 0.1
    boundary = 0.3
    below = boundary
    for _ in range(5):
        below = math.nextafter(below, 0.0)
    reference = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(below, Direction.INBOUND, 70),
            TraceEvent(0.4, Direction.OUTBOUND, 80),
        )
    )

    model = FAMILY.fit(reference, (4,), W=0.4, bounds=BOUNDS)

    assert width > 0.0
    assert model.rates == (0.0, 0.0, 10.0, 10.0)
    assert model.bin_marks[2] == MarkDistribution((MarkCount(Direction.INBOUND, 70, 1),))


@pytest.mark.parametrize("window", [math.ulp(0.0), 1e-308])
def test_fit_rejects_unrepresentable_bin_width_or_rate(window: float) -> None:
    reference = TrafficTrace.from_events(
        (TraceEvent(0.0, Direction.OUTBOUND, 60), TraceEvent(window, Direction.INBOUND, 70))
    )

    with pytest.raises(TrafficlabError, match="NHPP bin width|NHPP rate"):
        FAMILY.fit(reference, (2,), W=window, bounds=BOUNDS)


def test_generation_crosses_nonzero_and_empty_bins_without_spurious_mark_draws() -> None:
    marks = MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 1),))
    model = NhppModel(rates=(2.0, 0.0, 3.0), bin_marks=(marks, None, marks), global_marks=marks)
    rng = ScriptedNhppRng(indices=[0, 0], exponentials=[1.5, 0.5, 0.6])

    result = _generate_with_rng(model, rng, W=3.0, limits=LIMITS, clock=ScriptedClock([0.0] * 20))

    assert result.require_complete() == (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(2.5, Direction.OUTBOUND, 60),
    )
    assert rng.calls == [
        ("choice", 1),
        ("exponential", 0.5),
        ("exponential", 1.0 / 3.0),
        ("choice", 1),
        ("exponential", 1.0 / 3.0),
    ]


def test_generation_emits_an_arrival_at_the_closed_window_endpoint() -> None:
    marks = MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 1),))
    model = NhppModel(rates=(1.0,), bin_marks=(marks,), global_marks=marks)
    rng = ScriptedNhppRng(indices=[0, 0], exponentials=[1.0, 0.5])

    result = _generate_with_rng(model, rng, W=1.0, limits=LIMITS, clock=ScriptedClock([0.0] * 20))

    assert result.require_complete() == (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(1.0, Direction.OUTBOUND, 60),
    )
    assert rng.calls == [("choice", 1), ("exponential", 1.0), ("choice", 1), ("exponential", 1.0)]


def test_generation_uses_global_marks_when_the_active_bin_has_no_reference_mark() -> None:
    global_marks = MarkDistribution((MarkCount(Direction.INBOUND, 100, 1),))
    model = NhppModel(rates=(0.0, 1.0), bin_marks=(None, None), global_marks=global_marks)
    rng = ScriptedNhppRng(indices=[0, 0], exponentials=[0.5, 1.0])

    result = _generate_with_rng(model, rng, W=2.0, limits=LIMITS, clock=ScriptedClock([0.0] * 20))

    assert result.require_complete() == (
        TraceEvent(0.0, Direction.INBOUND, 100),
        TraceEvent(1.5, Direction.INBOUND, 100),
    )


def test_generation_checks_packet_and_byte_guards_at_the_initial_and_following_boundaries() -> None:
    marks = MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 1),))
    model = NhppModel(rates=(1.0,), bin_marks=(marks,), global_marks=marks)
    initial_byte = _generate_with_rng(
        model,
        ScriptedNhppRng(indices=[0], exponentials=[]),
        W=1.0,
        limits=GenerationLimits(max_packets=2, max_output_bytes=59, max_wall_seconds=1.0),
        clock=ScriptedClock([0.0] * 10),
    )
    following_packet = _generate_with_rng(
        model,
        ScriptedNhppRng(indices=[0], exponentials=[]),
        W=1.0,
        limits=GenerationLimits(max_packets=1, max_output_bytes=10_000, max_wall_seconds=1.0),
        clock=ScriptedClock([0.0] * 10),
    )
    assert (initial_byte.complete, initial_byte.reason, initial_byte.trace) == (False, "max_output_bytes", ())
    assert (following_packet.complete, following_packet.reason) == (False, "max_packets")


def test_generation_checks_wall_guard_before_and_after_draws() -> None:
    marks = MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 1),))
    model = NhppModel(rates=(1.0,), bin_marks=(marks,), global_marks=marks)
    before = _generate_with_rng(
        model,
        ScriptedNhppRng(indices=[0], exponentials=[]),
        W=1.0,
        limits=LIMITS,
        clock=ScriptedClock([math.inf]),
    )
    after = _generate_with_rng(
        model,
        ScriptedNhppRng(indices=[0], exponentials=[]),
        W=1.0,
        limits=LIMITS,
        clock=ScriptedClock([0.0, 0.0, 10.0]),
    )
    assert (before.complete, before.reason, before.trace) == (False, "max_wall_seconds", ())
    assert (after.complete, after.reason, after.trace) == (False, "max_wall_seconds", ())


def test_generation_checks_wall_guard_after_exponential_and_later_mark_draws() -> None:
    marks = MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 1),))
    model = NhppModel(rates=(1.0,), bin_marks=(marks,), global_marks=marks)
    after_exponential = _generate_with_rng(
        model,
        ScriptedNhppRng(indices=[0], exponentials=[0.5]),
        W=1.0,
        limits=LIMITS,
        clock=ScriptedClock([0.0, 0.0, 0.0, 0.0, 0.0, 10.0]),
    )
    after_mark = _generate_with_rng(
        model,
        ScriptedNhppRng(indices=[0, 0], exponentials=[0.5]),
        W=1.0,
        limits=LIMITS,
        clock=ScriptedClock([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0]),
    )
    assert (after_exponential.complete, after_exponential.reason) == (False, "max_wall_seconds")
    assert (after_mark.complete, after_mark.reason) == (False, "max_wall_seconds")


def test_generation_checks_later_event_byte_budget_before_emitting() -> None:
    marks = MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 1),))
    model = NhppModel(rates=(1.0,), bin_marks=(marks,), global_marks=marks)
    result = _generate_with_rng(
        model,
        ScriptedNhppRng(indices=[0, 0], exponentials=[0.5]),
        W=1.0,
        limits=GenerationLimits(max_packets=3, max_output_bytes=119, max_wall_seconds=1.0),
        clock=ScriptedClock([0.0] * 20),
    )
    assert (result.complete, result.reason) == (False, "max_output_bytes")
    assert result.trace.to_events() == (TraceEvent(0.0, Direction.OUTBOUND, 60),)


def test_generation_rejects_invalid_exponential_values() -> None:
    marks = MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 1),))
    model = NhppModel(rates=(1.0,), bin_marks=(marks,), global_marks=marks)
    with pytest.raises(TrafficlabError, match="random delay"):
        _generate_with_rng(
            model,
            ScriptedNhppRng(indices=[0], exponentials=[math.nan]),
            W=1.0,
            limits=LIMITS,
            clock=ScriptedClock([0.0] * 10),
        )


def test_generation_rejects_invalid_empirical_choice_primitive() -> None:
    marks = MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 1),))
    model = NhppModel(rates=(1.0,), bin_marks=(marks,), global_marks=marks)
    with pytest.raises(TrafficlabError, match="empirical random draw"):
        _generate_with_rng(
            model,
            ScriptedNhppRng(indices=[1], exponentials=[]),
            W=1.0,
            limits=LIMITS,
            clock=ScriptedClock([0.0] * 10),
        )


@pytest.mark.parametrize("seed", [True, -1, 1.0])
def test_generate_rejects_noncanonical_seed_primitives(seed: object) -> None:
    model = FAMILY.fit(REFERENCE, (2,), W=2.0, bounds=BOUNDS)
    with pytest.raises(TrafficlabError, match="seed"):
        FAMILY.generate(model, seed, 2.0, LIMITS)  # type: ignore[arg-type]


@pytest.mark.parametrize("window", [True, 0.0, math.nan, math.inf])
def test_generate_rejects_invalid_window_primitives(window: object) -> None:
    marks = MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 1),))
    model = NhppModel(rates=(1.0,), bin_marks=(marks,), global_marks=marks)
    with pytest.raises(TrafficlabError, match="observation window"):
        _generate_with_rng(model, ScriptedNhppRng(indices=[], exponentials=[]), W=window, limits=LIMITS)  # type: ignore[arg-type]


def test_fitted_model_round_trips_strict_payload_and_rejects_corruption() -> None:
    model = FAMILY.fit(REFERENCE, (2,), W=2.0, bounds=BOUNDS)
    payload = FAMILY.dump_fitted(model)

    assert payload == {
        "rates": [1.0, 3.0],
        "bin_marks": [
            [{"direction": "outbound", "frame_length": 60, "count": 2}],
            [{"direction": "inbound", "frame_length": 100, "count": 3}],
        ],
        "global_marks": [
            {"direction": "outbound", "frame_length": 60, "count": 2},
            {"direction": "inbound", "frame_length": 100, "count": 3},
        ],
    }
    assert FAMILY.load_fitted(payload, genes=(2,), bounds=BOUNDS) == model
    malformed_payloads: tuple[object, ...] = (
        {**payload, "rates": [1.0, -3.0]},
        {**payload, "rates": [1.0]},
        {**payload, "bin_marks": [[{"direction": "outbound", "frame_length": 60, "count": 1}] * 2, []]},
        {**payload, "global_marks": []},
        {**payload, "extra": 1},
        {**payload, "rates": [math.nan, 3.0]},
        {**payload, "rates": [math.inf, 3.0]},
    )
    for malformed in malformed_payloads:
        with pytest.raises(TrafficlabError):
            FAMILY.load_fitted(malformed, genes=(2,), bounds=BOUNDS)


def test_same_seed_generation_is_identical() -> None:
    model = FAMILY.fit(REFERENCE, (2,), W=2.0, bounds=BOUNDS)
    first = FAMILY.generate(model, 73, 2.0, LIMITS, clock=lambda: 0.0)
    second = FAMILY.generate(model, 73, 2.0, LIMITS, clock=lambda: 0.0)
    assert first == second
