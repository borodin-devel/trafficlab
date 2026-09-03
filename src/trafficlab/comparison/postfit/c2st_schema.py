"""Typed final-only C2ST and aggregate post-fit artifacts."""

import math
from typing import Annotated, Literal, Self, cast

from pydantic import BeforeValidator, StrictBool, StrictStr, model_validator

from trafficlab.comparison.diagnostics import (
    ExactFloat,
    FloatTuple,
    NonnegativeFloat,
    NonnegativeInt,
    PositiveFloat,
    PositiveInt,
    StrictArtifactModel,
    UnitFloat,
    require_close,
)
from trafficlab.comparison.postfit.schema import (
    AtLeastTwoInt,
    BoundedC2stWindowCount,
    FanoAllanDiagnostic,
    NonnegativeIntTuple,
    TransitionMatrixDiagnostic,
    maximum_fold_index_cells,
    snapped_window_count,
    tuple_input,
)
from trafficlab.comparison.similarity.common import JsonValue

POSTFIT_NAMES = ("fano_allan", "transition_matrix", "classical_c2st")


_C2ST_FEATURE_NAMES = (
    "outbound_packet_count",
    "inbound_packet_count",
    "outbound_byte_count",
    "inbound_byte_count",
    "frame_size_mean",
    "frame_size_q25",
    "frame_size_q50",
    "frame_size_q75",
    "positive_iat_mean",
    "positive_iat_q25",
    "positive_iat_q50",
    "positive_iat_q75",
    "zero_iat_count",
    "activity_count",
)


class C2stFoldDiagnostic(StrictArtifactModel):
    fold_index: NonnegativeInt
    training_window_indexes: NonnegativeIntTuple
    guard_window_indexes: NonnegativeIntTuple
    evaluation_window_indexes: NonnegativeIntTuple
    training_reference_count: PositiveInt
    training_generated_count: PositiveInt
    evaluation_reference_count: PositiveInt
    evaluation_generated_count: PositiveInt
    reference_training_mean: FloatTuple
    reference_training_scale: FloatTuple
    intercept: ExactFloat
    coefficients: FloatTuple
    iterations: NonnegativeInt
    final_loss: NonnegativeFloat
    converged: StrictBool
    auc: UnitFloat
    balanced_accuracy: UnitFloat

    @model_validator(mode="after")
    def validate_partition(self) -> Self:
        training = set(self.training_window_indexes)
        guard = set(self.guard_window_indexes)
        evaluation = set(self.evaluation_window_indexes)
        if (
            len(training) != len(self.training_window_indexes)
            or len(guard) != len(self.guard_window_indexes)
            or len(evaluation) != len(self.evaluation_window_indexes)
            or training & guard
            or training & evaluation
            or guard & evaluation
        ):
            raise ValueError("C2ST fold indexes must be unique and pairwise disjoint")
        if self.training_reference_count != len(training) or self.training_generated_count != len(training):
            raise ValueError("C2ST fold training labels must be balanced over training indexes")
        if self.evaluation_reference_count != len(evaluation) or self.evaluation_generated_count != len(evaluation):
            raise ValueError("C2ST fold evaluation labels must be balanced over evaluation indexes")
        if not self.converged:
            raise ValueError("C2ST every retained fold must have converged")
        return self


class C2stDiagnostic(StrictArtifactModel):
    observation_window_seconds: PositiveFloat
    feature_version: Literal["window-v1"]
    feature_names: Annotated[tuple[StrictStr, ...], BeforeValidator(tuple_input)]
    window_width_seconds: PositiveFloat
    window_count_per_trace: BoundedC2stWindowCount
    fold_count: AtLeastTwoInt
    guard_window_count: NonnegativeInt
    maximum_window_count: BoundedC2stWindowCount
    l2_regularization: PositiveFloat
    maximum_iterations: PositiveInt
    tolerance: PositiveFloat
    solver: Literal["scipy.optimize.minimize/L-BFGS-B"]
    intercept: ExactFloat
    coefficients: FloatTuple
    folds: Annotated[tuple[C2stFoldDiagnostic, ...], BeforeValidator(tuple_input)]
    out_of_fold_reference_count: PositiveInt
    out_of_fold_generated_count: PositiveInt
    balanced_accuracy: UnitFloat
    auc: UnitFloat

    @model_validator(mode="after")
    def validate_solver_evidence(self) -> Self:
        if self.feature_names != _C2ST_FEATURE_NAMES:
            raise ValueError("C2ST feature_names must equal the frozen window-v1 feature order")
        expected_window_count = snapped_window_count(
            self.observation_window_seconds,
            self.window_width_seconds,
            name="C2ST",
        )
        if self.window_count_per_trace != expected_window_count:
            raise ValueError("C2ST window_count_per_trace must equal ceil(snap(W / width))")
        if self.window_count_per_trace > self.maximum_window_count:
            raise ValueError("C2ST window count exceeds the retained cap")
        if self.fold_count > self.window_count_per_trace:
            raise ValueError("C2ST fold_count must not exceed the window count")
        if self.fold_count * self.window_count_per_trace > maximum_fold_index_cells:
            raise ValueError("C2ST total fold evidence exceeds the fixed cap")
        if len(self.folds) != self.fold_count:
            raise ValueError("C2ST folds must match fold_count")
        if (
            self.out_of_fold_reference_count != self.window_count_per_trace
            or self.out_of_fold_generated_count != self.window_count_per_trace
        ):
            raise ValueError("C2ST out-of-fold counts must equal the per-trace window count")
        if len(self.coefficients) != len(self.feature_names):
            raise ValueError("C2ST coefficients must match feature_names")
        base, remainder = divmod(self.window_count_per_trace, self.fold_count)
        start = 0
        for expected_fold_index, fold in enumerate(self.folds):
            if fold.fold_index != expected_fold_index:
                raise ValueError("C2ST fold_index values must be canonical and ordered")
            stop = start + base + int(expected_fold_index < remainder)
            guard_start = max(0, start - self.guard_window_count)
            guard_stop = min(self.window_count_per_trace, stop + self.guard_window_count)
            expected_evaluation = tuple(range(start, stop))
            expected_guard = (*range(guard_start, start), *range(stop, guard_stop))
            expected_training = (*range(guard_start), *range(guard_stop, self.window_count_per_trace))
            if (
                fold.evaluation_window_indexes != expected_evaluation
                or fold.guard_window_indexes != expected_guard
                or fold.training_window_indexes != expected_training
            ):
                raise ValueError("C2ST fold indexes must equal the exact ordered divmod partition")
            start = stop
            if fold.iterations > self.maximum_iterations:
                raise ValueError("C2ST fold iterations exceed maximum_iterations")
            if not (
                len(fold.reference_training_mean)
                == len(fold.reference_training_scale)
                == len(fold.coefficients)
                == len(self.feature_names)
            ) or any(scale <= 0.0 for scale in fold.reference_training_scale):
                raise ValueError("C2ST fold transform and coefficient vectors must match positive feature scales")
        expected_coefficients = tuple(
            math.fsum(fold.coefficients[index] for fold in self.folds) / len(self.folds)
            for index in range(len(self.feature_names))
        )
        for index, (value, expected) in enumerate(zip(self.coefficients, expected_coefficients, strict=True)):
            require_close(value, expected, name=f"C2ST coefficient {index}")
        require_close(
            self.intercept,
            math.fsum(fold.intercept for fold in self.folds) / len(self.folds),
            name="C2ST intercept",
        )
        return self


class FanoAllanComparison(StrictArtifactModel):
    diagnostics: FanoAllanDiagnostic
    score: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        require_close(self.score, 1.0 - self.diagnostics.discrepancy, name="Fano/Allan score")
        return self

    def as_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], self.model_dump(mode="json"))


class TransitionMatrixComparison(StrictArtifactModel):
    diagnostics: TransitionMatrixDiagnostic
    score: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        require_close(self.score, 1.0 - self.diagnostics.discrepancy, name="transition score")
        return self

    def as_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], self.model_dump(mode="json"))


class C2stComparison(StrictArtifactModel):
    diagnostics: C2stDiagnostic
    score: UnitFloat

    @model_validator(mode="after")
    def score_matches_diagnostics(self) -> Self:
        expected = 1.0 - 2.0 * abs(self.diagnostics.auc - 0.5)
        require_close(self.score, expected, name="C2ST score")
        return self

    def as_dict(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], self.model_dump(mode="json"))


type PostfitComparison = FanoAllanComparison | TransitionMatrixComparison | C2stComparison


class PostfitDiagnostics(StrictArtifactModel):
    classical_c2st: C2stComparison
    fano_allan: FanoAllanComparison
    transition_matrix: TransitionMatrixComparison

    def __getitem__(self, name: str) -> PostfitComparison:
        if name not in POSTFIT_NAMES:
            raise KeyError(name)
        return cast(PostfitComparison, getattr(self, name))

    def items(self) -> tuple[tuple[str, PostfitComparison], ...]:
        return tuple((name, self[name]) for name in POSTFIT_NAMES)
