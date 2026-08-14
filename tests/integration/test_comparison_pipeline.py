import copy
import hashlib
import json
import os
from pathlib import Path
from typing import cast

import pytest

import trafficlab.artifacts as artifacts
import trafficlab.comparison as comparison
from trafficlab.artifacts import create_run_directory
from trafficlab.comparison import compare_experiment, load_comparison_result
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import load_experiment, render_effective_config
from trafficlab.pcapng import write_pcapng
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata

pytestmark = pytest.mark.integration


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
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    capture_bytes = render_capture_metadata(metadata)
    (run_directory / "capture.json").write_bytes(capture_bytes)
    reference = (
        TraceEvent(10.0, Direction.OUTBOUND, 60),
        TraceEvent(11.0, Direction.INBOUND, 80),
        TraceEvent(13.0, Direction.OUTBOUND, 100),
    )
    generated = (
        TraceEvent(100.0, Direction.OUTBOUND, 60),
        TraceEvent(101.0, Direction.INBOUND, 80),
        TraceEvent(103.0, Direction.OUTBOUND, 100),
        TraceEvent(104.0, Direction.INBOUND, 120),
    )
    write_pcapng(run_directory / "reference.pcapng", reference, metadata)
    write_pcapng(run_directory / "generated.pcapng", generated, metadata)
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

    monkeypatch.setattr(comparison.os, "fsync", observed_fsync)
    monkeypatch.setattr(comparison.os, "link", observed_link)
    monkeypatch.setattr(artifacts.os, "fsync", observed_fsync)

    result = compare_experiment(caller_path)

    published = load_comparison_result(run_directory / "similarity.json")
    assert load_experiment(caller_path) == load_experiment(run_directory / "experiment.toml") == config
    assert published == result
    assert result.aggregate_score == 1.0
    assert result.observation_window_seconds == 3.0
    assert tuple(result.methods) == ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")
    assert all(method.score == 1.0 for method in result.methods.values())
    assert all(method.diagnostics["observation_window_seconds"] == 3.0 for method in result.methods.values())
    assert result.input_sha256 == {
        "capture_json": hashlib.sha256(capture_bytes).hexdigest(),
        "generated_pcapng": hashlib.sha256((run_directory / "generated.pcapng").read_bytes()).hexdigest(),
        "reference_pcapng": hashlib.sha256((run_directory / "reference.pcapng").read_bytes()).hexdigest(),
        "similarity_settings": comparison.similarity_settings_sha256(config.similarity),
    }
    assert linked == [(linked[0][0], run_directory / "similarity.json")]
    assert not linked[0][0].exists()
    assert len(fsync_calls) == 2
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []
    assert json.loads((run_directory / "similarity.json").read_text(encoding="utf-8"))["methods"] == {
        name: method.as_dict() for name, method in result.methods.items()
    }
    assert json.loads((run_directory / "run.log").read_text(encoding="utf-8").splitlines()[-1]) == {
        "aggregate_score": 1.0,
        "event": "comparison_succeeded",
        "observation_window_seconds": 3.0,
        "path": str(run_directory / "similarity.json"),
        "reused": False,
        "stage": "compare",
    }

    retried = compare_experiment(caller_path)

    assert retried == result
    assert linked == [(linked[0][0], run_directory / "similarity.json")]
    assert len(fsync_calls) == 3
    assert json.loads((run_directory / "run.log").read_text(encoding="utf-8").splitlines()[-1]) == {
        "aggregate_score": 1.0,
        "event": "comparison_succeeded",
        "observation_window_seconds": 3.0,
        "path": str(run_directory / "similarity.json"),
        "reused": True,
        "stage": "compare",
    }
