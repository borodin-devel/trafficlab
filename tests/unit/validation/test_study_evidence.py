from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

import trafficlab.study_evidence as study_evidence
from trafficlab.common.errors import TrafficlabError
from trafficlab.study_evidence import (
    StudyContentIdentity,
    ValidationStudyEnvironment,
    ValidationStudyLifecycle,
    ValidationStudyLineage,
    ValidationStudyManifest,
    ValidationStudyPrerequisite,
    ValidationStudyProtocol,
    ValidationStudyReport,
    ValidationStudyReportInput,
    publish_accepted_bundle,
    validate_study_model,
)

REPOSITORY = Path(__file__).resolve().parents[3]
_STUDY_FIXTURE = REPOSITORY / "tests" / "fixtures" / "data" / "validation_study" / "candidate"
_SCAPY_R2_STUDY = REPOSITORY / "examples" / "validation_study" / "evidence" / "2026-08-20-scapy-production-r2"
_CURRENT_STUDY = REPOSITORY / "examples" / "validation_study" / "evidence" / "2026-08-20-scapy-production-r3"
_HISTORICAL_STUDIES = (
    "examples/validation_study/evidence/2026-08-20-stack-adoption-r6",
    "examples/validation_study/evidence/2026-08-18-research-fitness-r21",
)

_STUDY_ROOTS: dict[str, tuple[type[BaseModel], str]] = {
    "study_environment": (ValidationStudyEnvironment, "environment.json"),
    "study_lifecycle": (ValidationStudyLifecycle, "lifecycle.json"),
    "study_lineage": (ValidationStudyLineage, "index.json"),
    "study_manifest": (ValidationStudyManifest, "manifest.json"),
    "study_prerequisite": (ValidationStudyPrerequisite, "prerequisites.json"),
    "study_protocol": (ValidationStudyProtocol, "protocol.json"),
    "study_report": (ValidationStudyReport, "report.json"),
    "study_report_input": (ValidationStudyReportInput, "report_inputs.json"),
}


def _checked_study_paths(filename: str) -> tuple[Path, ...]:
    return (_STUDY_FIXTURE / filename, _SCAPY_R2_STUDY / filename, _CURRENT_STUDY / filename)


@pytest.mark.integration
def test_current_study_is_schema_v4_scapy_production_and_passes_offline_audit(tmp_path: Path) -> None:
    """The navigated study must be the accepted schema-v4 production Scapy bundle."""

    environment = ValidationStudyEnvironment.model_validate_json((_CURRENT_STUDY / "environment.json").read_bytes())
    repository = tmp_path / "recorded-source"
    subprocess.run(
        ("git", "clone", "--no-local", "--no-hardlinks", "--no-checkout", str(REPOSITORY), str(repository)),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "checkout", "--detach", environment.source_commit),
        cwd=repository,
        check=True,
        capture_output=True,
    )
    copied = repository / _CURRENT_STUDY.relative_to(REPOSITORY)
    copied.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_CURRENT_STUDY, copied, copy_function=shutil.copy2)
    alternates = repository / ".git" / "objects" / "info" / "alternates"
    files = tuple(path for path in copied.rglob("*") if path.is_file())
    completed = subprocess.run(
        (
            "uv",
            "run",
            "--locked",
            "--offline",
            "python",
            "scripts/audit_validation_study.py",
            copied.relative_to(repository).as_posix(),
            "--repository",
            ".",
        ),
        cwd=repository,
        env={**os.environ, "PYTHONPATH": "", "UV_OFFLINE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert environment.scientific_artifact_schema == 4
    assert not alternates.exists() or alternates.read_bytes() == b""
    assert files
    assert all(not path.is_symlink() and path.stat().st_nlink == 1 for path in files)
    assert completed.returncode == 0, completed.stderr
    assert "validation-study-audit: accepted 231 retained files" in completed.stdout


def test_historical_r6_and_r21_are_byte_unchanged_from_mvp3() -> None:
    """Publishing current evidence must never rewrite either accepted predecessor."""

    completed = subprocess.run(
        ("git", "diff", "--quiet", "MVP_3", "HEAD", "--", *_HISTORICAL_STUDIES),
        cwd=REPOSITORY,
        check=False,
    )

    assert completed.returncode == 0


def test_scapy_r2_is_byte_unchanged_after_its_publication() -> None:
    """The superseded schema-v4 accepted study remains immutable after corrective source changes."""

    completed = subprocess.run(
        ("git", "diff", "--quiet", "efdd6d7", "HEAD", "--", _SCAPY_R2_STUDY.relative_to(REPOSITORY).as_posix()),
        cwd=REPOSITORY,
        check=False,
    )

    assert completed.returncode == 0


def test_public_validation_study_roots_are_strict_frozen_and_match_checked_wire_documents() -> None:
    """A permissive or runtime-shaped root would fail to describe the checked publication bytes."""

    for name, (model, filename) in _STUDY_ROOTS.items():
        assert model.model_config.get("extra") == "forbid", name
        assert model.model_config.get("frozen") is True, name
        assert model.model_config.get("strict") is True, name
        assert model.model_config.get("allow_inf_nan") is False, name
        schema = model.model_json_schema(mode="validation")
        Draft202012Validator.check_schema(schema)
        paths = _checked_study_paths(filename)
        assert len(paths) == 3, name
        for path in paths:
            content = path.read_bytes()
            document = json.loads(content)
            Draft202012Validator(schema).validate(document)  # pyright: ignore[reportUnknownMemberType]
            rendered = model.model_validate(document).model_dump(mode="json")
            assert rendered == document, path
            assert (
                json.dumps(
                    rendered, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
                ).encode()
                + b"\n"
                == content
            ), path


def test_historical_schema_v2_evidence_is_retained_for_its_recorded_source_checkout() -> None:
    """Current roots reject old semantics while the retained bundle still names an available source commit."""
    historical = REPOSITORY / "examples" / "validation_study" / "evidence" / "2026-08-18-research-fitness-r21"
    environment = json.loads((historical / "environment.json").read_bytes())

    assert environment["scientific_artifact_schema"] == 2
    with pytest.raises(ValidationError):
        ValidationStudyEnvironment.model_validate(environment)
    subprocess.run(
        ["git", "cat-file", "-e", f"{environment['source_commit']}^{{commit}}"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    ("root_name", "case"),
    (
        ("study_environment", "schema_type"),
        ("study_environment", "unknown"),
        ("study_prerequisite", "schema_version"),
        ("study_prerequisite", "exit_status_type"),
        ("study_manifest", "schema_version"),
        ("study_manifest", "hash"),
        ("study_manifest", "blank_owner"),
        ("study_lineage", "schema_version"),
        ("study_lineage", "repeat_type"),
        ("study_lifecycle", "cleanup_type"),
        ("study_protocol", "seed_type"),
        ("study_report_input", "formula"),
        ("study_report", "size"),
    ),
)
def test_public_validation_study_roots_reject_wire_type_key_version_hash_and_count_mutations(
    root_name: str,
    case: str,
) -> None:
    """Each root must reject a representative mutation before cross-record audit policy runs."""

    model, filename = _STUDY_ROOTS[root_name]
    document = cast(dict[str, object], json.loads((_STUDY_FIXTURE / filename).read_bytes()))
    changed = deepcopy(document)
    if case == "schema_type":
        changed["scientific_artifact_schema"] = True
    elif case == "unknown":
        changed["unknown"] = "field"
    elif case == "schema_version":
        changed["schema_version"] = 3 if root_name == "study_manifest" else 2
    elif case == "exit_status_type":
        cast(list[dict[str, object]], changed["commands"])[0]["exit_status"] = False
    elif case == "hash":
        cast(list[dict[str, object]], changed["files"])[0]["sha256"] = "0" * 63
    elif case == "blank_owner":
        cast(list[dict[str, object]], changed["files"])[0]["owner"] = " "
    elif case == "repeat_type":
        cast(list[dict[str, object]], changed["training"])[0]["repeat"] = True
    elif case == "cleanup_type":
        cast(list[dict[str, object]], changed["training"])[0]["cleanup_verified"] = 1
    elif case == "seed_type":
        changed["final_seed"] = 97.0
    elif case == "formula":
        changed["formula"] = "median"
    else:
        cast(dict[str, object], changed["report_inputs_identity"])["size"] = -1

    if case != "seed_type":
        assert not Draft202012Validator(model.model_json_schema(mode="validation")).is_valid(changed)  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
    with pytest.raises(ValidationError):
        model.model_validate(changed)


def test_study_model_validation_rejects_unsafe_paths_exact_numeric_aliases_and_poisoned_instances() -> None:
    """Path traversal, int-as-float, and constructed invalid children must not bypass a public root."""

    protocol = cast(dict[str, object], json.loads((_STUDY_FIXTURE / "protocol.json").read_bytes()))
    protocol["prerequisite_path"] = "../prerequisites.json"
    with pytest.raises(ValidationError):
        ValidationStudyProtocol.model_validate(protocol)

    report_input = cast(dict[str, object], json.loads((_STUDY_FIXTURE / "report_inputs.json").read_bytes()))
    cast(list[dict[str, object]], report_input["controlled_weight_analysis"])[0]["alternative_aggregate"] = 1
    with pytest.raises(ValidationError):
        ValidationStudyReportInput.model_validate(report_input)

    report = cast(dict[str, object], json.loads((_STUDY_FIXTURE / "report.json").read_bytes()))
    report["report_inputs_identity"] = StudyContentIdentity.model_construct(size=-1, sha256="0" * 64)
    with pytest.raises(ValidationError):
        ValidationStudyReport.model_validate(report)


def test_study_model_validation_diagnostic_is_stable_and_root_is_frozen() -> None:
    """Public validation must omit persisted input and Pydantic URLs, then expose immutable results."""

    marker = "persisted-secret-marker"
    with pytest.raises(ValueError) as error:
        validate_study_model(
            ValidationStudyEnvironment,
            {"unknown": marker},
            name="study environment",
        )
    assert marker not in str(error.value)
    assert "pydantic.dev" not in str(error.value)

    environment = ValidationStudyEnvironment.model_validate(
        json.loads((_STUDY_FIXTURE / "environment.json").read_bytes())
    )
    with pytest.raises(ValidationError):
        environment.source_commit = "0" * 40  # type: ignore[misc]


@pytest.mark.parametrize(
    "relative",
    (
        "training/short/r1/best_model.json",
        "held_out/short/record.json",
    ),
)
def test_lineage_roots_reject_bogus_repeated_and_held_out_relation_tags(relative: str) -> None:
    """A shape-correct lineage record must still use its exact canonical relation vocabulary."""

    document = cast(dict[str, object], json.loads((_STUDY_FIXTURE / "index.json").read_bytes()))
    lineage = cast(dict[str, dict[str, object]], document["lineage"])
    lineage[relative]["relation"] = "bogus-relation"
    schema = ValidationStudyLineage.model_json_schema(mode="validation")

    assert not Draft202012Validator(schema).is_valid(document)  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
    with pytest.raises(ValidationError):
        ValidationStudyLineage.model_validate(document)


@pytest.mark.parametrize("mutation", ("duplicate", "unsorted"))
def test_manifest_root_rejects_duplicate_and_unsorted_paths(mutation: str) -> None:
    """Manifest-local path uniqueness and UTF-8 order are validated before index comparison."""

    document = cast(dict[str, object], json.loads((_STUDY_FIXTURE / "manifest.json").read_bytes()))
    files = cast(list[dict[str, object]], document["files"])
    if mutation == "duplicate":
        files[1]["path"] = files[0]["path"]
    else:
        files[0], files[1] = files[1], files[0]

    with pytest.raises(ValidationError):
        ValidationStudyManifest.model_validate(document)


def _candidate(tmp_path: Path) -> Path:
    candidate = tmp_path / "candidate"
    (candidate / "runs" / "primary").mkdir(parents=True)
    (candidate / "manifest.json").write_bytes(b'{"files":[]}\n')
    (candidate / "runs" / "primary" / "result.json").write_bytes(b'{"score":1.0}\n')
    return candidate


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _temporary_siblings(evidence_root: Path, study_id: str) -> list[Path]:
    return list(evidence_root.glob(f".{study_id}.*.tmp")) if evidence_root.exists() else []


def _git_ignores(relative_path: str) -> bool:
    result = subprocess.run(
        ("git", "check-ignore", "--no-index", "--quiet", relative_path),
        cwd=REPOSITORY,
        check=False,
    )
    assert result.returncode in {0, 1}
    return result.returncode == 0


def test_only_completed_accepted_evidence_is_trackable() -> None:
    assert _git_ignores("runs/scratch/result.json")
    assert _git_ignores("examples/validation_study/.study-work/candidate/manifest.json")
    assert _git_ignores("examples/validation_study/evidence/.candidates/study-1/manifest.json")
    assert _git_ignores("examples/validation_study/evidence/.study-1.random.tmp/manifest.json")
    assert not _git_ignores("examples/validation_study/evidence/study-1/manifest.json")


@pytest.mark.parametrize(
    "study_id",
    ["", ".", "..", ".hidden", "-leading", "nested/study", "nested\\study", "contains space", "nonascii-\u044f"],
)
def test_publication_rejects_unsafe_or_noncomponent_study_ids(tmp_path: Path, study_id: str) -> None:
    candidate = _candidate(tmp_path)
    audit_called = False

    def audit(_path: Path) -> None:
        nonlocal audit_called
        audit_called = True

    with pytest.raises(TrafficlabError, match="invalid accepted evidence study ID"):
        publish_accepted_bundle(candidate, tmp_path / "evidence", study_id, audit)

    assert audit_called is False
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("candidate_kind", ["missing", "file", "symlink"])
def test_publication_requires_a_regular_candidate_directory(tmp_path: Path, candidate_kind: str) -> None:
    candidate = tmp_path / "candidate"
    if candidate_kind == "file":
        candidate.write_bytes(b"not a directory")
    elif candidate_kind == "symlink":
        target = _candidate(tmp_path / "target-root")
        candidate.symlink_to(target, target_is_directory=True)
    audit_called = False

    def audit(_path: Path) -> None:
        nonlocal audit_called
        audit_called = True

    with pytest.raises(TrafficlabError, match="regular directory"):
        publish_accepted_bundle(candidate, tmp_path / "evidence", "study-1", audit)

    assert audit_called is False
    assert not (tmp_path / "evidence").exists()


def test_audit_runs_before_any_destination_or_temporary_sibling_exists(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    destination = evidence_root / "study-1"
    calls: list[Path] = []

    def audit(path: Path) -> None:
        calls.append(path)
        assert not destination.exists()
        if path == candidate:
            assert not evidence_root.exists()
        else:
            assert path.name == "study-1"
            assert path.parent.parent == evidence_root

    published = publish_accepted_bundle(candidate, evidence_root, "study-1", audit)

    assert len(calls) == 2
    assert calls[0] == candidate
    assert calls[1].name == "study-1"
    assert published == destination
    assert _tree_bytes(destination) == _tree_bytes(candidate)
    assert list(evidence_root.iterdir()) == [destination]


def test_staged_audit_runs_on_the_final_named_child_before_rename(tmp_path: Path) -> None:
    """The bytes to be renamed, not only their source, must pass the bundle audit."""

    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    destination = evidence_root / "study-1"
    calls: list[Path] = []

    def audit(path: Path) -> None:
        calls.append(path)
        if path == candidate:
            assert not evidence_root.exists()
            return
        assert path.name == "study-1"
        assert path.parent.parent == evidence_root
        assert not destination.exists()
        assert (path / "manifest.json").read_bytes() == b'{"files":[]}\n'

    published = publish_accepted_bundle(candidate, evidence_root, "study-1", audit)

    assert published == destination
    assert len(calls) == 2
    assert calls[0] == candidate
    assert calls[1].name == "study-1"
    assert _tree_bytes(destination) == _tree_bytes(candidate)
    assert _temporary_siblings(evidence_root, "study-1") == []


class _StagedAuditAbort(BaseException):
    pass


class _CleanupAbort(BaseException):
    pass


def _injected_cleanup_failure(_path: Path) -> str:
    return "injected cleanup failure"


@pytest.mark.parametrize("failure", (OSError(errno.EIO, "staged audit I/O failure"), _StagedAuditAbort()))
def test_staged_audit_failure_preserves_source_and_removes_staging(tmp_path: Path, failure: BaseException) -> None:
    """A copied-but-rejected bundle never becomes an accepted destination."""

    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    destination = evidence_root / "study-1"
    before = _tree_bytes(candidate)
    calls = 0

    def audit(_path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise failure

    with pytest.raises(type(failure)) as caught:
        publish_accepted_bundle(candidate, evidence_root, "study-1", audit)

    assert caught.value is failure
    assert calls == 2
    assert not destination.exists()
    assert _tree_bytes(candidate) == before
    assert _temporary_siblings(evidence_root, "study-1") == []


def test_staged_audit_cleanup_failure_preserves_the_primary_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    rejection = _StagedAuditAbort("staged audit rejected the copied bundle")

    def audit(path: Path) -> None:
        if path != candidate:
            raise rejection

    def cleanup_failure(_path: Path) -> str:
        return "injected staging cleanup failure"

    monkeypatch.setattr(study_evidence, "_cleanup_temporary", cleanup_failure)

    with pytest.raises(_StagedAuditAbort) as caught:
        publish_accepted_bundle(candidate, tmp_path / "evidence", "study-1", audit)

    assert caught.value is rejection
    assert caught.value.__notes__ == ["temporary staging cleanup also failed: injected staging cleanup failure"]


def test_staged_audit_cleanup_base_exception_preserves_the_primary_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    rejection = _StagedAuditAbort("staged audit rejected the copied bundle")
    cleanup_abort = _CleanupAbort("injected cleanup interruption")

    def audit(path: Path) -> None:
        if path != candidate:
            raise rejection

    def interrupt_cleanup(_path: Path, *_args: object, **_kwargs: object) -> None:
        raise cleanup_abort

    monkeypatch.setattr(study_evidence.shutil, "rmtree", interrupt_cleanup)

    with pytest.raises(_StagedAuditAbort) as caught:
        publish_accepted_bundle(candidate, tmp_path / "evidence", "study-1", audit)

    assert caught.value is rejection
    assert caught.value.__notes__ == [
        "temporary staging cleanup also failed: _CleanupAbort: injected cleanup interruption"
    ]


def test_first_publication_fsyncs_parent_before_staging_and_root_after_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    operations: list[str] = []
    original_fsync_open_path = study_evidence._fsync_open_path  # pyright: ignore[reportPrivateUsage]
    original_fsync_tree = study_evidence._fsync_tree  # pyright: ignore[reportPrivateUsage]
    original_rename = study_evidence._rename_noreplace  # pyright: ignore[reportPrivateUsage]

    def record_fsync(path: Path, *, directory: bool) -> None:
        if path == evidence_root.parent:
            operations.append("parent_fsync")
        elif path == evidence_root:
            operations.append("root_fsync")
        original_fsync_open_path(path, directory=directory)

    def record_tree(root: Path) -> None:
        operations.append("tree_fsync")
        original_fsync_tree(root)

    def record_rename(source: Path, destination: Path) -> None:
        operations.append("rename")
        original_rename(source, destination)

    monkeypatch.setattr(study_evidence, "_fsync_open_path", record_fsync)
    monkeypatch.setattr(study_evidence, "_fsync_tree", record_tree)
    monkeypatch.setattr(study_evidence, "_rename_noreplace", record_rename)

    def audit(path: Path) -> None:
        operations.append("source_audit" if path == candidate else "staged_audit")

    publish_accepted_bundle(candidate, evidence_root, "study-1", audit)

    assert operations == ["source_audit", "parent_fsync", "tree_fsync", "staged_audit", "rename", "root_fsync"]


def test_post_rename_root_fsync_failure_reports_preserved_exact_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    destination = evidence_root / "study-1"
    expected = _tree_bytes(candidate)
    original_fsync_open_path = study_evidence._fsync_open_path  # pyright: ignore[reportPrivateUsage]

    def fail_root_fsync(path: Path, *, directory: bool) -> None:
        if path == evidence_root:
            raise OSError(errno.EIO, "injected post-rename evidence-root fsync failure")
        original_fsync_open_path(path, directory=directory)

    monkeypatch.setattr(study_evidence, "_fsync_open_path", fail_root_fsync)

    with pytest.raises(TrafficlabError) as error:
        publish_accepted_bundle(candidate, evidence_root, "study-1", lambda _path: None)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
        outcome.status,
    ) == ("publication_failed", "publication", "accepted evidence bundle", "preserved", "primary", None)
    assert outcome.detail == str(error.value)
    assert outcome.corrective_action == error.value.corrective_action
    assert getattr(error.value, "evidence_state", None) == "preserved"
    assert getattr(error.value, "destination", None) == destination
    assert error.value.corrective_action == (
        "preserve and validate the accepted destination; do not retry publication under the occupied study ID"
    )
    assert _tree_bytes(destination) == expected
    assert _temporary_siblings(evidence_root, "study-1") == []


def test_collision_preserves_its_immutable_outcome_when_staging_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    destination = evidence_root / "study-1"
    original_cleanup = study_evidence._cleanup_temporary  # pyright: ignore[reportPrivateUsage]

    def collide(_source: Path, _destination: Path) -> None:
        raise OSError(errno.EEXIST, "injected collision")

    def cleanup_with_diagnostic(path: Path) -> str | None:
        assert original_cleanup(path) is None
        return "injected staging cleanup failure"

    monkeypatch.setattr(study_evidence, "_rename_noreplace", collide)
    monkeypatch.setattr(study_evidence, "_cleanup_temporary", cleanup_with_diagnostic)

    with pytest.raises(TrafficlabError) as caught:
        publish_accepted_bundle(candidate, evidence_root, "study-1", lambda _path: None)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
        outcome.status,
    ) == (
        "publication_collision",
        "publication",
        "candidate accepted evidence bundle",
        "not_published",
        "primary",
        None,
    )
    assert outcome.detail == "accepted bundle already exists"
    assert outcome.corrective_action == "choose a new study ID"
    assert caught.value.__notes__ == ["temporary staging cleanup also failed: injected staging cleanup failure"]
    assert not destination.exists()
    assert list(evidence_root.glob(".*.tmp")) == []


def test_failed_audit_preserves_its_exception_and_publishes_nothing(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    rejection = RuntimeError("offline audit rejected candidate")

    def reject(_path: Path) -> None:
        raise rejection

    with pytest.raises(RuntimeError) as error:
        publish_accepted_bundle(candidate, evidence_root, "study-1", reject)

    assert error.value is rejection
    assert not evidence_root.exists()


@pytest.mark.parametrize("occupied_with_file", [False, True])
def test_occupied_study_id_is_never_replaced_or_merged(tmp_path: Path, occupied_with_file: bool) -> None:
    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    destination = evidence_root / "study-1"
    destination.mkdir(parents=True)
    if occupied_with_file:
        (destination / "accepted.txt").write_bytes(b"existing accepted bytes\n")
    before_inode = destination.stat().st_ino
    before = _tree_bytes(destination)
    audit_calls: list[Path] = []

    with pytest.raises(TrafficlabError, match="publication_collision") as error:
        publish_accepted_bundle(candidate, evidence_root, "study-1", audit_calls.append)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
        outcome.status,
    ) == (
        "publication_collision",
        "publication",
        "candidate accepted evidence bundle",
        "not_published",
        "primary",
        None,
    )
    assert outcome.detail == "accepted bundle already exists"
    assert outcome.corrective_action == "choose a new study ID"
    assert str(error.value).startswith("publication_collision: accepted evidence bundle already exists at ")
    assert error.value.corrective_action == "choose a new study ID; accepted evidence bundles are immutable"
    assert error.value.corrective_action == "choose a new study ID; accepted evidence bundles are immutable"
    assert len(audit_calls) == 2
    assert audit_calls[0] == candidate
    assert audit_calls[1].name == "study-1"
    assert destination.stat().st_ino == before_inode
    assert _tree_bytes(destination) == before
    assert _temporary_siblings(evidence_root, "study-1") == []


@pytest.mark.parametrize("failure_point", ["copy", "fsync", "rename"])
def test_copy_fsync_and_rename_failures_remove_the_owned_temporary_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    destination = evidence_root / "study-1"

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EIO, f"injected {failure_point} failure")

    failure_targets: dict[str, tuple[object, str]] = {
        "copy": (study_evidence.shutil, "copytree"),
        "fsync": (study_evidence, "_fsync_tree"),
        "rename": (study_evidence, "_rename_noreplace"),
    }
    target, attribute = failure_targets[failure_point]
    monkeypatch.setattr(target, attribute, fail)

    with pytest.raises(TrafficlabError, match="could not publish accepted evidence bundle"):
        publish_accepted_bundle(candidate, evidence_root, "study-1", lambda _path: None)

    assert not destination.exists()
    assert _temporary_siblings(evidence_root, "study-1") == []
    assert _tree_bytes(candidate)["manifest.json"] == b'{"files":[]}\n'


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("missing", None),
        ("oserror", "[Errno 5] injected cleanup I/O failure"),
        ("base_exception", "_CleanupAbort: injected cleanup interruption"),
    ),
)
def test_temporary_cleanup_reports_all_failure_classes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected: str | None,
) -> None:
    temporary = tmp_path / "temporary"
    if case == "missing":
        assert study_evidence._cleanup_temporary(temporary) is expected  # pyright: ignore[reportPrivateUsage]
        return

    temporary.mkdir()

    def fail_cleanup(_path: Path, *_args: object, **_kwargs: object) -> None:
        if case == "oserror":
            raise OSError(errno.EIO, "injected cleanup I/O failure")
        raise _CleanupAbort("injected cleanup interruption")

    monkeypatch.setattr(study_evidence.shutil, "rmtree", fail_cleanup)

    assert study_evidence._cleanup_temporary(temporary) == expected  # pyright: ignore[reportPrivateUsage]


def test_non_directory_evidence_root_fails_after_source_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    evidence_root.write_bytes(b"not a directory\n")
    audit_calls: list[Path] = []
    original_mkdir = Path.mkdir

    def preserve_non_directory(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        if path == evidence_root:
            return
        original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(study_evidence.Path, "mkdir", preserve_non_directory)

    with pytest.raises(TrafficlabError, match="could not publish accepted evidence bundle"):
        publish_accepted_bundle(candidate, evidence_root, "study-1", audit_calls.append)

    assert audit_calls == [candidate]


def test_staging_creation_oserror_preserves_the_source_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    before = _tree_bytes(candidate)

    def fail_mkdtemp(*_args: object, **_kwargs: object) -> str:
        raise OSError(errno.EIO, "injected staging creation failure")

    monkeypatch.setattr(study_evidence.tempfile, "mkdtemp", fail_mkdtemp)

    with pytest.raises(TrafficlabError, match="injected staging creation failure"):
        publish_accepted_bundle(candidate, evidence_root, "study-1", lambda _path: None)

    assert _tree_bytes(candidate) == before
    assert _temporary_siblings(evidence_root, "study-1") == []


def test_copy_oserror_cleanup_failure_preserves_diagnostic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _candidate(tmp_path)
    before = _tree_bytes(candidate)

    def fail_copy(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EIO, "injected copy failure")

    monkeypatch.setattr(study_evidence.shutil, "copytree", fail_copy)
    monkeypatch.setattr(study_evidence, "_cleanup_temporary", _injected_cleanup_failure)

    with pytest.raises(TrafficlabError, match="injected copy failure; temporary cleanup also failed"):
        publish_accepted_bundle(candidate, tmp_path / "evidence", "study-1", lambda _path: None)

    assert _tree_bytes(candidate) == before


def test_copy_base_exception_preserves_source_and_removes_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    interruption = _StagedAuditAbort("injected copy interruption")

    def interrupt_copy(*_args: object, **_kwargs: object) -> None:
        raise interruption

    monkeypatch.setattr(study_evidence.shutil, "copytree", interrupt_copy)

    with pytest.raises(_StagedAuditAbort) as caught:
        publish_accepted_bundle(candidate, evidence_root, "study-1", lambda _path: None)

    assert caught.value is interruption
    assert _temporary_siblings(evidence_root, "study-1") == []


def test_collision_cleanup_failure_preserves_the_occupied_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    destination = evidence_root / "study-1"
    destination.mkdir(parents=True)
    (destination / "accepted.txt").write_bytes(b"accepted\n")
    before = _tree_bytes(destination)
    monkeypatch.setattr(study_evidence, "_cleanup_temporary", _injected_cleanup_failure)

    with pytest.raises(TrafficlabError) as caught:
        publish_accepted_bundle(candidate, evidence_root, "study-1", lambda _path: None)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
        outcome.status,
    ) == (
        "publication_collision",
        "publication",
        "candidate accepted evidence bundle",
        "not_published",
        "primary",
        None,
    )
    assert outcome.detail == "accepted bundle already exists"
    assert outcome.corrective_action == "choose a new study ID"
    assert caught.value.__notes__ == ["temporary staging cleanup also failed: injected cleanup failure"]
    assert _tree_bytes(destination) == before


def test_rename_oserror_cleanup_failure_preserves_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)

    def fail_rename(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EIO, "injected rename failure")

    monkeypatch.setattr(study_evidence, "_rename_noreplace", fail_rename)
    monkeypatch.setattr(study_evidence, "_cleanup_temporary", _injected_cleanup_failure)

    with pytest.raises(TrafficlabError, match="injected rename failure; temporary cleanup also failed"):
        publish_accepted_bundle(candidate, tmp_path / "evidence", "study-1", lambda _path: None)


def test_post_rename_base_exception_preserves_destination_and_cleans_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    destination = evidence_root / "study-1"
    interruption = _StagedAuditAbort("injected post-rename interruption")
    original_rmdir = Path.rmdir

    def interrupt_staging_rmdir(path: Path) -> None:
        if path.parent == evidence_root and path.name.startswith(".study-1."):
            raise interruption
        original_rmdir(path)

    monkeypatch.setattr(study_evidence.Path, "rmdir", interrupt_staging_rmdir)

    with pytest.raises(_StagedAuditAbort) as caught:
        publish_accepted_bundle(candidate, evidence_root, "study-1", lambda _path: None)

    assert caught.value is interruption
    assert _tree_bytes(destination) == _tree_bytes(candidate)
    assert _temporary_siblings(evidence_root, "study-1") == []


def test_rename_base_exception_preserves_source_and_removes_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(tmp_path)
    evidence_root = tmp_path / "evidence"
    destination = evidence_root / "study-1"
    before = _tree_bytes(candidate)
    interruption = _StagedAuditAbort("injected publication interruption")

    def interrupt_rename(_source: Path, _destination: Path) -> None:
        raise interruption

    monkeypatch.setattr(study_evidence, "_rename_noreplace", interrupt_rename)

    with pytest.raises(_StagedAuditAbort) as caught:
        publish_accepted_bundle(candidate, evidence_root, "study-1", lambda _path: None)

    assert caught.value is interruption
    assert not destination.exists()
    assert _tree_bytes(candidate) == before
    assert _temporary_siblings(evidence_root, "study-1") == []


def test_audit_argument_must_be_callable(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)

    with pytest.raises(TypeError, match="audit must be callable"):
        publish_accepted_bundle(candidate, tmp_path / "evidence", "study-1", 0)  # type: ignore[arg-type]


def test_safe_study_id_accepts_the_documented_validation_study_shape(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)

    destination = publish_accepted_bundle(
        candidate,
        tmp_path / "evidence",
        "validation-study-20260815-r1",
        lambda _path: None,
    )

    assert destination.name == "validation-study-20260815-r1"
