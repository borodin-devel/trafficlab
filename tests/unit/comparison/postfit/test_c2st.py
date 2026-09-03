"""Independent hand oracles for the deterministic blocked classical C2ST."""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest
from numpy.typing import NDArray

import trafficlab.comparison.postfit.c2st as c2st
from trafficlab.common.config import C2stSettings
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace
from trafficlab.comparison.postfit.c2st import FEATURE_NAMES, classical_c2st_diagnostic

_extract_window_features = c2st._extract_window_features  # pyright: ignore[reportPrivateUsage]
_fit_logistic = c2st._fit_logistic  # pyright: ignore[reportPrivateUsage]
_guarded_folds = c2st._guarded_folds  # pyright: ignore[reportPrivateUsage]
_logistic_loss_gradient = c2st._logistic_loss_gradient  # pyright: ignore[reportPrivateUsage]
_reference_standardize = c2st._reference_standardize  # pyright: ignore[reportPrivateUsage]
_tie_aware_auc = c2st._tie_aware_auc  # pyright: ignore[reportPrivateUsage]


def _settings(**updates: object) -> C2stSettings:
    values: dict[str, object] = {
        "feature_version": "window-v1",
        "window_width_seconds": 1.0,
        "fold_count": 3,
        "guard_window_count": 1,
        "maximum_window_count": 64,
        "l2_regularization": 1.0,
        "maximum_iterations": 200,
        "tolerance": 1e-9,
    }
    values.update(updates)
    return C2stSettings.model_validate(values)


def _trace(events: tuple[tuple[float, Direction, int], ...]) -> TrafficTrace:
    return TrafficTrace.from_events(TraceEvent(timestamp, direction, length) for timestamp, direction, length in events)


def _regular_trace(*, direction: Direction, length: int, windows: int = 12) -> TrafficTrace:
    return _trace(tuple((index + 0.125, direction, length) for index in range(windows)))


def test_window_features_match_hand_counts_quantiles_iats_and_closed_endpoint() -> None:
    """Changing a feature definition or assigning an exact boundary left must fail this hand oracle."""
    trace = _trace(
        (
            (0.0, Direction.OUTBOUND, 10),
            (0.0, Direction.OUTBOUND, 20),
            (0.5, Direction.INBOUND, 30),
            (1.0, Direction.INBOUND, 40),
            (1.25, Direction.OUTBOUND, 50),
            (2.0, Direction.INBOUND, 60),
        )
    )

    features = _extract_window_features(trace, W=2.0, width=1.0, maximum_window_count=8)

    assert FEATURE_NAMES == (
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
    assert features.tolist() == [
        [2.0, 1.0, 30.0, 30.0, 20.0, 15.0, 20.0, 25.0, 0.5, 0.5, 0.5, 0.5, 1.0, 2.0],
        [1.0, 2.0, 50.0, 100.0, 50.0, 45.0, 50.0, 55.0, 0.5, 0.375, 0.5, 0.625, 0.0, 3.0],
    ]


def test_decimal_boundary_uses_shared_four_ulp_snap_before_window_assignment() -> None:
    """Raw floor of 0.3/0.1 must not move an exact decimal boundary into block two."""
    trace = _trace(((0.3, Direction.OUTBOUND, 10),))

    features = _extract_window_features(trace, W=0.4, width=0.1, maximum_window_count=4)

    assert features[:, 0].tolist() == [0.0, 0.0, 0.0, 1.0]


def test_guarded_folds_are_contiguous_complete_and_have_no_adjacent_leakage() -> None:
    """Moving a guard index into train/evaluation or leaving an adjacent train index must fail."""
    folds = _guarded_folds(window_count=12, fold_count=3, guard_window_count=1)

    assert [fold.evaluation_indexes for fold in folds] == [(0, 1, 2, 3), (4, 5, 6, 7), (8, 9, 10, 11)]
    assert [fold.guard_indexes for fold in folds] == [(4,), (3, 8), (7,)]
    assert [fold.training_indexes for fold in folds] == [
        (5, 6, 7, 8, 9, 10, 11),
        (0, 1, 2, 9, 10, 11),
        (0, 1, 2, 3, 4, 5, 6),
    ]
    assert tuple(index for fold in folds for index in fold.evaluation_indexes) == tuple(range(12))
    for fold in folds:
        assert set(fold.training_indexes).isdisjoint(fold.evaluation_indexes)
        assert set(fold.training_indexes).isdisjoint(fold.guard_indexes)
        assert set(fold.evaluation_indexes).isdisjoint(fold.guard_indexes)
        assert all(
            abs(training - evaluation) > 1
            for training in fold.training_indexes
            for evaluation in fold.evaluation_indexes
        )


def test_guarded_folds_reject_total_retained_index_evidence_above_cap_before_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A modest fold count must not materialize more than 65,536 retained index cells."""

    def prohibited_range(*_args: object) -> range:
        raise AssertionError("fold index allocation started before the evidence cap")

    monkeypatch.setattr(c2st, "range", prohibited_range, raising=False)
    with pytest.raises(TrafficlabError, match="fold evidence.*cap"):
        _guarded_folds(window_count=32_769, fold_count=2, guard_window_count=0)


def test_standardization_uses_reference_training_rows_only_and_zero_scale_fallback() -> None:
    """Generated values must not influence either retained reference transform coordinate."""
    reference_training = np.asarray([[1.0, 10.0], [3.0, 10.0]], dtype=np.float64)
    generated_training = np.asarray([[101.0, 1_000.0], [103.0, 2_000.0]], dtype=np.float64)
    evaluation = np.asarray([[2.0, 11.0]], dtype=np.float64)

    mean, scale, transformed = _reference_standardize(
        reference_training,
        (reference_training, generated_training, evaluation),
    )

    assert mean.tolist() == [2.0, 10.0]
    assert scale.tolist() == [1.0, 1.0]
    assert [value.tolist() for value in transformed] == [
        [[-1.0, 0.0], [1.0, 0.0]],
        [[99.0, 990.0], [101.0, 1_990.0]],
        [[0.0, 1.0]],
    ]


def test_logistic_loss_and_analytic_gradient_match_an_independent_scalar_oracle() -> None:
    """Wrong label signs, averaging, intercept penalty, or coefficient penalty must fail."""
    design = np.asarray([[1.0, -2.0], [1.0, 1.0], [1.0, 3.0]], dtype=np.float64)
    labels = np.asarray([0.0, 1.0, 1.0], dtype=np.float64)
    parameters = np.asarray([0.25, -0.5], dtype=np.float64)
    regularization = 0.75

    loss, gradient = _logistic_loss_gradient(parameters, design, labels, regularization)

    logits = [1.25, -0.25, -1.25]
    expected_loss = (
        sum(math.log1p(math.exp(logit)) - label * logit for logit, label in zip(logits, labels, strict=True)) / 3
    )
    expected_loss += 0.5 * regularization * parameters[1] ** 2
    residuals = [1.0 / (1.0 + math.exp(-logit)) - label for logit, label in zip(logits, labels, strict=True)]
    expected_gradient = (
        sum(residuals) / 3,
        sum(residual * value for residual, value in zip(residuals, (-2.0, 1.0, 3.0), strict=True)) / 3
        + regularization * parameters[1],
    )
    assert loss == pytest.approx(expected_loss, abs=1e-15)
    assert gradient.tolist() == pytest.approx(expected_gradient, abs=1e-15)


def test_logistic_fit_is_deterministic_and_retains_balanced_label_coefficients() -> None:
    """Random initialization, unbalanced labels, or unstable coefficient ordering must fail."""
    features = np.asarray([[-2.0], [-1.0], [1.0], [2.0]], dtype=np.float64)
    labels = np.asarray([0.0, 0.0, 1.0, 1.0], dtype=np.float64)

    first = _fit_logistic(features, labels, _settings())
    second = _fit_logistic(features, labels, _settings())

    assert first == second
    assert first.intercept == pytest.approx(0.0, abs=1e-12)
    assert first.coefficients[0] > 0.0
    assert first.iterations <= 200
    assert first.converged is True


def test_tie_aware_auc_matches_pairwise_oracle_and_indistinguishable_ties() -> None:
    """Treating ties as wins/losses or using input order as a tie-break must fail."""
    labels = np.asarray([0.0, 0.0, 1.0, 1.0], dtype=np.float64)
    scores = np.asarray([0.1, 0.5, 0.5, 0.9], dtype=np.float64)

    assert _tie_aware_auc(labels, scores) == 0.875
    assert _tie_aware_auc(labels[::-1], scores[::-1]) == 0.875
    assert _tie_aware_auc(labels, np.zeros(4, dtype=np.float64)) == 0.5


@pytest.mark.parametrize("invalid_result", ["nonconvergence", "nonfinite", "wrong-shape"])
def test_logistic_fit_rejects_solver_failure_or_invalid_coefficients(
    monkeypatch: pytest.MonkeyPatch,
    invalid_result: str,
) -> None:
    """A solver status or parameter defect must abort instead of publishing classifier evidence."""
    if invalid_result == "nonconvergence":
        result = SimpleNamespace(success=False, x=np.zeros(2), nit=200, fun=1.0, message="limit")
    elif invalid_result == "nonfinite":
        result = SimpleNamespace(success=True, x=np.asarray([0.0, math.nan]), nit=1, fun=1.0, message="ok")
    else:
        result = SimpleNamespace(success=True, x=np.zeros(3), nit=1, fun=1.0, message="ok")

    def fake_minimize(*_args: object, **_kwargs: object) -> object:
        return result

    monkeypatch.setattr(c2st, "minimize", fake_minimize)
    features = np.asarray([[-1.0], [1.0]], dtype=np.float64)
    labels = np.asarray([0.0, 1.0], dtype=np.float64)

    with pytest.raises(TrafficlabError, match="logistic solver"):
        _fit_logistic(features, labels, _settings())


def test_classical_c2st_scores_separable_windows_at_zero_with_balanced_blocked_folds() -> None:
    """Pooling temporal windows, leaking guards, or reversing AUC semantics must fail this separable case."""
    reference = _regular_trace(direction=Direction.OUTBOUND, length=10)
    generated = _regular_trace(direction=Direction.INBOUND, length=100)

    result = classical_c2st_diagnostic(reference, generated, 12.0, _settings())
    diagnostics = cast(dict[str, object], result.as_dict()["diagnostics"])
    folds = cast(list[dict[str, object]], diagnostics["folds"])

    assert result.score == 0.0
    assert diagnostics["auc"] == 1.0
    assert diagnostics["balanced_accuracy"] == 1.0
    assert diagnostics["feature_names"] == list(FEATURE_NAMES)
    assert diagnostics["window_count_per_trace"] == 12
    assert len(folds) == 3
    assert all(fold["training_reference_count"] == fold["training_generated_count"] for fold in folds)
    assert all(fold["evaluation_reference_count"] == fold["evaluation_generated_count"] for fold in folds)
    assert all(fold["converged"] is True for fold in folds)


def test_classical_c2st_identical_windows_have_tied_auc_and_unit_similarity() -> None:
    """An identical deterministic feature matrix must remain exactly chance-level under every fold."""
    trace = _regular_trace(direction=Direction.OUTBOUND, length=10)

    result = classical_c2st_diagnostic(trace, trace, 12.0, _settings())

    assert result.score == 1.0
    assert result.diagnostics["auc"] == 0.5
    assert result.diagnostics["balanced_accuracy"] == 0.5
    assert result.diagnostics["coefficients"] == tuple(0.0 for _ in FEATURE_NAMES)


def test_classical_c2st_rejects_insufficient_or_over_cap_windows() -> None:
    """A fold without guarded training data and an allocation above the declared cap must fail before fitting."""
    trace = _regular_trace(direction=Direction.OUTBOUND, length=10, windows=2)

    with pytest.raises(TrafficlabError, match="guarded folds"):
        classical_c2st_diagnostic(trace, trace, 2.0, _settings(fold_count=2))
    with pytest.raises(TrafficlabError, match="window count exceeds"):
        classical_c2st_diagnostic(trace, trace, 2.0, _settings(maximum_window_count=1, fold_count=2))


def test_reference_standardizer_rejects_nonfinite_or_mismatched_features() -> None:
    """Unsafe transform inputs must not reach SciPy or artifact diagnostics."""
    reference = np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64)
    bad = np.asarray([[1.0, math.inf]], dtype=np.float64)
    wrong_width = np.asarray([[1.0]], dtype=np.float64)

    with pytest.raises(TrafficlabError, match="standardization"):
        _reference_standardize(reference, (bad,))
    with pytest.raises(TrafficlabError, match="standardization"):
        _reference_standardize(reference, (wrong_width,))


def test_auc_rejects_nonbinary_unbalanced_or_nonfinite_inputs() -> None:
    """AUC evidence requires finite scores and both exact binary classes."""
    cases: tuple[tuple[NDArray[np.float64], NDArray[np.float64]], ...] = (
        (np.asarray([0.0, 2.0]), np.asarray([0.0, 1.0])),
        (np.asarray([0.0, 0.0]), np.asarray([0.0, 1.0])),
        (np.asarray([0.0, 1.0]), np.asarray([0.0, math.nan])),
        (np.asarray([0.0]), np.asarray([0.0, 1.0])),
    )
    for labels, scores in cases:
        with pytest.raises(TrafficlabError, match="AUC"):
            _tie_aware_auc(labels, scores)
