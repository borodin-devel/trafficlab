"""Strict constructor, fitted-payload, and segmentation rejection tests."""

from __future__ import annotations

import copy
import math
from dataclasses import replace
from typing import Any, cast

import pytest

from tests.unit.generation.models.markov_packet_train._support import marks, two_state_model
from trafficlab.common.config import IntegerBounds, MarkovPacketTrainConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.generation.models.common import MarkCount, MarkDistribution
from trafficlab.generation.models.markov_packet_train.family import MarkovPacketTrainFamily
from trafficlab.generation.models.markov_packet_train.model import (
    MarkovPacketTrainModel,
    PositionMarkPools,
    TrainState,
    WithinGapPools,
    inter_train_gap_selection,
)
from trafficlab.generation.models.markov_packet_train.segmentation import PacketTrain, segment_trains

FAMILY = MarkovPacketTrainFamily()
BOUNDS = MarkovPacketTrainConfig(length_cap=IntegerBounds(lower=3, upper=8))


@pytest.mark.parametrize(
    ("factory", "message"),
    (
        (lambda: PacketTrain(True, 2), "integer"),
        (lambda: PacketTrain(2, 2), "nonempty"),
        (lambda: PositionMarkPools(first=cast(Any, None), interior=None, last=None), "first"),
        (lambda: PositionMarkPools(first=cast(Any, object()), interior=None, last=None), "MarkDistribution"),
        (lambda: WithinGapPools(interior=cast(Any, []), last=()), "tuple"),
        (lambda: WithinGapPools(interior=(math.nan,), last=()), "finite"),
        (lambda: WithinGapPools(interior=(), last=()).for_position("first"), "first packet"),
        (
            lambda: PositionMarkPools(first=marks(Direction.OUTBOUND, 60), interior=None, last=None).for_position(
                "last"
            ),
            "missing last",
        ),
        (
            lambda: TrainState(
                length_state=0,
                actual_lengths=(1,),
                marks=PositionMarkPools(first=marks(Direction.OUTBOUND, 60), interior=None, last=None),
                within_gaps=WithinGapPools(interior=(), last=()),
                source_inter_train_gaps=(),
            ),
            "length_state",
        ),
        (
            lambda: TrainState(
                length_state=1,
                actual_lengths=(),
                marks=PositionMarkPools(first=marks(Direction.OUTBOUND, 60), interior=None, last=None),
                within_gaps=WithinGapPools(interior=(), last=()),
                source_inter_train_gaps=(),
            ),
            "actual_lengths",
        ),
        (
            lambda: TrainState(
                length_state=1,
                actual_lengths=(1,),
                marks=cast(Any, object()),
                within_gaps=WithinGapPools(interior=(), last=()),
                source_inter_train_gaps=(),
            ),
            "PositionMarkPools",
        ),
        (
            lambda: TrainState(
                length_state=1,
                actual_lengths=(1,),
                marks=PositionMarkPools(first=marks(Direction.OUTBOUND, 60), interior=None, last=None),
                within_gaps=WithinGapPools(interior=(), last=()),
                source_inter_train_gaps=(cast(Any, 1),),
            ),
            "finite",
        ),
        (
            lambda: TrainState(
                length_state=1,
                actual_lengths=(1, 1),
                marks=PositionMarkPools(first=marks(Direction.OUTBOUND, 60), interior=None, last=None),
                within_gaps=WithinGapPools(interior=(), last=()),
                source_inter_train_gaps=(),
            ),
            "first mark count",
        ),
        (
            lambda: TrainState(
                length_state=2,
                actual_lengths=(2,),
                marks=PositionMarkPools(first=marks(Direction.OUTBOUND, 60), interior=None, last=None),
                within_gaps=WithinGapPools(interior=(), last=(1.0,)),
                source_inter_train_gaps=(),
            ),
            "last mark count",
        ),
        (
            lambda: TrainState(
                length_state=2,
                actual_lengths=(2,),
                marks=PositionMarkPools(
                    first=marks(Direction.OUTBOUND, 60),
                    interior=None,
                    last=marks(Direction.INBOUND, 70),
                ),
                within_gaps=WithinGapPools(interior=(), last=()),
                source_inter_train_gaps=(),
            ),
            "gap counts",
        ),
    ),
)
def test_value_objects_reject_malformed_or_count_inconsistent_state(factory: object, message: str) -> None:
    """Each case removes one invariant required for safe reservoir selection."""
    with pytest.raises((TypeError, ValueError), match=message):
        cast(Any, factory)()


def test_model_rejects_disconnected_transition_evidence() -> None:
    """A self-loop cannot consume a state observed only once while another active state is isolated."""
    model = two_state_model()
    state1, state3 = model.states
    disconnected = (
        ((5.0,), ()),
        ((), ()),
    )

    with pytest.raises(ValueError, match="connected"):
        MarkovPacketTrainModel(
            conditional_inter_train_gaps=disconnected,
            gap_quantile=0.9,
            gap_threshold=4.4,
            global_inter_train_gaps=(5.0,),
            initial_probabilities=(0.5, 0.5),
            inside_train_endpoint="less_than_or_equal",
            length_cap=3,
            states=(replace(state1, source_inter_train_gaps=(5.0,)), state3),
            transition_pseudocount=1.0,
            transition_rows=((2.0 / 3.0, 1.0 / 3.0), (0.5, 0.5)),
        )


def test_model_rejects_corrupted_shapes_probabilities_counts_and_gap_membership() -> None:
    """Every redundant state-table view is validated against literal empirical identities."""
    model = two_state_model()
    state1, state3 = model.states
    duplicate_state = replace(state3, length_state=1)
    mismatched_state = replace(state3, length_state=2)
    high_within = replace(state3, within_gaps=WithinGapPools(interior=(4.5,), last=(2.0,)))
    extra_first_marks = MarkDistribution((MarkCount(Direction.OUTBOUND, 60, 2),))
    extra_state1 = TrainState(
        length_state=1,
        actual_lengths=(1, 1),
        marks=PositionMarkPools(first=extra_first_marks, interior=None, last=None),
        within_gaps=WithinGapPools(interior=(), last=()),
        source_inter_train_gaps=(5.0,),
    )
    cases: tuple[tuple[dict[str, object], str], ...] = (
        ({"gap_quantile": 0.8}, "gap_quantile"),
        ({"gap_threshold": math.nan}, "gap_threshold"),
        ({"inside_train_endpoint": cast(Any, "less_than")}, "endpoint"),
        ({"length_cap": 2}, "length_cap"),
        ({"transition_pseudocount": 0.5}, "pseudocount"),
        ({"states": cast(Any, ())}, "states"),
        ({"states": (state1, duplicate_state)}, "unique"),
        ({"states": (state1, mismatched_state)}, "actual lengths"),
        ({"states": (state1, high_within)}, "within-train"),
        ({"initial_probabilities": (1.0,)}, "initial_probabilities"),
        ({"initial_probabilities": (math.nan, 0.5)}, "initial probabilities"),
        ({"initial_probabilities": (0.4, 0.4)}, "sum to one"),
        ({"initial_probabilities": (0.25, 0.75)}, "occupancy"),
        ({"conditional_inter_train_gaps": cast(Any, ((),))}, "K rows"),
        ({"conditional_inter_train_gaps": cast(Any, (((),), ((), ())))}, "K x K"),
        ({"conditional_inter_train_gaps": (((4.0,), ()), ((), ()))}, "strictly above"),
        ({"global_inter_train_gaps": ()}, "nonempty"),
        ({"global_inter_train_gaps": (6.0,)}, "every conditional"),
        ({"global_inter_train_gaps": (4.0,)}, "every conditional|strictly above"),
        ({"states": (replace(state1, source_inter_train_gaps=(6.0,)), state3)}, "source inter-train"),
        (
            {
                "states": (extra_state1, state3),
                "initial_probabilities": (2.0 / 3.0, 1.0 / 3.0),
            },
            "train count",
        ),
        ({"transition_rows": ((1.0,), (0.5, 0.5))}, "K rows|sum to one"),
        ({"transition_rows": ((math.nan, 0.0), (0.5, 0.5))}, "finite"),
        ({"transition_rows": ((0.5, 0.5), (0.5, 0.5))}, "additive"),
    )
    for changes, message in cases:
        with pytest.raises((TypeError, ValueError), match=message):
            replace(model, **changes)  # type: ignore[arg-type]


def test_direct_model_construction_rejects_a_finite_non_q90_threshold() -> None:
    """A finite threshold still changes segmentation semantics unless it equals the stored gap multiset's q90."""
    with pytest.raises(ValueError, match="Type-7 q90"):
        replace(two_state_model(), gap_threshold=4.3)


def test_direct_model_accepts_only_the_documented_absolute_q90_tolerance() -> None:
    """Serialization roundoff within 1e-12 is accepted without allowing a relative threshold drift."""
    model = two_state_model()
    assert replace(model, gap_threshold=model.gap_threshold + 5e-13).gap_threshold == pytest.approx(4.4)
    with pytest.raises(ValueError, match="Type-7 q90"):
        replace(model, gap_threshold=model.gap_threshold + 2e-12)


@pytest.mark.parametrize(
    ("conditional", "source", "global_gaps"),
    (
        (cast(Any, []), (), (5.0,)),
        ((), (math.nan,), (5.0,)),
        ((), (), ()),
    ),
)
def test_inter_train_fallback_rejects_malformed_or_missing_gap_evidence(
    conditional: object, source: object, global_gaps: object
) -> None:
    """Fallback cannot manufacture a gap when every eligible empirical pool is invalid or empty."""
    with pytest.raises(ValueError, match="gap|tuple"):
        inter_train_gap_selection(conditional, source, global_gaps)  # type: ignore[arg-type]


def test_loader_rejects_malformed_nested_payload_shapes_and_scalars() -> None:
    """Wire arrays and objects retain exact dimensions and JSON scalar types."""
    original = FAMILY.dump_fitted(two_state_model())
    mutations: tuple[tuple[tuple[str | int, ...], object], ...] = (
        (("transition_pseudocount",), 2.0),
        (("gap_threshold",), 4.3),
        (("states",), []),
        (("states", 0), None),
        (("states", 0, "length_state"), 1.0),
        (("states", 0, "actual_lengths"), "1"),
        (("states", 0, "marks"), []),
        (("states", 0, "within_gaps"), []),
        (("states", 0, "marks", "first"), []),
        (("states", 0, "marks", "first", 0), None),
        (("states", 0, "marks", "first", 0, "count"), 0),
        (("states", 0, "source_inter_train_gaps"), [5]),
        (("conditional_inter_train_gaps",), None),
        (("conditional_inter_train_gaps",), cast(object, [[]])),
        (("conditional_inter_train_gaps", 0), None),
        (("conditional_inter_train_gaps", 0), cast(object, [[]])),
        (("conditional_inter_train_gaps", 0, 0), None),
        (("transition_rows",), None),
        (("transition_rows",), cast(object, [[]])),
        (("transition_rows", 0), None),
        (("transition_rows", 0), [1.0]),
    )
    for path, value in mutations:
        payload = copy.deepcopy(original)
        cursor: object = payload
        for component in path[:-1]:
            cursor = cursor[component]  # type: ignore[index]
        cursor[path[-1]] = value  # type: ignore[index]
        with pytest.raises(TrafficlabError, match="fitted payload|marks"):
            FAMILY.load_fitted(payload, genes=(3,), bounds=BOUNDS)

    with pytest.raises(TrafficlabError, match="fitted payload"):
        FAMILY.load_fitted(None, genes=(3,), bounds=BOUNDS)


def test_family_rejects_wrong_or_constructed_invalid_bounds() -> None:
    """Pydantic construction bypass cannot weaken the integer cap domain."""
    with pytest.raises(TrafficlabError, match="bounds"):
        FAMILY.repair((3,), cast(Any, object()), cast(Any, object()))
    invalid = MarkovPacketTrainConfig.model_construct(length_cap=IntegerBounds.model_construct(lower=2, upper=8))
    with pytest.raises(TrafficlabError, match="length_cap bounds"):
        FAMILY.repair((3,), invalid, cast(Any, object()))


def test_segmentation_rejects_nontrace_and_invalid_thresholds() -> None:
    """Invalid threshold types cannot silently change the strict split endpoint."""
    with pytest.raises(ValueError, match="TrafficTrace"):
        segment_trains(cast(Any, object()), 1.0)
    trace = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(1.0, Direction.INBOUND, 70),
        )
    )
    for threshold in (cast(Any, 1), math.nan, -1.0):
        with pytest.raises(ValueError, match="threshold"):
            segment_trains(trace, threshold)
