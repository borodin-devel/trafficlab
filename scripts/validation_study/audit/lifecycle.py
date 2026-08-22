"""Lifecycle owner for Validation Study tooling."""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scripts.validation_study.audit.artifacts import (
    build_expected_paths,
    headers_and_observations,
    metadata,
    rebuild_fresh,
    selected_training,
    verify_inventory,
)
from scripts.validation_study.audit.common import (
    INDEX,
    INDEX_SCHEMA,
    MANIFEST,
    REPEATS,
    WORKLOADS,
    AuditResult,
    Issue,
    artifact_identity,
    exact,
    fail,
    parse_json_object,
    parse_run_log_records,
    path_key,
    read_regular,
    relative_path,
    require_directory,
    required_log_record,
    string,
    validated_study_root,
    workload_name,
)
from scripts.validation_study.audit.environment import (
    load_environment,
    load_prerequisites,
    load_protocol,
    require_permitted_relocated_worktree,
)
from scripts.validation_study.audit.science import (
    load_frozen_profiles,
    rebuild_held_out,
    rebuild_report_inputs,
    rebuild_training,
)
from scripts.validation_study.common import PRIMARY_ORDER
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.errors import FailureOutcome, TrafficlabError
from trafficlab.study_evidence.protocol import (
    ValidationStudyLifecycle,
    ValidationStudyLineage,
)
from trafficlab.study_evidence.report import ValidationStudyReport, ValidationStudyReportInput

if TYPE_CHECKING:
    from scripts.validation_study.audit.common import Entry, Training
    from scripts.validation_study.records import HeldOutEvaluation


def _lifecycle_project_name(content: bytes, *, name: str) -> str:
    """Read the one capture project identity already bound by the retained run log."""
    records = parse_run_log_records(content, name=name)
    creation = required_log_record(records, event="capture_project_created", name=name)
    publication = required_log_record(records, event="capture_published", name=name)
    created_project_name = string(creation.get("project_name"), name=f"created capture project name for {name}")
    project_name = string(publication.get("project_name"), name=f"capture project name for {name}")
    if (
        creation.get("stage") != "capture"
        or publication.get("stage") != "capture"
        or (not created_project_name.startswith("trafficlab-capture-"))
        or (created_project_name != project_name)
        or (records.index(creation) >= records.index(publication))
    ):
        fail(
            "artifact_foreign",
            name,
            "capture project creation and publication do not retain one owned project identity",
            "restore matching capture lineage",
        )
    return project_name


def _lifecycle_rows(value: object, *, expected: Sequence[dict[str, object]], name: str) -> None:
    """Require a closed ordered lifecycle row list before trusting cleanup assertions."""
    if type(value) is not list:
        fail("artifact_corrupt", "lifecycle.json", f"{name} lifecycle rows must be a list", "restore lifecycle proof")
    rows = cast(list[dict[str, object]], value)
    if rows != expected:
        fail(
            "artifact_foreign",
            "lifecycle.json",
            f"{name} lifecycle rows do not bind the completed capture runs",
            "restore matching collection cleanup evidence",
        )


def validate_lifecycle(
    bundle: Path,
    value: object,
    *,
    protocol: Mapping[str, object],
    environment: Mapping[str, object],
    training: Sequence[Training],
) -> None:
    """Independently validate candidate-owned capture and phase-image cleanup proof."""
    if (
        type(value) is not dict
        or type(cast(dict[str, object], value).get("schema_version")) is not int
        or cast(dict[str, object], value).get("schema_version") != 1
    ):
        fail(
            "artifact_corrupt",
            "lifecycle.json",
            "collection lifecycle must use schema version 1",
            "restore canonical collection cleanup evidence",
        )
    document = validated_study_root(cast(dict[str, object], value), ValidationStudyLifecycle, name="lifecycle.json")
    study_id = string(document["study_id"], name="lifecycle study ID")
    if study_id != protocol["study_id"] or study_id != bundle.name:
        fail(
            "artifact_foreign",
            "lifecycle.json",
            "collection lifecycle study ID does not match frozen protocol identity",
            "restore matching collection cleanup evidence",
        )
    phase = cast(dict[str, object], document["phase_capture_image"])
    expected_phase = {
        "capture_image_id": environment["capture_image_id"],
        "cleanup_verified": True,
        "post_cleanup_inspect_exit_status": 1,
        "tag": f"trafficlab-validation-{study_id}:collection-capture",
    }
    if phase != expected_phase:
        fail(
            "artifact_foreign",
            "lifecycle.json",
            "collection phase capture image cleanup does not match frozen identity",
            "restore matching collection cleanup evidence",
        )
    training_by_key = {(item.workload, item.repeat): item for item in training}
    expected_training: list[dict[str, object]] = []
    for _order, run_id, workload, repeat in PRIMARY_ORDER:
        item = training_by_key[workload, repeat]
        relative = f"training/{workload}/r{repeat}"
        expected_training.append(
            {
                "cleanup_verified": True,
                "directory": relative,
                "project_name": _lifecycle_project_name(item.contents["run.log"], name=f"{relative}/run.log"),
                "run_id": run_id,
            }
        )
    _lifecycle_rows(document["training"], expected=expected_training, name="training")
    expected_held_out: list[dict[str, object]] = []
    for workload in WORKLOADS:
        relative = f"held_out/{workload}"
        content = read_regular(bundle / relative / "run.log", affected=f"{relative}/run.log")
        expected_held_out.append(
            {
                "cleanup_verified": True,
                "directory": relative,
                "project_name": _lifecycle_project_name(content, name=f"{relative}/run.log"),
                "run_id": f"held-out-{workload}",
            }
        )
    _lifecycle_rows(document["held_out"], expected=expected_held_out, name="held-out")
    project_names = [cast(str, row["project_name"]) for row in (*expected_training, *expected_held_out)]
    if len(project_names) != 12 or len(set(project_names)) != len(project_names):
        fail(
            "artifact_foreign",
            "lifecycle.json",
            "collection lifecycle must bind twelve distinct capture projects",
            "restore the exact retained capture cleanup proof",
        )


def _audit(
    bundle: Path, repository: Path, entries: tuple[Entry, ...], *, source_candidate: Path | None = None
) -> AuditResult:
    require_permitted_relocated_worktree(repository, candidate=bundle, source_candidate=source_candidate)
    index = parse_json_object(read_regular(bundle / INDEX, affected=INDEX), name=INDEX)
    index_version = index.get("schema_version")
    if type(index_version) is not int:
        fail(
            "artifact_corrupt",
            INDEX,
            "evidence index schema version must be an integer",
            "restore canonical evidence index",
        )
    if index_version != INDEX_SCHEMA:
        fail(
            "scientific_semantics_incompatible",
            INDEX,
            "evidence index must use schema version 4",
            "rebuild retained evidence under schema 4",
        )
    index = validated_study_root(index, ValidationStudyLineage, name=INDEX)
    metadata(index, entries)
    environment_path = relative_path(index["environment"], name="index environment")
    protocol_path = relative_path(index["protocol"], name="index protocol")
    prerequisites_path = relative_path(index["prerequisites"], name="index prerequisites")
    if (
        environment_path,
        protocol_path,
        prerequisites_path,
        relative_path(index["lifecycle"], name="index lifecycle"),
        relative_path(index["report_inputs"], name="index report inputs"),
        relative_path(index["report"], name="index report"),
    ) != (
        "environment.json",
        "protocol.json",
        "prerequisites.json",
        "lifecycle.json",
        "report_inputs.json",
        "report.json",
    ):
        fail(
            "artifact_foreign", INDEX, "index root evidence paths are not canonical", "restore canonical evidence index"
        )
    environment = load_environment(
        read_regular(bundle / environment_path, affected=environment_path), repository=repository
    )
    protocol = load_protocol(read_regular(bundle / protocol_path, affected=protocol_path))
    prerequisites, prerequisite_paths = load_prerequisites(bundle, prerequisites_path, environment=environment)
    if protocol["study_id"] != bundle.name:
        fail(
            "artifact_foreign",
            "protocol.json",
            "protocol destination ID must equal the candidate directory name",
            "restore the candidate under its frozen study ID",
        )
    if prerequisites["study_id"] != protocol["study_id"]:
        fail(
            "artifact_foreign",
            "prerequisites.json",
            "retained prerequisites must bind the frozen study identity",
            "restore matching prerequisite evidence",
        )
    headers_and_observations(bundle, prerequisites=prerequisites)
    frozen_profiles = load_frozen_profiles(
        repository, environment=environment, protocol=protocol, url=cast(str, prerequisites["url"])
    )
    training_items = cast(list[object], index["training"])
    training = tuple(
        rebuild_training(
            bundle,
            value,
            protocol=protocol,
            environment=environment,
            frozen_profiles=frozen_profiles,
            url=cast(str, prerequisites["url"]),
        )
        for value in training_items
    )
    expected_keys = {(workload, repeat) for workload in WORKLOADS for repeat in REPEATS}
    if {(item.workload, item.repeat) for item in training} != expected_keys:
        fail(
            "artifact_foreign",
            INDEX,
            "training records must contain each workload and repeat exactly once",
            "restore complete training evidence",
        )
    ordered_training = tuple(sorted(training, key=lambda item: (WORKLOADS.index(item.workload), item.repeat)))
    fresh_items = cast(list[object], index["fresh_simulation"])
    fresh_paths = {
        rebuild_fresh(bundle, value, item, final_seed=cast(int, protocol["final_seed"]))
        for value, item in zip(fresh_items, ordered_training, strict=True)
    }
    if len(fresh_paths) != 9:
        fail(
            "artifact_foreign",
            INDEX,
            "fresh_simulation records must be unique",
            "restore complete fresh simulation evidence",
        )
    held_items = cast(list[object], index["held_out"])
    selected = selected_training(protocol, ordered_training)
    training_references = {identify_bytes(item.contents["reference.pcapng"]).sha256 for item in ordered_training}
    held_evaluations: dict[str, HeldOutEvaluation] = {}
    held_paths: set[str] = set()
    for value in held_items:
        record = exact(
            value, ("capture_lineage", "directory", "training_directory", "workload"), name="held-out index record"
        )
        workload = workload_name(record["workload"], name="held-out workload")
        if workload in held_evaluations or workload not in selected:
            fail(
                "artifact_foreign",
                INDEX,
                "held-out records must bind each workload once",
                "restore complete held-out evidence",
            )
        _directory_relative, paths, evaluation = rebuild_held_out(
            bundle,
            value,
            selected[workload],
            final_seed=cast(int, protocol["final_seed"]),
            training_references=training_references,
            environment=environment,
            frozen_profiles=frozen_profiles,
        )
        held_evaluations[workload] = evaluation
        held_paths.update(paths)
    lifecycle_path = relative_path(index["lifecycle"], name="index lifecycle")
    validate_lifecycle(
        bundle,
        parse_json_object(read_regular(bundle / lifecycle_path, affected=lifecycle_path), name=lifecycle_path),
        protocol=protocol,
        environment=environment,
        training=ordered_training,
    )
    expected_paths = build_expected_paths(
        index, protocol, prerequisite_paths, ordered_training, fresh_paths, held_paths
    )
    actual_paths = {entry.path for entry in entries}
    for relative in sorted(expected_paths - actual_paths, key=path_key):
        fail(
            "artifact_missing",
            relative,
            f"{relative} is required by the complete evidence schema",
            "restore complete retained evidence",
        )
    for relative in sorted(actual_paths - expected_paths, key=path_key):
        fail(
            "artifact_foreign",
            relative,
            f"{relative} is not part of the complete evidence schema",
            "remove foreign retained evidence",
        )
    inputs_path = relative_path(index["report_inputs"], name="index report inputs")
    report_inputs = validated_study_root(
        parse_json_object(read_regular(bundle / inputs_path, affected=inputs_path), name=inputs_path),
        ValidationStudyReportInput,
        name=inputs_path,
    )
    expected_inputs = rebuild_report_inputs(ordered_training, held_evaluations)
    if report_inputs != expected_inputs:
        fail(
            "artifact_foreign",
            inputs_path,
            "report inputs do not match reconstructed evidence arithmetic",
            "restore matching report inputs",
        )
    report_path = relative_path(index["report"], name="index report")
    report = validated_study_root(
        parse_json_object(read_regular(bundle / report_path, affected=report_path), name=report_path),
        ValidationStudyReport,
        name=report_path,
    )
    if (
        report["formula"] != "arithmetic_mean"
        or report["report_inputs_identity"]
        != artifact_identity(read_regular(bundle / inputs_path, affected=inputs_path))
        or report["summary"] != expected_inputs
    ):
        fail(
            "artifact_foreign",
            report_path,
            "report does not match retained report inputs and arithmetic",
            "restore matching report",
        )
    return AuditResult(
        bundle,
        ordered_training[0].directory,
        hashlib.sha256(read_regular(bundle / MANIFEST, affected=MANIFEST)).hexdigest(),
        len(entries),
    )


def _audit_bundle(bundle: Path, *, repository: Path, source_candidate: Path | None = None) -> AuditResult:
    """Strictly audit one complete candidate before exclusive accepted publication."""
    try:
        root = require_directory(bundle, name="bundle")
        repository_root = require_directory(repository, name="repository")
        try:
            root.relative_to(repository_root)
        except ValueError:
            fail(
                "artifact_foreign",
                "bundle",
                "bundle must remain beneath the relocated repository",
                "use a retained candidate beneath the repository",
            )
        manifest = read_regular(root / MANIFEST, affected=MANIFEST)
        entries = verify_inventory(root, manifest)
        return _audit(root, repository_root, entries, source_candidate=source_candidate)
    except Issue as issue:
        outcome = FailureOutcome(
            kind=issue.kind,
            stage="publication",
            detail=issue.detail,
            affected_evidence=issue.affected,
            evidence_state="not_published",
            corrective_action=issue.action,
            authority="primary",
        )
        error = TrafficlabError(issue.detail, corrective_action=issue.action)
        error.failure_outcomes = (outcome,)
        error.failure_outcome = outcome
        raise error from issue
    except TrafficlabError as error:
        if error.failure_outcome is not None:
            raise
        outcome = FailureOutcome(
            kind="artifact_corrupt",
            stage="publication",
            detail=str(error),
            affected_evidence="candidate evidence",
            evidence_state="not_published",
            corrective_action=error.corrective_action,
            authority="primary",
        )
        error.failure_outcomes = (outcome,)
        error.failure_outcome = outcome
        raise


def audit_bundle(bundle: Path, *, repository: Path) -> AuditResult:
    """Strictly audit one complete candidate before exclusive accepted publication."""
    return _audit_bundle(bundle, repository=repository)


def audit_staged_bundle(bundle: Path, *, repository: Path, source_candidate: Path) -> AuditResult:
    """Audit a copied candidate while excluding its known source from worktree-state evidence."""
    return _audit_bundle(bundle, repository=repository, source_candidate=source_candidate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="audit_validation_study.py", description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parsed = build_parser().parse_args(argv)
    try:
        result = audit_bundle(parsed.bundle, repository=parsed.repository)
    except TrafficlabError as error:
        print(f"validation-study-audit: {error}; {error.corrective_action}", file=sys.stderr)
        return error.exit_code
    print(f"validation-study-audit: accepted {result.file_count} retained files at {result.bundle}")
    return 0
