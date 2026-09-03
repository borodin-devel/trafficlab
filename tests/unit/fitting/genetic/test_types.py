"""Tests for immutable genetic-search value contracts."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any, cast

import pytest
from pydantic import BaseModel, ValidationError

from trafficlab.comparison.diagnostics import FITNESS_METHOD_NAMES
from trafficlab.fitting.genetic.types import (
    METHOD_ORDER,
    Candidate,
    CandidateFailure,
    CandidateId,
    DuplicateDiagnostic,
    HistoryRow,
    MethodTrialResult,
    TrialResult,
    validate_candidate_id,
)

_MARKOV_MODEL_DIAGNOSTICS = {
    "timing_tier_transition_count": 1,
    "timing_tier_source_count": 2,
    "timing_tier_global_count": 3,
    "uniform_unobserved_row_count": 1,
}


def test_method_order_is_the_canonical_eight_fitness_method_order() -> None:
    assert METHOD_ORDER == (
        "autocorrelation",
        "frame_size_ks",
        "iat_ks",
        "multiscale_rate",
        "cramer_von_mises",
        "anderson_darling",
        "jensen_shannon",
        "approximate_mmd",
    )
    assert METHOD_ORDER is FITNESS_METHOD_NAMES


def test_method_trial_diagnostics_are_recursively_frozen_and_ordered() -> None:
    """A mutable nested diagnostic must not leak across the evaluator boundary."""
    method = MethodTrialResult(name="autocorrelation", score=1.0, diagnostics={"nested": [{"value": 1.0}]})

    assert method.name == METHOD_ORDER[0]
    with pytest.raises(TypeError):
        cast(dict[str, object], method.diagnostics)["changed"] = True
    frozen_nested = cast(tuple[object, ...], method.diagnostics["nested"])
    nested = cast(Mapping[str, object], frozen_nested[0])
    with pytest.raises(TypeError):
        cast(dict[str, object], nested)["changed"] = True


def test_checkpoint_value_records_are_strict_frozen_pydantic_models() -> None:
    """Dataclass-only records would bypass the one strict checkpoint schema path."""
    for model in (
        CandidateId,
        MethodTrialResult,
        TrialResult,
        CandidateFailure,
        DuplicateDiagnostic,
        Candidate,
        HistoryRow,
    ):
        assert issubclass(model, BaseModel)
        assert model.model_config.get("extra") == "forbid"
        assert model.model_config.get("frozen") is True
        assert model.model_config.get("strict") is True
        assert model.model_config.get("allow_inf_nan") is False
        assert model.model_config.get("revalidate_instances") == "always"

    with pytest.raises(TypeError):
        CandidateId(0, 0)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        CandidateId.model_validate({"birth_generation": True, "birth_index": 0})


@pytest.mark.parametrize("value", [object(), math.inf, math.nan])
def test_method_trial_rejects_nested_non_json_or_nonfinite_diagnostics(value: object) -> None:
    """Invalid diagnostic leaves must not be serializable genetic state."""
    with pytest.raises(ValueError, match="diagnostic"):
        MethodTrialResult(
            name="autocorrelation",
            score=1.0,
            diagnostics=cast(Any, {"nested": {"value": value}}),
        )


@pytest.mark.parametrize(
    "identifier",
    [
        CandidateId.model_construct(birth_generation=-1, birth_index=0),
        CandidateId.model_construct(birth_generation=0, birth_index=-1),
    ],
)
def test_candidate_id_rejects_negative_components(identifier: CandidateId) -> None:
    """Negative lineage coordinates would break stable lexical identity."""
    with pytest.raises(ValueError, match="nonnegative"):
        validate_candidate_id(identifier)


@pytest.mark.parametrize(
    "identifier",
    [
        CandidateId.model_construct(birth_generation=cast(Any, 0.0), birth_index=0),
        CandidateId.model_construct(birth_generation=0, birth_index=cast(Any, True)),
    ],
)
def test_candidate_id_requires_exact_integer_components(identifier: CandidateId) -> None:
    """Boolean or float lineage coordinates must not silently serialize as integers."""
    with pytest.raises(TypeError, match="integer"):
        validate_candidate_id(identifier)


def test_candidate_id_rejects_an_unrelated_value() -> None:
    """A tuple is not an identity even if it has two integer-looking components."""
    with pytest.raises(TypeError, match="CandidateId"):
        validate_candidate_id(cast(CandidateId, (0, 0)))


def test_trial_result_requires_each_method_once_in_published_order() -> None:
    """A reordered or partial metric tuple would make checkpoint scores ambiguous."""
    methods = _methods()

    with pytest.raises(ValueError, match="published order"):
        TrialResult(
            seed=3,
            aggregate_score=0.5,
            methods=cast(Any, methods[::-1]),
        )


def test_trial_result_freezes_model_diagnostic_counts() -> None:
    """Per-seed model diagnostics must remain immutable checkpoint evidence."""
    trial = TrialResult(seed=3, aggregate_score=0.5, methods=_methods(), model_diagnostics=_MARKOV_MODEL_DIAGNOSTICS)

    assert dict(trial.model_diagnostics) == _MARKOV_MODEL_DIAGNOSTICS
    with pytest.raises(TypeError):
        trial.model_diagnostics["timing_tier_global_count"] = 3  # type: ignore[index]

    for diagnostics in (
        {"": 1},
        {"counter": -1},
        {"counter": True},
        {1: 1},
        {"invented": 1},
        {"timing_tier_transition_count": 1},
        {**_MARKOV_MODEL_DIAGNOSTICS, "invented": 1},
    ):
        with pytest.raises((TypeError, ValueError), match="diagnostic"):
            TrialResult(seed=3, aggregate_score=0.5, methods=_methods(), model_diagnostics=diagnostics)  # type: ignore[arg-type]


def test_packet_hmm_diagnostics_require_contiguous_state_and_category_counters() -> None:
    """Missing or invented HMM indexes would make checkpointed generation evidence ambiguous."""
    diagnostics = {
        "hidden_state_0_count": 3,
        "hidden_state_1_count": 2,
        "category_0_count": 1,
        "category_1_count": 4,
    }
    trial = TrialResult(seed=3, aggregate_score=0.5, methods=_methods(), model_diagnostics=diagnostics)
    candidate = Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=9),
        family="packet_hmm",
        genes=(2,),
        status="valid",
        fitness=0.5,
        trials=(trial,),
        invalid=None,
        duplicate_diagnostics=(),
    )

    assert dict(candidate.trials[0].model_diagnostics) == diagnostics
    for malformed in (
        {"hidden_state_1_count": 1, "category_0_count": 1},
        {"hidden_state_0_count": 1},
        {"hidden_state_0_count": 1, "category_0_count": 1, "invented_0_count": 1},
    ):
        with pytest.raises(ValueError, match="diagnostic"):
            TrialResult(seed=3, aggregate_score=0.5, methods=_methods(), model_diagnostics=malformed)


def test_packet_hmm_candidate_diagnostics_hidden_state_count_matches_gene() -> None:
    """A four-state chromosome cannot checkpoint evidence containing only two hidden-state counters."""
    trial = TrialResult(
        seed=3,
        aggregate_score=0.5,
        methods=_methods(),
        model_diagnostics={
            "hidden_state_0_count": 3,
            "hidden_state_1_count": 2,
            "category_0_count": 5,
        },
    )

    with pytest.raises(ValueError, match="model diagnostics.*packet_hmm.*state_count"):
        Candidate(
            identifier=CandidateId(birth_generation=0, birth_index=10),
            family="packet_hmm",
            genes=(4,),
            status="valid",
            fitness=0.5,
            trials=(trial,),
            invalid=None,
            duplicate_diagnostics=(),
        )


def test_candidate_requires_model_diagnostics_owned_by_its_family() -> None:
    """A complete Markov namespace remains invalid evidence for another family."""
    trial = TrialResult(seed=3, aggregate_score=0.5, methods=_methods(), model_diagnostics=_MARKOV_MODEL_DIAGNOSTICS)
    markov = Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=0),
        family="markov_renewal",
        genes=(0.2, 0.7, 0.5, 2, 1.0),
        status="valid",
        fitness=0.5,
        trials=(trial,),
        invalid=None,
        duplicate_diagnostics=(),
    )

    assert dict(markov.trials[0].model_diagnostics) == _MARKOV_MODEL_DIAGNOSTICS
    packet_train = Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=2),
        family="markov_packet_train",
        genes=(3,),
        status="valid",
        fitness=0.5,
        trials=(trial,),
        invalid=None,
        duplicate_diagnostics=(),
    )
    assert dict(packet_train.trials[0].model_diagnostics) == _MARKOV_MODEL_DIAGNOSTICS
    for family in ("poisson_empirical", "mmpp"):
        with pytest.raises(ValueError, match=f"model diagnostics.*{family}"):
            Candidate(
                identifier=CandidateId(birth_generation=0, birth_index=1),
                family=family,
                genes=(1.0,),
                status="valid",
                fitness=0.5,
                trials=(trial,),
                invalid=None,
                duplicate_diagnostics=(),
            )


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: MethodTrialResult(name=cast(Any, "unknown"), score=0.5, diagnostics={}), "name"),
        (lambda: MethodTrialResult(name="autocorrelation", score=math.inf, diagnostics={}), "score"),
        (lambda: TrialResult(seed=-1, aggregate_score=0.5, methods=_methods()), "seed"),
        (lambda: TrialResult(seed=1, aggregate_score=0.5, methods=cast(Any, ())), "methods"),
        (lambda: TrialResult(seed=1, aggregate_score=0.5, methods=cast(Any, (object(),) * 8)), "MethodTrialResult"),
    ],
)
def test_method_and_trial_contracts_reject_invalid_scalar_or_method_values(factory: object, message: str) -> None:
    """Malformed trial values cannot enter checkpoints as apparently valid scores."""
    with pytest.raises((TypeError, ValueError), match=message):
        cast(Callable[[], object], factory)()


def _methods() -> tuple[
    MethodTrialResult,
    MethodTrialResult,
    MethodTrialResult,
    MethodTrialResult,
    MethodTrialResult,
    MethodTrialResult,
    MethodTrialResult,
    MethodTrialResult,
]:
    return (
        MethodTrialResult(name="autocorrelation", score=0.5, diagnostics={}),
        MethodTrialResult(name="frame_size_ks", score=0.5, diagnostics={}),
        MethodTrialResult(name="iat_ks", score=0.5, diagnostics={}),
        MethodTrialResult(name="multiscale_rate", score=0.5, diagnostics={}),
        MethodTrialResult(name="cramer_von_mises", score=0.5, diagnostics={}),
        MethodTrialResult(name="anderson_darling", score=0.5, diagnostics={}),
        MethodTrialResult(name="jensen_shannon", score=0.5, diagnostics={}),
        MethodTrialResult(name="approximate_mmd", score=0.5, diagnostics={}),
    )


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: CandidateFailure(
                kind=cast(Any, "wrong"),
                seed=None,
                detail="detail",
                stage="fit",
                affected_evidence="candidate evidence",
                evidence_state="diagnostic_only",
                corrective_action="repair candidate evidence",
                authority="primary",
            ),
            "kind",
        ),
        (
            lambda: CandidateFailure(
                kind="repair",
                seed=-1,
                detail="detail",
                stage="fit",
                affected_evidence="candidate evidence",
                evidence_state="diagnostic_only",
                corrective_action="repair candidate evidence",
                authority="primary",
            ),
            "seed",
        ),
        (
            lambda: CandidateFailure(
                kind="repair",
                seed=None,
                detail="",
                stage="fit",
                affected_evidence="candidate evidence",
                evidence_state="diagnostic_only",
                corrective_action="repair candidate evidence",
                authority="primary",
            ),
            "detail",
        ),
        (
            lambda: CandidateFailure(
                kind="repair",
                seed=None,
                detail="detail",
                stage="",
                affected_evidence="candidate evidence",
                evidence_state="diagnostic_only",
                corrective_action="repair candidate evidence",
                authority="primary",
            ),
            "stage",
        ),
        (
            lambda: CandidateFailure(
                kind="repair",
                seed=None,
                detail="detail",
                stage="fit",
                affected_evidence="",
                evidence_state="diagnostic_only",
                corrective_action="repair candidate evidence",
                authority="primary",
            ),
            "affected_evidence",
        ),
        (
            lambda: CandidateFailure(
                kind="repair",
                seed=None,
                detail="detail",
                stage="fit",
                affected_evidence="candidate evidence",
                evidence_state=cast(Any, "wrong"),
                corrective_action="repair candidate evidence",
                authority="primary",
            ),
            "evidence_state",
        ),
        (
            lambda: CandidateFailure(
                kind="repair",
                seed=None,
                detail="detail",
                stage="fit",
                affected_evidence="candidate evidence",
                evidence_state="diagnostic_only",
                corrective_action="",
                authority="primary",
            ),
            "corrective_action",
        ),
        (
            lambda: CandidateFailure(
                kind="repair",
                seed=None,
                detail="detail",
                stage="fit",
                affected_evidence="candidate evidence",
                evidence_state="diagnostic_only",
                corrective_action="repair candidate evidence",
                authority=cast(Any, "wrong"),
            ),
            "authority",
        ),
        (lambda: DuplicateDiagnostic(attempt=-1, outcome="duplicate", detail="detail"), "attempt"),
        (lambda: DuplicateDiagnostic(attempt=0, outcome=cast(Any, "wrong"), detail="detail"), "outcome"),
        (lambda: DuplicateDiagnostic(attempt=0, outcome="duplicate", detail=""), "detail"),
    ],
)
def test_failure_and_duplicate_records_reject_invalid_values(factory: object, message: str) -> None:
    """Invalid classifications must not be mistaken for actionable diagnostics."""
    with pytest.raises(ValueError, match=message):
        cast(Callable[[], object], factory)()


def test_candidate_related_records_preserve_valid_immutable_values() -> None:
    """Population/history contracts retain typed, immutable candidate state."""
    identifier = CandidateId(birth_generation=2, birth_index=4)
    trial = TrialResult(
        seed=7,
        aggregate_score=0.5,
        methods=_methods(),
    )
    candidate = Candidate(
        identifier=identifier,
        family="poisson_empirical",
        genes=(1.0,),
        status="valid",
        fitness=0.5,
        trials=(trial,),
        invalid=None,
        duplicate_diagnostics=(),
    )
    failure = CandidateFailure(
        kind="repair",
        seed=None,
        detail="cannot repair",
        stage="fit",
        affected_evidence="candidate model",
        evidence_state="diagnostic_only",
        corrective_action="repair the candidate model",
        authority="primary",
    )
    diagnostic = DuplicateDiagnostic(attempt=1, outcome="duplicate", detail="same genes")
    history = HistoryRow(
        generation=2,
        scope="family",
        family="poisson_empirical",
        candidate_count=3,
        valid_count=2,
        best_fitness=0.5,
        mean_fitness=0.25,
        best_identifier=identifier,
    )

    assert candidate.identifier == identifier
    assert failure.detail == "cannot repair"
    assert diagnostic.outcome == "duplicate"
    assert history.best_identifier == identifier


def test_candidate_failure_retains_canonical_scientific_diagnostics() -> None:
    """Candidate-invalid records keep the same provenance fields as failure outcomes."""
    failure = CandidateFailure(
        kind="incomplete_generation",
        seed=7,
        detail="max_packets",
        stage="generate",
        affected_evidence="candidate trace",
        evidence_state="not_published",
        corrective_action="increase generation limits or repair the candidate model",
        authority="primary",
    )

    assert (
        failure.stage,
        failure.affected_evidence,
        failure.evidence_state,
        failure.corrective_action,
        failure.authority,
    ) == (
        "generate",
        "candidate trace",
        "not_published",
        "increase generation limits or repair the candidate model",
        "primary",
    )


def test_candidate_accepts_immutable_exact_integer_and_float_genes() -> None:
    """Exact finite numeric tuples remain valid without imposing family arity here."""
    candidate = Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=0),
        family="poisson_empirical",
        genes=(1, 1.0),
        status="pending",
        fitness=0.0,
        trials=(),
        invalid=None,
        duplicate_diagnostics=(),
    )
    invalid = Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=1),
        family="mmpp",
        genes=None,
        status="invalid",
        fitness=0.0,
        trials=(),
        invalid=CandidateFailure(
            kind="repair",
            seed=None,
            detail="x",
            stage="fit",
            affected_evidence="candidate genes",
            evidence_state="diagnostic_only",
            corrective_action="repair candidate genes",
            authority="primary",
        ),
        duplicate_diagnostics=(),
    )

    assert candidate.genes == (1, 1.0)
    assert invalid.genes is None


@pytest.mark.parametrize(
    ("family", "genes", "message"),
    [
        (cast(Any, "unknown"), (1.0,), "family"),
        ("poisson_empirical", (True,), "exact finite"),
        ("poisson_empirical", (math.nan,), "exact finite"),
        ("poisson_empirical", (math.inf,), "exact finite"),
    ],
)
def test_candidate_rejects_noncanonical_family_or_genes(family: object, genes: object, message: str) -> None:
    """Mutable, nonfinite, or foreign chromosome data must not enter population state."""
    with pytest.raises((TypeError, ValueError), match=message):
        Candidate(
            identifier=CandidateId(birth_generation=0, birth_index=0),
            family=cast(Any, family),
            genes=cast(Any, genes),
            status="pending",
            fitness=0.0,
            trials=(),
            invalid=None,
            duplicate_diagnostics=(),
        )


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: Candidate(
                identifier=CandidateId(birth_generation=0, birth_index=0),
                family="poisson_empirical",
                genes=(),
                status=cast(Any, "wrong"),
                fitness=0.0,
                trials=(),
                invalid=None,
                duplicate_diagnostics=(),
            ),
            "status",
        ),
        (
            lambda: Candidate(
                identifier=CandidateId(birth_generation=0, birth_index=0),
                family="poisson_empirical",
                genes=(),
                status="pending",
                fitness=2.0,
                trials=(),
                invalid=None,
                duplicate_diagnostics=(),
            ),
            "fitness",
        ),
        (
            lambda: Candidate(
                identifier=CandidateId(birth_generation=0, birth_index=0),
                family="poisson_empirical",
                genes=(),
                status="pending",
                fitness=0.0,
                trials=(),
                invalid=cast(Any, object()),
                duplicate_diagnostics=(),
            ),
            "invalid",
        ),
    ],
)
def test_candidate_rejects_invalid_population_fields(factory: object, message: str) -> None:
    """Population state must retain only the exact immutable record types."""
    with pytest.raises((TypeError, ValueError), match=message):
        cast(Callable[[], object], factory)()


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: HistoryRow(
                generation=-1,
                scope="overall",
                family=None,
                candidate_count=0,
                valid_count=0,
                best_fitness=0.0,
                mean_fitness=0.0,
                best_identifier=CandidateId(birth_generation=0, birth_index=0),
            ),
            "generation",
        ),
        (
            lambda: HistoryRow(
                generation=0,
                scope=cast(Any, "bad"),
                family=None,
                candidate_count=0,
                valid_count=0,
                best_fitness=0.0,
                mean_fitness=0.0,
                best_identifier=CandidateId(birth_generation=0, birth_index=0),
            ),
            "scope",
        ),
        (
            lambda: HistoryRow(
                generation=0,
                scope="overall",
                family="mmpp",
                candidate_count=0,
                valid_count=0,
                best_fitness=0.0,
                mean_fitness=0.0,
                best_identifier=CandidateId(birth_generation=0, birth_index=0),
            ),
            "family history",
        ),
        (
            lambda: HistoryRow(
                generation=0,
                scope="overall",
                family=None,
                candidate_count=-1,
                valid_count=0,
                best_fitness=0.0,
                mean_fitness=0.0,
                best_identifier=CandidateId(birth_generation=0, birth_index=0),
            ),
            "candidate_count",
        ),
        (
            lambda: HistoryRow(
                generation=0,
                scope="overall",
                family=None,
                candidate_count=0,
                valid_count=1,
                best_fitness=0.0,
                mean_fitness=0.0,
                best_identifier=CandidateId(birth_generation=0, birth_index=0),
            ),
            "valid count",
        ),
    ],
)
def test_history_rejects_incoherent_generation_summaries(factory: object, message: str) -> None:
    """Invalid aggregate summaries must not be published as GA history."""
    with pytest.raises(ValueError, match=message):
        cast(Callable[[], object], factory)()
