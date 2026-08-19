"""Tests for deterministic genetic population construction and selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import cast

import pytest

from trafficlab.config import FamilyName, FloatBounds, IntegerBounds, MarkovRenewalConfig, MmppConfig, PoissonConfig
from trafficlab.genetic import population as population_module
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
        identifier=CandidateId(birth_generation=0, birth_index=index),
        family=family,
        genes=(float(index + 1),),
        status=status,
        fitness=fitness,
        trials=(),
        invalid=None,
        duplicate_diagnostics=(),
    )


POPULATION = (
    candidate(0, "markov_renewal", 0.8),
    candidate(1, "mmpp", 0.9),
    candidate(2, "poisson_empirical", 0.6),
    candidate(3, "markov_renewal", 0.1),
    candidate(4, "poisson_empirical", 0.7),
)


@pytest.mark.parametrize(
    ("master_seed", "expected_priority"),
    [
        (4, ("markov_renewal", "mmpp", "poisson_empirical")),
        (0, ("mmpp", "poisson_empirical", "markov_renewal")),
        (6, ("poisson_empirical", "markov_renewal", "mmpp")),
    ],
)
def test_derive_family_priority_is_seeded_permutation_invariant_and_separate_from_search_rng(
    master_seed: int, expected_priority: tuple[FamilyName, ...]
) -> None:
    """A temporary priority sampler must not privilege input order or advance search draws."""
    names = ("poisson_empirical", "mmpp", "markov_renewal")
    expected = tuple(Random(master_seed).sample(sorted(names), len(names)))
    search_rng = Random(master_seed)

    assert population_module.derive_family_priority(master_seed, names) == expected_priority == expected
    assert population_module.derive_family_priority(master_seed, tuple(reversed(names))) == expected_priority
    assert search_rng.random() == Random(master_seed).random()


def test_quotas_assign_remainder_in_family_priority_order() -> None:
    """The priority leader, rather than lexical spelling, receives the first remainder slot."""
    priority = ("poisson_empirical", "markov_renewal", "mmpp")
    assert family_quotas(8, priority) == {
        "poisson_empirical": 3,
        "markov_renewal": 3,
        "mmpp": 2,
    }


@pytest.mark.parametrize(
    ("family_priority", "population_size", "message"),
    [
        ((), 1, "at least one"),
        (("mmpp", "mmpp"), 2, "unique"),
        (("mmpp", "foreign_family"), 2, "registered"),
    ],
)
def test_quotas_reject_invalid_priorities_and_population_capacity(
    family_priority: object, population_size: int, message: str
) -> None:
    """Malformed priority inputs cannot create missing or multiply represented family slots."""
    with pytest.raises(ValueError, match=message):
        family_quotas(population_size, cast("tuple[FamilyName, ...]", family_priority))


def test_priority_helpers_reject_mutable_nonstring_invalid_seed_and_insufficient_population() -> None:
    """Priority validation rejects malformed inputs before it can decide any family slot or tie."""
    with pytest.raises(TypeError, match="immutable"):
        family_quotas(2, cast("tuple[FamilyName, ...]", ["mmpp", "poisson_empirical"]))
    with pytest.raises(ValueError, match="registered"):
        family_quotas(2, cast("tuple[FamilyName, ...]", ("mmpp", 7)))
    with pytest.raises(ValueError, match="master seed"):
        population_module.derive_family_priority(-1, ("mmpp",))
    with pytest.raises(ValueError, match="population size"):
        family_quotas(1, ("mmpp", "poisson_empirical"))


def test_initial_population_uses_contiguous_priority_slots_and_stable_ids() -> None:
    """Priority order fixes remainder recipients, initial family slots, and their creation IDs."""
    priority = ("poisson_empirical", "markov_renewal", "mmpp")

    population = initial_population(
        priority,
        population_size=4,
        bounds=BOUNDS,
        reference=REFERENCE,
        rng=Random(17),
    )

    assert tuple(item.family for item in population) == (
        "poisson_empirical",
        "poisson_empirical",
        "markov_renewal",
        "mmpp",
    )
    assert tuple(item.identifier for item in population) == tuple(
        CandidateId(birth_generation=0, birth_index=index) for index in range(4)
    )
    assert all(item.status == "pending" and item.fitness == 0.0 for item in population)


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
    with pytest.raises(ValueError, match="family priority"):
        initial_population(
            ("poisson_empirical",),
            population_size=1,
            bounds={},
            reference=REFERENCE,
            rng=cast(Random, rng),
        )
    assert rng.calls == []


def test_ranking_retention_and_champions_use_priority_across_families_and_ids_within_one_family() -> None:
    """Cross-family ties follow priority while same-family ties retain the smaller stable ID."""
    priority = ("poisson_empirical", "markov_renewal", "mmpp")
    tied = POPULATION + (
        candidate(5, "poisson_empirical", 0.9),
        candidate(6, "markov_renewal", 0.9),
        candidate(7, "poisson_empirical", 0.9),
    )

    assert tuple(item.identifier for item in rank_candidates(tied, family_priority=priority)[:5]) == (
        CandidateId(birth_generation=0, birth_index=5),
        CandidateId(birth_generation=0, birth_index=7),
        CandidateId(birth_generation=0, birth_index=6),
        CandidateId(birth_generation=0, birth_index=1),
        CandidateId(birth_generation=0, birth_index=0),
    )
    assert tuple(item.identifier for item in global_elites(tied, elite_count=2, family_priority=priority)) == (
        CandidateId(birth_generation=0, birth_index=5),
        CandidateId(birth_generation=0, birth_index=7),
    )
    assert tuple(item.identifier for item in family_champions(tied, family_priority=priority)) == (
        CandidateId(birth_generation=0, birth_index=5),
        CandidateId(birth_generation=0, birth_index=6),
        CandidateId(birth_generation=0, birth_index=1),
    )
    assert tuple(item.identifier for item in retained_population(tied, elite_count=2, family_priority=priority)) == (
        CandidateId(birth_generation=0, birth_index=5),
        CandidateId(birth_generation=0, birth_index=7),
        CandidateId(birth_generation=0, birth_index=6),
        CandidateId(birth_generation=0, birth_index=1),
    )

    symmetric_invalids = (
        candidate(8, "markov_renewal", 0.0, status="invalid"),
        candidate(9, "mmpp", 0.0, status="invalid"),
        candidate(10, "poisson_empirical", 0.0, status="invalid"),
    )
    assert tuple(
        item.identifier
        for item in rank_candidates(
            symmetric_invalids,
            family_priority=("poisson_empirical", "mmpp", "markov_renewal"),
        )
    ) == (
        CandidateId(birth_generation=0, birth_index=10),
        CandidateId(birth_generation=0, birth_index=9),
        CandidateId(birth_generation=0, birth_index=8),
    )


def test_tournament_uses_replacement_and_family_priority_for_cross_family_ties() -> None:
    """Sampling with replacement must still apply the retained cross-family competition rule."""
    tied = (
        candidate(0, "markov_renewal", 0.8),
        candidate(1, "mmpp", 0.8),
        candidate(2, "poisson_empirical", 0.2),
    )
    rng = ScriptedRandom(ranges=[1, 0, 1])

    assert tournament_select(
        tied,
        tournament_size=3,
        rng=cast(Random, rng),
        family_priority=("mmpp", "markov_renewal", "poisson_empirical"),
    ).identifier == CandidateId(birth_generation=0, birth_index=1)
    assert rng.calls == [("randrange", 3, None), ("randrange", 3, None), ("randrange", 3, None)]


def test_priority_validation_rejects_duplicate_missing_and_foreign_names() -> None:
    """Every comparison boundary knows and validates the complete enabled-family priority."""
    for priority in (
        ("mmpp", "mmpp", "poisson_empirical"),
        ("mmpp", "poisson_empirical"),
        ("mmpp", "markov_renewal", "foreign_family"),
    ):
        with pytest.raises(ValueError, match="priority"):
            rank_candidates(POPULATION, family_priority=cast("tuple[FamilyName, ...]", priority))


def test_pending_candidates_cannot_enter_ranking_or_selection() -> None:
    """Selecting an unevaluated candidate would violate the complete-generation boundary."""
    pending = (candidate(0, "poisson_empirical", 0.0, status="pending"),)

    with pytest.raises(ValueError, match="evaluated"):
        rank_candidates(pending, family_priority=("poisson_empirical",))
    with pytest.raises(ValueError, match="evaluated"):
        tournament_select(
            pending,
            tournament_size=1,
            rng=Random(1),
            family_priority=("poisson_empirical",),
        )


def test_empty_population_and_invalid_elite_count_are_rejected() -> None:
    """Ranking and retention need at least one evaluated candidate and a real elite slot."""
    with pytest.raises(ValueError, match="must not be empty"):
        rank_candidates((), family_priority=())
    with pytest.raises(ValueError, match="elite count"):
        global_elites(
            POPULATION,
            elite_count=0,
            family_priority=("markov_renewal", "mmpp", "poisson_empirical"),
        )


@pytest.mark.parametrize("tournament_size", [1, len(POPULATION) + 1, True])
def test_tournament_requires_architecture_bounds_and_exact_integer(tournament_size: object) -> None:
    """A one-candidate tournament would silently violate the configured selection contract."""
    with pytest.raises(ValueError, match="tournament size"):
        tournament_select(
            POPULATION,
            tournament_size=cast(int, tournament_size),
            rng=Random(1),
            family_priority=("markov_renewal", "mmpp", "poisson_empirical"),
        )


def test_retention_validates_reserved_population_capacity() -> None:
    """Elites plus one champion per family must fit before any child slots are allocated."""
    with pytest.raises(ValueError, match="elites and each enabled family"):
        retained_population(
            POPULATION,
            elite_count=3,
            family_priority=("markov_renewal", "mmpp", "poisson_empirical"),
        )
