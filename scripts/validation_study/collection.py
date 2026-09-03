"""Collection owner for Validation Study tooling."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from scripts.validation_study.candidate.artifacts import (
    begin_candidate_collection,
    candidate_fresh_record,
    candidate_training_record,
    collection_capture_lifecycle_record,
    load_candidate_training,
    select_candidate_training,
    stage_candidate_transfer_evidence,
    stage_retained_prerequisites,
    write_candidate_config_pair,
)
from scripts.validation_study.candidate.held_out import CollectionCallbackValueError, collect_held_out
from scripts.validation_study.candidate.reporting import candidate_natural_variation, candidate_report_inputs
from scripts.validation_study.common import (
    PRIMARY_ORDER,
    SUBPROCESS_TIMEOUTS,
    TARGET_REFERENCE,
    JsonObject,
    JsonValue,
    WorkloadName,
    candidate_identity,
    canonical_json,
    git_commit_value,
    image_id_value,
    phase_capture_tag,
    require,
    strict_int,
    strict_string,
    thaw_json,
    validate_endpoint_url,
    validate_study_id,
    write_candidate_bytes,
)
from scripts.validation_study.prerequisites.codec import (
    RETAINED_CAPABILITY_HEADER,
    RETAINED_PREREQUISITE_CAPABILITY_KEYS,
    parse_prerequisite_results,
    render_retained_prerequisites,
)
from scripts.validation_study.prerequisites.commands import (
    command_detail,
    completed_output,
    stdout_text,
    target_image_record,
)
from scripts.validation_study.prerequisites.run import validate_prerequisite_evidence
from scripts.validation_study.records import CommandRunner
from scripts.validation_study.rotation.run import require_successful_prerequisite_attempt, validate_base_configs
from scripts.validation_study.rotation.schema import collection_attempt_root
from scripts.validation_study.transfer import archive_transfer_evidence, prepare_transfer_scratch
from scripts.validation_study.workloads import config_with_run_directory, render_realized_config, workload_specs
from trafficlab.artifacts.io import append_run_log
from trafficlab.capture.docker.image import (
    cold_capture_build_argv,
    load_capture_image_lock,
    validate_capture_dockerfile,
)
from trafficlab.capture.stage import capture_experiment
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import TrafficlabError, attach_failure_outcome
from trafficlab.pipeline.stage import run_experiment

if TYPE_CHECKING:
    from scripts.validation_study.candidate.artifacts import CandidateTraining
    from scripts.validation_study.common import CollectionInputs, HeldOutCaptureRunner, TrainingRunner
    from scripts.validation_study.records import HeldOutEvaluation

DEFAULT_COMMAND_RUNNER: CommandRunner = cast(CommandRunner, subprocess.run)


@dataclass(slots=True)
class PhaseCaptureImage:
    """One temporary capture-image tag owned by a public study phase."""

    tag: str
    build_attempted: bool = False
    cleanup_verified: bool = False


def collection_inputs_from_prerequisites(
    repository_root: Path,
    prerequisite_path: Path,
    *,
    study_id: str,
    url: str,
    runner: CommandRunner,
    require_successful_prerequisite: bool = False,
    owned_capture_image: PhaseCaptureImage | None = None,
) -> CollectionInputs:
    """Derive immutable candidate inputs from retained same-revision prerequisite evidence."""
    root = repository_root.resolve()
    try:
        content = prerequisite_path.read_bytes()
        if require_successful_prerequisite:
            require_successful_prerequisite_attempt(root, study_id=study_id, url=url, prerequisite_content=content)
        prerequisites = parse_prerequisite_results(content, repository_root=root)
        require(
            (prerequisites.study_id, prerequisites.url) == (study_id, url),
            "collection URL and study ID must equal the retained prerequisites",
        )
        validate_prerequisite_evidence(root, prerequisites)
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
        require(commit_result.returncode == 0, "could not resolve collection Git commit")
        require(tree_result.returncode == 0, "could not resolve collection Git tree")
        status_stdout, _status_stderr = completed_output(status_result, operation="collection Git tree inspection")
        require(status_result.returncode == 0, "could not inspect collection Git tree")
        require(status_stdout == b"", "collection Git tree must remain exactly clean")
        source_commit = git_commit_value(stdout_text(commit_result, operation="collection Git commit"))
        source_tree = git_commit_value(stdout_text(tree_result, operation="collection Git tree"))
        require(
            source_commit == prerequisites.git_commit,
            "collection Git commit must equal the retained prerequisite commit",
        )
        image_lock_path = root / "docker" / "capture" / "image-lock.json"
        capture_lock = load_capture_image_lock(image_lock_path)
        validate_capture_dockerfile(
            (root / "docker" / "capture" / "Dockerfile").read_text(encoding="utf-8"), capture_lock
        )
        images = cast(JsonObject, thaw_json(prerequisites.images))
        tools = cast(JsonObject, thaw_json(prerequisites.tools))
        current_host = {
            "host_architecture": platform.machine(),
            "kernel_release": platform.release(),
            "platform": platform.platform(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
        }
        require(
            all(current_host[field] == tools[field] for field in current_host),
            "collection host, kernel, and Python must equal the retained prerequisites",
        )
        current_uv_lock = (root / "uv.lock").read_bytes()
        require(
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
        require(
            docker_version.returncode == 0
            and stdout_text(docker_version, operation="collection Docker version") == tools["docker_engine_version"],
            "collection Docker Engine must equal the retained prerequisites",
        )
        require(
            compose_version.returncode == 0
            and stdout_text(compose_version, operation="collection Docker Compose version")
            == tools["docker_compose_version"],
            "collection Docker Compose must equal the retained prerequisites",
        )
        capture_image_id = image_id_value(images["capture_image_id"], name="retained capture image ID")
        target_image_id = image_id_value(images["target_image_id"], name="retained target image ID")
        target_reference = strict_string(images["target_reference"], name="retained target image reference")
        require(target_reference == TARGET_REFERENCE, "retained target image reference must remain locked")
        target_inspect = runner(
            ("docker", "image", "inspect", target_reference),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
        )
        require(target_inspect.returncode == 0, "could not inspect the retained target image before collection")
        target_stdout, _target_stderr = completed_output(target_inspect, operation="collection target image inspect")
        current_target = target_image_record(target_stdout)
        retained_target = {
            field: images[field]
            for field in ("target_reference", "target_image_id", "target_repo_digests", "target_config_user")
        }
        require(
            current_target == retained_target, "collection target image must equal the retained prerequisite identity"
        )
        require(
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
            require(
                capture_inspect.returncode == 0
                and image_id_value(
                    stdout_text(capture_inspect, operation="collection capture image inspect"),
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
        capability = cast(JsonObject, thaw_json(prerequisites.capability))
        capability_header = (evidence_root / "capability.headers").read_bytes()
        require(
            hashlib.sha256(capability_header).hexdigest() == capability["canary_sha256"],
            "retained capability header must match the prerequisite capability identity",
        )
        files[RETAINED_CAPABILITY_HEADER] = capability_header
        retained_commands: list[JsonValue] = []
        for command, prefix in zip(prerequisites.commands, ("docker", "internet"), strict=True):
            record = cast(JsonObject, thaw_json(command))
            kind = strict_string(record["kind"], name="retained prerequisite command kind")
            expected_kind = "docker_matrix" if prefix == "docker" else "internet_smoke"
            require(kind == expected_kind, "retained prerequisite command kind does not match its evidence")
            argv = cast(list[JsonValue], record["argv"])
            tests = cast(JsonObject, record["tests"])
            contents = {
                "command": canonical_json(cast(JsonObject, {"argv": argv})),
                "status": canonical_json(cast(JsonObject, {"exit_status": 0, "tests": tests})),
                "stdout": (evidence_root / f"{prefix}.stdout").read_bytes(),
                "stderr": (evidence_root / f"{prefix}.stderr").read_bytes(),
                "junit": (evidence_root / f"{prefix}.xml").read_bytes(),
            }
            outputs: dict[str, JsonValue] = {}
            for field, body in contents.items():
                suffix = {"command": "command.json", "status": "status.json", "junit": "junit.xml"}.get(field, field)
                relative = f"prerequisites/{kind}.{suffix}"
                files[relative] = body
                outputs[field] = cast(JsonValue, {"identity": candidate_identity(body), "path": relative})
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
        uv_lock_identity = candidate_identity(current_uv_lock)
        retained_prerequisites = render_retained_prerequisites(
            cast(
                JsonObject,
                {
                    "capability": {field: capability[field] for field in RETAINED_PREREQUISITE_CAPABILITY_KEYS},
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
                    "schema_version": 5,
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
            "docker_compose_version": strict_string(tools["docker_compose_version"], name="retained Compose version"),
            "docker_engine_version": strict_string(tools["docker_engine_version"], name="retained Docker version"),
            "host_architecture": current_host["host_architecture"],
            "kernel_release": current_host["kernel_release"],
            "python_implementation": current_host["python_implementation"],
            "python_version": current_host["python_version"],
            "scientific_artifact_schema": 5,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "target_image_id": target_image_id,
            "target_image_reference": target_reference,
            "uv_lock_identity": uv_lock_identity,
        }
        require(
            bool(cast(str, environment["host_architecture"])) and bool(cast(str, environment["kernel_release"])),
            "collection environment must retain host architecture and kernel release",
        )
        configs = validate_base_configs(root, prerequisites)
        object_size_bytes = strict_int(
            prerequisites.capability["object_size_bytes"], name="retained prerequisite object size"
        )
        require(4 * 1024 * 1024 <= object_size_bytes <= 16 * 1024 * 1024, "retained object size is out of range")
        if owned_capture_image is not None:
            establish_phase_capture_image(
                root,
                phase="collection",
                expected_image_id=capture_image_id,
                capture_lock_image_id=capture_lock.expected_capture_image_id,
                owned_capture_image=owned_capture_image,
                iidfile=collection_attempt_root(root, study_id) / "collection-capture.iid",
                runner=runner,
            )
        return (environment, retained_prerequisites, files, configs, object_size_bytes)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise TrafficlabError(
            f"Validation Study collection inputs are invalid: {error}",
            corrective_action="preserve prerequisite evidence, correct the frozen inputs, and restart with a new study ID",
        ) from error


def establish_phase_capture_image(
    repository_root: Path,
    *,
    phase: Literal["collection", "study"],
    expected_image_id: str,
    capture_lock_image_id: str,
    owned_capture_image: PhaseCaptureImage,
    iidfile: Path,
    runner: CommandRunner,
) -> None:
    """Cold-build and inspect the image used by every capture in one public phase."""
    require(not owned_capture_image.build_attempted, f"{phase} capture image must be established exactly once")
    require(
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
        require(
            existing_tag.returncode == 1,
            f"{phase} capture image tag already exists and is not owned by this phase"
            if existing_tag.returncode == 0
            else f"could not inspect {phase} capture image tag before rebuild: {command_detail(existing_tag, operation=f'{phase} capture image tag inspect')}",
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
        require(
            completed.returncode == 0,
            f"could not cold-build {phase} capture image: {command_detail(completed, operation=f'{phase} capture image build')}",
        )
        rebuilt_image_id = image_id_value(iidfile.read_text(encoding="ascii").strip(), name=f"{phase} capture image ID")
        require(
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
        require(
            inspected.returncode == 0
            and image_id_value(
                stdout_text(inspected, operation=f"{phase} rebuilt capture image inspect"),
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


def remove_owned_phase_capture_image(
    owned_capture_image: PhaseCaptureImage,
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
    require(
        completed.returncode == 0,
        f"could not remove owned {phase} capture image: {command_detail(completed, operation=f'{phase} capture image cleanup')}",
    )


def _complete_collection_capture_image_cleanup(
    owned_capture_image: PhaseCaptureImage, *, repository_root: Path, runner: CommandRunner
) -> None:
    """Remove and prove absence of the exact collection tag before candidate finalization."""
    require(owned_capture_image.build_attempted, "collection capture image must be established before cleanup")
    try:
        remove_owned_phase_capture_image(
            owned_capture_image, phase="collection", repository_root=repository_root, runner=runner
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
            + command_detail(inspected, operation="collection capture image post-cleanup inspect")
        )
        raise ValueError(detail)
    owned_capture_image.cleanup_verified = True


def _finalize_collection_lifecycle(
    *,
    candidate: Path,
    environment: Mapping[str, object],
    held_out: Sequence[JsonObject],
    owned_capture_image: PhaseCaptureImage | None,
    repository_root: Path,
    runner: CommandRunner,
    study_id: str,
    training: Sequence[JsonObject],
) -> None:
    """Publish the one audit-owned cleanup contract only after every cleanup proof succeeds."""
    if owned_capture_image is None:
        raise ValueError("collection finalization requires its owned capture image")
    expected_tag = phase_capture_tag(study_id, "collection")
    require(owned_capture_image.tag == expected_tag, "collection lifecycle must use its exact owned capture image tag")
    capture_image_id = environment.get("capture_image_id")
    require(type(capture_image_id) is str and capture_image_id.startswith("sha256:"), "invalid capture image identity")
    project_names = [row.get("project_name") for row in (*training, *held_out)]
    require(
        len(project_names) == 12 and all(type(project_name) is str for project_name in project_names),
        "collection lifecycle must retain twelve exact capture project names",
    )
    require(
        len({cast(str, project_name) for project_name in project_names}) == len(project_names),
        "collection lifecycle must retain distinct capture project names",
    )
    _complete_collection_capture_image_cleanup(owned_capture_image, repository_root=repository_root, runner=runner)
    write_candidate_bytes(
        candidate / "lifecycle.json",
        canonical_json(
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
    owned_capture_image: PhaseCaptureImage | None = None,
    runner: CommandRunner = DEFAULT_COMMAND_RUNNER,
) -> Path:
    """Collect one immutable, audit-ready real-program validation candidate
    through the existing capture, fitting, generation, and comparison owners.
    """
    root = repository_root.resolve()
    checked_study_id = validate_study_id(study_id)
    checked_url = validate_endpoint_url(url)
    try:
        candidate, attempt = begin_candidate_collection(
            root,
            attempt=attempt,
            study_id=checked_study_id,
            url=checked_url,
            environment=environment,
            retained_prerequisites=retained_prerequisites,
            configs=configs,
            object_size_bytes=object_size_bytes,
        )
        write_candidate_bytes(candidate / "environment.json", canonical_json(cast(JsonObject, dict(environment))))
        stage_retained_prerequisites(candidate, content=retained_prerequisites, files=prerequisite_files)
        workloads = {item.name: item for item in workload_specs(checked_url)}
        training: list[CandidateTraining] = []
        training_lifecycle: list[JsonObject] = []
        for _order, run_id, workload_value, repeat in PRIMARY_ORDER:
            workload_name = cast(WorkloadName, workload_value)
            workload = workloads[workload_name]
            directory = candidate / "training" / workload_name / f"r{repeat}"
            config = config_with_run_directory(configs[workload_name], directory)
            source = attempt / f"training-{workload_name}-r{repeat}.toml"
            render_realized_config(config, source)
            prepared = prepare_transfer_scratch(root, checked_study_id, run_id, workload)
            started = perf_counter()
            try:
                run_result = run(source)
            except ValueError as error:
                raise CollectionCallbackValueError(error) from error
            runtime_seconds = perf_counter() - started
            require(math.isfinite(runtime_seconds) and runtime_seconds >= 0.0, "training runtime must be finite")
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
                root, checked_study_id, run_id, workload, prepared, object_size_bytes=object_size_bytes
            )
            stage_candidate_transfer_evidence(
                root, candidate, responses, scope="training", run_id=run_id, workload=workload
            )
            write_candidate_config_pair(
                config,
                candidate / "configs" / f"training-{workload_name}-r{repeat}.portable.toml",
                candidate / "configs" / f"training-{workload_name}-r{repeat}.realized.toml",
            )
            loaded = load_candidate_training(
                directory, workload=workload_name, repeat=repeat, config=config, runtime_seconds=runtime_seconds
            )
            training.append(loaded)
            training_lifecycle.append(
                collection_capture_lifecycle_record(
                    run_result.capture,
                    directory=directory,
                    directory_relative=f"training/{workload_name}/r{repeat}",
                    run_id=run_id,
                )
            )
        try:
            natural_variation = tuple(
                candidate_natural_variation([item for item in training if item.workload == workload])
                for workload in ("short", "streaming", "bursty")
            )
        except TrafficlabError as error:
            natural_error = TrafficlabError(
                str(error), corrective_action="correct samples or settings", failure_outcomes=error.failure_outcomes
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
            fresh_path, fresh_record = candidate_fresh_record(loaded)
            write_candidate_bytes(candidate / fresh_path, canonical_json(fresh_record))
            fresh.append(fresh_record)
        selected = select_candidate_training(training)
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
                "schema_version": 5,
                "selection_seeds": list(configs["short"].genetic.trial_seeds),
                "study_id": checked_study_id,
                "training_repetitions": 3,
                "workloads": ["short", "streaming", "bursty"],
            },
        )
        write_candidate_bytes(candidate / "protocol.json", canonical_json(protocol))
        selected_training: dict[WorkloadName, CandidateTraining] = {}
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
            held_record, evaluation, capture_result = collect_held_out(
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
                collection_capture_lifecycle_record(
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
        report_inputs = candidate_report_inputs(training, held_evaluations, natural_variation=natural_variation)
        write_candidate_bytes(candidate / "report_inputs.json", canonical_json(report_inputs))
        write_candidate_bytes(
            candidate / "report.json",
            canonical_json(
                cast(
                    JsonObject,
                    {
                        "formula": "arithmetic_mean",
                        "report_inputs_identity": candidate_identity((candidate / "report_inputs.json").read_bytes()),
                        "summary": report_inputs,
                    },
                )
            ),
        )
        workload_order: dict[WorkloadName, int] = {"short": 0, "streaming": 1, "bursty": 2}
        ordered_training = tuple(sorted(training, key=lambda item: (workload_order[item.workload], item.repeat)))
        sorted_fresh = sorted(
            fresh,
            key=lambda record: (workload_order[cast(WorkloadName, record["workload"])], cast(int, record["repeat"])),
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
            "schema_version": 5,
            "training": [
                cast(JsonValue, candidate_training_record(item, environment=environment)) for item in ordered_training
            ],
        }
        write_candidate_bytes(candidate / "index.json", canonical_json(index))
        from scripts.validation_study.audit.artifacts import lineage_for_path, owner_for_path, write_manifest
        from scripts.validation_study.audit.common import files_for_candidate
        from scripts.validation_study.audit.lifecycle import audit_bundle

        files = files_for_candidate(candidate, include_manifest=False)
        index["ownership"] = cast(JsonValue, {relative: owner_for_path(relative) for relative in files})
        index["lineage"] = cast(JsonValue, {relative: lineage_for_path(relative) for relative in files})
        (candidate / "index.json").write_bytes(canonical_json(index))
        files = files_for_candidate(candidate, include_manifest=False)
        write_manifest(
            candidate,
            ownership={relative: owner_for_path(relative) for relative in files},
            lineage={relative: lineage_for_path(relative) for relative in files},
        )
        audit_bundle(candidate, repository=root)
        return candidate
    except CollectionCallbackValueError as error:
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
