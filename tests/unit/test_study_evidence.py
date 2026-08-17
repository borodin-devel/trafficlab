from __future__ import annotations

import errno
from pathlib import Path

import pytest

import trafficlab.study_evidence as study_evidence
from trafficlab.errors import TrafficlabError
from trafficlab.study_evidence import publish_accepted_bundle


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
