from __future__ import annotations

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

from trafficlab.study_evidence.protocol import (
    StudyContentIdentity,
    ValidationStudyEnvironment,
    ValidationStudyLifecycle,
    ValidationStudyLineage,
    ValidationStudyManifest,
    ValidationStudyPrerequisite,
    ValidationStudyProtocol,
    validate_study_model,
)
from trafficlab.study_evidence.report import (
    ValidationStudyReport,
    ValidationStudyReportInput,
)

REPOSITORY = Path(__file__).resolve().parents[4]
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
    return (_STUDY_FIXTURE / filename,)


@pytest.mark.integration
def test_retained_schema_v4_scapy_production_study_passes_its_source_bound_offline_audit(tmp_path: Path) -> None:
    """The navigated historical study remains verifiable only by its recorded schema-v4 source."""

    environment = cast(dict[str, object], json.loads((_CURRENT_STUDY / "environment.json").read_bytes()))
    repository = tmp_path / "recorded-source"
    subprocess.run(
        ("git", "clone", "--no-local", "--no-hardlinks", "--no-checkout", str(REPOSITORY), str(repository)),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ("git", "checkout", "--detach", cast(str, environment["source_commit"])),
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
            "--active",
            "--no-project",
            "python",
            "scripts/audit_validation_study.py",
            copied.relative_to(repository).as_posix(),
            "--repository",
            ".",
        ),
        cwd=repository,
        env={**os.environ, "PYTHONPATH": str(repository / "src"), "UV_OFFLINE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert environment["scientific_artifact_schema"] == 4
    with pytest.raises(ValidationError):
        ValidationStudyEnvironment.model_validate(environment)
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
        assert len(paths) == 1, name
        for path in paths:
            content = path.read_bytes()
            document = json.loads(content)
            Draft202012Validator(schema).validate(document)  # pyright: ignore[reportUnknownMemberType]
            rendered = model.model_validate(document).model_dump(mode="json")
            assert rendered == document, path
            if path.is_relative_to(_STUDY_FIXTURE):
                expected = json.dumps(rendered, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
            else:
                expected = json.dumps(
                    rendered, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
                )
            assert expected.encode() + b"\n" == content, path


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
