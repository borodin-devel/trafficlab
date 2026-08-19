"""Empirical Markov renewal traffic model with observable direction-size states."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from random import Random
from time import monotonic
from typing import Literal, Protocol, cast

import numpy as np
from numpy.typing import NDArray

from trafficlab.config import FamilyName, FloatBounds, GenerationLimits, IntegerBounds, MarkovRenewalConfig
from trafficlab.errors import TrafficlabError
from trafficlab.models.common import (
    MARKOV_MODEL_DIAGNOSTIC_KEYS,
    FamilyBounds,
    FittedModel,
    Gene,
    GenerationGuard,
    GenerationResult,
    Genes,
    IncompleteReason,
    ReferenceTrace,
    coerce_reference_trace,
    validate_fit_inputs,
)
from trafficlab.trace import Direction, TraceEvent, TrafficTrace

_ROW_TOLERANCE = 1e-12
_MINIMUM_FRAME_LENGTH = 14
_MAXIMUM_FRAME_LENGTH = 2**32 - 1
type TimingTier = Literal["transition", "source", "global"]

# Sparse captures may not observe every direction transition.  Timing lookup
# falls back from the exact transition, to the source direction, to the global
# sample; retaining the tier makes that statistical provenance explicit.
_TIMING_TIERS = frozenset(("transition", "source", "global"))


def _invalid(detail: str, *, corrective_action: str) -> TrafficlabError:
    return TrafficlabError(detail, corrective_action=corrective_action)


def type7_quantile(values: Sequence[int | float], q: float) -> float:
    """Return the Hyndman--Fan Type 7 quantile of one nonempty finite sample."""
    try:
        sample = tuple(values)
    except TypeError as error:
        raise _invalid(
            "invalid quantile sample",
            corrective_action="provide a nonempty finite numerical sample and a quantile in [0, 1]",
        ) from error
    if (
        not sample
        or type(q) is not float
        or not math.isfinite(q)
        or not 0.0 <= q <= 1.0
        or any(type(value) not in (int, float) or not math.isfinite(value) for value in sample)
    ):
        raise _invalid(
            "invalid quantile sample or level",
            corrective_action="provide a nonempty finite numerical sample and a quantile in [0, 1]",
        )
    return float(np.quantile(np.asarray(sample, dtype=np.float64), q, method="linear"))


def type7_boundaries(frame_lengths: NDArray[np.uint32], quantiles: tuple[float, float]) -> NDArray[np.float64]:
    """Return the two Type 7 frame-length boundaries as a float64 vector."""
    if frame_lengths.ndim != 1 or len(frame_lengths) == 0:
        raise ValueError("frame lengths must be a nonempty one-dimensional array")
    if any(type(quantile) is not float or not 0.0 <= quantile <= 1.0 for quantile in quantiles):
        raise ValueError("quantiles must be finite floats in [0, 1]")
    return np.asarray(np.quantile(frame_lengths, quantiles, method="linear"), dtype=np.float64)


def size_bin(frame_length: int, lower_threshold: float, upper_threshold: float) -> int:
    """Map a frame length to one of three bins using inclusive upper comparisons."""
    if type(frame_length) is not int:
        raise TypeError("frame_length must be an exact integer")
    if (
        type(lower_threshold) is not float
        or type(upper_threshold) is not float
        or not math.isfinite(lower_threshold)
        or not math.isfinite(upper_threshold)
        or lower_threshold >= upper_threshold
    ):
        raise ValueError("size thresholds must be finite increasing floats")
    if frame_length <= lower_threshold:
        return 0
    if frame_length <= upper_threshold:
        return 1
    return 2


def _validate_float_bounds(value: object, *, name: str, lower_limit: float | None = None) -> FloatBounds:
    if (
        type(value) is not FloatBounds
        or type(value.lower) is not float
        or type(value.upper) is not float
        or not math.isfinite(value.lower)
        or not math.isfinite(value.upper)
        or value.lower >= value.upper
        or (lower_limit is not None and value.lower < lower_limit)
    ):
        raise _invalid(
            f"invalid Markov renewal {name} bounds",
            corrective_action="provide finite ordered bounds satisfying the Markov renewal chromosome domain",
        )
    return value


def _validate_bounds(bounds: object) -> MarkovRenewalConfig:
    if type(bounds) is not MarkovRenewalConfig:
        raise _invalid(
            "invalid Markov renewal bounds",
            corrective_action="provide configured q1, q2, alpha, r, and c_t bounds",
        )
    q1 = _validate_float_bounds(bounds.q1, name="q1")
    q2 = _validate_float_bounds(bounds.q2, name="q2")
    _validate_float_bounds(bounds.alpha, name="alpha", lower_limit=0.0)
    c_t = _validate_float_bounds(bounds.c_t, name="c_t")
    if q1.lower <= 0.0 or q1.upper >= 1.0 or q2.lower <= 0.0 or q2.upper >= 1.0 or c_t.lower <= 0.0:
        raise _invalid(
            "invalid Markov renewal bounds",
            corrective_action="keep quantiles in (0, 1), alpha nonnegative, and c_t positive",
        )
    r = bounds.r
    if (
        type(r) is not IntegerBounds
        or type(r.lower) is not int
        or type(r.upper) is not int
        or r.lower < 1
        or r.lower >= r.upper
    ):
        raise _invalid(
            "invalid Markov renewal r bounds",
            corrective_action="provide inclusive ordered integer r bounds starting at one or greater",
        )
    return bounds


def _canonical_genes(genes: Sequence[Gene], bounds: object) -> tuple[float, float, float, int, float]:
    checked_bounds = _validate_bounds(bounds)
    try:
        values = tuple(genes)
    except TypeError as error:
        raise _invalid(
            "invalid Markov renewal genes",
            corrective_action="provide exactly q1, q2, alpha, r, and c_t finite numerical genes",
        ) from error
    if (
        len(values) != 5
        or any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
        or any(type(values[index]) is not float for index in (0, 1, 2, 4))
    ):
        raise _invalid(
            "invalid Markov renewal genes",
            corrective_action="provide exact finite float q1, q2, alpha, c_t genes and a numerical r gene",
        )
    q1_raw, q2_raw = sorted((cast(float, values[0]), cast(float, values[1])))
    q1 = min(max(q1_raw, checked_bounds.q1.lower), checked_bounds.q1.upper)
    q2 = min(max(q2_raw, checked_bounds.q2.lower), checked_bounds.q2.upper)
    alpha_raw = cast(float, values[2])
    alpha = min(max(alpha_raw, checked_bounds.alpha.lower), checked_bounds.alpha.upper)
    r_raw = values[3]
    rounded_r = math.floor(r_raw + 0.5)
    minimum_support = min(max(rounded_r, checked_bounds.r.lower), checked_bounds.r.upper)
    c_t_raw = cast(float, values[4])
    time_scale = min(max(c_t_raw, checked_bounds.c_t.lower), checked_bounds.c_t.upper)
    if not 0.0 < q1 < q2 < 1.0:
        raise _invalid(
            "invalid repaired Markov renewal quantiles",
            corrective_action="use named quantile bounds that preserve strict q1 less than q2 order",
        )
    return (q1, q2, alpha, minimum_support, time_scale)


def _repair_with_trace(
    genes: Sequence[Gene], bounds: object, trace: TrafficTrace
) -> tuple[float, float, float, int, float]:
    repaired = _canonical_genes(genes, bounds)
    thresholds = type7_boundaries(trace.frame_lengths, (repaired[0], repaired[1]))
    if thresholds[0] >= thresholds[1]:
        raise _invalid(
            "invalid Markov renewal thresholds: repaired quantiles produce duplicate thresholds",
            corrective_action="provide a reference with enough distinct frame lengths for three bins",
        )
    return repaired


def _validate_iats(values: object, *, allow_empty: bool, context: str) -> tuple[float, ...]:
    if type(values) is not tuple or (not allow_empty and not values):
        raise ValueError(f"{context} must be {'a' if allow_empty else 'a nonempty'} tuple")
    items = cast(tuple[object, ...], values)
    if any(type(value) is not float or not math.isfinite(value) or value < 0.0 for value in items):
        raise ValueError(f"{context} must contain finite nonnegative floats")
    return cast(tuple[float, ...], items)


def _holding_selection(
    conditional: tuple[float, ...],
    source: tuple[float, ...],
    global_iats: tuple[float, ...],
    *,
    minimum_support: int,
) -> tuple[TimingTier, tuple[float, ...]]:
    try:
        checked_conditional = _validate_iats(conditional, allow_empty=True, context="conditional IAT sample")
        checked_source = _validate_iats(source, allow_empty=True, context="source IAT sample")
        checked_global = _validate_iats(global_iats, allow_empty=False, context="global IAT sample")
    except (TypeError, ValueError) as error:
        raise _invalid(
            f"invalid holding-time samples: {error}",
            corrective_action="provide finite nonnegative IAT samples and a nonempty global sample",
        ) from error
    if type(minimum_support) is not int or minimum_support < 1:
        raise _invalid(
            "invalid holding-time minimum support",
            corrective_action="provide a positive exact integer minimum support",
        )
    if len(checked_conditional) >= minimum_support:
        return ("transition", checked_conditional)
    if checked_source:
        return ("source", checked_source)
    return ("global", checked_global)


def choose_holding_sample(
    conditional: tuple[float, ...],
    source: tuple[float, ...],
    global_iats: tuple[float, ...],
    *,
    minimum_support: int,
) -> tuple[float, ...]:
    """Choose the first eligible empirical IAT sample in the documented fallback order."""
    return _holding_selection(
        conditional,
        source,
        global_iats,
        minimum_support=minimum_support,
    )[1]


class _MarkovRng(Protocol):
    def random(self) -> float:
        """Return one uniform continuous draw."""
        ...

    def randrange(self, stop: int) -> int:
        """Return one empirical sample index below stop."""
        ...


@dataclass(frozen=True, slots=True)
class _RandomMarkovRng:
    random_source: Random

    def random(self) -> float:
        """Draw one standard-library uniform variate."""
        return self.random_source.random()

    def randrange(self, stop: int) -> int:
        """Draw one standard-library empirical index."""
        return self.random_source.randrange(stop)


def _weighted_index_from_draw(weights: tuple[float, ...], draw: object) -> int:
    if (
        not weights
        or any(type(weight) is not float or not math.isfinite(weight) or weight < 0.0 for weight in weights)
        or not math.isfinite(sum(weights))
        or sum(weights) <= 0.0
        or type(draw) is not float
        or not math.isfinite(draw)
        or not 0.0 <= draw < 1.0
    ):
        raise _invalid(
            "invalid Markov weighted random draw",
            corrective_action="use finite nonnegative weights and an RNG returning exact floats in [0, 1)",
        )
    threshold = draw * sum(weights)
    cumulative = 0.0
    for index, weight in enumerate(weights[:-1]):
        cumulative += weight
        if threshold < cumulative:
            return index
    return len(weights) - 1


def _probability_index_from_draw(probabilities: tuple[float, ...], draw: object) -> int:
    if (
        not probabilities
        or any(
            type(probability) is not float or not math.isfinite(probability) or probability < 0.0
            for probability in probabilities
        )
        or not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=_ROW_TOLERANCE)
        or type(draw) is not float
        or not math.isfinite(draw)
        or not 0.0 <= draw < 1.0
    ):
        raise _invalid(
            "invalid Markov probability row or random draw",
            corrective_action="use a valid probability row and an RNG returning exact floats in [0, 1)",
        )
    cumulative = 0.0
    for index, probability in enumerate(probabilities[:-1]):
        cumulative += probability
        if draw < cumulative:
            return index
    return len(probabilities) - 1


def _empirical_index_from_draw(stop: int, draw: object) -> int:
    if type(stop) is not int or stop <= 0 or type(draw) is not int or not 0 <= draw < stop:
        raise _invalid(
            "invalid Markov empirical random draw",
            corrective_action="use an RNG returning exact integers in the requested range",
        )
    return draw


def sample_transition(probabilities: tuple[float, ...], rng: _MarkovRng) -> int:
    """Sample an ordered transition row with one continuous draw."""
    return _probability_index_from_draw(probabilities, rng.random())


def sample_empirical(values: tuple[int, ...] | tuple[float, ...], rng: _MarkovRng) -> int | float:
    """Sample one ordered empirical value with one integer draw."""
    return values[_empirical_index_from_draw(len(values), rng.randrange(len(values)))]


def _validate_window(window: object) -> float:
    if type(window) is not float or not math.isfinite(window) or window <= 0.0:
        raise _invalid(
            "invalid observation window: it must be a finite positive float",
            corrective_action="provide a finite positive normalized observation window",
        )
    return window


def _validate_frame_lengths(values: object) -> tuple[int, ...]:
    if type(values) is not tuple or not values:
        raise ValueError("state frame_lengths must be a nonempty tuple")
    items = cast(tuple[object, ...], values)
    if any(type(value) is not int or not _MINIMUM_FRAME_LENGTH <= value <= _MAXIMUM_FRAME_LENGTH for value in items):
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
        _validate_iats(self.source_iats, allow_empty=True, context="state source_iats")


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
            ) or not math.isclose(sum(row), 1.0, rel_tol=0.0, abs_tol=_ROW_TOLERANCE):
                raise ValueError("transition rows must be finite, nonnegative, and sum to one")
        if (
            type(self.conditional_iats) is not tuple
            or len(self.conditional_iats) != state_count
            or any(type(row) is not tuple or len(row) != state_count for row in self.conditional_iats)
        ):
            raise ValueError("conditional_iats must be a K x K tuple")
        for row in self.conditional_iats:
            for sample in row:
                _validate_iats(sample, allow_empty=True, context="conditional IAT sample")
        _validate_iats(self.global_iats, allow_empty=False, context="global_iats")
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
                not math.isclose(actual, wanted, rel_tol=0.0, abs_tol=_ROW_TOLERANCE)
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
    if directions.ndim != 1 or frame_lengths.ndim != 1 or len(directions) != len(frame_lengths):
        raise ValueError("state columns must be equal-length one-dimensional arrays")
    if len(thresholds) != 2 or thresholds.ndim != 1 or thresholds[0] > thresholds[1]:
        raise ValueError("state thresholds must be two nondecreasing values")
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
    if states.ndim != 1 or len(states) < 2 or type(state_count) is not int or state_count < 1:
        raise ValueError("state indices must contain at least two values for one positive state count")
    flattened = states[:-1] * state_count + states[1:]
    return (
        np.bincount(flattened, minlength=state_count * state_count)
        .reshape(state_count, state_count)
        .astype(np.int64, copy=False)
    )


def _fit_trace(trace: TrafficTrace, genes: tuple[float, float, float, int, float]) -> MarkovRenewalModel:
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


def _validate_model(model: object) -> MarkovRenewalModel:
    if type(model) is not MarkovRenewalModel:
        raise TypeError("model must be a MarkovRenewalModel")
    try:
        return MarkovRenewalModel(
            alpha=model.alpha,
            conditional_iats=model.conditional_iats,
            global_iats=model.global_iats,
            minimum_support=model.minimum_support,
            states=model.states,
            thresholds=model.thresholds,
            time_scale=model.time_scale,
            transition_rows=model.transition_rows,
        )
    except (TypeError, ValueError) as error:
        raise _invalid(
            f"invalid fitted Markov renewal model: {error}",
            corrective_action="load or fit a complete finite Markov renewal model",
        ) from error


def _generate_with_rng(
    model: MarkovRenewalModel,
    rng: _MarkovRng,
    *,
    W: float,
    limits: GenerationLimits,
    clock: Callable[[], float] = monotonic,
) -> GenerationResult:
    """Generate with an injected RNG while preserving the documented stochastic order."""
    checked_model = _validate_model(model)
    window = _validate_window(W)
    guard = GenerationGuard.start(limits, clock=clock)
    events: list[TraceEvent] = []
    output_bytes = 0
    timing_counts: dict[str, int] = {name: 0 for name in MARKOV_MODEL_DIAGNOSTIC_KEYS}

    def generation_result(
        *,
        complete: bool,
        result_events: tuple[TraceEvent, ...],
        reason: IncompleteReason | None = None,
    ) -> GenerationResult:
        return GenerationResult(
            complete=complete,
            events=result_events,
            reason=reason,
            model_diagnostics=timing_counts,
        )

    reason = guard.pre_draw_reason(0, 0)
    if reason is not None:
        return generation_result(complete=False, result_events=(), reason=reason)
    raw_state_draw = rng.random()
    reason = guard.post_draw_reason()
    if reason is not None:
        return generation_result(complete=False, result_events=(), reason=reason)
    state_index = _weighted_index_from_draw(
        tuple(float(len(state.frame_lengths)) for state in checked_model.states), raw_state_draw
    )
    state = checked_model.states[state_index]

    raw_frame_draw = rng.randrange(len(state.frame_lengths))
    reason = guard.post_draw_reason()
    if reason is not None:
        return generation_result(complete=False, result_events=(), reason=reason)
    frame_length = state.frame_lengths[_empirical_index_from_draw(len(state.frame_lengths), raw_frame_draw)]
    reason = guard.prospective_reason(0, 0, frame_length)
    if reason is not None:
        return generation_result(complete=False, result_events=(), reason=reason)
    events.append(TraceEvent(0.0, state.direction, frame_length))
    output_bytes = frame_length
    current_time = 0.0

    while True:
        reason = guard.pre_draw_reason(len(events), output_bytes)
        if reason is not None:
            return generation_result(complete=False, result_events=tuple(events), reason=reason)
        raw_transition_draw = rng.random()
        reason = guard.post_draw_reason()
        if reason is not None:
            return generation_result(complete=False, result_events=tuple(events), reason=reason)
        destination_index = _probability_index_from_draw(
            checked_model.transition_rows[state_index], raw_transition_draw
        )
        timing_tier, holding_sample = _holding_selection(
            checked_model.conditional_iats[state_index][destination_index],
            checked_model.states[state_index].source_iats,
            checked_model.global_iats,
            minimum_support=checked_model.minimum_support,
        )
        timing_counts[f"timing_tier_{timing_tier}_count"] += 1
        if state_index in checked_model.timing_diagnostics.unobserved_rows:
            timing_counts["uniform_unobserved_row_count"] += 1

        raw_holding_draw = rng.randrange(len(holding_sample))
        reason = guard.post_draw_reason()
        if reason is not None:
            return generation_result(complete=False, result_events=tuple(events), reason=reason)
        holding_time = holding_sample[_empirical_index_from_draw(len(holding_sample), raw_holding_draw)]
        scaled_holding_time = holding_time * checked_model.time_scale
        next_time = current_time + scaled_holding_time
        if not math.isfinite(scaled_holding_time) or not math.isfinite(next_time):
            raise _invalid(
                "invalid Markov renewal arrival time",
                corrective_action="use finite fitted timing samples and scale values that do not overflow",
            )
        if next_time > window:
            return generation_result(complete=True, result_events=tuple(events))

        destination = checked_model.states[destination_index]
        raw_destination_frame_draw = rng.randrange(len(destination.frame_lengths))
        reason = guard.post_draw_reason()
        if reason is not None:
            return generation_result(complete=False, result_events=tuple(events), reason=reason)
        destination_frame_length = destination.frame_lengths[
            _empirical_index_from_draw(len(destination.frame_lengths), raw_destination_frame_draw)
        ]
        reason = guard.prospective_reason(len(events), output_bytes, destination_frame_length)
        if reason is not None:
            return generation_result(complete=False, result_events=tuple(events), reason=reason)
        events.append(TraceEvent(next_time, destination.direction, destination_frame_length))
        output_bytes += destination_frame_length
        current_time = next_time
        state_index = destination_index


def _load_float_list(value: object, *, context: str) -> tuple[float, ...]:
    if type(value) is not list:
        raise ValueError(f"{context} must be a list of finite exact floats")
    items = cast(list[object], value)
    if any(type(item) is not float or not math.isfinite(item) for item in items):
        raise ValueError(f"{context} must be a list of finite exact floats")
    return tuple(cast(list[float], items))


def _load_int_list(value: object, *, context: str) -> tuple[int, ...]:
    if type(value) is not list:
        raise ValueError(f"{context} must be a list of exact integers")
    items = cast(list[object], value)
    if any(type(item) is not int for item in items):
        raise ValueError(f"{context} must be a list of exact integers")
    return tuple(cast(list[int], items))


def _timing_diagnostics_document(diagnostics: MarkovTimingDiagnostics) -> dict[str, object]:
    return {
        "reference_usage_counts": {
            "global": diagnostics.reference_global_count,
            "source": diagnostics.reference_source_count,
            "transition": diagnostics.reference_transition_count,
        },
        "transition_tiers": [list(row) for row in diagnostics.transition_tiers],
        "unobserved_rows": list(diagnostics.unobserved_rows),
    }


def _matches_timing_diagnostics(value: object, expected: MarkovTimingDiagnostics) -> bool:
    if type(value) is not dict:
        return False
    document = cast(dict[str, object], value)
    if set(document) != {"reference_usage_counts", "transition_tiers", "unobserved_rows"}:
        return False
    counts = document["reference_usage_counts"]
    if type(counts) is not dict:
        return False
    count_values = cast(dict[object, object], counts)
    if (
        len(count_values) != len(_TIMING_TIERS)
        or any(type(name) is not str or name not in _TIMING_TIERS for name in count_values)
        or any(type(item) is not int or item < 0 for item in count_values.values())
    ):
        return False
    rows = document["transition_tiers"]
    if type(rows) is not list or any(
        type(row) is not list
        or any(type(tier) is not str or tier not in _TIMING_TIERS for tier in cast(list[object], row))
        for row in cast(list[object], rows)
    ):
        return False
    unobserved = document["unobserved_rows"]
    if type(unobserved) is not list or any(
        type(index) is not int or index < 0 for index in cast(list[object], unobserved)
    ):
        return False
    return document == _timing_diagnostics_document(expected)


def _load_state(value: object) -> MarkovState:
    if type(value) is not dict:
        raise ValueError("each state must be an object")
    state = cast(dict[str, object], value)
    if set(state) != {"direction", "frame_lengths", "size_bin", "source_iats"}:
        raise ValueError("each state must contain exactly direction, frame_lengths, size_bin, and source_iats")
    direction = state["direction"]
    size_bin_value = state["size_bin"]
    if type(direction) is not str or type(size_bin_value) is not int:
        raise ValueError("state direction and size_bin must use exact JSON scalar types")
    try:
        parsed_direction = Direction(direction)
    except ValueError as error:
        raise ValueError("state direction must be outbound or inbound") from error
    return MarkovState(
        direction=parsed_direction,
        size_bin=size_bin_value,
        frame_lengths=_load_int_list(state["frame_lengths"], context="state frame_lengths"),
        source_iats=_load_float_list(state["source_iats"], context="state source_iats"),
    )


class MarkovRenewalFamily:
    """Fit, serialize, and generate the observable-state Markov renewal family."""

    name: FamilyName = "markov_renewal"
    gene_names: tuple[str, ...] = ("q1", "q2", "alpha", "r", "c_t")
    bounds_type = MarkovRenewalConfig
    estimator_choices: Mapping[str, str | int | float] = {
        "first_event": "zero",
        "quantile": "type7_linear",
        "state_order": "first_appearance",
        "timing": "conditional_source_global",
        "transition": "additive_uniform_empty_row",
    }

    def repair(self, genes: Sequence[Gene], bounds: FamilyBounds, reference: ReferenceTrace) -> Genes:
        """Return a canonical chromosome whose quantiles form distinct reference thresholds."""
        trace = coerce_reference_trace(reference)
        if len(trace) < 2:
            raise _invalid(
                "invalid Markov renewal reference",
                corrective_action="provide at least two canonical nondecreasing reference events",
            )
        return _repair_with_trace(genes, bounds, trace)

    def fit(
        self, reference: ReferenceTrace, genes: Sequence[Gene], *, W: float, bounds: FamilyBounds
    ) -> MarkovRenewalModel:
        """Fit active states, a complete transition matrix, and aligned empirical IAT samples."""
        trace = validate_fit_inputs(reference, W=W)
        repaired = _repair_with_trace(genes, bounds, trace)
        return _fit_trace(trace, repaired)

    def generate(
        self,
        model: FittedModel,
        seed: int,
        W: float,
        limits: GenerationLimits,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> GenerationResult:
        """Generate reproducibly from one locally owned random stream."""
        if type(seed) is not int or seed < 0:
            raise _invalid(
                "invalid Markov renewal seed: it must be a nonnegative exact integer",
                corrective_action="provide a nonnegative integer generation seed",
            )
        return _generate_with_rng(
            cast(MarkovRenewalModel, model),
            _RandomMarkovRng(Random(seed)),
            W=W,
            limits=limits,
            clock=clock,
        )

    def dump_fitted(self, model: FittedModel) -> dict[str, object]:
        """Return the strict JSON-compatible fitted payload."""
        checked_model = _validate_model(model)
        return {
            "alpha": checked_model.alpha,
            "conditional_iats": [[list(sample) for sample in row] for row in checked_model.conditional_iats],
            "global_iats": list(checked_model.global_iats),
            "minimum_support": checked_model.minimum_support,
            "states": [
                {
                    "direction": state.direction.value,
                    "frame_lengths": list(state.frame_lengths),
                    "size_bin": state.size_bin,
                    "source_iats": list(state.source_iats),
                }
                for state in checked_model.states
            ],
            "thresholds": list(checked_model.thresholds),
            "time_scale": checked_model.time_scale,
            "timing_diagnostics": _timing_diagnostics_document(checked_model.timing_diagnostics),
            "transition_rows": [list(row) for row in checked_model.transition_rows],
        }

    def load_fitted(self, data: object, *, genes: Genes, bounds: FamilyBounds) -> MarkovRenewalModel:
        """Load and validate one strict fitted payload bound to its outer genes."""
        repaired = _canonical_genes(genes, bounds)
        expected_keys = {
            "alpha",
            "conditional_iats",
            "global_iats",
            "minimum_support",
            "states",
            "thresholds",
            "time_scale",
            "timing_diagnostics",
            "transition_rows",
        }
        if type(data) is not dict:
            raise _invalid(
                "invalid fitted Markov renewal payload",
                corrective_action="provide exactly the documented fitted Markov renewal JSON fields",
            )
        payload = cast(dict[str, object], data)
        if set(payload) != expected_keys:
            raise _invalid(
                "invalid fitted Markov renewal payload",
                corrective_action="provide exactly the documented fitted Markov renewal JSON fields",
            )
        alpha = payload["alpha"]
        minimum_support = payload["minimum_support"]
        time_scale = payload["time_scale"]
        if (
            type(alpha) is not float
            or type(minimum_support) is not int
            or type(time_scale) is not float
            or alpha != repaired[2]
            or minimum_support != repaired[3]
            or time_scale != repaired[4]
        ):
            raise _invalid(
                "invalid fitted Markov renewal parameters",
                corrective_action="bind alpha, minimum_support, and time_scale to the repaired outer genes",
            )
        try:
            thresholds = _load_float_list(payload["thresholds"], context="thresholds")
            if len(thresholds) != 2:
                raise ValueError("thresholds must contain exactly two values")
            states_data = payload["states"]
            if type(states_data) is not list or not states_data:
                raise ValueError("states must be a nonempty list")
            state_items = cast(list[object], states_data)
            states = tuple(_load_state(value) for value in state_items)
            frame_lengths = tuple(frame_length for state in states for frame_length in state.frame_lengths)
            expected_thresholds = (
                type7_quantile(frame_lengths, repaired[0]),
                type7_quantile(frame_lengths, repaired[1]),
            )
            if thresholds != expected_thresholds:
                raise ValueError("thresholds must equal Type 7 quantiles from the repaired outer q genes")
            state_count = len(states)
            conditional_data = payload["conditional_iats"]
            if type(conditional_data) is not list:
                raise ValueError("conditional_iats must contain K rows")
            conditional_items = cast(list[object], conditional_data)
            if len(conditional_items) != state_count:
                raise ValueError("conditional_iats must contain K rows")
            conditional_rows: list[tuple[tuple[float, ...], ...]] = []
            for row in conditional_items:
                if type(row) is not list:
                    raise ValueError("conditional_iats must be a K x K array")
                samples = cast(list[object], row)
                if len(samples) != state_count:
                    raise ValueError("conditional_iats must be a K x K array")
                conditional_rows.append(
                    tuple(_load_float_list(sample, context="conditional IAT sample") for sample in samples)
                )
            transition_data = payload["transition_rows"]
            if type(transition_data) is not list:
                raise ValueError("transition_rows must contain K rows")
            transition_items = cast(list[object], transition_data)
            if len(transition_items) != state_count:
                raise ValueError("transition_rows must contain K rows")
            transition_rows: list[tuple[float, ...]] = []
            for row in transition_items:
                values = _load_float_list(row, context="transition row")
                if len(values) != state_count:
                    raise ValueError("transition_rows must be a K x K array")
                transition_rows.append(values)
            model = MarkovRenewalModel(
                alpha=alpha,
                conditional_iats=tuple(conditional_rows),
                global_iats=_load_float_list(payload["global_iats"], context="global_iats"),
                minimum_support=minimum_support,
                states=states,
                thresholds=thresholds,
                time_scale=time_scale,
                transition_rows=tuple(transition_rows),
            )
            if not _matches_timing_diagnostics(payload["timing_diagnostics"], model.timing_diagnostics):
                raise ValueError("timing_diagnostics must exactly match the fitted sparse timing evidence")
            return model
        except (TypeError, ValueError) as error:
            raise _invalid(
                f"invalid fitted Markov renewal payload: {error}",
                corrective_action="provide a complete finite dimensionally aligned fitted Markov renewal payload",
            ) from error
