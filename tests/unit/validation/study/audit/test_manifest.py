"""Manifest behavior."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest

import scripts.validation_study.audit.artifacts as vs_audit_artifacts
import scripts.validation_study.audit.common as vs_audit_common
import scripts.validation_study.audit.lifecycle as vs_audit_lifecycle
from tests.fixtures.paths import VALIDATION_STUDY_CANDIDATE
from tests.support.validation_study.artifacts import (
    candidate_index,
    rewrite_candidate_manifest,
    write_candidate_index,
    write_canonical_json,
)
from tests.support.validation_study.repository import copy_validation_study_candidate
from tests.unit.validation.study.audit._audit_support import (
    candidate_bytes,
)
from trafficlab.common.errors import TrafficlabError


@pytest.mark.parametrize(
    ("mutation", "expected_kind"),
    (
        ("missing", "artifact_missing"),
        ("corrupt", "artifact_corrupt"),
        ("foreign", "artifact_foreign"),
        ("extra", "artifact_foreign"),
        ("symlink", "artifact_foreign"),
        ("temporary", "artifact_foreign"),
        ("owner", "artifact_foreign"),
        ("lineage", "artifact_corrupt"),
    ),
)
def test_offline_bundle_audit_rejects_first_manifest_or_artifact_mismatch(
    tmp_path: Path,
    mutation: str,
    expected_kind: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)

    if mutation == "missing":
        (candidate / "training" / "short" / "r1" / "best_model.json").unlink()
    elif mutation == "corrupt":
        path = candidate / "training" / "short" / "r1" / "checkpoint.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "foreign":
        target = candidate / "training" / "short" / "r1" / "generated.pcapng"
        target.write_bytes((candidate / "training" / "short" / "r1" / "reference.pcapng").read_bytes())
        rewrite_candidate_manifest(candidate)
    elif mutation == "extra":
        (candidate / "unexpected.bin").write_bytes(b"unexpected")
    elif mutation == "symlink":
        (candidate / "training" / "short" / "r1" / "unexpected-link").symlink_to("generated.pcapng")
    elif mutation == "temporary":
        (candidate / "training" / "short" / "r1" / ".generated.tmp").write_bytes(b"temporary")
    else:
        index_path = candidate / "index.json"
        index = cast(dict[str, object], json.loads(index_path.read_text(encoding="utf-8")))
        relative = "training/short/r1/generated.pcapng"
        mapping_name = "ownership" if mutation == "owner" else "lineage"
        mapping = cast(dict[str, object], index[mapping_name])
        mapping[relative] = f"changed-{mutation}"
        write_canonical_json(index_path, index)
        rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == expected_kind
    assert outcome.stage == "publication"
    assert outcome.evidence_state == "not_published"
    assert outcome.authority == "primary"
    assert error.value.failure_outcomes == (outcome,)
    if mutation == "missing":
        assert (
            outcome.kind,
            outcome.stage,
            outcome.detail,
            outcome.affected_evidence,
            outcome.evidence_state,
            outcome.corrective_action,
            outcome.authority,
        ) == (
            "artifact_missing",
            "publication",
            "training/short/r1/best_model.json is missing from the retained bundle",
            "training/short/r1/best_model.json",
            "not_published",
            "restore the exact retained artifact",
            "primary",
        )


@pytest.mark.parametrize(
    "binding",
    vs_audit_common.TRANSFER_BINDINGS,
    ids=lambda binding: f"{binding.scope}-{binding.run_id}-{binding.transfer_index}",
)
@pytest.mark.parametrize("kind", ("header", "observation"))
def test_offline_bundle_audit_rejects_each_scoped_transfer_file(
    tmp_path: Path,
    binding: vs_audit_common._Transfer,  # pyright: ignore[reportPrivateUsage]
    kind: str,
) -> None:
    """Every prerequisite, training, and held-out transfer is an independently retained audit input."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    if kind == "header":
        relative = f"headers/{binding.scope}/{binding.run_id}/{binding.filename}"
        path = candidate / relative
        path.write_bytes(path.read_bytes().replace(b"206", b"205", 1))
    else:
        relative = f"observations/{binding.scope}/{binding.run_id}/{binding.filename}.json"
        path = candidate / relative
        document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        document["status"] = 205
        write_canonical_json(path, document)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.affected_evidence == relative


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (
        ("schema", "scientific_semantics_incompatible"),
        ("root_path", "artifact_foreign"),
        ("training_type", "artifact_corrupt"),
        ("training_count", "artifact_corrupt"),
        ("training_duplicate", "artifact_foreign"),
        ("fresh_type", "artifact_corrupt"),
        ("fresh_count", "artifact_corrupt"),
        ("held_type", "artifact_corrupt"),
        ("held_count", "artifact_corrupt"),
        ("held_duplicate", "artifact_foreign"),
        ("report_inputs", "artifact_corrupt"),
        ("report", "artifact_corrupt"),
    ),
)
def test_offline_bundle_audit_covers_complete_index_schema_boundaries(
    tmp_path: Path,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    index = candidate_index(candidate)
    if case == "schema":
        index["schema_version"] = 1
        write_candidate_index(candidate, index)
    elif case == "root_path":
        index["report"] = "other-report.json"
        write_candidate_index(candidate, index)
    elif case == "training_type":
        index["training"] = {}
        write_candidate_index(candidate, index)
    elif case == "training_count":
        index["training"] = cast(list[object], index["training"])[:-1]
        write_candidate_index(candidate, index)
    elif case == "training_duplicate":
        training = cast(list[dict[str, object]], index["training"])
        training[-1] = copy.deepcopy(training[0])
        write_candidate_index(candidate, index)
    elif case == "fresh_type":
        index["fresh_simulation"] = {}
        write_candidate_index(candidate, index)
    elif case == "fresh_count":
        index["fresh_simulation"] = cast(list[object], index["fresh_simulation"])[:-1]
        write_candidate_index(candidate, index)
    elif case == "held_type":
        index["held_out"] = {}
        write_candidate_index(candidate, index)
    elif case == "held_count":
        index["held_out"] = cast(list[object], index["held_out"])[:-1]
        write_candidate_index(candidate, index)
    elif case == "held_duplicate":
        held = cast(list[dict[str, object]], index["held_out"])
        held[1] = copy.deepcopy(held[0])
        write_candidate_index(candidate, index)
    elif case == "report_inputs":
        path = candidate / "report_inputs.json"
        document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        document["formula"] = "not-arithmetic"
        write_canonical_json(path, document)
    else:
        path = candidate / "report.json"
        document = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
        document["formula"] = "not-arithmetic"
        write_canonical_json(path, document)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "not_published",
        "primary",
    )


@pytest.mark.parametrize(
    ("case", "expected_kind"),
    (("wrong_type", "artifact_corrupt"), ("manifest_disagreement", "artifact_foreign")),
)
def test_offline_bundle_audit_validates_index_metadata_before_scientific_reconstruction(
    tmp_path: Path,
    case: str,
    expected_kind: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    index = candidate_index(candidate)
    ownership = copy.deepcopy(cast(dict[str, str], index["ownership"]))
    lineage = copy.deepcopy(cast(dict[str, object], index["lineage"]))
    if case == "wrong_type":
        index["ownership"] = []
    else:
        cast(dict[str, object], index["ownership"])["training/short/r1/generated.pcapng"] = "wrong-owner"
    write_candidate_index(candidate, index)
    vs_audit_artifacts.write_manifest(candidate, ownership=ownership, lineage=lineage)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence) == (expected_kind, "index.json")


def test_schema_manifest_writer_rejects_incomplete_keys_and_empty_owner(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "retained.bin").write_bytes(b"retained")

    with pytest.raises(ValueError, match="keys must equal"):
        vs_audit_artifacts.write_manifest(candidate, ownership={}, lineage={})
    with pytest.raises(ValueError, match="nonempty string"):
        vs_audit_artifacts.write_manifest(candidate, ownership={"retained.bin": ""}, lineage={"retained.bin": {}})


def test_validation_fixture_retains_the_complete_232_file_evidence_inventory() -> None:
    assert len(candidate_bytes(VALIDATION_STUDY_CANDIDATE)) == 232
