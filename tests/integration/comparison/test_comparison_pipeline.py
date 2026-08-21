import copy
import hashlib
import json
import os
from pathlib import Path
from typing import cast

import pytest

import trafficlab.artifacts.io as artifacts
import trafficlab.comparison.codec as comparison_codec
import trafficlab.comparison.publication as comparison_publication
from tests.fixtures.paths import PIPELINE_FIXTURE_ROOT
from trafficlab.artifacts.run_directory import create_run_directory
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_experiment, render_effective_config
from trafficlab.comparison.codec import load_comparison_result
from trafficlab.comparison.stage import compare_experiment

pytestmark = pytest.mark.integration

_REPOSITORY = Path(__file__).parents[3]
_EXAMPLE_DATA = PIPELINE_FIXTURE_ROOT
_EXPECTED_AGGREGATE_SCORE = 0.5662202380952381
_EXPECTED_METHOD_SCORES = {
    "autocorrelation": 0.756547619047619,
    "frame_size_ks": 0.8,
    "iat_ks": 0.5,
    "multiscale_rate": 0.20833333333333326,
}


def test_real_offline_comparison_uses_the_snapshot_one_window_all_metrics_and_durable_publication(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any disconnected boundary would invalidate the authoritative reproducible offline comparison stage."""
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / "run"
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    config = ExperimentConfig.model_validate(data)
    caller_path = tmp_path / "caller.toml"
    caller_path.write_bytes(render_effective_config(config))
    create_run_directory(config)
    for artifact_name in (
        "capture.json",
        "reference.pcapng",
        "best_model.json",
        "generated.pcapng",
    ):
        (run_directory / artifact_name).write_bytes((_EXAMPLE_DATA / artifact_name).read_bytes())
    capture_bytes = (run_directory / "capture.json").read_bytes()
    real_fsync = os.fsync
    real_link = os.link
    fsync_calls: list[int] = []
    linked: list[tuple[Path, Path]] = []

    def observed_fsync(file_descriptor: int) -> None:
        if not fsync_calls:
            assert os.fstat(file_descriptor).st_size > 0
        fsync_calls.append(file_descriptor)
        real_fsync(file_descriptor)

    def observed_link(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        assert source_path.parent == destination_path.parent == run_directory
        assert source_path.name.startswith(".similarity.json.")
        assert not destination_path.exists()
        assert fsync_calls
        load_comparison_result(source_path)
        linked.append((source_path, destination_path))
        real_link(source, destination)

    monkeypatch.setattr(comparison_publication.os, "fsync", observed_fsync)
    monkeypatch.setattr(comparison_publication.os, "link", observed_link)
    monkeypatch.setattr(artifacts.os, "fsync", observed_fsync)

    result = compare_experiment(caller_path)

    published = load_comparison_result(run_directory / "similarity.json")
    assert load_experiment(caller_path) == load_experiment(run_directory / "experiment.toml") == config
    assert published == result
    assert result.aggregate_score == _EXPECTED_AGGREGATE_SCORE
    assert result.observation_window_seconds == 10.0
    assert result.methods.keys() == ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")
    assert {name: method.score for name, method in result.methods.items()} == pytest.approx(_EXPECTED_METHOD_SCORES)
    assert all(method.diagnostics["observation_window_seconds"] == 10.0 for method in result.methods.values())
    assert result.input_sha256 == {
        "capture_json": hashlib.sha256(capture_bytes).hexdigest(),
        "generated_pcapng": hashlib.sha256((run_directory / "generated.pcapng").read_bytes()).hexdigest(),
        "reference_pcapng": hashlib.sha256((run_directory / "reference.pcapng").read_bytes()).hexdigest(),
        "similarity_settings": comparison_codec.similarity_settings_sha256(config.similarity),
    }
    assert linked == [(linked[0][0], run_directory / "similarity.json")]
    assert not linked[0][0].exists()
    assert len(fsync_calls) == 3
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []
    assert json.loads((run_directory / "similarity.json").read_text(encoding="utf-8"))["methods"] == {
        name: method.as_dict() for name, method in result.methods.items()
    }
    assert json.loads((run_directory / "run.log").read_text(encoding="utf-8").splitlines()[-1]) == {
        "aggregate_score": _EXPECTED_AGGREGATE_SCORE,
        "event": "comparison_succeeded",
        "observation_window_seconds": 10.0,
        "path": str(run_directory / "similarity.json"),
        "reused": False,
        "stage": "compare",
    }

    retried = compare_experiment(caller_path)

    assert retried == result
    assert linked == [(linked[0][0], run_directory / "similarity.json")]
    assert len(fsync_calls) == 4
    assert json.loads((run_directory / "run.log").read_text(encoding="utf-8").splitlines()[-1]) == {
        "aggregate_score": _EXPECTED_AGGREGATE_SCORE,
        "event": "comparison_succeeded",
        "observation_window_seconds": 10.0,
        "path": str(run_directory / "similarity.json"),
        "reused": True,
        "stage": "compare",
    }
