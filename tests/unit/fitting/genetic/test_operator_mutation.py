"""Direct genetic mutation behavior tests."""

from __future__ import annotations

from typing import cast

import pytest

from tests.support.genetic_operators import (
    MARKOV_INTEGER_MUTATION,
    MARKOV_NO_MUTATION,
    MMPP_MUTATION,
    POISSON_NO_MUTATION,
    ScriptedRandom,
    context,
    evaluated,
    missing_genes,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.fitting.genetic.coordinates import GeneticRng
from trafficlab.fitting.genetic.operators import reproduce_child
from trafficlab.fitting.genetic.types import (
    CandidateId,
)
from trafficlab.generation.models.common import Genes
from trafficlab.generation.models.registry import (
    POISSON_FAMILY,
)


def test_multi_gene_mutation_draws_all_decisions_before_selected_gaussians() -> None:
    """Selected Gaussian draws must follow the complete chromosome's Bernoulli decisions."""
    parent = evaluated(0, 0, "mmpp", (0.2, 0.3, 0.4, 3.0), 0.8)
    other = evaluated(0, 1, "mmpp", (0.3, 0.4, 0.5, 4.0), 0.1)
    rng = ScriptedRandom(
        random_values=[0.9, 0.0, 0.9, 0.0, 0.9],
        normal_values=[0.1, -0.1],
    )

    child = reproduce_child(
        parent,
        other,
        context=context(("mmpp", MMPP_MUTATION)),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=cast(GeneticRng, rng),
    )

    assert child.genes == pytest.approx((0.28102316529672927, 0.3, 0.31773129361652736, 3.0))
    assert rng.calls == [("random",)] * 5 + [
        ("normal", 0.0, 0.1),
        ("normal", 0.0, 0.1),
    ]


@pytest.mark.parametrize(
    ("starting_r", "epsilon", "expected_r"),
    [(3, 0.01, 4), (3, -0.01, 2), (3, 0.0, 4), (1, -0.01, 2), (5, 0.01, 4)],
)
def test_cross_family_mandatory_integer_mutation_moves_unchanged_decode_by_signed_reflected_step(
    starting_r: int, epsilon: float, expected_r: int
) -> None:
    """Mandatory integer mutation must handle both signs, exact zero, and both endpoints."""
    markov = evaluated(0, 0, "markov_renewal", (0.2, 0.7, 1.0, starting_r, 1.0), 0.9)
    poisson = evaluated(0, 1, "poisson_empirical", (1.0,), 0.1)
    rng = ScriptedRandom(random_values=[0.9] * 5, ranges=[3], normal_values=[epsilon])

    child = reproduce_child(
        markov,
        poisson,
        context=context(("markov_renewal", MARKOV_NO_MUTATION), ("poisson_empirical", POISSON_NO_MUTATION)),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=cast(GeneticRng, rng),
    )

    assert child.genes == (0.2, 0.7, 1.0, expected_r, 1.0)
    assert rng.calls == [("random",)] * 5 + [("integers", 0, 5, False), ("normal", 0.0, 0.1)]


def test_ordinary_same_family_integer_mutation_may_decode_unchanged() -> None:
    """The signed one-step rule must not affect ordinary same-family selected mutation."""
    parent = evaluated(0, 0, "markov_renewal", (0.2, 0.7, 1.0, 3, 1.0), 0.9)
    other = evaluated(0, 1, "markov_renewal", (0.3, 0.8, 1.5, 4, 1.5), 0.1)
    rng = ScriptedRandom(
        random_values=[0.9, 0.9, 0.9, 0.9, 0.0, 0.9],
        normal_values=[0.01],
    )

    child = reproduce_child(
        parent,
        other,
        context=context(("markov_renewal", MARKOV_INTEGER_MUTATION)),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=cast(GeneticRng, rng),
    )

    assert child.genes == parent.genes
    assert rng.calls == [("random",)] * 6 + [("normal", 0.0, 0.1)]


def test_direct_repair_error_creates_invalid_child_without_duplicate_draws(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a direct family repair TrafficlabError is candidate-invalid, and invalid children skip retries."""
    parent = evaluated(0, 0, "poisson_empirical", (1.0,), 0.9)
    other = evaluated(0, 1, "poisson_empirical", (1.5,), 0.1)
    survivor = evaluated(0, 2, "poisson_empirical", (1.0,), 1.0)
    rng = ScriptedRandom(random_values=[0.9, 0.9])

    def fail_repair(*_args: object, **_kwargs: object) -> Genes:
        raise TrafficlabError("broken repair", corrective_action="use valid genes")

    monkeypatch.setattr(type(POISSON_FAMILY), "repair", fail_repair)
    child = reproduce_child(
        parent,
        other,
        context=context(("poisson_empirical", POISSON_NO_MUTATION), attempts=2, existing=(survivor,)),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=cast(GeneticRng, rng),
    )

    assert (child.status, child.fitness, child.genes) == ("invalid", 0.0, None)
    assert child.invalid is not None
    assert (child.invalid.kind, child.invalid.seed, child.invalid.detail) == ("repair", None, "broken repair")
    assert child.duplicate_diagnostics == ()
    assert rng.calls == [("random",), ("random",)]


def test_non_trafficlab_repair_exception_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unexpected repair implementation defect must abort instead of becoming weak fitness."""
    parent = evaluated(0, 0, "poisson_empirical", (1.0,), 0.9)

    def fail_repair(*_args: object, **_kwargs: object) -> Genes:
        raise ValueError("implementation defect")

    monkeypatch.setattr(type(POISSON_FAMILY), "repair", fail_repair)
    with pytest.raises(ValueError, match="implementation defect"):
        reproduce_child(
            parent,
            parent,
            context=context(("poisson_empirical", POISSON_NO_MUTATION)),
            identifier=CandidateId(birth_generation=1, birth_index=0),
            rng=cast(GeneticRng, ScriptedRandom(random_values=[0.9, 0.9])),
        )


def test_repair_invalid_survivor_without_canonical_genes_is_not_a_duplicate() -> None:
    """A failed repair has no chromosome identity to compare with a repaired child."""
    parent = evaluated(0, 0, "poisson_empirical", (1.0,), 0.9)
    rng = ScriptedRandom(random_values=[0.9, 0.9])

    child = reproduce_child(
        parent,
        parent,
        context=context(
            ("poisson_empirical", POISSON_NO_MUTATION),
            attempts=1,
            existing=(missing_genes(4, "poisson_empirical"),),
        ),
        identifier=CandidateId(birth_generation=1, birth_index=0),
        rng=cast(GeneticRng, rng),
    )

    assert child.status == "pending"
    assert child.duplicate_diagnostics == ()
    assert rng.calls == [("random",), ("random",)]
