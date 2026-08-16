#!/usr/bin/env python3
"""Run and validate the fixed MVP Validation Study protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from statistics import fmean, variance
from types import MappingProxyType
from typing import Literal, Protocol, cast
from urllib.parse import urljoin, urlsplit

from trafficlab import __version__
from trafficlab.artifacts import (
    FileIdentity,
    _file_identity,  # pyright: ignore[reportPrivateUsage]
    quantize_generated_events,
)
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.comparison import (
    ComparisonResult,
    compare_traces,
    parse_comparison_result,
    render_comparison_result,
    sha256_bytes,
    similarity_settings_identity,
)
from trafficlab.compatibility import ContentIdentity, identify_bytes, require_compatible
from trafficlab.config import ExperimentConfig, FamilyName, SimilarityConfig
from trafficlab.config_io import load_experiment, render_effective_config
from trafficlab.errors import TrafficlabError
from trafficlab.generation import reproduce_generated_pcapng
from trafficlab.genetic.checkpoint import CheckpointState, parse_checkpoint, render_history_csv
from trafficlab.genetic.evaluation import evaluate_final, validate_evaluation_context
from trafficlab.genetic.strategy import StrategyContext, make_strategy_context
from trafficlab.genetic.types import METHOD_ORDER, Candidate, CandidateId, TrialResult
from trafficlab.models.registry import BestModel, get_family, load_best_model, render_best_model
from trafficlab.pcapng import encode_pcapng, parse_pcapng_bytes
from trafficlab.preflight import open_or_prepare_experiment
from trafficlab.run import RunResult, _validate_final_artifacts, run_experiment  # pyright: ignore[reportPrivateUsage]
from trafficlab.study_evidence import publish_accepted_bundle
from trafficlab.trace import (
    CaptureMetadata,
    Direction,
    TraceEvent,
    align_generated,
    normalize_reference,
    parse_capture_metadata,
)

type JsonScalar = str | int | float | bool
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type FrozenJsonValue = JsonScalar | tuple[FrozenJsonValue, ...] | Mapping[str, FrozenJsonValue]
type FrozenJsonObject = Mapping[str, FrozenJsonValue]
type WorkloadName = Literal["short", "streaming", "bursty"]
type PrerequisiteCommandKind = Literal["docker_matrix", "internet_smoke"]
type TransferRange = tuple[int, int, str]


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class HeldOutEvaluation:
    """One study-only evaluation of a frozen training model on an independent capture."""

    training_model: BestModel
    training_model_identity: ContentIdentity
    capture_identity: ContentIdentity
    reference_identity: ContentIdentity
    generated_identity: ContentIdentity
    similarity_settings_identity: ContentIdentity
    generated_pcapng: bytes
    comparison: ComparisonResult
    comparison_json: bytes
    seed: int
    observation_window_seconds: float


def evaluate_study_held_out(
    *,
    model_content: bytes,
    model_source: Path,
    config: ExperimentConfig,
    capture_content: bytes,
    capture_source: Path,
    reference_content: bytes,
    reference_source: Path,
) -> HeldOutEvaluation:
    """Evaluate a retained training model against one independent held-out capture.

    This is intentionally a study-evidence boundary rather than a relaxation of
    the ordinary generate/compare lineage checks: the retained model keeps its
    training identities while this result records a separate held-out pair.
    """

    if type(config) is not ExperimentConfig:
        raise TypeError("config must be an ExperimentConfig")
    model = load_best_model(model_content, source=model_source)
    capture_identity = identify_bytes(capture_content)
    reference_identity = identify_bytes(reference_content)
    if reference_identity == model.reference_identity:
        raise TrafficlabError(
            "study held-out reference must be an independent held-out reference",
            corrective_action="capture a new held-out reference after freezing the training model",
        )
    require_compatible(
        {
            "final seed": model.final_seed,
            "final generation limits": model.final_limits,
            "training observation window": model.observation_window_seconds,
        },
        {
            "final seed": config.run.final_seed,
            "final generation limits": config.generation.final,
            "training observation window": model.observation_window_seconds,
        },
    )
    metadata = parse_capture_metadata(capture_content, source=capture_source)
    reference, W = normalize_reference(parse_pcapng_bytes(reference_content, metadata, source=reference_source))
    if W != model.observation_window_seconds:
        raise TrafficlabError(
            "study held-out reference window must equal the frozen training model window",
            corrective_action="retain a held-out capture with the protocol observation window",
        )
    _, generated, generated_pcapng = reproduce_generated_pcapng(model, metadata)
    aligned = align_generated(generated, W)
    settings_identity = similarity_settings_identity(config.similarity)
    comparison = compare_traces(reference, aligned, W, config.similarity).with_input_identities(
        {
            "capture_json": capture_identity,
            "generated_pcapng": identify_bytes(generated_pcapng),
            "reference_pcapng": reference_identity,
            "similarity_settings": settings_identity,
        }
    )
    comparison_json = render_comparison_result(comparison)
    return HeldOutEvaluation(
        training_model=model,
        training_model_identity=identify_bytes(model_content),
        capture_identity=capture_identity,
        reference_identity=reference_identity,
        generated_identity=identify_bytes(generated_pcapng),
        similarity_settings_identity=settings_identity,
        generated_pcapng=generated_pcapng,
        comparison=comparison,
        comparison_json=comparison_json,
        seed=model.final_seed,
        observation_window_seconds=W,
    )


TARGET_REFERENCE = "curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b"
FAMILY_ORDER: tuple[FamilyName, ...] = ("markov_renewal", "mmpp", "poisson_empirical")
PUBLISHED_METHOD_ORDER = METHOD_ORDER
ARTIFACT_NAMES = (
    "experiment.toml",
    "reference.pcapng",
    "capture.json",
    "checkpoint.json",
    "ga_history.csv",
    "best_model.json",
    "generated.pcapng",
    "similarity.json",
    "run.log",
)
PRIMARY_ORDER = (
    (1, "01-short-r1", "short", 1),
    (2, "02-streaming-r1", "streaming", 1),
    (3, "03-bursty-r1", "bursty", 1),
    (4, "04-streaming-r2", "streaming", 2),
    (5, "05-bursty-r2", "bursty", 2),
    (6, "06-short-r2", "short", 2),
    (7, "07-bursty-r3", "bursty", 3),
    (8, "08-short-r3", "short", 3),
    (9, "09-streaming-r3", "streaming", 3),
)
RUNTIME_BOUNDARY = "run_experiment_cached_images_full_lifecycle"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPORT_HEADINGS = (
    "## Question, scope, environment, and protocol",
    "## Natural variation",
    "## Family champions",
    "## Fresh simulation, published, and runtime",
    "## Trace diagnostics",
    "## Saved-run reproduction",
    "## Limitations and next work",
)
CURL_COMMON = (
    "--fail",
    "--silent",
    "--show-error",
    "--location",
    "--max-redirs",
    "3",
    "--proto",
    "=https",
    "--proto-redir",
    "=https",
    "--http1.1",
    "--connect-timeout",
    "15",
)
_LOCKED_CURL_COMMON = CURL_COMMON
_ORACLE_URL = "https://validation-study.example/object"

PREREQUISITE_ROOT_KEYS = (
    "schema_version",
    "created_utc",
    "study_id",
    "git_commit",
    "git_tree_clean",
    "url",
    "tools",
    "images",
    "capability",
    "config_sha256",
    "commands",
)
IMAGE_KEYS = (
    "target_reference",
    "target_image_id",
    "target_repo_digests",
    "target_config_user",
    "capture_image_id",
    "capture_dockerfile_sha256",
    "capture_script_sha256",
)
CAPABILITY_KEYS = (
    "argv",
    "started_utc",
    "completed_utc",
    "exit_status",
    "status",
    "content_length",
    "object_size_bytes",
    "redirect_count",
    "body_bytes_downloaded",
    "content_range",
    "final_url",
    "mount_source",
    "canary_archive_path",
    "canary_sha256",
    "container_id",
    "stdout_sha256",
    "stderr_sha256",
    "used_image_default_user",
    "mount_directory_mode",
    "canary_file_mode",
    "canary_archive_mode",
    "container_cleanup_verified",
)
ENVIRONMENT_KEYS = (
    "git_commit",
    "python_version",
    "trafficlab_version",
    "docker_engine_version",
    "docker_compose_version",
    "platform",
    "target_image_id",
    "capture_image_id",
    "study_date_utc",
)
PROTOCOL_KEYS = (
    "study_id",
    "url",
    "capability",
    "prerequisites_sha256",
    "target_reference",
    "capture_image_id",
    "transfer_evidence_mount_source",
    "base_config_sha256",
    "primary_order",
    "seeds",
    "families",
    "methods",
    "workloads",
    "runtime_boundary",
)
TRANSFER_RESPONSE_KEYS = (
    "transfer_index",
    "requested_start",
    "requested_end",
    "status",
    "content_length",
    "content_range",
    "header_archive_path",
    "header_sha256",
    "scratch_precreate_mode",
    "archive_mode",
    "inode_preserved",
)
RAW_SEQUENCE_KEYS = (
    "seed",
    "observation_window_seconds",
    "trial_event_count",
    "final_event_count",
    "raw_events_equal",
    "fresh_simulation_score_reproduced",
    "reparsed_event_count",
    "reparsed_matches_quantized",
)
WORKLOAD_SUMMARY_KEYS = (
    "workload",
    "runtime",
    "family_champions",
    "winner_selection_fitness",
    "fresh_simulation",
    "published",
    "reference_descriptors",
    "winner_counts",
)
REPRODUCTION_COMPARISON_KEYS = (
    "winner_family_equal",
    "winner_genes_equal",
    "winner_selection_fitness_delta",
    "fresh_simulation_delta",
    "published_delta",
    "reference_similarity",
)
_RESULT_ROOT_KEYS = (
    "schema_version",
    "environment",
    "protocol",
    "runs",
    "natural_variation",
    "workload_summaries",
    "reproduction",
)
_STUDY_RUN_KEYS = (
    "execution_order",
    "run_id",
    "key",
    "config_path",
    "run_directory",
    "transfer_evidence_directory",
    "elapsed_seconds",
    "reuse",
    "cleanup_verified",
    "transfer_responses",
    "artifact_sha256",
    "reference",
    "generated",
    "family_champions",
    "winner",
    "fresh_simulation",
    "published",
    "raw_sequence",
)
_REPRODUCTION_KEYS = (
    "source_key",
    "execution_order",
    "run_id",
    "config_path",
    "run_directory",
    "transfer_evidence_directory",
    "command",
    "guard_command",
    "guard_exit_status",
    "guard_stdout_sha256",
    "guard_stderr_sha256",
    "elapsed_seconds",
    "changed_config_fields",
    "same_locked_config",
    "seeded_artifact_count",
    "cleanup_verified",
    "reuse",
    "transfer_responses",
    "artifact_sha256",
    "reference",
    "generated",
    "family_champions",
    "winner",
    "fresh_simulation",
    "published",
    "raw_sequence",
    "comparison_to_source",
)
_DESCRIPTOR_KEYS = (
    "packet_count",
    "observation_window_seconds",
    "outbound_packets",
    "inbound_packets",
    "outbound_bytes",
    "inbound_bytes",
)

_STUDY_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_UTC_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
_DNS_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
SUBPROCESS_TIMEOUTS = {
    "git_or_version": 20.0,
    "image_pull_or_build": 300.0,
    "capability": 45.0,
    "container_inspect_or_remove": 20.0,
    "docker_matrix_guard": 1230.0,
    "internet_smoke_guard": 630.0,
    "reproduction_guard": 1230.0,
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _require_type(condition: bool, message: str) -> None:
    if not condition:
        raise TypeError(message)


def _require_frozen_mapping(value: object, *, name: str) -> FrozenJsonObject:
    _require_type(type(value) is MappingProxyType, f"{name} must be a frozen JSON object")
    return cast(FrozenJsonObject, value)


@dataclass(frozen=True, slots=True)
class WorkloadSpec:
    name: WorkloadName
    argv: tuple[str, ...]
    transfers: tuple[TransferRange, ...]
    workload_timeout_seconds: float
    total_timeout_seconds: float
    multiscale_widths_seconds: tuple[float, float]


def _validate_workload_specs(
    specs: tuple[WorkloadSpec, WorkloadSpec, WorkloadSpec],
    *,
    url: str,
) -> None:
    short, streaming, bursty = specs
    expected_short = (
        *_LOCKED_CURL_COMMON,
        "--max-time",
        "30",
        "--limit-rate",
        "4M",
        "--range",
        "0-262143",
        "--max-filesize",
        "262144",
        "--dump-header",
        "/trafficlab-study/short.headers",
        "--output",
        "/dev/null",
        "--url",
        url,
    )
    expected_streaming = (
        *_LOCKED_CURL_COMMON,
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
                *_LOCKED_CURL_COMMON,
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
        ("short", ((0, 262143, "short.headers"),), 35.0, 90.0, (0.001, 0.01), expected_short),
        ("streaming", ((0, 4194303, "streaming.headers"),), 50.0, 120.0, (0.25, 1.0), expected_streaming),
        (
            "bursty",
            tuple(
                (start, start + 32767, f"bursty-{index}.headers")
                for index, start in enumerate((0, 524288, 1048576, 1572864, 2097152, 2621440, 3145728, 3670016))
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
    _require(actual_shape == expected_shape, "workloads must use the exact HTTPS-only curl profile oracle")


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
            "0-262143",
            "--max-filesize",
            "262144",
            "--dump-header",
            "/trafficlab-study/short.headers",
            "--output",
            "/dev/null",
            "--url",
            validated_url,
        ),
        transfers=((0, 262143, "short.headers"),),
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
    transfers = tuple((start, start + 32767, f"bursty-{index}.headers") for index, start in enumerate(starts))
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
    return {
        "short": "01-short-r1",
        "streaming": "02-streaming-r1",
        "bursty": "03-bursty-r1",
    }[workload]


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
    _image_id(capture_image_id, name="capture image ID")
    exact_workloads = workload_specs(url)
    _require(workload in exact_workloads, "workload must equal one exact Validation Study profile")
    root = repository_root.resolve()
    run_directory = (root / "runs" / "validation_study" / study_id / _base_run_id(workload.name)).resolve()
    if require_absent_run_directory:
        _require(not _path_entry_exists(run_directory), f"run directory already exists: {run_directory}")
    mount_source = (root / "examples" / "validation_study" / ".study-work" / "mount" / study_id).resolve()
    return ExperimentConfig.model_validate(
        {
            "run": {
                "directory": run_directory,
                "minimum_free_bytes": 1_048_576,
                "master_seed": 73,
                "final_seed": 97,
            },
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
                "trial": {
                    "max_packets": 25_000,
                    "max_output_bytes": 40_000_000,
                    "max_wall_seconds": 5.0,
                },
                "final": {
                    "max_packets": 50_000,
                    "max_output_bytes": 80_000_000,
                    "max_wall_seconds": 10.0,
                },
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
                "max_direction_bin_cells": 100_000,
                "method_weights": {
                    "frame_size_ks": 0.25,
                    "iat_ks": 0.25,
                    "autocorrelation": 0.25,
                    "multiscale_rate": 0.25,
                },
            },
        }
    )


def _config_with_run_directory(  # pyright: ignore[reportUnusedFunction]
    config: ExperimentConfig, run_directory: Path
) -> ExperimentConfig:
    _require(type(config) is ExperimentConfig, "config must be an ExperimentConfig")
    resolved = run_directory.resolve()
    _require(run_directory.is_absolute(), "realized run directory must be absolute")
    _require(not _path_entry_exists(run_directory), f"run directory already exists: {run_directory}")
    run = config.run.model_copy(update={"directory": resolved})
    return config.model_copy(update={"run": run})


def _workload_for_config(config: ExperimentConfig) -> WorkloadSpec:
    matches = tuple(
        workload for workload in workload_specs(config.capture.network_probe_url) if workload.argv == config.target.argv
    )
    _require(len(matches) == 1, "config target argv must equal one exact Validation Study workload profile")
    return matches[0]


def _portable_base_config(
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
    _require(config == expected, "base config must equal every locked Validation Study value")
    relative_run = Path("../../../runs/validation_study") / study_id / _base_run_id(workload.name)
    run = config.run.model_copy(update={"directory": relative_run})
    relative_mount = Path("../.study-work/mount") / study_id
    mount = config.target.mounts[0].model_copy(update={"source": relative_mount})
    target = config.target.model_copy(update={"mounts": (mount,)})
    return config.model_copy(update={"run": run, "target": target})


def _write_new_config(destination: Path, content: bytes) -> None:
    _require(not _path_entry_exists(destination), f"config target already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as stream:
            stream.write(content)
    except FileExistsError as error:
        raise ValueError(f"config target already exists: {destination}") from error


def render_checked_base_config(config: ExperimentConfig, destination: Path, repository_root: Path) -> bytes:
    root = repository_root.resolve()
    workload = _workload_for_config(config)
    expected_destination = root / "examples" / "validation_study" / "configs" / f"{workload.name}.toml"
    _require(destination.resolve() == expected_destination, "checked config must use its exact profile path")
    portable = _portable_base_config(config, repository_root=root, workload=workload)
    content = render_effective_config(portable)
    _write_new_config(destination, content)
    _require(load_experiment(destination) == config, "checked config must reload to its exact absolute oracle")
    return content


def _render_realized_config(  # pyright: ignore[reportUnusedFunction]
    config: ExperimentConfig, destination: Path
) -> bytes:
    _require(type(config) is ExperimentConfig, "config must be an ExperimentConfig")
    _require(config.run.directory.is_absolute(), "realized run directory must be absolute")
    _require(
        not _path_entry_exists(config.run.directory),
        f"run directory already exists: {config.run.directory}",
    )
    _require(
        len(config.target.mounts) == 1 and config.target.mounts[0].source.is_absolute(),
        "realized config must contain the one absolute study mount",
    )
    content = render_effective_config(config)
    _write_new_config(destination, content)
    _require(load_experiment(destination) == config, "realized config must reload to its exact absolute oracle")
    return content


def validate_base_configs(
    repository_root: Path,
    prerequisites: PrerequisiteResults,
    *,
    require_absent_run_directories: bool = True,
) -> dict[WorkloadName, ExperimentConfig]:
    root = repository_root.resolve()
    validated_prerequisites = _validate_prerequisite_document(
        _prerequisite_document(prerequisites), repository_root=root
    )
    capture_image_id = cast(str, validated_prerequisites.images["capture_image_id"])
    hashes = validated_prerequisites.config_sha256
    result: dict[WorkloadName, ExperimentConfig] = {}
    for workload in workload_specs(validated_prerequisites.url):
        path = root / "examples" / "validation_study" / "configs" / f"{workload.name}.toml"
        try:
            content = path.read_bytes()
        except OSError as error:
            raise ValueError(f"could not read checked {workload.name} config: {error}") from error
        _require(
            hashlib.sha256(content).hexdigest() == hashes[workload.name],
            f"checked {workload.name} config SHA-256 must equal prerequisite evidence",
        )
        config = load_experiment(path)
        expected = build_base_config(
            workload,
            repository_root=root,
            study_id=validated_prerequisites.study_id,
            url=validated_prerequisites.url,
            capture_image_id=capture_image_id,
            require_absent_run_directory=require_absent_run_directories,
        )
        _require(config == expected, f"checked {workload.name} config must equal every locked Validation Study value")
        expected_content = render_effective_config(
            _portable_base_config(
                expected,
                repository_root=root,
                workload=workload,
                require_absent_run_directory=require_absent_run_directories,
            )
        )
        _require(content == expected_content, f"checked {workload.name} config must use exact portable TOML")
        result[workload.name] = config
    return result


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _workload_url(workload: WorkloadSpec) -> str:
    urls = tuple(workload.argv[index + 1] for index, token in enumerate(workload.argv[:-1]) if token == "--url")
    _require(bool(urls) and len(set(urls)) == 1, "workload must contain one exact URL for every transfer")
    url = validate_endpoint_url(urls[0])
    _require(workload in workload_specs(url), "workload must equal one exact Validation Study profile")
    return url


def prepare_transfer_scratch(
    repository_root: Path,
    study_id: str,
    run_id: str,
    workload: WorkloadSpec,
) -> dict[str, tuple[Path, int]]:
    _workload_url(workload)
    _require(
        re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", run_id) is not None,
        "run ID must be a simple lowercase identifier",
    )
    root = repository_root.resolve()
    mount_directory = root / "examples" / "validation_study" / ".study-work" / "mount" / validate_study_id(study_id)
    if _path_entry_exists(mount_directory):
        mode = mount_directory.lstat().st_mode
        _require(stat.S_ISDIR(mode) and not stat.S_ISLNK(mode), "study mount must be a regular directory")
    else:
        mount_directory.mkdir(parents=True, mode=0o755)
    mount_directory.chmod(0o755)
    prepared: dict[str, tuple[Path, int]] = {}
    for _start, _end, filename in workload.transfers:
        path = mount_directory / filename
        if _path_entry_exists(path):
            mode = path.lstat().st_mode
            _require(stat.S_ISREG(mode) and not stat.S_ISLNK(mode), f"scratch {filename} must be a regular file")
            path.unlink()
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666)
        except FileExistsError as error:
            raise ValueError(f"scratch {filename} already exists") from error
        os.close(descriptor)
        path.chmod(0o666)
        metadata = path.lstat()
        _require(
            stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 0o666,
            f"scratch {filename} must be an exclusive regular 0666 file",
        )
        prepared[filename] = (path, metadata.st_ino)

    evidence_parent = root / "examples" / "validation_study" / ".study-work" / "evidence" / study_id
    evidence_parent.mkdir(parents=True, exist_ok=True)
    evidence_directory = evidence_parent / run_id
    try:
        evidence_directory.mkdir()
    except FileExistsError as error:
        raise ValueError(f"transfer evidence directory already exists: {evidence_directory}") from error
    return prepared


def _header_blocks(content: bytes) -> tuple[tuple[int, dict[str, list[str]]], ...]:
    _require(bool(content), "transfer header must be nonempty")
    try:
        text = content.decode("iso-8859-1")
    except UnicodeDecodeError as error:
        raise ValueError("transfer header must use HTTP header bytes") from error
    raw_blocks = tuple(block for block in text.split("\r\n\r\n") if block)
    _require(bool(raw_blocks), "transfer header must contain at least one response block")
    blocks: list[tuple[int, dict[str, list[str]]]] = []
    for raw_block in raw_blocks:
        lines = raw_block.split("\r\n")
        match = re.fullmatch(r"HTTP/1\.1[ \t]+(\d{3})(?:[ \t].*)?", lines[0])
        if match is None:
            raise ValueError("each response block must contain exactly one HTTP/1.1 status line")
        headers: dict[str, list[str]] = {}
        for line in lines[1:]:
            _require(not line.upper().startswith("HTTP/"), "response block must not contain a duplicate status line")
            _require(":" in line, "response header lines must contain a field name and value")
            name, value = line.split(":", 1)
            key = name.strip().lower()
            _require(bool(key), "response header field name must be nonempty")
            headers.setdefault(key, []).append(value.strip())
        blocks.append((int(match.group(1)), headers))
    return tuple(blocks)


def _singleton_header(headers: Mapping[str, list[str]], name: str) -> str:
    values = headers.get(name.lower(), [])
    _require(len(values) == 1, f"final response must contain exactly one {name} header")
    return values[0]


def _parse_transfer_header(
    content: bytes,
    *,
    initial_url: str,
    start: int,
    end: int,
    object_size_bytes: int,
) -> tuple[int, int, str]:
    blocks = _header_blocks(content)
    _require(len(blocks) <= 4, "transfer header must contain at most three redirects")
    current_url = initial_url
    for status_code, headers in blocks[:-1]:
        _require(300 <= status_code <= 399, "every response before the final block must be a redirect")
        location = _singleton_header(headers, "Location")
        current_url = validate_endpoint_url(urljoin(current_url, location))
    status_code, final_headers = blocks[-1]
    _require(status_code == 206, "final transfer response status must be exactly 206")
    content_range = _singleton_header(final_headers, "Content-Range")
    expected_range = f"bytes {start}-{end}/{object_size_bytes}"
    _require(content_range == expected_range, "final Content-Range must equal the exact requested range and total")
    length_text = _singleton_header(final_headers, "Content-Length")
    _require(re.fullmatch(r"[0-9]+", length_text) is not None, "final Content-Length must be an exact integer")
    content_length = int(length_text)
    _require(content_length == end - start + 1, "final Content-Length must equal the requested range length")
    return status_code, content_length, content_range


def _best_effort_archive(
    evidence_directory: Path,
    prepared: Mapping[str, tuple[Path, int]],
) -> str | None:
    diagnostics: list[str] = []
    for filename, (scratch, _inode) in prepared.items():
        archive = evidence_directory / filename
        if _path_entry_exists(archive):
            continue
        try:
            metadata = scratch.lstat()
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                diagnostics.append(f"{filename}: scratch is not a regular file")
                continue
            content = scratch.read_bytes()
            with archive.open("xb") as stream:
                stream.write(content)
            archive.chmod(0o600)
        except OSError as error:
            diagnostics.append(f"{filename}: {error}")
    return "; ".join(diagnostics) if diagnostics else None


def archive_transfer_evidence(
    repository_root: Path,
    study_id: str,
    run_id: str,
    workload: WorkloadSpec,
    prepared: Mapping[str, tuple[Path, int]],
    *,
    object_size_bytes: int,
) -> tuple[JsonObject, ...]:
    initial_url = _workload_url(workload)
    object_size_bytes = _strict_int(object_size_bytes, name="object size", minimum=1)
    _require(
        4 * 1024 * 1024 <= object_size_bytes <= 16 * 1024 * 1024,
        "object size must be from 4 MiB through 16 MiB",
    )
    expected_names = tuple(filename for _start, _end, filename in workload.transfers)
    _require(tuple(prepared) == expected_names, "prepared scratch must contain the exact workload header names")
    root = repository_root.resolve()
    evidence_directory = (
        root / "examples" / "validation_study" / ".study-work" / "evidence" / validate_study_id(study_id) / run_id
    )
    evidence_mode = evidence_directory.lstat().st_mode
    _require(
        stat.S_ISDIR(evidence_mode) and not stat.S_ISLNK(evidence_mode),
        "transfer evidence directory must be prepared exclusively",
    )
    validated: list[tuple[int, int, str, Path, int, bytes, int, int, str]] = []
    try:
        for index, (start, end, filename) in enumerate(workload.transfers):
            scratch, inode = prepared[filename]
            expected_scratch = root / "examples" / "validation_study" / ".study-work" / "mount" / study_id / filename
            _require(scratch == expected_scratch, f"scratch {filename} must use the exact study mount path")
            metadata = scratch.lstat()
            _require(
                stat.S_ISREG(metadata.st_mode)
                and not stat.S_ISLNK(metadata.st_mode)
                and metadata.st_ino == inode
                and stat.S_IMODE(metadata.st_mode) == 0o666,
                f"scratch {filename} must preserve its exclusive regular 0666 inode",
            )
            content = scratch.read_bytes()
            after_read = scratch.lstat()
            _require(after_read.st_ino == inode, f"scratch {filename} inode changed while reading")
            status_code, content_length, content_range = _parse_transfer_header(
                content,
                initial_url=initial_url,
                start=start,
                end=end,
                object_size_bytes=object_size_bytes,
            )
            archive = evidence_directory / filename
            _require(not _path_entry_exists(archive), f"header archive already exists: {archive}")
            validated.append(
                (index, start, filename, archive, inode, content, status_code, content_length, content_range)
            )

        responses: list[JsonObject] = []
        for index, start, filename, archive, inode, content, status_code, content_length, content_range in validated:
            with archive.open("xb") as stream:
                stream.write(content)
            archive.chmod(0o600)
            archived = archive.read_bytes()
            _require(archived == content, f"header archive {filename} must preserve exact bytes")
            _require(stat.S_IMODE(archive.lstat().st_mode) == 0o600, f"header archive {filename} must use mode 0600")
            end = start + content_length - 1
            responses.append(
                {
                    "transfer_index": index,
                    "requested_start": start,
                    "requested_end": end,
                    "status": status_code,
                    "content_length": content_length,
                    "content_range": content_range,
                    "header_archive_path": archive.relative_to(root).as_posix(),
                    "header_sha256": hashlib.sha256(archived).hexdigest(),
                    "scratch_precreate_mode": 438,
                    "archive_mode": 384,
                    "inode_preserved": True,
                }
            )
            scratch = prepared[filename][0]
            _require(scratch.lstat().st_ino == inode, f"scratch {filename} inode changed before removal")
        for _index, _start, filename, _archive, _inode, _content, _status, _length, _range in validated:
            prepared[filename][0].unlink()
        return tuple(responses)
    except (OSError, ValueError) as error:
        _best_effort_archive(evidence_directory, prepared)
        if isinstance(error, ValueError):
            raise
        raise ValueError(f"could not archive transfer evidence: {error}") from error


@dataclass(frozen=True, slots=True)
class StudyRunSpec:
    execution_order: int
    run_id: str
    workload: WorkloadName
    repeat: int
    config_path: Path
    run_directory: Path
    transfer_evidence_directory: Path


@dataclass(frozen=True, slots=True)
class StudyRunRecord:
    execution_order: int
    run_id: str
    key: FrozenJsonObject
    config_path: str
    run_directory: str
    transfer_evidence_directory: str
    elapsed_seconds: float
    reuse: FrozenJsonObject
    cleanup_verified: bool
    transfer_responses: tuple[FrozenJsonObject, ...]
    artifact_sha256: FrozenJsonObject
    reference: FrozenJsonObject
    generated: FrozenJsonObject
    family_champions: tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject]
    winner: FrozenJsonObject
    fresh_simulation: FrozenJsonObject
    published: FrozenJsonObject
    raw_sequence: FrozenJsonObject

    def __post_init__(self) -> None:
        for name in (
            "key",
            "reuse",
            "artifact_sha256",
            "reference",
            "generated",
            "winner",
            "fresh_simulation",
            "published",
            "raw_sequence",
        ):
            _require_frozen_mapping(getattr(self, name), name=name)
        _require_type(
            type(self.transfer_responses) is tuple
            and all(type(item) is MappingProxyType for item in self.transfer_responses),
            "transfer_responses must be a tuple of frozen JSON objects",
        )
        _require_type(
            type(self.family_champions) is tuple
            and len(self.family_champions) == 3
            and all(type(item) is MappingProxyType for item in self.family_champions),
            "family_champions must be three frozen JSON objects",
        )


@dataclass(frozen=True, slots=True)
class ReproductionRecord:
    document: FrozenJsonObject

    def __post_init__(self) -> None:
        _require_frozen_mapping(self.document, name="reproduction document")


@dataclass(frozen=True, slots=True)
class StudyResults:
    schema_version: int
    environment: FrozenJsonObject
    protocol: FrozenJsonObject
    runs: tuple[StudyRunRecord, ...]
    natural_variation: tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject]
    workload_summaries: tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject]
    reproduction: ReproductionRecord

    def __post_init__(self) -> None:
        _require_frozen_mapping(self.environment, name="environment")
        _require_frozen_mapping(self.protocol, name="protocol")
        _require_type(
            type(self.runs) is tuple and all(type(item) is StudyRunRecord for item in self.runs),
            "runs must be a tuple of study run records",
        )
        for name, value in (
            ("natural_variation", self.natural_variation),
            ("workload_summaries", self.workload_summaries),
        ):
            _require_type(
                type(value) is tuple and len(value) == 3 and all(type(item) is MappingProxyType for item in value),
                f"{name} must be three frozen JSON objects",
            )
        _require_type(type(self.reproduction) is ReproductionRecord, "reproduction must be a reproduction record")


@dataclass(frozen=True, slots=True)
class PrerequisiteResults:
    schema_version: int
    created_utc: str
    study_id: str
    git_commit: str
    git_tree_clean: bool
    url: str
    tools: FrozenJsonObject
    images: FrozenJsonObject
    capability: FrozenJsonObject
    config_sha256: FrozenJsonObject
    commands: tuple[FrozenJsonObject, FrozenJsonObject]

    def __post_init__(self) -> None:
        for name in ("tools", "images", "capability", "config_sha256"):
            _require_frozen_mapping(getattr(self, name), name=name)
        _require_type(
            type(self.commands) is tuple
            and len(self.commands) == 2
            and all(type(item) is MappingProxyType for item in self.commands),
            "commands must be two frozen JSON objects",
        )


def validate_study_id(value: str) -> str:
    _require(
        type(value) is str and _STUDY_ID_PATTERN.fullmatch(value) is not None,
        "study ID must match [a-z0-9][a-z0-9-]{0,31}",
    )
    return value


def validate_endpoint_url(value: str) -> str:
    _require(type(value) is str, "URL must be an absolute credential-free HTTPS URL with a DNS hostname")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("URL must contain a valid DNS hostname and port") from error
    hostname = parsed.hostname
    labels = () if hostname is None else tuple(hostname.rstrip(".").split("."))
    valid_hostname = (
        bool(labels)
        and all(_DNS_LABEL_PATTERN.fullmatch(label) is not None for label in labels)
        and not all(character.isdigit() or character == "." for character in hostname or "")
    )
    _require(
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and valid_hostname
        and (port is None or 1 <= port <= 65535),
        "URL must be absolute credential-free HTTPS with a DNS hostname and no query or fragment",
    )
    return value


def _exact_object(value: object, keys: Sequence[str], *, name: str) -> dict[str, object]:
    _require(type(value) is dict, f"{name} must be a JSON object with exact keys")
    document = cast(dict[object, object], value)
    _require(all(type(key) is str for key in document), f"{name} must have string keys")
    result = cast(dict[str, object], document)
    _require(
        set(result) == set(keys) and len(result) == len(keys),
        f"{name} must contain exact keys: {', '.join(keys)}",
    )
    return result


def _strict_int(value: object, *, name: str, minimum: int | None = None) -> int:
    _require(type(value) is int, f"{name} must be an exact integer")
    result = cast(int, value)
    _require(minimum is None or result >= minimum, f"{name} must be an integer at least {minimum}")
    return result


def _strict_float(
    value: object,
    *,
    name: str,
    lower: float | None = None,
    upper: float | None = None,
) -> float:
    _require(type(value) is float, f"{name} must be an exact finite float")
    result = cast(float, value)
    _require(math.isfinite(result), f"{name} must be an exact finite float")
    _require(
        (lower is None or result >= lower) and (upper is None or result <= upper),
        f"{name} must be a float in [{lower}, {upper}]",
    )
    return result


def _strict_bool(value: object, *, name: str) -> bool:
    _require(type(value) is bool, f"{name} must be an exact boolean")
    return cast(bool, value)


def _strict_string(value: object, *, name: str, nonempty: bool = True) -> str:
    qualifier = "nonempty " if nonempty else ""
    _require(type(value) is str and (not nonempty or bool(value)), f"{name} must be a {qualifier}string")
    return cast(str, value)


def _sha256(value: object, *, name: str) -> str:
    result = _strict_string(value, name=name)
    _require(
        _SHA256_PATTERN.fullmatch(result) is not None,
        f"{name} must be a 64-character lowercase SHA-256",
    )
    return result


def _utc_timestamp(value: object, *, name: str) -> str:
    result = _strict_string(value, name=name)
    _require(
        _UTC_PATTERN.fullmatch(result) is not None,
        f"{name} must be a UTC RFC 3339 timestamp ending in Z",
    )
    try:
        parsed = datetime.fromisoformat(f"{result[:-1]}+00:00")
    except ValueError as error:
        raise ValueError(f"{name} must be a valid UTC RFC 3339 timestamp ending in Z") from error
    _require(parsed.tzinfo == UTC, f"{name} must be a UTC RFC 3339 timestamp ending in Z")
    return result


def _repository_relative_path(value: object, *, repository_root: Path, name: str) -> str:
    result = _strict_string(value, name=name)
    parts = result.split("/")
    pure = PurePosixPath(result)
    _require(
        "\\" not in result
        and not pure.is_absolute()
        and all(part not in {"", ".", ".."} for part in parts)
        and pure.as_posix() == result,
        f"{name} must be a normalized repository-relative POSIX path",
    )
    root = repository_root.resolve()
    resolved = (root / Path(*parts)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{name} must resolve as a repository-relative path beneath the repository") from error
    return result


def _freeze_json(value: JsonValue) -> FrozenJsonValue:
    if type(value) is dict:
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    if type(value) in {str, int, float, bool}:
        return cast(JsonScalar, value)
    raise TypeError("JSON value must contain only exact JSON scalar and collection types")


def _freeze_object(value: JsonObject) -> FrozenJsonObject:
    return cast(FrozenJsonObject, _freeze_json(value))


def _thaw_json(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw_json(item) for item in value]
    return cast(JsonScalar, value)


def _canonical_json(document: JsonObject) -> bytes:
    try:
        rendered = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"could not render canonical JSON: {error}") from error
    return f"{rendered}\n".encode()


def _load_json(content: bytes) -> JsonObject:
    def duplicate_free_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(token: str) -> object:
        raise ValueError(f"invalid JSON constant: {token}")

    try:
        loaded = json.loads(
            content.decode("utf-8"), object_pairs_hook=duplicate_free_object, parse_constant=invalid_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid UTF-8 JSON: {error}") from error
    if type(loaded) is not dict:
        raise ValueError("JSON root must be an object")
    return cast(JsonObject, loaded)


def _numeric_sample(values: Sequence[int | float], *, name: str) -> tuple[float, ...]:
    result: list[float] = []
    for value in values:
        if type(value) not in {int, float} or not math.isfinite(value):
            raise ValueError(f"{name} must contain only finite non-boolean numbers")
        result.append(float(value))
    _require(bool(result), f"{name} must be nonempty")
    return tuple(result)


def _median(values: Sequence[int | float]) -> float:
    ordered = sorted(_numeric_sample(values, name="median sample"))
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _nearest_rank(values: Sequence[int | float], probability: float) -> float:
    ordered = sorted(_numeric_sample(values, name="quantile sample"))
    probability = _strict_float(probability, name="quantile probability", lower=0.0, upper=1.0)
    _require(probability > 0.0, "quantile probability must be greater than zero")
    return ordered[math.ceil(probability * len(ordered)) - 1]


def _sample_record(  # pyright: ignore[reportUnusedFunction]
    values: Sequence[int | float],
    *,
    quantile_probability: float,
    zero_count: int,
) -> JsonObject:
    sample = _numeric_sample(values, name="sample")
    _require(all(value >= 0.0 for value in sample), "sample must contain nonnegative values")
    zero_count = _strict_int(zero_count, name="zero count", minimum=0)
    _require(zero_count <= len(sample), "zero count must not exceed the sample count")
    return {
        "count": len(sample),
        "minimum": min(sample),
        "median": _median(sample),
        "quantile_probability": _strict_float(quantile_probability, name="quantile probability", lower=0.0, upper=1.0),
        "quantile": _nearest_rank(sample, quantile_probability),
        "maximum": max(sample),
        "zero_count": zero_count,
    }


def descriptive_statistics(values: Sequence[int | float]) -> JsonObject:
    sample = _numeric_sample(values, name="descriptive statistics sample")
    _require(len(sample) == 3, "descriptive statistics require exactly three observations")
    sample_variance = variance(sample)
    minimum = min(sample)
    maximum = max(sample)
    return {
        "count": 3,
        "mean": fmean(sample),
        "minimum": minimum,
        "maximum": maximum,
        "range": maximum - minimum,
        "sample_variance": sample_variance,
        "sample_standard_deviation": math.sqrt(sample_variance),
    }


def _score_from_trial(trial: TrialResult) -> JsonObject:
    _require_type(type(trial) is TrialResult, "trial score source must be a TrialResult")
    _bounded_score(trial.aggregate_score, name="trial aggregate score")
    _require(
        tuple(method.name for method in trial.methods) == PUBLISHED_METHOD_ORDER,
        "trial methods must use published method order",
    )
    methods: JsonObject = {}
    for method in trial.methods:
        methods[method.name] = _bounded_score(method.score, name=f"trial {method.name} score")
    return {"aggregate": trial.aggregate_score, "methods": methods}


def _score_from_comparison(result: ComparisonResult) -> JsonObject:
    _require_type(type(result) is ComparisonResult, "published score source must be a ComparisonResult")
    _bounded_score(result.aggregate_score, name="comparison aggregate score")
    _require(
        tuple(result.methods) == PUBLISHED_METHOD_ORDER,
        "comparison methods must use published method order",
    )
    return {
        "aggregate": result.aggregate_score,
        "methods": {
            name: _bounded_score(result.methods[name].score, name=f"comparison {name} score")
            for name in PUBLISHED_METHOD_ORDER
        },
    }


def _candidate_id(identifier: CandidateId) -> JsonObject:
    _require_type(type(identifier) is CandidateId, "candidate identifier must be a CandidateId")
    _strict_int(identifier.birth_generation, name="candidate birth generation", minimum=0)
    _strict_int(identifier.birth_index, name="candidate birth index", minimum=0)
    return {
        "birth_generation": identifier.birth_generation,
        "birth_index": identifier.birth_index,
    }


def _canonical_genes(candidate: Candidate) -> list[int | float]:
    _require_type(type(candidate) is Candidate, "candidate must be a Candidate")
    genes_value = candidate.genes
    if genes_value is None:
        raise ValueError("evidence candidate must have canonical genes")
    _require(
        candidate.status == "valid" and candidate.invalid is None,
        "evidence candidate must be valid with canonical genes",
    )
    genes = list(genes_value)
    _validate_genes(genes, family=candidate.family)
    return genes


def _family_champions(state: CheckpointState) -> tuple[JsonObject, JsonObject, JsonObject]:
    _require_type(type(state) is CheckpointState, "champion source must be a CheckpointState")
    _require(state.terminal_reason != "running", "champions require a terminal checkpoint")
    _require(
        tuple(family.name for family in state.compatibility.families) == FAMILY_ORDER,
        "checkpoint must contain all three families in lexical order",
    )
    _require(
        state.compatibility.trial_seeds == (17, 29),
        "checkpoint selection seeds must be exactly [17, 29]",
    )
    records: list[JsonObject] = []
    for family in FAMILY_ORDER:
        candidates = tuple(
            candidate
            for candidate in state.population
            if candidate.family == family and candidate.status == "valid" and candidate.genes is not None
        )
        _require(bool(candidates), f"terminal checkpoint has no valid {family} family champion")
        champion = min(candidates, key=lambda item: (-item.fitness, item.identifier))
        _require(
            tuple(trial.seed for trial in champion.trials) == (17, 29),
            f"{family} champion trials must use exactly selection seeds [17, 29]",
        )
        aggregate = fmean(trial.aggregate_score for trial in champion.trials)
        _require(
            aggregate == champion.fitness,
            f"{family} champion fitness must equal its mean selection aggregate score",
        )
        method_means = {
            name: fmean(
                next(method.score for method in trial.methods if method.name == name) for trial in champion.trials
            )
            for name in PUBLISHED_METHOD_ORDER
        }
        records.append(
            cast(
                JsonObject,
                {
                    "family": family,
                    "candidate_id": _candidate_id(champion.identifier),
                    "genes": _canonical_genes(champion),
                    "selection_fitness": champion.fitness,
                    "selection_seeds": [17, 29],
                    "selection_score": {"aggregate": aggregate, "methods": method_means},
                },
            )
        )
    return cast(tuple[JsonObject, JsonObject, JsonObject], tuple(records))


def _winner(state: CheckpointState, best: BestModel) -> JsonObject:
    _require_type(type(state) is CheckpointState, "winner source must be a CheckpointState")
    _require_type(type(best) is BestModel, "published winner must be a BestModel")
    matches = tuple(candidate for candidate in state.population if candidate.identifier == state.best_identifier)
    _require(len(matches) == 1, "checkpoint best identifier must identify exactly one terminal candidate")
    candidate = matches[0]
    genes = _canonical_genes(candidate)
    _require(candidate.fitness == state.best_fitness, "checkpoint best fitness must match its identified candidate")
    _require(
        candidate.family == best.family and tuple(genes) == best.genes,
        "checkpoint winner family and genes must match the published best model",
    )
    return cast(
        JsonObject,
        {
            "family": candidate.family,
            "candidate_id": _candidate_id(candidate.identifier),
            "genes": genes,
            "selection_fitness": candidate.fitness,
        },
    )


@dataclass(frozen=True, slots=True)
class _LoadedRunEvidence:
    config: ExperimentConfig
    context: StrategyContext
    metadata: CaptureMetadata
    contents: Mapping[str, bytes]
    artifact_sha256: JsonObject
    reference: tuple[TraceEvent, ...]
    generated: tuple[TraceEvent, ...]
    checkpoint: CheckpointState
    best_model: BestModel
    comparison: ComparisonResult
    log_records: tuple[JsonObject, ...]


def _read_exact_artifact_set(run_directory: Path) -> dict[str, bytes]:
    try:
        entries = tuple(run_directory.iterdir())
        _require(
            {entry.name for entry in entries} == set(ARTIFACT_NAMES),
            "successful run directory must contain exactly the documented nine artifacts",
        )
        _require(
            all(entry.is_file() and not entry.is_symlink() for entry in entries),
            "every successful run artifact must be a regular non-symlink file",
        )
        return {name: (run_directory / name).read_bytes() for name in ARTIFACT_NAMES}
    except OSError as error:
        raise TrafficlabError(
            f"could not read complete Validation Study run evidence {run_directory}: {error}",
            corrective_action="preserve the run and inspect its exact nine artifact files",
        ) from error


def _artifact_identities(run_directory: Path) -> dict[str, FileIdentity]:
    identities: dict[str, FileIdentity] = {}
    for name in ARTIFACT_NAMES:
        identity = _file_identity(
            run_directory / name,
            kind="Validation Study evidence artifact",
            corrective_action="preserve the run and inspect its exact nine artifact files",
        )
        if identity is None:
            raise ValueError(f"Validation Study evidence artifact is missing: {name}")
        identities[name] = identity
    return identities


def _parse_run_log(content: bytes) -> tuple[JsonObject, ...]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"run.log must be valid UTF-8: {error}") from error
    _require(text.endswith("\n"), "run.log must end with one newline")
    records = tuple(_load_json(line.encode("utf-8")) for line in text.splitlines())
    _require(bool(records), "run.log must contain records")
    return records


def _load_persisted_run_evidence(spec: StudyRunSpec) -> _LoadedRunEvidence:
    identities = _artifact_identities(spec.run_directory)
    contents = _read_exact_artifact_set(spec.run_directory)
    config = load_experiment(spec.config_path)
    snapshot_path = spec.run_directory / "experiment.toml"
    snapshot_config = load_experiment(snapshot_path)
    _require(config == snapshot_config, "realized config and run snapshot must load to the same experiment")
    _require(
        contents["experiment.toml"] == render_effective_config(config),
        "experiment.toml must be the canonical effective configuration",
    )

    capture_path = spec.run_directory / "capture.json"
    reference_path = spec.run_directory / "reference.pcapng"
    inspection = validate_capture_pair(capture_path, reference_path, deadline=None)
    metadata = parse_capture_metadata(contents["capture.json"], source=capture_path)
    captured = parse_pcapng_bytes(contents["reference.pcapng"], metadata, source=reference_path)
    _require(inspection.packet_count == len(captured), "strict persisted reference packet counts must agree")
    reference, window = normalize_reference(captured)

    artifact_sha256: JsonObject = {name: sha256_bytes(contents[name]) for name in ARTIFACT_NAMES}
    context = make_strategy_context(
        config,
        reference,
        window,
        spec.run_directory,
        experiment_identity=ContentIdentity(
            size=len(contents["experiment.toml"]), sha256=cast(str, artifact_sha256["experiment.toml"])
        ),
        reference_identity=ContentIdentity(
            size=len(contents["reference.pcapng"]), sha256=cast(str, artifact_sha256["reference.pcapng"])
        ),
        capture_identity=ContentIdentity(
            size=len(contents["capture.json"]), sha256=cast(str, artifact_sha256["capture.json"])
        ),
    )
    checkpoint = parse_checkpoint(contents["checkpoint.json"], context.compatibility)
    _require(
        contents["ga_history.csv"] == render_history_csv(checkpoint),
        "ga_history.csv must be the exact terminal checkpoint history projection",
    )
    best_path = spec.run_directory / "best_model.json"
    best_model = load_best_model(contents["best_model.json"], source=best_path)
    _require(
        contents["best_model.json"] == render_best_model(best_model),
        "best_model.json must use its canonical production encoding",
    )
    generated_path = spec.run_directory / "generated.pcapng"
    generated = parse_pcapng_bytes(contents["generated.pcapng"], metadata, source=generated_path)
    similarity = parse_comparison_result(contents["similarity.json"])
    _require(
        contents["similarity.json"] == render_comparison_result(similarity),
        "similarity.json must use its canonical production encoding",
    )
    _require(
        _read_exact_artifact_set(spec.run_directory) == contents
        and _artifact_identities(spec.run_directory) == identities,
        "run artifacts must retain exact identities throughout evidence extraction",
    )
    return _LoadedRunEvidence(
        config,
        context,
        metadata,
        MappingProxyType(contents),
        artifact_sha256,
        reference,
        generated,
        checkpoint,
        best_model,
        similarity,
        _parse_run_log(contents["run.log"]),
    )


def _load_run_evidence(spec: StudyRunSpec, result: RunResult) -> _LoadedRunEvidence:
    _require_type(type(result) is RunResult, "run evidence source must be an exact RunResult")
    _require(
        result.experiment_path == spec.config_path and result.run_directory == spec.run_directory,
        "run result paths must equal the selected study run",
    )
    prepared = open_or_prepare_experiment(spec.config_path)
    _require(
        prepared.run_directory == spec.run_directory,
        "effective run directory must equal the selected study run directory",
    )
    _validate_final_artifacts(
        prepared,
        result.capture,
        result.fit,
        result.generation,
        result.comparison,
    )
    evidence = _load_persisted_run_evidence(spec)
    _require(
        len(evidence.reference) == result.capture.packet_count,
        "capture result and strict persisted reference packet counts must agree",
    )
    _require(
        evidence.context.evaluation.window == result.fit.observation_window_seconds,
        "strict reference window must equal fitting evidence",
    )
    return evidence


def _direction_values(events: Sequence[TraceEvent], *, bytes_: bool) -> JsonObject:
    return {
        "outbound": sum(
            event.frame_length if bytes_ else 1 for event in events if event.direction is Direction.OUTBOUND
        ),
        "inbound": sum(event.frame_length if bytes_ else 1 for event in events if event.direction is Direction.INBOUND),
    }


def _trace_summary(
    events: Sequence[TraceEvent],
    result: ComparisonResult,
    *,
    role: Literal["reference", "generated"],
) -> JsonObject:
    trace = tuple(events)
    _require_type(
        bool(trace) and all(type(event) is TraceEvent for event in trace),
        "trace summary requires canonical TraceEvent values",
    )
    _require(len(trace) >= 2, "trace summary requires at least two events")
    frame_lengths = tuple(event.frame_length for event in trace)
    iats = tuple(current.timestamp - previous.timestamp for previous, current in zip(trace, trace[1:], strict=False))
    packet_totals = _direction_values(trace, bytes_=False)
    byte_totals = _direction_values(trace, bytes_=True)
    multiscale = result.methods["multiscale_rate"].diagnostics
    scale_values = multiscale.get("scales")
    _require_type(type(scale_values) is tuple, "multiscale diagnostics scales must be a tuple")
    scales: list[JsonValue] = []
    for value in cast(tuple[object, ...], scale_values):
        _require_type(isinstance(value, Mapping), "multiscale scale diagnostics must be a mapping")
        scale = cast(Mapping[str, object], value)
        totals_value = scale.get(f"{role}_totals")
        _require_type(isinstance(totals_value, Mapping), "multiscale direction totals must be a mapping")
        totals = cast(Mapping[str, object], totals_value)
        scale_packets = cast(JsonObject, _thaw_json(cast(FrozenJsonValue, totals["packet"])))
        scale_bytes = cast(JsonObject, _thaw_json(cast(FrozenJsonValue, totals["byte"])))
        _require(
            scale_packets == packet_totals and scale_bytes == byte_totals,
            f"{role} multiscale direction totals must equal the canonical trace",
        )
        scales.append(
            {
                "width_seconds": cast(float, scale["width_seconds"]),
                "bins_per_direction": cast(int, scale["bins_per_direction"]),
                "packet_totals": scale_packets,
                "byte_totals": scale_bytes,
            }
        )
    return {
        "packet_count": len(trace),
        "observation_window_seconds": result.observation_window_seconds,
        "packet_totals": packet_totals,
        "byte_totals": byte_totals,
        "frame_lengths": _sample_record(frame_lengths, quantile_probability=0.95, zero_count=0),
        "iats": _sample_record(iats, quantile_probability=0.95, zero_count=iats.count(0.0)),
        "scales": scales,
    }


def _comparison_equals_trial(comparison: ComparisonResult, trial: TrialResult) -> bool:
    return (
        comparison.aggregate_score == trial.aggregate_score
        and tuple(comparison.methods) == tuple(method.name for method in trial.methods)
        and all(
            comparison.methods[method.name].score == method.score
            and comparison.methods[method.name].diagnostics == method.diagnostics
            for method in trial.methods
        )
    )


def _fresh_run_log_proofs(records: Sequence[JsonObject]) -> None:
    capture_records = tuple(record for record in records if record.get("event") == "capture_published")
    best_model_records = tuple(record for record in records if record.get("event") == "best_model_published")
    generated_records = tuple(record for record in records if record.get("event") == "generated_pcapng_published")
    comparison_records = tuple(record for record in records if record.get("event") == "comparison_succeeded")
    completed = tuple(record for record in records if record.get("event") == "run_completed")
    _require(
        len(capture_records) == 1
        and capture_records[0].get("stage") == "capture"
        and capture_records[0].get("reused") is False,
        "fresh run must contain one successful non-reused capture publication",
    )
    _require(
        not any(str(record.get("event", "")).endswith("_reused") for record in records),
        "fresh run log must not contain a reused-stage event",
    )
    _require(
        len(best_model_records) == 1 and len(generated_records) == 1,
        "fresh run must publish one new best model and one new generated PCAPNG",
    )
    _require(
        len(comparison_records) == 1 and comparison_records[0].get("reused") is False,
        "fresh run must contain one successful non-reused comparison publication",
    )
    _require(
        len(completed) == 1 and records[-1] == completed[0],
        "fresh run must end with exactly one run_completed record",
    )


def _sole_final_trial(trials: Sequence[TrialResult]) -> TrialResult:
    values = cast(tuple[object, ...], trials)
    _require(
        type(trials) is tuple and len(values) == 1 and type(values[0]) is TrialResult,
        "fresh simulation evaluation must return exactly one TrialResult",
    )
    trial = cast(TrialResult, values[0])
    _require(trial.seed == 97, "fresh simulation evaluation must use exact final seed 97")
    return trial


def _require_published_lineage(
    rebuilt: ComparisonResult,
    persisted: ComparisonResult,
    artifact_contents: Mapping[str, bytes],
    settings_identity: ContentIdentity,
) -> None:
    input_identities = rebuilt.input_identities
    _require(input_identities is not None, "published comparison must carry exact input lineage")
    expected = {
        "capture_json": identify_bytes(artifact_contents["capture.json"]),
        "reference_pcapng": identify_bytes(artifact_contents["reference.pcapng"]),
        "generated_pcapng": identify_bytes(artifact_contents["generated.pcapng"]),
        "similarity_settings": settings_identity,
    }
    _require(input_identities == expected, "published comparison input lineage must match exact artifact identities")
    _require(rebuilt == persisted, "published comparison must equal strict persisted similarity evidence")


@dataclass(frozen=True, slots=True)
class _ReconstructedScience:
    fresh_simulation: TrialResult
    raw_events: tuple[TraceEvent, ...]
    reparsed_events: tuple[TraceEvent, ...]
    aligned_events: tuple[TraceEvent, ...]
    published: ComparisonResult


def _reconstruct_science(
    evidence: _LoadedRunEvidence,
    fresh_simulation: TrialResult,
    *,
    generated_path: Path,
) -> _ReconstructedScience:
    window = evidence.best_model.observation_window_seconds
    family = get_family(evidence.best_model.family)
    raw_trial = family.generate(
        evidence.best_model.fitted, 97, window, evidence.config.generation.trial
    ).require_complete()
    raw_final = family.generate(
        evidence.best_model.fitted, 97, window, evidence.config.generation.final
    ).require_complete()
    _require(raw_trial == raw_final, "trial and final guards must produce one exact raw seed-97 sequence")
    raw_comparison = compare_traces(evidence.reference, raw_trial, window, evidence.config.similarity)
    _require(
        _comparison_equals_trial(raw_comparison, fresh_simulation),
        "raw seed-97 comparison must equal the sole direct fresh simulation evaluation",
    )
    quantized = quantize_generated_events(raw_trial, window)
    rendered = encode_pcapng(quantized, evidence.metadata)
    reparsed = parse_pcapng_bytes(rendered, evidence.metadata, source=generated_path)
    _require(
        rendered == evidence.contents["generated.pcapng"] and reparsed == quantized == evidence.generated,
        "generated artifact must equal quantized and reparsed raw seed-97 events",
    )
    aligned = align_generated(reparsed, window)
    settings_identity = similarity_settings_identity(evidence.config.similarity)
    published = compare_traces(evidence.reference, aligned, window, evidence.config.similarity).with_input_identities(
        {
            "capture_json": identify_bytes(evidence.contents["capture.json"]),
            "reference_pcapng": identify_bytes(evidence.contents["reference.pcapng"]),
            "generated_pcapng": identify_bytes(evidence.contents["generated.pcapng"]),
            "similarity_settings": settings_identity,
        }
    )
    _require_published_lineage(published, evidence.comparison, evidence.contents, settings_identity)
    return _ReconstructedScience(fresh_simulation, raw_trial, reparsed, aligned, published)


def _repository_path_record(path: Path, *, repository_root: Path, name: str) -> str:
    root = repository_root.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError(f"{name} must remain beneath the repository root") from error
    return _repository_relative_path(relative, repository_root=root, name=name)


def _transfer_object_size(responses: Sequence[JsonObject]) -> int:
    sizes: set[int] = set()
    for response in responses:
        value = _strict_string(response.get("content_range"), name="transfer content range")
        match = re.fullmatch(r"bytes \d+-\d+/(\d+)", value)
        if match is None:
            raise ValueError("transfer content range must contain an exact object size")
        sizes.add(int(match.group(1)))
    _require(len(sizes) == 1, "all transfer responses must name one object size")
    return next(iter(sizes))


def _validate_transfer_archives(
    repository_root: Path,
    responses: Sequence[JsonObject],
    *,
    workload: WorkloadName,
    evidence_directory: str,
) -> int:
    object_size = _transfer_object_size(responses)
    _validate_transfer_responses(
        list(responses),
        repository_root=repository_root,
        workload=workload,
        evidence_directory=evidence_directory,
        object_size=object_size,
    )
    for response in responses:
        relative = cast(str, response["header_archive_path"])
        archive = repository_root / Path(*relative.split("/"))
        try:
            metadata = archive.lstat()
            content = archive.read_bytes()
        except OSError as error:
            raise ValueError(f"could not validate transfer header archive {relative}: {error}") from error
        _require(stat.S_ISREG(metadata.st_mode), "transfer header archive must be a regular file")
        _require(stat.S_IMODE(metadata.st_mode) == 0o600, "transfer header archive must retain mode 0600")
        _require(
            sha256_bytes(content) == response["header_sha256"],
            "transfer header archive must match its recorded SHA-256",
        )
    return object_size


def extract_primary_record(
    repository_root: Path,
    spec: StudyRunSpec,
    workload: WorkloadSpec,
    result: RunResult,
    elapsed_seconds: float,
    transfer_responses: tuple[JsonObject, ...],
) -> StudyRunRecord:
    root = repository_root.resolve()
    _require(
        1 <= spec.execution_order <= len(PRIMARY_ORDER)
        and PRIMARY_ORDER[spec.execution_order - 1] == (spec.execution_order, spec.run_id, spec.workload, spec.repeat),
        "primary extraction spec must equal one exact balanced-order entry",
    )
    _require(spec.workload == workload.name, "primary workload must match its selected run spec")
    elapsed = _strict_float(elapsed_seconds, name="primary run elapsed seconds", lower=0.0)
    _require(elapsed > 0.0, "primary run elapsed seconds must be positive")
    evidence = _load_run_evidence(spec, result)
    _require(
        evidence.config.target.argv == workload.argv
        and evidence.config.similarity.multiscale_widths_seconds == workload.multiscale_widths_seconds,
        "primary run config must equal its exact workload profile",
    )
    _require(
        not result.capture.reused and not result.fit.reused_best_model and not result.generation.reused,
        "primary run capture, best model, and generated output must all be fresh",
    )
    _fresh_run_log_proofs(evidence.log_records)

    validate_evaluation_context(evidence.context.evaluation)
    fresh_simulation_trial = _sole_final_trial(result.fit.outcome.final_trials)
    science = _reconstruct_science(
        evidence, fresh_simulation_trial, generated_path=spec.run_directory / "generated.pcapng"
    )
    _require(
        science.reparsed_events == result.generation.events and science.published == result.comparison,
        "run result must equal the reconstructed generated artifact and published comparison",
    )

    config_path = _repository_path_record(spec.config_path, repository_root=root, name="primary config path")
    run_directory = _repository_path_record(spec.run_directory, repository_root=root, name="primary run directory")
    evidence_directory = _repository_path_record(
        spec.transfer_evidence_directory,
        repository_root=root,
        name="primary transfer evidence directory",
    )
    object_size = _validate_transfer_archives(
        root,
        transfer_responses,
        workload=spec.workload,
        evidence_directory=evidence_directory,
    )
    document: JsonObject = {
        "execution_order": spec.execution_order,
        "run_id": spec.run_id,
        "key": {"workload": spec.workload, "repeat": spec.repeat},
        "config_path": config_path,
        "run_directory": run_directory,
        "transfer_evidence_directory": evidence_directory,
        "elapsed_seconds": elapsed,
        "reuse": {"capture": False, "best_model": False, "generated": False, "similarity": False},
        "cleanup_verified": True,
        "transfer_responses": list(transfer_responses),
        "artifact_sha256": evidence.artifact_sha256,
        "reference": _trace_summary(evidence.reference, science.published, role="reference"),
        "generated": _trace_summary(science.aligned_events, science.published, role="generated"),
        "family_champions": list(_family_champions(evidence.checkpoint)),
        "winner": _winner(evidence.checkpoint, evidence.best_model),
        "fresh_simulation": {
            "seed": 97,
            "score": _score_from_trial(science.fresh_simulation),
            "source": "run_experiment_fit_outcome",
        },
        "published": {"seed": 97, "score": _score_from_comparison(science.published)},
        "raw_sequence": {
            "seed": 97,
            "observation_window_seconds": evidence.best_model.observation_window_seconds,
            "trial_event_count": len(science.raw_events),
            "final_event_count": len(science.raw_events),
            "raw_events_equal": True,
            "fresh_simulation_score_reproduced": True,
            "reparsed_event_count": len(science.reparsed_events),
            "reparsed_matches_quantized": True,
        },
    }
    _validate_run_evidence(
        document,
        repository_root=root,
        workload=spec.workload,
        evidence_directory=evidence_directory,
        object_size=object_size,
        fresh_simulation_source="run_experiment_fit_outcome",
    )
    return _run_record_from_document(document)


_WORKLOAD_ORDER: tuple[WorkloadName, WorkloadName, WorkloadName] = ("short", "streaming", "bursty")


def _group_run_documents(records: Sequence[StudyRunRecord]) -> dict[WorkloadName, tuple[JsonObject, ...]]:
    _require(
        len(records) == len(PRIMARY_ORDER) and all(type(record) is StudyRunRecord for record in records),
        "study summaries require exactly nine primary run records",
    )
    grouped: dict[WorkloadName, dict[int, JsonObject]] = {workload: {} for workload in _WORKLOAD_ORDER}
    for record in records:
        document = _study_run_document(record)
        key = cast(JsonObject, document["key"])
        workload_value = _strict_string(key.get("workload"), name="primary summary workload")
        repeat_value = _strict_int(key.get("repeat"), name="primary summary repeat", minimum=1)
        _require(
            workload_value in _WORKLOAD_ORDER and repeat_value <= 3,
            "primary summary keys must contain one exact workload and repeat 1..3",
        )
        workload = cast(WorkloadName, workload_value)
        _require(repeat_value not in grouped[workload], "primary summary keys must be unique")
        grouped[workload][repeat_value] = document
    for workload in _WORKLOAD_ORDER:
        _require(set(grouped[workload]) == {1, 2, 3}, f"{workload} summaries require repeats 1, 2, and 3")
    return {workload: tuple(grouped[workload][repeat] for repeat in (1, 2, 3)) for workload in _WORKLOAD_ORDER}


def _reference_descriptions(runs: Sequence[JsonObject]) -> JsonObject:
    observations = _descriptor_observations(runs)
    return {key: descriptive_statistics(observations[key]) for key in _DESCRIPTOR_KEYS}


def natural_variation(
    records: Sequence[StudyRunRecord],
    traces: Mapping[tuple[WorkloadName, int], tuple[TraceEvent, ...]],
    settings: Mapping[WorkloadName, SimilarityConfig],
) -> tuple[JsonObject, JsonObject, JsonObject]:
    grouped = _group_run_documents(records)
    expected_trace_keys = {(workload, repeat) for workload in _WORKLOAD_ORDER for repeat in (1, 2, 3)}
    _require(set(traces) == expected_trace_keys, "natural variation requires exactly nine primary reference traces")
    _require(set(settings) == set(_WORKLOAD_ORDER), "natural variation requires exact per-workload settings")
    results: list[JsonObject] = []
    for workload in _WORKLOAD_ORDER:
        pairs: list[JsonValue] = []
        for left_repeat, right_repeat in ((1, 2), (1, 3), (2, 3)):
            directional: list[JsonObject] = []
            for reference_repeat, generated_repeat in (
                (left_repeat, right_repeat),
                (right_repeat, left_repeat),
            ):
                reference, window = normalize_reference(traces[(workload, reference_repeat)])
                generated = align_generated(traces[(workload, generated_repeat)], window)
                directional.append(
                    _score_from_comparison(compare_traces(reference, generated, window, settings[workload]))
                )
            forward, reverse = directional
            pairs.append(
                {
                    "left_repeat": left_repeat,
                    "right_repeat": right_repeat,
                    "forward": forward,
                    "reverse": reverse,
                    "symmetric": _average_score(forward, reverse),
                }
            )
        results.append(
            {
                "workload": workload,
                "pairs": pairs,
                "reference_descriptors": _reference_descriptions(grouped[workload]),
            }
        )
    return cast(tuple[JsonObject, JsonObject, JsonObject], tuple(results))


def _summarize_scores(scores: Sequence[JsonObject]) -> JsonObject:
    _require(len(scores) == 3, "score summaries require exactly three observations")
    methods = [cast(JsonObject, score["methods"]) for score in scores]
    return {
        "aggregate": descriptive_statistics([cast(float, score["aggregate"]) for score in scores]),
        "methods": {
            method: descriptive_statistics([cast(float, values[method]) for values in methods])
            for method in PUBLISHED_METHOD_ORDER
        },
    }


def workload_summaries(
    records: Sequence[StudyRunRecord],
) -> tuple[JsonObject, JsonObject, JsonObject]:
    grouped = _group_run_documents(records)
    results: list[JsonObject] = []
    for workload in _WORKLOAD_ORDER:
        runs = grouped[workload]
        champions_by_family: dict[FamilyName, list[JsonObject]] = {family: [] for family in FAMILY_ORDER}
        for run in runs:
            champions = cast(list[JsonValue], run["family_champions"])
            for family, champion in zip(FAMILY_ORDER, champions, strict=True):
                champion_document = cast(JsonObject, champion)
                _require(champion_document.get("family") == family, "family champions must retain lexical order")
                champions_by_family[family].append(champion_document)
        family_summaries: JsonObject = {}
        for family in FAMILY_ORDER:
            champions = champions_by_family[family]
            selection_scores = [cast(JsonObject, champion["selection_score"]) for champion in champions]
            method_scores = [cast(JsonObject, score["methods"]) for score in selection_scores]
            family_summaries[family] = {
                "selection_fitness": descriptive_statistics(
                    [cast(float, champion["selection_fitness"]) for champion in champions]
                ),
                "selection_components": {
                    method: descriptive_statistics([cast(float, scores[method]) for scores in method_scores])
                    for method in PUBLISHED_METHOD_ORDER
                },
            }
        winners = [cast(JsonObject, run["winner"]) for run in runs]
        fresh_simulation = [cast(JsonObject, cast(JsonObject, run["fresh_simulation"])["score"]) for run in runs]
        published = [cast(JsonObject, cast(JsonObject, run["published"])["score"]) for run in runs]
        results.append(
            {
                "workload": workload,
                "runtime": descriptive_statistics([cast(float, run["elapsed_seconds"]) for run in runs]),
                "family_champions": family_summaries,
                "winner_selection_fitness": descriptive_statistics(
                    [cast(float, winner["selection_fitness"]) for winner in winners]
                ),
                "fresh_simulation": _summarize_scores(fresh_simulation),
                "published": _summarize_scores(published),
                "reference_descriptors": _reference_descriptions(runs),
                "winner_counts": {
                    family: sum(winner["family"] == family for winner in winners) for family in FAMILY_ORDER
                },
            }
        )
    return cast(tuple[JsonObject, JsonObject, JsonObject], tuple(results))


def _strict_list(value: object, *, name: str) -> list[object]:
    _require(type(value) is list, f"{name} must be a JSON array")
    return cast(list[object], value)


def _string_array(value: object, *, name: str, nonempty: bool = False) -> tuple[str, ...]:
    items = _strict_list(value, name=name)
    _require(not nonempty or bool(items), f"{name} must be a nonempty string array")
    return tuple(_strict_string(item, name=f"{name} item") for item in items)


def _timestamp_value(value: str) -> datetime:
    return datetime.fromisoformat(f"{value[:-1]}+00:00")


def _require_timestamp_order(started: str, completed: str, *, name: str) -> None:
    _require(
        _timestamp_value(completed) >= _timestamp_value(started),
        f"{name} completion timestamp must not precede its start",
    )


def _image_id(value: object, *, name: str) -> str:
    result = _strict_string(value, name=name)
    _require(re.fullmatch(r"sha256:[0-9a-f]{64}", result) is not None, f"{name} must be an exact sha256 image ID")
    return result


def _container_id(value: object, *, name: str) -> str:
    result = _strict_string(value, name=name)
    _require(re.fullmatch(r"[0-9a-f]{64}", result) is not None, f"{name} must be a full lowercase container ID")
    return result


def _git_commit(value: object) -> str:
    result = _strict_string(value, name="Git commit")
    _require(
        re.fullmatch(r"[0-9a-f]{40}", result) is not None,
        "Git commit must be 40 lowercase hexadecimal characters",
    )
    return result


def _profile_hashes(value: object) -> JsonObject:
    document = _exact_object(value, ("short", "streaming", "bursty"), name="profile hash map")
    for key in ("short", "streaming", "bursty"):
        _sha256(document[key], name=f"profile hash {key}")
    return cast(JsonObject, document)


def _validate_tools(value: object) -> JsonObject:
    keys = (
        "python_version",
        "trafficlab_version",
        "docker_engine_version",
        "docker_compose_version",
        "platform",
    )
    document = _exact_object(value, keys, name="tools")
    for key in keys:
        _strict_string(document[key], name=f"tools.{key}")
    _require(document["python_version"] == "3.12.3", "tools.python_version must be exactly 3.12.3")
    _require(
        document["trafficlab_version"] == __version__,
        f"tools.trafficlab_version must be exactly {__version__}",
    )
    return cast(JsonObject, document)


def _validate_images(value: object) -> JsonObject:
    document = _exact_object(value, IMAGE_KEYS, name="images")
    target_reference = _strict_string(document["target_reference"], name="target reference")
    _require(
        target_reference == TARGET_REFERENCE,
        "target reference must be the approved digest-pinned curl image",
    )
    repo_digests = _string_array(document["target_repo_digests"], name="target repository digests", nonempty=True)
    _require(
        repo_digests == tuple(sorted(repo_digests)) and TARGET_REFERENCE in repo_digests,
        "target repository digests must be sorted and include the approved target reference",
    )
    _image_id(document["target_image_id"], name="target image ID")
    _strict_string(document["target_config_user"], name="target configured user", nonempty=False)
    _image_id(document["capture_image_id"], name="capture image ID")
    _sha256(document["capture_dockerfile_sha256"], name="capture Dockerfile SHA-256")
    _sha256(document["capture_script_sha256"], name="capture script SHA-256")
    return cast(JsonObject, document)


def _validate_test_counts(value: object) -> JsonObject:
    keys = ("total", "passed", "failed", "errors", "skipped")
    document = _exact_object(value, keys, name="test counts")
    counts = {key: _strict_int(document[key], name=f"test counts.{key}", minimum=0) for key in keys}
    _require(counts["total"] > 0, "test counts.total must be positive")
    for key in ("failed", "errors", "skipped"):
        _require(counts[key] == 0, f"test counts.{key} must be zero")
    _require(counts["passed"] == counts["total"], "test counts.passed must equal total")
    return cast(JsonObject, document)


def _guard_prefix(wall_time: str) -> tuple[str, ...]:
    return (
        "scripts/run_bounded.sh",
        "--memory-high",
        "2G",
        "--memory-max",
        "3G",
        "--swap-max",
        "512M",
        "--wall-time",
        wall_time,
        "--kill-after",
        "10s",
        "--",
    )


def _docker_matrix_argv(study_id: str) -> tuple[str, ...]:
    return _expected_prerequisite_command(
        "docker_matrix",
        study_id=validate_study_id(study_id),
        url=_ORACLE_URL,
    )


def _internet_smoke_argv(study_id: str, url: str) -> tuple[str, ...]:
    return _expected_prerequisite_command(
        "internet_smoke",
        study_id=validate_study_id(study_id),
        url=validate_endpoint_url(url),
    )


def _command_study_id(argv: Sequence[str]) -> str:
    _require(bool(argv), "prerequisite command argv must be nonempty")
    junit_path = PurePosixPath(argv[-1])
    parts = junit_path.parts
    _require(
        len(parts) == 7
        and parts[:4] == ("examples", "validation_study", ".study-work", "evidence")
        and parts[5] == "00-prerequisites",
        "prerequisite command must use its exact repository-relative JUnit path",
    )
    return validate_study_id(parts[4])


def _live_argv(
    kind: PrerequisiteCommandKind,
    argv: Sequence[str],
    *,
    repository_root: Path,
) -> tuple[str, ...]:
    checked = tuple(argv)
    study_id = _command_study_id(checked)
    if kind == "docker_matrix":
        expected = _docker_matrix_argv(study_id)
    else:
        if len(checked) < 4 or checked[-4] != "--internet-url":
            raise ValueError("internet argv must contain its exact URL")
        expected = _internet_smoke_argv(study_id, checked[-3])
    _require(checked == expected, f"{kind} argv must equal the exact guarded study command")
    if not checked:
        raise ValueError("prerequisite command argv must be nonempty")
    return (*checked[:-1], str(repository_root.resolve() / Path(*PurePosixPath(checked[-1]).parts)))


def _project_command_argv(
    kind: PrerequisiteCommandKind,
    argv: Sequence[str],
    *,
    repository_root: Path,
) -> tuple[str, ...]:
    live = tuple(argv)
    _require(bool(live), "prerequisite command argv must be nonempty")
    root = repository_root.resolve()
    junit_path = Path(live[-1])
    _require(junit_path.is_absolute(), "live prerequisite JUnit path must be absolute")
    try:
        relative = junit_path.resolve().relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError("live prerequisite JUnit path must resolve beneath the repository") from error
    projected = (*live[:-1], relative)
    _require(
        _live_argv(kind, projected, repository_root=root) == live,
        f"{kind} live argv may resolve only its exact JUnit operand",
    )
    return projected


def prerequisite_junit_counts(content: bytes) -> JsonObject:
    """Parse one retained pytest JUnit document into its strict all-passed counts."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError(f"JUnit evidence must be valid XML: {error}") from error
    if root.tag == "testsuite":
        suites = (root,)
    elif root.tag == "testsuites":
        suites = tuple(child for child in root if child.tag == "testsuite")
        _require(bool(suites), "JUnit evidence must contain at least one pytest test suite")
    else:
        raise ValueError("JUnit evidence root must be testsuite or testsuites")

    def count(suite: ET.Element[str], name: str) -> int:
        raw = suite.get(name)
        if raw is None or re.fullmatch(r"[0-9]+", raw) is None:
            raise ValueError(f"JUnit {name} must be an integer")
        return int(raw)

    total = sum(count(suite, "tests") for suite in suites)
    failed = sum(count(suite, "failures") for suite in suites)
    errors = sum(count(suite, "errors") for suite in suites)
    skipped = sum(count(suite, "skipped") for suite in suites)
    counts: JsonObject = {
        "total": total,
        "passed": total - failed - errors - skipped,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
    }
    return _validate_test_counts(counts)


def _parse_junit_counts(content: bytes) -> JsonObject:
    """Backward-compatible private spelling used by the live prerequisite runner."""
    return prerequisite_junit_counts(content)


def _timestamp_now(utc_now: Callable[[], datetime]) -> str:
    value = utc_now()
    _require(value.tzinfo is not None, "prerequisite clock must return a timezone-aware UTC datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _completed_output(completed: subprocess.CompletedProcess[bytes], *, operation: str) -> tuple[bytes, bytes]:
    _require_type(type(completed.stdout) is bytes, f"{operation} stdout must be bytes")
    _require_type(type(completed.stderr) is bytes, f"{operation} stderr must be bytes")
    return completed.stdout, completed.stderr


def _command_detail(completed: subprocess.CompletedProcess[bytes], *, operation: str) -> str:
    stdout, stderr = _completed_output(completed, operation=operation)
    detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
    return detail or "no command output"


def _private_bytes(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(content)
        path.chmod(0o600)
        _require(path.read_bytes() == content, f"retained evidence {path.name} must preserve exact bytes")
        _require(stat.S_IMODE(path.lstat().st_mode) == 0o600, f"retained evidence {path.name} must use mode 0600")
    except OSError as error:
        raise ValueError(f"could not retain prerequisite evidence {path}: {error}") from error


def _best_effort_preserve_capability_canary(evidence_directory: Path, canary: Path) -> None:
    archive = evidence_directory / "capability.headers"
    if _path_entry_exists(archive) or not evidence_directory.is_dir():
        return
    try:
        metadata = canary.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return
        with archive.open("xb") as stream:
            stream.write(canary.read_bytes())
        archive.chmod(0o600)
    except OSError:
        return


def _stdout_text(completed: subprocess.CompletedProcess[bytes], *, operation: str) -> str:
    stdout, _stderr = _completed_output(completed, operation=operation)
    try:
        return stdout.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise ValueError(f"{operation} stdout must be UTF-8") from error


def _target_image_record(content: bytes) -> JsonObject:
    try:
        loaded = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"target image inspect must return UTF-8 JSON: {error}") from error
    _require(type(loaded) is list and len(loaded) == 1, "target image inspect must return exactly one image")
    image = cast(dict[str, object], loaded[0])
    _require(type(image) is dict, "target image inspect entry must be an object")
    target_image_id = _image_id(image.get("Id"), name="target image ID")
    repo_digests = _string_array(image.get("RepoDigests"), name="target repository digests", nonempty=True)
    _require(TARGET_REFERENCE in repo_digests, "target repository digests must include the approved target reference")
    config = image.get("Config")
    _require(type(config) is dict, "target image inspect Config must be an object")
    configured_user = _strict_string(
        cast(dict[str, object], config).get("User"), name="target configured user", nonempty=False
    )
    return {
        "target_reference": TARGET_REFERENCE,
        "target_image_id": target_image_id,
        "target_repo_digests": list(sorted(repo_digests)),
        "target_config_user": configured_user,
    }


def _inspected_image_id(content: bytes, *, name: str) -> str:
    try:
        loaded = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} inspect must return UTF-8 JSON: {error}") from error
    _require(type(loaded) is list and len(loaded) == 1, f"{name} inspect must return exactly one image")
    image = loaded[0]
    _require(type(image) is dict, f"{name} inspect entry must be an object")
    return _image_id(cast(dict[str, object], image).get("Id"), name=f"{name} image ID")


def _capability_header_values(content: bytes, *, initial_url: str) -> tuple[int, int, str, str]:
    blocks = _header_blocks(content)
    _require(len(blocks) <= 4, "capability header must contain at most three redirects")
    current_url = initial_url
    for status_code, headers in blocks[:-1]:
        _require(300 <= status_code <= 399, "every capability response before the final block must be a redirect")
        current_url = validate_endpoint_url(urljoin(current_url, _singleton_header(headers, "Location")))
    status_code, final_headers = blocks[-1]
    _require(status_code == 206, "capability final response status must be exactly 206")
    content_range = _singleton_header(final_headers, "Content-Range")
    match = re.fullmatch(r"bytes 0-0/([0-9]+)", content_range)
    if match is None:
        raise ValueError("capability Content-Range must be bytes 0-0/TOTAL")
    object_size = int(match.group(1))
    _require(
        4 * 1024 * 1024 <= object_size <= 16 * 1024 * 1024,
        "capability object size must be from 4 MiB through 16 MiB",
    )
    length = _singleton_header(final_headers, "Content-Length")
    _require(length == "1", "capability Content-Length must be exactly one")
    return len(blocks) - 1, object_size, content_range, current_url


def _capability_write_out(content: bytes) -> tuple[int, int, str, int]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("capability write-out must be UTF-8") from error
    match = re.fullmatch(r"status=([0-9]+)\nsize=([0-9]+)\nurl=([^\n]+)\nredirects=([0-9]+)\n", text)
    if match is None:
        raise ValueError("capability write-out must contain the exact status, size, URL, and redirects lines")
    status = int(match.group(1))
    downloaded = int(match.group(2))
    final_url = validate_endpoint_url(match.group(3))
    redirects = int(match.group(4))
    _require(status == 206 and downloaded == 1, "capability write-out must report status 206 and size one")
    _require(0 <= redirects <= 3, "capability write-out redirects must be in 0..3")
    return status, downloaded, final_url, redirects


def _run_prerequisite_test(
    kind: PrerequisiteCommandKind,
    checked_argv: tuple[str, ...],
    *,
    repository_root: Path,
    evidence_directory: Path,
    runner: CommandRunner,
    utc_now: Callable[[], datetime],
) -> JsonObject:
    live_argv = _live_argv(kind, checked_argv, repository_root=repository_root)
    prefix = "docker" if kind == "docker_matrix" else "internet"
    timeout = SUBPROCESS_TIMEOUTS["docker_matrix_guard" if kind == "docker_matrix" else "internet_smoke_guard"]
    started = _timestamp_now(utc_now)
    try:
        completed = runner(
            live_argv,
            cwd=repository_root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"{kind} guarded pytest command failed: {error}") from error
    completed_time = _timestamp_now(utc_now)
    stdout, stderr = _completed_output(completed, operation=f"{kind} guarded pytest")
    _private_bytes(evidence_directory / f"{prefix}.stdout", stdout)
    _private_bytes(evidence_directory / f"{prefix}.stderr", stderr)
    if completed.returncode != 0:
        raise ValueError(
            f"{kind} guarded pytest failed with status {completed.returncode}: "
            f"{_command_detail(completed, operation=kind)}"
        )
    junit_path = Path(live_argv[-1])
    try:
        junit = junit_path.read_bytes()
    except OSError as error:
        raise ValueError(f"could not read {kind} JUnit evidence: {error}") from error
    junit_path.chmod(0o600)
    tests = _parse_junit_counts(junit)
    return {
        "kind": kind,
        "argv": list(_project_command_argv(kind, live_argv, repository_root=repository_root)),
        "started_utc": started,
        "completed_utc": completed_time,
        "exit_status": completed.returncode,
        "tests": tests,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "junit_sha256": hashlib.sha256(junit).hexdigest(),
    }


def _timeout_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="replace")


def _retain_failed_capability_output(evidence_directory: Path, *, stdout: bytes, stderr: bytes) -> str:
    failures: list[str] = []
    for name, content in (("capability.stdout", stdout), ("capability.stderr", stderr)):
        try:
            _private_bytes(evidence_directory / name, content)
        except (TypeError, ValueError) as error:
            failures.append(str(error))
    if failures:
        return f"evidence retention incomplete: {'; '.join(failures)}"
    return "capability stdout and stderr were retained"


def _container_listing(
    repository_root: Path,
    filter_value: str,
    *,
    runner: CommandRunner,
) -> tuple[str, ...]:
    command = ("docker", "container", "ls", "-a", "--filter", filter_value, "--format", "{{.ID}}")
    completed = runner(
        command,
        cwd=repository_root,
        check=False,
        capture_output=True,
        shell=False,
        timeout=SUBPROCESS_TIMEOUTS["container_inspect_or_remove"],
    )
    stdout, _stderr = _completed_output(completed, operation="capability container listing")
    _require(
        completed.returncode == 0,
        f"could not prove capability container absence: "
        f"{_command_detail(completed, operation='capability container listing')}",
    )
    try:
        lines = tuple(line for line in stdout.decode("utf-8").splitlines() if line)
    except UnicodeDecodeError as error:
        raise ValueError("capability container listing must be UTF-8") from error
    return lines


def _remove_owned_capability_if_present(
    *,
    repository_root: Path,
    study_id: str,
    capability_name: str,
    container_id: str,
    runner: CommandRunner,
) -> bool:
    removed_owned = False
    if _container_listing(repository_root, f"id={container_id}", runner=runner):
        inspected = runner(
            ("docker", "container", "inspect", container_id),
            cwd=repository_root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["container_inspect_or_remove"],
        )
        stdout, _stderr = _completed_output(inspected, operation="capability ownership inspection")
        _require(inspected.returncode == 0, f"capability container {container_id} could not be inspected")
        try:
            loaded = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"capability ownership inspection must return JSON: {error}") from error
        _require(type(loaded) is list and len(loaded) == 1, "capability container inspect must return one object")
        document = cast(dict[str, object], loaded[0])
        _require(type(document) is dict, "capability container inspect entry must be an object")
        config = document.get("Config")
        _require(type(config) is dict, "capability container inspect Config must be an object")
        labels = cast(dict[str, object], config).get("Labels")
        _require(type(labels) is dict, "capability container labels must be an object")
        _require(
            document.get("Id") == container_id
            and document.get("Name") == f"/{capability_name}"
            and cast(dict[str, object], labels).get("org.trafficlab.validation-study.study") == study_id,
            f"ownership could not be proved; container {container_id} may remain",
        )
        removed = runner(
            ("docker", "container", "rm", "--force", container_id),
            cwd=repository_root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["container_inspect_or_remove"],
        )
        _completed_output(removed, operation="capability removal")
        _require(removed.returncode == 0, f"owned capability container {container_id} could not be removed")
        _require(
            not _container_listing(repository_root, f"id={container_id}", runner=runner),
            f"owned capability container still exists: {container_id}",
        )
        removed_owned = True
    _require(
        not _container_listing(repository_root, f"name=^/{capability_name}$", runner=runner),
        f"capability container name still exists: {capability_name}",
    )
    return removed_owned


def _cleanup_failed_capability(
    *,
    repository_root: Path,
    study_id: str,
    capability_name: str,
    capability_cid: Path,
    runner: CommandRunner,
) -> str:
    try:
        container_id = capability_cid.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as error:
        return f"cleanup could not read the exclusive CID; container name {capability_name} may remain: {error}"
    try:
        container_id = _container_id(container_id, name="capability CID")
        capability_cid.chmod(0o600)
        removed = _remove_owned_capability_if_present(
            repository_root=repository_root,
            study_id=study_id,
            capability_name=capability_name,
            container_id=container_id,
            runner=runner,
        )
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        return f"cleanup incomplete: {error}"
    if removed:
        return f"owned capability container {container_id} was removed and its ID is absent"
    return f"capability container {container_id} is absent"


def _prepare_capability(
    *,
    repository_root: Path,
    study_id: str,
    url: str,
    evidence_directory: Path,
    mount_directory: Path,
    runner: CommandRunner,
    utc_now: Callable[[], datetime],
) -> JsonObject:
    capability_name = f"trafficlab-validation-study-capability-{study_id}"
    capability_cid = evidence_directory / "capability.cid"
    canary = mount_directory / ".capability.headers"
    _require(not _path_entry_exists(capability_cid), "capability CID path must be absent before launch")
    _require(not _path_entry_exists(canary), "capability canary path must be absent before launch")
    _require(
        not _container_listing(repository_root, f"name=^/{capability_name}$", runner=runner),
        f"capability container name already exists: {capability_name}",
    )
    descriptor = os.open(canary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666)
    os.close(descriptor)
    canary.chmod(0o666)
    inode = canary.lstat().st_ino
    checked_argv = _expected_capability_argv(study_id, url)
    live_argv = list(checked_argv)
    live_argv[8] = str(capability_cid)
    live_argv[12] = f"type=bind,src={mount_directory},dst=/trafficlab-study"
    _require("--user" not in live_argv, "capability must use the image default user")
    started = _timestamp_now(utc_now)
    try:
        completed = runner(
            tuple(live_argv),
            cwd=repository_root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["capability"],
        )
    except subprocess.TimeoutExpired as error:
        retained = _retain_failed_capability_output(
            evidence_directory,
            stdout=_timeout_bytes(error.output),
            stderr=_timeout_bytes(error.stderr),
        )
        cleanup = _cleanup_failed_capability(
            repository_root=repository_root,
            study_id=study_id,
            capability_name=capability_name,
            capability_cid=capability_cid,
            runner=runner,
        )
        raise ValueError(f"capability command timed out after 45 seconds; {cleanup}; {retained}") from error
    except OSError as error:
        cleanup = _cleanup_failed_capability(
            repository_root=repository_root,
            study_id=study_id,
            capability_name=capability_name,
            capability_cid=capability_cid,
            runner=runner,
        )
        raise ValueError(f"capability command could not start: {error}; {cleanup}") from error
    completed_time = _timestamp_now(utc_now)
    stdout, stderr = _completed_output(completed, operation="capability")
    if completed.returncode != 0:
        retained = _retain_failed_capability_output(evidence_directory, stdout=stdout, stderr=stderr)
        cleanup = _cleanup_failed_capability(
            repository_root=repository_root,
            study_id=study_id,
            capability_name=capability_name,
            capability_cid=capability_cid,
            runner=runner,
        )
        raise ValueError(
            f"capability command failed with status {completed.returncode}: "
            f"{_command_detail(completed, operation='capability')}; {cleanup}; {retained}"
        )
    _private_bytes(evidence_directory / "capability.stdout", stdout)
    _private_bytes(evidence_directory / "capability.stderr", stderr)
    try:
        container_id = capability_cid.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"could not read capability CID: {error}") from error
    container_id = _container_id(container_id, name="capability CID")
    capability_cid.chmod(0o600)
    _remove_owned_capability_if_present(
        repository_root=repository_root,
        study_id=study_id,
        capability_name=capability_name,
        container_id=container_id,
        runner=runner,
    )
    metadata = canary.lstat()
    _require(
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_ino == inode
        and stat.S_IMODE(metadata.st_mode) == 0o666,
        "capability canary must preserve its exclusive regular 0666 inode",
    )
    header_bytes = canary.read_bytes()
    _require(bool(header_bytes), "capability canary must be nonempty")
    after_read = canary.lstat()
    _require(after_read.st_ino == inode, "capability canary inode changed while reading")
    redirect_count, object_size, content_range, header_final_url = _capability_header_values(
        header_bytes, initial_url=url
    )
    status, downloaded, write_final_url, write_redirects = _capability_write_out(stdout)
    _require(
        (write_final_url, write_redirects) == (header_final_url, redirect_count),
        "capability write-out URL and redirect count must equal header evidence",
    )
    archive = evidence_directory / "capability.headers"
    _private_bytes(archive, header_bytes)
    canary.unlink()
    return {
        "argv": list(checked_argv),
        "started_utc": started,
        "completed_utc": completed_time,
        "exit_status": completed.returncode,
        "status": status,
        "content_length": 1,
        "object_size_bytes": object_size,
        "redirect_count": redirect_count,
        "body_bytes_downloaded": downloaded,
        "content_range": content_range,
        "final_url": header_final_url,
        "mount_source": f"examples/validation_study/.study-work/mount/{study_id}",
        "canary_archive_path": (
            f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites/capability.headers"
        ),
        "canary_sha256": hashlib.sha256(header_bytes).hexdigest(),
        "container_id": container_id,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "used_image_default_user": True,
        "mount_directory_mode": stat.S_IMODE(mount_directory.lstat().st_mode),
        "canary_file_mode": stat.S_IMODE(metadata.st_mode),
        "canary_archive_mode": stat.S_IMODE(archive.lstat().st_mode),
        "container_cleanup_verified": True,
    }


def _expected_prerequisite_command(kind: PrerequisiteCommandKind, *, study_id: str, url: str) -> tuple[str, ...]:
    evidence = f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites"
    pytest_prefix = ("uv", "run", "--locked", "pytest", "-vv", "-n", "0", "-m")
    if kind == "docker_matrix":
        return (*_guard_prefix("20m"), *pytest_prefix, "docker", "--junitxml", f"{evidence}/docker.xml")
    return (
        *_guard_prefix("10m"),
        *pytest_prefix,
        "internet",
        "--internet-url",
        url,
        "--junitxml",
        f"{evidence}/internet.xml",
    )


def prerequisite_command_argv(kind: str, *, study_id: str, url: str) -> tuple[str, ...]:
    """Return the one frozen, repository-relative prerequisite command for a retained study."""
    if kind not in ("docker_matrix", "internet_smoke"):
        raise ValueError("prerequisite kind must be docker_matrix or internet_smoke")
    checked_kind = kind
    return _expected_prerequisite_command(
        checked_kind,
        study_id=validate_study_id(study_id),
        url=validate_endpoint_url(url),
    )


def validate_frozen_prerequisite_command(
    kind: str,
    argv: Sequence[str],
    exit_status: object,
    tests: object,
    *,
    study_id: str,
    url: str,
) -> tuple[str, ...]:
    """Validate the command/count core shared by live and retained prerequisite evidence."""
    if kind not in ("docker_matrix", "internet_smoke"):
        raise ValueError("prerequisite kind must be docker_matrix or internet_smoke")
    checked_kind = kind
    checked_argv = tuple(_strict_string(item, name=f"{kind} argv item") for item in argv)
    _require(bool(checked_argv), f"{kind} argv must be nonempty")
    _require(
        checked_argv == prerequisite_command_argv(checked_kind, study_id=study_id, url=url),
        f"{kind} argv must equal the exact guarded study command",
    )
    _require(_strict_int(exit_status, name=f"{kind} exit status") == 0, f"{kind} exit status must be zero")
    _validate_test_counts(tests)
    return checked_argv


def _validate_command(value: object, *, expected_kind: PrerequisiteCommandKind, study_id: str, url: str) -> JsonObject:
    keys = (
        "kind",
        "argv",
        "started_utc",
        "completed_utc",
        "exit_status",
        "tests",
        "stdout_sha256",
        "stderr_sha256",
        "junit_sha256",
    )
    document = _exact_object(value, keys, name="prerequisite command")
    kind = _strict_string(document["kind"], name="prerequisite command kind")
    _require(
        kind == expected_kind,
        f"prerequisite commands must be ordered docker_matrix then internet_smoke; got {kind}",
    )
    argv = _string_array(document["argv"], name=f"{kind} argv", nonempty=True)
    started = _utc_timestamp(document["started_utc"], name=f"{kind} start")
    completed = _utc_timestamp(document["completed_utc"], name=f"{kind} completion")
    _require_timestamp_order(started, completed, name=kind)
    validate_frozen_prerequisite_command(
        kind,
        argv,
        document["exit_status"],
        document["tests"],
        study_id=study_id,
        url=url,
    )
    _sha256(document["stdout_sha256"], name=f"{kind} stdout SHA-256")
    _sha256(document["stderr_sha256"], name=f"{kind} stderr SHA-256")
    _sha256(document["junit_sha256"], name=f"{kind} JUnit SHA-256")
    return cast(JsonObject, document)


_RETAINED_PREREQUISITE_ENVIRONMENT_KEYS = (
    "capture_image_id",
    "capture_image_reference",
    "capture_tool_version",
    "source_commit",
    "source_tree",
    "target_image_id",
    "target_image_reference",
    "uv_lock_identity",
)


def _retained_identity(value: object, *, name: str) -> JsonObject:
    try:
        identity = ContentIdentity.from_dict(value, name=name)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a content identity: {error}") from error
    return cast(JsonObject, identity.as_dict())


def _retained_output(value: object, *, name: str, expected_path: str) -> JsonObject:
    document = _exact_object(value, ("identity", "path"), name=name)
    path = _strict_string(document["path"], name=f"{name} path")
    _require(path == expected_path, f"{name} path must be exactly {expected_path}")
    return {"identity": _retained_identity(document["identity"], name=name), "path": path}


def _retained_prerequisite_environment(value: object) -> JsonObject:
    document = _exact_object(value, _RETAINED_PREREQUISITE_ENVIRONMENT_KEYS, name="retained prerequisite environment")
    source_commit = _git_commit(document["source_commit"])
    source_tree = _git_commit(document["source_tree"])
    target_reference = _strict_string(document["target_image_reference"], name="retained target image reference")
    _require(
        target_reference == TARGET_REFERENCE,
        "retained target image reference must equal the approved digest-pinned target",
    )
    capture_reference = _strict_string(document["capture_image_reference"], name="retained capture image reference")
    _require(
        "@sha256:" in capture_reference
        and re.fullmatch(r"[0-9a-f]{64}", capture_reference.rsplit("@sha256:", 1)[-1]) is not None,
        "retained capture image reference must be an immutable digest reference",
    )
    return {
        "capture_image_id": _image_id(document["capture_image_id"], name="retained capture image ID"),
        "capture_image_reference": capture_reference,
        "capture_tool_version": _strict_string(document["capture_tool_version"], name="retained capture tool version"),
        "source_commit": source_commit,
        "source_tree": source_tree,
        "target_image_id": _image_id(document["target_image_id"], name="retained target image ID"),
        "target_image_reference": target_reference,
        "uv_lock_identity": _retained_identity(document["uv_lock_identity"], name="retained uv.lock"),
    }


def _retained_prerequisite_document(value: object) -> dict[str, object]:
    root = _exact_object(
        value,
        ("commands", "environment", "schema_version", "study_id", "url"),
        name="retained prerequisite evidence",
    )
    _require(
        _strict_int(root["schema_version"], name="retained prerequisite schema version") == 3,
        "retained prerequisite schema version must be exactly 3",
    )
    study_id = validate_study_id(_strict_string(root["study_id"], name="retained prerequisite study ID"))
    url = validate_endpoint_url(_strict_string(root["url"], name="retained prerequisite URL"))
    values = _strict_list(root["commands"], name="retained prerequisite commands")
    _require(len(values) == 2, "retained prerequisite commands must contain docker_matrix then internet_smoke")
    commands: list[JsonObject] = []
    for value, expected_kind in zip(values, ("docker_matrix", "internet_smoke"), strict=True):
        document = _exact_object(
            value,
            ("argv", "exit_status", "junit", "kind", "stderr", "stdout", "tests"),
            name=f"retained {expected_kind} command",
        )
        kind = _strict_string(document["kind"], name="retained prerequisite command kind")
        _require(kind == expected_kind, "retained prerequisite commands must use the fixed kind order")
        argv = _string_array(document["argv"], name=f"retained {kind} argv", nonempty=True)
        validate_frozen_prerequisite_command(
            kind,
            argv,
            document["exit_status"],
            document["tests"],
            study_id=study_id,
            url=url,
        )
        commands.append(
            {
                "argv": list(argv),
                "exit_status": 0,
                "junit": _retained_output(
                    document["junit"], name=f"retained {kind} JUnit", expected_path=f"prerequisites/{kind}.junit.xml"
                ),
                "kind": kind,
                "stderr": _retained_output(
                    document["stderr"], name=f"retained {kind} stderr", expected_path=f"prerequisites/{kind}.stderr"
                ),
                "stdout": _retained_output(
                    document["stdout"], name=f"retained {kind} stdout", expected_path=f"prerequisites/{kind}.stdout"
                ),
                "tests": _validate_test_counts(document["tests"]),
            }
        )
    return {
        "commands": commands,
        "environment": _retained_prerequisite_environment(root["environment"]),
        "schema_version": 3,
        "study_id": study_id,
        "url": url,
    }


def render_retained_prerequisites(value: object) -> bytes:
    """Render one canonical, complete retained prerequisite evidence document."""
    return _canonical_json(cast(JsonObject, _retained_prerequisite_document(value)))


def parse_retained_prerequisites(content: bytes) -> dict[str, object]:
    """Strictly parse a canonical retained prerequisite document without executing it."""
    document = _retained_prerequisite_document(_load_json(content))
    if _canonical_json(cast(JsonObject, document)) != content:
        raise ValueError("retained prerequisite JSON must use canonical sorted compact encoding with one trailing newline")
    return document


def retained_prerequisite_paths(value: object) -> tuple[str, ...]:
    """Return the three retained output paths for each validated prerequisite command."""
    document = _retained_prerequisite_document(value)
    paths = [
        cast(str, cast(JsonObject, command[field])["path"])
        for command in cast(list[JsonObject], document["commands"])
        for field in ("stdout", "stderr", "junit")
    ]
    return tuple(paths)


def _expected_capability_argv(study_id: str, url: str) -> tuple[str, ...]:
    evidence = f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites"
    mount_source = f"examples/validation_study/.study-work/mount/{study_id}"
    return (
        "docker",
        "run",
        "--rm",
        "--name",
        f"trafficlab-validation-study-capability-{study_id}",
        "--label",
        f"org.trafficlab.validation-study.study={study_id}",
        "--cidfile",
        f"{evidence}/capability.cid",
        "--network",
        "bridge",
        "--mount",
        f"type=bind,src={mount_source},dst=/trafficlab-study",
        TARGET_REFERENCE,
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--max-redirs",
        "3",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--http1.1",
        "--connect-timeout",
        "15",
        "--max-time",
        "30",
        "--range",
        "0-0",
        "--max-filesize",
        "1",
        "--dump-header",
        "/trafficlab-study/.capability.headers",
        "--output",
        "/dev/null",
        "--write-out",
        "status=%{response_code}\nsize=%{size_download}\nurl=%{url_effective}\nredirects=%{num_redirects}\n",
        "--url",
        url,
    )


def _validate_capability(
    value: object,
    *,
    repository_root: Path,
    study_id: str,
    url: str,
) -> JsonObject:
    document = _exact_object(value, CAPABILITY_KEYS, name="capability")
    argv = _string_array(document["argv"], name="capability argv", nonempty=True)
    _require(
        argv == _expected_capability_argv(study_id, url),
        "capability argv must equal the exact repository-relative Docker/curl projection",
    )
    started = _utc_timestamp(document["started_utc"], name="capability start")
    completed = _utc_timestamp(document["completed_utc"], name="capability completion")
    _require_timestamp_order(started, completed, name="capability")
    exit_status = _strict_int(document["exit_status"], name="capability exit status")
    status = _strict_int(document["status"], name="capability status")
    content_length = _strict_int(document["content_length"], name="capability content length")
    object_size = _strict_int(document["object_size_bytes"], name="capability object size")
    redirect_count = _strict_int(document["redirect_count"], name="capability redirect count")
    downloaded = _strict_int(document["body_bytes_downloaded"], name="capability downloaded bytes")
    _require(
        (exit_status, status, content_length, downloaded) == (0, 206, 1, 1),
        "capability must have exit zero, status 206, content length one, and one downloaded byte",
    )
    _require(
        4 * 1024 * 1024 <= object_size <= 16 * 1024 * 1024,
        "capability object size must be from 4 MiB through 16 MiB",
    )
    _require(0 <= redirect_count <= 3, "capability redirect count must be in 0..3")
    content_range = _strict_string(document["content_range"], name="capability content range")
    _require(
        content_range == f"bytes 0-0/{object_size}",
        "capability content range must exactly match its object size",
    )
    validate_endpoint_url(_strict_string(document["final_url"], name="capability final URL"))
    mount_source = _repository_relative_path(
        document["mount_source"], repository_root=repository_root, name="capability mount source"
    )
    expected_mount = f"examples/validation_study/.study-work/mount/{study_id}"
    _require(
        mount_source == expected_mount,
        "capability mount source must equal the study repository-relative mount path",
    )
    archive_path = _repository_relative_path(
        document["canary_archive_path"], repository_root=repository_root, name="capability canary archive path"
    )
    expected_archive = f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites/capability.headers"
    _require(
        archive_path == expected_archive,
        "capability canary archive path must equal the study evidence path",
    )
    default_user = _strict_bool(document["used_image_default_user"], name="capability default image user")
    cleanup = _strict_bool(document["container_cleanup_verified"], name="capability cleanup verification")
    mount_mode = _strict_int(document["mount_directory_mode"], name="capability mount directory mode")
    file_mode = _strict_int(document["canary_file_mode"], name="capability canary file mode")
    archive_mode = _strict_int(document["canary_archive_mode"], name="capability canary archive mode")
    _require(default_user, "capability must use the image default user")
    _require(cleanup, "capability container cleanup must be verified")
    _require(mount_mode == 493, "capability mount directory mode must be decimal 493")
    _require(file_mode == 438, "capability canary file mode must be decimal 438")
    _require(archive_mode == 384, "capability canary archive mode must be decimal 384")
    _sha256(document["canary_sha256"], name="capability canary SHA-256")
    _container_id(document["container_id"], name="capability container ID")
    _sha256(document["stdout_sha256"], name="capability stdout SHA-256")
    _sha256(document["stderr_sha256"], name="capability stderr SHA-256")
    return cast(JsonObject, document)


def _prerequisite_document(value: PrerequisiteResults) -> JsonObject:
    _require_type(type(value) is PrerequisiteResults, "prerequisite value must be PrerequisiteResults")
    return {
        "schema_version": value.schema_version,
        "created_utc": value.created_utc,
        "study_id": value.study_id,
        "git_commit": value.git_commit,
        "git_tree_clean": value.git_tree_clean,
        "url": value.url,
        "tools": _thaw_json(value.tools),
        "images": _thaw_json(value.images),
        "capability": _thaw_json(value.capability),
        "config_sha256": _thaw_json(value.config_sha256),
        "commands": [_thaw_json(command) for command in value.commands],
    }


def _validate_prerequisite_document(
    document: JsonObject,
    *,
    repository_root: Path,
) -> PrerequisiteResults:
    root = _exact_object(document, PREREQUISITE_ROOT_KEYS, name="prerequisite root")
    schema_version = _strict_int(root["schema_version"], name="prerequisite schema version")
    _require(schema_version == 1, "prerequisite schema version must be exactly 1")
    created = _utc_timestamp(root["created_utc"], name="prerequisite creation time")
    study_id = validate_study_id(_strict_string(root["study_id"], name="study ID"))
    git_commit = _git_commit(root["git_commit"])
    tree_clean = _strict_bool(root["git_tree_clean"], name="Git tree clean")
    _require(tree_clean, "prerequisite Git tree must be clean")
    url = validate_endpoint_url(_strict_string(root["url"], name="operator URL"))
    tools = _validate_tools(root["tools"])
    images = _validate_images(root["images"])
    capability = _validate_capability(root["capability"], repository_root=repository_root, study_id=study_id, url=url)
    hashes = _profile_hashes(root["config_sha256"])
    commands = _strict_list(root["commands"], name="prerequisite commands")
    _require(len(commands) == 2, "prerequisite commands must contain docker_matrix then internet_smoke")
    validated_commands = (
        _validate_command(commands[0], expected_kind="docker_matrix", study_id=study_id, url=url),
        _validate_command(commands[1], expected_kind="internet_smoke", study_id=study_id, url=url),
    )
    return PrerequisiteResults(
        schema_version=schema_version,
        created_utc=created,
        study_id=study_id,
        git_commit=git_commit,
        git_tree_clean=tree_clean,
        url=url,
        tools=_freeze_object(tools),
        images=_freeze_object(images),
        capability=_freeze_object(capability),
        config_sha256=_freeze_object(hashes),
        commands=(
            _freeze_object(validated_commands[0]),
            _freeze_object(validated_commands[1]),
        ),
    )


def render_prerequisite_results(value: PrerequisiteResults) -> bytes:
    document = _prerequisite_document(value)
    validated = _validate_prerequisite_document(document, repository_root=REPOSITORY_ROOT)
    return _canonical_json(_prerequisite_document(validated))


def parse_prerequisite_results(content: bytes, *, repository_root: Path) -> PrerequisiteResults:
    document = _load_json(content)
    result = _validate_prerequisite_document(document, repository_root=repository_root)
    if _canonical_json(_prerequisite_document(result)) != content:
        raise ValueError("prerequisite JSON must use canonical sorted compact encoding with one trailing newline")
    return result


def _publish_support_json(
    path: Path,
    content: bytes,
    *,
    validate: Callable[[bytes], None],
) -> None:
    if _path_entry_exists(path):
        raise TrafficlabError(
            f"official Validation Study publication target already exists: {path}",
            corrective_action="preserve the existing official file and restart with a new study ID",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor_open = False
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        persisted = temporary.read_bytes()
        _require(persisted == content, "temporary official Validation Study JSON bytes changed before publication")
        validate(persisted)
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise TrafficlabError(
                f"official Validation Study publication target already exists: {path}",
                corrective_action="preserve the existing official file and restart with a new study ID",
            ) from error
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        published = path.read_bytes()
        _require(published == content, "published official Validation Study JSON bytes changed")
        validate(published)
    finally:
        if descriptor_open:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _validate_prerequisite_evidence(
    repository_root: Path,
    prerequisites: PrerequisiteResults,
) -> None:
    root = repository_root.resolve()
    evidence_directory = (
        root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / prerequisites.study_id
        / "00-prerequisites"
    )
    expected_names = {
        "capability.headers",
        "capability.stdout",
        "capability.stderr",
        "capability.cid",
        "capture.iid",
        "docker.stdout",
        "docker.stderr",
        "docker.xml",
        "internet.stdout",
        "internet.stderr",
        "internet.xml",
    }
    try:
        directory_mode = evidence_directory.lstat().st_mode
        _require(
            stat.S_ISDIR(directory_mode)
            and not stat.S_ISLNK(directory_mode)
            and evidence_directory.resolve() == evidence_directory,
            "prerequisite evidence directory must be the exact non-symlink study directory",
        )
        entries = tuple(evidence_directory.iterdir())
        _require({entry.name for entry in entries} == expected_names, "prerequisite evidence must use exact file names")
        _require(
            all(
                stat.S_ISREG(entry.lstat().st_mode)
                and not stat.S_ISLNK(entry.lstat().st_mode)
                and stat.S_IMODE(entry.lstat().st_mode) == 0o600
                for entry in entries
            ),
            "every retained prerequisite evidence file must be a regular non-symlink at mode 0600",
        )
        contents = {entry.name: entry.read_bytes() for entry in entries}
        dockerfile_path = root / "docker" / "capture" / "Dockerfile"
        capture_script_path = root / "docker" / "capture" / "capture.sh"
        source_modes = (dockerfile_path.lstat().st_mode, capture_script_path.lstat().st_mode)
        _require(
            all(stat.S_ISREG(mode) and not stat.S_ISLNK(mode) for mode in source_modes)
            and dockerfile_path.resolve() == dockerfile_path
            and capture_script_path.resolve() == capture_script_path,
            "capture source files must be exact regular non-symlinks",
        )
        dockerfile = dockerfile_path.read_bytes()
        capture_script = capture_script_path.read_bytes()
    except OSError as error:
        raise ValueError(f"could not read retained prerequisite evidence: {error}") from error

    images = prerequisites.images
    _require(
        sha256_bytes(dockerfile) == images["capture_dockerfile_sha256"]
        and sha256_bytes(capture_script) == images["capture_script_sha256"],
        "capture source files must match prerequisite source hashes",
    )
    capability = prerequisites.capability
    archive_record = _repository_relative_path(
        capability["canary_archive_path"], repository_root=root, name="capability evidence path"
    )
    _require(
        root / Path(*archive_record.split("/")) == evidence_directory / "capability.headers",
        "capability archive record must resolve to its exact retained evidence file",
    )
    _require(
        sha256_bytes(contents["capability.headers"]) == capability["canary_sha256"]
        and sha256_bytes(contents["capability.stdout"]) == capability["stdout_sha256"]
        and sha256_bytes(contents["capability.stderr"]) == capability["stderr_sha256"],
        "retained capability hashes must match prerequisite evidence",
    )
    redirect_count, object_size, content_range, final_url = _capability_header_values(
        contents["capability.headers"], initial_url=prerequisites.url
    )
    status, downloaded, write_final_url, write_redirects = _capability_write_out(contents["capability.stdout"])
    _require(
        (redirect_count, object_size, content_range, final_url)
        == (
            capability["redirect_count"],
            capability["object_size_bytes"],
            capability["content_range"],
            capability["final_url"],
        )
        and (status, downloaded, write_final_url, write_redirects)
        == (capability["status"], capability["body_bytes_downloaded"], final_url, redirect_count),
        "retained capability headers and write-out must match prerequisite facts",
    )
    try:
        retained_cid = _container_id(
            contents["capability.cid"].decode("ascii").strip(), name="retained capability container ID"
        )
        retained_iid = _image_id(contents["capture.iid"].decode("ascii").strip(), name="retained capture image ID")
    except UnicodeDecodeError as error:
        raise ValueError("retained capability CID and capture IID must be ASCII") from error
    _require(
        retained_cid == capability["container_id"] and retained_iid == images["capture_image_id"],
        "retained capability CID and capture IID must match prerequisite identities",
    )

    for command, prefix, expected_kind in zip(
        prerequisites.commands,
        ("docker", "internet"),
        ("docker_matrix", "internet_smoke"),
        strict=True,
    ):
        _require(command["kind"] == expected_kind, "retained prerequisite commands must retain exact kind order")
        argv = cast(tuple[FrozenJsonValue, ...], command["argv"])
        junit_record = _repository_relative_path(argv[-1], repository_root=root, name=f"{prefix} JUnit path")
        _require(
            root / Path(*junit_record.split("/")) == evidence_directory / f"{prefix}.xml",
            f"{prefix} JUnit record must resolve to its exact retained evidence file",
        )
        stdout = contents[f"{prefix}.stdout"]
        stderr = contents[f"{prefix}.stderr"]
        junit = contents[f"{prefix}.xml"]
        _require(
            sha256_bytes(stdout) == command["stdout_sha256"]
            and sha256_bytes(stderr) == command["stderr_sha256"]
            and sha256_bytes(junit) == command["junit_sha256"],
            f"retained {prefix} output and JUnit hashes must match prerequisite evidence",
        )
        _require(_parse_junit_counts(junit) == command["tests"], f"retained {prefix} JUnit counts must match evidence")


def _publish_prerequisites(  # pyright: ignore[reportUnusedFunction]
    path: Path,
    value: PrerequisiteResults,
    *,
    repository_root: Path,
) -> None:
    content = render_prerequisite_results(value)

    def validate(persisted: bytes) -> None:
        parsed = parse_prerequisite_results(persisted, repository_root=repository_root)
        if render_prerequisite_results(parsed) != content:
            raise ValueError("persisted prerequisite JSON is not canonical")

    _publish_support_json(path, content, validate=validate)


def run_prerequisites(
    url: str,
    study_id: str,
    *,
    repository_root: Path,
    runner: CommandRunner,
    utc_now: Callable[[], datetime],
) -> PrerequisiteResults:
    root = repository_root.resolve()
    try:
        _require(root.is_dir(), f"repository root must be an existing directory: {root}")
        url = validate_endpoint_url(url)
        study_id = validate_study_id(study_id)
        prerequisite_path = root / "examples" / "validation_study" / "prerequisites.json"
        config_paths = {
            name: root / "examples" / "validation_study" / "configs" / f"{name}.toml"
            for name in ("short", "streaming", "bursty")
        }
        _require(not _path_entry_exists(prerequisite_path), f"prerequisite target already exists: {prerequisite_path}")
        for name, path in config_paths.items():
            _require(not _path_entry_exists(path), f"checked {name} config target already exists: {path}")

        commit_result = runner(
            ("git", "rev-parse", "HEAD"),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        )
        _require(
            commit_result.returncode == 0,
            f"could not resolve clean prerequisite Git commit: "
            f"{_command_detail(commit_result, operation='Git commit inspection')}",
        )
        git_commit = _git_commit(_stdout_text(commit_result, operation="Git commit inspection"))
        status_result = runner(
            ("git", "status", "--porcelain=v1", "--untracked-files=all"),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        )
        status_stdout, _status_stderr = _completed_output(status_result, operation="Git tree inspection")
        _require(status_result.returncode == 0, "could not inspect prerequisite Git tree")
        _require(status_stdout == b"", "prerequisites require an exactly clean tracked and untracked Git tree")
        _require(platform.python_version() == "3.12.3", "prerequisites require exact CPython 3.12.3")

        docker_version = runner(
            ("docker", "version", "--format", "{{.Server.Version}}"),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        )
        _require(
            docker_version.returncode == 0,
            f"could not read Docker Engine version: {_command_detail(docker_version, operation='Docker version')}",
        )
        docker_engine_version = _strict_string(
            _stdout_text(docker_version, operation="Docker version"), name="Docker Engine version"
        )
        compose_version = runner(
            ("docker", "compose", "version", "--short"),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        )
        _require(
            compose_version.returncode == 0,
            f"could not read Docker Compose version: "
            f"{_command_detail(compose_version, operation='Docker Compose version')}",
        )
        docker_compose_version = _strict_string(
            _stdout_text(compose_version, operation="Docker Compose version"), name="Docker Compose version"
        )

        evidence_directory = (
            root / "examples" / "validation_study" / ".study-work" / "evidence" / study_id / "00-prerequisites"
        )
        evidence_directory.parent.mkdir(parents=True, exist_ok=True)
        evidence_directory.mkdir()
        mount_directory = root / "examples" / "validation_study" / ".study-work" / "mount" / study_id
        mount_directory.mkdir(parents=True)
        mount_directory.chmod(0o755)
        _require(
            stat.S_ISDIR(mount_directory.lstat().st_mode)
            and not stat.S_ISLNK(mount_directory.lstat().st_mode)
            and stat.S_IMODE(mount_directory.lstat().st_mode) == 0o755,
            "capability mount must be a host-owned regular 0755 directory",
        )

        pull = runner(
            ("docker", "image", "pull", TARGET_REFERENCE),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
        )
        _require(
            pull.returncode == 0,
            f"could not pull approved target image: {_command_detail(pull, operation='target image pull')}",
        )
        inspect = runner(
            ("docker", "image", "inspect", TARGET_REFERENCE),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
        )
        _require(
            inspect.returncode == 0,
            f"could not inspect approved target image: {_command_detail(inspect, operation='target image inspect')}",
        )
        inspect_stdout, _inspect_stderr = _completed_output(inspect, operation="target image inspect")
        images = _target_image_record(inspect_stdout)

        iid_path = evidence_directory / "capture.iid"
        _require(not _path_entry_exists(iid_path), "capture IID path must be absent before build")
        build = runner(
            ("docker", "build", "--pull=false", "--iidfile", str(iid_path), "docker/capture"),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
        )
        _require(
            build.returncode == 0,
            f"could not build capture image: {_command_detail(build, operation='capture image build')}",
        )
        try:
            capture_image_id = _image_id(iid_path.read_text(encoding="ascii").strip(), name="capture image ID")
            iid_path.chmod(0o600)
            dockerfile = (root / "docker" / "capture" / "Dockerfile").read_bytes()
            capture_script = (root / "docker" / "capture" / "capture.sh").read_bytes()
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError(f"could not read capture image identity or source: {error}") from error
        images.update(
            {
                "capture_image_id": capture_image_id,
                "capture_dockerfile_sha256": hashlib.sha256(dockerfile).hexdigest(),
                "capture_script_sha256": hashlib.sha256(capture_script).hexdigest(),
            }
        )

        capability = _prepare_capability(
            repository_root=root,
            study_id=study_id,
            url=url,
            evidence_directory=evidence_directory,
            mount_directory=mount_directory,
            runner=runner,
            utc_now=utc_now,
        )
        docker_command = _run_prerequisite_test(
            "docker_matrix",
            _docker_matrix_argv(study_id),
            repository_root=root,
            evidence_directory=evidence_directory,
            runner=runner,
            utc_now=utc_now,
        )
        internet_command = _run_prerequisite_test(
            "internet_smoke",
            _internet_smoke_argv(study_id, url),
            repository_root=root,
            evidence_directory=evidence_directory,
            runner=runner,
            utc_now=utc_now,
        )

        config_hashes: JsonObject = {}
        for workload in workload_specs(url):
            config = build_base_config(
                workload,
                repository_root=root,
                study_id=study_id,
                url=url,
                capture_image_id=capture_image_id,
            )
            content = render_checked_base_config(config, config_paths[workload.name], root)
            config_hashes[workload.name] = hashlib.sha256(content).hexdigest()
        result = PrerequisiteResults(
            schema_version=1,
            created_utc=_timestamp_now(utc_now),
            study_id=study_id,
            git_commit=git_commit,
            git_tree_clean=True,
            url=url,
            tools=_freeze_object(
                {
                    "python_version": platform.python_version(),
                    "trafficlab_version": __version__,
                    "docker_engine_version": docker_engine_version,
                    "docker_compose_version": docker_compose_version,
                    "platform": platform.platform(),
                }
            ),
            images=_freeze_object(images),
            capability=_freeze_object(capability),
            config_sha256=_freeze_object(config_hashes),
            commands=(_freeze_object(docker_command), _freeze_object(internet_command)),
        )
        prerequisite_path.parent.mkdir(parents=True, exist_ok=True)
        _publish_prerequisites(prerequisite_path, result, repository_root=root)
        return result
    except TrafficlabError:
        raise
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        if type(study_id) is str and _STUDY_ID_PATTERN.fullmatch(study_id) is not None:
            _best_effort_preserve_capability_canary(
                root / "examples" / "validation_study" / ".study-work" / "evidence" / study_id / "00-prerequisites",
                root / "examples" / "validation_study" / ".study-work" / "mount" / study_id / ".capability.headers",
            )
        raise TrafficlabError(
            f"Validation Study prerequisite validation failed: {error}",
            corrective_action="preserve the ignored evidence, correct the prerequisite, and restart with a new study ID",
        ) from error


def _validate_run_key(value: object, *, name: str = "run key") -> JsonObject:
    document = _exact_object(value, ("workload", "repeat"), name=name)
    workload = _strict_string(document["workload"], name=f"{name}.workload")
    _require(
        workload in {"short", "streaming", "bursty"},
        f"{name}.workload must be short, streaming, or bursty",
    )
    repeat = _strict_int(document["repeat"], name=f"{name}.repeat")
    _require(1 <= repeat <= 3, f"{name}.repeat must be in 1..3")
    return {"workload": workload, "repeat": repeat}


def _validate_candidate_id(value: object) -> JsonObject:
    document = _exact_object(value, ("birth_generation", "birth_index"), name="candidate ID")
    return {
        "birth_generation": _strict_int(document["birth_generation"], name="birth generation", minimum=0),
        "birth_index": _strict_int(document["birth_index"], name="birth index", minimum=0),
    }


def _validate_genes(value: object, *, family: str) -> list[JsonValue]:
    genes = _strict_list(value, name=f"{family} genes")
    if family == "poisson_empirical":
        _require(len(genes) == 1, "poisson_empirical genes must have one value")
        _strict_float(genes[0], name="poisson c_lambda", lower=0.25, upper=4.0)
    elif family == "markov_renewal":
        _require(len(genes) == 5, "markov_renewal genes must have five values")
        _strict_float(genes[0], name="markov q1", lower=0.1, upper=0.4)
        _strict_float(genes[1], name="markov q2", lower=0.6, upper=0.9)
        _strict_float(genes[2], name="markov alpha", lower=0.0, upper=2.0)
        r = _strict_int(genes[3], name="markov r")
        _require(1 <= r <= 8, "markov r must be in [1, 8]")
        _strict_float(genes[4], name="markov c_t", lower=0.25, upper=4.0)
    elif family == "mmpp":
        _require(len(genes) == 4, "mmpp genes must have four values")
        _strict_float(genes[0], name="MMPP q01", lower=0.01, upper=10.0)
        _strict_float(genes[1], name="MMPP q10", lower=0.01, upper=10.0)
        lambda0 = _strict_float(genes[2], name="MMPP lambda0", lower=10.0, upper=100.0)
        lambda1 = _strict_float(genes[3], name="MMPP lambda1", lower=0.1, upper=1000.0)
        _require(lambda0 < lambda1, "MMPP lambda0 must be strictly less than lambda1")
    else:
        raise ValueError("family must be one of the three published model families")
    return cast(list[JsonValue], genes)


def _bounded_score(value: object, *, name: str) -> float:
    return _strict_float(value, name=name, lower=0.0, upper=1.0)


def _validate_method_scores(value: object, *, name: str = "method scores") -> JsonObject:
    document = _exact_object(value, PUBLISHED_METHOD_ORDER, name=name)
    for method in PUBLISHED_METHOD_ORDER:
        _bounded_score(document[method], name=f"{name}.{method}")
    return cast(JsonObject, document)


def _validate_score(value: object, *, name: str = "score") -> JsonObject:
    document = _exact_object(value, ("aggregate", "methods"), name=name)
    _bounded_score(document["aggregate"], name=f"{name}.aggregate")
    _validate_method_scores(document["methods"], name=f"{name}.methods")
    return cast(JsonObject, document)


def _validate_descriptive(
    value: object,
    *,
    name: str,
    observations: Sequence[int | float] | None = None,
) -> JsonObject:
    keys = (
        "count",
        "mean",
        "minimum",
        "maximum",
        "range",
        "sample_variance",
        "sample_standard_deviation",
    )
    document = _exact_object(value, keys, name=name)
    if observations is not None:
        _require(
            document == descriptive_statistics(observations),
            f"{name} is stale and does not recompute from its three source observations",
        )
        return cast(JsonObject, document)
    count = _strict_int(document["count"], name=f"{name}.count")
    _strict_float(document["mean"], name=f"{name}.mean")
    minimum = _strict_float(document["minimum"], name=f"{name}.minimum")
    maximum = _strict_float(document["maximum"], name=f"{name}.maximum")
    range_ = _strict_float(document["range"], name=f"{name}.range", lower=0.0)
    _strict_float(document["sample_variance"], name=f"{name}.sample_variance", lower=0.0)
    _strict_float(document["sample_standard_deviation"], name=f"{name}.sample_standard_deviation", lower=0.0)
    _require(count == 3, f"{name}.count must be exactly 3")
    _require(minimum <= maximum, f"{name} minimum must not exceed maximum")
    _require(range_ == maximum - minimum, f"{name}.range must equal maximum minus minimum")
    return cast(JsonObject, document)


def _validate_score_summary(value: object, *, name: str, observations: Sequence[JsonObject]) -> JsonObject:
    document = _exact_object(value, ("aggregate", "methods"), name=name)
    aggregate_values = [cast(float, score["aggregate"]) for score in observations]
    methods = _exact_object(document["methods"], PUBLISHED_METHOD_ORDER, name=f"{name}.methods")
    source_methods = [cast(dict[str, JsonValue], score["methods"]) for score in observations]
    _validate_descriptive(document["aggregate"], name=f"{name}.aggregate", observations=aggregate_values)
    for method in PUBLISHED_METHOD_ORDER:
        _validate_descriptive(
            methods[method],
            name=f"{name}.methods.{method}",
            observations=[cast(float, values[method]) for values in source_methods],
        )
    return cast(JsonObject, document)


def _validate_direction_values(value: object, *, name: str, positive_total: bool = False) -> JsonObject:
    document = _exact_object(value, ("outbound", "inbound"), name=name)
    outbound = _strict_int(document["outbound"], name=f"{name}.outbound", minimum=0)
    inbound = _strict_int(document["inbound"], name=f"{name}.inbound", minimum=0)
    _require(not positive_total or outbound + inbound > 0, f"{name} must have a positive total")
    return cast(JsonObject, document)


def _validate_sample(value: object, *, name: str, expected_count: int, frame_lengths: bool) -> JsonObject:
    keys = ("count", "minimum", "median", "quantile_probability", "quantile", "maximum", "zero_count")
    document = _exact_object(value, keys, name=name)
    count = _strict_int(document["count"], name=f"{name}.count", minimum=1)
    _require(count == expected_count, f"{name}.count must equal {expected_count}")
    minimum = _strict_float(document["minimum"], name=f"{name}.minimum", lower=0.0)
    median = _strict_float(document["median"], name=f"{name}.median", lower=0.0)
    probability = _strict_float(document["quantile_probability"], name=f"{name}.quantile_probability")
    quantile = _strict_float(document["quantile"], name=f"{name}.quantile", lower=0.0)
    maximum = _strict_float(document["maximum"], name=f"{name}.maximum", lower=0.0)
    zero_count = _strict_int(document["zero_count"], name=f"{name}.zero_count", minimum=0)
    _require(probability == 0.95, f"{name}.quantile_probability must be exactly 0.95")
    _require(
        minimum <= median <= maximum and minimum <= quantile <= maximum,
        f"{name} median and quantile must lie between minimum and maximum",
    )
    _require(
        zero_count <= count and (not frame_lengths or zero_count == 0),
        f"{name}.zero_count is inconsistent with its sample",
    )
    _require(not frame_lengths or minimum > 0.0, f"{name} lengths must be positive")
    return cast(JsonObject, document)


def _workload_widths(workload: str) -> tuple[float, float]:
    return next(spec.multiscale_widths_seconds for spec in workload_specs(_ORACLE_URL) if spec.name == workload)


def _validate_scale(
    value: object,
    *,
    expected_width: float,
    packet_totals: JsonObject,
    byte_totals: JsonObject,
) -> JsonObject:
    document = _exact_object(
        value, ("width_seconds", "bins_per_direction", "packet_totals", "byte_totals"), name="scale total"
    )
    width = _strict_float(document["width_seconds"], name="scale width", lower=0.0)
    _require(width == expected_width, "scale width must equal the configured positive width")
    _strict_int(document["bins_per_direction"], name="scale bins per direction", minimum=1)
    packets = _validate_direction_values(document["packet_totals"], name="scale packet totals")
    bytes_ = _validate_direction_values(document["byte_totals"], name="scale byte totals", positive_total=True)
    _require(
        packets == packet_totals and bytes_ == byte_totals,
        "scale direction totals must equal trace direction totals",
    )
    return cast(JsonObject, document)


def _validate_trace(value: object, *, workload: str, name: str) -> JsonObject:
    keys = (
        "packet_count",
        "observation_window_seconds",
        "packet_totals",
        "byte_totals",
        "frame_lengths",
        "iats",
        "scales",
    )
    document = _exact_object(value, keys, name=name)
    packet_count = _strict_int(document["packet_count"], name=f"{name}.packet_count", minimum=2)
    window = _strict_float(document["observation_window_seconds"], name=f"{name}.window", lower=0.0)
    _require(window > 0.0, f"{name}.observation_window_seconds must be positive")
    packets = _validate_direction_values(document["packet_totals"], name=f"{name}.packet totals")
    bytes_ = _validate_direction_values(document["byte_totals"], name=f"{name}.byte totals", positive_total=True)
    _require(
        cast(int, packets["outbound"]) + cast(int, packets["inbound"]) == packet_count,
        f"{name} packet totals must sum to packet count",
    )
    _validate_sample(
        document["frame_lengths"], name=f"{name}.frame lengths", expected_count=packet_count, frame_lengths=True
    )
    _validate_sample(document["iats"], name=f"{name}.IATs", expected_count=packet_count - 1, frame_lengths=False)
    scales = _strict_list(document["scales"], name=f"{name}.scales")
    widths = _workload_widths(workload)
    _require(len(scales) == len(widths), f"{name}.scales must contain the exact configured widths")
    for scale, width in zip(scales, widths, strict=True):
        _validate_scale(scale, expected_width=width, packet_totals=packets, byte_totals=bytes_)
    return cast(JsonObject, document)


def _expected_transfers(workload: str) -> tuple[tuple[int, int, str], ...]:
    return next(spec.transfers for spec in workload_specs(_ORACLE_URL) if spec.name == workload)


def _validate_transfer_responses(
    value: object,
    *,
    repository_root: Path,
    workload: str,
    evidence_directory: str,
    object_size: int,
) -> list[JsonValue]:
    responses = _strict_list(value, name="transfer responses")
    expected = _expected_transfers(workload)
    _require(
        len(responses) == len(expected),
        "transfer responses must contain the exact workload response count",
    )
    paths: set[str] = set()
    for index, (response, (expected_start, expected_end, filename)) in enumerate(zip(responses, expected, strict=True)):
        document = _exact_object(response, TRANSFER_RESPONSE_KEYS, name="transfer response")
        transfer_index = _strict_int(document["transfer_index"], name="transfer index")
        start = _strict_int(document["requested_start"], name="requested start", minimum=0)
        end = _strict_int(document["requested_end"], name="requested end", minimum=0)
        status = _strict_int(document["status"], name="transfer status")
        length = _strict_int(document["content_length"], name="transfer content length", minimum=1)
        _require(
            (transfer_index, start, end) == (index, expected_start, expected_end),
            "transfer response index and range must equal the exact workload transfer",
        )
        _require(
            status == 206 and length == end - start + 1,
            "transfer response must have status 206 and exact inclusive content length",
        )
        content_range = _strict_string(document["content_range"], name="transfer content range")
        _require(
            content_range == f"bytes {start}-{end}/{object_size}",
            "transfer content range must equal its requested range and capability object size",
        )
        path = _repository_relative_path(
            document["header_archive_path"], repository_root=repository_root, name="header archive path"
        )
        _require(
            path == f"{evidence_directory}/{filename}" and path not in paths,
            "header archive path must be unique beneath the transfer evidence directory",
        )
        paths.add(path)
        scratch_mode = _strict_int(document["scratch_precreate_mode"], name="scratch precreate mode")
        archive_mode = _strict_int(document["archive_mode"], name="header archive mode")
        inode = _strict_bool(document["inode_preserved"], name="header inode preservation")
        _require(
            scratch_mode == 438 and archive_mode == 384 and inode,
            "transfer response must preserve inode and exact 0666/0600 modes",
        )
        _sha256(document["header_sha256"], name="header SHA-256")
    return cast(list[JsonValue], responses)


def _validate_family_champion(value: object, *, expected_family: str) -> JsonObject:
    keys = ("family", "candidate_id", "genes", "selection_fitness", "selection_seeds", "selection_score")
    document = _exact_object(value, keys, name="family champion")
    family = _strict_string(document["family"], name="champion family")
    _require(family == expected_family, "family champions must use exact lexical family order")
    seeds = _strict_list(document["selection_seeds"], name="selection seeds")
    _require(
        tuple(_strict_int(seed, name="selection seed") for seed in seeds) == (17, 29),
        "family champion selection seeds must be exactly [17, 29]",
    )
    fitness = _bounded_score(document["selection_fitness"], name="selection fitness")
    score = _validate_score(document["selection_score"], name="selection score")
    _require(
        score["aggregate"] == fitness,
        "family champion selection score aggregate must equal selection fitness",
    )
    _validate_candidate_id(document["candidate_id"])
    _validate_genes(document["genes"], family=family)
    return cast(JsonObject, document)


def _validate_champions(value: object) -> list[JsonValue]:
    champions = _strict_list(value, name="family champions")
    _require(len(champions) == 3, "family champions must contain all three families")
    for champion, family in zip(champions, FAMILY_ORDER, strict=True):
        _validate_family_champion(champion, expected_family=family)
    return cast(list[JsonValue], champions)


def _validate_winner(value: object, *, champions: Sequence[JsonValue]) -> JsonObject:
    keys = ("family", "candidate_id", "genes", "selection_fitness")
    document = _exact_object(value, keys, name="winner")
    family = _strict_string(document["family"], name="winner family")
    _validate_candidate_id(document["candidate_id"])
    _validate_genes(document["genes"], family=family)
    _bounded_score(document["selection_fitness"], name="winner selection fitness")
    champion_objects = [cast(JsonObject, champion) for champion in champions]
    expected = min(
        champion_objects,
        key=lambda champion: (
            -cast(float, champion["selection_fitness"]),
            cast(int, cast(dict[str, JsonValue], champion["candidate_id"])["birth_generation"]),
            cast(int, cast(dict[str, JsonValue], champion["candidate_id"])["birth_index"]),
        ),
    )
    _require(
        document == {key: expected[key] for key in keys},
        "winner must be the stable overall best family champion",
    )
    return cast(JsonObject, document)


def _validate_fresh_simulation(value: object, *, expected_source: str) -> JsonObject:
    document = _exact_object(value, ("seed", "score", "source"), name="fresh simulation record")
    seed = _strict_int(document["seed"], name="fresh simulation seed")
    source = _strict_string(document["source"], name="fresh simulation source")
    _require(
        seed == 97 and source == expected_source,
        f"fresh simulation evidence must use seed 97 and source {expected_source}",
    )
    _validate_score(document["score"], name="fresh simulation score")
    return cast(JsonObject, document)


def _validate_published(value: object) -> JsonObject:
    document = _exact_object(value, ("seed", "score"), name="published record")
    seed = _strict_int(document["seed"], name="published seed")
    _require(seed == 97, "published seed must be exactly 97")
    _validate_score(document["score"], name="published score")
    return cast(JsonObject, document)


def _validate_raw_sequence(value: object, *, reference: JsonObject, generated: JsonObject) -> JsonObject:
    document = _exact_object(value, RAW_SEQUENCE_KEYS, name="raw sequence")
    seed = _strict_int(document["seed"], name="raw sequence seed")
    window = _strict_float(document["observation_window_seconds"], name="raw sequence window", lower=0.0)
    trial_count = _strict_int(document["trial_event_count"], name="trial event count", minimum=1)
    final_count = _strict_int(document["final_event_count"], name="final event count", minimum=1)
    reparsed_count = _strict_int(document["reparsed_event_count"], name="reparsed event count", minimum=1)
    raw_equal = _strict_bool(document["raw_events_equal"], name="raw event equality")
    score_reproduced = _strict_bool(
        document["fresh_simulation_score_reproduced"], name="fresh simulation score reproduction"
    )
    reparsed_equal = _strict_bool(document["reparsed_matches_quantized"], name="reparsed generated equality")
    _require(
        seed == 97 and window == reference["observation_window_seconds"] == generated["observation_window_seconds"],
        "raw sequence must use seed 97 and all raw/reference/generated observation windows must match",
    )
    _require(
        trial_count == final_count == reparsed_count == generated["packet_count"],
        "raw sequence trial/final/reparsed/generated event counts must all match",
    )
    _require(
        raw_equal and score_reproduced and reparsed_equal,
        "raw sequence equality and reproduction proofs must all be true",
    )
    return cast(JsonObject, document)


def _validate_reuse(value: object) -> JsonObject:
    keys = ("capture", "best_model", "generated", "similarity")
    document = _exact_object(value, keys, name="reuse")
    for key in keys:
        _strict_bool(document[key], name=f"reuse.{key}")
    _require(
        not any(cast(bool, document[key]) for key in keys),
        "fresh study reuse fields must all be false",
    )
    return cast(JsonObject, document)


def _validate_artifact_hashes(value: object) -> JsonObject:
    document = _exact_object(value, ARTIFACT_NAMES, name="artifact hashes")
    for name in ARTIFACT_NAMES:
        _sha256(document[name], name=f"artifact hash {name}")
    return cast(JsonObject, document)


def _validate_run_evidence(
    document: JsonObject,
    *,
    repository_root: Path,
    workload: WorkloadName,
    evidence_directory: str,
    object_size: int,
    fresh_simulation_source: str,
) -> None:
    elapsed = _strict_float(document["elapsed_seconds"], name="run elapsed seconds", lower=0.0)
    _require(elapsed > 0.0, "run elapsed seconds must be positive")
    cleanup = _strict_bool(document["cleanup_verified"], name="run cleanup verification")
    _require(cleanup, "run cleanup must be verified")
    reference = _validate_trace(document["reference"], workload=workload, name="reference trace")
    generated = _validate_trace(document["generated"], workload=workload, name="generated trace")
    champions = _validate_champions(document["family_champions"])
    _validate_reuse(document["reuse"])
    _validate_transfer_responses(
        document["transfer_responses"],
        repository_root=repository_root,
        workload=workload,
        evidence_directory=evidence_directory,
        object_size=object_size,
    )
    _validate_artifact_hashes(document["artifact_sha256"])
    _validate_winner(document["winner"], champions=champions)
    _validate_fresh_simulation(document["fresh_simulation"], expected_source=fresh_simulation_source)
    _validate_published(document["published"])
    _validate_raw_sequence(document["raw_sequence"], reference=reference, generated=generated)


def _validate_run_document(
    value: object,
    *,
    expected: tuple[int, str, str, int],
    repository_root: Path,
    study_id: str,
    object_size: int,
) -> JsonObject:
    document = _exact_object(value, _STUDY_RUN_KEYS, name="study run")
    order = _strict_int(document["execution_order"], name="execution order")
    run_id = _strict_string(document["run_id"], name="run ID")
    key = _validate_run_key(document["key"])
    _require(
        (order, run_id, key["workload"], key["repeat"]) == expected,
        "primary runs must use the exact balanced primary order and unique run keys",
    )
    workload = cast(str, key["workload"])
    config_path = _repository_relative_path(
        document["config_path"], repository_root=repository_root, name="run config path"
    )
    run_directory = _repository_relative_path(
        document["run_directory"], repository_root=repository_root, name="run directory"
    )
    evidence_directory = _repository_relative_path(
        document["transfer_evidence_directory"],
        repository_root=repository_root,
        name="transfer evidence directory",
    )
    _require(
        config_path == f"runs/validation_study/{study_id}/realized-configs/{run_id}.toml",
        "primary config path must equal its exact realized config path",
    )
    _require(
        run_directory == f"runs/validation_study/{study_id}/{run_id}",
        "primary run directory must equal its exact run path",
    )
    _require(
        evidence_directory == f"examples/validation_study/.study-work/evidence/{study_id}/{run_id}",
        "primary transfer evidence directory must equal its exact sibling evidence path",
    )
    _validate_run_evidence(
        cast(JsonObject, document),
        repository_root=repository_root,
        workload=cast(WorkloadName, workload),
        evidence_directory=evidence_directory,
        object_size=object_size,
        fresh_simulation_source="run_experiment_fit_outcome",
    )
    return cast(JsonObject, document)


def _validate_environment(value: object) -> JsonObject:
    document = _exact_object(value, ENVIRONMENT_KEYS, name="environment")
    _git_commit(document["git_commit"])
    for key in ("python_version", "trafficlab_version", "docker_engine_version", "docker_compose_version", "platform"):
        _strict_string(document[key], name=f"environment {key}")
    _image_id(document["target_image_id"], name="environment target image ID")
    _image_id(document["capture_image_id"], name="environment capture image ID")
    _utc_timestamp(document["study_date_utc"], name="environment study date")
    _require(
        document["python_version"] == "3.12.3" and document["trafficlab_version"] == __version__,
        "environment Python and Trafficlab versions must equal the locked study versions",
    )
    return cast(JsonObject, document)


def _validate_seeds(value: object) -> JsonObject:
    document = _exact_object(value, ("master", "final", "selection"), name="seeds")
    master = _strict_int(document["master"], name="master seed")
    final = _strict_int(document["final"], name="final seed")
    selection = tuple(
        _strict_int(seed, name="selection seed") for seed in _strict_list(document["selection"], name="selection")
    )
    _require(
        (master, final, selection) == (73, 97, (17, 29)),
        "study seeds must be master 73, final 97, selection [17, 29], with final fresh simulation",
    )
    return cast(JsonObject, document)


def _validate_workloads(value: object, *, url: str) -> list[JsonValue]:
    items = _strict_list(value, name="workload definitions")
    expected_specs = workload_specs(url)
    _require(len(items) == 3, "workload definitions must contain short, streaming, and bursty")
    keys = ("name", "argv", "workload_timeout_seconds", "total_timeout_seconds", "multiscale_widths_seconds")
    for item, expected in zip(items, expected_specs, strict=True):
        document = _exact_object(item, keys, name="workload definition")
        name = _strict_string(document["name"], name="workload name")
        _require(name == expected.name, "workload definitions must be ordered short, streaming, bursty")
        argv = _string_array(document["argv"], name=f"{name} argv", nonempty=True)
        workload_timeout = _strict_float(document["workload_timeout_seconds"], name=f"{name} workload timeout")
        total_timeout = _strict_float(document["total_timeout_seconds"], name=f"{name} total timeout")
        widths = tuple(
            _strict_float(width, name=f"{name} multiscale width", lower=0.0)
            for width in _strict_list(document["multiscale_widths_seconds"], name=f"{name} widths")
        )
        actual = (argv, workload_timeout, total_timeout, widths)
        oracle = (
            expected.argv,
            expected.workload_timeout_seconds,
            expected.total_timeout_seconds,
            expected.multiscale_widths_seconds,
        )
        _require(actual == oracle, f"{name} workload definition must equal the exact workload oracle")
    return cast(list[JsonValue], items)


def _validate_protocol(value: object, *, repository_root: Path) -> JsonObject:
    document = _exact_object(value, PROTOCOL_KEYS, name="protocol")
    study_id = validate_study_id(_strict_string(document["study_id"], name="protocol study ID"))
    url = validate_endpoint_url(_strict_string(document["url"], name="protocol URL"))
    _validate_capability(document["capability"], repository_root=repository_root, study_id=study_id, url=url)
    target_reference = _strict_string(document["target_reference"], name="protocol target reference")
    _require(
        target_reference == TARGET_REFERENCE,
        "protocol target reference must equal the approved digest-pinned image",
    )
    _image_id(document["capture_image_id"], name="protocol capture image ID")
    mount_source = _repository_relative_path(
        document["transfer_evidence_mount_source"],
        repository_root=repository_root,
        name="protocol transfer evidence mount source",
    )
    _require(
        mount_source == f"examples/validation_study/.study-work/mount/{study_id}",
        "protocol mount source must equal the exact study mount",
    )
    primary_items = _strict_list(document["primary_order"], name="protocol primary order")
    primary_order = [_validate_run_key(item, name="protocol primary key") for item in primary_items]
    expected_keys = [{"workload": workload, "repeat": repeat} for _, _, workload, repeat in PRIMARY_ORDER]
    _require(
        primary_order == expected_keys,
        "protocol primary order must equal the exact balanced nine-run order",
    )
    families = _string_array(document["families"], name="protocol families")
    methods = _string_array(document["methods"], name="protocol methods")
    _require(families == FAMILY_ORDER, "protocol families must use exact lexical family order")
    _require(methods == PUBLISHED_METHOD_ORDER, "protocol methods must use exact published method order")
    runtime = _strict_string(document["runtime_boundary"], name="protocol runtime boundary")
    _require(
        runtime == RUNTIME_BOUNDARY,
        "protocol runtime boundary must equal the locked full-lifecycle token",
    )
    _sha256(document["prerequisites_sha256"], name="prerequisite file SHA-256")
    _profile_hashes(document["base_config_sha256"])
    _validate_seeds(document["seeds"])
    _validate_workloads(document["workloads"], url=url)
    return cast(JsonObject, document)


def _descriptor_observations(runs: Sequence[JsonObject]) -> dict[str, list[int | float]]:
    references = [cast(dict[str, JsonValue], run["reference"]) for run in runs]
    packet_totals = [cast(dict[str, JsonValue], reference["packet_totals"]) for reference in references]
    byte_totals = [cast(dict[str, JsonValue], reference["byte_totals"]) for reference in references]
    result: dict[str, list[int | float]] = {
        "packet_count": [cast(int, reference["packet_count"]) for reference in references],
        "observation_window_seconds": [
            cast(float, reference["observation_window_seconds"]) for reference in references
        ],
        "outbound_packets": [cast(int, totals["outbound"]) for totals in packet_totals],
        "inbound_packets": [cast(int, totals["inbound"]) for totals in packet_totals],
        "outbound_bytes": [cast(int, totals["outbound"]) for totals in byte_totals],
        "inbound_bytes": [cast(int, totals["inbound"]) for totals in byte_totals],
    }
    return result


def _validate_descriptors(value: object, *, name: str, runs: Sequence[JsonObject]) -> JsonObject:
    document = _exact_object(value, _DESCRIPTOR_KEYS, name=name)
    observations = _descriptor_observations(runs)
    for key in _DESCRIPTOR_KEYS:
        _validate_descriptive(document[key], name=f"{name}.{key}", observations=observations[key])
    return cast(JsonObject, document)


def _average_score(forward: JsonObject, reverse: JsonObject) -> JsonObject:
    forward_methods = cast(dict[str, JsonValue], forward["methods"])
    reverse_methods = cast(dict[str, JsonValue], reverse["methods"])
    return {
        "aggregate": (cast(float, forward["aggregate"]) + cast(float, reverse["aggregate"])) / 2.0,
        "methods": {
            method: (cast(float, forward_methods[method]) + cast(float, reverse_methods[method])) / 2.0
            for method in PUBLISHED_METHOD_ORDER
        },
    }


def _validate_natural_variation(value: object, *, workload: str, runs: Sequence[JsonObject]) -> JsonObject:
    document = _exact_object(value, ("workload", "pairs", "reference_descriptors"), name="natural variation")
    name = _strict_string(document["workload"], name="natural variation workload")
    _require(name == workload, "natural variation records must be ordered short, streaming, bursty")
    pair_items = _strict_list(document["pairs"], name="natural variation pairs")
    expected_pairs = ((1, 2), (1, 3), (2, 3))
    _require(len(pair_items) == 3, "natural variation must contain exactly three unordered repeat pairs")
    for item, expected in zip(pair_items, expected_pairs, strict=True):
        pair = _exact_object(
            item, ("left_repeat", "right_repeat", "forward", "reverse", "symmetric"), name="pair comparison"
        )
        left = _strict_int(pair["left_repeat"], name="left repeat")
        right = _strict_int(pair["right_repeat"], name="right repeat")
        _require(
            (left, right) == expected,
            "natural variation pair order must be (1,2), (1,3), (2,3)",
        )
        forward = _validate_score(pair["forward"], name="forward pair score")
        reverse = _validate_score(pair["reverse"], name="reverse pair score")
        symmetric = _validate_score(pair["symmetric"], name="symmetric pair score")
        _require(
            symmetric == _average_score(forward, reverse),
            "symmetric pair score must be the arithmetic mean of forward and reverse",
        )
    _validate_descriptors(document["reference_descriptors"], name="natural reference descriptors", runs=runs)
    return cast(JsonObject, document)


def _validate_family_summary(
    value: object,
    *,
    family: str,
    champions: Sequence[JsonObject],
) -> JsonObject:
    document = _exact_object(value, ("selection_fitness", "selection_components"), name="family summary")
    fitness_values = [cast(float, champion["selection_fitness"]) for champion in champions]
    component_maps = [
        cast(dict[str, JsonValue], cast(dict[str, JsonValue], champion["selection_score"])["methods"])
        for champion in champions
    ]
    components = _exact_object(
        document["selection_components"], PUBLISHED_METHOD_ORDER, name=f"{family} selection components"
    )
    _validate_descriptive(
        document["selection_fitness"], name=f"{family} selection fitness", observations=fitness_values
    )
    for method in PUBLISHED_METHOD_ORDER:
        _validate_descriptive(
            components[method],
            name=f"{family} selection component {method}",
            observations=[cast(float, values[method]) for values in component_maps],
        )
    return cast(JsonObject, document)


def _validate_workload_summary(value: object, *, workload: str, runs: Sequence[JsonObject]) -> JsonObject:
    document = _exact_object(value, WORKLOAD_SUMMARY_KEYS, name="workload summary")
    name = _strict_string(document["workload"], name="workload summary name")
    _require(name == workload, "workload summaries must be ordered short, streaming, bursty")
    families = _exact_object(document["family_champions"], FAMILY_ORDER, name="family summary map")
    champions_by_family: dict[str, list[JsonObject]] = {family: [] for family in FAMILY_ORDER}
    for run in runs:
        champions = cast(list[JsonValue], run["family_champions"])
        for family, champion in zip(FAMILY_ORDER, champions, strict=True):
            champions_by_family[family].append(cast(JsonObject, champion))
    winners = [cast(dict[str, JsonValue], run["winner"]) for run in runs]
    held_scores = [cast(JsonObject, cast(dict[str, JsonValue], run["fresh_simulation"])["score"]) for run in runs]
    published_scores = [cast(JsonObject, cast(dict[str, JsonValue], run["published"])["score"]) for run in runs]
    counts_document = _exact_object(document["winner_counts"], FAMILY_ORDER, name="winner counts")
    counts = {
        family: _strict_int(counts_document[family], name=f"winner count {family}", minimum=0)
        for family in FAMILY_ORDER
    }
    expected_counts = {family: sum(winner["family"] == family for winner in winners) for family in FAMILY_ORDER}
    _require(
        counts == expected_counts,
        "winner counts must recompute from the three selected winners and sum to three",
    )
    _validate_descriptive(
        document["runtime"],
        name=f"{name} runtime",
        observations=[cast(float, run["elapsed_seconds"]) for run in runs],
    )
    for family in FAMILY_ORDER:
        _validate_family_summary(families[family], family=family, champions=champions_by_family[family])
    _validate_descriptive(
        document["winner_selection_fitness"],
        name=f"{name} winner selection fitness",
        observations=[cast(float, winner["selection_fitness"]) for winner in winners],
    )
    _validate_score_summary(document["fresh_simulation"], name=f"{name} fresh simulation", observations=held_scores)
    _validate_score_summary(document["published"], name=f"{name} published", observations=published_scores)
    _validate_descriptors(document["reference_descriptors"], name=f"{name} reference descriptors", runs=runs)
    return cast(JsonObject, document)


def _validate_delta_score(value: object, *, name: str, reproduction: JsonObject, source: JsonObject) -> JsonObject:
    document = _exact_object(value, ("aggregate", "methods"), name=name)
    aggregate = _strict_float(document["aggregate"], name=f"{name}.aggregate", lower=-1.0, upper=1.0)
    methods_document = _exact_object(document["methods"], PUBLISHED_METHOD_ORDER, name=f"{name}.methods")
    methods = {
        method: _strict_float(methods_document[method], name=f"{name}.{method}", lower=-1.0, upper=1.0)
        for method in PUBLISHED_METHOD_ORDER
    }
    expected_aggregate = cast(float, reproduction["aggregate"]) - cast(float, source["aggregate"])
    reproduction_methods = cast(dict[str, JsonValue], reproduction["methods"])
    source_methods = cast(dict[str, JsonValue], source["methods"])
    expected_methods = {
        method: cast(float, reproduction_methods[method]) - cast(float, source_methods[method])
        for method in PUBLISHED_METHOD_ORDER
    }
    _require(
        aggregate == expected_aggregate and methods == expected_methods,
        f"{name} must recompute as reproduction minus source",
    )
    return cast(JsonObject, document)


def _validate_reproduction_comparison(
    value: object,
    *,
    reproduction: JsonObject,
    source: JsonObject,
) -> JsonObject:
    document = _exact_object(value, REPRODUCTION_COMPARISON_KEYS, name="reproduction comparison")
    source_winner = cast(dict[str, JsonValue], source["winner"])
    reproduction_winner = cast(dict[str, JsonValue], reproduction["winner"])
    family_equal = _strict_bool(document["winner_family_equal"], name="winner family equality")
    genes_equal = _strict_bool(document["winner_genes_equal"], name="winner genes equality")
    expected_family_equal = reproduction_winner["family"] == source_winner["family"]
    expected_genes_equal = reproduction_winner["genes"] == source_winner["genes"]
    _require(
        family_equal == expected_family_equal and genes_equal == expected_genes_equal,
        "winner equality observations must recompute from reproduction and source",
    )
    fitness_delta = _strict_float(
        document["winner_selection_fitness_delta"],
        name="winner selection fitness delta",
        lower=-1.0,
        upper=1.0,
    )
    expected_fitness_delta = cast(float, reproduction_winner["selection_fitness"]) - cast(
        float, source_winner["selection_fitness"]
    )
    _require(
        fitness_delta == expected_fitness_delta,
        "winner selection fitness delta must recompute from reproduction minus source",
    )
    reproduction_held = cast(JsonObject, cast(dict[str, JsonValue], reproduction["fresh_simulation"])["score"])
    source_held = cast(JsonObject, cast(dict[str, JsonValue], source["fresh_simulation"])["score"])
    reproduction_published = cast(JsonObject, cast(dict[str, JsonValue], reproduction["published"])["score"])
    source_published = cast(JsonObject, cast(dict[str, JsonValue], source["published"])["score"])
    _validate_delta_score(
        document["fresh_simulation_delta"],
        name="fresh simulation delta",
        reproduction=reproduction_held,
        source=source_held,
    )
    _validate_delta_score(
        document["published_delta"],
        name="published delta",
        reproduction=reproduction_published,
        source=source_published,
    )
    _validate_score(document["reference_similarity"], name="reproduction reference similarity")
    return cast(JsonObject, document)


def _validate_reproduction(
    value: object,
    *,
    repository_root: Path,
    protocol: JsonObject,
    source: JsonObject,
) -> JsonObject:
    document = _exact_object(value, _REPRODUCTION_KEYS, name="reproduction")
    study_id = cast(str, protocol["study_id"])
    object_size = cast(int, cast(dict[str, JsonValue], protocol["capability"])["object_size_bytes"])
    source_key = _validate_run_key(document["source_key"], name="reproduction source key")
    _require(
        source_key == {"workload": "streaming", "repeat": 2} and source_key == source["key"],
        "reproduction source must be streaming repeat 2",
    )
    order = _strict_int(document["execution_order"], name="reproduction execution order")
    run_id = _strict_string(document["run_id"], name="reproduction run ID")
    _require(
        order == 10 and run_id == "10-streaming-r2-reproduction",
        "reproduction must have execution order 10 and the exact reproduction run ID",
    )
    config_path = _repository_relative_path(
        document["config_path"], repository_root=repository_root, name="reproduction config path"
    )
    run_directory = _repository_relative_path(
        document["run_directory"], repository_root=repository_root, name="reproduction run directory"
    )
    evidence_directory = _repository_relative_path(
        document["transfer_evidence_directory"],
        repository_root=repository_root,
        name="reproduction transfer evidence directory",
    )
    expected_config = f"runs/validation_study/{study_id}/realized-configs/reproduction.toml"
    expected_run = f"runs/validation_study/{study_id}/{run_id}"
    expected_evidence = f"examples/validation_study/.study-work/evidence/{study_id}/{run_id}"
    _require(
        (config_path, run_directory, evidence_directory) == (expected_config, expected_run, expected_evidence),
        "reproduction paths must equal the exact fresh tenth-run paths",
    )
    command = _string_array(document["command"], name="reproduction command")
    expected_command = ("uv", "run", "--locked", "trafficlab", "run", config_path)
    expected_guard = (*_guard_prefix("20m"), *expected_command)
    guard = _string_array(document["guard_command"], name="reproduction guard command")
    _require(
        command == expected_command and guard == expected_guard,
        "reproduction command and guard must equal the exact five-flag installed-CLI command",
    )
    guard_status = _strict_int(document["guard_exit_status"], name="reproduction guard status")
    changed = _string_array(document["changed_config_fields"], name="changed config fields")
    same_config = _strict_bool(document["same_locked_config"], name="same locked config")
    seeded_count = _strict_int(document["seeded_artifact_count"], name="seeded artifact count", minimum=0)
    _require(guard_status == 0, "reproduction guard must succeed")
    _require(
        changed == ("run.directory",) and same_config and seeded_count == 0,
        "reproduction must change only run.directory, seed nothing, and match config",
    )
    _sha256(document["guard_stdout_sha256"], name="guard stdout SHA-256")
    _sha256(document["guard_stderr_sha256"], name="guard stderr SHA-256")
    _validate_run_evidence(
        cast(JsonObject, document),
        repository_root=repository_root,
        workload="streaming",
        evidence_directory=evidence_directory,
        object_size=object_size,
        fresh_simulation_source="post_cli_evaluate_final",
    )
    _validate_reproduction_comparison(
        document["comparison_to_source"], reproduction=cast(JsonObject, document), source=source
    )
    return cast(JsonObject, document)


def _study_run_document(value: StudyRunRecord) -> JsonObject:
    _require_type(type(value) is StudyRunRecord, "study run value must be StudyRunRecord")
    return {
        "execution_order": value.execution_order,
        "run_id": value.run_id,
        "key": _thaw_json(value.key),
        "config_path": value.config_path,
        "run_directory": value.run_directory,
        "transfer_evidence_directory": value.transfer_evidence_directory,
        "elapsed_seconds": value.elapsed_seconds,
        "reuse": _thaw_json(value.reuse),
        "cleanup_verified": value.cleanup_verified,
        "transfer_responses": [_thaw_json(item) for item in value.transfer_responses],
        "artifact_sha256": _thaw_json(value.artifact_sha256),
        "reference": _thaw_json(value.reference),
        "generated": _thaw_json(value.generated),
        "family_champions": [_thaw_json(item) for item in value.family_champions],
        "winner": _thaw_json(value.winner),
        "fresh_simulation": _thaw_json(value.fresh_simulation),
        "published": _thaw_json(value.published),
        "raw_sequence": _thaw_json(value.raw_sequence),
    }


def _reproduction_document(value: ReproductionRecord) -> JsonObject:
    _require_type(type(value) is ReproductionRecord, "reproduction value must be ReproductionRecord")
    return cast(JsonObject, _thaw_json(value.document))


def _study_document(value: StudyResults) -> JsonObject:
    _require_type(type(value) is StudyResults, "study result value must be StudyResults")
    return {
        "schema_version": value.schema_version,
        "environment": _thaw_json(value.environment),
        "protocol": _thaw_json(value.protocol),
        "runs": [_study_run_document(run) for run in value.runs],
        "natural_variation": [_thaw_json(item) for item in value.natural_variation],
        "workload_summaries": [_thaw_json(item) for item in value.workload_summaries],
        "reproduction": _reproduction_document(value.reproduction),
    }


def _run_record_from_document(document: JsonObject) -> StudyRunRecord:
    champions = tuple(
        _freeze_object(cast(JsonObject, item)) for item in cast(list[JsonValue], document["family_champions"])
    )
    return StudyRunRecord(
        execution_order=cast(int, document["execution_order"]),
        run_id=cast(str, document["run_id"]),
        key=_freeze_object(cast(JsonObject, document["key"])),
        config_path=cast(str, document["config_path"]),
        run_directory=cast(str, document["run_directory"]),
        transfer_evidence_directory=cast(str, document["transfer_evidence_directory"]),
        elapsed_seconds=cast(float, document["elapsed_seconds"]),
        reuse=_freeze_object(cast(JsonObject, document["reuse"])),
        cleanup_verified=cast(bool, document["cleanup_verified"]),
        transfer_responses=tuple(
            _freeze_object(cast(JsonObject, item)) for item in cast(list[JsonValue], document["transfer_responses"])
        ),
        artifact_sha256=_freeze_object(cast(JsonObject, document["artifact_sha256"])),
        reference=_freeze_object(cast(JsonObject, document["reference"])),
        generated=_freeze_object(cast(JsonObject, document["generated"])),
        family_champions=cast(tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject], champions),
        winner=_freeze_object(cast(JsonObject, document["winner"])),
        fresh_simulation=_freeze_object(cast(JsonObject, document["fresh_simulation"])),
        published=_freeze_object(cast(JsonObject, document["published"])),
        raw_sequence=_freeze_object(cast(JsonObject, document["raw_sequence"])),
    )


def _validate_study_document(document: JsonObject, *, repository_root: Path) -> StudyResults:
    root = _exact_object(document, _RESULT_ROOT_KEYS, name="result root")
    schema_version = _strict_int(root["schema_version"], name="result schema version")
    _require(schema_version == 1, "result schema version must be exactly 1")
    environment = _validate_environment(root["environment"])
    protocol = _validate_protocol(root["protocol"], repository_root=repository_root)
    _require(
        environment["capture_image_id"] == protocol["capture_image_id"],
        "environment and protocol capture image IDs must match",
    )
    run_items = _strict_list(root["runs"], name="primary runs")
    _require(len(run_items) == 9, "results must contain exactly nine primary runs")
    object_size = cast(int, cast(dict[str, JsonValue], protocol["capability"])["object_size_bytes"])
    study_id = cast(str, protocol["study_id"])
    validated_runs = [
        _validate_run_document(
            item,
            expected=expected,
            repository_root=repository_root,
            study_id=study_id,
            object_size=object_size,
        )
        for item, expected in zip(run_items, PRIMARY_ORDER, strict=True)
    ]
    grouped = {
        workload: sorted(
            [run for run in validated_runs if cast(dict[str, JsonValue], run["key"])["workload"] == workload],
            key=lambda run: cast(int, cast(dict[str, JsonValue], run["key"])["repeat"]),
        )
        for workload in ("short", "streaming", "bursty")
    }
    natural_items = _strict_list(root["natural_variation"], name="natural variation records")
    summary_items = _strict_list(root["workload_summaries"], name="workload summaries")
    _require(
        len(natural_items) == 3 and len(summary_items) == 3,
        "natural variation and workload summaries must each contain three workloads",
    )
    workloads = ("short", "streaming", "bursty")
    natural = [
        _validate_natural_variation(item, workload=workload, runs=grouped[workload])
        for item, workload in zip(natural_items, workloads, strict=True)
    ]
    summaries = [
        _validate_workload_summary(item, workload=workload, runs=grouped[workload])
        for item, workload in zip(summary_items, workloads, strict=True)
    ]
    source = grouped["streaming"][1]
    reproduction = _validate_reproduction(
        root["reproduction"], repository_root=repository_root, protocol=protocol, source=source
    )
    return StudyResults(
        schema_version=schema_version,
        environment=_freeze_object(environment),
        protocol=_freeze_object(protocol),
        runs=tuple(_run_record_from_document(run) for run in validated_runs),
        natural_variation=cast(
            tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject],
            tuple(_freeze_object(item) for item in natural),
        ),
        workload_summaries=cast(
            tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject],
            tuple(_freeze_object(item) for item in summaries),
        ),
        reproduction=ReproductionRecord(_freeze_object(reproduction)),
    )


def render_study_results(value: StudyResults) -> bytes:
    document = _study_document(value)
    validated = _validate_study_document(document, repository_root=REPOSITORY_ROOT)
    return _canonical_json(_study_document(validated))


def parse_study_results(content: bytes, *, repository_root: Path) -> StudyResults:
    document = _load_json(content)
    result = _validate_study_document(document, repository_root=repository_root)
    if _canonical_json(_study_document(result)) != content:
        raise ValueError("study results JSON must use canonical sorted compact encoding with one trailing newline")
    return result


def _publish_results(  # pyright: ignore[reportUnusedFunction]
    path: Path, value: StudyResults, *, repository_root: Path
) -> None:
    content = render_study_results(value)

    def validate(persisted: bytes) -> None:
        parsed = parse_study_results(persisted, repository_root=repository_root)
        if render_study_results(parsed) != content:
            raise ValueError("persisted study results JSON is not canonical")

    _publish_support_json(path, content, validate=validate)


def _study_git_status_is_permitted(content: bytes) -> bool:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return False
    permitted = {
        "examples/validation_study/prerequisites.json",
        "examples/validation_study/configs/short.toml",
        "examples/validation_study/configs/streaming.toml",
        "examples/validation_study/configs/bursty.toml",
    }
    return all(line.startswith("?? ") and line[3:] in permitted for line in lines)


def _study_identity(
    *,
    repository_root: Path,
    runner: CommandRunner,
) -> JsonObject:
    commands = (
        (("git", "rev-parse", "HEAD"), "Git commit inspection"),
        (("git", "status", "--porcelain=v1", "--untracked-files=all"), "Git tree inspection"),
        (("docker", "version", "--format", "{{.Server.Version}}"), "Docker version"),
        (("docker", "compose", "version", "--short"), "Docker Compose version"),
    )
    completed: list[subprocess.CompletedProcess[bytes]] = []
    for argv, operation in commands:
        result = runner(
            argv,
            cwd=repository_root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        )
        _require(
            result.returncode == 0, f"could not complete {operation}: {_command_detail(result, operation=operation)}"
        )
        completed.append(result)
    commit_result, status_result, docker_result, compose_result = completed
    status_stdout, _status_stderr = _completed_output(status_result, operation="Git tree inspection")
    _require(
        _study_git_status_is_permitted(status_stdout),
        "study checkout may differ only by the generated Validation Study prerequisite and checked base configs",
    )
    return {
        "git_commit": _git_commit(_stdout_text(commit_result, operation="Git commit inspection")),
        "python_version": platform.python_version(),
        "trafficlab_version": __version__,
        "docker_engine_version": _strict_string(
            _stdout_text(docker_result, operation="Docker version"), name="Docker Engine version"
        ),
        "docker_compose_version": _strict_string(
            _stdout_text(compose_result, operation="Docker Compose version"), name="Docker Compose version"
        ),
        "platform": platform.platform(),
    }


def _study_image_identity(
    *,
    repository_root: Path,
    capture_image_id: str,
    runner: CommandRunner,
) -> tuple[JsonObject, str]:
    target_result = runner(
        ("docker", "image", "inspect", TARGET_REFERENCE),
        cwd=repository_root,
        check=False,
        capture_output=True,
        shell=False,
        timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
    )
    _require(
        target_result.returncode == 0,
        f"could not inspect live target image: {_command_detail(target_result, operation='target image inspect')}",
    )
    target_stdout, _target_stderr = _completed_output(target_result, operation="target image inspect")
    capture_result = runner(
        ("docker", "image", "inspect", capture_image_id),
        cwd=repository_root,
        check=False,
        capture_output=True,
        shell=False,
        timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
    )
    _require(
        capture_result.returncode == 0,
        f"could not inspect live capture image: {_command_detail(capture_result, operation='capture image inspect')}",
    )
    capture_stdout, _capture_stderr = _completed_output(capture_result, operation="capture image inspect")
    return _target_image_record(target_stdout), _inspected_image_id(capture_stdout, name="capture")


def _validated_study_inputs(
    url: str,
    study_id: str,
    prerequisite_path: Path,
    *,
    repository_root: Path,
    runner: CommandRunner,
) -> tuple[PrerequisiteResults, dict[WorkloadName, ExperimentConfig], JsonObject, bytes]:
    root = repository_root.resolve()
    expected_path = root / "examples" / "validation_study" / "prerequisites.json"
    _require(prerequisite_path.resolve() == expected_path, "study prerequisite path must use its exact checked path")
    try:
        prerequisite_content = prerequisite_path.read_bytes()
    except OSError as error:
        raise ValueError(f"could not read Validation Study prerequisites: {error}") from error
    prerequisites = parse_prerequisite_results(prerequisite_content, repository_root=root)
    _validate_prerequisite_evidence(root, prerequisites)
    _require(
        prerequisites.study_id == study_id and prerequisites.url == url,
        "study ID and URL must exactly match prerequisite evidence",
    )
    identity = _study_identity(repository_root=root, runner=runner)
    tools = prerequisites.tools
    _require(
        identity["git_commit"] == prerequisites.git_commit
        and identity["python_version"] == tools["python_version"]
        and identity["trafficlab_version"] == tools["trafficlab_version"]
        and identity["docker_engine_version"] == tools["docker_engine_version"]
        and identity["docker_compose_version"] == tools["docker_compose_version"]
        and identity["platform"] == tools["platform"],
        "live study commit and tool identities must exactly match prerequisite evidence",
    )
    images = prerequisites.images
    capture_image_id = cast(str, images["capture_image_id"])
    live_target, live_capture_image_id = _study_image_identity(
        repository_root=root,
        capture_image_id=capture_image_id,
        runner=runner,
    )
    _require(
        live_target["target_reference"] == images["target_reference"]
        and live_target["target_image_id"] == images["target_image_id"]
        and tuple(cast(list[JsonValue], live_target["target_repo_digests"])) == images["target_repo_digests"]
        and live_target["target_config_user"] == images["target_config_user"]
        and live_capture_image_id == capture_image_id,
        "study image identities must exactly match approved prerequisite evidence",
    )
    configs = validate_base_configs(root, prerequisites)
    return prerequisites, configs, identity, prerequisite_content


def _primary_run_specs(
    repository_root: Path,
    study_id: str,
    configs: Mapping[WorkloadName, ExperimentConfig],
) -> tuple[StudyRunSpec, ...]:
    root = repository_root.resolve()
    specs: list[StudyRunSpec] = []
    for order, run_id, workload_value, repeat in PRIMARY_ORDER:
        workload = cast(WorkloadName, workload_value)
        run_directory = root / "runs" / "validation_study" / study_id / run_id
        config_path = root / "runs" / "validation_study" / study_id / "realized-configs" / f"{run_id}.toml"
        evidence_directory = root / "examples" / "validation_study" / ".study-work" / "evidence" / study_id / run_id
        for path, name in (
            (run_directory, "primary run directory"),
            (config_path, "primary realized config"),
            (evidence_directory, "primary transfer evidence directory"),
        ):
            _require(not _path_entry_exists(path), f"{name} already exists: {path}")
        expected_config = _config_with_run_directory(configs[workload], run_directory)
        _require(expected_config.run.directory == run_directory, "primary run directory must be exact")
        specs.append(StudyRunSpec(order, run_id, workload, repeat, config_path, run_directory, evidence_directory))
    return tuple(specs)


def _load_reference_trace(run_directory: Path) -> tuple[TraceEvent, ...]:
    capture_path = run_directory / "capture.json"
    reference_path = run_directory / "reference.pcapng"
    validate_capture_pair(capture_path, reference_path, deadline=None)
    metadata = parse_capture_metadata(capture_path.read_bytes(), source=capture_path)
    return parse_pcapng_bytes(reference_path.read_bytes(), metadata, source=reference_path)


def _validate_primary_derived_records(
    records: Sequence[StudyRunRecord],
    variation: Sequence[JsonObject | FrozenJsonObject],
    summaries: Sequence[JsonObject | FrozenJsonObject],
) -> tuple[
    tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject],
    tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject],
]:
    grouped = _group_run_documents(records)
    _require(len(variation) == 3 and len(summaries) == 3, "study derived records must contain three workloads")
    validated_variation: list[FrozenJsonObject] = []
    validated_summaries: list[FrozenJsonObject] = []
    for index, workload in enumerate(_WORKLOAD_ORDER):
        variation_document = cast(JsonObject, _thaw_json(cast(FrozenJsonValue, variation[index])))
        summary_document = cast(JsonObject, _thaw_json(cast(FrozenJsonValue, summaries[index])))
        validated_variation.append(
            _freeze_object(_validate_natural_variation(variation_document, workload=workload, runs=grouped[workload]))
        )
        validated_summaries.append(
            _freeze_object(_validate_workload_summary(summary_document, workload=workload, runs=grouped[workload]))
        )
    return (
        cast(tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject], tuple(validated_variation)),
        cast(tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject], tuple(validated_summaries)),
    )


def _environment_record(prerequisites: PrerequisiteResults, identity: JsonObject, created: str) -> FrozenJsonObject:
    images = prerequisites.images
    return _freeze_object(
        {
            **identity,
            "target_image_id": cast(str, images["target_image_id"]),
            "capture_image_id": cast(str, images["capture_image_id"]),
            "study_date_utc": created,
        }
    )


def _protocol_record(
    prerequisites: PrerequisiteResults,
    prerequisite_content: bytes,
) -> FrozenJsonObject:
    specs = workload_specs(prerequisites.url)
    return _freeze_object(
        {
            "study_id": prerequisites.study_id,
            "url": prerequisites.url,
            "capability": _thaw_json(prerequisites.capability),
            "prerequisites_sha256": sha256_bytes(prerequisite_content),
            "target_reference": TARGET_REFERENCE,
            "capture_image_id": cast(str, prerequisites.images["capture_image_id"]),
            "transfer_evidence_mount_source": (f"examples/validation_study/.study-work/mount/{prerequisites.study_id}"),
            "base_config_sha256": _thaw_json(prerequisites.config_sha256),
            "primary_order": [
                {"workload": workload, "repeat": repeat} for _order, _run_id, workload, repeat in PRIMARY_ORDER
            ],
            "seeds": {"master": 73, "final": 97, "selection": [17, 29]},
            "families": list(FAMILY_ORDER),
            "methods": list(PUBLISHED_METHOD_ORDER),
            "workloads": [
                {
                    "name": spec.name,
                    "argv": list(spec.argv),
                    "workload_timeout_seconds": spec.workload_timeout_seconds,
                    "total_timeout_seconds": spec.total_timeout_seconds,
                    "multiscale_widths_seconds": list(spec.multiscale_widths_seconds),
                }
                for spec in specs
            ],
            "runtime_boundary": RUNTIME_BOUNDARY,
        }
    )


def _run_cli_reproduction(
    repository_root: Path,
    study_id: str,
    config: ExperimentConfig,
    source: StudyRunRecord,
    workload: WorkloadSpec,
    *,
    object_size_bytes: int,
    runner: CommandRunner,
    perf_counter: Callable[[], float],
) -> ReproductionRecord:
    root = repository_root.resolve()
    run_id = "10-streaming-r2-reproduction"
    run_directory = root / "runs" / "validation_study" / study_id / run_id
    config_path = root / "runs" / "validation_study" / study_id / "realized-configs" / "reproduction.toml"
    evidence_directory = root / "examples" / "validation_study" / ".study-work" / "evidence" / study_id / run_id
    prepared: Mapping[str, tuple[Path, int]] = {}
    try:
        _require(
            source.key == {"workload": "streaming", "repeat": 2} and workload.name == "streaming",
            "reproduction source must be preselected streaming repeat 2",
        )
        source_snapshot = root / source.run_directory / "experiment.toml"
        source_config = load_experiment(source_snapshot)
        expected_source_run = root / source.run_directory
        expected_source = config.model_copy(
            update={"run": config.run.model_copy(update={"directory": expected_source_run})}
        )
        _require(source_config == expected_source, "saved reproduction source config must equal the locked base config")
        for path, name in (
            (run_directory, "reproduction run directory"),
            (config_path, "reproduction config"),
            (evidence_directory, "reproduction evidence directory"),
        ):
            _require(not _path_entry_exists(path), f"{name} already exists: {path}")
        reproduction_config = source_config.model_copy(
            update={"run": source_config.run.model_copy(update={"directory": run_directory})}
        )
        _render_realized_config(reproduction_config, config_path)
        reloaded = load_experiment(config_path)
        _require(
            reloaded == reproduction_config
            and reloaded.run.model_copy(update={"directory": source_config.run.directory}) == source_config.run
            and reloaded.model_copy(update={"run": source_config.run}) == source_config,
            "reproduction config must change only run.directory",
        )
        _require(not _path_entry_exists(run_directory), "reproduction run directory must remain absent before CLI")
        config_record = _repository_path_record(config_path, repository_root=root, name="reproduction config path")
        command = ("uv", "run", "--locked", "trafficlab", "run", config_record)
        guard_command = (*_guard_prefix("20m"), *command)
        prepared = prepare_transfer_scratch(root, study_id, run_id, workload)
        _require(not _path_entry_exists(run_directory), "reproduction must seed no stage artifact before CLI")
        started = perf_counter()
        completed = runner(
            guard_command,
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["reproduction_guard"],
        )
        elapsed = perf_counter() - started
        stdout, stderr = _completed_output(completed, operation="reproduction guard")
        _private_bytes(evidence_directory / "guard.stdout", stdout)
        _private_bytes(evidence_directory / "guard.stderr", stderr)
        _require(
            completed.returncode == 0,
            f"reproduction guard failed with status {completed.returncode}: "
            f"{_command_detail(completed, operation='reproduction guard')}",
        )
        responses = archive_transfer_evidence(
            root,
            study_id,
            run_id,
            workload,
            prepared,
            object_size_bytes=object_size_bytes,
        )
        spec = StudyRunSpec(10, run_id, "streaming", 2, config_path, run_directory, evidence_directory)
        return reconstruct_reproduction(
            root,
            spec,
            source,
            command=command,
            guard_command=guard_command,
            completed=completed,
            elapsed_seconds=elapsed,
            transfer_responses=responses,
        )
    except Exception as error:
        archive_diagnostic = _best_effort_archive(evidence_directory, prepared)
        secondary = f"; secondary evidence archive failure: {archive_diagnostic}" if archive_diagnostic else ""
        raise TrafficlabError(
            f"Validation Study reproduction failed for workload streaming, repeat 2, position 10, raw run path "
            f"{_repository_path_record(run_directory, repository_root=root, name='failed reproduction path')}: "
            f"{error}{secondary}",
            corrective_action="preserve the failed evidence and restart the balanced protocol with a new study ID",
        ) from error


def _checkpoint_winner(evidence: _LoadedRunEvidence) -> Candidate:
    candidates = tuple(
        candidate
        for candidate in evidence.checkpoint.population
        if candidate.identifier == evidence.checkpoint.best_identifier
    )
    _require(len(candidates) == 1, "terminal checkpoint must contain exactly one reproduction winner")
    candidate = candidates[0]
    _winner(evidence.checkpoint, evidence.best_model)
    return candidate


def _score_delta(reproduction: JsonObject, source: JsonObject) -> JsonObject:
    reproduction_methods = cast(JsonObject, reproduction["methods"])
    source_methods = cast(JsonObject, source["methods"])
    return {
        "aggregate": cast(float, reproduction["aggregate"]) - cast(float, source["aggregate"]),
        "methods": {
            method: cast(float, reproduction_methods[method]) - cast(float, source_methods[method])
            for method in PUBLISHED_METHOD_ORDER
        },
    }


def _symmetric_reference_score(
    source: Sequence[TraceEvent],
    reproduction: Sequence[TraceEvent],
    settings: SimilarityConfig,
) -> JsonObject:
    scores: list[JsonObject] = []
    for reference_events, generated_events in ((source, reproduction), (reproduction, source)):
        reference, window = normalize_reference(reference_events)
        generated = align_generated(generated_events, window)
        scores.append(_score_from_comparison(compare_traces(reference, generated, window, settings)))
    return _average_score(scores[0], scores[1])


def reconstruct_reproduction(
    repository_root: Path,
    spec: StudyRunSpec,
    source: StudyRunRecord,
    *,
    command: tuple[str, ...],
    guard_command: tuple[str, ...],
    completed: subprocess.CompletedProcess[bytes],
    elapsed_seconds: float,
    transfer_responses: tuple[JsonObject, ...],
) -> ReproductionRecord:
    root = repository_root.resolve()
    _require(
        (spec.execution_order, spec.run_id, spec.workload, spec.repeat)
        == (10, "10-streaming-r2-reproduction", "streaming", 2),
        "reproduction spec must equal the exact fresh tenth run",
    )
    _require(source.key == {"workload": "streaming", "repeat": 2}, "reproduction source must be streaming repeat 2")
    _require(completed.returncode == 0, "reproduction guard must succeed before reconstruction")
    elapsed = _strict_float(elapsed_seconds, name="reproduction elapsed seconds", lower=0.0)
    _require(elapsed > 0.0, "reproduction elapsed seconds must be positive")
    evidence = _load_persisted_run_evidence(spec)
    source_config = load_experiment(root / source.run_directory / "experiment.toml")
    expected_config = source_config.model_copy(
        update={"run": source_config.run.model_copy(update={"directory": spec.run_directory})}
    )
    _require(
        evidence.config == expected_config,
        "reproduction retained config must differ from its saved source only by run.directory",
    )
    _fresh_run_log_proofs(evidence.log_records)
    candidate = _checkpoint_winner(evidence)
    validated_context = validate_evaluation_context(evidence.context.evaluation)
    fresh_simulation = _sole_final_trial(evaluate_final(candidate, validated_context, 97))
    science = _reconstruct_science(evidence, fresh_simulation, generated_path=spec.run_directory / "generated.pcapng")
    window = evidence.best_model.observation_window_seconds

    config_path = _repository_path_record(spec.config_path, repository_root=root, name="reproduction config path")
    run_directory = _repository_path_record(spec.run_directory, repository_root=root, name="reproduction run directory")
    evidence_directory = _repository_path_record(
        spec.transfer_evidence_directory,
        repository_root=root,
        name="reproduction transfer evidence directory",
    )
    object_size = _validate_transfer_archives(
        root,
        transfer_responses,
        workload="streaming",
        evidence_directory=evidence_directory,
    )
    _require(object_size >= 4 * 1024 * 1024, "reproduction transfer must retain the prerequisite object size")
    winner = _winner(evidence.checkpoint, evidence.best_model)
    fresh_simulation_score = _score_from_trial(science.fresh_simulation)
    published_score = _score_from_comparison(science.published)
    source_document = _study_run_document(source)
    source_winner = cast(JsonObject, source_document["winner"])
    source_fresh_simulation = cast(JsonObject, cast(JsonObject, source_document["fresh_simulation"])["score"])
    source_published = cast(JsonObject, cast(JsonObject, source_document["published"])["score"])
    source_reference = _load_reference_trace(root / source.run_directory)
    stdout, stderr = _completed_output(completed, operation="reproduction guard")
    document: JsonObject = {
        "source_key": {"workload": "streaming", "repeat": 2},
        "execution_order": 10,
        "run_id": spec.run_id,
        "config_path": config_path,
        "run_directory": run_directory,
        "transfer_evidence_directory": evidence_directory,
        "command": list(command),
        "guard_command": list(guard_command),
        "guard_exit_status": completed.returncode,
        "guard_stdout_sha256": sha256_bytes(stdout),
        "guard_stderr_sha256": sha256_bytes(stderr),
        "elapsed_seconds": elapsed,
        "changed_config_fields": ["run.directory"],
        "same_locked_config": True,
        "seeded_artifact_count": 0,
        "cleanup_verified": True,
        "reuse": {"capture": False, "best_model": False, "generated": False, "similarity": False},
        "transfer_responses": list(transfer_responses),
        "artifact_sha256": evidence.artifact_sha256,
        "reference": _trace_summary(evidence.reference, science.published, role="reference"),
        "generated": _trace_summary(science.aligned_events, science.published, role="generated"),
        "family_champions": list(_family_champions(evidence.checkpoint)),
        "winner": winner,
        "fresh_simulation": {"seed": 97, "score": fresh_simulation_score, "source": "post_cli_evaluate_final"},
        "published": {"seed": 97, "score": published_score},
        "raw_sequence": {
            "seed": 97,
            "observation_window_seconds": window,
            "trial_event_count": len(science.raw_events),
            "final_event_count": len(science.raw_events),
            "raw_events_equal": True,
            "fresh_simulation_score_reproduced": True,
            "reparsed_event_count": len(science.reparsed_events),
            "reparsed_matches_quantized": True,
        },
        "comparison_to_source": {
            "winner_family_equal": winner["family"] == source_winner["family"],
            "winner_genes_equal": winner["genes"] == source_winner["genes"],
            "winner_selection_fitness_delta": cast(float, winner["selection_fitness"])
            - cast(float, source_winner["selection_fitness"]),
            "fresh_simulation_delta": _score_delta(fresh_simulation_score, source_fresh_simulation),
            "published_delta": _score_delta(published_score, source_published),
            "reference_similarity": _symmetric_reference_score(
                source_reference, evidence.reference, evidence.config.similarity
            ),
        },
    }
    _validate_run_evidence(
        document,
        repository_root=root,
        workload="streaming",
        evidence_directory=evidence_directory,
        object_size=object_size,
        fresh_simulation_source="post_cli_evaluate_final",
    )
    _validate_reproduction_comparison(document["comparison_to_source"], reproduction=document, source=source_document)
    return ReproductionRecord(_freeze_object(document))


def run_study(
    url: str,
    study_id: str,
    prerequisite_path: Path,
    *,
    repository_root: Path,
    run: Callable[[Path], RunResult],
    runner: CommandRunner,
    perf_counter: Callable[[], float],
    utc_now: Callable[[], datetime],
) -> StudyResults:
    root = repository_root.resolve()
    try:
        url = validate_endpoint_url(url)
        study_id = validate_study_id(study_id)
        results_path = root / "examples" / "validation_study" / "results.json"
        _require(not _path_entry_exists(results_path), f"study result target already exists: {results_path}")
        prerequisites, configs, identity, prerequisite_content = _validated_study_inputs(
            url,
            study_id,
            prerequisite_path,
            repository_root=root,
            runner=runner,
        )
        specifications = _primary_run_specs(root, study_id, configs)
        workloads = {spec.name: spec for spec in workload_specs(url)}
        object_size = cast(int, prerequisites.capability["object_size_bytes"])
        records: list[StudyRunRecord] = []
        traces: dict[tuple[WorkloadName, int], tuple[TraceEvent, ...]] = {}
        settings: dict[WorkloadName, SimilarityConfig] = {}
        for spec in specifications:
            workload = workloads[spec.workload]
            prepared: Mapping[str, tuple[Path, int]] = {}
            try:
                config = _config_with_run_directory(configs[spec.workload], spec.run_directory)
                _render_realized_config(config, spec.config_path)
                prepared = prepare_transfer_scratch(root, study_id, spec.run_id, workload)
                started = perf_counter()
                result = run(spec.config_path)
                elapsed = perf_counter() - started
                responses = archive_transfer_evidence(
                    root,
                    study_id,
                    spec.run_id,
                    workload,
                    prepared,
                    object_size_bytes=object_size,
                )
                record = extract_primary_record(root, spec, workload, result, elapsed, responses)
                document = _study_run_document(record)
                _validate_run_document(
                    document,
                    expected=PRIMARY_ORDER[spec.execution_order - 1],
                    repository_root=root,
                    study_id=study_id,
                    object_size=object_size,
                )
                records.append(record)
                traces[(spec.workload, spec.repeat)] = _load_reference_trace(spec.run_directory)
                settings[spec.workload] = config.similarity
            except Exception as error:
                archive_diagnostic = _best_effort_archive(spec.transfer_evidence_directory, prepared)
                secondary = f"; secondary evidence archive failure: {archive_diagnostic}" if archive_diagnostic else ""
                raise TrafficlabError(
                    f"Validation Study primary failed for workload {spec.workload}, repeat {spec.repeat}, "
                    f"position {spec.execution_order}, raw run path "
                    f"{_repository_path_record(spec.run_directory, repository_root=root, name='failed run path')}; "
                    f"restart with a new study ID: {error}{secondary}",
                    corrective_action="preserve the failed evidence and restart the balanced protocol with a new study ID",
                ) from error

        variation_values = natural_variation(records, traces, settings)
        summary_values = workload_summaries(records)
        validated_variation, validated_summaries = _validate_primary_derived_records(
            records, variation_values, summary_values
        )
        reproduction = _run_cli_reproduction(
            root,
            study_id,
            configs["streaming"],
            records[3],
            workloads["streaming"],
            object_size_bytes=object_size,
            runner=runner,
            perf_counter=perf_counter,
        )
        created = _timestamp_now(utc_now)
        result = StudyResults(
            schema_version=1,
            environment=_environment_record(prerequisites, identity, created),
            protocol=_protocol_record(prerequisites, prerequisite_content),
            runs=tuple(records),
            natural_variation=validated_variation,
            workload_summaries=validated_summaries,
            reproduction=reproduction,
        )
        validated = _validate_study_document(_study_document(result), repository_root=root)
        results_path.parent.mkdir(parents=True, exist_ok=True)
        _publish_results(results_path, validated, repository_root=root)
        return validated
    except TrafficlabError:
        raise
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        raise TrafficlabError(
            f"Validation Study failed validation: {error}",
            corrective_action="preserve the ignored evidence, correct the failure, and restart with a new study ID",
        ) from error


def _audit_primary_record(
    repository_root: Path,
    record: StudyRunRecord,
    object_size_bytes: int,
) -> tuple[TraceEvent, ...]:
    root = repository_root.resolve()
    key = record.key
    workload_name = cast(WorkloadName, key["workload"])
    spec = StudyRunSpec(
        record.execution_order,
        record.run_id,
        workload_name,
        cast(int, key["repeat"]),
        root / record.config_path,
        root / record.run_directory,
        root / record.transfer_evidence_directory,
    )
    evidence = _load_persisted_run_evidence(spec)
    _fresh_run_log_proofs(evidence.log_records)
    _require(evidence.artifact_sha256 == record.artifact_sha256, "primary artifact hashes must match retained files")
    responses = tuple(cast(JsonObject, _thaw_json(item)) for item in record.transfer_responses)
    observed_size = _validate_transfer_archives(
        root,
        responses,
        workload=workload_name,
        evidence_directory=record.transfer_evidence_directory,
    )
    _require(observed_size == object_size_bytes, "primary transfer object size must match prerequisite capability")

    candidate = _checkpoint_winner(evidence)
    fresh_simulation = _sole_final_trial(
        evaluate_final(candidate, validate_evaluation_context(evidence.context.evaluation), 97)
    )
    science = _reconstruct_science(evidence, fresh_simulation, generated_path=spec.run_directory / "generated.pcapng")
    window = evidence.best_model.observation_window_seconds
    expected = {
        "reference": _trace_summary(evidence.reference, science.published, role="reference"),
        "generated": _trace_summary(science.aligned_events, science.published, role="generated"),
        "family_champions": list(_family_champions(evidence.checkpoint)),
        "winner": _winner(evidence.checkpoint, evidence.best_model),
        "fresh_simulation": {
            "seed": 97,
            "score": _score_from_trial(science.fresh_simulation),
            "source": "run_experiment_fit_outcome",
        },
        "published": {"seed": 97, "score": _score_from_comparison(science.published)},
        "raw_sequence": {
            "seed": 97,
            "observation_window_seconds": window,
            "trial_event_count": len(science.raw_events),
            "final_event_count": len(science.raw_events),
            "raw_events_equal": True,
            "fresh_simulation_score_reproduced": True,
            "reparsed_event_count": len(science.reparsed_events),
            "reparsed_matches_quantized": True,
        },
    }
    document = _study_run_document(record)
    for name, value in expected.items():
        _require(document[name] == value, f"primary {name} must match locally reconstructed evidence")
    return evidence.reference


def _audit_reproduction_record(
    repository_root: Path,
    results: StudyResults,
) -> None:
    root = repository_root.resolve()
    document = cast(JsonObject, _thaw_json(results.reproduction.document))
    source = results.runs[3]
    spec = StudyRunSpec(
        10,
        cast(str, document["run_id"]),
        "streaming",
        2,
        root / cast(str, document["config_path"]),
        root / cast(str, document["run_directory"]),
        root / cast(str, document["transfer_evidence_directory"]),
    )
    stdout_path = spec.transfer_evidence_directory / "guard.stdout"
    stderr_path = spec.transfer_evidence_directory / "guard.stderr"
    try:
        stdout = stdout_path.read_bytes()
        stderr = stderr_path.read_bytes()
    except OSError as error:
        raise ValueError(f"could not read retained reproduction guard output: {error}") from error
    _require(
        stat.S_IMODE(stdout_path.lstat().st_mode) == stat.S_IMODE(stderr_path.lstat().st_mode) == 0o600,
        "reproduction guard output must retain mode 0600",
    )
    completed = subprocess.CompletedProcess(
        tuple(cast(list[str], document["guard_command"])),
        cast(int, document["guard_exit_status"]),
        stdout=stdout,
        stderr=stderr,
    )
    reconstructed = reconstruct_reproduction(
        root,
        spec,
        source,
        command=tuple(cast(list[str], document["command"])),
        guard_command=tuple(cast(list[str], document["guard_command"])),
        completed=completed,
        elapsed_seconds=cast(float, document["elapsed_seconds"]),
        transfer_responses=tuple(
            cast(JsonObject, item) for item in cast(list[JsonValue], document["transfer_responses"])
        ),
    )
    _require(reconstructed == results.reproduction, "reproduction must match local read-only reconstruction")


def _require_report_evidence(
    content: str,
    prerequisites: PrerequisiteResults,
    results: StudyResults,
) -> None:
    identifiers = (
        prerequisites.study_id,
        prerequisites.git_commit,
        cast(str, prerequisites.images["target_image_id"]),
        cast(str, prerequisites.images["capture_image_id"]),
        *(record.run_id for record in results.runs),
        cast(str, results.reproduction.document["run_id"]),
    )
    _require(all(heading in content for heading in REPORT_HEADINGS), "report must contain all seven required headings")
    _require(
        all(identifier in content for identifier in identifiers), "report must identify the study and all ten runs"
    )


def audit_published_study(
    *,
    repository_root: Path,
    prerequisite_path: Path,
    result_path: Path,
    report_path: Path,
) -> None:
    root = repository_root.resolve()
    try:
        expected_paths = (
            (prerequisite_path, root / "examples" / "validation_study" / "prerequisites.json", "prerequisite"),
            (result_path, root / "examples" / "validation_study" / "results.json", "result"),
            (report_path, root / "examples" / "validation_study" / "REPORT.md", "report"),
        )
        for path, expected, name in expected_paths:
            _require(path.resolve() == expected, f"audit {name} path must use its exact checked location")
        prerequisite_content = prerequisite_path.read_bytes()
        result_content = result_path.read_bytes()
        report_content = report_path.read_text(encoding="utf-8")
        prerequisites = parse_prerequisite_results(prerequisite_content, repository_root=root)
        results = parse_study_results(result_content, repository_root=root)
        _validate_prerequisite_evidence(root, prerequisites)
        _require_report_evidence(report_content, prerequisites, results)
        protocol = results.protocol
        environment = results.environment
        _require(
            protocol["study_id"] == prerequisites.study_id
            and protocol["url"] == prerequisites.url
            and protocol["capability"] == prerequisites.capability
            and protocol["prerequisites_sha256"] == sha256_bytes(prerequisite_content)
            and protocol["base_config_sha256"] == prerequisites.config_sha256,
            "published result protocol must exactly match canonical prerequisites",
        )
        _require(
            environment["git_commit"] == prerequisites.git_commit
            and environment["python_version"] == prerequisites.tools["python_version"]
            and environment["trafficlab_version"] == prerequisites.tools["trafficlab_version"]
            and environment["docker_engine_version"] == prerequisites.tools["docker_engine_version"]
            and environment["docker_compose_version"] == prerequisites.tools["docker_compose_version"]
            and environment["platform"] == prerequisites.tools["platform"]
            and environment["target_image_id"] == prerequisites.images["target_image_id"]
            and environment["capture_image_id"] == prerequisites.images["capture_image_id"],
            "published environment must exactly match prerequisite identities",
        )
        configs = validate_base_configs(root, prerequisites, require_absent_run_directories=False)
        object_size = cast(int, prerequisites.capability["object_size_bytes"])
        traces: dict[tuple[WorkloadName, int], tuple[TraceEvent, ...]] = {}
        settings: dict[WorkloadName, SimilarityConfig] = {}
        for record in results.runs:
            workload = cast(WorkloadName, record.key["workload"])
            expected_config = configs[workload].model_copy(
                update={"run": configs[workload].run.model_copy(update={"directory": root / record.run_directory})}
            )
            _require(
                load_experiment(root / record.config_path) == expected_config, "realized primary config must be exact"
            )
            traces[(workload, cast(int, record.key["repeat"]))] = _audit_primary_record(root, record, object_size)
            settings[workload] = expected_config.similarity
        variation = natural_variation(results.runs, traces, settings)
        summaries = workload_summaries(results.runs)
        _require(
            tuple(_freeze_object(value) for value in variation) == results.natural_variation
            and tuple(_freeze_object(value) for value in summaries) == results.workload_summaries,
            "published variation and summaries must recompute from retained primary evidence",
        )
        _audit_reproduction_record(root, results)
    except TrafficlabError:
        raise
    except (OSError, TypeError, UnicodeDecodeError, ValueError) as error:
        raise TrafficlabError(
            f"Validation Study local report audit failed: {error}",
            corrective_action="preserve retained evidence and correct the checked report or result files",
        ) from error


def publish_audited_bundle(candidate: Path, study_id: str, *, repository_root: Path) -> Path:
    """Publish one candidate only after the standalone offline auditor accepts it."""

    from scripts.audit_validation_study import audit_bundle

    root = repository_root.resolve()

    def audit(candidate_root: Path) -> None:
        audit_bundle(candidate_root, repository=root)

    return publish_accepted_bundle(
        candidate,
        root / "examples" / "validation_study" / "evidence",
        study_id,
        audit,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_validation_study.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prerequisites = commands.add_parser("prerequisites")
    prerequisites.add_argument("--url", required=True)
    prerequisites.add_argument("--study-id", required=True)
    study_parser = commands.add_parser("study")
    study_parser.add_argument("--url", required=True)
    study_parser.add_argument("--study-id", required=True)
    study_parser.add_argument("--prerequisites", required=True, type=Path)
    publish_parser = commands.add_parser("publish")
    publish_parser.add_argument("--candidate", required=True, type=Path)
    publish_parser.add_argument("--study-id", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    repository_root: Path = REPOSITORY_ROOT,
    run: Callable[[Path], RunResult] = run_experiment,
    runner: CommandRunner = cast(CommandRunner, subprocess.run),  # noqa: B008 - fixed injected CLI boundary
    perf_counter: Callable[[], float] = time.perf_counter,
    utc_now: Callable[[], datetime] = _utc_now,
) -> int:
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        parser.print_usage(sys.stderr)
        return 2
    try:
        parsed = parser.parse_args(arguments)
    except SystemExit as error:
        return int(error.code) if error.code is not None else 0
    try:
        if parsed.command == "publish":
            candidate = parsed.candidate
            if not candidate.is_absolute():
                candidate = repository_root.resolve() / candidate
            destination = publish_audited_bundle(
                candidate, validate_study_id(parsed.study_id), repository_root=repository_root
            )
            print(f"validation-study: accepted evidence published at {destination}")
            return 0
        try:
            url = validate_endpoint_url(parsed.url)
            study_id = validate_study_id(parsed.study_id)
        except ValueError as error:
            raise TrafficlabError(
                f"invalid Validation Study command arguments: {error}",
                corrective_action="supply the exact credential-free HTTPS URL and lowercase study ID",
            ) from error
        if parsed.command == "prerequisites":
            result = run_prerequisites(
                url,
                study_id,
                repository_root=repository_root,
                runner=runner,
                utc_now=utc_now,
            )
            output_path = repository_root.resolve() / "examples" / "validation_study" / "prerequisites.json"
            print(f"validation-study: prerequisites validated for {result.study_id} at {output_path}")
            return 0
        try:
            prerequisite_record = _repository_relative_path(
                parsed.prerequisites.as_posix(),
                repository_root=repository_root,
                name="study prerequisite path",
            )
        except ValueError as error:
            raise TrafficlabError(
                f"invalid Validation Study command arguments: {error}",
                corrective_action="supply the exact repository-relative checked prerequisite path",
            ) from error
        prerequisite_path = repository_root.resolve() / Path(*prerequisite_record.split("/"))
        result = run_study(
            url,
            study_id,
            prerequisite_path,
            repository_root=repository_root,
            run=run,
            runner=runner,
            perf_counter=perf_counter,
            utc_now=utc_now,
        )
        output_path = repository_root.resolve() / "examples" / "validation_study" / "results.json"
        print(f"validation-study: study completed with {len(result.runs)} primary runs at {output_path}")
        return 0
    except TrafficlabError as error:
        print(f"validation-study: {error}; {error.corrective_action}", file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
