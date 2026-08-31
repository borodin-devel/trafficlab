from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from trafficlab.common.trace import Direction, TrafficTrace
from trafficlab_dashboard.aspects.base import (
    BarPlotData,
    BarSeries,
    CalculationSettings,
    LinePlotData,
    LineSeries,
)
from trafficlab_dashboard.aspects.numerics import choose_time_bin_width, shared_time_edges
from trafficlab_dashboard.run_data import DashboardRun


def _line_title(label: str, unit: str, reference_count: int, generated_count: int, *, bin_width: float) -> str:
    return (
        f"{label} ({unit})"
        f" · Reference n={reference_count}"
        f" · Generated n={generated_count}"
        f" · Bin width {bin_width:g} s"
    )


def _bar_title(
    label: str,
    reference_packets: int,
    generated_packets: int,
    *,
    reference_bytes: int,
    generated_bytes: int,
) -> str:
    return (
        f"{label} (proportion)"
        f" · Reference packets={reference_packets} bytes={reference_bytes}"
        f" · Generated packets={generated_packets} bytes={generated_bytes}"
    )


def _combined_limits(*arrays: NDArray[np.float64]) -> tuple[float, float]:
    non_empty = [array for array in arrays if len(array) > 0]
    if not non_empty:
        return 0.0, 0.0
    return float(min(float(np.min(array)) for array in non_empty)), float(
        max(float(np.max(array)) for array in non_empty)
    )


def _subset_counts(trace: TrafficTrace, direction: Direction) -> tuple[int, int]:
    mask = trace.direction_mask(direction)
    return int(np.count_nonzero(mask)), int(np.sum(trace.frame_lengths[mask], dtype=np.uint64))


def _directional_throughput(trace: TrafficTrace, edges: NDArray[np.float64], direction: Direction) -> NDArray[np.float64]:
    mask = trace.direction_mask(direction)
    bytes_per_bin, _ = np.histogram(trace.timestamps[mask], bins=edges, weights=trace.frame_lengths[mask])
    return np.asarray(bytes_per_bin * 8.0 / np.diff(edges) / 1_000_000.0, dtype=np.float64)


def _directional_packet_rate(trace: TrafficTrace, edges: NDArray[np.float64], direction: Direction) -> NDArray[np.float64]:
    mask = trace.direction_mask(direction)
    counts, _ = np.histogram(trace.timestamps[mask], bins=edges)
    return np.asarray(counts / np.diff(edges), dtype=np.float64)


def _shared_reduction_indices(series_values: tuple[NDArray[np.float64], ...], maximum_points: int) -> NDArray[np.int64]:
    length = len(series_values[0])
    if length <= maximum_points:
        return np.arange(length, dtype=np.int64)
    if maximum_points == 2:
        return np.array([0, length - 1], dtype=np.int64)

    bucket_count = max(1, (maximum_points - 2) // 2)
    boundaries = np.linspace(0, length, num=bucket_count + 1, dtype=np.int64)
    selected: set[int] = {0, length - 1}
    for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
        if stop <= start:
            continue
        for values in series_values:
            bucket_values = values[start:stop]
            selected.add(int(np.argmin(bucket_values)) + start)
            selected.add(int(np.argmax(bucket_values)) + start)
    ordered = np.array(sorted(selected), dtype=np.int64)
    if len(ordered) <= maximum_points:
        return ordered
    sample_indices = np.unique(np.linspace(0, len(ordered) - 1, num=maximum_points, dtype=np.int64))
    sample_indices[0] = 0
    sample_indices[-1] = len(ordered) - 1
    return ordered[sample_indices]


def _directional_line_series(
    *,
    x: NDArray[np.float64],
    reference_uplink: NDArray[np.float64],
    reference_downlink: NDArray[np.float64],
    generated_uplink: NDArray[np.float64],
    generated_downlink: NDArray[np.float64],
    run: DashboardRun,
    maximum_points: int,
) -> tuple[LineSeries, ...]:
    indices = _shared_reduction_indices(
        (reference_uplink, reference_downlink, generated_uplink, generated_downlink),
        maximum_points,
    )
    reduced_x = x[indices]
    reference_uplink_count, _ = _subset_counts(run.reference, Direction.OUTBOUND)
    reference_downlink_count, _ = _subset_counts(run.reference, Direction.INBOUND)
    generated_uplink_count, _ = _subset_counts(run.generated, Direction.OUTBOUND)
    generated_downlink_count, _ = _subset_counts(run.generated, Direction.INBOUND)
    return (
        LineSeries(
            label="Reference uplink",
            x=reduced_x,
            y=reference_uplink[indices],
            sample_count=reference_uplink_count,
            dataset="reference",
            line_style="solid",
        ),
        LineSeries(
            label="Reference downlink",
            x=reduced_x,
            y=reference_downlink[indices],
            sample_count=reference_downlink_count,
            dataset="reference",
            line_style="dashed",
        ),
        LineSeries(
            label="Generated uplink",
            x=reduced_x,
            y=generated_uplink[indices],
            sample_count=generated_uplink_count,
            dataset="generated",
            line_style="solid",
        ),
        LineSeries(
            label="Generated downlink",
            x=reduced_x,
            y=generated_downlink[indices],
            sample_count=generated_downlink_count,
            dataset="generated",
            line_style="dashed",
        ),
    )


def _line_plot(
    *,
    identifier: str,
    label: str,
    y_label: str,
    unit: str,
    series: tuple[LineSeries, ...],
    x_limits: tuple[float, float],
    y_limits: tuple[float, float],
    bin_width: float,
    bin_edges: NDArray[np.float64],
    reference_sample_count: int,
    generated_sample_count: int,
) -> LinePlotData:
    return LinePlotData(
        identifier=identifier,
        label=label,
        title=_line_title(label, unit, reference_sample_count, generated_sample_count, bin_width=bin_width),
        x_label="Time (s)",
        y_label=y_label,
        unit=unit,
        series=series,
        x_limits=x_limits,
        y_limits=y_limits,
        bin_width=bin_width,
        bin_edges=bin_edges,
        reference_sample_count=reference_sample_count,
        generated_sample_count=generated_sample_count,
    )


@dataclass(frozen=True, slots=True)
class DirectionalThroughputAspect:
    identifier: str = "directional_throughput"
    label: str = "Uplink/downlink throughput"
    category: str = "Direction"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        width = choose_time_bin_width(run.window, settings.automatic_bin_minimum, settings.automatic_bin_maximum)
        edges = shared_time_edges(run.window, width)
        x = edges[:-1]
        reference_uplink = _directional_throughput(run.reference, edges, Direction.OUTBOUND)
        reference_downlink = _directional_throughput(run.reference, edges, Direction.INBOUND)
        generated_uplink = _directional_throughput(run.generated, edges, Direction.OUTBOUND)
        generated_downlink = _directional_throughput(run.generated, edges, Direction.INBOUND)
        return _line_plot(
            identifier=self.identifier,
            label=self.label,
            y_label="Throughput (Mbps)",
            unit="Mbps",
            series=_directional_line_series(
                x=x,
                reference_uplink=reference_uplink,
                reference_downlink=reference_downlink,
                generated_uplink=generated_uplink,
                generated_downlink=generated_downlink,
                run=run,
                maximum_points=settings.maximum_display_points,
            ),
            x_limits=(0.0, float(edges[-1])),
            y_limits=_combined_limits(reference_uplink, reference_downlink, generated_uplink, generated_downlink),
            bin_width=width,
            bin_edges=edges,
            reference_sample_count=len(run.reference),
            generated_sample_count=len(run.generated),
        )


@dataclass(frozen=True, slots=True)
class DirectionalPacketRateAspect:
    identifier: str = "directional_packet_rate"
    label: str = "Uplink/downlink packet rate"
    category: str = "Direction"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> LinePlotData:
        width = choose_time_bin_width(run.window, settings.automatic_bin_minimum, settings.automatic_bin_maximum)
        edges = shared_time_edges(run.window, width)
        x = edges[:-1]
        reference_uplink = _directional_packet_rate(run.reference, edges, Direction.OUTBOUND)
        reference_downlink = _directional_packet_rate(run.reference, edges, Direction.INBOUND)
        generated_uplink = _directional_packet_rate(run.generated, edges, Direction.OUTBOUND)
        generated_downlink = _directional_packet_rate(run.generated, edges, Direction.INBOUND)
        return _line_plot(
            identifier=self.identifier,
            label=self.label,
            y_label="Packet rate (packets/s)",
            unit="packets/s",
            series=_directional_line_series(
                x=x,
                reference_uplink=reference_uplink,
                reference_downlink=reference_downlink,
                generated_uplink=generated_uplink,
                generated_downlink=generated_downlink,
                run=run,
                maximum_points=settings.maximum_display_points,
            ),
            x_limits=(0.0, float(edges[-1])),
            y_limits=_combined_limits(reference_uplink, reference_downlink, generated_uplink, generated_downlink),
            bin_width=width,
            bin_edges=edges,
            reference_sample_count=len(run.reference),
            generated_sample_count=len(run.generated),
        )


@dataclass(frozen=True, slots=True)
class DirectionBalanceAspect:
    identifier: str = "direction_balance"
    label: str = "Direction balance"
    category: str = "Direction"
    trace_controls: bool = True

    def calculate(self, run: DashboardRun, settings: CalculationSettings) -> BarPlotData:
        del settings
        reference_uplink_packets, reference_uplink_bytes = _subset_counts(run.reference, Direction.OUTBOUND)
        reference_downlink_packets, reference_downlink_bytes = _subset_counts(run.reference, Direction.INBOUND)
        generated_uplink_packets, generated_uplink_bytes = _subset_counts(run.generated, Direction.OUTBOUND)
        generated_downlink_packets, generated_downlink_bytes = _subset_counts(run.generated, Direction.INBOUND)
        reference_packet_total = len(run.reference)
        generated_packet_total = len(run.generated)
        reference_byte_total = reference_uplink_bytes + reference_downlink_bytes
        generated_byte_total = generated_uplink_bytes + generated_downlink_bytes
        return BarPlotData(
            identifier=self.identifier,
            label=self.label,
            title=_bar_title(
                self.label,
                reference_packet_total,
                generated_packet_total,
                reference_bytes=reference_byte_total,
                generated_bytes=generated_byte_total,
            ),
            categories=("Uplink packets", "Downlink packets", "Uplink bytes", "Downlink bytes"),
            series=(
                BarSeries(
                    label="Reference",
                    values=np.array(
                        [
                            reference_uplink_packets / reference_packet_total if reference_packet_total else 0.0,
                            reference_downlink_packets / reference_packet_total if reference_packet_total else 0.0,
                            reference_uplink_bytes / reference_byte_total if reference_byte_total else 0.0,
                            reference_downlink_bytes / reference_byte_total if reference_byte_total else 0.0,
                        ],
                        dtype=np.float64,
                    ),
                    sample_count=reference_packet_total,
                    dataset="reference",
                ),
                BarSeries(
                    label="Generated",
                    values=np.array(
                        [
                            generated_uplink_packets / generated_packet_total if generated_packet_total else 0.0,
                            generated_downlink_packets / generated_packet_total if generated_packet_total else 0.0,
                            generated_uplink_bytes / generated_byte_total if generated_byte_total else 0.0,
                            generated_downlink_bytes / generated_byte_total if generated_byte_total else 0.0,
                        ],
                        dtype=np.float64,
                    ),
                    sample_count=generated_packet_total,
                    dataset="generated",
                ),
            ),
            y_label="Share",
            unit="proportion",
            y_limits=(0.0, 1.0),
        )
