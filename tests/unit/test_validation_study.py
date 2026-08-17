from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import platform
import shutil
import socket
import stat
import subprocess
import tomllib
from collections.abc import Callable, Sequence
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
from trafficlab import USER_AGENT
from trafficlab.artifacts import append_run_log
from trafficlab.capture import CaptureResult
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.comparison import ComparisonResult, compare_experiment, compare_traces, parse_comparison_result
from trafficlab.compatibility import ContentIdentity, identify_bytes
from trafficlab.config import ExperimentConfig, GenerationLimits, SimilarityConfig
from trafficlab.config_io import load_configuration_pair, render_effective_config
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
_CAPTURE_DOCKERFILE = (_ROOT / "docker" / "capture" / "Dockerfile").read_bytes()
_CAPTURE_SCRIPT = (_ROOT / "docker" / "capture" / "capture.sh").read_bytes()
_CAPTURE_IMAGE_LOCK = json.loads((_ROOT / "docker" / "capture" / "image-lock.json").read_text(encoding="utf-8"))
_CAPTURE_IMAGE_ID = cast(str, _CAPTURE_IMAGE_LOCK["expected_capture_image_id"])
_STUDY_PHASE_CAPTURE_TAG = "trafficlab-validation-study-1:study-capture"
_COLLECTION_PHASE_CAPTURE_TAG = "trafficlab-validation-study-1:collection-capture"
_PRE_USER_AGENT_R6_FIXTURE = _ROOT / "tests" / "fixtures" / "validation_study_pre_user_agent_r6"


def _write_prerequisite_repository_inputs(repository_root: Path) -> None:
    capture_root = repository_root / "docker" / "capture"
    capture_root.mkdir(parents=True)
    for name in ("Dockerfile", "capture.sh", "image-lock.json"):
        shutil.copy2(_ROOT / "docker" / "capture" / name, capture_root / name)
    shutil.copy2(_ROOT / "uv.lock", repository_root / "uv.lock")


def _install_pre_user_agent_r6_predecessor(repository_root: Path) -> tuple[Path, bytes, dict[str, str]]:
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


class _ScriptedPrerequisiteRunner:
    def __init__(self, repository_root: Path, mutation: str = "happy", *, study_id: str = "study-1") -> None:
        self.root = repository_root
        self.mutation = mutation
        self.study_id = study_id
        self.url = "https://downloads.example.test/object.bin"
        self.final_url = "https://cdn.example.test/object.bin"
        self.target_id = f"sha256:{'b' * 64}"
        self.capture_id = _CAPTURE_IMAGE_ID
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
        self.git_trees: dict[str, bytes] = {}
        self.ignored_worktree_paths: frozenset[str] = frozenset()
        self.ignored_worktree_protocol = "valid"
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
        input: bytes | None = None,
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
        if command[:2] == ("git", "rev-parse") and len(command) == 3 and command[2] in self.git_trees:
            return subprocess.CompletedProcess(command, 0, stdout=self.git_trees[command[2]], stderr=b"")
        if command in identities:
            status, stdout, stderr = identities[command]
            return subprocess.CompletedProcess(command, status, stdout=stdout, stderr=stderr)
        if command == ("git", "check-ignore", "-z", "--stdin"):
            return self._check_ignored_worktree_paths(command, input=input)
        if command == ("docker", "image", "inspect", study.TARGET_REFERENCE):
            return self._inspect_target(command)
        if command[:2] == ("docker", "build"):
            return self._build_capture(command)
        if command == ("docker", "image", "rm", "--force", f"trafficlab-validation-{self.study_id}:capture"):
            return self._remove_capture_image(command)
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
        assert command == study.cold_capture_build_argv(  # pyright: ignore[reportPrivateUsage]
            f"trafficlab-validation-{self.study_id}:capture",
            self.evidence / "capture.iid",
        )
        if self.mutation != "capture-iid-missing":
            iid = "trafficlab-capture:local" if self.mutation == "capture-iid-tag" else self.capture_id
            (self.evidence / "capture.iid").write_text(f"{iid}\n", encoding="ascii")
        if self.mutation == "preexisting-cid":
            (self.evidence / "capability.cid").write_text(f"{self.container_id}\n", encoding="ascii")
        return subprocess.CompletedProcess(command, 0, stdout=b"built\n", stderr=b"")

    def _remove_capture_image(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
        if self.mutation in {"capture-image-cleanup-failed", "docker-matrix-failed-cleanup-failed"}:
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"cleanup failed\n")
        return subprocess.CompletedProcess(command, 0, stdout=b"removed\n", stderr=b"")

    def _check_ignored_worktree_paths(
        self,
        command: tuple[str, ...],
        *,
        input: bytes | None,
    ) -> subprocess.CompletedProcess[bytes]:
        if input is None or not input.endswith(b"\0"):
            return subprocess.CompletedProcess(command, 2, stdout=b"", stderr=b"missing NUL input\n")
        paths = tuple(record.decode("utf-8") for record in input[:-1].split(b"\0"))
        if self.ignored_worktree_protocol == "nonzero":
            return subprocess.CompletedProcess(command, 2, stdout=b"", stderr=b"synthetic ignore failure\n")
        if self.ignored_worktree_protocol == "truncated":
            return subprocess.CompletedProcess(command, 0, stdout=b"foreign", stderr=b"")
        if self.ignored_worktree_protocol == "nonempty-no-match":
            return subprocess.CompletedProcess(command, 1, stdout=b"foreign\0", stderr=b"")
        if self.ignored_worktree_protocol == "empty-match":
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        matches = tuple(path for path in paths if path in self.ignored_worktree_paths)
        stdout = b"".join(path.encode("utf-8") + b"\0" for path in matches)
        return subprocess.CompletedProcess(command, 0 if matches else 1, stdout=stdout, stderr=b"")

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
        if kind == "docker" and self.mutation in {"docker-matrix-failed", "docker-matrix-failed-cleanup-failed"}:
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


def _valid_prerequisite(*, study_id: str = "study-1") -> study.PrerequisiteResults:
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
        "--capture-image",
        f"trafficlab-validation-{study_id}:capture",
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
        "--capture-image",
        f"trafficlab-validation-{study_id}:capture",
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
                "docker_engine_version": "27.0.0",
                "docker_compose_version": "2.29.0",
                "host_architecture": "x86_64",
                "kernel_release": "test-kernel",
                "platform": "Linux-test",
                "python_implementation": "CPython",
                "python_version": "3.12.3",
                "trafficlab_version": "0.1.0",
                "uv_lock_sha256": _HASH,
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
        original_fsync = study._commit_prerequisite_fsync  # pyright: ignore[reportPrivateUsage]
        calls = 0

        def fail_second_config(destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("simulated config publication failure")
            original_fsync(destination)

        monkeypatch.setattr(study, "_commit_prerequisite_fsync", fail_second_config)


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
    metadata = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert USER_AGENT == f"{metadata['name']}/{metadata['version']} (+{metadata['urls']['Repository']})"
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
        "--user-agent",
        USER_AGENT,
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
    capability_argv = study._expected_capability_argv("study-1", url)  # pyright: ignore[reportPrivateUsage]
    capability_user_agent = capability_argv.index("--user-agent")
    assert capability_argv[capability_user_agent : capability_user_agent + 2] == ("--user-agent", USER_AGENT)

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
        "--capture-image",
        f"trafficlab-validation-{study_id}:capture",
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
        "--capture-image",
        f"trafficlab-validation-{study_id}:capture",
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
    _write_prerequisite_repository_inputs(repository_root)
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
        "host_architecture": study.platform.machine(),
        "kernel_release": study.platform.release(),
        "platform": study.platform.platform(),
        "python_implementation": "CPython",
        "uv_lock_sha256": hashlib.sha256((_ROOT / "uv.lock").read_bytes()).hexdigest(),
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
        (
            "git",
            "check-ignore",
            "-z",
            "--stdin",
        ),
        ("docker", "version", "--format", "{{.Server.Version}}"),
        ("docker", "compose", "version", "--short"),
        ("docker", "image", "pull", study.TARGET_REFERENCE),
        ("docker", "image", "inspect", study.TARGET_REFERENCE),
        study.cold_capture_build_argv(  # pyright: ignore[reportPrivateUsage]
            f"trafficlab-validation-{runner.study_id}:capture",
            runner.evidence / "capture.iid",
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
        ("docker", "image", "rm", "--force", f"trafficlab-validation-{runner.study_id}:capture"),
    ]
    assert [timeout for _command, timeout in runner.calls] == [
        20.0,
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
        300.0,
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


def test_prerequisite_rotation_preserves_the_one_checked_pre_user_agent_r6_predecessor(
    tmp_path: Path,
) -> None:
    """Only the retained r6 raw evidence can bridge the short-lived no-User-Agent format."""

    repository_root = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository_root)
    canonical, predecessor_content, source = _install_pre_user_agent_r6_predecessor(repository_root)
    assert identify_bytes(predecessor_content).as_dict() == {
        "sha256": "a6cb727911ad19333c2faffa09e7f8e246750c8524b04c8cac13f3402672d275",
        "size": 5662,
    }
    with pytest.raises(ValueError, match="capability argv"):
        study.parse_prerequisite_results(predecessor_content, repository_root=repository_root)

    runner = _ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    runner.git_trees[f"{source['git_commit']}^{{tree}}"] = f"{source['git_tree']}\n".encode("ascii")
    result = study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )

    published = canonical.read_bytes()
    parsed = study.parse_prerequisite_results(published, repository_root=repository_root)
    captured_live_argv = next(
        command
        for command, _timeout in runner.calls
        if command[:2] == ("docker", "run") and f"trafficlab-validation-study-capability-{runner.study_id}" in command
    )
    projected_argv = list(captured_live_argv)
    projected_argv[8] = str((runner.evidence / "capability.cid").relative_to(repository_root))
    projected_argv[12] = f"type=bind,src={runner.mount.relative_to(repository_root)},dst=/trafficlab-study"

    assert parsed == result
    assert cast(tuple[str, ...], parsed.capability["argv"]) == tuple(projected_argv)


def test_prerequisite_rotation_recreates_the_checked_r6_archive_when_the_legacy_root_lacks_one(
    tmp_path: Path,
) -> None:
    """The exact predecessor remains recoverable when its original raw archive was not yet retained."""

    repository_root = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository_root)
    canonical, predecessor_content, source = _install_pre_user_agent_r6_predecessor(repository_root)
    archive = canonical.parent / ".study-work" / "attempts" / source["study_id"] / "prerequisites.raw.json"
    archive.unlink()
    runner = _ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    runner.git_trees[f"{source['git_commit']}^{{tree}}"] = f"{source['git_tree']}\n".encode("ascii")

    study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )

    assert archive.read_bytes() == predecessor_content


def test_prerequisite_rotation_rejects_an_arbitrary_pre_user_agent_schema_one_predecessor(tmp_path: Path) -> None:
    """A synthetic schema-1 projection cannot opt in to the r6-only rotation exception."""

    repository_root = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository_root)
    prior_runner = _ScriptedPrerequisiteRunner(repository_root, study_id="study-r6")
    study.run_prerequisites(
        prior_runner.url,
        prior_runner.study_id,
        repository_root=repository_root,
        runner=prior_runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )
    study_root = repository_root / "examples" / "validation_study"
    canonical = study_root / "prerequisites.json"
    prior_archive = study_root / ".study-work" / "attempts" / prior_runner.study_id / "prerequisites.raw.json"
    prior_marker = prior_archive.with_name("prerequisites-success.json")
    legacy = cast(dict[str, object], json.loads(canonical.read_text(encoding="utf-8")))
    capability = cast(dict[str, object], legacy["capability"])
    argv = cast(list[str], capability["argv"])
    user_agent = argv.index("--user-agent")
    del argv[user_agent : user_agent + 2]
    legacy_content = json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    canonical.write_bytes(legacy_content)
    prior_archive.write_bytes(legacy_content)
    marker = cast(dict[str, object], json.loads(prior_marker.read_text(encoding="utf-8")))
    marker["prerequisites_identity"] = study.identify_bytes(legacy_content).as_dict()
    prior_marker.write_bytes(json.dumps(marker, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")

    runner = _ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    with pytest.raises(TrafficlabError, match="preserved pre-User-Agent r6 predecessor"):
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    assert canonical.read_bytes() == legacy_content


def test_prerequisite_rotation_rejects_an_unreadable_retained_r6_evidence_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I/O failures while pinning the fixed retained evidence remain a canonical rotation rejection."""

    repository_root = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository_root)
    canonical, _predecessor_content, source = _install_pre_user_agent_r6_predecessor(repository_root)
    evidence = canonical.parent / ".study-work" / "evidence" / source["study_id"] / "00-prerequisites"
    runner = _ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    runner.git_trees[f"{source['git_commit']}^{{tree}}"] = f"{source['git_tree']}\n".encode("ascii")
    original_iterdir = Path.iterdir

    def fail_preserved_evidence_iterdir(path: Path) -> Any:
        if path == evidence:
            raise OSError("simulated retained evidence read failure")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_preserved_evidence_iterdir)
    before = canonical.read_bytes()
    with pytest.raises(TrafficlabError, match="preserved pre-User-Agent r6 predecessor"):
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    assert canonical.read_bytes() == before


@pytest.mark.parametrize("mutation", ("study_id", "url", "source", "tree", "raw", "marker", "evidence"))
def test_prerequisite_rotation_rejects_each_mutation_of_the_preserved_r6_predecessor(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Every identity component of the exact compatibility bridge remains independently pinned."""

    repository_root = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository_root)
    canonical, predecessor_content, source = _install_pre_user_agent_r6_predecessor(repository_root)
    attempt = canonical.parent / ".study-work" / "attempts" / source["study_id"]
    evidence = canonical.parent / ".study-work" / "evidence" / source["study_id"] / "00-prerequisites"
    runner = _ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    runner.git_trees[f"{source['git_commit']}^{{tree}}"] = f"{source['git_tree']}\n".encode("ascii")

    if mutation in {"study_id", "url", "source"}:
        document = cast(dict[str, object], json.loads(predecessor_content))
        document[mutation if mutation != "source" else "git_commit"] = (
            "study-r6"
            if mutation == "study_id"
            else "https://example.test/other.bin"
            if mutation == "url"
            else "0" * 40
        )
        canonical.write_bytes(json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
    elif mutation == "tree":
        runner.git_trees[f"{source['git_commit']}^{{tree}}"] = b"0" * 40 + b"\n"
    elif mutation == "raw":
        canonical.write_bytes(predecessor_content + b" ")
    elif mutation == "marker":
        (attempt / "prerequisites-success.json").write_bytes(b"{}\n")
    else:
        (evidence / "capability.headers").write_bytes(b"mutated retained evidence\n")

    before = canonical.read_bytes()
    with pytest.raises(TrafficlabError, match="preserved pre-User-Agent r6 predecessor"):
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    assert canonical.read_bytes() == before


def test_prerequisites_remove_the_shared_capture_tag_after_a_guarded_test_failure(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository_root)
    runner = _ScriptedPrerequisiteRunner(repository_root, "docker-matrix-failed")

    with pytest.raises(TrafficlabError, match="docker_matrix guarded pytest failed"):
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    commands = [command for command, _timeout in runner.calls]
    shared_tag = f"trafficlab-validation-{runner.study_id}:capture"
    assert commands.count(study.cold_capture_build_argv(shared_tag, runner.evidence / "capture.iid")) == 1  # pyright: ignore[reportPrivateUsage]
    assert commands[-1] == ("docker", "image", "rm", "--force", shared_tag)


def test_prerequisite_cleanup_does_not_replace_its_guarded_test_failure(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository_root)
    runner = _ScriptedPrerequisiteRunner(repository_root, "docker-matrix-failed-cleanup-failed")

    with pytest.raises(TrafficlabError, match="docker_matrix guarded pytest failed") as captured:
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    assert captured.value.__notes__ == [
        "prerequisite capture image cleanup failed: could not remove owned prerequisite capture image: cleanup failed"
    ]
    assert [command for command, _timeout in runner.calls][-1] == (
        "docker",
        "image",
        "rm",
        "--force",
        f"trafficlab-validation-{runner.study_id}:capture",
    )


def test_prerequisites_preserve_an_arbitrary_primary_when_shared_capture_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interruption keeps ownership cleanup as an ordered secondary diagnostic."""

    class ControlledAbort(BaseException):
        pass

    repository_root = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository_root)
    runner = _ScriptedPrerequisiteRunner(repository_root, "capture-image-cleanup-failed")

    def abort(*_args: object, **_kwargs: object) -> study.JsonObject:
        raise ControlledAbort("controlled abort")

    monkeypatch.setattr(study, "_run_prerequisite_test", abort)
    with pytest.raises(ControlledAbort) as captured:
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    assert captured.value.__notes__ == [
        "prerequisite capture image cleanup failed: could not remove owned prerequisite capture image: cleanup failed"
    ]
    assert [command for command, _timeout in runner.calls][-1] == (
        "docker",
        "image",
        "rm",
        "--force",
        f"trafficlab-validation-{runner.study_id}:capture",
    )


@pytest.mark.parametrize("entry_kind", ("regular", "symlink", "fifo"))
@pytest.mark.parametrize(
    ("protocol", "expected"),
    (
        ("valid", "ignored prerequisite worktree entry is not permitted"),
        ("truncated", "ignored prerequisite paths must be terminal NUL-delimited"),
        ("nonempty-no-match", "ignored prerequisite paths must be empty for no-match status"),
        ("empty-match", "ignored prerequisite paths must be nonempty for match status"),
        ("nonzero", "could not resolve ignored prerequisite paths"),
    ),
)
def test_prerequisites_reject_local_exclude_ignored_worktree_entries_before_docker(
    tmp_path: Path,
    entry_kind: str,
    protocol: str,
    expected: str,
) -> None:
    """Ignored source entries use the same strict Git-NUL boundary as accepted evidence."""

    if entry_kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("nonregular FIFO entries require POSIX")
    repository_root = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository_root)
    relative = f"locally-excluded-{entry_kind}"
    entry = repository_root / relative
    exclude = repository_root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(f"{relative}\n", encoding="utf-8")
    if entry_kind == "regular":
        entry.write_text("ignored foreign source\n", encoding="utf-8")
    elif entry_kind == "symlink":
        entry.symlink_to("source.py")
    else:
        os.mkfifo(entry)
    runner = _ScriptedPrerequisiteRunner(repository_root)
    runner.ignored_worktree_paths = frozenset({relative})
    runner.ignored_worktree_protocol = protocol

    with pytest.raises(TrafficlabError, match=expected):
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    commands = [command for command, _timeout in runner.calls]
    assert any(command == ("git", "check-ignore", "-z", "--stdin") for command in commands)
    assert not any(command[:2] == ("docker", "version") for command in commands)
    assert (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / runner.study_id
        / "prerequisites.json"
    ).is_file()


def test_prerequisites_do_not_publish_success_when_shared_capture_cleanup_fails(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository_root)
    runner = _ScriptedPrerequisiteRunner(repository_root, "capture-image-cleanup-failed")

    with pytest.raises(TrafficlabError, match="remove owned prerequisite capture image"):
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    assert not (repository_root / "examples" / "validation_study" / "prerequisites.json").exists()
    assert [command for command, _timeout in runner.calls][-1] == (
        "docker",
        "image",
        "rm",
        "--force",
        f"trafficlab-validation-{runner.study_id}:capture",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "dirty-tree",
        "wrong-python",
        "target-digest-absent",
        "capture-iid-tag",
        "capture-iid-missing",
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
    _write_prerequisite_repository_inputs(repository_root)
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
            "capture-iid-missing": (("docker", "container", "inspect"),),
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


def test_prerequisites_wrap_invalid_study_id_without_attempt_preservation(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()

    with pytest.raises(TrafficlabError, match="prerequisite validation failed"):
        study.run_prerequisites(
            "https://downloads.example.test/object.bin",
            "INVALID_ID",
            repository_root=repository_root,
            runner=_StudyIdentityRunner(repository_root),
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    assert not (repository_root / "examples" / "validation_study" / ".study-work").exists()


def test_capability_normal_exit_proves_exact_full_id_and_anchored_name_absent(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository_root)
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
    _write_prerequisite_repository_inputs(repository_root)
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
    _write_prerequisite_repository_inputs(repository_root)
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
        input: bytes | None = None,
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
        input: bytes | None = None,
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
        input: bytes | None = None,
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
        input: bytes | None = None,
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
    _write_prerequisite_repository_inputs(repository_root)
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
        capture_image_id: str = _CAPTURE_IMAGE_ID,
        target_config_user: str = "",
        dirty: bool = False,
        capture_image_present: bool = True,
        owned_capture_tags: set[str] | None = None,
        build_exit_status: int = 0,
        write_build_iid: bool = True,
        build_iid_content: str | None = None,
        inspected_capture_image_id: str | None = None,
        cleanup_exit_status: int = 0,
        on_target_inspect: Callable[[], None] | None = None,
    ) -> None:
        self.root = repository_root
        self.target_image_id = target_image_id
        self.capture_image_id = capture_image_id
        self.target_config_user = target_config_user
        self.target_repo_digests: tuple[str, ...] = (study.TARGET_REFERENCE,)
        self.docker_engine_version = "27.0.0"
        self.docker_compose_version = "2.29.0"
        self.dirty = dirty
        self.capture_image_present = capture_image_present
        self.owned_capture_tags: set[str] = set() if owned_capture_tags is None else set(owned_capture_tags)
        self.build_exit_status = build_exit_status
        self.write_build_iid = write_build_iid
        self.build_iid_content = build_iid_content
        self.inspected_capture_image_id = inspected_capture_image_id
        self.cleanup_exit_status = cleanup_exit_status
        self.on_target_inspect = on_target_inspect
        self.calls: list[tuple[str, ...]] = []
        self.capture_image_cleanup_tags: list[str] = []

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
        del timeout
        command = tuple(argv)
        assert cwd == self.root
        assert check is False
        assert capture_output is True
        assert shell is False
        self.calls.append(command)
        if command[:2] == ("docker", "build"):
            iidfile = Path(command[command.index("--iidfile") + 1])
            self.owned_capture_tags.add(command[command.index("--tag") + 1])
            if self.write_build_iid:
                iidfile.write_text(
                    f"{self.capture_image_id if self.build_iid_content is None else self.build_iid_content}\n",
                    encoding="ascii",
                )
            if self.build_exit_status == 0:
                self.capture_image_present = True
            return subprocess.CompletedProcess(
                command,
                self.build_exit_status,
                stdout=b"built\n" if self.build_exit_status == 0 else b"",
                stderr=b"" if self.build_exit_status == 0 else b"simulated build failure\n",
            )
        if command[:4] == ("docker", "image", "rm", "--force"):
            self.capture_image_cleanup_tags.append(command[4])
            if self.cleanup_exit_status == 0:
                self.capture_image_present = False
                self.owned_capture_tags.discard(command[4])
            return subprocess.CompletedProcess(
                command,
                self.cleanup_exit_status,
                stdout=b"removed\n" if self.cleanup_exit_status == 0 else b"",
                stderr=b"" if self.cleanup_exit_status == 0 else b"simulated cleanup failure\n",
            )
        if (
            len(command) == 6
            and command[:3] == ("docker", "image", "inspect")
            and command[3].startswith("trafficlab-validation-")
            and command[4:] == ("--format", "{{.Id}}")
        ):
            present = command[3] in self.owned_capture_tags
            return subprocess.CompletedProcess(
                command,
                0 if present else 1,
                stdout=f"{self.capture_image_id}\n".encode() if present else b"",
                stderr=b"" if present else b"not present\n",
            )
        if (
            len(command) == 6
            and command[:3] == ("docker", "image", "inspect")
            and command[3].startswith("sha256:")
            and command[4:] == ("--format", "{{.Id}}")
        ):
            inspected_capture_image_id = self.inspected_capture_image_id or self.capture_image_id
            return subprocess.CompletedProcess(
                command,
                0 if self.capture_image_present else 1,
                stdout=f"{inspected_capture_image_id}\n".encode() if self.capture_image_present else b"",
                stderr=b"" if self.capture_image_present else b"not present\n",
            )
        if (
            command
            in {
                ("docker", "image", "inspect", self.capture_image_id),
                (
                    "docker",
                    "image",
                    "inspect",
                    self.capture_image_id,
                    "--format",
                    "{{.Id}}",
                ),
            }
            and not self.capture_image_present
        ):
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"not present\n")
        if command == ("docker", "image", "inspect", study.TARGET_REFERENCE) and self.on_target_inspect is not None:
            self.on_target_inspect()
        outputs: dict[tuple[str, ...], bytes] = {
            ("git", "rev-parse", "HEAD"): b"c" * 40 + b"\n",
            ("git", "rev-parse", "HEAD^{tree}"): b"e" * 40 + b"\n",
            ("git", "status", "--porcelain=v1", "--untracked-files=all"): b" M source.py\n" if self.dirty else b"",
            ("docker", "version", "--format", "{{.Server.Version}}"): f"{self.docker_engine_version}\n".encode(),
            ("docker", "compose", "version", "--short"): f"{self.docker_compose_version}\n".encode(),
            ("docker", "image", "inspect", study.TARGET_REFERENCE): json.dumps(
                [
                    {
                        "Id": self.target_image_id,
                        "RepoDigests": list(self.target_repo_digests),
                        "Config": {"User": self.target_config_user},
                    }
                ]
            ).encode(),
            ("docker", "image", "inspect", self.capture_image_id): json.dumps([{"Id": self.capture_image_id}]).encode(),
            (
                "docker",
                "image",
                "inspect",
                self.capture_image_id,
                "--format",
                "{{.Id}}",
            ): f"{self.capture_image_id}\n".encode(),
        }
        if command not in outputs:
            raise AssertionError(f"unexpected study command: {command!r}")
        return subprocess.CompletedProcess(command, 0, stdout=outputs[command], stderr=b"")


def _write_study_inputs(repository_root: Path) -> tuple[Path, study.StudyResults]:
    repository_root.mkdir()
    prerequisite, _contents = _write_checked_configs(repository_root, capture_image_id=_CAPTURE_IMAGE_ID)
    images = cast(study.JsonObject, study._thaw_json(prerequisite.images))  # pyright: ignore[reportPrivateUsage]
    images["capture_image_id"] = _CAPTURE_IMAGE_ID
    prerequisite = _write_retained_prerequisite_evidence(repository_root, replace(prerequisite, images=_frozen(images)))
    capture_root = repository_root / "docker" / "capture"
    shutil.copy2(_ROOT / "docker" / "capture" / "image-lock.json", capture_root / "image-lock.json")
    prerequisite_path = repository_root / "examples" / "validation_study" / "prerequisites.json"
    prerequisite_path.write_bytes(study.render_prerequisite_results(prerequisite))
    document = _valid_result_document(repository_root)
    return prerequisite_path, _result_value(document)


def _write_collection_compatible_inputs(repository_root: Path) -> Path:
    """Write retained inputs that bind the local revalidation boundary exactly."""

    repository_root.mkdir()
    shutil.copy2(_ROOT / "uv.lock", repository_root / "uv.lock")
    prerequisite, _contents = _write_checked_configs(repository_root, capture_image_id=_CAPTURE_IMAGE_ID)
    tools = cast(study.JsonObject, study._thaw_json(prerequisite.tools))  # pyright: ignore[reportPrivateUsage]
    tools.update(
        {
            "host_architecture": platform.machine(),
            "kernel_release": platform.release(),
            "platform": platform.platform(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "uv_lock_sha256": hashlib.sha256((repository_root / "uv.lock").read_bytes()).hexdigest(),
        }
    )
    images = cast(study.JsonObject, study._thaw_json(prerequisite.images))  # pyright: ignore[reportPrivateUsage]
    images["capture_image_id"] = _CAPTURE_IMAGE_ID
    prerequisite = replace(prerequisite, tools=_frozen(tools), images=_frozen(images))
    prerequisite = _write_retained_prerequisite_evidence(repository_root, prerequisite)
    capture_root = repository_root / "docker" / "capture"
    shutil.copy2(_ROOT / "docker" / "capture" / "image-lock.json", capture_root / "image-lock.json")
    prerequisite_path = repository_root / "examples" / "validation_study" / "prerequisites.json"
    prerequisite_path.write_bytes(study.render_prerequisite_results(prerequisite))
    return prerequisite_path


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
    assert runner.calls[:6] == [
        ("git", "rev-parse", "HEAD"),
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        ("docker", "version", "--format", "{{.Server.Version}}"),
        ("docker", "compose", "version", "--short"),
        ("docker", "image", "inspect", study.TARGET_REFERENCE),
        ("docker", "image", "inspect", _STUDY_PHASE_CAPTURE_TAG, "--format", "{{.Id}}"),
    ]
    build = runner.calls[6]
    assert build[:2] == ("docker", "build")
    assert build[build.index("--tag") + 1] == _STUDY_PHASE_CAPTURE_TAG
    assert Path(build[build.index("--iidfile") + 1]).name == "capture.iid"
    assert build[-1] == "docker/capture"
    assert runner.calls[7:] == [
        ("docker", "image", "inspect", _CAPTURE_IMAGE_ID, "--format", "{{.Id}}"),
        ("docker", "image", "inspect", _CAPTURE_IMAGE_ID),
        ("docker", "image", "rm", "--force", _STUDY_PHASE_CAPTURE_TAG),
    ]
    for order, run_id, workload, repeat in study.PRIMARY_ORDER:
        record = result.runs[order - 1]
        assert record.key == {"workload": workload, "repeat": repeat}
        assert record.config_path == f"runs/validation_study/study-1/realized-configs/{run_id}.toml"
        assert record.run_directory == f"runs/validation_study/study-1/{run_id}"
        assert record.transfer_evidence_directory.endswith(f"/study-1/{run_id}")


@pytest.mark.parametrize("outcome", ("success", "failure", "interrupt"))
def test_public_study_rebuilds_and_cleans_a_no_residue_capture_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """The legacy study owner leases one cold lock-checked image through all primary runs."""

    repository_root = tmp_path / "repository"
    prerequisite_path, expected = _write_study_inputs(repository_root)
    events: list[str] = []
    _install_primary_orchestration_doubles(monkeypatch, expected, events)
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        capture_image_present=False,
    )
    run_calls: list[Path] = []

    def run(path: Path) -> RunResult:
        assert runner.capture_image_present
        run_calls.append(path)
        if outcome == "failure":
            raise TrafficlabError("controlled study failure", corrective_action="preserve the run")
        if outcome == "interrupt":
            raise KeyboardInterrupt()
        return cast(RunResult, object())

    def invoke() -> study.StudyResults:
        return study.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=run,
            runner=runner,
            perf_counter=iter(float(value) for value in range(20)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    if outcome == "success":
        result = invoke()
        assert len(result.runs) == 9
        assert result.environment["capture_image_id"] == _CAPTURE_IMAGE_ID
    elif outcome == "failure":
        with pytest.raises(TrafficlabError, match="controlled study failure"):
            invoke()
    else:
        with pytest.raises(KeyboardInterrupt):
            invoke()

    tag = _STUDY_PHASE_CAPTURE_TAG
    build = next(command for command in runner.calls if command[:2] == ("docker", "build"))
    assert build[build.index("--tag") + 1] == tag
    iidfile = Path(build[build.index("--iidfile") + 1])
    assert not iidfile.exists()
    assert runner.capture_image_cleanup_tags == [tag]
    assert not runner.capture_image_present
    assert len(run_calls) == (9 if outcome == "success" else 1)
    for workload in ("short", "streaming", "bursty"):
        config = study.load_experiment(
            repository_root / "examples" / "validation_study" / "configs" / f"{workload}.toml"
        )
        assert config.capture.image == _CAPTURE_IMAGE_ID


def test_public_study_fails_when_owned_image_cleanup_fails_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed legacy study remains failed if its phase-owned image cannot be removed."""

    repository_root = tmp_path / "repository"
    prerequisite_path, expected = _write_study_inputs(repository_root)
    events: list[str] = []
    _install_primary_orchestration_doubles(monkeypatch, expected, events)
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        capture_image_present=False,
        cleanup_exit_status=1,
    )

    with pytest.raises(TrafficlabError, match="study capture image cleanup failed"):
        study.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=lambda _path: cast(RunResult, object()),
            runner=runner,
            perf_counter=iter(float(value) for value in range(20)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    assert runner.capture_image_cleanup_tags == [_STUDY_PHASE_CAPTURE_TAG]


def test_public_study_preserves_a_primary_base_exception_when_owned_image_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup becomes an ordered secondary diagnostic without replacing an unexpected primary."""

    class ControlledAbort(BaseException):
        pass

    repository_root = tmp_path / "repository"
    prerequisite_path, expected = _write_study_inputs(repository_root)
    events: list[str] = []
    _install_primary_orchestration_doubles(monkeypatch, expected, events)
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        capture_image_present=False,
        cleanup_exit_status=1,
    )

    def abort(_path: Path) -> RunResult:
        raise ControlledAbort("controlled abort")

    with pytest.raises(ControlledAbort) as captured:
        study.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=abort,
            runner=runner,
            perf_counter=iter(float(value) for value in range(20)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    assert captured.value.__notes__ == [
        "study capture image cleanup failed: could not remove owned study capture image: simulated cleanup failure"
    ]
    assert runner.capture_image_cleanup_tags == [_STUDY_PHASE_CAPTURE_TAG]


@pytest.mark.parametrize("mode", ("non-owned", "missing-iid", "missing-prerequisites"))
def test_validated_study_inputs_covers_owned_image_preconditions(
    tmp_path: Path,
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The study input boundary preserves its non-owned and owned-IID validation paths."""

    repository_root = tmp_path / "repository"
    prerequisite_path, _expected = _write_study_inputs(repository_root)
    runner = _StudyIdentityRunner(repository_root, capture_image_id=_CAPTURE_IMAGE_ID)
    retained = study.parse_prerequisite_results(prerequisite_path.read_bytes(), repository_root=repository_root)
    tools = retained.tools
    identity = cast(
        study.JsonObject,
        {
            "git_commit": retained.git_commit,
            "python_version": tools["python_version"],
            "trafficlab_version": tools["trafficlab_version"],
            "docker_engine_version": tools["docker_engine_version"],
            "docker_compose_version": tools["docker_compose_version"],
            "platform": tools["platform"],
        },
    )

    def current_identity(*, repository_root: Path, runner: study.CommandRunner) -> study.JsonObject:
        del repository_root, runner
        return identity

    monkeypatch.setattr(study, "_study_identity", current_identity)

    if mode == "non-owned":
        prerequisites, configs, actual_identity, content = study._validated_study_inputs(  # pyright: ignore[reportPrivateUsage]
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            runner=runner,
        )
        assert prerequisites.study_id == "study-1"
        assert tuple(configs) == ("short", "streaming", "bursty")
        assert actual_identity["git_commit"] == retained.git_commit
        assert content == prerequisite_path.read_bytes()
    elif mode == "missing-iid":
        with pytest.raises(ValueError, match="study capture IID file is required"):
            study._validated_study_inputs(  # pyright: ignore[reportPrivateUsage]
                "https://downloads.example.test/object.bin",
                "study-1",
                prerequisite_path,
                repository_root=repository_root,
                runner=runner,
                owned_capture_image=study._PhaseCaptureImage("trafficlab-validation-study-1:capture"),  # pyright: ignore[reportPrivateUsage]
            )
    else:
        prerequisite_path.unlink()
        with pytest.raises(ValueError, match="could not read Validation Study prerequisites"):
            study._validated_study_inputs(  # pyright: ignore[reportPrivateUsage]
                "https://downloads.example.test/object.bin",
                "study-1",
                prerequisite_path,
                repository_root=repository_root,
                runner=runner,
            )


def test_phase_capture_image_reports_iid_cleanup_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A temporary IID-file cleanup failure remains an actionable owner-boundary error."""

    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    iidfile = tmp_path / "capture.iid"
    runner = _StudyIdentityRunner(repository_root, capture_image_id=_CAPTURE_IMAGE_ID)
    original_unlink = Path.unlink

    def fail_iid_unlink(self: Path, *, missing_ok: bool = False) -> None:
        if self == iidfile:
            raise OSError("simulated IID cleanup failure")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_iid_unlink)
    with pytest.raises(ValueError, match="could not remove study capture IID file"):
        study._establish_phase_capture_image(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            phase="study",
            expected_image_id=_CAPTURE_IMAGE_ID,
            capture_lock_image_id=_CAPTURE_IMAGE_ID,
            owned_capture_image=study._PhaseCaptureImage("trafficlab-validation-study-1:capture"),  # pyright: ignore[reportPrivateUsage]
            iidfile=iidfile,
            runner=runner,
        )
    assert iidfile.exists()


def test_phase_capture_image_preserves_build_failure_when_iid_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IID cleanup remains secondary when a cold build has already failed."""

    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    iidfile = tmp_path / "capture.iid"
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        build_exit_status=1,
    )
    original_unlink = Path.unlink

    def fail_iid_unlink(self: Path, *, missing_ok: bool = False) -> None:
        if self == iidfile:
            raise OSError("simulated IID cleanup failure")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_iid_unlink)
    with pytest.raises(ValueError, match="could not cold-build study capture image") as captured:
        study._establish_phase_capture_image(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            phase="study",
            expected_image_id=_CAPTURE_IMAGE_ID,
            capture_lock_image_id=_CAPTURE_IMAGE_ID,
            owned_capture_image=study._PhaseCaptureImage("trafficlab-validation-study-1:capture"),  # pyright: ignore[reportPrivateUsage]
            iidfile=iidfile,
            runner=runner,
        )
    assert captured.value.__notes__ == [
        "study capture IID file cleanup failed: could not remove study capture IID file "
        f"{iidfile}: simulated IID cleanup failure"
    ]
    assert iidfile.exists()


@pytest.mark.parametrize("mismatch", ("target", "lock"))
def test_public_study_validates_immutable_inputs_before_cold_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    """A bad retained lock or live target cannot create a study-owned capture tag."""

    repository_root = tmp_path / "repository"
    prerequisite_path, expected = _write_study_inputs(repository_root)
    events: list[str] = []
    _install_primary_orchestration_doubles(monkeypatch, expected, events)
    if mismatch == "lock":
        lock_path = repository_root / "docker" / "capture" / "image-lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["expected_capture_image_id"] = f"sha256:{'8' * 64}"
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        target_image_id=f"sha256:{'8' * 64}" if mismatch == "target" else _IMAGE_ID,
        capture_image_present=False,
    )
    runs: list[Path] = []

    def must_not_run(path: Path) -> RunResult:
        runs.append(path)
        raise AssertionError("immutable validation reached a study primary")

    with pytest.raises(TrafficlabError, match="Validation Study failed validation"):
        study.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=must_not_run,
            runner=runner,
            perf_counter=iter(float(value) for value in range(20)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    assert runs == []
    assert not [command for command in runner.calls if command[:2] == ("docker", "build")]
    assert runner.capture_image_cleanup_tags == []
    if mismatch == "target":
        assert ("docker", "image", "inspect", study.TARGET_REFERENCE) in runner.calls


def test_public_study_rejects_a_conflicting_phase_capture_tag_before_any_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy study route never adopts or removes a stale phase tag."""

    repository_root = tmp_path / "repository"
    prerequisite_path, expected = _write_study_inputs(repository_root)
    events: list[str] = []
    _install_primary_orchestration_doubles(monkeypatch, expected, events)
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        capture_image_present=False,
        owned_capture_tags={_STUDY_PHASE_CAPTURE_TAG},
    )
    runs: list[Path] = []

    def must_not_run(path: Path) -> RunResult:
        runs.append(path)
        raise AssertionError("conflicting phase tag reached a study primary")

    with pytest.raises(TrafficlabError, match="study capture image tag already exists"):
        study.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=must_not_run,
            runner=runner,
            perf_counter=iter(float(value) for value in range(20)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    assert runs == []
    assert not [command for command in runner.calls if command[:2] == ("docker", "build")]
    assert runner.capture_image_cleanup_tags == []
    assert _STUDY_PHASE_CAPTURE_TAG in runner.owned_capture_tags


@pytest.mark.parametrize(
    ("build_exit_status", "write_build_iid", "build_iid_content", "inspected_capture_image_id"),
    (
        (1, True, None, None),
        (0, False, None, None),
        (0, True, "not-an-image-id", None),
        (0, True, f"sha256:{'8' * 64}", None),
        (0, True, None, f"sha256:{'8' * 64}"),
    ),
)
def test_public_study_rejects_invalid_cold_build_before_any_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_exit_status: int,
    write_build_iid: bool,
    build_iid_content: str | None,
    inspected_capture_image_id: str | None,
) -> None:
    """Every cold-build/IID identity failure stops the legacy study before a primary run."""

    repository_root = tmp_path / "repository"
    prerequisite_path, expected = _write_study_inputs(repository_root)
    events: list[str] = []
    _install_primary_orchestration_doubles(monkeypatch, expected, events)
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        capture_image_present=False,
        build_exit_status=build_exit_status,
        write_build_iid=write_build_iid,
        build_iid_content=build_iid_content,
        inspected_capture_image_id=inspected_capture_image_id,
    )
    runs: list[Path] = []

    def must_not_run(path: Path) -> RunResult:
        runs.append(path)
        raise AssertionError("invalid cold build reached a study primary")

    with pytest.raises(TrafficlabError, match="Validation Study failed validation"):
        study.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=must_not_run,
            runner=runner,
            perf_counter=iter(float(value) for value in range(20)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    assert runs == []
    assert runner.capture_image_cleanup_tags == [_STUDY_PHASE_CAPTURE_TAG]


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
        input: bytes | None = None,
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
        input: bytes | None = None,
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


def test_collect_cli_uses_only_frozen_prerequisite_inputs_and_the_candidate_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    inputs: study.CollectionInputs = (
        {"frozen": "environment"},
        b"frozen prerequisites\n",
        {},
        {},
        4_194_304,
    )
    calls: list[dict[str, object]] = []

    def load_inputs(*_args: object, **_kwargs: object) -> study.CollectionInputs:
        return inputs

    def collect(**kwargs: object) -> Path:
        calls.append(kwargs)
        return repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"

    monkeypatch.setattr(study, "_collection_inputs_from_prerequisites", load_inputs, raising=False)
    monkeypatch.setattr(study, "collect_validation_candidate", collect)

    assert (
        study.main(
            [
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/prerequisites.json",
            ],
            repository_root=repository_root,
            runner=_StudyIdentityRunner(repository_root),
        )
        == 0
    )
    assert calls == [
        {
            "repository_root": repository_root,
            "study_id": "study-1",
            "url": "https://downloads.example.test/object.bin",
            "attempt": repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1",
            "environment": inputs[0],
            "retained_prerequisites": inputs[1],
            "prerequisite_files": inputs[2],
            "configs": inputs[3],
            "run": run_experiment,
            "capture": study.capture_experiment,
            "object_size_bytes": 4_194_304,
            "perf_counter": study.time.perf_counter,
        }
    ]
    assert "candidate collected" in capsys.readouterr().out


def test_collect_cli_rejects_an_in_repository_noncanonical_prerequisite_path_before_loading_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the frozen canonical prerequisite path can begin collection."""

    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    calls: list[str] = []

    def should_not_load(*_args: object, **_kwargs: object) -> study.CollectionInputs:
        calls.append("inputs")
        raise AssertionError("noncanonical prerequisite path reached collection inputs")

    def should_not_collect(**_kwargs: object) -> Path:
        calls.append("collect")
        raise AssertionError("noncanonical prerequisite path reached candidate collection")

    monkeypatch.setattr(study, "_collection_inputs_from_prerequisites", should_not_load)
    monkeypatch.setattr(study, "collect_validation_candidate", should_not_collect)

    assert (
        study.main(
            [
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/other.json",
            ],
            repository_root=repository_root,
            runner=_StudyIdentityRunner(repository_root),
        )
        == 2
    )
    assert calls == []
    assert not (repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1").exists()
    assert not (repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1").exists()


def test_prerequisites_cli_publishes_the_canonical_prerequisite_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The public prerequisite command reports its canonical retained path after success."""

    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    calls: list[tuple[str, str, Path]] = []

    class Result:
        study_id = "study-1"

    def prerequisites(
        url: str,
        study_id: str,
        *,
        repository_root: Path,
        **_kwargs: object,
    ) -> study.PrerequisiteResults:
        calls.append((url, study_id, repository_root))
        return cast(study.PrerequisiteResults, Result())

    monkeypatch.setattr(study, "run_prerequisites", prerequisites)

    assert (
        study.main(
            ["prerequisites", "--url", "https://downloads.example.test/object.bin", "--study-id", "study-1"],
            repository_root=repository_root,
            runner=_StudyIdentityRunner(repository_root),
        )
        == 0
    )
    assert calls == [("https://downloads.example.test/object.bin", "study-1", repository_root)]
    assert str(repository_root / "examples" / "validation_study" / "prerequisites.json") in capsys.readouterr().out


@pytest.mark.parametrize("relative", (True, False))
def test_publish_cli_resolves_relative_and_absolute_candidate_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: bool,
) -> None:
    """The publish command resolves only relative candidates against the supplied repository root."""

    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    candidate = repository_root / "candidate"
    calls: list[tuple[Path, str, Path]] = []

    def publish(candidate_path: Path, study_id: str, *, repository_root: Path) -> Path:
        calls.append((candidate_path, study_id, repository_root))
        return repository_root / "examples" / "validation_study" / "evidence" / study_id

    monkeypatch.setattr(study, "publish_audited_bundle", publish)
    argument = Path("candidate") if relative else candidate

    assert (
        study.main(
            ["publish", "--candidate", str(argument), "--study-id", "study-1"],
            repository_root=repository_root,
            runner=_StudyIdentityRunner(repository_root),
        )
        == 0
    )
    assert calls == [(candidate, "study-1", repository_root)]


def test_collection_inputs_revalidates_current_checked_inputs(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path = _write_collection_compatible_inputs(repository_root)

    environment, retained, files, configs, object_size_bytes = study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        prerequisite_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        runner=_StudyIdentityRunner(repository_root, capture_image_id=_CAPTURE_IMAGE_ID),
    )

    parsed = cast(study.JsonObject, study.parse_retained_prerequisites(retained))
    retained_environment = cast(study.JsonObject, parsed["environment"])
    assert retained_environment["capture_image_id"] == _CAPTURE_IMAGE_ID
    assert environment["source_commit"] == "c" * 40
    assert environment["source_tree"] == "e" * 40
    assert set(files) == {
        "prerequisites/docker_matrix.command.json",
        "prerequisites/docker_matrix.junit.xml",
        "prerequisites/docker_matrix.status.json",
        "prerequisites/docker_matrix.stderr",
        "prerequisites/docker_matrix.stdout",
        "prerequisites/internet_smoke.command.json",
        "prerequisites/internet_smoke.junit.xml",
        "prerequisites/internet_smoke.status.json",
        "prerequisites/internet_smoke.stderr",
        "prerequisites/internet_smoke.stdout",
        "headers/prerequisites/00-prerequisites/capability.headers",
    }
    assert tuple(configs) == ("short", "streaming", "bursty")
    assert object_size_bytes == 4_194_304
    with pytest.raises(TrafficlabError, match="collection Git tree must remain exactly clean"):
        study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            prerequisite_path,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            runner=_StudyIdentityRunner(repository_root, capture_image_id=_CAPTURE_IMAGE_ID, dirty=True),
        )
    (repository_root / "uv.lock").unlink()
    with pytest.raises(TrafficlabError, match="Validation Study collection inputs are invalid"):
        study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            prerequisite_path,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            runner=_StudyIdentityRunner(repository_root, capture_image_id=_CAPTURE_IMAGE_ID),
        )


@pytest.mark.parametrize("outcome", ("success", "failure", "interrupt"))
def test_public_collection_rebuilds_and_cleans_a_no_residue_capture_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """Public collection owns a fresh lock-checked image without relying on a prerequisite cache tag."""

    repository_root = tmp_path / "repository"
    prerequisite_path = _write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        capture_image_present=False,
    )
    calls: list[Path] = []

    def collect(**_kwargs: object) -> Path:
        calls.append(repository_root)
        if outcome == "failure":
            raise TrafficlabError("controlled collection failure", corrective_action="preserve the attempt")
        if outcome == "interrupt":
            raise KeyboardInterrupt()
        return repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"

    monkeypatch.setattr(study, "collect_validation_candidate", collect)
    argv = (
        "collect",
        "--url",
        "https://downloads.example.test/object.bin",
        "--study-id",
        "study-1",
        "--prerequisites",
        "examples/validation_study/prerequisites.json",
    )
    if outcome == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            study.main(argv, repository_root=repository_root, runner=runner)
    else:
        assert study.main(argv, repository_root=repository_root, runner=runner) == (0 if outcome == "success" else 2)

    attempt = repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1"
    tag = _COLLECTION_PHASE_CAPTURE_TAG
    assert study.cold_capture_build_argv(tag, attempt / "collection-capture.iid") in runner.calls  # pyright: ignore[reportPrivateUsage]
    assert runner.capture_image_cleanup_tags == [tag]
    assert not runner.capture_image_present
    assert not (attempt / "collection-capture.iid").exists()
    assert calls == [repository_root]


def test_public_collection_rejects_a_conflicting_phase_capture_tag_before_candidate_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale phase tag is not overwritten or treated as collection-owned state."""

    repository_root = tmp_path / "repository"
    prerequisite_path = _write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        capture_image_present=False,
        owned_capture_tags={_COLLECTION_PHASE_CAPTURE_TAG},
    )
    calls: list[str] = []

    def must_not_collect(**_kwargs: object) -> Path:
        calls.append("candidate")
        raise AssertionError("conflicting phase tag reached candidate collection")

    monkeypatch.setattr(study, "collect_validation_candidate", must_not_collect)
    assert (
        study.main(
            (
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/prerequisites.json",
            ),
            repository_root=repository_root,
            runner=runner,
        )
        == 2
    )
    assert calls == []
    assert not [command for command in runner.calls if command[:2] == ("docker", "build")]
    assert runner.capture_image_cleanup_tags == []
    assert _COLLECTION_PHASE_CAPTURE_TAG in runner.owned_capture_tags


@pytest.mark.parametrize(
    ("build_exit_status", "write_build_iid", "build_iid_content", "inspected_capture_image_id"),
    (
        (1, True, None, None),
        (0, False, None, None),
        (0, True, "not-an-image-id", None),
        (0, True, f"sha256:{'8' * 64}", None),
        (0, True, None, f"sha256:{'8' * 64}"),
    ),
)
def test_public_collection_rejects_invalid_cold_build_before_candidate_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_exit_status: int,
    write_build_iid: bool,
    build_iid_content: str | None,
    inspected_capture_image_id: str | None,
) -> None:
    """A failed, missing, malformed, or mismatched cold build cannot begin collection."""

    repository_root = tmp_path / "repository"
    prerequisite_path = _write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        capture_image_present=False,
        build_exit_status=build_exit_status,
        write_build_iid=write_build_iid,
        build_iid_content=build_iid_content,
        inspected_capture_image_id=inspected_capture_image_id,
    )
    calls: list[str] = []

    def must_not_collect(**_kwargs: object) -> Path:
        calls.append("candidate")
        raise AssertionError("invalid cold build reached candidate collection")

    monkeypatch.setattr(study, "collect_validation_candidate", must_not_collect)
    assert (
        study.main(
            (
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/prerequisites.json",
            ),
            repository_root=repository_root,
            runner=runner,
        )
        == 2
    )
    attempt = repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1"
    assert calls == []
    assert runner.capture_image_cleanup_tags == [_COLLECTION_PHASE_CAPTURE_TAG]
    assert not (attempt / "collection-capture.iid").exists()


@pytest.mark.parametrize("mismatch", ("target", "lock"))
def test_public_collection_validates_immutable_inputs_before_cold_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    """Collection refuses a bad live target or retained lock before candidate creation."""

    repository_root = tmp_path / "repository"
    prerequisite_path = _write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    if mismatch == "lock":
        lock_path = repository_root / "docker" / "capture" / "image-lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["expected_capture_image_id"] = f"sha256:{'8' * 64}"
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        target_image_id=f"sha256:{'8' * 64}" if mismatch == "target" else _IMAGE_ID,
        capture_image_present=False,
    )
    calls: list[str] = []

    def must_not_collect(**_kwargs: object) -> Path:
        calls.append("candidate")
        raise AssertionError("immutable validation reached candidate collection")

    monkeypatch.setattr(study, "collect_validation_candidate", must_not_collect)
    assert (
        study.main(
            (
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/prerequisites.json",
            ),
            repository_root=repository_root,
            runner=runner,
        )
        == 2
    )
    assert calls == []
    assert not [command for command in runner.calls if command[:2] == ("docker", "build")]
    assert runner.capture_image_cleanup_tags == []
    if mismatch == "target":
        assert ("docker", "image", "inspect", study.TARGET_REFERENCE) in runner.calls


@pytest.mark.parametrize("failure", ("config", "retained-artifact"))
def test_public_collection_defers_cold_build_until_all_retained_inputs_validate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Late immutable config/evidence failures must not create a capture-image lease."""

    repository_root = tmp_path / "repository"
    prerequisite_path = _write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    if failure == "config":
        config_path = repository_root / "examples" / "validation_study" / "configs" / "short.toml"
        config_path.write_bytes(config_path.read_bytes() + b"\n# retained config mutation\n")
        runner = _StudyIdentityRunner(
            repository_root,
            capture_image_id=_CAPTURE_IMAGE_ID,
            capture_image_present=False,
        )
    else:
        capability_header = (
            repository_root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "evidence"
            / "study-1"
            / "00-prerequisites"
            / "capability.headers"
        )

        def mutate_retained_header() -> None:
            capability_header.write_bytes(b"retained evidence changed after its initial validation\n")

        runner = _StudyIdentityRunner(
            repository_root,
            capture_image_id=_CAPTURE_IMAGE_ID,
            capture_image_present=False,
            on_target_inspect=mutate_retained_header,
        )
    candidates: list[str] = []
    runs: list[Path] = []
    captures: list[Path] = []

    def must_not_collect(**_kwargs: object) -> Path:
        candidates.append("candidate")
        raise AssertionError("late retained validation reached candidate collection")

    def must_not_run(path: Path) -> RunResult:
        runs.append(path)
        raise AssertionError("late retained validation reached a training run")

    def must_not_capture(path: Path) -> CaptureResult:
        captures.append(path)
        raise AssertionError("late retained validation reached a held-out capture")

    monkeypatch.setattr(study, "collect_validation_candidate", must_not_collect)
    assert (
        study.main(
            (
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/prerequisites.json",
            ),
            repository_root=repository_root,
            runner=runner,
            run=must_not_run,
            capture=must_not_capture,
        )
        == 2
    )
    assert candidates == []
    assert runs == []
    assert captures == []
    assert not [command for command in runner.calls if command[:2] == ("docker", "build")]
    assert runner.capture_image_cleanup_tags == []


def test_public_collection_fails_when_owned_image_cleanup_fails_after_candidate_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful candidate collection is not reported when its phase-owned image leaks."""

    repository_root = tmp_path / "repository"
    prerequisite_path = _write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        capture_image_present=False,
        cleanup_exit_status=1,
    )
    calls: list[str] = []

    def collect(**_kwargs: object) -> Path:
        calls.append("candidate")
        return repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"

    monkeypatch.setattr(study, "collect_validation_candidate", collect)
    assert (
        study.main(
            (
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/prerequisites.json",
            ),
            repository_root=repository_root,
            runner=runner,
        )
        == 2
    )
    assert calls == ["candidate"]
    assert runner.capture_image_cleanup_tags == [_COLLECTION_PHASE_CAPTURE_TAG]


@pytest.mark.parametrize("primary_kind", ("trafficlab", "base"))
def test_public_collection_preserves_primary_when_owned_image_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primary_kind: str,
) -> None:
    """Cleanup is a secondary diagnostic for both expected and arbitrary collection primaries."""

    class ControlledAbort(BaseException):
        pass

    repository_root = tmp_path / "repository"
    prerequisite_path = _write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        capture_image_present=False,
        cleanup_exit_status=1,
    )
    primary: BaseException
    if primary_kind == "trafficlab":
        primary = TrafficlabError("controlled collection failure", corrective_action="preserve the attempt")
    else:
        primary = ControlledAbort("controlled abort")

    def abort_collection(**_kwargs: object) -> Path:
        raise primary

    monkeypatch.setattr(study, "collect_validation_candidate", abort_collection)
    argv = (
        "collect",
        "--url",
        "https://downloads.example.test/object.bin",
        "--study-id",
        "study-1",
        "--prerequisites",
        "examples/validation_study/prerequisites.json",
    )
    if primary_kind == "trafficlab":
        assert study.main(argv, repository_root=repository_root, runner=runner) == 2
    else:
        with pytest.raises(ControlledAbort) as captured:
            study.main(argv, repository_root=repository_root, runner=runner)
        assert captured.value is primary

    assert primary.__notes__ == [
        "collection capture image cleanup failed: "
        "could not remove owned collection capture image: simulated cleanup failure"
    ]
    assert runner.capture_image_cleanup_tags == [_COLLECTION_PHASE_CAPTURE_TAG]


def test_phase_capture_tags_are_explicitly_disjoint() -> None:
    """Study and collection must never contend for the same temporary capture tag."""

    assert study._phase_capture_tag("study-1", "study") == "trafficlab-validation-study-1:study-capture"  # pyright: ignore[reportPrivateUsage]
    assert study._phase_capture_tag("study-1", "collection") == "trafficlab-validation-study-1:collection-capture"  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("route", "owned_tag", "foreign_tag"),
    (
        (
            "study",
            "trafficlab-validation-study-1:study-capture",
            "trafficlab-validation-study-1:collection-capture",
        ),
        (
            "collection",
            "trafficlab-validation-study-1:collection-capture",
            "trafficlab-validation-study-1:study-capture",
        ),
    ),
)
def test_public_phase_does_not_adopt_or_remove_another_phase_capture_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    owned_tag: str,
    foreign_tag: str,
) -> None:
    """A stale tag owned by the other public phase neither blocks nor leaks into this phase."""

    repository_root = tmp_path / "repository"
    runner = _StudyIdentityRunner(
        repository_root,
        capture_image_id=_CAPTURE_IMAGE_ID,
        capture_image_present=False,
        owned_capture_tags={foreign_tag},
    )
    if route == "study":
        prerequisite_path, expected = _write_study_inputs(repository_root)
        events: list[str] = []
        _install_primary_orchestration_doubles(monkeypatch, expected, events)

        def stop_primary(_path: Path) -> RunResult:
            raise TrafficlabError("controlled study primary", corrective_action="preserve the study")

        with pytest.raises(TrafficlabError, match="controlled study primary"):
            study.run_study(
                "https://downloads.example.test/object.bin",
                "study-1",
                prerequisite_path,
                repository_root=repository_root,
                run=stop_primary,
                runner=runner,
                perf_counter=iter(float(value) for value in range(20)).__next__,
                utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
            )
    else:
        prerequisite_path = _write_collection_compatible_inputs(repository_root)
        study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            prerequisite_content=prerequisite_path.read_bytes(),
        )

        def stop_collection(**_kwargs: object) -> Path:
            raise TrafficlabError("controlled collection primary", corrective_action="preserve the attempt")

        monkeypatch.setattr(study, "collect_validation_candidate", stop_collection)
        assert (
            study.main(
                (
                    "collect",
                    "--url",
                    "https://downloads.example.test/object.bin",
                    "--study-id",
                    "study-1",
                    "--prerequisites",
                    "examples/validation_study/prerequisites.json",
                ),
                repository_root=repository_root,
                runner=runner,
            )
            == 2
        )

    build = next(command for command in runner.calls if command[:2] == ("docker", "build"))
    assert build[build.index("--tag") + 1] == owned_tag
    assert runner.capture_image_cleanup_tags == [owned_tag]
    assert foreign_tag in runner.owned_capture_tags
    assert owned_tag not in runner.owned_capture_tags


def test_collection_inputs_rejects_legacy_image_lock_before_capture_revalidation(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path = _write_collection_compatible_inputs(repository_root)
    image_lock = repository_root / "docker" / "capture" / "image-lock.json"
    image_lock.write_text(
        json.dumps(
            {
                "base_digest": f"sha256:{'a' * 64}",
                "base_reference": "docker.io/library/debian@sha256:" + "b" * 64,
                "capture_tool_version": "4.0.17",
                "debian_snapshot": "20260816T000000Z",
                "direct_packages": ["tshark"],
                "expected_capture_image_id": _CAPTURE_IMAGE_ID,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(TrafficlabError, match="image lock"):
        study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            prerequisite_path,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            runner=_StudyIdentityRunner(repository_root, capture_image_id=_CAPTURE_IMAGE_ID),
        )


def test_collection_inputs_rejects_changed_target_metadata_before_candidate_creation(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path = _write_collection_compatible_inputs(repository_root)

    with pytest.raises(TrafficlabError, match="target image"):
        study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            prerequisite_path,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            runner=_StudyIdentityRunner(
                repository_root,
                capture_image_id=_CAPTURE_IMAGE_ID,
                target_config_user="unexpected",
            ),
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "host_architecture",
        "kernel_release",
        "platform",
        "python_implementation",
        "python_version",
        "uv_lock",
        "docker_engine_version",
        "docker_compose_version",
        "target_image_id",
        "target_repo_digests",
        "target_config_user",
        "capture_image_id",
    ),
)
def test_collection_inputs_rejects_each_live_environment_mismatch_before_candidate_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    """Collection revalidates every retained host, tool, and image identity before capture."""

    repository_root = tmp_path / "repository"
    prerequisite_path = _write_collection_compatible_inputs(repository_root)
    runner = _StudyIdentityRunner(repository_root, capture_image_id=_CAPTURE_IMAGE_ID)
    if mutation == "host_architecture":
        monkeypatch.setattr(study.platform, "machine", lambda: "other-architecture")
    elif mutation == "kernel_release":
        monkeypatch.setattr(study.platform, "release", lambda: "other-kernel")
    elif mutation == "platform":
        monkeypatch.setattr(study.platform, "platform", lambda: "Other-platform")
    elif mutation == "python_implementation":
        monkeypatch.setattr(study.platform, "python_implementation", lambda: "OtherPython")
    elif mutation == "python_version":
        monkeypatch.setattr(study.platform, "python_version", lambda: "0.0.0")
    elif mutation == "uv_lock":
        (repository_root / "uv.lock").write_bytes(b"changed checked lock\n")
    elif mutation == "docker_engine_version":
        runner.docker_engine_version = "28.0.0"
    elif mutation == "docker_compose_version":
        runner.docker_compose_version = "3.0.0"
    elif mutation == "target_image_id":
        runner.target_image_id = f"sha256:{'9' * 64}"
    elif mutation == "target_repo_digests":
        runner.target_repo_digests = ("curlimages/curl@sha256:" + "f" * 64,)
    elif mutation == "target_config_user":
        runner.target_config_user = "unexpected"
    else:
        runner.capture_image_id = f"sha256:{'8' * 64}"

    with pytest.raises(TrafficlabError):
        study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            prerequisite_path,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            runner=runner,
        )

    assert not (repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1").exists()


def test_collection_binds_retained_prerequisite_before_creating_a_candidate(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path = _write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    environment, retained, files, configs, object_size_bytes = study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        prerequisite_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        runner=_StudyIdentityRunner(repository_root, capture_image_id=_CAPTURE_IMAGE_ID),
    )
    mismatched = study.parse_retained_prerequisites(retained)
    mismatched["study_id"] = "other-study"
    for command in cast(list[dict[str, object]], mismatched["commands"]):
        kind = cast(str, command["kind"])
        argv = list(
            study.prerequisite_command_argv(
                kind, study_id="other-study", url="https://downloads.example.test/object.bin"
            )
        )
        command["argv"] = argv
        command_record = cast(dict[str, object], command["command"])
        command_record["identity"] = identify_bytes(
            json.dumps({"argv": argv}, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        ).as_dict()
    mismatched_content = study.render_retained_prerequisites(mismatched)
    attempt = study._begin_phase_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        phase="collection",
    )
    calls: list[Path] = []

    def should_not_run(path: Path) -> RunResult:
        calls.append(path)
        raise AssertionError("mismatched retained prerequisite must stop before training")

    candidate = repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"
    with pytest.raises(TrafficlabError, match="retained prerequisite"):
        study.collect_validation_candidate(
            repository_root=repository_root,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            attempt=attempt,
            environment=environment,
            retained_prerequisites=mismatched_content,
            prerequisite_files=files,
            configs=configs,
            run=should_not_run,
            object_size_bytes=object_size_bytes,
        )

    assert calls == []
    assert not candidate.exists()
    assert (
        repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1" / "collection.json"
    ).is_file()


def test_collection_persists_its_phase_marker_before_later_object_validation(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path = _write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    environment, retained, files, configs, _object_size_bytes = study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        prerequisite_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        runner=_StudyIdentityRunner(repository_root, capture_image_id=_CAPTURE_IMAGE_ID),
    )
    attempt = study._begin_phase_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        phase="collection",
    )
    candidate = repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"
    marker = (
        repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1" / "collection.json"
    )

    with pytest.raises(TrafficlabError, match="object size"):
        study.collect_validation_candidate(
            repository_root=repository_root,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            attempt=attempt,
            environment=environment,
            retained_prerequisites=retained,
            prerequisite_files=files,
            configs=configs,
            object_size_bytes=1,
        )

    assert marker.is_file()
    assert not candidate.exists()


def test_audited_publisher_rejects_a_different_destination_id_before_audit(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    candidate = repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "candidate-study"
    candidate.mkdir(parents=True)

    with pytest.raises(TrafficlabError, match="candidate ID"):
        study.publish_audited_bundle(candidate, "destination-study", repository_root=repository_root)

    assert candidate.is_dir()
    assert not (repository_root / "examples" / "validation_study" / "evidence" / "destination-study").exists()


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
        input: bytes | None = None,
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
        ("git", "worktree", "add", "--detach", str(repository), source_commit),
        cwd=_ROOT,
        check=True,
        capture_output=True,
    )
    candidate = repository / "fixture-study"
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


def test_relocated_audit_candidate_uses_a_detached_git_worktree(tmp_path: Path) -> None:
    """Repeated unit audits use a real checkout without duplicating repository objects."""
    repository, _candidate = _copy_validation_study_candidate(tmp_path)
    source_environment = cast(
        dict[str, object],
        json.loads(
            (_ROOT / "tests" / "fixtures" / "validation_study_candidate" / "environment.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    assert (repository / ".git").is_file()
    assert (
        subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == source_environment["source_commit"]
    )


def _candidate_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_offline_audit_reconstructs_held_out_without_calling_the_producer_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The auditor derives the independent held-out horizon from retained public bytes."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)

    def producer_boundary_must_not_run(**_kwargs: object) -> study.HeldOutEvaluation:
        raise AssertionError("auditor delegated held-out reconstruction to the producer boundary")

    monkeypatch.setattr(auditor, "evaluate_study_held_out", producer_boundary_must_not_run, raising=False)
    assert auditor.audit_bundle(candidate, repository=repository).bundle == candidate


@pytest.mark.parametrize("case", ("tracked_auditor", "tracked_source", "untracked_source"))
def test_offline_auditor_rejects_non_evidence_worktree_changes(
    tmp_path: Path,
    case: str,
) -> None:
    """The accepted audit cannot trust a checkout with mutable auditor or source inputs."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)
    if case == "tracked_auditor":
        changed = repository / "scripts" / "audit_validation_study.py"
        changed.write_bytes(changed.read_bytes() + b"\n# dirty auditor\n")
    elif case == "tracked_source":
        changed = repository / "src" / "trafficlab" / "comparison.py"
        changed.write_bytes(changed.read_bytes() + b"\n# dirty source\n")
    else:
        (repository / "untracked_source.py").write_text("sentinel = True\n", encoding="utf-8")

    with pytest.raises(TrafficlabError) as captured:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", "environment", "not_published", "primary")
    assert "working-tree" in outcome.detail


def test_offline_auditor_allows_document_evidence_and_ignored_candidate_worktree_changes(tmp_path: Path) -> None:
    """The source guard shares the committed descendant evidence/document allowlist."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)
    for relative in ("examples/validation_study/README.md", "examples/validation_study/REPORT.md"):
        path = repository / relative
        path.write_bytes(path.read_bytes() + b"\nlocal audit note\n")
    evidence_note = repository / "examples" / "validation_study" / "evidence" / "local-audit-note.txt"
    evidence_note.parent.mkdir(parents=True, exist_ok=True)
    evidence_note.write_text("retained evidence note\n", encoding="utf-8")
    for relative in (
        "examples/validation_study/.study-work/attempts/fixture-study/state.json",
        "examples/validation_study/evidence/.candidates/fixture-study/state.json",
    ):
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    if hasattr(os, "mkfifo"):
        ignored_fifo = (
            repository / "examples" / "validation_study" / ".study-work" / "attempts" / "fixture-study" / "state.fifo"
        )
        os.mkfifo(ignored_fifo)

    status = subprocess.run(
        ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    assert b"examples/validation_study/README.md" in status
    assert b"examples/validation_study/evidence/local-audit-note.txt" in status
    assert b".study-work" not in status
    assert b".candidates" not in status
    assert auditor.audit_bundle(candidate, repository=repository).bundle == candidate


def test_offline_auditor_allows_a_clean_committed_accepted_bundle(tmp_path: Path) -> None:
    """A relocated descendant may check accepted evidence in without making its worktree dirty."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)
    accepted = repository / "examples" / "validation_study" / "evidence" / candidate.name
    accepted.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(candidate, accepted)
    shutil.rmtree(candidate)
    relative = accepted.relative_to(repository).as_posix()
    for command in (
        ("git", "add", "--", relative),
        (
            "git",
            "-c",
            "user.name=Trafficlab Test",
            "-c",
            "user.email=trafficlab-test@example.invalid",
            "commit",
            "-m",
            "test accepted evidence",
        ),
    ):
        subprocess.run(command, cwd=repository, check=True, capture_output=True)
    assert not subprocess.run(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    assert auditor.audit_bundle(accepted, repository=repository).bundle == accepted


def test_offline_auditor_does_not_exempt_an_external_staged_source_candidate(tmp_path: Path) -> None:
    """Only source candidates beneath the relocated repository can suppress worktree evidence."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)

    assert (
        auditor._audit_staged_bundle(  # pyright: ignore[reportPrivateUsage]
            candidate,
            repository=repository,
            source_candidate=tmp_path / "external-candidate",
        ).bundle
        == candidate
    )


def test_offline_auditor_never_treats_the_repository_root_as_a_candidate_exemption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller cannot hide every source path by naming the repository as its candidate."""

    repository = tmp_path / "repository"
    repository.mkdir()

    def source_paths(_repository: Path) -> tuple[str, ...]:
        return ("source.py",)

    def no_nonregular_paths(_repository: Path, *, candidate_paths: Sequence[str]) -> tuple[str, ...]:
        return ()

    monkeypatch.setattr(auditor, "_relocated_worktree_paths", source_paths)
    monkeypatch.setattr(auditor, "_nonregular_relocated_worktree_paths", no_nonregular_paths)

    with pytest.raises(auditor._Issue, match="non-evidence working-tree change") as captured:  # pyright: ignore[reportPrivateUsage]
        auditor._require_permitted_relocated_worktree(  # pyright: ignore[reportPrivateUsage]
            repository,
            candidate=repository,
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_foreign", "environment")


@pytest.mark.parametrize("case", ("symlink", "nonregular"))
def test_offline_auditor_rejects_untracked_nonregular_source_paths(tmp_path: Path, case: str) -> None:
    """Filesystem special entries outside retained evidence cannot become audit inputs."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)
    source = repository / f"foreign-{case}"
    if case == "symlink":
        source.symlink_to("scripts/audit_validation_study.py")
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("nonregular FIFO entries require POSIX")
        os.mkfifo(source)

    with pytest.raises(TrafficlabError) as captured:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_foreign",
        "environment",
        "not_published",
    )


@pytest.mark.parametrize("entry_kind", ("regular", "symlink", "fifo"))
def test_offline_auditor_rejects_local_exclude_ignored_non_evidence_entries(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    """Local Git exclusion cannot exempt a source entry from the relocated audit boundary."""

    if entry_kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("nonregular FIFO entries require POSIX")
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    relative = f"locally-excluded-{entry_kind}"
    source = repository / relative
    exclude_value = subprocess.run(
        ("git", "rev-parse", "--git-path", "info/exclude"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    exclude = Path(exclude_value)
    if not exclude.is_absolute():
        exclude = repository / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as stream:
        stream.write(f"{relative}\n")
    if entry_kind == "regular":
        source.write_text("ignored foreign source\n", encoding="utf-8")
    elif entry_kind == "symlink":
        source.symlink_to("scripts/audit_validation_study.py")
    else:
        os.mkfifo(source)

    status = subprocess.run(
        ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    assert relative.encode("utf-8") not in status
    with pytest.raises(TrafficlabError) as captured:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.detail,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == (
        "artifact_foreign",
        "publication",
        f"relocated checkout contains non-evidence working-tree change: {relative}",
        "environment",
        "not_published",
        "primary",
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        (b"broken\0", "working-tree status"),
        (b"?? untracked_source.py", "working-tree status"),
        (b"?? " + bytes((255, 0)), "working-tree path is not UTF-8"),
        (bytes((255, 63, 32)) + b"source.py\0", "working-tree status is not ASCII"),
        (b"!! source.py\0", "working-tree status is malformed"),
        (b"?? /source.py\0", "working-tree path is not repository-relative"),
        (b"?? ../source.py\0", "working-tree path is not repository-relative"),
        (b"?? \0", "working-tree status is malformed"),
    ),
)
def test_offline_auditor_rejects_malformed_worktree_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: bytes,
    expected: str,
) -> None:
    """Git-status decoding is itself canonical audit evidence, not a best-effort hint."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)
    original_git_bytes = auditor._git_bytes  # pyright: ignore[reportPrivateUsage]

    def malformed_status(repository: Path, argv: tuple[str, ...], *, name: str) -> bytes:
        if name == "relocated Git working tree":
            return status
        return original_git_bytes(repository, argv, name=name)

    monkeypatch.setattr(auditor, "_git_bytes", malformed_status)

    with pytest.raises(TrafficlabError) as captured:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_corrupt",
        "environment",
        "not_published",
    )
    assert expected in outcome.detail


@pytest.mark.parametrize(("case", "expected_kind"), (("oserror", "artifact_corrupt"), ("nonzero", "artifact_foreign")))
def test_offline_auditor_classifies_worktree_git_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_kind: str,
) -> None:
    """The worktree inspection retains the existing Git failure taxonomy."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)
    original_run = auditor.subprocess.run

    def worktree_failure(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        if command == ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"):
            if case == "oserror":
                raise OSError("synthetic status failure")
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"synthetic status failure\n")
        return cast(Any, original_run)(*args, **kwargs)

    monkeypatch.setattr(auditor.subprocess, "run", worktree_failure)

    with pytest.raises(TrafficlabError) as captured:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence, outcome.evidence_state) == (
        expected_kind,
        "environment",
        "not_published",
    )


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    (
        (1, b"", frozenset[str]()),
        (0, b"foreign.fifo\0", frozenset({"foreign.fifo"})),
    ),
)
def test_offline_auditor_exactly_parses_terminal_nul_ignored_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: bytes,
    expected: frozenset[str],
) -> None:
    """The Git NUL protocol has explicit empty and exactly-delimited records."""

    repository = tmp_path / "repository"
    repository.mkdir()
    calls: list[tuple[tuple[str, ...], bytes]] = []

    def check_ignore(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        calls.append((command, cast(bytes, kwargs["input"])))
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=b"")

    monkeypatch.setattr(auditor.subprocess, "run", check_ignore)

    assert (
        auditor._ignored_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            ("foreign.fifo",),
        )
        == expected
    )
    assert calls == [(("git", "check-ignore", "-z", "--stdin"), b"foreign.fifo\0")]


def test_offline_auditor_rejects_empty_match_ignored_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Git's match status must include an exact ignored-path record."""

    repository = tmp_path / "repository"
    repository.mkdir()

    def inconsistent_match(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        assert kwargs["input"] == b"foreign.fifo\0"
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(auditor.subprocess, "run", inconsistent_match)

    with pytest.raises(auditor._Issue, match="must be nonempty for match status") as captured:  # pyright: ignore[reportPrivateUsage]
        auditor._ignored_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            ("foreign.fifo",),
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_corrupt", "environment")


def test_offline_auditor_rejects_nonempty_no_match_ignored_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Git's no-match status cannot carry a record that exempts a special entry."""

    repository = tmp_path / "repository"
    repository.mkdir()

    def inconsistent_no_match(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        assert kwargs["input"] == b"foreign.fifo\0"
        return subprocess.CompletedProcess(command, 1, stdout=b"foreign.fifo\0", stderr=b"")

    monkeypatch.setattr(auditor.subprocess, "run", inconsistent_no_match)

    with pytest.raises(auditor._Issue, match="must be empty for no-match status") as captured:  # pyright: ignore[reportPrivateUsage]
        auditor._ignored_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            ("foreign.fifo",),
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_corrupt", "environment")


@pytest.mark.parametrize(
    ("case", "expected_kind", "expected"),
    (
        ("oserror", "artifact_corrupt", "could not inspect relocated Git ignored paths"),
        ("nonzero", "artifact_foreign", "could not resolve ignored paths"),
        ("non_utf8", "artifact_corrupt", "relocated Git ignored path is not UTF-8"),
        ("foreign_path", "artifact_corrupt", "ignored paths do not match"),
        ("truncated", "artifact_corrupt", "ignored paths must be terminal NUL-delimited"),
        ("duplicate", "artifact_corrupt", "ignored paths must be unique"),
        ("nonempty_no_match", "artifact_corrupt", "ignored paths must be empty for no-match status"),
        ("empty_match", "artifact_corrupt", "ignored paths must be nonempty for match status"),
    ),
)
def test_offline_auditor_classifies_ignored_special_entry_git_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_kind: str,
    expected: str,
) -> None:
    """The special-entry ignore query remains a strict Git audit boundary."""

    if not hasattr(os, "mkfifo"):
        pytest.skip("nonregular FIFO entries require POSIX")
    repository, candidate = _copy_validation_study_candidate(tmp_path)
    source = repository / "foreign.fifo"
    os.mkfifo(source)
    original_run = auditor.subprocess.run

    def ignored_path_failure(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        if command == ("git", "check-ignore", "-z", "--stdin"):
            if case == "oserror":
                raise OSError("synthetic ignored-path failure")
            if case == "nonzero":
                return subprocess.CompletedProcess(command, 2, stdout=b"", stderr=b"synthetic failure\n")
            if case == "non_utf8":
                return subprocess.CompletedProcess(command, 0, stdout=bytes((255, 0)), stderr=b"")
            if case == "truncated":
                return subprocess.CompletedProcess(command, 0, stdout=b"foreign.fifo", stderr=b"")
            if case == "duplicate":
                return subprocess.CompletedProcess(command, 0, stdout=b"foreign.fifo\0foreign.fifo\0", stderr=b"")
            if case == "nonempty_no_match":
                return subprocess.CompletedProcess(command, 1, stdout=b"foreign.fifo\0", stderr=b"")
            if case == "empty_match":
                return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
            return subprocess.CompletedProcess(command, 0, stdout=b"elsewhere\0", stderr=b"")
        return cast(Any, original_run)(*args, **kwargs)

    monkeypatch.setattr(auditor.subprocess, "run", ignored_path_failure)

    with pytest.raises(TrafficlabError) as captured:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence, outcome.evidence_state) == (
        expected_kind,
        "environment",
        "not_published",
    )
    assert expected in outcome.detail


@pytest.mark.parametrize("case", ("directory", "entry"))
def test_offline_auditor_covers_special_entry_scan_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Unreadable worktree directories and entries have canonical local diagnostics."""

    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "source.py"
    source.write_text("sentinel = True\n", encoding="utf-8")
    original_iterdir = Path.iterdir
    original_lstat = Path.lstat

    def failing_iterdir(path: Path) -> Any:
        if case == "directory" and path == repository:
            raise OSError("synthetic directory failure")
        return original_iterdir(path)

    def failing_lstat(path: Path) -> os.stat_result:
        if case == "entry" and path == source:
            raise OSError("synthetic entry failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "iterdir", failing_iterdir)
    monkeypatch.setattr(Path, "lstat", failing_lstat)

    with pytest.raises(auditor._Issue, match="could not inspect relocated working-tree") as captured:  # pyright: ignore[reportPrivateUsage]
        auditor._nonregular_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            candidate_paths=(),
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_corrupt", "environment")


def test_offline_auditor_rejects_a_non_utf8_special_entry_path(tmp_path: Path) -> None:
    """Filesystem paths that cannot be rendered into Git's UTF-8 protocol remain corrupt."""

    repository = tmp_path / "repository"
    repository.mkdir()

    with pytest.raises(auditor._Issue, match="working-tree path is not UTF-8") as captured:  # pyright: ignore[reportPrivateUsage]
        auditor._ignored_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            ("bad\udcff",),
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_corrupt", "environment")


def test_offline_auditor_checks_the_worktree_before_committed_descendant_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mutable source is primary before the auditor trusts the committed descendant diff."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)
    changed = repository / "scripts" / "audit_validation_study.py"
    changed.write_bytes(changed.read_bytes() + b"\n# dirty auditor\n")
    original_git_bytes = auditor._git_bytes  # pyright: ignore[reportPrivateUsage]

    def require_worktree_first(repository: Path, argv: tuple[str, ...], *, name: str) -> bytes:
        if name == "post-source changed paths":
            pytest.fail("committed descendant paths were trusted before the dirty worktree")
        return original_git_bytes(repository, argv, name=name)

    monkeypatch.setattr(auditor, "_git_bytes", require_worktree_first)

    with pytest.raises(TrafficlabError, match="working-tree"):
        auditor.audit_bundle(candidate, repository=repository)


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("recorded_tree", "does not resolve"),
        ("non_ancestor", "is not an ancestor"),
        ("ancestry_oserror", "could not inspect source ancestry"),
        ("non_utf8_path", "post-source path is not UTF-8"),
        ("non_evidence_path", "non-evidence changes"),
        ("changed_image_lock", "capture image-lock bytes"),
    ),
)
def test_offline_auditor_covers_environment_source_binding_failure_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected: str,
) -> None:
    """Supplemental coverage exercises every local Git/source binding rejection."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)
    content = (candidate / "environment.json").read_bytes()
    if case == "recorded_tree":
        original_identity = auditor._git_identity  # pyright: ignore[reportPrivateUsage]

        def mismatched_recorded_tree(repository: Path, argv: tuple[str, ...], *, name: str) -> str:
            if name == "recorded source tree":
                return "0" * 39 + "1"
            return original_identity(repository, argv, name=name)

        monkeypatch.setattr(auditor, "_git_identity", mismatched_recorded_tree)
    elif case in {"non_ancestor", "ancestry_oserror"}:
        original_run = auditor.subprocess.run

        def source_binding_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            command = tuple(cast(Sequence[str], args[0]))
            if command[:3] == ("git", "merge-base", "--is-ancestor"):
                if case == "ancestry_oserror":
                    raise OSError("synthetic Git failure")
                return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"")
            return cast(Any, original_run)(*args, **kwargs)

        monkeypatch.setattr(auditor.subprocess, "run", source_binding_run)
    else:
        original_git_bytes = auditor._git_bytes  # pyright: ignore[reportPrivateUsage]

        def source_binding_bytes(repository: Path, argv: tuple[str, ...], *, name: str) -> bytes:
            if case == "non_utf8_path" and name == "post-source changed paths":
                return b"\xff\0"
            if case == "non_evidence_path" and name == "post-source changed paths":
                return b"src/trafficlab/__init__.py\0"
            if case == "changed_image_lock" and name == "recorded capture image lock":
                return b"different checked image lock\n"
            return original_git_bytes(repository, argv, name=name)

        monkeypatch.setattr(auditor, "_git_bytes", source_binding_bytes)

    with pytest.raises(auditor._Issue, match=expected):  # pyright: ignore[reportPrivateUsage]
        auditor._environment(content, repository=repository)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("mismatch", ("protocol", "prerequisites"))
def test_offline_auditor_covers_root_study_identity_rejections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    """The public bundle checker rejects conflicting candidate, protocol, and prerequisite IDs first."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)
    entries = auditor._verify_inventory(  # pyright: ignore[reportPrivateUsage]
        candidate,
        (candidate / "manifest.json").read_bytes(),
    )

    def empty_environment(_content: bytes, *, repository: Path) -> dict[str, object]:
        return {}

    def mismatched_prerequisites(*_args: object, **_kwargs: object) -> tuple[dict[str, object], set[str]]:
        return {"study_id": "fixture-study"}, set()

    def wrong_protocol(_content: bytes) -> dict[str, object]:
        return {"study_id": "other-study"}

    def matching_protocol(_content: bytes) -> dict[str, object]:
        return {"study_id": "fixture-study"}

    def wrong_prerequisites(*_args: object, **_kwargs: object) -> tuple[dict[str, object], set[str]]:
        return {"study_id": "other-study"}, set()

    monkeypatch.setattr(auditor, "_environment", empty_environment)
    if mismatch == "protocol":
        monkeypatch.setattr(auditor, "_protocol", wrong_protocol)
        monkeypatch.setattr(auditor, "_prerequisites", mismatched_prerequisites)
        expected = "protocol destination ID"
    else:
        monkeypatch.setattr(auditor, "_protocol", matching_protocol)
        monkeypatch.setattr(auditor, "_prerequisites", wrong_prerequisites)
        expected = "retained prerequisites must bind"

    with pytest.raises(auditor._Issue, match=expected):  # pyright: ignore[reportPrivateUsage]
        auditor._audit(candidate, repository, entries)  # pyright: ignore[reportPrivateUsage]


def _tree_inventory(root: Path) -> dict[str, tuple[object, ...]]:
    """Capture exact entry kinds, symlink targets, and regular bytes without following links."""
    if not root.exists() and not root.is_symlink():
        return {".": ("missing",)}
    inventory: dict[str, tuple[object, ...]] = {}
    for path in sorted((root, *root.rglob("*")), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            inventory[relative] = ("symlink", os.readlink(path))
        elif stat.S_ISREG(mode):
            inventory[relative] = ("regular", path.read_bytes())
        elif stat.S_ISDIR(mode):
            inventory[relative] = ("directory",)
        else:
            inventory[relative] = ("other", mode)
    return inventory


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


def _auditor_semantics_fixture_config() -> ExperimentConfig:
    """Build one valid config with the declared host mount that publication relocates."""

    document = tomllib.loads(
        (
            _ROOT / "tests" / "fixtures" / "validation_study_candidate" / "configs" / "training-short-r1.realized.toml"
        ).read_text(encoding="utf-8")
    )
    target = cast(dict[str, Any], document["target"])
    target["mounts"] = [{"source": "/retained/mount", "target": "/trafficlab-study/short.headers", "read_only": True}]
    return ExperimentConfig.model_validate(document)


def test_offline_auditor_config_semantics_masks_only_declared_operational_paths() -> None:
    """Relocation may alter only the run directory and host-side mount source."""

    baseline = _auditor_semantics_fixture_config()
    relocated_mount = baseline.target.mounts[0].model_copy(update={"source": Path("/relocated/mount")})
    relocated_target = baseline.target.model_copy(update={"mounts": (relocated_mount,)})
    relocated = baseline.model_copy(
        update={
            "run": baseline.run.model_copy(update={"directory": Path("/relocated/run")}),
            "target": relocated_target,
        }
    )

    assert auditor._config_semantics(relocated) == auditor._config_semantics(baseline)  # pyright: ignore[reportPrivateUsage]


_NONOPERATIONAL_CONFIG_MUTATIONS = (
    "master_seed",
    "target_image",
    "target_argv_order",
    "target_environment",
    "target_working_directory",
    "mount_target",
    "mount_read_only",
    "capture_timeout",
    "trial_limit",
    "final_limit",
    "population_size",
    "model_bound",
    "similarity_limit",
)
_NONOPERATIONAL_REALIZED_CONFIG_MUTATIONS = tuple(
    case for case in _NONOPERATIONAL_CONFIG_MUTATIONS if case not in {"mount_target", "mount_read_only"}
)


def _nonoperational_config_mutation(config: ExperimentConfig, case: str) -> ExperimentConfig:
    document = config.model_dump(mode="json")
    target = cast(dict[str, Any], document["target"])
    if case == "master_seed":
        cast(dict[str, Any], document["run"])["master_seed"] = 74
    elif case == "target_image":
        target["image"] = "curlimages/curl@sha256:" + "1" * 64
    elif case == "target_argv_order":
        target["argv"] = list(reversed(cast(list[str], target["argv"])))
    elif case == "target_environment":
        target["environment"] = {"TRAFFICLAB_MUTATION": "1"}
    elif case == "target_working_directory":
        target["working_directory"] = "/changed"
    elif case == "mount_target":
        mount = cast(dict[str, Any], cast(list[object], target["mounts"])[0])
        mount["target"] = "/changed.headers"
    elif case == "mount_read_only":
        mount = cast(dict[str, Any], cast(list[object], target["mounts"])[0])
        mount["read_only"] = False
    elif case == "capture_timeout":
        cast(dict[str, Any], document["capture"])["readiness_timeout_seconds"] = 3.0
    elif case == "trial_limit":
        cast(dict[str, Any], cast(dict[str, Any], document["generation"])["trial"])["max_packets"] = 501
    elif case == "final_limit":
        cast(dict[str, Any], cast(dict[str, Any], document["generation"])["final"])["max_packets"] = 1001
    elif case == "population_size":
        cast(dict[str, Any], document["genetic"])["population_size"] = 7
    elif case == "model_bound":
        cast(dict[str, Any], cast(dict[str, Any], document["models"])["poisson_empirical"])["c_lambda"]["lower"] = 0.6
    else:
        cast(dict[str, Any], document["similarity"])["max_direction_bin_cells"] = 101

    return ExperimentConfig.model_validate(document)


@pytest.mark.parametrize("case", _NONOPERATIONAL_CONFIG_MUTATIONS)
def test_offline_auditor_config_semantics_rejects_each_nonoperational_mutation(case: str) -> None:
    """Every scientific/workload field remains part of the retained config identity."""

    baseline = _auditor_semantics_fixture_config()
    mutated = _nonoperational_config_mutation(baseline, case)

    assert auditor._config_semantics(mutated) != auditor._config_semantics(baseline)  # pyright: ignore[reportPrivateUsage]


def _config_semantic_leaf_paths(value: Any, prefix: tuple[str | int, ...] = ()) -> tuple[tuple[str | int, ...], ...]:
    if prefix == ("run", "directory"):
        return ()
    if prefix == ("target", "mounts"):
        mounts = cast(list[dict[str, Any]], value)
        return tuple(
            path
            for index, mount in enumerate(mounts)
            for path in (("target", "mounts", index, "target"), ("target", "mounts", index, "read_only"))
            if path[-1] in mount
        )
    if prefix == ("similarity", "method_weights"):
        return (prefix,)
    if prefix == ("similarity",):
        coupled = {
            "acf_lags",
            "acf_lag_weights",
            "acf_iat_weight",
            "acf_size_weight",
            "multiscale_packet_weight",
            "multiscale_byte_weight",
        }
        settings = cast(dict[str, Any], value)
        return (
            ("similarity", "__acf_lags_and_weights__"),
            ("similarity", "__acf_component_weights__"),
            ("similarity", "__multiscale_component_weights__"),
            *(
                path
                for key, child in settings.items()
                if key not in coupled
                for path in _config_semantic_leaf_paths(child, (*prefix, key))
            ),
        )
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        if not mapping:
            return (prefix,)
        return tuple(
            path for key, child in mapping.items() for path in _config_semantic_leaf_paths(child, (*prefix, key))
        )
    return (prefix,)


def _config_semantic_path_value(document: dict[str, Any], path: tuple[str | int, ...]) -> Any:
    if isinstance(path[-1], str) and path[-1].startswith("__"):
        return None
    value: Any = document
    for part in path:
        value = value[part]
    return value


def _set_config_semantic_path_value(
    document: dict[str, Any],
    path: tuple[str | int, ...],
    replacement: Any,
) -> None:
    if path == ("similarity", "__acf_lags_and_weights__"):
        similarity = cast(dict[str, Any], document["similarity"])
        similarity["acf_lags"] = [1, 2]
        similarity["acf_lag_weights"] = [0.5, 0.5]
        return
    if path == ("similarity", "__acf_component_weights__"):
        similarity = cast(dict[str, Any], document["similarity"])
        similarity["acf_iat_weight"] = 0.6
        similarity["acf_size_weight"] = 0.4
        return
    if path == ("similarity", "__multiscale_component_weights__"):
        similarity = cast(dict[str, Any], document["similarity"])
        similarity["multiscale_packet_weight"] = 0.6
        similarity["multiscale_byte_weight"] = 0.4
        return
    parent = _config_semantic_path_value(document, path[:-1])
    parent[path[-1]] = replacement


def _config_semantic_replacements(path: tuple[str | int, ...], value: Any) -> tuple[Any, ...]:
    if isinstance(path[-1], str) and path[-1].startswith("__"):
        return (None,)
    if path == ("target", "image"):
        return ("curlimages/curl@sha256:" + "1" * 64,)
    if path == ("capture", "image"):
        return ("trafficlab-capture@sha256:" + "2" * 64,)
    if path == ("capture", "network_probe_url"):
        return ("https://example.test/changed",)
    if path == ("similarity", "method_weights"):
        weights = cast(dict[str, float], value)
        return ({**weights, "frame_size_ks": 0.30, "iat_ks": 0.20},)
    if type(value) is bool:
        return (not value,)
    if type(value) is int:
        return (value + 1, value - 1)
    if type(value) is float:
        return (value + 0.01, value - 0.01)
    if type(value) is str:
        return (f"{value}-changed",)
    if isinstance(value, list):
        items = cast(list[Any], value)
        if path == ("genetic", "trial_seeds"):
            return ([cast(int, items[0]) + 1],)
        if all(type(item) is str for item in items):
            return (list(reversed(items)),)
        if all(type(item) is int for item in items):
            return ([cast(int, items[0]) + 1],)
        if len(items) == 1:
            return ([cast(float, items[0]) + 0.1], [cast(float, items[0]) - 0.1])
        changed = list(items)
        changed[0] = cast(float, changed[0]) + 0.1
        changed[-1] = cast(float, changed[-1]) - 0.1
        return (changed,)
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        return ({**mapping, "TRAFFICLAB_MUTATION": "1"},)
    raise AssertionError(f"no mutation candidate for config path {path}")


def test_offline_auditor_config_semantics_retains_every_nonoperational_control() -> None:
    """Only the two documented host-path classes are removed from config comparison."""

    baseline = _auditor_semantics_fixture_config()
    document = baseline.model_dump(mode="json")
    paths = _config_semantic_leaf_paths(document)
    assert paths
    for path in paths:
        value = _config_semantic_path_value(document, path)
        for replacement in _config_semantic_replacements(path, value):
            mutated_document = copy.deepcopy(document)
            _set_config_semantic_path_value(mutated_document, path, replacement)
            try:
                mutated = ExperimentConfig.model_validate(mutated_document)
            except ValueError:
                continue
            assert auditor._config_semantics(mutated) != auditor._config_semantics(baseline)  # pyright: ignore[reportPrivateUsage]
            break
        else:
            raise AssertionError(f"no valid semantic mutation for config path {path}")


@pytest.mark.parametrize("case", _NONOPERATIONAL_REALIZED_CONFIG_MUTATIONS)
def test_offline_auditor_rejects_each_nonoperational_realized_config_mutation(
    tmp_path: Path,
    case: str,
) -> None:
    """A portable/realized pair rejects every non-operational relocation mutation."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)
    realized_path = candidate / "configs" / "training-short-r1.realized.toml"
    original = realized_path.read_bytes()
    baseline = ExperimentConfig.model_validate(tomllib.loads(original.decode("utf-8")))
    realized_path.write_bytes(render_effective_config(_nonoperational_config_mutation(baseline, case)))
    _rewrite_candidate_manifest(candidate)

    with pytest.raises(
        TrafficlabError, match="realized configuration does not match its portable configuration"
    ) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == (
        "artifact_foreign",
        "publication",
        "configs/training-short-r1.realized.toml",
        "not_published",
        "primary",
    )


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
        study.publish_audited_bundle(candidate, "fixture-study", repository_root=repository)

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
        ("capture_image_reference", "trafficlab-capture:latest", "artifact_corrupt"),
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
        ("run_configuration", "artifact_corrupt"),
        ("run_configuration_semantics", "artifact_foreign"),
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
    elif case == "run_configuration_semantics":
        document = tomllib.loads((run / "experiment.toml").read_text(encoding="utf-8"))
        cast(dict[str, Any], document["run"])["master_seed"] = 74
        (run / "experiment.toml").write_bytes(render_effective_config(ExperimentConfig.model_validate(document)))
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


@pytest.mark.parametrize("field", ("runtime", "winner", "weights", "invalid_chromosome", "natural_variation"))
def test_offline_bundle_audit_recomputes_each_report_input_family(tmp_path: Path, field: str) -> None:
    """Report inputs are independently reconstructed rather than trusted as producer output."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)
    path = candidate / "report_inputs.json"
    document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    if field == "runtime":
        records = cast(list[dict[str, object]], document["runtime_winner_variance"])
        runtime = cast(dict[str, object], records[0]["runtime_seconds"])
        runtime["mean"] = cast(float, runtime["mean"]) + 1.0
    elif field == "winner":
        records = cast(list[dict[str, object]], document["runtime_winner_variance"])
        winners = cast(dict[str, object], records[0]["winner_family_counts"])
        winners["mmpp"] = cast(int, winners["mmpp"]) + 1
    elif field == "weights":
        records = cast(list[dict[str, object]], document["controlled_weight_analysis"])
        records[0]["alternative_aggregate"] = cast(float, records[0]["alternative_aggregate"]) + 1.0
    elif field == "invalid_chromosome":
        records = cast(list[dict[str, object]], document["invalid_chromosome_diagnostics"])
        limits = cast(dict[str, object], records[0]["trial_limits"])
        limits["max_packets"] = cast(int, limits["max_packets"]) + 1
    else:
        records = cast(list[dict[str, object]], document["natural_variation"])
        pairs = cast(list[dict[str, object]], records[0]["pairs"])
        forward = cast(dict[str, object], pairs[0]["forward"])
        forward["aggregate"] = cast(float, forward["aggregate"]) + 1.0
    _write_canonical_json(path, document)
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
    ) == ("artifact_foreign", "publication", "report_inputs.json", "not_published", "primary")


@pytest.mark.parametrize(
    "binding",
    auditor._TRANSFER_BINDINGS,  # pyright: ignore[reportPrivateUsage]
    ids=lambda binding: f"{binding.scope}-{binding.run_id}-{binding.transfer_index}",
)
@pytest.mark.parametrize("kind", ("header", "observation"))
def test_offline_bundle_audit_rejects_each_scoped_transfer_file(
    tmp_path: Path,
    binding: auditor._Transfer,  # pyright: ignore[reportPrivateUsage]
    kind: str,
) -> None:
    """Every prerequisite, training, and held-out transfer is an independently retained audit input."""

    repository, candidate = _copy_validation_study_candidate(tmp_path)
    if kind == "header":
        relative = f"headers/{binding.scope}/{binding.run_id}/{binding.filename}"
        path = candidate / relative
        path.write_bytes(path.read_bytes().replace(b"206", b"205", 1))
    else:
        relative = f"observations/{binding.scope}/{binding.run_id}/{binding.filename}.json"
        path = candidate / relative
        document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        document["status"] = 205
        _write_canonical_json(path, document)
    _rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        auditor.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.affected_evidence == relative


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
        (
            "headers/prerequisites/00-prerequisites/capability.headers",
            "transfer-header:prerequisites:00-prerequisites:0",
            {
                "filename": "capability.headers",
                "relation": "transfer-header",
                "requested_end": 0,
                "requested_start": 0,
                "run_id": "00-prerequisites",
                "scope": "prerequisites",
                "transfer_index": 0,
                "workload": "prerequisites",
            },
        ),
        (
            "observations/held_out/held-out-streaming/streaming.headers.json",
            "external-observation:held_out:held-out-streaming:0",
            {
                "filename": "streaming.headers",
                "relation": "external-observation",
                "requested_end": 4_194_303,
                "requested_start": 0,
                "run_id": "held-out-streaming",
                "scope": "held_out",
                "transfer_index": 0,
                "workload": "streaming",
            },
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


def test_validation_fixture_generator_check_honors_explicit_source_identities() -> None:
    environment = cast(
        dict[str, object],
        json.loads((_ROOT / "tests" / "fixtures" / "validation_study_candidate" / "environment.json").read_text()),
    )
    source_commit = cast(str, environment["source_commit"])
    alternate_commit = "a" * 40 if source_commit != "a" * 40 else "b" * 40

    assert (
        fixture_generator.main(
            [
                "--check",
                "--source-commit",
                alternate_commit,
                "--source-tree",
                cast(str, environment["source_tree"]),
            ]
        )
        == 1
    )


def test_validation_fixture_generator_main_requires_complete_ids_and_writes_to_its_owned_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = cast(
        dict[str, object],
        json.loads((_ROOT / "tests" / "fixtures" / "validation_study_candidate" / "environment.json").read_text()),
    )
    source_commit = cast(str, environment["source_commit"])
    source_tree = cast(str, environment["source_tree"])
    with pytest.raises(TrafficlabError, match="requires explicit source"):
        fixture_generator.main([])
    with pytest.raises(TrafficlabError, match="requires explicit source"):
        fixture_generator.main(["--check", "--source-commit", source_commit])

    output = tmp_path / "owned-fixture"
    monkeypatch.setattr(fixture_generator, "FIXTURE", output)
    assert fixture_generator.main(["--source-commit", source_commit, "--source-tree", source_tree]) == 0
    assert len(_candidate_bytes(output)) == 231


def test_validation_fixture_retains_the_complete_231_file_evidence_inventory() -> None:
    assert len(_candidate_bytes(_ROOT / "tests" / "fixtures" / "validation_study_candidate")) == 231


def test_checked_study_result_uses_canonical_fresh_simulation_records() -> None:
    content = (_ROOT / "examples" / "validation_study" / "results.json").read_bytes()
    document = cast(dict[str, object], json.loads(content))
    capability = cast(dict[str, object], cast(dict[str, object], document["protocol"])["capability"])
    argv = cast(list[str], capability["argv"])
    assert "--user-agent" not in argv
    result = study.parse_study_results(content, repository_root=_ROOT)

    assert b'"fresh_simulation"' in content
    assert b'"held_out"' not in content
    assert study.render_study_results(result) == content

    near_miss = copy.deepcopy(document)
    near_miss_capability = cast(dict[str, object], cast(dict[str, object], near_miss["protocol"])["capability"])
    near_miss_argv = cast(list[str], near_miss_capability["argv"])
    near_miss_argv[near_miss_argv.index("--max-time") + 1] = "31"
    with pytest.raises(ValueError, match="capability argv"):
        study.parse_study_results(
            json.dumps(near_miss, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
            repository_root=_ROOT,
        )

    workload_near_miss = copy.deepcopy(document)
    first_workload = cast(
        dict[str, object], cast(list[object], cast(dict[str, object], workload_near_miss["protocol"])["workloads"])[0]
    )
    workload_argv = cast(list[str], first_workload["argv"])
    workload_argv[workload_argv.index("--max-time") + 1] = "31"
    with pytest.raises(ValueError, match="short workload definition"):
        study.parse_study_results(
            json.dumps(workload_near_miss, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
            repository_root=_ROOT,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("current-capability", "capability argv"),
        ("current-workload", "short workload definition"),
        ("all-current", "capability argv"),
        ("wrong-study-id", "historic schema-1 protocol identity"),
        ("wrong-url", "historic schema-1 protocol identity"),
    ),
)
def test_historic_schema_one_protocol_is_one_atomic_identity(mutation: str, message: str) -> None:
    """The sole legacy result cannot combine independent current and historic command projections."""
    content = (_ROOT / "examples" / "validation_study" / "results.json").read_bytes()
    document = copy.deepcopy(cast(dict[str, object], json.loads(content)))
    protocol = cast(dict[str, object], document["protocol"])
    study_id = cast(str, protocol["study_id"])
    url = cast(str, protocol["url"])
    capability = cast(dict[str, object], protocol["capability"])
    workloads = cast(list[dict[str, object]], protocol["workloads"])
    current_capability = study._expected_capability_argv(  # pyright: ignore[reportPrivateUsage]
        study_id,
        url,
    )
    current_workloads = study.workload_specs(url)

    if mutation in {"current-capability", "all-current"}:
        capability["argv"] = list(current_capability)
    if mutation in {"current-workload", "all-current"}:
        workloads[0]["argv"] = list(current_workloads[0].argv)
    if mutation == "all-current":
        for workload, current in zip(workloads, current_workloads, strict=True):
            workload["argv"] = list(current.argv)
    if mutation == "wrong-study-id":
        protocol["study_id"] = "legacy-study"
    if mutation == "wrong-url":
        protocol["url"] = "https://downloads.example.test/other.bin"

    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    with pytest.raises(ValueError, match=message):
        study.parse_study_results(invalid, repository_root=_ROOT)


def test_current_protocol_rejects_a_capability_projection_without_the_package_user_agent() -> None:
    content = (_ROOT / "examples" / "validation_study" / "results.json").read_bytes()
    current = cast(dict[str, object], json.loads(content))
    environment = cast(dict[str, object], current["environment"])
    environment["git_commit"] = "c" * 40

    capability = cast(dict[str, object], cast(dict[str, object], current["protocol"])["capability"])
    argv = cast(list[str], capability["argv"])
    assert "--user-agent" not in argv

    with pytest.raises(ValueError, match="capability argv"):
        study.parse_study_results(
            json.dumps(current, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n",
            repository_root=_ROOT,
        )


def test_study_held_out_evaluator_uses_the_independent_window_with_the_fixed_training_model() -> None:
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

    longer_window = tuple(
        TraceEvent(
            event.timestamp + (1.0 if index == len(independent) - 1 else 0.0), event.direction, event.frame_length
        )
        for index, event in enumerate(independent)
    )
    shorter_window = tuple(
        TraceEvent(event.timestamp * 0.8, event.direction, event.frame_length) for event in independent
    )
    for name, events in (("short", shorter_window), ("long", longer_window)):
        _normalized, held_out_window = normalize_reference(events)
        evaluation = study.evaluate_study_held_out(
            model_content=(fixture / "best_model.json").read_bytes(),
            model_source=fixture / "best_model.json",
            config=config,
            capture_content=_CAPTURE_BYTES,
            capture_source=fixture / "capture.json",
            reference_content=encode_pcapng(events, metadata),
            reference_source=Path(f"held_out/{name}-window.pcapng"),
        )
        assert held_out_window != result.training_model.observation_window_seconds
        assert evaluation.observation_window_seconds == held_out_window
        assert evaluation.training_model == result.training_model
        assert evaluation.training_model_identity == result.training_model_identity
        assert evaluation.seed == result.training_model.final_seed
        assert evaluation.training_model.final_limits == result.training_model.final_limits


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
    capability_header = b"HTTP/1.1 206 Partial Content\r\nContent-Length: 1\r\nContent-Range: bytes 0-0/4194304\r\n\r\n"
    document = {
        "capability": {
            "canary_sha256": hashlib.sha256(capability_header).hexdigest(),
            "content_length": 1,
            "content_range": "bytes 0-0/4194304",
            "object_size_bytes": 4_194_304,
            "status": 206,
        },
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
    report_inputs = cast(dict[str, object], json.loads((candidate / "report_inputs.json").read_text(encoding="utf-8")))

    selection = cast(dict[str, object], protocol["model_selection"])
    assert protocol["schema_version"] == 3
    assert "natural_variation_windows" not in protocol
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
    evidence_root = repository / "examples" / "validation_study" / "evidence"
    destination = evidence_root / "fixture-study"
    (repository / "inventory-sentinel").symlink_to("candidate")
    candidate_before = _tree_inventory(candidate)
    evidence_before = _tree_inventory(evidence_root)
    repository_before = _tree_inventory(repository)
    assert repository_before["inventory-sentinel"] == ("symlink", "candidate")

    with pytest.raises(TrafficlabError) as error:
        study.publish_audited_bundle(candidate, "fixture-study", repository_root=repository)

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
    assert _tree_inventory(candidate) == candidate_before
    assert _tree_inventory(evidence_root) == evidence_before
    assert _tree_inventory(repository) == repository_before
    assert not destination.exists()


def test_retained_prerequisite_codec_rejects_invalid_public_forms() -> None:
    """The public retained codec rejects unsupported roots, kinds, and noncanonical bytes."""
    content = (_ROOT / "tests" / "fixtures" / "validation_study_candidate" / "prerequisites.json").read_bytes()
    noncanonical = content.replace(b"{", b"{ ", 1)
    assert noncanonical != content

    with pytest.raises(ValueError, match="root must be testsuite or testsuites"):
        study.prerequisite_junit_counts(b"<unexpected/>")
    with pytest.raises(ValueError, match="prerequisite kind"):
        study.prerequisite_command_argv(
            "unsupported", study_id="fixture-study", url="https://downloads.example.test/object.bin"
        )
    with pytest.raises(ValueError, match="prerequisite kind"):
        study.validate_frozen_prerequisite_command(
            "unsupported",
            (),
            0,
            {},
            study_id="fixture-study",
            url="https://downloads.example.test/object.bin",
        )
    with pytest.raises(ValueError, match="canonical sorted compact"):
        study.parse_retained_prerequisites(noncanonical)


def test_validation_study_gitignore_tracks_only_accepted_run_logs() -> None:
    """Accepted evidence logs remain trackable while candidates and ordinary logs stay ignored."""

    def ignored(path: str) -> bool:
        result = subprocess.run(
            ("git", "check-ignore", "-q", "--", path),
            cwd=_ROOT,
            check=False,
            capture_output=True,
        )
        assert result.returncode in (0, 1)
        return result.returncode == 0

    assert not ignored("examples/validation_study/evidence/study-1/training/short/r1/run.log")
    assert ignored("examples/validation_study/evidence/.candidates/study-1/training/short/r1/run.log")
    assert ignored("runs/study-1/run.log")


def test_prerequisite_attempt_marker_is_written_before_any_later_failure(tmp_path: Path) -> None:
    """A syntactically valid prerequisite attempt is permanently visible even if Git fails first."""

    repository = tmp_path / "repository"
    repository.mkdir()

    def fail_git(
        argv: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 1, b"", b"missing Git state")

    with pytest.raises(TrafficlabError, match="prerequisite validation failed"):
        study.run_prerequisites(
            "https://downloads.example.test/object.bin",
            "study-1",
            repository_root=repository,
            runner=cast(study.CommandRunner, fail_git),
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    marker = (
        repository / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1" / "prerequisites.json"
    )
    assert json.loads(marker.read_text()) == {
        "phase": "prerequisites",
        "study_id": "study-1",
        "url": "https://downloads.example.test/object.bin",
    }
    assert not marker.with_name("prerequisites-success.json").exists()


def test_successful_prerequisite_marker_binds_the_published_prerequisite_bytes(tmp_path: Path) -> None:
    """Collection can only follow the exact successful prerequisite publication."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    runner = _ScriptedPrerequisiteRunner(repository)
    study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    prerequisite = repository / "examples" / "validation_study" / "prerequisites.json"
    marker = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / runner.study_id
        / "prerequisites-success.json"
    )
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "phase": "prerequisites",
        "prerequisites_identity": identify_bytes(prerequisite.read_bytes()).as_dict(),
        "study_id": runner.study_id,
        "url": runner.url,
    }


def _write_legacy_prerequisite_root(
    repository: Path,
    *,
    study_id: str = "study-r4",
) -> tuple[Path, bytes]:
    """Create the schema-1 root and markers published before raw archives existed."""

    prerequisite = _valid_prerequisite(study_id=study_id)
    content = study.render_prerequisite_results(prerequisite)
    root = repository / "examples" / "validation_study" / "prerequisites.json"
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_bytes(content)
    attempt = root.parent / ".study-work" / "attempts" / study_id
    attempt.mkdir(parents=True)
    (attempt / "prerequisites.json").write_bytes(
        study._canonical_json(  # pyright: ignore[reportPrivateUsage]
            cast(
                study.JsonObject,
                {"phase": "prerequisites", "study_id": study_id, "url": prerequisite.url},
            )
        )
    )
    (attempt / "prerequisites-success.json").write_bytes(
        study._canonical_json(  # pyright: ignore[reportPrivateUsage]
            cast(
                study.JsonObject,
                {
                    "phase": "prerequisites",
                    "prerequisites_identity": identify_bytes(content).as_dict(),
                    "study_id": study_id,
                    "url": prerequisite.url,
                },
            )
        )
    )
    assert not (attempt / "prerequisites.raw.json").exists()
    return root, content


def test_rotation_bootstraps_a_matching_legacy_raw_archive_before_replacement(tmp_path: Path) -> None:
    """A schema-1 root and success marker gain a forensic archive before r5 overwrites them."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    root, r4_bytes = _write_legacy_prerequisite_root(repository)
    r4_archive = root.parent / ".study-work" / "attempts" / "study-r4" / "prerequisites.raw.json"
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")

    study.run_prerequisites(
        r5.url,
        r5.study_id,
        repository_root=repository,
        runner=r5,
        utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )

    assert r4_archive.read_bytes() == r4_bytes
    assert root.read_bytes() != r4_bytes


def test_rotation_rejects_a_legacy_marker_identity_mismatch_before_replacement(
    tmp_path: Path,
) -> None:
    """A legacy archive is never inferred from root bytes that disagree with its successful marker."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    root, r4_bytes = _write_legacy_prerequisite_root(repository)
    marker = root.parent / ".study-work" / "attempts" / "study-r4" / "prerequisites-success.json"
    marker_value = json.loads(marker.read_text(encoding="utf-8"))
    marker_value["prerequisites_identity"] = identify_bytes(b"wrong legacy identity\n").as_dict()
    marker.write_bytes(study._canonical_json(cast(study.JsonObject, marker_value)))  # pyright: ignore[reportPrivateUsage]
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")

    with pytest.raises(TrafficlabError, match="matching successful prerequisite marker"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    assert root.read_bytes() == r4_bytes
    assert not (root.parent / ".study-work" / "attempts" / "study-r4" / "prerequisites.raw.json").exists()


def test_rotation_rejects_a_conflicting_legacy_raw_archive_before_replacement(
    tmp_path: Path,
) -> None:
    """A prior archive collision is preserved rather than silently replaced during rotation."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    root, r4_bytes = _write_legacy_prerequisite_root(repository)
    r4_archive = root.parent / ".study-work" / "attempts" / "study-r4" / "prerequisites.raw.json"
    r4_archive.write_bytes(b"conflicting legacy archive\n")
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")

    with pytest.raises(TrafficlabError, match="archived prerequisite"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    assert root.read_bytes() == r4_bytes
    assert r4_archive.read_bytes() == b"conflicting legacy archive\n"


def test_rotation_rejects_a_nonregular_legacy_raw_archive_before_replacement(
    tmp_path: Path,
) -> None:
    """A legacy archive directory is never replaced while preserving the current canonical root."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    root, r4_bytes = _write_legacy_prerequisite_root(repository)
    r4_archive = root.parent / ".study-work" / "attempts" / "study-r4" / "prerequisites.raw.json"
    r4_archive.mkdir()
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")

    with pytest.raises(TrafficlabError, match="archived prerequisite document must be a regular file"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    assert root.read_bytes() == r4_bytes
    assert r4_archive.is_dir()


def test_failed_prerequisite_rotation_preserves_current_raw_document_and_old_archive(tmp_path: Path) -> None:
    """A fresh failed ID cannot alter the prior canonical prerequisite publication."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    r4 = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    canonical = repository / "examples" / "validation_study" / "prerequisites.json"
    r4_bytes = canonical.read_bytes()
    r4_archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / r4.study_id
        / "prerequisites.raw.json"
    )
    assert r4_archive.read_bytes() == r4_bytes

    r5 = _ScriptedPrerequisiteRunner(repository, "docker-matrix-failed", study_id="study-r5")
    with pytest.raises(TrafficlabError, match="docker_matrix guarded pytest failed"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    assert canonical.read_bytes() == r4_bytes
    assert r4_archive.read_bytes() == r4_bytes
    r5_attempt = repository / "examples" / "validation_study" / ".study-work" / "attempts" / r5.study_id
    assert (r5_attempt / "prerequisites.json").is_file()
    assert not (r5_attempt / "prerequisites-success.json").exists()
    assert not (r5_attempt / "prerequisites.raw.json").exists()


def test_successful_prerequisite_rotation_replaces_current_raw_and_preserves_old_archive(tmp_path: Path) -> None:
    """A new successful ID atomically advances the canonical root without erasing r4 evidence."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    r4 = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    canonical = repository / "examples" / "validation_study" / "prerequisites.json"
    r4_bytes = canonical.read_bytes()
    r4_archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / r4.study_id
        / "prerequisites.raw.json"
    )

    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    result = study.run_prerequisites(
        r5.url,
        r5.study_id,
        repository_root=repository,
        runner=r5,
        utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )

    r5_bytes = canonical.read_bytes()
    r5_archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / r5.study_id
        / "prerequisites.raw.json"
    )
    assert r5_bytes != r4_bytes
    assert study.parse_prerequisite_results(r5_bytes, repository_root=repository) == result
    assert r4_archive.read_bytes() == r4_bytes
    assert r5_archive.read_bytes() == r5_bytes
    r5_marker = json.loads((r5_archive.with_name("prerequisites-success.json")).read_text(encoding="utf-8"))
    assert r5_marker["prerequisites_identity"] == identify_bytes(r5_archive.read_bytes()).as_dict()
    assert study.validate_base_configs(repository, result) == {
        name: study.build_base_config(
            workload,
            repository_root=repository,
            study_id=r5.study_id,
            url=r5.url,
            capture_image_id=r5.capture_id,
        )
        for name, workload in ((workload.name, workload) for workload in study.workload_specs(r5.url))
    }


@pytest.mark.parametrize("failure_index", (1, 2, 3, 4))
def test_prerequisite_rotation_rolls_back_every_replacement_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_index: int,
) -> None:
    """Every replaceable config/root target restores the complete r4 state."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    r4 = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    canonical_before = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    r4_archive = study_root / ".study-work" / "attempts" / r4.study_id / "prerequisites.raw.json"
    r4_archive_before = r4_archive.read_bytes()
    r4_attempt = r4_archive.parent
    r4_attempt_before = _tree_inventory(r4_attempt)
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    original_replace = study.os.replace
    replacements = 0

    def fail_nth_replacement(source: str | Path, target: str | Path) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == failure_index:
            raise OSError(f"simulated replacement {failure_index}")
        original_replace(source, target)

    monkeypatch.setattr(study.os, "replace", fail_nth_replacement)
    with pytest.raises(TrafficlabError, match=f"replacement {failure_index}"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    canonical_after = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    assert canonical_after == canonical_before
    assert r4_archive.read_bytes() == r4_archive_before
    assert _tree_inventory(r4_attempt) == r4_attempt_before
    r5_attempt = study_root / ".study-work" / "attempts" / r5.study_id
    assert (r5_attempt / "prerequisites.json").is_file()
    assert not (r5_attempt / "prerequisites.raw.json").exists()
    assert not (r5_attempt / "prerequisites-success.json").exists()
    assert not tuple(study_root.rglob(".*.tmp"))
    assert not tuple(study_root.rglob(".*.bak"))


@pytest.mark.parametrize("failure_index", (1, 2, 3, 4, 5, 6))
def test_prerequisite_rotation_rolls_back_every_commit_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_index: int,
) -> None:
    """A post-replacement fsync failure removes every partially committed r5 publication."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    r4 = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    canonical_before = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    r4_archive = study_root / ".study-work" / "attempts" / r4.study_id / "prerequisites.raw.json"
    r4_archive_before = r4_archive.read_bytes()
    r4_attempt = r4_archive.parent
    r4_attempt_before = _tree_inventory(r4_attempt)
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    fsyncs = 0

    def fail_nth_commit_fsync(_destination: Path) -> None:
        nonlocal fsyncs
        fsyncs += 1
        if fsyncs == failure_index:
            raise OSError(f"simulated commit fsync {failure_index}")

    monkeypatch.setattr(study, "_commit_prerequisite_fsync", fail_nth_commit_fsync, raising=False)
    with pytest.raises(TrafficlabError, match=f"commit fsync {failure_index}"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    canonical_after = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    assert canonical_after == canonical_before
    assert r4_archive.read_bytes() == r4_archive_before
    assert _tree_inventory(r4_attempt) == r4_attempt_before
    r5_attempt = study_root / ".study-work" / "attempts" / r5.study_id
    assert (r5_attempt / "prerequisites.json").is_file()
    assert not (r5_attempt / "prerequisites.raw.json").exists()
    assert not (r5_attempt / "prerequisites-success.json").exists()
    assert not tuple(study_root.rglob(".*.tmp"))
    assert not tuple(study_root.rglob(".*.bak"))


def test_prerequisite_rotation_target_reports_lstat_and_read_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Target inspection distinguishes an unreadable entry from a read failure without following it."""

    destination = tmp_path / "prerequisites.json"
    destination.write_bytes(b"canonical\n")
    original_lstat = Path.lstat

    def fail_target_lstat(path: Path) -> os.stat_result:
        if path == destination:
            raise OSError("simulated target lstat failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_target_lstat)
    with pytest.raises(ValueError, match="could not inspect rotation target"):
        study._read_regular_prerequisite_rotation_target(  # pyright: ignore[reportPrivateUsage]
            destination,
            name="rotation target",
        )

    monkeypatch.undo()
    original_read_bytes = Path.read_bytes

    def fail_target_read(path: Path) -> bytes:
        if path == destination:
            raise OSError("simulated target read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_target_read)
    with pytest.raises(ValueError, match="could not read rotation target"):
        study._read_regular_prerequisite_rotation_target(  # pyright: ignore[reportPrivateUsage]
            destination,
            name="rotation target",
        )


def test_prerequisite_rotation_stage_cleans_validator_and_descriptor_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A private staging file never survives validation or descriptor-wrapper failure."""

    destination = tmp_path / "prerequisites.json"

    def reject_stage(_stage: Path, _content: bytes) -> None:
        raise ValueError("simulated staged validation failure")

    with pytest.raises(ValueError, match="staged validation"):
        study._stage_prerequisite_rotation_file(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"canonical\n",
            validate=reject_stage,
        )
    assert not tuple(tmp_path.glob(".*.tmp"))

    closed: list[int] = []
    original_close = study.os.close

    def fail_fdopen(_descriptor: int, _mode: str) -> None:
        raise OSError("simulated staging fdopen failure")

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(study.os, "fdopen", fail_fdopen)
    monkeypatch.setattr(study.os, "close", record_close)
    with pytest.raises(OSError, match="staging fdopen failure"):
        study._stage_prerequisite_rotation_file(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"canonical\n",
            validate=lambda _stage, _content: None,
        )
    assert closed
    assert not tuple(tmp_path.glob(".*.tmp"))

    monkeypatch.undo()

    def fail_mkstemp(*_args: object, **_kwargs: object) -> tuple[int, str]:
        raise OSError("simulated staging mkstemp failure")

    monkeypatch.setattr(study.tempfile, "mkstemp", fail_mkstemp)
    with pytest.raises(OSError, match="staging mkstemp failure"):
        study._stage_prerequisite_rotation_file(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"canonical\n",
            validate=lambda _stage, _content: None,
        )

    monkeypatch.undo()
    original_unlink = Path.unlink

    def fail_staged_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.name.endswith(".tmp"):
            raise OSError("simulated staging unlink failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_staged_unlink)
    with pytest.raises(ValueError, match="staged validation failure"):
        study._stage_prerequisite_rotation_file(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"canonical\n",
            validate=reject_stage,
        )
    monkeypatch.undo()
    for temporary in tmp_path.glob(".*.tmp"):
        temporary.unlink()


def test_prerequisite_rotation_rollback_reports_a_durability_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback retains its failure diagnostic if its own directory fsync cannot complete."""

    destination = tmp_path / "prerequisites.json"
    destination.write_bytes(b"partially committed\n")

    def fail_fsync(_destination: Path) -> None:
        raise OSError("simulated rollback fsync failure")

    monkeypatch.setattr(study, "_commit_prerequisite_fsync", fail_fsync)
    target = study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
        kind="archive",
        destination=destination,
        stage=tmp_path / ".prerequisites.json.stage.tmp",
        backup=None,
        before_identity=None,
        target_identity=cast(study.JsonObject, identify_bytes(b"partially committed\n").as_dict()),
        must_be_absent=True,
    )
    failures, failed_targets = study._rollback_prerequisite_rotation([target])  # pyright: ignore[reportPrivateUsage]

    assert failures == [f"{destination}: simulated rollback fsync failure"]
    assert failed_targets == [target]
    assert not destination.exists()


def test_prerequisite_rotation_preserves_primary_when_rollback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A primary publication error remains visible with an ordered rollback durability diagnostic."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    r4 = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")

    def fail_commit(_destination: Path) -> None:
        raise OSError("simulated primary commit failure")

    def simulated_rollback_failure(
        _committed: Sequence[study._PrerequisiteRotationTarget],  # pyright: ignore[reportPrivateUsage]
    ) -> tuple[list[str], list[study._PrerequisiteRotationTarget]]:  # pyright: ignore[reportPrivateUsage]
        return ["simulated rollback durability failure"], []

    monkeypatch.setattr(study, "_commit_prerequisite_fsync", fail_commit)
    monkeypatch.setattr(
        study,
        "_rollback_prerequisite_rotation",
        simulated_rollback_failure,
    )
    with pytest.raises(TrafficlabError, match="rollback failed after simulated primary commit failure"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )


def test_prerequisite_rotation_retains_its_journal_when_rollback_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleanup fault after a restored primary target leaves the journal for a later public recovery."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    r4 = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    canonical = study_root / "prerequisites.json"
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    original_commit_fsync = study._commit_prerequisite_fsync  # pyright: ignore[reportPrivateUsage]
    original_unlink = Path.unlink
    failed_primary = False

    def fail_canonical_commit(destination: Path) -> None:
        nonlocal failed_primary
        if destination == canonical and not failed_primary:
            failed_primary = True
            raise OSError("simulated primary canonical commit failure")
        original_commit_fsync(destination)

    def fail_marker_stage_cleanup(path: Path, *, missing_ok: bool = False) -> None:
        if path.name.startswith(".prerequisites-success.json.") and path.name.endswith(".tmp"):
            raise OSError("simulated rollback cleanup unlink failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(study, "_commit_prerequisite_fsync", fail_canonical_commit)
    monkeypatch.setattr(Path, "unlink", fail_marker_stage_cleanup)
    with pytest.raises(TrafficlabError, match="rollback cleanup failed after") as raised:
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    journal = study_root / ".study-work" / "attempts" / r5.study_id / "prerequisites-rotation.json"
    assert journal.is_file()
    assert "retained recovery journal" in str(raised.value)
    assert tuple(study_root.rglob(".prerequisites-success.json.*.tmp"))


@pytest.mark.parametrize(
    ("boundary", "owned_prefix", "owned_suffix"),
    (
        ("archive stage", ".prerequisites.raw.json.", ".tmp"),
        ("short backup", ".short.toml.", ".bak"),
        ("streaming backup", ".streaming.toml.", ".bak"),
        ("bursty backup", ".bursty.toml.", ".bak"),
        ("root backup", ".prerequisites.json.", ".bak"),
        ("marker stage", ".prerequisites-success.json.", ".tmp"),
    ),
)
@pytest.mark.parametrize("failure_kind", ("unlink", "fsync", "baseexception"))
def test_prerequisite_rotation_recovers_success_cleanup_failures_before_a_new_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    owned_prefix: str,
    owned_suffix: str,
    failure_kind: str,
) -> None:
    """A durable journal survives every post-marker cleanup fault until public recovery succeeds."""

    class SimulatedCleanupCrash(BaseException):
        pass

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    r4 = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    cleanup_started = False
    enabled = True
    original_after_commit = study._after_prerequisite_rotation_commit  # pyright: ignore[reportPrivateUsage]
    original_unlink = Path.unlink
    original_fsync = study._fsync_prerequisite_rotation_directory  # pyright: ignore[reportPrivateUsage]

    def matches_owned_cleanup_path(path: Path) -> bool:
        return enabled and cleanup_started and path.name.startswith(owned_prefix) and path.name.endswith(owned_suffix)

    def mark_successful_marker_commit(destination: Path) -> None:
        nonlocal cleanup_started
        original_after_commit(destination)
        if destination.name == "prerequisites-success.json":
            cleanup_started = True

    def fail_selected_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if matches_owned_cleanup_path(path):
            if failure_kind == "baseexception":
                raise SimulatedCleanupCrash(f"simulated {boundary} cleanup crash")
            if failure_kind == "unlink":
                raise OSError(f"simulated {boundary} cleanup unlink failure")
        original_unlink(path, missing_ok=missing_ok)

    def fail_selected_directory_fsync(path: Path) -> None:
        if matches_owned_cleanup_path(path) and failure_kind == "fsync":
            raise OSError(f"simulated {boundary} cleanup fsync failure")
        original_fsync(path)

    monkeypatch.setattr(study, "_after_prerequisite_rotation_commit", mark_successful_marker_commit)
    monkeypatch.setattr(Path, "unlink", fail_selected_unlink)
    monkeypatch.setattr(study, "_fsync_prerequisite_rotation_directory", fail_selected_directory_fsync)

    if failure_kind == "baseexception":
        with pytest.raises(SimulatedCleanupCrash, match=f"{boundary} cleanup crash"):
            study.run_prerequisites(
                r5.url,
                r5.study_id,
                repository_root=repository,
                runner=r5,
                utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
            )
    else:
        with pytest.raises(TrafficlabError, match="retained recovery journal"):
            study.run_prerequisites(
                r5.url,
                r5.study_id,
                repository_root=repository,
                runner=r5,
                utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
            )

    r5_attempt = study_root / ".study-work" / "attempts" / r5.study_id
    journal = r5_attempt / "prerequisites-rotation.json"
    assert journal.is_file()
    canonical = study_root / "prerequisites.json"
    assert study.parse_prerequisite_results(canonical.read_bytes(), repository_root=repository).study_id == r5.study_id
    expected_r5_inventory = {
        ".": ("directory",),
        **{
            name: _tree_inventory(r5_attempt)[name]
            for name in ("prerequisites.json", "prerequisites.raw.json", "prerequisites-success.json")
        },
    }

    enabled = False
    r6 = _ScriptedPrerequisiteRunner(repository, study_id="study-r6")
    original_begin = study._begin_phase_attempt  # pyright: ignore[reportPrivateUsage]

    def assert_recovered_before_begin(
        root: Path,
        *,
        study_id: str,
        url: str,
        phase: Literal["prerequisites", "collection"],
    ) -> Path:
        if study_id == r6.study_id:
            assert not journal.exists()
            assert _tree_inventory(r5_attempt) == expected_r5_inventory
            assert not tuple(study_root.rglob(".*.tmp"))
            assert not tuple(study_root.rglob(".*.bak"))
            raise ValueError("success cleanup recovery inspection complete")
        return original_begin(root, study_id=study_id, url=url, phase=phase)

    monkeypatch.setattr(study, "_begin_phase_attempt", assert_recovered_before_begin)
    with pytest.raises(TrafficlabError, match="success cleanup recovery inspection complete"):
        study.run_prerequisites(
            r6.url,
            r6.study_id,
            repository_root=repository,
            runner=r6,
            utc_now=lambda: datetime(2026, 8, 18, tzinfo=UTC),
        )


def test_prerequisite_rotation_nonstrict_prestage_cleanup_preserves_its_primary_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only pre-journal cleanup may suppress a secondary staging-unlink error."""

    destination = tmp_path / "prerequisites.json"
    staged = tmp_path / ".prerequisites.json.primary.tmp"
    content = b"canonical prerequisite bytes\n"
    staged.write_bytes(content)
    target = study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
        kind="archive",
        destination=destination,
        stage=staged,
        backup=None,
        before_identity=None,
        target_identity=cast(study.JsonObject, identify_bytes(content).as_dict()),
        must_be_absent=True,
    )
    original_unlink = Path.unlink

    def fail_staged_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path == staged:
            raise OSError("simulated pre-journal cleanup unlink failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_staged_unlink)
    assert (
        study._cleanup_prerequisite_rotation_staging(  # pyright: ignore[reportPrivateUsage]
            (target,),
            strict=False,
        )
        == []
    )
    assert staged.read_bytes() == content


@pytest.mark.parametrize("entry_kind", ("symlink", "fifo"))
def test_successful_prerequisite_marker_rejects_nonregular_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_kind: str,
) -> None:
    """A matching marker must be a canonical regular file, never an indirection or device."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    runner = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    prerequisite = repository / "examples" / "validation_study" / "prerequisites.json"
    marker = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / runner.study_id
        / "prerequisites-success.json"
    )
    marker_bytes = marker.read_bytes()
    marker.unlink()
    if entry_kind == "symlink":
        outside = repository / "outside-marker.json"
        outside.write_bytes(marker_bytes)
        marker.symlink_to(outside)
    else:
        os.mkfifo(marker)
        original_read_bytes = Path.read_bytes

        def forbid_fifo_read(path: Path) -> bytes:
            if path == marker:
                raise AssertionError("marker reader must reject a FIFO before opening it")
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", forbid_fifo_read)

    with pytest.raises(TrafficlabError, match="matching successful prerequisite marker"):
        study._require_successful_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id=runner.study_id,
            url=runner.url,
            prerequisite_content=prerequisite.read_bytes(),
        )


def test_successful_prerequisite_marker_and_legacy_archive_use_durable_regular_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal marker path and a bootstrap archive both bind regular canonical bytes durably."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    runner = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    prerequisite = repository / "examples" / "validation_study" / "prerequisites.json"
    study._require_successful_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository,
        study_id=runner.study_id,
        url=runner.url,
        prerequisite_content=prerequisite.read_bytes(),
    )

    archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / runner.study_id
        / "prerequisites.raw.json"
    )
    content = archive.read_bytes()
    archive.unlink()
    fsynced: list[Path] = []
    original_fsync = study._fsync_prerequisite_rotation_directory  # pyright: ignore[reportPrivateUsage]

    def record_fsync(destination: Path) -> None:
        fsynced.append(destination)
        original_fsync(destination)

    monkeypatch.setattr(study, "_fsync_prerequisite_rotation_directory", record_fsync)
    assert (
        study._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id=runner.study_id,
            content=content,
        )
        == content
    )
    assert archive in fsynced
    assert (
        study._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id=runner.study_id,
            content=content,
        )
        == content
    )


def test_prerequisite_rotation_journal_requires_a_stage_for_every_owned_target(tmp_path: Path) -> None:
    """A journal cannot omit a stage path before it records mutable transaction state."""

    target = study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
        kind="archive",
        destination=tmp_path / "archive.json",
        stage=None,
        backup=None,
        before_identity=None,
        target_identity=cast(study.JsonObject, identify_bytes(b"incoming\n").as_dict()),
        must_be_absent=True,
    )

    with pytest.raises(ValueError, match="requires every staged target"):
        study._render_prerequisite_rotation_journal(  # pyright: ignore[reportPrivateUsage]
            tmp_path,
            study_id="study-r5",
            targets=(target,),
        )


def test_prerequisite_rotation_recovery_allows_an_absent_attempt_directory(tmp_path: Path) -> None:
    """Recovery is a no-op before any prerequisite phase has ever allocated an attempt directory."""

    repository = tmp_path / "repository"
    repository.mkdir()
    study._recover_incomplete_prerequisite_rotations(repository)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("cleanup_mode", ("remove", "read-error", "mismatched", "link-failure"))
def test_prerequisite_rotation_exclusive_publication_preserves_or_removes_only_its_own_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_mode: str,
) -> None:
    """A post-link validation failure removes only verified bytes and retains uncertain recovery evidence."""

    destination = tmp_path / "prerequisites.raw.json"
    content = b"canonical prerequisite bytes\n"
    validation_calls = 0
    cleanup_read = False
    original_read = study._read_regular_prerequisite_rotation_target  # pyright: ignore[reportPrivateUsage]
    original_link = study.os.link

    def validate(persisted: bytes) -> None:
        nonlocal cleanup_read, validation_calls
        validation_calls += 1
        assert persisted == content
        if validation_calls == 2:
            cleanup_read = True
            raise ValueError("simulated post-link validation failure")

    def maybe_fail_cleanup_read(path: Path, *, name: str) -> bytes:
        if cleanup_mode == "read-error" and cleanup_read and path == destination:
            raise OSError("simulated uncertain post-link read")
        if cleanup_mode == "mismatched" and cleanup_read and path == destination:
            return b"different retained bytes\n"
        return original_read(path, name=name)

    def fail_before_link(source: str | Path, target: str | Path) -> None:
        if cleanup_mode == "link-failure":
            raise OSError("simulated exclusive publication collision")
        original_link(source, target)

    monkeypatch.setattr(study, "_read_regular_prerequisite_rotation_target", maybe_fail_cleanup_read)
    monkeypatch.setattr(study.os, "link", fail_before_link)
    expected_error = OSError if cleanup_mode == "link-failure" else ValueError
    expected_message = (
        "exclusive publication collision" if cleanup_mode == "link-failure" else "post-link validation failure"
    )
    with pytest.raises(expected_error, match=expected_message):
        study._publish_prerequisite_rotation_exclusive_file(  # pyright: ignore[reportPrivateUsage]
            destination,
            content,
            validate=validate,
            name="test prerequisite archive",
        )

    assert validation_calls == (1 if cleanup_mode == "link-failure" else 2)
    if cleanup_mode in {"read-error", "mismatched"}:
        assert destination.read_bytes() == content
    else:
        assert not destination.exists()
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_prerequisite_rotation_target_guard_paths_are_explicit(tmp_path: Path) -> None:
    """Missing stages/backups cannot silently turn journal recovery into arbitrary deletion."""

    incoming = cast(study.JsonObject, identify_bytes(b"incoming\n").as_dict())
    prior = cast(study.JsonObject, identify_bytes(b"prior\n").as_dict())
    destination = tmp_path / "target.json"
    destination.write_bytes(b"incoming\n")
    missing_stage = study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
        kind="archive",
        destination=destination,
        stage=None,
        backup=None,
        before_identity=None,
        target_identity=incoming,
        must_be_absent=True,
    )
    with pytest.raises(ValueError, match="retain its staged path"):
        study._restore_prerequisite_rotation_target(missing_stage)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="staged before publication"):
        study._publish_prerequisite_rotation_target(missing_stage)  # pyright: ignore[reportPrivateUsage]
    assert (
        study._cleanup_prerequisite_rotation_staging(  # pyright: ignore[reportPrivateUsage]
            (missing_stage,),
            strict=True,
        )
        == []
    )

    wrong_stage = tmp_path / ".target.json.wrong.tmp"
    wrong_stage.write_bytes(b"foreign staging bytes\n")
    wrong_stage_target = study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
        kind="archive",
        destination=destination,
        stage=wrong_stage,
        backup=None,
        before_identity=None,
        target_identity=incoming,
        must_be_absent=True,
    )
    cleanup_failures = study._cleanup_prerequisite_rotation_staging(  # pyright: ignore[reportPrivateUsage]
        (wrong_stage_target,),
        strict=True,
    )
    assert cleanup_failures == [
        f"{wrong_stage}: prerequisite rotation archive stage does not match its transaction-owned identity"
    ]
    assert wrong_stage.read_bytes() == b"foreign staging bytes\n"

    prior_target = study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
        kind="config-short",
        destination=destination,
        stage=tmp_path / ".target.json.stage.tmp",
        backup=None,
        before_identity=prior,
        target_identity=incoming,
        must_be_absent=False,
    )
    with pytest.raises(ValueError, match="prior bytes require a backup"):
        study._restore_prerequisite_rotation_target(prior_target)  # pyright: ignore[reportPrivateUsage]


def test_prerequisite_rotation_completion_rejects_semantically_invalid_target_bytes(tmp_path: Path) -> None:
    """Matching journal hashes alone never bless malformed prerequisite semantics as a completed rotation."""

    repository = tmp_path / "repository"
    repository.mkdir()
    targets: list[study._PrerequisiteRotationTarget] = []  # pyright: ignore[reportPrivateUsage]
    for kind, destination, must_be_absent in study._prerequisite_rotation_expected_targets(  # pyright: ignore[reportPrivateUsage]
        repository,
        "study-r5",
    ):
        content = b"not canonical prerequisite JSON\n" if kind == "root" else b"placeholder\n"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        stage = destination.parent / f".{destination.name}.complete.tmp"
        stage.write_bytes(content)
        targets.append(
            study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
                kind=kind,
                destination=destination,
                stage=stage,
                backup=None,
                before_identity=None,
                target_identity=cast(study.JsonObject, identify_bytes(content).as_dict()),
                must_be_absent=must_be_absent,
            )
        )

    assert not study._prerequisite_rotation_is_complete(  # pyright: ignore[reportPrivateUsage]
        repository,
        study_id="study-r5",
        targets=targets,
    )


@pytest.mark.parametrize("failure", ("attempt-lstat", "attempt-enumeration", "child-lstat"))
def test_prerequisite_rotation_recovery_reports_attempt_directory_io_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """A recovery scan never skips an unreadable attempt boundary before consuming a new study ID."""

    repository = tmp_path / "repository"
    attempts = repository / "examples" / "validation_study" / ".study-work" / "attempts"
    attempts.mkdir(parents=True)
    child = attempts / "study-r5"
    child.mkdir()
    original_lstat = Path.lstat
    original_iterdir = Path.iterdir

    def fail_selected_lstat(path: Path) -> os.stat_result:
        if (failure == "attempt-lstat" and path == attempts) or (failure == "child-lstat" and path == child):
            raise OSError(f"simulated {failure}")
        return original_lstat(path)

    def fail_selected_iterdir(path: Path) -> list[Path]:
        if failure == "attempt-enumeration" and path == attempts:
            raise OSError("simulated attempt enumeration")
        return list(original_iterdir(path))

    monkeypatch.setattr(Path, "lstat", fail_selected_lstat)
    monkeypatch.setattr(Path, "iterdir", fail_selected_iterdir)
    with pytest.raises(ValueError, match="could not (inspect|enumerate) prerequisite attempt"):
        study._recover_incomplete_prerequisite_rotations(repository)  # pyright: ignore[reportPrivateUsage]


def test_prerequisite_rotation_retains_failed_restore_backup_with_its_recovery_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed rollback restore never deletes the only exact prior bytes."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    r4 = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    short_config = study_root / "configs" / "short.toml"
    short_before = short_config.read_bytes()
    canonical = study_root / "prerequisites.json"
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    original_fsync = study._commit_prerequisite_fsync  # pyright: ignore[reportPrivateUsage]
    original_replace = study.os.replace
    failed_backup: Path | None = None

    def fail_canonical_commit(destination: Path) -> None:
        if destination == canonical:
            raise OSError("simulated primary canonical fsync failure")
        original_fsync(destination)

    def fail_short_restore(source: str | Path, target: str | Path) -> None:
        nonlocal failed_backup
        source_path = Path(source)
        target_path = Path(target)
        if source_path.suffix == ".bak" and target_path == short_config:
            failed_backup = source_path
            raise OSError("simulated short rollback restore failure")
        original_replace(source, target)

    monkeypatch.setattr(study, "_commit_prerequisite_fsync", fail_canonical_commit)
    monkeypatch.setattr(study.os, "replace", fail_short_restore)
    with pytest.raises(TrafficlabError, match="rollback failed after") as raised:
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    assert failed_backup is not None
    assert str(failed_backup) in str(raised.value)
    assert failed_backup.read_bytes() == short_before
    assert _tree_inventory(failed_backup.parent)[failed_backup.name] == ("regular", short_before)
    journal = study_root / ".study-work" / "attempts" / r5.study_id / "prerequisites-rotation.json"
    assert journal.is_file()

    r6 = _ScriptedPrerequisiteRunner(repository, study_id="study-r6")
    with pytest.raises(TrafficlabError, match="retained recovery paths") as recovery:
        study.run_prerequisites(
            r6.url,
            r6.study_id,
            repository_root=repository,
            runner=r6,
            utc_now=lambda: datetime(2026, 8, 18, tzinfo=UTC),
        )
    assert str(failed_backup) in str(recovery.value)
    assert failed_backup.read_bytes() == short_before
    assert journal.is_file()
    assert not (study_root / ".study-work" / "attempts" / r6.study_id / "prerequisites.json").exists()


@pytest.mark.parametrize("target_name", ("prerequisites.raw.json", "prerequisites-success.json"))
def test_prerequisite_rotation_rejects_late_absent_target_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_name: str,
) -> None:
    """A file created after staging is never overwritten at an absent archive or marker target."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    r4 = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    canonical_before = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    r4_attempt = study_root / ".study-work" / "attempts" / r4.study_id
    r4_attempt_before = _tree_inventory(r4_attempt)
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    collision = b"late foreign prerequisite collision\n"
    original_stage = study._stage_prerequisite_rotation_file  # pyright: ignore[reportPrivateUsage]

    def stage_then_collide(
        destination: Path,
        content: bytes,
        *,
        validate: Callable[[Path, bytes], None],
        suffix: str = ".tmp",
    ) -> Path:
        stage = original_stage(destination, content, validate=validate, suffix=suffix)
        if destination.name == target_name and suffix == ".tmp":
            destination.write_bytes(collision)
        return stage

    monkeypatch.setattr(study, "_stage_prerequisite_rotation_file", stage_then_collide)
    with pytest.raises(TrafficlabError, match="absent|exists|collision"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    canonical_after = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    assert canonical_after == canonical_before
    assert _tree_inventory(r4_attempt) == r4_attempt_before
    target = study_root / ".study-work" / "attempts" / r5.study_id / target_name
    assert target.read_bytes() == collision
    assert not tuple(study_root.rglob(".*.tmp"))
    assert not tuple(study_root.rglob(".*.bak"))


def test_prerequisite_rotation_cleans_a_backup_created_before_later_stage_validation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every backup is transaction-owned before its paired incoming stage can fail."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    r4 = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    canonical_before = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    r4_attempt = study_root / ".study-work" / "attempts" / r4.study_id
    r4_attempt_before = _tree_inventory(r4_attempt)
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    original_stage = study._stage_prerequisite_rotation_file  # pyright: ignore[reportPrivateUsage]
    backup: Path | None = None

    def fail_short_incoming_stage(
        destination: Path,
        content: bytes,
        *,
        validate: Callable[[Path, bytes], None],
        suffix: str = ".tmp",
    ) -> Path:
        nonlocal backup
        if destination.name == "short.toml" and suffix == ".tmp":
            raise ValueError("simulated incoming stage validation failure")
        staged = original_stage(destination, content, validate=validate, suffix=suffix)
        if destination.name == "short.toml" and suffix == ".bak":
            backup = staged
        return staged

    monkeypatch.setattr(study, "_stage_prerequisite_rotation_file", fail_short_incoming_stage)
    with pytest.raises(TrafficlabError, match="incoming stage validation failure"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    assert backup is not None
    assert not backup.exists()
    canonical_after = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    assert canonical_after == canonical_before
    assert _tree_inventory(r4_attempt) == r4_attempt_before
    r5_attempt = study_root / ".study-work" / "attempts" / r5.study_id
    assert set(path.name for path in r5_attempt.iterdir()) == {"prerequisites.json"}
    assert not tuple(study_root.rglob(".*.tmp"))
    assert not tuple(study_root.rglob(".*.bak"))


@pytest.mark.parametrize("commit_index", (1, 2, 3, 4, 5, 6))
def test_prerequisite_rotation_recovers_every_baseexception_crash_before_a_new_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    commit_index: int,
) -> None:
    """A fresh public prerequisites invocation restores all r4 bytes before it consumes r6."""

    class SimulatedCrash(BaseException):
        pass

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    r4 = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    canonical_before = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    r4_attempt = study_root / ".study-work" / "attempts" / r4.study_id
    r4_attempt_before = _tree_inventory(r4_attempt)
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    commits = 0

    def crash_after_commit(_destination: Path) -> None:
        nonlocal commits
        commits += 1
        if commits == commit_index:
            raise SimulatedCrash(f"simulated crash after commit {commit_index}")

    monkeypatch.setattr(study, "_after_prerequisite_rotation_commit", crash_after_commit, raising=False)
    with pytest.raises(SimulatedCrash, match=f"commit {commit_index}"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    r5_attempt = study_root / ".study-work" / "attempts" / r5.study_id
    r5_canonical_after_crash = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts and not path.name.endswith((".tmp", ".bak"))
    }
    r5_success_marker = r5_attempt / "prerequisites-success.json"
    r6 = _ScriptedPrerequisiteRunner(repository, study_id="study-r6")
    original_begin = study._begin_phase_attempt  # pyright: ignore[reportPrivateUsage]

    def assert_recovered_before_begin(
        root: Path,
        *,
        study_id: str,
        url: str,
        phase: Literal["prerequisites", "collection"],
    ) -> Path:
        if study_id == r6.study_id:
            canonical_after = {
                path.relative_to(study_root): path.read_bytes()
                for path in sorted(study_root.rglob("*"))
                if path.is_file() and ".study-work" not in path.parts
            }
            assert _tree_inventory(r4_attempt) == r4_attempt_before
            if commit_index == 6:
                assert canonical_after == r5_canonical_after_crash
                assert _tree_inventory(r5_attempt) == {
                    ".": ("directory",),
                    "prerequisites.json": (
                        "regular",
                        study._canonical_json(  # pyright: ignore[reportPrivateUsage]
                            cast(
                                study.JsonObject,
                                {"phase": "prerequisites", "study_id": r5.study_id, "url": r5.url},
                            )
                        ),
                    ),
                    "prerequisites-success.json": ("regular", r5_success_marker.read_bytes()),
                    "prerequisites.raw.json": (
                        "regular",
                        (study_root / "prerequisites.json").read_bytes(),
                    ),
                }
            else:
                assert canonical_after == canonical_before
                assert _tree_inventory(r5_attempt) == {
                    ".": ("directory",),
                    "prerequisites.json": (
                        "regular",
                        study._canonical_json(  # pyright: ignore[reportPrivateUsage]
                            cast(
                                study.JsonObject,
                                {"phase": "prerequisites", "study_id": r5.study_id, "url": r5.url},
                            )
                        ),
                    ),
                }
            assert not tuple(study_root.rglob(".*.tmp"))
            assert not tuple(study_root.rglob(".*.bak"))
            raise ValueError("recovery inspection complete")
        return original_begin(root, study_id=study_id, url=url, phase=phase)

    monkeypatch.setattr(study, "_begin_phase_attempt", assert_recovered_before_begin)
    with pytest.raises(TrafficlabError, match="recovery inspection complete"):
        study.run_prerequisites(
            r6.url,
            r6.study_id,
            repository_root=repository,
            runner=r6,
            utc_now=lambda: datetime(2026, 8, 18, tzinfo=UTC),
        )


def test_collection_rejects_old_id_after_prerequisite_rotation_but_keeps_its_raw_archive(tmp_path: Path) -> None:
    """The collector accepts only the current canonical root, never an old ignored archive."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    r4 = _ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    r4_archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / r4.study_id
        / "prerequisites.raw.json"
    )
    r4_bytes = r4_archive.read_bytes()
    r5 = _ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    study.run_prerequisites(
        r5.url,
        r5.study_id,
        repository_root=repository,
        runner=r5,
        utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )
    canonical = repository / "examples" / "validation_study" / "prerequisites.json"

    def forbidden_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("old collection must fail before environment work")

    with pytest.raises(TrafficlabError, match="matching successful prerequisite marker"):
        study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
            repository,
            canonical,
            study_id=r4.study_id,
            url=r4.url,
            runner=cast(study.CommandRunner, forbidden_runner),
            require_successful_prerequisite=True,
        )

    assert r4_archive.read_bytes() == r4_bytes


def test_prerequisite_rotation_refuses_symlink_and_cleans_replacement_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a regular canonical prerequisite file is eligible for atomic rotation."""

    repository = tmp_path / "repository"
    repository.mkdir()
    destination = repository / "examples" / "validation_study" / "prerequisites.json"
    destination.parent.mkdir(parents=True)
    outside = repository / "outside.json"
    outside.write_bytes(b"outside\n")
    destination.symlink_to(outside)

    with pytest.raises(TrafficlabError, match="regular"):
        study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
            destination,
            _valid_prerequisite(),
            repository_root=repository,
            replace_existing=True,
        )

    assert destination.is_symlink()
    assert outside.read_bytes() == b"outside\n"
    assert not tuple(destination.parent.glob(".prerequisites.json.*"))

    destination.unlink()
    destination.write_bytes(b"r4\n")

    def fail_replace(_source: str | Path, _target: str | Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(study.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
            destination,
            _valid_prerequisite(),
            repository_root=repository,
            replace_existing=True,
        )

    assert destination.read_bytes() == b"r4\n"
    assert not tuple(destination.parent.glob(".prerequisites.json.*"))


def test_prerequisite_archive_refuses_a_preexisting_symlink(tmp_path: Path) -> None:
    """A raw prerequisite archive cannot follow an attacker-controlled attempt entry."""

    repository = tmp_path / "repository"
    archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / "study-r4"
        / "prerequisites.raw.json"
    )
    archive.parent.mkdir(parents=True)
    outside = repository / "outside.json"
    outside.write_bytes(b"canonical\n")
    archive.symlink_to(outside)

    with pytest.raises(ValueError, match="regular"):
        study._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id="study-r4",
            content=b"canonical\n",
        )

    assert archive.is_symlink()
    assert outside.read_bytes() == b"canonical\n"


def test_prerequisite_rotation_preserves_current_file_when_replacement_setup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacement setup cannot alter a current regular prerequisite publication."""

    repository = tmp_path / "repository"
    repository.mkdir()
    destination = repository / "examples" / "validation_study" / "prerequisites.json"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"r4\n")
    original_lstat = Path.lstat
    calls = 0

    def fail_second_destination_lstat(path: Path) -> os.stat_result:
        nonlocal calls
        if path == destination:
            calls += 1
            if calls == 3:
                raise OSError("simulated lstat failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_second_destination_lstat)
    with pytest.raises(ValueError, match="could not inspect"):
        study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
            destination,
            _valid_prerequisite(),
            repository_root=repository,
            replace_existing=True,
        )

    assert destination.read_bytes() == b"r4\n"
    assert not tuple(destination.parent.glob(".prerequisites.json.*"))


def test_prerequisite_rotation_cleans_temp_when_replacement_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed descriptor wrapper leaves the old prerequisite bytes and no sibling temp."""

    repository = tmp_path / "repository"
    repository.mkdir()
    destination = repository / "examples" / "validation_study" / "prerequisites.json"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"r4\n")

    def fail_fdopen(_descriptor: int, _mode: str) -> None:
        raise OSError("simulated replacement fdopen failure")

    monkeypatch.setattr(study.os, "fdopen", fail_fdopen)
    with pytest.raises(OSError, match="replacement fdopen failure"):
        study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
            destination,
            _valid_prerequisite(),
            repository_root=repository,
            replace_existing=True,
        )

    assert destination.read_bytes() == b"r4\n"
    assert not tuple(destination.parent.glob(".prerequisites.json.*"))


def test_checked_config_create_race_is_reported_without_a_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legacy exclusive-create path retains its race handling for first publication."""

    destination = tmp_path / "short.toml"
    original_open = Path.open

    def fail_config_create(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> object:
        if path == destination and mode == "xb":
            raise FileExistsError("simulated config create race")
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", fail_config_create)
    with pytest.raises(ValueError, match="config target already exists"):
        study._write_new_config(destination, b"[run]\n")  # pyright: ignore[reportPrivateUsage]

    assert not destination.exists()


def test_prerequisite_publication_rejects_noncanonical_validated_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prerequisite publication codec rejects a parser/render disagreement before link or replace."""

    repository = tmp_path / "repository"
    repository.mkdir()
    destination = repository / "prerequisites.json"
    original_render = study.render_prerequisite_results
    calls = 0

    def render_once_then_mismatch(value: study.PrerequisiteResults) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_render(value)
        return b"{}\n"

    monkeypatch.setattr(study, "render_prerequisite_results", render_once_then_mismatch)
    with pytest.raises(ValueError, match="persisted prerequisite JSON is not canonical"):
        study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
            destination,
            _valid_prerequisite(),
            repository_root=repository,
        )

    assert not destination.exists()
    assert not tuple(destination.parent.glob(".prerequisites.json.*"))


def test_prerequisite_archive_reports_existing_archive_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archive read errors are never treated as a matching prior prerequisite document."""

    repository = tmp_path / "repository"
    archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / "study-r4"
        / "prerequisites.raw.json"
    )
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"canonical\n")
    original_read_bytes = Path.read_bytes

    def fail_archive_read(path: Path) -> bytes:
        if path == archive:
            raise OSError("simulated archive read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_archive_read)
    with pytest.raises(ValueError, match="could not read archived prerequisite document"):
        study._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id="study-r4",
            content=b"canonical\n",
        )


def test_prerequisite_archive_reports_existing_archive_lstat_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archive lstat failures cannot be mistaken for an absent attempt record."""

    repository = tmp_path / "repository"
    archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / "study-r4"
        / "prerequisites.raw.json"
    )
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"canonical\n")
    original_lstat = Path.lstat
    calls = 0

    def fail_second_archive_lstat(path: Path) -> os.stat_result:
        nonlocal calls
        if path == archive:
            calls += 1
            if calls == 2:
                raise OSError("simulated archive lstat failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_second_archive_lstat)
    with pytest.raises(ValueError, match="could not inspect archived prerequisite document"):
        study._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id="study-r4",
            content=b"canonical\n",
        )


def test_collect_cli_freezes_its_attempt_before_any_input_bridge_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every post-syntax collection failure consumes the study ID before bridge validation."""

    repository = tmp_path / "repository"
    repository.mkdir()
    marker = repository / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1" / "collection.json"
    candidate = repository / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"
    calls = 0

    def reject_bridge(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        assert marker.is_file()
        raise TrafficlabError("synthetic input bridge failure", corrective_action="preserve the attempt")

    monkeypatch.setattr(study, "_collection_inputs_from_prerequisites", reject_bridge)
    argv = (
        "collect",
        "--url",
        "https://downloads.example.test/object.bin",
        "--study-id",
        "study-1",
        "--prerequisites",
        "examples/validation_study/prerequisites.json",
    )

    assert study.main(argv, repository_root=repository) == 2
    assert marker.is_file()
    assert not candidate.exists()
    assert study.main(argv, repository_root=repository) == 2
    assert calls == 1


def test_public_prerequisites_then_collect_binds_the_raw_published_marker_before_transformation(tmp_path: Path) -> None:
    """The public phase transition checks schema-1 publication bytes before schema-3 retention."""

    repository = tmp_path / "repository"
    _write_prerequisite_repository_inputs(repository)
    scripted = _ScriptedPrerequisiteRunner(repository)
    now = datetime(2026, 8, 16, tzinfo=UTC)
    assert (
        study.main(
            ("prerequisites", "--url", scripted.url, "--study-id", scripted.study_id),
            repository_root=repository,
            runner=scripted,
            utc_now=lambda: now,
        )
        == 0
    )

    collection_builds: list[tuple[str, ...]] = []
    collection_cleanups: list[tuple[str, ...]] = []

    def collection_runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        command = tuple(argv)
        if command == ("git", "rev-parse", "HEAD^{tree}"):
            return subprocess.CompletedProcess(command, 0, stdout=b"d" * 40 + b"\n", stderr=b"")
        if command == (
            "docker",
            "image",
            "inspect",
            f"trafficlab-validation-{scripted.study_id}:collection-capture",
            "--format",
            "{{.Id}}",
        ):
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"not present\n")
        if command[:2] == ("docker", "build"):
            collection_builds.append(command)
            iidfile = Path(command[command.index("--iidfile") + 1])
            iidfile.write_text(f"{scripted.capture_id}\n", encoding="ascii")
            return subprocess.CompletedProcess(command, 0, stdout=b"rebuilt\n", stderr=b"")
        if command == ("docker", "image", "inspect", scripted.capture_id, "--format", "{{.Id}}"):
            return subprocess.CompletedProcess(command, 0, stdout=f"{scripted.capture_id}\n".encode(), stderr=b"")
        if command == (
            "docker",
            "image",
            "rm",
            "--force",
            f"trafficlab-validation-{scripted.study_id}:collection-capture",
        ):
            collection_cleanups.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=b"removed\n", stderr=b"")
        return scripted(argv, cwd=cwd, check=check, capture_output=capture_output, shell=shell, timeout=timeout)

    training_calls: list[Path] = []

    def stop_at_training(path: Path) -> RunResult:
        training_calls.append(path)
        raise ValueError("training callback reached")

    argv = (
        "collect",
        "--url",
        scripted.url,
        "--study-id",
        scripted.study_id,
        "--prerequisites",
        "examples/validation_study/prerequisites.json",
    )
    with pytest.raises(ValueError, match="training callback reached"):
        study.main(
            argv,
            repository_root=repository,
            runner=collection_runner,
            run=stop_at_training,
            capture=lambda _path: pytest.fail("held-out capture must not begin"),
        )

    prerequisite = repository / "examples" / "validation_study" / "prerequisites.json"
    attempt = repository / "examples" / "validation_study" / ".study-work" / "attempts" / scripted.study_id
    candidate = repository / "examples" / "validation_study" / "evidence" / ".candidates" / scripted.study_id
    success = cast(dict[str, object], json.loads((attempt / "prerequisites-success.json").read_text(encoding="utf-8")))
    assert success["prerequisites_identity"] == identify_bytes(prerequisite.read_bytes()).as_dict()
    assert (candidate / "prerequisites.json").read_bytes() != prerequisite.read_bytes()
    assert (attempt / "collection.json").is_file()
    assert (attempt / "frozen-protocol.json").is_file()
    assert len(training_calls) == 1
    assert collection_builds == [
        study.cold_capture_build_argv(  # pyright: ignore[reportPrivateUsage]
            f"trafficlab-validation-{scripted.study_id}:collection-capture",
            attempt / "collection-capture.iid",
        )
    ]
    assert collection_cleanups == [
        ("docker", "image", "rm", "--force", f"trafficlab-validation-{scripted.study_id}:collection-capture")
    ]
    assert not (attempt / "collection-capture.iid").exists()
    assert study.main(argv, repository_root=repository, runner=collection_runner) == 2
    assert len(training_calls) == 1


def test_cold_capture_build_argv_freezes_task9_reproducibility_controls(tmp_path: Path) -> None:
    """Study prerequisites use the same cold locked capture-build contract as the Docker owner."""

    assert study.cold_capture_build_argv(
        "trafficlab-validation-study-1:capture",
        tmp_path / "capture.iid",
    ) == (
        "docker",
        "build",
        "--pull",
        "--no-cache",
        "--provenance=false",
        "--platform",
        "linux/amd64",
        "--output",
        "type=image,rewrite-timestamp=true,unpack=false",
        "--tag",
        "trafficlab-validation-study-1:capture",
        "--iidfile",
        str(tmp_path / "capture.iid"),
        "docker/capture",
    )


@pytest.mark.parametrize(
    ("tag", "iidfile", "error", "message"),
    (
        (object(), Path("capture.iid"), ValueError, "capture image tag must be a nonempty string"),
        ("trafficlab-validation-study-1:capture", object(), TypeError, "iidfile must be a pathlib.Path"),
    ),
)
def test_cold_capture_build_argv_rejects_invalid_boundary_types(
    tag: object,
    iidfile: object,
    error: type[Exception],
    message: str,
) -> None:
    """The public cold-build boundary retains deterministic runtime validation."""

    with pytest.raises(error, match=message):
        study.cold_capture_build_argv(tag, iidfile)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("case", "expected_kind", "expected_path"),
    (
        ("document", "artifact_corrupt", "prerequisites.json"),
        ("environment", "artifact_foreign", "prerequisites.json"),
        ("output_identity", "artifact_foreign", "prerequisites/docker_matrix.stdout"),
        ("command", "artifact_foreign", "prerequisites/docker_matrix.command.json"),
        ("status", "artifact_foreign", "prerequisites/docker_matrix.status.json"),
        ("utf8", "artifact_corrupt", "prerequisites/docker_matrix.stdout"),
        ("junit_invalid", "artifact_corrupt", "prerequisites/docker_matrix.junit.xml"),
        ("junit_counts", "artifact_foreign", "prerequisites/docker_matrix.junit.xml"),
    ),
)
def test_offline_auditor_covers_retained_prerequisite_rejection_branches(
    tmp_path: Path,
    case: str,
    expected_kind: str,
    expected_path: str,
) -> None:
    """Retained prerequisite output evidence is independently checked through the public audit boundary."""
    repository, candidate = _copy_validation_study_candidate(tmp_path, generated=True)
    prerequisite_path = candidate / "prerequisites.json"
    document = study.parse_retained_prerequisites(prerequisite_path.read_bytes())
    command = next(
        item for item in cast(list[dict[str, object]], document["commands"]) if item["kind"] == "docker_matrix"
    )

    def replace_output(field: str, content: bytes) -> None:
        output = cast(dict[str, object], command[field])
        path = candidate / cast(str, output["path"])
        path.write_bytes(content)
        output["identity"] = identify_bytes(content).as_dict()

    render_document = True
    if case == "document":
        prerequisite_path.write_bytes(b"{}\n")
        render_document = False
    elif case == "environment":
        cast(dict[str, object], document["environment"])["source_tree"] = "c" * 40
    elif case == "output_identity":
        output = cast(dict[str, object], command["stdout"])
        (candidate / cast(str, output["path"])).write_bytes(b"changed stdout\n")
        render_document = False
    elif case == "command":
        replace_output("command", b'{"argv":[]}\n')
    elif case == "status":
        replace_output(
            "status",
            b'{"exit_status":0,"tests":{"errors":0,"failed":0,"passed":999,"skipped":0,"total":999}}\n',
        )
    elif case == "utf8":
        replace_output("stdout", b"\xff")
    elif case == "junit_invalid":
        replace_output("junit", b"<unexpected/>")
    else:
        replace_output("junit", b'<testsuite tests="2" failures="0" errors="0" skipped="0"/>')
    if render_document:
        prerequisite_path.write_bytes(study.render_retained_prerequisites(document))
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
    ) == (expected_kind, "publication", expected_path, "not_published", "primary")


@pytest.mark.parametrize("case", ("recorded_lock", "image_lock"))
def test_offline_auditor_rejects_environment_binding_after_the_first_identity_check(
    tmp_path: Path,
    case: str,
) -> None:
    """The auditor separately binds current lock bytes and image-lock identities."""
    repository, candidate = _copy_validation_study_candidate(tmp_path, generated=True)
    environment_path = candidate / "environment.json"
    environment = cast(dict[str, object], json.loads(environment_path.read_text(encoding="utf-8")))
    if case == "recorded_lock":
        changed_lock = b"different committed lock\n"
        (repository / "uv.lock").write_bytes(changed_lock)
        environment["uv_lock_identity"] = identify_bytes(changed_lock).as_dict()
    else:
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


def test_offline_auditor_rejects_training_configuration_with_foreign_image_lock_binding(tmp_path: Path) -> None:
    """Every retained training configuration is bound to the prerequisite image references."""
    repository, candidate = _copy_validation_study_candidate(tmp_path, generated=True)
    target_reference = study.TARGET_REFERENCE.encode("ascii")
    foreign_reference = b"curlimages/curl@sha256:" + b"1" * 64
    for name in ("configs/training-short-r1.portable.toml", "configs/training-short-r1.realized.toml"):
        path = candidate / name
        content = path.read_bytes()
        assert target_reference in content
        path.write_bytes(content.replace(target_reference, foreign_reference))
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
    ) == ("artifact_foreign", "publication", "training/short/r1", "not_published", "primary")


def test_offline_auditor_rejects_training_configuration_without_the_frozen_curl_argv(tmp_path: Path) -> None:
    """A candidate cannot replace the frozen workload command while retaining the image lock."""

    repository, candidate = _copy_validation_study_candidate(tmp_path, generated=True)
    assert auditor.audit_bundle(candidate, repository=repository).bundle == candidate
    for name in ("configs/training-short-r1.portable.toml", "configs/training-short-r1.realized.toml"):
        path = candidate / name
        content = path.read_bytes()
        assert USER_AGENT.encode("ascii") in content
        path.write_bytes(content.replace(USER_AGENT.encode("ascii"), b"trafficlab/0.0 (+https://invalid.example)", 1))
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
    ) == ("artifact_foreign", "publication", "training/short/r1", "not_published", "primary")


@pytest.mark.parametrize(
    ("section", "expected_directory"),
    (("training", "training/short/r1"), ("held_out", "held_out/short")),
)
def test_offline_auditor_rejects_capture_lineage_that_disagrees_with_retained_bytes(
    tmp_path: Path,
    section: str,
    expected_directory: str,
) -> None:
    """Training and held-out capture provenance cannot be substituted after capture validation."""
    repository, candidate = _copy_validation_study_candidate(tmp_path, generated=True)
    index = _candidate_index(candidate)
    record = cast(list[dict[str, object]], index[section])[0]
    cast(dict[str, object], record["capture_lineage"])["capture_tool_version"] = "tampered"
    _write_candidate_index(candidate, index)
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
    ) == ("artifact_foreign", "publication", expected_directory, "not_published", "primary")


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("rule", "scientific_semantics_incompatible"),
        ("count", "artifact_corrupt"),
        ("duplicate", "artifact_foreign"),
        ("mismatch", "artifact_foreign"),
        ("order", "artifact_foreign"),
    ),
)
def test_offline_auditor_reconstructs_all_training_model_selection_rejections(
    tmp_path: Path,
    case: str,
    expected_kind: str,
) -> None:
    """The protocol's retained training-only selection is recomputed before held-out evaluation."""
    repository, candidate = _copy_validation_study_candidate(tmp_path, generated=True)
    protocol_path = candidate / "protocol.json"
    protocol = cast(dict[str, object], json.loads(protocol_path.read_text(encoding="utf-8")))
    selection = cast(dict[str, object], protocol["model_selection"])
    selected = cast(list[dict[str, object]], selection["selected"])
    if case == "rule":
        selection["rule"] = "first_training_record"
    elif case == "count":
        selection["selected"] = []
    elif case == "duplicate":
        selected[1] = copy.deepcopy(selected[0])
    elif case == "mismatch":
        selected_repeat = cast(int, selected[0]["repeat"])
        selected[0]["repeat"] = 1 if selected_repeat != 1 else 2
    else:
        selection["selected"] = list(reversed(selected))
    _write_canonical_json(protocol_path, protocol)
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
    ) == (expected_kind, "publication", "protocol", "not_published", "primary")


def test_candidate_natural_variation_derives_each_directional_reference_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Natural variation compares repeated captures at each reference-derived W, not a metric bin width."""
    base_config = load_configuration_pair(_FIT_FIXTURE / "experiment.toml").realized
    config = base_config.model_copy(
        update={
            "similarity": base_config.similarity.model_copy(
                update={"max_direction_bin_cells": 2_000, "multiscale_widths_seconds": (0.001, 0.01)}
            )
        }
    )
    frozen_bin_width = max(config.similarity.multiscale_widths_seconds)
    assert frozen_bin_width == 0.01

    def trace(spacing: float) -> tuple[TraceEvent, ...]:
        return tuple(
            TraceEvent(
                timestamp=index * spacing,
                direction=Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND,
                frame_length=128 + index,
            )
            for index in range(20)
        )

    metadata = parse_capture_metadata(_CAPTURE_BYTES, source=_FIT_FIXTURE / "capture.json")

    configurations = tuple(config.model_copy(deep=True) for _ in range(3))
    assert configurations[0].similarity is not configurations[1].similarity

    def training(
        repeat: int,
        raw_reference: tuple[TraceEvent, ...],
        configuration: ExperimentConfig,
    ) -> study._CandidateTraining:  # pyright: ignore[reportPrivateUsage]
        reference, window = normalize_reference(raw_reference)
        return study._CandidateTraining(  # pyright: ignore[reportPrivateUsage]
            workload="short",
            repeat=repeat,
            directory=tmp_path / f"r{repeat}",
            config=configuration,
            contents={},
            metadata=metadata,
            reference=reference,
            observation_window_seconds=window,
            runtime_seconds=0.0,
            checkpoint=cast(CheckpointState, object()),
            comparison=cast(ComparisonResult, object()),
        )

    records = tuple(
        training(repeat, raw_reference, configurations[repeat - 1])
        for repeat, raw_reference in enumerate((trace(0.005), trace(0.03), trace(0.025)), start=1)
    )
    with pytest.raises(TrafficlabError, match="invalid generated trace: at least two events"):
        compare_traces(
            align_generated(records[0].reference, frozen_bin_width),
            align_generated(records[1].reference, frozen_bin_width),
            frozen_bin_width,
            config.similarity,
        )

    first_reference, forward_window = normalize_reference(records[0].reference)
    second_reference, reverse_window = normalize_reference(records[1].reference)
    forward = compare_traces(
        first_reference,
        align_generated(records[1].reference, forward_window),
        forward_window,
        config.similarity,
    )
    reverse = compare_traces(
        second_reference,
        align_generated(records[0].reference, reverse_window),
        reverse_window,
        config.similarity,
    )

    settings_calls: list[SimilarityConfig] = []
    original_compare = study.compare_traces

    def comparison_spy(
        reference: tuple[TraceEvent, ...],
        generated: tuple[TraceEvent, ...],
        window: float,
        settings: SimilarityConfig,
    ) -> ComparisonResult:
        settings_calls.append(settings)
        return original_compare(reference, generated, window, settings)

    monkeypatch.setattr(study, "compare_traces", comparison_spy)
    result = study._candidate_natural_variation(records)  # pyright: ignore[reportPrivateUsage]
    assert settings_calls[:2] == [records[0].config.similarity, records[1].config.similarity]
    assert settings_calls[0] is records[0].config.similarity
    assert settings_calls[1] is records[1].config.similarity
    first_pair = cast(dict[str, object], cast(list[object], result["pairs"])[0])
    forward_score = cast(dict[str, object], first_pair["forward"])
    reverse_score = cast(dict[str, object], first_pair["reverse"])
    symmetric = cast(dict[str, object], first_pair["symmetric_mean"])
    assert forward_score == study._candidate_score(forward)  # pyright: ignore[reportPrivateUsage]
    assert reverse_score == study._candidate_score(reverse)  # pyright: ignore[reportPrivateUsage]
    assert symmetric["aggregate"] == fmean((forward.aggregate_score, reverse.aggregate_score))
    for method in ("frame_size_ks", "iat_ks", "autocorrelation", "multiscale_rate"):
        assert cast(dict[str, float], symmetric["methods"])[method] == fmean(
            (forward.methods[method].score, reverse.methods[method].score)
        )


def test_offline_auditor_uses_each_directional_similarity_settings_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Independent reconstruction applies the settings belonging to each reference trace."""

    repository, candidate = _copy_validation_study_candidate(tmp_path, generated=True)
    original_training = auditor._training  # pyright: ignore[reportPrivateUsage]
    original_report_inputs = auditor._report_inputs  # pyright: ignore[reportPrivateUsage]
    original_compare = auditor.compare_traces
    expected_settings: dict[tuple[tuple[float, Direction, int], ...], SimilarityConfig] = {}
    calls: list[tuple[tuple[tuple[float, Direction, int], ...], SimilarityConfig]] = []
    recording = False

    def trace_key(events: Sequence[TraceEvent]) -> tuple[tuple[float, Direction, int], ...]:
        return tuple((event.timestamp, event.direction, event.frame_length) for event in events)

    def isolated_training(*args: Any, **kwargs: Any) -> auditor._Training:  # pyright: ignore[reportPrivateUsage]
        item = original_training(*args, **kwargs)
        return replace(item, config=item.config.model_copy(deep=True))

    def report_inputs_spy(*args: Any, **kwargs: Any) -> dict[str, object]:
        nonlocal recording
        training = cast(Sequence[auditor._Training], args[0])  # pyright: ignore[reportPrivateUsage]
        expected_settings.update(
            {trace_key(normalize_reference(item.reference)[0]): item.config.similarity for item in training}
        )
        recording = True
        try:
            return original_report_inputs(*args, **kwargs)
        finally:
            recording = False

    def comparison_spy(
        reference: tuple[TraceEvent, ...],
        generated: tuple[TraceEvent, ...],
        window: float,
        settings: SimilarityConfig,
    ) -> ComparisonResult:
        if recording:
            calls.append((trace_key(reference), settings))
        return original_compare(reference, generated, window, settings)

    monkeypatch.setattr(auditor, "_training", isolated_training)
    monkeypatch.setattr(auditor, "_report_inputs", report_inputs_spy)
    monkeypatch.setattr(auditor, "compare_traces", comparison_spy)

    assert auditor.audit_bundle(candidate, repository=repository).bundle == candidate
    assert len(calls) == 18
    assert all(settings is expected_settings[reference] for reference, settings in calls)


@pytest.mark.parametrize(
    ("stdout", "expected"),
    (
        (bytes((255, 0)), "path is not UTF-8"),
        (b"foreign\0foreign\0", "paths must be unique"),
        (b"elsewhere\0", "paths do not match the inspected worktree"),
    ),
)
def test_prerequisite_ignored_path_parser_rejects_invalid_match_records(
    tmp_path: Path,
    stdout: bytes,
    expected: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        assert argv == ("git", "check-ignore", "-z", "--stdin")
        assert input == b"foreign\0"
        assert cwd == repository
        assert check is False
        assert capture_output is True
        assert shell is False
        assert timeout == study.SUBPROCESS_TIMEOUTS["git_or_version"]
        return subprocess.CompletedProcess(tuple(argv), 0, stdout=stdout, stderr=b"")

    assert study._ignored_prerequisite_worktree_paths(repository, (), runner=runner) == frozenset()  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match=expected):
        study._ignored_prerequisite_worktree_paths(repository, ("foreign",), runner=runner)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("failure", ("directory", "entry"))
def test_prerequisite_worktree_entry_scan_rejects_filesystem_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["directory", "entry"],
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    entry = repository / "source.py"
    entry.write_text("pass\n", encoding="utf-8")

    if failure == "directory":

        def unavailable_iterdir(path: Path) -> Any:
            assert path == repository
            raise OSError("directory unavailable")

        monkeypatch.setattr(Path, "iterdir", unavailable_iterdir)
    else:

        def one_entry(path: Path) -> Any:
            assert path == repository
            return iter((entry,))

        def unavailable_lstat(path: Path) -> Any:
            assert path == entry
            raise OSError("entry unavailable")

        monkeypatch.setattr(Path, "iterdir", one_entry)
        monkeypatch.setattr(Path, "lstat", unavailable_lstat)

    with pytest.raises(ValueError):
        study._prerequisite_worktree_entries(repository)  # pyright: ignore[reportPrivateUsage]


def test_prerequisite_worktree_cleanliness_rejects_unignored_special_entries(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "ordinary.txt").write_text("ordinary\n", encoding="utf-8")
    special = repository / "foreign.fifo"
    os.mkfifo(special)
    runner = _ScriptedPrerequisiteRunner(repository)
    runner.ignored_worktree_paths = frozenset()

    with pytest.raises(ValueError, match="non-regular entry"):
        study._require_clean_prerequisite_worktree(repository, runner=runner)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        (".superpowers/state", True),
        (".coverage", True),
        ("TASK.md", True),
        (".env.local", True),
        (".coverage.local", True),
        ("pkg/__pycache__/x.pyc", True),
        ("pkg.egg-info/METADATA", True),
        ("module.pyd", True),
        ("collector.log", True),
        ("runs/local/state.json", True),
        ("examples/validation_study/configs/short.toml", True),
        ("examples/validation_study/results.json", True),
        ("examples/validation_study/.study-work/state", True),
        ("examples/validation_study/.candidates/study-1/state", True),
        ("examples/validation_study/evidence/.candidates/study-1/state", True),
        ("examples/validation_study/evidence/.study-1.tmp/state", True),
        ("foreign.py", False),
    ),
)
def test_auditor_ignored_worktree_path_policy_is_explicit(path: str, expected: bool) -> None:
    assert auditor._permitted_ignored_relocated_worktree_path(path) is expected  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        (".superpowers/state", True),
        (".coverage", True),
        ("TASK.md", True),
        (".env.local", True),
        (".coverage.local", True),
        ("pkg/__pycache__/x.pyc", True),
        ("pkg.egg-info/METADATA", True),
        ("module.pyd", True),
        ("collector.log", True),
        ("runs/local/state.json", True),
        ("examples/validation_study/prerequisites.json", True),
        ("examples/validation_study/results.json", True),
        ("examples/validation_study/configs/short.toml", True),
        ("examples/validation_study/.study-work/state", True),
        ("examples/validation_study/.candidates/study-1/state", True),
        ("examples/validation_study/evidence/.candidates/study-1/state", True),
        ("examples/validation_study/evidence/.study-1.tmp/state", True),
        ("foreign.py", False),
    ),
)
def test_prerequisite_ignored_worktree_path_policy_is_explicit(path: str, expected: bool) -> None:
    assert study._permitted_ignored_prerequisite_worktree_path(path) is expected  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("status", (b"", b"?? foreign\0"))
def test_auditor_worktree_status_parser_accepts_empty_and_canonical_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: bytes,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()

    def git_bytes(*_args: object, **_kwargs: object) -> bytes:
        return status

    monkeypatch.setattr(auditor, "_git_bytes", git_bytes)
    auditor._relocated_worktree_paths(repository)  # pyright: ignore[reportPrivateUsage]


def test_auditor_worktree_entry_scan_covers_regular_directory_special_and_skipped_paths(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "source.py").write_text("pass\n", encoding="utf-8")
    nested = repository / "nested"
    nested.mkdir()
    (nested / "child.py").write_text("pass\n", encoding="utf-8")
    (repository / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    (repository / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    (repository / ".venv" / "deep").mkdir(parents=True)
    (repository / ".venv" / "deep" / "ignored.py").write_text("pass\n", encoding="utf-8")
    special = repository / "special.fifo"
    os.mkfifo(special)

    entries, nonregular = auditor._relocated_worktree_entry_paths(  # pyright: ignore[reportPrivateUsage]
        repository,
        candidate_paths=("candidate.txt",),
    )

    assert "source.py" in entries
    assert "nested/child.py" in entries
    assert "candidate.txt" not in entries
    assert ".git" not in entries
    assert ".venv/deep/ignored.py" not in entries
    assert nonregular == ("special.fifo",)


def test_prerequisite_cleanliness_continues_past_permitted_ignored_special_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    runner = _ScriptedPrerequisiteRunner(repository)

    def entries(_root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (("first", "second"), ("first", "second"))

    def ignored_paths(_root: Path, _paths: Sequence[str], *, runner: Any) -> frozenset[str]:
        return frozenset({"first"})

    def permitted_path(path: str) -> bool:
        return path == "first"

    monkeypatch.setattr(study, "_prerequisite_worktree_entries", entries)
    monkeypatch.setattr(study, "_ignored_prerequisite_worktree_paths", ignored_paths)
    monkeypatch.setattr(study, "_permitted_ignored_prerequisite_worktree_path", permitted_path)

    with pytest.raises(ValueError, match="non-regular entry: second"):
        study._require_clean_prerequisite_worktree(repository, runner=runner)  # pyright: ignore[reportPrivateUsage]


def test_prerequisite_cleanliness_uses_real_git_stdin_nul_records_for_ignored_foreign_names(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "init", "--quiet"), cwd=repository, check=True, capture_output=True)
    names = ("foreign space", "foreign\nnewline")
    (repository / ".git" / "info" / "exclude").write_text("foreign*\n", encoding="utf-8")
    for name in names:
        (repository / name).write_text("ignored\n", encoding="utf-8")
    calls: list[tuple[tuple[str, ...], bytes | None]] = []

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        assert cwd == repository
        assert check is False
        assert capture_output is True
        assert shell is False
        assert timeout == study.SUBPROCESS_TIMEOUTS["git_or_version"]
        calls.append((tuple(argv), input))
        return subprocess.run(
            argv,
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            shell=shell,
            timeout=timeout,
            input=input,
        )

    with pytest.raises(ValueError, match="ignored prerequisite worktree entry is not permitted"):
        study._require_clean_prerequisite_worktree(repository, runner=runner)  # pyright: ignore[reportPrivateUsage]

    check_ignore = [call for call in calls if call[0][:3] == ("git", "check-ignore", "-z")]
    assert check_ignore == [
        (
            ("git", "check-ignore", "-z", "--stdin"),
            b"".join(os.fsencode(name) + b"\0" for name in sorted(names)),
        )
    ]


def test_prerequisite_cleanliness_rejects_non_utf8_ignored_git_record_after_byte_exact_input(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "init", "--quiet"), cwd=repository, check=True, capture_output=True)
    raw_name = b"foreign-\xff"
    name = os.fsdecode(raw_name)
    (repository / ".git" / "info" / "exclude").write_text("foreign*\n", encoding="utf-8")
    (repository / name).write_text("ignored\n", encoding="utf-8")
    check_ignore_inputs: list[bytes | None] = []

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        assert cwd == repository
        if tuple(argv[:3]) == ("git", "check-ignore", "-z"):
            check_ignore_inputs.append(input)
        return subprocess.run(
            argv,
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            shell=shell,
            timeout=timeout,
            input=input,
        )

    with pytest.raises(ValueError, match="ignored prerequisite path is not UTF-8"):
        study._require_clean_prerequisite_worktree(repository, runner=runner)  # pyright: ignore[reportPrivateUsage]

    assert check_ignore_inputs == [raw_name + b"\0"]


def test_prerequisite_ignored_path_codec_skips_real_git_for_an_empty_path_set(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    calls: list[tuple[str, ...]] = []

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(tuple(argv))
        return subprocess.run(
            argv,
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            shell=shell,
            timeout=timeout,
            input=input,
        )

    assert study._ignored_prerequisite_worktree_paths(repository, (), runner=runner) == frozenset()  # pyright: ignore[reportPrivateUsage]
    assert calls == []
