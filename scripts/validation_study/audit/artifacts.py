"""Artifacts owner for Validation Study tooling."""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

from scripts.validation_study.audit.common import (
    INDEX,
    MANIFEST,
    SCHEMA,
    TRANSFER_BINDINGS,
    WORKLOADS,
    artifact_identity,
    canonical_json_bytes,
    exact,
    fail,
    files_for_candidate,
    frozen_workload_profiles,
    integer,
    manifest_entries,
    parse_json_object,
    path_key,
    read_regular,
    relative_path,
    repeat_number,
    require_capture_log_lineage,
    require_directory,
    require_log_fields,
    require_ordered_log_events,
    require_terminal_log_events,
    required_log_record,
    scoped_transfer_path,
    string,
    workload_name,
)
from scripts.validation_study.audit.environment import config_semantics, git_bytes
from scripts.validation_study.common import ARTIFACT_NAMES, MODEL_FAMILIES
from scripts.validation_study.records import HeldOutEvaluation
from scripts.validation_study.transfer import parse_transfer_header
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_configuration_pair, render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import encode_pcapng, read_pcapng_bytes
from trafficlab.common.trace import align_generated, normalize_reference, parse_capture_metadata
from trafficlab.comparison.codec import render_comparison_result, similarity_settings_identity
from trafficlab.comparison.metrics import compare_final_traces
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.checkpoint import CheckpointState
from trafficlab.generation.models.fitted_model import (
    BestModel,
    runtime_fitted_model,
)
from trafficlab.generation.models.registry import get_family

if TYPE_CHECKING:
    from scripts.validation_study.audit.common import Entry, Training


def write_manifest(candidate: Path, ownership: Mapping[str, str], lineage: Mapping[str, object]) -> Path:
    """Write the canonical schema-2 manifest for a completed local candidate tree."""
    root = require_directory(candidate, name="candidate")
    files = files_for_candidate(root, include_manifest=False)
    if set(ownership) != set(files) or set(lineage) != set(files):
        raise ValueError("ownership and lineage keys must equal the regular-file inventory")
    entries: list[dict[str, object]] = []
    for relative in sorted(files, key=path_key):
        owner = ownership[relative]
        if type(owner) is not str or not owner.strip():
            raise ValueError(f"manifest owner for {relative} must be a nonempty string")
        content = read_regular(files[relative], affected=relative)
        entries.append(
            {
                "lineage": lineage[relative],
                "owner": owner,
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    manifest = root / MANIFEST
    manifest.write_bytes(canonical_json_bytes({"files": entries, "schema_version": SCHEMA}))
    return manifest


def verify_inventory(root: Path, manifest: bytes) -> tuple[Entry, ...]:
    entries = manifest_entries(manifest)
    actual = files_for_candidate(root, include_manifest=False)
    expected = {entry.path: entry for entry in entries}
    for relative in sorted(expected, key=path_key):
        if relative not in actual:
            fail(
                "artifact_missing",
                relative,
                f"{relative} is missing from the retained bundle",
                "restore the exact retained artifact",
            )
    for relative in sorted(set(actual) - set(expected), key=path_key):
        fail(
            "artifact_foreign",
            relative,
            f"{relative} is not listed by the manifest",
            "remove the unlisted artifact and rebuild the manifest",
        )
    for relative in sorted(expected, key=path_key):
        content = read_regular(actual[relative], affected=relative)
        entry = expected[relative]
        if len(content) != entry.size or hashlib.sha256(content).hexdigest() != entry.sha256:
            fail(
                "artifact_corrupt",
                relative,
                f"{relative} does not match its manifest identity",
                "restore the exact retained artifact",
            )
    return entries


def owner_for_path(relative: str) -> str:
    parts = PurePosixPath(relative).parts
    if relative == INDEX:
        return "study-index"
    if relative == "protocol.json":
        return "study-protocol"
    if relative == "environment.json":
        return "study-environment"
    if relative == "prerequisites.json":
        return "study-prerequisites"
    if relative == "report_inputs.json":
        return "study-report-inputs"
    if relative == "report.json":
        return "study-report"
    if relative == "lifecycle.json":
        return "study-lifecycle"
    if len(parts) == 2 and parts[0] == "prerequisites":
        kind, _, suffix = parts[1].partition(".")
        if kind in ("docker_matrix", "internet_smoke") and suffix in (
            "command.json",
            "stdout",
            "stderr",
            "status.json",
            "junit.xml",
        ):
            return f"prerequisite:{kind}:{suffix}"
    transfer_path = scoped_transfer_path(relative)
    if transfer_path is not None:
        kind, binding = transfer_path
        owner = "transfer-header" if kind == "headers" else "external-observation"
        return f"{owner}:{binding.scope}:{binding.run_id}:{binding.transfer_index}"
    if len(parts) == 2 and parts[0] == "configs" and parts[1].endswith(".toml"):
        return f"configuration:{parts[1].removesuffix('.toml')}"
    if (
        len(parts) == 4
        and parts[0] == "training"
        and (parts[1] in WORKLOADS)
        and (parts[2] in ("r1", "r2", "r3"))
        and (parts[3] in ARTIFACT_NAMES)
    ):
        return f"training:{parts[1]}:{parts[2]}"
    if (
        len(parts) == 3
        and parts[0] == "fresh_simulation"
        and (parts[1] in WORKLOADS)
        and (parts[2] in ("r1.json", "r2.json", "r3.json"))
    ):
        return f"fresh-simulation:{parts[1]}:{parts[2].removesuffix('.json')}"
    if (
        len(parts) == 3
        and parts[0] == "held_out"
        and (parts[1] in WORKLOADS)
        and (
            parts[2]
            in {
                "capture.json",
                "reference.pcapng",
                "portable.toml",
                "realized.toml",
                "generated.pcapng",
                "similarity.json",
                "record.json",
                "run.log",
            }
        )
    ):
        return f"held-out:{parts[1]}"
    fail("artifact_foreign", relative, f"{relative} has no documented owner", "rebuild the candidate inventory")


def lineage_for_path(relative: str) -> dict[str, object]:
    parts = PurePosixPath(relative).parts
    if relative == INDEX:
        return {"relation": "study-index"}
    if relative in {
        "protocol.json",
        "environment.json",
        "prerequisites.json",
        "report_inputs.json",
        "report.json",
        "lifecycle.json",
    }:
        return {"relation": relative.removesuffix(".json")}
    if len(parts) == 2 and parts[0] == "prerequisites":
        return {"relation": "prerequisite", "record": parts[1]}
    transfer_path = scoped_transfer_path(relative)
    if transfer_path is not None:
        kind, binding = transfer_path
        return {
            "filename": binding.filename,
            "relation": "transfer-header" if kind == "headers" else "external-observation",
            "requested_end": binding.requested_end,
            "requested_start": binding.requested_start,
            "run_id": binding.run_id,
            "scope": binding.scope,
            "transfer_index": binding.transfer_index,
            "workload": binding.workload,
        }
    if len(parts) == 2 and parts[0] == "configs":
        return {"relation": "configuration", "name": parts[1].removesuffix(".toml")}
    if len(parts) == 4 and parts[0] == "training":
        return {"relation": parts[3], "repeat": int(parts[2][1:]), "workload": parts[1]}
    if len(parts) == 3 and parts[0] == "fresh_simulation":
        return {"relation": "fresh_simulation", "repeat": int(parts[2][1]), "workload": parts[1]}
    if len(parts) == 3 and parts[0] == "held_out":
        return {"relation": parts[2], "workload": parts[1]}
    fail("artifact_foreign", relative, f"{relative} has no documented lineage", "rebuild the candidate inventory")


def metadata(index: dict[str, object], entries: tuple[Entry, ...]) -> None:
    ownership = index["ownership"]
    lineage = index["lineage"]
    if type(ownership) is not dict or type(lineage) is not dict:
        fail(
            "artifact_corrupt", INDEX, "ownership and lineage must be JSON objects", "restore canonical evidence index"
        )
    expected_owners = {entry.path: entry.owner for entry in entries}
    expected_lineage = {entry.path: entry.lineage for entry in entries}
    if ownership != expected_owners or lineage != expected_lineage:
        fail(
            "artifact_foreign",
            INDEX,
            "index ownership or lineage does not match the manifest",
            "restore matching manifest and index",
        )
    for relative in sorted(expected_owners, key=path_key):
        if expected_owners[relative] != owner_for_path(relative) or expected_lineage[relative] != lineage_for_path(
            relative
        ):
            fail(
                "artifact_foreign",
                relative,
                f"{relative} has invalid owner or lineage",
                "restore documented ownership and lineage",
            )


def require_training_log_lineage(
    records: Sequence[Mapping[str, object]],
    *,
    name: str,
    environment: Mapping[str, object],
    contents: Mapping[str, bytes],
    reference_count: int,
    generated_count: int,
    checkpoint: CheckpointState,
    best: BestModel,
    comparison: ComparisonResult,
    window: float,
) -> None:
    require_capture_log_lineage(
        records,
        name=name,
        environment=environment,
        capture=contents["capture.json"],
        reference=contents["reference.pcapng"],
        experiment=contents["experiment.toml"],
        packet_count=reference_count,
    )
    require_log_fields(
        required_log_record(records, event="best_model_published", name=name),
        {
            "event": "best_model_published",
            "stage": "fit",
            "family": best.family,
            "observation_window_seconds": window,
            "reference_sha256": artifact_identity(contents["reference.pcapng"])["sha256"],
        },
        name=name,
        event="best_model_published",
    )
    require_log_fields(
        required_log_record(records, event="generated_pcapng_published", name=name),
        {
            "event": "generated_pcapng_published",
            "stage": "generate",
            "seed": best.final_seed,
            "observation_window_seconds": window,
            "packet_count": generated_count,
        },
        name=name,
        event="generated_pcapng_published",
    )
    require_log_fields(
        required_log_record(records, event="comparison_succeeded", name=name),
        {
            "event": "comparison_succeeded",
            "stage": "compare",
            "observation_window_seconds": window,
            "aggregate_score": comparison.aggregate_score,
            "reused": False,
        },
        name=name,
        event="comparison_succeeded",
    )
    require_log_fields(
        required_log_record(records, event="run_completed", name=name),
        {
            "event": "run_completed",
            "stage": "run",
            "family": best.family,
            "fitness": checkpoint.best_fitness,
            "reference_packet_count": reference_count,
            "generated_packet_count": generated_count,
            "aggregate_score": comparison.aggregate_score,
        },
        name=name,
        event="run_completed",
    )
    require_terminal_log_events(records, events=("run_completed", "validation_study_training_completed"), name=name)
    require_ordered_log_events(
        records,
        events=(
            "capture_environment_identity",
            "capture_published",
            "best_model_published",
            "generated_pcapng_published",
            "comparison_succeeded",
            "run_completed",
            "validation_study_training_completed",
        ),
        name=name,
    )


def require_held_out_log_lineage(
    records: Sequence[Mapping[str, object]],
    *,
    name: str,
    workload: str,
    environment: Mapping[str, object],
    capture: bytes,
    reference: bytes,
    experiment: bytes,
) -> None:
    require_capture_log_lineage(
        records,
        name=name,
        environment=environment,
        capture=capture,
        reference=reference,
        experiment=experiment,
        packet_count=None,
    )
    require_log_fields(
        required_log_record(records, event="held_out_evaluated", name=name),
        {"event": "held_out_evaluated", "stage": "compare", "workload": workload},
        name=name,
        event="held_out_evaluated",
    )
    require_terminal_log_events(records, events=("held_out_evaluated",), name=name)
    require_ordered_log_events(
        records, events=("capture_environment_identity", "capture_published", "held_out_evaluated"), name=name
    )


def config_pair(
    bundle: Path, portable: str, realized: str, *, directory: Path, name: str
) -> tuple[ExperimentConfig, set[str]]:
    portable_path = bundle / portable
    portable_content = read_regular(portable_path, affected=portable)
    try:
        pair = load_configuration_pair(portable_path)
    except TrafficlabError as error:
        fail(
            "artifact_corrupt",
            portable,
            f"portable configuration is invalid: {error}",
            "restore canonical portable configuration",
        )
    if render_effective_config(pair.portable) != portable_content or pair.realized.run.directory != directory:
        fail(
            "artifact_foreign",
            portable,
            "portable configuration does not realize to its retained directory",
            "restore matching configuration pair",
        )
    realized_content = read_regular(bundle / realized, affected=realized)
    try:
        realized_document = tomllib.loads(realized_content.decode("utf-8"))
        realized_config = ExperimentConfig.model_validate(realized_document)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as error:
        fail(
            "artifact_corrupt",
            realized,
            f"realized configuration is invalid: {error}",
            "restore canonical realized configuration",
        )
    if render_effective_config(realized_config) != realized_content or config_semantics(
        realized_config
    ) != config_semantics(pair.realized):
        fail(
            "artifact_foreign",
            realized,
            "realized configuration does not match its portable configuration",
            "restore matching configuration pair",
        )
    return (pair.realized, {portable, realized})


def fixture_profile(
    repository: Path, *, source_commit: str, workload: str, url: str, environment: Mapping[str, object]
) -> ExperimentConfig:
    """Derive the deterministic fixture profile from its recorded source tree."""
    relative = "examples/data/fit/experiment.toml"
    path = repository / relative
    content = read_regular(path, affected=relative)
    committed = git_bytes(repository, ("show", f"{source_commit}:{relative}"), name="recorded fixture profile")
    if content != committed:
        fail(
            "artifact_foreign",
            relative,
            "fixture profile bytes do not match the recorded source revision",
            "audit the exact recorded fixture source",
        )
    try:
        pair = load_configuration_pair(path)
    except TrafficlabError as error:
        fail(
            "artifact_corrupt",
            relative,
            f"recorded fixture profile is invalid: {error}",
            "restore the checked fixture profile",
        )
    if render_effective_config(pair.portable) != content:
        fail(
            "artifact_foreign",
            relative,
            "recorded fixture profile is not canonical",
            "restore the canonical fixture profile",
        )
    document = pair.portable.model_dump(mode="python")
    models_document = cast(dict[str, object], document["models"])
    models_document["enabled"] = MODEL_FAMILIES
    for family in tuple(models_document):
        if family not in {"enabled", *MODEL_FAMILIES}:
            del models_document[family]
    profile = ExperimentConfig.model_validate(document)
    spec = frozen_workload_profiles(url)[workload]
    target = profile.target.model_copy(update={"argv": spec.argv, "image": environment["target_image_reference"]})
    capture = profile.capture.model_copy(update={"image": environment["capture_image_reference"]})
    return profile.model_copy(update={"target": target, "capture": capture})


def rebuild_fresh(bundle: Path, value: object, training: Training, *, final_seed: int) -> str:
    document = exact(
        value,
        (
            "comparison_identity",
            "generated_identity",
            "path",
            "reference_identity",
            "seed",
            "training_directory",
            "training_model_identity",
            "workload",
            "repeat",
        ),
        name="fresh simulation record",
    )
    workload = workload_name(document["workload"], name="fresh simulation workload")
    repeat = repeat_number(document["repeat"], name="fresh simulation repeat")
    expected_path = f"fresh_simulation/{workload}/r{repeat}.json"
    path = relative_path(document["path"], name="fresh simulation path")
    if (workload, repeat, path, document["training_directory"], document["seed"]) != (
        training.workload,
        training.repeat,
        expected_path,
        f"training/{training.workload}/r{training.repeat}",
        final_seed,
    ):
        fail(
            "artifact_foreign",
            path,
            "fresh simulation record does not bind its training run",
            "restore matching fresh simulation evidence",
        )
    stored = parse_json_object(read_regular(bundle / path, affected=path), name=path)
    if stored != document:
        fail(
            "artifact_foreign",
            path,
            "fresh simulation record differs from index",
            "restore matching fresh simulation record",
        )
    expected = {
        "comparison_identity": artifact_identity(training.contents["similarity.json"]),
        "generated_identity": artifact_identity(training.contents["generated.pcapng"]),
        "reference_identity": artifact_identity(training.contents["reference.pcapng"]),
        "training_model_identity": artifact_identity(training.contents["best_model.json"]),
    }
    if any((document[name] != item for name, item in expected.items())):
        fail(
            "artifact_foreign",
            path,
            "fresh simulation identities do not match the training run",
            "restore matching fresh simulation evidence",
        )
    return path


def selected_training(protocol: Mapping[str, object], training: Sequence[Training]) -> dict[str, Training]:
    selection = exact(protocol["model_selection"], ("rule", "selected"), name="protocol model selection")
    if selection["rule"] != "highest_best_fitness_then_lowest_repeat":
        fail(
            "scientific_semantics_incompatible",
            "protocol",
            "protocol must freeze the training-only highest-best-fitness selection rule",
            "restore the frozen training-only model-selection protocol",
        )
    values = selection["selected"]
    if type(values) is not list or len(cast(list[object], values)) != len(WORKLOADS):
        fail(
            "artifact_corrupt",
            "protocol",
            "protocol must retain one selected training model for each workload",
            "restore complete model-selection evidence",
        )
    selected: dict[str, Training] = {}
    for value in cast(list[object], values):
        record = exact(
            value,
            ("best_model_identity", "repeat", "training_directory", "workload"),
            name="protocol selected training model",
        )
        workload = workload_name(record["workload"], name="protocol selected workload")
        if workload in selected:
            fail(
                "artifact_foreign",
                "protocol",
                "protocol selected training models must be unique by workload",
                "restore complete model-selection evidence",
            )
        group = tuple(item for item in training if item.workload == workload)
        winner = min(group, key=lambda item: (-item.checkpoint.best_fitness, item.repeat))
        expected = {
            "best_model_identity": artifact_identity(winner.contents["best_model.json"]),
            "repeat": winner.repeat,
            "training_directory": f"training/{winner.workload}/r{winner.repeat}",
            "workload": winner.workload,
        }
        if record != expected:
            fail(
                "artifact_foreign",
                "protocol",
                "protocol selected model does not match the reconstructed training-only rule",
                "restore matching model-selection evidence",
            )
        selected[workload] = winner
    if tuple(selected) != WORKLOADS:
        fail(
            "artifact_foreign",
            "protocol",
            "protocol selected model records must use workload order",
            "restore ordered model-selection evidence",
        )
    return selected


def reconstruct_held_out_trace(
    training: Training,
    *,
    config: ExperimentConfig,
    capture_content: bytes,
    capture_source: Path,
    reference_content: bytes,
    reference_source: Path,
) -> HeldOutEvaluation:
    """Independently reproduce a fixed training model at the held-out horizon."""
    metadata = parse_capture_metadata(capture_content, source=capture_source)
    reference, W = normalize_reference(read_pcapng_bytes(reference_content, metadata, source=reference_source))
    model = training.best_model
    raw_generated = (
        get_family(model.family)
        .generate(runtime_fitted_model(model), model.final_seed, W, model.final_limits)
        .require_complete()
    )
    encoded = encode_pcapng(raw_generated, metadata, observation_window_seconds=W)
    generated = encoded.trace
    generated_pcapng = encoded.content
    settings_identity = similarity_settings_identity(config.similarity)
    comparison = compare_final_traces(
        reference,
        align_generated(generated, W),
        W,
        config.similarity,
        {
            "capture_json": identify_bytes(capture_content),
            "generated_pcapng": identify_bytes(generated_pcapng),
            "reference_pcapng": identify_bytes(reference_content),
            "similarity_settings": settings_identity,
        },
    )
    comparison_json = render_comparison_result(comparison)
    return HeldOutEvaluation(
        training_model=model,
        training_model_identity=identify_bytes(training.contents["best_model.json"]),
        capture_identity=identify_bytes(capture_content),
        reference_identity=identify_bytes(reference_content),
        generated_identity=identify_bytes(generated_pcapng),
        similarity_settings_identity=settings_identity,
        generated_pcapng=generated_pcapng,
        comparison=comparison,
        comparison_json=comparison_json,
        seed=model.final_seed,
        observation_window_seconds=W,
    )


def build_expected_paths(
    index: dict[str, object],
    protocol: dict[str, object],
    prerequisite_paths: set[str],
    training: Sequence[Training],
    fresh_paths: set[str],
    held_paths: set[str],
) -> set[str]:
    paths = {
        INDEX,
        relative_path(index["environment"], name="index environment"),
        relative_path(index["protocol"], name="index protocol"),
        relative_path(index["prerequisites"], name="index prerequisites"),
        relative_path(index["lifecycle"], name="index lifecycle"),
        relative_path(index["report_inputs"], name="index report inputs"),
        relative_path(index["report"], name="index report"),
        *prerequisite_paths,
        *fresh_paths,
        *held_paths,
    }
    for binding in TRANSFER_BINDINGS:
        paths.add(f"headers/{binding.scope}/{binding.run_id}/{binding.filename}")
        paths.add(f"observations/{binding.scope}/{binding.run_id}/{binding.filename}.json")
    for item in training:
        relative = f"training/{item.workload}/r{item.repeat}"
        paths.update(f"{relative}/{name}" for name in ARTIFACT_NAMES)
        paths.add(f"configs/training-{item.workload}-r{item.repeat}.portable.toml")
        paths.add(f"configs/training-{item.workload}-r{item.repeat}.realized.toml")
    return paths


def headers_and_observations(bundle: Path, *, prerequisites: Mapping[str, object]) -> set[str]:
    capability_value = prerequisites.get("capability")
    if not isinstance(capability_value, Mapping):
        fail(
            "artifact_corrupt",
            "prerequisites.json",
            "prerequisites must retain a capability record",
            "restore canonical prerequisite evidence",
        )
    capability = cast(Mapping[str, object], capability_value)
    initial_url = string(prerequisites.get("url"), name="prerequisite URL")
    object_size = integer(capability.get("object_size_bytes"), name="prerequisite object size", minimum=1)
    paths: set[str] = set()
    for binding in TRANSFER_BINDINGS:
        header = f"headers/{binding.scope}/{binding.run_id}/{binding.filename}"
        observation = f"observations/{binding.scope}/{binding.run_id}/{binding.filename}.json"
        content = read_regular(bundle / header, affected=header)
        try:
            status, content_length, content_range = parse_transfer_header(
                content,
                initial_url=initial_url,
                start=binding.requested_start,
                end=binding.requested_end,
                object_size_bytes=object_size,
            )
        except ValueError as error:
            fail(
                "artifact_corrupt",
                header,
                f"protocol header is not the retained transfer response: {error}",
                "restore protocol-used headers",
            )
        if binding.scope == "prerequisites" and (
            capability.get("canary_sha256") != hashlib.sha256(content).hexdigest()
            or capability.get("content_length") != content_length
            or capability.get("content_range") != content_range
            or (capability.get("status") != status)
        ):
            fail(
                "artifact_foreign",
                header,
                "capability header does not match the retained prerequisite facts",
                "restore the exact retained capability header",
            )
        document = exact(
            parse_json_object(read_regular(bundle / observation, affected=observation), name=observation),
            (
                "content_length",
                "content_range",
                "header_identity",
                "requested_end",
                "requested_start",
                "run_id",
                "scope",
                "status",
                "transfer_index",
                "workload",
            ),
            name=observation,
        )
        expected = {
            "content_length": content_length,
            "content_range": content_range,
            "header_identity": artifact_identity(content),
            "requested_end": binding.requested_end,
            "requested_start": binding.requested_start,
            "run_id": binding.run_id,
            "scope": binding.scope,
            "status": status,
            "transfer_index": binding.transfer_index,
            "workload": binding.workload,
        }
        if document != expected:
            fail(
                "artifact_foreign",
                observation,
                "external observation does not match retained protocol header",
                "restore matching observation",
            )
        paths.update((header, observation))
    return paths
