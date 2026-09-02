"""Candidate evaluation through registered model and real similarity paths."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace as replace_dataclass
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import BaseModel

import trafficlab.comparison.metrics as comparison
from trafficlab.common.config import (
    C2stSettings,
    DispersionSettings,
    FamilyName,
    FloatBounds,
    GenerationLimits,
    MethodWeights,
    PoissonConfig,
    PostfitSettings,
    SimilarityConfig,
    TransitionSettings,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.fitting.genetic import evaluation
from trafficlab.fitting.genetic.coordinates import CandidateEvaluationError
from trafficlab.fitting.genetic.evaluation import (
    EvaluationContext,
    evaluate_candidate,
    evaluate_final,
    validate_candidate_similarity_preconditions,
    validate_evaluation_context,
)
from trafficlab.fitting.genetic.types import METHOD_ORDER, Candidate, CandidateFailure, CandidateId
from trafficlab.generation.models.common import FamilyBounds, FittedModel, Gene, GenerationResult, Genes, ModelFamily


def replace[Record](record: Record, **changes: object) -> Record:
    """Build deliberate model states at this test boundary."""
    if isinstance(record, BaseModel):
        values = {name: getattr(record, name) for name in type(record).model_fields}
        values.update(changes)
        return cast(Record, type(record).model_construct(**values))
    return cast(Record, replace_dataclass(cast(Any, record), **changes))


W = 2.0
BOUNDS = PoissonConfig(c_lambda=FloatBounds(lower=0.25, upper=4.0))
TRIAL_LIMITS = GenerationLimits(max_packets=20, max_output_bytes=20_000, max_wall_seconds=2.0)
REFERENCE = (
    TraceEvent(0.0, Direction.OUTBOUND, 60),
    TraceEvent(1.0, Direction.INBOUND, 60),
    TraceEvent(2.0, Direction.OUTBOUND, 60),
)
GENERATED = (
    TraceEvent(0.0, Direction.OUTBOUND, 120),
    TraceEvent(1.0, Direction.INBOUND, 120),
    TraceEvent(2.0, Direction.OUTBOUND, 120),
)
REFERENCE_TRACE = TrafficTrace.from_events(REFERENCE)
GENERATED_TRACE = TrafficTrace.from_events(GENERATED)
EMPTY_TRACE = TrafficTrace.from_events(())
SIMILARITY = SimilarityConfig(
    iat_diagnostic_quantile=0.5,
    acf_lags=(1,),
    acf_lag_weights=(1.0,),
    acf_iat_weight=0.5,
    acf_size_weight=0.5,
    multiscale_widths_seconds=(1.0,),
    multiscale_scale_weights=(1.0,),
    multiscale_packet_weight=1.0,
    multiscale_byte_weight=0.0,
    max_direction_bin_cells=10,
    cvm_iat_weight=0.5,
    cvm_size_weight=0.5,
    ad_iat_weight=0.5,
    ad_size_weight=0.5,
    js_iat_bin_count=4,
    js_iat_weight=0.5,
    js_mark_weight=0.5,
    mmd_feature_count=8,
    mmd_seed=17,
    mmd_scale_floor=0.001,
    method_weights=MethodWeights(
        frame_size_ks=0.125,
        iat_ks=0.125,
        autocorrelation=0.125,
        multiscale_rate=0.125,
        cramer_von_mises=0.125,
        anderson_darling=0.125,
        jensen_shannon=0.125,
        approximate_mmd=0.125,
    ),
    postfit=PostfitSettings(
        dispersion=DispersionSettings(
            widths_seconds=(0.5, 1.0),
            scale_weights=(0.5, 0.5),
            fano_weight=0.5,
            allan_weight=0.5,
        ),
        transition=TransitionSettings(
            size_bin_count=2,
            iat_bin_count=2,
            pseudocount=0.5,
            occupancy_weight=0.34,
            transition_rows_weight=0.33,
            runs_weight=0.33,
        ),
        c2st=C2stSettings(
            feature_version="window-v1",
            window_width_seconds=0.25,
            fold_count=2,
            guard_window_count=1,
            maximum_window_count=64,
            l2_regularization=1.0,
            maximum_iterations=100,
            tolerance=1e-9,
        ),
    ),
)
PENDING_POISSON = Candidate(
    identifier=CandidateId(birth_generation=0, birth_index=0),
    family="poisson_empirical",
    genes=(1.0,),
    status="pending",
    fitness=0.0,
    trials=(),
    invalid=None,
    duplicate_diagnostics=(),
)
VALID_COMPARISON = comparison.compare_traces(REFERENCE, GENERATED, W, SIMILARITY)


class RecordingModel:
    """Minimal fitted state owned by the recording family."""

    family: FamilyName = "poisson_empirical"


class RecordingFamily:
    """In-process registered family double whose output uses the real metrics."""

    name: FamilyName = "poisson_empirical"
    gene_names = ("c_lambda",)
    bounds_type = PoissonConfig
    estimator_choices: Mapping[str, str | int | float] = MappingProxyType({"test": "recording"})

    def __init__(self) -> None:
        self.repair_calls = 0
        self.fit_calls = 0
        self.generate_calls: list[tuple[int, float, GenerationLimits]] = []
        self.repair_error: Exception | None = None
        self.fit_error: Exception | None = None
        self.generate_errors: dict[int, Exception] = {}
        self.results: dict[int, object] = {}

    def repair(self, genes: Sequence[Gene], bounds: FamilyBounds, reference: TrafficTrace) -> Genes:
        self.repair_calls += 1
        if self.repair_error is not None:
            raise self.repair_error
        assert bounds is BOUNDS
        assert reference is REFERENCE_TRACE
        return tuple(genes)

    def fit(
        self,
        reference: TrafficTrace,
        genes: Sequence[Gene],
        *,
        W: float,
        bounds: FamilyBounds,
    ) -> FittedModel:
        self.fit_calls += 1
        if self.fit_error is not None:
            raise self.fit_error
        assert reference is REFERENCE_TRACE
        assert tuple(genes) == (1.0,)
        assert W == 2.0
        assert bounds is BOUNDS
        return RecordingModel()

    def generate(
        self,
        model: FittedModel,
        seed: int,
        W: float,
        limits: GenerationLimits,
        *,
        clock: Callable[[], float] | None = None,
    ) -> GenerationResult:
        del clock
        self.generate_calls.append((seed, W, limits))
        if error := self.generate_errors.get(seed):
            raise error
        assert model.family == self.name
        return cast(GenerationResult, self.results.get(seed, GenerationResult(True, GENERATED_TRACE)))

    def load_fitted(self, data: object, *, genes: Genes, bounds: FamilyBounds) -> FittedModel:
        del data, genes, bounds
        return RecordingModel()

    def dump_fitted(self, model: FittedModel) -> dict[str, object]:
        del model
        return {"test": "recording"}


@pytest.fixture
def family(monkeypatch: pytest.MonkeyPatch) -> RecordingFamily:
    value = RecordingFamily()
    monkeypatch.setattr(
        evaluation,
        "REGISTRY",
        MappingProxyType({"poisson_empirical": cast(Any, value)}),
    )
    return value


def _context(family: RecordingFamily, **changes: object) -> EvaluationContext:
    values: dict[str, object] = {
        "reference": REFERENCE_TRACE,
        "window": W,
        "families": {"poisson_empirical": family},
        "bounds": {"poisson_empirical": BOUNDS},
        "trial_seeds": (7, 9),
        "trial_limits": TRIAL_LIMITS,
        "similarity": SIMILARITY,
    }
    values.update(changes)
    return EvaluationContext(**cast(Any, values))


def _trafficlab_error(message: str) -> TrafficlabError:
    return TrafficlabError(message, corrective_action="test corrective action")


def _similarity_with_zero_weight(method_name: str) -> SimilarityConfig:
    weights = SIMILARITY.method_weights.model_dump()
    selected_method = next(name for name in weights if name != method_name)
    weights[method_name] = 0.0
    weights[selected_method] = 1.0
    for name in weights:
        if name not in {method_name, selected_method}:
            weights[name] = 0.0
    return SIMILARITY.model_copy(update={"method_weights": MethodWeights(**weights)})


def test_evaluation_fits_once_and_gives_each_trial_the_same_window_and_limits(
    family: RecordingFamily,
) -> None:
    """Refitting per seed or changing the common budget would make candidates incomparable."""
    validated = validate_evaluation_context(_context(family))
    evaluated = evaluate_candidate(PENDING_POISSON, validated)

    assert evaluated.status == "valid"
    assert [trial.seed for trial in evaluated.trials] == [7, 9]
    assert family.repair_calls == 1
    assert family.fit_calls == 1
    assert family.generate_calls == [(7, W, TRIAL_LIMITS), (9, W, TRIAL_LIMITS)]
    assert all(tuple(method.name for method in trial.methods) == METHOD_ORDER for trial in evaluated.trials)
    expected = VALID_COMPARISON.aggregate_score
    assert tuple(trial.aggregate_score for trial in evaluated.trials) == (expected, expected)
    assert evaluated.fitness == math.fsum((expected, expected)) / 2.0
    with pytest.raises(TypeError):
        cast(dict[str, object], evaluated.trials[0].methods[0].diagnostics)["changed"] = True


def test_genetic_candidate_evaluation_never_calls_postfit(
    family: RecordingFamily, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selection trials must stay isolated even when every post-fit entry raises immediately."""

    def prohibited(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("genetic evaluation called final-only post-fit")

    monkeypatch.setattr(comparison, "evaluate_postfit", prohibited)
    monkeypatch.setattr(comparison, "fano_allan_diagnostic", prohibited)
    monkeypatch.setattr(comparison, "transition_matrix_diagnostic", prohibited)
    monkeypatch.setattr(comparison, "classical_c2st_diagnostic", prohibited)

    evaluated = evaluate_candidate(PENDING_POISSON, validate_evaluation_context(_context(family)))

    assert evaluated.status == "valid"
    assert len(evaluated.trials) == 2


def test_candidate_evaluation_never_materializes_reference_or_generated_events(
    family: RecordingFamily, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Candidate fit, generation, and all eight metrics must share columnar traces end to end."""

    def reject_event_materialization(_trace: TrafficTrace) -> tuple[TraceEvent, ...]:
        raise AssertionError("candidate evaluation materialized TraceEvent objects")

    monkeypatch.setattr(TrafficTrace, "to_events", reject_event_materialization)

    evaluated = evaluate_candidate(PENDING_POISSON, validate_evaluation_context(_context(family)))

    assert evaluated.status == "valid"
    assert len(evaluated.trials) == 2


def test_evaluation_rejects_generation_diagnostics_from_the_wrong_family(family: RecordingFamily) -> None:
    """A Poisson candidate cannot persist counters from the Markov generation owner."""
    diagnostics = {
        "timing_tier_transition_count": 1,
        "timing_tier_source_count": 2,
        "timing_tier_global_count": 3,
        "uniform_unobserved_row_count": 1,
    }
    family.results[7] = GenerationResult(True, GENERATED_TRACE, model_diagnostics=diagnostics)

    with pytest.raises(ValueError, match="model diagnostics.*poisson_empirical"):
        evaluate_candidate(PENDING_POISSON, validate_evaluation_context(_context(family)))


def test_common_metric_precondition_failure_aborts_before_candidate_loop(family: RecordingFamily) -> None:
    """Skipping reference self-comparison would misclassify a shared bad lag per candidate."""
    similarity = SIMILARITY.model_copy(update={"acf_lags": (2,)})

    with pytest.raises(TrafficlabError, match="autocorrelation"):
        validate_evaluation_context(_context(family, similarity=similarity))

    assert family.repair_calls == 0
    assert family.fit_calls == 0
    assert family.generate_calls == []


def test_incomplete_generation_is_invalid_candidate_not_infrastructure_abort(family: RecordingFamily) -> None:
    """A bounded guard stop is candidate science and must receive zero fitness."""
    family.results[7] = GenerationResult(False, EMPTY_TRACE, "max_packets")

    evaluated = evaluate_candidate(PENDING_POISSON, validate_evaluation_context(_context(family)))

    assert evaluated.status == "invalid"
    assert evaluated.fitness == 0.0
    assert evaluated.trials == ()
    assert evaluated.invalid == CandidateFailure(
        kind="incomplete_generation",
        seed=7,
        detail="max_packets",
        stage="fit",
        affected_evidence="candidate trace",
        evidence_state="diagnostic_only",
        corrective_action="increase generation limits or repair the candidate model",
        authority="primary",
    )
    assert family.generate_calls == [(7, W, TRIAL_LIMITS)]


def test_generated_method_precondition_is_a_narrow_candidate_failure(family: RecordingFamily) -> None:
    """A complete but one-packet candidate cannot satisfy IAT or configured-lag metrics."""
    family.results[7] = GenerationResult(True, TrafficTrace.from_events((TraceEvent(0.0, Direction.OUTBOUND, 60),)))

    evaluated = evaluate_candidate(PENDING_POISSON, validate_evaluation_context(_context(family)))

    assert evaluated.status == "invalid"
    assert evaluated.invalid is not None
    assert evaluated.invalid.kind == "similarity_precondition"
    assert evaluated.invalid.seed == 7
    assert "autocorrelation" in evaluated.invalid.detail


def test_final_validation_uses_only_fresh_seed_and_is_stage_fatal_when_incomplete(
    family: RecordingFamily,
) -> None:
    """Final validation must neither reuse selection trials nor reselect after failure."""
    family.results[101] = GenerationResult(False, EMPTY_TRACE, "max_packets")
    valid = replace(PENDING_POISSON, status="valid", fitness=0.75)

    with pytest.raises(TrafficlabError, match="final validation.*max_packets"):
        evaluate_final(valid, validate_evaluation_context(_context(family)), final_seed=101)

    assert family.fit_calls == 1
    assert family.generate_calls == [(101, W, TRIAL_LIMITS)]


def test_final_validation_refits_once_and_returns_no_fitted_python_state(family: RecordingFamily) -> None:
    """The final result must contain only serializable trial values from the fresh seed."""
    valid = replace(PENDING_POISSON, status="valid", fitness=0.75)

    trials = evaluate_final(valid, validate_evaluation_context(_context(family)), final_seed=101)

    assert family.repair_calls == 0
    assert family.fit_calls == 1
    assert family.generate_calls == [(101, W, TRIAL_LIMITS)]
    assert tuple(trial.seed for trial in trials) == (101,)
    assert trials[0].aggregate_score == VALID_COMPARISON.aggregate_score


@pytest.mark.parametrize(
    ("boundary", "kind", "seed", "affected_evidence"),
    [
        ("repair", "repair", None, "candidate genes"),
        ("fit", "fit", None, "candidate model"),
        ("generate", "generation", 7, "candidate trace"),
    ],
)
def test_only_direct_family_trafficlab_errors_are_invalid_candidates(
    family: RecordingFamily,
    boundary: str,
    kind: str,
    seed: int | None,
    affected_evidence: str,
) -> None:
    """Registered repair, fit, and generation failures are the named family classifications."""
    error = _trafficlab_error(f"{boundary} rejected")
    if boundary == "repair":
        family.repair_error = error
    elif boundary == "fit":
        family.fit_error = error
    else:
        family.generate_errors[7] = error

    evaluated = evaluate_candidate(PENDING_POISSON, validate_evaluation_context(_context(family)))

    assert evaluated.status == "invalid"
    assert evaluated.invalid == CandidateFailure(
        kind=cast(Any, kind),
        seed=seed,
        detail=f"{boundary} rejected",
        stage="fit",
        affected_evidence=affected_evidence,
        evidence_state="diagnostic_only",
        corrective_action="test corrective action",
        authority="primary",
    )


@pytest.mark.parametrize("boundary", ["repair", "fit", "generate"])
def test_unexpected_family_exceptions_abort(family: RecordingFamily, boundary: str) -> None:
    """A broad family-boundary catch would hide implementation defects as weak candidates."""
    error = RuntimeError(f"{boundary} bug")
    if boundary == "repair":
        family.repair_error = error
    elif boundary == "fit":
        family.fit_error = error
    else:
        family.generate_errors[7] = error

    with pytest.raises(RuntimeError, match=f"{boundary} bug"):
        evaluate_candidate(PENDING_POISSON, validate_evaluation_context(_context(family)))


@pytest.mark.parametrize("score", [math.nan, math.inf, -0.1, 1.1, 1])
def test_component_scores_must_be_exact_finite_bounded_floats(
    family: RecordingFamily,
    monkeypatch: pytest.MonkeyPatch,
    score: object,
) -> None:
    """Malformed evaluator scores must become the explicit nonfinite-score candidate category."""
    validated = validate_evaluation_context(_context(family))
    methods = {
        name: SimpleNamespace(
            score=score if name == "iat_ks" else 1.0,
            diagnostics=VALID_COMPARISON.methods[name].diagnostics,
        )
        for name in METHOD_ORDER
    }
    result = SimpleNamespace(aggregate_score=1.0, methods=methods)

    def compare_scores(*_args: object) -> Any:
        return result

    monkeypatch.setattr(evaluation, "evaluate_fitness", compare_scores)

    evaluated = evaluate_candidate(PENDING_POISSON, validated)

    assert evaluated.status == "invalid"
    assert evaluated.fitness == 0.0
    assert evaluated.invalid is not None
    assert evaluated.invalid.kind == "nonfinite_score"
    assert evaluated.invalid.seed == 7


def test_aggregate_score_is_checked_separately(family: RecordingFamily, monkeypatch: pytest.MonkeyPatch) -> None:
    """A finite method set cannot legitimize a nonfinite aggregate supplied by the evaluator."""
    validated = validate_evaluation_context(_context(family))
    methods = {
        name: SimpleNamespace(score=1.0, diagnostics=VALID_COMPARISON.methods[name].diagnostics)
        for name in METHOD_ORDER
    }

    def compare_scores(*_args: object) -> Any:
        return SimpleNamespace(aggregate_score=math.nan, methods=methods)

    monkeypatch.setattr(evaluation, "evaluate_fitness", compare_scores)

    evaluated = evaluate_candidate(PENDING_POISSON, validated)

    assert evaluated.invalid == CandidateFailure(
        kind="nonfinite_score",
        seed=7,
        detail="aggregate score must be a finite float in [0, 1]",
        stage="fit",
        affected_evidence="candidate similarity",
        evidence_state="diagnostic_only",
        corrective_action="repair the candidate model or similarity computation",
        authority="primary",
    )


def test_fitness_uses_math_fsum_across_all_trials(
    family: RecordingFamily,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary repeated addition must not replace the specified accurate trial mean."""
    scores = (2.9654131715463945e-54, 4.003084325304506e-54, 5.430004719775657e-171)
    validated = validate_evaluation_context(_context(family, trial_seeds=tuple(range(3))))
    remaining_scores = iter(scores)

    def compare_scores(*_args: object) -> Any:
        score = next(remaining_scores)
        methods = {
            name: SimpleNamespace(score=score, diagnostics=VALID_COMPARISON.methods[name].diagnostics)
            for name in METHOD_ORDER
        }
        return SimpleNamespace(aggregate_score=score, methods=methods)

    monkeypatch.setattr(evaluation, "evaluate_fitness", compare_scores)

    evaluated = evaluate_candidate(PENDING_POISSON, validated)

    assert evaluated.fitness == math.fsum(scores) / 3.0
    assert evaluated.fitness != sum(scores) / 3.0


@pytest.mark.parametrize("error", [RuntimeError("injected")])
def test_unclassified_evaluator_errors_abort(
    family: RecordingFamily,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    """A broad comparison catch would turn infrastructure or evaluator defects into fitness zero."""
    validated = validate_evaluation_context(_context(family))

    def fail(*_args: object) -> Any:
        raise error

    monkeypatch.setattr(evaluation, "evaluate_fitness", fail)
    with pytest.raises(type(error), match="injected"):
        evaluate_candidate(PENDING_POISSON, validated)


def test_already_invalid_candidate_never_calls_a_family(family: RecordingFamily) -> None:
    """Initialization failures with no genes must flow through evaluation deterministically."""
    invalid = replace(
        PENDING_POISSON,
        genes=None,
        status="invalid",
        invalid=CandidateFailure(
            kind="repair",
            seed=None,
            detail="initialization failed",
            stage="fit",
            affected_evidence="candidate genes",
            evidence_state="diagnostic_only",
            corrective_action="repair candidate initialization",
            authority="primary",
        ),
    )

    assert evaluate_candidate(invalid, validate_evaluation_context(_context(family))) is invalid
    assert family.repair_calls == family.fit_calls == 0
    assert family.generate_calls == []


def test_context_copies_family_and_bound_mappings(family: RecordingFamily) -> None:
    """Caller mutation must not change the common scientific inputs after construction."""
    families = {"poisson_empirical": family}
    bounds = {"poisson_empirical": BOUNDS}
    context = _context(family, families=families, bounds=bounds)
    families.clear()
    bounds.clear()

    assert tuple(context.families) == ("poisson_empirical",)
    assert tuple(context.bounds) == ("poisson_empirical",)
    with pytest.raises(TypeError):
        cast(dict[str, object], context.families)["poisson_empirical"] = family


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"families": {}}, "family"),
        ({"bounds": {}}, "bounds"),
        ({"trial_seeds": ()}, "seed"),
        ({"trial_seeds": (7, 7)}, "unique"),
        ({"trial_seeds": (cast(Any, True),)}, "integer"),
        ({"trial_limits": cast(Any, object())}, "GenerationLimits"),
        ({"similarity": cast(Any, object())}, "SimilarityConfig"),
    ],
)
def test_common_context_rejects_malformed_registry_budget_and_seed_values(
    family: RecordingFamily,
    changes: dict[str, object],
    message: str,
) -> None:
    """Malformed common inputs must abort once rather than fail inside individual candidates."""
    with pytest.raises((TypeError, ValueError, TrafficlabError), match=message):
        validate_evaluation_context(_context(family, **changes))


def test_context_requires_the_exact_family_object_from_the_closed_registry(family: RecordingFamily) -> None:
    """An unregistered lookalike family must not become an evaluator extension point."""
    lookalike = RecordingFamily()

    with pytest.raises(TrafficlabError, match="registered"):
        validate_evaluation_context(_context(family, families={"poisson_empirical": lookalike}))


def test_context_rejects_the_wrong_or_internally_invalid_bounds(family: RecordingFamily) -> None:
    """Bound-table type or invariant corruption must fail before any candidate family call."""
    with pytest.raises(ValueError, match="PoissonConfig"):
        validate_evaluation_context(_context(family, bounds={"poisson_empirical": object()}))

    invalid_bounds = PoissonConfig.model_construct(
        c_lambda=FloatBounds.model_construct(lower=4.0, upper=0.25),
        crossover_probability=0.9,
        mutation_probability=1.0,
        mutation_scale=0.1,
    )
    with pytest.raises(ValueError, match="invalid evaluation bounds"):
        validate_evaluation_context(_context(family, bounds={"poisson_empirical": invalid_bounds}))


def test_context_revalidates_constructed_limits_and_similarity(family: RecordingFamily) -> None:
    """Pydantic construction bypasses must not leak malformed common values into trials."""
    limits = GenerationLimits.model_construct(max_packets=0, max_output_bytes=20_000, max_wall_seconds=2.0)
    with pytest.raises(ValueError, match="invalid evaluation context GenerationLimits"):
        validate_evaluation_context(_context(family, trial_limits=limits))

    similarity = SIMILARITY.model_copy(update={"acf_lag_weights": ()})
    with pytest.raises(ValueError, match="invalid evaluation context SimilarityConfig"):
        validate_evaluation_context(_context(family, similarity=similarity))


def test_evaluation_requires_raw_then_validated_context_types(family: RecordingFamily) -> None:
    """Skipping or repeating the one shared validation pass is a caller defect."""
    raw = _context(family)
    validated = validate_evaluation_context(raw)
    with pytest.raises(TypeError, match="unvalidated"):
        validate_evaluation_context(cast(Any, validated))
    with pytest.raises(TypeError, match="ValidatedEvaluationContext"):
        evaluate_candidate(PENDING_POISSON, cast(Any, raw))


def test_context_requires_an_exact_immutable_traffic_trace(family: RecordingFamily) -> None:
    """A caller-owned event list could change the shared reference between candidates."""
    context = _context(family, reference=cast(Any, list(REFERENCE)))

    with pytest.raises(TypeError, match="reference must be a TrafficTrace"):
        validate_evaluation_context(context)


def test_malformed_generation_return_aborts_as_a_family_defect(family: RecordingFamily) -> None:
    """A non-GenerationResult return is not one of the six candidate-science failures."""
    family.results[7] = object()

    with pytest.raises(TypeError, match="GenerationResult"):
        evaluate_candidate(PENDING_POISSON, validate_evaluation_context(_context(family)))


def test_all_generated_sample_preconditions_are_reported() -> None:
    """An empty generated trace identifies every configured method lacking samples."""
    with pytest.raises(
        CandidateEvaluationError,
        match=(
            "frame_size_ks.*iat_ks.*multiscale_rate.*cramer_von_mises.*"
            "anderson_darling.*jensen_shannon.*approximate_mmd"
        ),
    ) as captured:
        validate_candidate_similarity_preconditions(EMPTY_TRACE, SIMILARITY, seed=7)

    assert captured.value.kind == "similarity_precondition"
    assert captured.value.seed == 7


@pytest.mark.parametrize(
    "zero_weight_method",
    [
        "frame_size_ks",
        "iat_ks",
        "autocorrelation",
        "multiscale_rate",
        "cramer_von_mises",
        "anderson_darling",
        "jensen_shannon",
        "approximate_mmd",
    ],
)
def test_zero_weight_method_preconditions_remain_mandatory(
    zero_weight_method: str,
) -> None:
    """A zero aggregation weight must not bypass any component's candidate precondition."""
    similarity = _similarity_with_zero_weight(zero_weight_method)

    with pytest.raises(CandidateEvaluationError) as captured:
        validate_candidate_similarity_preconditions(EMPTY_TRACE, similarity, seed=7)

    assert captured.value.kind == "similarity_precondition"
    assert captured.value.seed == 7
    assert zero_weight_method in captured.value.detail


@pytest.mark.parametrize(
    ("zero_weight_method", "generated"),
    [
        ("iat_ks", (TraceEvent(0.0, Direction.OUTBOUND, 60),)),
        ("cramer_von_mises", (TraceEvent(0.0, Direction.OUTBOUND, 60),)),
        ("anderson_darling", (TraceEvent(0.0, Direction.OUTBOUND, 60),)),
        ("jensen_shannon", (TraceEvent(0.0, Direction.OUTBOUND, 60),)),
        ("approximate_mmd", (TraceEvent(0.0, Direction.OUTBOUND, 60),)),
        (
            "autocorrelation",
            (TraceEvent(0.0, Direction.OUTBOUND, 60), TraceEvent(1.0, Direction.INBOUND, 60)),
        ),
    ],
)
def test_zero_weight_method_preconditions_invalidate_evaluable_candidates(
    family: RecordingFamily,
    zero_weight_method: str,
    generated: tuple[TraceEvent, ...],
) -> None:
    """A complete candidate still becomes invalid when a zero-weight method lacks its required samples."""
    similarity = _similarity_with_zero_weight(zero_weight_method)
    family.results[7] = GenerationResult(True, TrafficTrace.from_events(generated))

    evaluated = evaluate_candidate(
        PENDING_POISSON, validate_evaluation_context(_context(family, similarity=similarity))
    )

    assert evaluated.invalid is not None
    assert evaluated.invalid.kind == "similarity_precondition"
    assert evaluated.invalid.seed == 7
    assert zero_weight_method in evaluated.invalid.detail


@pytest.mark.parametrize(
    ("zero_weight_method", "component_name"),
    [
        ("frame_size_ks", "frame_size_ks"),
        ("iat_ks", "iat_ks"),
        ("autocorrelation", "autocorrelation_similarity"),
        ("multiscale_rate", "multiscale_rate_similarity"),
        ("cramer_von_mises", "cramer_von_mises_similarity"),
        ("anderson_darling", "anderson_darling_similarity"),
        ("jensen_shannon", "jensen_shannon_similarity"),
        ("approximate_mmd", "approximate_mmd_similarity"),
    ],
)
def test_zero_weight_component_failure_invalidates_a_complete_candidate(
    family: RecordingFamily,
    monkeypatch: pytest.MonkeyPatch,
    zero_weight_method: str,
    component_name: str,
) -> None:
    """A complete candidate must classify an expected failure from every zero-weight metric."""
    similarity = _similarity_with_zero_weight(zero_weight_method)
    validated = validate_evaluation_context(_context(family, similarity=similarity))
    failure = TrafficlabError(
        f"{zero_weight_method} component failed",
        corrective_action=f"repair {zero_weight_method} evidence",
    )

    def fail_component(*_args: object) -> Any:
        raise failure

    monkeypatch.setattr(comparison, component_name, fail_component)

    evaluated = evaluate_candidate(PENDING_POISSON, validated)

    assert evaluated.status == "invalid"
    assert evaluated.fitness == 0.0
    assert evaluated.trials == ()
    assert evaluated.invalid == CandidateFailure(
        kind="similarity_precondition",
        seed=7,
        detail=f"{zero_weight_method} component failed",
        stage="fit",
        affected_evidence="candidate similarity",
        evidence_state="diagnostic_only",
        corrective_action=f"repair {zero_weight_method} evidence",
        authority="primary",
    )
    assert failure.corrective_action == f"repair {zero_weight_method} evidence"


def test_pending_candidate_without_genes_is_invalid_without_family_call(family: RecordingFamily) -> None:
    """A missing chromosome is a deterministic repair failure, not a family invocation."""
    pending = replace(PENDING_POISSON, genes=None)

    evaluated = evaluate_candidate(pending, validate_evaluation_context(_context(family)))

    assert evaluated.invalid == CandidateFailure(
        kind="repair",
        seed=None,
        detail="candidate has no genes",
        stage="fit",
        affected_evidence="candidate genes",
        evidence_state="diagnostic_only",
        corrective_action="provide canonical candidate genes",
        authority="primary",
    )
    assert family.repair_calls == family.fit_calls == 0


def test_candidate_input_state_and_family_membership_are_not_silently_coerced(family: RecordingFamily) -> None:
    """Reevaluation and disabled-family candidates indicate caller defects and must abort."""
    validated = validate_evaluation_context(_context(family))
    with pytest.raises(ValueError, match="pending"):
        evaluate_candidate(replace(PENDING_POISSON, status="valid", fitness=0.5), validated)
    empty_families: dict[FamilyName, ModelFamily] = {}
    empty_bounds: dict[FamilyName, FamilyBounds] = {}
    other_context = replace(
        validated,
        families=MappingProxyType(empty_families),
        bounds=MappingProxyType(empty_bounds),
    )
    with pytest.raises(ValueError, match="enabled"):
        evaluate_candidate(PENDING_POISSON, other_context)
    with pytest.raises(TypeError, match="candidate must be"):
        evaluate_candidate(cast(Any, object()), validated)


def test_final_validation_rejects_nonvalid_candidate_and_bad_seed(family: RecordingFamily) -> None:
    """Only the stored valid winner and one exact nonnegative seed can enter final validation."""
    validated = validate_evaluation_context(_context(family))
    with pytest.raises(TrafficlabError, match="final validation"):
        evaluate_final(PENDING_POISSON, validated, final_seed=101)
    with pytest.raises(ValueError, match="final seed"):
        evaluate_final(replace(PENDING_POISSON, status="valid", fitness=0.5), validated, final_seed=cast(Any, True))
    with pytest.raises(ValueError, match="fresh"):
        evaluate_final(replace(PENDING_POISSON, status="valid", fitness=0.5), validated, final_seed=7)
    with pytest.raises(TypeError, match="candidate must be"):
        evaluate_final(cast(Any, object()), validated, final_seed=101)


def test_final_classified_fit_failure_is_stage_fatal(family: RecordingFamily) -> None:
    """A selected winner's direct fit failure cannot trigger candidate reselection."""
    family.fit_error = _trafficlab_error("fit rejected")
    valid = replace(PENDING_POISSON, status="valid", fitness=0.75)

    with pytest.raises(TrafficlabError, match="final validation.*fit rejected"):
        evaluate_final(valid, validate_evaluation_context(_context(family)), final_seed=101)


def test_final_validation_translates_a_classified_component_failure(
    family: RecordingFamily,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final validation must preserve the classified component cause and its stage corrective action."""
    validated = validate_evaluation_context(_context(family))
    error = _trafficlab_error("evaluator defect")

    def fail(*_args: object) -> Any:
        raise error

    monkeypatch.setattr(evaluation, "evaluate_fitness", fail)
    valid = replace(PENDING_POISSON, status="valid", fitness=0.75)

    with pytest.raises(TrafficlabError, match="final validation failed: evaluator defect") as captured:
        evaluate_final(valid, validated, final_seed=101)

    assert captured.value.corrective_action == (
        "inspect the selected winner and increase trial generation limits if necessary"
    )
    cause = captured.value.__cause__
    assert type(cause) is CandidateEvaluationError
    assert cause == CandidateEvaluationError(
        "similarity_precondition",
        101,
        "evaluator defect",
        stage="fit",
        affected_evidence="candidate similarity",
        evidence_state="diagnostic_only",
        corrective_action="test corrective action",
        authority="primary",
    )
    assert cause.__cause__ is error
