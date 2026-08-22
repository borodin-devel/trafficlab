"""Empirical Markov renewal traffic model with observable direction-size states."""

from __future__ import annotations

import math
from typing import Literal, Protocol, cast

from trafficlab.generation.models.markov_renewal.parameters import ROW_TOLERANCE, invalid_markov

type TimingTier = Literal["transition", "source", "global"]

# Sparse captures may not observe every direction transition.  Timing lookup
# falls back from the exact transition, to the source direction, to the global
# sample; retaining the tier makes that statistical provenance explicit.
TIMING_TIERS = frozenset(("transition", "source", "global"))


def validate_iats(values: object, *, allow_empty: bool, context: str) -> tuple[float, ...]:
    if type(values) is not tuple or (not allow_empty and not values):
        raise ValueError(f"{context} must be {'a' if allow_empty else 'a nonempty'} tuple")
    items = cast(tuple[object, ...], values)
    if any(type(value) is not float or not math.isfinite(value) or value < 0.0 for value in items):
        raise ValueError(f"{context} must contain finite nonnegative floats")
    return cast(tuple[float, ...], items)


def holding_selection(
    conditional: tuple[float, ...],
    source: tuple[float, ...],
    global_iats: tuple[float, ...],
    *,
    minimum_support: int,
) -> tuple[TimingTier, tuple[float, ...]]:
    try:
        checked_conditional = validate_iats(conditional, allow_empty=True, context="conditional IAT sample")
        checked_source = validate_iats(source, allow_empty=True, context="source IAT sample")
        checked_global = validate_iats(global_iats, allow_empty=False, context="global IAT sample")
    except (TypeError, ValueError) as error:
        raise invalid_markov(
            f"invalid holding-time samples: {error}",
            corrective_action="provide finite nonnegative IAT samples and a nonempty global sample",
        ) from error
    if type(minimum_support) is not int or minimum_support < 1:
        raise invalid_markov(
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
    return holding_selection(
        conditional,
        source,
        global_iats,
        minimum_support=minimum_support,
    )[1]


class MarkovRng(Protocol):
    def random(self) -> float:
        """Return one uniform continuous draw."""
        ...

    def choice(self, a: int) -> int:
        """Return one empirical sample index below a positive population size."""
        ...


def weighted_index_from_draw(weights: tuple[float, ...], draw: object) -> int:
    if (
        not weights
        or any(type(weight) is not float or not math.isfinite(weight) or weight < 0.0 for weight in weights)
        or not math.isfinite(sum(weights))
        or sum(weights) <= 0.0
        or type(draw) is not float
        or not math.isfinite(draw)
        or not 0.0 <= draw < 1.0
    ):
        raise invalid_markov(
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


def probability_index_from_draw(probabilities: tuple[float, ...], draw: object) -> int:
    if (
        not probabilities
        or any(
            type(probability) is not float or not math.isfinite(probability) or probability < 0.0
            for probability in probabilities
        )
        or not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=ROW_TOLERANCE)
        or type(draw) is not float
        or not math.isfinite(draw)
        or not 0.0 <= draw < 1.0
    ):
        raise invalid_markov(
            "invalid Markov probability row or random draw",
            corrective_action="use a valid probability row and an RNG returning exact floats in [0, 1)",
        )
    cumulative = 0.0
    for index, probability in enumerate(probabilities[:-1]):
        cumulative += probability
        if draw < cumulative:
            return index
    return len(probabilities) - 1


def empirical_index_from_draw(stop: int, draw: object) -> int:
    if type(stop) is not int or stop <= 0 or type(draw) is not int or not 0 <= draw < stop:
        raise invalid_markov(
            "invalid Markov empirical random draw",
            corrective_action="use an RNG returning exact integers in the requested range",
        )
    return draw


def sample_transition(probabilities: tuple[float, ...], rng: MarkovRng) -> int:
    """Sample an ordered transition row with one continuous draw."""
    return probability_index_from_draw(probabilities, rng.random())


def sample_empirical(values: tuple[int, ...] | tuple[float, ...], rng: MarkovRng) -> int | float:
    """Sample one ordered empirical value with one integer draw."""
    return values[empirical_index_from_draw(len(values), rng.choice(len(values)))]
