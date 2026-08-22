"""Direct genetic crossover behavior tests."""

from __future__ import annotations

from typing import cast

import pytest

from tests.support.genetic_operators import (
    MMPP_CROSSOVER,
    POISSON,
    POISSON_NO_MUTATION,
    ScriptedRandom,
    context,
    evaluated,
    missing_genes,
)
from trafficlab.fitting.genetic.coordinates import GeneticRng
from trafficlab.fitting.genetic.operators import reproduce_child
from trafficlab.fitting.genetic.types import (
    CandidateFailure,
    CandidateId,
)


def test_same_family_crossover_then_mutation_uses_exact_draw_order() -> None:
    """Interleaving mutation decisions and Gaussian draws would desynchronize later genes."""
    parent_a = evaluated(0, 0, "poisson_empirical", (1.0,), 0.7)
    parent_b = evaluated(0, 1, "poisson_empirical", (1.5,), 0.8)
    rng = ScriptedRandom(random_values=[0.0, 0.6, 0.0], normal_values=[0.1])

    child = reproduce_child(
        parent_a,
        parent_b,
        context=context(("poisson_empirical", POISSON)),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=cast(GeneticRng, rng),
    )

    assert child.family == "poisson_empirical"
    assert child.genes == pytest.approx((1.148698354997035,))
    assert rng.calls == [
        ("random",),
        ("random",),
        ("random",),
        ("normal", 0.0, 0.1),
    ]


def test_uniform_crossover_makes_one_parent_choice_per_gene_before_mutation_decisions() -> None:
    """Uniform crossover must choose every gene independently in published MMPP order."""
    parent_a = evaluated(0, 0, "mmpp", (0.2, 0.3, 0.4, 3.0), 0.6)
    parent_b = evaluated(0, 1, "mmpp", (1.2, 1.3, 0.8, 4.0), 0.7)
    rng = ScriptedRandom(random_values=[0.0, 0.6, 0.4, 0.6, 0.4] + [0.9] * 4)

    child = reproduce_child(
        parent_a,
        parent_b,
        context=context(("mmpp", MMPP_CROSSOVER)),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=cast(GeneticRng, rng),
    )

    assert child.genes == (0.2, 1.3, 0.4, 4.0)
    assert rng.calls == [("random",)] * 9


def test_same_family_no_crossover_clones_stable_fitter_tie_and_consumes_endpoint_draws() -> None:
    """A tie must clone the lower ID while p_c=0 and p_m=0 still consume their Bernoulli draws."""
    lower_id = evaluated(0, 1, "poisson_empirical", (0.75,), 0.5)
    higher_id = evaluated(0, 3, "poisson_empirical", (1.5,), 0.5)
    rng = ScriptedRandom(random_values=[0.0, 0.0])

    child = reproduce_child(
        higher_id,
        lower_id,
        context=context(("poisson_empirical", POISSON_NO_MUTATION)),
        identifier=CandidateId(birth_generation=1, birth_index=2),
        rng=cast(GeneticRng, rng),
    )

    assert child.genes == lower_id.genes
    assert rng.calls == [("random",), ("random",)]


def test_same_family_missing_nonfitter_clones_fitter_after_no_crossover_draw() -> None:
    """A missing nonfitter chromosome is irrelevant when the required crossover decision selects cloning."""
    fitter = evaluated(0, 0, "poisson_empirical", (1.0,), 0.9)
    other = missing_genes(1, "poisson_empirical")
    rng = ScriptedRandom(random_values=[0.9, 0.9])

    child = reproduce_child(
        fitter,
        other,
        context=context(("poisson_empirical", POISSON_NO_MUTATION)),
        identifier=CandidateId(birth_generation=1, birth_index=1),
        rng=cast(GeneticRng, rng),
    )

    assert (child.family, child.genes, child.status) == ("poisson_empirical", fitter.genes, "pending")
    assert child.invalid is None
    assert rng.calls == [("random",), ("random",)]


def test_same_family_selected_crossover_with_missing_parent_is_invalid_after_decision_only() -> None:
    """A selected crossover cannot choose genes from a missing chromosome and consumes no later draws."""
    fitter = evaluated(0, 0, "poisson_empirical", (1.0,), 0.9)
    other = missing_genes(1, "poisson_empirical")
    rng = ScriptedRandom(random_values=[0.0])

    child = reproduce_child(
        fitter,
        other,
        context=context(("poisson_empirical", POISSON)),
        identifier=CandidateId(birth_generation=1, birth_index=2),
        rng=cast(GeneticRng, rng),
    )

    assert (child.family, child.genes, child.status, child.fitness) == (
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
    assert rng.calls == [("random",)]


def test_cross_family_clone_forces_mutation_when_no_gene_is_selected() -> None:
    """Different families must omit crossover and use the fitter family's forced-mutation settings."""
    poisson = evaluated(0, 0, "poisson_empirical", (1.0,), 0.9)
    mmpp = evaluated(0, 1, "mmpp", (0.2, 0.3, 0.4, 3.0), 0.2)
    rng = ScriptedRandom(random_values=[0.7], ranges=[0], normal_values=[0.1])

    child = reproduce_child(
        poisson,
        mmpp,
        context=context(("poisson_empirical", POISSON_NO_MUTATION), ("mmpp", MMPP_CROSSOVER)),
        identifier=CandidateId(birth_generation=1, birth_index=3),
        rng=cast(GeneticRng, rng),
    )

    assert child.family == "poisson_empirical"
    assert child.genes == pytest.approx((1.148698354997035,))
    assert child.duplicate_diagnostics == ()
    assert rng.calls == [("random",), ("integers", 0, 1, False), ("normal", 0.0, 0.1)]


def test_cross_family_clone_ignores_missing_nonsource_parent_genes() -> None:
    """Cross-family reproduction depends only on the fitter source chromosome and its operator settings."""
    source = evaluated(0, 0, "poisson_empirical", (1.0,), 0.9)
    other = missing_genes(1, "mmpp")
    rng = ScriptedRandom(random_values=[0.9], ranges=[0], normal_values=[0.1])

    child = reproduce_child(
        source,
        other,
        context=context(("poisson_empirical", POISSON_NO_MUTATION), ("mmpp", MMPP_CROSSOVER)),
        identifier=CandidateId(birth_generation=1, birth_index=3),
        rng=cast(GeneticRng, rng),
    )

    assert child.family == "poisson_empirical"
    assert child.genes == pytest.approx((1.148698354997035,))
    assert child.status == "pending"
    assert rng.calls == [("random",), ("integers", 0, 1, False), ("normal", 0.0, 0.1)]
