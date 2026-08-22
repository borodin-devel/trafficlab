"""Artifacts owner for Validation Study tooling."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from scripts.validation_study.audit.artifacts import write_manifest
from scripts.validation_study.common import JsonObject, thaw_json
from scripts.validation_study.records import StudyRunSpec
from scripts.validation_study.workloads import (
    WorkloadSpec,
    build_base_config,
    config_with_run_directory,
    render_realized_config,
    workload_specs,
)
from tests.support.validation_study.builders import frozen, response_headers
from tests.support.validation_study.constants import CAPTURE_BYTES, CAPTURE_DOCKERFILE, CAPTURE_SCRIPT, REFERENCE_BYTES
from trafficlab.artifacts.io import append_run_log
from trafficlab.capture.lineage import CaptureResult
from trafficlab.capture.validation import validate_capture_pair
from trafficlab.common.config import ExperimentConfig
from trafficlab.comparison.stage import compare_experiment
from trafficlab.fitting.stage import fit_experiment
from trafficlab.generation.stage import generate_experiment
from trafficlab.pipeline.stage import run_experiment
from trafficlab.pipeline.types import RunDependencies, RunResult
from trafficlab.preflight.stage import open_or_prepare_experiment
from trafficlab.preflight.types import PreparedExperiment

if TYPE_CHECKING:
    from scripts.validation_study.common import WorkloadName
    from scripts.validation_study.records import PrerequisiteResults


def write_retained_prerequisite_evidence(
    repository_root: Path, prerequisite: PrerequisiteResults
) -> PrerequisiteResults:
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
        "docker.xml": b'<testsuites tests="2" failures="0" errors="0" skipped="0"><testsuite tests="2" failures="0" errors="0" skipped="0"/></testsuites>',
        "internet.stdout": b"internet pass\n",
        "internet.stderr": b"",
        "internet.xml": b'<testsuites tests="2" failures="0" errors="0" skipped="0"><testsuite tests="2" failures="0" errors="0" skipped="0"/></testsuites>',
    }
    for name, content in retained.items():
        path = evidence / name
        path.write_bytes(content)
        path.chmod(384)
    images = cast(JsonObject, thaw_json(prerequisite.images))
    images["capture_dockerfile_sha256"] = hashlib.sha256(dockerfile).hexdigest()
    images["capture_script_sha256"] = hashlib.sha256(capture_script).hexdigest()
    capability = cast(JsonObject, thaw_json(prerequisite.capability))
    capability["canary_sha256"] = hashlib.sha256(capability_headers).hexdigest()
    capability["stdout_sha256"] = hashlib.sha256(capability_stdout).hexdigest()
    capability["stderr_sha256"] = hashlib.sha256(capability_stderr).hexdigest()
    commands = [cast(JsonObject, thaw_json(command)) for command in prerequisite.commands]
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
    workload_name: WorkloadName = "short",
    repeat: int = 1,
    base_config: ExperimentConfig | None = None,
) -> tuple[RunResult, StudyRunSpec, WorkloadSpec, tuple[JsonObject, ...]]:
    repository_root.mkdir(exist_ok=True)
    url = "https://downloads.example.test/object.bin"
    study_id = "study-1"
    workload = {value.name: value for value in workload_specs(url)}[workload_name]
    mount = repository_root / "examples" / "validation_study" / ".study-work" / "mount" / study_id
    mount.mkdir(parents=True, exist_ok=True)
    config = base_config or build_base_config(
        workload, repository_root=repository_root, study_id=study_id, url=url, capture_image_id=f"sha256:{'d' * 64}"
    )
    if config.run.directory.name != run_id:
        config = config_with_run_directory(config, repository_root / "runs" / "validation_study" / study_id / run_id)
    config_path = repository_root / "runs" / "validation_study" / study_id / "realized-configs" / f"{run_id}.toml"
    render_realized_config(config, config_path)
    result = run_experiment(
        config_path,
        dependencies=RunDependencies(
            open_or_prepare_experiment, offline_capture, fit_experiment, generate_experiment, compare_experiment
        ),
    )
    evidence_directory = (
        repository_root / "examples" / "validation_study" / ".study-work" / "evidence" / study_id / run_id
    )
    evidence_directory.mkdir(parents=True)
    transfer_responses_list: list[JsonObject] = []
    for index, (start, end, filename) in enumerate(workload.transfers):
        header_bytes = response_headers(start, end)
        header = evidence_directory / filename
        header.write_bytes(header_bytes)
        header.chmod(384)
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
    spec = StudyRunSpec(
        execution_order, run_id, workload_name, repeat, config_path, config.run.directory, evidence_directory
    )
    return (result, spec, workload, transfer_responses)


@pytest.fixture(scope="session")
def offline_primary_baselines(tmp_path_factory: pytest.TempPathFactory) -> dict[str, OfflinePrimaryBaseline]:
    """Build the two immutable real-pipeline primary templates once per worker."""
    short_parent = tmp_path_factory.mktemp("validation-study-primary-short")
    short_root = short_parent / "repository"
    short_result, short_spec, short_workload, short_responses = offline_validation_study_primary(short_root)
    short_template = short_parent / "template"
    shutil.copytree(short_root, short_template, copy_function=shutil.copy2)
    streaming_parent = tmp_path_factory.mktemp("validation-study-primary-streaming")
    streaming_root = streaming_parent / "repository"
    streaming_result, streaming_spec, streaming_workload, streaming_responses = offline_validation_study_primary(
        streaming_root, execution_order=4, run_id="04-streaming-r2", workload_name="streaming", repeat=2
    )
    streaming_template = streaming_parent / "template"
    shutil.copytree(streaming_root, streaming_template, copy_function=shutil.copy2)
    return {
        "short": (short_root, short_template, short_result, short_spec, short_workload, short_responses),
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
) -> tuple[Path, RunResult, StudyRunSpec, WorkloadSpec, tuple[JsonObject, ...]]:
    """Restore one exact primary tree at its original absolute workspace path."""
    repository_root, template_root, result, spec, workload, transfer_responses = baseline
    if repository_root.exists():
        shutil.rmtree(repository_root)
    shutil.copytree(template_root, repository_root, copy_function=shutil.copy2)
    return (repository_root, result, spec, workload, deepcopy(transfer_responses))


OfflinePrimaryBaseline = tuple[Path, Path, RunResult, StudyRunSpec, WorkloadSpec, tuple[JsonObject, ...]]


def tree_inventory(root: Path) -> dict[str, tuple[object, ...]]:
    """Capture exact entry kinds, symlink targets, and regular bytes without following links."""
    if not root.exists() and (not root.is_symlink()):
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
    write_manifest(
        candidate, ownership=cast(dict[str, str], index["ownership"]), lineage=cast(dict[str, object], index["lineage"])
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
