from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
import pytest
from PIL import Image
from pytestqt.qtbot import QtBot

from trafficlab.common.errors import TrafficlabError
from trafficlab_dashboard.aspects.base import BarPlotData, BarSeries, LinePlotData, LineSeries, TraceVisibility
from trafficlab_dashboard.plotting.canvas import DashboardCanvas
from trafficlab_dashboard.plotting.export import export_figure


@pytest.fixture
def canvas(qtbot: QtBot) -> Iterator[DashboardCanvas]:
    widget = DashboardCanvas()
    qtbot.addWidget(widget)
    widget.show()
    yield widget
    widget.close()


def _line_data() -> LinePlotData:
    return LinePlotData(
        identifier="throughput",
        label="Throughput",
        title="Throughput · Mbps · ref 3/gen 2",
        x_label="Time (s)",
        y_label="Rate (Mbps)",
        unit="Mbps",
        series=(
            LineSeries(
                label="Reference",
                x=np.array([0.0, 1.0, 2.0], dtype=np.float64),
                y=np.array([1.0, 2.0, 3.0], dtype=np.float64),
                sample_count=3,
                dataset="reference",
            ),
            LineSeries(
                label="Generated",
                x=np.array([0.0, 1.0, 2.0], dtype=np.float64),
                y=np.array([1.5, 2.5, 3.5], dtype=np.float64),
                sample_count=2,
                dataset="generated",
            ),
        ),
        x_limits=(0.0, 2.0),
        y_limits=(1.0, 3.5),
        reference_sample_count=3,
        generated_sample_count=2,
    )


def _direction_bar_data() -> BarPlotData:
    return BarPlotData(
        identifier="direction_balance",
        label="Direction Balance",
        title="Direction Balance",
        categories=("Uplink packets", "Downlink packets"),
        series=(
            BarSeries(
                label="Reference",
                values=np.array([0.75, 0.25], dtype=np.float64),
                sample_count=4,
                dataset="reference",
            ),
            BarSeries(
                label="Generated",
                values=np.array([0.5, 0.5], dtype=np.float64),
                sample_count=4,
                dataset="generated",
            ),
        ),
        y_label="Share",
        unit="proportion",
        y_limits=(0.0, 1.0),
    )


def test_export_svg_contains_current_title_and_visible_series(tmp_path: Path, canvas: DashboardCanvas) -> None:
    canvas.render(_line_data(), TraceVisibility(reference=True, generated=False))
    canvas.axes.set_xlim(0.5, 1.5)
    canvas.axes.set_ylim(1.25, 2.75)
    destination = tmp_path / "plot.svg"

    export_figure(canvas.figure, destination, format="svg")

    root = ElementTree.parse(destination).getroot()
    text = destination.read_text(encoding="utf-8")

    assert root.tag.endswith("svg")
    assert "Throughput" in text
    assert "Reference" in text
    assert "Generated" not in text
    assert list(tmp_path.glob(".plot.svg.*.tmp")) == []


def test_export_png_preserves_current_viewport_and_writes_a_real_image(tmp_path: Path, canvas: DashboardCanvas) -> None:
    canvas.render(_line_data(), TraceVisibility(reference=True, generated=True))
    canvas.axes.set_xlim(0.25, 1.25)
    canvas.axes.set_ylim(1.0, 2.0)
    destination = tmp_path / "plot.png"

    export_figure(canvas.figure, destination, format="png")

    assert destination.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(destination) as image:
        assert image.width > 0
        assert image.height > 0
    assert canvas.axes.get_xlim() == pytest.approx((0.25, 1.25))
    assert canvas.axes.get_ylim() == pytest.approx((1.0, 2.0))


def test_export_direction_balance_contains_only_visible_dataset(tmp_path: Path, canvas: DashboardCanvas) -> None:
    canvas.render(_direction_bar_data(), TraceVisibility(reference=True, generated=False))
    destination = tmp_path / "direction.svg"

    export_figure(canvas.figure, destination, format="svg")

    text = destination.read_text(encoding="utf-8")
    assert "Reference" in text
    assert "Generated" not in text


def test_export_refuses_to_overwrite_an_existing_destination(tmp_path: Path, canvas: DashboardCanvas) -> None:
    canvas.render(_line_data(), TraceVisibility(reference=True, generated=True))
    destination = tmp_path / "plot.svg"
    destination.write_text("keep me", encoding="utf-8")

    with pytest.raises(TrafficlabError, match="already exists"):
        export_figure(canvas.figure, destination, format="svg")

    assert destination.read_text(encoding="utf-8") == "keep me"
