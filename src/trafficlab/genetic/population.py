"""Deterministic construction, ranking, selection, and retention of GA populations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from random import Random

from trafficlab.config import FamilyName
from trafficlab.genetic.coordinates import CandidateEvaluationError, initialize_candidate
from trafficlab.genetic.types import Candidate, CandidateFailure, CandidateId
from trafficlab.models.common import FamilyBounds
from trafficlab.models.registry import get_family
from trafficlab.trace import TraceEvent


def _lexical_families(families: Sequence[FamilyName]) -> tuple[FamilyName, ...]:
    names = tuple(families)
    if not names:
        raise ValueError("at least one enabled family is required")
    if len(names) != len(set(names)):
        raise ValueError("enabled families must be unique")
    for name in names:
        get_family(name)
    return tuple(sorted(names))


def family_quotas(families: Sequence[FamilyName], population_size: int) -> dict[FamilyName, int]:
    """Allocate equal family quotas and assign remainders in lexical family order."""
    names = _lexical_families(families)
    if type(population_size) is not int or population_size < len(names):
        raise ValueError("population size must be an exact integer covering every enabled family")
    base, remainder = divmod(population_size, len(names))
    return {name: base + (index < remainder) for index, name in enumerate(names)}


def initial_population(
    families: Sequence[FamilyName],
    *,
    population_size: int,
    bounds: Mapping[FamilyName, FamilyBounds],
    reference: Sequence[TraceEvent],
    rng: Random,
) -> tuple[Candidate, ...]:
    """Initialize contiguous lexical family slots with stable generation-zero IDs."""
    quotas = family_quotas(families, population_size)
    if set(bounds) != set(quotas):
        raise ValueError("family bounds must exactly match the enabled families")
    materialized_reference = tuple(reference)
    population: list[Candidate] = []
    for family_name, quota in quotas.items():
        family = get_family(family_name)
        family_bounds = bounds[family_name]
        for _ in range(quota):
            identifier = CandidateId(0, len(population))
            try:
                genes = initialize_candidate(family, family_bounds, materialized_reference, rng)
            except CandidateEvaluationError as error:
                population.append(
                    Candidate(
                        identifier,
                        family_name,
                        None,
                        "invalid",
                        0.0,
                        (),
                        CandidateFailure(error.kind, error.seed, error.detail),
                        (),
                    )
                )
            else:
                population.append(Candidate(identifier, family_name, genes, "pending", 0.0, (), None, ()))
    return tuple(population)


def _evaluated(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    values = tuple(candidates)
    if not values:
        raise ValueError("population must not be empty")
    if any(candidate.status == "pending" for candidate in values):
        raise ValueError("all candidates must be evaluated before ranking or selection")
    return values


def rank_candidates(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    """Rank an evaluated population by descending fitness then stable candidate ID."""
    values = _evaluated(candidates)
    return tuple(sorted(values, key=lambda item: (-item.fitness, item.identifier)))


def tournament_select(candidates: Sequence[Candidate], *, tournament_size: int, rng: Random) -> Candidate:
    """Sample with replacement and return the stable best tournament member."""
    values = _evaluated(candidates)
    if type(tournament_size) is not int or not 2 <= tournament_size <= len(values):
        raise ValueError("tournament size must be an exact integer within the population")
    selected = tuple(values[rng.randrange(len(values))] for _ in range(tournament_size))
    return rank_candidates(selected)[0]


def global_elites(candidates: Sequence[Candidate], *, elite_count: int) -> tuple[Candidate, ...]:
    """Return the configured leading global candidates without changing their IDs."""
    ranked = rank_candidates(candidates)
    if type(elite_count) is not int or not 1 <= elite_count < len(ranked):
        raise ValueError("elite count must be an exact positive integer below population size")
    return ranked[:elite_count]


def family_champions(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    """Return the best evaluated candidate of each represented family in lexical order."""
    ranked = rank_candidates(candidates)
    return tuple(
        next(candidate for candidate in ranked if candidate.family == family)
        for family in sorted({candidate.family for candidate in ranked})
    )


def retained_population(candidates: Sequence[Candidate], *, elite_count: int) -> tuple[Candidate, ...]:
    """Return global elites followed by only the missing lexical family champions."""
    values = tuple(candidates)
    family_count = len({candidate.family for candidate in values})
    if len(values) < elite_count + family_count:
        raise ValueError("population size must include elites and each enabled family")
    retained = list(global_elites(values, elite_count=elite_count))
    retained_ids = {candidate.identifier for candidate in retained}
    retained.extend(champion for champion in family_champions(values) if champion.identifier not in retained_ids)
    return tuple(retained)
