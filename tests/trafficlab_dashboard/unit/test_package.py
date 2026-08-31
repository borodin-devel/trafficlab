from __future__ import annotations

import importlib.metadata
import os
import runpy
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet


def test_dashboard_distribution_and_entrypoint_are_declared() -> None:
    """Missing the root dashboard script or Python contract would break installation."""
    metadata = importlib.metadata.metadata("trafficlab")
    scripts = {entry.name: entry.value for entry in importlib.metadata.entry_points(group="console_scripts")}

    assert SpecifierSet(str(metadata["Requires-Python"])) == SpecifierSet(">=3.12,<3.13")
    assert scripts["trafficlab-dashboard"] == "trafficlab_dashboard.app:main"


def test_dashboard_conftest_forces_offscreen_qt_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeping an inherited platform plugin would break deterministic headless Qt tests."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")

    runpy.run_path(str(Path("tests/trafficlab_dashboard/conftest.py")))

    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"
