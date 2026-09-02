"""Fitted state and empirical reservoirs for Markov packet trains."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal, cast

from trafficlab.common.config import FamilyName
from trafficlab.common.trace import TrafficTrace
from trafficlab.generation.models.common import MarkDistribution
from trafficlab.generation.models.markov_packet_train.segmentation import PacketPosition, position_class, segment_trains
from trafficlab.generation.models.markov_renewal.parameters import ROW_TOLERANCE, type7_quantile

GAP_QUANTILE = 0.9
INSIDE_TRAIN_ENDPOINT: Literal["less_than_or_equal"] = "less_than_or_equal"
TRANSITION_PSEUDOCOUNT = 1.0
GAP_THRESHOLD_TOLERANCE = ROW_TOLERANCE
type GapTimingTier = Literal["transition", "source", "global"]


def _validate_gaps(values: object, *, allow_empty: bool, context: str) -> tuple[float, ...]:
    if type(values) is not tuple or (not allow_empty and not values):
        raise ValueError(f"{context} must be {'a' if allow_empty else 'a nonempty'} tuple")
    items = cast(tuple[object, ...], values)
    if any(type(value) is not float or not math.isfinite(value) or value < 0.0 for value in items):
        raise ValueError(f"{context} must contain finite nonnegative exact floats")
    return cast(tuple[float, ...], items)


def _validate_distribution(value: object, *, required: bool, context: str) -> MarkDistribution | None:
    if value is None:
        if required:
            raise ValueError(f"{context} mark distribution must not be empty")
        return None
    if type(value) is not MarkDistribution:
        raise TypeError(f"{context} marks must be a MarkDistribution or None")
    return MarkDistribution(value.entries)


@dataclass(frozen=True, slots=True)
class PositionMarkPools:
    """Individual joint-mark distributions for disjoint train positions."""

    first: MarkDistribution
    interior: MarkDistribution | None
    last: MarkDistribution | None

    def __post_init__(self) -> None:
        _validate_distribution(self.first, required=True, context="first")
        _validate_distribution(self.interior, required=False, context="interior")
        _validate_distribution(self.last, required=False, context="last")

    def for_position(self, position: PacketPosition) -> MarkDistribution:
        value = self.first if position == "first" else self.interior if position == "interior" else self.last
        if value is None:
            raise ValueError(f"missing {position} mark distribution")
        return value


@dataclass(frozen=True, slots=True)
class WithinGapPools:
    """Within-train gaps keyed by the destination packet position."""

    interior: tuple[float, ...]
    last: tuple[float, ...]

    def __post_init__(self) -> None:
        _validate_gaps(self.interior, allow_empty=True, context="interior within-train gaps")
        _validate_gaps(self.last, allow_empty=True, context="last within-train gaps")

    def for_position(self, position: PacketPosition) -> tuple[float, ...]:
        if position == "first":
            raise ValueError("a first packet has no within-train gap")
        return self.interior if position == "interior" else self.last


@dataclass(frozen=True, slots=True)
class TrainState:
    """One active capped-length state with only individual empirical reservoirs."""

    length_state: int
    actual_lengths: tuple[int, ...]
    marks: PositionMarkPools
    within_gaps: WithinGapPools
    source_inter_train_gaps: tuple[float, ...]

    def __post_init__(self) -> None:
        if type(self.length_state) is not int or not 1 <= self.length_state <= 8:
            raise ValueError("length_state must be an exact integer in 1..8")
        if (
            type(self.actual_lengths) is not tuple
            or not self.actual_lengths
            or any(type(length) is not int or length <= 0 for length in self.actual_lengths)
        ):
            raise ValueError("actual_lengths must be a nonempty tuple of positive exact integers")
        if type(self.marks) is not PositionMarkPools or type(self.within_gaps) is not WithinGapPools:
            raise TypeError("train state requires PositionMarkPools and WithinGapPools")
        _validate_gaps(
            self.source_inter_train_gaps,
            allow_empty=True,
            context="source inter-train gaps",
        )
        train_count = len(self.actual_lengths)
        last_count = sum(length >= 2 for length in self.actual_lengths)
        interior_count = sum(max(length - 2, 0) for length in self.actual_lengths)
        if self.marks.first.total_count != train_count:
            raise ValueError("first mark count must equal the train count")
        for name, distribution, expected in (
            ("interior", self.marks.interior, interior_count),
            ("last", self.marks.last, last_count),
        ):
            actual = 0 if distribution is None else distribution.total_count
            if actual != expected:
                raise ValueError(f"{name} mark count must match actual train lengths")
        if len(self.within_gaps.interior) != interior_count or len(self.within_gaps.last) != last_count:
            raise ValueError("within-train gap counts must match actual train lengths")


@dataclass(frozen=True, slots=True)
class TrainTimingDiagnostics:
    """Frozen sparse inter-train timing provenance."""

    transition_tiers: tuple[tuple[GapTimingTier, ...], ...]
    reference_transition_count: int
    reference_source_count: int
    reference_global_count: int
    unobserved_rows: tuple[int, ...]


def inter_train_gap_selection(
    conditional: tuple[float, ...],
    source: tuple[float, ...],
    global_gaps: tuple[float, ...],
) -> tuple[GapTimingTier, tuple[float, ...]]:
    """Choose the strongest nonempty inter-train gap reservoir."""
    checked_conditional = _validate_gaps(conditional, allow_empty=True, context="conditional inter-train gaps")
    checked_source = _validate_gaps(source, allow_empty=True, context="source inter-train gaps")
    checked_global = _validate_gaps(global_gaps, allow_empty=False, context="global inter-train gaps")
    if checked_conditional:
        return ("transition", checked_conditional)
    if checked_source:
        return ("source", checked_source)
    return ("global", checked_global)


def _timing_diagnostics(
    conditional: tuple[tuple[tuple[float, ...], ...], ...],
    states: tuple[TrainState, ...],
    global_gaps: tuple[float, ...],
) -> TrainTimingDiagnostics:
    rows: list[tuple[GapTimingTier, ...]] = []
    counts: dict[GapTimingTier, int] = {"transition": 0, "source": 0, "global": 0}
    for state, samples in zip(states, conditional, strict=True):
        tiers: list[GapTimingTier] = []
        for sample in samples:
            tier = inter_train_gap_selection(sample, state.source_inter_train_gaps, global_gaps)[0]
            tiers.append(tier)
            counts[tier] += len(sample)
        rows.append(tuple(tiers))
    return TrainTimingDiagnostics(
        transition_tiers=tuple(rows),
        reference_transition_count=counts["transition"],
        reference_source_count=counts["source"],
        reference_global_count=counts["global"],
        unobserved_rows=tuple(index for index, state in enumerate(states) if not state.source_inter_train_gaps),
    )


@dataclass(frozen=True, slots=True)
class MarkovPacketTrainModel:
    """Complete capped train-state kernel and individual empirical packet pools."""

    conditional_inter_train_gaps: tuple[tuple[tuple[float, ...], ...], ...]
    gap_quantile: float
    gap_threshold: float
    global_inter_train_gaps: tuple[float, ...]
    initial_probabilities: tuple[float, ...]
    inside_train_endpoint: Literal["less_than_or_equal"]
    length_cap: int
    states: tuple[TrainState, ...]
    transition_pseudocount: float
    transition_rows: tuple[tuple[float, ...], ...]
    timing_diagnostics: TrainTimingDiagnostics = field(init=False)

    def __post_init__(self) -> None:
        if type(self.gap_quantile) is not float or self.gap_quantile != GAP_QUANTILE:
            raise ValueError("gap_quantile must equal the fixed Type-7 level 0.9")
        if type(self.gap_threshold) is not float or not math.isfinite(self.gap_threshold) or self.gap_threshold < 0.0:
            raise ValueError("gap_threshold must be a finite nonnegative exact float")
        if self.inside_train_endpoint != INSIDE_TRAIN_ENDPOINT:
            raise ValueError("inside-train endpoint must be less_than_or_equal")
        if type(self.length_cap) is not int or not 3 <= self.length_cap <= 8:
            raise ValueError("length_cap must be an exact integer in 3..8")
        if type(self.transition_pseudocount) is not float or self.transition_pseudocount != TRANSITION_PSEUDOCOUNT:
            raise ValueError("transition_pseudocount must equal the fixed additive value 1.0")
        if (
            type(self.states) is not tuple
            or not self.states
            or any(type(state) is not TrainState for state in self.states)
        ):
            raise ValueError("states must be a nonempty tuple of TrainState values")
        if len({state.length_state for state in self.states}) != len(self.states):
            raise ValueError("train states must have unique capped lengths")
        for state in self.states:
            if state.length_state > self.length_cap or any(
                min(length, self.length_cap) != state.length_state for length in state.actual_lengths
            ):
                raise ValueError("actual lengths must belong to their capped state")
            if any(gap > self.gap_threshold for gap in (*state.within_gaps.interior, *state.within_gaps.last)):
                raise ValueError("within-train gaps must be at or below the threshold")
        state_count = len(self.states)
        if type(self.initial_probabilities) is not tuple or len(self.initial_probabilities) != state_count:
            raise ValueError("initial_probabilities must contain one value per active state")
        if any(
            type(value) is not float or not math.isfinite(value) or value < 0.0 for value in self.initial_probabilities
        ) or not math.isclose(sum(self.initial_probabilities), 1.0, rel_tol=0.0, abs_tol=ROW_TOLERANCE):
            raise ValueError("initial probabilities must be finite, nonnegative, and sum to one")
        train_counts = tuple(len(state.actual_lengths) for state in self.states)
        total_trains = sum(train_counts)
        expected_initial = tuple(count / total_trains for count in train_counts)
        if any(
            not math.isclose(actual, expected, rel_tol=0.0, abs_tol=ROW_TOLERANCE)
            for actual, expected in zip(self.initial_probabilities, expected_initial, strict=True)
        ):
            raise ValueError("initial probabilities must equal empirical train-state occupancy")
        if (
            type(self.conditional_inter_train_gaps) is not tuple
            or len(self.conditional_inter_train_gaps) != state_count
        ):
            raise ValueError("conditional inter-train gaps must contain K rows")
        for row in self.conditional_inter_train_gaps:
            if type(row) is not tuple or len(row) != state_count:
                raise ValueError("conditional inter-train gaps must be a K x K tuple")
            for sample in row:
                _validate_gaps(sample, allow_empty=True, context="conditional inter-train gaps")
                if any(gap <= self.gap_threshold for gap in sample):
                    raise ValueError("inter-train gaps must be strictly above the threshold")
        _validate_gaps(self.global_inter_train_gaps, allow_empty=False, context="global inter-train gaps")
        flattened = tuple(gap for row in self.conditional_inter_train_gaps for sample in row for gap in sample)
        if Counter(flattened) != Counter(self.global_inter_train_gaps):
            raise ValueError("global inter-train gaps must contain every conditional gap")
        if any(gap <= self.gap_threshold for gap in self.global_inter_train_gaps):
            raise ValueError("global inter-train gaps must be strictly above the threshold")
        all_reference_gaps = (
            tuple(gap for state in self.states for gap in (*state.within_gaps.interior, *state.within_gaps.last))
            + self.global_inter_train_gaps
        )
        expected_threshold = type7_quantile(all_reference_gaps, GAP_QUANTILE)
        if not math.isclose(
            self.gap_threshold,
            expected_threshold,
            rel_tol=0.0,
            abs_tol=GAP_THRESHOLD_TOLERANCE,
        ):
            raise ValueError("gap_threshold must equal the stored reference gaps' Type-7 q90")
        for state, row in zip(self.states, self.conditional_inter_train_gaps, strict=True):
            if Counter(gap for sample in row for gap in sample) != Counter(state.source_inter_train_gaps):
                raise ValueError("source inter-train gaps must contain every gap leaving its state")
        if sum(train_counts) != len(self.global_inter_train_gaps) + 1:
            raise ValueError("train count must equal inter-train gap count plus one")
        outgoing = tuple(sum(len(sample) for sample in row) for row in self.conditional_inter_train_gaps)
        incoming = tuple(
            sum(len(self.conditional_inter_train_gaps[source][destination]) for source in range(state_count))
            for destination in range(state_count)
        )
        if (
            any(count not in (trains, trains - 1) for count, trains in zip(outgoing, train_counts, strict=True))
            or any(count not in (trains, trains - 1) for count, trains in zip(incoming, train_counts, strict=True))
            or sum(trains - count for count, trains in zip(outgoing, train_counts, strict=True)) != 1
            or sum(trains - count for count, trains in zip(incoming, train_counts, strict=True)) != 1
        ):
            raise ValueError("train-state counts must match one initial and one final train")
        adjacency = [set[int]() for _ in self.states]
        for source, row in enumerate(self.conditional_inter_train_gaps):
            for destination, sample in enumerate(row):
                if sample:
                    adjacency[source].add(destination)
                    adjacency[destination].add(source)
        visited = {0}
        frontier = [0]
        while frontier:
            current = frontier.pop()
            for neighbor in adjacency[current] - visited:
                visited.add(neighbor)
                frontier.append(neighbor)
        if len(visited) != state_count:
            raise ValueError("active train states must form one connected transition component")
        if type(self.transition_rows) is not tuple or len(self.transition_rows) != state_count:
            raise ValueError("transition_rows must contain K rows")
        for probabilities, sample_row in zip(self.transition_rows, self.conditional_inter_train_gaps, strict=True):
            if (
                type(probabilities) is not tuple
                or len(probabilities) != state_count
                or any(type(value) is not float or not math.isfinite(value) or value < 0.0 for value in probabilities)
                or not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=ROW_TOLERANCE)
            ):
                raise ValueError("transition rows must be finite, nonnegative, and sum to one")
            denominator = sum(len(sample) for sample in sample_row) + self.transition_pseudocount * state_count
            expected = tuple((len(sample) + self.transition_pseudocount) / denominator for sample in sample_row)
            if any(
                not math.isclose(actual, wanted, rel_tol=0.0, abs_tol=ROW_TOLERANCE)
                for actual, wanted in zip(probabilities, expected, strict=True)
            ):
                raise ValueError("transition rows must match the fixed additive estimator")
        object.__setattr__(
            self,
            "timing_diagnostics",
            _timing_diagnostics(self.conditional_inter_train_gaps, self.states, self.global_inter_train_gaps),
        )

    @property
    def family(self) -> FamilyName:
        return "markov_packet_train"


def _distribution(events: list[object]) -> MarkDistribution | None:
    if not events:
        return None
    return MarkDistribution.from_reference(events)  # type: ignore[arg-type]


def fit_trace(trace: TrafficTrace, *, length_cap: int) -> MarkovPacketTrainModel:
    """Fit capped train states and individual packet/gap reservoirs in one pass."""
    if type(trace) is not TrafficTrace or len(trace) < 2:
        raise ValueError("packet-train fitting requires at least two canonical events")
    if type(length_cap) is not int or not 3 <= length_cap <= 8:
        raise ValueError("length_cap must be an exact integer in 3..8")
    threshold = type7_quantile(tuple(float(value) for value in trace.iats()), GAP_QUANTILE)
    trains = segment_trains(trace, threshold)
    events = trace.to_events()
    state_values = tuple(min(train.length, length_cap) for train in trains)
    state_order = tuple(dict.fromkeys(state_values))
    state_index = {state: index for index, state in enumerate(state_order)}
    train_state_indices = tuple(state_index[state] for state in state_values)
    state_count = len(state_order)
    actual_lengths: dict[int, list[int]] = {state: [] for state in state_order}
    mark_events: dict[tuple[int, PacketPosition], list[object]] = {
        (state, position): [] for state in state_order for position in ("first", "interior", "last")
    }
    within_gaps: dict[tuple[int, PacketPosition], list[float]] = {
        (state, position): [] for state in state_order for position in ("first", "interior", "last")
    }
    for train, state in zip(trains, state_values, strict=True):
        actual_lengths[state].append(train.length)
        for offset, event_index in enumerate(range(train.start, train.stop)):
            position = position_class(offset, train.length)
            mark_events[state, position].append(events[event_index])
            if offset > 0:
                within_gaps[state, position].append(events[event_index].timestamp - events[event_index - 1].timestamp)

    conditional: list[list[list[float]]] = [[[] for _ in range(state_count)] for _ in range(state_count)]
    for index, (source, destination) in enumerate(zip(train_state_indices, train_state_indices[1:], strict=False)):
        gap = events[trains[index + 1].start].timestamp - events[trains[index].stop - 1].timestamp
        conditional[source][destination].append(gap)
    conditional_tuple = tuple(tuple(tuple(sample) for sample in row) for row in conditional)
    states = tuple(
        TrainState(
            length_state=state,
            actual_lengths=tuple(actual_lengths[state]),
            marks=PositionMarkPools(
                first=cast(MarkDistribution, _distribution(mark_events[state, "first"])),
                interior=_distribution(mark_events[state, "interior"]),
                last=_distribution(mark_events[state, "last"]),
            ),
            within_gaps=WithinGapPools(
                interior=tuple(within_gaps[state, "interior"]),
                last=tuple(within_gaps[state, "last"]),
            ),
            source_inter_train_gaps=tuple(gap for sample in conditional_tuple[index] for gap in sample),
        )
        for index, state in enumerate(state_order)
    )
    transition_rows = tuple(
        tuple((len(sample) + TRANSITION_PSEUDOCOUNT) / (sum(len(cell) for cell in row) + state_count) for sample in row)
        for row in conditional_tuple
    )
    return MarkovPacketTrainModel(
        conditional_inter_train_gaps=conditional_tuple,
        gap_quantile=GAP_QUANTILE,
        gap_threshold=threshold,
        global_inter_train_gaps=tuple(gap for row in conditional_tuple for sample in row for gap in sample),
        initial_probabilities=tuple(len(actual_lengths[state]) / len(trains) for state in state_order),
        inside_train_endpoint=INSIDE_TRAIN_ENDPOINT,
        length_cap=length_cap,
        states=states,
        transition_pseudocount=TRANSITION_PSEUDOCOUNT,
        transition_rows=transition_rows,
    )
