"""Workloads owner for Validation Study tooling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.validation_study.common import (
    CURL_COMMON,
    LOCKED_CURL_COMMON,
    TARGET_REFERENCE,
    image_id_value,
    path_entry_exists,
    replace_existing_regular_file,
    require,
    validate_endpoint_url,
    validate_study_id,
)
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_experiment, render_effective_config

if TYPE_CHECKING:
    from scripts.validation_study.common import TransferRange, WorkloadName


@dataclass(frozen=True, slots=True)
class WorkloadSpec:
    name: WorkloadName
    argv: tuple[str, ...]
    transfers: tuple[TransferRange, ...]
    workload_timeout_seconds: float
    total_timeout_seconds: float
    multiscale_widths_seconds: tuple[float, float]


def _validate_workload_specs(specs: tuple[WorkloadSpec, WorkloadSpec, WorkloadSpec], *, url: str) -> None:
    short, streaming, bursty = specs
    expected_short = (
        *LOCKED_CURL_COMMON,
        "--max-time",
        "30",
        "--limit-rate",
        "4M",
        "--range",
        "0-1048575",
        "--max-filesize",
        "1048576",
        "--dump-header",
        "/trafficlab-study/short.headers",
        "--output",
        "/dev/null",
        "--url",
        url,
    )
    expected_streaming = (
        *LOCKED_CURL_COMMON,
        "--max-time",
        "40",
        "--limit-rate",
        "256K",
        "--range",
        "0-4194303",
        "--max-filesize",
        "4194304",
        "--dump-header",
        "/trafficlab-study/streaming.headers",
        "--output",
        "/dev/null",
        "--url",
        url,
    )
    bursty_groups: list[str] = []
    for index, (start, end, filename) in enumerate(bursty.transfers):
        if index:
            bursty_groups.append("--next")
        bursty_groups.extend(
            (
                *LOCKED_CURL_COMMON,
                "--max-time",
                "30",
                "--range",
                f"{start}-{end}",
                "--max-filesize",
                "32768",
                "--dump-header",
                f"/trafficlab-study/{filename}",
                "--output",
                "/dev/null",
                "--url",
                url,
            )
        )
    expected_bursty = ("--parallel", "--parallel-max", "4", "--fail-early", *bursty_groups)
    expected_shape = (
        ("short", ((0, 1048575, "short.headers"),), 35.0, 90.0, (0.001, 0.01), expected_short),
        ("streaming", ((0, 4194303, "streaming.headers"),), 50.0, 120.0, (0.25, 1.0), expected_streaming),
        (
            "bursty",
            tuple(
                (
                    (start, start + 32767, f"bursty-{index}.headers")
                    for index, start in enumerate((0, 524288, 1048576, 1572864, 2097152, 2621440, 3145728, 3670016))
                )
            ),
            35.0,
            90.0,
            (0.001, 0.01),
            expected_bursty,
        ),
    )
    actual_shape = tuple(
        (
            spec.name,
            spec.transfers,
            spec.workload_timeout_seconds,
            spec.total_timeout_seconds,
            spec.multiscale_widths_seconds,
            spec.argv,
        )
        for spec in (short, streaming, bursty)
    )
    require(actual_shape == expected_shape, "workloads must use the exact HTTPS-only curl profile oracle")


def workload_specs(url: str) -> tuple[WorkloadSpec, WorkloadSpec, WorkloadSpec]:
    validated_url = validate_endpoint_url(url)
    short = WorkloadSpec(
        name="short",
        argv=(
            *CURL_COMMON,
            "--max-time",
            "30",
            "--limit-rate",
            "4M",
            "--range",
            "0-1048575",
            "--max-filesize",
            "1048576",
            "--dump-header",
            "/trafficlab-study/short.headers",
            "--output",
            "/dev/null",
            "--url",
            validated_url,
        ),
        transfers=((0, 1048575, "short.headers"),),
        workload_timeout_seconds=35.0,
        total_timeout_seconds=90.0,
        multiscale_widths_seconds=(0.001, 0.01),
    )
    streaming = WorkloadSpec(
        name="streaming",
        argv=(
            *CURL_COMMON,
            "--max-time",
            "40",
            "--limit-rate",
            "256K",
            "--range",
            "0-4194303",
            "--max-filesize",
            "4194304",
            "--dump-header",
            "/trafficlab-study/streaming.headers",
            "--output",
            "/dev/null",
            "--url",
            validated_url,
        ),
        transfers=((0, 4194303, "streaming.headers"),),
        workload_timeout_seconds=50.0,
        total_timeout_seconds=120.0,
        multiscale_widths_seconds=(0.25, 1.0),
    )
    starts = (0, 524288, 1048576, 1572864, 2097152, 2621440, 3145728, 3670016)
    transfers = tuple(((start, start + 32767, f"bursty-{index}.headers") for index, start in enumerate(starts)))
    groups: list[str] = []
    for index, (start, end, filename) in enumerate(transfers):
        if index:
            groups.append("--next")
        groups.extend(
            (
                *CURL_COMMON,
                "--max-time",
                "30",
                "--range",
                f"{start}-{end}",
                "--max-filesize",
                "32768",
                "--dump-header",
                f"/trafficlab-study/{filename}",
                "--output",
                "/dev/null",
                "--url",
                validated_url,
            )
        )
    bursty = WorkloadSpec(
        name="bursty",
        argv=("--parallel", "--parallel-max", "4", "--fail-early", *groups),
        transfers=transfers,
        workload_timeout_seconds=35.0,
        total_timeout_seconds=90.0,
        multiscale_widths_seconds=(0.001, 0.01),
    )
    specs = (short, streaming, bursty)
    _validate_workload_specs(specs, url=validated_url)
    return specs


def _base_run_id(workload: WorkloadName) -> str:
    return {"short": "01-short-r1", "streaming": "02-streaming-r1", "bursty": "03-bursty-r1"}[workload]


def build_base_config(
    workload: WorkloadSpec,
    *,
    repository_root: Path,
    study_id: str,
    url: str,
    capture_image_id: str,
    require_absent_run_directory: bool = True,
) -> ExperimentConfig:
    study_id = validate_study_id(study_id)
    url = validate_endpoint_url(url)
    image_id_value(capture_image_id, name="capture image ID")
    exact_workloads = workload_specs(url)
    require(workload in exact_workloads, "workload must equal one exact Validation Study profile")
    root = repository_root.resolve()
    run_directory = (root / "runs" / "validation_study" / study_id / _base_run_id(workload.name)).resolve()
    if require_absent_run_directory:
        require(not path_entry_exists(run_directory), f"run directory already exists: {run_directory}")
    mount_source = (root / "examples" / "validation_study" / ".study-work" / "mount" / study_id).resolve()
    return ExperimentConfig.model_validate(
        {
            "run": {"directory": run_directory, "minimum_free_bytes": 1048576, "master_seed": 73, "final_seed": 97},
            "target": {
                "image": TARGET_REFERENCE,
                "argv": workload.argv,
                "environment": {},
                "working_directory": "/",
                "mounts": ({"source": mount_source, "target": "/trafficlab-study", "read_only": False},),
            },
            "capture": {
                "image": capture_image_id,
                "network_probe_url": url,
                "readiness_timeout_seconds": 10.0,
                "workload_timeout_seconds": workload.workload_timeout_seconds,
                "flush_timeout_seconds": 5.0,
                "total_timeout_seconds": workload.total_timeout_seconds,
            },
            "generation": {
                "trial": {"max_packets": 25000, "max_output_bytes": 40000000, "max_wall_seconds": 5.0},
                "final": {"max_packets": 50000, "max_output_bytes": 80000000, "max_wall_seconds": 10.0},
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
                "multiscale_widths_seconds": workload.multiscale_widths_seconds,
                "multiscale_scale_weights": (0.5, 0.5),
                "multiscale_packet_weight": 0.5,
                "multiscale_byte_weight": 0.5,
                "max_direction_bin_cells": 100000,
                "method_weights": {
                    "frame_size_ks": 0.25,
                    "iat_ks": 0.25,
                    "autocorrelation": 0.25,
                    "multiscale_rate": 0.25,
                },
            },
        }
    )


def config_with_run_directory(config: ExperimentConfig, run_directory: Path) -> ExperimentConfig:
    require(type(config) is ExperimentConfig, "config must be an ExperimentConfig")
    resolved = run_directory.resolve()
    require(run_directory.is_absolute(), "realized run directory must be absolute")
    require(not path_entry_exists(run_directory), f"run directory already exists: {run_directory}")
    run = config.run.model_copy(update={"directory": resolved})
    return config.model_copy(update={"run": run})


def config_workload(config: ExperimentConfig) -> WorkloadSpec:
    matches = tuple(
        workload for workload in workload_specs(config.capture.network_probe_url) if workload.argv == config.target.argv
    )
    require(len(matches) == 1, "config target argv must equal one exact Validation Study workload profile")
    return matches[0]


def portable_base_config(
    config: ExperimentConfig,
    *,
    repository_root: Path,
    workload: WorkloadSpec,
    require_absent_run_directory: bool = True,
) -> ExperimentConfig:
    study_id = config.run.directory.parent.name
    expected = build_base_config(
        workload,
        repository_root=repository_root,
        study_id=study_id,
        url=config.capture.network_probe_url,
        capture_image_id=config.capture.image,
        require_absent_run_directory=require_absent_run_directory,
    )
    require(config == expected, "base config must equal every locked Validation Study value")
    relative_run = Path("../../../runs/validation_study") / study_id / _base_run_id(workload.name)
    run = config.run.model_copy(update={"directory": relative_run})
    relative_mount = Path("../.study-work/mount") / study_id
    mount = config.target.mounts[0].model_copy(update={"source": relative_mount})
    target = config.target.model_copy(update={"mounts": (mount,)})
    return config.model_copy(update={"run": run, "target": target})


def _write_new_config(destination: Path, content: bytes, *, replace_existing: bool = False) -> None:
    if replace_existing:
        replace_existing_regular_file(
            destination, content, validate=lambda _persisted: None, target_name="checked config"
        )
        return
    require(not path_entry_exists(destination), f"config target already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as stream:
            stream.write(content)
    except FileExistsError as error:
        raise ValueError(f"config target already exists: {destination}") from error


def render_checked_base_config_content(config: ExperimentConfig, repository_root: Path) -> bytes:
    """Render the portable configuration that must reload to the supplied absolute oracle."""
    root = repository_root.resolve()
    workload = config_workload(config)
    portable = portable_base_config(config, repository_root=root, workload=workload)
    return render_effective_config(portable)


def render_checked_base_config(
    config: ExperimentConfig, destination: Path, repository_root: Path, *, replace_existing: bool = False
) -> bytes:
    root = repository_root.resolve()
    workload = config_workload(config)
    expected_destination = root / "examples" / "validation_study" / "configs" / f"{workload.name}.toml"
    require(destination.resolve() == expected_destination, "checked config must use its exact profile path")
    content = render_checked_base_config_content(config, root)
    _write_new_config(destination, content, replace_existing=replace_existing)
    require(load_experiment(destination) == config, "checked config must reload to its exact absolute oracle")
    return content


def render_realized_config(config: ExperimentConfig, destination: Path) -> bytes:
    require(type(config) is ExperimentConfig, "config must be an ExperimentConfig")
    require(config.run.directory.is_absolute(), "realized run directory must be absolute")
    require(not path_entry_exists(config.run.directory), f"run directory already exists: {config.run.directory}")
    require(
        len(config.target.mounts) == 1 and config.target.mounts[0].source.is_absolute(),
        "realized config must contain the one absolute study mount",
    )
    content = render_effective_config(config)
    _write_new_config(destination, content)
    require(load_experiment(destination) == config, "realized config must reload to its exact absolute oracle")
    return content
