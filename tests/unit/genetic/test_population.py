"""Tests for deterministic genetic population construction and selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import cast

import pytest

from trafficlab.config import FamilyName, FloatBounds, IntegerBounds, MarkovRenewalConfig, MmppConfig, PoissonConfig
from trafficlab.genetic.population import (
    family_champions,
    family_quotas,
    global_elites,
    initial_population,
    rank_candidates,
    retained_population,
    tournament_select,
)
from trafficlab.genetic.types import Candidate, CandidateId, CandidateStatus
from trafficlab.models.common import FamilyBounds
from trafficlab.trace import Direction, TraceEvent


@dataclass
class ScriptedRandom:
    """Record population RNG calls while returning literal scripted values."""

    random_values: list[float] = field(default_factory=list[float])
    ranges: list[int] = field(default_factory=list[int])
    calls: list[tuple[object, ...]] = field(default_factory=list[tuple[object, ...]])

    def random(self) -> float:
        self.calls.append(("random",))
        return self.random_values.pop(0)

    def randrange(self, start: int, stop: int | None = None) -> int:
        self.calls.append(("randrange", start, stop))
        return self.ranges.pop(0)


REFERENCE = (
    TraceEvent(0.0, Direction.OUTBOUND, 64),
    TraceEvent(1.0, Direction.INBOUND, 128),
    TraceEvent(2.0, Direction.OUTBOUND, 256),
)
POISSON = PoissonConfig(c_lambda=FloatBounds(lower=0.5, upper=2.0))
MARKOV = MarkovRenewalConfig(
    q1=FloatBounds(lower=0.1, upper=0.4),
    q2=FloatBounds(lower=0.6, upper=0.9),
    alpha=FloatBounds(lower=0.0, upper=2.0),
    r=IntegerBounds(lower=1, upper=5),
    c_t=FloatBounds(lower=0.5, upper=2.0),
)
MMPP = MmppConfig(
    q01=FloatBounds(lower=0.1, upper=3.0),
    q10=FloatBounds(lower=0.1, upper=3.0),
    lambda0=FloatBounds(lower=0.1, upper=1.0),
    lambda1=FloatBounds(lower=2.0, upper=5.0),
)
BOUNDS: dict[FamilyName, FamilyBounds] = {
    "poisson_empirical": POISSON,
    "markov_renewal": MARKOV,
    "mmpp": MMPP,
}


def candidate(index: int, family: FamilyName, fitness: float, *, status: CandidateStatus = "valid") -> Candidate:
    """Build a literal evaluated candidate for pure ordering examples."""
    return Candidate(
        CandidateId(0, index),
        family,
        (float(index + 1),),
        status,
        fitness,
        (),
        None,
        (),
    )


POPULATION = (
    candidate(0, "markov_renewal", 0.8),
    candidate(1, "mmpp", 0.9),
    candidate(2, "poisson_empirical", 0.6),
    candidate(3, "markov_renewal", 0.1),
    candidate(4, "poisson_empirical", 0.7),
)


def test_quotas_assign_remainder_in_lexical_family_order() -> None:
    """Input order must not decide which competing family receives a remainder slot."""
    assert family_quotas(("poisson_empirical", "mmpp", "markov_renewal"), 8) == {
        "markov_renewal": 3,
        "mmpp": 3,
        "poisson_empirical": 2,
    }


@pytest.mark.parametrize(
    ("families", "population_size", "message"),
    [
        ((), 1, "at least one"),
        (("mmpp", "mmpp"), 2, "unique"),
        (("mmpp", "poisson_empirical"), 1, "covering"),
    ],
)
def test_quotas_reject_invalid_family_sets_and_population_capacity(
    families: object, population_size: int, message: str
) -> None:
    """Malformed quota inputs cannot create missing or multiply represented family slots."""
    with pytest.raises(ValueError, match=message):
        family_quotas(cast("tuple[FamilyName, ...]", families), population_size)


def test_initial_population_uses_contiguous_lexical_slots_and_stable_ids() -> None:
    """A nonlexical enabled-family tuple must not reorder initial IDs or initializer draws."""
    rng = ScriptedRandom(
        random_values=[0.0] * 8 + [0.25] * 4 + [0.5],
        ranges=[1, 2],
    )

    population = initial_population(
        ("poisson_empirical", "mmpp", "markov_renewal"),
        population_size=4,
        bounds=BOUNDS,
        reference=REFERENCE,
        rng=cast(Random, rng),
    )

    assert tuple(item.family for item in population) == (
        "markov_renewal",
        "markov_renewal",
        "mmpp",
        "poisson_empirical",
    )
    assert tuple(item.identifier for item in population) == tuple(CandidateId(0, index) for index in range(4))
    assert all(item.status == "pending" and item.fitness == 0.0 for item in population)
    assert rng.calls == [
        ("random",),
        ("random",),
        ("random",),
        ("randrange", 1, 6),
        ("random",),
        ("random",),
        ("random",),
        ("random",),
        ("randrange", 1, 6),
        ("random",),
        ("random",),
        ("random",),
        ("random",),
        ("random",),
        ("random",),
    ]


def test_initial_population_classifies_registered_initializer_repair_failure() -> None:
    """A registered mathematical initialization failure remains an invalid fixed population slot."""
    invalid_reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 64),
        TraceEvent(1.0, Direction.INBOUND, 64),
    )
    rng = ScriptedRandom(random_values=[0.0] * 4, ranges=[1])

    population = initial_population(
        ("markov_renewal",),
        population_size=1,
        bounds={"markov_renewal": MARKOV},
        reference=invalid_reference,
        rng=cast(Random, rng),
    )

    assert population[0].status == "invalid"
    assert population[0].genes is None
    assert population[0].invalid is not None
    assert (population[0].invalid.kind, population[0].invalid.seed) == ("repair", None)


def test_initial_population_requires_exact_enabled_bounds() -> None:
    """A missing family table must fail before any initializer draw."""
    rng = ScriptedRandom(random_values=[0.5])
    with pytest.raises(ValueError, match="exactly match"):
        initial_population(
            ("poisson_empirical",),
            population_size=1,
            bounds={},
            reference=REFERENCE,
            rng=cast(Random, rng),
        )
    assert rng.calls == []


def test_ranking_global_elites_and_missing_champions_are_stable() -> None:
    """Fitness ties and family retention must use IDs and lexical family order, never input accidents."""
    tied = POPULATION + (candidate(5, "mmpp", 0.9),)

    assert tuple(item.identifier for item in rank_candidates(tied)[:3]) == (
        CandidateId(0, 1),
        CandidateId(0, 5),
        CandidateId(0, 0),
    )
    assert tuple(item.identifier for item in global_elites(POPULATION, elite_count=2)) == (
        CandidateId(0, 1),
        CandidateId(0, 0),
    )
    assert tuple(item.identifier for item in family_champions(POPULATION)) == (
        CandidateId(0, 0),
        CandidateId(0, 1),
        CandidateId(0, 4),
    )
    assert tuple(item.identifier for item in retained_population(POPULATION, elite_count=2)) == (
        CandidateId(0, 1),
        CandidateId(0, 0),
        CandidateId(0, 4),
    )


def test_tournament_uses_replacement_and_stable_id_for_equal_fitness() -> None:
    """Sampling without replacement or retaining sample order would choose the wrong tied parent."""
    tied = (
        candidate(0, "markov_renewal", 0.8),
        candidate(1, "mmpp", 0.8),
        candidate(2, "poisson_empirical", 0.2),
    )
    rng = ScriptedRandom(ranges=[1, 0, 1])

    assert tournament_select(tied, tournament_size=3, rng=cast(Random, rng)).identifier == CandidateId(0, 0)
    assert rng.calls == [("randrange", 3, None), ("randrange", 3, None), ("randrange", 3, None)]


def test_pending_candidates_cannot_enter_ranking_or_selection() -> None:
    """Selecting an unevaluated candidate would violate the complete-generation boundary."""
    pending = (candidate(0, "poisson_empirical", 0.0, status="pending"),)

    with pytest.raises(ValueError, match="evaluated"):
        rank_candidates(pending)
    with pytest.raises(ValueError, match="evaluated"):
        tournament_select(pending, tournament_size=1, rng=Random(1))


def test_empty_population_and_invalid_elite_count_are_rejected() -> None:
    """Ranking and retention need at least one evaluated candidate and a real elite slot."""
    with pytest.raises(ValueError, match="must not be empty"):
        rank_candidates(())
    with pytest.raises(ValueError, match="elite count"):
        global_elites(POPULATION, elite_count=0)


@pytest.mark.parametrize("tournament_size", [1, len(POPULATION) + 1, True])
def test_tournament_requires_architecture_bounds_and_exact_integer(tournament_size: object) -> None:
    """A one-candidate tournament would silently violate the configured selection contract."""
    with pytest.raises(ValueError, match="tournament size"):
        tournament_select(POPULATION, tournament_size=cast(int, tournament_size), rng=Random(1))


def test_retention_validates_reserved_population_capacity() -> None:
    """Elites plus one champion per family must fit before any child slots are allocated."""
    with pytest.raises(ValueError, match="elites and each enabled family"):
        retained_population(POPULATION, elite_count=3)
