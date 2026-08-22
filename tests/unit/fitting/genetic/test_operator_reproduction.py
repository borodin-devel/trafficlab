"""Direct genetic reproduction behavior tests."""

from __future__ import annotations

import math
from typing import cast

import pytest

from tests.support.genetic_operators import (
    MARKOV_NO_MUTATION,
    MMPP_CROSSOVER,
    POISSON_NO_MUTATION,
    REFERENCE,
    ScriptedRandom,
    context,
    evaluated,
    missing_genes,
)
from trafficlab.common.config import (
    FamilyName,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.fitting.genetic.coordinates import GeneticRng
from trafficlab.fitting.genetic.operators import ReproductionContext, reproduce_child
from trafficlab.fitting.genetic.types import (
    Candidate,
    CandidateFailure,
    CandidateFailureKind,
    CandidateId,
    DuplicateDiagnostic,
)
from trafficlab.generation.models.common import Genes
from trafficlab.generation.models.registry import (
    POISSON_FAMILY,
)


def test_missing_fitter_genes_create_invalid_child_without_operator_draws() -> None:
    """A selected fitter with no repaired chromosome must remain bounded rather than abort the generation."""
    fitter = missing_genes(0, "poisson_empirical")
    other = evaluated(0, 1, "poisson_empirical", (1.0,), 0.0)
    rng = ScriptedRandom()

    child = reproduce_child(
        fitter,
        other,
        context=context(("poisson_empirical", POISSON_NO_MUTATION)),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=cast(GeneticRng, rng),
    )

    assert (child.identifier, child.family, child.genes, child.status, child.fitness) == (
        CandidateId(birth_generation=1, birth_index=0),
        "poisson_empirical",
        None,
        "invalid",
        0.0,
    )
    assert child.invalid == CandidateFailure(
        kind="repair",
        seed=None,
        detail="selected parent has no canonical genes",
        stage="fit",
        affected_evidence="candidate genes",
        evidence_state="diagnostic_only",
        corrective_action="select a parent with canonical genes",
        authority="primary",
    )
    assert child.duplicate_diagnostics == ()
    assert rng.calls == []


def test_reproduction_context_rejects_duplicate_missing_and_foreign_priority_names() -> None:
    """A reproduction boundary requires one exact priority for its configured families."""
    for priority in (
        ("mmpp", "mmpp"),
        ("mmpp",),
        ("mmpp", "foreign_family"),
    ):
        with pytest.raises(ValueError, match="priority"):
            context(
                ("poisson_empirical", POISSON_NO_MUTATION),
                ("mmpp", MMPP_CROSSOVER),
                family_priority=cast("tuple[FamilyName, ...]", priority),
            )


def test_reproduction_context_rejects_wrong_bounds_and_noncandidate_existing_values() -> None:
    """Priority validation must not weaken the pre-existing context value contracts."""
    with pytest.raises(ValueError, match="invalid poisson_empirical"):
        ReproductionContext(
            reference=REFERENCE,
            family_bounds={"poisson_empirical": MARKOV_NO_MUTATION},
            family_priority=("poisson_empirical",),
            duplicate_mutation_attempts=0,
        )
    with pytest.raises(TypeError, match="existing candidates"):
        ReproductionContext(
            reference=REFERENCE,
            family_bounds={"poisson_empirical": POISSON_NO_MUTATION},
            family_priority=("poisson_empirical",),
            duplicate_mutation_attempts=0,
            existing_candidates=cast("tuple[Candidate, ...]", (object(),)),
        )


def test_zero_retries_retains_source_equal_cross_family_child_even_when_source_is_not_a_survivor() -> None:
    """Exact source equality is a duplicate for cross-family cloning outside the survivor set."""
    source = evaluated(0, 0, "poisson_empirical", (1.0,), 0.9)
    other_family = evaluated(0, 1, "mmpp", (0.2, 0.3, 0.4, 3.0), 0.2)
    rng = ScriptedRandom(random_values=[0.7], ranges=[0], normal_values=[0.0])

    child = reproduce_child(
        source,
        other_family,
        context=context(("poisson_empirical", POISSON_NO_MUTATION), ("mmpp", MMPP_CROSSOVER)),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=cast(GeneticRng, rng),
    )

    assert child.genes == source.genes
    assert child.duplicate_diagnostics == (
        DuplicateDiagnostic(attempt=0, outcome="exhausted", detail="source-equal child"),
    )
    assert rng.calls == [("random",), ("integers", 0, 1, False), ("normal", 0.0, 0.1)]


def test_duplicate_attempts_repeat_selection_then_forced_draws_and_accept_first_distinct_child() -> None:
    """A still-duplicate retry becomes the next base and later distinct repair stops the bounded loop."""
    parent = evaluated(0, 0, "poisson_empirical", (1.0,), 0.9)
    survivor = evaluated(0, 4, "poisson_empirical", (1.0,), 1.0)
    rng = ScriptedRandom(
        random_values=[0.9, 0.9, 0.9, 0.9],
        ranges=[0, 0],
        normal_values=[0.0, 0.1],
    )

    child = reproduce_child(
        parent,
        parent,
        context=context(("poisson_empirical", POISSON_NO_MUTATION), attempts=2, existing=(survivor,)),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=cast(GeneticRng, rng),
    )

    assert child.genes == pytest.approx((1.148698354997035,))
    assert child.duplicate_diagnostics == (
        DuplicateDiagnostic(attempt=1, outcome="duplicate", detail="duplicate child"),
    )
    assert rng.calls == [
        ("random",),
        ("random",),
        ("random",),
        ("integers", 0, 1, False),
        ("normal", 0.0, 0.1),
        ("random",),
        ("integers", 0, 1, False),
        ("normal", 0.0, 0.1),
    ]


@pytest.mark.parametrize(
    ("kind", "seed"),
    [("fit", None), ("generation", 7), ("nonfinite_score", 7)],
)
def test_evaluation_invalid_survivor_with_repaired_genes_remains_a_duplicate(
    kind: CandidateFailureKind,
    seed: int | None,
) -> None:
    """Later evaluation status must not erase the exact identity of a repaired survivor."""
    parent = evaluated(0, 0, "poisson_empirical", (1.0,), 0.9)
    survivor = Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=4),
        family="poisson_empirical",
        genes=(1.0,),
        status="invalid",
        fitness=0.0,
        trials=(),
        invalid=CandidateFailure(
            kind=kind,
            seed=seed,
            detail="candidate evaluation failed",
            stage="fit",
            affected_evidence="candidate diagnostic",
            evidence_state="diagnostic_only",
            corrective_action="repair candidate evidence",
            authority="primary",
        ),
        duplicate_diagnostics=(),
    )
    rng = ScriptedRandom(random_values=[0.9, 0.9, 0.9], ranges=[0], normal_values=[0.0])

    child = reproduce_child(
        parent,
        parent,
        context=context(("poisson_empirical", POISSON_NO_MUTATION), attempts=1, existing=(survivor,)),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=cast(GeneticRng, rng),
    )

    assert child.genes == parent.genes
    assert child.duplicate_diagnostics == (
        DuplicateDiagnostic(attempt=1, outcome="exhausted", detail="duplicate attempts exhausted"),
    )
    assert rng.calls == [
        ("random",),
        ("random",),
        ("random",),
        ("integers", 0, 1, False),
        ("normal", 0.0, 0.1),
    ]


def test_invalid_duplicate_attempt_keeps_last_valid_base_and_terminates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid retry must not replace the last valid base or consume unbounded attempts."""
    parent = evaluated(0, 0, "poisson_empirical", (1.0,), 0.9)
    survivor = evaluated(0, 4, "poisson_empirical", (1.0,), 1.0)
    repair_calls = 0

    def scripted_repair(_self: object, genes: object, *_args: object, **_kwargs: object) -> Genes:
        nonlocal repair_calls
        repair_calls += 1
        if repair_calls == 2:
            raise TrafficlabError("retry repair failed", corrective_action="use valid genes")
        return cast(Genes, tuple(cast("tuple[float, ...]", genes)))

    monkeypatch.setattr(type(POISSON_FAMILY), "repair", scripted_repair)
    rng = ScriptedRandom(
        random_values=[0.9, 0.9, 0.9, 0.9],
        ranges=[0, 0],
        normal_values=[0.0, 0.0],
    )

    child = reproduce_child(
        parent,
        parent,
        context=context(("poisson_empirical", POISSON_NO_MUTATION), attempts=2, existing=(survivor,)),
        identifier=CandidateId(birth_generation=1, birth_index=2),
        rng=cast(GeneticRng, rng),
    )

    assert child.status == "pending"
    assert child.genes == parent.genes
    assert child.duplicate_diagnostics == (
        DuplicateDiagnostic(attempt=1, outcome="invalid", detail="repair failed"),
        DuplicateDiagnostic(attempt=2, outcome="exhausted", detail="duplicate attempts exhausted"),
    )
    assert repair_calls == 3


def test_final_invalid_duplicate_attempt_records_exhaustion_and_retains_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repair failure on the last allowed retry must take the bounded exhaustion path."""
    parent = evaluated(0, 0, "poisson_empirical", (1.0,), 0.9)
    survivor = evaluated(0, 4, "poisson_empirical", (1.0,), 1.0)
    repair_calls = 0

    def scripted_repair(_self: object, genes: object, *_args: object, **_kwargs: object) -> Genes:
        nonlocal repair_calls
        repair_calls += 1
        if repair_calls == 2:
            raise TrafficlabError("final retry repair failed", corrective_action="use valid genes")
        return cast(Genes, tuple(cast("tuple[float, ...]", genes)))

    monkeypatch.setattr(type(POISSON_FAMILY), "repair", scripted_repair)
    rng = ScriptedRandom(random_values=[0.9] * 3, ranges=[0], normal_values=[0.0])

    child = reproduce_child(
        parent,
        parent,
        context=context(("poisson_empirical", POISSON_NO_MUTATION), attempts=1, existing=(survivor,)),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=cast(GeneticRng, rng),
    )

    assert child.genes == parent.genes
    assert child.duplicate_diagnostics == (
        DuplicateDiagnostic(attempt=1, outcome="exhausted", detail="duplicate attempts exhausted"),
    )
    assert repair_calls == 2


def test_reproduction_rejects_nonfinite_parent_fitness_before_any_draw() -> None:
    """Defensive operator validation must fail before consuming the dedicated RNG."""
    with pytest.raises(ValueError, match="fitness"):
        evaluated(0, 0, "poisson_empirical", (1.0,), math.nan)
