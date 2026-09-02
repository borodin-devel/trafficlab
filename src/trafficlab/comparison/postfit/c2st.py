"""Deterministic time-blocked classical classifier two-sample diagnostic."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

import numpy as np
from numpy.typing import NDArray
from scipy import optimize as scipy_optimize  # pyright: ignore[reportMissingTypeStubs]

from trafficlab.common.config import C2stSettings
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import TrafficTrace, validate_traffic_trace
from trafficlab.comparison.similarity.common import JsonDiagnostics, SimilarityResult, validate_observation_window

FEATURE_NAMES = (
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
_QUANTILES = (0.25, 0.5, 0.75)
_ROUNDING_TOLERANCE = 1e-15


class _OptimizeResult(Protocol):
    success: bool
    x: NDArray[np.float64]
    nit: int
    fun: float
    message: object


class _Minimize(Protocol):
    def __call__(
        self,
        fun: Callable[..., object],
        x0: NDArray[np.float64],
        args: tuple[object, ...],
        *,
        method: str,
        jac: bool,
        tol: float,
        options: dict[str, int | float],
    ) -> _OptimizeResult: ...


minimize = cast(_Minimize, scipy_optimize.minimize)  # pyright: ignore[reportUnknownMemberType]


@dataclass(frozen=True, slots=True)
class GuardedFold:
    evaluation_indexes: tuple[int, ...]
    guard_indexes: tuple[int, ...]
    training_indexes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class LogisticFit:
    intercept: float
    coefficients: tuple[float, ...]
    iterations: int
    final_loss: float
    converged: bool


def _window_count(W: float, width: float, maximum_window_count: int) -> int:
    if type(width) is not float or not math.isfinite(width) or width <= 0.0:
        raise TrafficlabError(
            "invalid C2ST window width: it must be a finite positive float",
            corrective_action="configure a finite positive C2ST window width",
        )
    if type(maximum_window_count) is not int or maximum_window_count <= 0:
        raise TrafficlabError(
            "invalid C2ST maximum window count: it must be a positive integer",
            corrective_action="configure a positive bounded C2ST maximum window count",
        )
    quotient = W / width
    if not math.isfinite(quotient):
        raise TrafficlabError(
            "invalid C2ST window width: W divided by the width must be finite",
            corrective_action="configure a wider C2ST window",
        )
    nearest = round(quotient)
    if abs(quotient - nearest) <= 4.0 * math.ulp(quotient):
        quotient = float(nearest)
    count = math.ceil(quotient)
    if count > maximum_window_count:
        raise TrafficlabError(
            "invalid C2ST window count: window count exceeds the configured cap",
            corrective_action="configure a wider C2ST window or a larger bounded maximum window count",
        )
    return count


def _summary(values: NDArray[np.generic]) -> tuple[float, float, float, float]:
    if not len(values):
        return (0.0, 0.0, 0.0, 0.0)
    numeric = np.asarray(values, dtype=np.float64)
    quantiles = np.quantile(numeric, _QUANTILES, method="linear")
    return (
        float(np.mean(numeric)),
        float(quantiles[0]),
        float(quantiles[1]),
        float(quantiles[2]),
    )


def _expit(values: NDArray[np.float64]) -> NDArray[np.float64]:
    result = np.empty_like(values)
    nonnegative = values >= 0.0
    result[nonnegative] = 1.0 / (1.0 + np.exp(-values[nonnegative]))
    exponentials = np.exp(values[~nonnegative])
    result[~nonnegative] = exponentials / (1.0 + exponentials)
    return result


def _extract_window_features(
    trace: TrafficTrace,
    *,
    W: float,
    width: float,
    maximum_window_count: int,
) -> NDArray[np.float64]:
    """Extract the frozen v1 feature vector from each closed-window time block."""
    window = validate_observation_window(W)
    checked = validate_traffic_trace(trace, minimum_events=1, trace_name="C2ST", window=window)
    count = _window_count(window, width, maximum_window_count)
    indexes = np.floor(checked.timestamps / width).astype(np.int64)
    indexes[indexes >= count] = count - 1
    rows: list[tuple[float, ...]] = []
    for index in range(count):
        mask = indexes == index
        timestamps = checked.timestamps[mask]
        directions = checked.directions[mask]
        frame_lengths = checked.frame_lengths[mask]
        outbound = directions == 0
        inbound = directions == 1
        size_mean, size_q25, size_q50, size_q75 = _summary(frame_lengths)
        iats = np.diff(timestamps)
        positive_iats = iats[iats > 0.0]
        iat_mean, iat_q25, iat_q50, iat_q75 = _summary(positive_iats)
        zero_iat_count = int(np.count_nonzero(iats == 0.0))
        rows.append(
            (
                float(np.count_nonzero(outbound)),
                float(np.count_nonzero(inbound)),
                float(np.sum(frame_lengths[outbound], dtype=np.uint64)),
                float(np.sum(frame_lengths[inbound], dtype=np.uint64)),
                size_mean,
                size_q25,
                size_q50,
                size_q75,
                iat_mean,
                iat_q25,
                iat_q50,
                iat_q75,
                float(zero_iat_count),
                float(len(timestamps) - zero_iat_count),
            )
        )
    features = np.asarray(rows, dtype=np.float64)
    if features.shape != (count, len(FEATURE_NAMES)) or not np.all(np.isfinite(features)):
        raise TrafficlabError(
            "invalid C2ST window features: extraction produced an unsafe matrix",
            corrective_action="provide finite bounded canonical traces and post-fit settings",
        )
    return features


def _guarded_folds(window_count: int, fold_count: int, guard_window_count: int) -> tuple[GuardedFold, ...]:
    """Partition window indexes into contiguous evaluation blocks and excluded guards."""
    if (
        type(window_count) is not int
        or type(fold_count) is not int
        or type(guard_window_count) is not int
        or window_count <= 0
        or fold_count < 2
        or guard_window_count < 0
        or fold_count > window_count
    ):
        raise TrafficlabError(
            "invalid C2ST guarded folds: settings cannot form the requested folds",
            corrective_action="configure at least two folds with enough windows and nonnegative guards",
        )
    base, remainder = divmod(window_count, fold_count)
    folds: list[GuardedFold] = []
    start = 0
    all_indexes = set(range(window_count))
    for fold_index in range(fold_count):
        size = base + int(fold_index < remainder)
        stop = start + size
        evaluation = tuple(range(start, stop))
        guard_start = max(0, start - guard_window_count)
        guard_stop = min(window_count, stop + guard_window_count)
        guard = tuple(index for index in range(guard_start, guard_stop) if index < start or index >= stop)
        training = tuple(sorted(all_indexes.difference(evaluation).difference(guard)))
        if not evaluation or not training:
            raise TrafficlabError(
                "invalid C2ST guarded folds: every fold requires evaluation and guarded training windows",
                corrective_action="configure more windows, fewer folds, or smaller guards",
            )
        folds.append(GuardedFold(evaluation, guard, training))
        start = stop
    return tuple(folds)


def _reference_standardize(
    reference_training: NDArray[np.float64],
    arrays: tuple[NDArray[np.float64], ...],
) -> tuple[NDArray[np.float64], NDArray[np.float64], tuple[NDArray[np.float64], ...]]:
    """Freeze a population transform from reference training rows and apply it unchanged."""
    if (
        reference_training.ndim != 2
        or not reference_training.shape[0]
        or not reference_training.shape[1]
        or not np.all(np.isfinite(reference_training))
        or any(
            array.ndim != 2
            or array.shape[1] != reference_training.shape[1]
            or not np.all(np.isfinite(array))
            for array in arrays
        )
    ):
        raise TrafficlabError(
            "invalid C2ST reference standardization matrix",
            corrective_action="provide finite feature matrices with one shared nonempty feature width",
        )
    mean = np.mean(reference_training, axis=0, dtype=np.float64)
    raw_scale = np.std(reference_training, axis=0, ddof=0, dtype=np.float64)
    scale = np.where(raw_scale > 0.0, raw_scale, 1.0)
    transformed = tuple(np.asarray((array - mean) / scale, dtype=np.float64) for array in arrays)
    if not np.all(np.isfinite(mean)) or not np.all(np.isfinite(scale)) or any(
        not np.all(np.isfinite(value)) for value in transformed
    ):
        raise TrafficlabError(
            "invalid C2ST reference standardization arithmetic",
            corrective_action="provide finite bounded feature values",
        )
    return mean, scale, transformed


def _logistic_loss_gradient(
    parameters: NDArray[np.float64],
    design: NDArray[np.float64],
    labels: NDArray[np.float64],
    regularization: float,
) -> tuple[float, NDArray[np.float64]]:
    logits = design @ parameters
    loss = float(np.mean(np.logaddexp(0.0, logits) - labels * logits))
    loss += 0.5 * regularization * float(parameters[1:] @ parameters[1:])
    gradient = np.asarray(design.T @ (_expit(logits) - labels) / len(labels), dtype=np.float64)
    gradient[1:] += regularization * parameters[1:]
    return loss, gradient


def _fit_logistic(
    features: NDArray[np.float64], labels: NDArray[np.float64], settings: C2stSettings
) -> LogisticFit:
    """Fit one deterministic L2 logistic model from zero with an analytic gradient."""
    if (
        features.ndim != 2
        or not features.shape[0]
        or not features.shape[1]
        or labels.ndim != 1
        or len(labels) != len(features)
        or not np.all(np.isfinite(features))
        or not np.all(np.isfinite(labels))
        or not np.all((labels == 0.0) | (labels == 1.0))
        or int(np.count_nonzero(labels == 0.0)) != int(np.count_nonzero(labels == 1.0))
    ):
        raise TrafficlabError(
            "invalid C2ST logistic design: labels must be balanced binary values over finite features",
            corrective_action="provide balanced reference/generated training windows",
        )
    design = np.column_stack((np.ones(len(features), dtype=np.float64), features))
    result = minimize(
        _logistic_loss_gradient,
        np.zeros(design.shape[1], dtype=np.float64),
        args=(design, labels, settings.l2_regularization),
        method="L-BFGS-B",
        jac=True,
        tol=settings.tolerance,
        options={
            "maxiter": settings.maximum_iterations,
            "ftol": settings.tolerance,
            "gtol": settings.tolerance,
            "maxls": 20,
        },
    )
    parameters = np.asarray(result.x, dtype=np.float64)
    final_loss = float(result.fun)
    iterations = int(result.nit)
    if (
        not bool(result.success)
        or parameters.shape != (design.shape[1],)
        or not np.all(np.isfinite(parameters))
        or not math.isfinite(final_loss)
        or iterations < 0
        or iterations > settings.maximum_iterations
    ):
        raise TrafficlabError(
            f"invalid C2ST logistic solver result: {result.message}",
            corrective_action="increase the C2ST iteration limit or correct the finite feature/settings inputs",
        )
    return LogisticFit(
        intercept=float(parameters[0]),
        coefficients=tuple(float(value) for value in parameters[1:]),
        iterations=iterations,
        final_loss=final_loss,
        converged=True,
    )


def _tie_aware_auc(labels: NDArray[np.float64], scores: NDArray[np.float64]) -> float:
    """Return the Mann--Whitney AUC with half credit for exact score ties."""
    if (
        labels.ndim != 1
        or scores.ndim != 1
        or len(labels) != len(scores)
        or not len(labels)
        or not np.all(np.isfinite(scores))
        or not np.all((labels == 0.0) | (labels == 1.0))
    ):
        raise TrafficlabError(
            "invalid C2ST AUC inputs",
            corrective_action="provide equal-length finite scores and exact binary labels",
        )
    negative_count = int(np.count_nonzero(labels == 0.0))
    positive_count = int(np.count_nonzero(labels == 1.0))
    if not negative_count or not positive_count:
        raise TrafficlabError(
            "invalid C2ST AUC inputs: both classes are required",
            corrective_action="provide balanced reference and generated evaluation windows",
        )
    order = np.argsort(scores, kind="stable")
    sorted_scores = scores[order]
    sorted_labels = labels[order]
    prior_negatives = 0
    favorable = 0.0
    start = 0
    while start < len(sorted_scores):
        stop = start + 1
        while stop < len(sorted_scores) and sorted_scores[stop] == sorted_scores[start]:
            stop += 1
        group = sorted_labels[start:stop]
        group_negatives = int(np.count_nonzero(group == 0.0))
        group_positives = len(group) - group_negatives
        favorable += group_positives * prior_negatives + 0.5 * group_positives * group_negatives
        prior_negatives += group_negatives
        start = stop
    return favorable / (positive_count * negative_count)


def _balanced_accuracy(labels: NDArray[np.float64], probabilities: NDArray[np.float64]) -> float:
    predictions = probabilities >= 0.5
    reference = labels == 0.0
    generated = labels == 1.0
    specificity = float(np.mean(~predictions[reference]))
    sensitivity = float(np.mean(predictions[generated]))
    return (specificity + sensitivity) / 2.0


def _probabilities(features: NDArray[np.float64], fit: LogisticFit) -> NDArray[np.float64]:
    coefficients = np.asarray(fit.coefficients, dtype=np.float64)
    return _expit(np.asarray(fit.intercept + features @ coefficients, dtype=np.float64))


def _bounded_score(value: float) -> float:
    if 0.0 <= value <= 1.0:
        return value
    if -_ROUNDING_TOLERANCE <= value < 0.0:
        return 0.0
    if 1.0 < value <= 1.0 + _ROUNDING_TOLERANCE:
        return 1.0
    raise TrafficlabError(
        "invalid C2ST similarity score arithmetic",
        corrective_action="report the deterministic C2ST arithmetic defect",
    )


def classical_c2st_diagnostic(
    reference: TrafficTrace,
    generated: TrafficTrace,
    W: float,
    settings: C2stSettings,
) -> SimilarityResult:
    """Evaluate deterministic guarded out-of-fold classifier distinguishability."""
    window = validate_observation_window(W)
    reference_trace = validate_traffic_trace(reference, minimum_events=1, trace_name="reference", window=window)
    generated_trace = validate_traffic_trace(generated, minimum_events=1, trace_name="generated", window=window)
    reference_features = _extract_window_features(
        reference_trace,
        W=window,
        width=settings.window_width_seconds,
        maximum_window_count=settings.maximum_window_count,
    )
    generated_features = _extract_window_features(
        generated_trace,
        W=window,
        width=settings.window_width_seconds,
        maximum_window_count=settings.maximum_window_count,
    )
    folds = _guarded_folds(len(reference_features), settings.fold_count, settings.guard_window_count)
    fold_diagnostics: list[dict[str, object]] = []
    out_of_fold_labels: list[NDArray[np.float64]] = []
    out_of_fold_probabilities: list[NDArray[np.float64]] = []
    fits: list[LogisticFit] = []
    for fold_index, fold in enumerate(folds):
        training_indexes = list(fold.training_indexes)
        evaluation_indexes = list(fold.evaluation_indexes)
        reference_training = reference_features[training_indexes]
        generated_training = generated_features[training_indexes]
        reference_evaluation = reference_features[evaluation_indexes]
        generated_evaluation = generated_features[evaluation_indexes]
        mean, scale, transformed = _reference_standardize(
            reference_training,
            (reference_training, generated_training, reference_evaluation, generated_evaluation),
        )
        standardized_reference_training, standardized_generated_training, standardized_reference_evaluation, standardized_generated_evaluation = transformed
        training_features = np.vstack((standardized_reference_training, standardized_generated_training))
        training_labels = np.concatenate(
            (
                np.zeros(len(standardized_reference_training), dtype=np.float64),
                np.ones(len(standardized_generated_training), dtype=np.float64),
            )
        )
        fit = _fit_logistic(training_features, training_labels, settings)
        evaluation_features = np.vstack((standardized_reference_evaluation, standardized_generated_evaluation))
        evaluation_labels = np.concatenate(
            (
                np.zeros(len(standardized_reference_evaluation), dtype=np.float64),
                np.ones(len(standardized_generated_evaluation), dtype=np.float64),
            )
        )
        probabilities = _probabilities(evaluation_features, fit)
        fold_auc = _tie_aware_auc(evaluation_labels, probabilities)
        fold_balanced_accuracy = _balanced_accuracy(evaluation_labels, probabilities)
        fits.append(fit)
        out_of_fold_labels.append(evaluation_labels)
        out_of_fold_probabilities.append(probabilities)
        fold_diagnostics.append(
            {
                "fold_index": fold_index,
                "training_window_indexes": fold.training_indexes,
                "guard_window_indexes": fold.guard_indexes,
                "evaluation_window_indexes": fold.evaluation_indexes,
                "training_reference_count": len(reference_training),
                "training_generated_count": len(generated_training),
                "evaluation_reference_count": len(reference_evaluation),
                "evaluation_generated_count": len(generated_evaluation),
                "reference_training_mean": tuple(float(value) for value in mean),
                "reference_training_scale": tuple(float(value) for value in scale),
                "intercept": fit.intercept,
                "coefficients": fit.coefficients,
                "iterations": fit.iterations,
                "final_loss": fit.final_loss,
                "converged": fit.converged,
                "auc": fold_auc,
                "balanced_accuracy": fold_balanced_accuracy,
            }
        )
    labels = np.concatenate(out_of_fold_labels)
    probabilities = np.concatenate(out_of_fold_probabilities)
    auc = _tie_aware_auc(labels, probabilities)
    balanced_accuracy = _balanced_accuracy(labels, probabilities)
    coefficients = tuple(
        math.fsum(fit.coefficients[index] for fit in fits) / len(fits) for index in range(len(FEATURE_NAMES))
    )
    intercept = math.fsum(fit.intercept for fit in fits) / len(fits)
    score = _bounded_score(1.0 - 2.0 * abs(auc - 0.5))
    diagnostics: JsonDiagnostics = cast(
        JsonDiagnostics,
        {
            "observation_window_seconds": window,
            "feature_version": settings.feature_version,
            "feature_names": FEATURE_NAMES,
            "window_width_seconds": settings.window_width_seconds,
            "window_count_per_trace": len(reference_features),
            "fold_count": settings.fold_count,
            "guard_window_count": settings.guard_window_count,
            "maximum_window_count": settings.maximum_window_count,
            "l2_regularization": settings.l2_regularization,
            "maximum_iterations": settings.maximum_iterations,
            "tolerance": settings.tolerance,
            "solver": "scipy.optimize.minimize/L-BFGS-B",
            "intercept": intercept,
            "coefficients": coefficients,
            "folds": tuple(fold_diagnostics),
            "out_of_fold_reference_count": len(reference_features),
            "out_of_fold_generated_count": len(generated_features),
            "balanced_accuracy": balanced_accuracy,
            "auc": auc,
        },
    )
    return SimilarityResult(score=score, diagnostics=diagnostics)
