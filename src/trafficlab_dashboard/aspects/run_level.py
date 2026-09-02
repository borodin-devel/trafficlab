from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np

from trafficlab.common.config import ExperimentConfig, FamilyName
from trafficlab.comparison.diagnostics import FITNESS_METHOD_NAMES, MultiscaleDiagnostic
from trafficlab.comparison.schema import (
    C2stDiagnostic,
    ComparisonResult,
    FanoAllanDiagnostic,
    TransitionMatrixDiagnostic,
)
from trafficlab.fitting.genetic.types import HistoryRow
from trafficlab_dashboard.aspects.base import BarPlotData, BarSeries, CalculationSettings, LinePlotData, LineSeries
from trafficlab_dashboard.aspects.numerics import minmax_envelope
from trafficlab_dashboard.run_data import DashboardRun

_SIMILARITY_METHOD_ORDER = FITNESS_METHOD_NAMES
_SIMILARITY_LABELS = {
    "autocorrelation": "Autocorrelation",
    "frame_size_ks": "Frame-size KS",
    "iat_ks": "IAT KS",
    "multiscale_rate": "Multiscale rate",
    "cramer_von_mises": "Cramér–von Mises",
    "anderson_darling": "Anderson–Darling",
    "jensen_shannon": "Jensen–Shannon",
    "approximate_mmd": "Approximate MMD",
}
_DISPLAY_DIRECTION_NAMES = {"total": "total", "outbound": "uplink", "inbound": "downlink"}
_GA_LABELS: dict[str, str] = {
    "markov_renewal": "Markov Renewal",
    "mmpp": "MMPP",
    "poisson_empirical": "Poisson empirical",
    "overall": "Overall",
}


def _line_limits(series: tuple[LineSeries, ...]) -> tuple[tuple[float, float], tuple[float, float]]:
    x_arrays = tuple(item.x for item in series if len(item.x) > 0)
    y_arrays = tuple(item.y for item in series if len(item.y) > 0)
    if not x_arrays:
        return (0.0, 0.0), (0.0, 1.0)
    x_min = min(float(np.min(array)) for array in x_arrays)
    x_max = max(float(np.max(array)) for array in x_arrays)
    y_min = min(float(np.min(array)) for array in y_arrays) if y_arrays else 0.0
    y_max = max(float(np.max(array)) for array in y_arrays) if y_arrays else 1.0
    return (x_min, x_max), (y_min, max(1.0, y_max))


def _reduced_line_series(
    *,
    label: str,
    x: np.ndarray,
    y: np.ndarray,
    maximum_points: int,
) -> LineSeries:
    reduced = minmax_envelope(x, y, maximum_points=maximum_points)
    return LineSeries(label=label, x=reduced.x, y=reduced.y, sample_count=len(x))


def _require_similarity(run: DashboardRun, *, aspect_id: str) -> ComparisonResult:
    if run.similarity is None:
        reason = run.unavailable.get(aspect_id, "similarity.json is unavailable")
        raise ValueError(reason)
    return run.similarity


def _require_history(run: DashboardRun, *, aspect_id: str) -> tuple[HistoryRow, ...]:
    if run.history is None:
        reason = run.unavailable.get(aspect_id, "ga_history.csv is unavailable")
        raise ValueError(reason)
    return run.history


def _require_experiment(run: DashboardRun, *, aspect_id: str) -> ExperimentConfig:
    if run.experiment is None:
        reason = run.unavailable.get(aspect_id, "ga_history.csv requires a valid experiment.toml")
        raise ValueError(reason)
    return run.experiment


def _multiscale_diagnostics(run: DashboardRun, *, aspect_id: str) -> MultiscaleDiagnostic:
    similarity = _require_similarity(run, aspect_id=aspect_id)
    return cast(MultiscaleDiagnostic, similarity.methods["multiscale_rate"].diagnostics)


def _require_fano_allan(run: DashboardRun, *, aspect_id: str) -> FanoAllanDiagnostic:
    diagnostics = run.fano_allan_diagnostic
    if diagnostics is None:
        reason = run.unavailable.get(aspect_id, "similarity.json does not contain schema-5 Fano/Allan diagnostics")
        raise ValueError(reason)
    return diagnostics


def _require_transition_fidelity(run: DashboardRun, *, aspect_id: str) -> TransitionMatrixDiagnostic:
    diagnostics = run.transition_fidelity_diagnostic
    if diagnostics is None:
        reason = run.unavailable.get(aspect_id, "similarity.json does not contain schema-5 transition diagnostics")
        raise ValueError(reason)
    return diagnostics


def _require_c2st(run: DashboardRun, *, aspect_id: str) -> C2stDiagnostic:
    diagnostics = run.c2st_diagnostic
    if diagnostics is None:
        reason = run.unavailable.get(aspect_id, "similarity.json does not contain schema-5 C2ST diagnostics")
        raise ValueError(reason)
    return diagnostics


def _history_key(row: HistoryRow) -> str:
    if row.scope == "overall":
        return "overall"
    family = cast(FamilyName, row.family)
    return family


def _history_series(rows: tuple[HistoryRow, ...], key: str) -> tuple[HistoryRow, ...]:
    return tuple(sorted((row for row in rows if _history_key(row) == key), key=lambda row: row.generation))


def _history_order(rows: tuple[HistoryRow, ...], experiment: ExperimentConfig) -> tuple[str, ...]:
    expected_families = tuple(sorted(experiment.models.enabled))
    observed_families = frozenset(cast(FamilyName, row.family) for row in rows if row.scope == "family")
    expected_family_set = frozenset(expected_families)
    if observed_families != expected_family_set:
        missing = tuple(name for name in expected_families if name not in observed_families)
        unexpected = tuple(name for name in sorted(observed_families - expected_family_set))
        parts: list[str] = []
        if missing:
            parts.append(f"missing families: {', '.join(missing)}")
        if unexpected:
            parts.append(f"unexpected families: {', '.join(unexpected)}")
        raise ValueError(
            f"ga_history.csv is unavailable: history rows do not match experiment families ({'; '.join(parts)})"
        )
    if not any(row.scope == "overall" for row in rows):
        raise ValueError("ga_history.csv is unavailable: history rows do not contain an overall series")
    return expected_families + ("overall",)


@dataclass(frozen=True, slots=True)
class SimilarityScoresAspect:
    identifier: str = "similarity_scores"
    label: str = "Similarity scores"
    category: str = "Comparison"
    trace_controls: bool = False

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> BarPlotData:
        del settings
        similarity = _require_similarity(run, aspect_id=self.identifier)
        component_scores = np.asarray(
            [similarity.methods[name].score for name in _SIMILARITY_METHOD_ORDER],
            dtype=np.float64,
        )
        values = np.concatenate((component_scores, np.asarray([similarity.aggregate_score], dtype=np.float64)))
        return BarPlotData(
            identifier=self.identifier,
            label=self.label,
            title=f"{self.label} (unitless) · W={similarity.observation_window_seconds:g} s",
            categories=tuple(_SIMILARITY_LABELS[name] for name in _SIMILARITY_METHOD_ORDER) + ("Aggregate",),
            series=(
                BarSeries(
                    label="Stored score",
                    values=values,
                    sample_count=len(_SIMILARITY_METHOD_ORDER) + 1,
                ),
            ),
            y_label="Score",
            unit="unitless",
            y_limits=(0.0, 1.0),
            metadata={
                "x_label": "Component",
                "y_range": (0.0, 1.0),
                "observation_window_seconds": similarity.observation_window_seconds,
                "component_method_identifiers": _SIMILARITY_METHOD_ORDER,
                "method_identifiers": _SIMILARITY_METHOD_ORDER + ("aggregate",),
                "component_weights": tuple(similarity.methods[name].weight for name in _SIMILARITY_METHOD_ORDER),
                "aggregate_score": similarity.aggregate_score,
            },
        )


@dataclass(frozen=True, slots=True)
class MultiscaleDiscrepancyAspect:
    identifier: str = "multiscale_discrepancy"
    label: str = "Multiscale discrepancy"
    category: str = "Comparison"
    trace_controls: bool = False

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        diagnostics = _multiscale_diagnostics(run, aspect_id=self.identifier)
        widths = np.asarray(diagnostics.widths, dtype=np.float64)
        if tuple(scale.width_seconds for scale in diagnostics.scales) != diagnostics.widths:
            raise ValueError("stored multiscale widths are inconsistent")
        if (
            tuple(scale.direction_bin_cell_count for scale in diagnostics.scales)
            != diagnostics.direction_bin_cell_counts
        ):
            raise ValueError("stored multiscale cell counts are inconsistent")
        if tuple(scale.discrepancy for scale in diagnostics.scales) != diagnostics.scale_discrepancies:
            raise ValueError("stored multiscale discrepancies are inconsistent")
        packet = _reduced_line_series(
            label="Packet discrepancy",
            x=widths,
            y=np.asarray([scale.feature_discrepancies.packet for scale in diagnostics.scales], dtype=np.float64),
            maximum_points=settings.maximum_display_points,
        )
        byte = _reduced_line_series(
            label="Byte discrepancy",
            x=widths,
            y=np.asarray([scale.feature_discrepancies.byte for scale in diagnostics.scales], dtype=np.float64),
            maximum_points=settings.maximum_display_points,
        )
        x_limits, y_limits = _line_limits((packet, byte))
        return LinePlotData(
            identifier=self.identifier,
            label=self.label,
            title=f"{self.label} (unitless) · W={diagnostics.observation_window_seconds:g} s",
            x_label="Scale width (s)",
            y_label="Discrepancy",
            unit="unitless",
            series=(packet, byte),
            x_limits=x_limits,
            y_limits=(0.0, max(1.0, y_limits[1])),
            metadata={
                "y_range": (0.0, 1.0),
                "observation_window_seconds": diagnostics.observation_window_seconds,
                "scale_weights": diagnostics.scale_weights,
                "scale_discrepancies": diagnostics.scale_discrepancies,
                "direction_bin_cell_counts": diagnostics.direction_bin_cell_counts,
                "total_direction_bin_cells": diagnostics.total_direction_bin_cells,
                "feature_weights": {
                    "packet": diagnostics.feature_weights.packet,
                    "byte": diagnostics.feature_weights.byte,
                },
                "aggregate_feature_discrepancies": {
                    "packet": diagnostics.feature_discrepancies.packet,
                    "byte": diagnostics.feature_discrepancies.byte,
                },
                "discrepancy": diagnostics.discrepancy,
            },
            reference_sample_count=0,
            generated_sample_count=0,
        )


@dataclass(frozen=True, slots=True)
class FanoAllanAspect:
    identifier: str = "fano_allan"
    label: str = "Fano/Allan dispersion"
    category: str = "Comparison"
    trace_controls: bool = False

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        diagnostics = _require_fano_allan(run, aspect_id=self.identifier)
        widths = np.asarray(diagnostics.widths, dtype=np.float64)
        series: list[LineSeries] = []
        for factor in ("fano", "allan"):
            series.extend(
                (
                    _reduced_line_series(
                        label=f"Reference total {factor.title()}",
                        x=widths,
                        y=np.asarray(
                            [(scale.reference_fano if factor == "fano" else scale.reference_allan).total for scale in diagnostics.scales],
                            dtype=np.float64,
                        ),
                        maximum_points=settings.maximum_display_points,
                    ),
                    _reduced_line_series(
                        label=f"Generated total {factor.title()}",
                        x=widths,
                        y=np.asarray(
                            [(scale.generated_fano if factor == "fano" else scale.generated_allan).total for scale in diagnostics.scales],
                            dtype=np.float64,
                        ),
                        maximum_points=settings.maximum_display_points,
                    ),
                )
            )
        resolved_series = tuple(series)
        x_limits, y_limits = _line_limits(resolved_series)
        return LinePlotData(
            identifier=self.identifier,
            label=self.label,
            title=f"{self.label} (factor) · W={diagnostics.observation_window_seconds:g} s",
            x_label="Scale width (s)",
            y_label="Factor",
            unit="factor",
            series=resolved_series,
            x_limits=x_limits,
            y_limits=(0.0, max(1.0, y_limits[1])),
            metadata={
                "direction_channels": ("total", "outbound", "inbound"),
                "rendered_direction_channels": ("total",),
                "display_directions": ("total", "uplink", "downlink"),
                "scale_weights": diagnostics.scale_weights,
                "component_weights": {
                    "fano": diagnostics.component_weights.fano,
                    "allan": diagnostics.component_weights.allan,
                },
                "component_differences": {
                    "fano": diagnostics.component_differences.fano,
                    "allan": diagnostics.component_differences.allan,
                },
                "scale_differences": diagnostics.scale_differences,
                "observation_window_seconds": diagnostics.observation_window_seconds,
            },
            reference_sample_count=0,
            generated_sample_count=0,
        )


@dataclass(frozen=True, slots=True)
class TransitionFidelityAspect:
    identifier: str = "transition_fidelity"
    label: str = "Transition fidelity"
    category: str = "Comparison"
    trace_controls: bool = False

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        diagnostics = _require_transition_fidelity(run, aspect_id=self.identifier)
        state_indexes = np.arange(len(diagnostics.vocabulary), dtype=np.float64)
        component_indexes = np.arange(3, dtype=np.float64)
        series = (
            _reduced_line_series(
                label="Ref. occupancy",
                x=state_indexes,
                y=np.asarray(diagnostics.occupancy.reference_probabilities, dtype=np.float64),
                maximum_points=settings.maximum_display_points,
            ),
            _reduced_line_series(
                label="Gen. occupancy",
                x=state_indexes,
                y=np.asarray(diagnostics.occupancy.generated_probabilities, dtype=np.float64),
                maximum_points=settings.maximum_display_points,
            ),
            _reduced_line_series(
                label="Row JSD",
                x=state_indexes,
                y=np.asarray([row.jsd for row in diagnostics.transitions.rows], dtype=np.float64),
                maximum_points=settings.maximum_display_points,
            ),
            _reduced_line_series(
                label="Component JSD",
                x=component_indexes,
                y=np.asarray(
                    (
                        diagnostics.component_jsd.occupancy,
                        diagnostics.component_jsd.transition_rows,
                        diagnostics.component_jsd.runs,
                    ),
                    dtype=np.float64,
                ),
                maximum_points=settings.maximum_display_points,
            ),
        )
        x_limits, y_limits = _line_limits(series)
        state_labels = tuple(
            f"{_DISPLAY_DIRECTION_NAMES[direction]}/{size_category}/{iat_category}"
            for direction, size_category, iat_category in diagnostics.vocabulary
        )
        return LinePlotData(
            identifier=self.identifier,
            label=self.label,
            title=f"{self.label} · W={diagnostics.observation_window_seconds:g} s",
            x_label="State index",
            y_label="Value",
            unit="unitless",
            series=series,
            x_limits=x_limits,
            y_limits=(0.0, max(1.0, y_limits[1])),
            metadata={
                "state_labels": state_labels,
                "artifact_directions": ("outbound", "inbound"),
                "display_directions": ("uplink", "downlink"),
                "occupancy_reference_counts": diagnostics.occupancy.reference_counts,
                "occupancy_generated_counts": diagnostics.occupancy.generated_counts,
                "transition_row_jsd": tuple(row.jsd for row in diagnostics.transitions.rows),
                "component_identifiers": ("occupancy", "transition_rows", "runs"),
                "component_weights": {
                    "occupancy": diagnostics.component_weights.occupancy,
                    "transition_rows": diagnostics.component_weights.transition_rows,
                    "runs": diagnostics.component_weights.runs,
                },
                "observation_window_seconds": diagnostics.observation_window_seconds,
            },
            reference_sample_count=0,
            generated_sample_count=0,
        )


@dataclass(frozen=True, slots=True)
class C2stAspect:
    identifier: str = "c2st"
    label: str = "Classical C2ST"
    category: str = "Comparison"
    trace_controls: bool = False

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        diagnostics = _require_c2st(run, aspect_id=self.identifier)
        metric_indexes = np.arange(2, dtype=np.float64)
        coefficient_indexes = np.arange(len(diagnostics.coefficients), dtype=np.float64)
        series = (
            _reduced_line_series(
                label="AUC / balanced accuracy",
                x=metric_indexes,
                y=np.asarray((diagnostics.auc, diagnostics.balanced_accuracy), dtype=np.float64),
                maximum_points=settings.maximum_display_points,
            ),
            _reduced_line_series(
                label="Coefficient magnitude",
                x=coefficient_indexes,
                y=np.asarray(tuple(abs(value) for value in diagnostics.coefficients), dtype=np.float64),
                maximum_points=settings.maximum_display_points,
            ),
        )
        x_limits, y_limits = _line_limits(series)
        return LinePlotData(
            identifier=self.identifier,
            label=self.label,
            title=f"{self.label} (stored classifier evidence) · W={diagnostics.observation_window_seconds:g} s",
            x_label="Metric or feature index",
            y_label="Score / coefficient magnitude",
            unit="unitless",
            series=series,
            x_limits=x_limits,
            y_limits=(0.0, max(1.0, y_limits[1])),
            metadata={
                "metric_labels": ("AUC", "Balanced accuracy"),
                "coefficient_feature_names": diagnostics.feature_names,
                "coefficient_magnitudes": tuple(abs(value) for value in diagnostics.coefficients),
                "intercept": diagnostics.intercept,
                "fold_count": diagnostics.fold_count,
                "window_count_per_trace": diagnostics.window_count_per_trace,
                "observation_window_seconds": diagnostics.observation_window_seconds,
            },
            reference_sample_count=0,
            generated_sample_count=0,
        )
@dataclass(frozen=True, slots=True)
class GaFitnessHistoryAspect:
    identifier: str = "ga_fitness_history"
    label: str = "GA fitness history"
    category: str = "Optimization"
    trace_controls: bool = False

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        experiment = _require_experiment(run, aspect_id=self.identifier)
        rows = _require_history(run, aspect_id=self.identifier)
        order = _history_order(rows, experiment)
        series: list[LineSeries] = []
        candidate_counts: dict[str, tuple[int, ...]] = {}
        valid_counts: dict[str, tuple[int, ...]] = {}
        best_birth_generations: dict[str, tuple[int, ...]] = {}
        best_birth_indices: dict[str, tuple[int, ...]] = {}
        for key in order:
            grouped = _history_series(rows, key)
            if not grouped:
                raise ValueError(f"ga_history.csv is unavailable: history rows do not contain the {key} series")
            series.append(
                _reduced_line_series(
                    label=_GA_LABELS[key],
                    x=np.asarray([row.generation for row in grouped], dtype=np.float64),
                    y=np.asarray([row.best_fitness for row in grouped], dtype=np.float64),
                    maximum_points=settings.maximum_display_points,
                )
            )
            candidate_counts[key] = tuple(row.candidate_count for row in grouped)
            valid_counts[key] = tuple(row.valid_count for row in grouped)
            best_birth_generations[key] = tuple(row.best_identifier.birth_generation for row in grouped)
            best_birth_indices[key] = tuple(row.best_identifier.birth_index for row in grouped)
        resolved_series = tuple(series)
        x_limits, y_limits = _line_limits(resolved_series)
        return LinePlotData(
            identifier=self.identifier,
            label=self.label,
            title=f"{self.label} (unitless) · Series={len(resolved_series)}",
            x_label="Generation",
            y_label="Best fitness",
            unit="unitless",
            series=resolved_series,
            x_limits=x_limits,
            y_limits=(0.0, max(1.0, y_limits[1])),
            metadata={
                "y_range": (0.0, 1.0),
                "series_identifiers": order,
                "series_labels": tuple(_GA_LABELS[key] for key in order),
                "candidate_counts": candidate_counts,
                "valid_counts": valid_counts,
                "best_birth_generations": best_birth_generations,
                "best_birth_indices": best_birth_indices,
            },
            reference_sample_count=0,
            generated_sample_count=0,
        )
