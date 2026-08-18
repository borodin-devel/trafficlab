#!/usr/bin/env python3
"""Generate or verify the deterministic complete offline Validation Study fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import audit_validation_study as auditor
from scripts import run_validation_study as study
from trafficlab.artifacts import append_run_log
from trafficlab.comparison import compare_traces, render_comparison_result, similarity_settings_identity
from trafficlab.compatibility import identify_bytes
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import load_configuration_pair, render_effective_config
from trafficlab.errors import TrafficlabError
from trafficlab.fitting import FitDependencies, fit_experiment, read_fit_input
from trafficlab.generation import reproduce_generated_pcapng
from trafficlab.genetic.strategy import run_strategy
from trafficlab.pcapng import encode_pcapng, parse_pcapng_bytes
from trafficlab.preflight import PreflightReport, PreparedExperiment
from trafficlab.trace import CaptureMetadata, TraceEvent, align_generated, normalize_reference, parse_capture_metadata

REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURE = REPOSITORY / "fixtures" / "tests" / "validation_study" / "candidate"
FIT_FIXTURE = REPOSITORY / "fixtures" / "examples" / "pipeline" / "fit"
WORKLOADS: tuple[study.WorkloadName, ...] = ("short", "streaming", "bursty")
REPEATS = (1, 2, 3)
_IMAGE_LOCK = cast(dict[str, object], json.loads((REPOSITORY / "docker" / "capture" / "image-lock.json").read_text()))
CAPTURE_ID = cast(str, _IMAGE_LOCK["expected_capture_image_id"])
CAPTURE_REFERENCE = f"trafficlab-capture@{CAPTURE_ID}"
CAPTURE_TOOL_VERSION = cast(str, _IMAGE_LOCK["capture_tool_version"])
TARGET_ID = f"sha256:{study.TARGET_REFERENCE.rsplit(':', 1)[-1]}"
_HEX40 = re.compile(r"[0-9a-f]{40}", flags=re.ASCII)
_STUDY_ID = "fixture-study"
_URL = "https://downloads.example.test/object.bin"
_OBJECT_SIZE_BYTES = 4 * 1024 * 1024


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        + b"\n"
    )


def _base_config(workload: study.WorkloadName) -> ExperimentConfig:
    config = load_configuration_pair(FIT_FIXTURE / "experiment.toml").portable
    profile = next(spec for spec in study.workload_specs(_URL) if spec.name == workload)
    return config.model_copy(
        update={
            "target": config.target.model_copy(update={"argv": profile.argv, "image": study.TARGET_REFERENCE}),
            "capture": config.capture.model_copy(update={"image": CAPTURE_REFERENCE}),
        }
    )


def _metadata() -> CaptureMetadata:
    return parse_capture_metadata((FIT_FIXTURE / "capture.json").read_bytes(), source=FIT_FIXTURE / "capture.json")


def _base_events(metadata: CaptureMetadata) -> tuple[TraceEvent, ...]:
    return parse_pcapng_bytes(
        (FIT_FIXTURE / "reference.pcapng").read_bytes(), metadata, source=FIT_FIXTURE / "reference.pcapng"
    )


def _variant_events(events: Sequence[TraceEvent], *, variant: int) -> tuple[TraceEvent, ...]:
    result: list[TraceEvent] = []
    last = len(events) - 1
    for index, event in enumerate(events):
        timestamp = event.timestamp
        if 0 < index < last:
            timestamp += 0.001 * variant * ((index % 5) - 2)
        frame_length = event.frame_length + variant * ((index % 3) + 1)
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
        "target_image_reference": study.TARGET_REFERENCE,
    }


def _capture_log_environment() -> dict[str, object]:
    return {
        "capture_content_id": CAPTURE_ID,
        "capture_reference": CAPTURE_REFERENCE,
        "capture_tool_version": CAPTURE_TOOL_VERSION,
        "host_architecture": "linux/amd64",
        "target_content_id": TARGET_ID,
        "target_reference": study.TARGET_REFERENCE,
    }


def _append_capture_log_lineage(
    directory: Path,
    *,
    capture: bytes,
    reference: bytes,
    experiment: bytes,
    packet_count: int,
    project_name: str,
) -> None:
    environment = _capture_log_environment()
    append_run_log(directory, {"event": "capture_environment_identity", "stage": "preflight", **environment})
    append_run_log(
        directory,
        {"event": "capture_project_created", "project_name": project_name, "stage": "capture"},
    )
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
        parse_pcapng_bytes(files["reference.pcapng"], metadata, source=Path("reference.pcapng"))
    )
    checkpoint = study.parse_checkpoint(  # pyright: ignore[reportPrivateUsage]
        files["checkpoint.json"],
        study.make_strategy_context(  # pyright: ignore[reportPrivateUsage]
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
    configs: Mapping[study.WorkloadName, ExperimentConfig],
    metadata: CaptureMetadata,
) -> tuple[dict[str, object], ...]:
    selected: list[dict[str, object]] = []
    for workload in WORKLOADS:
        candidates = [item for item in training if item["workload"] == workload]
        winner = min(
            candidates,
            key=lambda item: (
                -_checkpoint_fitness(
                    training_files[(workload, cast(int, item["repeat"]))],
                    configs[cast(study.WorkloadName, workload)],
                    metadata,
                ),
                cast(int, item["repeat"]),
            ),
        )
        repeat = cast(int, winner["repeat"])
        files = training_files[(workload, repeat)]
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


def _transfer_header(start: int, end: int) -> bytes:
    return (
        b"HTTP/1.1 206 Partial Content\r\n"
        + f"Content-Length: {end - start + 1}\r\n".encode("ascii")
        + f"Content-Range: bytes {start}-{end}/{_OBJECT_SIZE_BYTES}\r\n\r\n".encode("ascii")
    )


def _write_transfer_evidence(root: Path) -> bytes:
    bindings: list[tuple[str, str, str, int, int, int, str]] = [
        ("prerequisites", "00-prerequisites", "prerequisites", 0, 0, 0, "capability.headers")
    ]
    transfers = {spec.name: spec.transfers for spec in study.workload_specs(_URL)}
    for _order, run_id, workload, _repeat in study.PRIMARY_ORDER:
        bindings.extend(
            ("training", run_id, workload, index, start, end, filename)
            for index, (start, end, filename) in enumerate(transfers[workload])
        )
    for workload in WORKLOADS:
        bindings.extend(
            ("held_out", f"held-out-{workload}", workload, index, start, end, filename)
            for index, (start, end, filename) in enumerate(transfers[workload])
        )
    capability_header = b""
    for scope, run_id, workload, transfer_index, start, end, filename in bindings:
        header = _transfer_header(start, end)
        if scope == "prerequisites":
            capability_header = header
        header_relative = f"headers/{scope}/{run_id}/{filename}"
        observation_relative = f"observations/{scope}/{run_id}/{filename}.json"
        header_path = root / header_relative
        header_path.parent.mkdir(parents=True, exist_ok=True)
        header_path.write_bytes(header)
        observation_path = root / observation_relative
        observation_path.parent.mkdir(parents=True, exist_ok=True)
        observation_path.write_bytes(
            _canonical(
                {
                    "content_length": end - start + 1,
                    "content_range": f"bytes {start}-{end}/{_OBJECT_SIZE_BYTES}",
                    "header_identity": identify_bytes(header).as_dict(),
                    "requested_end": end,
                    "requested_start": start,
                    "run_id": run_id,
                    "scope": scope,
                    "status": 206,
                    "transfer_index": transfer_index,
                    "workload": workload,
                }
            )
        )
    if not capability_header:
        raise ValueError("fixture transfer evidence must retain the prerequisite capability header")
    return capability_header


def _write_training_tree(
    root: Path,
    *,
    config: ExperimentConfig,
    metadata: CaptureMetadata,
    events: tuple[TraceEvent, ...],
    workload: str,
    repeat: int,
) -> tuple[dict[str, object], dict[str, bytes], study.ComparisonResult, tuple[TraceEvent, ...], float]:
    directory_relative = f"training/{workload}/r{repeat}"
    experiment = render_effective_config(config)
    capture = (
        json.dumps(
            {"interface": metadata.interface, "target_mac": metadata.target_mac},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    reference = encode_pcapng(events, metadata)
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
        model = study.load_best_model(best, source=run_directory / "best_model.json")
        _, generated, generated_bytes = reproduce_generated_pcapng(model, metadata)
        parsed_reference, window = normalize_reference(
            parse_pcapng_bytes(reference, metadata, source=Path("reference.pcapng"))
        )
        comparison = compare_traces(
            parsed_reference, align_generated(generated, window), window, config.similarity
        ).with_input_identities(
            {
                "capture_json": identify_bytes(capture),
                "generated_pcapng": identify_bytes(generated_bytes),
                "reference_pcapng": identify_bytes(reference),
                "similarity_settings": similarity_settings_identity(config.similarity),
            }
        )
        (run_directory / "generated.pcapng").write_bytes(generated_bytes)
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
                "packet_count": len(generated),
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
                "generated_packet_count": len(generated),
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
        files = {name: (run_directory / name).read_bytes() for name in study.ARTIFACT_NAMES}
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
    return record, files, comparison, parsed_reference, window


def _write_held_out(
    root: Path,
    *,
    config: ExperimentConfig,
    metadata: CaptureMetadata,
    events: tuple[TraceEvent, ...],
    workload: str,
    training: dict[str, object],
    training_files: Mapping[str, bytes],
) -> tuple[dict[str, object], study.HeldOutEvaluation]:
    directory_relative = f"held_out/{workload}"
    directory = root / directory_relative
    directory.mkdir(parents=True, exist_ok=True)
    capture = (
        json.dumps(
            {"interface": metadata.interface, "target_mac": metadata.target_mac},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    reference = encode_pcapng(events, metadata)
    portable = _config_bytes(config, ".")
    realized = _config_bytes(config, f"/retained/{directory_relative}")
    evaluation = study.evaluate_study_held_out(
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
        "record.json": _canonical(record),
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
    return {
        "capture_lineage": _capture_lineage(capture),
        "directory": directory_relative,
        "training_directory": training["directory"],
        "workload": workload,
    }, evaluation


def validate_source_identities(source_commit: str, source_tree: str) -> None:
    """Require the nonzero lowercase Git commit and tree identities retained by a fixture."""
    if (
        _HEX40.fullmatch(source_commit) is None
        or _HEX40.fullmatch(source_tree) is None
        or set(source_commit) == {"0"}
        or set(source_tree) == {"0"}
    ):
        raise ValueError("source identities must be nonzero commit/tree hexadecimal values")


def generate_fixture_tree(*, source_commit: str, source_tree: str) -> dict[str, bytes]:
    """Build one complete, credential-free schema-3 candidate through public scientific owners."""
    validate_source_identities(source_commit, source_tree)
    configs: dict[study.WorkloadName, ExperimentConfig] = {}
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
                training_files[(workload, repeat)] = files
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
                path.write_bytes(_canonical(fresh_record))
                fresh.append(fresh_record)
        selections = _selected_training_records(training, training_files, configs=configs, metadata=metadata)
        selected_training = {
            cast(str, item["workload"]): next(
                record for record in training if record["directory"] == item["training_directory"]
            )
            for item in selections
        }
        held: list[dict[str, object]] = []
        held_evaluations: dict[study.WorkloadName, study.HeldOutEvaluation] = {}
        held_window_scales = {"short": 0.5, "streaming": 1.2, "bursty": 0.8}
        for number, workload in enumerate(WORKLOADS, start=20):
            selected = selected_training[workload]
            record, evaluation = _write_held_out(
                root,
                config=configs[workload],
                metadata=metadata,
                events=_held_out_events(
                    base_events,
                    variant=number,
                    window_scale=held_window_scales[workload],
                ),
                workload=workload,
                training=selected,
                training_files=training_files[(workload, cast(int, selected["repeat"]))],
            )
            held.append(record)
            held_evaluations[workload] = evaluation
        protocol = {
            "candidate_id": _STUDY_ID,
            "destination_id": _STUDY_ID,
            "final_seed": 97,
            "model_selection": {
                "rule": "highest_best_fitness_then_lowest_repeat",
                "selected": list(selections),
            },
            "prerequisite_path": "examples/validation_study/prerequisites.json",
            "schema_version": 3,
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
            "scientific_artifact_schema": 2,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "target_image_id": TARGET_ID,
            "target_image_reference": study.TARGET_REFERENCE,
            "uv_lock_identity": identify_bytes((REPOSITORY / "uv.lock").read_bytes()).as_dict(),
        }
        (root / "protocol.json").write_bytes(_canonical(protocol))
        (root / "environment.json").write_bytes(_canonical(environment))
        prerequisite_commands: list[dict[str, object]] = []
        for kind in ("docker_matrix", "internet_smoke"):
            argv = list(study.prerequisite_command_argv(kind, study_id=_STUDY_ID, url=_URL))
            tests = study.prerequisite_junit_counts(
                b'<testsuite errors="0" failures="0" name="fixture" skipped="0" tests="1"/>\n'
            )
            outputs = {
                "command": _canonical({"argv": argv}),
                "junit": b'<testsuite errors="0" failures="0" name="fixture" skipped="0" tests="1"/>\n',
                "status": _canonical({"exit_status": 0, "tests": tests}),
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
        capability_header = _write_transfer_evidence(root)
        (root / "prerequisites.json").write_bytes(
            study.render_retained_prerequisites(
                {
                    "capability": {
                        "canary_sha256": hashlib.sha256(capability_header).hexdigest(),
                        "content_length": 1,
                        "content_range": f"bytes 0-0/{_OBJECT_SIZE_BYTES}",
                        "object_size_bytes": _OBJECT_SIZE_BYTES,
                        "status": 206,
                    },
                    "commands": prerequisite_commands,
                    "environment": prerequisite_environment,
                    "schema_version": 3,
                    "study_id": _STUDY_ID,
                    "url": _URL,
                }
            )
        )
        candidate_training = tuple(
            study._load_candidate_training(  # pyright: ignore[reportPrivateUsage]
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
            study._candidate_natural_variation(  # pyright: ignore[reportPrivateUsage]
                [item for item in candidate_training if item.workload == workload]
            )
            for workload in ("short", "streaming", "bursty")
        )
        report_inputs = study._candidate_report_inputs(  # pyright: ignore[reportPrivateUsage]
            candidate_training,
            held_evaluations,
            natural_variation=natural_variation,
        )
        (root / "report_inputs.json").write_bytes(_canonical(report_inputs))
        (root / "report.json").write_bytes(
            _canonical(
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
                for _order, run_id, workload, repeat in study.PRIMARY_ORDER
            ],
        }
        (root / "lifecycle.json").write_bytes(_canonical(lifecycle))
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
            "schema_version": 3,
            "training": training,
        }
        (root / "index.json").write_bytes(_canonical(index))
        files = auditor.files_for_candidate(root, include_manifest=False)
        ownership = {relative: auditor.owner_for_path(relative) for relative in files}
        lineage = {relative: auditor.lineage_for_path(relative) for relative in files}
        index["ownership"] = ownership
        index["lineage"] = lineage
        (root / "index.json").write_bytes(_canonical(index))
        files = auditor.files_for_candidate(root, include_manifest=False)
        auditor.write_manifest(
            root,
            ownership={relative: auditor.owner_for_path(relative) for relative in files},
            lineage={relative: auditor.lineage_for_path(relative) for relative in files},
        )
        return {
            path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()
        }


def compare_fixture_tree(expected: Mapping[str, bytes]) -> int:
    actual = (
        {
            path.relative_to(FIXTURE).as_posix(): path.read_bytes()
            for path in FIXTURE.rglob("*")
            if path.is_file() and not path.is_symlink()
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
    return cast(str, environment["source_commit"]), cast(str, environment["source_tree"])


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
        source_commit, source_tree = arguments.source_commit, arguments.source_tree
    expected = generate_fixture_tree(source_commit=source_commit, source_tree=source_tree)
    if arguments.check:
        status = compare_fixture_tree(expected)
        if status == 0:
            print("validation-study fixture: checked-in paths and bytes match deterministic production output")
        return status
    write_fixture_tree(expected)
    print(f"validation-study fixture: wrote {len(expected)} deterministic retained files")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TrafficlabError as error:
        print(f"validation-study fixture: {error}; {error.corrective_action}", file=sys.stderr)
        raise SystemExit(error.exit_code) from None
