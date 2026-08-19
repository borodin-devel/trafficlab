from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from itertools import count
from pathlib import Path
from typing import Literal, cast

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
from trafficlab.trace import TraceEvent, TrafficTrace, parse_capture_metadata

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
    shutil.copy2(_ROOT / ".gitignore", root / ".gitignore")
    shutil.copy2(_ROOT / "uv.lock", root / "uv.lock")
    shutil.copy2(_ROOT / "docker" / "capture" / "image-lock.json", lock / "image-lock.json")
    for argv in (
        ("git", "init", "--quiet"),
        ("git", "config", "user.email", "validation-study@example.test"),
        ("git", "config", "user.name", "Validation Study"),
        ("git", "add", ".gitignore", "uv.lock", "docker/capture/image-lock.json"),
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
        if index < 3:
            timestamp = 0.004 * index
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
    capability_header = b"HTTP/1.1 206 Partial Content\r\nContent-Length: 1\r\nContent-Range: bytes 0-0/4194304\r\n\r\n"
    outputs["headers/prerequisites/00-prerequisites/capability.headers"] = capability_header
    document = study.render_retained_prerequisites(
        {
            "capability": {
                "canary_sha256": hashlib.sha256(capability_header).hexdigest(),
                "content_length": 1,
                "content_range": "bytes 0-0/4194304",
                "object_size_bytes": 4_194_304,
                "status": 206,
            },
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
        "scientific_artifact_schema": 3,
        "source_commit": commit,
        "source_tree": tree,
        "target_image_id": f"sha256:{study.TARGET_REFERENCE.rsplit(':', 1)[-1]}",
        "target_image_reference": study.TARGET_REFERENCE,
        "uv_lock_identity": identify_bytes((root / "uv.lock").read_bytes()).as_dict(),
    }
    prerequisite, files = _retained_prerequisites(study_id=study_id, url=url, environment=environment)
    success_marker = (
        root / "examples" / "validation_study" / ".study-work" / "attempts" / study_id / "prerequisites-success.json"
    )
    success_marker.parent.mkdir(parents=True)
    success_marker.write_bytes(
        _canonical(
            {
                "phase": "prerequisites",
                "prerequisites_identity": identify_bytes(prerequisite).as_dict(),
                "study_id": study_id,
                "url": url,
            }
        )
    )
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
            update={
                "capture": base.capture.model_copy(update={"image": capture_reference}),
                "genetic": base.genetic.model_copy(update={"generation_count": 1}),
            }
        )
    (root / "examples" / "validation_study" / ".study-work" / "mount" / study_id).mkdir(parents=True)
    return environment, prerequisite, files, configs


def _offline_stage_runners(
    root: Path,
    *,
    candidate: Path,
    environment: dict[str, object],
) -> tuple[Callable[[Path], RunResult], Callable[[Path], CaptureResult], list[str]]:
    metadata = parse_capture_metadata(_CAPTURE_BYTES, source=_FIT_FIXTURE / "capture.json")
    base_events = parse_pcapng_bytes(_REFERENCE_BYTES, metadata, source=_FIT_FIXTURE / "reference.pcapng")
    sequence = count(1)
    calls: list[str] = []

    capture_environment = {
        "capture_content_id": environment["capture_image_id"],
        "capture_reference": environment["capture_image_reference"],
        "capture_tool_version": environment["capture_tool_version"],
        "host_architecture": "linux/amd64",
        "target_content_id": environment["target_image_id"],
        "target_reference": environment["target_image_reference"],
    }

    def publish_capture(
        prepared: PreparedExperiment, *, number: int, label: str, window_scale: float = 1.0
    ) -> CaptureResult:
        capture_path = prepared.run_directory / "capture.json"
        reference_path = prepared.run_directory / "reference.pcapng"
        capture_path.write_bytes(_CAPTURE_BYTES)
        events = tuple(
            TraceEvent(event.timestamp * window_scale, event.direction, event.frame_length)
            for event in _variant(base_events, number=number)
        )
        reference_path.write_bytes(encode_pcapng(events, metadata))
        inspection = validate_capture_pair(capture_path, reference_path, deadline=None)
        append_run_log(
            prepared.run_directory,
            {
                "event": "capture_environment_identity",
                "stage": "preflight",
                **capture_environment,
            },
        )
        project_name = f"trafficlab-capture-{label}-{number}"
        append_run_log(
            prepared.run_directory,
            {"event": "capture_project_created", "project_name": project_name, "stage": "capture"},
        )
        append_run_log(
            prepared.run_directory,
            {
                "capture_environment_identity": capture_environment,
                "capture_identity": identify_bytes(capture_path.read_bytes()).as_dict(),
                "event": "capture_published",
                "experiment_identity": identify_bytes(
                    (prepared.run_directory / "experiment.toml").read_bytes()
                ).as_dict(),
                "packet_count": inspection.packet_count,
                "project_name": project_name,
                "reference_identity": identify_bytes(reference_path.read_bytes()).as_dict(),
                "reused": False,
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
        result = publish_capture(
            prepared,
            number=100 + next(sequence),
            label=workload.name,
            window_scale={"short": 0.5, "streaming": 1.2, "bursty": 0.8}[workload.name],
        )
        for start, end, filename in workload.transfers:
            header = (
                f"HTTP/1.1 206 Partial Content\r\nContent-Length: {end - start + 1}\r\n"
                f"Content-Range: bytes {start}-{end}/4194304\r\n\r\n"
            ).encode()
            (config.target.mounts[0].source / filename).write_bytes(header)
        return result

    return run_training, capture_held_out, calls


def test_collection_builds_auditable_frozen_training_fresh_and_held_out_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    environment, prerequisite, prerequisite_files, configs = _collection_inputs(repository)
    assert {config.genetic.generation_count for config in configs.values()} == {1}
    assert {config.genetic.trial_seeds for config in configs.values()} == {(17, 29)}

    validation_profile = auditor._validation_profile  # pyright: ignore[reportPrivateUsage]

    def one_generation_validation_profile(
        *,
        workload: str,
        url: str,
        environment: Mapping[str, object],
    ) -> ExperimentConfig:
        profile = validation_profile(workload=workload, url=url, environment=environment)
        return profile.model_copy(update={"genetic": profile.genetic.model_copy(update={"generation_count": 1})})

    monkeypatch.setattr(auditor, "_validation_profile", one_generation_validation_profile)
    candidate = repository / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"
    run_training, capture_held_out, calls = _offline_stage_runners(
        repository, candidate=candidate, environment=environment
    )
    attempt = study._begin_phase_attempt(  # pyright: ignore[reportPrivateUsage]
        repository,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        phase="collection",
    )
    tag = study._phase_capture_tag("study-1", "collection")  # pyright: ignore[reportPrivateUsage]
    phase_commands: list[tuple[str, ...]] = []

    def phase_runner(
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
        assert input is None
        assert timeout == study.SUBPROCESS_TIMEOUTS["image_pull_or_build"]
        command = tuple(argv)
        phase_commands.append(command)
        if command == ("docker", "image", "rm", "--force", tag):
            return subprocess.CompletedProcess(command, 0, stdout=b"removed\n", stderr=b"")
        assert command == ("docker", "image", "inspect", tag, "--format", "{{.Id}}")
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"")

    collected = study.collect_validation_candidate(
        repository_root=repository,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        attempt=attempt,
        environment=environment,
        retained_prerequisites=prerequisite,
        prerequisite_files=prerequisite_files,
        configs=configs,
        run=run_training,
        capture=capture_held_out,
        object_size_bytes=4_194_304,
        owned_capture_image=study._PhaseCaptureImage(tag=tag, build_attempted=True),  # pyright: ignore[reportPrivateUsage]
        runner=phase_runner,
    )

    assert collected == candidate
    assert auditor.audit_bundle(candidate, repository=repository).bundle == candidate
    index = cast(dict[str, object], json.loads((candidate / "index.json").read_text()))
    assert len(cast(list[object], index["training"])) == 9
    assert len(cast(list[object], index["fresh_simulation"])) == 9
    assert len(cast(list[object], index["held_out"])) == 3
    expected_headers = (
        {
            f"headers/training/{run_id}/{filename}"
            for _order, run_id, workload, _repeat in study.PRIMARY_ORDER
            for _start, _end, filename in study.workload_specs("https://downloads.example.test/object.bin")[
                ("short", "streaming", "bursty").index(workload)
            ].transfers
        }
        | {
            f"headers/held_out/held-out-{workload.name}/{filename}"
            for workload in study.workload_specs("https://downloads.example.test/object.bin")
            for _start, _end, filename in workload.transfers
        }
        | {"headers/prerequisites/00-prerequisites/capability.headers"}
    )
    expected_observations = {path.replace("headers/", "observations/", 1) + ".json" for path in expected_headers}
    assert len(expected_headers) == len(expected_observations) == 41
    assert len({path for path in expected_headers if "/bursty-" in path}) == 32
    assert "headers/prerequisites/00-prerequisites/capability.headers" in expected_headers
    assert {
        path.relative_to(candidate).as_posix() for path in candidate.glob("headers/**/*.headers")
    } == expected_headers
    assert {
        path.relative_to(candidate).as_posix() for path in candidate.glob("observations/**/*.json")
    } == expected_observations
    report_inputs = cast(dict[str, object], json.loads((candidate / "report_inputs.json").read_text()))
    assert set(report_inputs) == {
        "controlled_weight_analysis",
        "formula",
        "fresh_simulation",
        "held_out",
        "invalid_chromosome_diagnostics",
        "natural_variation",
        "runtime_winner_variance",
        "training",
    }
    held_rows = cast(list[dict[str, object]], report_inputs["held_out"])
    held_records = {
        workload: cast(dict[str, object], json.loads((candidate / "held_out" / workload / "record.json").read_text()))
        for workload in ("short", "streaming", "bursty")
    }
    assert {row["workload"] for row in held_rows} == {"short", "streaming", "bursty"}
    assert {row["workload"]: row["observation_window_seconds"] for row in held_rows} == {
        workload: record["observation_window_seconds"] for workload, record in held_records.items()
    }
    assert cast(float, held_records["short"]["observation_window_seconds"]) < cast(
        float, held_records["streaming"]["observation_window_seconds"]
    )
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
    protocol = cast(dict[str, object], json.loads((candidate / "protocol.json").read_text()))
    assert protocol["study_id"] == protocol["candidate_id"] == protocol["destination_id"] == "study-1"
    assert protocol["prerequisite_path"] == "examples/validation_study/prerequisites.json"
    assert protocol["schema_version"] == 3
    assert "natural_variation_windows" not in protocol
    lifecycle = cast(dict[str, object], json.loads((candidate / "lifecycle.json").read_text()))
    assert lifecycle["study_id"] == "study-1"
    assert lifecycle["phase_capture_image"] == {
        "capture_image_id": environment["capture_image_id"],
        "cleanup_verified": True,
        "post_cleanup_inspect_exit_status": 1,
        "tag": tag,
    }
    assert [cast(dict[str, object], row)["run_id"] for row in cast(list[object], lifecycle["training"])] == [
        run_id for _order, run_id, _workload, _repeat in study.PRIMARY_ORDER
    ]
    assert [cast(dict[str, object], row)["run_id"] for row in cast(list[object], lifecycle["held_out"])] == [
        "held-out-short",
        "held-out-streaming",
        "held-out-bursty",
    ]
    assert all(
        cast(dict[str, object], row)["cleanup_verified"] is True
        and cast(str, cast(dict[str, object], row)["project_name"]).startswith("trafficlab-capture-")
        for row in (*cast(list[object], lifecycle["training"]), *cast(list[object], lifecycle["held_out"]))
    )
    project_names = [
        cast(str, cast(dict[str, object], row)["project_name"])
        for row in (*cast(list[object], lifecycle["training"]), *cast(list[object], lifecycle["held_out"]))
    ]
    assert len(project_names) == len(set(project_names)) == 12
    assert phase_commands == [
        ("docker", "image", "rm", "--force", tag),
        ("docker", "image", "inspect", tag, "--format", "{{.Id}}"),
    ]
    published = study.publish_audited_bundle(candidate, "study-1", repository_root=repository)
    assert published == repository / "examples" / "validation_study" / "evidence" / "study-1"
    assert auditor.audit_bundle(published, repository=repository).bundle == published
    with pytest.raises(TrafficlabError, match="already exists"):
        study.publish_audited_bundle(candidate, "study-1", repository_root=repository)


def test_collection_rejects_late_capture_project_record_before_final_artifacts(tmp_path: Path) -> None:
    """Collection rejects a causally impossible capture project record before finalization."""

    repository = tmp_path / "repository"
    repository.mkdir()
    environment, prerequisite, prerequisite_files, configs = _collection_inputs(repository)
    candidate = repository / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"
    run_training, capture_held_out, _calls = _offline_stage_runners(
        repository, candidate=candidate, environment=environment
    )
    attempt = study._begin_phase_attempt(  # pyright: ignore[reportPrivateUsage]
        repository,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        phase="collection",
    )
    tag = study._phase_capture_tag("study-1", "collection")  # pyright: ignore[reportPrivateUsage]

    def late_created_capture(path: Path) -> CaptureResult:
        result = capture_held_out(path)
        log_path = result.reference_path.parent / "run.log"
        lines = log_path.read_text().splitlines()
        created = [
            line
            for line in lines
            if cast(str, cast(dict[str, object], json.loads(line))["event"]) == "capture_project_created"
        ]
        assert len(created) == 1
        log_path.write_text(
            "\n".join(
                [
                    line
                    for line in lines
                    if cast(str, cast(dict[str, object], json.loads(line))["event"]) != "capture_project_created"
                ]
                + created
            )
            + "\n"
        )
        return result

    def phase_runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        pytest.fail("phase image cleanup must not begin after malformed capture lifecycle")

    with pytest.raises(
        TrafficlabError, match="collection capture must bind its exact created project name to publication"
    ):
        study.collect_validation_candidate(
            repository_root=repository,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            attempt=attempt,
            environment=environment,
            retained_prerequisites=prerequisite,
            prerequisite_files=prerequisite_files,
            configs=configs,
            run=run_training,
            capture=late_created_capture,
            object_size_bytes=4_194_304,
            owned_capture_image=study._PhaseCaptureImage(tag=tag, build_attempted=True),  # pyright: ignore[reportPrivateUsage]
            runner=phase_runner,
        )

    assert not (candidate / "lifecycle.json").exists()
    assert not (candidate / "report_inputs.json").exists()
    assert not (candidate / "REPORT.md").exists()
    assert not (candidate / "index.json").exists()
    assert not (candidate / "manifest.json").exists()


@pytest.mark.parametrize("cleanup_failure", ("remove", "retained_tag"))
def test_collection_refuses_to_finalize_when_phase_image_cleanup_is_not_verified(
    tmp_path: Path,
    cleanup_failure: str,
) -> None:
    """A capture tag that survives removal cannot become audit-ready lifecycle evidence."""

    repository = tmp_path / "repository"
    repository.mkdir()
    environment, prerequisite, prerequisite_files, configs = _collection_inputs(repository)
    candidate = repository / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"
    run_training, capture_held_out, _calls = _offline_stage_runners(
        repository, candidate=candidate, environment=environment
    )
    attempt = study._begin_phase_attempt(  # pyright: ignore[reportPrivateUsage]
        repository,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        phase="collection",
    )
    tag = study._phase_capture_tag("study-1", "collection")  # pyright: ignore[reportPrivateUsage]

    def retained_tag_runner(
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
        assert input is None
        assert timeout == study.SUBPROCESS_TIMEOUTS["image_pull_or_build"]
        command = tuple(argv)
        if command == ("docker", "image", "rm", "--force", tag):
            return subprocess.CompletedProcess(
                command,
                1 if cleanup_failure == "remove" else 0,
                stdout=b"removed\n" if cleanup_failure != "remove" else b"",
                stderr=b"simulated removal failure\n" if cleanup_failure == "remove" else b"",
            )
        assert command == ("docker", "image", "inspect", tag, "--format", "{{.Id}}")
        return subprocess.CompletedProcess(command, 0, stdout=b"sha256:still-owned\n", stderr=b"")

    with pytest.raises(TrafficlabError, match="capture image cleanup"):
        study.collect_validation_candidate(
            repository_root=repository,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            attempt=attempt,
            environment=environment,
            retained_prerequisites=prerequisite,
            prerequisite_files=prerequisite_files,
            configs=configs,
            run=run_training,
            capture=capture_held_out,
            object_size_bytes=4_194_304,
            owned_capture_image=study._PhaseCaptureImage(tag=tag, build_attempted=True),  # pyright: ignore[reportPrivateUsage]
            runner=retained_tag_runner,
        )

    assert not (candidate / "lifecycle.json").exists()
    assert not (candidate / "report_inputs.json").exists()
    assert not (candidate / "index.json").exists()
    assert not (candidate / "manifest.json").exists()


def test_collection_failure_locks_the_study_id_to_a_new_attempt(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    environment, prerequisite, prerequisite_files, configs = _collection_inputs(repository)
    calls = 0
    attempt = study._begin_phase_attempt(  # pyright: ignore[reportPrivateUsage]
        repository,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        phase="collection",
    )

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
            attempt=attempt,
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
    marker = repository / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1" / "collection.json"
    assert json.loads(marker.read_text()) == {
        "phase": "collection",
        "study_id": "study-1",
        "url": "https://downloads.example.test/object.bin",
    }
    assert calls == 1


def test_collection_rejects_natural_variation_before_fresh_protocol_or_held_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A primary-derived metric precondition must fail before independent held-out capture."""
    repository = tmp_path / "repository"
    repository.mkdir()
    environment, prerequisite, prerequisite_files, configs = _collection_inputs(repository)
    candidate = repository / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"
    run_training, capture_held_out, calls = _offline_stage_runners(
        repository, candidate=candidate, environment=environment
    )
    attempt = study._begin_phase_attempt(  # pyright: ignore[reportPrivateUsage]
        repository,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        phase="collection",
    )
    original_align_generated = study.align_generated
    original_natural_variation = study._candidate_natural_variation  # pyright: ignore[reportPrivateUsage]
    targeted_underflows = 0

    def underflow_natural_variation(
        training: Sequence[study._CandidateTraining],  # pyright: ignore[reportPrivateUsage]
    ) -> object:
        nonlocal targeted_underflows
        assert training[0].workload == "short"
        assert [item.repeat for item in training] == [1, 2, 3]
        alignment_index = 0

        def underflow_generated(events: Sequence[TraceEvent], window: float) -> TrafficTrace:
            nonlocal alignment_index, targeted_underflows
            alignment_index += 1
            aligned = original_align_generated(events, window)
            if alignment_index == 2:
                assert events == training[0].reference
                assert window == training[1].observation_window_seconds
                targeted_underflows += 1
                return aligned[:1]
            return aligned

        with monkeypatch.context() as variation_patch:
            variation_patch.setattr(study, "align_generated", underflow_generated)
            return original_natural_variation(training)

    monkeypatch.setattr(study, "_candidate_natural_variation", underflow_natural_variation)

    with pytest.raises(TrafficlabError, match="invalid generated trace: at least two events") as error:
        study.collect_validation_candidate(
            repository_root=repository,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            attempt=attempt,
            environment=environment,
            retained_prerequisites=prerequisite,
            prerequisite_files=prerequisite_files,
            configs=configs,
            run=run_training,
            capture=capture_held_out,
            object_size_bytes=4_194_304,
        )

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("metric_infeasible", "compare", "similarity.json", "not_published", "primary")
    assert calls == [
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
    assert targeted_underflows == 1
    assert (candidate / "training" / "bursty" / "r3" / "reference.pcapng").is_file()
    assert not (candidate / "fresh_simulation").exists()
    assert not (candidate / "protocol.json").exists()
    assert not (candidate / "held_out").exists()
    assert not (candidate / "report_inputs.json").exists()
    assert not (candidate / "report.json").exists()
    assert not (candidate / "index.json").exists()
    assert not (candidate / "manifest.json").exists()


def test_collection_preserves_unexpected_programming_errors_after_freezing_the_attempt(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    environment, prerequisite, prerequisite_files, configs = _collection_inputs(repository)
    attempt = study._begin_phase_attempt(  # pyright: ignore[reportPrivateUsage]
        repository,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        phase="collection",
    )

    def broken_training(_path: Path) -> RunResult:
        raise ValueError("offline programming defect")

    with pytest.raises(ValueError, match="offline programming defect"):
        study.collect_validation_candidate(
            repository_root=repository,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            attempt=attempt,
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
    attempt = study._begin_phase_attempt(  # pyright: ignore[reportPrivateUsage]
        repository,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        phase="collection",
    )

    def inaccessible_training(_path: Path) -> RunResult:
        raise OSError("offline filesystem failure")

    with pytest.raises(TrafficlabError, match="offline filesystem failure"):
        study.collect_validation_candidate(
            repository_root=repository,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            attempt=attempt,
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
