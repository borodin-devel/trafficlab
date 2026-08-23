"""Fixture owner for Validation Study tooling."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scripts.validation_study.audit.artifacts import lineage_for_path, owner_for_path, write_manifest
from scripts.validation_study.audit.common import files_for_candidate
from scripts.validation_study.audit.environment import validate_source_identities
from scripts.validation_study.candidate.artifacts import load_candidate_training
from scripts.validation_study.candidate.held_out import evaluate_study_held_out
from scripts.validation_study.candidate.reporting import candidate_natural_variation, candidate_report_inputs
from scripts.validation_study.common import ARTIFACT_NAMES, PRIMARY_ORDER, TARGET_REFERENCE, WorkloadName
from scripts.validation_study.prerequisites.codec import render_retained_prerequisites
from scripts.validation_study.prerequisites.commands import prerequisite_command_argv, prerequisite_junit_counts
from scripts.validation_study.transfer import (
    OBJECT_SIZE_BYTES,
    URL,
    WORKLOADS,
    fixture_canonical_json,
    write_transfer_evidence,
)
from scripts.validation_study.workloads import workload_specs
from trafficlab.artifacts.io import append_run_log
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_configuration_pair, render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import encode_pcapng, read_pcapng_bytes
from trafficlab.common.trace import (
    CaptureMetadata,
    TraceEvent,
    TrafficTrace,
    align_generated,
    normalize_reference,
    parse_capture_metadata,
    render_capture_metadata,
)
from trafficlab.comparison.codec import render_comparison_result, similarity_settings_identity
from trafficlab.comparison.metrics import compare_traces
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.checkpoint import parse_checkpoint
from trafficlab.fitting.genetic.strategy import make_strategy_context, run_strategy
from trafficlab.fitting.stage import FitDependencies, fit_experiment, read_fit_input
from trafficlab.generation.models.fitted_model import (
    load_best_model,
)
from trafficlab.generation.stage import reproduce_generated_pcapng
from trafficlab.preflight.types import PreflightReport, PreparedExperiment

if TYPE_CHECKING:
    from scripts.validation_study.records import HeldOutEvaluation

REPOSITORY = Path(__file__).resolve().parents[2]

FIXTURE = REPOSITORY / "tests" / "fixtures" / "data" / "validation_study" / "candidate"

FIT_FIXTURE = REPOSITORY / "examples" / "data" / "fit"

REPEATS = (1, 2, 3)

_IMAGE_LOCK = cast(dict[str, object], json.loads((REPOSITORY / "docker" / "capture" / "image-lock.json").read_text()))

CAPTURE_ID = cast(str, _IMAGE_LOCK["expected_capture_image_id"])

CAPTURE_REFERENCE = f"trafficlab-capture@{CAPTURE_ID}"

CAPTURE_TOOL_VERSION = cast(str, _IMAGE_LOCK["capture_tool_version"])

TARGET_ID = f"sha256:{TARGET_REFERENCE.rsplit(':', 1)[-1]}"

_STUDY_ID = "fixture-study"


def _base_config(workload: WorkloadName) -> ExperimentConfig:
    config = load_configuration_pair(FIT_FIXTURE / "experiment.toml").portable
    profile = next(spec for spec in workload_specs(URL) if spec.name == workload)
    return config.model_copy(
        update={
            "target": config.target.model_copy(update={"argv": profile.argv, "image": TARGET_REFERENCE}),
            "capture": config.capture.model_copy(update={"image": CAPTURE_REFERENCE}),
        }
    )


def _metadata() -> CaptureMetadata:
    return parse_capture_metadata((FIT_FIXTURE / "capture.json").read_bytes(), source=FIT_FIXTURE / "capture.json")


def _base_events(metadata: CaptureMetadata) -> tuple[TraceEvent, ...]:
    return read_pcapng_bytes(
        (FIT_FIXTURE / "reference.pcapng").read_bytes(), metadata, source=FIT_FIXTURE / "reference.pcapng"
    ).to_events()


def _encode_events(events: Sequence[TraceEvent], metadata: CaptureMetadata) -> bytes:
    trace = TrafficTrace.from_events(events)
    return encode_pcapng(trace, metadata, observation_window_seconds=max(1.0, float(trace.timestamps[-1]))).content


def _variant_events(events: Sequence[TraceEvent], *, variant: int) -> tuple[TraceEvent, ...]:
    result: list[TraceEvent] = []
    last = len(events) - 1
    for index, event in enumerate(events):
        timestamp = event.timestamp
        if 0 < index < last:
            timestamp += 0.001 * variant * (index % 5 - 2)
        frame_length = event.frame_length + variant * (index % 3 + 1)
        result.append(TraceEvent(timestamp, event.direction, frame_length))
    return tuple(result)


def _held_out_events(events: Sequence[TraceEvent], *, variant: int, window_scale: float) -> tuple[TraceEvent, ...]:
    """Make one independent held-out trace with its own valid comparison horizon."""
    return tuple(
        TraceEvent(event.timestamp * window_scale, event.direction, event.frame_length)
        for event in _variant_events(events, variant=variant)
    )


def _prepared(config: ExperimentConfig, run_directory: Path) -> PreparedExperiment:
    return PreparedExperiment(
        source=Path("fixture-validation-study.toml"),
        config=config,
        report=PreflightReport(config, ()),
        run_directory=run_directory,
    )


def _config_bytes(config: ExperimentConfig, run_directory: str) -> bytes:
    return render_effective_config(
        config.model_copy(update={"run": config.run.model_copy(update={"directory": Path(run_directory)})})
    )


def _capture_lineage(capture: bytes) -> dict[str, object]:
    return {
        "capture_identity": identify_bytes(capture).as_dict(),
        "capture_image_id": CAPTURE_ID,
        "capture_image_reference": CAPTURE_REFERENCE,
        "capture_tool_version": CAPTURE_TOOL_VERSION,
        "target_image_id": TARGET_ID,
        "target_image_reference": TARGET_REFERENCE,
    }


def _capture_log_environment() -> dict[str, object]:
    return {
        "capture_content_id": CAPTURE_ID,
        "capture_reference": CAPTURE_REFERENCE,
        "capture_tool_version": CAPTURE_TOOL_VERSION,
        "host_architecture": "linux/amd64",
        "target_content_id": TARGET_ID,
        "target_reference": TARGET_REFERENCE,
    }


def _append_capture_log_lineage(
    directory: Path, *, capture: bytes, reference: bytes, experiment: bytes, packet_count: int, project_name: str
) -> None:
    environment = _capture_log_environment()
    append_run_log(directory, {"event": "capture_environment_identity", "stage": "preflight", **environment})
    append_run_log(directory, {"event": "capture_project_created", "project_name": project_name, "stage": "capture"})
    append_run_log(
        directory,
        {
            "capture_environment_identity": environment,
            "capture_identity": identify_bytes(capture).as_dict(),
            "event": "capture_published",
            "experiment_identity": identify_bytes(experiment).as_dict(),
            "packet_count": packet_count,
            "project_name": project_name,
            "reference_identity": identify_bytes(reference).as_dict(),
            "reused": False,
            "stage": "capture",
        },
    )


def _checkpoint_fitness(files: Mapping[str, bytes], config: ExperimentConfig, metadata: CaptureMetadata) -> float:
    reference, window = normalize_reference(
        read_pcapng_bytes(files["reference.pcapng"], metadata, source=Path("reference.pcapng"))
    )
    checkpoint = parse_checkpoint(
        files["checkpoint.json"],
        make_strategy_context(
            config,
            reference,
            window,
            Path("fixture"),
            experiment_identity=identify_bytes(files["experiment.toml"]),
            reference_identity=identify_bytes(files["reference.pcapng"]),
            capture_identity=identify_bytes(files["capture.json"]),
        ).compatibility,
    )
    return checkpoint.best_fitness


def _selected_training_records(
    training: Sequence[dict[str, object]],
    training_files: Mapping[tuple[str, int], Mapping[str, bytes]],
    *,
    configs: Mapping[WorkloadName, ExperimentConfig],
    metadata: CaptureMetadata,
) -> tuple[dict[str, object], ...]:
    selected: list[dict[str, object]] = []
    for workload in WORKLOADS:
        candidates = [item for item in training if item["workload"] == workload]
        winner = min(
            candidates,
            key=lambda item: (
                -_checkpoint_fitness(
                    training_files[workload, cast(int, item["repeat"])], configs[cast(WorkloadName, workload)], metadata
                ),
                cast(int, item["repeat"]),
            ),
        )
        repeat = cast(int, winner["repeat"])
        files = training_files[workload, repeat]
        selected.append(
            {
                "best_model_identity": identify_bytes(files["best_model.json"]).as_dict(),
                "repeat": repeat,
                "training_directory": winner["directory"],
                "workload": workload,
            }
        )
    return tuple(selected)


def _runtime_seconds(workload: str, repeat: int) -> float:
    return float(WORKLOADS.index(workload) * len(REPEATS) + repeat)


def _write_training_tree(
    root: Path,
    *,
    config: ExperimentConfig,
    metadata: CaptureMetadata,
    events: tuple[TraceEvent, ...],
    workload: str,
    repeat: int,
) -> tuple[dict[str, object], dict[str, bytes], ComparisonResult, TrafficTrace, float]:
    directory_relative = f"training/{workload}/r{repeat}"
    experiment = render_effective_config(config)
    capture = render_capture_metadata(metadata)
    reference = _encode_events(events, metadata)
    with tempfile.TemporaryDirectory(prefix="trafficlab-validation-study-fixture-") as temporary:
        run_directory = Path(temporary) / "run"
        run_directory.mkdir()
        (run_directory / "experiment.toml").write_bytes(experiment)
        (run_directory / "capture.json").write_bytes(capture)
        (run_directory / "reference.pcapng").write_bytes(reference)
        (run_directory / "run.log").write_bytes(b"")
        dependencies = FitDependencies(lambda _path: _prepared(config, run_directory), read_fit_input, run_strategy)
        fit_experiment(Path("fixture-validation-study.toml"), dependencies=dependencies)
        best = (run_directory / "best_model.json").read_bytes()
        model = load_best_model(best, source=run_directory / "best_model.json")
        _, generated = reproduce_generated_pcapng(model, metadata)
        parsed_reference, window = normalize_reference(
            read_pcapng_bytes(reference, metadata, source=Path("reference.pcapng"))
        )
        comparison = compare_traces(
            parsed_reference, align_generated(generated.trace, window), window, config.similarity
        ).with_input_identities(
            {
                "capture_json": identify_bytes(capture),
                "generated_pcapng": identify_bytes(generated.content),
                "reference_pcapng": identify_bytes(reference),
                "similarity_settings": similarity_settings_identity(config.similarity),
            }
        )
        (run_directory / "generated.pcapng").write_bytes(generated.content)
        (run_directory / "similarity.json").write_bytes(render_comparison_result(comparison))
        (run_directory / "run.log").write_bytes(b"")
        checkpoint = cast(dict[str, object], json.loads((run_directory / "checkpoint.json").read_bytes()))
        best = cast(dict[str, object], checkpoint["best"])
        fitness = cast(float, best["fitness"])
        _append_capture_log_lineage(
            run_directory,
            capture=capture,
            reference=reference,
            experiment=experiment,
            packet_count=len(parsed_reference),
            project_name=f"trafficlab-capture-fixture-{workload}-r{repeat}",
        )
        append_run_log(
            run_directory,
            {
                "event": "best_model_published",
                "family": model.family,
                "fitness": fitness,
                "observation_window_seconds": window,
                "reference_sha256": identify_bytes(reference).sha256,
                "stage": "fit",
            },
        )
        append_run_log(
            run_directory,
            {
                "event": "generated_pcapng_published",
                "observation_window_seconds": window,
                "packet_count": len(generated.trace),
                "seed": model.final_seed,
                "stage": "generate",
            },
        )
        append_run_log(
            run_directory,
            {
                "aggregate_score": comparison.aggregate_score,
                "event": "comparison_succeeded",
                "observation_window_seconds": window,
                "reused": False,
                "stage": "compare",
            },
        )
        append_run_log(
            run_directory,
            {
                "aggregate_score": comparison.aggregate_score,
                "event": "run_completed",
                "family": model.family,
                "fitness": fitness,
                "generated_packet_count": len(generated.trace),
                "reference_packet_count": len(parsed_reference),
                "stage": "run",
            },
        )
        append_run_log(
            run_directory,
            {
                "event": "validation_study_training_completed",
                "repeat": repeat,
                "runtime_seconds": _runtime_seconds(workload, repeat),
                "stage": "study",
                "workload": workload,
            },
        )
        files = {name: (run_directory / name).read_bytes() for name in ARTIFACT_NAMES}
    for name, content in files.items():
        destination = root / directory_relative / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    portable_relative = f"configs/training-{workload}-r{repeat}.portable.toml"
    realized_relative = f"configs/training-{workload}-r{repeat}.realized.toml"
    portable = _config_bytes(config, f"../{directory_relative}")
    realized = _config_bytes(config, f"/retained/{directory_relative}")
    (root / portable_relative).parent.mkdir(parents=True, exist_ok=True)
    (root / portable_relative).write_bytes(portable)
    (root / realized_relative).write_bytes(realized)
    record: dict[str, object] = {
        "capture_lineage": _capture_lineage(capture),
        "directory": directory_relative,
        "portable_config": portable_relative,
        "portable_config_identity": identify_bytes(portable).as_dict(),
        "realized_config": realized_relative,
        "realized_config_identity": identify_bytes(realized).as_dict(),
        "reference_identity": identify_bytes(reference).as_dict(),
        "repeat": repeat,
        "run_config_identity": identify_bytes(experiment).as_dict(),
        "workload": workload,
    }
    return (record, files, comparison, parsed_reference, window)


def _write_held_out(
    root: Path,
    *,
    config: ExperimentConfig,
    metadata: CaptureMetadata,
    events: tuple[TraceEvent, ...],
    workload: str,
    training: dict[str, object],
    training_files: Mapping[str, bytes],
) -> tuple[dict[str, object], HeldOutEvaluation]:
    directory_relative = f"held_out/{workload}"
    directory = root / directory_relative
    directory.mkdir(parents=True, exist_ok=True)
    capture = render_capture_metadata(metadata)
    reference = _encode_events(events, metadata)
    portable = _config_bytes(config, ".")
    realized = _config_bytes(config, f"/retained/{directory_relative}")
    evaluation = evaluate_study_held_out(
        model_content=training_files["best_model.json"],
        model_source=Path(cast(str, training["directory"])) / "best_model.json",
        config=config,
        capture_content=capture,
        capture_source=directory / "capture.json",
        reference_content=reference,
        reference_source=directory / "reference.pcapng",
    )
    record = {
        "capture_identity": identify_bytes(capture).as_dict(),
        "capture_lineage": _capture_lineage(capture),
        "comparison_identity": identify_bytes(evaluation.comparison_json).as_dict(),
        "generated_identity": identify_bytes(evaluation.generated_pcapng).as_dict(),
        "observation_window_seconds": evaluation.observation_window_seconds,
        "reference_identity": identify_bytes(reference).as_dict(),
        "seed": 97,
        "training_directory": training["directory"],
        "training_model_identity": identify_bytes(training_files["best_model.json"]).as_dict(),
        "workload": workload,
    }
    contents = {
        "capture.json": capture,
        "reference.pcapng": reference,
        "portable.toml": portable,
        "realized.toml": realized,
        "generated.pcapng": evaluation.generated_pcapng,
        "similarity.json": evaluation.comparison_json,
        "record.json": fixture_canonical_json(record),
    }
    for name, content in contents.items():
        (directory / name).write_bytes(content)
    (directory / "run.log").write_bytes(b"")
    _append_capture_log_lineage(
        directory,
        capture=capture,
        reference=reference,
        experiment=realized,
        packet_count=len(events),
        project_name=f"trafficlab-capture-fixture-held-out-{workload}",
    )
    append_run_log(directory, {"event": "held_out_evaluated", "stage": "compare", "workload": workload})
    return (
        {
            "capture_lineage": _capture_lineage(capture),
            "directory": directory_relative,
            "training_directory": training["directory"],
            "workload": workload,
        },
        evaluation,
    )


def generate_fixture_tree(*, source_commit: str, source_tree: str) -> dict[str, bytes]:
    """Build one complete, credential-free schema-4 candidate through public scientific owners."""
    validate_source_identities(source_commit, source_tree)
    configs: dict[WorkloadName, ExperimentConfig] = {}
    for workload in WORKLOADS:
        configs[workload] = _base_config(workload)
    metadata = _metadata()
    base_events = _base_events(metadata)
    with tempfile.TemporaryDirectory(prefix="trafficlab-validation-study-candidate-") as temporary:
        root = Path(temporary) / _STUDY_ID
        root.mkdir()
        training: list[dict[str, object]] = []
        fresh: list[dict[str, object]] = []
        training_files: dict[tuple[str, int], dict[str, bytes]] = {}
        variant = 1
        for workload in WORKLOADS:
            config = configs[workload]
            for repeat in REPEATS:
                record, files, _comparison, _reference, _window = _write_training_tree(
                    root,
                    config=config,
                    metadata=metadata,
                    events=_variant_events(base_events, variant=variant),
                    workload=workload,
                    repeat=repeat,
                )
                variant += 1
                training.append(record)
                training_files[workload, repeat] = files
                fresh_record = {
                    "comparison_identity": identify_bytes(files["similarity.json"]).as_dict(),
                    "generated_identity": identify_bytes(files["generated.pcapng"]).as_dict(),
                    "path": f"fresh_simulation/{workload}/r{repeat}.json",
                    "reference_identity": identify_bytes(files["reference.pcapng"]).as_dict(),
                    "seed": 97,
                    "training_directory": record["directory"],
                    "training_model_identity": identify_bytes(files["best_model.json"]).as_dict(),
                    "workload": workload,
                    "repeat": repeat,
                }
                path = root / cast(str, fresh_record["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(fixture_canonical_json(fresh_record))
                fresh.append(fresh_record)
        selections = _selected_training_records(training, training_files, configs=configs, metadata=metadata)
        selected_training = {
            cast(str, item["workload"]): next(
                record for record in training if record["directory"] == item["training_directory"]
            )
            for item in selections
        }
        held: list[dict[str, object]] = []
        held_evaluations: dict[WorkloadName, HeldOutEvaluation] = {}
        held_window_scales = {"short": 0.5, "streaming": 1.2, "bursty": 0.8}
        for number, workload in enumerate(WORKLOADS, start=20):
            selected = selected_training[workload]
            record, evaluation = _write_held_out(
                root,
                config=configs[workload],
                metadata=metadata,
                events=_held_out_events(base_events, variant=number, window_scale=held_window_scales[workload]),
                workload=workload,
                training=selected,
                training_files=training_files[workload, cast(int, selected["repeat"])],
            )
            held.append(record)
            held_evaluations[workload] = evaluation
        protocol = {
            "candidate_id": _STUDY_ID,
            "destination_id": _STUDY_ID,
            "final_seed": 97,
            "model_selection": {"rule": "highest_best_fitness_then_lowest_repeat", "selected": list(selections)},
            "prerequisite_path": "examples/validation_study/prerequisites.json",
            "schema_version": 4,
            "selection_seeds": list(configs["short"].genetic.trial_seeds),
            "study_id": _STUDY_ID,
            "training_repetitions": 3,
            "workloads": list(WORKLOADS),
        }
        environment = {
            "capture_image_id": CAPTURE_ID,
            "capture_image_reference": CAPTURE_REFERENCE,
            "capture_tool_version": CAPTURE_TOOL_VERSION,
            "compatibility_decision": {
                "reason": "source, lock, and image-lock identities are compatible",
                "status": "compatible",
            },
            "docker_compose_version": "fixture-compose-2.0",
            "docker_engine_version": "fixture-engine-27.0",
            "host_architecture": "fixture-x86_64",
            "kernel_release": "fixture-kernel-1",
            "python_implementation": "CPython",
            "python_version": sys.version.split()[0],
            "scientific_artifact_schema": 4,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "target_image_id": TARGET_ID,
            "target_image_reference": TARGET_REFERENCE,
            "uv_lock_identity": identify_bytes((REPOSITORY / "uv.lock").read_bytes()).as_dict(),
        }
        (root / "protocol.json").write_bytes(fixture_canonical_json(protocol))
        (root / "environment.json").write_bytes(fixture_canonical_json(environment))
        prerequisite_commands: list[dict[str, object]] = []
        for kind in ("docker_matrix", "internet_smoke"):
            argv = list(prerequisite_command_argv(kind, study_id=_STUDY_ID, url=URL))
            tests = prerequisite_junit_counts(
                b'<testsuite errors="0" failures="0" name="fixture" skipped="0" tests="1"/>\n'
            )
            outputs = {
                "command": fixture_canonical_json({"argv": argv}),
                "junit": b'<testsuite errors="0" failures="0" name="fixture" skipped="0" tests="1"/>\n',
                "status": fixture_canonical_json({"exit_status": 0, "tests": tests}),
                "stderr": f"{kind} fixture stderr\n".encode(),
                "stdout": f"{kind} fixture stdout\n".encode(),
            }
            for field, content in outputs.items():
                suffix = {"command": "command.json", "junit": "junit.xml", "status": "status.json"}.get(field, field)
                path = root / "prerequisites" / f"{kind}.{suffix}"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            prerequisite_commands.append(
                {
                    "argv": argv,
                    "command": {
                        "identity": identify_bytes(outputs["command"]).as_dict(),
                        "path": f"prerequisites/{kind}.command.json",
                    },
                    "exit_status": 0,
                    "junit": {
                        "identity": identify_bytes(outputs["junit"]).as_dict(),
                        "path": f"prerequisites/{kind}.junit.xml",
                    },
                    "kind": kind,
                    "status": {
                        "identity": identify_bytes(outputs["status"]).as_dict(),
                        "path": f"prerequisites/{kind}.status.json",
                    },
                    "stderr": {
                        "identity": identify_bytes(outputs["stderr"]).as_dict(),
                        "path": f"prerequisites/{kind}.stderr",
                    },
                    "stdout": {
                        "identity": identify_bytes(outputs["stdout"]).as_dict(),
                        "path": f"prerequisites/{kind}.stdout",
                    },
                    "tests": tests,
                }
            )
        prerequisite_environment = {
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
        }
        capability_header = write_transfer_evidence(root)
        (root / "prerequisites.json").write_bytes(
            render_retained_prerequisites(
                {
                    "capability": {
                        "canary_sha256": hashlib.sha256(capability_header).hexdigest(),
                        "content_length": 1,
                        "content_range": f"bytes 0-0/{OBJECT_SIZE_BYTES}",
                        "object_size_bytes": OBJECT_SIZE_BYTES,
                        "status": 206,
                    },
                    "commands": prerequisite_commands,
                    "environment": prerequisite_environment,
                    "schema_version": 4,
                    "study_id": _STUDY_ID,
                    "url": URL,
                }
            )
        )
        candidate_training = tuple(
            load_candidate_training(
                root / "training" / workload / f"r{repeat}",
                workload=workload,
                repeat=repeat,
                config=configs[workload],
                runtime_seconds=_runtime_seconds(workload, repeat),
            )
            for workload in WORKLOADS
            for repeat in REPEATS
        )
        natural_variation = tuple(
            candidate_natural_variation([item for item in candidate_training if item.workload == workload])
            for workload in ("short", "streaming", "bursty")
        )
        report_inputs = candidate_report_inputs(
            candidate_training, held_evaluations, natural_variation=natural_variation
        )
        (root / "report_inputs.json").write_bytes(fixture_canonical_json(report_inputs))
        (root / "report.json").write_bytes(
            fixture_canonical_json(
                {
                    "formula": "arithmetic_mean",
                    "report_inputs_identity": identify_bytes((root / "report_inputs.json").read_bytes()).as_dict(),
                    "summary": report_inputs,
                }
            )
        )
        lifecycle = {
            "held_out": [
                {
                    "cleanup_verified": True,
                    "directory": f"held_out/{workload}",
                    "project_name": f"trafficlab-capture-fixture-held-out-{workload}",
                    "run_id": f"held-out-{workload}",
                }
                for workload in WORKLOADS
            ],
            "phase_capture_image": {
                "capture_image_id": CAPTURE_ID,
                "cleanup_verified": True,
                "post_cleanup_inspect_exit_status": 1,
                "tag": f"trafficlab-validation-{_STUDY_ID}:collection-capture",
            },
            "schema_version": 1,
            "study_id": _STUDY_ID,
            "training": [
                {
                    "cleanup_verified": True,
                    "directory": f"training/{workload}/r{repeat}",
                    "project_name": f"trafficlab-capture-fixture-{workload}-r{repeat}",
                    "run_id": run_id,
                }
                for _order, run_id, workload, repeat in PRIMARY_ORDER
            ],
        }
        (root / "lifecycle.json").write_bytes(fixture_canonical_json(lifecycle))
        index: dict[str, object] = {
            "environment": "environment.json",
            "fresh_simulation": fresh,
            "held_out": held,
            "lifecycle": "lifecycle.json",
            "lineage": {},
            "ownership": {},
            "prerequisites": "prerequisites.json",
            "protocol": "protocol.json",
            "report": "report.json",
            "report_inputs": "report_inputs.json",
            "schema_version": 4,
            "training": training,
        }
        (root / "index.json").write_bytes(fixture_canonical_json(index))
        files = files_for_candidate(root, include_manifest=False)
        ownership = {relative: owner_for_path(relative) for relative in files}
        lineage = {relative: lineage_for_path(relative) for relative in files}
        index["ownership"] = ownership
        index["lineage"] = lineage
        (root / "index.json").write_bytes(fixture_canonical_json(index))
        files = files_for_candidate(root, include_manifest=False)
        write_manifest(
            root,
            ownership={relative: owner_for_path(relative) for relative in files},
            lineage={relative: lineage_for_path(relative) for relative in files},
        )
        return {
            path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()
        }


def compare_fixture_tree(expected: Mapping[str, bytes]) -> int:
    actual = (
        {
            path.relative_to(FIXTURE).as_posix(): path.read_bytes()
            for path in FIXTURE.rglob("*")
            if path.is_file() and (not path.is_symlink())
        }
        if FIXTURE.exists()
        else {}
    )
    mismatches = sorted({*actual, *expected})
    failed = [name for name in mismatches if actual.get(name) != expected.get(name)]
    for name in failed:
        print(f"mismatched validation-study fixture path: {name}", file=sys.stderr)
    return int(bool(failed))


def write_fixture_tree(expected: Mapping[str, bytes]) -> None:
    if FIXTURE.exists():
        shutil.rmtree(FIXTURE)
    for relative, content in expected.items():
        path = FIXTURE / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _recorded_source_identities() -> tuple[str, str]:
    environment = json.loads((FIXTURE / "environment.json").read_text(encoding="utf-8"))
    return (cast(str, environment["source_commit"]), cast(str, environment["source_tree"]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--source-commit")
    parser.add_argument("--source-tree")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.source_commit is None and arguments.source_tree is None:
        if not arguments.check:
            raise TrafficlabError(
                "fixture generation requires explicit source commit and tree identities",
                corrective_action="commit the implementation, then supply its exact commit and tree identities",
            )
        source_commit, source_tree = _recorded_source_identities()
    else:
        if arguments.source_commit is None or arguments.source_tree is None:
            raise TrafficlabError(
                "fixture generation requires explicit source commit and tree identities",
                corrective_action="commit the implementation, then supply its exact commit and tree identities",
            )
        source_commit, source_tree = (arguments.source_commit, arguments.source_tree)
    expected = generate_fixture_tree(source_commit=source_commit, source_tree=source_tree)
    if arguments.check:
        status = compare_fixture_tree(expected)
        if status == 0:
            print("validation-study fixture: checked-in paths and bytes match deterministic production output")
        return status
    write_fixture_tree(expected)
    print(f"validation-study fixture: wrote {len(expected)} deterministic retained files")
    return 0
