from __future__ import annotations

import json
import platform
import shutil
import subprocess
from collections.abc import Callable
from itertools import count
from pathlib import Path
from typing import cast

import pytest

from scripts import audit_validation_study as auditor
from scripts import run_validation_study as study
from trafficlab.artifacts import append_run_log
from trafficlab.capture import CaptureResult
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.comparison import compare_experiment
from trafficlab.compatibility import identify_bytes
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import load_experiment
from trafficlab.errors import TrafficlabError
from trafficlab.fitting import fit_experiment
from trafficlab.generation import generate_experiment
from trafficlab.pcapng import encode_pcapng, parse_pcapng_bytes
from trafficlab.preflight import PreparedExperiment, open_or_prepare_experiment
from trafficlab.run import RunDependencies, RunResult, run_experiment
from trafficlab.trace import TraceEvent, parse_capture_metadata

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_FIT_FIXTURE = _ROOT / "examples" / "data" / "fit"
_CAPTURE_BYTES = (_FIT_FIXTURE / "capture.json").read_bytes()
_REFERENCE_BYTES = (_FIT_FIXTURE / "reference.pcapng").read_bytes()


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        + b"\n"
    )


def _initialize_repository(root: Path) -> tuple[str, str, dict[str, object]]:
    lock = root / "docker" / "capture"
    lock.mkdir(parents=True)
    shutil.copy2(_ROOT / "uv.lock", root / "uv.lock")
    shutil.copy2(_ROOT / "docker" / "capture" / "image-lock.json", lock / "image-lock.json")
    for argv in (
        ("git", "init", "--quiet"),
        ("git", "config", "user.email", "validation-study@example.test"),
        ("git", "config", "user.name", "Validation Study"),
        ("git", "add", "uv.lock", "docker/capture/image-lock.json"),
        ("git", "commit", "--quiet", "-m", "fixture source"),
    ):
        subprocess.run(argv, cwd=root, check=True, capture_output=True)
    commit = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    tree = subprocess.run(
        ("git", "rev-parse", "HEAD^{tree}"), cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return commit, tree, cast(dict[str, object], json.loads((lock / "image-lock.json").read_text()))


def _variant(events: tuple[TraceEvent, ...], *, number: int) -> tuple[TraceEvent, ...]:
    varied: list[TraceEvent] = []
    last = len(events) - 1
    for index, event in enumerate(events):
        timestamp = event.timestamp
        if 0 < index < last:
            timestamp += 0.000000001 * number * index
        varied.append(TraceEvent(timestamp, event.direction, event.frame_length + number * ((index % 3) + 1)))
    return tuple(varied)


def _retained_prerequisites(
    *,
    study_id: str,
    url: str,
    environment: dict[str, object],
) -> tuple[bytes, dict[str, bytes]]:
    outputs: dict[str, bytes] = {}
    commands: list[dict[str, object]] = []
    junit = b'<testsuite errors="0" failures="0" name="fixture" skipped="0" tests="1"/>\n'
    for kind in ("docker_matrix", "internet_smoke"):
        argv = list(study.prerequisite_command_argv(kind, study_id=study_id, url=url))
        tests = study.prerequisite_junit_counts(junit)
        records = {
            "command": _canonical({"argv": argv}),
            "junit": junit,
            "status": _canonical({"exit_status": 0, "tests": tests}),
            "stderr": f"{kind} stderr\n".encode(),
            "stdout": f"{kind} stdout\n".encode(),
        }
        fields: dict[str, dict[str, object]] = {}
        for name, content in records.items():
            suffix = {"command": "command.json", "junit": "junit.xml", "status": "status.json"}.get(name, name)
            path = f"prerequisites/{kind}.{suffix}"
            outputs[path] = content
            fields[name] = {"identity": identify_bytes(content).as_dict(), "path": path}
        commands.append(
            {
                "argv": argv,
                "command": fields["command"],
                "exit_status": 0,
                "junit": fields["junit"],
                "kind": kind,
                "status": fields["status"],
                "stderr": fields["stderr"],
                "stdout": fields["stdout"],
                "tests": tests,
            }
        )
    document = study.render_retained_prerequisites(
        {
            "commands": commands,
            "environment": {
                key: environment[key]
                for key in (
                    "capture_image_id",
                    "capture_image_reference",
                    "capture_tool_version",
                    "source_commit",
                    "source_tree",
                    "target_image_id",
                    "target_image_reference",
                    "uv_lock_identity",
                )
            },
            "schema_version": 3,
            "study_id": study_id,
            "url": url,
        }
    )
    return document, outputs


def _collection_inputs(
    root: Path,
) -> tuple[dict[str, object], bytes, dict[str, bytes], dict[study.WorkloadName, ExperimentConfig]]:
    study_id = "study-1"
    url = "https://downloads.example.test/object.bin"
    commit, tree, image_lock = _initialize_repository(root)
    capture_id = cast(str, image_lock["expected_capture_image_id"])
    capture_reference = capture_id
    environment: dict[str, object] = {
        "capture_image_id": capture_id,
        "capture_image_reference": capture_reference,
        "capture_tool_version": image_lock["capture_tool_version"],
        "compatibility_decision": {
            "reason": "source, lock, and image-lock identities are compatible",
            "status": "compatible",
        },
        "docker_compose_version": "fixture-compose-2.0",
        "docker_engine_version": "fixture-engine-27.0",
        "host_architecture": "fixture-x86_64",
        "kernel_release": "fixture-kernel-1",
        "python_implementation": "CPython",
        "python_version": platform.python_version(),
        "scientific_artifact_schema": 2,
        "source_commit": commit,
        "source_tree": tree,
        "target_image_id": f"sha256:{study.TARGET_REFERENCE.rsplit(':', 1)[-1]}",
        "target_image_reference": study.TARGET_REFERENCE,
        "uv_lock_identity": identify_bytes((root / "uv.lock").read_bytes()).as_dict(),
    }
    prerequisite, files = _retained_prerequisites(study_id=study_id, url=url, environment=environment)
    configs: dict[study.WorkloadName, ExperimentConfig] = {}
    for workload in study.workload_specs(url):
        base = study.build_base_config(
            workload,
            repository_root=root,
            study_id=study_id,
            url=url,
            capture_image_id=capture_id,
        )
        configs[workload.name] = base.model_copy(
            update={"capture": base.capture.model_copy(update={"image": capture_reference})}
        )
    (root / "examples" / "validation_study" / ".study-work" / "mount" / study_id).mkdir(parents=True)
    return environment, prerequisite, files, configs


def _offline_stage_runners(
    root: Path,
    *,
    candidate: Path,
) -> tuple[Callable[[Path], RunResult], Callable[[Path], CaptureResult], list[str]]:
    metadata = parse_capture_metadata(_CAPTURE_BYTES, source=_FIT_FIXTURE / "capture.json")
    base_events = parse_pcapng_bytes(_REFERENCE_BYTES, metadata, source=_FIT_FIXTURE / "reference.pcapng")
    sequence = count(1)
    calls: list[str] = []

    def publish_capture(prepared: PreparedExperiment, *, number: int, label: str) -> CaptureResult:
        capture_path = prepared.run_directory / "capture.json"
        reference_path = prepared.run_directory / "reference.pcapng"
        capture_path.write_bytes(_CAPTURE_BYTES)
        reference_path.write_bytes(encode_pcapng(_variant(base_events, number=number), metadata))
        inspection = validate_capture_pair(capture_path, reference_path, deadline=None)
        append_run_log(
            prepared.run_directory,
            {
                "event": "capture_published",
                "packet_count": inspection.packet_count,
                "stage": "capture",
                "workload": label,
            },
        )
        return CaptureResult(prepared.run_directory, reference_path, inspection.packet_count, 0, reused=False)

    def run_training(path: Path) -> RunResult:
        config = load_experiment(path)
        workload = study._workload_for_config(config)  # pyright: ignore[reportPrivateUsage]
        calls.append(f"training:{workload.name}")
        number = next(sequence)

        def capture(_path: Path, prepared: PreparedExperiment) -> CaptureResult:
            result = publish_capture(prepared, number=number, label=workload.name)
            for start, end, filename in workload.transfers:
                header = (
                    f"HTTP/1.1 206 Partial Content\r\nContent-Length: {end - start + 1}\r\n"
                    f"Content-Range: bytes {start}-{end}/4194304\r\n\r\n"
                ).encode()
                (config.target.mounts[0].source / filename).write_bytes(header)
            return result

        return run_experiment(
            path,
            dependencies=RunDependencies(
                open_or_prepare_experiment,
                capture,
                fit_experiment,
                generate_experiment,
                compare_experiment,
            ),
        )

    def capture_held_out(path: Path) -> CaptureResult:
        config = load_experiment(path)
        workload = study._workload_for_config(config)  # pyright: ignore[reportPrivateUsage]
        protocol = candidate / "protocol.json"
        assert protocol.exists(), "held-out capture started before training selection was frozen"
        calls.append(f"held-out:{workload.name}")
        prepared = open_or_prepare_experiment(path)
        result = publish_capture(prepared, number=100 + next(sequence), label=workload.name)
        for start, end, filename in workload.transfers:
            header = (
                f"HTTP/1.1 206 Partial Content\r\nContent-Length: {end - start + 1}\r\n"
                f"Content-Range: bytes {start}-{end}/4194304\r\n\r\n"
            ).encode()
            (config.target.mounts[0].source / filename).write_bytes(header)
        return result

    return run_training, capture_held_out, calls


def test_collection_builds_auditable_frozen_training_fresh_and_held_out_candidate(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    environment, prerequisite, prerequisite_files, configs = _collection_inputs(repository)
    candidate = repository / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"
    run_training, capture_held_out, calls = _offline_stage_runners(repository, candidate=candidate)

    collected = study.collect_validation_candidate(
        repository_root=repository,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        environment=environment,
        retained_prerequisites=prerequisite,
        prerequisite_files=prerequisite_files,
        configs=configs,
        run=run_training,
        capture=capture_held_out,
        object_size_bytes=4_194_304,
    )

    assert collected == candidate
    assert auditor.audit_bundle(candidate, repository=repository).bundle == candidate
    index = cast(dict[str, object], json.loads((candidate / "index.json").read_text()))
    assert len(cast(list[object], index["training"])) == 9
    assert len(cast(list[object], index["fresh_simulation"])) == 9
    assert len(cast(list[object], index["held_out"])) == 3
    assert set(cast(dict[str, object], json.loads((candidate / "report_inputs.json").read_text()))) == {
        "formula",
        "fresh_simulation",
        "held_out",
        "natural_variation",
        "training",
    }
    assert calls[:9] == [
        "training:short",
        "training:streaming",
        "training:bursty",
        "training:streaming",
        "training:bursty",
        "training:short",
        "training:bursty",
        "training:short",
        "training:streaming",
    ]
    assert calls[9:] == ["held-out:short", "held-out:streaming", "held-out:bursty"]
    selected = cast(dict[str, object], json.loads((candidate / "protocol.json").read_text()))["model_selection"]
    assert cast(dict[str, object], selected)["rule"] == "highest_best_fitness_then_lowest_repeat"
    published = study.publish_audited_bundle(candidate, "study-1", repository_root=repository)
    assert published == repository / "examples" / "validation_study" / "evidence" / "study-1"
    with pytest.raises(TrafficlabError, match="already exists"):
        study.publish_audited_bundle(candidate, "study-1", repository_root=repository)


def test_collection_failure_locks_the_study_id_to_a_new_attempt(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    environment, prerequisite, prerequisite_files, configs = _collection_inputs(repository)
    calls = 0

    def fail_training(_path: Path) -> RunResult:
        nonlocal calls
        calls += 1
        raise TrafficlabError("offline primary failure", corrective_action="preserve failure")

    def unreachable_capture(_path: Path) -> CaptureResult:
        raise AssertionError("held-out capture must not begin after a training failure")

    def collect() -> Path:
        return study.collect_validation_candidate(
            repository_root=repository,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            environment=environment,
            retained_prerequisites=prerequisite,
            prerequisite_files=prerequisite_files,
            configs=configs,
            run=fail_training,
            capture=unreachable_capture,
            object_size_bytes=4_194_304,
        )

    with pytest.raises(TrafficlabError, match="new study ID"):
        collect()
    with pytest.raises(TrafficlabError, match="new study ID"):
        collect()
    assert calls == 1


def test_collection_preserves_unexpected_programming_errors_after_freezing_the_attempt(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    environment, prerequisite, prerequisite_files, configs = _collection_inputs(repository)

    def broken_training(_path: Path) -> RunResult:
        raise ValueError("offline programming defect")

    with pytest.raises(ValueError, match="offline programming defect"):
        study.collect_validation_candidate(
            repository_root=repository,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            environment=environment,
            retained_prerequisites=prerequisite,
            prerequisite_files=prerequisite_files,
            configs=configs,
            run=broken_training,
            capture=lambda _path: pytest.fail("held-out capture must not begin"),
            object_size_bytes=4_194_304,
        )
    assert (
        repository / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1" / "frozen-protocol.json"
    ).is_file()


def test_collection_wraps_operational_os_errors_after_freezing_the_attempt(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    environment, prerequisite, prerequisite_files, configs = _collection_inputs(repository)

    def inaccessible_training(_path: Path) -> RunResult:
        raise OSError("offline filesystem failure")

    with pytest.raises(TrafficlabError, match="offline filesystem failure"):
        study.collect_validation_candidate(
            repository_root=repository,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            environment=environment,
            retained_prerequisites=prerequisite,
            prerequisite_files=prerequisite_files,
            configs=configs,
            run=inaccessible_training,
            capture=lambda _path: pytest.fail("held-out capture must not begin"),
            object_size_bytes=4_194_304,
        )
    assert (
        repository / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1" / "frozen-protocol.json"
    ).is_file()
