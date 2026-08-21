"""Behavioral tests for the common traffic-model contract."""

import math
from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from trafficlab.common.config import GenerationLimits
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.common import (
    GenerationGuard,
    GenerationResult,
    IncompleteReason,
    MarkCount,
    MarkDistribution,
    validate_fit_inputs,
    weighted_index,
)


class ScriptedClock:
    """A typed clock whose call order is observable to the test."""

    def __init__(self, values: Sequence[float]) -> None:
        self._values = iter(values)
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return next(self._values)


class ScriptedRandrange:
    """A typed integer sampler that records its requested population sizes."""

    def __init__(self, values: Sequence[int]) -> None:
        self._values = iter(values)
        self.stops: list[int] = []

    def choice(self, a: int) -> int:
        self.stops.append(a)
        return next(self._values)


class ScriptedRandom:
    """A typed continuous sampler that records its exact draw count."""

    def __init__(self, values: Sequence[float]) -> None:
        self._values = iter(values)
        self.calls = 0

    def random(self) -> float:
        self.calls += 1
        return next(self._values)


def _reference(*lengths: int) -> tuple[TraceEvent, ...]:
    return tuple(TraceEvent(float(index), Direction.OUTBOUND, length) for index, length in enumerate(lengths))


def _trace(*lengths: int) -> TrafficTrace:
    return TrafficTrace.from_events(_reference(*lengths))


def test_generation_result_accepts_only_complete_or_diagnostic_states() -> None:
    """Permitting ambiguous result states would make partial output reusable."""
    trace = _trace(14, 60)
    empty = TrafficTrace.from_events(())

    assert GenerationResult(complete=True, trace=trace) == GenerationResult(True, trace, None)
    assert GenerationResult(complete=False, trace=trace, reason="max_packets").reason == "max_packets"

    for complete, result_trace, reason in (
        (True, empty, None),
        (True, trace, "max_packets"),
        (False, trace, None),
        (False, trace, "unknown"),
    ):
        with pytest.raises((TypeError, ValueError)):
            GenerationResult(complete=complete, trace=result_trace, reason=reason)  # type: ignore[arg-type]

    result = GenerationResult(complete=True, trace=trace)
    with pytest.raises(FrozenInstanceError):
        result.complete = False  # type: ignore[misc]
    assert not hasattr(result, "__dict__")


def test_generation_result_requires_complete_trace_before_reuse() -> None:
    """Returning incomplete diagnostic events would publish a truncated trace."""
    trace = _trace(14, 60)

    assert GenerationResult(complete=True, trace=trace).require_complete() is trace
    with pytest.raises(TrafficlabError, match="max_output_bytes") as caught:
        GenerationResult(complete=False, trace=trace, reason="max_output_bytes").require_complete()
    assert caught.value.corrective_action == "increase generation limits and generate a complete trace"


def test_generation_result_carries_the_exact_columnar_trace_without_materializing_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generated trace must stay immutable and columnar until an external artifact boundary."""
    trace = TrafficTrace.from_events(_reference(14, 60))

    def reject_event_materialization(_trace: TrafficTrace) -> tuple[TraceEvent, ...]:
        raise AssertionError("generation materialized TraceEvent objects")

    monkeypatch.setattr(TrafficTrace, "to_events", reject_event_materialization)

    result = GenerationResult(complete=True, trace=trace)

    assert result.trace is trace
    assert result.require_complete() is trace


def test_generation_result_freezes_exact_nonnegative_model_diagnostic_counts() -> None:
    """Evaluation diagnostics must not be mutable or accept ambiguous counter values."""
    result = GenerationResult(
        complete=True,
        trace=_trace(14),
        model_diagnostics={"timing_tier_source_count": 2},
    )

    assert dict(result.model_diagnostics) == {"timing_tier_source_count": 2}
    with pytest.raises(TypeError):
        result.model_diagnostics["timing_tier_source_count"] = 3  # type: ignore[index]

    invalid_diagnostics: tuple[object, ...] = (
        cast(object, []),
        {"": 1},
        {"counter": -1},
        {"counter": True},
        {1: 1},
    )
    for diagnostics in invalid_diagnostics:
        with pytest.raises((TypeError, ValueError), match="diagnostic"):
            GenerationResult(
                complete=True,
                trace=_trace(14),
                model_diagnostics=diagnostics,  # type: ignore[arg-type]
            )

    with pytest.raises(TypeError, match="complete"):
        GenerationResult(complete=1, trace=_trace(14))  # type: ignore[arg-type]


def test_generation_result_rejects_noncolumnar_or_unrenderable_diagnostic_traces() -> None:
    """Malformed diagnostic prefixes must fail at the single columnar result boundary."""
    with pytest.raises(TypeError, match="TrafficTrace"):
        GenerationResult(complete=False, trace=[], reason="max_packets")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="frame length"):
        GenerationResult(complete=False, trace=_trace(13), reason="max_packets")


@pytest.mark.parametrize("window", [1.0, 0.5])
def test_validate_fit_inputs_returns_a_valid_normalized_reference(window: float) -> None:
    """Changing normalized endpoints would fit a model against a different window."""
    reference = TrafficTrace.from_events(
        (TraceEvent(0.0, Direction.OUTBOUND, 14), TraceEvent(window, Direction.INBOUND, 2**32 - 1))
    )

    assert validate_fit_inputs(reference, W=window) == reference


@pytest.mark.parametrize("frame_length", [14, 70_000, 2**32 - 1])
def test_validate_fit_inputs_accepts_renderer_compatible_frame_lengths(frame_length: int) -> None:
    """Rejecting valid canonical Ethernet lengths would discard valid captures."""
    reference = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, frame_length),
            TraceEvent(1.0, Direction.INBOUND, frame_length),
        )
    )

    assert validate_fit_inputs(reference, W=1.0) == reference


@pytest.mark.parametrize(
    ("reference", "window", "message"),
    [
        (TrafficTrace.from_events((_reference(14)[0],)), 1.0, "at least two"),
        (
            TrafficTrace.from_events(
                (
                    TraceEvent(0.1, Direction.OUTBOUND, 14),
                    TraceEvent(1.0, Direction.INBOUND, 60),
                )
            ),
            1.0,
            "start at zero",
        ),
        (
            TrafficTrace.from_events(
                (
                    TraceEvent(0.0, Direction.OUTBOUND, 14),
                    TraceEvent(0.5, Direction.INBOUND, 60),
                )
            ),
            1.0,
            "end at W",
        ),
        (
            TrafficTrace.from_events((TraceEvent(0.0, Direction.OUTBOUND, 13), TraceEvent(1.0, Direction.INBOUND, 60))),
            1.0,
            "14",
        ),
    ],
)
def test_validate_fit_inputs_rejects_noncanonical_reference_inputs(
    reference: TrafficTrace, window: float, message: str
) -> None:
    """Invalid fit input would produce a model that cannot meet the shared contract."""
    with pytest.raises(TrafficlabError, match=message):
        validate_fit_inputs(reference, W=window)


@pytest.mark.parametrize("window", [0.0, -1.0, math.inf, -math.inf, math.nan, 1, True])
def test_validate_fit_inputs_requires_an_exact_finite_positive_float_window(window: object) -> None:
    """Coercing a window changes the common comparison boundary silently."""
    with pytest.raises(TrafficlabError, match="observation window"):
        validate_fit_inputs(_trace(14, 60), W=window)  # type: ignore[arg-type]


def test_validate_fit_inputs_rejects_a_noncolumnar_reference() -> None:
    """A legacy event object must fail at the exact columnar model-fit boundary."""
    with pytest.raises(TrafficlabError, match="exact TrafficTrace"):
        validate_fit_inputs(object(), W=1.0)  # type: ignore[arg-type]


def test_mark_distribution_preserves_first_appearance_and_joint_counts() -> None:
    """Sorting marks or separating their fields would lose empirical dependence."""
    marks = MarkDistribution.from_reference(
        (
            TraceEvent(0.0, Direction.INBOUND, 60),
            TraceEvent(0.5, Direction.OUTBOUND, 80),
            TraceEvent(1.0, Direction.INBOUND, 60),
        )
    )
    assert marks.entries == (
        MarkCount(Direction.INBOUND, 60, 2),
        MarkCount(Direction.OUTBOUND, 80, 1),
    )
    rng = ScriptedRandrange([1, 2])
    assert marks.sample(rng) == (Direction.INBOUND, 60)
    assert marks.sample(rng) == (Direction.OUTBOUND, 80)
    assert rng.stops == [3, 3]


@pytest.mark.parametrize(
    ("frame_length", "count", "duplicate"),
    [
        (14, 1, True),
        (14, 0, False),
        (13, 1, False),
        (2**32, 1, False),
    ],
)
def test_mark_distribution_rejects_unusable_empirical_marks(frame_length: int, count: int, duplicate: bool) -> None:
    """A malformed mark table would either bias sampling or fail rendering later."""
    with pytest.raises((TypeError, ValueError, TrafficlabError)):
        entry = MarkCount(Direction.OUTBOUND, frame_length, count)
        entries = (entry, entry) if duplicate else (entry,)
        MarkDistribution(entries)


def test_mark_count_rejects_noncanonical_direction_and_count_types() -> None:
    """Coercing mark fields would let malformed fitted data enter sampling."""
    with pytest.raises(TypeError, match="direction"):
        MarkCount("outbound", 14, 1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="count"):
        MarkCount(Direction.OUTBOUND, 14, True)  # type: ignore[arg-type]


def test_mark_distribution_rejects_empty_and_noncanonical_reference_marks() -> None:
    """An empty or malformed reference cannot define an empirical sampler."""
    with pytest.raises(ValueError, match="must not be empty"):
        MarkDistribution(())
    with pytest.raises(TypeError, match="TraceEvent"):
        MarkDistribution.from_reference((object(),))  # type: ignore[arg-type]


def test_mark_distribution_revalidates_constructed_mark_entries() -> None:
    """A malformed stored entry must not bypass the distribution's renderability guard."""
    malformed = object.__new__(MarkCount)
    object.__setattr__(malformed, "direction", Direction.OUTBOUND)
    object.__setattr__(malformed, "frame_length", 13)
    object.__setattr__(malformed, "count", 1)

    with pytest.raises(TrafficlabError, match="frame length"):
        MarkDistribution((malformed,))


@pytest.mark.parametrize(
    ("weights", "draw", "expected"),
    [
        ((1.0, 2.0, 3.0), 0.0, 0),
        ((1.0, 2.0, 3.0), 1.0 / 6.0, 1),
        ((1.0, 2.0, 3.0), 0.5, 2),
        ((1.0, 2.0, 3.0), 0.9999999999999999, 2),
    ],
)
def test_weighted_index_uses_tuple_order_and_one_random_draw(
    weights: tuple[float, ...], draw: float, expected: int
) -> None:
    """A changed boundary rule or extra draw would break reproducible generation."""
    rng = ScriptedRandom([draw])

    assert weighted_index(weights, rng) == expected
    assert rng.calls == 1


@pytest.mark.parametrize("weights", [(), (-1.0, 1.0), (0.0, 0.0), (math.inf, 1.0), (math.nan, 1.0)])
def test_weighted_index_rejects_invalid_weight_tables(weights: tuple[float, ...]) -> None:
    """Invalid transition probabilities must not be sampled as though normalized."""
    with pytest.raises(TrafficlabError, match="weights"):
        weighted_index(weights, ScriptedRandom([0.0]))


@pytest.mark.parametrize("draw", [1.0, -0.1, math.inf, math.nan])
def test_weighted_index_rejects_invalid_random_draws(draw: float) -> None:
    """An invalid unit draw would select an undefined probability interval."""
    with pytest.raises(TrafficlabError, match="weighted random draw"):
        weighted_index((1.0,), ScriptedRandom([draw]))


@pytest.mark.parametrize(
    ("count", "byte_count", "now", "reason"),
    [
        (2, 10, 0.0, "max_packets"),
        (1, 100, 0.0, "max_output_bytes"),
        (1, 10, 1.0, "max_wall_seconds"),
    ],
)
def test_guard_reports_exhaustion_before_another_draw(
    count: int, byte_count: int, now: float, reason: IncompleteReason
) -> None:
    """Drawing after exhausted budgets could mistake a truncated trace for completion."""
    clock = ScriptedClock([0.0, now])
    guard = GenerationGuard.start(
        GenerationLimits(max_packets=2, max_output_bytes=100, max_wall_seconds=1.0), clock=clock
    )

    assert guard.pre_draw_reason(count, byte_count) == reason
    assert clock.calls == 2


def test_guard_reports_wall_failure_immediately_after_a_draw() -> None:
    """A delayed wall check would permit draws after the reliability limit."""
    clock = ScriptedClock([0.0, 1.0])
    guard = GenerationGuard.start(
        GenerationLimits(max_packets=2, max_output_bytes=100, max_wall_seconds=1.0), clock=clock
    )

    assert guard.post_draw_reason() == "max_wall_seconds"
    assert clock.calls == 2


@pytest.mark.parametrize(
    ("count", "byte_count", "frame_length", "now", "reason"),
    [
        (2, 10, 20, 0.0, "max_packets"),
        (0, 90, 20, 0.0, "max_output_bytes"),
        (0, 10, 20, 1.0, "max_wall_seconds"),
    ],
)
def test_guard_checks_each_prospective_event_before_emission(
    count: int, byte_count: int, frame_length: int, now: float, reason: IncompleteReason
) -> None:
    """Checking limits after emission would create an over-budget event."""
    guard = GenerationGuard.start(
        GenerationLimits(max_packets=2, max_output_bytes=100, max_wall_seconds=1.0),
        clock=ScriptedClock([0.0, now]),
    )

    assert guard.prospective_reason(count, byte_count, frame_length) == reason


@pytest.mark.parametrize("values", [(math.nan,), (math.inf,), (0.0, -0.1)])
def test_guard_treats_invalid_clock_progress_as_wall_exhaustion(values: tuple[float, ...]) -> None:
    """Accepting a broken clock would make the wall-time reliability limit meaningless."""
    guard = GenerationGuard.start(
        GenerationLimits(max_packets=2, max_output_bytes=100, max_wall_seconds=1.0), clock=ScriptedClock(values)
    )

    assert guard.pre_draw_reason(0, 0) == "max_wall_seconds"


@pytest.mark.parametrize("now", [math.nan, math.inf, -math.inf])
def test_guard_treats_nonfinite_clock_reads_after_start_as_wall_exhaustion(now: float) -> None:
    """A nonfinite elapsed clock read cannot safely establish completion."""
    guard = GenerationGuard.start(
        GenerationLimits(max_packets=2, max_output_bytes=100, max_wall_seconds=1.0), clock=ScriptedClock([0.0, now])
    )

    assert guard.post_draw_reason() == "max_wall_seconds"


def test_guard_treats_an_overflowed_deadline_as_immediate_wall_exhaustion() -> None:
    """An infinite deadline would disable wall-time protection for a generator."""
    guard = GenerationGuard.start(
        GenerationLimits.model_construct(max_packets=2, max_output_bytes=100, max_wall_seconds=1e308),
        clock=ScriptedClock([1e308]),
    )

    assert guard.pre_draw_reason(0, 0) == "max_wall_seconds"


@pytest.mark.parametrize(("count", "byte_count"), [(-1, 0), (0, -1), (True, 0), (0, True)])
def test_guard_rejects_noncanonical_accounting_values(count: object, byte_count: object) -> None:
    """Coercing accounting values would make budget enforcement ambiguous."""
    guard = GenerationGuard.start(
        GenerationLimits(max_packets=2, max_output_bytes=100, max_wall_seconds=1.0), clock=ScriptedClock([0.0, 0.0])
    )

    with pytest.raises(TypeError):
        guard.pre_draw_reason(count, byte_count)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "limits",
    [
        GenerationLimits.model_construct(max_packets=True, max_output_bytes=100, max_wall_seconds=1.0),
        GenerationLimits.model_construct(max_packets=2, max_output_bytes=True, max_wall_seconds=1.0),
        GenerationLimits.model_construct(max_packets=2, max_output_bytes=100, max_wall_seconds=True),
    ],
)
def test_guard_rejects_nonexact_generation_limit_types(limits: GenerationLimits) -> None:
    """Pydantic-bypassed limit types must not change guard arithmetic silently."""
    with pytest.raises(TypeError):
        GenerationGuard.start(limits, clock=ScriptedClock([0.0]))
