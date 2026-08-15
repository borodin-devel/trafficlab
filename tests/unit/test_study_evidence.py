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
        assert not evidence_root.exists()

    published = publish_accepted_bundle(candidate, evidence_root, "study-1", audit)

    assert calls == [candidate]
    assert published == destination
    assert _tree_bytes(destination) == _tree_bytes(candidate)
    assert list(evidence_root.iterdir()) == [destination]


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

    publish_accepted_bundle(candidate, evidence_root, "study-1", lambda _path: None)

    assert operations == ["parent_fsync", "tree_fsync", "rename", "root_fsync"]


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
    ) == ("publication_collision", "publication", "candidate accepted evidence bundle", "not_published", "primary", None)
    assert outcome.detail == str(error.value)
    assert outcome.corrective_action == error.value.corrective_action
    assert error.value.corrective_action == "choose a new study ID; accepted evidence bundles are immutable"
    assert audit_calls == [candidate]
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
