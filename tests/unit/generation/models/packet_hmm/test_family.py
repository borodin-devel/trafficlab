"""Packet-HMM family metadata, codec, and fit tests."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from tests.unit.generation.models.packet_hmm._support import two_state_model
from trafficlab.common.config import IntegerBounds, PacketHmmConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.fitted_schema import PacketHmmPayload
from trafficlab.generation.models.packet_hmm.family import PacketHmmFamily

FAMILY = PacketHmmFamily()
BOUNDS = PacketHmmConfig(state_count=IntegerBounds(lower=2, upper=4))
REFERENCE = TrafficTrace.from_events(
    (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(0.0, Direction.INBOUND, 70),
        TraceEvent(1.0, Direction.OUTBOUND, 90),
        TraceEvent(3.0, Direction.INBOUND, 120),
        TraceEvent(6.0, Direction.OUTBOUND, 150),
    )
)


def test_family_declares_integer_state_count_and_fixed_estimator_policy() -> None:
    """Changing EM, binning, or label policy must invalidate the outer artifact metadata."""
    assert FAMILY.name == "packet_hmm"
    assert FAMILY.gene_names == ("state_count",)
    assert FAMILY.gene_coordinate_kinds == ("integer",)
    assert FAMILY.bounds_type is PacketHmmConfig
    assert FAMILY.estimator_choices == {
        "em": "scaled_baum_welch_bounded_100_tolerance_1e-8",
        "emission": "observed_category_additive_0.001",
        "first_event": "zero_empirical_initial_mark",
        "iat_bins": "zero_plus_type7_terciles",
        "initialization": "fixed_cyclic_v1",
        "reservoirs": "individual_raw_category_members",
        "size_bins": "type7_terciles",
        "state_order": "expected_iat_then_emission_transition",
    }


def test_repair_clamps_exact_integer_state_count() -> None:
    """A floating or out-of-range latent dimension would make fitted matrices ambiguous."""
    assert FAMILY.repair((1,), BOUNDS, REFERENCE) == (2,)
    assert FAMILY.repair((3,), BOUNDS, REFERENCE) == (3,)
    assert FAMILY.repair((8,), BOUNDS, REFERENCE) == (4,)


def test_dump_load_round_trip_retains_estimators_and_individual_reservoirs() -> None:
    """Strict loading must retain all generation evidence without adding observation templates."""
    model = two_state_model()
    payload = FAMILY.dump_fitted(model)

    assert FAMILY.load_fitted(payload, genes=(2,), bounds=BOUNDS) == model
    assert set(payload) == {
        "additive_smoothing",
        "convergence_tolerance",
        "diagnostics",
        "emission_rows",
        "iat_quantiles",
        "iat_thresholds",
        "initial_marks",
        "initial_probabilities",
        "initialization",
        "maximum_iterations",
        "reservoirs",
        "size_quantiles",
        "size_thresholds",
        "state_count",
        "transition_rows",
        "vocabulary",
    }
    assert "sequence" not in repr(payload)
    assert "template" not in repr(payload)


def test_fit_is_repeatable_and_persists_non_decreasing_likelihoods() -> None:
    """Data-order randomness or an unchecked EM decrease would make equal candidates diverge."""
    first = FAMILY.fit(REFERENCE, (2,), W=6.0, bounds=BOUNDS)
    second = FAMILY.fit(REFERENCE, (2,), W=6.0, bounds=BOUNDS)

    assert first == second
    assert all(
        right + 1e-10 >= left
        for left, right in zip(first.diagnostics.log_likelihoods, first.diagnostics.log_likelihoods[1:], strict=False)
    )


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("state_count",), 3, "state_count"),
        (("additive_smoothing",), 0.01, "smoothing"),
        (("maximum_iterations",), 99, "maximum_iterations"),
        (("initialization",), "random", "initialization"),
        (("emission_rows", 0), [0.7, 0.2], "sum to one"),
        (("transition_rows", 0), [0.8], "K x K"),
        (("reservoirs", 0, 0, "iat"), 2.0, "IAT"),
        (("diagnostics", "log_likelihoods"), [-4.0, -4.1, -3.5], "nondecreasing"),
    ),
)
def test_loader_rejects_corrupt_and_outer_gene_inconsistent_payload(
    path: tuple[str | int, ...], value: object, message: str
) -> None:
    """Every redundant estimator, category, and matrix invariant must be rechecked after JSON decoding."""
    payload = copy.deepcopy(FAMILY.dump_fitted(two_state_model()))
    cursor: object = payload
    for component in path[:-1]:
        cursor = cursor[component]  # type: ignore[index]
    cursor[path[-1]] = value  # type: ignore[index]

    with pytest.raises(TrafficlabError, match=message):
        FAMILY.load_fitted(payload, genes=(2,), bounds=BOUNDS)


def test_wire_payload_rejects_changed_fixed_constants_before_family_load() -> None:
    """Publication schema must not accept a numerically valid but scientifically different estimator."""
    payload = FAMILY.dump_fitted(two_state_model())
    payload["additive_smoothing"] = 0.01

    with pytest.raises(ValidationError, match="additive_smoothing"):
        PacketHmmPayload.model_validate(payload)


@pytest.mark.parametrize("genes", [(), (2, 3), (True,), (2.0,), (float("nan"),)])
def test_repair_rejects_noncanonical_state_count_chromosomes(genes: tuple[object, ...]) -> None:
    """Repair accepts one exact integer coordinate and never rounds ambiguous numeric inputs."""
    with pytest.raises(TrafficlabError, match="state_count"):
        FAMILY.repair(genes, BOUNDS, REFERENCE)  # type: ignore[arg-type]
