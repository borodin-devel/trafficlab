"""Repository owner for Validation Study tooling."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from scripts.validation_study.common import JsonObject, thaw_json
from scripts.validation_study.fixture import generate_fixture_tree
from scripts.validation_study.prerequisites.codec import render_prerequisite_results
from tests.conftest import retained_test_body_failure
from tests.fixtures.paths import VALIDATION_STUDY_CANDIDATE
from tests.support.validation_study.artifacts import write_retained_prerequisite_evidence
from tests.support.validation_study.builders import (
    frozen,
    study_result_value,
    valid_result_document,
    write_checked_configs,
)
from tests.support.validation_study.constants import CAPTURE_IMAGE_ID, REAL_SUBPROCESS_RUN, ROOT

if TYPE_CHECKING:
    from scripts.validation_study.records import StudyResults

_shared_validation_study_repository_path: Path | None = None

_current_validation_study_test_name: str | None = None

_current_isolated_validation_study_worktrees: list[Path] | None = None

ISOLATED_VALIDATION_STUDY_REPOSITORY_TESTS = frozenset(
    {
        "test_audited_bundle_publication_rechecks_candidate_and_preserves_an_occupied_destination",
        "test_audited_bundle_rejects_the_first_primary_without_publication_residue",
        "test_offline_auditor_allows_a_clean_committed_accepted_bundle",
        "test_offline_auditor_allows_document_evidence_and_ignored_candidate_worktree_changes",
        "test_offline_auditor_checks_the_worktree_before_committed_descendant_changes",
        "test_offline_auditor_classifies_ignored_special_entry_git_failures",
        "test_offline_auditor_rejects_environment_binding_after_the_first_identity_check",
        "test_offline_auditor_rejects_local_exclude_ignored_non_evidence_entries",
        "test_offline_auditor_rejects_non_evidence_worktree_changes",
        "test_offline_auditor_rejects_untracked_nonregular_source_paths",
        "test_offline_auditor_rejects_untrusted_fixture_profile_source_bytes",
        "test_offline_bundle_audit_reconstructs_environment_and_final_controls",
        "test_simultaneous_evidence_mismatches_preserve_the_first_complete_primary_and_all_inventories",
    }
)


def write_study_inputs(repository_root: Path) -> tuple[Path, StudyResults]:
    repository_root.mkdir()
    prerequisite, _contents = write_checked_configs(repository_root, capture_image_id=CAPTURE_IMAGE_ID)
    images = cast(JsonObject, thaw_json(prerequisite.images))
    images["capture_image_id"] = CAPTURE_IMAGE_ID
    prerequisite = write_retained_prerequisite_evidence(repository_root, replace(prerequisite, images=frozen(images)))
    capture_root = repository_root / "docker" / "capture"
    shutil.copy2(ROOT / "docker" / "capture" / "image-lock.json", capture_root / "image-lock.json")
    prerequisite_path = repository_root / "examples" / "validation_study" / "prerequisites.json"
    prerequisite_path.write_bytes(render_prerequisite_results(prerequisite))
    document = valid_result_document(repository_root)
    return (prerequisite_path, study_result_value(document))


def validation_study_fixture_identity() -> tuple[str, str]:
    source_environment = cast(
        dict[str, object], json.loads((VALIDATION_STUDY_CANDIDATE / "environment.json").read_text(encoding="utf-8"))
    )
    return (cast(str, source_environment["source_commit"]), cast(str, source_environment["source_tree"]))


def validation_study_request_test_name(request: pytest.FixtureRequest) -> str:
    """Resolve the stable base test name used to select a shared or isolated checkout."""
    node = cast(Any, request).node
    return cast(str, node.originalname or node.name)


def _add_validation_study_worktree(repository: Path, source_commit: str) -> None:
    REAL_SUBPROCESS_RUN(
        ("git", "worktree", "add", "--detach", str(repository), source_commit),
        cwd=ROOT,
        check=True,
        capture_output=True,
    )


def remove_validation_study_worktree(repository: Path) -> None:
    REAL_SUBPROCESS_RUN(
        ("git", "worktree", "remove", "--force", str(repository)), cwd=ROOT, check=True, capture_output=True
    )


def finish_validation_study_worktree_cleanup(
    repositories: Sequence[Path],
    *,
    body_error: BaseException | None,
    remove: Callable[[Path], None] = remove_validation_study_worktree,
) -> None:
    """Remove all owned checkouts while retaining a prior body failure and cleanup diagnostics."""
    cleanup_errors: list[BaseException] = []
    for repository in reversed(repositories):
        try:
            remove(repository)
        except BaseException as error:
            cleanup_errors.append(error)
    if not cleanup_errors:
        return
    if body_error is not None:
        raise BaseExceptionGroup(
            "validation-study test body and detached-checkout cleanup both failed", (body_error, *cleanup_errors)
        ) from None
    if len(cleanup_errors) == 1:
        raise cleanup_errors[0]
    raise BaseExceptionGroup("validation-study detached-checkout cleanup failed", cleanup_errors)


@pytest.fixture(scope="session")
def shared_validation_study_repository(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Provide one detached source checkout per pytest worker for candidate-only audits."""
    global _shared_validation_study_repository_path
    source_commit, _source_tree = validation_study_fixture_identity()
    repository = tmp_path_factory.mktemp("validation-study-checkout") / "repository"
    _add_validation_study_worktree(repository, source_commit)
    _shared_validation_study_repository_path = repository
    try:
        yield repository
    finally:
        _shared_validation_study_repository_path = None
        remove_validation_study_worktree(repository)


@pytest.fixture(autouse=True)
def validation_study_candidate_context(
    request: pytest.FixtureRequest, shared_validation_study_repository: Path
) -> Iterator[None]:
    """Track the active test so source-mutating audits retain isolated checkouts."""
    del shared_validation_study_repository
    global _current_isolated_validation_study_worktrees, _current_validation_study_test_name
    _current_isolated_validation_study_worktrees = []
    _current_validation_study_test_name = validation_study_request_test_name(request)
    try:
        yield
    finally:
        repositories = _current_isolated_validation_study_worktrees
        _current_isolated_validation_study_worktrees = None
        _current_validation_study_test_name = None
        assert repositories is not None
        finish_validation_study_worktree_cleanup(repositories, body_error=retained_test_body_failure(request))


@pytest.fixture(scope="session")
def generated_validation_study_candidate_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate one immutable candidate template per pytest worker."""
    source_commit, source_tree = validation_study_fixture_identity()
    template = tmp_path_factory.mktemp("validation-study-generated-template") / "fixture-study"
    for relative, content in generate_fixture_tree(source_commit=source_commit, source_tree=source_tree).items():
        path = template / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return template


def copy_validation_study_candidate(tmp_path: Path, *, generated_template: Path | None = None) -> tuple[Path, Path]:
    source_commit, _source_tree = validation_study_fixture_identity()
    shared_repository = _shared_validation_study_repository_path
    if (
        shared_repository is not None
        and _current_validation_study_test_name not in ISOLATED_VALIDATION_STUDY_REPOSITORY_TESTS
    ):
        repository = shared_repository
    else:
        repository = tmp_path / "relocated-repository"
        _add_validation_study_worktree(repository, source_commit)
        repositories = _current_isolated_validation_study_worktrees
        assert repositories is not None
        repositories.append(repository)
    candidate = repository / "fixture-study"
    if candidate.exists():
        shutil.rmtree(candidate)
    if generated_template is not None:
        shutil.copytree(generated_template, candidate, copy_function=shutil.copy2)
    else:
        shutil.copytree(VALIDATION_STUDY_CANDIDATE, candidate)
    return (repository, candidate)
