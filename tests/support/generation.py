from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

from tests.fixtures.paths import PIPELINE_FIXTURE_ROOT
from trafficlab.artifacts.run_directory import create_run_directory
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.scapy_io import encode_pcapng
from trafficlab.common.trace import CaptureMetadata, TraceEvent, TrafficTrace, parse_capture_metadata
from trafficlab.generation.models.fitted_model import load_best_model, runtime_fitted_model
from trafficlab.generation.models.registry import get_family

EXAMPLE_DATA = PIPELINE_FIXTURE_ROOT
MODEL_BYTES = (EXAMPLE_DATA / "models" / "best_model.json").read_bytes()
CAPTURE_BYTES = (EXAMPLE_DATA / "capture.json").read_bytes()


def authoritative_trace(
    events: tuple[TraceEvent, ...], metadata: CaptureMetadata, *, window: float = 1.0
) -> TrafficTrace:
    return encode_pcapng(TrafficTrace.from_events(events), metadata, observation_window_seconds=window).trace


def expected_scapy_final_content(config: ExperimentConfig) -> bytes:
    metadata = parse_capture_metadata(CAPTURE_BYTES, source=Path("capture.json"))
    best = load_best_model(MODEL_BYTES, source=Path("best_model.json"))
    reproduced = (
        get_family(best.family)
        .generate(
            runtime_fitted_model(best),
            config.run.final_seed,
            best.observation_window_seconds,
            config.generation.final,
            clock=lambda: 0.0,
        )
        .require_complete()
    )
    return encode_pcapng(
        reproduced,
        metadata,
        observation_window_seconds=best.observation_window_seconds,
    ).content


def prepare_stage_run(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    *,
    name: str = "run",
) -> tuple[Path, Path, ExperimentConfig]:
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / name
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / f"{name}.toml"
    experiment_path.write_bytes(render_effective_config(config))
    create_run_directory(config)
    (run_directory / "best_model.json").write_bytes(MODEL_BYTES)
    (run_directory / "capture.json").write_bytes(CAPTURE_BYTES)
    return experiment_path, run_directory, config


def log_records(run_directory: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (run_directory / "run.log").read_text(encoding="utf-8").splitlines()]
