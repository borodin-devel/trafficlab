"""Recovery behavior."""

from __future__ import annotations

import os
import tempfile as tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import pytest

import scripts.validation_study.common as vs_common
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.prerequisites.run as vs_prereq_run
import scripts.validation_study.rotation.run as vs_rotation_run
import scripts.validation_study.rotation.schema as vs_rotation_schema
from tests.support.validation_study.artifacts import tree_inventory
from tests.support.validation_study.runners import ScriptedPrerequisiteRunner, write_prerequisite_repository_inputs
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.errors import TrafficlabError


def test_prerequisite_rotation_retains_its_journal_when_rollback_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleanup fault after a restored primary target leaves the journal for a later public recovery."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    vs_prereq_run.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    canonical = study_root / "prerequisites.json"
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    original_commit_fsync = vs_rotation_run._commit_prerequisite_fsync  # pyright: ignore[reportPrivateUsage]
    original_unlink = Path.unlink
    failed_primary = False

    def fail_canonical_commit(destination: Path) -> None:
        nonlocal failed_primary
        if destination == canonical and not failed_primary:
            failed_primary = True
            raise OSError("simulated primary canonical commit failure")
        original_commit_fsync(destination)

    def fail_marker_stage_cleanup(path: Path, *, missing_ok: bool = False) -> None:
        if path.name.startswith(".prerequisites-success.json.") and path.name.endswith(".tmp"):
            raise OSError("simulated rollback cleanup unlink failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(vs_rotation_run, "_commit_prerequisite_fsync", fail_canonical_commit)
    monkeypatch.setattr(Path, "unlink", fail_marker_stage_cleanup)
    with pytest.raises(TrafficlabError, match="rollback cleanup failed after") as raised:
        vs_prereq_run.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    journal = study_root / ".study-work" / "attempts" / r5.study_id / "prerequisites-rotation.json"
    assert journal.is_file()
    assert "retained recovery journal" in str(raised.value)
    assert tuple(study_root.rglob(".prerequisites-success.json.*.tmp"))


@pytest.mark.parametrize(
    ("boundary", "owned_prefix", "owned_suffix"),
    (
        ("archive stage", ".prerequisites.raw.json.", ".tmp"),
        ("short backup", ".short.toml.", ".bak"),
        ("streaming backup", ".streaming.toml.", ".bak"),
        ("bursty backup", ".bursty.toml.", ".bak"),
        ("root backup", ".prerequisites.json.", ".bak"),
        ("marker stage", ".prerequisites-success.json.", ".tmp"),
    ),
)
@pytest.mark.parametrize("failure_kind", ("unlink", "fsync", "baseexception"))
def test_prerequisite_rotation_recovers_success_cleanup_failures_before_a_new_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    owned_prefix: str,
    owned_suffix: str,
    failure_kind: str,
) -> None:
    """A durable journal survives every post-marker cleanup fault until public recovery succeeds."""

    class SimulatedCleanupCrash(BaseException):
        pass

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    vs_prereq_run.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    cleanup_started = False
    enabled = True
    original_after_commit = vs_rotation_run._after_prerequisite_rotation_commit  # pyright: ignore[reportPrivateUsage]
    original_unlink = Path.unlink
    original_fsync = vs_rotation_run._fsync_prerequisite_rotation_directory  # pyright: ignore[reportPrivateUsage]

    def matches_owned_cleanup_path(path: Path) -> bool:
        return enabled and cleanup_started and path.name.startswith(owned_prefix) and path.name.endswith(owned_suffix)

    def mark_successful_marker_commit(destination: Path) -> None:
        nonlocal cleanup_started
        original_after_commit(destination)
        if destination.name == "prerequisites-success.json":
            cleanup_started = True

    def fail_selected_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if matches_owned_cleanup_path(path):
            if failure_kind == "baseexception":
                raise SimulatedCleanupCrash(f"simulated {boundary} cleanup crash")
            if failure_kind == "unlink":
                raise OSError(f"simulated {boundary} cleanup unlink failure")
        original_unlink(path, missing_ok=missing_ok)

    def fail_selected_directory_fsync(path: Path) -> None:
        if matches_owned_cleanup_path(path) and failure_kind == "fsync":
            raise OSError(f"simulated {boundary} cleanup fsync failure")
        original_fsync(path)

    monkeypatch.setattr(vs_rotation_run, "_after_prerequisite_rotation_commit", mark_successful_marker_commit)
    monkeypatch.setattr(Path, "unlink", fail_selected_unlink)
    monkeypatch.setattr(vs_rotation_run, "_fsync_prerequisite_rotation_directory", fail_selected_directory_fsync)

    if failure_kind == "baseexception":
        with pytest.raises(SimulatedCleanupCrash, match=f"{boundary} cleanup crash"):
            vs_prereq_run.run_prerequisites(
                r5.url,
                r5.study_id,
                repository_root=repository,
                runner=r5,
                utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
            )
    else:
        with pytest.raises(TrafficlabError, match="retained recovery journal"):
            vs_prereq_run.run_prerequisites(
                r5.url,
                r5.study_id,
                repository_root=repository,
                runner=r5,
                utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
            )

    r5_attempt = study_root / ".study-work" / "attempts" / r5.study_id
    journal = r5_attempt / "prerequisites-rotation.json"
    assert journal.is_file()
    canonical = study_root / "prerequisites.json"
    assert (
        vs_prereq_codec.parse_prerequisite_results(canonical.read_bytes(), repository_root=repository).study_id
        == r5.study_id
    )
    expected_r5_inventory = {
        ".": ("directory",),
        **{
            name: tree_inventory(r5_attempt)[name]
            for name in ("prerequisites.json", "prerequisites.raw.json", "prerequisites-success.json")
        },
    }

    enabled = False
    r6 = ScriptedPrerequisiteRunner(repository, study_id="study-r6")
    original_begin = vs_rotation_run.begin_phase_attempt

    def assert_recovered_before_begin(
        root: Path,
        *,
        study_id: str,
        url: str,
        phase: Literal["prerequisites", "collection"],
    ) -> Path:
        if study_id == r6.study_id:
            assert not journal.exists()
            assert tree_inventory(r5_attempt) == expected_r5_inventory
            assert not tuple(study_root.rglob(".*.tmp"))
            assert not tuple(study_root.rglob(".*.bak"))
            raise ValueError("success cleanup recovery inspection complete")
        return original_begin(root, study_id=study_id, url=url, phase=phase)

    monkeypatch.setattr(vs_prereq_run, "begin_phase_attempt", assert_recovered_before_begin)
    with pytest.raises(TrafficlabError, match="success cleanup recovery inspection complete"):
        vs_prereq_run.run_prerequisites(
            r6.url,
            r6.study_id,
            repository_root=repository,
            runner=r6,
            utc_now=lambda: datetime(2026, 8, 18, tzinfo=UTC),
        )


def test_prerequisite_rotation_journal_requires_a_stage_for_every_owned_target(tmp_path: Path) -> None:
    """A journal cannot omit a stage path before it records mutable transaction state."""

    target = vs_rotation_schema.PrerequisiteRotationTarget(
        kind="archive",
        destination=tmp_path / "archive.json",
        stage=None,
        backup=None,
        before_identity=None,
        target_identity=cast(vs_common.JsonObject, identify_bytes(b"incoming\n").as_dict()),
        must_be_absent=True,
    )

    with pytest.raises(ValueError, match="requires every staged target"):
        vs_rotation_schema.render_prerequisite_rotation_journal(
            tmp_path,
            study_id="study-r5",
            targets=(target,),
        )


def test_prerequisite_rotation_recovery_allows_an_absent_attempt_directory(tmp_path: Path) -> None:
    """Recovery is a no-op before any prerequisite phase has ever allocated an attempt directory."""

    repository = tmp_path / "repository"
    repository.mkdir()
    vs_rotation_run.recover_incomplete_prerequisite_rotations(repository)


@pytest.mark.parametrize("failure", ("attempt-lstat", "attempt-enumeration", "child-lstat"))
def test_prerequisite_rotation_recovery_reports_attempt_directory_io_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """A recovery scan never skips an unreadable attempt boundary before consuming a new study ID."""

    repository = tmp_path / "repository"
    attempts = repository / "examples" / "validation_study" / ".study-work" / "attempts"
    attempts.mkdir(parents=True)
    child = attempts / "study-r5"
    child.mkdir()
    original_lstat = Path.lstat
    original_iterdir = Path.iterdir

    def fail_selected_lstat(path: Path) -> os.stat_result:
        if (failure == "attempt-lstat" and path == attempts) or (failure == "child-lstat" and path == child):
            raise OSError(f"simulated {failure}")
        return original_lstat(path)

    def fail_selected_iterdir(path: Path) -> list[Path]:
        if failure == "attempt-enumeration" and path == attempts:
            raise OSError("simulated attempt enumeration")
        return list(original_iterdir(path))

    monkeypatch.setattr(Path, "lstat", fail_selected_lstat)
    monkeypatch.setattr(Path, "iterdir", fail_selected_iterdir)
    with pytest.raises(ValueError, match="could not (inspect|enumerate) prerequisite attempt"):
        vs_rotation_run.recover_incomplete_prerequisite_rotations(repository)


def test_prerequisite_rotation_retains_failed_restore_backup_with_its_recovery_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed rollback restore never deletes the only exact prior bytes."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    vs_prereq_run.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    short_config = study_root / "configs" / "short.toml"
    short_before = short_config.read_bytes()
    canonical = study_root / "prerequisites.json"
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    original_fsync = vs_rotation_run._commit_prerequisite_fsync  # pyright: ignore[reportPrivateUsage]
    original_replace = os.replace
    failed_backup: Path | None = None

    def fail_canonical_commit(destination: Path) -> None:
        if destination == canonical:
            raise OSError("simulated primary canonical fsync failure")
        original_fsync(destination)

    def fail_short_restore(source: str | Path, target: str | Path) -> None:
        nonlocal failed_backup
        source_path = Path(source)
        target_path = Path(target)
        if source_path.suffix == ".bak" and target_path == short_config:
            failed_backup = source_path
            raise OSError("simulated short rollback restore failure")
        original_replace(source, target)

    monkeypatch.setattr(vs_rotation_run, "_commit_prerequisite_fsync", fail_canonical_commit)
    monkeypatch.setattr(os, "replace", fail_short_restore)
    with pytest.raises(TrafficlabError, match="rollback failed after") as raised:
        vs_prereq_run.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    assert failed_backup is not None
    assert str(failed_backup) in str(raised.value)
    assert failed_backup.read_bytes() == short_before
    assert tree_inventory(failed_backup.parent)[failed_backup.name] == ("regular", short_before)
    journal = study_root / ".study-work" / "attempts" / r5.study_id / "prerequisites-rotation.json"
    assert journal.is_file()

    r6 = ScriptedPrerequisiteRunner(repository, study_id="study-r6")
    with pytest.raises(TrafficlabError, match="retained recovery paths") as recovery:
        vs_prereq_run.run_prerequisites(
            r6.url,
            r6.study_id,
            repository_root=repository,
            runner=r6,
            utc_now=lambda: datetime(2026, 8, 18, tzinfo=UTC),
        )
    assert str(failed_backup) in str(recovery.value)
    assert failed_backup.read_bytes() == short_before
    assert journal.is_file()
    assert not (study_root / ".study-work" / "attempts" / r6.study_id / "prerequisites.json").exists()


@pytest.mark.parametrize("commit_index", (1, 2, 3, 4, 5, 6))
def test_prerequisite_rotation_recovers_every_baseexception_crash_before_a_new_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    commit_index: int,
) -> None:
    """A fresh public prerequisites invocation restores all r4 bytes before it consumes r6."""

    class SimulatedCrash(BaseException):
        pass

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    vs_prereq_run.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    canonical_before = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    r4_attempt = study_root / ".study-work" / "attempts" / r4.study_id
    r4_attempt_before = tree_inventory(r4_attempt)
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    commits = 0

    def crash_after_commit(_destination: Path) -> None:
        nonlocal commits
        commits += 1
        if commits == commit_index:
            raise SimulatedCrash(f"simulated crash after commit {commit_index}")

    monkeypatch.setattr(vs_rotation_run, "_after_prerequisite_rotation_commit", crash_after_commit, raising=False)
    with pytest.raises(SimulatedCrash, match=f"commit {commit_index}"):
        vs_prereq_run.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    r5_attempt = study_root / ".study-work" / "attempts" / r5.study_id
    r5_canonical_after_crash = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts and not path.name.endswith((".tmp", ".bak"))
    }
    r5_success_marker = r5_attempt / "prerequisites-success.json"
    r6 = ScriptedPrerequisiteRunner(repository, study_id="study-r6")
    original_begin = vs_rotation_run.begin_phase_attempt

    def assert_recovered_before_begin(
        root: Path,
        *,
        study_id: str,
        url: str,
        phase: Literal["prerequisites", "collection"],
    ) -> Path:
        if study_id == r6.study_id:
            canonical_after = {
                path.relative_to(study_root): path.read_bytes()
                for path in sorted(study_root.rglob("*"))
                if path.is_file() and ".study-work" not in path.parts
            }
            assert tree_inventory(r4_attempt) == r4_attempt_before
            if commit_index == 6:
                assert canonical_after == r5_canonical_after_crash
                assert tree_inventory(r5_attempt) == {
                    ".": ("directory",),
                    "prerequisites.json": (
                        "regular",
                        vs_common.canonical_json(
                            cast(
                                vs_common.JsonObject,
                                {"phase": "prerequisites", "study_id": r5.study_id, "url": r5.url},
                            )
                        ),
                    ),
                    "prerequisites-success.json": ("regular", r5_success_marker.read_bytes()),
                    "prerequisites.raw.json": (
                        "regular",
                        (study_root / "prerequisites.json").read_bytes(),
                    ),
                }
            else:
                assert canonical_after == canonical_before
                assert tree_inventory(r5_attempt) == {
                    ".": ("directory",),
                    "prerequisites.json": (
                        "regular",
                        vs_common.canonical_json(
                            cast(
                                vs_common.JsonObject,
                                {"phase": "prerequisites", "study_id": r5.study_id, "url": r5.url},
                            )
                        ),
                    ),
                }
            assert not tuple(study_root.rglob(".*.tmp"))
            assert not tuple(study_root.rglob(".*.bak"))
            raise ValueError("recovery inspection complete")
        return original_begin(root, study_id=study_id, url=url, phase=phase)

    monkeypatch.setattr(vs_prereq_run, "begin_phase_attempt", assert_recovered_before_begin)
    with pytest.raises(TrafficlabError, match="recovery inspection complete"):
        vs_prereq_run.run_prerequisites(
            r6.url,
            r6.study_id,
            repository_root=repository,
            runner=r6,
            utc_now=lambda: datetime(2026, 8, 18, tzinfo=UTC),
        )


@pytest.mark.parametrize("failure_index", (1, 2, 3, 4))
def test_prerequisite_rotation_rolls_back_every_replacement_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_index: int,
) -> None:
    """Every replaceable config/root target restores the complete r4 state."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    vs_prereq_run.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    canonical_before = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    r4_archive = study_root / ".study-work" / "attempts" / r4.study_id / "prerequisites.raw.json"
    r4_archive_before = r4_archive.read_bytes()
    r4_attempt = r4_archive.parent
    r4_attempt_before = tree_inventory(r4_attempt)
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    original_replace = os.replace
    replacements = 0

    def fail_nth_replacement(source: str | Path, target: str | Path) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == failure_index:
            raise OSError(f"simulated replacement {failure_index}")
        original_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_nth_replacement)
    with pytest.raises(TrafficlabError, match=f"replacement {failure_index}"):
        vs_prereq_run.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    canonical_after = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    assert canonical_after == canonical_before
    assert r4_archive.read_bytes() == r4_archive_before
    assert tree_inventory(r4_attempt) == r4_attempt_before
    r5_attempt = study_root / ".study-work" / "attempts" / r5.study_id
    assert (r5_attempt / "prerequisites.json").is_file()
    assert not (r5_attempt / "prerequisites.raw.json").exists()
    assert not (r5_attempt / "prerequisites-success.json").exists()
    assert not tuple(study_root.rglob(".*.tmp"))
    assert not tuple(study_root.rglob(".*.bak"))


@pytest.mark.parametrize("failure_index", (1, 2, 3, 4, 5, 6))
def test_prerequisite_rotation_rolls_back_every_commit_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_index: int,
) -> None:
    """A post-replacement fsync failure removes every partially committed r5 publication."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    vs_prereq_run.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    canonical_before = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    r4_archive = study_root / ".study-work" / "attempts" / r4.study_id / "prerequisites.raw.json"
    r4_archive_before = r4_archive.read_bytes()
    r4_attempt = r4_archive.parent
    r4_attempt_before = tree_inventory(r4_attempt)
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    fsyncs = 0

    def fail_nth_commit_fsync(_destination: Path) -> None:
        nonlocal fsyncs
        fsyncs += 1
        if fsyncs == failure_index:
            raise OSError(f"simulated commit fsync {failure_index}")

    monkeypatch.setattr(vs_rotation_run, "_commit_prerequisite_fsync", fail_nth_commit_fsync, raising=False)
    with pytest.raises(TrafficlabError, match=f"commit fsync {failure_index}"):
        vs_prereq_run.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    canonical_after = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    assert canonical_after == canonical_before
    assert r4_archive.read_bytes() == r4_archive_before
    assert tree_inventory(r4_attempt) == r4_attempt_before
    r5_attempt = study_root / ".study-work" / "attempts" / r5.study_id
    assert (r5_attempt / "prerequisites.json").is_file()
    assert not (r5_attempt / "prerequisites.raw.json").exists()
    assert not (r5_attempt / "prerequisites-success.json").exists()
    assert not tuple(study_root.rglob(".*.tmp"))
    assert not tuple(study_root.rglob(".*.bak"))


def test_prerequisite_rotation_rollback_reports_a_durability_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback retains its failure diagnostic if its own directory fsync cannot complete."""

    destination = tmp_path / "prerequisites.json"
    destination.write_bytes(b"partially committed\n")

    def fail_fsync(_destination: Path) -> None:
        raise OSError("simulated rollback fsync failure")

    monkeypatch.setattr(vs_rotation_run, "_commit_prerequisite_fsync", fail_fsync)
    target = vs_rotation_schema.PrerequisiteRotationTarget(
        kind="archive",
        destination=destination,
        stage=tmp_path / ".prerequisites.json.stage.tmp",
        backup=None,
        before_identity=None,
        target_identity=cast(vs_common.JsonObject, identify_bytes(b"partially committed\n").as_dict()),
        must_be_absent=True,
    )
    failures, failed_targets = vs_rotation_run._rollback_prerequisite_rotation([target])  # pyright: ignore[reportPrivateUsage]

    assert failures == [f"{destination}: simulated rollback fsync failure"]
    assert failed_targets == [target]
    assert not destination.exists()


def test_prerequisite_rotation_preserves_primary_when_rollback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A primary publication error remains visible with an ordered rollback durability diagnostic."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    vs_prereq_run.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")

    def fail_commit(_destination: Path) -> None:
        raise OSError("simulated primary commit failure")

    def simulated_rollback_failure(
        _committed: Sequence[vs_rotation_schema.PrerequisiteRotationTarget],
    ) -> tuple[list[str], list[vs_rotation_schema.PrerequisiteRotationTarget]]:
        return ["simulated rollback durability failure"], []

    monkeypatch.setattr(vs_rotation_run, "_commit_prerequisite_fsync", fail_commit)
    monkeypatch.setattr(
        vs_rotation_run,
        "_rollback_prerequisite_rotation",
        simulated_rollback_failure,
    )
    with pytest.raises(TrafficlabError, match="rollback failed after simulated primary commit failure"):
        vs_prereq_run.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )


def test_prerequisite_rotation_nonstrict_prestage_cleanup_preserves_its_primary_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only pre-journal cleanup may suppress a secondary staging-unlink error."""

    destination = tmp_path / "prerequisites.json"
    staged = tmp_path / ".prerequisites.json.primary.tmp"
    content = b"canonical prerequisite bytes\n"
    staged.write_bytes(content)
    target = vs_rotation_schema.PrerequisiteRotationTarget(
        kind="archive",
        destination=destination,
        stage=staged,
        backup=None,
        before_identity=None,
        target_identity=cast(vs_common.JsonObject, identify_bytes(content).as_dict()),
        must_be_absent=True,
    )
    original_unlink = Path.unlink

    def fail_staged_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path == staged:
            raise OSError("simulated pre-journal cleanup unlink failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_staged_unlink)
    assert (
        vs_rotation_run._cleanup_prerequisite_rotation_staging(  # pyright: ignore[reportPrivateUsage]
            (target,),
            strict=False,
        )
        == []
    )
    assert staged.read_bytes() == content
