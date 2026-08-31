from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from trafficlab.common.trace import TrafficTrace
from trafficlab_dashboard.aspects.base import (
    AxisScale,
    CalculationSettings,
    HistogramPlotData,
    HistogramSeries,
    LinePlotData,
    LineSeries,
    SeriesDataset,
)
from trafficlab_dashboard.aspects.numerics import (
    choose_time_bin_width,
    ecdf_points,
    shared_histogram_edges,
    shared_time_edges,
)
from trafficlab_dashboard.run_data import DashboardRun


def _title(label: str, unit: str, reference_count: int, generated_count: int) -> str:
    return " · ".join(
        (
            f"{label} ({unit})",
            f"Reference n={reference_count}",
            f"Generated n={generated_count}",
        )
    )


def _combined_limits(*arrays: NDArray[np.float64]) -> tuple[float, float]:
    non_empty = [array for array in arrays if len(array) > 0]
    if not non_empty:
        return 0.0, 0.0
    return float(min(float(np.min(array)) for array in non_empty)), float(
        max(float(np.max(array)) for array in non_empty)
    )


def _ecdf_series(
    *,
    label: str,
    dataset: SeriesDataset,
    sample: NDArray[np.float64],
    maximum_points: int,
) -> LineSeries:
    if len(sample) == 0:
        return LineSeries(
            label=label,
            x=np.array([], dtype=np.float64),
            y=np.array([], dtype=np.float64),
            sample_count=0,
            dataset=dataset,
        )
    reduced = ecdf_points(sample, maximum_points=maximum_points)
    return LineSeries(label=label, x=reduced.x, y=reduced.y, sample_count=len(sample), dataset=dataset)


def _line_plot(
    *,
    identifier: str,
    label: str,
    unit: str,
    x_label: str,
    reference: LineSeries,
    generated: LineSeries,
    x_limits: tuple[float, float],
) -> LinePlotData:
    return LinePlotData(
        identifier=identifier,
        label=label,
        title=_title(label, unit, reference.sample_count, generated.sample_count),
        x_label=x_label,
        y_label="ECDF",
        unit=unit,
        series=(reference, generated),
        x_limits=x_limits,
        y_limits=(0.0, 1.0),
        reference_sample_count=reference.sample_count,
        generated_sample_count=generated.sample_count,
    )


def _histogram_plot(
    *,
    identifier: str,
    label: str,
    unit: str,
    x_label: str,
    x_scale: AxisScale,
    reference: HistogramSeries,
    generated: HistogramSeries,
    x_limits: tuple[float, float] | None = None,
) -> HistogramPlotData:
    edges = tuple(series.edges for series in (reference, generated) if len(series.edges) > 0)
    resolved_x_limits = x_limits if x_limits is not None else (_combined_limits(*edges) if edges else (0.0, 0.0))
    maxima = tuple(series.values for series in (reference, generated) if len(series.values) > 0)
    _, y_max = _combined_limits(*maxima) if maxima else (0.0, 0.0)
    return HistogramPlotData(
        identifier=identifier,
        label=label,
        title=_title(label, unit, reference.sample_count, generated.sample_count),
        x_label=x_label,
        y_label="Density",
        unit=unit,
        series=(reference, generated),
        x_limits=resolved_x_limits,
        y_limits=(0.0, y_max),
        x_scale=x_scale,
        reference_sample_count=reference.sample_count,
        generated_sample_count=generated.sample_count,
    )


def _frame_sizes(trace: TrafficTrace) -> NDArray[np.float64]:
    return np.asarray(trace.frame_lengths, dtype=np.float64)


def _iats(trace: TrafficTrace) -> NDArray[np.float64]:
    return trace.iats()


def _density_histogram(sample: NDArray[np.float64], edges: NDArray[np.float64]) -> NDArray[np.float64]:
    if len(sample) == 0 or len(edges) == 0:
        return np.array([], dtype=np.float64)
    values, _ = np.histogram(sample, bins=edges, density=True)
    return np.asarray(values, dtype=np.float64)


def _positive_iats(sample: NDArray[np.float64]) -> tuple[NDArray[np.float64], int]:
    positive = sample[sample > 0.0]
    zero_count = int(np.count_nonzero(sample == 0.0))
    return np.asarray(positive, dtype=np.float64), zero_count


def _throughput_sample(trace: TrafficTrace, edges: NDArray[np.float64]) -> NDArray[np.float64]:
    bytes_per_bin, _ = np.histogram(trace.timestamps, bins=edges, weights=trace.frame_lengths)
    widths = np.diff(edges)
    return np.asarray(bytes_per_bin * 8.0 / widths / 1_000_000.0, dtype=np.float64)


def _annotation_only_log_domain() -> tuple[float, float]:
    return 1.0, 10.0


@dataclass(frozen=True, slots=True)
class FrameSizeEcdfAspect:
    identifier: str = "frame_size_ecdf"
    label: str = "Frame-size ECDF"
    category: str = "Distributions"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        reference_sample = _frame_sizes(run.reference)
        generated_sample = _frame_sizes(run.generated)
        return _line_plot(
            identifier=self.identifier,
            label=self.label,
            unit="bytes",
            x_label="Frame size (bytes)",
            reference=_ecdf_series(
                label="Reference",
                dataset="reference",
                sample=reference_sample,
                maximum_points=settings.maximum_display_points,
            ),
            generated=_ecdf_series(
                label="Generated",
                dataset="generated",
                sample=generated_sample,
                maximum_points=settings.maximum_display_points,
            ),
            x_limits=_combined_limits(reference_sample, generated_sample),
        )


@dataclass(frozen=True, slots=True)
class IatEcdfAspect:
    identifier: str = "iat_ecdf"
    label: str = "IAT ECDF"
    category: str = "Distributions"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        reference_sample = _iats(run.reference)
        generated_sample = _iats(run.generated)
        return _line_plot(
            identifier=self.identifier,
            label=self.label,
            unit="s",
            x_label="Inter-arrival time (s)",
            reference=_ecdf_series(
                label="Reference",
                dataset="reference",
                sample=reference_sample,
                maximum_points=settings.maximum_display_points,
            ),
            generated=_ecdf_series(
                label="Generated",
                dataset="generated",
                sample=generated_sample,
                maximum_points=settings.maximum_display_points,
            ),
            x_limits=_combined_limits(reference_sample, generated_sample),
        )


@dataclass(frozen=True, slots=True)
class FrameSizeHistogramAspect:
    identifier: str = "frame_size_histogram"
    label: str = "Frame-size normalized histogram"
    category: str = "Distributions"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> HistogramPlotData:
        del settings
        reference_sample = _frame_sizes(run.reference)
        generated_sample = _frame_sizes(run.generated)
        edges = shared_histogram_edges(reference_sample, generated_sample)
        reference = HistogramSeries(
            label="Reference",
            edges=edges,
            values=_density_histogram(reference_sample, edges),
            sample_count=len(reference_sample),
            dataset="reference",
        )
        generated = HistogramSeries(
            label="Generated",
            edges=edges,
            values=_density_histogram(generated_sample, edges),
            sample_count=len(generated_sample),
            dataset="generated",
        )
        return _histogram_plot(
            identifier=self.identifier,
            label=self.label,
            unit="bytes",
            x_label="Frame size (bytes)",
            x_scale="linear",
            reference=reference,
            generated=generated,
        )


@dataclass(frozen=True, slots=True)
class IatHistogramAspect:
    identifier: str = "iat_histogram"
    label: str = "IAT normalized histogram"
    category: str = "Distributions"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> HistogramPlotData:
        del settings
        reference_sample = _iats(run.reference)
        generated_sample = _iats(run.generated)
        reference_positive, reference_zero_count = _positive_iats(reference_sample)
        generated_positive, generated_zero_count = _positive_iats(generated_sample)
        edges = (
            np.array([], dtype=np.float64)
            if len(reference_positive) == 0 and len(generated_positive) == 0
            else shared_histogram_edges(reference_positive, generated_positive, logarithmic=True)
        )
        reference = HistogramSeries(
            label="Reference",
            edges=edges,
            values=_density_histogram(reference_positive, edges),
            sample_count=len(reference_sample),
            dataset="reference",
            zero_count=reference_zero_count,
            positive_sample_count=len(reference_positive),
        )
        generated = HistogramSeries(
            label="Generated",
            edges=edges,
            values=_density_histogram(generated_positive, edges),
            sample_count=len(generated_sample),
            dataset="generated",
            zero_count=generated_zero_count,
            positive_sample_count=len(generated_positive),
        )
        return _histogram_plot(
            identifier=self.identifier,
            label=self.label,
            unit="s",
            x_label="Inter-arrival time (s)",
            x_scale="log",
            reference=reference,
            generated=generated,
            x_limits=_annotation_only_log_domain() if len(edges) == 0 else None,
        )


@dataclass(frozen=True, slots=True)
class ThroughputEcdfAspect:
    identifier: str = "throughput_ecdf"
    label: str = "Throughput ECDF"
    category: str = "Distributions"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        width = choose_time_bin_width(run.window, settings.automatic_bin_minimum, settings.automatic_bin_maximum)
        edges = shared_time_edges(run.window, width)
        reference_sample = _throughput_sample(run.reference, edges)
        generated_sample = _throughput_sample(run.generated, edges)
        return _line_plot(
            identifier=self.identifier,
            label=self.label,
            unit="Mbps",
            x_label="Throughput (Mbps)",
            reference=_ecdf_series(
                label="Reference",
                dataset="reference",
                sample=reference_sample,
                maximum_points=settings.maximum_display_points,
            ),
            generated=_ecdf_series(
                label="Generated",
                dataset="generated",
                sample=generated_sample,
                maximum_points=settings.maximum_display_points,
            ),
            x_limits=_combined_limits(reference_sample, generated_sample),
        )
