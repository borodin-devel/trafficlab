"""Reproduction owner for Validation Study tooling."""

from __future__ import annotations

import platform
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scripts.validation_study.collection import establish_phase_capture_image
from scripts.validation_study.common import (
    FAMILY_ORDER,
    PRIMARY_ORDER,
    PUBLISHED_METHOD_ORDER,
    REPORT_HEADINGS,
    RUNTIME_BOUNDARY,
    SUBPROCESS_TIMEOUTS,
    TARGET_REFERENCE,
    FrozenJsonObject,
    FrozenJsonValue,
    JsonObject,
    JsonValue,
    WorkloadName,
    freeze_object,
    git_commit_value,
    image_id_value,
    path_entry_exists,
    require,
    strict_float,
    strict_string,
    study_git_status_is_permitted,
    thaw_json,
    validate_study_id,
)
from scripts.validation_study.evidence import (
    fresh_run_log_proofs,
    load_persisted_run_evidence,
    reconstruct_science,
    repository_path_record,
    sole_final_trial,
    trace_summary,
    validate_transfer_archives,
)
from scripts.validation_study.prerequisites.codec import parse_prerequisite_results
from scripts.validation_study.prerequisites.commands import (
    command_detail,
    completed_output,
    guard_prefix,
    inspected_image_id,
    private_bytes,
    stdout_text,
    target_image_record,
)
from scripts.validation_study.prerequisites.run import validate_prerequisite_evidence
from scripts.validation_study.records import ReproductionRecord, StudyRunSpec
from scripts.validation_study.results.codec import (
    parse_study_results,
    validate_reproduction_comparison,
    validate_run_evidence,
)
from scripts.validation_study.results.reporting import (
    WORKLOAD_ORDER,
    family_champions,
    group_run_documents,
    natural_variation,
    score_delta,
    score_from_comparison,
    score_from_trial,
    select_winner,
    study_run_document,
    symmetric_reference_score,
    validate_natural_variation,
    validate_workload_summary,
    workload_summaries,
)
from scripts.validation_study.rotation.run import validate_base_configs
from scripts.validation_study.transfer import archive_transfer_evidence, best_effort_archive, prepare_transfer_scratch
from scripts.validation_study.workloads import config_with_run_directory, render_realized_config, workload_specs
from trafficlab import __version__
from trafficlab.capture.docker.image import (
    load_capture_image_lock,
    validate_capture_dockerfile,
)
from trafficlab.capture.validation import validate_capture_pair
from trafficlab.common.config import ExperimentConfig, SimilarityConfig
from trafficlab.common.config_io import load_experiment
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import (
    TrafficTrace,
    parse_capture_metadata,
)
from trafficlab.comparison.codec import (
    sha256_bytes,
)
from trafficlab.fitting.genetic.evaluation import evaluate_final, validate_evaluation_context
from trafficlab.fitting.genetic.types import Candidate
from trafficlab.study_evidence.publication import publish_accepted_bundle

if TYPE_CHECKING:
    from scripts.validation_study.collection import PhaseCaptureImage
    from scripts.validation_study.evidence import LoadedRunEvidence
    from scripts.validation_study.records import CommandRunner, PrerequisiteResults, StudyResults, StudyRunRecord
    from scripts.validation_study.workloads import WorkloadSpec


def _study_identity(*, repository_root: Path, runner: CommandRunner) -> JsonObject:
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
        require(
            result.returncode == 0, f"could not complete {operation}: {command_detail(result, operation=operation)}"
        )
        completed.append(result)
    commit_result, status_result, docker_result, compose_result = completed
    status_stdout, _status_stderr = completed_output(status_result, operation="Git tree inspection")
    require(
        study_git_status_is_permitted(status_stdout),
        "study checkout may differ only by the generated Validation Study prerequisite and checked base configs",
    )
    return {
        "git_commit": git_commit_value(stdout_text(commit_result, operation="Git commit inspection")),
        "python_version": platform.python_version(),
        "trafficlab_version": __version__,
        "docker_engine_version": strict_string(
            stdout_text(docker_result, operation="Docker version"), name="Docker Engine version"
        ),
        "docker_compose_version": strict_string(
            stdout_text(compose_result, operation="Docker Compose version"), name="Docker Compose version"
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
    require(
        target_result.returncode == 0,
        f"could not inspect live target image: {command_detail(target_result, operation='target image inspect')}",
    )
    target_stdout, _target_stderr = completed_output(target_result, operation="target image inspect")
    return target_image_record(target_stdout)


def _study_capture_image_identity(*, repository_root: Path, capture_image_id: str, runner: CommandRunner) -> str:
    """Inspect the fresh study-owned capture image after its cold build."""
    capture_result = runner(
        ("docker", "image", "inspect", capture_image_id),
        cwd=repository_root,
        check=False,
        capture_output=True,
        shell=False,
        timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
    )
    require(
        capture_result.returncode == 0,
        f"could not inspect live capture image: {command_detail(capture_result, operation='capture image inspect')}",
    )
    capture_stdout, _capture_stderr = completed_output(capture_result, operation="capture image inspect")
    return inspected_image_id(capture_stdout, name="capture")


def validated_study_inputs(
    url: str,
    study_id: str,
    prerequisite_path: Path,
    *,
    repository_root: Path,
    runner: CommandRunner,
    owned_capture_image: PhaseCaptureImage | None = None,
    capture_iidfile: Path | None = None,
) -> tuple[PrerequisiteResults, dict[WorkloadName, ExperimentConfig], JsonObject, bytes]:
    root = repository_root.resolve()
    expected_path = root / "examples" / "validation_study" / "prerequisites.json"
    require(prerequisite_path.resolve() == expected_path, "study prerequisite path must use its exact checked path")
    try:
        prerequisite_content = prerequisite_path.read_bytes()
    except OSError as error:
        raise ValueError(f"could not read Validation Study prerequisites: {error}") from error
    prerequisites = parse_prerequisite_results(prerequisite_content, repository_root=root)
    validate_prerequisite_evidence(root, prerequisites)
    require(
        prerequisites.study_id == study_id and prerequisites.url == url,
        "study ID and URL must exactly match prerequisite evidence",
    )
    identity = _study_identity(repository_root=root, runner=runner)
    tools = prerequisites.tools
    require(
        identity["git_commit"] == prerequisites.git_commit
        and identity["python_version"] == tools["python_version"]
        and (identity["trafficlab_version"] == tools["trafficlab_version"])
        and (identity["docker_engine_version"] == tools["docker_engine_version"])
        and (identity["docker_compose_version"] == tools["docker_compose_version"])
        and (identity["platform"] == tools["platform"]),
        "live study commit and tool identities must exactly match prerequisite evidence",
    )
    configs = validate_base_configs(root, prerequisites)
    images = prerequisites.images
    capture_image_id = image_id_value(images["capture_image_id"], name="retained capture image ID")
    capture_lock_image_id = capture_image_id
    if owned_capture_image is not None:
        if capture_iidfile is None:
            raise ValueError("study capture IID file is required for an owned capture image")
        capture_lock = load_capture_image_lock(root / "docker" / "capture" / "image-lock.json")
        validate_capture_dockerfile(
            (root / "docker" / "capture" / "Dockerfile").read_text(encoding="utf-8"), capture_lock
        )
        capture_lock_image_id = capture_lock.expected_capture_image_id
        require(
            capture_image_id == capture_lock_image_id,
            "study capture image must equal the checked image lock before rebuild",
        )
    live_target = _study_target_image_identity(repository_root=root, runner=runner)
    require(
        live_target["target_reference"] == images["target_reference"]
        and live_target["target_image_id"] == images["target_image_id"]
        and (tuple(cast(list[JsonValue], live_target["target_repo_digests"])) == images["target_repo_digests"])
        and (live_target["target_config_user"] == images["target_config_user"]),
        "study image identities must exactly match approved prerequisite evidence",
    )
    if owned_capture_image is not None:
        assert capture_iidfile is not None
        establish_phase_capture_image(
            root,
            phase="study",
            expected_image_id=capture_image_id,
            capture_lock_image_id=capture_lock_image_id,
            owned_capture_image=owned_capture_image,
            iidfile=capture_iidfile,
            runner=runner,
        )
    live_capture_image_id = _study_capture_image_identity(
        repository_root=root, capture_image_id=capture_image_id, runner=runner
    )
    require(
        live_capture_image_id == capture_image_id,
        "study image identities must exactly match approved prerequisite evidence",
    )
    return (prerequisites, configs, identity, prerequisite_content)


def primary_run_specs(
    repository_root: Path, study_id: str, configs: Mapping[WorkloadName, ExperimentConfig]
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
            require(not path_entry_exists(path), f"{name} already exists: {path}")
        expected_config = config_with_run_directory(configs[workload], run_directory)
        require(expected_config.run.directory == run_directory, "primary run directory must be exact")
        specs.append(StudyRunSpec(order, run_id, workload, repeat, config_path, run_directory, evidence_directory))
    return tuple(specs)


def load_reference_trace(run_directory: Path) -> TrafficTrace:
    capture_path = run_directory / "capture.json"
    reference_path = run_directory / "reference.pcapng"
    validate_capture_pair(capture_path, reference_path, deadline=None)
    metadata = parse_capture_metadata(capture_path.read_bytes(), source=capture_path)
    return read_pcapng_bytes(reference_path.read_bytes(), metadata, source=reference_path)


def validate_primary_derived_records(
    records: Sequence[StudyRunRecord],
    variation: Sequence[JsonObject | FrozenJsonObject],
    summaries: Sequence[JsonObject | FrozenJsonObject],
) -> tuple[
    tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject],
    tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject],
]:
    grouped = group_run_documents(records)
    require(len(variation) == 3 and len(summaries) == 3, "study derived records must contain three workloads")
    validated_variation: list[FrozenJsonObject] = []
    validated_summaries: list[FrozenJsonObject] = []
    for index, workload in enumerate(WORKLOAD_ORDER):
        variation_document = cast(JsonObject, thaw_json(cast(FrozenJsonValue, variation[index])))
        summary_document = cast(JsonObject, thaw_json(cast(FrozenJsonValue, summaries[index])))
        validated_variation.append(
            freeze_object(validate_natural_variation(variation_document, workload=workload, runs=grouped[workload]))
        )
        validated_summaries.append(
            freeze_object(validate_workload_summary(summary_document, workload=workload, runs=grouped[workload]))
        )
    return (
        cast(tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject], tuple(validated_variation)),
        cast(tuple[FrozenJsonObject, FrozenJsonObject, FrozenJsonObject], tuple(validated_summaries)),
    )


def environment_record(prerequisites: PrerequisiteResults, identity: JsonObject, created: str) -> FrozenJsonObject:
    images = prerequisites.images
    return freeze_object(
        {
            **identity,
            "target_image_id": cast(str, images["target_image_id"]),
            "capture_image_id": cast(str, images["capture_image_id"]),
            "study_date_utc": created,
        }
    )


def protocol_record(prerequisites: PrerequisiteResults, prerequisite_content: bytes) -> FrozenJsonObject:
    specs = workload_specs(prerequisites.url)
    return freeze_object(
        {
            "study_id": prerequisites.study_id,
            "url": prerequisites.url,
            "capability": thaw_json(prerequisites.capability),
            "prerequisites_sha256": sha256_bytes(prerequisite_content),
            "target_reference": TARGET_REFERENCE,
            "capture_image_id": cast(str, prerequisites.images["capture_image_id"]),
            "transfer_evidence_mount_source": f"examples/validation_study/.study-work/mount/{prerequisites.study_id}",
            "base_config_sha256": thaw_json(prerequisites.config_sha256),
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


def run_cli_reproduction(
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
        require(
            source.key == {"workload": "streaming", "repeat": 2} and workload.name == "streaming",
            "reproduction source must be preselected streaming repeat 2",
        )
        source_snapshot = root / source.run_directory / "experiment.toml"
        source_config = load_experiment(source_snapshot)
        expected_source_run = root / source.run_directory
        expected_source = config.model_copy(
            update={"run": config.run.model_copy(update={"directory": expected_source_run})}
        )
        require(source_config == expected_source, "saved reproduction source config must equal the locked base config")
        for path, name in (
            (run_directory, "reproduction run directory"),
            (config_path, "reproduction config"),
            (evidence_directory, "reproduction evidence directory"),
        ):
            require(not path_entry_exists(path), f"{name} already exists: {path}")
        reproduction_config = source_config.model_copy(
            update={"run": source_config.run.model_copy(update={"directory": run_directory})}
        )
        render_realized_config(reproduction_config, config_path)
        reloaded = load_experiment(config_path)
        require(
            reloaded == reproduction_config
            and reloaded.run.model_copy(update={"directory": source_config.run.directory}) == source_config.run
            and (reloaded.model_copy(update={"run": source_config.run}) == source_config),
            "reproduction config must change only run.directory",
        )
        require(not path_entry_exists(run_directory), "reproduction run directory must remain absent before CLI")
        config_record = repository_path_record(config_path, repository_root=root, name="reproduction config path")
        command = ("uv", "run", "--locked", "trafficlab", "run", config_record)
        guard_command = (*guard_prefix("20m"), *command)
        prepared = prepare_transfer_scratch(root, study_id, run_id, workload)
        require(not path_entry_exists(run_directory), "reproduction must seed no stage artifact before CLI")
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
        stdout, stderr = completed_output(completed, operation="reproduction guard")
        private_bytes(evidence_directory / "guard.stdout", stdout)
        private_bytes(evidence_directory / "guard.stderr", stderr)
        require(
            completed.returncode == 0,
            f"reproduction guard failed with status {completed.returncode}: {command_detail(completed, operation='reproduction guard')}",
        )
        responses = archive_transfer_evidence(
            root, study_id, run_id, workload, prepared, object_size_bytes=object_size_bytes
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
        archive_diagnostic = best_effort_archive(evidence_directory, prepared)
        secondary = f"; secondary evidence archive failure: {archive_diagnostic}" if archive_diagnostic else ""
        raise TrafficlabError(
            f"Validation Study reproduction failed for workload streaming, repeat 2, position 10, raw run path {repository_path_record(run_directory, repository_root=root, name='failed reproduction path')}: {error}{secondary}",
            corrective_action="preserve the failed evidence and restart the balanced protocol with a new study ID",
        ) from error


def _checkpoint_winner(evidence: LoadedRunEvidence) -> Candidate:
    candidates = tuple(
        candidate
        for candidate in evidence.checkpoint.population
        if candidate.identifier == evidence.checkpoint.best_identifier
    )
    require(len(candidates) == 1, "terminal checkpoint must contain exactly one reproduction winner")
    candidate = candidates[0]
    select_winner(evidence.checkpoint, evidence.best_model)
    return candidate


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
    require(
        (spec.execution_order, spec.run_id, spec.workload, spec.repeat)
        == (10, "10-streaming-r2-reproduction", "streaming", 2),
        "reproduction spec must equal the exact fresh tenth run",
    )
    require(source.key == {"workload": "streaming", "repeat": 2}, "reproduction source must be streaming repeat 2")
    require(completed.returncode == 0, "reproduction guard must succeed before reconstruction")
    elapsed = strict_float(elapsed_seconds, name="reproduction elapsed seconds", lower=0.0)
    require(elapsed > 0.0, "reproduction elapsed seconds must be positive")
    evidence = load_persisted_run_evidence(spec)
    source_config = load_experiment(root / source.run_directory / "experiment.toml")
    expected_config = source_config.model_copy(
        update={"run": source_config.run.model_copy(update={"directory": spec.run_directory})}
    )
    require(
        evidence.config == expected_config,
        "reproduction retained config must differ from its saved source only by run.directory",
    )
    fresh_run_log_proofs(evidence.log_records)
    candidate = _checkpoint_winner(evidence)
    validated_context = validate_evaluation_context(evidence.context.evaluation)
    fresh_simulation = sole_final_trial(evaluate_final(candidate, validated_context, 97))
    science = reconstruct_science(evidence, fresh_simulation, generated_path=spec.run_directory / "generated.pcapng")
    window = evidence.best_model.observation_window_seconds
    config_path = repository_path_record(spec.config_path, repository_root=root, name="reproduction config path")
    run_directory = repository_path_record(spec.run_directory, repository_root=root, name="reproduction run directory")
    evidence_directory = repository_path_record(
        spec.transfer_evidence_directory, repository_root=root, name="reproduction transfer evidence directory"
    )
    object_size = validate_transfer_archives(
        root, transfer_responses, workload="streaming", evidence_directory=evidence_directory
    )
    require(object_size >= 4 * 1024 * 1024, "reproduction transfer must retain the prerequisite object size")
    winner = select_winner(evidence.checkpoint, evidence.best_model)
    fresh_simulation_score = score_from_trial(science.fresh_simulation)
    published_score = score_from_comparison(science.published)
    source_document = study_run_document(source)
    source_winner = cast(JsonObject, source_document["winner"])
    source_fresh_simulation = cast(JsonObject, cast(JsonObject, source_document["fresh_simulation"])["score"])
    source_published = cast(JsonObject, cast(JsonObject, source_document["published"])["score"])
    source_reference = load_reference_trace(root / source.run_directory)
    stdout, stderr = completed_output(completed, operation="reproduction guard")
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
        "reference": trace_summary(evidence.reference, science.published, role="reference"),
        "generated": trace_summary(science.aligned_events, science.published, role="generated"),
        "family_champions": list(family_champions(evidence.checkpoint)),
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
            "fresh_simulation_delta": score_delta(fresh_simulation_score, source_fresh_simulation),
            "published_delta": score_delta(published_score, source_published),
            "reference_similarity": symmetric_reference_score(
                source_reference, evidence.reference, evidence.config.similarity
            ),
        },
    }
    validate_run_evidence(
        document,
        repository_root=root,
        workload="streaming",
        evidence_directory=evidence_directory,
        object_size=object_size,
        fresh_simulation_source="post_cli_evaluate_final",
    )
    validate_reproduction_comparison(document["comparison_to_source"], reproduction=document, source=source_document)
    return ReproductionRecord(freeze_object(document))


def _audit_primary_record(repository_root: Path, record: StudyRunRecord, object_size_bytes: int) -> TrafficTrace:
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
    evidence = load_persisted_run_evidence(spec)
    fresh_run_log_proofs(evidence.log_records)
    require(evidence.artifact_sha256 == record.artifact_sha256, "primary artifact hashes must match retained files")
    responses = tuple(cast(JsonObject, thaw_json(item)) for item in record.transfer_responses)
    observed_size = validate_transfer_archives(
        root, responses, workload=workload_name, evidence_directory=record.transfer_evidence_directory
    )
    require(observed_size == object_size_bytes, "primary transfer object size must match prerequisite capability")
    candidate = _checkpoint_winner(evidence)
    fresh_simulation = sole_final_trial(
        evaluate_final(candidate, validate_evaluation_context(evidence.context.evaluation), 97)
    )
    science = reconstruct_science(evidence, fresh_simulation, generated_path=spec.run_directory / "generated.pcapng")
    window = evidence.best_model.observation_window_seconds
    expected = {
        "reference": trace_summary(evidence.reference, science.published, role="reference"),
        "generated": trace_summary(science.aligned_events, science.published, role="generated"),
        "family_champions": list(family_champions(evidence.checkpoint)),
        "winner": select_winner(evidence.checkpoint, evidence.best_model),
        "fresh_simulation": {
            "seed": 97,
            "score": score_from_trial(science.fresh_simulation),
            "source": "run_experiment_fit_outcome",
        },
        "published": {"seed": 97, "score": score_from_comparison(science.published)},
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
    document = study_run_document(record)
    for name, value in expected.items():
        require(document[name] == value, f"primary {name} must match locally reconstructed evidence")
    return evidence.reference


def _audit_reproduction_record(repository_root: Path, results: StudyResults) -> None:
    root = repository_root.resolve()
    document = cast(JsonObject, thaw_json(results.reproduction.document))
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
    require(
        stat.S_IMODE(stdout_path.lstat().st_mode) == stat.S_IMODE(stderr_path.lstat().st_mode) == 384,
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
    require(reconstructed == results.reproduction, "reproduction must match local read-only reconstruction")


def _require_report_evidence(content: str, prerequisites: PrerequisiteResults, results: StudyResults) -> None:
    identifiers = (
        prerequisites.study_id,
        prerequisites.git_commit,
        cast(str, prerequisites.images["target_image_id"]),
        cast(str, prerequisites.images["capture_image_id"]),
        *(record.run_id for record in results.runs),
        cast(str, results.reproduction.document["run_id"]),
    )
    require(all(heading in content for heading in REPORT_HEADINGS), "report must contain all seven required headings")
    require(all(identifier in content for identifier in identifiers), "report must identify the study and all ten runs")


def audit_published_study(
    *, repository_root: Path, prerequisite_path: Path, result_path: Path, report_path: Path
) -> None:
    root = repository_root.resolve()
    try:
        expected_paths = (
            (prerequisite_path, root / "examples" / "validation_study" / "prerequisites.json", "prerequisite"),
            (result_path, root / "examples" / "validation_study" / "results.json", "result"),
            (report_path, root / "examples" / "validation_study" / "REPORT.md", "report"),
        )
        for path, expected, name in expected_paths:
            require(path.resolve() == expected, f"audit {name} path must use its exact checked location")
        prerequisite_content = prerequisite_path.read_bytes()
        result_content = result_path.read_bytes()
        report_content = report_path.read_text(encoding="utf-8")
        prerequisites = parse_prerequisite_results(prerequisite_content, repository_root=root)
        results = parse_study_results(result_content, repository_root=root)
        validate_prerequisite_evidence(root, prerequisites)
        _require_report_evidence(report_content, prerequisites, results)
        protocol = results.protocol
        environment = results.environment
        require(
            protocol["study_id"] == prerequisites.study_id
            and protocol["url"] == prerequisites.url
            and (protocol["capability"] == prerequisites.capability)
            and (protocol["prerequisites_sha256"] == sha256_bytes(prerequisite_content))
            and (protocol["base_config_sha256"] == prerequisites.config_sha256),
            "published result protocol must exactly match canonical prerequisites",
        )
        require(
            environment["git_commit"] == prerequisites.git_commit
            and environment["python_version"] == prerequisites.tools["python_version"]
            and (environment["trafficlab_version"] == prerequisites.tools["trafficlab_version"])
            and (environment["docker_engine_version"] == prerequisites.tools["docker_engine_version"])
            and (environment["docker_compose_version"] == prerequisites.tools["docker_compose_version"])
            and (environment["platform"] == prerequisites.tools["platform"])
            and (environment["target_image_id"] == prerequisites.images["target_image_id"])
            and (environment["capture_image_id"] == prerequisites.images["capture_image_id"]),
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
            require(
                load_experiment(root / record.config_path) == expected_config, "realized primary config must be exact"
            )
            traces[workload, cast(int, record.key["repeat"])] = _audit_primary_record(root, record, object_size)
            settings[workload] = expected_config.similarity
        variation = natural_variation(results.runs, traces, settings)
        summaries = workload_summaries(results.runs)
        require(
            tuple(freeze_object(value) for value in variation) == results.natural_variation
            and tuple(freeze_object(value) for value in summaries) == results.workload_summaries,
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
    from scripts.validation_study.audit.lifecycle import audit_staged_bundle

    root = repository_root.resolve()
    checked_study_id = validate_study_id(study_id)
    try:
        require(candidate.name == checked_study_id, "candidate ID must equal the requested destination ID")
    except ValueError as error:
        raise TrafficlabError(
            f"Validation Study candidate ID is incompatible with the requested destination: {error}",
            corrective_action="preserve the candidate and publish it only to its frozen study ID",
        ) from error

    def audit(candidate_root: Path) -> None:
        audit_staged_bundle(candidate_root, repository=root, source_candidate=candidate.resolve())

    return publish_accepted_bundle(
        candidate, root / "examples" / "validation_study" / "evidence", checked_study_id, audit
    )
