"""Family metadata, fitting, strict payload, and codec tests."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from tests.unit.generation.models.markov_packet_train._support import two_state_model
from trafficlab.common.config import IntegerBounds, MarkovPacketTrainConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.fitted_schema import MarkovPacketTrainPayload
from trafficlab.generation.models.markov_packet_train.family import MarkovPacketTrainFamily

FAMILY = MarkovPacketTrainFamily()
BOUNDS = MarkovPacketTrainConfig(length_cap=IntegerBounds(lower=3, upper=8))
REFERENCE = TrafficTrace.from_events(
    tuple(
        TraceEvent(timestamp, Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND, 60 + index)
        for index, timestamp in enumerate((0.0, 1.0, 2.0, 12.0, 13.0, 14.0, 15.0, 25.0, 26.0, 27.0, 28.0, 29.0, 30.0))
    )
)


def test_family_declares_single_integer_cap_and_fixed_estimators() -> None:
    """Changing fixed segmentation or smoothing policy must invalidate artifact metadata."""
    assert FAMILY.name == "markov_packet_train"
    assert FAMILY.gene_names == ("length_cap",)
    assert FAMILY.gene_coordinate_kinds == ("integer",)
    assert FAMILY.bounds_type is MarkovPacketTrainConfig
    assert FAMILY.estimator_choices == {
        "first_event": "zero",
        "gap_endpoint": "less_than_or_equal",
        "gap_quantile": "type7_linear_0.90",
        "inter_train_timing": "transition_source_global_nonempty",
        "marks": "state_position_joint_empirical_first_appearance",
        "state": "capped_actual_train_length",
        "state_order": "first_appearance",
        "transition": "additive_1_uniform_empty_row",
        "within_train_timing": "state_destination_position_empirical",
    }


def test_repair_clamps_exact_integer_length_cap() -> None:
    """A floating structural cap would make the state-matrix dimension ambiguous."""
    assert FAMILY.repair((2,), BOUNDS, REFERENCE) == (3,)
    assert FAMILY.repair((3,), BOUNDS, REFERENCE) == (3,)
    assert FAMILY.repair((99,), BOUNDS, REFERENCE) == (8,)


def test_dump_and_load_round_trip_without_whole_train_samples() -> None:
    """The strict payload must retain individual pools and reject a replay-template escape hatch."""
    model = two_state_model()
    payload = FAMILY.dump_fitted(model)

    assert FAMILY.load_fitted(payload, genes=(3,), bounds=BOUNDS) == model
    serialized_names = repr(payload)
    assert "template" not in serialized_names
    assert "train_samples" not in serialized_names
    assert set(payload) == {
        "conditional_inter_train_gaps",
        "gap_quantile",
        "gap_threshold",
        "global_inter_train_gaps",
        "initial_probabilities",
        "inside_train_endpoint",
        "length_cap",
        "states",
        "timing_diagnostics",
        "transition_pseudocount",
        "transition_rows",
    }


def test_family_fit_translates_a_reference_without_a_boundary_gap() -> None:
    """An unsegmentable reference is an actionable invalid candidate, not a leaked constructor error."""
    reference = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(1.0, Direction.INBOUND, 70),
        )
    )

    with pytest.raises(TrafficlabError, match="segmentation"):
        FAMILY.fit(reference, (3,), W=1.0, bounds=BOUNDS)


def test_dump_revalidates_a_finite_non_q90_direct_model() -> None:
    """Dump must not serialize a direct model whose finite threshold no longer matches its reservoirs."""
    model = copy.copy(two_state_model())
    object.__setattr__(model, "gap_threshold", 4.3)

    with pytest.raises(TrafficlabError, match="Type-7 q90"):
        FAMILY.dump_fitted(model)


@pytest.mark.parametrize(("field", "value"), (("gap_quantile", 0.8), ("transition_pseudocount", 2.0)))
def test_wire_payload_rejects_changed_fixed_packet_train_constants(field: str, value: float) -> None:
    """Pydantic publication must bind fixed segmentation and smoothing semantics without family loading."""
    payload = FAMILY.dump_fitted(two_state_model())
    payload[field] = value

    with pytest.raises(ValidationError, match=field):
        MarkovPacketTrainPayload.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("gap_quantile",), 0.8, "gap_quantile"),
        (("inside_train_endpoint",), "less_than", "endpoint"),
        (("length_cap",), 4, "length_cap"),
        (("transition_rows",), [[0.5, 0.5], [0.4, 0.6]], "transition"),
        (("states", 0, "actual_lengths"), [2], "actual"),
        (("timing_diagnostics", "reference_usage_counts", "global"), 99, "diagnostic"),
    ),
)
def test_loader_rejects_corrupted_or_outer_gene_inconsistent_payload(
    path: tuple[str | int, ...], value: object, message: str
) -> None:
    """Every redundant fitted field must be checked rather than trusted from JSON."""
    payload = copy.deepcopy(FAMILY.dump_fitted(two_state_model()))
    cursor: object = payload
    for component in path[:-1]:
        cursor = cursor[component]  # type: ignore[index]
    cursor[path[-1]] = value  # type: ignore[index]

    with pytest.raises(TrafficlabError, match=message):
        FAMILY.load_fitted(payload, genes=(3,), bounds=BOUNDS)


@pytest.mark.parametrize("genes", [(), (3, 4), (True,), (3.0,), (float("nan"),)])
def test_repair_rejects_noncanonical_length_cap_chromosomes(genes: tuple[object, ...]) -> None:
    """Only one exact numerical structural coordinate may enter deterministic repair."""
    with pytest.raises(TrafficlabError, match="length_cap"):
        FAMILY.repair(genes, BOUNDS, REFERENCE)  # type: ignore[arg-type]
