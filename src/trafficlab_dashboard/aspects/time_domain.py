from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from trafficlab.common.trace import TrafficTrace
from trafficlab_dashboard.aspects.base import CalculationSettings, LinePlotData, LineSeries, SeriesDataset
from trafficlab_dashboard.aspects.numerics import choose_time_bin_width, minmax_envelope, shared_time_edges
from trafficlab_dashboard.run_data import DashboardRun


def _title(label: str, unit: str, reference_count: int, generated_count: int, *, bin_width: float | None = None) -> str:
    parts = [f"{label} ({unit})", f"Reference n={reference_count}", f"Generated n={generated_count}"]
    if bin_width is not None:
        parts.append(f"Bin width {bin_width:g} s")
    return " · ".join(parts)


def _combined_limits(*arrays: NDArray[np.float64]) -> tuple[float, float]:
    non_empty = [array for array in arrays if len(array) > 0]
    if not non_empty:
        return 0.0, 0.0
    return float(min(float(np.min(array)) for array in non_empty)), float(
        max(float(np.max(array)) for array in non_empty)
    )


def _reduced_timeline_series(
    *,
    label: str,
    dataset: SeriesDataset,
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    sample_count: int,
    maximum_points: int,
) -> LineSeries:
    if len(x) > maximum_points:
        reduced = minmax_envelope(x, y, maximum_points=maximum_points)
        x_values = reduced.x
        y_values = reduced.y
    else:
        x_values = x
        y_values = y
    return LineSeries(label=label, x=x_values, y=y_values, sample_count=sample_count, dataset=dataset)


def _monotone_indices(length: int, maximum_points: int) -> NDArray[np.int64]:
    if length <= maximum_points:
        return np.arange(length, dtype=np.int64)
    indices = np.unique(np.linspace(0, length - 1, num=maximum_points, dtype=np.int64))
    indices[0] = 0
    indices[-1] = length - 1
    return indices


def _reduced_cumulative_series(
    *,
    label: str,
    dataset: SeriesDataset,
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    sample_count: int,
    maximum_points: int,
) -> LineSeries:
    if len(x) == 0:
        return LineSeries(label=label, x=x, y=y, sample_count=sample_count, dataset=dataset)
    indices = _monotone_indices(len(x), maximum_points)
    return LineSeries(label=label, x=x[indices], y=y[indices], sample_count=sample_count, dataset=dataset)


def _throughput_mbps(trace: TrafficTrace, edges: NDArray[np.float64]) -> NDArray[np.float64]:
    bytes_per_bin, _ = np.histogram(trace.timestamps, bins=edges, weights=trace.frame_lengths)
    widths = np.diff(edges)
    return np.asarray(bytes_per_bin * 8.0 / widths / 1_000_000.0, dtype=np.float64)


def _packet_rate(trace: TrafficTrace, edges: NDArray[np.float64]) -> NDArray[np.float64]:
    counts, _ = np.histogram(trace.timestamps, bins=edges)
    widths = np.diff(edges)
    return np.asarray(counts / widths, dtype=np.float64)


def _cumulative_bytes_mib(trace: TrafficTrace) -> NDArray[np.float64]:
    return np.asarray(np.cumsum(trace.frame_lengths, dtype=np.uint64) / 1_048_576.0, dtype=np.float64)


def _cumulative_packets(trace: TrafficTrace) -> NDArray[np.float64]:
    return np.arange(1, len(trace) + 1, dtype=np.float64)


def _frame_sizes(trace: TrafficTrace) -> NDArray[np.float64]:
    return np.asarray(trace.frame_lengths, dtype=np.float64)


def _iat_values(trace: TrafficTrace) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    return trace.timestamps[1:], trace.iats()


def _line_plot(
    *,
    identifier: str,
    label: str,
    y_label: str,
    unit: str,
    reference: LineSeries,
    generated: LineSeries,
    x_limits: tuple[float, float],
    y_limits: tuple[float, float],
    bin_width: float | None = None,
    bin_edges: NDArray[np.float64] | None = None,
) -> LinePlotData:
    return LinePlotData(
        identifier=identifier,
        label=label,
        title=_title(label, unit, reference.sample_count, generated.sample_count, bin_width=bin_width),
        x_label="Time (s)",
        y_label=y_label,
        unit=unit,
        series=(reference, generated),
        x_limits=x_limits,
        y_limits=y_limits,
        bin_width=bin_width,
        bin_edges=bin_edges,
        reference_sample_count=reference.sample_count,
        generated_sample_count=generated.sample_count,
    )


@dataclass(frozen=True, slots=True)
class ThroughputAspect:
    identifier: str = "throughput"
    label: str = "Throughput"
    category: str = "Time domain"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        width = choose_time_bin_width(run.window, settings.automatic_bin_minimum, settings.automatic_bin_maximum)
        edges = shared_time_edges(run.window, width)
        reference_y = _throughput_mbps(run.reference, edges)
        generated_y = _throughput_mbps(run.generated, edges)
        x = edges[:-1]
        return _line_plot(
            identifier=self.identifier,
            label=self.label,
            y_label="Throughput (Mbps)",
            unit="Mbps",
            reference=_reduced_timeline_series(
                label="Reference",
                dataset="reference",
                x=x,
                y=reference_y,
                sample_count=len(run.reference),
                maximum_points=settings.maximum_display_points,
            ),
            generated=_reduced_timeline_series(
                label="Generated",
                dataset="generated",
                x=x,
                y=generated_y,
                sample_count=len(run.generated),
                maximum_points=settings.maximum_display_points,
            ),
            x_limits=(0.0, float(edges[-1])),
            y_limits=_combined_limits(reference_y, generated_y),
            bin_width=width,
            bin_edges=edges,
        )


@dataclass(frozen=True, slots=True)
class PacketRateAspect:
    identifier: str = "packet_rate"
    label: str = "Packet rate"
    category: str = "Time domain"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        width = choose_time_bin_width(run.window, settings.automatic_bin_minimum, settings.automatic_bin_maximum)
        edges = shared_time_edges(run.window, width)
        reference_y = _packet_rate(run.reference, edges)
        generated_y = _packet_rate(run.generated, edges)
        x = edges[:-1]
        return _line_plot(
            identifier=self.identifier,
            label=self.label,
            y_label="Packet rate (packets/s)",
            unit="packets/s",
            reference=_reduced_timeline_series(
                label="Reference",
                dataset="reference",
                x=x,
                y=reference_y,
                sample_count=len(run.reference),
                maximum_points=settings.maximum_display_points,
            ),
            generated=_reduced_timeline_series(
                label="Generated",
                dataset="generated",
                x=x,
                y=generated_y,
                sample_count=len(run.generated),
                maximum_points=settings.maximum_display_points,
            ),
            x_limits=(0.0, float(edges[-1])),
            y_limits=_combined_limits(reference_y, generated_y),
            bin_width=width,
            bin_edges=edges,
        )


@dataclass(frozen=True, slots=True)
class CumulativeBytesAspect:
    identifier: str = "cumulative_bytes"
    label: str = "Cumulative bytes"
    category: str = "Time domain"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        reference_y = _cumulative_bytes_mib(run.reference)
        generated_y = _cumulative_bytes_mib(run.generated)
        return _line_plot(
            identifier=self.identifier,
            label=self.label,
            y_label="Cumulative bytes (MiB)",
            unit="MiB",
            reference=_reduced_cumulative_series(
                label="Reference",
                dataset="reference",
                x=run.reference.timestamps,
                y=reference_y,
                sample_count=len(run.reference),
                maximum_points=settings.maximum_display_points,
            ),
            generated=_reduced_cumulative_series(
                label="Generated",
                dataset="generated",
                x=run.generated.timestamps,
                y=generated_y,
                sample_count=len(run.generated),
                maximum_points=settings.maximum_display_points,
            ),
            x_limits=(0.0, run.window),
            y_limits=_combined_limits(reference_y, generated_y),
        )


@dataclass(frozen=True, slots=True)
class CumulativePacketsAspect:
    identifier: str = "cumulative_packets"
    label: str = "Cumulative packets"
    category: str = "Time domain"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        reference_y = _cumulative_packets(run.reference)
        generated_y = _cumulative_packets(run.generated)
        return _line_plot(
            identifier=self.identifier,
            label=self.label,
            y_label="Cumulative packets",
            unit="packets",
            reference=_reduced_cumulative_series(
                label="Reference",
                dataset="reference",
                x=run.reference.timestamps,
                y=reference_y,
                sample_count=len(run.reference),
                maximum_points=settings.maximum_display_points,
            ),
            generated=_reduced_cumulative_series(
                label="Generated",
                dataset="generated",
                x=run.generated.timestamps,
                y=generated_y,
                sample_count=len(run.generated),
                maximum_points=settings.maximum_display_points,
            ),
            x_limits=(0.0, run.window),
            y_limits=_combined_limits(reference_y, generated_y),
        )


@dataclass(frozen=True, slots=True)
class FrameSizeTimelineAspect:
    identifier: str = "frame_size_timeline"
    label: str = "Frame size versus time"
    category: str = "Time domain"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        reference_y = _frame_sizes(run.reference)
        generated_y = _frame_sizes(run.generated)
        return _line_plot(
            identifier=self.identifier,
            label=self.label,
            y_label="Frame size (bytes)",
            unit="bytes",
            reference=_reduced_timeline_series(
                label="Reference",
                dataset="reference",
                x=run.reference.timestamps,
                y=reference_y,
                sample_count=len(run.reference),
                maximum_points=settings.maximum_display_points,
            ),
            generated=_reduced_timeline_series(
                label="Generated",
                dataset="generated",
                x=run.generated.timestamps,
                y=generated_y,
                sample_count=len(run.generated),
                maximum_points=settings.maximum_display_points,
            ),
            x_limits=(0.0, run.window),
            y_limits=_combined_limits(reference_y, generated_y),
        )


@dataclass(frozen=True, slots=True)
class IatTimelineAspect:
    identifier: str = "iat_timeline"
    label: str = "IAT versus time"
    category: str = "Time domain"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        reference_x, reference_y = _iat_values(run.reference)
        generated_x, generated_y = _iat_values(run.generated)
        return _line_plot(
            identifier=self.identifier,
            label=self.label,
            y_label="Inter-arrival time (s)",
            unit="s",
            reference=_reduced_timeline_series(
                label="Reference",
                dataset="reference",
                x=reference_x,
                y=reference_y,
                sample_count=len(reference_y),
                maximum_points=settings.maximum_display_points,
            ),
            generated=_reduced_timeline_series(
                label="Generated",
                dataset="generated",
                x=generated_x,
                y=generated_y,
                sample_count=len(generated_y),
                maximum_points=settings.maximum_display_points,
            ),
            x_limits=_combined_limits(reference_x, generated_x),
            y_limits=_combined_limits(reference_y, generated_y),
        )
