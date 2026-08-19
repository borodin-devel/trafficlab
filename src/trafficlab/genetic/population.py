"""Deterministic construction, ranking, selection, and retention of GA populations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from random import Random
from typing import cast

from trafficlab.config import FamilyName
from trafficlab.errors import TrafficlabError
from trafficlab.genetic.coordinates import CandidateEvaluationError, initialize_candidate
from trafficlab.genetic.types import Candidate, CandidateFailure, CandidateId, FamilyPriority
from trafficlab.models.common import FamilyBounds
from trafficlab.models.registry import get_family
from trafficlab.trace import TraceEvent


def validate_family_priority(
    family_priority: FamilyPriority,
    *,
    enabled_families: Iterable[str] | None = None,
) -> FamilyPriority:
    """Return one immutable priority containing each known enabled family exactly once."""
    if type(family_priority) is not tuple:
        raise TypeError("family priority must be an immutable tuple")
    names = family_priority
    if not names:
        raise ValueError("family priority requires at least one enabled family")
    if len(names) != len(set(names)):
        raise ValueError("family priority families must be unique")
    for name in names:
        if type(name) is not str:
            raise ValueError("family priority names must be registered families")
        try:
            get_family(name)
        except TrafficlabError as error:
            raise ValueError("family priority names must be registered families") from error
    if enabled_families is not None and set(names) != set(enabled_families):
        raise ValueError("family priority must contain every enabled family exactly once")
    return names


def derive_family_priority(master_seed: int, family_names: Iterable[FamilyName]) -> FamilyPriority:
    """Derive the one neutral family order without touching the dedicated search RNG."""
    if type(master_seed) is not int or master_seed < 0:
        raise ValueError("master seed must be a nonnegative exact integer")
    names = validate_family_priority(tuple(family_names))
    return tuple(Random(master_seed).sample(sorted(names), len(names)))


def family_quotas(population_size: int, family_priority: FamilyPriority) -> dict[FamilyName, int]:
    """Allocate equal family quotas and assign remainders in retained priority order."""
    names = validate_family_priority(family_priority)
    if type(population_size) is not int or population_size < len(names):
        raise ValueError("population size must be an exact integer covering every enabled family")
    base, remainder = divmod(population_size, len(names))
    return {cast(FamilyName, name): base + (index < remainder) for index, name in enumerate(names)}


def initial_population(
    family_priority: FamilyPriority,
    *,
    population_size: int,
    bounds: Mapping[FamilyName, FamilyBounds],
    reference: Sequence[TraceEvent],
    rng: Random,
) -> tuple[Candidate, ...]:
    """Initialize contiguous priority family slots with stable generation-zero IDs."""
    priority = validate_family_priority(family_priority, enabled_families=bounds)
    quotas = family_quotas(population_size, priority)
    materialized_reference = tuple(reference)
    population: list[Candidate] = []
    for family_name, quota in quotas.items():
        family = get_family(family_name)
        family_bounds = bounds[family_name]
        for _ in range(quota):
            identifier = CandidateId(birth_generation=0, birth_index=len(population))
            try:
                genes = initialize_candidate(family, family_bounds, materialized_reference, rng)
            except CandidateEvaluationError as error:
                population.append(
                    Candidate(
                        identifier=identifier,
                        family=family_name,
                        genes=None,
                        status="invalid",
                        fitness=0.0,
                        trials=(),
                        invalid=CandidateFailure(
                            kind=error.kind,
                            seed=error.seed,
                            detail=error.detail,
                            stage=error.stage,
                            affected_evidence=error.affected_evidence,
                            evidence_state=error.evidence_state,
                            corrective_action=error.corrective_action,
                            authority=error.authority,
                        ),
                        duplicate_diagnostics=(),
                    )
                )
            else:
                population.append(
                    Candidate(
                        identifier=identifier,
                        family=family_name,
                        genes=genes,
                        status="pending",
                        fitness=0.0,
                        trials=(),
                        invalid=None,
                        duplicate_diagnostics=(),
                    )
                )
    return tuple(population)


def _evaluated(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    values = tuple(candidates)
    if not values:
        raise ValueError("population must not be empty")
    if any(candidate.status == "pending" for candidate in values):
        raise ValueError("all candidates must be evaluated before ranking or selection")
    return values


def priority_rank_key(
    fitness: float,
    family: FamilyName,
    identifier: CandidateId,
    *,
    family_priority: FamilyPriority,
) -> tuple[float, int, CandidateId]:
    """Return the one scientific competition key for candidate-like records."""
    priority = validate_family_priority(family_priority)
    return (-fitness, priority.index(family), identifier)


def rank_candidates(candidates: Sequence[Candidate], *, family_priority: FamilyPriority) -> tuple[Candidate, ...]:
    """Rank by fitness, priority across families, and stable ID inside one family."""
    values = _evaluated(candidates)
    priority = validate_family_priority(family_priority, enabled_families=(candidate.family for candidate in values))
    return tuple(
        sorted(
            values,
            key=lambda item: priority_rank_key(
                item.fitness,
                item.family,
                item.identifier,
                family_priority=priority,
            ),
        )
    )


def tournament_select(
    candidates: Sequence[Candidate],
    *,
    tournament_size: int,
    rng: Random,
    family_priority: FamilyPriority,
) -> Candidate:
    """Sample with replacement and return the priority-ranked tournament member."""
    values = _evaluated(candidates)
    if type(tournament_size) is not int or not 2 <= tournament_size <= len(values):
        raise ValueError("tournament size must be an exact integer within the population")
    priority = validate_family_priority(family_priority, enabled_families=(candidate.family for candidate in values))
    selected = tuple(values[rng.randrange(len(values))] for _ in range(tournament_size))
    return min(
        selected,
        key=lambda item: priority_rank_key(
            item.fitness,
            item.family,
            item.identifier,
            family_priority=priority,
        ),
    )


def global_elites(
    candidates: Sequence[Candidate], *, elite_count: int, family_priority: FamilyPriority
) -> tuple[Candidate, ...]:
    """Return the configured leading global candidates without changing their IDs."""
    ranked = rank_candidates(candidates, family_priority=family_priority)
    if type(elite_count) is not int or not 1 <= elite_count < len(ranked):
        raise ValueError("elite count must be an exact positive integer below population size")
    return ranked[:elite_count]


def family_champions(candidates: Sequence[Candidate], *, family_priority: FamilyPriority) -> tuple[Candidate, ...]:
    """Return the best evaluated candidate of each represented family in priority order."""
    ranked = rank_candidates(candidates, family_priority=family_priority)
    priority = validate_family_priority(family_priority, enabled_families=(candidate.family for candidate in ranked))
    return tuple(next(candidate for candidate in ranked if candidate.family == family) for family in priority)


def retained_population(
    candidates: Sequence[Candidate], *, elite_count: int, family_priority: FamilyPriority
) -> tuple[Candidate, ...]:
    """Return global elites followed by only missing priority-ordered family champions."""
    values = tuple(candidates)
    priority = validate_family_priority(family_priority, enabled_families=(candidate.family for candidate in values))
    family_count = len({candidate.family for candidate in values})
    if len(values) < elite_count + family_count:
        raise ValueError("population size must include elites and each enabled family")
    retained = list(global_elites(values, elite_count=elite_count, family_priority=priority))
    retained_ids = {candidate.identifier for candidate in retained}
    retained.extend(
        champion
        for champion in family_champions(values, family_priority=priority)
        if champion.identifier not in retained_ids
    )
    return tuple(retained)
