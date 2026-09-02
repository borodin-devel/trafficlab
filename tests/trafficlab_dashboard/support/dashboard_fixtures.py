from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from tests.fixtures.paths import REPOSITORY_ROOT
from tests.support.config import valid_config_data
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata

_CHECKED_DATA = REPOSITORY_ROOT / "examples" / "data"
TEST_METADATA = CaptureMetadata(interface="eth0", target_mac="02:00:00:00:00:10")


def _events_from_times(times: Sequence[float]) -> tuple[TraceEvent, ...]:
    return tuple(
        TraceEvent(
            float(timestamp),
            Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND,
            60 + index * 10,
        )
        for index, timestamp in enumerate(times)
    )


def _config_for_run_directory(run_directory: Path) -> ExperimentConfig:
    data = valid_config_data(run_directory.parent)
    run_section = dict(cast(dict[str, object], data["run"]))
    data["run"] = {
        **run_section,
        "directory": str(run_directory),
    }
    return ExperimentConfig.model_validate(data)


def write_complete_dashboard_run(
    root: Path,
    *,
    reference_times: Sequence[float] = (10.0, 11.0, 13.0),
    generated_times: Sequence[float] = (20.0, 21.0, 24.0),
) -> Path:
    run_directory = root / "run"
    run_directory.mkdir()
    (run_directory / "capture.json").write_bytes(render_capture_metadata(TEST_METADATA))
    (run_directory / "reference.pcapng").write_bytes(encode_pcapng(_events_from_times(reference_times), TEST_METADATA))
    (run_directory / "generated.pcapng").write_bytes(encode_pcapng(_events_from_times(generated_times), TEST_METADATA))
    for artifact_name, source_name in (
        ("best_model.json", "best_model.json"),
        ("ga_history.csv", "fit/ga_history.csv"),
        ("similarity.json", "similarity.json"),
    ):
        (run_directory / artifact_name).write_bytes((_CHECKED_DATA / source_name).read_bytes())
    experiment = _config_for_run_directory(run_directory)
    (run_directory / "experiment.toml").write_bytes(render_effective_config(experiment))
    return run_directory


def copy_checked_dashboard_run(root: Path) -> Path:
    run_directory = root / "run"
    run_directory.mkdir(parents=True)
    for source_name, destination_name in (
        ("capture.json", "capture.json"),
        ("reference.pcapng", "reference.pcapng"),
        ("generated.pcapng", "generated.pcapng"),
        ("best_model.json", "best_model.json"),
        ("similarity.json", "similarity.json"),
        ("fit/experiment.toml", "experiment.toml"),
        ("fit/ga_history.csv", "ga_history.csv"),
    ):
        shutil.copy2(_CHECKED_DATA / source_name, run_directory / destination_name)
    return run_directory
