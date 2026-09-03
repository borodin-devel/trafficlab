from __future__ import annotations

from pathlib import Path
from typing import Final, cast

import pytest
from PySide6.QtGui import QStandardItemModel
from pytestqt.qtbot import QtBot

from tests.trafficlab_dashboard.support.dashboard_fixtures import copy_checked_dashboard_run
from trafficlab_dashboard.app import create_window
from trafficlab_dashboard.aspects.base import PlotData
from trafficlab_dashboard.aspects.registry import ASPECTS
from trafficlab_dashboard.run_loader import load_dashboard_run
from trafficlab_dashboard.window import DashboardWindow

EXPECTED_ASPECT_IDS: Final[tuple[str, ...]] = (
    "throughput",
    "packet_rate",
    "cumulative_bytes",
    "cumulative_packets",
    "frame_size_timeline",
    "iat_timeline",
    "frame_size_ecdf",
    "iat_ecdf",
    "frame_size_histogram",
    "iat_histogram",
    "throughput_ecdf",
    "directional_throughput",
    "directional_packet_rate",
    "direction_balance",
    "frame_size_acf",
    "iat_acf",
    "frame_size_iat_hexbin",
    "similarity_scores",
    "multiscale_discrepancy",
    "fano_allan",
    "transition_fidelity",
    "c2st",
    "ga_fitness_history",
)


def _loaded_window(qtbot: QtBot, run_directory: Path) -> DashboardWindow:
    window = create_window(run_directory)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.state.run is not None and window.canvas.current_aspect == "throughput")
    return window


def test_checked_run_supports_every_available_aspect(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)

    run = load_dashboard_run(run_directory)
    available = [aspect.identifier for aspect in ASPECTS if aspect.identifier not in run.unavailable]

    assert available == list(EXPECTED_ASPECT_IDS)
    assert dict(run.unavailable) == {}


def test_checked_run_window_selects_every_enabled_aspect(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _loaded_window(qtbot, copy_checked_dashboard_run(tmp_path))

    def record_render(data: PlotData, *_args: object, **_kwargs: object) -> None:
        window.canvas._current_aspect = data.identifier  # pyright: ignore[reportPrivateUsage]

    def record_error(title: str, message: str) -> None:
        errors.append((title, message))

    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(window.canvas, "render", record_render)
    monkeypatch.setattr(window, "_show_error_dialog", record_error)
    model = cast(QStandardItemModel, window.aspect_combo.model())

    assert window.state.run is not None
    assert dict(window.state.run.unavailable) == {}
    assert window.aspect_combo.count() == len(EXPECTED_ASPECT_IDS)
    for index, aspect_id in enumerate(EXPECTED_ASPECT_IDS):
        assert window.aspect_combo.itemData(index) == aspect_id
        item = model.item(index)
        assert item is not None
        assert item.isEnabled() is True
        window.aspect_combo.setCurrentIndex(index)
        qtbot.waitUntil(
            lambda aspect_id=aspect_id: window.canvas.current_aspect == aspect_id or bool(errors), timeout=10_000
        )
        assert errors == []
