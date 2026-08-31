# pyright: reportUnknownMemberType=false

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

import numpy as np
from matplotlib.artist import Artist
from matplotlib.backend_bases import DrawEvent, MouseButton, MouseEvent
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from trafficlab_dashboard.aspects.base import (
    AxisScale,
    BarPlotData,
    HexbinPlotData,
    HistogramPlotData,
    LinePlotData,
    MetadataValue,
    PlotData,
    SeriesDataset,
    TraceVisibility,
)
from trafficlab_dashboard.plotting.interaction import AxisSelection, AxisView, pan_limits, zoom_limits

_REFERENCE_COLOR = "#1f77b4"
_GENERATED_COLOR = "#ff7f0e"
_DENSE_ALPHA = 0.45
_SCATTER_ALPHA = 0.55
_RUN_LEVEL_COLORS = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b")
_RUN_LEVEL_STYLES = ("solid", "dashed", "dashdot", "dotted")


class _BufferRegion(Protocol):
    pass


@dataclass(slots=True)
class _DragState:
    anchor: tuple[float, float]
    view: AxisView
    x_scale: AxisScale
    y_scale: AxisScale


class DashboardCanvas(QWidget):
    visibility_painted = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.figure = Figure(figsize=(8.0, 5.0), tight_layout=True)
        self.figure_canvas = FigureCanvasQTAgg(self.figure)
        self.axes = self.figure.add_subplot(111)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.figure_canvas)
        self._complete_view: AxisView | None = None
        self._current_aspect: str | None = None
        self._drag_state: _DragState | None = None
        self._rendered_data: PlotData | None = None
        self._visibility_artists: list[tuple[SeriesDataset, Artist]] = []
        self._legend_datasets: list[SeriesDataset] = []
        self._animated_legend: Artist | None = None
        self._blit_background: _BufferRegion | None = None
        self._blit_view: AxisView | None = None
        self._visibility_regions: dict[TraceVisibility, _BufferRegion] = {}
        self._current_visibility = TraceVisibility(reference=True, generated=True)
        self._connect_events()

    @property
    def current_aspect(self) -> str | None:
        return self._current_aspect

    def render(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        data: PlotData,
        visibility: TraceVisibility,
        *,
        preserve_viewport: bool = False,
    ) -> None:
        if preserve_viewport and data is self._rendered_data:
            self._update_visibility(visibility)
            self._paint_cached_visibility()
            return
        preserve_view = self._current_view() if preserve_viewport else None
        self.axes.clear()
        self._visibility_artists.clear()
        self._legend_datasets.clear()
        self._animated_legend = None
        self._blit_background = None
        self._blit_view = None
        self._visibility_regions.clear()
        self._current_visibility = visibility
        self.axes.set_title(data.title)
        self.axes.grid(True, alpha=0.25)
        artists = self._render_data(data, visibility)
        self._apply_labels_and_scales(data)
        self._apply_annotations(data, visibility)
        self._complete_view = self._complete_view_for(data)
        self._current_aspect = data.identifier
        target_view = self._complete_view if preserve_view is None else preserve_view
        self._set_view(target_view)
        if artists:
            legend = self.axes.legend()
            legend.set_animated(True)
            self._animated_legend = legend
            self._update_legend_visibility(visibility)
        for _dataset, artist in self._visibility_artists:
            artist.set_animated(True)
        self._rendered_data = data
        self.figure_canvas.draw()

    def reset_view(self) -> None:
        if self._complete_view is None:
            return
        self._set_view(self._complete_view)
        self.figure_canvas.draw()

    def _connect_events(self) -> None:
        self.figure_canvas.mpl_connect("button_press_event", self._on_press)
        self.figure_canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.figure_canvas.mpl_connect("button_release_event", self._on_release)
        self.figure_canvas.mpl_connect("scroll_event", self._on_scroll)
        self.figure_canvas.mpl_connect("draw_event", self._on_draw)

    def _on_draw(self, event: DrawEvent) -> None:
        if event.canvas is not self.figure_canvas:
            return
        self._blit_background = self.figure_canvas.copy_from_bbox(self.axes.bbox)
        self._blit_view = self._current_view()
        self._build_visibility_regions()

    def _draw_animated_artists(self) -> None:
        if self._blit_background is None:
            return
        self.figure_canvas.restore_region(self._blit_background)
        for _dataset, artist in self._visibility_artists:
            if artist.get_visible():
                self.axes.draw_artist(artist)
        if self._animated_legend is not None and self._animated_legend.get_visible():
            self.axes.draw_artist(self._animated_legend)

    def _build_visibility_regions(self) -> None:
        if self._blit_background is None:
            return
        requested = self._current_visibility
        self._visibility_regions.clear()
        states = (
            TraceVisibility(reference=False, generated=False),
            TraceVisibility(reference=False, generated=True),
            TraceVisibility(reference=True, generated=False),
            TraceVisibility(reference=True, generated=True),
        )
        ordered = tuple(visibility for visibility in states if visibility != requested) + (requested,)
        for visibility in ordered:
            self._set_artist_visibility(visibility)
            self._draw_animated_artists()
            self._visibility_regions[visibility] = self.figure_canvas.copy_from_bbox(self.axes.bbox)
        self._set_artist_visibility(requested)

    def _paint_visibility_region(self, visibility: TraceVisibility) -> None:
        region = self._visibility_regions.get(visibility)
        if region is None:
            self._draw_animated_artists()
        else:
            self.figure_canvas.restore_region(region)
        self.figure_canvas.blit(self.axes.bbox)

    def _paint_cached_visibility(self) -> None:
        current_view = self._current_view()
        if self._blit_background is None or current_view is None or current_view != self._blit_view:
            self.figure_canvas.draw()
            self.figure_canvas.repaint()
        else:
            self._paint_visibility_region(self._current_visibility)
        self.visibility_painted.emit()

    def _current_view(self) -> AxisView | None:
        xlim = self.axes.get_xlim()
        ylim = self.axes.get_ylim()
        if not self._has_finite_limits(xlim, ylim):
            return None
        return AxisView(x=xlim, y=ylim)

    def _set_view(self, view: AxisView) -> None:
        self.axes.set_xlim(*view.x)
        self.axes.set_ylim(*view.y)

    def _apply_labels_and_scales(self, data: PlotData) -> None:
        if isinstance(data, BarPlotData):
            self.axes.set_xlabel("")
            self.axes.set_ylabel(data.y_label)
            if data.x_scale != "linear":
                self.axes.set_xscale(data.x_scale)
            self.axes.set_yscale(data.y_scale)
            return
        self.axes.set_xlabel(data.x_label)
        self.axes.set_ylabel(data.y_label)
        self.axes.set_xscale(data.x_scale)
        self.axes.set_yscale(data.y_scale)

    def _complete_view_for(self, data: PlotData) -> AxisView:
        if isinstance(data, BarPlotData):
            positions = self._bar_positions(data)
            return AxisView(x=(positions[0] - 0.5, positions[-1] + 0.5), y=data.y_limits)
        return AxisView(x=data.x_limits, y=data.y_limits)

    def _render_data(self, data: PlotData, visibility: TraceVisibility) -> list[Artist]:
        if isinstance(data, LinePlotData):
            return self._render_line_data(data, visibility)
        if isinstance(data, HistogramPlotData):
            return self._render_histogram_data(data, visibility)
        if isinstance(data, BarPlotData):
            return self._render_bar_data(data, visibility)
        return self._render_hexbin_data(data, visibility)

    def _render_line_data(self, data: LinePlotData, visibility: TraceVisibility) -> list[Artist]:
        artists: list[Artist] = []
        for index, series in enumerate(data.series):
            color = (
                self._color_for_dataset(series.dataset)
                if series.dataset is not None
                else _RUN_LEVEL_COLORS[index % len(_RUN_LEVEL_COLORS)]
            )
            line_style = (
                series.line_style if series.dataset is not None else _RUN_LEVEL_STYLES[index % len(_RUN_LEVEL_STYLES)]
            )
            (artist,) = self.axes.plot(
                series.x,
                series.y,
                color=color,
                linestyle=line_style,
                label=series.label,
                linewidth=1.8,
            )
            artist.set_visible(self._dataset_visible(series.dataset, visibility))
            self._visibility_artists.append((series.dataset, artist))
            self._legend_datasets.append(series.dataset)
            artists.append(artist)
        return artists

    def _render_histogram_data(self, data: HistogramPlotData, visibility: TraceVisibility) -> list[Artist]:
        artists: list[Artist] = []
        for series in data.series:
            artist = self.axes.stairs(
                values=series.values,
                edges=series.edges,
                label=series.label,
                color=self._color_for_dataset(series.dataset),
                alpha=_DENSE_ALPHA,
                linewidth=1.6,
            )
            artist.set_visible(self._dataset_visible(series.dataset, visibility))
            self._visibility_artists.append((series.dataset, artist))
            self._legend_datasets.append(series.dataset)
            artists.append(artist)
        return artists

    def _render_bar_data(self, data: BarPlotData, visibility: TraceVisibility) -> list[Artist]:
        positions = self._bar_positions(data)
        width = 0.8 / max(len(data.series), 1)
        artists: list[Artist] = []
        for index, series in enumerate(data.series):
            offset = (index - (len(data.series) - 1) / 2.0) * width
            artist = self.axes.bar(
                positions + offset,
                series.values,
                width=width,
                label=series.label,
                color=self._color_for_dataset(series.dataset),
                alpha=0.8,
            )
            visible = self._dataset_visible(series.dataset, visibility)
            rectangles = cast(list[Rectangle], list(artist))
            for rectangle in rectangles:
                rectangle.set_visible(visible)
                self._visibility_artists.append((series.dataset, rectangle))
            self._legend_datasets.append(series.dataset)
            artists.extend(rectangles)
        self.axes.set_xticks(positions, data.categories)
        return artists

    def _render_hexbin_data(self, data: HexbinPlotData, visibility: TraceVisibility) -> list[Artist]:
        artists: list[Artist] = []
        datasets: tuple[tuple[SeriesDataset, np.ndarray, np.ndarray, str], ...] = (
            ("reference", data.reference_x, data.reference_y, "Reference"),
            ("generated", data.generated_x, data.generated_y, "Generated"),
        )
        for dataset, x, y, label in datasets:
            artist = self._render_hexbin_dataset(
                x=x,
                y=y,
                label=label,
                color=self._color_for_dataset(dataset),
                render_mode=data.render_mode,
            )
            artist.set_visible(self._dataset_visible(dataset, visibility))
            self._visibility_artists.append((dataset, artist))
            self._legend_datasets.append(dataset)
            artists.append(artist)
        return artists

    def _render_hexbin_dataset(
        self,
        *,
        x: np.ndarray,
        y: np.ndarray,
        label: str,
        color: str,
        render_mode: str,
    ) -> Artist:
        if render_mode == "hexbin":
            artist = self.axes.hexbin(
                x,
                y,
                gridsize=24,
                mincnt=1,
                linewidths=0.0,
                alpha=_DENSE_ALPHA,
                cmap=None,
                color=color,
            )
            artist.set_label(label)
            return cast(Artist, artist)
        return cast(Artist, self.axes.scatter(x, y, label=label, color=color, alpha=_SCATTER_ALPHA, s=18.0))

    def _apply_annotations(self, data: PlotData, visibility: TraceVisibility) -> None:
        y = 0.99
        all_visible = TraceVisibility(reference=True, generated=True)
        for text in self._annotation_lines(data, all_visible):
            dataset: SeriesDataset = (
                "reference" if text.startswith("Reference ") else "generated" if text.startswith("Generated ") else None
            )
            annotation = self.axes.annotate(text, xy=(0.01, y), xycoords="axes fraction", va="top", ha="left")
            annotation.set_visible(self._dataset_visible(dataset, visibility))
            self._visibility_artists.append((dataset, annotation))
            y -= 0.06

    def _update_visibility(self, visibility: TraceVisibility) -> None:
        self._current_visibility = visibility
        self._set_artist_visibility(visibility)

    def _set_artist_visibility(self, visibility: TraceVisibility) -> None:
        for dataset, artist in self._visibility_artists:
            artist.set_visible(self._dataset_visible(dataset, visibility))
        self._update_legend_visibility(visibility)

    def _update_legend_visibility(self, visibility: TraceVisibility) -> None:
        legend = self.axes.get_legend()
        if legend is None:
            return
        texts = legend.get_texts()
        handles = legend.legend_handles
        for dataset, text, handle in zip(self._legend_datasets, texts, handles, strict=True):
            visible = self._dataset_visible(dataset, visibility)
            text.set_visible(visible)
            if handle is not None:
                handle.set_visible(visible)

    def _annotation_lines(self, data: PlotData, visibility: TraceVisibility) -> list[str]:
        lines: list[str] = []
        if isinstance(data, LinePlotData):
            lines.extend(self._acf_unavailable_lines(data, visibility))
            if data.unavailable_reason is not None:
                lines.append(data.unavailable_reason)
        if isinstance(data, HistogramPlotData):
            lines.extend(self._zero_iat_lines(data, visibility))
        lines.extend(self._metadata_lines(data))
        return lines

    def _acf_unavailable_lines(self, data: LinePlotData, visibility: TraceVisibility) -> list[str]:
        lines: list[str] = []
        if data.requested_lags is None:
            return lines
        if visibility.reference and data.reference_available is not None:
            missing = [
                str(lag)
                for lag, available in zip(data.requested_lags, data.reference_available, strict=True)
                if not available
            ]
            if missing:
                lines.append(f"Reference unavailable lags: {', '.join(missing)}")
        if visibility.generated and data.generated_available is not None:
            missing = [
                str(lag)
                for lag, available in zip(data.requested_lags, data.generated_available, strict=True)
                if not available
            ]
            if missing:
                lines.append(f"Generated unavailable lags: {', '.join(missing)}")
        return lines

    def _zero_iat_lines(self, data: HistogramPlotData, visibility: TraceVisibility) -> list[str]:
        lines: list[str] = []
        for series in data.series:
            if not self._dataset_visible(series.dataset, visibility):
                continue
            if series.zero_count > 0:
                lines.append(f"{series.label} zero IAT samples: {series.zero_count} of {series.sample_count}")
        return lines

    def _metadata_lines(self, data: PlotData) -> list[str]:
        metadata = data.metadata
        return [f"{key}: {self._format_metadata_value(value)}" for key, value in metadata.items()]

    def _format_metadata_value(self, value: MetadataValue) -> str:
        if isinstance(value, tuple):
            return ", ".join(self._format_metadata_value(item) for item in value)
        if isinstance(value, Mapping):
            return ", ".join(f"{key}={self._format_metadata_value(item)}" for key, item in value.items())
        return str(value)

    def _bar_positions(self, data: BarPlotData) -> np.ndarray:
        if data.x_scale == "log":
            return np.arange(1, len(data.categories) + 1, dtype=np.float64)
        return np.arange(len(data.categories), dtype=np.float64)

    def _dataset_visible(self, dataset: SeriesDataset, visibility: TraceVisibility) -> bool:
        if dataset is None:
            return True
        if dataset == "reference":
            return visibility.reference
        return visibility.generated

    def _color_for_dataset(self, dataset: SeriesDataset) -> str:
        if dataset == "generated":
            return _GENERATED_COLOR
        return _REFERENCE_COLOR

    def _has_finite_limits(self, xlim: tuple[float, float], ylim: tuple[float, float]) -> bool:
        return all(math.isfinite(value) for value in (*xlim, *ylim))

    def _modifier_axes(self, key: str | None) -> AxisSelection:
        if key == "shift":
            return "x"
        if key in {"control", "ctrl"}:
            return "y"
        return "both"

    def _event_point(self, event: MouseEvent) -> tuple[float, float] | None:
        if event.inaxes != self.axes or event.xdata is None or event.ydata is None:
            return None
        if not math.isfinite(event.xdata) or not math.isfinite(event.ydata):
            return None
        return (float(event.xdata), float(event.ydata))

    def _view_from_axes(self) -> AxisView | None:
        xlim = self.axes.get_xlim()
        ylim = self.axes.get_ylim()
        if not self._has_finite_limits(xlim, ylim):
            return None
        return AxisView(x=xlim, y=ylim)

    def _axis_scales(self) -> tuple[AxisScale, AxisScale]:
        x_scale = self.axes.get_xscale()
        y_scale = self.axes.get_yscale()
        if x_scale not in {"linear", "log"} or y_scale not in {"linear", "log"}:
            raise ValueError("dashboard interaction supports only linear and log axes")
        return cast(AxisScale, x_scale), cast(AxisScale, y_scale)

    def _on_press(self, event: MouseEvent) -> None:
        if event.dblclick and event.button == MouseButton.LEFT:
            self.reset_view()
            return
        if event.button != MouseButton.LEFT:
            return
        point = self._event_point(event)
        view = self._view_from_axes()
        if point is None or view is None:
            return
        x_scale, y_scale = self._axis_scales()
        self._drag_state = _DragState(anchor=point, view=view, x_scale=x_scale, y_scale=y_scale)

    def _on_motion(self, event: MouseEvent) -> None:
        if self._drag_state is None:
            return
        point = self._event_point(event)
        if point is None:
            return
        view = pan_limits(
            xlim=self._drag_state.view.x,
            ylim=self._drag_state.view.y,
            anchor=self._drag_state.anchor,
            current=point,
            x_scale=self._drag_state.x_scale,
            y_scale=self._drag_state.y_scale,
        )
        if view is None:
            return
        self._set_view(view)
        self.figure_canvas.draw()

    def _on_release(self, event: MouseEvent) -> None:
        if event.button == MouseButton.LEFT:
            self._drag_state = None

    def _on_scroll(self, event: MouseEvent) -> None:
        point = self._event_point(event)
        view = self._view_from_axes()
        if point is None or view is None:
            return
        factor = 0.8 if event.step > 0 else 1.25 if event.step < 0 else None
        if factor is None:
            return
        x_scale, y_scale = self._axis_scales()
        updated = zoom_limits(
            xlim=view.x,
            ylim=view.y,
            cursor=point,
            factor=factor,
            axes=self._modifier_axes(event.key),
            x_scale=x_scale,
            y_scale=y_scale,
        )
        if updated is None:
            return
        self._set_view(updated)
        self.figure_canvas.draw()
