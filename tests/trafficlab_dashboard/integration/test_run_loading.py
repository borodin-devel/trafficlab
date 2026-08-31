from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.trafficlab_dashboard.support.dashboard_fixtures import copy_checked_dashboard_run
from trafficlab.common.errors import TrafficlabError
from trafficlab_dashboard.run_loader import load_dashboard_run


def test_checked_canonical_run_loads_from_tracked_example_artifacts(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)

    loaded = load_dashboard_run(run_directory)

    assert loaded.window > 0.0
    assert loaded.reference_packet_count > 1
    assert loaded.generated_packet_count > 0
    assert loaded.similarity is not None
    assert loaded.best_model is not None
    assert loaded.history is not None
    assert loaded.experiment is not None
    assert dict(loaded.unavailable) == {}


def test_malformed_required_artifact_rejects_loading(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    document = {"interface": "eth0", "target_mac": "02:00:00:00:00:10"}
    (run_directory / "capture.json").write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")

    with pytest.raises(TrafficlabError, match="capture.json"):
        load_dashboard_run(run_directory)


def test_missing_optional_artifact_disables_only_dependent_aspects(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    (run_directory / "similarity.json").unlink()

    loaded = load_dashboard_run(run_directory)

    assert loaded.similarity is None
    assert loaded.history is not None
    assert loaded.unavailable["similarity_scores"] == "similarity.json is missing"
    assert loaded.unavailable["multiscale_discrepancy"] == "similarity.json is missing"
    assert "ga_fitness_history" not in loaded.unavailable
