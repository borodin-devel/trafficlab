"""Literal packet-train models used by focused behavioral tests."""

from __future__ import annotations

from collections.abc import Sequence

from trafficlab.common.trace import Direction
from trafficlab.generation.models.common import MarkCount, MarkDistribution
from trafficlab.generation.models.markov_packet_train.model import (
    MarkovPacketTrainModel,
    PositionMarkPools,
    TrainState,
    WithinGapPools,
)


def marks(direction: Direction, frame_length: int) -> MarkDistribution:
    return MarkDistribution((MarkCount(direction, frame_length, 1),))


def two_state_model() -> MarkovPacketTrainModel:
    """Return empirical evidence for state sequence 1 -> 3."""
    state1 = TrainState(
        length_state=1,
        actual_lengths=(1,),
        marks=PositionMarkPools(
            first=marks(Direction.OUTBOUND, 60),
            interior=None,
            last=None,
        ),
        within_gaps=WithinGapPools(interior=(), last=()),
        source_inter_train_gaps=(5.0,),
    )
    state3 = TrainState(
        length_state=3,
        actual_lengths=(3,),
        marks=PositionMarkPools(
            first=marks(Direction.INBOUND, 70),
            interior=marks(Direction.OUTBOUND, 80),
            last=marks(Direction.INBOUND, 90),
        ),
        within_gaps=WithinGapPools(interior=(1.0,), last=(2.0,)),
        source_inter_train_gaps=(),
    )
    return MarkovPacketTrainModel(
        conditional_inter_train_gaps=(((), (5.0,)), ((), ())),
        gap_quantile=0.9,
        gap_threshold=4.4,
        global_inter_train_gaps=(5.0,),
        initial_probabilities=(0.5, 0.5),
        inside_train_endpoint="less_than_or_equal",
        length_cap=3,
        states=(state1, state3),
        transition_pseudocount=1.0,
        transition_rows=((1.0 / 3.0, 2.0 / 3.0), (0.5, 0.5)),
    )


class ScriptedTrainRng:
    """Record exact scalar random and empirical calls."""

    def __init__(self, *, randoms: Sequence[float], choices: Sequence[int]) -> None:
        self._randoms = iter(randoms)
        self._choices = iter(choices)
        self.calls: list[tuple[str, int | None]] = []

    def random(self) -> float:
        self.calls.append(("random", None))
        return next(self._randoms)

    def choice(self, a: int) -> int:
        self.calls.append(("choice", a))
        return next(self._choices)
