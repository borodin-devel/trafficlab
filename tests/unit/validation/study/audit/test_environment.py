"""Environment behavior."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import scripts.validation_study.audit.common as vs_audit_common
import scripts.validation_study.audit.environment as vs_audit_environment
import scripts.validation_study.audit.lifecycle as vs_audit_lifecycle
import scripts.validation_study.cli as vs_cli
from tests.fixtures.paths import VALIDATION_STUDY_CANDIDATE
from tests.support.validation_study.artifacts import (
    rewrite_candidate_manifest,
    write_canonical_json,
)
from tests.support.validation_study.constants import ROOT
from tests.support.validation_study.repository import (
    ISOLATED_VALIDATION_STUDY_REPOSITORY_TESTS,
    copy_validation_study_candidate,
    finish_validation_study_worktree_cleanup,
    remove_validation_study_worktree,
    validation_study_request_test_name,
)
from tests.unit.validation.study.audit._audit_support import (
    VALIDATION_STUDY_LOCAL_EXCLUDE_LOCK,
    candidate_bytes,
    exclusive_validation_study_file_lock,
    finish_validation_study_exclude_restore,
)
from trafficlab.common.errors import TrafficlabError


def test_relocated_audit_candidate_uses_a_detached_git_worktree(tmp_path: Path) -> None:
    """Repeated unit audits use a real checkout without duplicating repository objects."""
    repository, _candidate = copy_validation_study_candidate(tmp_path)
    source_environment = cast(
        dict[str, object],
        json.loads((VALIDATION_STUDY_CANDIDATE / "environment.json").read_text(encoding="utf-8")),
    )
    assert (repository / ".git").is_file()
    assert (
        subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == source_environment["source_commit"]
    )


def test_shared_validation_study_checkout_refreshes_each_candidate(
    tmp_path: Path, shared_validation_study_repository: Path
) -> None:
    """Candidate-only audits reuse one worker checkout without retaining prior candidate bytes."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    assert repository == shared_validation_study_repository
    (candidate / "foreign.txt").write_text("test-only residue\n", encoding="utf-8")

    next_repository, next_candidate = copy_validation_study_candidate(tmp_path)

    assert next_repository == repository
    assert next_candidate == candidate
    assert not (next_candidate / "foreign.txt").exists()


def test_validation_study_worktree_removal_propagates_cleanup_failure(tmp_path: Path) -> None:
    """Detached-checkout cleanup must not silently retain Git administration state."""

    with pytest.raises(subprocess.CalledProcessError):
        remove_validation_study_worktree(tmp_path / "not-a-worktree")


def test_validation_study_worktree_cleanup_preserves_a_primary_failure() -> None:
    """A failing finalizer adds its diagnostic without erasing the body failure."""

    primary = RuntimeError("primary test failure")
    cleanup = OSError("synthetic worktree cleanup failure")

    def cleanup_failure(_repository: Path) -> None:
        raise cleanup

    with pytest.raises(BaseExceptionGroup) as captured:
        finish_validation_study_worktree_cleanup(
            (Path("owned-worktree"),),
            body_error=primary,
            remove=cleanup_failure,
        )

    assert captured.value.exceptions == (primary, cleanup)


@pytest.mark.parametrize("case", ("tracked_auditor", "tracked_source", "untracked_source"))
def test_offline_auditor_rejects_non_evidence_worktree_changes(
    tmp_path: Path,
    case: str,
) -> None:
    """The accepted audit cannot trust a checkout with mutable auditor or source inputs."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    if case == "tracked_auditor":
        changed = repository / "scripts" / "audit_validation_study.py"
        changed.write_bytes(changed.read_bytes() + b"\n# dirty auditor\n")
    elif case == "tracked_source":
        changed = repository / "src" / "trafficlab" / "comparison.py"
        changed.write_bytes(changed.read_bytes() + b"\n# dirty source\n")
    else:
        (repository / "untracked_source.py").write_text("sentinel = True\n", encoding="utf-8")

    with pytest.raises(TrafficlabError) as captured:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", "environment", "not_published", "primary")
    assert "working-tree" in outcome.detail


def test_offline_auditor_allows_document_test_and_evidence_worktree_changes(tmp_path: Path) -> None:
    """The source guard permits non-production test changes and retained evidence."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    for relative in (
        "examples/validation_study/README.md",
        "examples/validation_study/REPORT.md",
        "tests/fixtures/data/manifest.json",
        "tests/fixtures/data/validation_study/candidate/environment.json",
    ):
        path = repository / relative
        path.write_bytes(path.read_bytes() + b"\nlocal audit note\n")
    evidence_note = repository / "examples" / "validation_study" / "evidence" / "local-audit-note.txt"
    evidence_note.parent.mkdir(parents=True, exist_ok=True)
    evidence_note.write_text("retained evidence note\n", encoding="utf-8")
    for relative in (
        "examples/validation_study/.study-work/attempts/fixture-study/state.json",
        "examples/validation_study/evidence/.candidates/fixture-study/state.json",
    ):
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    if hasattr(os, "mkfifo"):
        ignored_fifo = (
            repository / "examples" / "validation_study" / ".study-work" / "attempts" / "fixture-study" / "state.fifo"
        )
        os.mkfifo(ignored_fifo)

    status = subprocess.run(
        ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    assert b"examples/validation_study/README.md" in status
    assert b"examples/validation_study/evidence/local-audit-note.txt" in status
    assert b".study-work" not in status
    assert b".candidates" not in status
    assert vs_audit_lifecycle.audit_bundle(candidate, repository=repository).bundle == candidate


@pytest.mark.parametrize("entry_kind", ("regular", "symlink", "fifo"))
def test_offline_auditor_rejects_local_exclude_ignored_non_evidence_entries(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    """Local Git exclusion cannot exempt a source entry from the relocated audit boundary."""

    if entry_kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("nonregular FIFO entries require POSIX")
    repository, candidate = copy_validation_study_candidate(tmp_path)
    relative = f"locally-excluded-{entry_kind}"
    source = repository / relative
    exclude_value = subprocess.run(
        ("git", "rev-parse", "--git-path", "info/exclude"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    exclude = Path(exclude_value)
    if not exclude.is_absolute():
        exclude = repository / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_validation_study_file_lock(VALIDATION_STUDY_LOCAL_EXCLUDE_LOCK):
        original_exclude = exclude.read_bytes() if exclude.exists() else None
        body_error: BaseException | None = None
        try:
            with exclude.open("a", encoding="utf-8") as stream:
                stream.write(f"{relative}\n")
            if entry_kind == "regular":
                source.write_text("ignored foreign source\n", encoding="utf-8")
            elif entry_kind == "symlink":
                source.symlink_to("scripts/audit_validation_study.py")
            else:
                os.mkfifo(source)

            status = subprocess.run(
                ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
                cwd=repository,
                check=True,
                capture_output=True,
            ).stdout
            assert relative.encode("utf-8") not in status
            with pytest.raises(TrafficlabError) as captured:
                vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

            outcome = captured.value.failure_outcome
            assert outcome is not None
            assert (
                outcome.kind,
                outcome.stage,
                outcome.detail,
                outcome.affected_evidence,
                outcome.evidence_state,
                outcome.authority,
            ) == (
                "artifact_foreign",
                "publication",
                f"relocated checkout contains non-evidence working-tree change: {relative}",
                "environment",
                "not_published",
                "primary",
            )
        except BaseException as error:
            body_error = error
            raise
        finally:

            def restore() -> None:
                if original_exclude is None:
                    exclude.unlink(missing_ok=True)
                else:
                    exclude.write_bytes(original_exclude)

            finish_validation_study_exclude_restore(body_error=body_error, restore=restore)


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        (b"broken\0", "working-tree status"),
        (b"?? untracked_source.py", "working-tree status"),
        (b"?? " + bytes((255, 0)), "working-tree path is not UTF-8"),
        (bytes((255, 63, 32)) + b"source.py\0", "working-tree status is not ASCII"),
        (b"!! source.py\0", "working-tree status is malformed"),
        (b"?? /source.py\0", "working-tree path is not repository-relative"),
        (b"?? ../source.py\0", "working-tree path is not repository-relative"),
        (b"?? \0", "working-tree status is malformed"),
    ),
)
def test_offline_auditor_rejects_malformed_worktree_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: bytes,
    expected: str,
) -> None:
    """Git-status decoding is itself canonical audit evidence, not a best-effort hint."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    original_git_bytes = vs_audit_environment.git_bytes

    def malformed_status(repository: Path, argv: tuple[str, ...], *, name: str) -> bytes:
        if name == "relocated Git working tree":
            return status
        return original_git_bytes(repository, argv, name=name)

    monkeypatch.setattr(vs_audit_environment, "git_bytes", malformed_status)

    with pytest.raises(TrafficlabError) as captured:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_corrupt",
        "environment",
        "not_published",
    )
    assert expected in outcome.detail


@pytest.mark.parametrize(("case", "expected_kind"), (("oserror", "artifact_corrupt"), ("nonzero", "artifact_foreign")))
def test_offline_auditor_classifies_worktree_git_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_kind: str,
) -> None:
    """The worktree inspection retains the existing Git failure taxonomy."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    original_run = subprocess.run

    def worktree_failure(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        if command == ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"):
            if case == "oserror":
                raise OSError("synthetic status failure")
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"synthetic status failure\n")
        return cast(Any, original_run)(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", worktree_failure)

    with pytest.raises(TrafficlabError) as captured:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence, outcome.evidence_state) == (
        expected_kind,
        "environment",
        "not_published",
    )


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    (
        (1, b"", frozenset[str]()),
        (0, b"foreign.fifo\0", frozenset({"foreign.fifo"})),
    ),
)
def test_offline_auditor_exactly_parses_terminal_nul_ignored_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: bytes,
    expected: frozenset[str],
) -> None:
    """The Git NUL protocol has explicit empty and exactly-delimited records."""

    repository = tmp_path / "repository"
    repository.mkdir()
    calls: list[tuple[tuple[str, ...], bytes]] = []

    def check_ignore(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        calls.append((command, cast(bytes, kwargs["input"])))
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=b"")

    monkeypatch.setattr(subprocess, "run", check_ignore)

    assert (
        vs_audit_environment._ignored_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            ("foreign.fifo",),
        )
        == expected
    )
    assert calls == [(("git", "check-ignore", "-z", "--stdin"), b"foreign.fifo\0")]


def test_offline_auditor_rejects_empty_match_ignored_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Git's match status must include an exact ignored-path record."""

    repository = tmp_path / "repository"
    repository.mkdir()

    def inconsistent_match(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        assert kwargs["input"] == b"foreign.fifo\0"
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", inconsistent_match)

    with pytest.raises(vs_audit_common.Issue, match="must be nonempty for match status") as captured:
        vs_audit_environment._ignored_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            ("foreign.fifo",),
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_corrupt", "environment")


def test_offline_auditor_rejects_nonempty_no_match_ignored_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Git's no-match status cannot carry a record that exempts a special entry."""

    repository = tmp_path / "repository"
    repository.mkdir()

    def inconsistent_no_match(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        assert kwargs["input"] == b"foreign.fifo\0"
        return subprocess.CompletedProcess(command, 1, stdout=b"foreign.fifo\0", stderr=b"")

    monkeypatch.setattr(subprocess, "run", inconsistent_no_match)

    with pytest.raises(vs_audit_common.Issue, match="must be empty for no-match status") as captured:
        vs_audit_environment._ignored_relocated_worktree_paths(  # pyright: ignore[reportPrivateUsage]
            repository,
            ("foreign.fifo",),
        )

    assert (captured.value.kind, captured.value.affected) == ("artifact_corrupt", "environment")


@pytest.mark.parametrize(
    ("case", "expected_kind", "expected"),
    (
        ("oserror", "artifact_corrupt", "could not inspect relocated Git ignored paths"),
        ("nonzero", "artifact_foreign", "could not resolve ignored paths"),
        ("non_utf8", "artifact_corrupt", "relocated Git ignored path is not UTF-8"),
        ("foreign_path", "artifact_corrupt", "ignored paths do not match"),
        ("truncated", "artifact_corrupt", "ignored paths must be terminal NUL-delimited"),
        ("duplicate", "artifact_corrupt", "ignored paths must be unique"),
        ("nonempty_no_match", "artifact_corrupt", "ignored paths must be empty for no-match status"),
        ("empty_match", "artifact_corrupt", "ignored paths must be nonempty for match status"),
    ),
)
def test_offline_auditor_classifies_ignored_special_entry_git_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_kind: str,
    expected: str,
) -> None:
    """The special-entry ignore query remains a strict Git audit boundary."""

    if not hasattr(os, "mkfifo"):
        pytest.skip("nonregular FIFO entries require POSIX")
    repository, candidate = copy_validation_study_candidate(tmp_path)
    source = repository / "foreign.fifo"
    os.mkfifo(source)
    original_run = subprocess.run

    def ignored_path_failure(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        command = tuple(cast(Sequence[str], args[0]))
        if command == ("git", "check-ignore", "-z", "--stdin"):
            if case == "oserror":
                raise OSError("synthetic ignored-path failure")
            if case == "nonzero":
                return subprocess.CompletedProcess(command, 2, stdout=b"", stderr=b"synthetic failure\n")
            if case == "non_utf8":
                return subprocess.CompletedProcess(command, 0, stdout=bytes((255, 0)), stderr=b"")
            if case == "truncated":
                return subprocess.CompletedProcess(command, 0, stdout=b"foreign.fifo", stderr=b"")
            if case == "duplicate":
                return subprocess.CompletedProcess(command, 0, stdout=b"foreign.fifo\0foreign.fifo\0", stderr=b"")
            if case == "nonempty_no_match":
                return subprocess.CompletedProcess(command, 1, stdout=b"foreign.fifo\0", stderr=b"")
            if case == "empty_match":
                return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
            return subprocess.CompletedProcess(command, 0, stdout=b"elsewhere\0", stderr=b"")
        return cast(Any, original_run)(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", ignored_path_failure)

    with pytest.raises(TrafficlabError) as captured:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.affected_evidence, outcome.evidence_state) == (
        expected_kind,
        "environment",
        "not_published",
    )
    assert expected in outcome.detail


def test_offline_auditor_checks_the_worktree_before_committed_descendant_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mutable source is primary before the auditor trusts the committed descendant diff."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    changed = repository / "scripts" / "audit_validation_study.py"
    changed.write_bytes(changed.read_bytes() + b"\n# dirty auditor\n")
    original_git_bytes = vs_audit_environment.git_bytes

    def require_worktree_first(repository: Path, argv: tuple[str, ...], *, name: str) -> bytes:
        if name == "post-source changed paths":
            pytest.fail("committed descendant paths were trusted before the dirty worktree")
        return original_git_bytes(repository, argv, name=name)

    monkeypatch.setattr(vs_audit_environment, "git_bytes", require_worktree_first)

    with pytest.raises(TrafficlabError, match="working-tree"):
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("recorded_tree", "does not resolve"),
        ("non_ancestor", "is not an ancestor"),
        ("ancestry_oserror", "could not inspect source ancestry"),
        ("non_utf8_path", "post-source path is not UTF-8"),
        ("non_evidence_path", "non-evidence changes"),
        ("changed_image_lock", "capture image-lock bytes"),
    ),
)
def test_offline_auditor_covers_environment_source_binding_failure_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected: str,
) -> None:
    """Supplemental coverage exercises every local Git/source binding rejection."""

    repository, candidate = copy_validation_study_candidate(tmp_path)
    content = (candidate / "environment.json").read_bytes()
    if case == "recorded_tree":
        original_identity = vs_audit_environment._git_identity  # pyright: ignore[reportPrivateUsage]

        def mismatched_recorded_tree(repository: Path, argv: tuple[str, ...], *, name: str) -> str:
            if name == "recorded source tree":
                return "0" * 39 + "1"
            return original_identity(repository, argv, name=name)

        monkeypatch.setattr(vs_audit_environment, "_git_identity", mismatched_recorded_tree)
    elif case in {"non_ancestor", "ancestry_oserror"}:
        original_run = subprocess.run

        def source_binding_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
            command = tuple(cast(Sequence[str], args[0]))
            if command[:3] == ("git", "merge-base", "--is-ancestor"):
                if case == "ancestry_oserror":
                    raise OSError("synthetic Git failure")
                return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"")
            return cast(Any, original_run)(*args, **kwargs)

        monkeypatch.setattr(subprocess, "run", source_binding_run)
    else:
        original_git_bytes = vs_audit_environment.git_bytes

        def source_binding_bytes(repository: Path, argv: tuple[str, ...], *, name: str) -> bytes:
            if case == "non_utf8_path" and name == "post-source changed paths":
                return b"\xff\0"
            if case == "non_evidence_path" and name == "post-source changed paths":
                return b"src/trafficlab/__init__.py\0"
            if case == "changed_image_lock" and name == "recorded capture image lock":
                return b"different checked image lock\n"
            return original_git_bytes(repository, argv, name=name)

        monkeypatch.setattr(vs_audit_environment, "git_bytes", source_binding_bytes)

    with pytest.raises(vs_audit_common.Issue, match=expected):
        vs_audit_environment.load_environment(content, repository=repository)


def test_offline_bundle_audit_reconstructs_relocated_complete_fixture_without_external_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    before = candidate_bytes(candidate)

    def reject_external(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("offline audit attempted an external operation")

    monkeypatch.setattr(socket, "socket", reject_external)
    monkeypatch.setattr(socket, "create_connection", reject_external)
    original_run = subprocess.run

    def local_git_only(argv: Sequence[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if tuple(argv[:1]) == ("git",):
            return original_run(argv, *args, **kwargs)  # type: ignore[call-overload]
        raise AssertionError("offline audit attempted a non-Git subprocess")

    monkeypatch.setattr(subprocess, "run", local_git_only)
    monkeypatch.setattr(vs_cli, "run_experiment", reject_external)

    result = vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    assert result.bundle == candidate
    assert result.run_directory == candidate / "training" / "short" / "r1"
    assert result.file_count == len(before) - 1
    assert result.manifest_sha256 == hashlib.sha256(before["manifest.json"]).hexdigest()
    assert candidate_bytes(candidate) == before


@pytest.mark.parametrize("mutation", ("environment", "final-controls"))
def test_offline_bundle_audit_reconstructs_environment_and_final_controls(
    tmp_path: Path,
    mutation: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    index_path = candidate / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if mutation == "environment":
        (repository / "uv.lock").write_bytes(b"different lock\n")
    else:
        cast(list[dict[str, object]], index["fresh_simulation"])[0]["seed"] = 98
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == "artifact_foreign"
    assert outcome.affected_evidence in {"environment", "fresh_simulation/short/r1.json"}


@pytest.mark.parametrize(
    ("field", "value", "expected_kind"),
    (
        ("scientific_artifact_schema", 1, "scientific_semantics_incompatible"),
        ("python_implementation", "PyPy", "scientific_semantics_incompatible"),
        ("source_commit", "z" * 40, "artifact_corrupt"),
        ("target_image_reference", "trafficlab-target:latest", "artifact_corrupt"),
        ("target_image_id", "sha256:bad", "artifact_corrupt"),
        ("capture_image_reference", "trafficlab-capture:latest", "artifact_corrupt"),
        (
            "compatibility_decision",
            {"reason": "fixture", "status": "incompatible"},
            "scientific_semantics_incompatible",
        ),
    ),
)
def test_offline_bundle_audit_validates_every_environment_lock_boundary(
    tmp_path: Path,
    field: str,
    value: object,
    expected_kind: str,
) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    environment_path = candidate / "environment.json"
    environment = cast(dict[str, object], json.loads(environment_path.read_text(encoding="utf-8")))
    environment[field] = value
    write_canonical_json(environment_path, environment)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state, outcome.authority) == (
        expected_kind,
        "publication",
        "environment",
        "not_published",
        "primary",
    )


def test_audit_bundle_rejects_a_candidate_outside_the_relocated_repository(tmp_path: Path) -> None:
    repository, candidate = copy_validation_study_candidate(tmp_path)
    outside = tmp_path / "outside-candidate"
    shutil.copytree(candidate, outside)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(outside, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.detail,
        outcome.affected_evidence,
        outcome.corrective_action,
    ) == (
        "artifact_foreign",
        "bundle must remain beneath the relocated repository",
        "bundle",
        "use a retained candidate beneath the repository",
    )


def test_offline_auditor_binds_the_environment_to_the_relocated_git_and_image_locks(
    tmp_path: Path,
    generated_validation_study_candidate_template: Path,
) -> None:
    repository, candidate = copy_validation_study_candidate(
        tmp_path,
        generated_template=generated_validation_study_candidate_template,
    )
    environment_path = candidate / "environment.json"
    environment = cast(dict[str, object], json.loads(environment_path.read_text(encoding="utf-8")))
    environment["source_commit"] = "b" * 40
    environment["capture_image_id"] = f"sha256:{'e' * 64}"
    write_canonical_json(environment_path, environment)
    rewrite_candidate_manifest(candidate)

    with pytest.raises(TrafficlabError) as error:
        vs_audit_lifecycle.audit_bundle(candidate, repository=repository)

    outcome = error.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.authority,
    ) == ("artifact_foreign", "publication", "environment", "not_published", "primary")


def test_validation_study_context_uses_node_name_when_original_name_is_absent() -> None:
    """Non-parametrized pytest nodes retain an isolation key for source-mutating audits."""

    request = cast(
        pytest.FixtureRequest,
        SimpleNamespace(
            node=SimpleNamespace(
                name="test_audited_bundle_publication_rechecks_candidate_and_preserves_an_occupied_destination",
                originalname=None,
            )
        ),
    )

    assert validation_study_request_test_name(request) in ISOLATED_VALIDATION_STUDY_REPOSITORY_TESTS


def test_validation_study_local_exclude_lock_is_process_exclusive(tmp_path: Path) -> None:
    """The common Git exclusion mutation serializes independently scheduled workers."""

    lock_path = tmp_path / "exclude.lock"
    probe = (
        "import fcntl, pathlib, sys\n"
        "with pathlib.Path(sys.argv[1]).open('a+b') as stream:\n"
        "    try:\n"
        "        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    except BlockingIOError:\n"
        "        raise SystemExit(0)\n"
        "    raise SystemExit(1)\n"
    )

    with exclusive_validation_study_file_lock(lock_path):
        result = subprocess.run(
            (sys.executable, "-c", probe, str(lock_path)),
            check=False,
            capture_output=True,
        )

    assert result.returncode == 0


def test_validation_study_exclude_restore_preserves_a_primary_failure() -> None:
    """A shared-Git restore failure retains the audit assertion that triggered cleanup."""

    primary = RuntimeError("primary audit failure")
    cleanup = OSError("synthetic exclude restore failure")

    def restore_failure() -> None:
        raise cleanup

    with pytest.raises(BaseExceptionGroup) as captured:
        finish_validation_study_exclude_restore(body_error=primary, restore=restore_failure)

    assert captured.value.exceptions == (primary, cleanup)


def test_validation_study_gitignore_tracks_only_accepted_run_logs() -> None:
    """Accepted evidence logs remain trackable while candidates and ordinary logs stay ignored."""

    def ignored(path: str) -> bool:
        result = subprocess.run(
            ("git", "check-ignore", "-q", "--", path),
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        assert result.returncode in (0, 1)
        return result.returncode == 0

    assert not ignored("examples/validation_study/evidence/study-1/training/short/r1/run.log")
    assert ignored("examples/validation_study/evidence/.candidates/study-1/training/short/r1/run.log")
    assert ignored("runs/study-1/run.log")
