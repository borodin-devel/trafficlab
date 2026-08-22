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

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trafficlab import USER_AGENT, __version__
from trafficlab.artifacts.io import FileIdentity, append_run_log, file_identity
from trafficlab.capture.docker.image import (
    cold_capture_build_argv,
    load_capture_image_lock,
    validate_capture_dockerfile,
)
from trafficlab.capture.lineage import CaptureResult
from trafficlab.capture.stage import capture_experiment
from trafficlab.capture.validation import validate_capture_pair
from trafficlab.common.compatibility import ContentIdentity, identify_bytes, require_compatible
from trafficlab.common.config import ExperimentConfig, FamilyName, SimilarityConfig
from trafficlab.common.config_io import load_experiment, render_effective_config
from trafficlab.common.errors import TrafficlabError, attach_failure_outcome
from trafficlab.common.scapy_io import encode_pcapng, read_pcapng_bytes
from trafficlab.common.statistics import bootstrap_interval
from trafficlab.common.trace import (
    CaptureMetadata,
    TrafficTrace,
    align_generated,
    normalize_reference,
    parse_capture_metadata,
)
from trafficlab.comparison.codec import (
    parse_comparison_result,
    render_comparison_result,
    sha256_bytes,
    similarity_settings_identity,
)
from trafficlab.comparison.diagnostics import MultiscaleDiagnostic
from trafficlab.comparison.metrics import compare_traces
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.checkpoint import CheckpointState, parse_checkpoint, render_history_csv
from trafficlab.fitting.genetic.evaluation import evaluate_final, validate_evaluation_context
from trafficlab.fitting.genetic.strategy import StrategyContext, make_strategy_context
from trafficlab.fitting.genetic.types import METHOD_ORDER, Candidate, CandidateId, TrialResult
from trafficlab.generation.models.fitted_model import (
    BestModel,
    load_best_model,
    render_best_model,
    runtime_fitted_model,
)
from trafficlab.generation.models.registry import (
    get_family,
)
from trafficlab.pipeline.stage import run_experiment
from trafficlab.pipeline.types import RunResult
from trafficlab.pipeline.validation import validate_final_artifacts
from trafficlab.preflight.stage import open_or_prepare_experiment
from trafficlab.study_evidence.protocol import ValidationStudyPrerequisite, validate_study_model
from trafficlab.study_evidence.publication import publish_accepted_bundle

type JsonScalar = str | int | float | bool
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type FrozenJsonValue = JsonScalar | tuple[FrozenJsonValue, ...] | Mapping[str, FrozenJsonValue]
type FrozenJsonObject = Mapping[str, FrozenJsonValue]
type WorkloadName = Literal["short", "streaming", "bursty"]
type PrerequisiteCommandKind = Literal["docker_matrix", "internet_smoke"]
type TransferRange = tuple[int, int, str]
type TrainingRunner = Callable[[Path], RunResult]
type HeldOutCaptureRunner = Callable[[Path], CaptureResult]
type CollectionInputs = tuple[dict[str, object], bytes, dict[str, bytes], dict[WorkloadName, ExperimentConfig], int]


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
        input: bytes | None = None,
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


@dataclass(slots=True)
class _PhaseCaptureImage:
    """One temporary capture-image tag owned by a public study phase."""

    tag: str
    build_attempted: bool = False
    cleanup_verified: bool = False


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
        },
        {
            "final seed": config.run.final_seed,
            "final generation limits": config.generation.final,
        },
    )
    metadata = parse_capture_metadata(capture_content, source=capture_source)
    reference, W = normalize_reference(read_pcapng_bytes(reference_content, metadata, source=reference_source))
    raw_generated = (
        get_family(model.family)
        .generate(
            runtime_fitted_model(model),
            model.final_seed,
            W,
            model.final_limits,
        )
        .require_complete()
    )
    encoded = encode_pcapng(raw_generated, metadata, observation_window_seconds=W)
    generated = encoded.trace
    generated_pcapng = encoded.content
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
_BOOTSTRAP_SEED = 20_260_819
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
    "--user-agent",
    USER_AGENT,
    "--connect-timeout",
    "15",
)
_LOCKED_CURL_COMMON = CURL_COMMON
_ORACLE_URL = "https://validation-study.example/object"
_HISTORIC_SCHEMA_ONE_RESULT_COMMIT = "976dcd6ba8bfb4df4894e79263fb8b75dc426ad0"
_HISTORIC_SCHEMA_ONE_RESULT_STUDY_ID = "validation-study-20260814-ovh-r3"
_HISTORIC_SCHEMA_ONE_RESULT_URL = "https://sbg.proof.ovh.net/files/10Mb.dat"
_PRESERVED_PRE_USER_AGENT_R6_COMMIT = "6ea60c35922855264b574c03bee2ab64e622d183"
_PRESERVED_PRE_USER_AGENT_R6_TREE = "210b52105df20da973bd507c1b2f832398035c65"
_PRESERVED_PRE_USER_AGENT_R6_STUDY_ID = "2026-08-16-research-fitness-r6"
_PRESERVED_PRE_USER_AGENT_R6_URL = (
    "https://upload.wikimedia.org/wikipedia/commons/5/5b/"
    "SPACE_ELECTRIC_ROCKET_TEST%2C_SERT_II_IN_TANK_5_%28GRC-1968-C-03031%29.jpg"
)
_PRESERVED_PRE_USER_AGENT_R6_RAW_IDENTITY = {
    "sha256": "a6cb727911ad19333c2faffa09e7f8e246750c8524b04c8cac13f3402672d275",
    "size": 5662,
}
_PRESERVED_PRE_USER_AGENT_R6_MARKER_IDENTITY = {
    "sha256": "c450ec554562c364dd2dcd824fa2f4edccfa2c9d936136efc0c72739da8550e6",
    "size": 320,
}
_PRESERVED_PRE_USER_AGENT_R6_EVIDENCE_IDENTITIES: tuple[tuple[str, int, str], ...] = (
    ("capability.cid", 64, "2e9d83a41fd783fcd00c394ebb3d5aef2c7ccd259b812aa4921c17be8962c3a1"),
    ("capability.headers", 2066, "c271e6e5e909db84e54bb7231936eb680d145df8c4daff4a5239056aeb1613de"),
    ("capability.stderr", 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("capability.stdout", 161, "807fe709e0c95382a0cdd878bf71e77f525dc0ec58b0142d3636f49bcedb7d97"),
    ("capture.iid", 71, "10d7ebabfa8724f6e70b02ef48d2e96b31320b9bf60306b8030d1377f4326dcd"),
    ("docker.stderr", 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("docker.stdout", 3124, "4bb15010ceebbe53ec7487d6d68b195afd08d4b7c3bda6f00020d2d7e92cf4fe"),
    ("docker.xml", 3113, "f6e61b41be3b5659d0a1e198e946a566ef2c5438c35eec9a2f016865050fa47c"),
    ("internet.stderr", 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("internet.stdout", 900, "b15c52b90ad5d364a678e90821530a751d43bcb8ad110d4ad41ef827492316f3"),
    ("internet.xml", 403, "5a3d2d9e02961e08fe898d0c5654cc921d645b5756cdc0eb7db03a892c22a01f"),
)

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
        ("short", ((0, 1048575, "short.headers"),), 35.0, 90.0, (0.001, 0.01), expected_short),
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


def _replace_existing_regular_file(
    destination: Path,
    content: bytes,
    *,
    validate: Callable[[bytes], None],
    target_name: str,
) -> None:
    """Atomically replace one regular ignored support file after validating staged bytes."""

    if _path_entry_exists(destination):
        try:
            mode = destination.lstat().st_mode
        except OSError as error:
            raise ValueError(f"could not inspect {target_name} target {destination}: {error}") from error
        if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            raise TrafficlabError(
                f"{target_name} target must be a regular file: {destination}",
                corrective_action="preserve the existing path and use a regular canonical target",
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor_open = False
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        persisted = temporary.read_bytes()
        _require(persisted == content, f"temporary {target_name} bytes changed before publication")
        validate(persisted)
        os.replace(temporary, destination)
        directory_descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        published = destination.read_bytes()
        _require(published == content, f"published {target_name} bytes changed")
        validate(published)
    finally:
        if descriptor_open:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _read_regular_prerequisite_rotation_target(destination: Path, *, name: str) -> bytes:
    """Read one existing rotation target without following a symlink."""

    try:
        mode = destination.lstat().st_mode
    except OSError as error:
        raise ValueError(f"could not inspect {name} {destination}: {error}") from error
    _require(stat.S_ISREG(mode) and not stat.S_ISLNK(mode), f"{name} must be a regular file")
    try:
        return destination.read_bytes()
    except OSError as error:
        raise ValueError(f"could not read {name} {destination}: {error}") from error


def _stage_prerequisite_rotation_file(
    destination: Path,
    content: bytes,
    *,
    validate: Callable[[Path, bytes], None],
    suffix: str = ".tmp",
) -> Path:
    """Write, fsync, reread, and validate a private prerequisite-publication staging file."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=suffix, dir=destination.parent
        )
        temporary = Path(raw_temporary)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        persisted = temporary.read_bytes()
        _require(persisted == content, "staged prerequisite publication bytes changed before validation")
        validate(temporary, persisted)
        return temporary
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise


@dataclass(slots=True)
class _PrerequisiteRotationTarget:
    """One owned file in a marker-last prerequisite rotation."""

    kind: str
    destination: Path
    stage: Path | None
    backup: Path | None
    before_identity: JsonObject | None
    target_identity: JsonObject
    must_be_absent: bool


def _fsync_prerequisite_rotation_directory(destination: Path) -> None:
    """Fsync the parent directory after a rotation-owned entry mutation."""

    descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _commit_prerequisite_fsync(destination: Path) -> None:
    """Durably record one committed prerequisite-publication directory entry."""

    _fsync_prerequisite_rotation_directory(destination)


def _after_prerequisite_rotation_commit(_destination: Path) -> None:
    """Private crash-injection seam after one durable rotation boundary."""


def _publish_prerequisite_rotation_exclusive_file(
    destination: Path,
    content: bytes,
    *,
    validate: Callable[[bytes], None],
    name: str,
) -> None:
    """Durably link one previously-absent rotation file without replacement."""

    stage = _stage_prerequisite_rotation_file(
        destination,
        content,
        validate=lambda _stage, persisted: validate(persisted),
    )
    published = False
    try:
        os.link(stage, destination)
        published = True
        _fsync_prerequisite_rotation_directory(destination)
        persisted = _read_regular_prerequisite_rotation_target(destination, name=name)
        _require(persisted == content, f"published {name} bytes changed")
        validate(persisted)
    except BaseException:
        if published:
            try:
                persisted = _read_regular_prerequisite_rotation_target(destination, name=name)
                if persisted == content:
                    destination.unlink()
                    _fsync_prerequisite_rotation_directory(destination)
            except OSError:
                pass
        raise
    finally:
        try:
            stage.unlink(missing_ok=True)
        except OSError:
            pass


def _write_new_config(destination: Path, content: bytes, *, replace_existing: bool = False) -> None:
    if replace_existing:
        _replace_existing_regular_file(
            destination,
            content,
            validate=lambda _persisted: None,
            target_name="checked config",
        )
        return
    _require(not _path_entry_exists(destination), f"config target already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as stream:
            stream.write(content)
    except FileExistsError as error:
        raise ValueError(f"config target already exists: {destination}") from error


def _render_checked_base_config_content(config: ExperimentConfig, repository_root: Path) -> bytes:
    """Render the portable configuration that must reload to the supplied absolute oracle."""

    root = repository_root.resolve()
    workload = _workload_for_config(config)
    portable = _portable_base_config(config, repository_root=root, workload=workload)
    return render_effective_config(portable)


def render_checked_base_config(
    config: ExperimentConfig,
    destination: Path,
    repository_root: Path,
    *,
    replace_existing: bool = False,
) -> bytes:
    root = repository_root.resolve()
    workload = _workload_for_config(config)
    expected_destination = root / "examples" / "validation_study" / "configs" / f"{workload.name}.toml"
    _require(destination.resolve() == expected_destination, "checked config must use its exact profile path")
    content = _render_checked_base_config_content(config, root)
    _write_new_config(destination, content, replace_existing=replace_existing)
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
        "bootstrap": cast(JsonValue, bootstrap_interval(sample, seed=_BOOTSTRAP_SEED).as_dict()),
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
        result.methods.keys() == PUBLISHED_METHOD_ORDER,
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
    reference: TrafficTrace
    generated: TrafficTrace
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
        identity = file_identity(
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
    captured = read_pcapng_bytes(contents["reference.pcapng"], metadata, source=reference_path)
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
    generated = read_pcapng_bytes(contents["generated.pcapng"], metadata, source=generated_path)
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
    validate_final_artifacts(
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


def _direction_values(trace: TrafficTrace, *, bytes_: bool) -> JsonObject:
    outbound = trace.directions == 0
    inbound = trace.directions == 1
    return {
        "outbound": int(sum(int(value) for value in trace.frame_lengths[outbound])) if bytes_ else int(outbound.sum()),
        "inbound": int(sum(int(value) for value in trace.frame_lengths[inbound])) if bytes_ else int(inbound.sum()),
    }


def _trace_summary(
    trace: TrafficTrace,
    result: ComparisonResult,
    *,
    role: Literal["reference", "generated"],
) -> JsonObject:
    _require_type(
        type(trace) is TrafficTrace and bool(trace),
        "trace summary requires a canonical TrafficTrace",
    )
    _require(len(trace) >= 2, "trace summary requires at least two events")
    frame_lengths = tuple(int(value) for value in trace.frame_lengths)
    iats = tuple(float(value) for value in trace.iats())
    packet_totals = _direction_values(trace, bytes_=False)
    byte_totals = _direction_values(trace, bytes_=True)
    multiscale = result.methods["multiscale_rate"].diagnostics
    _require_type(isinstance(multiscale, MultiscaleDiagnostic), "multiscale diagnostics must be typed")
    multiscale = cast(MultiscaleDiagnostic, multiscale)
    scales: list[JsonValue] = []
    for value in multiscale.scales:
        scale = value.model_dump(mode="json")
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
        and comparison.methods.keys() == tuple(method.name for method in trial.methods)
        and all(
            comparison.methods[method.name].score == method.score
            and comparison.methods[method.name].diagnostics.model_dump(mode="json")
            == _thaw_json(cast(FrozenJsonValue, method.diagnostics))
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
    assert input_identities is not None
    expected = {
        "capture_json": identify_bytes(artifact_contents["capture.json"]),
        "reference_pcapng": identify_bytes(artifact_contents["reference.pcapng"]),
        "generated_pcapng": identify_bytes(artifact_contents["generated.pcapng"]),
        "similarity_settings": settings_identity,
    }
    _require(
        input_identities.as_content_identities() == expected,
        "published comparison input lineage must match exact artifact identities",
    )
    _require(rebuilt == persisted, "published comparison must equal strict persisted similarity evidence")


@dataclass(frozen=True, slots=True)
class _ReconstructedScience:
    fresh_simulation: TrialResult
    raw_events: TrafficTrace
    reparsed_events: TrafficTrace
    aligned_events: TrafficTrace
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
        runtime_fitted_model(evidence.best_model), 97, window, evidence.config.generation.trial
    ).require_complete()
    raw_final = family.generate(
        runtime_fitted_model(evidence.best_model), 97, window, evidence.config.generation.final
    ).require_complete()
    _require(raw_trial == raw_final, "trial and final guards must produce one exact raw seed-97 sequence")
    raw_comparison = compare_traces(evidence.reference, raw_trial, window, evidence.config.similarity)
    _require(
        _comparison_equals_trial(raw_comparison, fresh_simulation),
        "raw seed-97 comparison must equal the sole direct fresh simulation evaluation",
    )
    encoded = encode_pcapng(raw_trial, evidence.metadata, observation_window_seconds=window)
    rendered = encoded.content
    reparsed = encoded.trace
    _require(
        rendered == evidence.contents["generated.pcapng"] and reparsed == evidence.generated,
        "generated artifact must equal reparsed Scapy seed-97 events",
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
        science.reparsed_events == result.generation.trace and science.published == result.comparison,
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
    traces: Mapping[tuple[WorkloadName, int], TrafficTrace],
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
        "docker_engine_version",
        "docker_compose_version",
        "host_architecture",
        "kernel_release",
        "platform",
        "python_implementation",
        "python_version",
        "trafficlab_version",
        "uv_lock_sha256",
    )
    document = _exact_object(value, keys, name="tools")
    for key in keys:
        _strict_string(document[key], name=f"tools.{key}")
    _require(document["python_version"] == "3.12.3", "tools.python_version must be exactly 3.12.3")
    _require(document["python_implementation"] == "CPython", "tools.python_implementation must be CPython")
    _sha256(document["uv_lock_sha256"], name="tools uv.lock SHA-256")
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


def _remove_owned_prerequisite_capture_image(
    capture_tag: str,
    *,
    repository_root: Path,
    runner: CommandRunner,
) -> None:
    """Remove the runner-owned shared image without granting fixture ownership of it."""

    completed = runner(
        ("docker", "image", "rm", "--force", capture_tag),
        cwd=repository_root,
        check=False,
        capture_output=True,
        shell=False,
        timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
    )
    _require(
        completed.returncode == 0,
        f"could not remove owned prerequisite capture image: "
        f"{_command_detail(completed, operation='capture image cleanup')}",
    )


_PREREQUISITE_IGNORED_TOOL_ROOTS = frozenset(
    {
        ".superpowers",
        ".venv",
        ".worktrees",
        ".pytest_cache",
        ".pyright",
        ".ruff_cache",
        "build",
        "dist",
        "htmlcov",
    }
)
_PREREQUISITE_IGNORED_TOOL_FILES = frozenset({".coverage", "TASK.md"})
_PREREQUISITE_OWNED_IGNORED_PATHS = frozenset(
    {
        "examples/validation_study/prerequisites.json",
        "examples/validation_study/results.json",
        "examples/validation_study/configs/short.toml",
        "examples/validation_study/configs/streaming.toml",
        "examples/validation_study/configs/bursty.toml",
    }
)
_PREREQUISITE_OWNED_IGNORED_PREFIXES = (
    "examples/validation_study/.study-work/",
    "examples/validation_study/.candidates/",
    "examples/validation_study/evidence/.candidates/",
)


def _prerequisite_publisher_temporary_path(path: str) -> bool:
    parts = path.split("/")
    return (
        len(parts) >= 4
        and parts[:3] == ["examples", "validation_study", "evidence"]
        and parts[3].startswith(".")
        and parts[3].endswith(".tmp")
    )


def _permitted_ignored_prerequisite_worktree_path(path: str) -> bool:
    parts = path.split("/")
    first = parts[0]
    if (
        path in _PREREQUISITE_IGNORED_TOOL_FILES
        or first in _PREREQUISITE_IGNORED_TOOL_ROOTS
        or first == ".env"
        or first.startswith(".env.")
        or first.startswith(".coverage.")
        or "__pycache__" in parts
        or any(part.endswith(".egg-info") for part in parts)
        or path.endswith((".pyc", ".pyo", ".pyd"))
        or path.endswith(".log")
        or first == "runs"
    ):
        return True
    return (
        path in _PREREQUISITE_OWNED_IGNORED_PATHS
        or path.startswith(_PREREQUISITE_OWNED_IGNORED_PREFIXES)
        or _prerequisite_publisher_temporary_path(path)
    )


def _prerequisite_worktree_entries(repository_root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    directories = [repository_root]
    entries: list[str] = []
    nonregular_entries: list[str] = []
    while directories:
        directory = directories.pop()
        try:
            children = tuple(sorted(directory.iterdir(), key=lambda child: child.name))
        except OSError as error:
            raise ValueError(f"could not inspect prerequisite worktree directory: {error}") from error
        for child in children:
            relative = child.relative_to(repository_root).as_posix()
            if relative == ".git" or _permitted_ignored_prerequisite_worktree_path(relative):
                continue
            try:
                mode = child.lstat().st_mode
            except OSError as error:
                raise ValueError(f"could not inspect prerequisite worktree entry: {error}") from error
            if stat.S_ISDIR(mode):
                directories.append(child)
            else:
                entries.append(relative)
                if not stat.S_ISREG(mode):
                    nonregular_entries.append(relative)
    return tuple(entries), tuple(nonregular_entries)


def _ignored_prerequisite_worktree_paths(
    repository_root: Path,
    paths: Sequence[str],
    *,
    runner: CommandRunner,
) -> frozenset[str]:
    if not paths:
        return frozenset()
    completed = runner(
        ("git", "check-ignore", "-z", "--stdin"),
        cwd=repository_root,
        check=False,
        capture_output=True,
        shell=False,
        timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        input=b"".join(os.fsencode(path) + b"\0" for path in paths),
    )
    output, _stderr = _completed_output(completed, operation="ignored prerequisite paths")
    if completed.returncode not in (0, 1):
        raise ValueError(
            "could not resolve ignored prerequisite paths: "
            f"{_command_detail(completed, operation='ignored prerequisite paths')}"
        )
    if completed.returncode == 0 and not output:
        raise ValueError("ignored prerequisite paths must be nonempty for match status")
    if completed.returncode == 1 and output:
        raise ValueError("ignored prerequisite paths must be empty for no-match status")
    if output and not output.endswith(b"\0"):
        raise ValueError("ignored prerequisite paths must be terminal NUL-delimited")
    records = output[:-1].split(b"\0") if output else ()
    try:
        ignored_paths = tuple(record.decode("utf-8") for record in records)
    except UnicodeDecodeError as error:
        raise ValueError(f"ignored prerequisite path is not UTF-8: {error}") from error
    if len(set(ignored_paths)) != len(ignored_paths):
        raise ValueError("ignored prerequisite paths must be unique")
    if any(path not in paths for path in ignored_paths):
        raise ValueError("ignored prerequisite paths do not match the inspected worktree")
    return frozenset(ignored_paths)


def _require_clean_prerequisite_worktree(repository_root: Path, *, runner: CommandRunner) -> None:
    status_result = runner(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=repository_root,
        check=False,
        capture_output=True,
        shell=False,
        timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
    )
    status_stdout, _status_stderr = _completed_output(status_result, operation="Git tree inspection")
    _require(status_result.returncode == 0, "could not inspect prerequisite Git tree")
    _require(status_stdout == b"", "prerequisites require an exactly clean tracked and untracked Git tree")
    entries, nonregular_entries = _prerequisite_worktree_entries(repository_root)
    ignored_entries = _ignored_prerequisite_worktree_paths(repository_root, entries, runner=runner)
    for path in entries:
        if path in ignored_entries and not _permitted_ignored_prerequisite_worktree_path(path):
            raise ValueError(f"ignored prerequisite worktree entry is not permitted: {path}")
    for path in nonregular_entries:
        if path not in ignored_entries:
            raise ValueError(f"prerequisite worktree contains non-regular entry: {path}")


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
    # The exclusive CID file is the ownership proof for destructive cleanup.
    # Never fall back to a name-only removal: a colliding container may belong
    # to another attempt even when it uses the expected naming convention.
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
    capture_tag = f"trafficlab-validation-{study_id}:capture"
    pytest_prefix = ("uv", "run", "--locked", "pytest", "-vv", "-n", "0", "-m")
    if kind == "docker_matrix":
        return (
            *_guard_prefix("20m"),
            *pytest_prefix,
            "docker",
            "--capture-image",
            capture_tag,
            "--junitxml",
            f"{evidence}/docker.xml",
        )
    return (
        *_guard_prefix("10m"),
        *pytest_prefix,
        "internet",
        "--capture-image",
        capture_tag,
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
_RETAINED_PREREQUISITE_CAPABILITY_KEYS = (
    "canary_sha256",
    "content_length",
    "content_range",
    "object_size_bytes",
    "status",
)
_RETAINED_CAPABILITY_HEADER = "headers/prerequisites/00-prerequisites/capability.headers"


def _retained_identity(value: object, *, name: str) -> JsonObject:
    try:
        identity = ContentIdentity.from_dict(value, name=name)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a content identity: {error}") from error
    return cast(JsonObject, identity.as_dict())


def _retained_output(value: object, *, name: str, expected_path: str) -> JsonObject:
    document = cast(JsonObject, value)
    _require(document["path"] == expected_path, f"{name} path must be exactly {expected_path}")
    return document


def _retained_prerequisite_environment(value: object) -> JsonObject:
    document = cast(JsonObject, value)
    target_reference = cast(str, document["target_image_reference"])
    _require(
        target_reference == TARGET_REFERENCE,
        "retained target image reference must equal the approved digest-pinned target",
    )
    capture_id = cast(str, document["capture_image_id"])
    capture_reference = cast(str, document["capture_image_reference"])
    _require(
        capture_reference == capture_id
        or (
            "@sha256:" in capture_reference
            and re.fullmatch(r"[0-9a-f]{64}", capture_reference.rsplit("@sha256:", 1)[-1]) is not None
        ),
        "retained capture image reference must be its immutable image ID or digest reference",
    )
    return document


def _retained_prerequisite_capability(value: object) -> JsonObject:
    document = cast(JsonObject, value)
    object_size = cast(int, document["object_size_bytes"])
    _require(
        4 * 1024 * 1024 <= object_size <= 16 * 1024 * 1024,
        "retained capability object size must be from 4 MiB through 16 MiB",
    )
    status = cast(int, document["status"])
    length = cast(int, document["content_length"])
    _require((status, length) == (206, 1), "retained capability must record one 206 byte-range response")
    content_range = cast(str, document["content_range"])
    _require(
        content_range == f"bytes 0-0/{object_size}",
        "retained capability content range must bind the recorded object size",
    )
    return document


def _retained_prerequisite_document(value: object) -> dict[str, object]:
    _require(type(value) is dict, "retained prerequisite evidence must be a JSON object")
    raw = cast(dict[str, object], value)
    _require(
        raw.get("schema_version") == 4,
        "retained prerequisite schema version must be exactly 4",
    )
    validated = validate_study_model(
        ValidationStudyPrerequisite,
        raw,
        name="retained prerequisite evidence",
    )
    root = cast(dict[str, object], validated.model_dump(mode="json"))
    study_id = validate_study_id(_strict_string(root["study_id"], name="retained prerequisite study ID"))
    url = validate_endpoint_url(_strict_string(root["url"], name="retained prerequisite URL"))
    values = cast(list[object], root["commands"])
    commands: list[JsonObject] = []
    for value, expected_kind in zip(values, ("docker_matrix", "internet_smoke"), strict=True):
        document = cast(dict[str, object], value)
        kind = cast(str, document["kind"])
        _require(kind == expected_kind, "retained prerequisite commands must use the fixed kind order")
        argv = tuple(cast(list[str], document["argv"]))
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
                "command": _retained_output(
                    document["command"],
                    name=f"retained {kind} command",
                    expected_path=f"prerequisites/{kind}.command.json",
                ),
                "exit_status": 0,
                "junit": _retained_output(
                    document["junit"], name=f"retained {kind} JUnit", expected_path=f"prerequisites/{kind}.junit.xml"
                ),
                "kind": kind,
                "status": _retained_output(
                    document["status"],
                    name=f"retained {kind} status",
                    expected_path=f"prerequisites/{kind}.status.json",
                ),
                "stderr": _retained_output(
                    document["stderr"], name=f"retained {kind} stderr", expected_path=f"prerequisites/{kind}.stderr"
                ),
                "stdout": _retained_output(
                    document["stdout"], name=f"retained {kind} stdout", expected_path=f"prerequisites/{kind}.stdout"
                ),
                "tests": cast(JsonObject, document["tests"]),
            }
        )
    return {
        "capability": _retained_prerequisite_capability(root["capability"]),
        "commands": commands,
        "environment": _retained_prerequisite_environment(root["environment"]),
        "schema_version": 4,
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
        raise ValueError(
            "retained prerequisite JSON must use canonical sorted compact encoding with one trailing newline"
        )
    return document


def retained_prerequisite_paths(value: object) -> tuple[str, ...]:
    """Return the three retained output paths for each validated prerequisite command."""
    document = _retained_prerequisite_document(value)
    paths = [
        cast(str, cast(JsonObject, command[field])["path"])
        for command in cast(list[JsonObject], document["commands"])
        for field in ("command", "status", "stdout", "stderr", "junit")
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
        "--user-agent",
        USER_AGENT,
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


def _pre_user_agent_capability_argv(study_id: str, url: str) -> tuple[str, ...]:
    """Return the immediately preceding capability projection for rotation-only compatibility."""

    current = _expected_capability_argv(study_id, url)
    user_agent = current.index("--user-agent")
    return current[:user_agent] + current[user_agent + 2 :]


def _historic_schema_one_capability_argv() -> tuple[str, ...]:
    """Return the sole pre-User-Agent command retained in checked schema-1 evidence."""

    return _pre_user_agent_capability_argv(
        _HISTORIC_SCHEMA_ONE_RESULT_STUDY_ID,
        _HISTORIC_SCHEMA_ONE_RESULT_URL,
    )


def _historic_schema_one_workload_argvs() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return the sole pre-User-Agent workload projections retained in checked schema-1 evidence."""

    common = (
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
    url = _HISTORIC_SCHEMA_ONE_RESULT_URL
    short = (
        *common,
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
    streaming = (
        *common,
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
    bursty_transfers = tuple(
        (start, start + 32_767, f"bursty-{index}.headers")
        for index, start in enumerate((0, 524_288, 1_048_576, 1_572_864, 2_097_152, 2_621_440, 3_145_728, 3_670_016))
    )
    bursty_groups: list[str] = []
    for index, (start, end, filename) in enumerate(bursty_transfers):
        if index:
            bursty_groups.append("--next")
        bursty_groups.extend(
            (
                *common,
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
    return short, streaming, ("--parallel", "--parallel-max", "4", "--fail-early", *bursty_groups)


def _historic_schema_one_workload_specs() -> tuple[WorkloadSpec, WorkloadSpec, WorkloadSpec]:
    """Return the complete measured profile of the sole retained schema-1 study."""

    short, streaming, bursty = _historic_schema_one_workload_argvs()
    bursty_transfers = tuple(
        (start, start + 32_767, f"bursty-{index}.headers")
        for index, start in enumerate((0, 524_288, 1_048_576, 1_572_864, 2_097_152, 2_621_440, 3_145_728, 3_670_016))
    )
    return (
        WorkloadSpec(
            name="short",
            argv=short,
            transfers=((0, 262_143, "short.headers"),),
            workload_timeout_seconds=35.0,
            total_timeout_seconds=90.0,
            multiscale_widths_seconds=(0.001, 0.01),
        ),
        WorkloadSpec(
            name="streaming",
            argv=streaming,
            transfers=((0, 4_194_303, "streaming.headers"),),
            workload_timeout_seconds=50.0,
            total_timeout_seconds=120.0,
            multiscale_widths_seconds=(0.25, 1.0),
        ),
        WorkloadSpec(
            name="bursty",
            argv=bursty,
            transfers=bursty_transfers,
            workload_timeout_seconds=35.0,
            total_timeout_seconds=90.0,
            multiscale_widths_seconds=(0.001, 0.01),
        ),
    )


def _historic_schema_one_workload_transfers(workload: str) -> tuple[tuple[int, int, str], ...]:
    """Return the exact range requests recorded by the sole schema-1 study."""

    return next(spec.transfers for spec in _historic_schema_one_workload_specs() if spec.name == workload)


def _validate_capability(
    value: object,
    *,
    repository_root: Path,
    study_id: str,
    url: str,
    historic_schema_one_result: bool = False,
    expected_capability_argv: tuple[str, ...] | None = None,
) -> JsonObject:
    document = _exact_object(value, CAPABILITY_KEYS, name="capability")
    argv = _string_array(document["argv"], name="capability argv", nonempty=True)
    expected_argv = expected_capability_argv or (
        _historic_schema_one_capability_argv()
        if historic_schema_one_result
        else _expected_capability_argv(study_id, url)
    )
    _require(
        argv == expected_argv,
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
    expected_capability_argv: tuple[str, ...] | None = None,
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
    capability = _validate_capability(
        root["capability"],
        repository_root=repository_root,
        study_id=study_id,
        url=url,
        expected_capability_argv=expected_capability_argv,
    )
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


def _parse_preserved_pre_user_agent_r6_predecessor(
    content: bytes,
    *,
    repository_root: Path,
    runner: CommandRunner,
) -> PrerequisiteResults:
    """Validate the one retained raw r6 document without relaxing the public prerequisite codec."""

    try:
        _require(
            identify_bytes(content).as_dict() == _PRESERVED_PRE_USER_AGENT_R6_RAW_IDENTITY,
            "preserved pre-User-Agent r6 predecessor must equal its exact raw canonical identity",
        )
        document = _load_json(content)
        result = _validate_prerequisite_document(
            document,
            repository_root=repository_root,
            expected_capability_argv=_pre_user_agent_capability_argv(
                _PRESERVED_PRE_USER_AGENT_R6_STUDY_ID,
                _PRESERVED_PRE_USER_AGENT_R6_URL,
            ),
        )
        _require(
            _canonical_json(document) == content,
            "preserved pre-User-Agent r6 predecessor must use canonical JSON",
        )
        _require(
            result.study_id == _PRESERVED_PRE_USER_AGENT_R6_STUDY_ID
            and result.url == _PRESERVED_PRE_USER_AGENT_R6_URL
            and result.git_commit == _PRESERVED_PRE_USER_AGENT_R6_COMMIT
            and result.git_tree_clean,
            "preserved pre-User-Agent r6 predecessor must match its retained study identity and source commit",
        )
        tree_result = runner(
            ("git", "rev-parse", f"{_PRESERVED_PRE_USER_AGENT_R6_COMMIT}^{{tree}}"),
            cwd=repository_root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        )
        _require(tree_result.returncode == 0, "preserved pre-User-Agent r6 source tree could not be resolved")
        _require(
            tree_result.stdout.decode("ascii", errors="strict").strip() == _PRESERVED_PRE_USER_AGENT_R6_TREE,
            "preserved pre-User-Agent r6 source tree must match its retained commit tree",
        )
        marker = _collection_attempt_root(repository_root, result.study_id) / "prerequisites-success.json"
        marker_content = _read_regular_prerequisite_rotation_target(marker, name="successful prerequisite marker")
        _require(
            identify_bytes(marker_content).as_dict() == _PRESERVED_PRE_USER_AGENT_R6_MARKER_IDENTITY,
            "preserved pre-User-Agent r6 predecessor must match its exact success-marker identity",
        )
        _require_successful_prerequisite_marker_content(
            marker_content,
            study_id=result.study_id,
            url=result.url,
            prerequisite_content=content,
        )
        evidence = (
            repository_root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "evidence"
            / result.study_id
            / "00-prerequisites"
        )
        try:
            mode = evidence.lstat().st_mode
            _require(
                stat.S_ISDIR(mode) and not stat.S_ISLNK(mode),
                "preserved pre-User-Agent r6 evidence directory must be a regular directory",
            )
            names = tuple(sorted(path.name for path in evidence.iterdir()))
        except OSError as error:
            raise ValueError(f"could not inspect preserved pre-User-Agent r6 evidence {evidence}: {error}") from error
        expected_names = tuple(name for name, _size, _sha256 in _PRESERVED_PRE_USER_AGENT_R6_EVIDENCE_IDENTITIES)
        _require(names == expected_names, "preserved pre-User-Agent r6 evidence inventory must match exactly")
        for name, size, sha256 in _PRESERVED_PRE_USER_AGENT_R6_EVIDENCE_IDENTITIES:
            retained = _read_regular_prerequisite_rotation_target(
                evidence / name,
                name=f"preserved pre-User-Agent r6 evidence {name}",
            )
            _require(
                identify_bytes(retained).as_dict() == {"sha256": sha256, "size": size},
                f"preserved pre-User-Agent r6 evidence {name} must match its retained identity",
            )
        return result
    except (OSError, TypeError, UnicodeError, ValueError) as error:
        raise ValueError("preserved pre-User-Agent r6 predecessor is not the exact retained evidence") from error


def _publish_support_json(
    path: Path,
    content: bytes,
    *,
    validate: Callable[[bytes], None],
    replace_existing: bool = False,
) -> None:
    if _path_entry_exists(path):
        if replace_existing:
            _replace_existing_regular_file(
                path,
                content,
                validate=validate,
                target_name="official Validation Study publication",
            )
            return
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
    replace_existing: bool = False,
) -> None:
    content = render_prerequisite_results(value)

    def validate(persisted: bytes) -> None:
        parsed = parse_prerequisite_results(persisted, repository_root=repository_root)
        if render_prerequisite_results(parsed) != content:
            raise ValueError("persisted prerequisite JSON is not canonical")

    _publish_support_json(path, content, validate=validate, replace_existing=replace_existing)


def run_prerequisites(
    url: str,
    study_id: str,
    *,
    repository_root: Path,
    runner: CommandRunner,
    utc_now: Callable[[], datetime],
) -> PrerequisiteResults:
    root = repository_root.resolve()
    owned_capture_tag: str | None = None
    primary: BaseException | None = None
    try:
        _require(root.is_dir(), f"repository root must be an existing directory: {root}")
        url = validate_endpoint_url(url)
        study_id = validate_study_id(study_id)
        _recover_incomplete_prerequisite_rotations(root)
        _begin_phase_attempt(root, study_id=study_id, url=url, phase="prerequisites")
        prerequisite_path = root / "examples" / "validation_study" / "prerequisites.json"
        config_paths = {
            name: root / "examples" / "validation_study" / "configs" / f"{name}.toml"
            for name in ("short", "streaming", "bursty")
        }
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
        _require_clean_prerequisite_worktree(root, runner=runner)
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

        capture_lock = load_capture_image_lock(root / "docker" / "capture" / "image-lock.json")
        validate_capture_dockerfile(
            (root / "docker" / "capture" / "Dockerfile").read_text(encoding="utf-8"), capture_lock
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
        capture_tag = f"trafficlab-validation-{study_id}:capture"
        owned_capture_tag = capture_tag
        build = runner(
            cold_capture_build_argv(capture_tag, iid_path),
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
            _require(
                capture_image_id == capture_lock.expected_capture_image_id,
                "cold capture rebuild ID must equal the checked image lock before capability validation",
            )
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
        tag_to_remove = owned_capture_tag
        owned_capture_tag = None
        _remove_owned_prerequisite_capture_image(
            tag_to_remove,
            repository_root=root,
            runner=runner,
        )

        config_hashes: JsonObject = {}
        config_payloads: list[tuple[ExperimentConfig, Path, bytes]] = []
        for workload in workload_specs(url):
            config = build_base_config(
                workload,
                repository_root=root,
                study_id=study_id,
                url=url,
                capture_image_id=capture_image_id,
            )
            content = _render_checked_base_config_content(config, root)
            config_hashes[workload.name] = hashlib.sha256(content).hexdigest()
            config_payloads.append((config, config_paths[workload.name], content))
        result = PrerequisiteResults(
            schema_version=1,
            created_utc=_timestamp_now(utc_now),
            study_id=study_id,
            git_commit=git_commit,
            git_tree_clean=True,
            url=url,
            tools=_freeze_object(
                {
                    "docker_engine_version": docker_engine_version,
                    "docker_compose_version": docker_compose_version,
                    "host_architecture": platform.machine(),
                    "kernel_release": platform.release(),
                    "platform": platform.platform(),
                    "python_implementation": platform.python_implementation(),
                    "python_version": platform.python_version(),
                    "trafficlab_version": __version__,
                    "uv_lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
                }
            ),
            images=_freeze_object(images),
            capability=_freeze_object(capability),
            config_sha256=_freeze_object(config_hashes),
            commands=(_freeze_object(docker_command), _freeze_object(internet_command)),
        )
        _commit_prerequisite_rotation(
            root,
            prerequisite_path=prerequisite_path,
            configs=tuple(config_payloads),
            result=result,
            study_id=study_id,
            url=url,
            runner=runner,
        )
        return result
    except TrafficlabError as error:
        primary = error
        raise
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        if type(study_id) is str and _STUDY_ID_PATTERN.fullmatch(study_id) is not None:
            _best_effort_preserve_capability_canary(
                root / "examples" / "validation_study" / ".study-work" / "evidence" / study_id / "00-prerequisites",
                root / "examples" / "validation_study" / ".study-work" / "mount" / study_id / ".capability.headers",
            )
        primary = TrafficlabError(
            f"Validation Study prerequisite validation failed: {error}",
            corrective_action="preserve the ignored evidence, correct the prerequisite, and restart with a new study ID",
        )
        raise primary from error
    except BaseException as error:
        primary = error
        raise
    finally:
        if owned_capture_tag is not None:
            tag_to_remove = owned_capture_tag
            owned_capture_tag = None
            try:
                _remove_owned_prerequisite_capture_image(
                    tag_to_remove,
                    repository_root=root,
                    runner=runner,
                )
            except BaseException as cleanup_error:
                if primary is None:
                    raise TrafficlabError(
                        f"Validation Study prerequisite capture image cleanup failed: {cleanup_error}",
                        corrective_action=(
                            "preserve the prerequisite evidence, remove the exact owned capture image tag, "
                            "and restart with a new study ID"
                        ),
                    ) from cleanup_error
                primary.add_note(f"prerequisite capture image cleanup failed: {cleanup_error}")


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
    historic_schema_one_result: bool = False,
) -> JsonObject:
    legacy_keys = (
        "count",
        "mean",
        "minimum",
        "maximum",
        "range",
        "sample_variance",
        "sample_standard_deviation",
    )
    keys = legacy_keys if historic_schema_one_result else ("bootstrap", *legacy_keys)
    document = _exact_object(value, keys, name=name)
    if observations is not None:
        expected = descriptive_statistics(observations)
        if historic_schema_one_result:
            expected.pop("bootstrap")
        _require(
            document == expected,
            f"{name} is stale and does not recompute from its three source observations",
        )
        return cast(JsonObject, document)
    if not historic_schema_one_result:
        bootstrap = _exact_object(
            document["bootstrap"],
            (
                "confidence_level",
                "generator",
                "generator_state",
                "lower_bound",
                "method",
                "n_resamples",
                "sample_size",
                "seed",
                "statistic",
                "upper_bound",
            ),
            name=f"{name}.bootstrap",
        )
        _require(bootstrap["confidence_level"] == 0.95, f"{name}.bootstrap confidence level must be 0.95")
        _require(bootstrap["generator"] == "PCG64", f"{name}.bootstrap generator must be PCG64")
        _require(bootstrap["method"] == "percentile", f"{name}.bootstrap method must be percentile")
        _require(bootstrap["n_resamples"] == 10_000, f"{name}.bootstrap resamples must be 10000")
        _require(bootstrap["sample_size"] == 3, f"{name}.bootstrap sample size must be three")
        _require(bootstrap["seed"] == _BOOTSTRAP_SEED, f"{name}.bootstrap seed is not the fixed report seed")
        _require(bootstrap["statistic"] == "mean", f"{name}.bootstrap statistic must be mean")
        lower = _strict_float(bootstrap["lower_bound"], name=f"{name}.bootstrap lower bound")
        upper = _strict_float(bootstrap["upper_bound"], name=f"{name}.bootstrap upper bound")
        _require(lower <= upper, f"{name}.bootstrap bounds must not be inverted")
        _require(type(bootstrap["generator_state"]) is dict, f"{name}.bootstrap generator state must be an object")
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


def _validate_score_summary(
    value: object,
    *,
    name: str,
    observations: Sequence[JsonObject],
    historic_schema_one_result: bool = False,
) -> JsonObject:
    document = _exact_object(value, ("aggregate", "methods"), name=name)
    aggregate_values = [cast(float, score["aggregate"]) for score in observations]
    methods = _exact_object(document["methods"], PUBLISHED_METHOD_ORDER, name=f"{name}.methods")
    source_methods = [cast(dict[str, JsonValue], score["methods"]) for score in observations]
    _validate_descriptive(
        document["aggregate"],
        name=f"{name}.aggregate",
        observations=aggregate_values,
        historic_schema_one_result=historic_schema_one_result,
    )
    for method in PUBLISHED_METHOD_ORDER:
        _validate_descriptive(
            methods[method],
            name=f"{name}.methods.{method}",
            observations=[cast(float, values[method]) for values in source_methods],
            historic_schema_one_result=historic_schema_one_result,
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


def _workload_widths(workload: str, *, historic_schema_one_result: bool = False) -> tuple[float, float]:
    specs = _historic_schema_one_workload_specs() if historic_schema_one_result else workload_specs(_ORACLE_URL)
    return next(spec.multiscale_widths_seconds for spec in specs if spec.name == workload)


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


def _validate_trace(
    value: object,
    *,
    workload: str,
    name: str,
    historic_schema_one_result: bool = False,
) -> JsonObject:
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
    widths = _workload_widths(workload, historic_schema_one_result=historic_schema_one_result)
    _require(len(scales) == len(widths), f"{name}.scales must contain the exact configured widths")
    for scale, width in zip(scales, widths, strict=True):
        _validate_scale(scale, expected_width=width, packet_totals=packets, byte_totals=bytes_)
    return cast(JsonObject, document)


def _expected_transfers(
    workload: str,
    *,
    historic_schema_one_result: bool = False,
) -> tuple[tuple[int, int, str], ...]:
    if historic_schema_one_result:
        return _historic_schema_one_workload_transfers(workload)
    return next(spec.transfers for spec in workload_specs(_ORACLE_URL) if spec.name == workload)


def _validate_transfer_responses(
    value: object,
    *,
    repository_root: Path,
    workload: str,
    evidence_directory: str,
    object_size: int,
    historic_schema_one_result: bool = False,
) -> list[JsonValue]:
    responses = _strict_list(value, name="transfer responses")
    expected = _expected_transfers(workload, historic_schema_one_result=historic_schema_one_result)
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
    historic_schema_one_result: bool = False,
) -> None:
    elapsed = _strict_float(document["elapsed_seconds"], name="run elapsed seconds", lower=0.0)
    _require(elapsed > 0.0, "run elapsed seconds must be positive")
    cleanup = _strict_bool(document["cleanup_verified"], name="run cleanup verification")
    _require(cleanup, "run cleanup must be verified")
    reference = _validate_trace(
        document["reference"],
        workload=workload,
        name="reference trace",
        historic_schema_one_result=historic_schema_one_result,
    )
    generated = _validate_trace(
        document["generated"],
        workload=workload,
        name="generated trace",
        historic_schema_one_result=historic_schema_one_result,
    )
    champions = _validate_champions(document["family_champions"])
    _validate_reuse(document["reuse"])
    _validate_transfer_responses(
        document["transfer_responses"],
        repository_root=repository_root,
        workload=workload,
        evidence_directory=evidence_directory,
        object_size=object_size,
        historic_schema_one_result=historic_schema_one_result,
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
    historic_schema_one_result: bool = False,
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
        historic_schema_one_result=historic_schema_one_result,
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


def _validate_workloads(
    value: object,
    *,
    url: str,
    historic_schema_one_result: bool = False,
) -> list[JsonValue]:
    items = _strict_list(value, name="workload definitions")
    expected_specs = _historic_schema_one_workload_specs() if historic_schema_one_result else workload_specs(url)
    _require(len(items) == 3, "workload definitions must contain short, streaming, and bursty")
    keys = ("name", "argv", "workload_timeout_seconds", "total_timeout_seconds", "multiscale_widths_seconds")
    for _index, (item, expected) in enumerate(zip(items, expected_specs, strict=True)):
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
        _require(
            actual == oracle,
            f"{name} workload definition must equal the exact workload oracle",
        )
    return cast(list[JsonValue], items)


def _validate_protocol(
    value: object,
    *,
    repository_root: Path,
    historic_schema_one_result: bool = False,
) -> JsonObject:
    document = _exact_object(value, PROTOCOL_KEYS, name="protocol")
    study_id = validate_study_id(_strict_string(document["study_id"], name="protocol study ID"))
    url = validate_endpoint_url(_strict_string(document["url"], name="protocol URL"))
    if historic_schema_one_result:
        _require(
            study_id == _HISTORIC_SCHEMA_ONE_RESULT_STUDY_ID and url == _HISTORIC_SCHEMA_ONE_RESULT_URL,
            "historic schema-1 protocol identity must equal the sole retained study ID and URL",
        )
    _validate_capability(
        document["capability"],
        repository_root=repository_root,
        study_id=study_id,
        url=url,
        historic_schema_one_result=historic_schema_one_result,
    )
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
    _validate_workloads(
        document["workloads"],
        url=url,
        historic_schema_one_result=historic_schema_one_result,
    )
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


def _validate_descriptors(
    value: object,
    *,
    name: str,
    runs: Sequence[JsonObject],
    historic_schema_one_result: bool = False,
) -> JsonObject:
    document = _exact_object(value, _DESCRIPTOR_KEYS, name=name)
    observations = _descriptor_observations(runs)
    for key in _DESCRIPTOR_KEYS:
        _validate_descriptive(
            document[key],
            name=f"{name}.{key}",
            observations=observations[key],
            historic_schema_one_result=historic_schema_one_result,
        )
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


def _validate_natural_variation(
    value: object,
    *,
    workload: str,
    runs: Sequence[JsonObject],
    historic_schema_one_result: bool = False,
) -> JsonObject:
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
    _validate_descriptors(
        document["reference_descriptors"],
        name="natural reference descriptors",
        runs=runs,
        historic_schema_one_result=historic_schema_one_result,
    )
    return cast(JsonObject, document)


def _validate_family_summary(
    value: object,
    *,
    family: str,
    champions: Sequence[JsonObject],
    historic_schema_one_result: bool = False,
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
        document["selection_fitness"],
        name=f"{family} selection fitness",
        observations=fitness_values,
        historic_schema_one_result=historic_schema_one_result,
    )
    for method in PUBLISHED_METHOD_ORDER:
        _validate_descriptive(
            components[method],
            name=f"{family} selection component {method}",
            observations=[cast(float, values[method]) for values in component_maps],
            historic_schema_one_result=historic_schema_one_result,
        )
    return cast(JsonObject, document)


def _validate_workload_summary(
    value: object,
    *,
    workload: str,
    runs: Sequence[JsonObject],
    historic_schema_one_result: bool = False,
) -> JsonObject:
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
        historic_schema_one_result=historic_schema_one_result,
    )
    for family in FAMILY_ORDER:
        _validate_family_summary(
            families[family],
            family=family,
            champions=champions_by_family[family],
            historic_schema_one_result=historic_schema_one_result,
        )
    _validate_descriptive(
        document["winner_selection_fitness"],
        name=f"{name} winner selection fitness",
        observations=[cast(float, winner["selection_fitness"]) for winner in winners],
        historic_schema_one_result=historic_schema_one_result,
    )
    _validate_score_summary(
        document["fresh_simulation"],
        name=f"{name} fresh simulation",
        observations=held_scores,
        historic_schema_one_result=historic_schema_one_result,
    )
    _validate_score_summary(
        document["published"],
        name=f"{name} published",
        observations=published_scores,
        historic_schema_one_result=historic_schema_one_result,
    )
    _validate_descriptors(
        document["reference_descriptors"],
        name=f"{name} reference descriptors",
        runs=runs,
        historic_schema_one_result=historic_schema_one_result,
    )
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
    historic_schema_one_result: bool = False,
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
        historic_schema_one_result=historic_schema_one_result,
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
    historic_schema_one_result = cast(str, environment["git_commit"]) == _HISTORIC_SCHEMA_ONE_RESULT_COMMIT
    protocol = _validate_protocol(
        root["protocol"],
        repository_root=repository_root,
        historic_schema_one_result=historic_schema_one_result,
    )
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
            historic_schema_one_result=historic_schema_one_result,
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
        _validate_natural_variation(
            item,
            workload=workload,
            runs=grouped[workload],
            historic_schema_one_result=historic_schema_one_result,
        )
        for item, workload in zip(natural_items, workloads, strict=True)
    ]
    summaries = [
        _validate_workload_summary(
            item,
            workload=workload,
            runs=grouped[workload],
            historic_schema_one_result=historic_schema_one_result,
        )
        for item, workload in zip(summary_items, workloads, strict=True)
    ]
    source = grouped["streaming"][1]
    reproduction = _validate_reproduction(
        root["reproduction"],
        repository_root=repository_root,
        protocol=protocol,
        source=source,
        historic_schema_one_result=historic_schema_one_result,
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


def _study_target_image_identity(*, repository_root: Path, runner: CommandRunner) -> JsonObject:
    """Inspect the target before a study phase creates its owned capture image."""

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
    return _target_image_record(target_stdout)


def _study_capture_image_identity(
    *,
    repository_root: Path,
    capture_image_id: str,
    runner: CommandRunner,
) -> str:
    """Inspect the fresh study-owned capture image after its cold build."""

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
    return _inspected_image_id(capture_stdout, name="capture")


def _validated_study_inputs(
    url: str,
    study_id: str,
    prerequisite_path: Path,
    *,
    repository_root: Path,
    runner: CommandRunner,
    owned_capture_image: _PhaseCaptureImage | None = None,
    capture_iidfile: Path | None = None,
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
    configs = validate_base_configs(root, prerequisites)
    images = prerequisites.images
    capture_image_id = _image_id(images["capture_image_id"], name="retained capture image ID")
    capture_lock_image_id = capture_image_id
    if owned_capture_image is not None:
        if capture_iidfile is None:
            raise ValueError("study capture IID file is required for an owned capture image")
        capture_lock = load_capture_image_lock(root / "docker" / "capture" / "image-lock.json")
        validate_capture_dockerfile(
            (root / "docker" / "capture" / "Dockerfile").read_text(encoding="utf-8"),
            capture_lock,
        )
        capture_lock_image_id = capture_lock.expected_capture_image_id
        _require(
            capture_image_id == capture_lock_image_id,
            "study capture image must equal the checked image lock before rebuild",
        )
    live_target = _study_target_image_identity(repository_root=root, runner=runner)
    _require(
        live_target["target_reference"] == images["target_reference"]
        and live_target["target_image_id"] == images["target_image_id"]
        and tuple(cast(list[JsonValue], live_target["target_repo_digests"])) == images["target_repo_digests"]
        and live_target["target_config_user"] == images["target_config_user"],
        "study image identities must exactly match approved prerequisite evidence",
    )
    if owned_capture_image is not None:
        assert capture_iidfile is not None
        _establish_phase_capture_image(
            root,
            phase="study",
            expected_image_id=capture_image_id,
            capture_lock_image_id=capture_lock_image_id,
            owned_capture_image=owned_capture_image,
            iidfile=capture_iidfile,
            runner=runner,
        )
    live_capture_image_id = _study_capture_image_identity(
        repository_root=root,
        capture_image_id=capture_image_id,
        runner=runner,
    )
    _require(
        live_capture_image_id == capture_image_id,
        "study image identities must exactly match approved prerequisite evidence",
    )
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


def _load_reference_trace(run_directory: Path) -> TrafficTrace:
    capture_path = run_directory / "capture.json"
    reference_path = run_directory / "reference.pcapng"
    validate_capture_pair(capture_path, reference_path, deadline=None)
    metadata = parse_capture_metadata(capture_path.read_bytes(), source=capture_path)
    return read_pcapng_bytes(reference_path.read_bytes(), metadata, source=reference_path)


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
    source: TrafficTrace,
    reproduction: TrafficTrace,
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
    owned_capture_image = _PhaseCaptureImage(tag="")
    primary: BaseException | None = None
    try:
        url = validate_endpoint_url(url)
        study_id = validate_study_id(study_id)
        owned_capture_image.tag = _phase_capture_tag(study_id, "study")
        results_path = root / "examples" / "validation_study" / "results.json"
        _require(not _path_entry_exists(results_path), f"study result target already exists: {results_path}")
        with tempfile.TemporaryDirectory(prefix=f"trafficlab-validation-{study_id}-capture-") as temporary_directory:
            prerequisites, configs, identity, prerequisite_content = _validated_study_inputs(
                url,
                study_id,
                prerequisite_path,
                repository_root=root,
                runner=runner,
                owned_capture_image=owned_capture_image,
                capture_iidfile=Path(temporary_directory) / "capture.iid",
            )
        specifications = _primary_run_specs(root, study_id, configs)
        workloads = {spec.name: spec for spec in workload_specs(url)}
        object_size = cast(int, prerequisites.capability["object_size_bytes"])
        records: list[StudyRunRecord] = []
        traces: dict[tuple[WorkloadName, int], TrafficTrace] = {}
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
    except TrafficlabError as error:
        primary = error
        raise
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        primary = TrafficlabError(
            f"Validation Study failed validation: {error}",
            corrective_action="preserve the ignored evidence, correct the failure, and restart with a new study ID",
        )
        raise primary from error
    except BaseException as error:
        primary = error
        raise
    finally:
        if owned_capture_image.build_attempted:
            try:
                _remove_owned_phase_capture_image(
                    owned_capture_image,
                    phase="study",
                    repository_root=root,
                    runner=runner,
                )
            except BaseException as cleanup_error:
                if primary is None:
                    raise TrafficlabError(
                        f"Validation Study study capture image cleanup failed: {cleanup_error}",
                        corrective_action=(
                            "preserve the study evidence, remove the exact owned capture image tag, "
                            "and restart with a new study ID"
                        ),
                    ) from cleanup_error
                primary.add_note(f"study capture image cleanup failed: {cleanup_error}")


def _audit_primary_record(
    repository_root: Path,
    record: StudyRunRecord,
    object_size_bytes: int,
) -> TrafficTrace:
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
        traces: dict[tuple[WorkloadName, int], TrafficTrace] = {}
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

    from scripts.audit_validation_study import _audit_staged_bundle  # pyright: ignore[reportPrivateUsage]

    root = repository_root.resolve()
    checked_study_id = validate_study_id(study_id)
    try:
        _require(candidate.name == checked_study_id, "candidate ID must equal the requested destination ID")
    except ValueError as error:
        raise TrafficlabError(
            f"Validation Study candidate ID is incompatible with the requested destination: {error}",
            corrective_action="preserve the candidate and publish it only to its frozen study ID",
        ) from error

    def audit(candidate_root: Path) -> None:
        _audit_staged_bundle(candidate_root, repository=root, source_candidate=candidate.resolve())

    return publish_accepted_bundle(
        candidate,
        root / "examples" / "validation_study" / "evidence",
        checked_study_id,
        audit,
    )


@dataclass(frozen=True, slots=True)
class _CandidateTraining:
    workload: WorkloadName
    repeat: int
    directory: Path
    config: ExperimentConfig
    contents: Mapping[str, bytes]
    metadata: CaptureMetadata
    reference: TrafficTrace
    observation_window_seconds: float
    runtime_seconds: float
    checkpoint: CheckpointState
    comparison: ComparisonResult


def _candidate_root(repository_root: Path, study_id: str) -> Path:
    return repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / study_id


def _collection_attempt_root(repository_root: Path, study_id: str) -> Path:
    return repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / study_id


def _prerequisite_raw_archive_path(repository_root: Path, study_id: str) -> Path:
    return _collection_attempt_root(repository_root, study_id) / "prerequisites.raw.json"


def _prerequisite_rotation_journal_path(repository_root: Path, study_id: str) -> Path:
    return _collection_attempt_root(repository_root, study_id) / "prerequisites-rotation.json"


def _archive_prerequisite_raw_document(
    repository_root: Path,
    *,
    study_id: str,
    content: bytes,
) -> bytes:
    """Persist the byte-exact canonical prerequisite document beside its irreversible attempt."""

    archive = _prerequisite_raw_archive_path(repository_root, study_id)

    def validate(persisted: bytes) -> None:
        parsed = parse_prerequisite_results(persisted, repository_root=repository_root)
        _require(render_prerequisite_results(parsed) == content, "archived prerequisite document is not canonical")

    if _path_entry_exists(archive):
        persisted = _read_regular_prerequisite_rotation_target(
            archive,
            name="archived prerequisite document",
        )
    else:
        _publish_prerequisite_rotation_exclusive_file(
            archive,
            content,
            validate=validate,
            name="archived prerequisite document",
        )
        persisted = _read_regular_prerequisite_rotation_target(
            archive,
            name="archived prerequisite document",
        )
    _require(persisted == content, "archived prerequisite document must equal the canonical publication bytes")
    validate(persisted)
    return persisted


def _archive_preserved_pre_user_agent_r6_predecessor(
    repository_root: Path,
    *,
    content: bytes,
    runner: CommandRunner,
) -> bytes:
    """Persist the exact retained r6 predecessor without exposing a general legacy codec."""

    archive = _prerequisite_raw_archive_path(repository_root, _PRESERVED_PRE_USER_AGENT_R6_STUDY_ID)

    def validate(persisted: bytes) -> None:
        _parse_preserved_pre_user_agent_r6_predecessor(
            persisted,
            repository_root=repository_root,
            runner=runner,
        )

    if _path_entry_exists(archive):
        persisted = _read_regular_prerequisite_rotation_target(
            archive,
            name="archived preserved pre-User-Agent r6 prerequisite document",
        )
    else:
        _publish_prerequisite_rotation_exclusive_file(
            archive,
            content,
            validate=validate,
            name="archived preserved pre-User-Agent r6 prerequisite document",
        )
        persisted = _read_regular_prerequisite_rotation_target(
            archive,
            name="archived preserved pre-User-Agent r6 prerequisite document",
        )
    _require(persisted == content, "archived preserved pre-User-Agent r6 document must equal canonical root bytes")
    validate(persisted)
    return persisted


def _begin_phase_attempt(
    repository_root: Path, *, study_id: str, url: str, phase: Literal["prerequisites", "collection"]
) -> Path:
    """Persist one irreversible phase marker immediately after input syntax checks."""

    attempt = _collection_attempt_root(repository_root, study_id)
    marker = attempt / f"{phase}.json"
    if _path_entry_exists(marker):
        raise TrafficlabError(
            f"Validation Study {phase} already began for {study_id}; use a new study ID",
            corrective_action="preserve the failed attempt and restart with a new study ID",
        )
    _write_candidate_bytes(
        marker,
        _canonical_json(cast(JsonObject, {"phase": phase, "study_id": study_id, "url": url})),
    )
    return attempt


def _render_successful_prerequisite_marker(
    *,
    study_id: str,
    url: str,
    prerequisite_content: bytes,
) -> bytes:
    """Render the sole success-visible record for one canonical prerequisite document."""

    return _canonical_json(
        cast(
            JsonObject,
            {
                "phase": "prerequisites",
                "prerequisites_identity": _candidate_identity(prerequisite_content),
                "study_id": study_id,
                "url": url,
            },
        )
    )


def _require_successful_prerequisite_marker_content(
    content: bytes,
    *,
    study_id: str,
    url: str,
    prerequisite_content: bytes,
) -> None:
    """Validate an in-memory success marker against the exact prerequisite bytes it authorizes."""

    document = _exact_object(
        _load_json(content),
        ("phase", "prerequisites_identity", "study_id", "url"),
        name="successful prerequisite marker",
    )
    _require(_canonical_json(cast(JsonObject, document)) == content, "successful prerequisite marker must be canonical")
    _require(
        document["phase"] == "prerequisites" and document["study_id"] == study_id and document["url"] == url,
        "collection requires a matching successful prerequisite marker",
    )
    _require(
        _retained_identity(document["prerequisites_identity"], name="successful prerequisite marker identity")
        == _candidate_identity(prerequisite_content),
        "collection requires a matching successful prerequisite marker",
    )


def _prerequisite_rotation_expected_targets(repository_root: Path, study_id: str) -> tuple[tuple[str, Path, bool], ...]:
    study_root = repository_root / "examples" / "validation_study"
    attempt = _collection_attempt_root(repository_root, study_id)
    return (
        ("archive", attempt / "prerequisites.raw.json", True),
        ("config-short", study_root / "configs" / "short.toml", False),
        ("config-streaming", study_root / "configs" / "streaming.toml", False),
        ("config-bursty", study_root / "configs" / "bursty.toml", False),
        ("root", study_root / "prerequisites.json", False),
        ("marker", attempt / "prerequisites-success.json", True),
    )


def _prerequisite_rotation_relative_path(repository_root: Path, path: Path, *, name: str) -> str:
    try:
        return path.relative_to(repository_root).as_posix()
    except ValueError as error:
        raise ValueError(f"{name} must be beneath the repository root") from error


def _prerequisite_rotation_sibling_path(
    repository_root: Path,
    value: object,
    *,
    destination: Path,
    suffix: str,
    name: str,
) -> Path:
    relative = _repository_relative_path(value, repository_root=repository_root, name=name)
    path = repository_root / Path(*PurePosixPath(relative).parts)
    _require(
        path.parent == destination.parent
        and path.name.startswith(f".{destination.name}.")
        and path.name.endswith(suffix),
        f"{name} must be an exact prerequisite rotation sibling path",
    )
    return path


def _render_prerequisite_rotation_journal(
    repository_root: Path,
    *,
    study_id: str,
    targets: Sequence[_PrerequisiteRotationTarget],
) -> bytes:
    entries: list[object] = []
    for target in targets:
        stage = target.stage
        backup = target.backup
        if stage is None:
            raise ValueError("prerequisite rotation journal requires every staged target")
        entries.append(
            {
                "backup": (
                    _prerequisite_rotation_relative_path(repository_root, backup, name="rotation backup")
                    if backup is not None
                    else None
                ),
                "before_identity": target.before_identity,
                "destination": _prerequisite_rotation_relative_path(
                    repository_root,
                    target.destination,
                    name="rotation destination",
                ),
                "kind": target.kind,
                "must_be_absent": target.must_be_absent,
                "stage": _prerequisite_rotation_relative_path(
                    repository_root,
                    stage,
                    name="rotation stage",
                ),
                "target_identity": target.target_identity,
            }
        )
    return _canonical_json(
        cast(
            JsonObject,
            {
                "phase": "prerequisite-rotation",
                "schema_version": 1,
                "study_id": study_id,
                "targets": entries,
            },
        )
    )


def _parse_prerequisite_rotation_journal(
    content: bytes,
    *,
    repository_root: Path,
    journal: Path,
) -> tuple[str, list[_PrerequisiteRotationTarget]]:
    document = _exact_object(
        _load_json(content),
        ("phase", "schema_version", "study_id", "targets"),
        name="prerequisite rotation journal",
    )
    _require(
        _canonical_json(cast(JsonObject, document)) == content,
        "prerequisite rotation journal must be canonical",
    )
    _require(document["phase"] == "prerequisite-rotation", "prerequisite rotation journal phase is invalid")
    _require(
        _strict_int(document["schema_version"], name="prerequisite rotation journal schema_version") == 1,
        "prerequisite rotation journal schema_version is unsupported",
    )
    study_id = validate_study_id(_strict_string(document["study_id"], name="prerequisite rotation journal study_id"))
    _require(
        journal == _prerequisite_rotation_journal_path(repository_root, study_id),
        "prerequisite rotation journal must use its exact attempt path",
    )
    raw_targets_value = document["targets"]
    _require(type(raw_targets_value) is list, "prerequisite rotation journal targets must be an array")
    raw_targets = cast(list[object], raw_targets_value)
    expected = _prerequisite_rotation_expected_targets(repository_root, study_id)
    _require(
        len(raw_targets) == len(expected),
        "prerequisite rotation journal must contain its exact target count",
    )
    parsed: list[_PrerequisiteRotationTarget] = []
    for raw_target, (kind, destination, must_be_absent) in zip(raw_targets, expected, strict=True):
        target = _exact_object(
            raw_target,
            ("backup", "before_identity", "destination", "kind", "must_be_absent", "stage", "target_identity"),
            name="prerequisite rotation journal target",
        )
        _require(target["kind"] == kind, "prerequisite rotation journal target order is invalid")
        _require(
            _strict_bool(target["must_be_absent"], name="prerequisite rotation target must_be_absent")
            == must_be_absent,
            "prerequisite rotation journal target absence policy is invalid",
        )
        destination_relative = _repository_relative_path(
            target["destination"],
            repository_root=repository_root,
            name="prerequisite rotation destination",
        )
        _require(
            destination_relative
            == _prerequisite_rotation_relative_path(
                repository_root,
                destination,
                name="expected prerequisite rotation destination",
            ),
            "prerequisite rotation journal destination is invalid",
        )
        stage = _prerequisite_rotation_sibling_path(
            repository_root,
            target["stage"],
            destination=destination,
            suffix=".tmp",
            name="prerequisite rotation stage",
        )
        before_value = target["before_identity"]
        backup_value = target["backup"]
        before_identity = (
            None
            if before_value is None
            else _retained_identity(before_value, name="prerequisite rotation prior identity")
        )
        backup = (
            None
            if backup_value is None
            else _prerequisite_rotation_sibling_path(
                repository_root,
                backup_value,
                destination=destination,
                suffix=".bak",
                name="prerequisite rotation backup",
            )
        )
        _require(
            (before_identity is None) == (backup is None),
            "prerequisite rotation journal backup and prior identity must agree",
        )
        if must_be_absent:
            _require(
                before_identity is None and backup is None,
                "prerequisite rotation absent target cannot have prior bytes",
            )
        parsed.append(
            _PrerequisiteRotationTarget(
                kind=kind,
                destination=destination,
                stage=stage,
                backup=backup,
                before_identity=before_identity,
                target_identity=_retained_identity(
                    target["target_identity"],
                    name="prerequisite rotation target identity",
                ),
                must_be_absent=must_be_absent,
            )
        )
    return study_id, parsed


def _publish_prerequisite_rotation_journal(
    repository_root: Path,
    *,
    study_id: str,
    targets: Sequence[_PrerequisiteRotationTarget],
) -> Path:
    journal = _prerequisite_rotation_journal_path(repository_root, study_id)
    content = _render_prerequisite_rotation_journal(repository_root, study_id=study_id, targets=targets)

    def validate(persisted: bytes) -> None:
        parsed_study_id, _parsed_targets = _parse_prerequisite_rotation_journal(
            persisted,
            repository_root=repository_root,
            journal=journal,
        )
        _require(parsed_study_id == study_id, "prerequisite rotation journal study ID changed")

    _publish_prerequisite_rotation_exclusive_file(
        journal,
        content,
        validate=validate,
        name="prerequisite rotation journal",
    )
    return journal


def _read_prerequisite_rotation_target_if_present(destination: Path, *, name: str) -> bytes | None:
    if not _path_entry_exists(destination):
        return None
    return _read_regular_prerequisite_rotation_target(destination, name=name)


def _remove_owned_prerequisite_rotation_path(
    path: Path,
    *,
    identity: JsonObject,
    name: str,
) -> None:
    content = _read_prerequisite_rotation_target_if_present(path, name=name)
    if content is None:
        return
    _require(
        _candidate_identity(content) == identity,
        f"{name} does not match its transaction-owned identity",
    )
    path.unlink()
    _fsync_prerequisite_rotation_directory(path)


def _restore_prerequisite_rotation_target(target: _PrerequisiteRotationTarget) -> None:
    """Restore one journal-owned target only when each extant byte sequence is expected."""

    destination_content = _read_prerequisite_rotation_target_if_present(
        target.destination,
        name=f"prerequisite rotation {target.kind} destination",
    )
    stage = target.stage
    if stage is None:
        raise ValueError("prerequisite rotation target must retain its staged path")
    if target.before_identity is None:
        if destination_content is not None:
            _require(
                _candidate_identity(destination_content) == target.target_identity,
                f"prerequisite rotation {target.kind} destination is not transaction-owned",
            )
            target.destination.unlink()
            _commit_prerequisite_fsync(target.destination)
    else:
        backup = target.backup
        if backup is None:
            raise ValueError("prerequisite rotation prior bytes require a backup")
        backup_content = _read_prerequisite_rotation_target_if_present(
            backup,
            name=f"prerequisite rotation {target.kind} backup",
        )
        if backup_content is not None:
            _require(
                _candidate_identity(backup_content) == target.before_identity,
                f"prerequisite rotation {target.kind} backup bytes changed",
            )
        destination_identity = _candidate_identity(destination_content) if destination_content is not None else None
        if destination_identity != target.before_identity:
            _require(
                destination_identity is None or destination_identity == target.target_identity,
                f"prerequisite rotation {target.kind} destination is not transaction-owned",
            )
            _require(backup_content is not None, f"prerequisite rotation {target.kind} backup is unavailable")
            os.replace(backup, target.destination)
            _commit_prerequisite_fsync(target.destination)
        restored = _read_regular_prerequisite_rotation_target(
            target.destination,
            name=f"restored prerequisite rotation {target.kind} destination",
        )
        _require(
            _candidate_identity(restored) == target.before_identity,
            f"prerequisite rotation {target.kind} restore bytes changed",
        )
        if backup_content is not None:
            _remove_owned_prerequisite_rotation_path(
                backup,
                identity=target.before_identity,
                name=f"prerequisite rotation {target.kind} backup",
            )
    _remove_owned_prerequisite_rotation_path(
        stage,
        identity=target.target_identity,
        name=f"prerequisite rotation {target.kind} stage",
    )


def _rollback_prerequisite_rotation(
    committed: Sequence[_PrerequisiteRotationTarget],
) -> tuple[list[str], list[_PrerequisiteRotationTarget]]:
    """Restore committed prerequisite targets in reverse order after a controlled failure."""

    failures: list[str] = []
    failed_targets: list[_PrerequisiteRotationTarget] = []
    for target in reversed(committed):
        try:
            _restore_prerequisite_rotation_target(target)
        except (OSError, ValueError) as error:
            failed_targets.append(target)
            retained = (
                f"; retained recovery backup: {target.backup}"
                if target.backup is not None and _path_entry_exists(target.backup)
                else ""
            )
            failures.append(f"{target.destination}: {error}{retained}")
    return failures, failed_targets


def _cleanup_prerequisite_rotation_staging(
    targets: Sequence[_PrerequisiteRotationTarget],
    *,
    strict: bool,
) -> list[str]:
    """Discard only journal-owned staging and backup files after restore or success."""

    failures: list[str] = []
    for target in targets:
        owned: list[tuple[Path, JsonObject, str]] = []
        if target.stage is not None:
            owned.append(
                (
                    target.stage,
                    target.target_identity,
                    f"prerequisite rotation {target.kind} stage",
                )
            )
        if target.backup is not None and target.before_identity is not None:
            owned.append(
                (
                    target.backup,
                    target.before_identity,
                    f"prerequisite rotation {target.kind} backup",
                )
            )
        for path, identity, name in owned:
            try:
                _remove_owned_prerequisite_rotation_path(path, identity=identity, name=name)
            except (OSError, ValueError) as error:
                if strict:
                    failures.append(f"{path}: {error}")
    return failures


def _prerequisite_rotation_is_complete(
    repository_root: Path,
    *,
    study_id: str,
    targets: Sequence[_PrerequisiteRotationTarget],
) -> bool:
    try:
        for target in targets:
            content = _read_prerequisite_rotation_target_if_present(
                target.destination,
                name=f"prerequisite rotation {target.kind} destination",
            )
            if content is None or _candidate_identity(content) != target.target_identity:
                return False
        root_target = next(target for target in targets if target.kind == "root")
        marker_target = next(target for target in targets if target.kind == "marker")
        prerequisite_content = _read_regular_prerequisite_rotation_target(
            root_target.destination,
            name="canonical prerequisite target",
        )
        prerequisite = parse_prerequisite_results(prerequisite_content, repository_root=repository_root)
        _require(prerequisite.study_id == study_id, "completed rotation prerequisite study ID is invalid")
        marker_content = _read_regular_prerequisite_rotation_target(
            marker_target.destination,
            name="completed prerequisite success marker",
        )
        _require_successful_prerequisite_marker_content(
            marker_content,
            study_id=study_id,
            url=prerequisite.url,
            prerequisite_content=prerequisite_content,
        )
        validate_base_configs(repository_root, prerequisite)
    except (OSError, TypeError, ValueError, TrafficlabError):
        return False
    return True


def _clear_prerequisite_rotation_journal(journal: Path) -> None:
    _read_regular_prerequisite_rotation_target(journal, name="prerequisite rotation journal")
    journal.unlink()
    _fsync_prerequisite_rotation_directory(journal)


def _recover_prerequisite_rotation_journal(repository_root: Path, journal: Path) -> None:
    content = _read_regular_prerequisite_rotation_target(journal, name="prerequisite rotation journal")
    study_id, targets = _parse_prerequisite_rotation_journal(
        content,
        repository_root=repository_root,
        journal=journal,
    )
    if _prerequisite_rotation_is_complete(repository_root, study_id=study_id, targets=targets):
        failures = _cleanup_prerequisite_rotation_staging(targets, strict=True)
    else:
        failures, failed_targets = _rollback_prerequisite_rotation(targets)
        failed_target_ids = {id(target) for target in failed_targets}
        failures.extend(
            _cleanup_prerequisite_rotation_staging(
                [target for target in targets if id(target) not in failed_target_ids],
                strict=True,
            )
        )
    if failures:
        raise ValueError(
            f"could not recover prerequisite rotation journal {journal}; retained recovery paths: {'; '.join(failures)}"
        )
    _clear_prerequisite_rotation_journal(journal)


def _recover_incomplete_prerequisite_rotations(repository_root: Path) -> None:
    """Recover each durable incomplete rotation before any new phase marker is consumed."""

    attempts = repository_root / "examples" / "validation_study" / ".study-work" / "attempts"
    try:
        mode = attempts.lstat().st_mode
    except FileNotFoundError:
        return
    except OSError as error:
        raise ValueError(f"could not inspect prerequisite attempt directory {attempts}: {error}") from error
    _require(
        stat.S_ISDIR(mode) and not stat.S_ISLNK(mode),
        "prerequisite attempt directory must be a regular directory",
    )
    try:
        attempt_paths = sorted(attempts.iterdir(), key=lambda path: path.name)
    except OSError as error:
        raise ValueError(f"could not enumerate prerequisite attempts {attempts}: {error}") from error
    for attempt in attempt_paths:
        try:
            mode = attempt.lstat().st_mode
        except OSError as error:
            raise ValueError(f"could not inspect prerequisite attempt {attempt}: {error}") from error
        _require(
            stat.S_ISDIR(mode) and not stat.S_ISLNK(mode),
            "prerequisite attempt must be a regular directory",
        )
        journal = attempt / "prerequisites-rotation.json"
        if _path_entry_exists(journal):
            _recover_prerequisite_rotation_journal(repository_root, journal)


def _bootstrap_current_prerequisite_archive(
    repository_root: Path,
    prerequisite_path: Path,
    *,
    runner: CommandRunner,
) -> None:
    """Preserve a schema-1 canonical root that predates per-attempt raw archives."""

    if not _path_entry_exists(prerequisite_path):
        return
    content = _read_regular_prerequisite_rotation_target(prerequisite_path, name="canonical prerequisite target")
    try:
        prior = parse_prerequisite_results(content, repository_root=repository_root)
    except ValueError:
        prior = _parse_preserved_pre_user_agent_r6_predecessor(
            content,
            repository_root=repository_root,
            runner=runner,
        )
        _require_successful_prerequisite_attempt(
            repository_root,
            study_id=prior.study_id,
            url=prior.url,
            prerequisite_content=content,
            require_archive=False,
        )
        _archive_preserved_pre_user_agent_r6_predecessor(
            repository_root,
            content=content,
            runner=runner,
        )
        return
    _require_successful_prerequisite_attempt(
        repository_root,
        study_id=prior.study_id,
        url=prior.url,
        prerequisite_content=content,
        require_archive=False,
    )
    _archive_prerequisite_raw_document(
        repository_root,
        study_id=prior.study_id,
        content=content,
    )


def _complete_prerequisite_attempt(  # pyright: ignore[reportUnusedFunction]
    repository_root: Path,
    *,
    study_id: str,
    url: str,
    prerequisite_content: bytes,
) -> None:
    """Record a prerequisite success only after its canonical publication succeeds."""

    archived = _archive_prerequisite_raw_document(
        repository_root,
        study_id=study_id,
        content=prerequisite_content,
    )
    marker = _collection_attempt_root(repository_root, study_id) / "prerequisites-success.json"
    _publish_prerequisite_rotation_exclusive_file(
        marker,
        _render_successful_prerequisite_marker(
            study_id=study_id,
            url=url,
            prerequisite_content=archived,
        ),
        validate=lambda persisted: _require_successful_prerequisite_marker_content(
            persisted,
            study_id=study_id,
            url=url,
            prerequisite_content=archived,
        ),
        name="successful prerequisite marker",
    )


def _require_successful_prerequisite_attempt(
    repository_root: Path,
    *,
    study_id: str,
    url: str,
    prerequisite_content: bytes,
    require_archive: bool = True,
) -> None:
    """Refuse collection unless the matching prerequisite phase completed successfully."""

    marker = _collection_attempt_root(repository_root, study_id) / "prerequisites-success.json"
    try:
        _require(
            not _path_entry_exists(_prerequisite_rotation_journal_path(repository_root, study_id)),
            "collection requires a completed prerequisite rotation",
        )
        content = _read_regular_prerequisite_rotation_target(marker, name="successful prerequisite marker")
        _require_successful_prerequisite_marker_content(
            content,
            study_id=study_id,
            url=url,
            prerequisite_content=prerequisite_content,
        )
        if require_archive:
            archived = _read_regular_prerequisite_rotation_target(
                _prerequisite_raw_archive_path(repository_root, study_id),
                name="archived prerequisite document",
            )
            _require(
                _candidate_identity(archived) == _candidate_identity(prerequisite_content),
                "collection requires a matching successful prerequisite marker",
            )
    except (OSError, TypeError, ValueError) as error:
        raise TrafficlabError(
            "Validation Study collection requires a matching successful prerequisite marker",
            corrective_action="complete the same-study prerequisite phase before collection",
        ) from error


def _publish_prerequisite_rotation_target(target: _PrerequisiteRotationTarget) -> None:
    """Publish one staged target without overwriting an absent-only archive or marker."""

    stage = target.stage
    if stage is None:
        raise ValueError("prerequisite rotation target must be staged before publication")
    if target.must_be_absent:
        os.link(stage, target.destination)
    else:
        os.replace(stage, target.destination)


def _commit_prerequisite_rotation(
    repository_root: Path,
    *,
    prerequisite_path: Path,
    configs: Sequence[tuple[ExperimentConfig, Path, bytes]],
    result: PrerequisiteResults,
    study_id: str,
    url: str,
    runner: CommandRunner,
) -> None:
    """Publish the coupled prerequisite artifacts as one marker-last rollback transaction."""

    root = repository_root.resolve()
    _bootstrap_current_prerequisite_archive(root, prerequisite_path, runner=runner)
    prerequisite_content = render_prerequisite_results(result)
    archive = _prerequisite_raw_archive_path(root, study_id)
    marker = _collection_attempt_root(root, study_id) / "prerequisites-success.json"
    marker_content = _render_successful_prerequisite_marker(
        study_id=study_id,
        url=url,
        prerequisite_content=prerequisite_content,
    )

    targets: list[tuple[str, Path, bytes, Callable[[Path, bytes], None], bool]] = []

    def validate_archive(_stage: Path, persisted: bytes) -> None:
        parsed = parse_prerequisite_results(persisted, repository_root=root)
        _require(
            render_prerequisite_results(parsed) == prerequisite_content, "staged prerequisite archive is not canonical"
        )

    targets.append(("archive", archive, prerequisite_content, validate_archive, True))
    expected = {
        kind: destination
        for kind, destination, _must_be_absent in _prerequisite_rotation_expected_targets(root, study_id)
    }
    config_kinds: list[str] = []
    for config, destination, content in configs:
        workload = _workload_for_config(config)
        kind = f"config-{workload.name}"
        _require(kind in expected, "prerequisite rotation has an unknown checked config workload")
        _require(destination == expected[kind], "prerequisite rotation checked config destination is invalid")
        config_kinds.append(kind)

        def validate_config(
            stage: Path,
            persisted: bytes,
            *,
            expected: ExperimentConfig = config,
            expected_content: bytes = content,
        ) -> None:
            _require(persisted == expected_content, "staged checked config bytes changed before validation")
            _require(
                load_experiment(stage) == expected, "staged checked config must reload to its exact absolute oracle"
            )

        targets.append((kind, destination, content, validate_config, False))

    _require(
        tuple(config_kinds) == ("config-short", "config-streaming", "config-bursty"),
        "prerequisite rotation must publish its exact checked config order",
    )

    def validate_prerequisite(_stage: Path, persisted: bytes) -> None:
        parsed = parse_prerequisite_results(persisted, repository_root=root)
        _require(
            render_prerequisite_results(parsed) == prerequisite_content, "staged prerequisite JSON is not canonical"
        )

    def validate_marker(_stage: Path, persisted: bytes) -> None:
        _require_successful_prerequisite_marker_content(
            persisted,
            study_id=study_id,
            url=url,
            prerequisite_content=prerequisite_content,
        )

    targets.extend(
        (
            ("root", prerequisite_path, prerequisite_content, validate_prerequisite, False),
            ("marker", marker, marker_content, validate_marker, True),
        )
    )

    prepared: list[_PrerequisiteRotationTarget] = []
    journal: Path | None = None
    cleanup_staging = False
    strict_cleanup_complete = False
    retain_recovery = False
    try:
        for kind, destination, content, validate, must_be_absent in targets:
            previous = (
                _read_regular_prerequisite_rotation_target(destination, name="prerequisite rotation target")
                if _path_entry_exists(destination)
                else None
            )
            if must_be_absent:
                _require(previous is None, f"prerequisite rotation target must be absent: {destination}")
            target = _PrerequisiteRotationTarget(
                kind=kind,
                destination=destination,
                stage=None,
                backup=None,
                before_identity=_candidate_identity(previous) if previous is not None else None,
                target_identity=_candidate_identity(content),
                must_be_absent=must_be_absent,
            )
            prepared.append(target)
            if previous is not None:
                target.backup = _stage_prerequisite_rotation_file(
                    destination,
                    previous,
                    validate=lambda _stage, persisted, expected=previous: _require(
                        persisted == expected, "staged prerequisite rollback bytes changed before validation"
                    ),
                    suffix=".bak",
                )
            target.stage = _stage_prerequisite_rotation_file(destination, content, validate=validate)

        journal = _publish_prerequisite_rotation_journal(root, study_id=study_id, targets=prepared)
        cleanup_staging = False
        committed: list[_PrerequisiteRotationTarget] = []
        try:
            for target in prepared[:-1]:
                _publish_prerequisite_rotation_target(target)
                committed.append(target)
                _commit_prerequisite_fsync(target.destination)
                _after_prerequisite_rotation_commit(target.destination)
            validate_base_configs(root, result)
            marker_target = prepared[-1]
            _publish_prerequisite_rotation_target(marker_target)
            committed.append(marker_target)
            _commit_prerequisite_fsync(marker_target.destination)
            _after_prerequisite_rotation_commit(marker_target.destination)
        except (OSError, TypeError, ValueError, TrafficlabError) as error:
            rollback_failures, _failed_targets = _rollback_prerequisite_rotation(committed)
            if rollback_failures:
                retain_recovery = True
                raise ValueError(
                    f"prerequisite rotation rollback failed after {error}; retained recovery journal "
                    f"{journal}: {'; '.join(rollback_failures)}"
                ) from error
            cleanup_failures = _cleanup_prerequisite_rotation_staging(prepared, strict=True)
            if cleanup_failures:
                retain_recovery = True
                raise ValueError(
                    f"prerequisite rotation rollback cleanup failed after {error}; retained recovery journal "
                    f"{journal}: {'; '.join(cleanup_failures)}"
                ) from error
            strict_cleanup_complete = True
            _clear_prerequisite_rotation_journal(journal)
            journal = None
            raise
        cleanup_failures = _cleanup_prerequisite_rotation_staging(prepared, strict=True)
        if cleanup_failures:
            retain_recovery = True
            raise ValueError(
                f"prerequisite rotation postcommit cleanup failed; retained recovery journal "
                f"{journal}: {'; '.join(cleanup_failures)}"
            )
        strict_cleanup_complete = True
        _clear_prerequisite_rotation_journal(journal)
        journal = None
    except (OSError, TypeError, ValueError, TrafficlabError):
        if journal is None and not retain_recovery and not strict_cleanup_complete:
            cleanup_staging = True
        raise
    finally:
        if cleanup_staging:
            _cleanup_prerequisite_rotation_staging(prepared, strict=False)


def _write_candidate_bytes(path: Path, content: bytes) -> None:
    _require(not _path_entry_exists(path), f"collection output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except OSError as error:
        raise ValueError(f"could not write collection output {path}: {error}") from error


def _candidate_identity(content: bytes) -> JsonObject:
    return cast(JsonObject, identify_bytes(content).as_dict())


def _candidate_capture_lineage(capture: bytes, environment: Mapping[str, object]) -> JsonObject:
    return cast(
        JsonObject,
        {
            "capture_identity": _candidate_identity(capture),
            "capture_image_id": cast(JsonValue, environment["capture_image_id"]),
            "capture_image_reference": cast(JsonValue, environment["capture_image_reference"]),
            "capture_tool_version": cast(JsonValue, environment["capture_tool_version"]),
            "target_image_id": cast(JsonValue, environment["target_image_id"]),
            "target_image_reference": cast(JsonValue, environment["target_image_reference"]),
        },
    )


def _candidate_portable_config(config: ExperimentConfig, destination: Path) -> ExperimentConfig:
    _require(config.run.directory.is_absolute(), "collection realized run directory must be absolute")
    _require(
        len(config.target.mounts) == 1 and config.target.mounts[0].source.is_absolute(), "collection must use one mount"
    )
    relative_run = Path(os.path.relpath(config.run.directory, start=destination.parent))
    relative_mount = Path(os.path.relpath(config.target.mounts[0].source, start=destination.parent))
    mount = config.target.mounts[0].model_copy(update={"source": relative_mount})
    target = config.target.model_copy(update={"mounts": (mount,)})
    return config.model_copy(
        update={"run": config.run.model_copy(update={"directory": relative_run}), "target": target}
    )


def _write_candidate_config_pair(config: ExperimentConfig, portable_path: Path, realized_path: Path) -> None:
    portable = _candidate_portable_config(config, portable_path)
    _write_candidate_bytes(portable_path, render_effective_config(portable))
    _require(load_experiment(portable_path) == config, "candidate portable configuration must realize exactly")
    _write_candidate_bytes(realized_path, render_effective_config(config))
    _require(load_experiment(realized_path) == config, "candidate realized configuration must reload exactly")


def _load_candidate_training(
    directory: Path,
    *,
    workload: WorkloadName,
    repeat: int,
    config: ExperimentConfig,
    runtime_seconds: float,
) -> _CandidateTraining:
    contents = _read_exact_artifact_set(directory)
    metadata = parse_capture_metadata(contents["capture.json"], source=directory / "capture.json")
    reference, window = normalize_reference(
        read_pcapng_bytes(contents["reference.pcapng"], metadata, source=directory / "reference.pcapng")
    )
    context = make_strategy_context(
        config,
        reference,
        window,
        directory,
        experiment_identity=identify_bytes(contents["experiment.toml"]),
        reference_identity=identify_bytes(contents["reference.pcapng"]),
        capture_identity=identify_bytes(contents["capture.json"]),
    )
    checkpoint = parse_checkpoint(contents["checkpoint.json"], context.compatibility)
    _require(
        {candidate.family for candidate in checkpoint.population} == set(FAMILY_ORDER),
        "each training run must retain all three enabled model families",
    )
    return _CandidateTraining(
        workload=workload,
        repeat=repeat,
        directory=directory,
        config=config,
        contents=contents,
        metadata=metadata,
        reference=reference,
        observation_window_seconds=window,
        runtime_seconds=runtime_seconds,
        checkpoint=checkpoint,
        comparison=parse_comparison_result(contents["similarity.json"]),
    )


def _candidate_training_record(training: _CandidateTraining, *, environment: Mapping[str, object]) -> JsonObject:
    relative = f"training/{training.workload}/r{training.repeat}"
    portable = f"configs/training-{training.workload}-r{training.repeat}.portable.toml"
    realized = f"configs/training-{training.workload}-r{training.repeat}.realized.toml"
    root = training.directory.parents[2]
    return cast(
        JsonObject,
        {
            "capture_lineage": _candidate_capture_lineage(training.contents["capture.json"], environment),
            "directory": relative,
            "portable_config": portable,
            "portable_config_identity": _candidate_identity((root / portable).read_bytes()),
            "realized_config": realized,
            "realized_config_identity": _candidate_identity((root / realized).read_bytes()),
            "reference_identity": _candidate_identity(training.contents["reference.pcapng"]),
            "repeat": training.repeat,
            "run_config_identity": _candidate_identity(training.contents["experiment.toml"]),
            "workload": training.workload,
        },
    )


def _candidate_fresh_record(training: _CandidateTraining) -> tuple[str, JsonObject]:
    path = f"fresh_simulation/{training.workload}/r{training.repeat}.json"
    return (
        path,
        cast(
            JsonObject,
            {
                "comparison_identity": _candidate_identity(training.contents["similarity.json"]),
                "generated_identity": _candidate_identity(training.contents["generated.pcapng"]),
                "path": path,
                "reference_identity": _candidate_identity(training.contents["reference.pcapng"]),
                "seed": training.config.run.final_seed,
                "training_directory": f"training/{training.workload}/r{training.repeat}",
                "training_model_identity": _candidate_identity(training.contents["best_model.json"]),
                "workload": training.workload,
                "repeat": training.repeat,
            },
        ),
    )


def _select_candidate_training(training: Sequence[_CandidateTraining]) -> tuple[JsonObject, ...]:
    selected: list[JsonObject] = []
    for workload in ("short", "streaming", "bursty"):
        candidates = [item for item in training if item.workload == workload]
        _require(len(candidates) == 3, f"training selection requires three {workload} repetitions")
        winner = min(candidates, key=lambda item: (-item.checkpoint.best_fitness, item.repeat))
        selected.append(
            cast(
                JsonObject,
                {
                    "best_model_identity": _candidate_identity(winner.contents["best_model.json"]),
                    "repeat": winner.repeat,
                    "training_directory": f"training/{workload}/r{winner.repeat}",
                    "workload": workload,
                },
            )
        )
    return tuple(selected)


def _candidate_score(result: ComparisonResult) -> JsonObject:
    return cast(
        JsonObject,
        {
            "aggregate": result.aggregate_score,
            "methods": cast(JsonObject, {method: result.methods[method].score for method in PUBLISHED_METHOD_ORDER}),
        },
    )


def _candidate_score_mean(scores: Sequence[JsonObject]) -> JsonObject:
    _require(bool(scores), "candidate score mean requires observations")
    methods = [cast(JsonObject, score["methods"]) for score in scores]
    return cast(
        JsonObject,
        {
            "aggregate": fmean(cast(float, score["aggregate"]) for score in scores),
            "methods": cast(
                JsonObject,
                {method: fmean(cast(float, item[method]) for item in methods) for method in PUBLISHED_METHOD_ORDER},
            ),
        },
    )


def _candidate_sample_summary(values: Sequence[float], *, name: str) -> JsonObject:
    _require(len(values) == 3, f"{name} requires exactly three observations")
    _require(all(math.isfinite(value) and value >= 0.0 for value in values), f"{name} values must be finite")
    return {
        "bootstrap": cast(JsonValue, bootstrap_interval(values, seed=_BOOTSTRAP_SEED).as_dict()),
        "mean": fmean(values),
        "sample_variance": variance(values),
    }


def _candidate_winner_family(training: _CandidateTraining) -> FamilyName:
    winner = next(
        candidate
        for candidate in training.checkpoint.population
        if candidate.identifier == training.checkpoint.best_identifier
    )
    return winner.family


def _candidate_natural_variation(training: Sequence[_CandidateTraining]) -> JsonObject:
    _require(len(training) == 3, "natural variation requires exactly three training records")
    settings = similarity_settings_identity(training[0].config.similarity)
    _require(
        all(similarity_settings_identity(item.config.similarity) == settings for item in training),
        "natural variation requires common frozen similarity settings",
    )
    pairs: list[JsonValue] = []
    for left_index in range(3):
        for right_index in range(left_index + 1, 3):
            left = training[left_index]
            right = training[right_index]
            left_reference, forward_window = normalize_reference(left.reference)
            right_reference, reverse_window = normalize_reference(right.reference)
            forward = _candidate_score(
                compare_traces(
                    left_reference,
                    align_generated(right.reference, forward_window),
                    forward_window,
                    left.config.similarity,
                )
            )
            reverse = _candidate_score(
                compare_traces(
                    right_reference,
                    align_generated(left.reference, reverse_window),
                    reverse_window,
                    right.config.similarity,
                )
            )
            pairs.append(
                cast(
                    JsonObject,
                    {
                        "forward": forward,
                        "left_repeat": left.repeat,
                        "reverse": reverse,
                        "right_repeat": right.repeat,
                        "symmetric_mean": _candidate_score_mean((forward, reverse)),
                    },
                )
            )
    return cast(
        JsonObject,
        {
            "pairs": pairs,
            "symmetric_mean": _candidate_score_mean(
                [cast(JsonObject, cast(JsonObject, pair)["symmetric_mean"]) for pair in pairs]
            ),
            "workload": training[0].workload,
        },
    )


def _candidate_controlled_weight_analysis(training: Sequence[_CandidateTraining]) -> list[JsonValue]:
    """Reweight fixed completed metrics without changing diagnostics or execution."""

    rows: list[JsonValue] = []
    alternate_weights: JsonObject = {
        "autocorrelation": 0.2,
        "frame_size_ks": 0.4,
        "iat_ks": 0.2,
        "multiscale_rate": 0.2,
    }
    for workload in ("short", "streaming", "bursty"):
        group = [item for item in training if item.workload == workload]
        selected = min(group, key=lambda item: (-item.checkpoint.best_fitness, item.repeat))
        baseline_weights = selected.config.similarity.method_weights.model_dump(mode="json")
        _require(
            baseline_weights == {method: 0.25 for method in PUBLISHED_METHOD_ORDER},
            "controlled weight analysis requires the frozen equal-weight baseline",
        )
        scores = _candidate_score(selected.comparison)
        components = cast(JsonObject, scores["methods"])
        alternate_aggregate = math.fsum(
            cast(float, alternate_weights[method]) * cast(float, components[method])
            for method in PUBLISHED_METHOD_ORDER
        )
        rendered_comparison = _load_json(render_comparison_result(selected.comparison))
        rendered_methods = cast(JsonObject, rendered_comparison["methods"])
        diagnostics = {
            method: cast(JsonObject, rendered_methods[method])["diagnostics"] for method in PUBLISHED_METHOD_ORDER
        }
        rows.append(
            cast(
                JsonObject,
                {
                    "alternative_aggregate": alternate_aggregate,
                    "alternative_weights": alternate_weights,
                    "baseline_aggregate": scores["aggregate"],
                    "baseline_weights": baseline_weights,
                    "components": components,
                    "diagnostics": diagnostics,
                    "executed_methods": list(PUBLISHED_METHOD_ORDER),
                    "training_directory": f"training/{selected.workload}/r{selected.repeat}",
                    "workload": workload,
                },
            )
        )
    return rows


def _candidate_invalid_chromosome_diagnostics(training: Sequence[_CandidateTraining]) -> list[JsonValue]:
    """Retain classified infeasibility with its declared genes, settings, and limits."""

    rows: list[JsonValue] = []
    workload_order: dict[WorkloadName, int] = {"short": 0, "streaming": 1, "bursty": 2}
    for item in sorted(training, key=lambda item: (workload_order[item.workload], item.repeat)):
        invalid: list[object] = []
        for candidate in item.checkpoint.population:
            if candidate.status != "invalid":
                continue
            failure = candidate.invalid
            _require(failure is not None, "invalid candidate must retain its classified failure")
            assert failure is not None
            invalid.append(
                {
                    "affected_evidence": failure.affected_evidence,
                    "authority": failure.authority,
                    "corrective_action": failure.corrective_action,
                    "detail": failure.detail,
                    "evidence_state": failure.evidence_state,
                    "family": candidate.family,
                    "genes": list(candidate.genes) if candidate.genes is not None else None,
                    "identifier": {
                        "birth_generation": candidate.identifier.birth_generation,
                        "birth_index": candidate.identifier.birth_index,
                    },
                    "kind": failure.kind,
                    "seed": failure.seed,
                    "stage": failure.stage,
                }
            )
        rows.append(
            cast(
                JsonObject,
                {
                    "invalid_candidates": cast(JsonValue, invalid),
                    "trial_limits": cast(JsonObject, item.config.generation.trial.model_dump(mode="json")),
                    "training_directory": f"training/{item.workload}/r{item.repeat}",
                    "workload": item.workload,
                    "repeat": item.repeat,
                },
            )
        )
    return rows


def _candidate_report_inputs(
    training: Sequence[_CandidateTraining],
    held_out: Mapping[WorkloadName, HeldOutEvaluation],
    *,
    natural_variation: Sequence[JsonObject],
) -> JsonObject:
    fresh_simulation: list[JsonValue] = []
    held_out_scores: list[JsonValue] = []
    training_scores: list[JsonValue] = []
    for workload in ("short", "streaming", "bursty"):
        group = [item for item in training if item.workload == workload]
        _require(len(group) == 3, f"report inputs require three {workload} training records")
        fresh_simulation.append(
            cast(
                JsonObject,
                {
                    "score": _candidate_score_mean([_candidate_score(item.comparison) for item in group]),
                    "workload": workload,
                },
            )
        )
        training_scores.append(
            cast(
                JsonObject,
                {
                    "runtime_seconds": _candidate_sample_summary(
                        [item.runtime_seconds for item in group], name="training runtime variance"
                    ),
                    "selection_fitness": _candidate_sample_summary(
                        [item.checkpoint.best_fitness for item in group], name="training selection variance"
                    ),
                    "winner_family_count_variance": variance(
                        [sum(_candidate_winner_family(item) == family for item in group) for family in FAMILY_ORDER]
                    ),
                    "winner_family_counts": cast(
                        JsonObject,
                        {
                            family: sum(_candidate_winner_family(item) == family for item in group)
                            for family in FAMILY_ORDER
                        },
                    ),
                    "workload": workload,
                },
            )
        )
        held_out_scores.append(
            cast(
                JsonObject,
                {
                    "observation_window_seconds": held_out[workload].observation_window_seconds,
                    "score": _candidate_score(held_out[workload].comparison),
                    "workload": workload,
                },
            )
        )
    return {
        "controlled_weight_analysis": _candidate_controlled_weight_analysis(training),
        "formula": "arithmetic_mean",
        "fresh_simulation": fresh_simulation,
        "held_out": held_out_scores,
        "invalid_chromosome_diagnostics": _candidate_invalid_chromosome_diagnostics(training),
        "natural_variation": list(natural_variation),
        "runtime_winner_variance": training_scores,
        "training": training_scores,
    }


def _begin_candidate_collection(
    repository_root: Path,
    *,
    attempt: Path,
    study_id: str,
    url: str,
    environment: Mapping[str, object],
    retained_prerequisites: bytes,
    configs: Mapping[WorkloadName, ExperimentConfig],
    object_size_bytes: int,
) -> tuple[Path, Path]:
    candidate = _candidate_root(repository_root, study_id)
    _require(
        attempt == _collection_attempt_root(repository_root, study_id),
        "collection attempt must use its exact study attempt path",
    )
    _require(
        (attempt / "collection.json").is_file(),
        "collection attempt marker must exist before collection validation",
    )
    _require(set(configs) == {"short", "streaming", "bursty"}, "collection requires exactly three workload configs")
    _require(4 * 1024 * 1024 <= object_size_bytes <= 16 * 1024 * 1024, "collection object size is out of range")
    document = parse_retained_prerequisites(retained_prerequisites)
    _require(
        document["study_id"] == study_id and document["url"] == url,
        "retained prerequisite study ID and URL must equal the collection request",
    )
    marker = attempt / "frozen-protocol.json"
    if _path_entry_exists(marker) or _path_entry_exists(candidate):
        raise TrafficlabError(
            f"Validation Study collection already began for {study_id}; use a new study ID",
            corrective_action="preserve the failed attempt and restart with a new study ID",
        )
    controls = {
        "base_config_identities": {
            workload: identify_bytes(render_effective_config(configs[workload])).as_dict()
            for workload in ("short", "streaming", "bursty")
        },
        "environment_identity": identify_bytes(_canonical_json(cast(JsonObject, dict(environment)))).as_dict(),
        "prerequisites_identity": identify_bytes(retained_prerequisites).as_dict(),
        "study_id": study_id,
        "url": url,
    }
    _write_candidate_bytes(marker, _canonical_json(cast(JsonObject, controls)))
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate, attempt


def _stage_retained_prerequisites(
    candidate: Path,
    *,
    content: bytes,
    files: Mapping[str, bytes],
) -> None:
    document = parse_retained_prerequisites(content)
    expected_paths = retained_prerequisite_paths(document)
    _require(
        set(files) == {*expected_paths, _RETAINED_CAPABILITY_HEADER},
        "retained prerequisite files must exactly match the frozen document and capability header",
    )
    for command in cast(list[JsonObject], document["commands"]):
        for field in ("command", "junit", "status", "stderr", "stdout"):
            record = cast(JsonObject, command[field])
            relative = cast(str, record["path"])
            identity = ContentIdentity.from_dict(record["identity"], name=f"retained prerequisite {relative}")
            _require(identify_bytes(files[relative]) == identity, f"retained prerequisite {relative} has wrong bytes")
            _write_candidate_bytes(candidate / relative, files[relative])
    capability = cast(JsonObject, document["capability"])
    capability_header = files[_RETAINED_CAPABILITY_HEADER]
    _require(
        hashlib.sha256(capability_header).hexdigest() == capability["canary_sha256"],
        "retained capability header must match its frozen prerequisite identity",
    )
    _write_candidate_bytes(candidate / _RETAINED_CAPABILITY_HEADER, capability_header)
    _write_candidate_bytes(
        candidate / "observations/prerequisites/00-prerequisites/capability.headers.json",
        _canonical_json(
            cast(
                JsonObject,
                {
                    "content_length": capability["content_length"],
                    "content_range": capability["content_range"],
                    "header_identity": _candidate_identity(capability_header),
                    "requested_end": 0,
                    "requested_start": 0,
                    "run_id": "00-prerequisites",
                    "scope": "prerequisites",
                    "status": capability["status"],
                    "transfer_index": 0,
                    "workload": "prerequisites",
                },
            )
        ),
    )
    _write_candidate_bytes(candidate / "prerequisites.json", content)


def _stage_candidate_transfer_evidence(
    repository_root: Path,
    candidate: Path,
    responses: Sequence[JsonObject],
    *,
    scope: Literal["training", "held_out"],
    run_id: str,
    workload: WorkloadSpec,
) -> None:
    """Copy every protocol-used response into the immutable candidate by run and transfer."""

    _require(
        len(responses) == len(workload.transfers),
        f"{scope} {run_id} must retain every workload transfer response",
    )
    for transfer_index, (start, end, filename) in enumerate(workload.transfers):
        response = responses[transfer_index]
        _require(
            response["transfer_index"] == transfer_index
            and response["requested_start"] == start
            and response["requested_end"] == end
            and response["status"] == 206,
            f"{scope} {run_id} transfer {filename} does not match the frozen workload profile",
        )
        archive_relative = _repository_relative_path(
            response["header_archive_path"],
            repository_root=repository_root,
            name=f"{scope} {run_id} header archive",
        )
        archive = repository_root / Path(*archive_relative.split("/"))
        try:
            header = archive.read_bytes()
        except OSError as error:
            raise ValueError(f"could not read retained {scope} {run_id} header {filename}: {error}") from error
        _require(hashlib.sha256(header).hexdigest() == response["header_sha256"], "archived transfer header changed")
        header_relative = f"headers/{scope}/{run_id}/{filename}"
        observation_relative = f"observations/{scope}/{run_id}/{filename}.json"
        _write_candidate_bytes(candidate / header_relative, header)
        _write_candidate_bytes(
            candidate / observation_relative,
            _canonical_json(
                cast(
                    JsonObject,
                    {
                        "content_length": response["content_length"],
                        "content_range": response["content_range"],
                        "header_identity": _candidate_identity(header),
                        "requested_end": end,
                        "requested_start": start,
                        "run_id": run_id,
                        "scope": scope,
                        "status": 206,
                        "transfer_index": transfer_index,
                        "workload": workload.name,
                    },
                )
            ),
        )


class _CollectionCallbackValueError(Exception):
    """Carry an unexpected callback ValueError beyond collection normalization."""

    def __init__(self, error: ValueError) -> None:
        super().__init__(str(error))
        self.error = error


def _collect_held_out(
    repository_root: Path,
    candidate: Path,
    attempt: Path,
    *,
    study_id: str,
    workload: WorkloadSpec,
    training: _CandidateTraining,
    environment: Mapping[str, object],
    capture: HeldOutCaptureRunner,
    object_size_bytes: int,
) -> tuple[JsonObject, HeldOutEvaluation, CaptureResult]:
    directory = candidate / "held_out" / workload.name
    _require(not _path_entry_exists(directory), f"held-out directory already exists: {directory}")
    config = _config_with_run_directory(training.config, directory)
    source = attempt / f"held-out-{workload.name}.toml"
    _render_realized_config(config, source)
    prepared = prepare_transfer_scratch(repository_root, study_id, f"held-out-{workload.name}", workload)
    try:
        result = capture(source)
    except ValueError as error:
        raise _CollectionCallbackValueError(error) from error
    _require(
        result.run_directory == directory and not result.reused,
        "held-out capture must publish one fresh non-reused capture pair",
    )
    responses = archive_transfer_evidence(
        repository_root,
        study_id,
        f"held-out-{workload.name}",
        workload,
        prepared,
        object_size_bytes=object_size_bytes,
    )
    _stage_candidate_transfer_evidence(
        repository_root,
        candidate,
        responses,
        scope="held_out",
        run_id=f"held-out-{workload.name}",
        workload=workload,
    )
    experiment = directory / "experiment.toml"
    _require(
        experiment.is_file() and not experiment.is_symlink(), "held-out capture must retain its stage configuration"
    )
    experiment.unlink()
    capture_content = (directory / "capture.json").read_bytes()
    reference_content = (directory / "reference.pcapng").read_bytes()
    evaluation = evaluate_study_held_out(
        model_content=training.contents["best_model.json"],
        model_source=training.directory / "best_model.json",
        config=config,
        capture_content=capture_content,
        capture_source=directory / "capture.json",
        reference_content=reference_content,
        reference_source=directory / "reference.pcapng",
    )
    _write_candidate_bytes(directory / "generated.pcapng", evaluation.generated_pcapng)
    _write_candidate_bytes(directory / "similarity.json", evaluation.comparison_json)
    _write_candidate_config_pair(config, directory / "portable.toml", directory / "realized.toml")
    append_run_log(directory, {"event": "held_out_evaluated", "stage": "compare", "workload": workload.name})
    record = cast(
        JsonObject,
        {
            "capture_identity": cast(JsonObject, evaluation.capture_identity.as_dict()),
            "capture_lineage": _candidate_capture_lineage(capture_content, environment),
            "comparison_identity": _candidate_identity(evaluation.comparison_json),
            "generated_identity": cast(JsonObject, evaluation.generated_identity.as_dict()),
            "observation_window_seconds": evaluation.observation_window_seconds,
            "reference_identity": cast(JsonObject, evaluation.reference_identity.as_dict()),
            "seed": evaluation.seed,
            "training_directory": f"training/{training.workload}/r{training.repeat}",
            "training_model_identity": cast(JsonObject, evaluation.training_model_identity.as_dict()),
            "workload": workload.name,
        },
    )
    _write_candidate_bytes(directory / "record.json", _canonical_json(record))
    return (
        cast(
            JsonObject,
            {
                "capture_lineage": _candidate_capture_lineage(capture_content, environment),
                "directory": f"held_out/{workload.name}",
                "training_directory": f"training/{training.workload}/r{training.repeat}",
                "workload": workload.name,
            },
        ),
        evaluation,
        result,
    )


def _collection_inputs_from_prerequisites(
    repository_root: Path,
    prerequisite_path: Path,
    *,
    study_id: str,
    url: str,
    runner: CommandRunner,
    require_successful_prerequisite: bool = False,
    owned_capture_image: _PhaseCaptureImage | None = None,
) -> CollectionInputs:
    """Derive immutable candidate inputs from retained same-revision prerequisite evidence."""
    root = repository_root.resolve()
    try:
        content = prerequisite_path.read_bytes()
        if require_successful_prerequisite:
            _require_successful_prerequisite_attempt(
                root,
                study_id=study_id,
                url=url,
                prerequisite_content=content,
            )
        prerequisites = parse_prerequisite_results(content, repository_root=root)
        _require(
            (prerequisites.study_id, prerequisites.url) == (study_id, url),
            "collection URL and study ID must equal the retained prerequisites",
        )
        _validate_prerequisite_evidence(root, prerequisites)
        commit_result = runner(
            ("git", "rev-parse", "HEAD"),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        )
        tree_result = runner(
            ("git", "rev-parse", "HEAD^{tree}"),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        )
        status_result = runner(
            ("git", "status", "--porcelain=v1", "--untracked-files=all"),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        )
        _require(commit_result.returncode == 0, "could not resolve collection Git commit")
        _require(tree_result.returncode == 0, "could not resolve collection Git tree")
        status_stdout, _status_stderr = _completed_output(status_result, operation="collection Git tree inspection")
        _require(status_result.returncode == 0, "could not inspect collection Git tree")
        _require(status_stdout == b"", "collection Git tree must remain exactly clean")
        source_commit = _git_commit(_stdout_text(commit_result, operation="collection Git commit"))
        source_tree = _git_commit(_stdout_text(tree_result, operation="collection Git tree"))
        _require(
            source_commit == prerequisites.git_commit,
            "collection Git commit must equal the retained prerequisite commit",
        )
        image_lock_path = root / "docker" / "capture" / "image-lock.json"
        capture_lock = load_capture_image_lock(image_lock_path)
        validate_capture_dockerfile(
            (root / "docker" / "capture" / "Dockerfile").read_text(encoding="utf-8"),
            capture_lock,
        )
        images = cast(JsonObject, _thaw_json(prerequisites.images))
        tools = cast(JsonObject, _thaw_json(prerequisites.tools))
        current_host = {
            "host_architecture": platform.machine(),
            "kernel_release": platform.release(),
            "platform": platform.platform(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
        }
        _require(
            all(current_host[field] == tools[field] for field in current_host),
            "collection host, kernel, and Python must equal the retained prerequisites",
        )
        current_uv_lock = (root / "uv.lock").read_bytes()
        _require(
            hashlib.sha256(current_uv_lock).hexdigest() == tools["uv_lock_sha256"],
            "collection uv.lock must equal the retained prerequisite lock",
        )
        docker_version = runner(
            ("docker", "version", "--format", "{{.Server.Version}}"),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
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
            docker_version.returncode == 0
            and _stdout_text(docker_version, operation="collection Docker version") == tools["docker_engine_version"],
            "collection Docker Engine must equal the retained prerequisites",
        )
        _require(
            compose_version.returncode == 0
            and _stdout_text(compose_version, operation="collection Docker Compose version")
            == tools["docker_compose_version"],
            "collection Docker Compose must equal the retained prerequisites",
        )
        capture_image_id = _image_id(images["capture_image_id"], name="retained capture image ID")
        target_image_id = _image_id(images["target_image_id"], name="retained target image ID")
        target_reference = _strict_string(images["target_reference"], name="retained target image reference")
        _require(target_reference == TARGET_REFERENCE, "retained target image reference must remain locked")
        target_inspect = runner(
            ("docker", "image", "inspect", target_reference),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
        )
        _require(target_inspect.returncode == 0, "could not inspect the retained target image before collection")
        target_stdout, _target_stderr = _completed_output(target_inspect, operation="collection target image inspect")
        current_target = _target_image_record(target_stdout)
        retained_target = {
            field: images[field]
            for field in ("target_reference", "target_image_id", "target_repo_digests", "target_config_user")
        }
        _require(
            current_target == retained_target,
            "collection target image must equal the retained prerequisite identity",
        )
        _require(
            capture_image_id == capture_lock.expected_capture_image_id,
            "cold capture rebuild ID must equal the checked image lock",
        )
        if owned_capture_image is None:
            capture_inspect = runner(
                ("docker", "image", "inspect", capture_image_id, "--format", "{{.Id}}"),
                cwd=root,
                check=False,
                capture_output=True,
                shell=False,
                timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
            )
            _require(
                capture_inspect.returncode == 0
                and _image_id(
                    _stdout_text(capture_inspect, operation="collection capture image inspect"),
                    name="current capture image ID",
                )
                == capture_image_id,
                "collection capture image must equal the retained prerequisite identity",
            )
        capture_tool_version = capture_lock.capture_tool_version
        evidence_root = (
            root / "examples" / "validation_study" / ".study-work" / "evidence" / study_id / "00-prerequisites"
        )
        files: dict[str, bytes] = {}
        capability = cast(JsonObject, _thaw_json(prerequisites.capability))
        capability_header = (evidence_root / "capability.headers").read_bytes()
        _require(
            hashlib.sha256(capability_header).hexdigest() == capability["canary_sha256"],
            "retained capability header must match the prerequisite capability identity",
        )
        files[_RETAINED_CAPABILITY_HEADER] = capability_header
        retained_commands: list[JsonValue] = []
        for command, prefix in zip(prerequisites.commands, ("docker", "internet"), strict=True):
            record = cast(JsonObject, _thaw_json(command))
            kind = _strict_string(record["kind"], name="retained prerequisite command kind")
            expected_kind = "docker_matrix" if prefix == "docker" else "internet_smoke"
            _require(kind == expected_kind, "retained prerequisite command kind does not match its evidence")
            argv = cast(list[JsonValue], record["argv"])
            tests = cast(JsonObject, record["tests"])
            contents = {
                "command": _canonical_json(cast(JsonObject, {"argv": argv})),
                "status": _canonical_json(cast(JsonObject, {"exit_status": 0, "tests": tests})),
                "stdout": (evidence_root / f"{prefix}.stdout").read_bytes(),
                "stderr": (evidence_root / f"{prefix}.stderr").read_bytes(),
                "junit": (evidence_root / f"{prefix}.xml").read_bytes(),
            }
            outputs: dict[str, JsonValue] = {}
            for field, body in contents.items():
                suffix = {"command": "command.json", "status": "status.json", "junit": "junit.xml"}.get(field, field)
                relative = f"prerequisites/{kind}.{suffix}"
                files[relative] = body
                outputs[field] = cast(JsonValue, {"identity": _candidate_identity(body), "path": relative})
            retained_commands.append(
                cast(
                    JsonObject,
                    {
                        "argv": argv,
                        "command": outputs["command"],
                        "exit_status": 0,
                        "junit": outputs["junit"],
                        "kind": kind,
                        "status": outputs["status"],
                        "stderr": outputs["stderr"],
                        "stdout": outputs["stdout"],
                        "tests": tests,
                    },
                )
            )
        uv_lock_identity = _candidate_identity(current_uv_lock)
        retained_prerequisites = render_retained_prerequisites(
            cast(
                JsonObject,
                {
                    "capability": {field: capability[field] for field in _RETAINED_PREREQUISITE_CAPABILITY_KEYS},
                    "commands": retained_commands,
                    "environment": cast(
                        JsonObject,
                        {
                            "capture_image_id": capture_image_id,
                            "capture_image_reference": capture_image_id,
                            "capture_tool_version": capture_tool_version,
                            "source_commit": source_commit,
                            "source_tree": source_tree,
                            "target_image_id": target_image_id,
                            "target_image_reference": target_reference,
                            "uv_lock_identity": uv_lock_identity,
                        },
                    ),
                    "schema_version": 4,
                    "study_id": study_id,
                    "url": url,
                },
            )
        )
        environment: dict[str, object] = {
            "capture_image_id": capture_image_id,
            "capture_image_reference": capture_image_id,
            "capture_tool_version": capture_tool_version,
            "compatibility_decision": {
                "reason": "source, lock, and image-lock identities are compatible",
                "status": "compatible",
            },
            "docker_compose_version": _strict_string(tools["docker_compose_version"], name="retained Compose version"),
            "docker_engine_version": _strict_string(tools["docker_engine_version"], name="retained Docker version"),
            "host_architecture": current_host["host_architecture"],
            "kernel_release": current_host["kernel_release"],
            "python_implementation": current_host["python_implementation"],
            "python_version": current_host["python_version"],
            "scientific_artifact_schema": 4,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "target_image_id": target_image_id,
            "target_image_reference": target_reference,
            "uv_lock_identity": uv_lock_identity,
        }
        _require(
            bool(cast(str, environment["host_architecture"])) and bool(cast(str, environment["kernel_release"])),
            "collection environment must retain host architecture and kernel release",
        )
        configs = validate_base_configs(root, prerequisites)
        object_size_bytes = _strict_int(
            prerequisites.capability["object_size_bytes"], name="retained prerequisite object size"
        )
        _require(4 * 1024 * 1024 <= object_size_bytes <= 16 * 1024 * 1024, "retained object size is out of range")
        if owned_capture_image is not None:
            _establish_phase_capture_image(
                root,
                phase="collection",
                expected_image_id=capture_image_id,
                capture_lock_image_id=capture_lock.expected_capture_image_id,
                owned_capture_image=owned_capture_image,
                iidfile=_collection_attempt_root(root, study_id) / "collection-capture.iid",
                runner=runner,
            )
        return environment, retained_prerequisites, files, configs, object_size_bytes
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise TrafficlabError(
            f"Validation Study collection inputs are invalid: {error}",
            corrective_action="preserve prerequisite evidence, correct the frozen inputs, and restart with a new study ID",
        ) from error


def _phase_capture_tag(study_id: str, phase: Literal["collection", "study"]) -> str:
    """Return the exclusive capture-image tag for one irreversible public phase."""

    return f"trafficlab-validation-{validate_study_id(study_id)}:{phase}-capture"


def _establish_phase_capture_image(
    repository_root: Path,
    *,
    phase: Literal["collection", "study"],
    expected_image_id: str,
    capture_lock_image_id: str,
    owned_capture_image: _PhaseCaptureImage,
    iidfile: Path,
    runner: CommandRunner,
) -> None:
    """Cold-build and inspect the image used by every capture in one public phase."""

    _require(not owned_capture_image.build_attempted, f"{phase} capture image must be established exactly once")
    _require(
        expected_image_id == capture_lock_image_id,
        f"{phase} capture image must equal the checked image lock before rebuild",
    )
    primary: BaseException | None = None
    try:
        existing_tag = runner(
            ("docker", "image", "inspect", owned_capture_image.tag, "--format", "{{.Id}}"),
            cwd=repository_root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
        )
        _require(
            existing_tag.returncode == 1,
            (
                f"{phase} capture image tag already exists and is not owned by this phase"
                if existing_tag.returncode == 0
                else f"could not inspect {phase} capture image tag before rebuild: "
                f"{_command_detail(existing_tag, operation=f'{phase} capture image tag inspect')}"
            ),
        )
        iidfile.parent.mkdir(parents=True, exist_ok=True)
        owned_capture_image.build_attempted = True
        completed = runner(
            cold_capture_build_argv(owned_capture_image.tag, iidfile),
            cwd=repository_root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
        )
        _require(
            completed.returncode == 0,
            f"could not cold-build {phase} capture image: "
            f"{_command_detail(completed, operation=f'{phase} capture image build')}",
        )
        rebuilt_image_id = _image_id(iidfile.read_text(encoding="ascii").strip(), name=f"{phase} capture image ID")
        _require(
            rebuilt_image_id == expected_image_id == capture_lock_image_id,
            f"cold {phase} capture rebuild ID must equal retained prerequisite and image-lock identities",
        )
        inspected = runner(
            ("docker", "image", "inspect", rebuilt_image_id, "--format", "{{.Id}}"),
            cwd=repository_root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
        )
        _require(
            inspected.returncode == 0
            and _image_id(
                _stdout_text(inspected, operation=f"{phase} rebuilt capture image inspect"),
                name=f"rebuilt {phase} capture image ID",
            )
            == rebuilt_image_id,
            f"{phase} rebuilt capture image must remain the retained prerequisite identity",
        )
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            iidfile.unlink(missing_ok=True)
        except OSError as error:
            cleanup_error = ValueError(f"could not remove {phase} capture IID file {iidfile}: {error}")
            if primary is None:
                raise cleanup_error from error
            primary.add_note(f"{phase} capture IID file cleanup failed: {cleanup_error}")


def _remove_owned_phase_capture_image(
    owned_capture_image: _PhaseCaptureImage,
    *,
    phase: Literal["collection", "study"],
    repository_root: Path,
    runner: CommandRunner,
) -> None:
    """Remove only the capture-image tag created for this public phase."""

    completed = runner(
        ("docker", "image", "rm", "--force", owned_capture_image.tag),
        cwd=repository_root,
        check=False,
        capture_output=True,
        shell=False,
        timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
    )
    _require(
        completed.returncode == 0,
        f"could not remove owned {phase} capture image: "
        f"{_command_detail(completed, operation=f'{phase} capture image cleanup')}",
    )


def _complete_collection_capture_image_cleanup(
    owned_capture_image: _PhaseCaptureImage,
    *,
    repository_root: Path,
    runner: CommandRunner,
) -> None:
    """Remove and prove absence of the exact collection tag before candidate finalization."""

    _require(owned_capture_image.build_attempted, "collection capture image must be established before cleanup")
    try:
        _remove_owned_phase_capture_image(
            owned_capture_image,
            phase="collection",
            repository_root=repository_root,
            runner=runner,
        )
    except ValueError as error:
        raise ValueError(f"collection capture image cleanup failed: {error}") from error
    inspected = runner(
        ("docker", "image", "inspect", owned_capture_image.tag, "--format", "{{.Id}}"),
        cwd=repository_root,
        check=False,
        capture_output=True,
        shell=False,
        timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
    )
    if inspected.returncode != 1:
        detail = (
            "collection capture image cleanup left the exact owned tag present"
            if inspected.returncode == 0
            else "could not inspect the collection capture image tag after cleanup: "
            + _command_detail(inspected, operation="collection capture image post-cleanup inspect")
        )
        raise ValueError(detail)
    owned_capture_image.cleanup_verified = True


def _collection_capture_lifecycle_record(
    capture_result: CaptureResult,
    *,
    directory: Path,
    directory_relative: str,
    run_id: str,
) -> JsonObject:
    """Bind one successful capture return to its retained project-cleanup lineage."""

    _require(
        capture_result.run_directory == directory
        and capture_result.reference_path == directory / "reference.pcapng"
        and capture_result.target_status == 0
        and not capture_result.reused,
        "collection capture must return one fresh successful capture pair",
    )
    records = _parse_run_log((directory / "run.log").read_bytes())
    creations = [record for record in records if record.get("event") == "capture_project_created"]
    _require(len(creations) == 1, "collection capture must retain one capture project creation record")
    publications = [record for record in records if record.get("event") == "capture_published"]
    _require(len(publications) == 1, "collection capture must retain one capture publication record")
    created_project_name = creations[0].get("project_name")
    project_name = publications[0].get("project_name")
    _require(
        creations[0].get("stage") == "capture"
        and publications[0].get("stage") == "capture"
        and type(created_project_name) is str
        and type(project_name) is str
        and created_project_name.startswith("trafficlab-capture-")
        and created_project_name == project_name
        and records.index(creations[0]) < records.index(publications[0]),
        "collection capture must bind its exact created project name to publication",
    )
    return cast(
        JsonObject,
        {
            "cleanup_verified": True,
            "directory": directory_relative,
            "project_name": project_name,
            "run_id": run_id,
        },
    )


def _finalize_collection_lifecycle(
    *,
    candidate: Path,
    environment: Mapping[str, object],
    held_out: Sequence[JsonObject],
    owned_capture_image: _PhaseCaptureImage | None,
    repository_root: Path,
    runner: CommandRunner,
    study_id: str,
    training: Sequence[JsonObject],
) -> None:
    """Publish the one audit-owned cleanup contract only after every cleanup proof succeeds."""

    if owned_capture_image is None:
        raise ValueError("collection finalization requires its owned capture image")
    expected_tag = _phase_capture_tag(study_id, "collection")
    _require(owned_capture_image.tag == expected_tag, "collection lifecycle must use its exact owned capture image tag")
    capture_image_id = environment.get("capture_image_id")
    _require(type(capture_image_id) is str and capture_image_id.startswith("sha256:"), "invalid capture image identity")
    project_names = [row.get("project_name") for row in (*training, *held_out)]
    _require(
        len(project_names) == 12 and all(type(project_name) is str for project_name in project_names),
        "collection lifecycle must retain twelve exact capture project names",
    )
    _require(
        len({cast(str, project_name) for project_name in project_names}) == len(project_names),
        "collection lifecycle must retain distinct capture project names",
    )
    _complete_collection_capture_image_cleanup(
        owned_capture_image,
        repository_root=repository_root,
        runner=runner,
    )
    _write_candidate_bytes(
        candidate / "lifecycle.json",
        _canonical_json(
            cast(
                JsonObject,
                {
                    "held_out": [cast(JsonValue, row) for row in held_out],
                    "phase_capture_image": {
                        "capture_image_id": capture_image_id,
                        "cleanup_verified": True,
                        "post_cleanup_inspect_exit_status": 1,
                        "tag": expected_tag,
                    },
                    "schema_version": 1,
                    "study_id": study_id,
                    "training": [cast(JsonValue, row) for row in training],
                },
            )
        ),
    )


def collect_validation_candidate(
    *,
    repository_root: Path,
    study_id: str,
    url: str,
    attempt: Path,
    environment: Mapping[str, object],
    retained_prerequisites: bytes,
    prerequisite_files: Mapping[str, bytes],
    configs: Mapping[WorkloadName, ExperimentConfig],
    run: TrainingRunner = run_experiment,
    capture: HeldOutCaptureRunner = capture_experiment,
    object_size_bytes: int,
    perf_counter: Callable[[], float] = time.perf_counter,
    owned_capture_image: _PhaseCaptureImage | None = None,
    runner: CommandRunner = cast(CommandRunner, subprocess.run),  # noqa: B008 - fixed injected lifecycle boundary
) -> Path:
    """Collect one immutable, audit-ready real-program validation candidate
    through the existing capture, fitting, generation, and comparison owners.
    """
    root = repository_root.resolve()
    checked_study_id = validate_study_id(study_id)
    checked_url = validate_endpoint_url(url)
    try:
        candidate, attempt = _begin_candidate_collection(
            root,
            attempt=attempt,
            study_id=checked_study_id,
            url=checked_url,
            environment=environment,
            retained_prerequisites=retained_prerequisites,
            configs=configs,
            object_size_bytes=object_size_bytes,
        )
        _write_candidate_bytes(candidate / "environment.json", _canonical_json(cast(JsonObject, dict(environment))))
        _stage_retained_prerequisites(candidate, content=retained_prerequisites, files=prerequisite_files)
        workloads = {item.name: item for item in workload_specs(checked_url)}
        training: list[_CandidateTraining] = []
        training_lifecycle: list[JsonObject] = []
        for _order, run_id, workload_value, repeat in PRIMARY_ORDER:
            workload_name = cast(WorkloadName, workload_value)
            workload = workloads[workload_name]
            directory = candidate / "training" / workload_name / f"r{repeat}"
            config = _config_with_run_directory(configs[workload_name], directory)
            source = attempt / f"training-{workload_name}-r{repeat}.toml"
            _render_realized_config(config, source)
            prepared = prepare_transfer_scratch(root, checked_study_id, run_id, workload)
            started = perf_counter()
            try:
                run_result = run(source)
            except ValueError as error:
                raise _CollectionCallbackValueError(error) from error
            runtime_seconds = perf_counter() - started
            _require(math.isfinite(runtime_seconds) and runtime_seconds >= 0.0, "training runtime must be finite")
            append_run_log(
                directory,
                {
                    "event": "validation_study_training_completed",
                    "repeat": repeat,
                    "runtime_seconds": runtime_seconds,
                    "stage": "study",
                    "workload": workload_name,
                },
            )
            responses = archive_transfer_evidence(
                root,
                checked_study_id,
                run_id,
                workload,
                prepared,
                object_size_bytes=object_size_bytes,
            )
            _stage_candidate_transfer_evidence(
                root,
                candidate,
                responses,
                scope="training",
                run_id=run_id,
                workload=workload,
            )
            _write_candidate_config_pair(
                config,
                candidate / "configs" / f"training-{workload_name}-r{repeat}.portable.toml",
                candidate / "configs" / f"training-{workload_name}-r{repeat}.realized.toml",
            )
            loaded = _load_candidate_training(
                directory,
                workload=workload_name,
                repeat=repeat,
                config=config,
                runtime_seconds=runtime_seconds,
            )
            training.append(loaded)
            training_lifecycle.append(
                _collection_capture_lifecycle_record(
                    run_result.capture,
                    directory=directory,
                    directory_relative=f"training/{workload_name}/r{repeat}",
                    run_id=run_id,
                )
            )

        try:
            natural_variation = tuple(
                _candidate_natural_variation([item for item in training if item.workload == workload])
                for workload in ("short", "streaming", "bursty")
            )
        except TrafficlabError as error:
            natural_error = TrafficlabError(
                str(error),
                corrective_action="correct samples or settings",
                failure_outcomes=error.failure_outcomes,
            )
            raise attach_failure_outcome(
                natural_error,
                kind="metric_infeasible",
                stage="compare",
                affected_evidence="similarity.json",
                evidence_state="not_published",
            ) from error
        fresh: list[JsonObject] = []
        for loaded in training:
            fresh_path, fresh_record = _candidate_fresh_record(loaded)
            _write_candidate_bytes(candidate / fresh_path, _canonical_json(fresh_record))
            fresh.append(fresh_record)

        selected = _select_candidate_training(training)
        protocol = cast(
            JsonObject,
            {
                "candidate_id": checked_study_id,
                "destination_id": checked_study_id,
                "final_seed": 97,
                "model_selection": cast(
                    JsonObject,
                    {
                        "rule": "highest_best_fitness_then_lowest_repeat",
                        "selected": [cast(JsonValue, record) for record in selected],
                    },
                ),
                "prerequisite_path": "examples/validation_study/prerequisites.json",
                "schema_version": 4,
                "selection_seeds": list(configs["short"].genetic.trial_seeds),
                "study_id": checked_study_id,
                "training_repetitions": 3,
                "workloads": ["short", "streaming", "bursty"],
            },
        )
        _write_candidate_bytes(candidate / "protocol.json", _canonical_json(protocol))
        selected_training: dict[WorkloadName, _CandidateTraining] = {}
        for record in selected:
            selected_workload = cast(WorkloadName, record["workload"])
            selected_directory = cast(str, record["training_directory"])
            selected_training[selected_workload] = next(
                item for item in training if f"training/{item.workload}/r{item.repeat}" == selected_directory
            )
        held_rows: list[JsonObject] = []
        held_lifecycle: list[JsonObject] = []
        held_evaluations: dict[WorkloadName, HeldOutEvaluation] = {}
        for workload_name in ("short", "streaming", "bursty"):
            held_record, evaluation, capture_result = _collect_held_out(
                root,
                candidate,
                attempt,
                study_id=checked_study_id,
                workload=workloads[workload_name],
                training=selected_training[workload_name],
                environment=environment,
                capture=capture,
                object_size_bytes=object_size_bytes,
            )
            held_rows.append(held_record)
            held_lifecycle.append(
                _collection_capture_lifecycle_record(
                    capture_result,
                    directory=candidate / "held_out" / workload_name,
                    directory_relative=f"held_out/{workload_name}",
                    run_id=f"held-out-{workload_name}",
                )
            )
            held_evaluations[workload_name] = evaluation
        _finalize_collection_lifecycle(
            candidate=candidate,
            environment=environment,
            held_out=held_lifecycle,
            owned_capture_image=owned_capture_image,
            repository_root=root,
            runner=runner,
            study_id=checked_study_id,
            training=training_lifecycle,
        )
        report_inputs = _candidate_report_inputs(
            training,
            held_evaluations,
            natural_variation=natural_variation,
        )
        _write_candidate_bytes(candidate / "report_inputs.json", _canonical_json(report_inputs))
        _write_candidate_bytes(
            candidate / "report.json",
            _canonical_json(
                cast(
                    JsonObject,
                    {
                        "formula": "arithmetic_mean",
                        "report_inputs_identity": _candidate_identity((candidate / "report_inputs.json").read_bytes()),
                        "summary": report_inputs,
                    },
                )
            ),
        )
        workload_order: dict[WorkloadName, int] = {"short": 0, "streaming": 1, "bursty": 2}
        ordered_training = tuple(sorted(training, key=lambda item: (workload_order[item.workload], item.repeat)))
        sorted_fresh = sorted(
            fresh,
            key=lambda record: (
                workload_order[cast(WorkloadName, record["workload"])],
                cast(int, record["repeat"]),
            ),
        )
        index: JsonObject = {
            "environment": "environment.json",
            "fresh_simulation": [cast(JsonValue, record) for record in sorted_fresh],
            "held_out": [cast(JsonValue, record) for record in held_rows],
            "lifecycle": "lifecycle.json",
            "lineage": {},
            "ownership": {},
            "prerequisites": "prerequisites.json",
            "protocol": "protocol.json",
            "report": "report.json",
            "report_inputs": "report_inputs.json",
            "schema_version": 4,
            "training": [
                cast(JsonValue, _candidate_training_record(item, environment=environment)) for item in ordered_training
            ],
        }
        _write_candidate_bytes(candidate / "index.json", _canonical_json(index))
        from scripts import audit_validation_study as auditor

        files = auditor.files_for_candidate(candidate, include_manifest=False)
        index["ownership"] = cast(JsonValue, {relative: auditor.owner_for_path(relative) for relative in files})
        index["lineage"] = cast(JsonValue, {relative: auditor.lineage_for_path(relative) for relative in files})
        (candidate / "index.json").write_bytes(_canonical_json(index))
        files = auditor.files_for_candidate(candidate, include_manifest=False)
        auditor.write_manifest(
            candidate,
            ownership={relative: auditor.owner_for_path(relative) for relative in files},
            lineage={relative: auditor.lineage_for_path(relative) for relative in files},
        )
        auditor.audit_bundle(candidate, repository=root)
        return candidate
    except _CollectionCallbackValueError as error:
        raise error.error from None
    except TrafficlabError as error:
        raise TrafficlabError(
            f"Validation Study collection failed; preserve the ignored attempt and restart with a new study ID: {error}",
            corrective_action="preserve the failed attempt and restart with a new study ID",
            failure_outcomes=error.failure_outcomes,
        ) from error
    except (OSError, ValueError) as error:
        raise TrafficlabError(
            f"Validation Study collection failed; preserve the ignored attempt and restart with a new study ID: {error}",
            corrective_action="preserve the failed attempt and restart with a new study ID",
        ) from error


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
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--url", required=True)
    collect_parser.add_argument("--study-id", required=True)
    collect_parser.add_argument("--prerequisites", required=True, type=Path)
    publish_parser = commands.add_parser("publish")
    publish_parser.add_argument("--candidate", required=True, type=Path)
    publish_parser.add_argument("--study-id", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    repository_root: Path = REPOSITORY_ROOT,
    run: Callable[[Path], RunResult] = run_experiment,
    capture: HeldOutCaptureRunner = capture_experiment,
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
        canonical_prerequisite_path = repository_root.resolve() / "examples" / "validation_study" / "prerequisites.json"
        try:
            _require(
                prerequisite_path == canonical_prerequisite_path,
                "collection prerequisites must use examples/validation_study/prerequisites.json before candidate creation",
            )
        except ValueError as error:
            raise TrafficlabError(
                f"invalid Validation Study command arguments: {error}",
                corrective_action="supply the exact repository-relative checked prerequisite path",
            ) from error
        if parsed.command == "collect":
            attempt = _begin_phase_attempt(
                repository_root.resolve(),
                study_id=study_id,
                url=url,
                phase="collection",
            )
            owned_capture_image = _PhaseCaptureImage(tag=_phase_capture_tag(study_id, "collection"))
            primary: BaseException | None = None
            try:
                environment, retained_prerequisites, prerequisite_files, configs, object_size_bytes = (
                    _collection_inputs_from_prerequisites(
                        repository_root,
                        prerequisite_path,
                        study_id=study_id,
                        url=url,
                        runner=runner,
                        require_successful_prerequisite=True,
                        owned_capture_image=owned_capture_image,
                    )
                )
                candidate = collect_validation_candidate(
                    repository_root=repository_root,
                    study_id=study_id,
                    url=url,
                    attempt=attempt,
                    environment=environment,
                    retained_prerequisites=retained_prerequisites,
                    prerequisite_files=prerequisite_files,
                    configs=configs,
                    run=run,
                    capture=capture,
                    object_size_bytes=object_size_bytes,
                    perf_counter=perf_counter,
                    owned_capture_image=owned_capture_image,
                    runner=runner,
                )
            except BaseException as error:
                primary = error
                raise
            finally:
                if owned_capture_image.build_attempted and not owned_capture_image.cleanup_verified:
                    try:
                        _remove_owned_phase_capture_image(
                            owned_capture_image,
                            phase="collection",
                            repository_root=repository_root.resolve(),
                            runner=runner,
                        )
                    except BaseException as cleanup_error:
                        if primary is None:
                            raise TrafficlabError(
                                f"Validation Study collection capture image cleanup failed: {cleanup_error}",
                                corrective_action=(
                                    "preserve the collection attempt, remove the exact owned capture image tag, "
                                    "and restart with a new study ID"
                                ),
                            ) from cleanup_error
                        primary.add_note(f"collection capture image cleanup failed: {cleanup_error}")
            print(f"validation-study: candidate collected at {candidate}")
            return 0
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
