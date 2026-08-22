"""Shared typed setup for this decomposed validation suite."""

from __future__ import annotations

import json
import platform as platform
import shutil
from pathlib import Path
from typing import cast

import pytest

import scripts.validation_study.common as vs_common
import scripts.validation_study.records as vs_records
import scripts.validation_study.rotation.run as vs_rotation_run
import scripts.validation_study.workloads as vs_workloads
from tests.fixtures.paths import PRE_USER_AGENT_R6_FIXTURE
from tests.support.validation_study.builders import study_result_value, valid_result_document
from trafficlab.common.config import SimilarityConfig
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace

_PRE_USER_AGENT_R6_FIXTURE = PRE_USER_AGENT_R6_FIXTURE


def install_pre_user_agent_r6_predecessor(repository_root: Path) -> tuple[Path, bytes, dict[str, str]]:
    """Install the one retained pre-User-Agent prerequisite publication verbatim."""

    fixture = _PRE_USER_AGENT_R6_FIXTURE
    content = (fixture / "prerequisites.raw.json").read_bytes()
    source = cast(dict[str, str], json.loads((fixture / "source.json").read_text(encoding="utf-8")))
    document = cast(dict[str, str], json.loads(content))
    source["study_id"] = document["study_id"]
    source["url"] = document["url"]
    root = repository_root / "examples" / "validation_study" / "prerequisites.json"
    attempt = root.parent / ".study-work" / "attempts" / source["study_id"]
    evidence = root.parent / ".study-work" / "evidence" / source["study_id"] / "00-prerequisites"
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_bytes(content)
    attempt.mkdir(parents=True)
    shutil.copy2(fixture / "prerequisites.raw.json", attempt / "prerequisites.raw.json")
    shutil.copy2(fixture / "prerequisites-success.json", attempt / "prerequisites-success.json")
    shutil.copytree(fixture / "evidence", evidence)
    return root, content, source


def contains_none(value: object) -> bool:
    if type(value) is dict:
        return any(contains_none(item) for item in cast(dict[object, object], value).values())
    if type(value) is list:
        return any(contains_none(item) for item in cast(list[object], value))
    return value is None


def expected_base_config(
    repository_root: Path,
    workload: str,
    *,
    url: str = "https://downloads.example.test/object.bin",
    study_id: str = "study-1",
    capture_image_id: str = f"sha256:{'d' * 64}",
) -> dict[str, object]:
    specs = {spec.name: spec for spec in vs_workloads.workload_specs(url)}
    spec = specs[cast(vs_common.WorkloadName, workload)]
    first_run = {
        "short": "01-short-r1",
        "streaming": "02-streaming-r1",
        "bursty": "03-bursty-r1",
    }[workload]
    return {
        "run": {
            "directory": (repository_root / "runs" / "validation_study" / study_id / first_run).resolve(),
            "minimum_free_bytes": 1_048_576,
            "master_seed": 73,
            "final_seed": 97,
        },
        "target": {
            "image": vs_common.TARGET_REFERENCE,
            "argv": spec.argv,
            "environment": {},
            "working_directory": "/",
            "mounts": (
                {
                    "source": (
                        repository_root / "examples" / "validation_study" / ".study-work" / "mount" / study_id
                    ).resolve(),
                    "target": "/trafficlab-study",
                    "read_only": False,
                },
            ),
        },
        "capture": {
            "image": capture_image_id,
            "network_probe_url": url,
            "readiness_timeout_seconds": 10.0,
            "workload_timeout_seconds": spec.workload_timeout_seconds,
            "flush_timeout_seconds": 5.0,
            "total_timeout_seconds": spec.total_timeout_seconds,
        },
        "generation": {
            "trial": {"max_packets": 25_000, "max_output_bytes": 40_000_000, "max_wall_seconds": 5.0},
            "final": {"max_packets": 50_000, "max_output_bytes": 80_000_000, "max_wall_seconds": 10.0},
        },
        "genetic": {
            "population_size": 6,
            "generation_count": 2,
            "tournament_size": 2,
            "elite_count": 1,
            "trial_seeds": (17, 29),
            "duplicate_mutation_attempts": 3,
            "early_stopping_generations": 0,
            "early_stopping_tolerance": 0.0,
            "resume": True,
        },
        "models": {
            "enabled": ("poisson_empirical", "markov_renewal", "mmpp"),
            "poisson_empirical": {
                "crossover_probability": 0.9,
                "mutation_probability": 1.0,
                "mutation_scale": 0.1,
                "c_lambda": {"lower": 0.25, "upper": 4.0},
            },
            "markov_renewal": {
                "crossover_probability": 0.9,
                "mutation_probability": 0.2,
                "mutation_scale": 0.1,
                "q1": {"lower": 0.1, "upper": 0.4},
                "q2": {"lower": 0.6, "upper": 0.9},
                "alpha": {"lower": 0.0, "upper": 2.0},
                "r": {"lower": 1, "upper": 8},
                "c_t": {"lower": 0.25, "upper": 4.0},
            },
            "mmpp": {
                "crossover_probability": 0.9,
                "mutation_probability": 0.25,
                "mutation_scale": 0.1,
                "q01": {"lower": 0.01, "upper": 10.0},
                "q10": {"lower": 0.01, "upper": 10.0},
                "lambda0": {"lower": 10.0, "upper": 100.0},
                "lambda1": {"lower": 0.1, "upper": 1000.0},
            },
        },
        "similarity": {
            "iat_diagnostic_quantile": 0.95,
            "acf_lags": (1,),
            "acf_lag_weights": (1.0,),
            "acf_iat_weight": 0.5,
            "acf_size_weight": 0.5,
            "multiscale_widths_seconds": spec.multiscale_widths_seconds,
            "multiscale_scale_weights": (0.5, 0.5),
            "multiscale_packet_weight": 0.5,
            "multiscale_byte_weight": 0.5,
            "max_direction_bin_cells": 100_000,
            "method_weights": {
                "frame_size_ks": 0.25,
                "iat_ks": 0.25,
                "autocorrelation": 0.25,
                "multiscale_rate": 0.25,
            },
        },
    }


def install_prerequisite_failure(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if mutation == "wrong-python":
        monkeypatch.setattr(platform, "python_version", lambda: "3.12.4")
    if mutation == "config-publication-failed":
        original_fsync = vs_rotation_run._commit_prerequisite_fsync  # pyright: ignore[reportPrivateUsage]
        calls = 0

        def fail_second_config(destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("simulated config publication failure")
            original_fsync(destination)

        monkeypatch.setattr(vs_rotation_run, "_commit_prerequisite_fsync", fail_second_config)


def natural_variation_inputs(
    tmp_path: Path,
) -> tuple[
    tuple[vs_records.StudyRunRecord, ...],
    dict[tuple[vs_common.WorkloadName, int], TrafficTrace],
    dict[vs_common.WorkloadName, SimilarityConfig],
    dict[str, object],
]:
    document = valid_result_document(tmp_path)
    records = study_result_value(document).runs
    url = "https://downloads.example.test/object.bin"
    traces: dict[tuple[vs_common.WorkloadName, int], TrafficTrace] = {}
    settings: dict[vs_common.WorkloadName, SimilarityConfig] = {}
    for workload in vs_workloads.workload_specs(url):
        config = vs_workloads.build_base_config(
            workload,
            repository_root=tmp_path,
            study_id="study-1",
            url=url,
            capture_image_id=f"sha256:{'d' * 64}",
        )
        settings[workload.name] = config.similarity
        for repeat in (1, 2, 3):
            start = float(10 * repeat)
            traces[(workload.name, repeat)] = TrafficTrace.from_events(
                (
                    TraceEvent(start, Direction.OUTBOUND, 60 + repeat),
                    TraceEvent(start + 0.25, Direction.INBOUND, 100 + repeat),
                    TraceEvent(start + 1.0, Direction.OUTBOUND, 180 + repeat),
                    TraceEvent(start + float(repeat + 2), Direction.INBOUND, 260 + repeat),
                )
            )
    return records, traces, settings, document
