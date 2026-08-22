"""Direct genetic selection behavior tests."""

from __future__ import annotations

from typing import cast

import pytest

from tests.support.genetic_operators import (
    INVALID_MARKOV_REFERENCE,
    MARKOV_NO_MUTATION,
    MMPP_CROSSOVER,
    POISSON,
    POISSON_NO_MUTATION,
    REFERENCE,
    ScriptedRandom,
    context,
    evaluated,
    replace,
)
from trafficlab.fitting.genetic.coordinates import GeneticRng
from trafficlab.fitting.genetic.operators import ReproductionContext, fill_next_population, reproduce_child
from trafficlab.fitting.genetic.population import initial_population
from trafficlab.fitting.genetic.types import (
    Candidate,
    CandidateFailure,
    CandidateId,
    DuplicateDiagnostic,
)
from trafficlab.generation.models.common import make_rng


def test_cross_family_priority_tie_selects_the_priority_source_and_retains_zero_retry_diagnostic() -> None:
    """Equal cross-family parents never fall back to their IDs when choosing a source chromosome."""
    poisson = evaluated(0, 0, "poisson_empirical", (1.0,), 0.5)
    mmpp = evaluated(0, 1, "mmpp", (0.2, 0.3, 0.4, 3.0), 0.5)
    rng = ScriptedRandom(random_values=[0.9] * 4, ranges=[0], normal_values=[0.0])

    child = reproduce_child(
        poisson,
        mmpp,
        context=context(
            ("poisson_empirical", POISSON_NO_MUTATION),
            ("mmpp", MMPP_CROSSOVER),
            family_priority=("mmpp", "poisson_empirical"),
        ),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=cast(GeneticRng, rng),
    )

    assert child.family == "mmpp"
    assert child.genes == mmpp.genes
    assert child.duplicate_diagnostics == (
        DuplicateDiagnostic(attempt=0, outcome="exhausted", detail="source-equal child"),
    )
    assert rng.calls == [("random",)] * 4 + [("integers", 0, 4, False), ("normal", 0.0, 0.1)]


def test_fill_next_population_retains_without_draws_then_assigns_children_in_creation_order() -> None:
    """Elites must consume no draws and child IDs/order must follow ascending open slots."""
    population = (
        evaluated(0, 0, "poisson_empirical", (0.75,), 0.9),
        evaluated(0, 1, "poisson_empirical", (1.0,), 0.8),
        evaluated(0, 2, "poisson_empirical", (1.25,), 0.7),
        evaluated(0, 3, "poisson_empirical", (1.5,), 0.6),
    )
    rng = ScriptedRandom(random_values=[0.9, 0.9] * 3, ranges=[0] * 12)

    next_population = fill_next_population(
        population,
        generation=1,
        population_size=4,
        elite_count=1,
        tournament_size=2,
        context=context(("poisson_empirical", POISSON_NO_MUTATION)),
        rng=cast(GeneticRng, rng),
    )

    assert tuple(item.identifier for item in next_population) == (
        CandidateId(birth_generation=0, birth_index=0),
        CandidateId(birth_generation=1, birth_index=0),
        CandidateId(birth_generation=1, birth_index=1),
        CandidateId(birth_generation=1, birth_index=2),
    )
    assert all(
        item.duplicate_diagnostics
        == (DuplicateDiagnostic(attempt=0, outcome="exhausted", detail="duplicate attempts exhausted"),)
        for item in next_population[1:]
    )
    assert rng.calls == [
        *(("integers", 0, 4, False),) * 4,
        ("random",),
        ("random",),
        *(("integers", 0, 4, False),) * 4,
        ("random",),
        ("random",),
        *(("integers", 0, 4, False),) * 4,
        ("random",),
        ("random",),
    ]


def test_fill_next_population_places_missing_family_champions_in_priority_order() -> None:
    """The retained prefix stays priority-neutral before the first child receives its creation ID."""
    population = (
        evaluated(0, 0, "poisson_empirical", (0.75,), 0.9),
        evaluated(0, 1, "mmpp", (0.2, 0.3, 0.4, 3.0), 0.8),
        evaluated(0, 2, "markov_renewal", (0.2, 0.7, 1.0, 3, 1.0), 0.7),
        evaluated(0, 3, "poisson_empirical", (1.0,), 0.6),
    )

    next_population = fill_next_population(
        population,
        generation=1,
        population_size=4,
        elite_count=1,
        tournament_size=2,
        context=context(
            ("poisson_empirical", POISSON_NO_MUTATION),
            ("mmpp", MMPP_CROSSOVER),
            ("markov_renewal", MARKOV_NO_MUTATION),
            family_priority=("mmpp", "markov_renewal", "poisson_empirical"),
        ),
        rng=make_rng(11),
    )

    assert tuple(item.identifier for item in next_population[:3]) == (
        CandidateId(birth_generation=0, birth_index=0),
        CandidateId(birth_generation=0, birth_index=1),
        CandidateId(birth_generation=0, birth_index=2),
    )
    assert next_population[3].identifier == CandidateId(birth_generation=1, birth_index=0)


def test_all_invalid_initialized_population_fills_generation_with_only_tournament_draws() -> None:
    """Repeated selection of initialization failures must preserve size and deterministic child identities."""
    rng = ScriptedRandom(random_values=[0.0] * 12, ranges=[1, 1, 1] + [0] * 8)
    population = initial_population(
        ("markov_renewal",),
        population_size=3,
        bounds={"markov_renewal": MARKOV_NO_MUTATION},
        reference=INVALID_MARKOV_REFERENCE,
        rng=cast(GeneticRng, rng),
    )

    next_population = fill_next_population(
        population,
        generation=1,
        population_size=3,
        elite_count=1,
        tournament_size=2,
        context=ReproductionContext(
            reference=INVALID_MARKOV_REFERENCE,
            family_bounds={"markov_renewal": MARKOV_NO_MUTATION},
            family_priority=("markov_renewal",),
            duplicate_mutation_attempts=0,
        ),
        rng=cast(GeneticRng, rng),
    )

    assert tuple(item.identifier for item in next_population) == (
        CandidateId(birth_generation=0, birth_index=0),
        CandidateId(birth_generation=1, birth_index=0),
        CandidateId(birth_generation=1, birth_index=1),
    )
    assert all(item.status == "invalid" and item.fitness == 0.0 for item in next_population)
    assert tuple(item.invalid for item in next_population[1:]) == (
        CandidateFailure(
            kind="repair",
            seed=None,
            detail="selected parent has no canonical genes",
            stage="fit",
            affected_evidence="candidate genes",
            evidence_state="diagnostic_only",
            corrective_action="select a parent with canonical genes",
            authority="primary",
        ),
        CandidateFailure(
            kind="repair",
            seed=None,
            detail="selected parent has no canonical genes",
            stage="fit",
            affected_evidence="candidate genes",
            evidence_state="diagnostic_only",
            corrective_action="select a parent with canonical genes",
            authority="primary",
        ),
    )
    initializer_calls = [
        call
        for _ in range(3)
        for call in (
            ("random",),
            ("random",),
            ("random",),
            ("integers", 1, 5, True),
            ("random",),
        )
    ]
    assert rng.calls == initializer_calls + [("integers", 0, 3, False)] * 8


def test_mixed_initialized_population_selected_invalid_parents_fill_without_operator_draws() -> None:
    """Uniform tournaments may select invalid initial candidates without filtering or generation failure."""
    rng = ScriptedRandom(random_values=[0.0] * 10, ranges=[1, 1] + [2] * 8)
    initialized = initial_population(
        ("poisson_empirical", "markov_renewal"),
        population_size=4,
        bounds={"markov_renewal": MARKOV_NO_MUTATION, "poisson_empirical": POISSON_NO_MUTATION},
        reference=INVALID_MARKOV_REFERENCE,
        rng=cast(GeneticRng, rng),
    )
    population = tuple(
        replace(candidate, status="valid") if candidate.status == "pending" else candidate for candidate in initialized
    )

    next_population = fill_next_population(
        population,
        generation=1,
        population_size=4,
        elite_count=1,
        tournament_size=2,
        context=ReproductionContext(
            reference=INVALID_MARKOV_REFERENCE,
            family_bounds={"markov_renewal": MARKOV_NO_MUTATION, "poisson_empirical": POISSON_NO_MUTATION},
            family_priority=("poisson_empirical", "markov_renewal"),
            duplicate_mutation_attempts=0,
        ),
        rng=cast(GeneticRng, rng),
    )

    assert tuple(item.identifier for item in next_population) == (
        CandidateId(birth_generation=0, birth_index=0),
        CandidateId(birth_generation=0, birth_index=2),
        CandidateId(birth_generation=1, birth_index=0),
        CandidateId(birth_generation=1, birth_index=1),
    )
    assert tuple(item.family for item in next_population[2:]) == ("markov_renewal", "markov_renewal")
    assert all(item.status == "invalid" and item.genes is None for item in next_population[2:])
    initializer_calls = [("random",), ("random",)] + [
        call
        for _ in range(2)
        for call in (
            ("random",),
            ("random",),
            ("random",),
            ("integers", 1, 5, True),
            ("random",),
        )
    ]
    assert rng.calls == initializer_calls + [("integers", 0, 4, False)] * 8


@pytest.mark.parametrize(
    ("population_size", "generation", "message"),
    [(3, 1, "population size"), (4, 0, "generation")],
)
def test_fill_next_population_rejects_mismatched_size_and_nonpositive_generation(
    population_size: int, generation: int, message: str
) -> None:
    """Generation construction must reject invalid fixed-size state before any RNG draw."""
    population = tuple(
        evaluated(0, index, "poisson_empirical", (0.75 + index * 0.1,), 0.9 - index * 0.1) for index in range(4)
    )
    rng = ScriptedRandom()
    with pytest.raises(ValueError, match=message):
        fill_next_population(
            population,
            generation=generation,
            population_size=population_size,
            elite_count=1,
            tournament_size=2,
            context=context(("poisson_empirical", POISSON_NO_MUTATION)),
            rng=cast(GeneticRng, rng),
        )
    assert rng.calls == []


def test_reproduction_context_and_parent_validation_fail_before_draws() -> None:
    """Missing family settings, invalid retries, and unfit parents must not consume master RNG state."""
    with pytest.raises(ValueError, match="attempts"):
        ReproductionContext(
            reference=REFERENCE,
            family_bounds={"poisson_empirical": POISSON},
            family_priority=("poisson_empirical",),
            duplicate_mutation_attempts=-1,
        )
    with pytest.raises(ValueError, match="at least one"):
        ReproductionContext(reference=REFERENCE, family_bounds={}, family_priority=(), duplicate_mutation_attempts=0)

    configured = context(("poisson_empirical", POISSON_NO_MUTATION))
    with pytest.raises(ValueError, match="missing"):
        configured.bounds_for("mmpp")

    pending = Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=0),
        family="poisson_empirical",
        genes=(1.0,),
        status="pending",
        fitness=0.0,
        trials=(),
        invalid=None,
        duplicate_diagnostics=(),
    )
    rng = ScriptedRandom()
    with pytest.raises(ValueError, match="evaluated"):
        reproduce_child(
            pending,
            pending,
            context=configured,
            identifier=CandidateId(birth_generation=1, birth_index=0),
            rng=cast(GeneticRng, rng),
        )
    assert rng.calls == []
