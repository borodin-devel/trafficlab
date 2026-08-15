"""Behavioral tests for the empirical Markov renewal traffic model."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import math
import random
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import cast

import pytest

from trafficlab.config import FloatBounds, GenerationLimits, IntegerBounds, MarkovRenewalConfig
from trafficlab.errors import TrafficlabError
from trafficlab.models import markov_renewal
from trafficlab.models.markov_renewal import (
    MarkovRenewalFamily,
    MarkovRenewalModel,
    MarkovState,
    size_bin,
    type7_quantile,
)
from trafficlab.trace import Direction, TraceEvent

FAMILY = MarkovRenewalFamily()
BOUNDS = MarkovRenewalConfig(
    q1=FloatBounds(lower=0.1, upper=0.4),
    q2=FloatBounds(lower=0.6, upper=0.9),
    alpha=FloatBounds(lower=0.0, upper=2.0),
    r=IntegerBounds(lower=1, upper=5),
    c_t=FloatBounds(lower=0.5, upper=2.0),
)
DISTINCT_REFERENCE = (
    TraceEvent(0.0, Direction.INBOUND, 20),
    TraceEvent(1.0, Direction.OUTBOUND, 80),
    TraceEvent(2.0, Direction.INBOUND, 40),
    TraceEvent(3.0, Direction.OUTBOUND, 60),
)
LARGE_LIMITS = GenerationLimits(max_packets=100, max_output_bytes=100_000, max_wall_seconds=10.0)


class ScriptedMarkovRng:
    """Expose every continuous and integer draw made by Markov generation."""

    def __init__(self, *, random_values: Sequence[float], indices: Sequence[int]) -> None:
        self._random_values = iter(random_values)
        self._indices = iter(indices)
        self.calls: list[tuple[str, int | None]] = []

    def random(self) -> float:
        self.calls.append(("random", None))
        return next(self._random_values)

    def randrange(self, stop: int) -> int:
        self.calls.append(("randrange", stop))
        return next(self._indices)


class ScriptedClock:
    """Place wall-clock boundaries exactly around stochastic draws."""

    def __init__(self, values: Sequence[float]) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def test_type7_thresholds_and_inclusive_bins() -> None:
    """Changing interpolation or bin inequalities would change observable states."""
    lengths = (10, 20, 30, 40)
    assert type7_quantile(lengths, 0.25) == 17.5
    assert type7_quantile(lengths, 0.75) == 32.5
    assert tuple(size_bin(length, 17.5, 32.5) for length in (10, 17, 18, 32, 33, 40)) == (0, 0, 1, 1, 2, 2)


@pytest.mark.parametrize(
    ("values", "q"),
    [
        (None, 0.5),
        ((), 0.5),
        ((1.0,), True),
        ((1.0,), math.nan),
        ((math.inf,), 0.5),
    ],
)
def test_type7_quantile_rejects_malformed_samples_and_levels(values: object, q: object) -> None:
    """Coercing an invalid sample or level would make state thresholds noncanonical."""
    with pytest.raises(TrafficlabError, match="quantile"):
        type7_quantile(values, q)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("frame_length", "lower", "upper"),
    [(True, 10.0, 20.0), (20, math.nan, 30.0), (20, 30.0, 30.0)],
)
def test_size_bin_rejects_noncanonical_inputs(frame_length: object, lower: float, upper: float) -> None:
    """Loose bin inputs would permit states fit could never construct."""
    with pytest.raises((TypeError, ValueError)):
        size_bin(frame_length, lower, upper)  # type: ignore[arg-type]


def test_repair_sorts_quantiles_before_named_clamping_and_rounds_r_half_up() -> None:
    """Clamping before sorting or banker's rounding would produce a different chromosome."""
    assert FAMILY.repair((0.8, 0.2, -1.0, 2.5, 10.0), BOUNDS, DISTINCT_REFERENCE) == (
        0.2,
        0.8,
        0.0,
        3,
        2.0,
    )


@pytest.mark.parametrize("r", [0.5, 1.0, 5.49, 9.5])
def test_repair_clamps_half_up_r_to_inclusive_integer_bounds(r: float) -> None:
    """Repair must preserve the configured inclusive integer coordinate domain."""
    repaired = FAMILY.repair((0.2, 0.8, 0.0, r, 1.0), BOUNDS, DISTINCT_REFERENCE)
    assert 1 <= repaired[3] <= 5
    assert type(repaired[3]) is int


def test_fit_builds_active_states_in_first_appearance_order_with_reference_lengths() -> None:
    """Sorting active states or lengths would alter every stored transition index and RNG sequence."""
    model = FAMILY.fit(DISTINCT_REFERENCE, (0.25, 0.75, 0.0, 1.0, 1.0), W=3.0, bounds=BOUNDS)
    assert tuple((state.direction, state.size_bin, state.frame_lengths) for state in model.states) == (
        (Direction.INBOUND, 0, (20,)),
        (Direction.OUTBOUND, 2, (80,)),
        (Direction.INBOUND, 1, (40,)),
        (Direction.OUTBOUND, 1, (60,)),
    )
    assert len({state.size_bin for state in model.states if state.direction is Direction.INBOUND}) <= 3
    assert len({state.size_bin for state in model.states if state.direction is Direction.OUTBOUND}) <= 3


@pytest.mark.parametrize(
    "genes",
    [
        (),
        (0.2, 0.8, 0.0, 2.0),
        (0.2, 0.8, 0.0, 2.0, 1.0, 9.0),
        (True, 0.8, 0.0, 2.0, 1.0),
        (0.2, 1, 0.0, 2.0, 1.0),
        (0.2, 0.8, math.nan, 2.0, 1.0),
        (0.2, 0.8, 0.0, math.inf, 1.0),
        (0.2, 0.8, 0.0, 2.0, -math.inf),
    ],
)
def test_repair_rejects_invalid_gene_arity_and_values(genes: tuple[object, ...]) -> None:
    """Coercing malformed genes would make persisted chromosomes ambiguous."""
    with pytest.raises(TrafficlabError):
        FAMILY.repair(genes, BOUNDS, DISTINCT_REFERENCE)  # type: ignore[arg-type]


def test_repair_rejects_named_clamping_that_destroys_quantile_order() -> None:
    """A sorted pair must still obey its named q1 and q2 domains after clamping."""
    crossing_bounds = MarkovRenewalConfig(
        q1=FloatBounds(lower=0.7, upper=0.8),
        q2=FloatBounds(lower=0.2, upper=0.3),
        alpha=FloatBounds(lower=0.0, upper=1.0),
        r=IntegerBounds(lower=1, upper=2),
        c_t=FloatBounds(lower=0.5, upper=2.0),
    )
    with pytest.raises(TrafficlabError, match="quantile"):
        FAMILY.repair((0.1, 0.9, 0.0, 1.0, 1.0), crossing_bounds, DISTINCT_REFERENCE)


def test_repair_rejects_equal_quantiles_and_duplicate_reference_thresholds() -> None:
    """Accepting either equality would collapse one of the three promised size bins."""
    overlapping_bounds = MarkovRenewalConfig(
        q1=FloatBounds(lower=0.2, upper=0.6),
        q2=FloatBounds(lower=0.4, upper=0.8),
        alpha=FloatBounds(lower=0.0, upper=1.0),
        r=IntegerBounds(lower=1, upper=2),
        c_t=FloatBounds(lower=0.5, upper=2.0),
    )
    with pytest.raises(TrafficlabError, match="quantile"):
        FAMILY.repair((0.5, 0.5, 0.0, 1.0, 1.0), overlapping_bounds, DISTINCT_REFERENCE)
    duplicate_lengths = (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(1.0, Direction.INBOUND, 60),
    )
    with pytest.raises(TrafficlabError, match="threshold"):
        FAMILY.repair((0.2, 0.8, 0.0, 1.0, 1.0), BOUNDS, duplicate_lengths)


@pytest.mark.parametrize("bounds", [object(), FloatBounds(lower=0.1, upper=0.9)])
def test_repair_rejects_foreign_bounds(bounds: object) -> None:
    """Using another family's bounds would silently assign the wrong chromosome domains."""
    with pytest.raises(TrafficlabError, match="bounds"):
        FAMILY.repair((0.2, 0.8, 0.0, 1.0, 1.0), bounds, DISTINCT_REFERENCE)  # type: ignore[arg-type]


@pytest.mark.parametrize("reference", [None, (), (TraceEvent(0.0, Direction.OUTBOUND, 20),), (object(), object())])
def test_repair_rejects_malformed_references(reference: object) -> None:
    """Repair must validate its materialized reference before deriving thresholds."""
    with pytest.raises(TrafficlabError, match="reference"):
        FAMILY.repair((0.2, 0.8, 0.0, 1.0, 1.0), BOUNDS, reference)  # type: ignore[arg-type]


def test_repair_revalidates_bounds_even_when_config_validation_was_bypassed() -> None:
    """Trusting a constructed config object would permit invalid genetic coordinate domains."""
    invalid_bounds = (
        BOUNDS.model_copy(update={"q1": FloatBounds.model_construct(lower=0.0, upper=0.4)}),
        BOUNDS.model_copy(update={"alpha": FloatBounds.model_construct(lower=-1.0, upper=2.0)}),
        BOUNDS.model_copy(update={"r": IntegerBounds.model_construct(lower=0, upper=5)}),
        BOUNDS.model_copy(update={"c_t": FloatBounds.model_construct(lower=0.0, upper=2.0)}),
    )
    for bounds in invalid_bounds:
        with pytest.raises(TrafficlabError, match="bounds"):
            FAMILY.repair((0.2, 0.8, 0.0, 1.0, 1.0), bounds, DISTINCT_REFERENCE)


def test_family_declares_the_markov_renewal_chromosome_contract() -> None:
    """Wrong metadata would make generic genetic-model code encode the family incorrectly."""
    assert FAMILY.name == "markov_renewal"
    assert FAMILY.gene_names == ("q1", "q2", "alpha", "r", "c_t")
    assert FAMILY.bounds_type is MarkovRenewalConfig
    assert FAMILY.estimator_choices == {
        "first_event": "zero",
        "quantile": "type7_linear",
        "state_order": "first_appearance",
        "timing": "conditional_source_global",
        "transition": "additive_uniform_empty_row",
    }


def _two_state_reference() -> tuple[TraceEvent, ...]:
    return (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(1.0, Direction.INBOUND, 80),
    )


def _two_state_model(*, alpha: float = 0.0, minimum_support: float = 2.0) -> MarkovRenewalModel:
    return FAMILY.fit(
        _two_state_reference(),
        (0.25, 0.75, alpha, minimum_support, 1.0),
        W=1.0,
        bounds=BOUNDS,
    )


def test_complete_additive_transition_estimator_and_ordered_iat_samples() -> None:
    """Dropping smoothing or misaligning samples would change both the kernel and holding times."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(1.0, Direction.INBOUND, 80),
        TraceEvent(2.0, Direction.OUTBOUND, 20),
        TraceEvent(4.0, Direction.INBOUND, 80),
        TraceEvent(5.0, Direction.INBOUND, 80),
    )
    model = FAMILY.fit(reference, (0.25, 0.75, 1.0, 2.0, 1.0), W=5.0, bounds=BOUNDS)

    assert model.transition_rows == ((0.25, 0.75), (0.5, 0.5))
    assert model.conditional_iats == ((((), (1.0, 2.0))), (((1.0,), (1.0,))))
    assert tuple(state.source_iats for state in model.states) == ((1.0, 2.0), (1.0, 1.0))
    assert model.global_iats == (1.0, 1.0, 2.0, 1.0)


def test_final_only_zero_smoothed_row_is_uniform() -> None:
    """Dividing an unobserved zero-smoothed row by zero would leave generation undefined."""
    model = _two_state_model(alpha=0.0)
    assert model.transition_rows == ((0.0, 1.0), (0.5, 0.5))


def test_positive_smoothing_empty_row_uses_the_ordinary_uniform_formula() -> None:
    """Special-casing every empty row could accidentally bypass additive smoothing."""
    model = _two_state_model(alpha=2.0)
    assert model.transition_rows[1] == (0.5, 0.5)
    assert model.transition_rows[0] == (0.4, 0.6)


def test_nonempty_zero_smoothed_row_equals_empirical_frequencies() -> None:
    """Applying smoothing at alpha zero would corrupt empirical transition frequencies."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(1.0, Direction.OUTBOUND, 20),
        TraceEvent(2.0, Direction.INBOUND, 80),
        TraceEvent(3.0, Direction.OUTBOUND, 20),
    )
    model = FAMILY.fit(reference, (0.25, 0.75, 0.0, 1.0, 1.0), W=3.0, bounds=BOUNDS)
    assert model.transition_rows[0] == (0.5, 0.5)
    assert model.transition_rows[1] == (1.0, 0.0)


@pytest.mark.parametrize(
    ("conditional", "source", "expected"),
    [
        ((0.1, 0.2), (0.1, 0.2, 0.3), (0.1, 0.2)),
        ((0.1,), (0.1, 0.3), (0.1, 0.3)),
        ((), (), (0.1, 0.2, 0.3)),
    ],
)
def test_timing_fallback_precedence(
    conditional: tuple[float, ...], source: tuple[float, ...], expected: tuple[float, ...]
) -> None:
    """Changing fallback order would sample a different empirical holding-time distribution."""
    assert (
        markov_renewal.choose_holding_sample(
            conditional,
            source,
            (0.1, 0.2, 0.3),
            minimum_support=2,
        )
        == expected
    )


@pytest.mark.parametrize("alpha", [0.0, 2.0])
def test_fitted_diagnostics_classify_sparse_tiers_counts_and_unobserved_rows(alpha: float) -> None:
    """Both uniform-row formulas and every sparse timing path must remain explicit fitted evidence."""
    model = _two_state_model(alpha=alpha, minimum_support=2.0)

    assert model.timing_diagnostics.transition_tiers == (
        ("source", "source"),
        ("global", "global"),
    )
    assert model.timing_diagnostics.reference_transition_count == 0
    assert model.timing_diagnostics.reference_source_count == 1
    assert model.timing_diagnostics.reference_global_count == 0
    assert model.timing_diagnostics.unobserved_rows == (1,)


@pytest.mark.parametrize(
    ("conditional", "source", "global_iats", "minimum_support"),
    [
        ((math.nan,), (), (0.1,), 1),
        ((), (), (), 1),
        ((), (), (-0.1,), 1),
        ((), (), (0.1,), True),
        ((), (), (0.1,), 0),
    ],
)
def test_timing_fallback_rejects_invalid_samples_and_support(
    conditional: tuple[float, ...],
    source: tuple[float, ...],
    global_iats: tuple[float, ...],
    minimum_support: object,
) -> None:
    """Fallback must never return absent, invalid, or ambiguously supported timing data."""
    with pytest.raises(TrafficlabError):
        markov_renewal.choose_holding_sample(
            conditional,
            source,
            global_iats,
            minimum_support=minimum_support,  # type: ignore[arg-type]
        )


def test_public_transition_and_empirical_samplers_preserve_ties_and_order() -> None:
    """A cumulative tie belongs to the next interval, while numerical tails belong to the final interval."""
    tie_rng = ScriptedMarkovRng(random_values=[0.25], indices=[])
    tail_rng = ScriptedMarkovRng(random_values=[0.999999999999], indices=[])
    empirical_rng = ScriptedMarkovRng(random_values=[], indices=[1])
    assert markov_renewal.sample_transition((0.25, 0.75), tie_rng) == 1
    assert markov_renewal.sample_transition((0.25, 0.75), tail_rng) == 1
    assert markov_renewal.sample_empirical((10, 20), empirical_rng) == 20


def test_transition_sampler_compares_raw_draw_at_tie_for_tolerance_valid_nonunit_row() -> None:
    """Rescaling a transition draw by a near-one row sum would move an exact cumulative tie."""
    rng = ScriptedMarkovRng(random_values=[0.25], indices=[])

    assert markov_renewal.sample_transition((0.25, 0.7499999999995), rng) == 1


def test_transition_sampler_rejects_an_invalid_probability_row() -> None:
    """A direct transition sampler call must not bypass the fitted row invariant."""
    rng = ScriptedMarkovRng(random_values=[0.5], indices=[])

    with pytest.raises(TrafficlabError, match="probability row"):
        markov_renewal.sample_transition((), rng)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"direction": "outbound"},
        {"size_bin": 3},
        {"frame_lengths": ()},
        {"frame_lengths": (13,)},
        {"source_iats": (-0.1,)},
    ],
)
def test_markov_state_rejects_values_fit_cannot_produce(kwargs: dict[str, object]) -> None:
    """Direct construction must enforce the same state invariants as fit and loading."""
    values: dict[str, object] = {
        "direction": Direction.OUTBOUND,
        "size_bin": 0,
        "frame_lengths": (20,),
        "source_iats": (),
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError)):
        MarkovState(**values)  # type: ignore[arg-type]


def test_zero_iats_remain_valid_in_every_fitted_sample() -> None:
    """Rejecting simultaneous packet observations would lose valid canonical trace data."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(0.0, Direction.INBOUND, 80),
        TraceEvent(1.0, Direction.OUTBOUND, 20),
    )
    model = FAMILY.fit(reference, (0.25, 0.75, 0.0, 1.0, 1.0), W=1.0, bounds=BOUNDS)
    assert model.global_iats == (0.0, 1.0)
    assert model.conditional_iats[0][1] == (0.0,)


def test_every_fitted_transition_row_has_k_entries_and_probability_invariants() -> None:
    """A malformed row would make cumulative transition sampling ambiguous."""
    model = FAMILY.fit(DISTINCT_REFERENCE, (0.25, 0.75, 0.5, 1.0, 1.0), W=3.0, bounds=BOUNDS)
    state_count = len(model.states)
    assert state_count >= 1
    assert len(model.transition_rows) == state_count
    assert len(model.conditional_iats) == state_count
    for row in model.transition_rows:
        assert len(row) == state_count
        assert all(math.isfinite(value) and value >= 0.0 for value in row)
        assert math.isclose(sum(row), 1.0, rel_tol=0.0, abs_tol=1e-12)
    assert all(len(row) == state_count for row in model.conditional_iats)


def test_fitted_model_round_trips_the_exact_strict_json_layout() -> None:
    """Adding redundant counts or accepting loose JSON would make fitted artifacts noncanonical."""
    fitted = _two_state_model(alpha=0.0, minimum_support=2.0)
    payload = FAMILY.dump_fitted(fitted)
    assert payload == {
        "alpha": 0.0,
        "conditional_iats": [[[], [1.0]], [[], []]],
        "global_iats": [1.0],
        "minimum_support": 2,
        "states": [
            {"direction": "outbound", "frame_lengths": [20], "size_bin": 0, "source_iats": [1.0]},
            {"direction": "inbound", "frame_lengths": [80], "size_bin": 2, "source_iats": []},
        ],
        "thresholds": [35.0, 65.0],
        "time_scale": 1.0,
        "timing_diagnostics": {
            "reference_usage_counts": {"global": 0, "source": 1, "transition": 0},
            "transition_tiers": [["source", "source"], ["global", "global"]],
            "unobserved_rows": [1],
        },
        "transition_rows": [[0.0, 1.0], [0.5, 0.5]],
    }
    assert tuple(fitted.__dataclass_fields__) == (
        "alpha",
        "conditional_iats",
        "global_iats",
        "minimum_support",
        "states",
        "thresholds",
        "time_scale",
        "transition_rows",
        "timing_diagnostics",
    )
    assert fitted.family == "markov_renewal"
    assert FAMILY.load_fitted(payload, genes=(0.25, 0.75, 0.0, 2.0, 1.0), bounds=BOUNDS) == fitted


PAYLOAD_MUTATIONS: tuple[Callable[[dict[str, object]], object], ...] = (
    lambda payload: {**payload, "unknown": None},
    lambda payload: {key: value for key, value in payload.items() if key != "global_iats"},
    lambda payload: {**payload, "global_iats": []},
    lambda payload: {**payload, "global_iats": [-0.1]},
    lambda payload: {**payload, "global_iats": [math.inf]},
    lambda payload: {**payload, "transition_rows": [[0.0, 1.0]]},
    lambda payload: {**payload, "transition_rows": [[0.0, 1.0], [0.6, 0.5]]},
    lambda payload: {**payload, "conditional_iats": [[[], [1.0]]]},
    lambda payload: {key: value for key, value in payload.items() if key != "timing_diagnostics"},
    lambda payload: {
        **payload,
        "timing_diagnostics": {
            "reference_usage_counts": {"global": 1, "source": 0, "transition": 0},
            "transition_tiers": [["source", "source"], ["global", "global"]],
            "unobserved_rows": [1],
        },
    },
    lambda payload: {**payload, "timing_diagnostics": []},
    lambda payload: {
        **payload,
        "timing_diagnostics": {
            "reference_usage_counts": {"global": 0, "source": 1, "transition": 0},
            "transition_tiers": [["source", "source"], ["global", "global"]],
        },
    },
    lambda payload: {
        **payload,
        "timing_diagnostics": {
            "reference_usage_counts": [],
            "transition_tiers": [["source", "source"], ["global", "global"]],
            "unobserved_rows": [1],
        },
    },
    lambda payload: {
        **payload,
        "timing_diagnostics": {
            "reference_usage_counts": {"global": 0, "source": 1, "transition": 0, "extra": 0},
            "transition_tiers": [["source", "source"], ["global", "global"]],
            "unobserved_rows": [1],
        },
    },
    lambda payload: {
        **payload,
        "timing_diagnostics": {
            "reference_usage_counts": {"global": 0, "source": True, "transition": 0},
            "transition_tiers": [["source", "source"], ["global", "global"]],
            "unobserved_rows": [1],
        },
    },
    lambda payload: {
        **payload,
        "timing_diagnostics": {
            "reference_usage_counts": {"global": 0, "source": -1, "transition": 0},
            "transition_tiers": [["source", "source"], ["global", "global"]],
            "unobserved_rows": [1],
        },
    },
    lambda payload: {
        **payload,
        "timing_diagnostics": {
            "reference_usage_counts": {"global": 0, "source": 1, "transition": 0},
            "transition_tiers": (),
            "unobserved_rows": [1],
        },
    },
    lambda payload: {
        **payload,
        "timing_diagnostics": {
            "reference_usage_counts": {"global": 0, "source": 1, "transition": 0},
            "transition_tiers": [(), ["global", "global"]],
            "unobserved_rows": [1],
        },
    },
    lambda payload: {
        **payload,
        "timing_diagnostics": {
            "reference_usage_counts": {"global": 0, "source": 1, "transition": 0},
            "transition_tiers": [[None, "source"], ["global", "global"]],
            "unobserved_rows": [1],
        },
    },
    lambda payload: {
        **payload,
        "timing_diagnostics": {
            "reference_usage_counts": {"global": 0, "source": 1, "transition": 0},
            "transition_tiers": [["wrong", "source"], ["global", "global"]],
            "unobserved_rows": [1],
        },
    },
    lambda payload: {
        **payload,
        "timing_diagnostics": {
            "reference_usage_counts": {"global": 0, "source": 1, "transition": 0},
            "transition_tiers": [["source", "source"], ["global", "global"]],
            "unobserved_rows": (),
        },
    },
    lambda payload: {
        **payload,
        "timing_diagnostics": {
            "reference_usage_counts": {"global": 0, "source": 1, "transition": 0},
            "transition_tiers": [["source", "source"], ["global", "global"]],
            "unobserved_rows": [True],
        },
    },
    lambda payload: {
        **payload,
        "timing_diagnostics": {
            "reference_usage_counts": {"global": 0, "source": 1, "transition": 0},
            "transition_tiers": [["source", "source"], ["global", "global"]],
            "unobserved_rows": [-1],
        },
    },
)


@pytest.mark.parametrize(
    "mutation",
    PAYLOAD_MUTATIONS,
)
def test_load_rejects_missing_malformed_or_inconsistent_fitted_data(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    """Bypassing any stored invariant would admit a model fit could never produce."""
    payload = FAMILY.dump_fitted(_two_state_model())
    malformed = mutation(payload)
    with pytest.raises(TrafficlabError):
        FAMILY.load_fitted(malformed, genes=(0.25, 0.75, 0.0, 2.0, 1.0), bounds=BOUNDS)


def test_model_constructor_rejects_misaligned_source_and_global_iats() -> None:
    """Aligned matrices alone cannot prove stored fallback samples include every observed IAT."""
    fitted = _two_state_model()
    with pytest.raises(ValueError, match="global_iats"):
        replace(fitted, global_iats=(0.5,))
    bad_states = (replace(fitted.states[0], source_iats=(0.5,)), fitted.states[1])
    with pytest.raises(ValueError, match="source_iats"):
        replace(fitted, states=bad_states)


def test_model_constructor_rejects_every_structural_invariant() -> None:
    """Direct construction must reject any fitted state that fitting or strict loading cannot produce."""
    fitted = _two_state_model()
    duplicate_states = (fitted.states[0], replace(fitted.states[0], frame_lengths=(20,)))
    wrong_bin_states = (replace(fitted.states[0], size_bin=1), fitted.states[1])
    mutations: tuple[dict[str, object], ...] = (
        {"alpha": math.inf},
        {"minimum_support": True},
        {"time_scale": 0.0},
        {"thresholds": (65.0, 35.0)},
        {"states": ()},
        {"states": duplicate_states},
        {"states": wrong_bin_states},
        {"transition_rows": ((1.0,),)},
        {"transition_rows": ((0.0, 1.0), (0.6, 0.5))},
        {"conditional_iats": (((), (1.0,)),)},
        {"transition_rows": ((0.5, 0.5), (0.5, 0.5))},
    )
    for changes in mutations:
        with pytest.raises((TypeError, ValueError)):
            replace(fitted, **changes)  # type: ignore[arg-type]


def test_dump_revalidates_a_mutated_fitted_model() -> None:
    """Dumping must not trust a model whose frozen boundary was bypassed."""
    fitted = _two_state_model()
    object.__setattr__(fitted, "alpha", -1.0)
    with pytest.raises(TrafficlabError, match="fitted Markov renewal model"):
        FAMILY.dump_fitted(fitted)


def test_load_rejects_strict_nested_shape_and_scalar_violations() -> None:
    """Every persisted array and state object must retain its exact JSON shape and scalar types."""
    fitted = _two_state_model()
    base = FAMILY.dump_fitted(fitted)
    malformed = cast(
        tuple[object, ...],
        (
            object(),
            {**base, "thresholds": [35.0]},
            {**base, "states": []},
            {**base, "states": [None, base["states"]]},
            {**base, "states": [{"direction": "sideways"}]},
            {**base, "conditional_iats": "not-an-array"},
            {**base, "conditional_iats": [None, None]},
            {**base, "conditional_iats": [[[]], [[], []]]},
            {**base, "transition_rows": "not-an-array"},
            {**base, "transition_rows": [[0.0, 1.0]]},
            {**base, "transition_rows": [[0.0], [0.5, 0.5]]},
            {**base, "global_iats": "not-an-array"},
        ),
    )
    for payload in malformed:
        with pytest.raises(TrafficlabError):
            FAMILY.load_fitted(payload, genes=(0.25, 0.75, 0.0, 2.0, 1.0), bounds=BOUNDS)


def _payload_state(payload: dict[str, object], index: int) -> dict[str, object]:
    states = cast(list[object], payload["states"])
    return cast(dict[str, object], states[index])


def test_load_rejects_state_frame_count_exceeding_global_transition_count() -> None:
    """An extra persisted packet with no adjacent IAT cannot come from any fitted reference trace."""
    payload = FAMILY.dump_fitted(_two_state_model())
    _payload_state(payload, 0)["frame_lengths"] = [20, 20]
    payload["thresholds"] = [20.0, 50.0]

    with pytest.raises(TrafficlabError, match="packet count"):
        FAMILY.load_fitted(payload, genes=(0.25, 0.75, 0.0, 2.0, 1.0), bounds=BOUNDS)


def test_load_rejects_state_counts_inconsistent_with_transition_flow() -> None:
    """A preserved total still needs one explainable initial and final packet across state degrees."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(1.0, Direction.INBOUND, 20),
        TraceEvent(2.0, Direction.OUTBOUND, 20),
        TraceEvent(3.0, Direction.INBOUND, 80),
    )
    model = FAMILY.fit(reference, (0.25, 0.75, 0.0, 2.0, 1.0), W=3.0, bounds=BOUNDS)
    payload = FAMILY.dump_fitted(model)
    _payload_state(payload, 0)["frame_lengths"] = [20]
    _payload_state(payload, 1)["frame_lengths"] = [20, 20]

    with pytest.raises(TrafficlabError, match="transition flow"):
        FAMILY.load_fitted(payload, genes=(0.25, 0.75, 0.0, 2.0, 1.0), bounds=BOUNDS)


def test_load_rejects_degree_valid_disconnected_transition_components() -> None:
    """Balanced degrees in separate edge components cannot describe one observed reference trace."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(1.0, Direction.INBOUND, 20),
        TraceEvent(2.0, Direction.OUTBOUND, 20),
        TraceEvent(3.0, Direction.OUTBOUND, 80),
        TraceEvent(4.0, Direction.INBOUND, 80),
    )
    model = FAMILY.fit(reference, (0.25, 0.75, 0.0, 2.0, 1.0), W=4.0, bounds=BOUNDS)
    payload = FAMILY.dump_fitted(model)
    payload["conditional_iats"] = [
        [[], [1.0], [], []],
        [[1.0], [], [], []],
        [[], [], [], [1.0]],
        [[], [], [1.0], []],
    ]
    payload["transition_rows"] = [
        [0.0, 1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
        [0.0, 0.0, 1.0, 0.0],
    ]
    for state_index in range(4):
        _payload_state(payload, state_index)["source_iats"] = [1.0]

    with pytest.raises(TrafficlabError, match="connected"):
        FAMILY.load_fitted(payload, genes=(0.25, 0.75, 0.0, 2.0, 1.0), bounds=BOUNDS)


def test_empirical_flow_accepts_one_state_with_a_self_transition() -> None:
    """The one-state boundary is connected by definition when its observed flow is internally consistent."""
    state = MarkovState(Direction.OUTBOUND, 0, (20, 20), (1.0,))

    markov_renewal._validate_empirical_flow((state,), (((1.0,),),), (1.0,))


def test_load_rejects_thresholds_that_differ_from_outer_gene_type7_quantiles() -> None:
    """Thresholds that preserve every state bin can still be tampered away from the repaired q genes."""
    payload = FAMILY.dump_fitted(_two_state_model())
    payload["thresholds"] = [34.0, 66.0]

    with pytest.raises(TrafficlabError, match="threshold"):
        FAMILY.load_fitted(payload, genes=(0.25, 0.75, 0.0, 2.0, 1.0), bounds=BOUNDS)


def test_load_accepts_flow_when_the_same_state_is_initial_and_final() -> None:
    """One state may account for both missing incoming and missing outgoing transition observations."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(1.0, Direction.INBOUND, 80),
        TraceEvent(2.0, Direction.OUTBOUND, 20),
    )
    model = FAMILY.fit(reference, (0.25, 0.75, 0.0, 2.0, 1.0), W=2.0, bounds=BOUNDS)
    payload = FAMILY.dump_fitted(model)

    assert FAMILY.load_fitted(payload, genes=(0.25, 0.75, 0.0, 2.0, 1.0), bounds=BOUNDS) == model


@pytest.mark.parametrize(
    "genes",
    [
        (0.25, 0.75, 1.0, 2.0, 1.0),
        (0.25, 0.75, 0.0, 3.0, 1.0),
        (0.25, 0.75, 0.0, 2.0, 2.0),
        (True, 0.75, 0.0, 2.0, 1.0),
    ],
)
def test_load_revalidates_and_binds_repaired_outer_genes(genes: tuple[object, ...]) -> None:
    """Ignoring outer genes would decouple fitted parameters from the persisted chromosome."""
    payload = FAMILY.dump_fitted(_two_state_model())
    with pytest.raises(TrafficlabError):
        FAMILY.load_fitted(payload, genes=genes, bounds=BOUNDS)  # type: ignore[arg-type]


def test_generation_uses_exact_draw_order_and_emits_the_closed_endpoint() -> None:
    """Changing draw order or treating W as open would break reproducibility and boundary semantics."""
    model = _two_state_model(alpha=0.0, minimum_support=1.0)
    rng = ScriptedMarkovRng(random_values=[0.0, 0.9, 0.1], indices=[0, 0, 0, 0])
    result = markov_renewal._generate_with_rng(
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
        ("randrange", 1),
        ("random", None),
        ("randrange", 1),
        ("randrange", 1),
        ("random", None),
        ("randrange", 1),
    ]


def test_final_only_state_uses_uniform_row_and_global_iat() -> None:
    """A state with no outgoing observations must still transition and reach global timing fallback."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(1.0, Direction.INBOUND, 50),
        TraceEvent(3.0, Direction.INBOUND, 80),
    )
    model = FAMILY.fit(reference, (0.25, 0.75, 0.0, 2.0, 1.0), W=3.0, bounds=BOUNDS)
    rng = ScriptedMarkovRng(random_values=[0.9, 0.1], indices=[0, 0])
    result = markov_renewal._generate_with_rng(
        model,
        rng,
        W=0.5,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0] * 12),
    )
    assert result.require_complete() == (TraceEvent(0.0, Direction.INBOUND, 80),)
    assert rng.calls == [("random", None), ("randrange", 1), ("random", None), ("randrange", 2)]
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
    model = _two_state_model(alpha=0.0, minimum_support=minimum_support)
    result = markov_renewal._generate_with_rng(
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
    model = _two_state_model(alpha=0.0, minimum_support=1.0)
    rng = ScriptedMarkovRng(random_values=[0.0, 0.9], indices=[0, 0])
    result = markov_renewal._generate_with_rng(
        model,
        rng,
        W=0.5,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0] * 12),
    )
    assert result.require_complete() == (TraceEvent(0.0, Direction.OUTBOUND, 20),)
    assert rng.calls == [("random", None), ("randrange", 1), ("random", None), ("randrange", 1)]


def test_generation_allows_zero_iats_until_a_reliability_guard_stops_it() -> None:
    """Zero holding times are valid but must not permit unbounded packet generation."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 20),
        TraceEvent(0.0, Direction.INBOUND, 80),
        TraceEvent(1.0, Direction.OUTBOUND, 20),
    )
    model = FAMILY.fit(reference, (0.25, 0.75, 0.0, 1.0, 1.0), W=1.0, bounds=BOUNDS)
    rng = ScriptedMarkovRng(random_values=[0.0, 0.9, 0.1], indices=[0, 0, 0, 0, 0])
    result = markov_renewal._generate_with_rng(
        model,
        rng,
        W=1.0,
        limits=GenerationLimits(max_packets=3, max_output_bytes=100_000, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0] * 24),
    )
    assert tuple(event.timestamp for event in result.events) == (0.0, 0.0, 1.0)
    assert result.reason == "max_packets"


@pytest.mark.parametrize("draw", [math.nan, math.inf, -0.1, 1.0, 1])
def test_generation_rejects_invalid_continuous_rng_draws(draw: object) -> None:
    """A noncanonical uniform draw would make cumulative state selection ambiguous."""
    with pytest.raises(TrafficlabError, match="random draw"):
        markov_renewal._generate_with_rng(
            _two_state_model(),
            ScriptedMarkovRng(random_values=[draw], indices=[]),  # type: ignore[list-item]
            W=1.0,
            limits=LARGE_LIMITS,
            clock=ScriptedClock([0.0] * 4),
        )


@pytest.mark.parametrize("index", [-1, 1, True, 0.0])
def test_generation_rejects_invalid_integer_rng_draws(index: object) -> None:
    """Coercing an empirical index would select a sample outside the requested population."""
    with pytest.raises(TrafficlabError, match="random draw"):
        markov_renewal._generate_with_rng(
            _two_state_model(),
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
    result = markov_renewal._generate_with_rng(
        _two_state_model(),
        ScriptedMarkovRng(random_values=random_values, indices=indices),
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock(clock_values),
    )
    assert result.reason == "max_wall_seconds"


def test_generation_checks_wall_after_destination_frame_draw_and_before_emission() -> None:
    """An in-window frame drawn at the deadline must not be emitted."""
    result = markov_renewal._generate_with_rng(
        _two_state_model(alpha=0.0, minimum_support=1.0),
        ScriptedMarkovRng(random_values=[0.0, 0.9], indices=[0, 0, 0]),
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0] * 8 + [10.0]),
    )
    assert result.events == (TraceEvent(0.0, Direction.OUTBOUND, 20),)
    assert result.reason == "max_wall_seconds"


def test_generation_checks_prospective_packet_and_output_limits_before_emission() -> None:
    """Prospective limit checks must retain only the valid diagnostic prefix."""
    model = _two_state_model(alpha=0.0, minimum_support=1.0)
    packet_result = markov_renewal._generate_with_rng(
        model,
        ScriptedMarkovRng(random_values=[0.0], indices=[0]),
        W=1.0,
        limits=GenerationLimits(max_packets=1, max_output_bytes=100_000, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0] * 8),
    )
    byte_result = markov_renewal._generate_with_rng(
        model,
        ScriptedMarkovRng(random_values=[0.0], indices=[0]),
        W=1.0,
        limits=GenerationLimits(max_packets=100, max_output_bytes=19, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0] * 8),
    )
    assert packet_result.events == (TraceEvent(0.0, Direction.OUTBOUND, 20),)
    assert packet_result.reason == "max_packets"
    assert byte_result.events == ()
    assert byte_result.reason == "max_output_bytes"


def test_generation_checks_initial_wall_guard_and_later_prospective_output_limit() -> None:
    """Wall failure must precede the first draw, and later output checks must precede emission."""
    model = _two_state_model(alpha=0.0, minimum_support=1.0)
    initial_rng = ScriptedMarkovRng(random_values=[], indices=[])
    initial_result = markov_renewal._generate_with_rng(
        model,
        initial_rng,
        W=1.0,
        limits=LARGE_LIMITS,
        clock=ScriptedClock([0.0, 10.0]),
    )
    later_result = markov_renewal._generate_with_rng(
        model,
        ScriptedMarkovRng(random_values=[0.0, 0.9], indices=[0, 0, 0]),
        W=1.0,
        limits=GenerationLimits(max_packets=100, max_output_bytes=99, max_wall_seconds=10.0),
        clock=ScriptedClock([0.0] * 12),
    )
    assert initial_result.reason == "max_wall_seconds"
    assert initial_rng.calls == []
    assert later_result.events == (TraceEvent(0.0, Direction.OUTBOUND, 20),)
    assert later_result.reason == "max_output_bytes"


def test_generation_rejects_overflowed_scaled_arrival_time() -> None:
    """Treating arithmetic overflow as natural completion would hide structural corruption."""
    fitted = FAMILY.fit(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 20),
            TraceEvent(1e308, Direction.INBOUND, 80),
        ),
        (0.25, 0.75, 0.0, 1.0, 2.0),
        W=1e308,
        bounds=BOUNDS,
    )
    with pytest.raises(TrafficlabError, match="arrival time"):
        markov_renewal._generate_with_rng(
            fitted,
            ScriptedMarkovRng(random_values=[0.9, 0.1], indices=[0, 0]),
            W=sys.float_info.max,
            limits=LARGE_LIMITS,
            clock=ScriptedClock([0.0] * 12),
        )


@pytest.mark.parametrize("seed", [True, -1, 1.0])
def test_public_generate_requires_an_exact_nonnegative_integer_seed(seed: object) -> None:
    """Coercible seeds would weaken the public reproducibility contract."""
    with pytest.raises(TrafficlabError, match="seed"):
        FAMILY.generate(_two_state_model(), seed, 1.0, LARGE_LIMITS)  # type: ignore[arg-type]


def test_public_generation_is_seed_reproducible_and_does_not_change_global_rng() -> None:
    """Using module-global randomness would couple otherwise independent experiments."""
    random.seed(812)
    expected = random.random()
    random.seed(812)
    first = FAMILY.generate(_two_state_model(), 7, 1.0, LARGE_LIMITS)
    second = FAMILY.generate(_two_state_model(), 7, 1.0, LARGE_LIMITS)
    assert first == second
    assert random.random() == expected


@pytest.mark.parametrize("window", [0.0, -1.0, math.inf, math.nan, True])
def test_generation_rejects_invalid_windows(window: object) -> None:
    """A nonpositive or nonfinite window cannot define closed generation semantics."""
    with pytest.raises(TrafficlabError, match="observation window"):
        markov_renewal._generate_with_rng(
            _two_state_model(),
            ScriptedMarkovRng(random_values=[], indices=[]),
            W=window,  # type: ignore[arg-type]
            limits=LARGE_LIMITS,
        )


def test_generation_rejects_a_non_markov_model() -> None:
    """Interpreting another fitted family as Markov state would corrupt generation."""
    with pytest.raises(TypeError, match="MarkovRenewalModel"):
        markov_renewal._generate_with_rng(
            object(),  # type: ignore[arg-type]
            ScriptedMarkovRng(random_values=[], indices=[]),
            W=1.0,
            limits=LARGE_LIMITS,
        )
