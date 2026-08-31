from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

from tests.fixtures.paths import REPOSITORY_ROOT
from tests.support.config import valid_config_data
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.common.config import ExperimentConfig, FamilyName
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata

_CHECKED_RUN = REPOSITORY_ROOT / "examples" / "scientific_stack" / "example_run_artifacts"
_OPTIONAL_ARTIFACTS = ("best_model.json", "ga_history.csv", "similarity.json")

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


def _config_for_run_directory(run_directory: Path, *, enabled_families: Sequence[FamilyName]) -> ExperimentConfig:
    data = valid_config_data(run_directory.parent)
    data["run"] = {
        **data["run"],
        "directory": str(run_directory),
    }
    models = dict(data["models"])
    models["enabled"] = list(enabled_families)
    for family_name in ("poisson_empirical", "markov_renewal", "mmpp"):
        if family_name not in enabled_families:
            models.pop(family_name)
    data["models"] = models
    return ExperimentConfig.model_validate(data)


def write_complete_dashboard_run(
    root: Path,
    *,
    reference_times: Sequence[float] = (10.0, 11.0, 13.0),
    generated_times: Sequence[float] = (20.0, 21.0, 24.0),
    enabled_families: Sequence[FamilyName] = ("poisson_empirical", "markov_renewal", "mmpp"),
) -> Path:
    run_directory = root / "run"
    run_directory.mkdir()
    (run_directory / "capture.json").write_bytes(render_capture_metadata(TEST_METADATA))
    (run_directory / "reference.pcapng").write_bytes(encode_pcapng(_events_from_times(reference_times), TEST_METADATA))
    (run_directory / "generated.pcapng").write_bytes(encode_pcapng(_events_from_times(generated_times), TEST_METADATA))
    for artifact_name in _OPTIONAL_ARTIFACTS:
        (run_directory / artifact_name).write_bytes((_CHECKED_RUN / artifact_name).read_bytes())
    experiment = _config_for_run_directory(run_directory, enabled_families=enabled_families)
    (run_directory / "experiment.toml").write_bytes(render_effective_config(experiment))
    return run_directory


def copy_checked_dashboard_run(root: Path) -> Path:
    run_directory = root / "run"
    shutil.copytree(_CHECKED_RUN, run_directory)
    return run_directory
