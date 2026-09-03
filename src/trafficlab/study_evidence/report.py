"""Accepted validation-study report schemas."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, StrictInt, model_validator

from trafficlab.comparison.diagnostics import (
    AndersonDarlingDiagnostic,
    ApproximateMmdDiagnostic,
    AutocorrelationDiagnostic,
    CramerVonMisesDiagnostic,
    FrameSizeDiagnostic,
    IatDiagnostic,
    JensenShannonDiagnostic,
    MultiscaleDiagnostic,
)
from trafficlab.study_evidence.protocol import (
    ExactFloat,
    ExactNumber,
    NonemptyString,
    NonnegativeFloat,
    NonnegativeInt,
    PositiveFloat,
    PositiveInt,
    RelativePath,
    Repeat,
    StrictStudyModel,
    StudyContentIdentity,
    UnitFloat,
    Workload,
    tuple_input,
)


class StudyMethodValues(StrictStudyModel):
    autocorrelation: UnitFloat
    frame_size_ks: UnitFloat
    iat_ks: UnitFloat
    multiscale_rate: UnitFloat
    cramer_von_mises: UnitFloat
    anderson_darling: UnitFloat
    jensen_shannon: UnitFloat
    approximate_mmd: UnitFloat


class StudyScore(StrictStudyModel):
    aggregate: UnitFloat
    methods: StudyMethodValues


class StudyDiagnostics(StrictStudyModel):
    autocorrelation: AutocorrelationDiagnostic
    frame_size_ks: FrameSizeDiagnostic
    iat_ks: IatDiagnostic
    multiscale_rate: MultiscaleDiagnostic
    cramer_von_mises: CramerVonMisesDiagnostic
    anderson_darling: AndersonDarlingDiagnostic
    jensen_shannon: JensenShannonDiagnostic
    approximate_mmd: ApproximateMmdDiagnostic


class StudyControlledWeightAnalysis(StrictStudyModel):
    alternative_aggregate: UnitFloat
    alternative_weights: StudyMethodValues
    baseline_aggregate: UnitFloat
    baseline_weights: StudyMethodValues
    components: StudyMethodValues
    diagnostics: StudyDiagnostics
    executed_methods: Annotated[
        tuple[NonemptyString, ...], Field(min_length=8, max_length=8), BeforeValidator(tuple_input)
    ]
    training_directory: RelativePath
    workload: Workload


class StudyWorkloadScore(StrictStudyModel):
    score: StudyScore
    workload: Workload


class StudyHeldOutScore(StudyWorkloadScore):
    observation_window_seconds: PositiveFloat


class StudyCandidateIdentifier(StrictStudyModel):
    birth_generation: NonnegativeInt
    birth_index: NonnegativeInt


class StudyInvalidCandidate(StrictStudyModel):
    affected_evidence: NonemptyString
    authority: Literal["primary", "secondary"]
    corrective_action: NonemptyString
    detail: NonemptyString
    evidence_state: Literal["not_published", "diagnostic_only", "preserved", "possibly_remaining"]
    family: Literal["markov_renewal", "mmpp", "poisson_empirical"]
    genes: Annotated[tuple[ExactNumber, ...], BeforeValidator(tuple_input)] | None
    identifier: StudyCandidateIdentifier
    kind: Literal["repair", "fit", "generation", "incomplete_generation", "similarity_precondition", "nonfinite_score"]
    seed: NonnegativeInt | None
    stage: NonemptyString


class StudyTrialLimits(StrictStudyModel):
    max_output_bytes: PositiveInt
    max_packets: PositiveInt
    max_wall_seconds: PositiveFloat


class StudyInvalidChromosomeDiagnostics(StrictStudyModel):
    invalid_candidates: Annotated[tuple[StudyInvalidCandidate, ...], BeforeValidator(tuple_input)]
    repeat: Repeat
    training_directory: RelativePath
    trial_limits: StudyTrialLimits
    workload: Workload


class StudyNaturalVariationPair(StrictStudyModel):
    forward: StudyScore
    left_repeat: Repeat
    reverse: StudyScore
    right_repeat: Repeat
    symmetric_mean: StudyScore


class StudyNaturalVariation(StrictStudyModel):
    pairs: Annotated[
        tuple[StudyNaturalVariationPair, ...], Field(min_length=3, max_length=3), BeforeValidator(tuple_input)
    ]
    symmetric_mean: StudyScore
    workload: Workload


class StudyPcg64CoreState(StrictStudyModel):
    state: Annotated[StrictInt, Field(ge=0, le=2**128 - 1)]
    inc: Annotated[StrictInt, Field(ge=0, le=2**128 - 1)]


class StudyRngState(StrictStudyModel):
    bit_generator: Literal["PCG64"]
    state: StudyPcg64CoreState
    has_uint32: Annotated[StrictInt, Field(ge=0, le=1)]
    uinteger: Annotated[StrictInt, Field(ge=0, le=2**32 - 1)]


class StudyBootstrapInterval(StrictStudyModel):
    confidence_level: UnitFloat
    generator: Literal["PCG64"]
    generator_state: StudyRngState
    lower_bound: ExactFloat
    method: Literal["percentile"]
    n_resamples: Literal[10_000]
    sample_size: Literal[3]
    seed: Literal[20_260_819]
    statistic: Literal["mean"]
    upper_bound: ExactFloat

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> Self:
        if self.confidence_level != 0.95:
            raise ValueError("bootstrap confidence_level must be 0.95")
        if self.lower_bound > self.upper_bound:
            raise ValueError("bootstrap lower_bound must not exceed upper_bound")
        return self


class StudyDescriptive(StrictStudyModel):
    bootstrap: StudyBootstrapInterval
    mean: NonnegativeFloat
    sample_variance: NonnegativeFloat


class StudyWinnerCounts(StrictStudyModel):
    markov_renewal: NonnegativeInt
    mmpp: NonnegativeInt
    poisson_empirical: NonnegativeInt


class StudyTrainingSummary(StrictStudyModel):
    runtime_seconds: StudyDescriptive
    selection_fitness: StudyDescriptive
    winner_family_count_variance: ExactNumber
    winner_family_counts: StudyWinnerCounts
    workload: Workload


class ValidationStudyReportInput(StrictStudyModel):
    """Typed report arithmetic inputs; the auditor still independently recomputes every value."""

    controlled_weight_analysis: Annotated[
        tuple[StudyControlledWeightAnalysis, ...], Field(min_length=3, max_length=3), BeforeValidator(tuple_input)
    ]
    formula: Literal["arithmetic_mean"]
    fresh_simulation: Annotated[
        tuple[StudyWorkloadScore, ...], Field(min_length=3, max_length=3), BeforeValidator(tuple_input)
    ]
    held_out: Annotated[tuple[StudyHeldOutScore, ...], Field(min_length=3, max_length=3), BeforeValidator(tuple_input)]
    invalid_chromosome_diagnostics: Annotated[
        tuple[StudyInvalidChromosomeDiagnostics, ...],
        Field(min_length=9, max_length=9),
        BeforeValidator(tuple_input),
    ]
    natural_variation: Annotated[
        tuple[StudyNaturalVariation, ...], Field(min_length=3, max_length=3), BeforeValidator(tuple_input)
    ]
    runtime_winner_variance: Annotated[
        tuple[StudyTrainingSummary, ...], Field(min_length=3, max_length=3), BeforeValidator(tuple_input)
    ]
    training: Annotated[
        tuple[StudyTrainingSummary, ...], Field(min_length=3, max_length=3), BeforeValidator(tuple_input)
    ]


class ValidationStudyReport(StrictStudyModel):
    """Published report root bound to the exact report-input bytes."""

    formula: Literal["arithmetic_mean"]
    report_inputs_identity: StudyContentIdentity
    summary: ValidationStudyReportInput


# Explicit plural alias matches the persisted filename without another model path.
ValidationStudyReportInputs = ValidationStudyReportInput
