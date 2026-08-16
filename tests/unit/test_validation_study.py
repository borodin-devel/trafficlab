from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
import socket
import stat
import subprocess
import tomllib
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from random import Random
from statistics import fmean, variance
from typing import Any, Literal, cast

import pytest
import tomli_w

from scripts import audit_validation_study as auditor
from scripts import generate_validation_study_fixture as fixture_generator
from scripts import run_validation_study as study
from trafficlab.artifacts import append_run_log
from trafficlab.capture import CaptureResult
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.comparison import ComparisonResult, compare_experiment, compare_traces, parse_comparison_result
from trafficlab.compatibility import ContentIdentity, identify_bytes
from trafficlab.config import GenerationLimits, SimilarityConfig
from trafficlab.config_io import load_configuration_pair
from trafficlab.errors import FailureOutcome, TrafficlabError
from trafficlab.fitting import fit_experiment
from trafficlab.generation import generate_experiment
from trafficlab.genetic.checkpoint import CheckpointState, encode_rng_state
from trafficlab.genetic.evaluation import ValidatedEvaluationContext, evaluate_candidate, validate_evaluation_context
from trafficlab.genetic.population import derive_family_priority, initial_population
from trafficlab.genetic.strategy import make_strategy_context
from trafficlab.genetic.types import Candidate, CandidateId, MethodTrialResult, TrialResult
from trafficlab.models.common import MARKOV_MODEL_DIAGNOSTIC_KEYS, FittedModel, GenerationResult
from trafficlab.models.registry import BestModel, get_family, load_best_model, make_best_model, render_best_model
from trafficlab.pcapng import encode_pcapng, parse_pcapng_bytes
from trafficlab.preflight import PreparedExperiment, open_or_prepare_experiment
from trafficlab.run import RunDependencies, RunResult, run_experiment
from trafficlab.trace import Direction, TraceEvent, align_generated, normalize_reference, parse_capture_metadata

_HASH = "a" * 64
_IMAGE_ID = f"sha256:{'b' * 64}"
_ROOT = Path(__file__).resolve().parents[2]
_FIT_FIXTURE = _ROOT / "examples" / "data" / "fit"
_CAPTURE_BYTES = (_FIT_FIXTURE / "capture.json").read_bytes()
_REFERENCE_BYTES = (_FIT_FIXTURE / "reference.pcapng").read_bytes()
_CAPTURE_DOCKERFILE = b"FROM capture-base\n"
_CAPTURE_SCRIPT = b"#!/bin/sh\nexec dumpcap\n"


class _ScriptedPrerequisiteRunner:
    def __init__(self, repository_root: Path, mutation: str = "happy") -> None:
        self.root = repository_root
        self.mutation = mutation
        self.study_id = "study-1"
        self.url = "https://downloads.example.test/object.bin"
        self.final_url = "https://cdn.example.test/object.bin"
        self.target_id = f"sha256:{'b' * 64}"
        self.capture_id = f"sha256:{'d' * 64}"
        self.container_id = "e" * 64
        self.capability_name = f"trafficlab-validation-study-capability-{self.study_id}"
        self.evidence = (
            self.root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "evidence"
            / self.study_id
            / "00-prerequisites"
        )
        self.mount = self.root / "examples" / "validation_study" / ".study-work" / "mount" / self.study_id
        self.calls: list[tuple[tuple[str, ...], float]] = []
        self.container_running = False
        self.capability_finished = False

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
        command = tuple(argv)
        assert cwd == self.root
        assert check is False
        assert capture_output is True
        assert shell is False
        self.calls.append((command, timeout))
        identities: dict[tuple[str, ...], tuple[int, bytes, bytes]] = {
            ("git", "rev-parse", "HEAD"): (0, b"c" * 40 + b"\n", b""),
            ("git", "status", "--porcelain=v1", "--untracked-files=all"): (
                0,
                b"?? dirty\n" if self.mutation == "dirty-tree" else b"",
                b"",
            ),
            ("docker", "version", "--format", "{{.Server.Version}}"): (0, b"27.0.0\n", b""),
            ("docker", "compose", "version", "--short"): (0, b"2.29.0\n", b""),
            ("docker", "image", "pull", study.TARGET_REFERENCE): (0, b"pulled\n", b""),
        }
        if command in identities:
            status, stdout, stderr = identities[command]
            return subprocess.CompletedProcess(command, status, stdout=stdout, stderr=stderr)
        if command == ("docker", "image", "inspect", study.TARGET_REFERENCE):
            return self._inspect_target(command)
        if command[:3] == ("docker", "build", "--pull=false"):
            return self._build_capture(command)
        if command[:3] == ("docker", "container", "inspect"):
            return self._inspect_container(command)
        if command[:4] == ("docker", "container", "ls", "-a"):
            return self._list_container(command)
        if command[:3] == ("docker", "container", "rm"):
            return self._remove_container(command)
        if command[:3] == ("docker", "run", "--rm"):
            return self._run_capability(command, timeout)
        if command == study._live_argv(  # pyright: ignore[reportPrivateUsage]
            "docker_matrix",
            study._docker_matrix_argv(self.study_id),  # pyright: ignore[reportPrivateUsage]
            repository_root=self.root,
        ):
            return self._run_test_scope(command, "docker")
        if command == study._live_argv(  # pyright: ignore[reportPrivateUsage]
            "internet_smoke",
            study._internet_smoke_argv(self.study_id, self.url),  # pyright: ignore[reportPrivateUsage]
            repository_root=self.root,
        ):
            return self._run_test_scope(command, "internet")
        raise AssertionError(f"unexpected command: {command!r}")

    def _inspect_target(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        repo_digests = ["curlimages/curl@sha256:" + "f" * 64]
        if self.mutation != "target-digest-absent":
            repo_digests.append(study.TARGET_REFERENCE)
        inspected = [{"Id": self.target_id, "RepoDigests": repo_digests, "Config": {"User": "curl_user"}}]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(inspected).encode(), stderr=b"")

    def _build_capture(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        assert command == (
            "docker",
            "build",
            "--pull=false",
            "--iidfile",
            str(self.evidence / "capture.iid"),
            "docker/capture",
        )
        iid = "trafficlab-capture:local" if self.mutation == "capture-iid-tag" else self.capture_id
        (self.evidence / "capture.iid").write_text(f"{iid}\n", encoding="ascii")
        if self.mutation == "preexisting-cid":
            (self.evidence / "capability.cid").write_text(f"{self.container_id}\n", encoding="ascii")
        return subprocess.CompletedProcess(command, 0, stdout=b"built\n", stderr=b"")

    def _inspect_container(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        identifier = command[-1]
        if (
            identifier == self.capability_name
            and not self.container_running
            and self.mutation == "capability-daemon-error"
        ):
            return subprocess.CompletedProcess(command, 125, stdout=b"", stderr=b"daemon unavailable\n")
        if identifier == self.capability_name and not self.container_running and self.mutation == "preexisting-name":
            return subprocess.CompletedProcess(command, 0, stdout=b"[{}]", stderr=b"")
        if self.container_running and identifier == self.container_id:
            label = (
                self.study_id
                if self.mutation
                in {
                    "capability-timeout-owned",
                    "capability-lingering-owned",
                    "capability-lingering-owned-name-reclaimed",
                }
                else "someone-else"
            )
            inspected = [
                {
                    "Id": self.container_id,
                    "Name": f"/{self.capability_name}",
                    "Config": {"Labels": {"org.trafficlab.validation-study.study": label}},
                }
            ]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(inspected).encode(), stderr=b"")
        if self.container_running and identifier == self.capability_name:
            return subprocess.CompletedProcess(command, 0, stdout=b"[{}]", stderr=b"")
        return subprocess.CompletedProcess(command, 1, stdout=b"[]\n", stderr=b"not found\n")

    def _list_container(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        assert command[-2] == "--format" and command[-1] == "{{.ID}}"
        if self.mutation == "capability-daemon-error":
            return subprocess.CompletedProcess(command, 125, stdout=b"", stderr=b"daemon unavailable\n")
        filtered = command[-3]
        if (
            self.capability_finished
            and self.mutation == "capability-post-id-daemon-error"
            and filtered.startswith("id=")
        ):
            return subprocess.CompletedProcess(command, 125, stdout=b"", stderr=b"daemon unavailable\n")
        if (
            self.capability_finished
            and self.mutation == "capability-post-name-daemon-error"
            and filtered.startswith("name=")
        ):
            return subprocess.CompletedProcess(command, 125, stdout=b"", stderr=b"daemon unavailable\n")
        name_reclaimed = self.capability_finished and self.mutation in {
            "capability-name-reclaimed",
            "capability-lingering-owned-name-reclaimed",
        }
        name_exists = self.mutation == "preexisting-name" and filtered.startswith("name=")
        if name_reclaimed and not self.container_running and filtered.startswith("name="):
            stdout = f"{'f' * 64}\n".encode()
        else:
            stdout = f"{self.container_id}\n".encode() if self.container_running or name_exists else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    def _remove_container(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        assert command == ("docker", "container", "rm", "--force", self.container_id)
        assert self.mutation in {
            "capability-timeout-owned",
            "capability-lingering-owned",
            "capability-lingering-owned-name-reclaimed",
        }
        self.container_running = False
        return subprocess.CompletedProcess(command, 0, stdout=f"{self.container_id}\n".encode(), stderr=b"")

    def _run_capability(self, command: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[bytes]:
        assert command == self.expected_capability()
        assert "--user" not in command
        if self.mutation == "capability-start-error":
            raise OSError("simulated launch failure")
        if self.mutation != "capability-missing-cid":
            (self.evidence / "capability.cid").write_text(f"{self.container_id}\n", encoding="ascii")
        canary = self.mount / ".capability.headers"
        if self.mutation == "canary-replaced":
            canary.unlink()
            canary.touch(mode=0o666)
        if self.mutation != "canary-not-written":
            canary.write_bytes(self._capability_headers())
        if self.mutation in {"capability-timeout-owned", "capability-timeout-unowned"}:
            self.container_running = True
            self.capability_finished = True
            raise subprocess.TimeoutExpired(command, timeout, output=b"partial", stderr=b"timeout")
        if self.mutation in {
            "capability-lingering-owned",
            "capability-lingering-unowned",
            "capability-lingering-owned-name-reclaimed",
        }:
            self.container_running = True
        self.capability_finished = True
        stdout = self._write_out()
        status = 7 if self.mutation == "capability-nonzero" else 0
        return subprocess.CompletedProcess(command, status, stdout=stdout, stderr=b"curl diagnostic\n")

    def expected_capability(self) -> tuple[str, ...]:
        checked = list(study._expected_capability_argv(self.study_id, self.url))  # pyright: ignore[reportPrivateUsage]
        checked[8] = str(self.evidence / "capability.cid")
        checked[12] = f"type=bind,src={self.mount},dst=/trafficlab-study"
        return tuple(checked)

    def _capability_headers(self) -> bytes:
        if self.mutation == "range-ignored":
            return b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\n"
        total = 16_777_217 if self.mutation == "oversize-object" else 4_194_304
        return (
            b"HTTP/1.1 302 Found\r\nLocation: https://cdn.example.test/object.bin\r\n\r\n"
            + f"HTTP/1.1 206 Partial Content\r\nContent-Range: bytes 0-0/{total}\r\n".encode()
            + b"Content-Length: 1\r\n\r\n"
        )

    def _write_out(self) -> bytes:
        if self.mutation == "wrong-write-out":
            return b"status=206\nsize=2\n"
        return f"status=206\nsize=1\nurl={self.final_url}\nredirects=1\n".encode()

    def _run_test_scope(self, command: tuple[str, ...], kind: str) -> subprocess.CompletedProcess[bytes]:
        if kind == "docker" and self.mutation == "docker-matrix-failed":
            Path(command[-1]).write_bytes(
                b'<testsuites><testsuite tests="7" failures="1" errors="0" skipped="0"/></testsuites>'
            )
            return subprocess.CompletedProcess(command, 1, stdout=b"docker failed\n", stderr=b"failure\n")
        skipped = 1 if kind == "internet" and self.mutation == "internet-skipped" else 0
        total = 7 if kind == "docker" else 1
        Path(command[-1]).write_bytes(
            f'<testsuites><testsuite tests="{total}" failures="0" errors="0" '
            f'skipped="{skipped}"/></testsuites>'.encode()
        )
        return subprocess.CompletedProcess(command, 0, stdout=f"{kind} pass\n".encode(), stderr=b"")


def _frozen(document: object) -> study.FrozenJsonObject:
    return cast(
        study.FrozenJsonObject,
        study._freeze_json(cast(study.JsonValue, document)),  # pyright: ignore[reportPrivateUsage]
    )


def _contains_none(value: object) -> bool:
    if type(value) is dict:
        return any(_contains_none(item) for item in cast(dict[object, object], value).values())
    if type(value) is list:
        return any(_contains_none(item) for item in cast(list[object], value))
    return value is None


def _valid_prerequisite() -> study.PrerequisiteResults:
    study_id = "study-1"
    url = "https://downloads.example.test/object.bin"
    started = "2026-08-13T12:00:00Z"
    completed = "2026-08-13T12:01:00Z"
    mount_source = f"examples/validation_study/.study-work/mount/{study_id}"
    archive_path = f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites/capability.headers"
    evidence_root = f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites"
    guard = [
        "scripts/run_bounded.sh",
        "--memory-high",
        "2G",
        "--memory-max",
        "3G",
        "--swap-max",
        "512M",
    ]
    docker_argv = [
        *guard,
        "--wall-time",
        "20m",
        "--kill-after",
        "10s",
        "--",
        "uv",
        "run",
        "--locked",
        "pytest",
        "-vv",
        "-n",
        "0",
        "-m",
        "docker",
        "--junitxml",
        f"{evidence_root}/docker.xml",
    ]
    internet_argv = [
        *guard,
        "--wall-time",
        "10m",
        "--kill-after",
        "10s",
        "--",
        "uv",
        "run",
        "--locked",
        "pytest",
        "-vv",
        "-n",
        "0",
        "-m",
        "internet",
        "--internet-url",
        url,
        "--junitxml",
        f"{evidence_root}/internet.xml",
    ]
    command = {
        "kind": "docker_matrix",
        "argv": docker_argv,
        "started_utc": started,
        "completed_utc": completed,
        "exit_status": 0,
        "tests": {"total": 2, "passed": 2, "failed": 0, "errors": 0, "skipped": 0},
        "stdout_sha256": _HASH,
        "stderr_sha256": _HASH,
        "junit_sha256": _HASH,
    }
    capability_argv = [
        "docker",
        "run",
        "--rm",
        "--name",
        f"trafficlab-validation-study-capability-{study_id}",
        "--label",
        f"org.trafficlab.validation-study.study={study_id}",
        "--cidfile",
        f"{evidence_root}/capability.cid",
        "--network",
        "bridge",
        "--mount",
        f"type=bind,src={mount_source},dst=/trafficlab-study",
        study.TARGET_REFERENCE,
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
    ]
    return study.PrerequisiteResults(
        schema_version=1,
        created_utc=started,
        study_id=study_id,
        git_commit="c" * 40,
        git_tree_clean=True,
        url=url,
        tools=_frozen(
            {
                "python_version": "3.12.3",
                "trafficlab_version": "0.1.0",
                "docker_engine_version": "27.0.0",
                "docker_compose_version": "2.29.0",
                "platform": "Linux-test",
            }
        ),
        images=_frozen(
            {
                "target_reference": study.TARGET_REFERENCE,
                "target_image_id": _IMAGE_ID,
                "target_repo_digests": [study.TARGET_REFERENCE],
                "target_config_user": "",
                "capture_image_id": f"sha256:{'d' * 64}",
                "capture_dockerfile_sha256": _HASH,
                "capture_script_sha256": _HASH,
            }
        ),
        capability=_frozen(
            {
                "argv": capability_argv,
                "started_utc": started,
                "completed_utc": completed,
                "exit_status": 0,
                "status": 206,
                "content_length": 1,
                "object_size_bytes": 4_194_304,
                "redirect_count": 0,
                "body_bytes_downloaded": 1,
                "content_range": "bytes 0-0/4194304",
                "final_url": url,
                "mount_source": mount_source,
                "canary_archive_path": archive_path,
                "canary_sha256": _HASH,
                "container_id": "e" * 64,
                "stdout_sha256": _HASH,
                "stderr_sha256": _HASH,
                "used_image_default_user": True,
                "mount_directory_mode": 493,
                "canary_file_mode": 438,
                "canary_archive_mode": 384,
                "container_cleanup_verified": True,
            }
        ),
        config_sha256=_frozen({"short": _HASH, "streaming": _HASH, "bursty": _HASH}),
        commands=(
            _frozen(command),
            _frozen({**command, "kind": "internet_smoke", "argv": internet_argv}),
        ),
    )


def _expected_base_config(
    repository_root: Path,
    workload: str,
    *,
    url: str = "https://downloads.example.test/object.bin",
    study_id: str = "study-1",
    capture_image_id: str = f"sha256:{'d' * 64}",
) -> dict[str, object]:
    specs = {spec.name: spec for spec in study.workload_specs(url)}
    spec = specs[cast(study.WorkloadName, workload)]
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
            "image": study.TARGET_REFERENCE,
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


def _changed_config_paths(left: object, right: object, *, prefix: str = "") -> set[str]:
    if type(left) is dict and type(right) is dict:
        left_mapping = cast(dict[str, object], left)
        right_mapping = cast(dict[str, object], right)
        assert set(left_mapping) == set(right_mapping)
        changes: set[str] = set()
        for key in left_mapping:
            child = f"{prefix}.{key}" if prefix else key
            changes.update(_changed_config_paths(left_mapping[key], right_mapping[key], prefix=child))
        return changes
    return set() if left == right else {prefix}


def _write_checked_configs(
    repository_root: Path,
    *,
    study_id: str = "study-1",
    url: str = "https://downloads.example.test/object.bin",
    capture_image_id: str = f"sha256:{'d' * 64}",
) -> tuple[study.PrerequisiteResults, dict[str, bytes]]:
    contents: dict[str, bytes] = {}
    for spec in study.workload_specs(url):
        config = study.build_base_config(
            spec,
            repository_root=repository_root,
            study_id=study_id,
            url=url,
            capture_image_id=capture_image_id,
        )
        destination = repository_root / "examples" / "validation_study" / "configs" / f"{spec.name}.toml"
        contents[spec.name] = study.render_checked_base_config(config, destination, repository_root)
    prerequisite = replace(
        _valid_prerequisite(),
        config_sha256=_frozen({name: hashlib.sha256(content).hexdigest() for name, content in contents.items()}),
    )
    return prerequisite, contents


def _write_retained_prerequisite_evidence(
    repository_root: Path,
    prerequisite: study.PrerequisiteResults,
) -> study.PrerequisiteResults:
    capture_root = repository_root / "docker" / "capture"
    capture_root.mkdir(parents=True, exist_ok=True)
    dockerfile = _CAPTURE_DOCKERFILE
    capture_script = _CAPTURE_SCRIPT
    (capture_root / "Dockerfile").write_bytes(dockerfile)
    (capture_root / "capture.sh").write_bytes(capture_script)

    evidence = (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / prerequisite.study_id
        / "00-prerequisites"
    )
    evidence.mkdir(parents=True, exist_ok=True)
    capability_headers = _response_headers(0, 0)
    capability_stdout = f"status=206\nsize=1\nurl={prerequisite.url}\nredirects=0\n".encode()
    capability_stderr = b"capability diagnostic\n"
    container_id = cast(str, prerequisite.capability["container_id"])
    capture_image_id = cast(str, prerequisite.images["capture_image_id"])
    retained: dict[str, bytes] = {
        "capability.headers": capability_headers,
        "capability.stdout": capability_stdout,
        "capability.stderr": capability_stderr,
        "capability.cid": f"{container_id}\n".encode(),
        "capture.iid": f"{capture_image_id}\n".encode(),
        "docker.stdout": b"docker pass\n",
        "docker.stderr": b"",
        "docker.xml": (
            b'<testsuites tests="2" failures="0" errors="0" skipped="0">'
            b'<testsuite tests="2" failures="0" errors="0" skipped="0"/></testsuites>'
        ),
        "internet.stdout": b"internet pass\n",
        "internet.stderr": b"",
        "internet.xml": (
            b'<testsuites tests="2" failures="0" errors="0" skipped="0">'
            b'<testsuite tests="2" failures="0" errors="0" skipped="0"/></testsuites>'
        ),
    }
    for name, content in retained.items():
        path = evidence / name
        path.write_bytes(content)
        path.chmod(0o600)

    images = cast(study.JsonObject, study._thaw_json(prerequisite.images))  # pyright: ignore[reportPrivateUsage]
    images["capture_dockerfile_sha256"] = hashlib.sha256(dockerfile).hexdigest()
    images["capture_script_sha256"] = hashlib.sha256(capture_script).hexdigest()
    capability = cast(study.JsonObject, study._thaw_json(prerequisite.capability))  # pyright: ignore[reportPrivateUsage]
    capability["canary_sha256"] = hashlib.sha256(capability_headers).hexdigest()
    capability["stdout_sha256"] = hashlib.sha256(capability_stdout).hexdigest()
    capability["stderr_sha256"] = hashlib.sha256(capability_stderr).hexdigest()
    commands = [
        cast(study.JsonObject, study._thaw_json(command))  # pyright: ignore[reportPrivateUsage]
        for command in prerequisite.commands
    ]
    for command, prefix in zip(commands, ("docker", "internet"), strict=True):
        command["stdout_sha256"] = hashlib.sha256(retained[f"{prefix}.stdout"]).hexdigest()
        command["stderr_sha256"] = hashlib.sha256(retained[f"{prefix}.stderr"]).hexdigest()
        command["junit_sha256"] = hashlib.sha256(retained[f"{prefix}.xml"]).hexdigest()
    return replace(
        prerequisite,
        images=_frozen(images),
        capability=_frozen(capability),
        commands=(_frozen(commands[0]), _frozen(commands[1])),
    )


def _response_headers(
    start: int,
    end: int,
    *,
    total: int = 4_194_304,
    status: int = 206,
    length: int | None = None,
    prefix: bytes = b"",
) -> bytes:
    content_length = end - start + 1 if length is None else length
    return (
        prefix
        + f"HTTP/1.1 {status} Response\r\n".encode()
        + f"Content-Range: bytes {start}-{end}/{total}\r\n".encode()
        + f"Content-Length: {content_length}\r\n\r\n".encode()
    )


def _score(value: float) -> dict[str, object]:
    return {
        "aggregate": value,
        "methods": {name: value for name in study.PUBLISHED_METHOD_ORDER},
    }


def _descriptive(values: list[int | float]) -> dict[str, object]:
    numbers = [float(value) for value in values]
    minimum = min(numbers)
    maximum = max(numbers)
    sample_variance = variance(numbers)
    return {
        "count": 3,
        "mean": fmean(numbers),
        "minimum": minimum,
        "maximum": maximum,
        "range": maximum - minimum,
        "sample_variance": sample_variance,
        "sample_standard_deviation": math.sqrt(sample_variance),
    }


def _trace_summary(workload: str, repeat: int, *, generated: bool = False) -> dict[str, object]:
    packet_count = repeat + (4 if generated else 3)
    outbound = repeat + (2 if generated else 1)
    inbound = packet_count - outbound
    outbound_bytes = 100 * outbound
    inbound_bytes = 200 * inbound
    widths = [0.25, 1.0] if workload == "streaming" else [0.001, 0.01]
    return {
        "packet_count": packet_count,
        "observation_window_seconds": float(10 + repeat),
        "packet_totals": {"outbound": outbound, "inbound": inbound},
        "byte_totals": {"outbound": outbound_bytes, "inbound": inbound_bytes},
        "frame_lengths": {
            "count": packet_count,
            "minimum": 60.0,
            "median": 100.0,
            "quantile_probability": 0.95,
            "quantile": 200.0,
            "maximum": 200.0,
            "zero_count": 0,
        },
        "iats": {
            "count": packet_count - 1,
            "minimum": 0.0,
            "median": 0.5,
            "quantile_probability": 0.95,
            "quantile": 1.0,
            "maximum": 1.0,
            "zero_count": 1,
        },
        "scales": [
            {
                "width_seconds": width,
                "bins_per_direction": 2,
                "packet_totals": {"outbound": outbound, "inbound": inbound},
                "byte_totals": {"outbound": outbound_bytes, "inbound": inbound_bytes},
            }
            for width in widths
        ],
    }


def _genes(family: str) -> list[int | float]:
    if family == "markov_renewal":
        return [0.2, 0.7, 1.0, 4, 1.0]
    if family == "mmpp":
        return [1.0, 2.0, 10.0, 20.0]
    return [1.0]


def _champions(repeat: int, *, delta: float = 0.0) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for index, family in enumerate(study.FAMILY_ORDER):
        fitness = 0.50 + 0.05 * index + 0.01 * repeat + delta
        result.append(
            {
                "family": family,
                "candidate_id": {"birth_generation": 2, "birth_index": index},
                "genes": _genes(family),
                "selection_fitness": fitness,
                "selection_seeds": [17, 29],
                "selection_score": _score(fitness),
            }
        )
    return result


def _transfer_responses(study_id: str, run_id: str, workload: str) -> list[dict[str, object]]:
    if workload == "short":
        transfers = [(0, 262143, "short.headers")]
    elif workload == "streaming":
        transfers = [(0, 4194303, "streaming.headers")]
    else:
        starts = (0, 524288, 1048576, 1572864, 2097152, 2621440, 3145728, 3670016)
        transfers = [(start, start + 32767, f"bursty-{index}.headers") for index, start in enumerate(starts)]
    return [
        {
            "transfer_index": index,
            "requested_start": start,
            "requested_end": end,
            "status": 206,
            "content_length": end - start + 1,
            "content_range": f"bytes {start}-{end}/4194304",
            "header_archive_path": (f"examples/validation_study/.study-work/evidence/{study_id}/{run_id}/{filename}"),
            "header_sha256": _HASH,
            "scratch_precreate_mode": 438,
            "archive_mode": 384,
            "inode_preserved": True,
        }
        for index, (start, end, filename) in enumerate(transfers)
    ]


def _run_document(
    study_id: str,
    execution_order: int,
    run_id: str,
    workload: str,
    repeat: int,
) -> dict[str, object]:
    champions = _champions(repeat)
    winner = champions[2]
    fresh_simulation_value = 0.70 + 0.01 * repeat
    published_value = 0.65 + 0.01 * repeat
    event_count = repeat + 4
    return {
        "execution_order": execution_order,
        "run_id": run_id,
        "key": {"workload": workload, "repeat": repeat},
        "config_path": f"runs/validation_study/{study_id}/realized-configs/{run_id}.toml",
        "run_directory": f"runs/validation_study/{study_id}/{run_id}",
        "transfer_evidence_directory": f"examples/validation_study/.study-work/evidence/{study_id}/{run_id}",
        "elapsed_seconds": float(repeat),
        "reuse": {"capture": False, "best_model": False, "generated": False, "similarity": False},
        "cleanup_verified": True,
        "transfer_responses": _transfer_responses(study_id, run_id, workload),
        "artifact_sha256": {name: _HASH for name in study.ARTIFACT_NAMES},
        "reference": _trace_summary(workload, repeat),
        "generated": _trace_summary(workload, repeat, generated=True),
        "family_champions": champions,
        "winner": {
            "family": winner["family"],
            "candidate_id": winner["candidate_id"],
            "genes": winner["genes"],
            "selection_fitness": winner["selection_fitness"],
        },
        "fresh_simulation": {
            "seed": 97,
            "score": _score(fresh_simulation_value),
            "source": "run_experiment_fit_outcome",
        },
        "published": {"seed": 97, "score": _score(published_value)},
        "raw_sequence": {
            "seed": 97,
            "observation_window_seconds": float(10 + repeat),
            "trial_event_count": event_count,
            "final_event_count": event_count,
            "raw_events_equal": True,
            "fresh_simulation_score_reproduced": True,
            "reparsed_event_count": event_count,
            "reparsed_matches_quantized": True,
        },
    }


def _score_summary(values: list[float]) -> dict[str, object]:
    return {
        "aggregate": _descriptive(values),
        "methods": {name: _descriptive(values) for name in study.PUBLISHED_METHOD_ORDER},
    }


def _reference_descriptors(runs: list[dict[str, object]]) -> dict[str, object]:
    references = [cast(dict[str, object], run["reference"]) for run in runs]
    packet_totals = [cast(dict[str, int], reference["packet_totals"]) for reference in references]
    byte_totals = [cast(dict[str, int], reference["byte_totals"]) for reference in references]
    return {
        "packet_count": _descriptive([cast(int, reference["packet_count"]) for reference in references]),
        "observation_window_seconds": _descriptive(
            [cast(float, reference["observation_window_seconds"]) for reference in references]
        ),
        "outbound_packets": _descriptive([totals["outbound"] for totals in packet_totals]),
        "inbound_packets": _descriptive([totals["inbound"] for totals in packet_totals]),
        "outbound_bytes": _descriptive([totals["outbound"] for totals in byte_totals]),
        "inbound_bytes": _descriptive([totals["inbound"] for totals in byte_totals]),
    }


def _valid_result_document(repository_root: Path) -> dict[str, object]:
    study_id = "study-1"
    url = "https://downloads.example.test/object.bin"
    prerequisite = cast(dict[str, object], json.loads(study.render_prerequisite_results(_valid_prerequisite())))
    capability = prerequisite["capability"]
    runs = [
        _run_document(study_id, order, run_id, workload, repeat)
        for order, run_id, workload, repeat in study.PRIMARY_ORDER
    ]
    by_workload: dict[str, list[dict[str, object]]] = {}
    for workload in ("short", "streaming", "bursty"):
        workload_runs = [run for run in runs if cast(dict[str, object], run["key"])["workload"] == workload]
        by_workload[workload] = sorted(
            workload_runs,
            key=lambda run: cast(int, cast(dict[str, object], run["key"])["repeat"]),
        )
    natural_variation: list[dict[str, object]] = []
    workload_summaries: list[dict[str, object]] = []
    for workload in ("short", "streaming", "bursty"):
        workload_runs = by_workload[workload]
        pairs: list[dict[str, object]] = []
        for left, right in ((1, 2), (1, 3), (2, 3)):
            forward_value = 0.40 + 0.01 * left + 0.02 * right
            reverse_value = forward_value + 0.02
            pairs.append(
                {
                    "left_repeat": left,
                    "right_repeat": right,
                    "forward": _score(forward_value),
                    "reverse": _score(reverse_value),
                    "symmetric": _score((forward_value + reverse_value) / 2.0),
                }
            )
        descriptors = _reference_descriptors(workload_runs)
        natural_variation.append(
            {"workload": workload, "pairs": pairs, "reference_descriptors": copy.deepcopy(descriptors)}
        )
        champion_summaries: dict[str, object] = {}
        for family_index, family in enumerate(study.FAMILY_ORDER):
            values = [0.50 + 0.05 * family_index + 0.01 * repeat for repeat in (1, 2, 3)]
            champion_summaries[family] = {
                "selection_fitness": _descriptive(values),
                "selection_components": {name: _descriptive(values) for name in study.PUBLISHED_METHOD_ORDER},
            }
        winners = [cast(dict[str, object], run["winner"]) for run in workload_runs]
        winner_values = [cast(float, winner["selection_fitness"]) for winner in winners]
        held_values = [
            cast(float, cast(dict[str, object], cast(dict[str, object], run["fresh_simulation"])["score"])["aggregate"])
            for run in workload_runs
        ]
        published_values = [
            cast(float, cast(dict[str, object], cast(dict[str, object], run["published"])["score"])["aggregate"])
            for run in workload_runs
        ]
        workload_summaries.append(
            {
                "workload": workload,
                "runtime": _descriptive([cast(float, run["elapsed_seconds"]) for run in workload_runs]),
                "family_champions": champion_summaries,
                "winner_selection_fitness": _descriptive(winner_values),
                "fresh_simulation": _score_summary(held_values),
                "published": _score_summary(published_values),
                "reference_descriptors": copy.deepcopy(descriptors),
                "winner_counts": {
                    "markov_renewal": 0,
                    "mmpp": 0,
                    "poisson_empirical": 3,
                },
            }
        )

    source = by_workload["streaming"][1]
    reproduction_run_id = "10-streaming-r2-reproduction"
    reproduction_champions = _champions(2, delta=0.01)
    reproduction_winner = reproduction_champions[2]
    reproduction_held = 0.73
    reproduction_published = 0.68
    source_winner = cast(dict[str, object], source["winner"])
    source_held_score = cast(dict[str, object], cast(dict[str, object], source["fresh_simulation"])["score"])
    source_published_score = cast(dict[str, object], cast(dict[str, object], source["published"])["score"])
    config_path = f"runs/validation_study/{study_id}/realized-configs/reproduction.toml"
    command = ["uv", "run", "--locked", "trafficlab", "run", config_path]
    guard_command = [
        "scripts/run_bounded.sh",
        "--memory-high",
        "2G",
        "--memory-max",
        "3G",
        "--swap-max",
        "512M",
        "--wall-time",
        "20m",
        "--kill-after",
        "10s",
        "--",
        *command,
    ]
    reproduction = {
        "source_key": {"workload": "streaming", "repeat": 2},
        "execution_order": 10,
        "run_id": reproduction_run_id,
        "config_path": config_path,
        "run_directory": f"runs/validation_study/{study_id}/{reproduction_run_id}",
        "transfer_evidence_directory": (
            f"examples/validation_study/.study-work/evidence/{study_id}/{reproduction_run_id}"
        ),
        "command": command,
        "guard_command": guard_command,
        "guard_exit_status": 0,
        "guard_stdout_sha256": _HASH,
        "guard_stderr_sha256": _HASH,
        "elapsed_seconds": 4.0,
        "changed_config_fields": ["run.directory"],
        "same_locked_config": True,
        "seeded_artifact_count": 0,
        "cleanup_verified": True,
        "reuse": {"capture": False, "best_model": False, "generated": False, "similarity": False},
        "transfer_responses": _transfer_responses(study_id, reproduction_run_id, "streaming"),
        "artifact_sha256": {name: "f" * 64 for name in study.ARTIFACT_NAMES},
        "reference": _trace_summary("streaming", 2),
        "generated": _trace_summary("streaming", 2, generated=True),
        "family_champions": reproduction_champions,
        "winner": {
            "family": reproduction_winner["family"],
            "candidate_id": reproduction_winner["candidate_id"],
            "genes": reproduction_winner["genes"],
            "selection_fitness": reproduction_winner["selection_fitness"],
        },
        "fresh_simulation": {
            "seed": 97,
            "score": _score(reproduction_held),
            "source": "post_cli_evaluate_final",
        },
        "published": {"seed": 97, "score": _score(reproduction_published)},
        "raw_sequence": {
            "seed": 97,
            "observation_window_seconds": 12.0,
            "trial_event_count": 6,
            "final_event_count": 6,
            "raw_events_equal": True,
            "fresh_simulation_score_reproduced": True,
            "reparsed_event_count": 6,
            "reparsed_matches_quantized": True,
        },
        "comparison_to_source": {
            "winner_family_equal": reproduction_winner["family"] == source_winner["family"],
            "winner_genes_equal": reproduction_winner["genes"] == source_winner["genes"],
            "winner_selection_fitness_delta": (
                cast(float, reproduction_winner["selection_fitness"]) - cast(float, source_winner["selection_fitness"])
            ),
            "fresh_simulation_delta": {
                "aggregate": reproduction_held - cast(float, source_held_score["aggregate"]),
                "methods": {
                    name: reproduction_held - cast(float, cast(dict[str, object], source_held_score["methods"])[name])
                    for name in study.PUBLISHED_METHOD_ORDER
                },
            },
            "published_delta": {
                "aggregate": reproduction_published - cast(float, source_published_score["aggregate"]),
                "methods": {
                    name: reproduction_published
                    - cast(float, cast(dict[str, object], source_published_score["methods"])[name])
                    for name in study.PUBLISHED_METHOD_ORDER
                },
            },
            "reference_similarity": _score(0.5),
        },
    }
    return {
        "schema_version": 1,
        "environment": {
            "git_commit": prerequisite["git_commit"],
            "python_version": "3.12.3",
            "trafficlab_version": "0.1.0",
            "docker_engine_version": "27.0.0",
            "docker_compose_version": "2.29.0",
            "platform": "Linux-test",
            "target_image_id": _IMAGE_ID,
            "capture_image_id": f"sha256:{'d' * 64}",
            "study_date_utc": "2026-08-13T13:00:00Z",
        },
        "protocol": {
            "study_id": study_id,
            "url": url,
            "capability": capability,
            "prerequisites_sha256": _HASH,
            "target_reference": study.TARGET_REFERENCE,
            "capture_image_id": f"sha256:{'d' * 64}",
            "transfer_evidence_mount_source": f"examples/validation_study/.study-work/mount/{study_id}",
            "base_config_sha256": {"short": _HASH, "streaming": _HASH, "bursty": _HASH},
            "primary_order": [
                {"workload": workload, "repeat": repeat} for _order, _run_id, workload, repeat in study.PRIMARY_ORDER
            ],
            "seeds": {"master": 73, "final": 97, "selection": [17, 29]},
            "families": list(study.FAMILY_ORDER),
            "methods": list(study.PUBLISHED_METHOD_ORDER),
            "workloads": [
                {
                    "name": spec.name,
                    "argv": list(spec.argv),
                    "workload_timeout_seconds": spec.workload_timeout_seconds,
                    "total_timeout_seconds": spec.total_timeout_seconds,
                    "multiscale_widths_seconds": list(spec.multiscale_widths_seconds),
                }
                for spec in study.workload_specs(url)
            ],
            "runtime_boundary": study.RUNTIME_BOUNDARY,
        },
        "runs": runs,
        "natural_variation": natural_variation,
        "workload_summaries": workload_summaries,
        "reproduction": reproduction,
    }


def _result_value(document: dict[str, object]) -> study.StudyResults:
    run_values: list[study.StudyRunRecord] = []
    for item in cast(list[dict[str, object]], document["runs"]):
        champions = tuple(_frozen(value) for value in cast(list[study.JsonObject], item["family_champions"]))
        run_values.append(
            study.StudyRunRecord(
                execution_order=cast(int, item["execution_order"]),
                run_id=cast(str, item["run_id"]),
                key=_frozen(cast(study.JsonObject, item["key"])),
                config_path=cast(str, item["config_path"]),
                run_directory=cast(str, item["run_directory"]),
                transfer_evidence_directory=cast(str, item["transfer_evidence_directory"]),
                elapsed_seconds=cast(float, item["elapsed_seconds"]),
                reuse=_frozen(cast(study.JsonObject, item["reuse"])),
                cleanup_verified=cast(bool, item["cleanup_verified"]),
                transfer_responses=tuple(
                    _frozen(value) for value in cast(list[study.JsonObject], item["transfer_responses"])
                ),
                artifact_sha256=_frozen(cast(study.JsonObject, item["artifact_sha256"])),
                reference=_frozen(cast(study.JsonObject, item["reference"])),
                generated=_frozen(cast(study.JsonObject, item["generated"])),
                family_champions=cast(
                    tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject], champions
                ),
                winner=_frozen(cast(study.JsonObject, item["winner"])),
                fresh_simulation=_frozen(cast(study.JsonObject, item["fresh_simulation"])),
                published=_frozen(cast(study.JsonObject, item["published"])),
                raw_sequence=_frozen(cast(study.JsonObject, item["raw_sequence"])),
            )
        )
    natural = tuple(_frozen(value) for value in cast(list[study.JsonObject], document["natural_variation"]))
    summaries = tuple(_frozen(value) for value in cast(list[study.JsonObject], document["workload_summaries"]))
    return study.StudyResults(
        schema_version=cast(int, document["schema_version"]),
        environment=_frozen(cast(study.JsonObject, document["environment"])),
        protocol=_frozen(cast(study.JsonObject, document["protocol"])),
        runs=tuple(run_values),
        natural_variation=cast(tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject], natural),
        workload_summaries=cast(
            tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject], summaries
        ),
        reproduction=study.ReproductionRecord(_frozen(cast(study.JsonObject, document["reproduction"]))),
    )


def _trial_result(seed: int, value: float) -> TrialResult:
    methods = tuple(
        MethodTrialResult(
            name,
            value,
            {"observation_window_seconds": 3.0, "seed": seed},
        )
        for name in study.PUBLISHED_METHOD_ORDER
    )
    return TrialResult(
        seed, value, cast(tuple[MethodTrialResult, MethodTrialResult, MethodTrialResult, MethodTrialResult], methods)
    )


def _evaluated_candidate(
    identifier: CandidateId,
    family: study.FamilyName,
    genes: tuple[int | float, ...],
    first_score: float,
    second_score: float,
) -> Candidate:
    diagnostics = {name: 0 for name in MARKOV_MODEL_DIAGNOSTIC_KEYS} if family == "markov_renewal" else {}
    trials = (
        replace(_trial_result(17, first_score), model_diagnostics=diagnostics),
        replace(_trial_result(29, second_score), model_diagnostics=diagnostics),
    )
    return Candidate(
        identifier,
        family,
        genes,
        "valid",
        math.fsum(trial.aggregate_score for trial in trials) / 2.0,
        trials,
        None,
        (),
    )


def _terminal_checkpoint_and_best(tmp_path: Path) -> tuple[CheckpointState, BestModel, ComparisonResult]:
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(1.0, Direction.INBOUND, 100),
        TraceEvent(2.0, Direction.OUTBOUND, 200),
        TraceEvent(3.0, Direction.INBOUND, 300),
    )
    config = study.build_base_config(
        study.workload_specs("https://downloads.example.test/object.bin")[0],
        repository_root=tmp_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        capture_image_id=f"sha256:{'d' * 64}",
    )
    context = make_strategy_context(
        config,
        reference,
        3.0,
        tmp_path / "run",
        experiment_identity=ContentIdentity(size=1, sha256="a" * 64),
        reference_identity=ContentIdentity(size=2, sha256="b" * 64),
        capture_identity=ContentIdentity(size=3, sha256="c" * 64),
    )
    population = (
        _evaluated_candidate(CandidateId(2, 3), "markov_renewal", (0.2, 0.7, 1.0, 4, 1.0), 0.5, 0.7),
        _evaluated_candidate(CandidateId(2, 0), "markov_renewal", (0.25, 0.75, 0.5, 3, 1.2), 0.6, 0.6),
        _evaluated_candidate(CandidateId(2, 4), "mmpp", (1.0, 2.0, 10.0, 20.0), 0.6, 0.8),
        _evaluated_candidate(CandidateId(2, 1), "mmpp", (1.5, 2.5, 12.0, 24.0), 0.6, 0.7),
        _evaluated_candidate(CandidateId(2, 5), "poisson_empirical", (1.0,), 0.8, 1.0),
        _evaluated_candidate(CandidateId(2, 2), "poisson_empirical", (1.5,), 0.7, 0.9),
    )
    state = CheckpointState(
        context.compatibility,
        2,
        population,
        (),
        encode_rng_state(Random(73).getstate()),
        CandidateId(2, 5),
        0.9,
        0,
        "hard_limit",
        context.compatibility.family_priority,
    )
    bounds = config.models.poisson_empirical
    assert bounds is not None
    best = make_best_model(
        get_family("poisson_empirical"),
        reference,
        (1.0,),
        reference_identity=ContentIdentity(size=2, sha256="b" * 64),
        capture_identity=ContentIdentity(size=3, sha256="c" * 64),
        final_seed=config.run.final_seed,
        final_limits=config.generation.final,
        W=3.0,
        bounds=bounds,
    )
    comparison = compare_traces(reference, reference, 3.0, config.similarity)
    return state, best, comparison


def test_family_champions_use_terminal_valid_candidates_stable_ids_and_selection_means(tmp_path: Path) -> None:
    state, _best, _comparison = _terminal_checkpoint_and_best(tmp_path)

    champions = study._family_champions(state)  # pyright: ignore[reportPrivateUsage]

    assert tuple(item["family"] for item in champions) == study.FAMILY_ORDER
    assert champions[0]["candidate_id"] == {"birth_generation": 2, "birth_index": 0}
    assert champions[0]["selection_seeds"] == [17, 29]
    assert champions[0]["selection_fitness"] == 0.6
    assert champions[0]["selection_score"] == {
        "aggregate": 0.6,
        "methods": {name: 0.6 for name in study.PUBLISHED_METHOD_ORDER},
    }
    assert champions[1]["selection_fitness"] == 0.7
    assert champions[2]["selection_fitness"] == 0.9


def test_winner_fresh_simulation_and_published_records_remain_distinct(tmp_path: Path) -> None:
    state, best, comparison = _terminal_checkpoint_and_best(tmp_path)
    final_trial = _trial_result(97, 0.75)

    winner = study._winner(state, best)  # pyright: ignore[reportPrivateUsage]
    fresh_simulation = {
        "seed": final_trial.seed,
        "score": study._score_from_trial(final_trial),  # pyright: ignore[reportPrivateUsage]
        "source": "run_experiment_fit_outcome",
    }
    published = {
        "seed": 97,
        "score": study._score_from_comparison(comparison),  # pyright: ignore[reportPrivateUsage]
    }

    assert winner == {
        "family": "poisson_empirical",
        "candidate_id": {"birth_generation": 2, "birth_index": 5},
        "genes": [1.0],
        "selection_fitness": 0.9,
    }
    assert fresh_simulation == {
        "seed": 97,
        "score": {"aggregate": 0.75, "methods": {name: 0.75 for name in study.PUBLISHED_METHOD_ORDER}},
        "source": "run_experiment_fit_outcome",
    }
    assert published["score"] == {"aggregate": 1.0, "methods": {name: 1.0 for name in study.PUBLISHED_METHOD_ORDER}}
    assert fresh_simulation != published


def _offline_capture(_path: Path, prepared: PreparedExperiment) -> CaptureResult:
    capture_path = prepared.run_directory / "capture.json"
    reference_path = prepared.run_directory / "reference.pcapng"
    capture_path.write_bytes(_CAPTURE_BYTES)
    reference_path.write_bytes(_REFERENCE_BYTES)
    inspection = validate_capture_pair(capture_path, reference_path, deadline=None)
    append_run_log(
        prepared.run_directory,
        {
            "event": "capture_published",
            "packet_count": inspection.packet_count,
            "path": str(reference_path),
            "project_name": "trafficlab-validation-study-unit",
            "reused": False,
            "stage": "capture",
        },
    )
    return CaptureResult(prepared.run_directory, reference_path, inspection.packet_count, 0, reused=False)


def _offline_validation_study_primary(
    repository_root: Path,
    *,
    execution_order: int = 1,
    run_id: str = "01-short-r1",
    workload_name: study.WorkloadName = "short",
    repeat: int = 1,
    base_config: study.ExperimentConfig | None = None,
) -> tuple[RunResult, study.StudyRunSpec, study.WorkloadSpec, tuple[study.JsonObject, ...]]:
    repository_root.mkdir(exist_ok=True)
    url = "https://downloads.example.test/object.bin"
    study_id = "study-1"
    workload = {value.name: value for value in study.workload_specs(url)}[workload_name]
    mount = repository_root / "examples" / "validation_study" / ".study-work" / "mount" / study_id
    mount.mkdir(parents=True, exist_ok=True)
    config = base_config or study.build_base_config(
        workload,
        repository_root=repository_root,
        study_id=study_id,
        url=url,
        capture_image_id=f"sha256:{'d' * 64}",
    )
    if config.run.directory.name != run_id:
        config = study._config_with_run_directory(  # pyright: ignore[reportPrivateUsage]
            config,
            repository_root / "runs" / "validation_study" / study_id / run_id,
        )
    config_path = repository_root / "runs" / "validation_study" / study_id / "realized-configs" / f"{run_id}.toml"
    study._render_realized_config(config, config_path)  # pyright: ignore[reportPrivateUsage]

    result = run_experiment(
        config_path,
        dependencies=RunDependencies(
            open_or_prepare_experiment,
            _offline_capture,
            fit_experiment,
            generate_experiment,
            compare_experiment,
        ),
    )
    evidence_directory = (
        repository_root / "examples" / "validation_study" / ".study-work" / "evidence" / study_id / run_id
    )
    evidence_directory.mkdir(parents=True)
    transfer_responses_list: list[study.JsonObject] = []
    for index, (start, end, filename) in enumerate(workload.transfers):
        header_bytes = _response_headers(start, end)
        header = evidence_directory / filename
        header.write_bytes(header_bytes)
        header.chmod(0o600)
        transfer_responses_list.append(
            {
                "transfer_index": index,
                "requested_start": start,
                "requested_end": end,
                "status": 206,
                "content_length": end - start + 1,
                "content_range": f"bytes {start}-{end}/4194304",
                "header_archive_path": header.relative_to(repository_root).as_posix(),
                "header_sha256": hashlib.sha256(header_bytes).hexdigest(),
                "scratch_precreate_mode": 438,
                "archive_mode": 384,
                "inode_preserved": True,
            }
        )
    transfer_responses = tuple(transfer_responses_list)
    spec = study.StudyRunSpec(
        execution_order,
        run_id,
        workload_name,
        repeat,
        config_path,
        config.run.directory,
        evidence_directory,
    )
    return result, spec, workload, transfer_responses


def test_trace_summary_uses_canonical_events_and_multiscale_direction_totals(tmp_path: Path) -> None:
    config = study.build_base_config(
        study.workload_specs("https://downloads.example.test/object.bin")[0],
        repository_root=tmp_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        capture_image_id=f"sha256:{'d' * 64}",
    )
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(0.0, Direction.INBOUND, 100),
        TraceEvent(1.0, Direction.OUTBOUND, 200),
        TraceEvent(3.0, Direction.INBOUND, 300),
    )
    generated = (
        TraceEvent(0.0, Direction.INBOUND, 80),
        TraceEvent(0.5, Direction.OUTBOUND, 120),
        TraceEvent(1.5, Direction.INBOUND, 160),
        TraceEvent(3.0, Direction.OUTBOUND, 240),
    )
    comparison = compare_traces(reference, generated, 3.0, config.similarity)

    reference_summary = study._trace_summary(  # pyright: ignore[reportPrivateUsage]
        reference, comparison, role="reference"
    )
    generated_summary = study._trace_summary(  # pyright: ignore[reportPrivateUsage]
        generated, comparison, role="generated"
    )

    assert reference_summary == {
        "packet_count": 4,
        "observation_window_seconds": 3.0,
        "packet_totals": {"outbound": 2, "inbound": 2},
        "byte_totals": {"outbound": 260, "inbound": 400},
        "frame_lengths": {
            "count": 4,
            "minimum": 60.0,
            "median": 150.0,
            "quantile_probability": 0.95,
            "quantile": 300.0,
            "maximum": 300.0,
            "zero_count": 0,
        },
        "iats": {
            "count": 3,
            "minimum": 0.0,
            "median": 1.0,
            "quantile_probability": 0.95,
            "quantile": 2.0,
            "maximum": 2.0,
            "zero_count": 1,
        },
        "scales": [
            {
                "width_seconds": width,
                "bins_per_direction": bins,
                "packet_totals": {"outbound": 2, "inbound": 2},
                "byte_totals": {"outbound": 260, "inbound": 400},
            }
            for width, bins in ((0.001, 3000), (0.01, 300))
        ],
    }
    assert generated_summary["packet_totals"] == {"outbound": 2, "inbound": 2}
    assert generated_summary["byte_totals"] == {"outbound": 360, "inbound": 240}
    assert generated_summary["frame_lengths"] == {
        "count": 4,
        "minimum": 80.0,
        "median": 140.0,
        "quantile_probability": 0.95,
        "quantile": 240.0,
        "maximum": 240.0,
        "zero_count": 0,
    }


def test_primary_extraction_reloads_nine_artifacts_and_proves_raw_quantized_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, spec, workload, transfer_responses = _offline_validation_study_primary(tmp_path / "repository")
    authoritative_trial = result.fit.outcome.final_trials[0]
    observed_trials: list[TrialResult] = []
    real_reconstruct = study._reconstruct_science  # pyright: ignore[reportPrivateUsage]

    def reconstruct(
        evidence: object,
        fresh_simulation: TrialResult,
        *,
        generated_path: Path,
    ) -> object:
        observed_trials.append(fresh_simulation)
        return real_reconstruct(evidence, fresh_simulation, generated_path=generated_path)  # type: ignore[arg-type]

    monkeypatch.setattr(study, "_reconstruct_science", reconstruct)

    def reject_evaluate(
        _candidate: Candidate,
        _context: ValidatedEvaluationContext,
        _seed: int,
    ) -> tuple[TrialResult, ...]:
        raise AssertionError("primary reevaluation")

    monkeypatch.setattr(study, "evaluate_final", reject_evaluate)

    record = study.extract_primary_record(
        tmp_path / "repository",
        spec,
        workload,
        result,
        1.25,
        transfer_responses,
    )

    assert tuple(item["family"] for item in record.family_champions) == study.FAMILY_ORDER
    assert record.reuse == {"capture": False, "best_model": False, "generated": False, "similarity": False}
    assert record.cleanup_verified is True
    assert set(record.artifact_sha256) == set(study.ARTIFACT_NAMES)
    assert record.fresh_simulation["source"] == "run_experiment_fit_outcome"
    assert observed_trials == [authoritative_trial]
    assert observed_trials[0] is authoritative_trial
    assert record.raw_sequence == {
        "seed": 97,
        "observation_window_seconds": 10.0,
        "trial_event_count": len(result.fit.outcome.final_trials) and len(result.generation.events),
        "final_event_count": len(result.generation.events),
        "raw_events_equal": True,
        "fresh_simulation_score_reproduced": True,
        "reparsed_event_count": len(result.generation.events),
        "reparsed_matches_quantized": True,
    }
    assert sorted(path.name for path in spec.run_directory.iterdir()) == sorted(study.ARTIFACT_NAMES)


def _install_prerequisite_failure(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if mutation == "wrong-python":
        monkeypatch.setattr(study.platform, "python_version", lambda: "3.12.4")
    if mutation == "config-publication-failed":
        original_render = study.render_checked_base_config
        calls = 0

        def fail_second_config(
            config: study.ExperimentConfig,
            destination: Path,
            root: Path,
        ) -> bytes:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated config publication failure")
            return original_render(config, destination, root)

        monkeypatch.setattr(study, "render_checked_base_config", fail_second_config)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-artifact",
        "tenth-run-entry",
        "reused-stage",
        "checkpoint-mismatch",
        "history-mismatch",
        "best-model-mismatch",
        "held-out-wrong-seed",
        "raw-trial-final-differ",
        "raw-score-differ",
        "quantized-events-differ",
        "similarity-lineage-differ",
        "cleanup-not-proven",
    ],
)
def test_run_extraction_rejects_missing_malformed_inconsistent_or_reused_evidence(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    result, spec, workload, transfer_responses = _offline_validation_study_primary(repository_root)

    if mutation == "missing-artifact":
        (spec.run_directory / "run.log").unlink()
    elif mutation == "tenth-run-entry":
        (spec.run_directory / "unexpected").write_bytes(b"unexpected")
    elif mutation == "reused-stage":
        result = replace(result, capture=replace(result.capture, reused=True))
    elif mutation == "checkpoint-mismatch":
        result = replace(result, fit=replace(result.fit, outcome=replace(result.fit.outcome, generation=99)))
    elif mutation == "history-mismatch":
        with (spec.run_directory / "ga_history.csv").open("ab") as stream:
            stream.write(b"stale\n")
    elif mutation == "best-model-mismatch":
        with (spec.run_directory / "best_model.json").open("ab") as stream:
            stream.write(b" ")
    elif mutation == "held-out-wrong-seed":
        trial = replace(result.fit.outcome.final_trials[0], seed=17)
        result = replace(result, fit=replace(result.fit, outcome=replace(result.fit.outcome, final_trials=(trial,))))
    elif mutation == "raw-trial-final-differ":
        original_family = get_family(result.fit.best_model.family)

        class DifferingFinalFamily:
            def __getattr__(self, name: str) -> object:
                return getattr(original_family, name)

            def generate(
                self,
                model: FittedModel,
                seed: int,
                W: float,
                limits: GenerationLimits,
            ) -> GenerationResult:
                generated = original_family.generate(model, seed, W, limits)
                if limits == study.load_experiment(spec.config_path).generation.final:
                    first, *remaining = generated.events
                    changed = TraceEvent(first.timestamp, first.direction, first.frame_length + 1)
                    return replace(generated, events=(changed, *remaining))
                return generated

        def differing_family(_name: str) -> Any:
            return DifferingFinalFamily()

        monkeypatch.setattr(
            study,
            "get_family",
            differing_family,
            raising=False,
        )
    elif mutation == "raw-score-differ":
        original = result.fit.outcome.final_trials[0]
        aggregate = 0.0 if original.aggregate_score != 0.0 else 1.0
        trial = replace(original, aggregate_score=aggregate)
        result = replace(result, fit=replace(result.fit, outcome=replace(result.fit.outcome, final_trials=(trial,))))
    elif mutation == "quantized-events-differ":
        first, *remaining = result.generation.events
        changed = TraceEvent(first.timestamp, first.direction, first.frame_length + 1)
        result = replace(result, generation=replace(result.generation, events=(changed, *remaining)))
    elif mutation == "similarity-lineage-differ":
        identities = dict(cast(dict[str, ContentIdentity], result.comparison.input_identities))
        identities["capture_json"] = ContentIdentity(size=identities["capture_json"].size, sha256="0" * 64)
        result = replace(result, comparison=result.comparison.with_input_identities(identities))
    elif mutation == "cleanup-not-proven":
        run_log = spec.run_directory / "run.log"
        records = [json.loads(line) for line in run_log.read_text().splitlines()]
        next(record for record in records if record.get("event") == "capture_published")["event"] = "capture_missing"
        run_log.write_text(
            "".join(f"{json.dumps(record, sort_keys=True, separators=(',', ':'))}\n" for record in records)
        )

    with pytest.raises((TrafficlabError, TypeError, ValueError)):
        study.extract_primary_record(
            repository_root,
            spec,
            workload,
            result,
            1.25,
            transfer_responses,
        )


def _natural_variation_inputs(
    tmp_path: Path,
) -> tuple[
    tuple[study.StudyRunRecord, ...],
    dict[tuple[study.WorkloadName, int], tuple[TraceEvent, ...]],
    dict[study.WorkloadName, SimilarityConfig],
    dict[str, object],
]:
    document = _valid_result_document(tmp_path)
    records = _result_value(document).runs
    url = "https://downloads.example.test/object.bin"
    traces: dict[tuple[study.WorkloadName, int], tuple[TraceEvent, ...]] = {}
    settings: dict[study.WorkloadName, SimilarityConfig] = {}
    for workload in study.workload_specs(url):
        config = study.build_base_config(
            workload,
            repository_root=tmp_path,
            study_id="study-1",
            url=url,
            capture_image_id=f"sha256:{'d' * 64}",
        )
        settings[workload.name] = config.similarity
        for repeat in (1, 2, 3):
            start = float(10 * repeat)
            traces[(workload.name, repeat)] = (
                TraceEvent(start, Direction.OUTBOUND, 60 + repeat),
                TraceEvent(start + 0.25, Direction.INBOUND, 100 + repeat),
                TraceEvent(start + 1.0, Direction.OUTBOUND, 180 + repeat),
                TraceEvent(start + float(repeat + 2), Direction.INBOUND, 260 + repeat),
            )
    return records, traces, settings, document


def test_natural_variation_compares_each_pair_in_both_directions_and_averages_scores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records, traces, settings, document = _natural_variation_inputs(tmp_path)
    calls: list[
        tuple[
            tuple[TraceEvent, ...],
            tuple[TraceEvent, ...],
            float,
            SimilarityConfig,
            ComparisonResult,
        ]
    ] = []

    def comparison_spy(
        reference: tuple[TraceEvent, ...],
        generated: tuple[TraceEvent, ...],
        window: float,
        config: SimilarityConfig,
    ) -> ComparisonResult:
        comparison = compare_traces(reference, generated, window, config)
        calls.append((reference, generated, window, config, comparison))
        return comparison

    monkeypatch.setattr(study, "compare_traces", comparison_spy)

    variation = study.natural_variation(records, traces, settings)

    workloads: tuple[study.WorkloadName, ...] = ("short", "streaming", "bursty")
    pairs = ((1, 2), (1, 3), (2, 3))
    assert len(calls) == 18
    call_index = 0
    expected_natural = cast(list[dict[str, object]], document["natural_variation"])
    for workload_index, workload in enumerate(workloads):
        record = variation[workload_index]
        assert record["workload"] == workload
        assert record["reference_descriptors"] == expected_natural[workload_index]["reference_descriptors"]
        result_pairs = cast(list[study.JsonValue], record["pairs"])
        assert (
            tuple(
                (
                    cast(dict[str, study.JsonValue], item)["left_repeat"],
                    cast(dict[str, study.JsonValue], item)["right_repeat"],
                )
                for item in result_pairs
            )
            == pairs
        )
        for pair_index, (left, right) in enumerate(pairs):
            pair = cast(study.JsonObject, result_pairs[pair_index])
            for source_repeat, generated_repeat, field in (
                (left, right, "forward"),
                (right, left, "reverse"),
            ):
                expected_reference, expected_window = normalize_reference(traces[(workload, source_repeat)])
                expected_generated = align_generated(traces[(workload, generated_repeat)], expected_window)
                actual_reference, actual_generated, actual_window, actual_settings, comparison = calls[call_index]
                assert (actual_reference, actual_generated, actual_window) == (
                    expected_reference,
                    expected_generated,
                    expected_window,
                )
                assert actual_settings is settings[workload]
                assert pair[field] == study._score_from_comparison(  # pyright: ignore[reportPrivateUsage]
                    comparison
                )
                call_index += 1
            assert pair["symmetric"] == study._average_score(  # pyright: ignore[reportPrivateUsage]
                cast(study.JsonObject, pair["forward"]),
                cast(study.JsonObject, pair["reverse"]),
            )


def test_workload_summaries_recompute_runtime_family_score_variance_and_winner_counts(tmp_path: Path) -> None:
    records, _traces, _settings, document = _natural_variation_inputs(tmp_path)

    summaries = study.workload_summaries(tuple(reversed(records)))

    assert summaries == tuple(cast(list[study.JsonObject], document["workload_summaries"]))
    short = summaries[0]
    assert short["runtime"] == study.descriptive_statistics((1.0, 2.0, 3.0))
    families = cast(study.JsonObject, short["family_champions"])
    poisson = cast(study.JsonObject, families["poisson_empirical"])
    assert poisson["selection_fitness"] == study.descriptive_statistics((0.61, 0.62, 0.63))
    assert short["winner_counts"] == {"markov_renewal": 0, "mmpp": 0, "poisson_empirical": 3}


def test_natural_variation_propagates_metric_precondition_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records, traces, settings, _document = _natural_variation_inputs(tmp_path)
    failure = TrafficlabError(
        "natural comparison precondition failed",
        corrective_action="retain the reference evidence",
    )

    def failing_comparison(
        reference: tuple[TraceEvent, ...],
        generated: tuple[TraceEvent, ...],
        window: float,
        config: SimilarityConfig,
    ) -> ComparisonResult:
        del reference, generated, window, config
        raise failure

    monkeypatch.setattr(study, "compare_traces", failing_comparison)

    with pytest.raises(TrafficlabError, match="natural comparison precondition failed") as captured:
        study.natural_variation(records, traces, settings)

    assert captured.value is failure


def test_study_id_url_repository_path_and_utc_validators_are_exact(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    assert study.validate_study_id("study-1") == "study-1"
    assert study.validate_endpoint_url("https://downloads.example.test/object.bin") == (
        "https://downloads.example.test/object.bin"
    )
    assert (
        study._repository_relative_path(  # pyright: ignore[reportPrivateUsage]
            "evidence/study-1/file", repository_root=repository_root, name="evidence path"
        )
        == "evidence/study-1/file"
    )
    assert (
        study._utc_timestamp(  # pyright: ignore[reportPrivateUsage]
            "2026-08-13T12:00:00Z", name="created time"
        )
        == "2026-08-13T12:00:00Z"
    )

    for value in ("", "Study-1", "study_1", "-study", "a" * 33):
        with pytest.raises(ValueError, match="study ID"):
            study.validate_study_id(value)
    for value in (
        "http://downloads.example.test/object.bin",
        "https://user@downloads.example.test/object.bin",
        "https://downloads.example.test/object.bin?token=x",
        "https://downloads.example.test/object.bin#fragment",
        "https://127.0.0.1/object.bin",
        "https:///object.bin",
    ):
        with pytest.raises(ValueError, match="URL"):
            study.validate_endpoint_url(value)
    for value in (
        "/evidence/study-1/file",
        "evidence\\study-1\\file",
        "evidence//file",
        "evidence/./file",
        "evidence/../file",
        "",
    ):
        with pytest.raises(ValueError, match="repository-relative|nonempty"):
            study._repository_relative_path(  # pyright: ignore[reportPrivateUsage]
                value, repository_root=repository_root, name="evidence path"
            )
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository_root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="repository-relative"):
        study._repository_relative_path(  # pyright: ignore[reportPrivateUsage]
            "escape/file", repository_root=repository_root, name="evidence path"
        )
    for value in (
        "2026-08-13T12:00:00+00:00",
        "2026-08-13T12:00:00z",
        "2026-08-13T12:00:00",
        "2026-02-30T12:00:00Z",
    ):
        with pytest.raises(ValueError, match="UTC RFC 3339"):
            study._utc_timestamp(value, name="created time")  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(ValueError, match="integer"):
        study._strict_int(True, name="count")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="float"):
        study._strict_float(1, name="score")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match=r"\[0.0, 1.0\]"):
        study._strict_float(1.1, name="score", lower=0.0, upper=1.0)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="finite"):
        study._strict_float(math.inf, name="score")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="SHA-256"):
        study._sha256("A" * 64, name="hash")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="duplicate JSON key"):
        study._load_json(b'{"value":1,"value":2}\n')  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="invalid JSON constant"):
        study._load_json(b'{"value":NaN}\n')  # pyright: ignore[reportPrivateUsage]


def test_endpoint_contract_rejects_noncredential_free_https_object_urls() -> None:
    assert study.validate_endpoint_url("https://downloads.example.test/object.bin") == (
        "https://downloads.example.test/object.bin"
    )
    for value in [
        "http://example.test/object",
        "https://user@example.test/object",
        "https://example.test/object?query=1",
        "https://example.test/object#fragment",
        "https://127.0.0.1/object",
        "https:///object",
    ]:
        with pytest.raises(ValueError, match="credential-free HTTPS.*DNS hostname"):
            study.validate_endpoint_url(value)


def test_workload_specs_expand_exact_short_streaming_and_eight_bursty_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://downloads.example.test/object.bin"
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
    short_argv = (
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
    streaming_argv = (
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
    starts = (0, 524288, 1048576, 1572864, 2097152, 2621440, 3145728, 3670016)
    bursty_groups: list[str] = []
    for index, start in enumerate(starts):
        if index:
            bursty_groups.append("--next")
        bursty_groups.extend(
            (
                *common,
                "--max-time",
                "30",
                "--range",
                f"{start}-{start + 32767}",
                "--max-filesize",
                "32768",
                "--dump-header",
                f"/trafficlab-study/bursty-{index}.headers",
                "--output",
                "/dev/null",
                "--url",
                url,
            )
        )
    bursty_argv = ("--parallel", "--parallel-max", "4", "--fail-early", *bursty_groups)

    specs = study.workload_specs(url)

    assert specs == (
        study.WorkloadSpec("short", short_argv, ((0, 262143, "short.headers"),), 35.0, 90.0, (0.001, 0.01)),
        study.WorkloadSpec(
            "streaming",
            streaming_argv,
            ((0, 4194303, "streaming.headers"),),
            50.0,
            120.0,
            (0.25, 1.0),
        ),
        study.WorkloadSpec(
            "bursty",
            bursty_argv,
            tuple((start, start + 32767, f"bursty-{index}.headers") for index, start in enumerate(starts)),
            35.0,
            90.0,
            (0.001, 0.01),
        ),
    )
    assert len(specs[2].transfers) == 8
    assert len({filename for _start, _end, filename in specs[2].transfers}) == 8
    assert specs[2].argv[:4] == ("--parallel", "--parallel-max", "4", "--fail-early")
    assert specs[2].argv.count("--next") == 7
    assert specs[2].argv[-1] == url
    assert all("sh" not in spec.argv and "-c" not in spec.argv for spec in specs)

    monkeypatch.setattr(study, "CURL_COMMON", (*common[:-1], "--proto-redir", "=http"))
    with pytest.raises(ValueError, match="exact HTTPS-only curl profile"):
        study.workload_specs(url)


def test_validation_study_mmpp_bounds_retain_a_valid_candidate_for_a_short_observation_window(tmp_path: Path) -> None:
    url = "https://downloads.example.test/object.bin"
    config = study.build_base_config(
        study.workload_specs(url)[0],
        repository_root=tmp_path,
        study_id="study-1",
        url=url,
        capture_image_id=f"sha256:{'d' * 64}",
    )
    window = 0.7874600887298584
    reference = tuple(
        TraceEvent(
            window * index / 176,
            Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND,
            60 if index % 2 == 0 else 100,
        )
        for index in range(177)
    )
    context = make_strategy_context(
        config,
        reference,
        window,
        tmp_path / "run",
        experiment_identity=ContentIdentity(size=1, sha256="a" * 64),
        reference_identity=ContentIdentity(size=2, sha256="b" * 64),
        capture_identity=ContentIdentity(size=3, sha256="c" * 64),
    )
    validated = validate_evaluation_context(context.evaluation)
    pending = initial_population(
        derive_family_priority(config.run.master_seed, config.models.enabled),
        population_size=config.genetic.population_size,
        bounds=validated.bounds,
        reference=validated.reference,
        rng=Random(config.run.master_seed),
    )
    evaluated = tuple(evaluate_candidate(candidate, validated) for candidate in pending)

    assert any(candidate.family == "mmpp" and candidate.status == "valid" for candidate in evaluated)


@pytest.mark.parametrize("workload", ["short", "streaming", "bursty"])
def test_base_config_contains_every_locked_value_and_only_profile_differences(
    workload: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    url = "https://downloads.example.test/object.bin"
    capture_image_id = f"sha256:{'d' * 64}"
    specs = {spec.name: spec for spec in study.workload_specs(url)}

    config = study.build_base_config(
        specs[cast(study.WorkloadName, workload)],
        repository_root=repository_root,
        study_id="study-1",
        url=url,
        capture_image_id=capture_image_id,
    )

    assert config.model_dump(mode="python") == _expected_base_config(repository_root, workload)
    all_configs = {
        name: study.build_base_config(
            spec,
            repository_root=repository_root,
            study_id="study-1",
            url=url,
            capture_image_id=capture_image_id,
        ).model_dump(mode="python")
        for name, spec in specs.items()
    }
    assert _changed_config_paths(all_configs["short"], all_configs["streaming"]) == {
        "run.directory",
        "target.argv",
        "capture.workload_timeout_seconds",
        "capture.total_timeout_seconds",
        "similarity.multiscale_widths_seconds",
    }
    assert _changed_config_paths(all_configs["short"], all_configs["bursty"]) == {
        "run.directory",
        "target.argv",
    }


def test_checked_and_realized_configs_reload_to_exact_absolute_oracles(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    prerequisite, contents = _write_checked_configs(repository_root)

    validated = study.validate_base_configs(repository_root, prerequisite)

    assert tuple(validated) == ("short", "streaming", "bursty")
    for name, config in validated.items():
        assert config.model_dump(mode="python") == _expected_base_config(repository_root, name)
        assert hashlib.sha256(contents[name]).hexdigest() == prerequisite.config_sha256[name]
    portable = tomllib.loads(contents["short"].decode())
    assert cast(dict[str, object], portable["run"])["directory"] == "../../../runs/validation_study/study-1/01-short-r1"
    target = cast(dict[str, object], portable["target"])
    mount = cast(list[dict[str, object]], target["mounts"])[0]
    assert mount["source"] == "../.study-work/mount/study-1"

    realized_directory = (repository_root / "runs" / "validation_study" / "study-1" / "10-streaming-r2").resolve()
    realized = study._config_with_run_directory(  # pyright: ignore[reportPrivateUsage]
        validated["streaming"], realized_directory
    )
    realized_path = repository_root / "runs" / "validation_study" / "study-1" / "realized-configs" / "streaming.toml"
    rendered = study._render_realized_config(realized, realized_path)  # pyright: ignore[reportPrivateUsage]
    assert realized_path.read_bytes() == rendered
    assert study.load_experiment(realized_path) == realized
    assert str(realized_directory) in rendered.decode()

    with pytest.raises(ValueError, match="already exists"):
        study.render_checked_base_config(
            validated["short"],
            repository_root / "examples" / "validation_study" / "configs" / "short.toml",
            repository_root,
        )
    with pytest.raises(ValueError, match="already exists"):
        study._render_realized_config(realized, realized_path)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-capability-header",
        "tamper-capability.headers",
        "tamper-capability.stdout",
        "tamper-capability.stderr",
        "tamper-capability.cid",
        "tamper-capture.iid",
        "tamper-docker.stdout",
        "tamper-docker.stderr",
        "tamper-docker.xml",
        "tamper-internet.stdout",
        "tamper-internet.stderr",
        "tamper-internet.xml",
        "evidence-mode",
        "evidence-read-error",
        "non-ascii-cid",
        "invalid-junit",
        "junit-counts",
        "cid-record",
        "dockerfile-source",
        "capture-script-source",
    ],
)
def test_retained_prerequisite_evidence_reopens_hashes_and_crosschecks_every_authority(
    mutation: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    prerequisite, _contents = _write_checked_configs(repository_root)
    prerequisite = _write_retained_prerequisite_evidence(repository_root, prerequisite)
    evidence = (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / prerequisite.study_id
        / "00-prerequisites"
    )
    if mutation == "missing-capability-header":
        (evidence / "capability.headers").unlink()
    elif mutation.startswith("tamper-"):
        name = mutation.removeprefix("tamper-")
        (evidence / name).write_bytes((evidence / name).read_bytes() + b"changed")
    elif mutation == "evidence-mode":
        (evidence / "internet.stderr").chmod(0o644)
    elif mutation == "evidence-read-error":
        (repository_root / "docker" / "capture" / "Dockerfile").unlink()
    elif mutation == "non-ascii-cid":
        (evidence / "capability.cid").write_bytes(b"\xff\n")
    elif mutation in {"invalid-junit", "junit-counts"}:
        junit = (
            b"not XML"
            if mutation == "invalid-junit"
            else (
                b'<testsuites tests="3" failures="0" errors="0" skipped="0">'
                b'<testsuite tests="3" failures="0" errors="0" skipped="0"/></testsuites>'
            )
        )
        (evidence / "docker.xml").write_bytes(junit)
        commands = [
            cast(study.JsonObject, study._thaw_json(command))  # pyright: ignore[reportPrivateUsage]
            for command in prerequisite.commands
        ]
        commands[0]["junit_sha256"] = hashlib.sha256(junit).hexdigest()
        prerequisite = replace(prerequisite, commands=(_frozen(commands[0]), _frozen(commands[1])))
    elif mutation == "cid-record":
        capability = cast(
            study.JsonObject,
            study._thaw_json(prerequisite.capability),  # pyright: ignore[reportPrivateUsage]
        )
        capability["container_id"] = "SHORT"
        prerequisite = replace(prerequisite, capability=_frozen(capability))
    elif mutation == "dockerfile-source":
        (repository_root / "docker" / "capture" / "Dockerfile").write_bytes(b"changed\n")
    elif mutation == "capture-script-source":
        (repository_root / "docker" / "capture" / "capture.sh").write_bytes(b"changed\n")

    with pytest.raises((TrafficlabError, ValueError)):
        study._validate_prerequisite_evidence(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            prerequisite,
        )


def test_retained_prerequisite_evidence_accepts_exact_local_files(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    prerequisite, _contents = _write_checked_configs(repository_root)
    prerequisite = _write_retained_prerequisite_evidence(repository_root, prerequisite)

    study._validate_prerequisite_evidence(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        prerequisite,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-capture-image",
        "disabled-family",
        "changed-operator",
        "final-seed-reused",
        "wrong-mount",
        "wrong-profile-argv",
        "unexpected-config-difference",
        "existing-run-directory",
        "missing-checked-config",
    ],
)
def test_config_validation_rejects_every_protocol_change(mutation: str, tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    prerequisite, contents = _write_checked_configs(repository_root)
    short_path = repository_root / "examples" / "validation_study" / "configs" / "short.toml"
    short_config = study.build_base_config(
        study.workload_specs(prerequisite.url)[0],
        repository_root=repository_root,
        study_id=prerequisite.study_id,
        url=prerequisite.url,
        capture_image_id=cast(str, prerequisite.images["capture_image_id"]),
    )

    if mutation == "missing-checked-config":
        short_path.unlink()
    elif mutation == "existing-run-directory":
        short_config.run.directory.mkdir(parents=True)
    else:
        document = tomllib.loads(contents["short"].decode())
        run = cast(dict[str, object], document["run"])
        target = cast(dict[str, object], document["target"])
        capture = cast(dict[str, object], document["capture"])
        models = cast(dict[str, object], document["models"])
        if mutation == "wrong-capture-image":
            capture["image"] = f"sha256:{'e' * 64}"
        elif mutation == "disabled-family":
            models["enabled"] = ["poisson_empirical", "markov_renewal"]
            models.pop("mmpp")
        elif mutation == "changed-operator":
            cast(dict[str, object], models["poisson_empirical"])["mutation_scale"] = 0.2
        elif mutation == "final-seed-reused":
            run["final_seed"] = 17
        elif mutation == "wrong-mount":
            cast(list[dict[str, object]], target["mounts"])[0]["source"] = "../.study-work/mount/other"
        elif mutation == "wrong-profile-argv":
            target["argv"] = ["--url", prerequisite.url]
        elif mutation == "unexpected-config-difference":
            run["master_seed"] = 74
        mutated = tomli_w.dumps(document).encode()
        short_path.write_bytes(mutated)
        hashes = dict(prerequisite.config_sha256)
        hashes["short"] = hashlib.sha256(mutated).hexdigest()
        prerequisite = replace(prerequisite, config_sha256=_frozen(hashes))

    with pytest.raises((ValueError, TrafficlabError)):
        study.validate_base_configs(repository_root, prerequisite)


def test_scratch_files_are_exclusive_regular_0666_and_archives_are_sibling_0600(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    workload = study.workload_specs("https://downloads.example.test/object.bin")[0]
    mount_directory = repository_root / "examples" / "validation_study" / ".study-work" / "mount" / "study-1"
    mount_directory.mkdir(parents=True)
    scratch = mount_directory / "short.headers"
    scratch.write_bytes(b"stale")
    run_directory = repository_root / "runs" / "validation_study" / "study-1" / "01-short-r1"
    run_directory.mkdir(parents=True)
    for name in study.ARTIFACT_NAMES:
        (run_directory / name).write_bytes(b"artifact")

    prepared = study.prepare_transfer_scratch(repository_root, "study-1", "01-short-r1", workload)

    assert tuple(prepared) == ("short.headers",)
    path, inode = prepared["short.headers"]
    assert path == scratch
    assert inode == path.lstat().st_ino
    assert stat.S_ISREG(path.lstat().st_mode)
    assert stat.S_IMODE(path.lstat().st_mode) == 0o666
    assert path.read_bytes() == b""
    header_bytes = _response_headers(0, 262143)
    path.write_bytes(header_bytes)

    responses = study.archive_transfer_evidence(
        repository_root,
        "study-1",
        "01-short-r1",
        workload,
        prepared,
        object_size_bytes=4_194_304,
    )

    archive = (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / "study-1"
        / "01-short-r1"
        / "short.headers"
    )
    assert responses == (
        {
            "transfer_index": 0,
            "requested_start": 0,
            "requested_end": 262143,
            "status": 206,
            "content_length": 262144,
            "content_range": "bytes 0-262143/4194304",
            "header_archive_path": "examples/validation_study/.study-work/evidence/study-1/01-short-r1/short.headers",
            "header_sha256": hashlib.sha256(header_bytes).hexdigest(),
            "scratch_precreate_mode": 438,
            "archive_mode": 384,
            "inode_preserved": True,
        },
    )
    assert archive.read_bytes() == header_bytes
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert not path.exists()
    assert not archive.is_relative_to(run_directory)
    assert set(item.name for item in run_directory.iterdir()) == set(study.ARTIFACT_NAMES)

    path.symlink_to(repository_root / "outside")
    with pytest.raises(ValueError, match="symlink|regular"):
        study.prepare_transfer_scratch(repository_root, "study-1", "02-short-r2", workload)
    assert path.is_symlink()


def test_range_header_parser_validates_redirect_chain_final_status_range_and_length(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    workload = study.workload_specs("https://downloads.example.test/object.bin")[1]
    prepared = study.prepare_transfer_scratch(repository_root, "study-1", "02-streaming-r1", workload)
    redirect = b"HTTP/1.1 302 Found\r\nLocation: /stable/object.bin\r\nContent-Length: 0\r\n\r\n"
    header_bytes = _response_headers(0, 4194303, prefix=redirect)
    prepared["streaming.headers"][0].write_bytes(header_bytes)

    responses = study.archive_transfer_evidence(
        repository_root,
        "study-1",
        "02-streaming-r1",
        workload,
        prepared,
        object_size_bytes=4_194_304,
    )

    assert responses[0]["status"] == 206
    assert responses[0]["content_range"] == "bytes 0-4194303/4194304"
    assert responses[0]["content_length"] == 4_194_304
    assert responses[0]["header_sha256"] == hashlib.sha256(header_bytes).hexdigest()


@pytest.mark.parametrize(
    "mutation",
    [
        "symlink",
        "replacement-inode",
        "empty-header",
        "duplicate-status",
        "duplicate-content-range",
        "wrong-total",
        "range-ignored-200",
        "wrong-content-length",
        "credential-redirect",
        "http-redirect",
        "archive-exists",
    ],
)
def test_transfer_evidence_rejects_unsafe_or_inexact_headers(mutation: str, tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    workload = study.workload_specs("https://downloads.example.test/object.bin")[0]
    mount_directory = repository_root / "examples" / "validation_study" / ".study-work" / "mount" / "study-1"
    scratch = mount_directory / "short.headers"
    if mutation == "symlink":
        mount_directory.mkdir(parents=True)
        scratch.symlink_to(repository_root / "outside")
        with pytest.raises(ValueError, match="symlink|regular"):
            study.prepare_transfer_scratch(repository_root, "study-1", "01-short-r1", workload)
        assert scratch.is_symlink()
        return

    prepared = study.prepare_transfer_scratch(repository_root, "study-1", "01-short-r1", workload)
    valid = _response_headers(0, 262143)
    invalid_headers = {
        "empty-header": b"",
        "duplicate-status": b"HTTP/1.1 206 Response\r\n" + valid,
        "duplicate-content-range": valid.replace(
            b"Content-Length:", b"Content-Range: bytes 0-262143/4194304\r\nContent-Length:"
        ),
        "wrong-total": _response_headers(0, 262143, total=4_194_305),
        "range-ignored-200": _response_headers(0, 262143, status=200),
        "wrong-content-length": _response_headers(0, 262143, length=262143),
        "credential-redirect": _response_headers(
            0,
            262143,
            prefix=b"HTTP/1.1 302 Found\r\nLocation: https://user@example.test/object\r\n\r\n",
        ),
        "http-redirect": _response_headers(
            0,
            262143,
            prefix=b"HTTP/1.1 302 Found\r\nLocation: http://example.test/object\r\n\r\n",
        ),
    }
    if mutation == "replacement-inode":
        replacement = mount_directory / "replacement.headers"
        replacement.write_bytes(valid)
        os.chmod(replacement, 0o666)
        os.replace(replacement, scratch)
    elif mutation == "archive-exists":
        scratch.write_bytes(valid)
        archive = (
            repository_root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "evidence"
            / "study-1"
            / "01-short-r1"
            / "short.headers"
        )
        archive.write_bytes(b"existing")
    else:
        scratch.write_bytes(invalid_headers[mutation])

    original = scratch.read_bytes()
    with pytest.raises(ValueError):
        study.archive_transfer_evidence(
            repository_root,
            "study-1",
            "01-short-r1",
            workload,
            prepared,
            object_size_bytes=4_194_304,
        )
    assert scratch.read_bytes() == original
    archive = (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / "study-1"
        / "01-short-r1"
        / "short.headers"
    )
    if mutation == "archive-exists":
        assert archive.read_bytes() == b"existing"
    else:
        assert archive.read_bytes() == original
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600


def test_median_quantile_and_descriptive_statistics_use_published_formulas() -> None:
    assert (
        study._sample_record(  # pyright: ignore[reportPrivateUsage]
            [1.0, 3.0, 5.0], quantile_probability=0.95, zero_count=0
        )["median"]
        == 3.0
    )
    assert (
        study._sample_record(  # pyright: ignore[reportPrivateUsage]
            [1.0, 3.0, 5.0, 9.0], quantile_probability=0.95, zero_count=0
        )["median"]
        == 4.0
    )
    assert (
        study._sample_record(  # pyright: ignore[reportPrivateUsage]
            [1.0, 3.0, 5.0, 9.0], quantile_probability=0.5, zero_count=0
        )["quantile"]
        == 3.0
    )
    assert study.descriptive_statistics([1, 2, 3]) == {
        "count": 3,
        "mean": 2.0,
        "minimum": 1.0,
        "maximum": 3.0,
        "range": 2.0,
        "sample_variance": 1.0,
        "sample_standard_deviation": 1.0,
    }

    for values in ([], [1, 2], [1, 2, 3, 4], [1, True, 3], [1, math.nan, 3]):
        with pytest.raises(ValueError):
            study.descriptive_statistics(values)
    with pytest.raises(ValueError, match="nonempty"):
        study._sample_record([], quantile_probability=0.95, zero_count=0)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="zero count"):
        study._sample_record(  # pyright: ignore[reportPrivateUsage]
            [1.0, 2.0], quantile_probability=0.95, zero_count=3
        )


def test_prerequisite_codec_round_trips_exact_canonical_schema(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    value = _valid_prerequisite()

    rendered = study.render_prerequisite_results(value)
    parsed = study.parse_prerequisite_results(rendered, repository_root=repository_root)

    assert study.render_prerequisite_results(parsed) == rendered
    assert rendered.endswith(b"\n")
    assert not rendered.endswith(b" \n")
    assert b": " not in rendered
    assert b", " not in rendered
    decoded = json.loads(rendered)
    assert not _contains_none(decoded)
    assert tuple(decoded) == tuple(sorted(decoded))
    assert tuple(decoded["commands"][0]) == tuple(sorted(decoded["commands"][0]))
    with pytest.raises(TypeError):
        cast(dict[str, object], parsed.capability)["status"] = 200

    destination = repository_root / "examples" / "validation_study" / "prerequisites.json"
    destination.parent.mkdir(parents=True)
    study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
        destination, value, repository_root=repository_root
    )
    assert destination.read_bytes() == rendered


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unknown-root", "exact keys"),
        ("duplicate-key", "duplicate JSON key"),
        ("wrong-command-order", "docker_matrix"),
        ("skipped-test", "skipped"),
        ("wrong-image", "target reference"),
        ("wrong-capability-mode", "canary file mode"),
        ("wrong-container-id", "lowercase container ID"),
        ("path-escape", "repository-relative"),
        ("nan", "invalid JSON constant"),
    ],
)
def test_prerequisite_codec_rejects_each_contract_violation(
    mutation: str,
    message: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    rendered = study.render_prerequisite_results(_valid_prerequisite())
    document = cast(dict[str, object], json.loads(rendered))

    if mutation == "unknown-root":
        document["unknown"] = "value"
        invalid = json.dumps(document, separators=(",", ":")).encode()
    elif mutation == "duplicate-key":
        invalid = rendered.replace(b'{"capability":', b'{"schema_version":1,"capability":', 1)
    elif mutation == "nan":
        invalid = rendered.replace(b'"schema_version":1', b'"schema_version":NaN', 1)
    else:
        mutated = copy.deepcopy(document)
        commands = cast(list[dict[str, object]], mutated["commands"])
        capability = cast(dict[str, object], mutated["capability"])
        images = cast(dict[str, object], mutated["images"])
        if mutation == "wrong-command-order":
            mutated["commands"] = list(reversed(commands))
        elif mutation == "skipped-test":
            tests = cast(dict[str, object], commands[0]["tests"])
            tests["passed"] = 1
            tests["skipped"] = 1
        elif mutation == "wrong-image":
            images["target_reference"] = "curlimages/curl:latest"
        elif mutation == "wrong-capability-mode":
            capability["canary_file_mode"] = 384
        elif mutation == "wrong-container-id":
            capability["container_id"] = "ABC123"
        elif mutation == "path-escape":
            capability["mount_source"] = "../escape"
        invalid = json.dumps(mutated, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match=message):
        study.parse_prerequisite_results(invalid, repository_root=repository_root)


def test_prerequisite_codec_rejects_changed_derived_capability_range(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    rendered = study.render_prerequisite_results(_valid_prerequisite())
    document = cast(dict[str, object], json.loads(rendered))
    capability = cast(dict[str, object], document["capability"])
    capability["content_range"] = "bytes 0-0/4194305"
    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match="content range"):
        study.parse_prerequisite_results(invalid, repository_root=repository_root)


def test_prerequisite_codec_accepts_a_valid_credential_free_https_final_redirect(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    rendered = study.render_prerequisite_results(_valid_prerequisite())
    document = cast(dict[str, object], json.loads(rendered))
    capability = cast(dict[str, object], document["capability"])
    capability["final_url"] = "https://cdn.example.test/object.bin"
    capability["redirect_count"] = 1
    redirected = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    parsed = study.parse_prerequisite_results(redirected, repository_root=repository_root)

    assert parsed.capability["final_url"] == "https://cdn.example.test/object.bin"
    assert parsed.capability["redirect_count"] == 1


@pytest.mark.parametrize("kind", ["prerequisites", "results"])
def test_official_publication_collision_preserves_winner_and_cleans_private_temp(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    destination = repository_root / "examples" / "validation_study" / f"{kind}.json"
    destination.parent.mkdir(parents=True)
    winner = b"concurrent publisher\n"
    linked_sources: list[Path] = []

    def collide(source: str | Path, target: str | Path, *_args: object, **_kwargs: object) -> None:
        temporary = Path(source)
        linked_sources.append(temporary)
        assert temporary.parent == destination.parent
        assert stat.S_IMODE(temporary.stat().st_mode) == 0o600
        Path(target).write_bytes(winner)
        raise FileExistsError("simulated publication race")

    monkeypatch.setattr(study.os, "link", collide)
    if kind == "prerequisites":
        prerequisite_value = _valid_prerequisite()

        def publish() -> None:
            study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
                destination,
                prerequisite_value,
                repository_root=repository_root,
            )

    else:
        result_value = _result_value(_valid_result_document(repository_root))

        def publish() -> None:
            study._publish_results(  # pyright: ignore[reportPrivateUsage]
                destination,
                result_value,
                repository_root=repository_root,
            )

    with pytest.raises(TrafficlabError, match="already exists"):
        publish()

    assert destination.read_bytes() == winner
    assert len(linked_sources) == 1
    assert not tuple(destination.parent.glob(f".{destination.name}.*"))


def test_support_publication_refuses_an_existing_target_before_creating_a_temp(tmp_path: Path) -> None:
    destination = tmp_path / "results.json"
    destination.write_bytes(b"winner\n")

    with pytest.raises(TrafficlabError, match="already exists"):
        study._publish_support_json(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"candidate\n",
            validate=lambda _content: None,
        )

    assert destination.read_bytes() == b"winner\n"
    assert not tuple(tmp_path.glob(".results.json.*"))


def test_support_publication_closes_and_cleans_a_temp_when_fdopen_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "results.json"

    def fail_fdopen(_descriptor: int, _mode: str) -> None:
        raise OSError("simulated fdopen failure")

    monkeypatch.setattr(study.os, "fdopen", fail_fdopen)
    with pytest.raises(OSError, match="fdopen"):
        study._publish_support_json(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"candidate\n",
            validate=lambda _content: None,
        )

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".results.json.*"))


def test_prerequisite_commands_are_exact_guarded_serial_argv_with_relative_projection(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    study_id = "study-1"
    url = "https://downloads.example.test/object.bin"
    evidence = f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites"
    docker = (
        "scripts/run_bounded.sh",
        "--memory-high",
        "2G",
        "--memory-max",
        "3G",
        "--swap-max",
        "512M",
        "--wall-time",
        "20m",
        "--kill-after",
        "10s",
        "--",
        "uv",
        "run",
        "--locked",
        "pytest",
        "-vv",
        "-n",
        "0",
        "-m",
        "docker",
        "--junitxml",
        f"{evidence}/docker.xml",
    )
    internet = (
        "scripts/run_bounded.sh",
        "--memory-high",
        "2G",
        "--memory-max",
        "3G",
        "--swap-max",
        "512M",
        "--wall-time",
        "10m",
        "--kill-after",
        "10s",
        "--",
        "uv",
        "run",
        "--locked",
        "pytest",
        "-vv",
        "-n",
        "0",
        "-m",
        "internet",
        "--internet-url",
        url,
        "--junitxml",
        f"{evidence}/internet.xml",
    )

    assert study._docker_matrix_argv(study_id) == docker  # pyright: ignore[reportPrivateUsage]
    assert study._internet_smoke_argv(study_id, url) == internet  # pyright: ignore[reportPrivateUsage]
    for kind, checked in (("docker_matrix", docker), ("internet_smoke", internet)):
        live: list[str] = list(checked)
        live[-1] = str(repository_root / checked[-1])
        assert study._live_argv(  # pyright: ignore[reportPrivateUsage]
            cast(study.PrerequisiteCommandKind, kind), checked, repository_root=repository_root
        ) == tuple(live)
        assert (
            study._project_command_argv(  # pyright: ignore[reportPrivateUsage]
                cast(study.PrerequisiteCommandKind, kind), live, repository_root=repository_root
            )
            == checked
        )
        tampered = list(checked)
        tampered[-2] = "--xml"
        with pytest.raises(ValueError, match="exact"):
            study._live_argv(  # pyright: ignore[reportPrivateUsage]
                cast(study.PrerequisiteCommandKind, kind), tampered, repository_root=repository_root
            )


@pytest.mark.parametrize(
    "invalid",
    [
        b'<testsuite tests="0" failures="0" errors="0" skipped="0"/>',
        b'<testsuite tests="2" failures="0" errors="0" skipped="1"/>',
        b'<testsuite tests="2" failures="1" errors="0" skipped="0"/>',
        b"not xml",
    ],
)
def test_junit_parser_requires_positive_all_passed_selection(invalid: bytes) -> None:
    assert study._parse_junit_counts(  # pyright: ignore[reportPrivateUsage]
        b'<testsuites tests="3" failures="0" errors="0" skipped="0">'
        b'<testsuite tests="3" failures="0" errors="0" skipped="0"/></testsuites>'
    ) == {"total": 3, "passed": 3, "failed": 0, "errors": 0, "skipped": 0}
    with pytest.raises(ValueError, match="JUnit|test"):
        study._parse_junit_counts(invalid)  # pyright: ignore[reportPrivateUsage]


def test_capability_records_digest_ids_default_user_range_canary_modes_and_cleanup(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    capture_root = repository_root / "docker" / "capture"
    capture_root.mkdir(parents=True)
    (capture_root / "Dockerfile").write_bytes(_CAPTURE_DOCKERFILE)
    (capture_root / "capture.sh").write_bytes(_CAPTURE_SCRIPT)
    runner = _ScriptedPrerequisiteRunner(repository_root)
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    result = study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: now,
    )

    assert result.git_commit == "c" * 40
    assert result.tools == {
        "python_version": "3.12.3",
        "trafficlab_version": "0.1.0",
        "docker_engine_version": "27.0.0",
        "docker_compose_version": "2.29.0",
        "platform": study.platform.platform(),
    }
    assert result.images == {
        "target_reference": study.TARGET_REFERENCE,
        "target_image_id": runner.target_id,
        "target_repo_digests": tuple(sorted(("curlimages/curl@sha256:" + "f" * 64, study.TARGET_REFERENCE))),
        "target_config_user": "curl_user",
        "capture_image_id": runner.capture_id,
        "capture_dockerfile_sha256": hashlib.sha256(_CAPTURE_DOCKERFILE).hexdigest(),
        "capture_script_sha256": hashlib.sha256(_CAPTURE_SCRIPT).hexdigest(),
    }
    capability = result.capability
    assert capability["status"] == 206
    assert capability["object_size_bytes"] == 4_194_304
    assert capability["redirect_count"] == 1
    assert capability["final_url"] == runner.final_url
    assert capability["container_id"] == runner.container_id
    assert capability["used_image_default_user"] is True
    assert capability["container_cleanup_verified"] is True
    assert capability["mount_directory_mode"] == 0o755
    assert capability["canary_file_mode"] == 0o666
    assert capability["canary_archive_mode"] == 0o600
    assert (
        capability["stdout_sha256"]
        == hashlib.sha256(f"status=206\nsize=1\nurl={runner.final_url}\nredirects=1\n".encode()).hexdigest()
    )
    assert capability["stderr_sha256"] == hashlib.sha256(b"curl diagnostic\n").hexdigest()
    assert stat.S_IMODE((runner.evidence / "capability.cid").stat().st_mode) == 0o600
    assert stat.S_IMODE((runner.evidence / "capability.headers").stat().st_mode) == 0o600
    assert stat.S_IMODE((runner.evidence / "capability.stdout").stat().st_mode) == 0o600
    assert stat.S_IMODE((runner.evidence / "capability.stderr").stat().st_mode) == 0o600
    assert not (runner.mount / ".capability.headers").exists()
    assert [command["tests"] for command in result.commands] == [
        _frozen({"total": 7, "passed": 7, "failed": 0, "errors": 0, "skipped": 0}),
        _frozen({"total": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0}),
    ]
    docker_live = study._live_argv(  # pyright: ignore[reportPrivateUsage]
        "docker_matrix",
        study._docker_matrix_argv(runner.study_id),  # pyright: ignore[reportPrivateUsage]
        repository_root=repository_root,
    )
    internet_live = study._live_argv(  # pyright: ignore[reportPrivateUsage]
        "internet_smoke",
        study._internet_smoke_argv(runner.study_id, runner.url),  # pyright: ignore[reportPrivateUsage]
        repository_root=repository_root,
    )
    assert [command for command, _timeout in runner.calls] == [
        ("git", "rev-parse", "HEAD"),
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        ("docker", "version", "--format", "{{.Server.Version}}"),
        ("docker", "compose", "version", "--short"),
        ("docker", "image", "pull", study.TARGET_REFERENCE),
        ("docker", "image", "inspect", study.TARGET_REFERENCE),
        (
            "docker",
            "build",
            "--pull=false",
            "--iidfile",
            str(runner.evidence / "capture.iid"),
            "docker/capture",
        ),
        (
            "docker",
            "container",
            "ls",
            "-a",
            "--filter",
            f"name=^/{runner.capability_name}$",
            "--format",
            "{{.ID}}",
        ),
        runner.expected_capability(),
        (
            "docker",
            "container",
            "ls",
            "-a",
            "--filter",
            f"id={runner.container_id}",
            "--format",
            "{{.ID}}",
        ),
        (
            "docker",
            "container",
            "ls",
            "-a",
            "--filter",
            f"name=^/{runner.capability_name}$",
            "--format",
            "{{.ID}}",
        ),
        docker_live,
        internet_live,
    ]
    assert [timeout for _command, timeout in runner.calls] == [
        20.0,
        20.0,
        20.0,
        20.0,
        300.0,
        300.0,
        300.0,
        20.0,
        45.0,
        20.0,
        20.0,
        1230.0,
        630.0,
    ]
    for command, prefix, stdout, stderr in (
        (result.commands[0], "docker", b"docker pass\n", b""),
        (result.commands[1], "internet", b"internet pass\n", b""),
    ):
        junit = (runner.evidence / f"{prefix}.xml").read_bytes()
        assert command["stdout_sha256"] == hashlib.sha256(stdout).hexdigest()
        assert command["stderr_sha256"] == hashlib.sha256(stderr).hexdigest()
        assert command["junit_sha256"] == hashlib.sha256(junit).hexdigest()
        for suffix in ("stdout", "stderr", "xml"):
            assert stat.S_IMODE((runner.evidence / f"{prefix}.{suffix}").stat().st_mode) == 0o600
    prerequisite_path = repository_root / "examples" / "validation_study" / "prerequisites.json"
    assert study.parse_prerequisite_results(prerequisite_path.read_bytes(), repository_root=repository_root) == result
    for name, content_hash in result.config_sha256.items():
        config_path = repository_root / "examples" / "validation_study" / "configs" / f"{name}.toml"
        assert hashlib.sha256(config_path.read_bytes()).hexdigest() == content_hash
        assert study.load_experiment(config_path).capture.image == runner.capture_id


@pytest.mark.parametrize(
    "mutation",
    [
        "dirty-tree",
        "wrong-python",
        "target-digest-absent",
        "capture-iid-tag",
        "preexisting-name",
        "preexisting-cid",
        "capability-daemon-error",
        "capability-lingering-unowned",
        "capability-timeout-owned",
        "capability-timeout-unowned",
        "canary-not-written",
        "canary-replaced",
        "wrong-write-out",
        "range-ignored",
        "oversize-object",
        "docker-matrix-failed",
        "internet-skipped",
        "config-publication-failed",
    ],
)
def test_prerequisites_stop_at_first_failure_preserve_primary_and_publish_no_valid_json(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    capture_root = repository_root / "docker" / "capture"
    capture_root.mkdir(parents=True)
    (capture_root / "Dockerfile").write_bytes(_CAPTURE_DOCKERFILE)
    (capture_root / "capture.sh").write_bytes(_CAPTURE_SCRIPT)
    runner = _ScriptedPrerequisiteRunner(repository_root, mutation)
    _install_prerequisite_failure(mutation, monkeypatch)

    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    with pytest.raises(TrafficlabError, match="prerequisite validation failed") as captured:
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: now,
        )

    assert "restart with a new study ID" in captured.value.corrective_action
    prerequisite_path = repository_root / "examples" / "validation_study" / "prerequisites.json"
    assert not prerequisite_path.exists()
    config_directory = repository_root / "examples" / "validation_study" / "configs"
    if mutation != "config-publication-failed":
        assert not config_directory.exists() or not tuple(config_directory.glob("*.toml"))
    commands = [command for command, _timeout in runner.calls]
    docker_guard = study._live_argv(  # pyright: ignore[reportPrivateUsage]
        "docker_matrix",
        study._docker_matrix_argv(runner.study_id),  # pyright: ignore[reportPrivateUsage]
        repository_root=repository_root,
    )
    internet_guard = study._live_argv(  # pyright: ignore[reportPrivateUsage]
        "internet_smoke",
        study._internet_smoke_argv(runner.study_id, runner.url),  # pyright: ignore[reportPrivateUsage]
        repository_root=repository_root,
    )
    forbidden_prefixes: list[tuple[str, ...]] = list(
        {
            "dirty-tree": (("docker", "version"),),
            "wrong-python": (("docker", "version"),),
            "target-digest-absent": (("docker", "build"),),
            "capture-iid-tag": (("docker", "container", "inspect"),),
            "preexisting-name": (("docker", "run", "--rm"),),
            "preexisting-cid": (("docker", "run", "--rm"),),
        }.get(mutation, ())
    )
    if mutation not in {"docker-matrix-failed", "internet-skipped", "config-publication-failed"}:
        forbidden_prefixes.append(docker_guard)
    if mutation not in {"internet-skipped", "config-publication-failed"}:
        forbidden_prefixes.append(internet_guard)
    for prefix in forbidden_prefixes:
        assert not any(command[: len(prefix)] == prefix for command in commands)
    if mutation == "capability-timeout-owned":
        assert ("docker", "container", "rm", "--force", runner.container_id) in commands
        assert runner.container_running is False
    if mutation in {"capability-timeout-unowned", "capability-lingering-unowned"}:
        assert not any(command[:3] == ("docker", "container", "rm") for command in commands)
        assert runner.container_running is True
        assert runner.container_id in str(captured.value)
    if mutation == "capability-daemon-error":
        assert "daemon" in str(captured.value).lower()
    if mutation.startswith("capability-timeout"):
        assert "timed out" in str(captured.value).lower()
    evidence = runner.evidence
    if mutation in {
        "canary-not-written",
        "canary-replaced",
        "wrong-write-out",
        "range-ignored",
        "oversize-object",
        "docker-matrix-failed",
        "internet-skipped",
        "config-publication-failed",
    }:
        assert evidence.is_dir()
    if mutation in {
        "capability-timeout-owned",
        "capability-timeout-unowned",
        "canary-not-written",
        "canary-replaced",
        "wrong-write-out",
        "range-ignored",
        "oversize-object",
    }:
        canary = runner.mount / ".capability.headers"
        archive = evidence / "capability.headers"
        assert archive.read_bytes() == canary.read_bytes()
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600


def test_capability_normal_exit_proves_exact_full_id_and_anchored_name_absent(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    capture_root = repository_root / "docker" / "capture"
    capture_root.mkdir(parents=True)
    (capture_root / "Dockerfile").write_bytes(_CAPTURE_DOCKERFILE)
    (capture_root / "capture.sh").write_bytes(_CAPTURE_SCRIPT)
    runner = _ScriptedPrerequisiteRunner(repository_root)

    result = study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )

    commands = [command for command, _timeout in runner.calls]
    id_listing = (
        "docker",
        "container",
        "ls",
        "-a",
        "--filter",
        f"id={runner.container_id}",
        "--format",
        "{{.ID}}",
    )
    name_listing = (
        "docker",
        "container",
        "ls",
        "-a",
        "--filter",
        f"name=^/{runner.capability_name}$",
        "--format",
        "{{.ID}}",
    )
    assert result.capability["container_cleanup_verified"] is True
    assert commands.count(id_listing) == 1
    assert commands.count(name_listing) == 2


def test_capability_removes_only_a_lingering_exact_owned_id_after_success(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    capture_root = repository_root / "docker" / "capture"
    capture_root.mkdir(parents=True)
    (capture_root / "Dockerfile").write_bytes(_CAPTURE_DOCKERFILE)
    (capture_root / "capture.sh").write_bytes(_CAPTURE_SCRIPT)
    runner = _ScriptedPrerequisiteRunner(repository_root, "capability-lingering-owned")

    result = study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )

    commands = [command for command, _timeout in runner.calls]
    assert result.capability["container_cleanup_verified"] is True
    assert ("docker", "container", "rm", "--force", runner.container_id) in commands
    assert (
        commands.count(
            (
                "docker",
                "container",
                "ls",
                "-a",
                "--filter",
                f"id={runner.container_id}",
                "--format",
                "{{.ID}}",
            )
        )
        == 2
    )
    assert (
        commands.count(
            (
                "docker",
                "container",
                "ls",
                "-a",
                "--filter",
                f"name=^/{runner.capability_name}$",
                "--format",
                "{{.ID}}",
            )
        )
        == 2
    )
    assert runner.container_running is False


@pytest.mark.parametrize(
    "mutation",
    [
        "capability-post-id-daemon-error",
        "capability-post-name-daemon-error",
        "capability-name-reclaimed",
        "capability-lingering-owned-name-reclaimed",
    ],
)
def test_capability_cleanup_fails_closed_for_each_listing_and_an_unrelated_name_reclaimer(
    mutation: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    capture_root = repository_root / "docker" / "capture"
    capture_root.mkdir(parents=True)
    (capture_root / "Dockerfile").write_bytes(_CAPTURE_DOCKERFILE)
    (capture_root / "capture.sh").write_bytes(_CAPTURE_SCRIPT)
    runner = _ScriptedPrerequisiteRunner(repository_root, mutation)

    with pytest.raises(TrafficlabError, match="prerequisite validation failed") as captured:
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    commands = [command for command, _timeout in runner.calls]
    unrelated_id = "f" * 64
    assert not any(command == ("docker", "container", "rm", "--force", unrelated_id) for command in commands)
    if mutation == "capability-lingering-owned-name-reclaimed":
        assert ("docker", "container", "rm", "--force", runner.container_id) in commands
    else:
        assert not any(command[:3] == ("docker", "container", "rm") for command in commands)
    if "daemon-error" in mutation:
        assert "daemon unavailable" in str(captured.value)
    else:
        assert "still exists" in str(captured.value)
    assert (runner.evidence / "capability.stdout").is_file()
    assert (runner.evidence / "capability.stderr").is_file()
    assert (runner.evidence / "capability.headers").is_file()
    assert not (repository_root / "examples" / "validation_study" / "prerequisites.json").exists()


def test_capability_absence_helpers_reject_invalid_daemon_evidence_and_report_absence(tmp_path: Path) -> None:
    container_id = "e" * 64

    def invalid_utf8(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, check, capture_output, shell, timeout
        return subprocess.CompletedProcess(tuple(argv), 0, stdout=b"\xff", stderr=b"")

    with pytest.raises(ValueError, match="UTF-8"):
        study._container_listing(  # pyright: ignore[reportPrivateUsage]
            tmp_path,
            f"id={container_id}",
            runner=invalid_utf8,
        )

    def invalid_inspect(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, check, capture_output, shell, timeout
        command = tuple(argv)
        stdout = f"{container_id}\n".encode() if command[:4] == ("docker", "container", "ls", "-a") else b"not JSON"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    with pytest.raises(ValueError, match="must return JSON"):
        study._remove_owned_capability_if_present(  # pyright: ignore[reportPrivateUsage]
            repository_root=tmp_path,
            study_id="study-1",
            capability_name="trafficlab-validation-study-capability-study-1",
            container_id=container_id,
            runner=invalid_inspect,
        )

    cid = tmp_path / "capability.cid"
    cid.write_text(f"{container_id}\n", encoding="ascii")
    cid.chmod(0o600)

    def absent(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, check, capture_output, shell, timeout
        return subprocess.CompletedProcess(tuple(argv), 0, stdout=b"", stderr=b"")

    diagnostic = study._cleanup_failed_capability(  # pyright: ignore[reportPrivateUsage]
        repository_root=tmp_path,
        study_id="study-1",
        capability_name="trafficlab-validation-study-capability-study-1",
        capability_cid=cid,
        runner=absent,
    )
    assert diagnostic == f"capability container {container_id} is absent"

    cid.unlink()
    unreadable = study._cleanup_failed_capability(  # pyright: ignore[reportPrivateUsage]
        repository_root=tmp_path,
        study_id="study-1",
        capability_name="trafficlab-validation-study-capability-study-1",
        capability_cid=cid,
        runner=absent,
    )
    assert "could not read the exclusive CID" in unreadable


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("capability-start-error", "could not start"),
        ("capability-nonzero", "failed with status 7"),
        ("capability-missing-cid", "could not read capability CID"),
    ],
)
def test_capability_failure_boundaries_retain_exact_context(
    mutation: str,
    message: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    runner = _ScriptedPrerequisiteRunner(repository_root, mutation)
    runner.evidence.mkdir(parents=True)
    runner.mount.mkdir(parents=True)

    with pytest.raises(ValueError, match=message):
        study._prepare_capability(  # pyright: ignore[reportPrivateUsage]
            repository_root=repository_root,
            study_id=runner.study_id,
            url=runner.url,
            evidence_directory=runner.evidence,
            mount_directory=runner.mount,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )


def test_prerequisite_cli_requires_exact_subcommand_arguments_and_reports_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reject_calls: list[tuple[str, ...]] = []

    def reject_runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, check, capture_output, shell, timeout
        reject_calls.append(tuple(argv))
        raise AssertionError("invalid CLI input must not run a command")

    assert study.main([], repository_root=tmp_path, runner=reject_runner) == 2
    assert "usage:" in capsys.readouterr().err
    invalid_arguments = (
        ["prerequisites"],
        ["prerequisites", "--url", "https://downloads.example.test/object.bin"],
        ["prerequisites", "--study-id", "study-1"],
        ["prerequisites", "--url", "http://example.test/object", "--study-id", "study-1"],
        ["prerequisites", "--url", "https://downloads.example.test/object.bin", "--study-id", "INVALID"],
        [
            "prerequisites",
            "--url",
            "https://downloads.example.test/object.bin",
            "--study-id",
            "study-1",
            "extra",
        ],
    )
    for arguments in invalid_arguments:
        assert study.main(arguments, repository_root=tmp_path, runner=reject_runner) == 2
        assert capsys.readouterr().err
    assert reject_calls == []

    repository_root = tmp_path / "repository"
    capture_root = repository_root / "docker" / "capture"
    capture_root.mkdir(parents=True)
    (capture_root / "Dockerfile").write_bytes(_CAPTURE_DOCKERFILE)
    (capture_root / "capture.sh").write_bytes(_CAPTURE_SCRIPT)
    runner = _ScriptedPrerequisiteRunner(repository_root, "dirty-tree")
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    assert (
        study.main(
            ["prerequisites", "--url", runner.url, "--study-id", runner.study_id],
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: now,
        )
        == 2
    )
    error = capsys.readouterr().err.strip()
    assert error.startswith("validation-study: Validation Study prerequisite validation failed:")
    assert "; preserve the ignored evidence" in error


def test_result_codec_rejects_nonoracle_workload_argv(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = _valid_result_document(repository_root)
    protocol = cast(dict[str, object], document["protocol"])
    workload = cast(list[dict[str, object]], protocol["workloads"])[0]
    workload["argv"] = ["--url", "https://downloads.example.test/object.bin"]
    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match="workload definition"):
        study.parse_study_results(invalid, repository_root=repository_root)


def test_result_codec_rejects_a_nonbest_family_champion_as_winner(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = _valid_result_document(repository_root)
    run = cast(list[dict[str, object]], document["runs"])[0]
    champion = cast(list[dict[str, object]], run["family_champions"])[0]
    run["winner"] = {
        key: copy.deepcopy(champion[key]) for key in ("family", "candidate_id", "genes", "selection_fitness")
    }
    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match="overall best"):
        study.parse_study_results(invalid, repository_root=repository_root)


def test_result_codec_round_trips_nine_runs_reproduction_and_recomputed_summaries(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = _valid_result_document(repository_root)
    value = _result_value(document)

    rendered = study.render_study_results(value)
    parsed = study.parse_study_results(rendered, repository_root=repository_root)

    assert study.render_study_results(parsed) == rendered
    assert len(parsed.runs) == 9
    assert tuple((run.execution_order, run.run_id) for run in parsed.runs) == tuple(
        (order, run_id) for order, run_id, _workload, _repeat in study.PRIMARY_ORDER
    )
    assert len(parsed.reproduction.document) == 27
    assert rendered.endswith(b"\n")
    assert b": " not in rendered
    assert not _contains_none(json.loads(rendered))
    destination = repository_root / "examples" / "validation_study" / "results.json"
    destination.parent.mkdir(parents=True)
    study._publish_results(destination, value, repository_root=repository_root)  # pyright: ignore[reportPrivateUsage]
    assert destination.read_bytes() == rendered


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-primary-order",
        "duplicate-run-key",
        "missing-family",
        "wrong-method-order",
        "nullable-value",
        "stale-statistic",
        "wrong-pair-average",
        "winner-count-mismatch",
        "wrong-reproduction-source",
        "extra-artifact-hash",
        "true-reuse",
        "wrong-guard",
    ],
)
def test_result_codec_rejects_nested_schema_and_cross_record_inconsistency(
    mutation: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = _valid_result_document(repository_root)
    runs = cast(list[dict[str, object]], document["runs"])
    protocol = cast(dict[str, object], document["protocol"])
    summaries = cast(list[dict[str, object]], document["workload_summaries"])
    natural = cast(list[dict[str, object]], document["natural_variation"])
    reproduction = cast(dict[str, object], document["reproduction"])

    if mutation == "wrong-primary-order":
        runs[0], runs[1] = runs[1], runs[0]
    elif mutation == "duplicate-run-key":
        runs[1]["key"] = copy.deepcopy(runs[0]["key"])
    elif mutation == "missing-family":
        cast(list[object], runs[0]["family_champions"]).pop()
    elif mutation == "wrong-method-order":
        protocol["methods"] = list(reversed(cast(list[object], protocol["methods"])))
    elif mutation == "nullable-value":
        runs[0]["elapsed_seconds"] = None
    elif mutation == "stale-statistic":
        cast(dict[str, object], summaries[0]["runtime"])["mean"] = 99.0
    elif mutation == "wrong-pair-average":
        first_pair = cast(list[dict[str, object]], natural[0]["pairs"])[0]
        cast(dict[str, object], first_pair["symmetric"])["aggregate"] = 0.0
    elif mutation == "winner-count-mismatch":
        cast(dict[str, object], summaries[0]["winner_counts"])["mmpp"] = 2
    elif mutation == "wrong-reproduction-source":
        reproduction["source_key"] = {"workload": "short", "repeat": 2}
    elif mutation == "extra-artifact-hash":
        cast(dict[str, object], runs[0]["artifact_sha256"])["extra"] = _HASH
    elif mutation == "true-reuse":
        cast(dict[str, object], runs[0]["reuse"])["capture"] = True
    elif mutation == "wrong-guard":
        cast(list[str], reproduction["guard_command"]).pop()

    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(ValueError):
        study.parse_study_results(invalid, repository_root=repository_root)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("integer-gene", "exact.*float"),
        ("escaping-path", "repository-relative"),
        ("score-over-one", r"\[0.0, 1.0\]"),
        ("wrong-trace-count", "packet totals"),
        ("wrong-artifact-set", "exact keys"),
        ("raw-window-lineage", "observation windows"),
        ("raw-count-lineage", "event counts"),
    ],
)
def test_result_codec_rejects_scalar_path_gene_trace_and_artifact_violations(
    mutation: str,
    message: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = _valid_result_document(repository_root)
    run = cast(list[dict[str, object]], document["runs"])[0]
    if mutation == "integer-gene":
        champion = cast(list[dict[str, object]], run["family_champions"])[2]
        champion["genes"] = [1]
    elif mutation == "escaping-path":
        run["config_path"] = "../escape.toml"
    elif mutation == "score-over-one":
        fresh_simulation = cast(dict[str, object], run["fresh_simulation"])
        cast(dict[str, object], fresh_simulation["score"])["aggregate"] = 1.1
    elif mutation == "wrong-trace-count":
        reference = cast(dict[str, object], run["reference"])
        cast(dict[str, object], reference["packet_totals"])["outbound"] = 99
    elif mutation == "wrong-artifact-set":
        cast(dict[str, object], run["artifact_sha256"]).pop("run.log")
    elif mutation == "raw-window-lineage":
        generated = cast(dict[str, object], run["generated"])
        generated["observation_window_seconds"] = 99.0
    elif mutation == "raw-count-lineage":
        raw_sequence = cast(dict[str, object], run["raw_sequence"])
        raw_sequence["trial_event_count"] = 99
        raw_sequence["final_event_count"] = 99
    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match=message):
        study.parse_study_results(invalid, repository_root=repository_root)


class _StudyIdentityRunner:
    def __init__(
        self,
        repository_root: Path,
        *,
        target_image_id: str = _IMAGE_ID,
        capture_image_id: str = f"sha256:{'d' * 64}",
    ) -> None:
        self.root = repository_root
        self.target_image_id = target_image_id
        self.capture_image_id = capture_image_id
        self.calls: list[tuple[str, ...]] = []

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
        del timeout
        command = tuple(argv)
        assert cwd == self.root
        assert check is False
        assert capture_output is True
        assert shell is False
        self.calls.append(command)
        outputs: dict[tuple[str, ...], bytes] = {
            ("git", "rev-parse", "HEAD"): b"c" * 40 + b"\n",
            ("git", "status", "--porcelain=v1", "--untracked-files=all"): b"",
            ("docker", "version", "--format", "{{.Server.Version}}"): b"27.0.0\n",
            ("docker", "compose", "version", "--short"): b"2.29.0\n",
            ("docker", "image", "inspect", study.TARGET_REFERENCE): json.dumps(
                [
                    {
                        "Id": self.target_image_id,
                        "RepoDigests": [study.TARGET_REFERENCE],
                        "Config": {"User": ""},
                    }
                ]
            ).encode(),
            ("docker", "image", "inspect", f"sha256:{'d' * 64}"): json.dumps([{"Id": self.capture_image_id}]).encode(),
        }
        if command not in outputs:
            raise AssertionError(f"unexpected study command: {command!r}")
        return subprocess.CompletedProcess(command, 0, stdout=outputs[command], stderr=b"")


def _write_study_inputs(repository_root: Path) -> tuple[Path, study.StudyResults]:
    repository_root.mkdir()
    prerequisite, _contents = _write_checked_configs(repository_root)
    prerequisite = _write_retained_prerequisite_evidence(repository_root, prerequisite)
    prerequisite_path = repository_root / "examples" / "validation_study" / "prerequisites.json"
    prerequisite_path.write_bytes(study.render_prerequisite_results(prerequisite))
    document = _valid_result_document(repository_root)
    return prerequisite_path, _result_value(document)


def _install_primary_orchestration_doubles(
    monkeypatch: pytest.MonkeyPatch,
    expected: study.StudyResults,
    events: list[str],
) -> None:
    records = iter(expected.runs)

    def prepare(
        _root: Path,
        _study_id: str,
        run_id: str,
        _workload: study.WorkloadSpec,
    ) -> dict[str, tuple[Path, int]]:
        events.append(f"scratch:{run_id}")
        return {}

    def archive(
        _root: Path,
        _study_id: str,
        run_id: str,
        workload: study.WorkloadSpec,
        _prepared: object,
        *,
        object_size_bytes: int,
    ) -> tuple[study.JsonObject, ...]:
        assert object_size_bytes == 4_194_304
        events.append(f"archive:{run_id}")
        return tuple(cast(study.JsonObject, value) for value in _transfer_responses("study-1", run_id, workload.name))

    def extract(
        _root: Path,
        spec: study.StudyRunSpec,
        _workload: study.WorkloadSpec,
        _result: object,
        elapsed: float,
        _responses: tuple[study.JsonObject, ...],
    ) -> study.StudyRunRecord:
        events.append(f"extract:{spec.run_id}:{elapsed}")
        return next(records)

    def load_reference(run_directory: Path) -> tuple[TraceEvent, ...]:
        events.append(f"trace:{run_directory.name}")
        return (TraceEvent(0.0, Direction.OUTBOUND, 60), TraceEvent(1.0, Direction.INBOUND, 80))

    def variation(
        _records: Sequence[study.StudyRunRecord],
        _traces: object,
        _settings: object,
    ) -> tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject]:
        events.append("variation")
        return expected.natural_variation

    def summaries(
        _records: Sequence[study.StudyRunRecord],
    ) -> tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject]:
        events.append("summaries")
        return expected.workload_summaries

    def reproduction(*_args: object, **_kwargs: object) -> study.ReproductionRecord:
        events.append("reproduction")
        return expected.reproduction

    def publish(*_args: object, **_kwargs: object) -> None:
        events.append("publish")

    monkeypatch.setattr(study, "prepare_transfer_scratch", prepare)
    monkeypatch.setattr(study, "archive_transfer_evidence", archive)
    monkeypatch.setattr(study, "extract_primary_record", extract)
    monkeypatch.setattr(study, "_load_reference_trace", load_reference, raising=False)
    monkeypatch.setattr(study, "natural_variation", variation)
    monkeypatch.setattr(study, "workload_summaries", summaries)
    monkeypatch.setattr(study, "_run_cli_reproduction", reproduction, raising=False)
    monkeypatch.setattr(study, "_publish_results", publish)
    monkeypatch.setattr(study.platform, "python_version", lambda: "3.12.3")
    monkeypatch.setattr(study.platform, "platform", lambda: "Linux-test")


def test_study_runs_nine_absent_primaries_serially_in_balanced_order_and_times_only_run_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, expected = _write_study_inputs(repository_root)
    events: list[str] = []
    _install_primary_orchestration_doubles(monkeypatch, expected, events)
    timer_values = iter(float(value) for value in range(20))

    def timer() -> float:
        value = next(timer_values)
        events.append(f"time:{value}")
        return value

    def run(path: Path) -> RunResult:
        events.append(f"run:{path.stem}")
        return cast(RunResult, object())

    runner = _StudyIdentityRunner(repository_root)
    result = study.run_study(
        "https://downloads.example.test/object.bin",
        "study-1",
        prerequisite_path,
        repository_root=repository_root,
        run=run,
        runner=runner,
        perf_counter=timer,
        utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
    )

    assert tuple((record.execution_order, record.run_id) for record in result.runs) == tuple(
        (order, run_id) for order, run_id, _workload, _repeat in study.PRIMARY_ORDER
    )
    for index, (_order, run_id, _workload, _repeat) in enumerate(study.PRIMARY_ORDER):
        segment = events[index * 7 : index * 7 + 7]
        assert segment == [
            f"scratch:{run_id}",
            f"time:{float(index * 2)}",
            f"run:{run_id}",
            f"time:{float(index * 2 + 1)}",
            f"archive:{run_id}",
            f"extract:{run_id}:1.0",
            f"trace:{run_id}",
        ]
    assert events[63:] == ["variation", "summaries", "reproduction", "publish"]
    assert runner.calls == [
        ("git", "rev-parse", "HEAD"),
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        ("docker", "version", "--format", "{{.Server.Version}}"),
        ("docker", "compose", "version", "--short"),
        ("docker", "image", "inspect", study.TARGET_REFERENCE),
        ("docker", "image", "inspect", f"sha256:{'d' * 64}"),
    ]
    for order, run_id, workload, repeat in study.PRIMARY_ORDER:
        record = result.runs[order - 1]
        assert record.key == {"workload": workload, "repeat": repeat}
        assert record.config_path == f"runs/validation_study/study-1/realized-configs/{run_id}.toml"
        assert record.run_directory == f"runs/validation_study/study-1/{run_id}"
        assert record.transfer_evidence_directory.endswith(f"/study-1/{run_id}")


@pytest.mark.parametrize("failed_position", [1, 5, 9])
def test_primary_failure_stops_preserves_evidence_and_publishes_no_results(
    failed_position: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, expected = _write_study_inputs(repository_root)
    events: list[str] = []
    _install_primary_orchestration_doubles(monkeypatch, expected, events)
    failure = TrafficlabError("simulated primary failure", corrective_action="inspect the failed run")

    def failed_archive(_directory: Path, _prepared: object) -> str:
        return "short.headers: disk full"

    monkeypatch.setattr(study, "_best_effort_archive", failed_archive)

    def run(path: Path) -> RunResult:
        position = len([event for event in events if event.startswith("run:")]) + 1
        events.append(f"run:{path.stem}")
        if position == failed_position:
            raise failure
        return cast(RunResult, object())

    with pytest.raises(TrafficlabError, match=rf"position {failed_position}.*restart with a new study ID") as captured:
        study.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=run,
            runner=_StudyIdentityRunner(repository_root),
            perf_counter=iter(float(value) for value in range(30)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    _order, run_id, workload, repeat = study.PRIMARY_ORDER[failed_position - 1]
    assert workload in str(captured.value)
    assert f"repeat {repeat}" in str(captured.value)
    assert f"runs/validation_study/study-1/{run_id}" in str(captured.value)
    assert "secondary evidence archive failure: short.headers: disk full" in str(captured.value)
    assert captured.value.__cause__ is failure
    assert not (repository_root / "examples" / "validation_study" / "results.json").exists()
    assert "reproduction" not in events
    assert len([event for event in events if event.startswith("run:")]) == failed_position


def test_primary_archive_failure_preserves_the_archive_cause_and_secondary_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, expected = _write_study_inputs(repository_root)
    events: list[str] = []
    _install_primary_orchestration_doubles(monkeypatch, expected, events)
    failure = OSError("simulated archive failure")

    def fail_archive(*_args: object, **_kwargs: object) -> tuple[study.JsonObject, ...]:
        raise failure

    def failed_best_effort(_directory: Path, _prepared: object) -> str:
        return "short.headers: disk full"

    monkeypatch.setattr(study, "archive_transfer_evidence", fail_archive)
    monkeypatch.setattr(study, "_best_effort_archive", failed_best_effort)

    with pytest.raises(TrafficlabError, match=r"short, repeat 1, position 1.*secondary evidence") as captured:
        study.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=lambda _path: cast(RunResult, object()),
            runner=_StudyIdentityRunner(repository_root),
            perf_counter=iter((1.0, 2.0)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    assert "runs/validation_study/study-1/01-short-r1" in str(captured.value)
    assert captured.value.__cause__ is failure
    assert not (repository_root / "examples" / "validation_study" / "results.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-url",
        "wrong-live-image",
        "wrong-live-capture-image",
        "existing-run",
        "existing-evidence",
        "reused-record",
    ],
)
def test_study_rejects_incompatible_prerequisites_existing_targets_and_any_reuse(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, expected = _write_study_inputs(repository_root)
    events: list[str] = []
    _install_primary_orchestration_doubles(monkeypatch, expected, events)
    if mutation == "existing-run":
        (repository_root / "runs" / "validation_study" / "study-1" / "01-short-r1").mkdir(parents=True)
    elif mutation == "existing-evidence":
        (
            repository_root / "examples" / "validation_study" / ".study-work" / "evidence" / "study-1" / "01-short-r1"
        ).mkdir(parents=True)
    elif mutation == "reused-record":
        original_extract = study.extract_primary_record

        def reused(
            root: Path,
            spec: study.StudyRunSpec,
            workload: study.WorkloadSpec,
            result: RunResult,
            elapsed: float,
            responses: tuple[study.JsonObject, ...],
        ) -> study.StudyRunRecord:
            record = original_extract(root, spec, workload, result, elapsed, responses)
            return replace(
                record, reuse=_frozen({"capture": True, "best_model": False, "generated": False, "similarity": False})
            )

        monkeypatch.setattr(study, "extract_primary_record", reused)

    url = "https://other.example.test/object.bin" if mutation == "wrong-url" else expected.protocol["url"]
    runner = _StudyIdentityRunner(
        repository_root,
        target_image_id=f"sha256:{'9' * 64}" if mutation == "wrong-live-image" else _IMAGE_ID,
        capture_image_id=(f"sha256:{'8' * 64}" if mutation == "wrong-live-capture-image" else f"sha256:{'d' * 64}"),
    )
    with pytest.raises(TrafficlabError):
        study.run_study(
            cast(str, url),
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=lambda _path: cast(RunResult, object()),
            runner=runner,
            perf_counter=iter(float(value) for value in range(30)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )
    assert "reproduction" not in events
    assert not (repository_root / "examples" / "validation_study" / "results.json").exists()


@pytest.mark.parametrize("invalid_derived", ["variation", "summary"])
def test_study_validates_variation_and_summaries_before_any_reproduction_runner_call(
    invalid_derived: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, expected = _write_study_inputs(repository_root)
    events: list[str] = []
    _install_primary_orchestration_doubles(monkeypatch, expected, events)
    if invalid_derived == "variation":

        def invalid_variation(*_args: object, **_kwargs: object) -> tuple[study.JsonObject, ...]:
            raise TrafficlabError("metric precondition failed", corrective_action="preserve evidence")

        monkeypatch.setattr(
            study,
            "natural_variation",
            invalid_variation,
        )
    else:
        invalid = [
            cast(study.JsonObject, study._thaw_json(value))  # pyright: ignore[reportPrivateUsage]
            for value in expected.workload_summaries
        ]
        cast(dict[str, object], invalid[0]["runtime"])["count"] = 2

        def invalid_summaries(_records: Sequence[study.StudyRunRecord]) -> tuple[study.JsonObject, ...]:
            return tuple(invalid)

        monkeypatch.setattr(study, "workload_summaries", invalid_summaries)

    with pytest.raises((TrafficlabError, ValueError)):
        study.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=lambda _path: cast(RunResult, object()),
            runner=_StudyIdentityRunner(repository_root),
            perf_counter=iter(float(value) for value in range(30)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )
    assert "reproduction" not in events
    assert not (
        repository_root / "runs" / "validation_study" / "study-1" / "realized-configs" / "reproduction.toml"
    ).exists()
    assert not (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / "study-1"
        / "10-streaming-r2-reproduction"
    ).exists()
    assert not (repository_root / "examples" / "validation_study" / "results.json").exists()


def _source_record_and_config(
    repository_root: Path,
) -> tuple[study.StudyRunRecord, study.ExperimentConfig, study.WorkloadSpec]:
    document = _valid_result_document(repository_root)
    source = _result_value(document).runs[3]
    workload = {item.name: item for item in study.workload_specs(_valid_prerequisite().url)}["streaming"]
    base = study.build_base_config(
        workload,
        repository_root=repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        capture_image_id=f"sha256:{'d' * 64}",
    )
    source_directory = repository_root / source.run_directory
    source_config = base.model_copy(
        update={"run": base.run.model_copy(update={"directory": source_directory.resolve()})}
    )
    source_directory.mkdir(parents=True)
    (source_directory / "experiment.toml").write_bytes(study.render_effective_config(source_config))
    return source, base, workload


def test_reproduction_changes_only_run_directory_seeds_nothing_and_invokes_exact_nonnested_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    source, base, workload = _source_record_and_config(repository_root)
    expected = _result_value(_valid_result_document(repository_root)).reproduction
    calls: list[tuple[str, ...]] = []
    reconstruction_calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        command = tuple(argv)
        calls.append(command)
        assert cwd == repository_root
        assert check is False and capture_output is True and shell is False
        assert timeout == 1230.0
        assert command.count("scripts/run_bounded.sh") == 1
        assert not (repository_root / "runs" / "validation_study" / "study-1" / "10-streaming-r2-reproduction").exists()
        scratch = (
            repository_root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "mount"
            / "study-1"
            / "streaming.headers"
        )
        scratch.write_bytes(_response_headers(0, 4_194_303))
        return subprocess.CompletedProcess(command, 0, stdout=b"installed cli output\n", stderr=b"")

    def reconstruct(
        root: Path,
        spec: study.StudyRunSpec,
        selected_source: study.StudyRunRecord,
        *,
        command: tuple[str, ...],
        guard_command: tuple[str, ...],
        completed: subprocess.CompletedProcess[bytes],
        elapsed_seconds: float,
        transfer_responses: tuple[study.JsonObject, ...],
    ) -> study.ReproductionRecord:
        assert root == repository_root
        assert selected_source == source
        assert spec.run_id == "10-streaming-r2-reproduction"
        assert elapsed_seconds == 1.0
        assert completed.returncode == 0
        assert len(transfer_responses) == 1
        reconstruction_calls.append((command, guard_command))
        return expected

    monkeypatch.setattr(study, "reconstruct_reproduction", reconstruct, raising=False)
    result = study._run_cli_reproduction(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        "study-1",
        base,
        source,
        workload,
        object_size_bytes=4_194_304,
        runner=runner,
        perf_counter=iter((10.0, 11.0)).__next__,
    )

    assert result == expected
    config_path = repository_root / "runs" / "validation_study" / "study-1" / "realized-configs" / "reproduction.toml"
    source_config = study.load_experiment(repository_root / source.run_directory / "experiment.toml")
    reproduction_config = study.load_experiment(config_path)
    assert _changed_config_paths(
        source_config.model_dump(mode="python"), reproduction_config.model_dump(mode="python")
    ) == {"run.directory"}
    config_record = config_path.relative_to(repository_root).as_posix()
    command = ("uv", "run", "--locked", "trafficlab", "run", config_record)
    assert reconstruction_calls == [(command, (*study._guard_prefix("20m"), *command))]  # pyright: ignore[reportPrivateUsage]
    assert calls == [(*study._guard_prefix("20m"), *command)]  # pyright: ignore[reportPrivateUsage]
    evidence = (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / "study-1"
        / "10-streaming-r2-reproduction"
    )
    assert (evidence / "guard.stdout").read_bytes() == b"installed cli output\n"
    assert stat.S_IMODE((evidence / "guard.stdout").stat().st_mode) == 0o600
    assert stat.S_IMODE((evidence / "guard.stderr").stat().st_mode) == 0o600


def test_reproduction_failure_preserves_primary_cause_and_appends_archive_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    source, base, workload = _source_record_and_config(repository_root)
    failure = TrafficlabError("installed CLI failed", corrective_action="inspect CLI output")

    def failed_archive(_directory: Path, _prepared: object) -> str:
        return "streaming.headers: read failed"

    monkeypatch.setattr(study, "_best_effort_archive", failed_archive)

    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise failure

    with pytest.raises(TrafficlabError, match=r"streaming, repeat 2, position 10.*secondary evidence") as captured:
        study._run_cli_reproduction(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            "study-1",
            base,
            source,
            workload,
            object_size_bytes=4_194_304,
            runner=cast(study.CommandRunner, runner),
            perf_counter=lambda: 1.0,
        )

    assert "runs/validation_study/study-1/10-streaming-r2-reproduction" in str(captured.value)
    assert "streaming.headers: read failed" in str(captured.value)
    assert captured.value.__cause__ is failure


def test_best_effort_archive_returns_secondary_diagnostics(tmp_path: Path) -> None:
    scratch = tmp_path / "missing.headers"

    diagnostic = study._best_effort_archive(  # pyright: ignore[reportPrivateUsage]
        tmp_path / "missing-evidence",
        {"streaming.headers": (scratch, 1)},
    )

    assert diagnostic is not None
    assert "streaming.headers" in diagnostic

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    scratch.mkdir()
    nonregular = study._best_effort_archive(  # pyright: ignore[reportPrivateUsage]
        evidence,
        {"streaming.headers": (scratch, 1)},
    )
    assert nonregular == "streaming.headers: scratch is not a regular file"


def test_cli_reproduction_reconstructs_fresh_fresh_simulation_lineage_and_honest_source_deltas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    evaluate_calls = 0
    real_evaluate_final = study.evaluate_final

    def count_evaluate(*args: Any, **kwargs: Any) -> tuple[TrialResult, ...]:
        nonlocal evaluate_calls
        evaluate_calls += 1
        return real_evaluate_final(*args, **kwargs)

    monkeypatch.setattr(study, "evaluate_final", count_evaluate)
    source_result, source_spec, workload, source_responses = _offline_validation_study_primary(
        repository_root,
        execution_order=4,
        run_id="04-streaming-r2",
        workload_name="streaming",
        repeat=2,
    )
    source = study.extract_primary_record(
        repository_root,
        source_spec,
        workload,
        source_result,
        1.5,
        source_responses,
    )
    base = study.build_base_config(
        workload,
        repository_root=repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        capture_image_id=f"sha256:{'d' * 64}",
    )

    def cli_runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        command = tuple(argv)
        assert cwd == repository_root
        assert check is False and capture_output is True and shell is False
        assert timeout == 1230.0
        config_path = repository_root / command[-1]

        def capture(_path: Path, prepared: PreparedExperiment) -> CaptureResult:
            capture_path = prepared.run_directory / "capture.json"
            reference_path = prepared.run_directory / "reference.pcapng"
            capture_path.write_bytes(_CAPTURE_BYTES)
            reference_path.write_bytes(_REFERENCE_BYTES)
            inspection = validate_capture_pair(capture_path, reference_path, deadline=None)
            append_run_log(
                prepared.run_directory,
                {
                    "event": "capture_published",
                    "packet_count": inspection.packet_count,
                    "path": str(reference_path),
                    "project_name": "trafficlab-validation-study-reproduction",
                    "reused": False,
                    "stage": "capture",
                },
            )
            return CaptureResult(prepared.run_directory, reference_path, inspection.packet_count, 0, reused=False)

        run_experiment(
            config_path,
            dependencies=RunDependencies(
                open_or_prepare_experiment,
                capture,
                fit_experiment,
                generate_experiment,
                compare_experiment,
            ),
        )
        scratch = (
            repository_root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "mount"
            / "study-1"
            / "streaming.headers"
        )
        scratch.write_bytes(_response_headers(0, 4_194_303))
        return subprocess.CompletedProcess(command, 0, stdout=b"reproduced\n", stderr=b"")

    reproduction = study._run_cli_reproduction(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        "study-1",
        base,
        source,
        workload,
        object_size_bytes=4_194_304,
        runner=cli_runner,
        perf_counter=iter((20.0, 22.0)).__next__,
    )

    document = cast(study.JsonObject, study._thaw_json(reproduction.document))  # pyright: ignore[reportPrivateUsage]
    assert evaluate_calls == 1
    assert document["fresh_simulation"]["source"] == "post_cli_evaluate_final"  # type: ignore[index]
    assert document["seeded_artifact_count"] == 0
    assert document["reuse"] == {"capture": False, "best_model": False, "generated": False, "similarity": False}
    assert document["raw_sequence"] == {
        "seed": 97,
        "observation_window_seconds": 10.0,
        "trial_event_count": cast(dict[str, object], document["generated"])["packet_count"],
        "final_event_count": cast(dict[str, object], document["generated"])["packet_count"],
        "raw_events_equal": True,
        "fresh_simulation_score_reproduced": True,
        "reparsed_event_count": cast(dict[str, object], document["generated"])["packet_count"],
        "reparsed_matches_quantized": True,
    }
    comparison = cast(dict[str, object], document["comparison_to_source"])
    assert comparison["winner_family_equal"] is True
    assert comparison["winner_genes_equal"] is True
    assert comparison["winner_selection_fitness_delta"] == 0.0
    assert comparison["reference_similarity"] == _score(1.0)


def _reject_direct_reproduction_mutation(mutation: str, repository_root: Path) -> bool:
    if mutation == "reused-log":
        with pytest.raises(ValueError, match="reused"):
            study._fresh_run_log_proofs(  # pyright: ignore[reportPrivateUsage]
                (
                    {"event": "capture_published", "stage": "capture", "reused": False},
                    {"event": "best_model_reused"},
                    {"event": "comparison_succeeded", "reused": False},
                    {"event": "run_completed"},
                )
            )
        return True
    if mutation == "evaluate-final-count":
        with pytest.raises(ValueError, match="exactly one"):
            study._sole_final_trial(  # pyright: ignore[reportPrivateUsage]
                (_trial_result(97, 0.5), _trial_result(97, 0.5))
            )
        return True
    if mutation == "unbound-published-comparison":
        _state, _best, comparison = _terminal_checkpoint_and_best(repository_root)
        with pytest.raises(ValueError, match="lineage"):
            study._require_published_lineage(  # pyright: ignore[reportPrivateUsage]
                comparison,
                comparison,
                {"capture.json": b"capture", "reference.pcapng": b"reference", "generated.pcapng": b"generated"},
                ContentIdentity(size=1, sha256=_HASH),
            )
        return True
    return False


@pytest.mark.parametrize(
    "mutation",
    [
        "source-not-streaming-r2",
        "extra-config-change",
        "seeded-artifact",
        "wrong-cli-suffix",
        "nested-guard",
        "nonzero-status",
        "reused-log",
        "winner-best-model-mismatch",
        "evaluate-final-count",
        "unbound-published-comparison",
    ],
)
def test_reproduction_rejects_nonfresh_or_inconsistent_evidence(
    mutation: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = _valid_result_document(repository_root)
    protocol = cast(study.JsonObject, document["protocol"])
    source = cast(study.JsonObject, cast(list[object], document["runs"])[3])
    reproduction = cast(study.JsonObject, document["reproduction"])
    if mutation == "source-not-streaming-r2":
        source = cast(study.JsonObject, cast(list[object], document["runs"])[0])
    elif mutation == "extra-config-change":
        reproduction["changed_config_fields"] = ["run.directory", "target.image"]
    elif mutation == "seeded-artifact":
        reproduction["seeded_artifact_count"] = 1
    elif mutation == "wrong-cli-suffix":
        cast(list[str], reproduction["command"])[-1] = "wrong.toml"
    elif mutation == "nested-guard":
        guard = cast(list[str], reproduction["guard_command"])
        guard[guard.index("--") + 1 : guard.index("--") + 1] = list(
            study._guard_prefix("20m")  # pyright: ignore[reportPrivateUsage]
        )
    elif mutation == "nonzero-status":
        reproduction["guard_exit_status"] = 1
    elif mutation == "winner-best-model-mismatch":
        cast(dict[str, object], reproduction["winner"])["genes"] = [2.0]
    elif _reject_direct_reproduction_mutation(mutation, repository_root):
        return

    with pytest.raises(ValueError):
        study._validate_reproduction(  # pyright: ignore[reportPrivateUsage]
            reproduction,
            repository_root=repository_root,
            protocol=protocol,
            source=source,
        )


def test_study_builds_variation_summaries_reproduction_and_publishes_one_canonical_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, expected = _write_study_inputs(repository_root)
    events: list[str] = []
    real_publish = study._publish_results  # pyright: ignore[reportPrivateUsage]
    _install_primary_orchestration_doubles(monkeypatch, expected, events)
    published: list[bytes] = []

    def publish(path: Path, value: study.StudyResults, *, repository_root: Path) -> None:
        real_publish(path, value, repository_root=repository_root)
        published.append(path.read_bytes())

    monkeypatch.setattr(study, "_publish_results", publish)
    result = study.run_study(
        "https://downloads.example.test/object.bin",
        "study-1",
        prerequisite_path,
        repository_root=repository_root,
        run=lambda _path: cast(RunResult, object()),
        runner=_StudyIdentityRunner(repository_root),
        perf_counter=iter(float(value) for value in range(30)).__next__,
        utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
    )

    result_path = repository_root / "examples" / "validation_study" / "results.json"
    assert len(published) == 1
    assert result_path.read_bytes() == published[0]
    assert study.parse_study_results(published[0], repository_root=repository_root) == result
    assert study.render_study_results(result) == published[0]
    assert len(result.runs) == 9
    assert len(result.natural_variation) == len(result.workload_summaries) == 3
    assert result.reproduction == expected.reproduction
    assert not (repository_root / "examples" / "validation_study" / "REPORT.md").exists()


def test_study_cli_requires_exact_url_id_and_prerequisite_path_and_never_wraps_itself(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    calls: list[tuple[object, ...]] = []
    expected = _result_value(_valid_result_document(repository_root))

    def run_study_double(*args: object, **kwargs: object) -> study.StudyResults:
        calls.append((*args, kwargs))
        return expected

    monkeypatch.setattr(study, "run_study", run_study_double)
    prerequisite_record = "examples/validation_study/prerequisites.json"
    assert (
        study.main(
            [
                "study",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                prerequisite_record,
            ],
            repository_root=repository_root,
            run=lambda _path: cast(RunResult, object()),
            runner=_StudyIdentityRunner(repository_root),
            perf_counter=lambda: 1.0,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )
        == 0
    )
    assert len(calls) == 1
    positional = calls[0][:-1]
    keywords = cast(dict[str, object], calls[0][-1])
    assert positional == (
        "https://downloads.example.test/object.bin",
        "study-1",
        repository_root / prerequisite_record,
    )
    assert keywords["repository_root"] == repository_root
    assert "run_bounded.sh" not in str(calls)
    assert "study completed" in capsys.readouterr().out

    invalid = (
        ["study"],
        [
            "study",
            "--url",
            "http://example.test/object",
            "--study-id",
            "study-1",
            "--prerequisites",
            prerequisite_record,
        ],
        [
            "study",
            "--url",
            "https://downloads.example.test/object.bin",
            "--study-id",
            "INVALID",
            "--prerequisites",
            prerequisite_record,
        ],
        [
            "study",
            "--url",
            "https://downloads.example.test/object.bin",
            "--study-id",
            "study-1",
            "--prerequisites",
            "../outside.json",
        ],
    )
    for arguments in invalid:
        assert study.main(arguments, repository_root=repository_root) == 2
        assert capsys.readouterr().err
    assert len(calls) == 1


def _offline_published_study(repository_root: Path) -> tuple[Path, Path, Path]:
    prerequisite_path, _expected = _write_study_inputs(repository_root)
    prerequisites = study.parse_prerequisite_results(prerequisite_path.read_bytes(), repository_root=repository_root)
    configs = study.validate_base_configs(repository_root, prerequisites)
    workloads = {item.name: item for item in study.workload_specs(prerequisites.url)}
    records: list[study.StudyRunRecord] = []
    traces: dict[tuple[study.WorkloadName, int], tuple[TraceEvent, ...]] = {}
    settings: dict[study.WorkloadName, SimilarityConfig] = {}
    for order, run_id, workload_value, repeat in study.PRIMARY_ORDER:
        workload_name = cast(study.WorkloadName, workload_value)
        run_result, spec, workload, responses = _offline_validation_study_primary(
            repository_root,
            execution_order=order,
            run_id=run_id,
            workload_name=workload_name,
            repeat=repeat,
            base_config=configs[workload_name],
        )
        records.append(
            study.extract_primary_record(
                repository_root,
                spec,
                workload,
                run_result,
                float(order),
                responses,
            )
        )
        traces[(workload_name, repeat)] = study._load_reference_trace(  # pyright: ignore[reportPrivateUsage]
            spec.run_directory
        )
        settings[workload_name] = configs[workload_name].similarity

    def reproduction_runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        command = tuple(argv)
        assert cwd == repository_root
        assert check is False and capture_output is True and shell is False
        assert timeout == 1230.0
        run_experiment(
            repository_root / command[-1],
            dependencies=RunDependencies(
                open_or_prepare_experiment,
                _offline_capture,
                fit_experiment,
                generate_experiment,
                compare_experiment,
            ),
        )
        scratch = (
            repository_root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "mount"
            / "study-1"
            / "streaming.headers"
        )
        scratch.write_bytes(_response_headers(0, 4_194_303))
        return subprocess.CompletedProcess(command, 0, stdout=b"offline reproduction\n", stderr=b"")

    reproduction = study._run_cli_reproduction(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        prerequisites.study_id,
        configs["streaming"],
        records[3],
        workloads["streaming"],
        object_size_bytes=cast(int, prerequisites.capability["object_size_bytes"]),
        runner=reproduction_runner,
        perf_counter=iter((20.0, 22.0)).__next__,
    )
    identity = cast(
        study.JsonObject,
        {
            "git_commit": prerequisites.git_commit,
            "python_version": prerequisites.tools["python_version"],
            "trafficlab_version": prerequisites.tools["trafficlab_version"],
            "docker_engine_version": prerequisites.tools["docker_engine_version"],
            "docker_compose_version": prerequisites.tools["docker_compose_version"],
            "platform": prerequisites.tools["platform"],
        },
    )
    result = study.StudyResults(
        schema_version=1,
        environment=study._environment_record(  # pyright: ignore[reportPrivateUsage]
            prerequisites, identity, "2026-08-13T13:00:00Z"
        ),
        protocol=study._protocol_record(  # pyright: ignore[reportPrivateUsage]
            prerequisites, prerequisite_path.read_bytes()
        ),
        runs=tuple(records),
        natural_variation=cast(
            tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject],
            tuple(_frozen(value) for value in study.natural_variation(records, traces, settings)),
        ),
        workload_summaries=cast(
            tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject],
            tuple(_frozen(value) for value in study.workload_summaries(records)),
        ),
        reproduction=reproduction,
    )
    result_path = repository_root / "examples" / "validation_study" / "results.json"
    study._publish_results(result_path, result, repository_root=repository_root)  # pyright: ignore[reportPrivateUsage]
    report_path = repository_root / "examples" / "validation_study" / "REPORT.md"
    identifiers = [
        prerequisites.study_id,
        prerequisites.git_commit,
        cast(str, prerequisites.images["target_image_id"]),
        cast(str, prerequisites.images["capture_image_id"]),
        *(record.run_id for record in records),
        "10-streaming-r2-reproduction",
    ]
    report_path.write_text("\n\n".join((*study.REPORT_HEADINGS, *identifiers)), encoding="utf-8")
    return prerequisite_path, result_path, report_path


def test_local_audit_revalidates_report_checkpoint_artifacts_and_lineage_without_external_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, result_path, report_path = _offline_published_study(repository_root)
    prerequisite = study.parse_prerequisite_results(prerequisite_path.read_bytes(), repository_root=repository_root)

    def reject_external(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("external")

    monkeypatch.setattr(study.subprocess, "run", reject_external)
    before = {path: path.read_bytes() for path in repository_root.rglob("*") if path.is_file()}
    study.audit_published_study(
        repository_root=repository_root,
        prerequisite_path=prerequisite_path,
        result_path=result_path,
        report_path=report_path,
    )
    after = {path: path.read_bytes() for path in repository_root.rglob("*") if path.is_file()}
    assert before == after
    for missing in (
        study.REPORT_HEADINGS[0],
        prerequisite.study_id,
        prerequisite.git_commit,
        cast(str, prerequisite.images["target_image_id"]),
        cast(str, prerequisite.images["capture_image_id"]),
        study.PRIMARY_ORDER[0][1],
        "10-streaming-r2-reproduction",
    ):
        original = report_path.read_text(encoding="utf-8")
        report_path.write_text(original.replace(missing, "removed", 1), encoding="utf-8")
        with pytest.raises(TrafficlabError, match="report"):
            study.audit_published_study(
                repository_root=repository_root,
                prerequisite_path=prerequisite_path,
                result_path=result_path,
                report_path=report_path,
            )
        report_path.write_text(original, encoding="utf-8")

    results = study.parse_study_results(result_path.read_bytes(), repository_root=repository_root)
    checkpoint_path = repository_root / results.runs[0].run_directory / "checkpoint.json"
    checkpoint_content = checkpoint_path.read_bytes()
    checkpoint_path.write_bytes(checkpoint_content + b" ")
    with pytest.raises(TrafficlabError):
        study.audit_published_study(
            repository_root=repository_root,
            prerequisite_path=prerequisite_path,
            result_path=result_path,
            report_path=report_path,
        )
    checkpoint_path.write_bytes(checkpoint_content)


def _copy_validation_study_candidate(tmp_path: Path, *, generated: bool = False) -> tuple[Path, Path]:
    repository = tmp_path / "relocated-repository"
    source_environment = cast(
        dict[str, object],
        json.loads(
            (_ROOT / "tests" / "fixtures" / "validation_study_candidate" / "environment.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    source_commit = cast(str, source_environment["source_commit"])
    source_tree = cast(str, source_environment["source_tree"])
    subprocess.run(
        ("git", "clone", "--no-hardlinks", "--no-checkout", str(_ROOT), str(repository)),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "checkout", "--detach", source_commit),
        cwd=repository,
        check=True,
        capture_output=True,
    )
    candidate = repository / "candidate"
    if generated:
        for relative, content in fixture_generator.generate_fixture_tree(
            source_commit=source_commit, source_tree=source_tree
        ).items():
            path = candidate / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    else:
        shutil.copytree(_ROOT / "tests" / "fixtures" / "validation_study_candidate", candidate)
    return repository, candidate


def _candidate_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _rewrite_candidate_manifest(candidate: Path) -> None:
    index = cast(dict[str, object], json.loads((candidate / "index.json").read_text(encoding="utf-8")))
    auditor.write_manifest(
        candidate,
        ownership=cast(dict[str, str], index["ownership"]),
        lineage=cast(dict[str, object], index["lineage"]),
    )


def _write_canonical_json(path: Path, document: object) -> None:
    path.write_bytes(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        + b"\n"
    )


def _candidate_index(candidate: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads((candidate / "index.json").read_text(encoding="utf-8")))


def _write_candidate_index(candidate: Path, index: dict[str, object]) -> None:
    _write_canonical_json(candidate / "index.json", index)


def test_offline_bundle_audit_reconstructs_relocated_complete_fixture_without_external_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    before = _candidate_bytes(candidate)

    def reject_external(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("offline audit attempted an external operation")

    monkeypatch.setattr(socket, "socket", reject_external)
    monkeypatch.setattr(socket, "create_connection", reject_external)
    original_run = subprocess.run

    def local_git_only(argv: Sequence[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if tuple(argv[:1]) == ("git",):
            return original_run(argv, *args, **kwargs)  # type: ignore[call-overload]
        raise AssertionError("offline audit attempted a non-Git subprocess")

    monkeypatch.setattr(subprocess, "run", local_git_only)
    monkeypatch.setattr(study, "run_experiment", reject_external)

    result = auditor.audit_bundle(candidate, repository=repository)

    assert result.bundle == candidate
    assert result.run_directory == candidate / "training" / "short" / "r1"
    assert result.file_count == len(before) - 1
    assert result.manifest_sha256 == hashlib.sha256(before["manifest.json"]).hexdigest()
    assert _candidate_bytes(candidate) == before


@pytest.mark.parametrize(
    ("mutation", "expected_kind"),
    (
        ("missing", "artifact_missing"),
        ("corrupt", "artifact_corrupt"),
        ("foreign", "artifact_foreign"),
        ("extra", "artifact_foreign"),
        ("symlink", "artifact_foreign"),
        ("temporary", "artifact_foreign"),
        ("owner", "artifact_foreign"),
        ("lineage", "artifact_foreign"),
    ),
)
def test_offline_bundle_audit_rejects_first_manifest_or_artifact_mismatch(
    tmp_path: Path,
    mutation: str,
    expected_kind: str,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)

    if mutation == "missing":
        (candidate / "training" / "short" / "r1" / "best_model.json").unlink()
    elif mutation == "corrupt":
        path = candidate / "training" / "short" / "r1" / "checkpoint.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "foreign":
        target = candidate / "training" / "short" / "r1" / "generated.pcapng"
        target.write_bytes((candidate / "training" / "short" / "r1" / "reference.pcapng").read_bytes())
        _rewrite_candidate_manifest(candidate)
    elif mutation == "extra":
        (candidate / "unexpected.bin").write_bytes(b"unexpected")
    elif mutation == "symlink":
        (candidate / "training" / "short" / "r1" / "unexpected-link").symlink_to("generated.pcapng")
    elif mutation == "temporary":
        (candidate / "training" / "short" / "r1" / ".generated.tmp").write_bytes(b"temporary")
    else:
        index_path = candidate / "index.json"
        index = cast(dict[str, object], json.loads(index_path.read_text(encoding="utf-8")))
        relative = "training/short/r1/generated.pcapng"
        mapping_name = "ownership" if mutation == "owner" else "lineage"
        mapping = cast(dict[str, object], index[mapping_name])
        mapping[relative] = f"changed-{mutation}"
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == expected_kind
    assert outcome.stage == "publication"
    assert outcome.evidence_state == "not_published"
    assert outcome.authority == "primary"
    assert error.value.failure_outcomes == (outcome,)
    if mutation == "missing":
        assert (
            outcome.kind,
            outcome.stage,
            outcome.detail,
            outcome.affected_evidence,
            outcome.evidence_state,
            outcome.corrective_action,
            outcome.authority,
        ) == (
            "artifact_missing",
            "publication",
            "training/short/r1/best_model.json is missing from the retained bundle",
            "training/short/r1/best_model.json",
            "not_published",
            "restore the exact retained artifact",
            "primary",
        )


def test_audited_bundle_publication_rechecks_candidate_and_preserves_an_occupied_destination(tmp_path: Path) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    evidence_root = repository / "examples" / "validation_study" / "evidence"

    destination = study.publish_audited_bundle(candidate, "fixture-study", repository_root=repository)
    before = _candidate_bytes(destination)
    root_before = _candidate_bytes(repository)

    with pytest.raises(TrafficlabError) as error:
        study.publish_audited_bundle(candidate, "fixture-study", repository_root=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "publication_collision"
    assert outcome.stage == "publication"
    assert destination == evidence_root / "fixture-study"
    assert _candidate_bytes(destination) == before
    assert _candidate_bytes(repository) == root_before
    assert not tuple(repository.rglob("*.tmp"))


def test_audited_bundle_rejects_the_first_primary_without_publication_residue(tmp_path: Path) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    before = _candidate_bytes(candidate)
    missing = "protocol.json"
    (candidate / missing).unlink()
    expected_candidate = dict(before)
    del expected_candidate[missing]

    with pytest.raises(TrafficlabError) as error:
        study.publish_audited_bundle(candidate, "rejected-study", repository_root=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.detail,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.corrective_action,
        outcome.authority,
    ) == (
        "artifact_missing",
        "publication",
        "protocol.json is missing from the retained bundle",
        "protocol.json",
        "not_published",
        "restore the exact retained artifact",
        "primary",
    )
    assert error.value.failure_outcomes == (outcome,)
    assert _candidate_bytes(candidate) == expected_candidate
    assert not (repository / "examples" / "validation_study" / "evidence").exists()
    assert not tuple(repository.rglob("*.tmp"))


@pytest.mark.parametrize("target", ("manifest", "run-log"))
def test_offline_bundle_audit_rejects_duplicate_json_keys_at_the_owned_boundary(
    tmp_path: Path,
    target: str,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    if target == "manifest":
        (candidate / "manifest.json").write_bytes(b'{"files":[],"files":[],"schema_version":2}\n')
    else:
        log_path = candidate / "training" / "short" / "r1" / "run.log"
        log_path.write_bytes(b'{"event":"fixture","event":"duplicate","stage":"fit"}\n')
        _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "artifact_corrupt"
    assert outcome.stage == "publication"
    assert outcome.authority == "primary"


@pytest.mark.parametrize("mutation", ("environment", "final-controls"))
def test_offline_bundle_audit_reconstructs_environment_and_final_controls(
    tmp_path: Path,
    mutation: str,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    index_path = candidate / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if mutation == "environment":
        (repository / "uv.lock").write_bytes(b"different lock\n")
    else:
        cast(list[dict[str, object]], index["fresh_simulation"])[0]["seed"] = 98
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "artifact_foreign"
    assert outcome.affected_evidence in {"environment", "fresh_simulation/short/r1.json"}


def test_offline_bundle_audit_derives_w_from_the_normalized_reference(tmp_path: Path) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    model_path = candidate / "training" / "short" / "r1" / "best_model.json"
    model = load_best_model(model_path.read_bytes(), source=model_path)
    model_path.write_bytes(
        render_best_model(replace(model, observation_window_seconds=model.observation_window_seconds + 1.0))
    )
    _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.detail,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.corrective_action,
        outcome.authority,
    ) == (
        "scientific_semantics_incompatible",
        "publication",
        "best model final controls do not match normalized training reference",
        "training/short/r1",
        "not_published",
        "restore frozen training evidence",
        "primary",
    )


@pytest.mark.parametrize(
    ("relative", "content"),
    (
        ("training/short/r1/experiment.toml", b"[run\n"),
        ("training/short/r1/run.log", b"\xff\n"),
        ("training/short/r1/run.log", b'{"event": "fixture"}\n'),
    ),
)
def test_offline_bundle_audit_rejects_noncanonical_owned_artifact_boundaries(
    tmp_path: Path,
    relative: str,
    content: bytes,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    (candidate / relative).write_bytes(content)
    _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "artifact_corrupt"
    assert outcome.stage == "publication"
    assert outcome.evidence_state == "not_published"


def test_offline_bundle_audit_reports_the_canonical_jsonl_owner_diagnostic(tmp_path: Path) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    log_path = candidate / "training" / "short" / "r1" / "run.log"
    log_path.write_bytes(b'{"event": "fixture"}\n')
    _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.detail,
        outcome.affected_evidence,
        outcome.corrective_action,
    ) == (
        "artifact_corrupt",
        "run log record is not canonical JSONL",
        "training/short/r1/run.log",
        "restore canonical run log",
    )


@pytest.mark.parametrize(
    ("content", "detail"),
    (
        (b"", "run log must be nonempty canonical JSONL with a terminal newline"),
        (b'{}\r{"event":"fixture"}\n', "run log must use LF-terminated records"),
    ),
)
def test_offline_bundle_audit_covers_the_remaining_canonical_jsonl_boundaries(
    tmp_path: Path,
    content: bytes,
    detail: str,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    (candidate / "training" / "short" / "r1" / "run.log").write_bytes(content)
    _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.detail, outcome.affected_evidence) == (
        "artifact_corrupt",
        detail,
        "training/short/r1/run.log",
    )


@pytest.mark.parametrize(
    ("field", "value", "expected_kind"),
    (
        ("scientific_artifact_schema", 1, "scientific_semantics_incompatible"),
        ("python_implementation", "PyPy", "scientific_semantics_incompatible"),
        ("source_commit", "z" * 40, "artifact_corrupt"),
        ("target_image_reference", "trafficlab-target:latest", "artifact_corrupt"),
        ("target_image_id", "sha256:bad", "artifact_corrupt"),
        (
            "compatibility_decision",
            {"reason": "fixture", "status": "incompatible"},
            "scientific_semantics_incompatible",
        ),
    ),
)
def test_offline_bundle_audit_validates_every_environment_lock_boundary(
    tmp_path: Path,
    field: str,
    value: object,
    expected_kind: str,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    environment_path = candidate / "environment.json"
    environment = cast(dict[str, object], json.loads(environment_path.read_text(encoding="utf-8")))
    environment[field] = value
    _write_canonical_json(environment_path, environment)
    _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "environment",
        "not_published",
        "primary",
    )


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("directory", "artifact_foreign"),
        ("configuration_path", "artifact_foreign"),
        ("seeds", "scientific_semantics_incompatible"),
        ("run_configuration", "artifact_foreign"),
        ("reconstruction", "artifact_corrupt"),
        ("history", "artifact_foreign"),
        ("winner", "artifact_foreign"),
        ("comparison_parse", "artifact_corrupt"),
        ("comparison", "artifact_foreign"),
        ("index_identity", "artifact_foreign"),
    ),
)
def test_offline_bundle_audit_covers_training_record_and_reconstruction_boundaries(
    tmp_path: Path,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    index = _candidate_index(candidate)
    training = cast(list[dict[str, object]], index["training"])
    record = training[0]
    run = candidate / "training" / "short" / "r1"

    if case == "directory":
        record["directory"] = "training/short/r2"
        _write_candidate_index(candidate, index)
    elif case == "configuration_path":
        record["portable_config"] = "configs/training-short-r1.realized.toml"
        _write_candidate_index(candidate, index)
    elif case == "seeds":
        protocol_path = candidate / "protocol.json"
        protocol = cast(dict[str, object], json.loads(protocol_path.read_text(encoding="utf-8")))
        protocol["selection_seeds"] = [18, 30]
        _write_canonical_json(protocol_path, protocol)
    elif case == "run_configuration":
        (run / "experiment.toml").write_bytes((candidate / "configs" / "training-short-r1.realized.toml").read_bytes())
    elif case == "reconstruction":
        (run / "capture.json").write_bytes(b"{}\n")
    elif case == "history":
        (run / "ga_history.csv").write_bytes(b"unexpected-history\n")
    elif case == "winner":
        (run / "best_model.json").write_bytes(
            (candidate / "training" / "short" / "r2" / "best_model.json").read_bytes()
        )
    elif case == "comparison_parse":
        (run / "similarity.json").write_bytes(b"{}\n")
    elif case == "comparison":
        (run / "similarity.json").write_bytes(
            (candidate / "training" / "short" / "r2" / "similarity.json").read_bytes()
        )
    else:
        identity = cast(dict[str, object], record["reference_identity"])
        identity["sha256"] = "0" * 64
        _write_candidate_index(candidate, index)
    _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "not_published",
        "primary",
    )


@pytest.mark.parametrize("case", ("stored_record", "identity"))
def test_offline_bundle_audit_covers_fresh_simulation_record_boundaries(tmp_path: Path, case: str) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    index = _candidate_index(candidate)
    record = cast(list[dict[str, object]], index["fresh_simulation"])[0]
    path = candidate / cast(str, record["path"])
    if case == "stored_record":
        stored = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        stored["seed"] = 98
        _write_canonical_json(path, stored)
    else:
        identity = cast(dict[str, object], record["reference_identity"])
        identity["sha256"] = "0" * 64
        _write_canonical_json(path, record)
        _write_candidate_index(candidate, index)
    _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence) == ("artifact_foreign", cast(str, record["path"]))


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("binding", "artifact_foreign"),
        ("configuration", "scientific_semantics_incompatible"),
        ("training_reference", "artifact_foreign"),
        ("reconstruction", "artifact_corrupt"),
        ("outputs", "artifact_foreign"),
        ("record", "artifact_foreign"),
    ),
)
def test_offline_bundle_audit_covers_independent_held_out_boundaries(
    tmp_path: Path,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    index = _candidate_index(candidate)
    held = cast(list[dict[str, object]], index["held_out"])[0]
    directory = candidate / cast(str, held["directory"])
    if case == "binding":
        held["training_directory"] = "training/short/r2"
        _write_candidate_index(candidate, index)
    elif case == "configuration":
        for name in ("portable.toml", "realized.toml"):
            path = directory / name
            path.write_bytes(path.read_bytes().replace(b"final_seed = 97", b"final_seed = 98"))
    elif case == "training_reference":
        (directory / "reference.pcapng").write_bytes(
            (candidate / "training" / "short" / "r1" / "reference.pcapng").read_bytes()
        )
    elif case == "reconstruction":
        (directory / "capture.json").write_bytes(b"{}\n")
    elif case == "outputs":
        (directory / "generated.pcapng").write_bytes(
            (candidate / "held_out" / "streaming" / "generated.pcapng").read_bytes()
        )
    else:
        record_path = directory / "record.json"
        record = cast(dict[str, object], json.loads(record_path.read_text(encoding="utf-8")))
        record["seed"] = 98
        _write_canonical_json(record_path, record)
    _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "not_published",
        "primary",
    )


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("schema", "scientific_semantics_incompatible"),
        ("root_path", "artifact_foreign"),
        ("training_type", "artifact_corrupt"),
        ("training_count", "artifact_corrupt"),
        ("training_duplicate", "artifact_foreign"),
        ("fresh_type", "artifact_corrupt"),
        ("fresh_count", "artifact_corrupt"),
        ("held_type", "artifact_corrupt"),
        ("held_count", "artifact_corrupt"),
        ("held_duplicate", "artifact_foreign"),
        ("report_inputs", "artifact_foreign"),
        ("report", "artifact_foreign"),
    ),
)
def test_offline_bundle_audit_covers_complete_index_schema_boundaries(
    tmp_path: Path,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    index = _candidate_index(candidate)
    if case == "schema":
        index["schema_version"] = 1
        _write_candidate_index(candidate, index)
    elif case == "root_path":
        index["report"] = "other-report.json"
        _write_candidate_index(candidate, index)
    elif case == "training_type":
        index["training"] = {}
        _write_candidate_index(candidate, index)
    elif case == "training_count":
        index["training"] = cast(list[object], index["training"])[:-1]
        _write_candidate_index(candidate, index)
    elif case == "training_duplicate":
        training = cast(list[dict[str, object]], index["training"])
        training[-1] = copy.deepcopy(training[0])
        _write_candidate_index(candidate, index)
    elif case == "fresh_type":
        index["fresh_simulation"] = {}
        _write_candidate_index(candidate, index)
    elif case == "fresh_count":
        index["fresh_simulation"] = cast(list[object], index["fresh_simulation"])[:-1]
        _write_candidate_index(candidate, index)
    elif case == "held_type":
        index["held_out"] = {}
        _write_candidate_index(candidate, index)
    elif case == "held_count":
        index["held_out"] = cast(list[object], index["held_out"])[:-1]
        _write_candidate_index(candidate, index)
    elif case == "held_duplicate":
        held = cast(list[dict[str, object]], index["held_out"])
        held[1] = copy.deepcopy(held[0])
        _write_candidate_index(candidate, index)
    elif case == "report_inputs":
        path = candidate / "report_inputs.json"
        document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        document["formula"] = "not-arithmetic"
        _write_canonical_json(path, document)
    else:
        path = candidate / "report.json"
        document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        document["formula"] = "not-arithmetic"
        _write_canonical_json(path, document)
    _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "not_published",
        "primary",
    )


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (("wrong_type", "artifact_corrupt"), ("manifest_disagreement", "artifact_foreign")),
)
def test_offline_bundle_audit_validates_index_metadata_before_scientific_reconstruction(
    tmp_path: Path,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    index = _candidate_index(candidate)
    ownership = copy.deepcopy(cast(dict[str, str], index["ownership"]))
    lineage = copy.deepcopy(cast(dict[str, object], index["lineage"]))
    if case == "wrong_type":
        index["ownership"] = []
    else:
        cast(dict[str, object], index["ownership"])["training/short/r1/generated.pcapng"] = "wrong-owner"
    _write_candidate_index(candidate, index)
    auditor.write_manifest(candidate, ownership=ownership, lineage=lineage)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence) == (expected_kind, "index.json")


@pytest.mark.parametrize(
    ("relative", "owner", "relation"),
    (
        (
            "prerequisites/docker_matrix.command.json",
            "prerequisite:docker_matrix:command.json",
            {"relation": "prerequisite", "record": "docker_matrix.command.json"},
        ),
        ("headers/short.headers", "transfer-header:short", {"relation": "transfer-header", "workload": "short"}),
        (
            "observations/streaming.json",
            "external-observation:streaming",
            {"relation": "external-observation", "workload": "streaming"},
        ),
        (
            "configs/training-short-r1.portable.toml",
            "configuration:training-short-r1.portable",
            {"relation": "configuration", "name": "training-short-r1.portable"},
        ),
        (
            "training/bursty/r2/run.log",
            "training:bursty:r2",
            {"relation": "run.log", "repeat": 2, "workload": "bursty"},
        ),
        (
            "fresh_simulation/short/r3.json",
            "fresh-simulation:short:r3",
            {"relation": "fresh_simulation", "repeat": 3, "workload": "short"},
        ),
        ("held_out/bursty/reference.pcapng", "held-out:bursty", {"relation": "reference.pcapng", "workload": "bursty"}),
    ),
)
def test_schema_owner_and_lineage_mapping_cover_every_retained_evidence_family(
    relative: str,
    owner: str,
    relation: dict[str, object],
) -> None:
    assert auditor.owner_for_path(relative) == owner
    assert auditor.lineage_for_path(relative) == relation


@pytest.mark.parametrize(
    "relative",
    (
        "prerequisites/unknown.command.json",
        "headers/unknown.headers",
        "observations/unknown.json",
        "not-documented.bin",
    ),
)
def test_schema_owner_mapping_rejects_partial_or_unknown_paths(relative: str) -> None:
    with pytest.raises(Exception, match="documented owner"):
        auditor.owner_for_path(relative)


def test_schema_lineage_mapping_rejects_unknown_path_family() -> None:
    with pytest.raises(Exception, match="documented lineage"):
        auditor.lineage_for_path("not-documented.bin")


def test_schema_file_inventory_reports_enumeration_lstat_and_nonregular_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()

    def unavailable_rglob(_path: Path, _pattern: str) -> Any:
        raise OSError("enumeration unavailable")

    monkeypatch.setattr(Path, "rglob", unavailable_rglob)
    with pytest.raises(Exception, match="could not enumerate retained bundle"):
        auditor.files_for_candidate(candidate, include_manifest=False)
    monkeypatch.undo()

    regular = candidate / "regular.bin"
    regular.write_bytes(b"regular")
    original_lstat = Path.lstat

    def unavailable_lstat(path: Path) -> os.stat_result:
        if path == regular:
            raise OSError("inspection unavailable")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", unavailable_lstat)
    with pytest.raises(Exception, match="could not inspect regular.bin"):
        auditor.files_for_candidate(candidate, include_manifest=False)
    monkeypatch.undo()

    fifo = candidate / "foreign.fifo"
    os.mkfifo(fifo)
    with pytest.raises(Exception, match="must be a regular file"):
        auditor.files_for_candidate(candidate, include_manifest=False)


def test_schema_manifest_writer_rejects_incomplete_keys_and_empty_owner(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "retained.bin").write_bytes(b"retained")

    with pytest.raises(ValueError, match="keys must equal"):
        auditor.write_manifest(candidate, ownership={}, lineage={})
    with pytest.raises(ValueError, match="nonempty string"):
        auditor.write_manifest(candidate, ownership={"retained.bin": ""}, lineage={"retained.bin": {}})


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("duplicate_fresh", "artifact_foreign"),
        ("missing_schema_path", "artifact_missing"),
        ("unlisted_schema_path", "artifact_foreign"),
    ),
)
def test_offline_bundle_audit_covers_internal_complete_schema_invariants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    if case == "duplicate_fresh":

        def duplicate_fresh(*_args: object, **_kwargs: object) -> str:
            return "fresh_simulation/short/r1.json"

        monkeypatch.setattr(auditor, "_fresh", duplicate_fresh)
    else:
        original = auditor._expected_paths  # pyright: ignore[reportPrivateUsage]

        def altered_expected(
            index: dict[str, object],
            protocol: dict[str, object],
            prerequisite_paths: set[str],
            training: Sequence[Any],
            fresh_paths: set[str],
            held_paths: set[str],
        ) -> set[str]:
            result = original(index, protocol, prerequisite_paths, training, fresh_paths, held_paths)
            if case == "missing_schema_path":
                return result | {"missing-schema-path.json"}
            return result - {"report.json"}

        monkeypatch.setattr(auditor, "_expected_paths", altered_expected)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "not_published",
        "primary",
    )


def test_audit_bundle_rejects_a_candidate_outside_the_relocated_repository(tmp_path: Path) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    outside = tmp_path / "outside-candidate"
    shutil.copytree(candidate, outside)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(outside, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.detail,
        outcome.affected_evidence,
        outcome.corrective_action,
    ) == (
        "artifact_foreign",
        "bundle must remain beneath the relocated repository",
        "bundle",
        "use a retained candidate beneath the repository",
    )


def test_audit_bundle_wraps_an_unclassified_owner_error_and_preserves_a_classified_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    unclassified = TrafficlabError("unclassified owner error", corrective_action="repair source evidence")

    def raise_unclassified(*_args: object, **_kwargs: object) -> object:
        raise unclassified

    monkeypatch.setattr(auditor, "_audit", raise_unclassified)
    with pytest.raises(TrafficlabError) as first:
        auditor.audit_bundle(candidate, repository=repository)
    first_outcome = first.value.failure_outcome
    assert first.value is unclassified
    assert first_outcome is not None
    assert (
        first_outcome.kind,
        first_outcome.affected_evidence,
        first_outcome.corrective_action,
        first_outcome.authority,
    ) == ("artifact_corrupt", "candidate evidence", "repair source evidence", "primary")

    classified_outcome = FailureOutcome(
        kind="artifact_missing",
        stage="fit",
        detail="classified owner error",
        affected_evidence="best_model.json",
        evidence_state="not_published",
        corrective_action="restore best model",
        authority="primary",
    )
    classified = TrafficlabError("classified owner error", corrective_action="restore best model")
    classified.failure_outcomes = (classified_outcome,)
    classified.failure_outcome = classified_outcome

    def raise_classified(*_args: object, **_kwargs: object) -> object:
        raise classified

    monkeypatch.setattr(auditor, "_audit", raise_classified)
    with pytest.raises(TrafficlabError) as second:
        auditor.audit_bundle(candidate, repository=repository)
    assert second.value is classified
    assert second.value.failure_outcomes == (classified_outcome,)


def test_offline_bundle_fixture_carries_complete_phase7_evidence_and_reconstructs_it(tmp_path: Path) -> None:
    """A retained candidate distinguishes training, fresh simulation, and independent held-out evidence."""
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    before = _candidate_bytes(candidate)
    index = json.loads((candidate / "index.json").read_text(encoding="utf-8"))

    assert index["schema_version"] == 2
    assert set(index) == {
        "environment",
        "fresh_simulation",
        "held_out",
        "lineage",
        "ownership",
        "prerequisites",
        "protocol",
        "report",
        "report_inputs",
        "schema_version",
        "training",
    }
    expected_training = {(workload, repeat) for workload in ("short", "streaming", "bursty") for repeat in (1, 2, 3)}
    training = index["training"]
    assert {(item["workload"], item["repeat"]) for item in training} == expected_training
    assert {(item["workload"], item["repeat"]) for item in index["fresh_simulation"]} == expected_training
    assert {item["workload"] for item in index["held_out"]} == {"short", "streaming", "bursty"}

    training_reference_identities = {item["reference_identity"]["sha256"] for item in training}
    assert len(training_reference_identities) == len(expected_training)
    assert all(
        json.loads((candidate / item["directory"] / "record.json").read_text(encoding="utf-8"))["reference_identity"][
            "sha256"
        ]
        not in training_reference_identities
        for item in index["held_out"]
    )
    for item in training:
        directory = candidate / item["directory"]
        lines = (directory / "run.log").read_bytes().splitlines(keepends=True)
        assert lines
        assert all(
            line
            == json.dumps(
                json.loads(line.decode("utf-8")),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
            for line in lines
        )

    result = auditor.audit_bundle(candidate, repository=repository)

    assert result.file_count == len(before) - 1
    assert _candidate_bytes(candidate) == before


def test_validation_fixture_generator_rejects_nonhex_source_identities() -> None:
    with pytest.raises(ValueError, match="source identities"):
        fixture_generator.generate_fixture_tree(source_commit="z" * 40, source_tree="f" * 40)


@pytest.mark.parametrize(
    ("source_commit", "source_tree", "accepted"),
    (
        ("a" * 40, "b" * 40, True),
        ("z" * 40, "b" * 40, False),
        ("a" * 40, "z" * 40, False),
        ("0" * 40, "b" * 40, False),
        ("a" * 40, "0" * 40, False),
    ),
)
def test_validation_fixture_source_identity_guard_has_exact_acceptance_boundaries(
    source_commit: str,
    source_tree: str,
    accepted: bool,
) -> None:
    if accepted:
        fixture_generator.validate_source_identities(source_commit, source_tree)
    else:
        with pytest.raises(ValueError, match="source identities"):
            fixture_generator.validate_source_identities(source_commit, source_tree)


def test_validation_fixture_generator_check_rebuilds_the_retained_bytes() -> None:
    assert fixture_generator.main(["--check"]) == 0


def test_validation_fixture_retains_the_complete_155_file_evidence_inventory() -> None:
    assert len(_candidate_bytes(_ROOT / "tests" / "fixtures" / "validation_study_candidate")) == 155


def test_checked_study_result_uses_canonical_fresh_simulation_records() -> None:
    content = (_ROOT / "examples" / "validation_study" / "results.json").read_bytes()
    result = study.parse_study_results(content, repository_root=_ROOT)

    assert b'"fresh_simulation"' in content
    assert b'"held_out"' not in content
    assert study.render_study_results(result) == content


def test_study_held_out_evaluator_requires_an_independent_reference_and_uses_the_fixed_training_model() -> None:
    """The study-only boundary evaluates a frozen training model without weakening ordinary stage lineage checks."""
    fixture = _FIT_FIXTURE
    config = load_configuration_pair(fixture / "experiment.toml").realized
    metadata = parse_capture_metadata(_CAPTURE_BYTES, source=fixture / "capture.json")
    original = parse_pcapng_bytes(_REFERENCE_BYTES, metadata, source=fixture / "reference.pcapng")
    independent = tuple(
        TraceEvent(event.timestamp, event.direction, event.frame_length + (1 if index == 1 else 0))
        for index, event in enumerate(original)
    )
    independent_bytes = encode_pcapng(independent, metadata)

    result = study.evaluate_study_held_out(
        model_content=(fixture / "best_model.json").read_bytes(),
        model_source=fixture / "best_model.json",
        config=config,
        capture_content=_CAPTURE_BYTES,
        capture_source=fixture / "capture.json",
        reference_content=independent_bytes,
        reference_source=Path("held_out/reference.pcapng"),
    )

    comparison = parse_comparison_result(result.comparison_json)
    assert result.seed == 97
    assert result.reference_identity.sha256 != result.training_model.reference_identity.sha256
    assert comparison.input_identities is not None
    assert result.generated_identity == comparison.input_identities["generated_pcapng"]
    assert tuple(comparison.methods) == study.PUBLISHED_METHOD_ORDER

    with pytest.raises(TrafficlabError, match="independent held-out reference"):
        study.evaluate_study_held_out(
            model_content=(fixture / "best_model.json").read_bytes(),
            model_source=fixture / "best_model.json",
            config=config,
            capture_content=_CAPTURE_BYTES,
            capture_source=fixture / "capture.json",
            reference_content=_REFERENCE_BYTES,
            reference_source=fixture / "reference.pcapng",
        )

    with pytest.raises(TypeError, match="ExperimentConfig"):
        study.evaluate_study_held_out(
            model_content=(fixture / "best_model.json").read_bytes(),
            model_source=fixture / "best_model.json",
            config=cast(Any, object()),
            capture_content=_CAPTURE_BYTES,
            capture_source=fixture / "capture.json",
            reference_content=independent_bytes,
            reference_source=Path("held_out/reference.pcapng"),
        )

    with pytest.raises(TrafficlabError, match="final seed"):
        study.evaluate_study_held_out(
            model_content=(fixture / "best_model.json").read_bytes(),
            model_source=fixture / "best_model.json",
            config=config.model_copy(update={"run": config.run.model_copy(update={"final_seed": 98})}),
            capture_content=_CAPTURE_BYTES,
            capture_source=fixture / "capture.json",
            reference_content=independent_bytes,
            reference_source=Path("held_out/reference.pcapng"),
        )

    different_window = tuple(
        TraceEvent(
            event.timestamp + (1.0 if index == len(independent) - 1 else 0.0), event.direction, event.frame_length
        )
        for index, event in enumerate(independent)
    )
    with pytest.raises(TrafficlabError, match="reference window"):
        study.evaluate_study_held_out(
            model_content=(fixture / "best_model.json").read_bytes(),
            model_source=fixture / "best_model.json",
            config=config,
            capture_content=_CAPTURE_BYTES,
            capture_source=fixture / "capture.json",
            reference_content=encode_pcapng(different_window, metadata),
            reference_source=Path("held_out/different-window.pcapng"),
        )


def test_retained_prerequisite_codec_freezes_all_output_identities_and_aggregates_production_junit() -> None:
    """Runner, generator, and auditor share one exact retained prerequisite contract."""
    url = "https://downloads.example.test/object.bin"
    study_id = "fixture-study"
    outputs = {
        "docker_matrix": {
            "stdout": b"docker passed\n",
            "stderr": b"",
            "junit": b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/><testsuite tests="2" failures="0" errors="0" skipped="0"/></testsuites>',
        },
        "internet_smoke": {
            "stdout": b"internet passed\n",
            "stderr": b"",
            "junit": b'<testsuite tests="1" failures="0" errors="0" skipped="0"/>',
        },
    }
    commands: list[dict[str, object]] = []
    for kind in ("docker_matrix", "internet_smoke"):
        values = outputs[kind]
        argv = list(study.prerequisite_command_argv(kind, study_id=study_id, url=url))
        tests = study.prerequisite_junit_counts(values["junit"])
        commands.append(
            {
                "argv": argv,
                "command": {
                    "identity": identify_bytes(
                        json.dumps({"argv": argv}, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
                    ).as_dict(),
                    "path": f"prerequisites/{kind}.command.json",
                },
                "exit_status": 0,
                "junit": {
                        "identity": identify_bytes(values["junit"]).as_dict(),
                    "path": f"prerequisites/{kind}.junit.xml",
                },
                "kind": kind,
                "status": {
                    "identity": identify_bytes(
                        json.dumps({"exit_status": 0, "tests": tests}, sort_keys=True, separators=(",", ":")).encode(
                            "utf-8"
                        )
                        + b"\n"
                    ).as_dict(),
                    "path": f"prerequisites/{kind}.status.json",
                },
                "stderr": {
                        "identity": identify_bytes(values["stderr"]).as_dict(),
                    "path": f"prerequisites/{kind}.stderr",
                },
                "stdout": {
                        "identity": identify_bytes(values["stdout"]).as_dict(),
                    "path": f"prerequisites/{kind}.stdout",
                },
                "tests": tests,
            }
        )
    document = {
        "commands": commands,
        "environment": {
            "capture_image_id": f"sha256:{'d' * 64}",
            "capture_image_reference": f"trafficlab-capture@sha256:{'c' * 64}",
            "capture_tool_version": "4.0.17",
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "target_image_id": f"sha256:{study.TARGET_REFERENCE.rsplit(':', 1)[-1]}",
            "target_image_reference": study.TARGET_REFERENCE,
            "uv_lock_identity": identify_bytes(b"locked\n").as_dict(),
        },
        "schema_version": 3,
        "study_id": study_id,
        "url": url,
    }

    rendered = study.render_retained_prerequisites(document)
    parsed = study.parse_retained_prerequisites(rendered)

    assert study.render_retained_prerequisites(parsed) == rendered
    commands = cast(list[dict[str, object]], parsed["commands"])
    assert commands[0]["tests"] == {"errors": 0, "failed": 0, "passed": 3, "skipped": 0, "total": 3}


def test_offline_auditor_binds_the_environment_to_the_relocated_git_and_image_locks(tmp_path: Path) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path, generated=True)
    environment_path = candidate / "environment.json"
    environment = cast(dict[str, object], json.loads(environment_path.read_text(encoding="utf-8")))
    environment["source_commit"] = "b" * 40
    environment["capture_image_id"] = f"sha256:{'e' * 64}"
    _write_canonical_json(environment_path, environment)
    _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", "environment", "not_published", "primary")


def test_complete_fixture_freezes_training_model_selection_and_bidirectional_variation(tmp_path: Path) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path, generated=True)
    protocol = cast(dict[str, object], json.loads((candidate / "protocol.json").read_text(encoding="utf-8")))
    report_inputs = cast(
        dict[str, object], json.loads((candidate / "report_inputs.json").read_text(encoding="utf-8"))
    )

    selection = cast(dict[str, object], protocol["model_selection"])
    assert selection["rule"] == "highest_best_fitness_then_lowest_repeat"
    assert {cast(dict[str, object], value)["workload"] for value in cast(list[object], selection["selected"])} == {
        "short",
        "streaming",
        "bursty",
    }
    for row in cast(list[object], report_inputs["natural_variation"]):
        document = cast(dict[str, object], row)
        assert set(document) == {"pairs", "symmetric_mean", "workload"}
        for pair in cast(list[object], document["pairs"]):
            assert set(cast(dict[str, object], pair)) == {
                "forward",
                "left_repeat",
                "reverse",
                "right_repeat",
                "symmetric_mean",
            }

    assert auditor.audit_bundle(candidate, repository=repository).bundle == candidate


def test_simultaneous_evidence_mismatches_preserve_the_first_complete_primary_and_all_inventories(
    tmp_path: Path,
) -> None:
    repository, candidate = _copy_validation_study_candidate(tmp_path, generated=True)
    missing = candidate / "training" / "short" / "r1" / "best_model.json"
    missing.unlink()
    (candidate / "training" / "short" / "r1" / "checkpoint.json").write_bytes(b"corrupt\n")
    (candidate / "foreign.bin").write_bytes(b"foreign\n")
    (candidate / "training" / "short" / "r1" / "generated.pcapng").write_bytes(
        (candidate / "training" / "short" / "r2" / "generated.pcapng").read_bytes()
    )
    candidate_before = _candidate_bytes(candidate)
    destination = repository / "examples" / "validation_study" / "evidence" / "simultaneous"

    with pytest.raises(TrafficlabError) as error:
        study.publish_audited_bundle(candidate, "simultaneous", repository_root=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.detail,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.corrective_action,
        outcome.authority,
    ) == (
        "artifact_missing",
        "publication",
        "training/short/r1/best_model.json is missing from the retained bundle",
        "training/short/r1/best_model.json",
        "not_published",
        "restore the exact retained artifact",
        "primary",
    )
    assert _candidate_bytes(candidate) == candidate_before
    assert not destination.exists()
    assert not tuple(repository.rglob("*.tmp"))
