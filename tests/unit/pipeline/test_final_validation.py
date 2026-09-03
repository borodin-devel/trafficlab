"""Explicit complete-experiment coordinator contract tests."""

from __future__ import annotations

import json
import os
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.pipeline.validation as run_validation
from tests.support.pipeline import (
    prepared_experiment,
    read_run_records,
    replace,
    success_dependencies,
)
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.artifacts.io import FileIdentity
from trafficlab.capture.validation import CaptureInspection
from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent
from trafficlab.comparison.codec import render_comparison_result
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.generation.models.fitted_model import rebuild_best_model, render_best_model
from trafficlab.pipeline.stage import run_experiment


def test_final_artifact_validation_rejects_a_fit_result_with_mismatched_priority(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A final coordinator must compare priority as well as winner, generation, and termination."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    dependencies, (_capture, fit, _generation, _comparison) = success_dependencies(experiment_path, prepared, [])
    object.__setattr__(
        fit, "outcome", replace(fit.outcome, family_priority=tuple(reversed(fit.outcome.family_priority)))
    )

    with pytest.raises(TrafficlabError, match="checkpoint terminal state"):
        run_experiment(experiment_path, dependencies=dependencies)

    records = read_run_records(prepared)
    assert [record for record in records if record.get("event") == "run_completed"] == []


@pytest.mark.parametrize(
    ("corruption", "owner", "match"),
    [
        ("capture-count", "capture", "packet count"),
        ("capture-window", "capture", "reference window"),
        ("capture-pair", "capture", "capture validation failed"),
        ("experiment", "preflight", "experiment.toml"),
        ("missing-checkpoint", "run", "directory entries"),
        ("checkpoint", "fit", "checkpoint schema is incompatible"),
        ("checkpoint-state", "fit", "terminal state"),
        ("history", "fit", "ga_history.csv"),
        ("best-model", "fit", "best_model.json"),
        ("best-model-invalid", "fit", "invalid JSON"),
        ("best-model-noncanonical", "fit", "not canonical"),
        ("best-model-lineage", "fit", "lineage"),
        ("generated", "generate", "generated.pcapng"),
        ("generated-invalid", "generate", "invalid PCAPNG"),
        ("generated-lineage", "compare", "similarity.json lineage"),
        ("similarity", "compare", "similarity.json"),
        ("similarity-invalid", "compare", "similarity.json is invalid"),
        ("similarity-noncanonical", "compare", "canonical sorted"),
        ("extra-entry", "run", "directory entries"),
    ],
)
def test_run_experiment_strictly_reloads_every_owned_artifact_before_completion(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    corruption: str,
    owner: str,
    match: str,
) -> None:
    """Trusting returned objects would let post-stage artifact corruption receive run_completed."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, (_capture, fit, _generation, comparison) = success_dependencies(experiment_path, prepared, calls)
    run_directory = prepared.run_directory
    original_compare = dependencies.compare
    changed_path: Path
    changed_content: bytes | None
    post_compare_best_model: object | None = None

    if corruption in {"capture-count", "capture-window"}:
        metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
        changed_path = run_directory / "reference.pcapng"
        if corruption == "capture-count":
            events = (
                TraceEvent(0.0, Direction.OUTBOUND, 64),
                TraceEvent(5.0, Direction.OUTBOUND, 80),
                TraceEvent(10.0, Direction.INBOUND, 96),
            )
        else:
            events = (TraceEvent(0.0, Direction.OUTBOUND, 64), TraceEvent(12.0, Direction.INBOUND, 96))
        changed_content = encode_pcapng(events, metadata)
    elif corruption == "capture-pair":
        changed_path = run_directory / "capture.json"
        changed_content = b"{}\n"
    elif corruption == "experiment":
        changed_path = run_directory / "experiment.toml"
        changed_content = b'[run]\ndirectory = "different"\n'
    elif corruption == "missing-checkpoint":
        changed_path = run_directory / "checkpoint.json"
        changed_content = None
    elif corruption == "checkpoint":
        changed_path = run_directory / "checkpoint.json"
        changed_content = b"{}\n"
    elif corruption == "checkpoint-state":
        changed_path = run_directory / "checkpoint.json"
        checkpoint_document = cast(dict[str, Any], json.loads(changed_path.read_bytes()))
        population = cast(list[dict[str, Any]], checkpoint_document["population"])
        population[0]["genes"] = [1.25]
        changed_content = (json.dumps(checkpoint_document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    elif corruption == "history":
        changed_path = run_directory / "ga_history.csv"
        changed_content = b"not the checkpoint projection\n"
    elif corruption in {"best-model", "best-model-lineage"}:
        changed_path = run_directory / "best_model.json"
        changed_model = rebuild_best_model(
            fit.best_model,
            capture_identity=ContentIdentity(size=fit.best_model.capture_identity.size, sha256="0" * 64),
        )
        if corruption == "best-model-lineage":
            post_compare_best_model = changed_model
        changed_content = render_best_model(changed_model)
    elif corruption == "best-model-invalid":
        changed_path = run_directory / "best_model.json"
        changed_content = b"not JSON\n"
    elif corruption == "best-model-noncanonical":
        changed_path = run_directory / "best_model.json"
        changed_content = changed_path.read_bytes().rstrip(b"\n") + b" \n"
    elif corruption in {"generated", "generated-lineage", "generated-invalid"}:
        metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
        changed_path = run_directory / "generated.pcapng"
        if corruption == "generated":
            changed_content = encode_pcapng((TraceEvent(0.0, Direction.OUTBOUND, 512),), metadata)
        elif corruption == "generated-invalid":
            changed_content = b"not PCAPNG"
        else:
            changed_content = changed_path.read_bytes() + struct.pack("<III", 0x12345678, 12, 12)
    elif corruption in {"similarity", "similarity-invalid", "similarity-noncanonical"}:
        changed_path = run_directory / "similarity.json"
        if corruption == "similarity-invalid":
            changed_content = b"not JSON\n"
        elif corruption == "similarity-noncanonical":
            changed_content = changed_path.read_bytes().rstrip(b"\n") + b" \n"
        else:
            assert comparison.input_identities is not None
            identities = comparison.input_identities.as_content_identities()
            identities["generated_pcapng"] = ContentIdentity(
                size=identities["generated_pcapng"].size,
                sha256="0" * 64,
            )
            changed_content = render_comparison_result(comparison.with_input_identities(identities))
    else:
        changed_path = run_directory / "unexpected.txt"
        changed_content = b"preserve unexpected entry\n"

    def corrupt_after_compare(path: Path) -> ComparisonResult:
        result = original_compare(path)
        if post_compare_best_model is not None:
            object.__setattr__(fit, "best_model", post_compare_best_model)
        if changed_content is None:
            changed_path.unlink()
        else:
            changed_path.write_bytes(changed_content)
        return result

    object.__setattr__(dependencies, "compare", corrupt_after_compare)

    with pytest.raises(TrafficlabError, match=match):
        run_experiment(experiment_path, dependencies=dependencies)

    assert calls == ["preflight", "capture", "fit", "generate", "compare"]
    if changed_content is None:
        assert not changed_path.exists()
    else:
        assert changed_path.read_bytes() == changed_content
    records = read_run_records(prepared)
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == owner
    assert [record for record in records if record.get("event") == "run_completed"] == []


@pytest.mark.parametrize(
    ("artifact_name", "detail", "corrective_action"),
    [
        (
            "checkpoint.json",
            "checkpoint schema is incompatible",
            "refit under the current schema in a new run directory",
        ),
        (
            "best_model.json",
            "best model schema is incompatible",
            "refit under the current schema",
        ),
    ],
)
def test_final_reload_preserves_schema_incompatibility_outcome(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    artifact_name: str,
    detail: str,
    corrective_action: str,
) -> None:
    """Final reloads retain the source schema outcome instead of reclassifying it as corruption."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, _results = success_dependencies(experiment_path, prepared, calls)
    artifact_path = prepared.run_directory / artifact_name
    original_compare = dependencies.compare
    schema_one: bytes | None = None

    def replace_schema_after_compare(path: Path) -> ComparisonResult:
        nonlocal schema_one
        result = original_compare(path)
        document = cast(dict[str, object], json.loads(artifact_path.read_bytes()))
        document["scientific_artifact_schema"] = 1
        schema_one = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
        artifact_path.write_bytes(schema_one)
        return result

    object.__setattr__(dependencies, "compare", replace_schema_after_compare)

    with pytest.raises(TrafficlabError) as captured:
        run_experiment(experiment_path, dependencies=dependencies)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "kind": "scientific_semantics_incompatible",
        "stage": "fit",
        "detail": detail,
        "corrective_action": corrective_action,
        "affected_evidence": artifact_name,
        "evidence_state": "preserved",
        "authority": "primary",
    }
    assert calls == ["preflight", "capture", "fit", "generate", "compare"]
    assert captured.value.failure_outcomes == (outcome,)
    assert schema_one is not None
    assert artifact_path.read_bytes() == schema_one
    records = read_run_records(prepared)
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "fit"
    assert failures[0]["failure_outcome"] == outcome.as_dict()
    assert failures[0]["corrective_action"] == corrective_action
    assert [record for record in records if record.get("event") == "run_completed"] == []


def test_final_reload_classifies_an_older_checkpoint_schema_as_scientifically_incompatible(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """An older checkpoint schema is rejected as incompatible scientific evidence."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    dependencies, _results = success_dependencies(experiment_path, prepared, [])
    checkpoint_path = prepared.run_directory / "checkpoint.json"
    original_compare = dependencies.compare

    def replace_checkpoint_after_compare(path: Path) -> ComparisonResult:
        result = original_compare(path)
        checkpoint_path.write_bytes(b'{"scientific_artifact_schema":4}\n')
        return result

    object.__setattr__(dependencies, "compare", replace_checkpoint_after_compare)

    with pytest.raises(TrafficlabError) as captured:
        run_experiment(experiment_path, dependencies=dependencies)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "scientific_semantics_incompatible",
        "fit",
        "checkpoint.json",
        "preserved",
    )


@pytest.mark.parametrize(
    ("name", "owner"),
    [
        ("experiment.toml", "preflight"),
        ("run.log", "preflight"),
        ("capture.json", "capture"),
        ("reference.pcapng", "capture"),
        ("checkpoint.json", "fit"),
        ("ga_history.csv", "fit"),
        ("best_model.json", "fit"),
        ("generated.pcapng", "generate"),
        ("similarity.json", "compare"),
    ],
)
def test_run_experiment_rejects_every_final_artifact_replaced_immediately_after_its_read(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    owner: str,
) -> None:
    """Detached validated bytes cannot authorize success after their canonical entry is replaced."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, _results = success_dependencies(experiment_path, prepared, calls)
    destination = prepared.run_directory / name
    replacement = (
        b'{"event":"concurrent_replacement","stage":"preflight"}\n'
        if name == "run.log"
        else b"concurrent replacement\n"
    )
    real_compare = dependencies.compare
    real_read_bytes = Path.read_bytes
    final_validation_started = False
    replaced = False
    destination_reads = 0

    def activate_final_validation(path: Path) -> ComparisonResult:
        nonlocal final_validation_started
        result = real_compare(path)
        final_validation_started = True
        return result

    def replace_after_read(path: Path) -> bytes:
        nonlocal destination_reads, replaced
        content = real_read_bytes(path)
        if final_validation_started and path == destination:
            destination_reads += 1
        trigger_read = 2 if name == "capture.json" else 1
        if destination_reads == trigger_read and not replaced:
            replacement_path = prepared.run_directory / f"replacement-{name}"
            replacement_path.write_bytes(replacement)
            os.replace(replacement_path, destination)
            replaced = True
        return content

    object.__setattr__(dependencies, "compare", activate_final_validation)
    monkeypatch.setattr(Path, "read_bytes", replace_after_read)

    with pytest.raises(TrafficlabError, match=f"{name}.*changed during final validation"):
        run_experiment(experiment_path, dependencies=dependencies)

    assert replaced is True
    persisted = real_read_bytes(destination)
    if name == "run.log":
        assert persisted.startswith(replacement)
    else:
        assert persisted == replacement
    records = [json.loads(line) for line in real_read_bytes(prepared.run_directory / "run.log").splitlines()]
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == owner
    assert [record for record in records if record.get("event") == "run_completed"] == []


def test_run_experiment_rechecks_the_exact_tree_after_the_last_artifact_read(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late new entry must not appear after the sole tree check and still receive completion."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    dependencies, _results = success_dependencies(experiment_path, prepared, [])
    unexpected = prepared.run_directory / "late-entry"
    real_read = run_validation._read_final_artifact  # pyright: ignore[reportPrivateUsage]

    def create_after_last_read(path: Path, *, owner: Any, identities: Any) -> bytes:
        content = real_read(path, owner=owner, identities=identities)
        if path.name == "similarity.json":
            unexpected.write_bytes(b"preserve late entry\n")
        return content

    monkeypatch.setattr(run_validation, "_read_final_artifact", create_after_last_read)

    with pytest.raises(TrafficlabError, match="directory entries"):
        run_experiment(experiment_path, dependencies=dependencies)

    assert unexpected.read_bytes() == b"preserve late entry\n"
    records = read_run_records(prepared)
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "run"
    assert [record for record in records if record.get("event") == "run_completed"] == []


def test_run_experiment_rejects_an_artifact_replaced_during_validation_of_its_read_bytes(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity must remain stable through parsing and lineage checks, not only through the read call."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    dependencies, _results = success_dependencies(experiment_path, prepared, [])
    destination = prepared.run_directory / "similarity.json"
    replacement = b"replacement during validation\n"
    real_parse = run_validation.parse_comparison_result

    def replace_during_parse(content: bytes) -> ComparisonResult:
        result = real_parse(content)
        replacement_path = prepared.run_directory / "replacement-similarity.json"
        replacement_path.write_bytes(replacement)
        os.replace(replacement_path, destination)
        return result

    monkeypatch.setattr(run_validation, "parse_comparison_result", replace_during_parse)

    with pytest.raises(TrafficlabError, match="similarity.json.*changed during final validation"):
        run_experiment(experiment_path, dependencies=dependencies)

    assert destination.read_bytes() == replacement
    records = read_run_records(prepared)
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "compare"
    assert [record for record in records if record.get("event") == "run_completed"] == []


def test_run_experiment_translates_a_final_identity_recheck_failure(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-follow stat failure after validation must retain the exact artifact owner."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    dependencies, _results = success_dependencies(experiment_path, prepared, [])
    destination = prepared.run_directory / "similarity.json"
    real_identity = run_validation.file_identity
    destination_calls = 0

    def fail_recheck(path: Path, *, kind: str, corrective_action: str) -> FileIdentity | None:
        nonlocal destination_calls
        if path == destination:
            destination_calls += 1
            if destination_calls == 3:
                raise TrafficlabError("simulated final identity failure", corrective_action="stabilize entry")
        return real_identity(path, kind=kind, corrective_action=corrective_action)

    monkeypatch.setattr(run_validation, "file_identity", fail_recheck)

    with pytest.raises(TrafficlabError, match="could not inspect similarity.json.*final identity failure"):
        run_experiment(experiment_path, dependencies=dependencies)

    records = read_run_records(prepared)
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "compare"
    assert [record for record in records if record.get("event") == "run_completed"] == []


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (b"{}", "end with a newline"),
        (b"[]\n", "JSON object"),
        (b'{ "event":"run_prepared"}\n', "canonical sorted compact JSON"),
    ],
)
def test_final_run_log_validation_rejects_each_noncanonical_shape(content: bytes, match: str) -> None:
    """The final reload must reject truncation, non-object records, and noncanonical JSON bytes."""
    with pytest.raises(TrafficlabError, match=match):
        run_validation._validate_final_run_log(content)  # pyright: ignore[reportPrivateUsage]


def test_final_artifact_read_failure_is_translated_with_its_owner(tmp_path: Path) -> None:
    """A missing final artifact must identify both its owning stage and exact filename."""
    missing = tmp_path / "checkpoint.json"

    with pytest.raises(TrafficlabError, match="final run artifact validation failed for fit.*checkpoint.json"):
        run_validation._read_final_artifact(  # pyright: ignore[reportPrivateUsage]
            missing,
            owner="fit",
            identities={},
        )


def test_run_experiment_translates_final_directory_inspection_failure(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A final directory read failure must prevent completion and retain one contextual run failure."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, _results = success_dependencies(experiment_path, prepared, calls)
    real_iterdir = Path.iterdir

    def fail_run_directory(path: Path) -> Iterator[Path]:
        if path == prepared.run_directory:
            raise PermissionError("simulated directory inspection failure")
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_run_directory)

    with pytest.raises(TrafficlabError, match="could not inspect the run directory"):
        run_experiment(experiment_path, dependencies=dependencies)

    assert calls == ["preflight", "capture", "fit", "generate", "compare"]
    failures = [record for record in read_run_records(prepared) if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "run"
    assert [record for record in read_run_records(prepared) if record.get("event") == "run_completed"] == []


def test_run_experiment_rejects_a_capture_pair_replaced_after_strict_pair_validation(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict pair validation cannot authorize lineage loading from subsequently replaced reference bytes."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    calls: list[str] = []
    dependencies, _results = success_dependencies(experiment_path, prepared, calls)
    reference_path = prepared.run_directory / "reference.pcapng"
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    replacement = encode_pcapng(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 64),
            TraceEvent(5.0, Direction.OUTBOUND, 80),
            TraceEvent(10.0, Direction.INBOUND, 96),
        ),
        metadata,
    )
    real_validate = run_validation.validate_capture_pair

    def replace_after_validation(
        metadata_path: Path,
        pcapng_path: Path,
        *,
        deadline: float | None,
    ) -> CaptureInspection:
        inspection = real_validate(metadata_path, pcapng_path, deadline=deadline)
        reference_path.write_bytes(replacement)
        return inspection

    monkeypatch.setattr(run_validation, "validate_capture_pair", replace_after_validation)

    with pytest.raises(TrafficlabError, match="changed between strict validation and lineage loading"):
        run_experiment(experiment_path, dependencies=dependencies)

    assert calls == ["preflight", "capture", "fit", "generate", "compare"]
    assert reference_path.read_bytes() == replacement
    records = read_run_records(prepared)
    assert len([record for record in records if record.get("event") == "run_failed"]) == 1
    assert [record for record in records if record.get("event") == "run_completed"] == []


def test_run_experiment_translates_invalid_reference_bytes_installed_after_pair_validation(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-inspection invalid reference must remain a capture-owned final-validation failure."""
    experiment_path, prepared = prepared_experiment(valid_config_data, tmp_path)
    dependencies, _results = success_dependencies(experiment_path, prepared, [])
    reference_path = prepared.run_directory / "reference.pcapng"
    replacement = b"not PCAPNG"
    real_validate = run_validation.validate_capture_pair

    def replace_after_validation(
        metadata_path: Path,
        pcapng_path: Path,
        *,
        deadline: float | None,
    ) -> CaptureInspection:
        inspection = real_validate(metadata_path, pcapng_path, deadline=deadline)
        reference_path.write_bytes(replacement)
        return inspection

    monkeypatch.setattr(run_validation, "validate_capture_pair", replace_after_validation)

    with pytest.raises(TrafficlabError, match="final run artifact validation failed for capture.*invalid PCAPNG"):
        run_experiment(experiment_path, dependencies=dependencies)

    assert reference_path.read_bytes() == replacement
    records = read_run_records(prepared)
    failures = [record for record in records if record.get("event") == "run_failed"]
    assert len(failures) == 1
    assert failures[0]["failed_stage"] == "capture"
    assert [record for record in records if record.get("event") == "run_completed"] == []
