from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
from collections.abc import Callable, Iterator, Sequence
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from statistics import fmean, variance
from typing import Any, Literal, cast

import pytest

from scripts import audit_validation_study as auditor
from scripts import generate_validation_study_fixture as fixture_generator
from scripts import run_validation_study as study
from tests.conftest import retained_test_body_failure
from tests.fixtures.paths import (
    PIPELINE_FIXTURE_ROOT,
    VALIDATION_STUDY_CANDIDATE,
)
from trafficlab import USER_AGENT
from trafficlab.artifacts import append_run_log
from trafficlab.capture import CaptureResult
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.comparison import ComparisonResult, compare_experiment, compare_traces
from trafficlab.compatibility import ContentIdentity
from trafficlab.fitting import fit_experiment
from trafficlab.generation import generate_experiment
from trafficlab.genetic.checkpoint import CheckpointState, encode_rng_state
from trafficlab.genetic.strategy import make_strategy_context
from trafficlab.genetic.types import Candidate, CandidateId, MethodTrialResult, TrialResult, rebuild_genetic_record
from trafficlab.models.common import MARKOV_MODEL_DIAGNOSTIC_KEYS, make_rng
from trafficlab.models.registry import BestModel, get_family, make_best_model
from trafficlab.preflight import PreparedExperiment, open_or_prepare_experiment
from trafficlab.run import RunDependencies, RunResult, run_experiment
from trafficlab.statistics import bootstrap_interval
from trafficlab.trace import Direction, TraceEvent

HASH = "a" * 64

IMAGE_ID = f"sha256:{'b' * 64}"

ROOT = Path(__file__).resolve().parents[2]

FIT_FIXTURE = PIPELINE_FIXTURE_ROOT / "fit"

CAPTURE_BYTES = (FIT_FIXTURE / "capture.json").read_bytes()

REFERENCE_BYTES = (FIT_FIXTURE / "reference.pcapng").read_bytes()

CAPTURE_DOCKERFILE = (ROOT / "docker" / "capture" / "Dockerfile").read_bytes()

CAPTURE_SCRIPT = (ROOT / "docker" / "capture" / "capture.sh").read_bytes()

_CAPTURE_IMAGE_LOCK = json.loads((ROOT / "docker" / "capture" / "image-lock.json").read_text(encoding="utf-8"))

CAPTURE_IMAGE_ID = cast(str, _CAPTURE_IMAGE_LOCK["expected_capture_image_id"])

_REAL_SUBPROCESS_RUN = subprocess.run

_shared_validation_study_repository_path: Path | None = None

_current_validation_study_test_name: str | None = None

_current_isolated_validation_study_worktrees: list[Path] | None = None

ISOLATED_VALIDATION_STUDY_REPOSITORY_TESTS = frozenset(
    {
        "test_audited_bundle_publication_rechecks_candidate_and_preserves_an_occupied_destination",
        "test_audited_bundle_rejects_the_first_primary_without_publication_residue",
        "test_offline_auditor_allows_a_clean_committed_accepted_bundle",
        "test_offline_auditor_allows_document_evidence_and_ignored_candidate_worktree_changes",
        "test_offline_auditor_checks_the_worktree_before_committed_descendant_changes",
        "test_offline_auditor_classifies_ignored_special_entry_git_failures",
        "test_offline_auditor_rejects_environment_binding_after_the_first_identity_check",
        "test_offline_auditor_rejects_local_exclude_ignored_non_evidence_entries",
        "test_offline_auditor_rejects_non_evidence_worktree_changes",
        "test_offline_auditor_rejects_untracked_nonregular_source_paths",
        "test_offline_auditor_rejects_untrusted_fixture_profile_source_bytes",
        "test_offline_bundle_audit_reconstructs_environment_and_final_controls",
        "test_simultaneous_evidence_mismatches_preserve_the_first_complete_primary_and_all_inventories",
    }
)


def write_prerequisite_repository_inputs(repository_root: Path) -> None:
    capture_root = repository_root / "docker" / "capture"
    capture_root.mkdir(parents=True)
    for name in ("Dockerfile", "capture.sh", "image-lock.json"):
        shutil.copy2(ROOT / "docker" / "capture" / name, capture_root / name)
    shutil.copy2(ROOT / "uv.lock", repository_root / "uv.lock")


class ScriptedPrerequisiteRunner:
    def __init__(self, repository_root: Path, mutation: str = "happy", *, study_id: str = "study-1") -> None:
        self.root = repository_root
        self.mutation = mutation
        self.study_id = study_id
        self.url = "https://downloads.example.test/object.bin"
        self.final_url = "https://cdn.example.test/object.bin"
        self.target_id = f"sha256:{'b' * 64}"
        self.capture_id = CAPTURE_IMAGE_ID
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


def frozen(document: object) -> study.FrozenJsonObject:
    return cast(
        study.FrozenJsonObject,
        study._freeze_json(cast(study.JsonValue, document)),  # pyright: ignore[reportPrivateUsage]
    )


def valid_prerequisite(*, study_id: str = "study-1") -> study.PrerequisiteResults:
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
        "stdout_sha256": HASH,
        "stderr_sha256": HASH,
        "junit_sha256": HASH,
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
        tools=frozen(
            {
                "docker_engine_version": "27.0.0",
                "docker_compose_version": "2.29.0",
                "host_architecture": "x86_64",
                "kernel_release": "test-kernel",
                "platform": "Linux-test",
                "python_implementation": "CPython",
                "python_version": "3.12.3",
                "trafficlab_version": "0.1.0",
                "uv_lock_sha256": HASH,
            }
        ),
        images=frozen(
            {
                "target_reference": study.TARGET_REFERENCE,
                "target_image_id": IMAGE_ID,
                "target_repo_digests": [study.TARGET_REFERENCE],
                "target_config_user": "",
                "capture_image_id": f"sha256:{'d' * 64}",
                "capture_dockerfile_sha256": HASH,
                "capture_script_sha256": HASH,
            }
        ),
        capability=frozen(
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
                "canary_sha256": HASH,
                "container_id": "e" * 64,
                "stdout_sha256": HASH,
                "stderr_sha256": HASH,
                "used_image_default_user": True,
                "mount_directory_mode": 493,
                "canary_file_mode": 438,
                "canary_archive_mode": 384,
                "container_cleanup_verified": True,
            }
        ),
        config_sha256=frozen({"short": HASH, "streaming": HASH, "bursty": HASH}),
        commands=(
            frozen(command),
            frozen({**command, "kind": "internet_smoke", "argv": internet_argv}),
        ),
    )


def changed_config_paths(left: object, right: object, *, prefix: str = "") -> set[str]:
    if type(left) is dict and type(right) is dict:
        left_mapping = cast(dict[str, object], left)
        right_mapping = cast(dict[str, object], right)
        assert set(left_mapping) == set(right_mapping)
        changes: set[str] = set()
        for key in left_mapping:
            child = f"{prefix}.{key}" if prefix else key
            changes.update(changed_config_paths(left_mapping[key], right_mapping[key], prefix=child))
        return changes
    return set() if left == right else {prefix}


def write_checked_configs(
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
        valid_prerequisite(),
        config_sha256=frozen({name: hashlib.sha256(content).hexdigest() for name, content in contents.items()}),
    )
    return prerequisite, contents


def write_retained_prerequisite_evidence(
    repository_root: Path,
    prerequisite: study.PrerequisiteResults,
) -> study.PrerequisiteResults:
    capture_root = repository_root / "docker" / "capture"
    capture_root.mkdir(parents=True, exist_ok=True)
    dockerfile = CAPTURE_DOCKERFILE
    capture_script = CAPTURE_SCRIPT
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
    capability_headers = response_headers(0, 0)
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
        images=frozen(images),
        capability=frozen(capability),
        commands=(frozen(commands[0]), frozen(commands[1])),
    )


def response_headers(
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


def score(value: float) -> dict[str, object]:
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
        "bootstrap": bootstrap_interval(numbers, seed=study._BOOTSTRAP_SEED).as_dict(),  # pyright: ignore[reportPrivateUsage]
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
                "selection_score": score(fitness),
            }
        )
    return result


def transfer_responses(study_id: str, run_id: str, workload: str) -> list[dict[str, object]]:
    if workload == "short":
        transfers = [(0, 1048575, "short.headers")]
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
            "header_sha256": HASH,
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
        "transfer_responses": transfer_responses(study_id, run_id, workload),
        "artifact_sha256": {name: HASH for name in study.ARTIFACT_NAMES},
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
            "score": score(fresh_simulation_value),
            "source": "run_experiment_fit_outcome",
        },
        "published": {"seed": 97, "score": score(published_value)},
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


def valid_result_document(repository_root: Path) -> dict[str, object]:
    study_id = "study-1"
    url = "https://downloads.example.test/object.bin"
    prerequisite = cast(dict[str, object], json.loads(study.render_prerequisite_results(valid_prerequisite())))
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
                    "forward": score(forward_value),
                    "reverse": score(reverse_value),
                    "symmetric": score((forward_value + reverse_value) / 2.0),
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
        "guard_stdout_sha256": HASH,
        "guard_stderr_sha256": HASH,
        "elapsed_seconds": 4.0,
        "changed_config_fields": ["run.directory"],
        "same_locked_config": True,
        "seeded_artifact_count": 0,
        "cleanup_verified": True,
        "reuse": {"capture": False, "best_model": False, "generated": False, "similarity": False},
        "transfer_responses": transfer_responses(study_id, reproduction_run_id, "streaming"),
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
            "score": score(reproduction_held),
            "source": "post_cli_evaluate_final",
        },
        "published": {"seed": 97, "score": score(reproduction_published)},
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
            "reference_similarity": score(0.5),
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
            "target_image_id": IMAGE_ID,
            "capture_image_id": f"sha256:{'d' * 64}",
            "study_date_utc": "2026-08-13T13:00:00Z",
        },
        "protocol": {
            "study_id": study_id,
            "url": url,
            "capability": capability,
            "prerequisites_sha256": HASH,
            "target_reference": study.TARGET_REFERENCE,
            "capture_image_id": f"sha256:{'d' * 64}",
            "transfer_evidence_mount_source": f"examples/validation_study/.study-work/mount/{study_id}",
            "base_config_sha256": {"short": HASH, "streaming": HASH, "bursty": HASH},
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


def study_result_value(document: dict[str, object]) -> study.StudyResults:
    run_values: list[study.StudyRunRecord] = []
    for item in cast(list[dict[str, object]], document["runs"]):
        champions = tuple(frozen(value) for value in cast(list[study.JsonObject], item["family_champions"]))
        run_values.append(
            study.StudyRunRecord(
                execution_order=cast(int, item["execution_order"]),
                run_id=cast(str, item["run_id"]),
                key=frozen(cast(study.JsonObject, item["key"])),
                config_path=cast(str, item["config_path"]),
                run_directory=cast(str, item["run_directory"]),
                transfer_evidence_directory=cast(str, item["transfer_evidence_directory"]),
                elapsed_seconds=cast(float, item["elapsed_seconds"]),
                reuse=frozen(cast(study.JsonObject, item["reuse"])),
                cleanup_verified=cast(bool, item["cleanup_verified"]),
                transfer_responses=tuple(
                    frozen(value) for value in cast(list[study.JsonObject], item["transfer_responses"])
                ),
                artifact_sha256=frozen(cast(study.JsonObject, item["artifact_sha256"])),
                reference=frozen(cast(study.JsonObject, item["reference"])),
                generated=frozen(cast(study.JsonObject, item["generated"])),
                family_champions=cast(
                    tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject], champions
                ),
                winner=frozen(cast(study.JsonObject, item["winner"])),
                fresh_simulation=frozen(cast(study.JsonObject, item["fresh_simulation"])),
                published=frozen(cast(study.JsonObject, item["published"])),
                raw_sequence=frozen(cast(study.JsonObject, item["raw_sequence"])),
            )
        )
    natural = tuple(frozen(value) for value in cast(list[study.JsonObject], document["natural_variation"]))
    summaries = tuple(frozen(value) for value in cast(list[study.JsonObject], document["workload_summaries"]))
    return study.StudyResults(
        schema_version=cast(int, document["schema_version"]),
        environment=frozen(cast(study.JsonObject, document["environment"])),
        protocol=frozen(cast(study.JsonObject, document["protocol"])),
        runs=tuple(run_values),
        natural_variation=cast(tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject], natural),
        workload_summaries=cast(
            tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject], summaries
        ),
        reproduction=study.ReproductionRecord(frozen(cast(study.JsonObject, document["reproduction"]))),
    )


def trial_result(seed: int, value: float) -> TrialResult:
    methods = tuple(
        MethodTrialResult(
            name=name,
            score=value,
            diagnostics={"observation_window_seconds": 3.0, "seed": seed},
        )
        for name in study.PUBLISHED_METHOD_ORDER
    )
    return TrialResult(
        seed=seed,
        aggregate_score=value,
        methods=cast(tuple[MethodTrialResult, MethodTrialResult, MethodTrialResult, MethodTrialResult], methods),
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
        rebuild_genetic_record(trial_result(17, first_score), model_diagnostics=diagnostics),
        rebuild_genetic_record(trial_result(29, second_score), model_diagnostics=diagnostics),
    )
    return Candidate(
        identifier=identifier,
        family=family,
        genes=genes,
        status="valid",
        fitness=math.fsum(trial.aggregate_score for trial in trials) / 2.0,
        trials=trials,
        invalid=None,
        duplicate_diagnostics=(),
    )


def terminal_checkpoint_and_best(tmp_path: Path) -> tuple[CheckpointState, BestModel, ComparisonResult]:
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
        _evaluated_candidate(
            CandidateId(birth_generation=2, birth_index=3), "markov_renewal", (0.2, 0.7, 1.0, 4, 1.0), 0.5, 0.7
        ),
        _evaluated_candidate(
            CandidateId(birth_generation=2, birth_index=0), "markov_renewal", (0.25, 0.75, 0.5, 3, 1.2), 0.6, 0.6
        ),
        _evaluated_candidate(CandidateId(birth_generation=2, birth_index=4), "mmpp", (1.0, 2.0, 10.0, 20.0), 0.6, 0.8),
        _evaluated_candidate(CandidateId(birth_generation=2, birth_index=1), "mmpp", (1.5, 2.5, 12.0, 24.0), 0.6, 0.7),
        _evaluated_candidate(CandidateId(birth_generation=2, birth_index=5), "poisson_empirical", (1.0,), 0.8, 1.0),
        _evaluated_candidate(CandidateId(birth_generation=2, birth_index=2), "poisson_empirical", (1.5,), 0.7, 0.9),
    )
    state = CheckpointState(
        compatibility=context.compatibility,
        generation=2,
        population=population,
        history=(),
        rng_state=encode_rng_state(make_rng(73)),
        best_identifier=CandidateId(birth_generation=2, birth_index=5),
        best_fitness=0.9,
        consecutive_stagnation=0,
        terminal_reason="hard_limit",
        family_priority=context.compatibility.family_priority,
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


def offline_capture(_path: Path, prepared: PreparedExperiment) -> CaptureResult:
    capture_path = prepared.run_directory / "capture.json"
    reference_path = prepared.run_directory / "reference.pcapng"
    capture_path.write_bytes(CAPTURE_BYTES)
    reference_path.write_bytes(REFERENCE_BYTES)
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


def offline_validation_study_primary(
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
            offline_capture,
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
        header_bytes = response_headers(start, end)
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


@pytest.fixture(scope="session")
def offline_primary_baselines(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, OfflinePrimaryBaseline]:
    """Build the two immutable real-pipeline primary templates once per worker."""

    short_parent = tmp_path_factory.mktemp("validation-study-primary-short")
    short_root = short_parent / "repository"
    short_result, short_spec, short_workload, short_responses = offline_validation_study_primary(short_root)
    short_template = short_parent / "template"
    shutil.copytree(short_root, short_template, copy_function=shutil.copy2)

    streaming_parent = tmp_path_factory.mktemp("validation-study-primary-streaming")
    streaming_root = streaming_parent / "repository"
    streaming_result, streaming_spec, streaming_workload, streaming_responses = offline_validation_study_primary(
        streaming_root,
        execution_order=4,
        run_id="04-streaming-r2",
        workload_name="streaming",
        repeat=2,
    )
    streaming_template = streaming_parent / "template"
    shutil.copytree(streaming_root, streaming_template, copy_function=shutil.copy2)
    return {
        "short": (
            short_root,
            short_template,
            short_result,
            short_spec,
            short_workload,
            short_responses,
        ),
        "streaming": (
            streaming_root,
            streaming_template,
            streaming_result,
            streaming_spec,
            streaming_workload,
            streaming_responses,
        ),
    }


def materialize_offline_primary_baseline(
    baseline: OfflinePrimaryBaseline,
) -> tuple[Path, RunResult, study.StudyRunSpec, study.WorkloadSpec, tuple[study.JsonObject, ...]]:
    """Restore one exact primary tree at its original absolute workspace path."""

    repository_root, template_root, result, spec, workload, transfer_responses = baseline
    if repository_root.exists():
        shutil.rmtree(repository_root)
    shutil.copytree(template_root, repository_root, copy_function=shutil.copy2)
    return (
        repository_root,
        result,
        spec,
        workload,
        deepcopy(transfer_responses),
    )


OfflinePrimaryBaseline = tuple[
    Path,
    Path,
    RunResult,
    study.StudyRunSpec,
    study.WorkloadSpec,
    tuple[study.JsonObject, ...],
]


class StudyIdentityRunner:
    def __init__(
        self,
        repository_root: Path,
        *,
        target_image_id: str = IMAGE_ID,
        capture_image_id: str = CAPTURE_IMAGE_ID,
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


def write_study_inputs(repository_root: Path) -> tuple[Path, study.StudyResults]:
    repository_root.mkdir()
    prerequisite, _contents = write_checked_configs(repository_root, capture_image_id=CAPTURE_IMAGE_ID)
    images = cast(study.JsonObject, study._thaw_json(prerequisite.images))  # pyright: ignore[reportPrivateUsage]
    images["capture_image_id"] = CAPTURE_IMAGE_ID
    prerequisite = write_retained_prerequisite_evidence(repository_root, replace(prerequisite, images=frozen(images)))
    capture_root = repository_root / "docker" / "capture"
    shutil.copy2(ROOT / "docker" / "capture" / "image-lock.json", capture_root / "image-lock.json")
    prerequisite_path = repository_root / "examples" / "validation_study" / "prerequisites.json"
    prerequisite_path.write_bytes(study.render_prerequisite_results(prerequisite))
    document = valid_result_document(repository_root)
    return prerequisite_path, study_result_value(document)


def validation_study_fixture_identity() -> tuple[str, str]:
    source_environment = cast(
        dict[str, object],
        json.loads((VALIDATION_STUDY_CANDIDATE / "environment.json").read_text(encoding="utf-8")),
    )
    return cast(str, source_environment["source_commit"]), cast(str, source_environment["source_tree"])


def validation_study_request_test_name(request: pytest.FixtureRequest) -> str:
    """Resolve the stable base test name used to select a shared or isolated checkout."""

    node = cast(Any, request).node
    return cast(str, node.originalname or node.name)


def _add_validation_study_worktree(repository: Path, source_commit: str) -> None:
    _REAL_SUBPROCESS_RUN(
        ("git", "worktree", "add", "--detach", str(repository), source_commit),
        cwd=ROOT,
        check=True,
        capture_output=True,
    )


def remove_validation_study_worktree(repository: Path) -> None:
    _REAL_SUBPROCESS_RUN(
        ("git", "worktree", "remove", "--force", str(repository)),
        cwd=ROOT,
        check=True,
        capture_output=True,
    )


def finish_validation_study_worktree_cleanup(
    repositories: Sequence[Path],
    *,
    body_error: BaseException | None,
    remove: Callable[[Path], None] = remove_validation_study_worktree,
) -> None:
    """Remove all owned checkouts while retaining a prior body failure and cleanup diagnostics."""

    cleanup_errors: list[BaseException] = []
    for repository in reversed(repositories):
        try:
            remove(repository)
        except BaseException as error:
            cleanup_errors.append(error)
    if not cleanup_errors:
        return
    if body_error is not None:
        raise BaseExceptionGroup(
            "validation-study test body and detached-checkout cleanup both failed",
            (body_error, *cleanup_errors),
        ) from None
    if len(cleanup_errors) == 1:
        raise cleanup_errors[0]
    raise BaseExceptionGroup("validation-study detached-checkout cleanup failed", cleanup_errors)


@pytest.fixture(scope="session")
def shared_validation_study_repository(  # pyright: ignore[reportUnusedFunction]
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Path]:
    """Provide one detached source checkout per pytest worker for candidate-only audits."""

    global _shared_validation_study_repository_path
    source_commit, _source_tree = validation_study_fixture_identity()
    repository = tmp_path_factory.mktemp("validation-study-checkout") / "repository"
    _add_validation_study_worktree(repository, source_commit)
    _shared_validation_study_repository_path = repository
    try:
        yield repository
    finally:
        _shared_validation_study_repository_path = None
        remove_validation_study_worktree(repository)


@pytest.fixture(autouse=True)
def validation_study_candidate_context(  # pyright: ignore[reportUnusedFunction]
    request: pytest.FixtureRequest, shared_validation_study_repository: Path
) -> Iterator[None]:
    """Track the active test so source-mutating audits retain isolated checkouts."""

    del shared_validation_study_repository
    global _current_isolated_validation_study_worktrees, _current_validation_study_test_name
    _current_isolated_validation_study_worktrees = []
    _current_validation_study_test_name = validation_study_request_test_name(request)
    try:
        yield
    finally:
        repositories = _current_isolated_validation_study_worktrees
        _current_isolated_validation_study_worktrees = None
        _current_validation_study_test_name = None
        assert repositories is not None
        finish_validation_study_worktree_cleanup(
            repositories,
            body_error=retained_test_body_failure(request),
        )


@pytest.fixture(scope="session")
def generated_validation_study_candidate_template(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Generate one immutable candidate template per pytest worker."""

    source_commit, source_tree = validation_study_fixture_identity()
    template = tmp_path_factory.mktemp("validation-study-generated-template") / "fixture-study"
    for relative, content in fixture_generator.generate_fixture_tree(
        source_commit=source_commit,
        source_tree=source_tree,
    ).items():
        path = template / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return template


def copy_validation_study_candidate(
    tmp_path: Path,
    *,
    generated_template: Path | None = None,
) -> tuple[Path, Path]:
    source_commit, _source_tree = validation_study_fixture_identity()
    shared_repository = _shared_validation_study_repository_path
    if (
        shared_repository is not None
        and _current_validation_study_test_name not in ISOLATED_VALIDATION_STUDY_REPOSITORY_TESTS
    ):
        repository = shared_repository
    else:
        repository = tmp_path / "relocated-repository"
        _add_validation_study_worktree(repository, source_commit)
        repositories = _current_isolated_validation_study_worktrees
        assert repositories is not None
        repositories.append(repository)
    candidate = repository / "fixture-study"
    if candidate.exists():
        shutil.rmtree(candidate)
    if generated_template is not None:
        shutil.copytree(generated_template, candidate, copy_function=shutil.copy2)
    else:
        shutil.copytree(VALIDATION_STUDY_CANDIDATE, candidate)
    return repository, candidate


def tree_inventory(root: Path) -> dict[str, tuple[object, ...]]:
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


def rewrite_candidate_manifest(candidate: Path) -> None:
    index = cast(dict[str, object], json.loads((candidate / "index.json").read_text(encoding="utf-8")))
    auditor.write_manifest(
        candidate,
        ownership=cast(dict[str, str], index["ownership"]),
        lineage=cast(dict[str, object], index["lineage"]),
    )


def write_canonical_json(path: Path, document: object) -> None:
    path.write_bytes(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        + b"\n"
    )


def candidate_index(candidate: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads((candidate / "index.json").read_text(encoding="utf-8")))


def write_candidate_index(candidate: Path, index: dict[str, object]) -> None:
    write_canonical_json(candidate / "index.json", index)
