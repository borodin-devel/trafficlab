"""Publication behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

import scripts.validation_study.audit.environment as vs_audit_environment
import scripts.validation_study.fixture as vs_fixture
import scripts.validation_study.results.reproduction as vs_results_reproduction
from tests.fixtures.paths import VALIDATION_STUDY_CANDIDATE
from tests.support.validation_study.repository import (
    copy_validation_study_candidate,
)
from tests.unit.validation.study.audit._audit_support import (
    candidate_bytes,
)
from trafficlab.common.errors import TrafficlabError


def test_generated_validation_study_template_restores_an_independent_candidate(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    """Generated mutation candidates restore immutable template bytes between tests."""

    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    original_environment = (candidate / "environment.json").read_bytes()
    assert original_environment == (generated_validation_study_candidate_template / "environment.json").read_bytes()
    (candidate / "environment.json").write_bytes(b"mutated test candidate\n")

    next_repository, next_candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )

    assert next_repository == repository
    assert next_candidate == candidate
    assert (next_candidate / "environment.json").read_bytes() == original_environment


def test_audited_bundle_publication_rechecks_candidate_and_preserves_an_occupied_destination(tmp_path: Path) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    evidence_root = repository / "examples" / "validation_study" / "evidence"

    destination = vs_results_reproduction.publish_audited_bundle(candidate, "fixture-study", repository_root=repository)
    before = candidate_bytes(destination)
    root_before = candidate_bytes(repository)

    with pytest.raises(TrafficlabError) as error:
        vs_results_reproduction.publish_audited_bundle(candidate, "fixture-study", repository_root=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "publication_collision"
    assert outcome.stage == "publication"
    assert destination == evidence_root / "fixture-study"
    assert candidate_bytes(destination) == before
    assert candidate_bytes(repository) == root_before
    assert not tuple(repository.rglob("*.tmp"))


def test_audited_bundle_rejects_the_first_primary_without_publication_residue(tmp_path: Path) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    before = candidate_bytes(candidate)
    evidence_root = repository / "examples" / "validation_study" / "evidence"
    before_evidence = candidate_bytes(evidence_root)
    missing = "protocol.json"
    (candidate / missing).unlink()
    expected_candidate = dict(before)
    del expected_candidate[missing]

    with pytest.raises(TrafficlabError) as error:
        vs_results_reproduction.publish_audited_bundle(candidate, "fixture-study", repository_root=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
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
        "protocol.json is missing from the retained bundle",
        "protocol.json",
        "not_published",
        "restore the exact retained artifact",
        "primary",
    )
    assert error.value.failure_outcomes == (outcome,)
    assert candidate_bytes(candidate) == expected_candidate
    assert candidate_bytes(evidence_root) == before_evidence
    assert not (evidence_root / "fixture-study").exists()
    assert not tuple(repository.rglob("*.tmp"))


def test_validation_fixture_generator_rejects_nonhex_source_identities() -> None:
    with pytest.raises(ValueError, match="source identities"):
        vs_fixture.generate_fixture_tree(source_commit="z" * 40, source_tree="f" * 40)


@pytest.mark.parametrize(
    ("source_commit", "source_tree", "accepted"),
    (
        ("a" * 40, "b" * 40, True),
        ("z" * 40, "b" * 40, False),
        ("a" * 40, "z" * 40, False),
        ("0" * 40, "b" * 40, False),
        ("a" * 40, "0" * 40, False),
    ),
)
def test_validation_fixture_source_identity_guard_has_exact_acceptance_boundaries(
    source_commit: str,
    source_tree: str,
    accepted: bool,
) -> None:
    if accepted:
        vs_audit_environment.validate_source_identities(source_commit, source_tree)
    else:
        with pytest.raises(ValueError, match="source identities"):
            vs_audit_environment.validate_source_identities(source_commit, source_tree)


def test_validation_fixture_generator_check_rebuilds_the_retained_bytes() -> None:
    assert vs_fixture.main(["--check"]) == 0


def test_validation_fixture_generator_check_honors_explicit_source_identities() -> None:
    environment = cast(
        dict[str, object],
        json.loads((VALIDATION_STUDY_CANDIDATE / "environment.json").read_text()),
    )
    source_commit = cast(str, environment["source_commit"])
    alternate_commit = "a" * 40 if source_commit != "a" * 40 else "b" * 40

    assert (
        vs_fixture.main(
            [
                "--check",
                "--source-commit",
                alternate_commit,
                "--source-tree",
                cast(str, environment["source_tree"]),
            ]
        )
        == 1
    )


def test_validation_fixture_generator_main_requires_complete_ids_and_writes_to_its_owned_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = cast(
        dict[str, object],
        json.loads((VALIDATION_STUDY_CANDIDATE / "environment.json").read_text()),
    )
    source_commit = cast(str, environment["source_commit"])
    source_tree = cast(str, environment["source_tree"])
    with pytest.raises(TrafficlabError, match="requires explicit source"):
        vs_fixture.main([])
    with pytest.raises(TrafficlabError, match="requires explicit source"):
        vs_fixture.main(["--check", "--source-commit", source_commit])

    output = tmp_path / "owned-fixture"
    monkeypatch.setattr(vs_fixture, "FIXTURE", output)
    assert vs_fixture.main(["--source-commit", source_commit, "--source-tree", source_tree]) == 0
    assert len(candidate_bytes(output)) == 232
