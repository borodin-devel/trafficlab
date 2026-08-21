"""Behavioral tests for the empirical homogeneous-Poisson model."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import math
import random
import sys
from collections.abc import Sequence

import pytest

from trafficlab.common.config import FloatBounds, GenerationLimits, PoissonConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.common import GenerationGuard, MarkCount, MarkDistribution
from trafficlab.generation.models.poisson import PoissonFamily, PoissonModel, _generate_with_rng

FAMILY = PoissonFamily()
BOUNDS = PoissonConfig(c_lambda=FloatBounds(lower=0.25, upper=4.0))
LARGE_LIMITS = GenerationLimits(max_packets=100, max_output_bytes=100_000, max_wall_seconds=10.0)


class ScriptedPoissonRng:
    """A deterministic Poisson RNG exposing the exact stochastic call order."""

    def __init__(self, *, marks: Sequence[int], delays: Sequence[float]) -> None:
        self._marks = iter(marks)
        self._delays = iter(delays)
        self.calls: list[tuple[str, float | int]] = []

    def choice(self, a: int) -> int:
        self.calls.append(("choice", a))
        return next(self._marks)

    def exponential(self, scale: float) -> float:
        self.calls.append(("exponential", scale))
        return next(self._delays)


class ScriptedClock:
    """A finite clock sequence that lets tests place wall checks around draws."""

    def __init__(self, values: Sequence[float]) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


@pytest.fixture
def model() -> PoissonModel:
    """Return a two-mark fitted model with a hand-checked rate."""
    return PoissonModel(
        base_rate=1.0,
        rate=2.0,
        marks=FAMILY.fit(
            TrafficTrace.from_events(
                (
                    TraceEvent(0.0, Direction.OUTBOUND, 60),
                    TraceEvent(1.0, Direction.INBOUND, 80),
                )
            ),
            (2.0,),
            W=1.0,
            bounds=BOUNDS,
        ).marks,
    )


@pytest.fixture
def reference() -> TrafficTrace:
    """Return a hand-checked normalized trace with rate one over W=2."""
    return TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(1.0, Direction.INBOUND, 80),
            TraceEvent(2.0, Direction.OUTBOUND, 60),
        )
    )


def test_family_declares_the_poisson_chromosome_contract() -> None:
    """Changing the family metadata would make generic GA handling select the wrong bounds."""
    assert FAMILY.name == "poisson_empirical"
    assert FAMILY.gene_names == ("c_lambda",)
    assert FAMILY.bounds_type is PoissonConfig
    assert FAMILY.estimator_choices == {
        "first_event": "zero",
        "marks": "joint_empirical_first_appearance",
        "rate": "interval_count_over_window",
    }


@pytest.mark.parametrize(
    ("genes", "expected"),
    [
        ((0.25,), (0.25,)),
        ((4.0,), (4.0,)),
        ((-1.0,), (0.25,)),
        ((5.0,), (4.0,)),
    ],
)
def test_repair_preserves_bounds_and_clamps_finite_outliers(
    reference: TrafficTrace, genes: tuple[float, ...], expected: tuple[float, ...]
) -> None:
    """Skipping clamp logic would pass an out-of-bounds chromosome to fitting."""
    assert FAMILY.repair(genes, BOUNDS, reference) == expected


@pytest.mark.parametrize(
    "genes",
    [
        (),
        (1.0, 2.0),
        (True,),
        (1,),
        (math.nan,),
        (math.inf,),
        (-math.inf,),
    ],
)
def test_repair_rejects_noncanonical_poisson_genes(reference: TrafficTrace, genes: tuple[object, ...]) -> None:
    """Coercing malformed genes would lose the chromosome's strict serialized form."""
    with pytest.raises(TrafficlabError):
        FAMILY.repair(genes, BOUNDS, reference)  # type: ignore[arg-type]


@pytest.mark.parametrize("bounds", [object(), FloatBounds(lower=0.25, upper=4.0)])
def test_repair_requires_real_poisson_bounds(reference: TrafficTrace, bounds: object) -> None:
    """Using a temporary or foreign bounds object would silently invalidate genetic constraints."""
    with pytest.raises(TrafficlabError):
        FAMILY.repair((1.0,), bounds, reference)  # type: ignore[arg-type]


def test_fit_uses_interval_count_over_full_window(reference: TrafficTrace) -> None:
    """Using packet count instead of interval count would overestimate the MLE rate."""
    fitted = FAMILY.fit(reference, (2.0,), W=2.0, bounds=BOUNDS)

    assert fitted.base_rate == 1.0
    assert fitted.rate == 2.0
    assert fitted.marks.entries == (
        MarkCount(Direction.OUTBOUND, 60, 2),
        MarkCount(Direction.INBOUND, 80, 1),
    )


def test_fit_defensively_repairs_its_genes(reference: TrafficTrace) -> None:
    """Bypassing repair in public fit would permit a rate outside configured bounds."""
    fitted = FAMILY.fit(reference, (100.0,), W=2.0, bounds=BOUNDS)

    assert fitted.rate == 4.0


@pytest.mark.parametrize(
    ("reference", "window"),
    [
        ((TraceEvent(0.0, Direction.OUTBOUND, 60),), 1.0),
        (
            (
                TraceEvent(0.0, Direction.OUTBOUND, 60),
                TraceEvent(0.0, Direction.INBOUND, 80),
            ),
            0.0,
        ),
        (
            (
                TraceEvent(0.0, Direction.OUTBOUND, 60),
                TraceEvent(1.0, Direction.INBOUND, 80),
                TraceEvent(0.5, Direction.OUTBOUND, 60),
            ),
            0.5,
        ),
    ],
)
def test_fit_rejects_invalid_normalized_references(reference: tuple[TraceEvent, ...], window: float) -> None:
    """Fitting an invalid trace would make its rate and marks untrustworthy."""
    with pytest.raises(TrafficlabError):
        FAMILY.fit(reference, (1.0,), W=window, bounds=BOUNDS)  # type: ignore[arg-type]


def test_fitted_model_round_trips_only_strict_json_and_canonical_outer_genes(
    reference: TrafficTrace,
) -> None:
    """Trusting payload fields or unrepaired outer genes would admit a divergent fitted model."""
    fitted = FAMILY.fit(reference, (2.0,), W=2.0, bounds=BOUNDS)
    payload = FAMILY.dump_fitted(fitted)

    assert payload == {
        "base_rate": 1.0,
        "rate": 2.0,
        "marks": [
            {"direction": "outbound", "frame_length": 60, "count": 2},
            {"direction": "inbound", "frame_length": 80, "count": 1},
        ],
    }
    assert FAMILY.load_fitted(payload, genes=(2.0,), bounds=BOUNDS) == fitted

    malformed_payloads: tuple[object, ...] = (
        {**payload, "unknown": None},
        {"base_rate": 1.0, "rate": 2.0, "marks": []},
        {"base_rate": 1, "rate": 2.0, "marks": payload["marks"]},
        {"base_rate": 1.0, "rate": 2.0, "marks": [{"direction": "outbound", "frame_length": 60, "count": True}]},
        {"base_rate": 1.0, "rate": math.inf, "marks": payload["marks"]},
    )
    for malformed in malformed_payloads:
        with pytest.raises(TrafficlabError):
            FAMILY.load_fitted(malformed, genes=(2.0,), bounds=BOUNDS)

    with pytest.raises(TrafficlabError):
        FAMILY.load_fitted(payload, genes=(100.0,), bounds=BOUNDS)


def test_fitted_model_exposes_only_its_three_serialized_fields(reference: TrafficTrace) -> None:
    """Adding mutable fitted fields would make persisted model state ambiguous."""
    fitted: PoissonModel = FAMILY.fit(reference, (1.0,), W=2.0, bounds=BOUNDS)
    assert tuple(fitted.__dataclass_fields__) == ("base_rate", "rate", "marks")
    assert fitted.family == "poisson_empirical"


def test_generation_draw_order_and_closed_endpoint(model: PoissonModel) -> None:
    """Drawing a mark before an out-of-window delay would break fixed-seed reproducibility."""
    rng = ScriptedPoissonRng(marks=[0, 1], delays=[2.0, 0.1])
    result = _generate_with_rng(model, rng, W=2.0, limits=LARGE_LIMITS, clock=ScriptedClock([0.0] * 12))

    assert result.require_complete() == (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(2.0, Direction.INBOUND, 80),
    )
    assert rng.calls == [
        ("choice", model.marks.total_count),
        ("exponential", 1.0 / model.rate),
        ("choice", model.marks.total_count),
        ("exponential", 1.0 / model.rate),
    ]


def test_generation_builds_the_complete_trace_without_materializing_event_records(
    model: PoissonModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model result must remain columnar until the later PCAPNG publication boundary."""
    rng = ScriptedPoissonRng(marks=[0], delays=[2.0])

    def reject_event_materialization(_trace: TrafficTrace) -> tuple[TraceEvent, ...]:
        raise AssertionError("model generation materialized TraceEvent objects")

    monkeypatch.setattr(TrafficTrace, "to_events", reject_event_materialization)

    result = _generate_with_rng(model, rng, W=1.0, limits=LARGE_LIMITS, clock=ScriptedClock([0.0] * 8))

    assert type(result.trace) is TrafficTrace
    assert result.require_complete() is result.trace
    assert result.trace.timestamps.tolist() == [0.0]


def test_generation_completes_naturally_after_first_out_of_window_delay(model: PoissonModel) -> None:
    """Treating natural exhaustion as a limit would discard a valid one-packet trace."""
    rng = ScriptedPoissonRng(marks=[0], delays=[1.1])

    assert _generate_with_rng(
        model, rng, W=1.0, limits=LARGE_LIMITS, clock=ScriptedClock([0.0] * 8)
    ).require_complete() == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert rng.calls == [("choice", 2), ("exponential", 0.5)]


def test_generation_checks_pre_draw_packet_limit_before_another_delay(model: PoissonModel) -> None:
    """Sampling another delay after the packet cap would mutate a stopped diagnostic RNG sequence."""
    rng = ScriptedPoissonRng(marks=[0], delays=[])

    result = _generate_with_rng(
        model,
        rng,
        W=1.0,
        limits=GenerationLimits(max_packets=1, max_output_bytes=100_000, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0] * 8),
    )
    assert result.trace == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert result.reason == "max_packets"
    assert rng.calls == [("choice", 2)]


def test_generation_checks_pre_draw_output_exhaustion_before_another_delay(model: PoissonModel) -> None:
    """Sampling another delay after exhausting output bytes would mutate a stopped diagnostic RNG sequence."""
    rng = ScriptedPoissonRng(marks=[0], delays=[])
    limits = GenerationLimits(max_packets=100, max_output_bytes=60, max_wall_seconds=10.0)

    result = _generate_with_rng(model, rng, W=1.0, limits=limits, clock=ScriptedClock([0.0] * 8))
    assert result.trace == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert result.reason == "max_output_bytes"
    assert rng.calls == [("choice", 2)]


def test_generation_accepts_the_initial_packet_at_exact_packet_and_output_boundaries(model: PoissonModel) -> None:
    """Using inclusive prospective limits would wrongly discard the first packet exactly at both limits."""
    rng = ScriptedPoissonRng(marks=[0], delays=[])
    limits = GenerationLimits(max_packets=1, max_output_bytes=60, max_wall_seconds=10.0)

    result = _generate_with_rng(model, rng, W=1.0, limits=limits, clock=ScriptedClock([0.0] * 8))
    assert result.trace == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert result.reason == "max_packets"
    assert rng.calls == [("choice", 2)]


def test_guard_rejects_a_prospective_packet_at_the_packet_cap() -> None:
    """Accepting count plus one at the packet cap would emit a limit-breaking event."""
    guard = GenerationGuard.start(
        GenerationLimits(max_packets=1, max_output_bytes=100, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0, 0.0]),
    )

    assert guard.prospective_reason(1, 60, 60) == "max_packets"


def test_generation_checks_prospective_output_limit_before_in_window_emission(model: PoissonModel) -> None:
    """Stopping after emitting a limit-breaking packet would return an oversized diagnostic trace."""
    rng = ScriptedPoissonRng(marks=[0, 1], delays=[0.5])

    result = _generate_with_rng(
        model,
        rng,
        W=1.0,
        limits=GenerationLimits(max_packets=100, max_output_bytes=119, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0] * 8),
    )
    assert result.trace == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert result.reason == "max_output_bytes"
    assert rng.calls == [("choice", 2), ("exponential", 0.5), ("choice", 2)]


def test_generation_checks_wall_time_before_the_next_exponential_draw(model: PoissonModel) -> None:
    """Drawing after the pre-decision wall boundary would perturb a stopped trace's random sequence."""
    rng = ScriptedPoissonRng(marks=[0], delays=[])

    result = _generate_with_rng(
        model,
        rng,
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0, 0.0, 0.0, 0.0, 10.0]),
    )
    assert result.trace == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert result.reason == "max_wall_seconds"
    assert rng.calls == [("choice", 2)]


def test_generation_checks_wall_time_at_initial_prospective_emission(model: PoissonModel) -> None:
    """Emitting an initially drawn mark after its prospective wall check would exceed the deadline."""
    rng = ScriptedPoissonRng(marks=[0], delays=[])

    result = _generate_with_rng(
        model,
        rng,
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0, 0.0, 0.0, 10.0]),
    )
    assert result.trace == ()
    assert result.reason == "max_wall_seconds"
    assert rng.calls == [("choice", 2)]


def test_generation_checks_wall_time_at_later_prospective_emission(model: PoissonModel) -> None:
    """Emitting an in-window mark after its prospective wall check would exceed the deadline."""
    rng = ScriptedPoissonRng(marks=[0, 1], delays=[0.5])

    result = _generate_with_rng(
        model,
        rng,
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0]),
    )
    assert result.trace == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert result.reason == "max_wall_seconds"
    assert rng.calls == [("choice", 2), ("exponential", 0.5), ("choice", 2)]


def test_generation_checks_wall_time_after_a_later_mark_draw(model: PoissonModel) -> None:
    """Keeping a mark drawn after expiry would publish an event beyond the wall deadline."""
    rng = ScriptedPoissonRng(marks=[0, 1], delays=[0.5])

    result = _generate_with_rng(
        model,
        rng,
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0]),
    )
    assert result.trace == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert result.reason == "max_wall_seconds"
    assert rng.calls == [("choice", 2), ("exponential", 0.5), ("choice", 2)]


def test_generation_checks_wall_time_after_exponential_and_mark_draws(model: PoissonModel) -> None:
    """Omitting post-draw wall checks could publish output after the configured deadline."""
    mark_late = _generate_with_rng(
        model,
        ScriptedPoissonRng(marks=[0], delays=[]),
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0, 10.0]),
    )
    delay_late = _generate_with_rng(
        model,
        ScriptedPoissonRng(marks=[0], delays=[2.0]),
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0, 0.0, 0.0, 0.0, 0.0, 10.0]),
    )

    assert mark_late.reason == "max_wall_seconds"
    assert mark_late.trace == ()
    assert delay_late.reason == "max_wall_seconds"
    assert delay_late.trace == (TraceEvent(0.0, Direction.OUTBOUND, 60),)


def test_generation_rejects_an_overflowed_next_arrival_time(model: PoissonModel) -> None:
    """Treating an infinite arrival time as natural completion would hide numeric corruption."""
    with pytest.raises(TrafficlabError, match="arrival time"):
        _generate_with_rng(
            model,
            ScriptedPoissonRng(marks=[0, 0], delays=[1e308, 1e308]),
            W=sys.float_info.max,
            limits=LARGE_LIMITS,
            clock=ScriptedClock([0.0] * 16),
        )


def test_generation_permits_zero_delays_with_non_decreasing_timestamps(model: PoissonModel) -> None:
    """Rejecting a valid zero RNG delay would make supported deterministic traces impossible."""
    rng = ScriptedPoissonRng(marks=[0, 1, 0], delays=[0.0, 0.0])
    limits = GenerationLimits(max_packets=3, max_output_bytes=100_000, max_wall_seconds=10.0)

    result = _generate_with_rng(model, rng, W=1.0, limits=limits, clock=ScriptedClock([0.0] * 16))
    assert result.trace == (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(0.0, Direction.INBOUND, 80),
        TraceEvent(0.0, Direction.OUTBOUND, 60),
    )
    assert result.reason == "max_packets"


@pytest.mark.parametrize("clock_values", [[0.0, math.inf], [0.0, 1.0, 0.5]])
def test_generation_stops_for_nonfinite_or_backward_clock(model: PoissonModel, clock_values: list[float]) -> None:
    """A bad monotonic clock must not let generation claim a complete bounded trace."""
    result = _generate_with_rng(
        model,
        ScriptedPoissonRng(marks=[0], delays=[]),
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock(clock_values),
    )
    assert result.reason == "max_wall_seconds"
    assert result.trace == ()


def test_generation_prioritizes_post_draw_wall_expiry_over_malformed_exponential(model: PoissonModel) -> None:
    """Validating a draw first would leak its structural error after the wall deadline already expired."""
    result = _generate_with_rng(
        model,
        ScriptedPoissonRng(marks=[0], delays=[math.inf]),
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0, 0.0, 0.0, 0.0, 0.0, 10.0]),
    )

    assert result.reason == "max_wall_seconds"
    assert result.trace == (TraceEvent(0.0, Direction.OUTBOUND, 60),)


@pytest.mark.parametrize("delay", [math.nan, math.inf, -math.inf, -0.1])
def test_generation_rejects_invalid_rng_delays(model: PoissonModel, delay: float) -> None:
    """Permitting an invalid delay would create a noncanonical or nonterminating trace."""
    with pytest.raises(TrafficlabError, match="random delay"):
        _generate_with_rng(
            model,
            ScriptedPoissonRng(marks=[0], delays=[delay]),
            W=1.0,
            limits=LARGE_LIMITS,
            clock=ScriptedClock([0.0] * 6),
        )


@pytest.mark.parametrize("seed", [True, -1, 1.0])
def test_public_generate_requires_an_exact_nonnegative_integer_seed(model: PoissonModel, seed: object) -> None:
    """Passing a coercible seed would make the public reproducibility contract ambiguous."""
    with pytest.raises(TrafficlabError, match="seed"):
        FAMILY.generate(model, seed, 1.0, LARGE_LIMITS)  # type: ignore[arg-type]


def test_public_generation_is_seed_reproducible_and_leaves_global_rng_unchanged(model: PoissonModel) -> None:
    """Using global randomness would perturb unrelated experiments and break reproduction."""
    random.seed(9182)
    expected = random.random()
    random.seed(9182)

    first = FAMILY.generate(model, 7, 1.0, LARGE_LIMITS)
    second = FAMILY.generate(model, 7, 1.0, LARGE_LIMITS)

    assert first == second
    assert random.random() == expected


@pytest.mark.parametrize(
    ("base_rate", "rate", "marks"),
    [
        (0.0, 1.0, MarkDistribution.from_reference((TraceEvent(0.0, Direction.OUTBOUND, 60),))),
        (1.0, math.inf, MarkDistribution.from_reference((TraceEvent(0.0, Direction.OUTBOUND, 60),))),
        (1.0, 1.0, object()),
    ],
)
def test_model_constructor_rejects_invalid_fitted_state(base_rate: float, rate: float, marks: object) -> None:
    """Allowing invalid fitted state would let generation bypass fit and JSON validation."""
    with pytest.raises((TypeError, ValueError)):
        PoissonModel(base_rate, rate, marks)  # type: ignore[arg-type]


@pytest.mark.parametrize("window", [0.0, -1.0, math.inf, math.nan, True])
def test_generation_rejects_invalid_windows(model: PoissonModel, window: object) -> None:
    """Coercing an invalid generation window would change the closed trace boundary."""
    with pytest.raises(TrafficlabError, match="observation window"):
        _generate_with_rng(model, ScriptedPoissonRng(marks=[], delays=[]), W=window, limits=LARGE_LIMITS)  # type: ignore[arg-type]


def test_generation_rejects_a_non_poisson_model() -> None:
    """Using another family's fitted state would misinterpret its rate and marks."""
    with pytest.raises(TypeError, match="PoissonModel"):
        _generate_with_rng(object(), ScriptedPoissonRng(marks=[], delays=[]), W=1.0, limits=LARGE_LIMITS)  # type: ignore[arg-type]


def test_fit_rejects_an_overflowing_candidate_rate() -> None:
    """Serializing an infinite fitted rate would create an unusable generation model."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(5e-324, Direction.INBOUND, 80),
    )
    with pytest.raises(TrafficlabError, match="fitted Poisson rate"):
        FAMILY.fit(TrafficTrace.from_events(reference), (4.0,), W=5e-324, bounds=BOUNDS)


@pytest.mark.parametrize(
    "payload",
    [
        object(),
        {"base_rate": 1.0, "rate": 2.0, "marks": "not-a-list"},
        {"base_rate": 1.0, "rate": 2.0, "marks": [{"direction": 1, "frame_length": 60, "count": 1}]},
        {"base_rate": 1.0, "rate": 2.0, "marks": [{"direction": "sideways", "frame_length": 60, "count": 1}]},
        {"base_rate": 1.0, "rate": 2.0, "marks": [{"direction": "outbound", "frame_length": 13, "count": 1}]},
        {"base_rate": 1.0, "rate": 2.0, "marks": [{"direction": "outbound", "frame_length": 60, "count": 0}]},
        {
            "base_rate": 1.0,
            "rate": 2.0,
            "marks": [
                {"direction": "outbound", "frame_length": 60, "count": 1},
                {"direction": "outbound", "frame_length": 60, "count": 1},
            ],
        },
    ],
)
def test_load_rejects_invalid_mark_payloads(payload: object) -> None:
    """Permitting malformed serialized marks would bypass the empirical distribution invariant."""
    with pytest.raises(TrafficlabError):
        FAMILY.load_fitted(payload, genes=(2.0,), bounds=BOUNDS)


@pytest.mark.parametrize("genes", [(), (True,), (1,), (math.nan,)])
def test_load_revalidates_outer_genes(genes: tuple[object, ...]) -> None:
    """Trusting caller-supplied outer genes would decouple a payload's rate from its chromosome."""
    payload = {
        "base_rate": 1.0,
        "rate": 2.0,
        "marks": [{"direction": "outbound", "frame_length": 60, "count": 1}],
    }
    with pytest.raises(TrafficlabError):
        FAMILY.load_fitted(payload, genes=genes, bounds=BOUNDS)  # type: ignore[arg-type]
