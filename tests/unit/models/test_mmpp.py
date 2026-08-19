"""Behavioral tests for the two-state MMPP traffic model."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import math
import random
import sys
from collections.abc import Sequence
from typing import cast

import pytest

from trafficlab.config import FloatBounds, GenerationLimits, MmppConfig
from trafficlab.errors import TrafficlabError
from trafficlab.models import mmpp
from trafficlab.models.common import MarkCount
from trafficlab.models.mmpp import MmppFamily, MmppModel, _generate_with_rng
from trafficlab.trace import Direction, TraceEvent, TrafficTrace

FAMILY = MmppFamily()
BOUNDS = MmppConfig(
    q01=FloatBounds(lower=0.25, upper=4.0),
    q10=FloatBounds(lower=0.5, upper=8.0),
    lambda0=FloatBounds(lower=1.0, upper=5.0),
    lambda1=FloatBounds(lower=2.0, upper=10.0),
)
LARGE_LIMITS = GenerationLimits(max_packets=100, max_output_bytes=100_000, max_wall_seconds=10.0)
REFERENCE = TrafficTrace.from_events(
    (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(1.0, Direction.INBOUND, 80),
        TraceEvent(2.0, Direction.OUTBOUND, 60),
    )
)


class ScriptedMmppRng:
    """Expose exact initial, mark, arrival, and transition draw order."""

    def __init__(
        self, *, random_values: Sequence[float], indices: Sequence[int], exponentials: Sequence[float]
    ) -> None:
        self._random_values = iter(random_values)
        self._indices = iter(indices)
        self._exponentials = iter(exponentials)
        self.calls: list[tuple[str, float | int | None]] = []

    def random(self) -> float:
        self.calls.append(("random", None))
        return next(self._random_values)

    def choice(self, a: int) -> int:
        self.calls.append(("choice", a))
        return next(self._indices)

    def exponential(self, scale: float) -> float:
        self.calls.append(("exponential", scale))
        return next(self._exponentials)


class ScriptedClock:
    """Place a wall deadline at exact positions around raw stochastic draws."""

    def __init__(self, values: Sequence[float]) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def fit_mmpp(genes: tuple[float, float, float, float]) -> MmppModel:
    """Fit the stable reference trace with one MMPP chromosome."""
    return FAMILY.fit(REFERENCE, genes, W=2.0, bounds=BOUNDS)


def test_family_declares_the_mmpp_chromosome_contract() -> None:
    """Generic GA code depends on this exact coordinate order and metadata."""
    assert FAMILY.name == "mmpp"
    assert FAMILY.gene_names == ("q01", "q10", "lambda0", "lambda1")
    assert FAMILY.bounds_type is MmppConfig
    assert FAMILY.estimator_choices == {
        "rates": "direct_genes",
        "initial_regime": "arrival_epoch",
        "marks": "joint_empirical_first_appearance",
        "tie": "regime_change",
        "first_event": "zero",
    }


def test_stationary_probabilities_keep_named_transition_rates() -> None:
    """Sorting q rates would silently reverse the CTMC's stationary distribution."""
    model = fit_mmpp((1.0, 3.0, 2.0, 8.0))
    assert (model.q01, model.q10) == (1.0, 3.0)
    assert (model.pi0, model.pi1) == (0.75, 0.25)


def test_stationary_probabilities_avoid_overflow_in_transition_sum() -> None:
    """Finite extreme q rates must not make their stationary normalization infinite."""
    model = MmppModel(math.ldexp(1.0, 1023), math.ldexp(1.0, 1022), 2.0, 8.0, fit_mmpp((1.0, 3.0, 2.0, 8.0)).marks)
    assert math.isfinite(model.pi0)
    assert math.isfinite(model.pi1)
    assert math.isclose(model.pi0 + model.pi1, 1.0, rel_tol=0.0, abs_tol=1e-12)


def test_arrival_epoch_probabilities_weight_stationary_regimes_by_arrival_rates() -> None:
    """Initial events sample stationary mass weighted by each regime's arrival intensity."""
    assert mmpp._arrival_epoch_probabilities(1.0, 3.0, 1.0, 9.0) == (0.25, 0.75)


def test_arrival_epoch_probabilities_normalize_near_the_largest_finite_rates() -> None:
    """Finite rate products near the float limit must not overflow before normalization."""
    below_maximum = math.nextafter(sys.float_info.max, 0.0)
    a0, a1 = mmpp._arrival_epoch_probabilities(
        sys.float_info.max,
        below_maximum,
        below_maximum,
        sys.float_info.max,
    )

    assert math.isfinite(a0)
    assert math.isfinite(a1)
    assert 0.0 < a0 < 1.0
    assert 0.0 < a1 < 1.0
    assert math.isclose(a0 + a1, 1.0, rel_tol=0.0, abs_tol=1e-12)


@pytest.mark.parametrize(
    "rates",
    [
        (0.0, 1.0, 1.0, 2.0),
        (1.0, math.nan, 1.0, 2.0),
        (1.0, 3.0, 0.0, 2.0),
        (1.0, 3.0, 1.0, math.inf),
    ],
)
def test_arrival_epoch_probabilities_reject_invalid_derived_rate_boundaries(
    rates: tuple[float, float, float, float],
) -> None:
    """A zero or nonfinite CTMC or arrival rate has no valid arrival-epoch law."""
    with pytest.raises(ValueError, match="arrival-epoch"):
        mmpp._arrival_epoch_probabilities(*rates)


@pytest.mark.parametrize(
    "rates",
    [
        (True, 3.0, 1.0, 2.0),
        (1.0, 3, 1.0, 2.0),
        (1.0, 3.0, 1, 2.0),
        (1.0, 3.0, 1.0, False),
        (-1.0, 3.0, 1.0, 2.0),
        (1.0, -3.0, 1.0, 2.0),
        (1.0, 3.0, -1.0, 2.0),
        (1.0, 3.0, 1.0, -2.0),
    ],
)
def test_arrival_epoch_probabilities_reject_non_float_and_negative_rates(
    rates: tuple[object, object, object, object],
) -> None:
    """All four rate positions require exact positive finite floats."""
    with pytest.raises(ValueError, match="arrival-epoch"):
        mmpp._arrival_epoch_probabilities(*rates)


def test_repair_sorts_only_arrival_rates() -> None:
    """Transition rates have named directions; only lambda genes are exchangeable."""
    assert FAMILY.repair((1.0, 3.0, 8.0, 2.0), BOUNDS, REFERENCE) == (1.0, 3.0, 2.0, 8.0)


@pytest.mark.parametrize(
    ("genes", "expected"),
    [
        ((1.0, 99.0, 9.0, 2.0), (1.0, 8.0, 2.0, 9.0)),
        ((4.0, 0.5, 1.0, 10.0), (4.0, 0.5, 1.0, 10.0)),
    ],
)
def test_repair_clamps_each_gene_to_its_named_inclusive_bound(
    genes: tuple[float, float, float, float], expected: tuple[float, float, float, float]
) -> None:
    """Reusing a bound by position after lambda sorting changes the chromosome domain."""
    assert FAMILY.repair(genes, BOUNDS, REFERENCE) == expected


@pytest.mark.parametrize(
    "genes",
    [
        (),
        (1.0, 3.0, 2.0),
        (1.0, 3.0, 2.0, 8.0, 9.0),
        (True, 3.0, 2.0, 8.0),
        (1.0, 3, 2.0, 8.0),
        (0.0, 3.0, 2.0, 8.0),
        (1.0, -3.0, 2.0, 8.0),
        (1.0, 3.0, math.nan, 8.0),
        (1.0, 3.0, 2.0, math.inf),
    ],
)
def test_repair_rejects_noncanonical_genes(genes: tuple[object, ...]) -> None:
    """Coercion would make persisted chromosomes ambiguous."""
    with pytest.raises(TrafficlabError):
        FAMILY.repair(genes, BOUNDS, REFERENCE)  # type: ignore[arg-type]


def test_repair_rejects_equal_or_post_clamp_disordered_arrival_rates() -> None:
    """Identifiability requires strict low and high arrival rates after clamping."""
    with pytest.raises(TrafficlabError, match="arrival"):
        FAMILY.repair((1.0, 3.0, 2.0, 2.0), BOUNDS, REFERENCE)
    crossing = MmppConfig(
        q01=FloatBounds(lower=0.25, upper=4.0),
        q10=FloatBounds(lower=0.5, upper=8.0),
        lambda0=FloatBounds(lower=8.0, upper=9.0),
        lambda1=FloatBounds(lower=1.0, upper=2.0),
    )
    with pytest.raises(TrafficlabError, match="arrival"):
        FAMILY.repair((1.0, 3.0, 1.0, 10.0), crossing, REFERENCE)


def test_fit_stores_the_repaired_genes_and_joint_empirical_marks() -> None:
    """MMPP fitting owns no separate likelihood optimizer in this MVP."""
    model = fit_mmpp((1.0, 3.0, 8.0, 2.0))
    assert (model.q01, model.q10, model.lambda0, model.lambda1) == (1.0, 3.0, 2.0, 8.0)
    assert model.marks.entries == (
        MarkCount(Direction.OUTBOUND, 60, 2),
        MarkCount(Direction.INBOUND, 80, 1),
    )


def test_fitted_model_round_trips_only_strict_json_without_derived_probabilities() -> None:
    """Derived stationary values must remain reproducible from the named q rates alone."""
    model = fit_mmpp((1.0, 3.0, 2.0, 8.0))
    payload = FAMILY.dump_fitted(model)
    assert payload == {
        "q01": 1.0,
        "q10": 3.0,
        "lambda0": 2.0,
        "lambda1": 8.0,
        "marks": [
            {"direction": "outbound", "frame_length": 60, "count": 2},
            {"direction": "inbound", "frame_length": 80, "count": 1},
        ],
    }
    assert FAMILY.load_fitted(payload, genes=(1.0, 3.0, 2.0, 8.0), bounds=BOUNDS) == model
    malformed_payloads: tuple[object, ...] = (
        {**payload, "pi0": 0.75},
        {**payload, "q01": True},
        {**payload, "lambda0": math.inf},
        {**payload, "marks": []},
        {**payload, "marks": [{"direction": "outbound", "frame_length": 60, "count": 0}]},
    )
    for malformed in malformed_payloads:
        with pytest.raises(TrafficlabError):
            FAMILY.load_fitted(malformed, genes=(1.0, 3.0, 2.0, 8.0), bounds=BOUNDS)


@pytest.fixture
def model() -> MmppModel:
    """Return a hand-checked low/high regime model with two empirical marks."""
    return fit_mmpp((1.0, 3.0, 2.0, 8.0))


def test_generate_races_arrival_before_transition_and_resamples_both_clocks(model: MmppModel) -> None:
    """Reusing a losing clock would violate the documented memoryless simulation construction."""
    rng = ScriptedMmppRng(random_values=[0.0], indices=[0, 2], exponentials=[0.5, 0.75, 2.0, 2.0])
    result = _generate_with_rng(model, rng, W=1.0, limits=LARGE_LIMITS, clock=ScriptedClock([0.0] * 20))

    assert result.require_complete() == (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(0.5, Direction.INBOUND, 80),
    )
    assert rng.calls == [
        ("random", None),
        ("choice", model.marks.total_count),
        ("exponential", 1.0 / model.lambda0),
        ("exponential", 1.0 / model.q01),
        ("choice", model.marks.total_count),
        ("exponential", 1.0 / model.lambda0),
        ("exponential", 1.0 / model.q01),
    ]


def test_generate_stops_before_any_draw_when_initial_wall_clock_is_invalid(model: MmppModel) -> None:
    rng = ScriptedMmppRng(random_values=[0.0], indices=[0], exponentials=[2.0, 2.0])

    result = _generate_with_rng(model, rng, W=1.0, limits=LARGE_LIMITS, clock=ScriptedClock([math.inf]))

    assert result.complete is False
    assert result.events == ()
    assert result.reason == "max_wall_seconds"
    assert rng.calls == []


def test_generate_exact_race_tie_changes_regime_without_emission(model: MmppModel) -> None:
    """Arrival ties belong to the CTMC transition, so no simultaneous packet is emitted."""
    rng = ScriptedMmppRng(random_values=[0.0], indices=[0], exponentials=[0.5, 0.5, 2.0, 2.0])
    result = _generate_with_rng(model, rng, W=1.0, limits=LARGE_LIMITS, clock=ScriptedClock([0.0] * 20))

    assert result.require_complete() == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert rng.calls[:4] == [
        ("random", None),
        ("choice", model.marks.total_count),
        ("exponential", 1.0 / model.lambda0),
        ("exponential", 1.0 / model.q01),
    ]
    assert ("choice", model.marks.total_count) not in rng.calls[2:]


def test_generate_starts_in_high_regime_from_arrival_epoch_draw(model: MmppModel) -> None:
    """Selecting the wrong arrival-epoch partition would use the wrong first pair of rates."""
    rng = ScriptedMmppRng(random_values=[0.9], indices=[0, 2], exponentials=[0.25, 0.5, 2.0, 2.0])
    result = _generate_with_rng(model, rng, W=1.0, limits=LARGE_LIMITS, clock=ScriptedClock([0.0] * 20))

    assert result.require_complete()[1] == TraceEvent(0.25, Direction.INBOUND, 80)
    assert rng.calls[2:4] == [("exponential", 1.0 / model.lambda1), ("exponential", 1.0 / model.q10)]


def test_generate_arrival_epoch_threshold_selects_regime_one(model: MmppModel) -> None:
    """The equality boundary belongs to regime one under the conditioned initial law."""
    conditioned = MmppModel(1.0, 3.0, 1.0, 9.0, model.marks)
    rng = ScriptedMmppRng(random_values=[0.25], indices=[0], exponentials=[2.0, 2.0])

    result = _generate_with_rng(conditioned, rng, W=1.0, limits=LARGE_LIMITS, clock=ScriptedClock([0.0] * 12))

    assert result.require_complete() == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert rng.calls[2:4] == [("exponential", 1.0 / conditioned.lambda1), ("exponential", 1.0 / conditioned.q10)]


def test_generate_samples_the_conditioned_time_zero_mark_before_later_clocks(model: MmppModel) -> None:
    """Arrival-epoch conditioning changes only regime selection, not the documented mark-first draw order."""
    conditioned = MmppModel(1.0, 3.0, 1.0, 9.0, model.marks)
    rng = ScriptedMmppRng(random_values=[0.0], indices=[2], exponentials=[2.0, 2.0])

    result = _generate_with_rng(conditioned, rng, W=1.0, limits=LARGE_LIMITS, clock=ScriptedClock([0.0] * 12))

    assert result.require_complete() == (TraceEvent(0.0, Direction.INBOUND, 80),)
    assert rng.calls == [
        ("random", None),
        ("choice", conditioned.marks.total_count),
        ("exponential", 1.0 / conditioned.lambda0),
        ("exponential", 1.0 / conditioned.q01),
    ]


def test_generate_arrival_epoch_tie_still_changes_regime_without_emission(model: MmppModel) -> None:
    """Conditioning the first regime must not change the strict arrival-before-transition race rule."""
    conditioned = MmppModel(1.0, 3.0, 1.0, 9.0, model.marks)
    rng = ScriptedMmppRng(random_values=[0.25], indices=[0], exponentials=[0.5, 0.5, 2.0, 2.0])

    result = _generate_with_rng(conditioned, rng, W=1.0, limits=LARGE_LIMITS, clock=ScriptedClock([0.0] * 20))

    assert result.require_complete() == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert rng.calls == [
        ("random", None),
        ("choice", conditioned.marks.total_count),
        ("exponential", 1.0 / conditioned.lambda1),
        ("exponential", 1.0 / conditioned.q10),
        ("exponential", 1.0 / conditioned.lambda0),
        ("exponential", 1.0 / conditioned.q01),
    ]


def test_generate_processes_closed_window_endpoint_and_completes_only_after_selected_event(model: MmppModel) -> None:
    """An event at W belongs in the trace, while either clock alone cannot establish completion."""
    endpoint = ScriptedMmppRng(random_values=[0.0], indices=[0, 2], exponentials=[1.0, 2.0, 2.0, 2.0])
    endpoint_result = _generate_with_rng(model, endpoint, W=1.0, limits=LARGE_LIMITS, clock=ScriptedClock([0.0] * 20))
    switches = ScriptedMmppRng(random_values=[0.0], indices=[0], exponentials=[2.0, 0.5, 2.0, 2.0])
    switched_result = _generate_with_rng(model, switches, W=1.0, limits=LARGE_LIMITS, clock=ScriptedClock([0.0] * 20))

    assert endpoint_result.require_complete()[-1] == TraceEvent(1.0, Direction.INBOUND, 80)
    assert switched_result.require_complete() == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert switches.calls == [
        ("random", None),
        ("choice", model.marks.total_count),
        ("exponential", 1.0 / model.lambda0),
        ("exponential", 1.0 / model.q01),
        ("exponential", 1.0 / model.lambda1),
        ("exponential", 1.0 / model.q10),
    ]


def test_generate_checks_following_pre_draw_guard_after_nonemitting_regime_switch(model: MmppModel) -> None:
    """A transition consumes no mark, then the next race must stop before further clocks at wall expiry."""
    rng = ScriptedMmppRng(random_values=[0.0], indices=[0], exponentials=[2.0, 0.5])
    result = _generate_with_rng(
        model,
        rng,
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0] * 8 + [10.0]),
    )

    assert result.events == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert result.reason == "max_wall_seconds"
    assert rng.calls == [
        ("random", None),
        ("choice", model.marks.total_count),
        ("exponential", 1.0 / model.lambda0),
        ("exponential", 1.0 / model.q01),
    ]


def test_generate_checks_wall_after_each_raw_draw_before_validating_it(model: MmppModel) -> None:
    """Expired generation returns its diagnostic before a malformed raw draw can leak an error."""
    initial = _generate_with_rng(
        model,
        ScriptedMmppRng(random_values=[math.inf], indices=[], exponentials=[]),
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0, 0.0, 10.0]),
    )
    arrival = _generate_with_rng(
        model,
        ScriptedMmppRng(random_values=[0.0], indices=[0], exponentials=[math.inf]),
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0] * 6 + [10.0]),
    )
    transition = _generate_with_rng(
        model,
        ScriptedMmppRng(random_values=[0.0], indices=[0], exponentials=[0.5, math.inf]),
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0] * 7 + [10.0]),
    )

    assert initial.reason == arrival.reason == transition.reason == "max_wall_seconds"
    assert initial.events == ()
    assert arrival.events == transition.events == (TraceEvent(0.0, Direction.OUTBOUND, 60),)


@pytest.mark.parametrize("delay", [math.nan, math.inf, -math.inf, -0.1])
def test_generate_rejects_invalid_exponential_delays(model: MmppModel, delay: float) -> None:
    """Malformed exponential output must never become an event time."""
    with pytest.raises(TrafficlabError, match="random delay"):
        _generate_with_rng(
            model,
            ScriptedMmppRng(random_values=[0.0], indices=[0], exponentials=[delay]),
            W=1.0,
            limits=LARGE_LIMITS,
            clock=ScriptedClock([0.0] * 30),
        )


@pytest.mark.parametrize("draw", [True, 1, -0.1, 1.0, math.nan, math.inf])
def test_generate_rejects_invalid_initial_regime_draw(model: MmppModel, draw: object) -> None:
    """A noncanonical uniform draw makes arrival-epoch initialization undefined."""
    with pytest.raises(TrafficlabError, match="random draw"):
        _generate_with_rng(
            model,
            ScriptedMmppRng(random_values=[draw], indices=[], exponentials=[]),  # type: ignore[list-item]
            W=1.0,
            limits=LARGE_LIMITS,
            clock=ScriptedClock([0.0] * 5),
        )


def test_generate_stops_at_prospective_mark_limit_after_an_in_window_arrival(model: MmppModel) -> None:
    """A mark that would exceed output bytes must not enter the diagnostic trace."""
    result = _generate_with_rng(
        model,
        ScriptedMmppRng(random_values=[0.0], indices=[0, 2], exponentials=[0.5, 1.0]),
        W=1.0,
        limits=GenerationLimits(max_packets=2, max_output_bytes=139, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0] * 20),
    )
    assert result.events == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert result.reason == "max_output_bytes"


@pytest.mark.parametrize("seed", [True, -1, 1.0])
def test_generate_requires_an_exact_nonnegative_seed(model: MmppModel, seed: object) -> None:
    """Coercible public seeds would make reproducibility ambiguous."""
    with pytest.raises(TrafficlabError, match="seed"):
        FAMILY.generate(model, seed, 1.0, LARGE_LIMITS)  # type: ignore[arg-type]


def test_generate_is_seed_reproducible_without_changing_global_rng(model: MmppModel) -> None:
    """Using global randomness would perturb unrelated fitting experiments."""
    random.seed(9182)
    expected = random.random()
    random.seed(9182)
    assert FAMILY.generate(model, 7, 1.0, LARGE_LIMITS) == FAMILY.generate(model, 7, 1.0, LARGE_LIMITS)
    assert random.random() == expected


def test_generate_rejects_overflowed_absolute_clock_time(model: MmppModel) -> None:
    """An overflowed arrival or transition time cannot be treated as natural completion."""
    with pytest.raises(TrafficlabError, match="arrival time"):
        _generate_with_rng(
            model,
            ScriptedMmppRng(random_values=[0.0], indices=[0, 0], exponentials=[1e308, 1.1e308, 1e308, 1e308]),
            W=sys.float_info.max,
            limits=LARGE_LIMITS,
            clock=ScriptedClock([0.0] * 30),
        )


def test_generate_rejects_overflowed_absolute_transition_clock_time(model: MmppModel) -> None:
    """The losing transition clock is structurally validated before the race winner is selected."""
    with pytest.raises(TrafficlabError, match="transition time"):
        _generate_with_rng(
            model,
            ScriptedMmppRng(
                random_values=[0.0],
                indices=[0, 0],
                exponentials=[1e308, 1.1e308, 0.0, 1e308],
            ),
            W=sys.float_info.max,
            limits=LARGE_LIMITS,
            clock=ScriptedClock([0.0] * 20),
        )


def test_generate_rejects_non_mmpp_model() -> None:
    """Interpreting another family's state as MMPP parameters would corrupt generation."""
    with pytest.raises(TypeError, match="MmppModel"):
        _generate_with_rng(
            cast(MmppModel, object()),
            ScriptedMmppRng(random_values=[], indices=[], exponentials=[]),
            W=1.0,
            limits=LARGE_LIMITS,
        )


@pytest.mark.parametrize("window", [0.0, -1.0, math.nan, math.inf, True])
def test_generate_rejects_noncanonical_windows(model: MmppModel, window: object) -> None:
    """Generation uses the same finite positive normalized window contract as fitting."""
    with pytest.raises(TrafficlabError, match="observation window"):
        _generate_with_rng(
            model,
            ScriptedMmppRng(random_values=[], indices=[], exponentials=[]),
            W=window,  # type: ignore[arg-type]
            limits=LARGE_LIMITS,
        )


def test_repair_rejects_foreign_and_bypassed_invalid_bounds() -> None:
    """Only a complete positive MMPP coordinate domain can repair a chromosome."""
    invalid = BOUNDS.model_copy(update={"q01": FloatBounds.model_construct(lower=0.0, upper=4.0)})
    for bounds in (object(), invalid):
        with pytest.raises(TrafficlabError, match="bounds"):
            FAMILY.repair((1.0, 3.0, 2.0, 8.0), bounds, REFERENCE)  # type: ignore[arg-type]


def test_model_and_stationary_helpers_reject_invalid_structural_state(model: MmppModel) -> None:
    """Constructor-level checks prevent direct callers from bypassing fitted invariants."""
    for args in (
        (0.0, 3.0, 2.0, 8.0, model.marks),
        (1.0, 3.0, 8.0, 2.0, model.marks),
        (1.0, 3.0, 2.0, 8.0, object()),
    ):
        with pytest.raises((TypeError, ValueError)):
            MmppModel(*args)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="transition rates"):
        mmpp._stationary_probabilities(0.0, 1.0)


@pytest.mark.parametrize("draw", [True, -1, 3])
def test_mark_draw_rejects_noncanonical_empirical_indices(model: MmppModel, draw: object) -> None:
    """Malformed raw mark draws must not be coerced into empirical entries."""
    with pytest.raises(TrafficlabError, match="empirical random draw"):
        mmpp._mark_from_draw(model.marks, draw)


def test_generate_checks_initial_and_later_mark_wall_deadlines(model: MmppModel) -> None:
    """Every randrange result receives its immediate wall check before validation or emission."""
    initial = _generate_with_rng(
        model,
        ScriptedMmppRng(random_values=[0.0], indices=[0], exponentials=[]),
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0, 0.0, 0.0, 10.0]),
    )
    later = _generate_with_rng(
        model,
        ScriptedMmppRng(random_values=[0.0], indices=[0, 2], exponentials=[0.5, 1.0]),
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0] * 8 + [10.0]),
    )
    assert initial.reason == later.reason == "max_wall_seconds"
    assert initial.events == ()
    assert later.events == (TraceEvent(0.0, Direction.OUTBOUND, 60),)


def test_generate_checks_initial_prospective_and_next_pre_draw_guards(model: MmppModel) -> None:
    """Initial marks and later event races each stop before their relevant reliability boundary."""
    initial = _generate_with_rng(
        model,
        ScriptedMmppRng(random_values=[0.0], indices=[0], exponentials=[]),
        W=1.0,
        limits=GenerationLimits(max_packets=1, max_output_bytes=59, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0] * 8),
    )
    next_race = _generate_with_rng(
        model,
        ScriptedMmppRng(random_values=[0.0], indices=[0], exponentials=[]),
        W=1.0,
        limits=GenerationLimits(max_packets=1, max_output_bytes=100_000, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0] * 8),
    )
    assert initial.events == ()
    assert initial.reason == "max_output_bytes"
    assert next_race.events == (TraceEvent(0.0, Direction.OUTBOUND, 60),)
    assert next_race.reason == "max_packets"


@pytest.mark.parametrize(
    "payload",
    [
        object(),
        {"q01": 1.0, "q10": 3.0, "lambda0": 2.0, "lambda1": 8.0, "marks": "wrong"},
        {"q01": 1.0, "q10": 3.0, "lambda0": 2.0, "lambda1": 8.0, "marks": [object()]},
        {
            "q01": 1.0,
            "q10": 3.0,
            "lambda0": 2.0,
            "lambda1": 8.0,
            "marks": [{"direction": "outbound", "frame_length": 60, "count": True}],
        },
    ],
)
def test_load_rejects_noncanonical_payload_and_mark_shapes(payload: object) -> None:
    """Strict loading cannot admit malformed JSON marks or an untyped root payload."""
    with pytest.raises(TrafficlabError):
        FAMILY.load_fitted(payload, genes=(1.0, 3.0, 2.0, 8.0), bounds=BOUNDS)
