"""Behavioral tests for one Markov renewal owner."""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

import trafficlab.generation.models.markov_renewal.model as markov_renewal
from tests.support.markov_renewal import (
    BOUNDS,
    FAMILY,
    LARGE_LIMITS,
    two_state_model,
)
from trafficlab.common.config import MarkovRenewalConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.markov_renewal import (
    MarkovState,
)

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


def test_fitted_model_round_trips_the_exact_strict_json_layout() -> None:
    """Adding redundant counts or accepting loose JSON would make fitted artifacts noncanonical."""
    fitted = two_state_model(alpha=0.0, minimum_support=2.0)
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


@pytest.mark.parametrize(
    "mutation",
    PAYLOAD_MUTATIONS,
)
def test_load_rejects_missing_malformed_or_inconsistent_fitted_data(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    """Bypassing any stored invariant would admit a model fit could never produce."""
    payload = FAMILY.dump_fitted(two_state_model())
    malformed = mutation(payload)
    with pytest.raises(TrafficlabError):
        FAMILY.load_fitted(malformed, genes=(0.25, 0.75, 0.0, 2.0, 1.0), bounds=BOUNDS)


def test_model_constructor_rejects_misaligned_source_and_global_iats() -> None:
    """Aligned matrices alone cannot prove stored fallback samples include every observed IAT."""
    fitted = two_state_model()
    with pytest.raises(ValueError, match="global_iats"):
        replace(fitted, global_iats=(0.5,))
    bad_states = (replace(fitted.states[0], source_iats=(0.5,)), fitted.states[1])
    with pytest.raises(ValueError, match="source_iats"):
        replace(fitted, states=bad_states)


def test_model_constructor_rejects_every_structural_invariant() -> None:
    """Direct construction must reject any fitted state that fitting or strict loading cannot produce."""
    fitted = two_state_model()
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
    fitted = two_state_model()
    object.__setattr__(fitted, "alpha", -1.0)
    with pytest.raises(TrafficlabError, match="fitted Markov renewal model"):
        FAMILY.dump_fitted(fitted)


def test_load_rejects_strict_nested_shape_and_scalar_violations() -> None:
    """Every persisted array and state object must retain its exact JSON shape and scalar types."""
    fitted = two_state_model()
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
    payload = FAMILY.dump_fitted(two_state_model())
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
    model = FAMILY.fit(TrafficTrace.from_events(reference), (0.25, 0.75, 0.0, 2.0, 1.0), W=3.0, bounds=BOUNDS)
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
    model = FAMILY.fit(TrafficTrace.from_events(reference), (0.25, 0.75, 0.0, 2.0, 1.0), W=4.0, bounds=BOUNDS)
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

    markov_renewal._validate_empirical_flow(  # pyright: ignore[reportPrivateUsage]
        (state,), (((1.0,),),), (1.0,)
    )


def test_load_rejects_thresholds_that_differ_from_outer_gene_type7_quantiles() -> None:
    """Thresholds that preserve every state bin can still be tampered away from the repaired q genes."""
    payload = FAMILY.dump_fitted(two_state_model())
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
    model = FAMILY.fit(TrafficTrace.from_events(reference), (0.25, 0.75, 0.0, 2.0, 1.0), W=2.0, bounds=BOUNDS)
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
    payload = FAMILY.dump_fitted(two_state_model())
    with pytest.raises(TrafficlabError):
        FAMILY.load_fitted(payload, genes=genes, bounds=BOUNDS)  # type: ignore[arg-type]


@pytest.mark.parametrize("seed", [True, -1, 1.0])
def test_public_generate_requires_an_exact_nonnegative_integer_seed(seed: object) -> None:
    """Coercible seeds would weaken the public reproducibility contract."""
    with pytest.raises(TrafficlabError, match="seed"):
        FAMILY.generate(two_state_model(), seed, 1.0, LARGE_LIMITS)  # type: ignore[arg-type]


def test_public_generation_is_seed_reproducible_and_does_not_change_global_rng() -> None:
    """Using module-global randomness would couple otherwise independent experiments."""
    random.seed(812)
    expected = random.random()
    random.seed(812)
    first = FAMILY.generate(two_state_model(), 7, 1.0, LARGE_LIMITS)
    second = FAMILY.generate(two_state_model(), 7, 1.0, LARGE_LIMITS)
    assert first == second
    assert random.random() == expected
