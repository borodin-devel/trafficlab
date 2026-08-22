"""Empirical Markov renewal traffic model with observable direction-size states."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import cast

import numpy as np
from numpy.typing import NDArray

from trafficlab.common.config import FamilyName
from trafficlab.common.trace import Direction, TrafficTrace
from trafficlab.generation.models.markov_renewal.parameters import (
    MAXIMUM_FRAME_LENGTH,
    MINIMUM_FRAME_LENGTH,
    ROW_TOLERANCE,
    size_bin,
    type7_boundaries,
)
from trafficlab.generation.models.markov_renewal.sampling import TimingTier, validate_iats


def _validate_frame_lengths(values: object) -> tuple[int, ...]:
    if type(values) is not tuple or not values:
        raise ValueError("state frame_lengths must be a nonempty tuple")
    items = cast(tuple[object, ...], values)
    if any(type(value) is not int or not MINIMUM_FRAME_LENGTH <= value <= MAXIMUM_FRAME_LENGTH for value in items):
        raise ValueError("state frame_lengths must contain canonical Ethernet lengths")
    return cast(tuple[int, ...], items)


def _validate_empirical_flow(
    states: tuple[MarkovState, ...],
    conditional_iats: tuple[tuple[tuple[float, ...], ...], ...],
    global_iats: tuple[float, ...],
) -> None:
    packet_counts = tuple(len(state.frame_lengths) for state in states)
    if sum(packet_counts) != len(global_iats) + 1:
        raise ValueError("state packet count must equal global IAT count plus one")
    outgoing_counts = tuple(sum(len(sample) for sample in row) for row in conditional_iats)
    incoming_counts = tuple(
        sum(len(conditional_iats[source][destination]) for source in range(len(states)))
        for destination in range(len(states))
    )
    if (
        any(
            outgoing not in (packets, packets - 1)
            for packets, outgoing in zip(packet_counts, outgoing_counts, strict=True)
        )
        or any(
            incoming not in (packets, packets - 1)
            for packets, incoming in zip(packet_counts, incoming_counts, strict=True)
        )
        or sum(packets - outgoing for packets, outgoing in zip(packet_counts, outgoing_counts, strict=True)) != 1
        or sum(packets - incoming for packets, incoming in zip(packet_counts, incoming_counts, strict=True)) != 1
    ):
        raise ValueError("state packet counts must match transition flow with one initial and one final packet")
    active_states = {index for index, packets in enumerate(packet_counts) if packets > 0}
    adjacency = [set[int]() for _ in states]
    for source, row in enumerate(conditional_iats):
        for destination, sample in enumerate(row):
            if sample:
                adjacency[source].add(destination)
                adjacency[destination].add(source)
    initial_state = next(iter(active_states))
    visited = {initial_state}
    frontier = [initial_state]
    while frontier:
        state = frontier.pop()
        for neighbor in adjacency[state] - visited:
            visited.add(neighbor)
            frontier.append(neighbor)
    if visited != active_states:
        raise ValueError("states with stored packets must form one connected transition component")


@dataclass(frozen=True, slots=True)
class MarkovState:
    """One active observable state and its ordered empirical emission samples."""

    direction: Direction
    size_bin: int
    frame_lengths: tuple[int, ...]
    source_iats: tuple[float, ...]

    def __post_init__(self) -> None:
        if type(self.direction) is not Direction:
            raise TypeError("state direction must be a Direction")
        if type(self.size_bin) is not int or not 0 <= self.size_bin <= 2:
            raise ValueError("state size_bin must be an exact integer in 0..2")
        _validate_frame_lengths(self.frame_lengths)
        validate_iats(self.source_iats, allow_empty=True, context="state source_iats")


@dataclass(frozen=True, slots=True)
class MarkovTimingDiagnostics:
    """Deterministic fitted evidence for sparse timing and unobserved rows."""

    transition_tiers: tuple[tuple[TimingTier, ...], ...]
    reference_transition_count: int
    reference_source_count: int
    reference_global_count: int
    unobserved_rows: tuple[int, ...]


def _timing_diagnostics(
    conditional_iats: tuple[tuple[tuple[float, ...], ...], ...],
    states: tuple[MarkovState, ...],
    minimum_support: int,
) -> MarkovTimingDiagnostics:
    rows: list[tuple[TimingTier, ...]] = []
    counts: dict[TimingTier, int] = {"transition": 0, "source": 0, "global": 0}
    for state, samples in zip(states, conditional_iats, strict=True):
        tiers: list[TimingTier] = []
        for sample in samples:
            tier: TimingTier
            if len(sample) >= minimum_support:
                tier = "transition"
            elif state.source_iats:
                tier = "source"
            else:
                tier = "global"
            tiers.append(tier)
            counts[tier] += len(sample)
        rows.append(tuple(tiers))
    return MarkovTimingDiagnostics(
        transition_tiers=tuple(rows),
        reference_transition_count=counts["transition"],
        reference_source_count=counts["source"],
        reference_global_count=counts["global"],
        unobserved_rows=tuple(index for index, state in enumerate(states) if not state.source_iats),
    )


@dataclass(frozen=True, slots=True)
class MarkovRenewalModel:
    """A complete fitted transition kernel and aligned empirical samples."""

    alpha: float
    conditional_iats: tuple[tuple[tuple[float, ...], ...], ...]
    global_iats: tuple[float, ...]
    minimum_support: int
    states: tuple[MarkovState, ...]
    thresholds: tuple[float, float]
    time_scale: float
    transition_rows: tuple[tuple[float, ...], ...]
    timing_diagnostics: MarkovTimingDiagnostics = field(init=False)

    def __post_init__(self) -> None:
        if type(self.alpha) is not float or not math.isfinite(self.alpha) or self.alpha < 0.0:
            raise ValueError("alpha must be a finite nonnegative float")
        if type(self.minimum_support) is not int or self.minimum_support < 1:
            raise ValueError("minimum_support must be a positive exact integer")
        if type(self.time_scale) is not float or not math.isfinite(self.time_scale) or self.time_scale <= 0.0:
            raise ValueError("time_scale must be a finite positive float")
        if (
            type(self.thresholds) is not tuple
            or len(self.thresholds) != 2
            or any(type(value) is not float or not math.isfinite(value) for value in self.thresholds)
            or self.thresholds[0] >= self.thresholds[1]
        ):
            raise ValueError("thresholds must be two finite strictly increasing floats")
        if (
            type(self.states) is not tuple
            or not self.states
            or any(type(state) is not MarkovState for state in self.states)
        ):
            raise ValueError("states must be a nonempty tuple of MarkovState values")
        if len({(state.direction, state.size_bin) for state in self.states}) != len(self.states):
            raise ValueError("states must have unique direction-size identities")
        for state in self.states:
            if any(size_bin(frame_length, *self.thresholds) != state.size_bin for frame_length in state.frame_lengths):
                raise ValueError("state frame_lengths must belong to its threshold bin")
        state_count = len(self.states)
        if (
            type(self.transition_rows) is not tuple
            or len(self.transition_rows) != state_count
            or any(type(row) is not tuple or len(row) != state_count for row in self.transition_rows)
        ):
            raise ValueError("transition_rows must be a K x K tuple")
        for row in self.transition_rows:
            if any(
                type(value) is not float or not math.isfinite(value) or value < 0.0 for value in row
            ) or not math.isclose(sum(row), 1.0, rel_tol=0.0, abs_tol=ROW_TOLERANCE):
                raise ValueError("transition rows must be finite, nonnegative, and sum to one")
        if (
            type(self.conditional_iats) is not tuple
            or len(self.conditional_iats) != state_count
            or any(type(row) is not tuple or len(row) != state_count for row in self.conditional_iats)
        ):
            raise ValueError("conditional_iats must be a K x K tuple")
        for row in self.conditional_iats:
            for sample in row:
                validate_iats(sample, allow_empty=True, context="conditional IAT sample")
        validate_iats(self.global_iats, allow_empty=False, context="global_iats")
        all_conditional = tuple(value for row in self.conditional_iats for sample in row for value in sample)
        if Counter(all_conditional) != Counter(self.global_iats):
            raise ValueError("global_iats must contain every conditional IAT")
        for state, row in zip(self.states, self.conditional_iats, strict=True):
            if Counter(value for sample in row for value in sample) != Counter(state.source_iats):
                raise ValueError("state source_iats must contain every IAT leaving its source")
        _validate_empirical_flow(self.states, self.conditional_iats, self.global_iats)
        for probabilities, sample_row in zip(self.transition_rows, self.conditional_iats, strict=True):
            outgoing = sum(len(sample) for sample in sample_row)
            denominator = outgoing + self.alpha * state_count
            expected = (
                tuple(1.0 / state_count for _ in range(state_count))
                if denominator == 0.0
                else tuple((len(sample) + self.alpha) / denominator for sample in sample_row)
            )
            if any(
                not math.isclose(actual, wanted, rel_tol=0.0, abs_tol=ROW_TOLERANCE)
                for actual, wanted in zip(probabilities, expected, strict=True)
            ):
                raise ValueError("transition rows must match the complete additive estimator")
        object.__setattr__(
            self,
            "timing_diagnostics",
            _timing_diagnostics(self.conditional_iats, self.states, self.minimum_support),
        )

    @property
    def family(self) -> FamilyName:
        """Return the family identifier required by the fitted-model contract."""
        return "markov_renewal"


def encode_markov_states(
    directions: NDArray[np.uint8], frame_lengths: NDArray[np.uint32], thresholds: NDArray[np.float64]
) -> tuple[NDArray[np.intp], NDArray[np.uint8]]:
    """Encode active direction-size states in their observed first-appearance order."""
    if type(directions) is not np.ndarray or directions.dtype != np.dtype(np.uint8):
        raise ValueError("directions must be a uint8 NumPy array")
    if type(frame_lengths) is not np.ndarray or frame_lengths.dtype != np.dtype(np.uint32):
        raise ValueError("frame lengths must be a uint32 NumPy array")
    if type(thresholds) is not np.ndarray or thresholds.dtype != np.dtype(np.float64):
        raise ValueError("state thresholds must be a float64 NumPy array")
    if directions.ndim != 1 or frame_lengths.ndim != 1 or len(directions) != len(frame_lengths) or len(directions) == 0:
        raise ValueError("state columns must be nonempty equal-length one-dimensional arrays")
    if np.any((directions != 0) & (directions != 1)) or np.any(frame_lengths == 0):
        raise ValueError("state columns must contain canonical direction and positive frame-length values")
    if (
        thresholds.ndim != 1
        or len(thresholds) != 2
        or not np.all(np.isfinite(thresholds))
        or thresholds[0] >= thresholds[1]
    ):
        raise ValueError("state thresholds must be two finite increasing values")
    size_bins = np.searchsorted(thresholds, frame_lengths, side="left").astype(np.uint8, copy=False)
    identity_codes = directions * np.uint8(3) + size_bins
    unique_codes, first_indices = np.unique(identity_codes, return_index=True)
    order = np.argsort(first_indices, kind="stable")
    ordered_codes = unique_codes[order]
    positions = np.empty(6, dtype=np.intp)
    positions[ordered_codes] = np.arange(len(ordered_codes), dtype=np.intp)
    return positions[identity_codes], ordered_codes


def transition_count_matrix(states: NDArray[np.intp], state_count: int) -> NDArray[np.int64]:
    """Count adjacent state pairs with flattened NumPy bincount indices."""
    if type(states) is not np.ndarray or states.dtype != np.dtype(np.intp):
        raise ValueError("state indices must be an intp NumPy array")
    if states.ndim != 1 or len(states) < 2 or type(state_count) is not int or state_count < 1:
        raise ValueError("state indices must contain at least two values for one positive state count")
    if np.any(states < 0) or np.any(states >= state_count):
        raise ValueError("state indices must be in [0, state_count)")
    maximum_index = np.iinfo(np.intp).max
    if state_count > maximum_index // state_count:
        raise ValueError("state count is too large for a platform index matrix")
    cell_count = state_count * state_count
    flattened = states[:-1] * state_count + states[1:]
    return np.bincount(flattened, minlength=cell_count).reshape(state_count, state_count).astype(np.int64, copy=False)


def fit_trace(trace: TrafficTrace, genes: tuple[float, float, float, int, float]) -> MarkovRenewalModel:
    q1, q2, alpha, minimum_support, time_scale = genes
    boundary_vector = type7_boundaries(trace.frame_lengths, (q1, q2))
    thresholds = (float(boundary_vector[0]), float(boundary_vector[1]))
    states_vector, identity_codes = encode_markov_states(trace.directions, trace.frame_lengths, boundary_vector)
    state_count = len(identity_codes)
    transition_counts = transition_count_matrix(states_vector, state_count)
    iats = trace.iats()
    source_indices = states_vector[:-1]
    destination_indices = states_vector[1:]
    denominators = transition_counts.sum(axis=1, dtype=np.float64) + alpha * state_count
    rows_array = np.divide(
        transition_counts + alpha,
        denominators[:, np.newaxis],
        out=np.zeros((state_count, state_count), dtype=np.float64),
        where=denominators[:, np.newaxis] != 0.0,
    )
    rows_array[denominators == 0.0] = 1.0 / state_count
    conditional_samples = tuple(
        tuple(
            tuple(float(value) for value in iats[(source_indices == source) & (destination_indices == destination)])
            for destination in range(state_count)
        )
        for source in range(state_count)
    )
    states = tuple(
        MarkovState(
            Direction.OUTBOUND if int(identity_code) // 3 == 0 else Direction.INBOUND,
            int(identity_code) % 3,
            tuple(int(value) for value in trace.frame_lengths[states_vector == index]),
            tuple(float(value) for value in iats[source_indices == index]),
        )
        for index, identity_code in enumerate(identity_codes)
    )
    return MarkovRenewalModel(
        alpha=alpha,
        conditional_iats=conditional_samples,
        global_iats=tuple(float(value) for value in iats),
        minimum_support=minimum_support,
        states=states,
        thresholds=thresholds,
        time_scale=time_scale,
        transition_rows=tuple(tuple(float(value) for value in row) for row in rows_array),
    )
