"""Tests for exact genetic reproduction, repair, and duplicate draw order."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from dataclasses import replace as replace_dataclass
from typing import Any, cast

import pytest
from pydantic import BaseModel

from trafficlab.config import FamilyName, FloatBounds, IntegerBounds, MarkovRenewalConfig, MmppConfig, PoissonConfig
from trafficlab.errors import TrafficlabError
from trafficlab.genetic.coordinates import GeneticRng
from trafficlab.genetic.operators import ReproductionContext, fill_next_population, reproduce_child
from trafficlab.genetic.population import initial_population
from trafficlab.genetic.types import (
    Candidate,
    CandidateFailure,
    CandidateFailureKind,
    CandidateId,
    CandidateStatus,
    DuplicateDiagnostic,
)
from trafficlab.models.common import FamilyBounds, Genes, make_rng
from trafficlab.models.registry import POISSON_FAMILY
from trafficlab.trace import Direction, TraceEvent, TrafficTrace


def replace[Record](record: Record, **changes: object) -> Record:
    """Build deliberate model states at this test boundary."""
    if isinstance(record, BaseModel):
        values = {name: getattr(record, name) for name in type(record).model_fields}
        values.update(changes)
        return cast(Record, type(record).model_construct(**values))
    return cast(Record, replace_dataclass(cast(Any, record), **changes))


@dataclass
class ScriptedRandom:
    """Strictly record the public RNG primitives chosen by reproduction."""

    random_values: list[float] = field(default_factory=list[float])
    ranges: list[int] = field(default_factory=list[int])
    normal_values: list[float] = field(default_factory=list[float])
    calls: list[tuple[object, ...]] = field(default_factory=list[tuple[object, ...]])

    def random(self) -> float:
        self.calls.append(("random",))
        return self.random_values.pop(0)

    def integers(self, low: int, high: int | None = None, *, endpoint: bool = False) -> int:
        self.calls.append(("integers", low, high, endpoint))
        return self.ranges.pop(0)

    def normal(self, loc: float, scale: float) -> float:
        self.calls.append(("normal", loc, scale))
        return self.normal_values.pop(0)


REFERENCE = TrafficTrace.from_events(
    (
        TraceEvent(0.0, Direction.OUTBOUND, 64),
        TraceEvent(1.0, Direction.INBOUND, 128),
        TraceEvent(2.0, Direction.OUTBOUND, 256),
    )
)
INVALID_MARKOV_REFERENCE = TrafficTrace.from_events(
    (
        TraceEvent(0.0, Direction.OUTBOUND, 64),
        TraceEvent(1.0, Direction.INBOUND, 64),
    )
)
POISSON = PoissonConfig(
    crossover_probability=1.0,
    mutation_probability=1.0,
    mutation_scale=0.1,
    c_lambda=FloatBounds(lower=0.5, upper=2.0),
)
POISSON_NO_MUTATION = PoissonConfig(
    crossover_probability=0.0,
    mutation_probability=0.0,
    mutation_scale=0.1,
    c_lambda=FloatBounds(lower=0.5, upper=2.0),
)
MARKOV_NO_MUTATION = MarkovRenewalConfig(
    crossover_probability=0.0,
    mutation_probability=0.0,
    mutation_scale=0.1,
    q1=FloatBounds(lower=0.1, upper=0.4),
    q2=FloatBounds(lower=0.6, upper=0.9),
    alpha=FloatBounds(lower=0.0, upper=2.0),
    r=IntegerBounds(lower=1, upper=5),
    c_t=FloatBounds(lower=0.5, upper=2.0),
)
MARKOV_INTEGER_MUTATION = MarkovRenewalConfig(
    crossover_probability=0.0,
    mutation_probability=0.2,
    mutation_scale=0.1,
    q1=MARKOV_NO_MUTATION.q1,
    q2=MARKOV_NO_MUTATION.q2,
    alpha=MARKOV_NO_MUTATION.alpha,
    r=MARKOV_NO_MUTATION.r,
    c_t=MARKOV_NO_MUTATION.c_t,
)
MMPP_CROSSOVER = MmppConfig(
    crossover_probability=1.0,
    mutation_probability=0.0,
    mutation_scale=0.1,
    q01=FloatBounds(lower=0.1, upper=3.0),
    q10=FloatBounds(lower=0.1, upper=3.0),
    lambda0=FloatBounds(lower=0.1, upper=1.0),
    lambda1=FloatBounds(lower=2.0, upper=5.0),
)
MMPP_MUTATION = MmppConfig(
    crossover_probability=0.0,
    mutation_probability=0.5,
    mutation_scale=0.1,
    q01=MMPP_CROSSOVER.q01,
    q10=MMPP_CROSSOVER.q10,
    lambda0=MMPP_CROSSOVER.lambda0,
    lambda1=MMPP_CROSSOVER.lambda1,
)


def evaluated(
    generation: int,
    index: int,
    family: FamilyName,
    genes: Genes,
    fitness: float,
    *,
    status: CandidateStatus = "valid",
) -> Candidate:
    """Build one evaluated parent or survivor with literal genes."""
    return Candidate(
        identifier=CandidateId(birth_generation=generation, birth_index=index),
        family=family,
        genes=genes,
        status=status,
        fitness=fitness,
        trials=(),
        invalid=None,
        duplicate_diagnostics=(),
    )


def context(
    *configs: tuple[FamilyName, FamilyBounds],
    attempts: int = 0,
    existing: tuple[Candidate, ...] = (),
    family_priority: tuple[FamilyName, ...] | None = None,
) -> ReproductionContext:
    """Build the immutable operator context from exact registered family configs."""
    return ReproductionContext(
        reference=REFERENCE,
        family_bounds=dict(configs),
        family_priority=family_priority if family_priority is not None else tuple(name for name, _ in configs),
        duplicate_mutation_attempts=attempts,
        existing_candidates=existing,
    )


def missing_genes(index: int, family: FamilyName) -> Candidate:
    """Build an evaluated repair-invalid candidate with no canonical chromosome."""
    return Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=index),
        family=family,
        genes=None,
        status="invalid",
        fitness=0.0,
        trials=(),
        invalid=CandidateFailure(
            kind="repair",
            seed=None,
            detail="initializer repair failed",
            stage="fit",
            affected_evidence="candidate genes",
            evidence_state="diagnostic_only",
            corrective_action="repair candidate initialization",
            authority="primary",
        ),
        duplicate_diagnostics=(),
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


def test_reproduction_rejects_nonfinite_parent_fitness_before_any_draw() -> None:
    """Defensive operator validation must fail before consuming the dedicated RNG."""
    with pytest.raises(ValueError, match="fitness"):
        evaluated(0, 0, "poisson_empirical", (1.0,), math.nan)
