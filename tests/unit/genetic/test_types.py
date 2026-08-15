"""Tests for immutable genetic-search value contracts."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any, cast

import pytest

from trafficlab.genetic.types import (
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


def test_method_trial_diagnostics_are_recursively_frozen_and_ordered() -> None:
    """A mutable nested diagnostic must not leak across the evaluator boundary."""
    method = MethodTrialResult("autocorrelation", 1.0, {"nested": [{"value": 1.0}]})

    assert method.name == METHOD_ORDER[0]
    with pytest.raises(TypeError):
        cast(dict[str, object], method.diagnostics)["changed"] = True
    frozen_nested = cast(tuple[object, ...], method.diagnostics["nested"])
    nested = cast(Mapping[str, object], frozen_nested[0])
    with pytest.raises(TypeError):
        cast(dict[str, object], nested)["changed"] = True


@pytest.mark.parametrize("value", [object(), math.inf, math.nan])
def test_method_trial_rejects_nested_non_json_or_nonfinite_diagnostics(value: object) -> None:
    """Invalid diagnostic leaves must not be serializable genetic state."""
    with pytest.raises(ValueError, match="diagnostic"):
        MethodTrialResult("autocorrelation", 1.0, {"nested": {"value": value}})


@pytest.mark.parametrize("identifier", [CandidateId(-1, 0), CandidateId(0, -1)])
def test_candidate_id_rejects_negative_components(identifier: CandidateId) -> None:
    """Negative lineage coordinates would break stable lexical identity."""
    with pytest.raises(ValueError, match="nonnegative"):
        validate_candidate_id(identifier)


@pytest.mark.parametrize("identifier", [CandidateId(cast(Any, 0.0), 0), CandidateId(0, cast(Any, True))])
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
    methods = tuple(MethodTrialResult(name, 0.5, {}) for name in METHOD_ORDER)

    with pytest.raises(ValueError, match="published order"):
        TrialResult(
            3,
            0.5,
            cast(tuple[MethodTrialResult, MethodTrialResult, MethodTrialResult, MethodTrialResult], methods[::-1]),
        )


def test_trial_result_freezes_model_diagnostic_counts() -> None:
    """Per-seed model diagnostics must remain immutable checkpoint evidence."""
    trial = TrialResult(3, 0.5, _methods(), {"timing_tier_global_count": 2})

    assert dict(trial.model_diagnostics) == {"timing_tier_global_count": 2}
    with pytest.raises(TypeError):
        trial.model_diagnostics["timing_tier_global_count"] = 3  # type: ignore[index]

    for diagnostics in ({"": 1}, {"counter": -1}, {"counter": True}, {1: 1}):
        with pytest.raises((TypeError, ValueError), match="diagnostic"):
            TrialResult(3, 0.5, _methods(), diagnostics)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: MethodTrialResult(cast(Any, "unknown"), 0.5, {}), "method name"),
        (lambda: MethodTrialResult("autocorrelation", math.inf, {}), "method score"),
        (lambda: TrialResult(-1, 0.5, _methods()), "trial seed"),
        (lambda: TrialResult(1, 0.5, cast(Any, ())), "trial methods"),
        (lambda: TrialResult(1, 0.5, cast(Any, (object(),) * 4)), "MethodTrialResult"),
    ],
)
def test_method_and_trial_contracts_reject_invalid_scalar_or_method_values(factory: object, message: str) -> None:
    """Malformed trial values cannot enter checkpoints as apparently valid scores."""
    with pytest.raises((TypeError, ValueError), match=message):
        cast(Callable[[], object], factory)()


def _methods() -> tuple[MethodTrialResult, MethodTrialResult, MethodTrialResult, MethodTrialResult]:
    return cast(
        tuple[MethodTrialResult, MethodTrialResult, MethodTrialResult, MethodTrialResult],
        tuple(MethodTrialResult(name, 0.5, {}) for name in METHOD_ORDER),
    )


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: CandidateFailure(
                cast(Any, "wrong"),
                None,
                "detail",
                stage="fit",
                affected_evidence="candidate evidence",
                evidence_state="diagnostic_only",
                corrective_action="repair candidate evidence",
                authority="primary",
            ),
            "failure kind",
        ),
        (
            lambda: CandidateFailure(
                "repair",
                -1,
                "detail",
                stage="fit",
                affected_evidence="candidate evidence",
                evidence_state="diagnostic_only",
                corrective_action="repair candidate evidence",
                authority="primary",
            ),
            "failure seed",
        ),
        (
            lambda: CandidateFailure(
                "repair",
                None,
                "",
                stage="fit",
                affected_evidence="candidate evidence",
                evidence_state="diagnostic_only",
                corrective_action="repair candidate evidence",
                authority="primary",
            ),
            "failure detail",
        ),
        (
            lambda: CandidateFailure(
                "repair",
                None,
                "detail",
                stage="",
                affected_evidence="candidate evidence",
                evidence_state="diagnostic_only",
                corrective_action="repair candidate evidence",
                authority="primary",
            ),
            "failure stage",
        ),
        (
            lambda: CandidateFailure(
                "repair",
                None,
                "detail",
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
                "repair",
                None,
                "detail",
                stage="fit",
                affected_evidence="candidate evidence",
                evidence_state=cast(Any, "wrong"),
                corrective_action="repair candidate evidence",
                authority="primary",
            ),
            "evidence state",
        ),
        (
            lambda: CandidateFailure(
                "repair",
                None,
                "detail",
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
                "repair",
                None,
                "detail",
                stage="fit",
                affected_evidence="candidate evidence",
                evidence_state="diagnostic_only",
                corrective_action="repair candidate evidence",
                authority=cast(Any, "wrong"),
            ),
            "authority",
        ),
        (lambda: DuplicateDiagnostic(-1, "duplicate", "detail"), "attempt"),
        (lambda: DuplicateDiagnostic(0, cast(Any, "wrong"), "detail"), "outcome"),
        (lambda: DuplicateDiagnostic(0, "duplicate", ""), "detail"),
    ],
)
def test_failure_and_duplicate_records_reject_invalid_values(factory: object, message: str) -> None:
    """Invalid classifications must not be mistaken for actionable diagnostics."""
    with pytest.raises(ValueError, match=message):
        cast(Callable[[], object], factory)()


def test_candidate_related_records_preserve_valid_immutable_values() -> None:
    """Population/history contracts retain typed, immutable candidate state."""
    identifier = CandidateId(2, 4)
    methods = tuple(MethodTrialResult(name, 0.5, {}) for name in METHOD_ORDER)
    trial = TrialResult(
        7, 0.5, cast(tuple[MethodTrialResult, MethodTrialResult, MethodTrialResult, MethodTrialResult], methods)
    )
    candidate = Candidate(identifier, "poisson_empirical", (1.0,), "valid", 0.5, (trial,), None, ())
    failure = CandidateFailure(
        "repair",
        None,
        "cannot repair",
        stage="fit",
        affected_evidence="candidate model",
        evidence_state="diagnostic_only",
        corrective_action="repair the candidate model",
        authority="primary",
    )
    diagnostic = DuplicateDiagnostic(1, "duplicate", "same genes")
    history = HistoryRow(2, "family", "poisson_empirical", 3, 2, 0.5, 0.25, identifier)

    assert candidate.identifier == identifier
    assert failure.detail == "cannot repair"
    assert diagnostic.outcome == "duplicate"
    assert history.best_identifier == identifier


def test_candidate_failure_retains_canonical_scientific_diagnostics() -> None:
    """Candidate-invalid records keep the same provenance fields as failure outcomes."""
    failure = CandidateFailure(
        "incomplete_generation",
        7,
        "max_packets",
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
    candidate = Candidate(CandidateId(0, 0), "poisson_empirical", (1, 1.0), "pending", 0.0, (), None, ())
    invalid = Candidate(
        CandidateId(0, 1),
        "mmpp",
        None,
        "invalid",
        0.0,
        (),
        CandidateFailure(
            "repair",
            None,
            "x",
            stage="fit",
            affected_evidence="candidate genes",
            evidence_state="diagnostic_only",
            corrective_action="repair candidate genes",
            authority="primary",
        ),
        (),
    )

    assert candidate.genes == (1, 1.0)
    assert invalid.genes is None


@pytest.mark.parametrize(
    ("family", "genes", "message"),
    [
        (cast(Any, "unknown"), (1.0,), "family"),
        ("poisson_empirical", cast(Any, [1.0]), "genes"),
        ("poisson_empirical", (True,), "exact finite"),
        ("poisson_empirical", (math.nan,), "exact finite"),
        ("poisson_empirical", (math.inf,), "exact finite"),
    ],
)
def test_candidate_rejects_noncanonical_family_or_genes(family: object, genes: object, message: str) -> None:
    """Mutable, nonfinite, or foreign chromosome data must not enter population state."""
    with pytest.raises((TypeError, ValueError), match=message):
        Candidate(CandidateId(0, 0), cast(Any, family), cast(Any, genes), "pending", 0.0, (), None, ())


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: Candidate(CandidateId(0, 0), "poisson_empirical", (), cast(Any, "wrong"), 0.0, (), None, ()),
            "status",
        ),
        (lambda: Candidate(CandidateId(0, 0), "poisson_empirical", (), "pending", 2.0, (), None, ()), "fitness"),
        (
            lambda: Candidate(CandidateId(0, 0), "poisson_empirical", (), "pending", 0.0, cast(Any, []), None, ()),
            "trials",
        ),
        (
            lambda: Candidate(CandidateId(0, 0), "poisson_empirical", (), "pending", 0.0, (), cast(Any, object()), ()),
            "invalid",
        ),
        (
            lambda: Candidate(CandidateId(0, 0), "poisson_empirical", (), "pending", 0.0, (), None, cast(Any, [])),
            "diagnostics",
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
        (lambda: HistoryRow(-1, "overall", None, 0, 0, 0.0, 0.0, CandidateId(0, 0)), "generation"),
        (lambda: HistoryRow(0, cast(Any, "bad"), None, 0, 0, 0.0, 0.0, CandidateId(0, 0)), "scope"),
        (lambda: HistoryRow(0, "overall", "mmpp", 0, 0, 0.0, 0.0, CandidateId(0, 0)), "family history"),
        (lambda: HistoryRow(0, "overall", None, -1, 0, 0.0, 0.0, CandidateId(0, 0)), "counts"),
        (lambda: HistoryRow(0, "overall", None, 0, 1, 0.0, 0.0, CandidateId(0, 0)), "valid count"),
    ],
)
def test_history_rejects_incoherent_generation_summaries(factory: object, message: str) -> None:
    """Invalid aggregate summaries must not be published as GA history."""
    with pytest.raises(ValueError, match=message):
        cast(Callable[[], object], factory)()
