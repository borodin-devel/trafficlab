"""Shared genetic-operator contexts, candidates, and scripted RNG."""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace as replace_dataclass
from typing import Any, cast

from pydantic import BaseModel

from trafficlab.common.config import (
    FamilyName,
    FloatBounds,
    IntegerBounds,
    MarkovRenewalConfig,
    MmppConfig,
    PoissonConfig,
)
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.fitting.genetic.operators import ReproductionContext
from trafficlab.fitting.genetic.types import (
    Candidate,
    CandidateFailure,
    CandidateId,
    CandidateStatus,
)
from trafficlab.generation.models.common import FamilyBounds, Genes

"""Tests for exact genetic reproduction, repair, and duplicate draw order."""


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
