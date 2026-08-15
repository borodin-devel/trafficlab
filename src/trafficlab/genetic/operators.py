"""Exact reproduction, repair, mutation, and bounded duplicate handling."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from random import Random
from types import MappingProxyType
from typing import cast

from trafficlab.config import FamilyName, FamilyOperators, IntegerBounds
from trafficlab.errors import TrafficlabError
from trafficlab.genetic.coordinates import (
    CandidateEvaluationError,
    GeneCoordinate,
    bernoulli,
    family_coordinates,
    mutate_coordinate,
)
from trafficlab.genetic.population import retained_population, tournament_select
from trafficlab.genetic.types import Candidate, CandidateFailure, CandidateId, DuplicateDiagnostic
from trafficlab.models.common import FamilyBounds, Gene, Genes
from trafficlab.models.registry import get_family
from trafficlab.trace import TraceEvent


@dataclass(frozen=True, slots=True, init=False)
class ReproductionContext:
    """Immutable family settings, reference, retry bound, and duplicate comparison set."""

    reference: tuple[TraceEvent, ...]
    family_bounds: Mapping[FamilyName, FamilyBounds]
    duplicate_mutation_attempts: int
    existing_candidates: tuple[Candidate, ...]

    def __init__(
        self,
        *,
        reference: Sequence[TraceEvent],
        family_bounds: Mapping[FamilyName, FamilyBounds],
        duplicate_mutation_attempts: int,
        existing_candidates: Sequence[Candidate] = (),
    ) -> None:
        if type(duplicate_mutation_attempts) is not int or duplicate_mutation_attempts < 0:
            raise ValueError("duplicate mutation attempts must be a nonnegative exact integer")
        copied_bounds = dict(family_bounds)
        if not copied_bounds:
            raise ValueError("reproduction requires at least one enabled family")
        for name, bounds in copied_bounds.items():
            family = get_family(name)
            if type(bounds) is not family.bounds_type:
                raise ValueError(f"invalid {name} reproduction bounds")
        candidates = tuple(existing_candidates)
        if any(type(candidate) is not Candidate for candidate in candidates):
            raise TypeError("existing candidates must be Candidate values")
        object.__setattr__(self, "reference", tuple(reference))
        object.__setattr__(self, "family_bounds", MappingProxyType(copied_bounds))
        object.__setattr__(self, "duplicate_mutation_attempts", duplicate_mutation_attempts)
        object.__setattr__(self, "existing_candidates", candidates)

    def bounds_for(self, family: FamilyName) -> FamilyBounds:
        """Return the exact registered bounds and operators for one enabled child family."""
        try:
            return self.family_bounds[family]
        except KeyError as error:
            raise ValueError(f"missing reproduction settings for {family}") from error

    def operators_for(self, family: FamilyName) -> FamilyOperators:
        """Return the operator projection shared by every configured family table."""
        return cast(FamilyOperators, self.bounds_for(family))

    def with_existing(self, candidates: Sequence[Candidate]) -> ReproductionContext:
        """Copy this context with the current survivors and accepted children as duplicate identities."""
        return ReproductionContext(
            reference=self.reference,
            family_bounds=self.family_bounds,
            duplicate_mutation_attempts=self.duplicate_mutation_attempts,
            existing_candidates=candidates,
        )


def _fitter(parent_a: Candidate, parent_b: Candidate) -> Candidate:
    if parent_a.status == "pending" or parent_b.status == "pending":
        raise ValueError("parents must be evaluated before reproduction")
    return min((parent_a, parent_b), key=lambda candidate: (-candidate.fitness, candidate.identifier))


def _parent_genes(candidate: Candidate, *, coordinate_count: int) -> Genes:
    if candidate.genes is None or len(candidate.genes) != coordinate_count:
        raise ValueError("selected parent must have one repaired gene per family coordinate")
    return candidate.genes


def _missing_parent_genes(identifier: CandidateId, family: FamilyName) -> Candidate:
    return Candidate(
        identifier,
        family,
        None,
        "invalid",
        0.0,
        (),
        CandidateFailure(
            "repair",
            None,
            "selected parent has no canonical genes",
            stage="fit",
            affected_evidence="candidate genes",
            evidence_state="diagnostic_only",
            corrective_action="select a parent with canonical genes",
            authority="primary",
        ),
        (),
    )


def _mandatory_integer_step(coordinate: GeneCoordinate, starting: Gene, epsilon: float, mutated: Gene) -> Gene:
    if coordinate.kind != "integer" or mutated != starting:
        return mutated
    bounds = cast(IntegerBounds, coordinate.bounds)
    value = cast(int, starting) + (1 if epsilon >= 0.0 else -1)
    if value > bounds.upper:
        return bounds.upper - 1
    if value < bounds.lower:
        return bounds.lower + 1
    return value


def _mutate_genes(
    genes: Genes,
    *,
    family: FamilyName,
    context: ReproductionContext,
    rng: Random,
    force_if_none: bool,
    mandatory_integers: bool,
) -> Genes:
    coordinates = family_coordinates(family, context.bounds_for(family))
    if len(genes) != len(coordinates):
        raise ValueError("genes must match the published family chromosome length")
    operators = context.operators_for(family)
    selected = tuple(bernoulli(rng, operators.mutation_probability) for _ in coordinates)
    values = list(genes)
    for index, is_selected in enumerate(selected):
        if not is_selected:
            continue
        epsilon = rng.normalvariate(0.0, operators.mutation_scale)
        mutated = mutate_coordinate(coordinates[index], values[index], epsilon)
        values[index] = (
            _mandatory_integer_step(coordinates[index], values[index], epsilon, mutated)
            if mandatory_integers
            else mutated
        )
    if force_if_none and not any(selected):
        index = rng.randrange(len(coordinates))
        epsilon = rng.normalvariate(0.0, operators.mutation_scale)
        mutated = mutate_coordinate(coordinates[index], values[index], epsilon)
        values[index] = _mandatory_integer_step(coordinates[index], values[index], epsilon, mutated)
    return tuple(values)


def _repair(family: FamilyName, genes: Genes, *, context: ReproductionContext) -> Genes:
    registered = get_family(family)
    try:
        return registered.repair(genes, context.bounds_for(family), context.reference)
    except TrafficlabError as error:
        raise CandidateEvaluationError(
            "repair",
            None,
            str(error),
            stage="fit",
            affected_evidence="candidate genes",
            evidence_state="diagnostic_only",
            corrective_action=error.corrective_action,
            authority="primary",
        ) from error


def _pending_or_invalid(
    family: FamilyName,
    genes: Genes,
    *,
    identifier: CandidateId,
    context: ReproductionContext,
    diagnostics: tuple[DuplicateDiagnostic, ...] = (),
) -> Candidate:
    try:
        repaired = _repair(family, genes, context=context)
    except CandidateEvaluationError as error:
        return Candidate(
            identifier,
            family,
            None,
            "invalid",
            0.0,
            (),
            CandidateFailure(
                error.kind,
                error.seed,
                error.detail,
                stage=error.stage,
                affected_evidence=error.affected_evidence,
                evidence_state=error.evidence_state,
                corrective_action=error.corrective_action,
                authority=error.authority,
            ),
            diagnostics,
        )
    return Candidate(identifier, family, repaired, "pending", 0.0, (), None, diagnostics)


def _is_population_duplicate(candidate: Candidate, existing: Sequence[Candidate]) -> bool:
    return any(
        other.genes is not None and other.family == candidate.family and other.genes == candidate.genes
        for other in existing
    )


def _retry_duplicate(
    child: Candidate,
    *,
    source: Candidate,
    cross_family: bool,
    context: ReproductionContext,
    rng: Random,
) -> Candidate:
    if child.genes is None or child.status != "pending":
        return child
    source_equal = cross_family and source.genes == child.genes
    if not source_equal and not _is_population_duplicate(child, context.existing_candidates):
        return child

    original = child
    base_genes = child.genes
    diagnostics: list[DuplicateDiagnostic] = []
    if context.duplicate_mutation_attempts == 0:
        detail = "source-equal child" if source_equal else "duplicate attempts exhausted"
        return Candidate(
            child.identifier,
            child.family,
            child.genes,
            child.status,
            child.fitness,
            child.trials,
            child.invalid,
            (DuplicateDiagnostic(0, "exhausted", detail),),
        )

    for attempt in range(1, context.duplicate_mutation_attempts + 1):
        mutated = _mutate_genes(
            base_genes,
            family=child.family,
            context=context,
            rng=rng,
            force_if_none=True,
            mandatory_integers=True,
        )
        try:
            repaired = _repair(child.family, mutated, context=context)
        except CandidateEvaluationError:
            if attempt < context.duplicate_mutation_attempts:
                diagnostics.append(DuplicateDiagnostic(attempt, "invalid", "repair failed"))
                continue
            diagnostics.append(DuplicateDiagnostic(attempt, "exhausted", "duplicate attempts exhausted"))
            break

        retry = Candidate(child.identifier, child.family, repaired, "pending", 0.0, (), None, tuple(diagnostics))
        retry_source_equal = cross_family and source.genes == repaired
        if not retry_source_equal and not _is_population_duplicate(retry, context.existing_candidates):
            return retry
        base_genes = repaired
        if attempt < context.duplicate_mutation_attempts:
            diagnostics.append(DuplicateDiagnostic(attempt, "duplicate", "duplicate child"))
        else:
            diagnostics.append(DuplicateDiagnostic(attempt, "exhausted", "duplicate attempts exhausted"))

    return Candidate(
        original.identifier,
        original.family,
        original.genes,
        original.status,
        original.fitness,
        original.trials,
        original.invalid,
        tuple(diagnostics),
    )


def reproduce_child(
    parent_a: Candidate,
    parent_b: Candidate,
    *,
    context: ReproductionContext,
    identifier: CandidateId,
    rng: Random,
) -> Candidate:
    """Reproduce one child with the architecture's exact conditional draw sequence."""
    source = _fitter(parent_a, parent_b)
    child_family = source.family
    coordinates = family_coordinates(child_family, context.bounds_for(child_family))
    cross_family = parent_a.family != parent_b.family
    if source.genes is None:
        return _missing_parent_genes(identifier, child_family)
    if cross_family:
        genes = _parent_genes(source, coordinate_count=len(coordinates))
    else:
        crossover = bernoulli(rng, context.operators_for(child_family).crossover_probability)
        if crossover:
            if parent_a.genes is None or parent_b.genes is None:
                return _missing_parent_genes(identifier, child_family)
            parent_a_genes = _parent_genes(parent_a, coordinate_count=len(coordinates))
            parent_b_genes = _parent_genes(parent_b, coordinate_count=len(coordinates))
            genes = tuple(
                right if bernoulli(rng, 0.5) else left
                for left, right in zip(parent_a_genes, parent_b_genes, strict=True)
            )
        else:
            genes = _parent_genes(source, coordinate_count=len(coordinates))

    mutated = _mutate_genes(
        genes,
        family=child_family,
        context=context,
        rng=rng,
        force_if_none=cross_family,
        mandatory_integers=cross_family,
    )
    child = _pending_or_invalid(child_family, mutated, identifier=identifier, context=context)
    return _retry_duplicate(child, source=source, cross_family=cross_family, context=context, rng=rng)


def fill_next_population(
    candidates: Sequence[Candidate],
    *,
    generation: int,
    population_size: int,
    elite_count: int,
    tournament_size: int,
    context: ReproductionContext,
    rng: Random,
) -> tuple[Candidate, ...]:
    """Retain elites/champions, then fill ascending child slots by paired tournaments."""
    current = tuple(candidates)
    if type(population_size) is not int or population_size != len(current):
        raise ValueError("population size must exactly match the evaluated current population")
    if type(generation) is not int or generation <= 0:
        raise ValueError("reproduced generation must be a positive exact integer")
    retained = retained_population(current, elite_count=elite_count)
    next_population = list(retained)
    birth_index = 0
    while len(next_population) < population_size:
        parent_a = tournament_select(current, tournament_size=tournament_size, rng=rng)
        parent_b = tournament_select(current, tournament_size=tournament_size, rng=rng)
        child = reproduce_child(
            parent_a,
            parent_b,
            context=context.with_existing(next_population),
            identifier=CandidateId(generation, birth_index),
            rng=rng,
        )
        next_population.append(child)
        birth_index += 1
    return tuple(next_population)
