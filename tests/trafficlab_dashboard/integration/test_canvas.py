# pyright: reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal, cast

import numpy as np
import pytest
from matplotlib.backend_bases import MouseButton, MouseEvent
from matplotlib.colors import to_hex
from matplotlib.patches import Rectangle
from matplotlib.text import Annotation
from pytestqt.qtbot import QtBot

from trafficlab_dashboard.aspects.base import (
    BarPlotData,
    BarSeries,
    HexbinPlotData,
    HistogramPlotData,
    HistogramSeries,
    LinePlotData,
    LineSeries,
    TraceVisibility,
)
from trafficlab_dashboard.plotting.canvas import DashboardCanvas


@pytest.fixture
def canvas(qtbot: QtBot) -> Iterator[DashboardCanvas]:
    widget = DashboardCanvas()
    qtbot.addWidget(widget)
    widget.show()
    yield widget
    widget.close()


def _line_data(*, identifier: str = "throughput") -> LinePlotData:
    return LinePlotData(
        identifier=identifier,
        label="Throughput",
        title="Throughput · Mbps · ref 4/gen 4 · 0.5 s bins",
        x_label="Time (s)",
        y_label="Rate (Mbps)",
        unit="Mbps",
        series=(
            LineSeries(
                label="Reference uplink",
                x=np.array([0.0, 1.0, 2.0], dtype=np.float64),
                y=np.array([1.0, 2.0, 3.0], dtype=np.float64),
                sample_count=4,
                dataset="reference",
                line_style="solid",
            ),
            LineSeries(
                label="Reference downlink",
                x=np.array([0.0, 1.0, 2.0], dtype=np.float64),
                y=np.array([0.5, 1.5, 2.5], dtype=np.float64),
                sample_count=4,
                dataset="reference",
                line_style="dashed",
            ),
            LineSeries(
                label="Generated uplink",
                x=np.array([0.0, 1.0, 2.0], dtype=np.float64),
                y=np.array([1.5, 2.5, 3.5], dtype=np.float64),
                sample_count=4,
                dataset="generated",
                line_style="solid",
            ),
            LineSeries(
                label="Generated downlink",
                x=np.array([0.0, 1.0, 2.0], dtype=np.float64),
                y=np.array([0.75, 1.25, 1.75], dtype=np.float64),
                sample_count=4,
                dataset="generated",
                line_style="dashed",
            ),
        ),
        x_limits=(0.0, 2.0),
        y_limits=(0.0, 4.0),
        x_scale="linear",
        y_scale="linear",
        bin_width=0.5,
        reference_sample_count=4,
        generated_sample_count=4,
        requested_lags=(1, 2, 3),
        reference_available=(True, False, True),
        generated_available=(True, True, False),
        unavailable_reason="Lag exceeds sample count",
    )


def _histogram_data() -> HistogramPlotData:
    return HistogramPlotData(
        identifier="iat_distribution",
        label="IAT Distribution",
        title="IAT Distribution · s · ref 4/gen 3",
        x_label="IAT (s)",
        y_label="Density",
        unit="s",
        series=(
            HistogramSeries(
                label="Reference",
                edges=np.array([0.25, 0.5, 1.0], dtype=np.float64),
                values=np.array([0.25, 0.75], dtype=np.float64),
                sample_count=4,
                dataset="reference",
                zero_count=2,
                positive_sample_count=2,
            ),
            HistogramSeries(
                label="Generated",
                edges=np.array([0.25, 0.5, 1.0], dtype=np.float64),
                values=np.array([0.5, 0.5], dtype=np.float64),
                sample_count=3,
                dataset="generated",
                zero_count=0,
                positive_sample_count=3,
            ),
        ),
        x_limits=(0.25, 1.0),
        y_limits=(0.0, 1.0),
        x_scale="log",
        y_scale="linear",
        reference_sample_count=4,
        generated_sample_count=3,
    )


def _bar_data() -> BarPlotData:
    return BarPlotData(
        identifier="similarity_scores",
        label="Similarity Scores",
        title="Similarity Scores · ratio",
        categories=("KS", "ACF", "Rate"),
        series=(BarSeries(label="Pair", values=np.array([0.9, 0.5, 0.25], dtype=np.float64), sample_count=3),),
        y_label="Score",
        unit="ratio",
        y_limits=(0.0, 1.0),
        metadata={"run_name": "streaming-r1"},
    )


def _direction_bar_data() -> BarPlotData:
    return BarPlotData(
        identifier="direction_balance",
        label="Direction Balance",
        title="Direction Balance · proportion",
        categories=("Uplink packets", "Downlink packets", "Uplink bytes", "Downlink bytes"),
        series=(
            BarSeries(
                label="Reference",
                values=np.array([0.75, 0.25, 0.6, 0.4], dtype=np.float64),
                sample_count=4,
                dataset="reference",
            ),
            BarSeries(
                label="Generated",
                values=np.array([0.5, 0.5, 0.45, 0.55], dtype=np.float64),
                sample_count=4,
                dataset="generated",
            ),
        ),
        y_label="Share",
        unit="proportion",
        y_limits=(0.0, 1.0),
    )


def _run_level_line_data() -> LinePlotData:
    x = np.array([0.0, 1.0, 2.0], dtype=np.float64)
    return LinePlotData(
        identifier="ga_fitness_history",
        label="GA fitness history",
        title="GA fitness history",
        x_label="Generation",
        y_label="Fitness",
        unit="unitless",
        series=tuple(
            LineSeries(label=label, x=x, y=x + offset, sample_count=3)
            for label, offset in zip(("Markov Renewal", "MMPP", "Poisson empirical", "Overall"), range(4), strict=True)
        ),
        x_limits=(0.0, 2.0),
        y_limits=(0.0, 5.0),
    )


def _hexbin_data(*, render_mode: Literal["scatter", "hexbin"] = "scatter") -> HexbinPlotData:
    return HexbinPlotData(
        identifier="frame_size_iat_hexbin",
        label="Frame Size / IAT",
        title=f"Frame Size / IAT · {render_mode}",
        x_label="IAT (s)",
        y_label="Frame Length (B)",
        unit="mixed",
        reference_x=np.array([0.1, 0.2, 0.3], dtype=np.float64),
        reference_y=np.array([100.0, 110.0, 120.0], dtype=np.float64),
        generated_x=np.array([0.15, 0.25], dtype=np.float64),
        generated_y=np.array([130.0, 150.0], dtype=np.float64),
        x_limits=(0.1, 0.3),
        y_limits=(100.0, 150.0),
        reference_sample_count=3,
        generated_sample_count=2,
        render_mode=render_mode,
    )


def _mouse_event(
    canvas: DashboardCanvas,
    name: str,
    *,
    xdata: float,
    ydata: float,
    button: MouseButton | Literal["up", "down"] | None = None,
    step: int = 0,
    dblclick: bool = False,
    key: str | None = None,
) -> MouseEvent:
    xdisplay, ydisplay = canvas.axes.transData.transform((xdata, ydata))
    event = MouseEvent(name, canvas.figure_canvas, xdisplay, ydisplay, button=button, step=step, dblclick=dblclick)
    event.inaxes = canvas.axes
    event.xdata = xdata
    event.ydata = ydata
    event.key = key
    return event


def test_canvas_renders_line_data_with_dataset_colors_direction_styles_and_acf_annotation(
    canvas: DashboardCanvas,
) -> None:
    canvas.render(_line_data(), TraceVisibility(reference=True, generated=True))

    labels = [line.get_label() for line in canvas.axes.lines]
    colors = [to_hex(line.get_color()) for line in canvas.axes.lines]
    styles = [line.get_linestyle() for line in canvas.axes.lines]
    legend = canvas.axes.get_legend()
    legend_labels = [text.get_text() for text in legend.get_texts()] if legend is not None else []
    annotations = [text.get_text() for text in canvas.axes.texts if isinstance(text, Annotation)]

    assert canvas.axes.get_title() == "Throughput · Mbps · ref 4/gen 4 · 0.5 s bins"
    assert canvas.axes.get_xlabel() == "Time (s)"
    assert canvas.axes.get_ylabel() == "Rate (Mbps)"
    assert canvas.axes.get_xlim() == pytest.approx((0.0, 2.0))
    assert canvas.axes.get_ylim() == pytest.approx((0.0, 4.0))
    assert labels == [
        "Reference uplink",
        "Reference downlink",
        "Generated uplink",
        "Generated downlink",
    ]
    assert colors == ["#1f77b4", "#1f77b4", "#ff7f0e", "#ff7f0e"]
    assert styles == ["-", "--", "-", "--"]
    assert legend_labels == labels
    assert any("Reference unavailable lags: 2" in text for text in annotations)
    assert any("Generated unavailable lags: 3" in text for text in annotations)
    assert any("Lag exceeds sample count" in text for text in annotations)


def test_canvas_ordinary_render_of_same_identifier_resets_to_new_complete_bounds(canvas: DashboardCanvas) -> None:
    canvas.render(_line_data(), TraceVisibility(reference=True, generated=True))
    canvas.axes.set_xlim(0.4, 1.2)
    canvas.axes.set_ylim(0.75, 2.25)

    canvas.render(
        LinePlotData(
            identifier="throughput",
            label="Throughput",
            title="Throughput · Mbps · ref 8/gen 8 · 1.0 s bins",
            x_label="Time (s)",
            y_label="Rate (Mbps)",
            unit="Mbps",
            series=_line_data().series,
            x_limits=(10.0, 20.0),
            y_limits=(100.0, 200.0),
            x_scale="linear",
            y_scale="linear",
            bin_width=1.0,
            reference_sample_count=8,
            generated_sample_count=8,
        ),
        TraceVisibility(reference=True, generated=True),
    )

    assert canvas.axes.get_xlim() == pytest.approx((10.0, 20.0))
    assert canvas.axes.get_ylim() == pytest.approx((100.0, 200.0))


def test_canvas_visibility_rerender_preserves_viewport_only_when_caller_requests_it(
    canvas: DashboardCanvas,
) -> None:
    canvas.render(_line_data(), TraceVisibility(reference=True, generated=True))
    canvas.axes.set_xlim(0.4, 1.2)
    canvas.axes.set_ylim(0.75, 2.25)

    canvas.render(_line_data(), TraceVisibility(reference=True, generated=False), preserve_viewport=True)

    assert canvas.axes.get_xlim() == pytest.approx((0.4, 1.2))
    assert canvas.axes.get_ylim() == pytest.approx((0.75, 2.25))
    assert [line.get_label() for line in canvas.axes.lines if line.get_visible()] == [
        "Reference uplink",
        "Reference downlink",
    ]

    canvas.render(_line_data(identifier="packet_rate"), TraceVisibility(reference=True, generated=False))

    assert canvas.axes.get_xlim() == pytest.approx((0.0, 2.0))
    assert canvas.axes.get_ylim() == pytest.approx((0.0, 4.0))


def test_canvas_cached_visibility_update_retains_artists_and_changes_only_visibility(canvas: DashboardCanvas) -> None:
    data = _line_data()
    canvas.render(data, TraceVisibility(reference=True, generated=True))
    retained_ids = [id(line) for line in canvas.axes.lines]

    canvas.render(data, TraceVisibility(reference=True, generated=False), preserve_viewport=True)

    assert [id(line) for line in canvas.axes.lines] == retained_ids
    assert [line.get_visible() for line in canvas.axes.lines] == [True, True, False, False]

    canvas.axes.set_xlim(0.5, 1.0)
    canvas.axes.set_ylim(1.0, 2.0)
    canvas.reset_view()

    assert canvas.axes.get_xlim() == pytest.approx((0.0, 2.0))
    assert canvas.axes.get_ylim() == pytest.approx((0.0, 4.0))


def test_canvas_renders_histogram_zero_iat_annotation_and_log_scale(canvas: DashboardCanvas) -> None:
    canvas.render(_histogram_data(), TraceVisibility(reference=True, generated=False))

    labels = [patch.get_label() for patch in canvas.axes.patches if patch.get_visible()]
    annotations = [text.get_text() for text in canvas.axes.texts if isinstance(text, Annotation)]

    assert canvas.axes.get_xscale() == "log"
    assert canvas.axes.get_yscale() == "linear"
    assert labels == ["Reference"]
    assert any("Reference zero IAT samples: 2 of 4" in text for text in annotations)


def test_canvas_renders_bar_data_even_when_trace_visibility_is_disabled(canvas: DashboardCanvas) -> None:
    canvas.render(_bar_data(), TraceVisibility(reference=False, generated=False))

    tick_labels = [tick.get_text() for tick in canvas.axes.get_xticklabels()]
    heights = [cast(Rectangle, patch).get_height() for patch in canvas.axes.patches]

    assert canvas.axes.get_title() == "Similarity Scores · ratio"
    assert tick_labels == ["KS", "ACF", "Rate"]
    assert heights == pytest.approx([0.9, 0.5, 0.25])


def test_canvas_filters_dataset_bar_series_but_preserves_dataset_free_bars(canvas: DashboardCanvas) -> None:
    canvas.render(_direction_bar_data(), TraceVisibility(reference=True, generated=False))

    assert [
        cast(Rectangle, patch).get_height() for patch in canvas.axes.patches if patch.get_visible()
    ] == pytest.approx([0.75, 0.25, 0.6, 0.4])
    legend = canvas.axes.get_legend()
    assert legend is not None
    assert [text.get_text() for text in legend.get_texts() if text.get_visible()] == ["Reference"]

    canvas.render(_bar_data(), TraceVisibility(reference=False, generated=False))

    assert [
        cast(Rectangle, patch).get_height() for patch in canvas.axes.patches if patch.get_visible()
    ] == pytest.approx([0.9, 0.5, 0.25])


def test_canvas_uses_deterministic_distinct_styles_for_dataset_free_lines(canvas: DashboardCanvas) -> None:
    canvas.render(_run_level_line_data(), TraceVisibility(reference=False, generated=False))

    assert [to_hex(line.get_color()) for line in canvas.axes.lines] == [
        "#1f77b4",
        "#ff7f0e",
        "#2ca02c",
        "#d62728",
    ]
    assert [line.get_linestyle() for line in canvas.axes.lines] == ["-", "--", "-.", ":"]


@pytest.mark.parametrize(("render_mode", "expected_collection_count"), [("scatter", 2), ("hexbin", 2)])
def test_canvas_renders_hexbin_variants_for_each_visible_dataset(
    canvas: DashboardCanvas,
    render_mode: Literal["scatter", "hexbin"],
    expected_collection_count: int,
) -> None:
    canvas.render(_hexbin_data(render_mode=render_mode), TraceVisibility(reference=True, generated=True))

    assert len(canvas.axes.collections) == expected_collection_count
    assert canvas.axes.get_xlim() == pytest.approx((0.1, 0.3))
    assert canvas.axes.get_ylim() == pytest.approx((100.0, 150.0))


def test_canvas_connects_pan_zoom_and_double_click_reset_interactions(canvas: DashboardCanvas) -> None:
    canvas.render(_line_data(), TraceVisibility(reference=True, generated=True))

    canvas.figure_canvas.callbacks.process(
        "button_press_event",
        _mouse_event(canvas, "button_press_event", xdata=0.5, ydata=1.0, button=MouseButton.LEFT),
    )
    canvas.figure_canvas.callbacks.process(
        "motion_notify_event",
        _mouse_event(canvas, "motion_notify_event", xdata=0.75, ydata=1.5, button=MouseButton.LEFT),
    )

    assert canvas.axes.get_xlim() == pytest.approx((-0.25, 1.75))
    assert canvas.axes.get_ylim() == pytest.approx((-0.5, 3.5))

    canvas.figure_canvas.callbacks.process(
        "button_release_event",
        _mouse_event(canvas, "button_release_event", xdata=0.75, ydata=1.5, button=MouseButton.LEFT),
    )
    canvas.figure_canvas.callbacks.process(
        "scroll_event",
        _mouse_event(canvas, "scroll_event", xdata=1.0, ydata=1.0, step=1, button="up", key="shift"),
    )

    assert canvas.axes.get_xlim() == pytest.approx((0.0, 1.6))
    assert canvas.axes.get_ylim() == pytest.approx((-0.5, 3.5))

    canvas.figure_canvas.callbacks.process(
        "scroll_event",
        _mouse_event(canvas, "scroll_event", xdata=1.0, ydata=1.0, step=-1, button="down", key="control"),
    )

    assert canvas.axes.get_xlim() == pytest.approx((0.0, 1.6))
    assert canvas.axes.get_ylim() == pytest.approx((-0.875, 4.125))

    canvas.figure_canvas.callbacks.process(
        "button_press_event",
        _mouse_event(canvas, "button_press_event", xdata=1.0, ydata=1.0, button=MouseButton.LEFT, dblclick=True),
    )

    assert canvas.axes.get_xlim() == pytest.approx((0.0, 2.0))
    assert canvas.axes.get_ylim() == pytest.approx((0.0, 4.0))


def test_canvas_log_axis_shift_control_and_drag_use_axis_space(canvas: DashboardCanvas) -> None:
    canvas.render(_histogram_data(), TraceVisibility(reference=True, generated=True))
    initial_y = canvas.axes.get_ylim()

    canvas.figure_canvas.callbacks.process(
        "scroll_event",
        _mouse_event(canvas, "scroll_event", xdata=0.5, ydata=0.5, step=1, button="up", key="shift"),
    )
    shifted_x = canvas.axes.get_xlim()
    assert shifted_x[0] > 0.0
    assert canvas.axes.get_ylim() == pytest.approx(initial_y)

    canvas.figure_canvas.callbacks.process(
        "scroll_event",
        _mouse_event(canvas, "scroll_event", xdata=0.5, ydata=0.5, step=-1, button="down", key="ctrl"),
    )
    assert canvas.axes.get_xlim() == pytest.approx(shifted_x)
    assert canvas.axes.get_ylim() != pytest.approx(initial_y)

    canvas.figure_canvas.callbacks.process(
        "button_press_event",
        _mouse_event(canvas, "button_press_event", xdata=0.5, ydata=0.5, button=MouseButton.LEFT),
    )
    canvas.figure_canvas.callbacks.process(
        "motion_notify_event",
        _mouse_event(canvas, "motion_notify_event", xdata=0.75, ydata=0.5, button=MouseButton.LEFT),
    )
    assert canvas.axes.get_xlim()[0] > 0.0
