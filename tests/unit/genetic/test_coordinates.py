"""Tests for exact transformed genetic-coordinate behavior."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from trafficlab.common.config import FloatBounds, IntegerBounds, MarkovRenewalConfig, MmppConfig, PoissonConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.fitting.genetic.coordinates import (
    CandidateEvaluationError,
    GeneCoordinate,
    GeneticRng,
    bernoulli,
    decode_gene,
    encode_gene,
    family_coordinates,
    initialize_candidate,
    mutate_coordinate,
    reflect,
)
from trafficlab.generation.models.registry import MARKOV_RENEWAL_FAMILY


@dataclass
class ScriptedRandom:
    """A strict RNG double that records the public primitives genetic code selects."""

    random_values: list[float] = field(default_factory=list[float])
    ranges: list[int] = field(default_factory=list[int])
    calls: list[tuple[object, ...]] = field(default_factory=list[tuple[object, ...]])

    def random(self) -> float:
        self.calls.append(("random",))
        return self.random_values.pop(0)

    def integers(self, low: int, high: int | None = None, *, endpoint: bool = False) -> int:
        self.calls.append(("integers", low, high, endpoint))
        return self.ranges.pop(0)


POISSON_BOUNDS = PoissonConfig(c_lambda=FloatBounds(lower=0.5, upper=2.0))
MARKOV_BOUNDS = MarkovRenewalConfig(
    q1=FloatBounds(lower=0.1, upper=0.4),
    q2=FloatBounds(lower=0.6, upper=0.9),
    alpha=FloatBounds(lower=0.0, upper=2.0),
    r=IntegerBounds(lower=1, upper=5),
    c_t=FloatBounds(lower=0.5, upper=2.0),
)
MMPP_BOUNDS = MmppConfig(
    q01=FloatBounds(lower=0.1, upper=3.0),
    q10=FloatBounds(lower=0.1, upper=3.0),
    lambda0=FloatBounds(lower=0.1, upper=1.0),
    lambda1=FloatBounds(lower=2.0, upper=5.0),
)
REFERENCE = TrafficTrace.from_events(
    (
        TraceEvent(0.0, Direction.OUTBOUND, 64),
        TraceEvent(1.0, Direction.INBOUND, 128),
        TraceEvent(2.0, Direction.OUTBOUND, 256),
    )
)


def test_coordinate_metadata_is_exact_for_all_registered_families() -> None:
    """Each family gene must use its architecture-defined transformed domain."""
    assert tuple(item.kind for item in family_coordinates("poisson_empirical", POISSON_BOUNDS)) == ("log",)
    assert tuple(item.kind for item in family_coordinates("markov_renewal", MARKOV_BOUNDS)) == (
        "linear",
        "linear",
        "linear",
        "integer",
        "log",
    )
    assert tuple(item.kind for item in family_coordinates("mmpp", MMPP_BOUNDS)) == ("log", "log", "log", "log")


def test_reflect_and_integer_decode_use_locked_endpoint_rules() -> None:
    """Clamp-like reflection and bankers rounding would change deterministic offspring."""
    integer = GeneCoordinate("r", "integer", IntegerBounds(lower=1, upper=5))

    assert (reflect(-0.2), reflect(1.2), reflect(2.2)) == pytest.approx((0.2, 0.8, 0.2))
    assert decode_gene(integer, 0.125) == 2
    assert decode_gene(integer, 0.875) == 5


def test_linear_and_logarithmic_coordinates_round_trip_named_endpoints() -> None:
    """Wrong transforms would bias mutation differently across gene scales."""
    linear = GeneCoordinate("alpha", "linear", FloatBounds(lower=2.0, upper=6.0))
    logarithmic = GeneCoordinate("c_lambda", "log", FloatBounds(lower=0.5, upper=8.0))

    assert (encode_gene(linear, 2.0), decode_gene(linear, 0.75)) == (0.0, 5.0)
    assert (encode_gene(logarithmic, 0.5), decode_gene(logarithmic, 1.0)) == (0.0, 8.0)
    assert encode_gene(logarithmic, 2.0) == 0.5


def test_initialization_uses_the_documented_rng_primitives() -> None:
    """Changing an initialization primitive changes every later master-RNG draw."""
    rng = ScriptedRandom(random_values=[0.25, 0.5, 0.75, 0.0], ranges=[3])

    assert initialize_candidate(MARKOV_RENEWAL_FAMILY, MARKOV_BOUNDS, REFERENCE, cast(GeneticRng, rng)) == (
        0.1 + 0.25 * (0.4 - 0.1),
        0.75,
        1.5,
        3,
        0.5,
    )
    assert rng.calls == [("random",), ("random",), ("random",), ("integers", 1, 5, True), ("random",)]


def test_initialize_candidate_translates_only_registered_repair_errors() -> None:
    """A mathematical repair failure must become an invalid-candidate classification."""
    invalid_reference = TrafficTrace.from_events(
        (TraceEvent(0.0, Direction.OUTBOUND, 64), TraceEvent(1.0, Direction.INBOUND, 64))
    )

    with pytest.raises(CandidateEvaluationError, match="thresholds") as raised:
        initialize_candidate(
            MARKOV_RENEWAL_FAMILY,
            MARKOV_BOUNDS,
            invalid_reference,
            cast(GeneticRng, ScriptedRandom([0.0] * 4, [1])),
        )

    assert (raised.value.kind, raised.value.seed) == ("repair", None)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_reflect_rejects_nonfinite_coordinates(value: float) -> None:
    """A nonfinite mutation coordinate cannot be reflected into a valid gene."""
    with pytest.raises(TrafficlabError, match="coordinate"):
        reflect(value)


def test_bernoulli_consumes_a_draw_at_both_probability_endpoints() -> None:
    """Skipping endpoint draws would desynchronize every subsequent genetic action."""
    rng = ScriptedRandom(random_values=[0.99, 0.0])

    assert (bernoulli(cast(GeneticRng, rng), 0.0), bernoulli(cast(GeneticRng, rng), 1.0)) == (False, True)
    assert rng.calls == [("random",), ("random",)]


def test_mutate_coordinate_reflects_before_decoding() -> None:
    """Selected mutation must reflect in normalized space rather than clamp a raw gene."""
    coordinate = GeneCoordinate("value", "linear", FloatBounds(lower=10.0, upper=20.0))

    assert mutate_coordinate(coordinate, 19.0, 0.3) == 18.0


def test_unknown_family_and_wrong_registered_bounds_are_rejected() -> None:
    """Coordinate metadata must not accept a family/bounds pairing the registry cannot repair."""
    with pytest.raises(TrafficlabError, match="unknown model family"):
        family_coordinates("unknown", POISSON_BOUNDS)  # type: ignore[arg-type]
    with pytest.raises(TrafficlabError, match="gene bounds"):
        family_coordinates("poisson_empirical", MARKOV_BOUNDS)


@pytest.mark.parametrize(
    ("coordinate", "value", "message"),
    [
        (cast(object, "not-coordinate"), 0.5, "GeneCoordinate"),
        (GeneCoordinate("bad", cast(Any, "bad"), FloatBounds(lower=1.0, upper=2.0)), 1.5, "kind"),
        (GeneCoordinate("integer", "integer", FloatBounds(lower=1.0, upper=2.0)), 1.5, "integer bounds"),
        (GeneCoordinate("linear", "linear", IntegerBounds(lower=1, upper=2)), 1.5, "float bounds"),
        (GeneCoordinate("integer", "integer", IntegerBounds(lower=1, upper=2)), 1.5, "integer gene"),
        (GeneCoordinate("linear", "linear", FloatBounds(lower=1.0, upper=2.0)), 3.0, "within"),
    ],
)
def test_encode_rejects_invalid_coordinate_metadata_and_gene_values(
    coordinate: object, value: object, message: str
) -> None:
    """Invalid coordinate domains must not turn arbitrary values into genes."""
    with pytest.raises((TypeError, TrafficlabError), match=message):
        encode_gene(cast(GeneCoordinate, coordinate), cast(float | int, value))


@pytest.mark.parametrize("value", [-0.1, 1.1, math.nan])
def test_decode_rejects_coordinates_outside_the_closed_unit_interval(value: float) -> None:
    """Mutation must reflect before decoding, never decode an out-of-domain coordinate."""
    with pytest.raises(TrafficlabError, match="normalized coordinate"):
        decode_gene(GeneCoordinate("value", "linear", FloatBounds(lower=1.0, upper=2.0)), value)


@pytest.mark.parametrize("probability", [-0.1, 1.1, math.nan])
def test_bernoulli_rejects_invalid_probability_without_consuming_a_draw(probability: float) -> None:
    """Bad operator configuration cannot desynchronize the dedicated master RNG."""
    rng = ScriptedRandom(random_values=[0.5])

    with pytest.raises(TrafficlabError, match="Bernoulli"):
        bernoulli(cast(GeneticRng, rng), probability)
    assert rng.calls == []
