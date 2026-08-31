from __future__ import annotations

import importlib.metadata

from packaging.specifiers import SpecifierSet


def test_dashboard_distribution_and_entrypoint_are_declared() -> None:
    """Missing the root dashboard script or Python contract would break installation."""
    metadata = importlib.metadata.metadata("trafficlab")
    scripts = {
        entry.name: entry.value for entry in importlib.metadata.entry_points(group="console_scripts")
    }

    assert SpecifierSet(str(metadata["Requires-Python"])) == SpecifierSet(">=3.12,<3.13")
    assert scripts["trafficlab-dashboard"] == "trafficlab_dashboard.app:main"
