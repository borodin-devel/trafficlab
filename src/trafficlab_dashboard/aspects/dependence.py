from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from trafficlab.common.trace import TrafficTrace
from trafficlab.comparison.similarity.autocorrelation import sample_autocorrelations
from trafficlab_dashboard.aspects.base import (
    CalculationSettings,
    HexbinPlotData,
    LinePlotData,
    LineSeries,
    SeriesDataset,
)
from trafficlab_dashboard.run_data import DashboardRun

_UNAVAILABLE_REASON = "lag must be smaller than sample length"


def _combined_limits(*arrays: NDArray[np.float64]) -> tuple[float, float]:
    non_empty = [array for array in arrays if len(array) > 0]
    if not non_empty:
        return 0.0, 0.0
    return float(min(float(np.min(array)) for array in non_empty)), float(
        max(float(np.max(array)) for array in non_empty)
    )


def _acf_title(label: str, reference_count: int, generated_count: int, *, lag_range: tuple[int, int]) -> str:
    return (
        f"{label} (unitless)"
        f" · Reference n={reference_count}"
        f" · Generated n={generated_count}"
        f" · Lags {lag_range[0]}-{lag_range[1]}"
    )


def _hexbin_title(label: str, reference_count: int, generated_count: int) -> str:
    return f"{label} (paired samples) · Reference n={reference_count} · Generated n={generated_count}"


def _available_mask(sample_length: int, requested_lags: tuple[int, ...]) -> tuple[bool, ...]:
    return tuple(lag < sample_length for lag in requested_lags)


def _available_lags(requested_lags: tuple[int, ...], availability: tuple[bool, ...]) -> tuple[int, ...]:
    return tuple(lag for lag, available in zip(requested_lags, availability, strict=True) if available)


def _acf_series(
    *,
    label: str,
    dataset: SeriesDataset,
    sample: NDArray[np.float64],
    requested_lags: tuple[int, ...],
    availability: tuple[bool, ...],
) -> LineSeries:
    available_lags = _available_lags(requested_lags, availability)
    if not available_lags:
        return LineSeries(
            label=label,
            x=np.array([], dtype=np.float64),
            y=np.array([], dtype=np.float64),
            sample_count=len(sample),
            dataset=dataset,
        )
    return LineSeries(
        label=label,
        x=np.asarray(available_lags, dtype=np.float64),
        y=np.asarray(sample_autocorrelations(sample, available_lags), dtype=np.float64),
        sample_count=len(sample),
        dataset=dataset,
    )


def _acf_plot(
    *,
    identifier: str,
    label: str,
    requested_lags: tuple[int, ...],
    reference_series: LineSeries,
    generated_series: LineSeries,
    reference_available: tuple[bool, ...],
    generated_available: tuple[bool, ...],
    reference_sample_count: int,
    generated_sample_count: int,
) -> LinePlotData:
    return LinePlotData(
        identifier=identifier,
        label=label,
        title=_acf_title(
            label,
            reference_sample_count,
            generated_sample_count,
            lag_range=(requested_lags[0], requested_lags[-1]),
        ),
        x_label="Lag",
        y_label="Autocorrelation",
        unit="unitless",
        series=(reference_series, generated_series),
        x_limits=(float(requested_lags[0]), float(requested_lags[-1])),
        y_limits=_combined_limits(reference_series.y, generated_series.y),
        lag_range=(requested_lags[0], requested_lags[-1]),
        requested_lags=requested_lags,
        reference_available=reference_available,
        generated_available=generated_available,
        unavailable_reason=(
            _UNAVAILABLE_REASON if (not all(reference_available) or not all(generated_available)) else None
        ),
        reference_sample_count=reference_sample_count,
        generated_sample_count=generated_sample_count,
    )


def _frame_size_sample(trace: TrafficTrace) -> NDArray[np.float64]:
    return np.asarray(trace.frame_lengths, dtype=np.float64)


def _frame_size_iat_pairs(trace: TrafficTrace) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    return np.asarray(trace.frame_lengths[1:], dtype=np.float64), trace.iats()


@dataclass(frozen=True, slots=True)
class FrameSizeAutocorrelationAspect:
    identifier: str = "frame_size_acf"
    label: str = "Frame-size autocorrelation"
    category: str = "Dependence"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        reference_sample = _frame_size_sample(run.reference)
        generated_sample = _frame_size_sample(run.generated)
        reference_available = _available_mask(len(reference_sample), settings.acf_lags)
        generated_available = _available_mask(len(generated_sample), settings.acf_lags)
        return _acf_plot(
            identifier=self.identifier,
            label=self.label,
            requested_lags=settings.acf_lags,
            reference_series=_acf_series(
                label="Reference",
                dataset="reference",
                sample=reference_sample,
                requested_lags=settings.acf_lags,
                availability=reference_available,
            ),
            generated_series=_acf_series(
                label="Generated",
                dataset="generated",
                sample=generated_sample,
                requested_lags=settings.acf_lags,
                availability=generated_available,
            ),
            reference_available=reference_available,
            generated_available=generated_available,
            reference_sample_count=len(reference_sample),
            generated_sample_count=len(generated_sample),
        )


@dataclass(frozen=True, slots=True)
class IatAutocorrelationAspect:
    identifier: str = "iat_acf"
    label: str = "IAT autocorrelation"
    category: str = "Dependence"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        reference_sample = run.reference.iats()
        generated_sample = run.generated.iats()
        reference_available = _available_mask(len(reference_sample), settings.acf_lags)
        generated_available = _available_mask(len(generated_sample), settings.acf_lags)
        return _acf_plot(
            identifier=self.identifier,
            label=self.label,
            requested_lags=settings.acf_lags,
            reference_series=_acf_series(
                label="Reference",
                dataset="reference",
                sample=reference_sample,
                requested_lags=settings.acf_lags,
                availability=reference_available,
            ),
            generated_series=_acf_series(
                label="Generated",
                dataset="generated",
                sample=generated_sample,
                requested_lags=settings.acf_lags,
                availability=generated_available,
            ),
            reference_available=reference_available,
            generated_available=generated_available,
            reference_sample_count=len(reference_sample),
            generated_sample_count=len(generated_sample),
        )


@dataclass(frozen=True, slots=True)
class FrameSizeIatHexbinAspect:
    identifier: str = "frame_size_iat_hexbin"
    label: str = "Frame size versus IAT"
    category: str = "Dependence"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> HexbinPlotData:
        reference_x, reference_y = _frame_size_iat_pairs(run.reference)
        generated_x, generated_y = _frame_size_iat_pairs(run.generated)
        return HexbinPlotData(
            identifier=self.identifier,
            label=self.label,
            title=_hexbin_title(self.label, len(reference_x), len(generated_x)),
            x_label="Frame size (bytes)",
            y_label="Inter-arrival time (s)",
            unit="paired samples",
            reference_x=reference_x,
            reference_y=reference_y,
            generated_x=generated_x,
            generated_y=generated_y,
            x_limits=_combined_limits(reference_x, generated_x),
            y_limits=_combined_limits(reference_y, generated_y),
            reference_sample_count=len(reference_x),
            generated_sample_count=len(generated_x),
            render_mode="hexbin"
            if len(reference_x) + len(generated_x) >= settings.maximum_display_points
            else "scatter",
        )
