from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np

from trafficlab.common.config import ExperimentConfig, FamilyName
from trafficlab.comparison.diagnostics import MultiscaleDiagnostic
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.types import HistoryRow
from trafficlab_dashboard.aspects.base import BarPlotData, BarSeries, CalculationSettings, LinePlotData, LineSeries
from trafficlab_dashboard.run_data import DashboardRun

_SIMILARITY_METHOD_ORDER = ("frame_size_ks", "iat_ks", "autocorrelation", "multiscale_rate")
_SIMILARITY_LABELS = {
    "frame_size_ks": "Frame-size KS",
    "iat_ks": "IAT KS",
    "autocorrelation": "Autocorrelation",
    "multiscale_rate": "Multiscale",
}
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


def _history_key(row: HistoryRow) -> str:
    if row.scope == "overall":
        return "overall"
    family = cast(FamilyName, row.family)
    return family


def _history_series(rows: tuple[HistoryRow, ...], key: str) -> tuple[HistoryRow, ...]:
    return tuple(row for row in rows if _history_key(row) == key)


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
        del settings
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
        packet = LineSeries(
            label="Packet discrepancy",
            x=widths,
            y=np.asarray([scale.feature_discrepancies.packet for scale in diagnostics.scales], dtype=np.float64),
            sample_count=len(diagnostics.scales),
        )
        byte = LineSeries(
            label="Byte discrepancy",
            x=widths,
            y=np.asarray([scale.feature_discrepancies.byte for scale in diagnostics.scales], dtype=np.float64),
            sample_count=len(diagnostics.scales),
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
class GaFitnessHistoryAspect:
    identifier: str = "ga_fitness_history"
    label: str = "GA fitness history"
    category: str = "Optimization"
    trace_controls: bool = False

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        del settings
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
                LineSeries(
                    label=_GA_LABELS[key],
                    x=np.asarray([row.generation for row in grouped], dtype=np.float64),
                    y=np.asarray([row.best_fitness for row in grouped], dtype=np.float64),
                    sample_count=len(grouped),
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
