from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import pytest

from scripts import run_validation_study as study
from tests.support.validation_study import (
    ScriptedPrerequisiteRunner,
    tree_inventory,
    valid_prerequisite,
    write_legacy_prerequisite_root,
    write_prerequisite_repository_inputs,
)
from trafficlab.compatibility import identify_bytes
from trafficlab.errors import TrafficlabError
from trafficlab.run import RunResult


def test_prerequisite_attempt_marker_is_written_before_any_later_failure(tmp_path: Path) -> None:
    """A syntactically valid prerequisite attempt is permanently visible even if Git fails first."""

    repository = tmp_path / "repository"
    repository.mkdir()

    def fail_git(
        argv: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 1, b"", b"missing Git state")

    with pytest.raises(TrafficlabError, match="prerequisite validation failed"):
        study.run_prerequisites(
            "https://downloads.example.test/object.bin",
            "study-1",
            repository_root=repository,
            runner=cast(study.CommandRunner, fail_git),
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    marker = (
        repository / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1" / "prerequisites.json"
    )
    assert json.loads(marker.read_text()) == {
        "phase": "prerequisites",
        "study_id": "study-1",
        "url": "https://downloads.example.test/object.bin",
    }
    assert not marker.with_name("prerequisites-success.json").exists()


def test_successful_prerequisite_marker_binds_the_published_prerequisite_bytes(tmp_path: Path) -> None:
    """Collection can only follow the exact successful prerequisite publication."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    runner = ScriptedPrerequisiteRunner(repository)
    study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    prerequisite = repository / "examples" / "validation_study" / "prerequisites.json"
    marker = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / runner.study_id
        / "prerequisites-success.json"
    )
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "phase": "prerequisites",
        "prerequisites_identity": identify_bytes(prerequisite.read_bytes()).as_dict(),
        "study_id": runner.study_id,
        "url": runner.url,
    }


def test_rotation_bootstraps_a_matching_legacy_raw_archive_before_replacement(tmp_path: Path) -> None:
    """A schema-1 root and success marker gain a forensic archive before r5 overwrites them."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    root, r4_bytes = write_legacy_prerequisite_root(repository)
    r4_archive = root.parent / ".study-work" / "attempts" / "study-r4" / "prerequisites.raw.json"
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")

    study.run_prerequisites(
        r5.url,
        r5.study_id,
        repository_root=repository,
        runner=r5,
        utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )

    assert r4_archive.read_bytes() == r4_bytes
    assert root.read_bytes() != r4_bytes


def test_rotation_rejects_a_legacy_marker_identity_mismatch_before_replacement(
    tmp_path: Path,
) -> None:
    """A legacy archive is never inferred from root bytes that disagree with its successful marker."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    root, r4_bytes = write_legacy_prerequisite_root(repository)
    marker = root.parent / ".study-work" / "attempts" / "study-r4" / "prerequisites-success.json"
    marker_value = json.loads(marker.read_text(encoding="utf-8"))
    marker_value["prerequisites_identity"] = identify_bytes(b"wrong legacy identity\n").as_dict()
    marker.write_bytes(study._canonical_json(cast(study.JsonObject, marker_value)))  # pyright: ignore[reportPrivateUsage]
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")

    with pytest.raises(TrafficlabError, match="matching successful prerequisite marker"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    assert root.read_bytes() == r4_bytes
    assert not (root.parent / ".study-work" / "attempts" / "study-r4" / "prerequisites.raw.json").exists()


def test_rotation_rejects_a_conflicting_legacy_raw_archive_before_replacement(
    tmp_path: Path,
) -> None:
    """A prior archive collision is preserved rather than silently replaced during rotation."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    root, r4_bytes = write_legacy_prerequisite_root(repository)
    r4_archive = root.parent / ".study-work" / "attempts" / "study-r4" / "prerequisites.raw.json"
    r4_archive.write_bytes(b"conflicting legacy archive\n")
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")

    with pytest.raises(TrafficlabError, match="archived prerequisite"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    assert root.read_bytes() == r4_bytes
    assert r4_archive.read_bytes() == b"conflicting legacy archive\n"


def test_rotation_rejects_a_nonregular_legacy_raw_archive_before_replacement(
    tmp_path: Path,
) -> None:
    """A legacy archive directory is never replaced while preserving the current canonical root."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    root, r4_bytes = write_legacy_prerequisite_root(repository)
    r4_archive = root.parent / ".study-work" / "attempts" / "study-r4" / "prerequisites.raw.json"
    r4_archive.mkdir()
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")

    with pytest.raises(TrafficlabError, match="archived prerequisite document must be a regular file"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    assert root.read_bytes() == r4_bytes
    assert r4_archive.is_dir()


def test_failed_prerequisite_rotation_preserves_current_raw_document_and_old_archive(tmp_path: Path) -> None:
    """A fresh failed ID cannot alter the prior canonical prerequisite publication."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    canonical = repository / "examples" / "validation_study" / "prerequisites.json"
    r4_bytes = canonical.read_bytes()
    r4_archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / r4.study_id
        / "prerequisites.raw.json"
    )
    assert r4_archive.read_bytes() == r4_bytes

    r5 = ScriptedPrerequisiteRunner(repository, "docker-matrix-failed", study_id="study-r5")
    with pytest.raises(TrafficlabError, match="docker_matrix guarded pytest failed"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    assert canonical.read_bytes() == r4_bytes
    assert r4_archive.read_bytes() == r4_bytes
    r5_attempt = repository / "examples" / "validation_study" / ".study-work" / "attempts" / r5.study_id
    assert (r5_attempt / "prerequisites.json").is_file()
    assert not (r5_attempt / "prerequisites-success.json").exists()
    assert not (r5_attempt / "prerequisites.raw.json").exists()


def test_successful_prerequisite_rotation_replaces_current_raw_and_preserves_old_archive(tmp_path: Path) -> None:
    """A new successful ID atomically advances the canonical root without erasing r4 evidence."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    canonical = repository / "examples" / "validation_study" / "prerequisites.json"
    r4_bytes = canonical.read_bytes()
    r4_archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / r4.study_id
        / "prerequisites.raw.json"
    )

    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    result = study.run_prerequisites(
        r5.url,
        r5.study_id,
        repository_root=repository,
        runner=r5,
        utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )

    r5_bytes = canonical.read_bytes()
    r5_archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / r5.study_id
        / "prerequisites.raw.json"
    )
    assert r5_bytes != r4_bytes
    assert study.parse_prerequisite_results(r5_bytes, repository_root=repository) == result
    assert r4_archive.read_bytes() == r4_bytes
    assert r5_archive.read_bytes() == r5_bytes
    r5_marker = json.loads((r5_archive.with_name("prerequisites-success.json")).read_text(encoding="utf-8"))
    assert r5_marker["prerequisites_identity"] == identify_bytes(r5_archive.read_bytes()).as_dict()
    assert study.validate_base_configs(repository, result) == {
        name: study.build_base_config(
            workload,
            repository_root=repository,
            study_id=r5.study_id,
            url=r5.url,
            capture_image_id=r5.capture_id,
        )
        for name, workload in ((workload.name, workload) for workload in study.workload_specs(r5.url))
    }


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
    study.run_prerequisites(
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
    original_replace = study.os.replace
    replacements = 0

    def fail_nth_replacement(source: str | Path, target: str | Path) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == failure_index:
            raise OSError(f"simulated replacement {failure_index}")
        original_replace(source, target)

    monkeypatch.setattr(study.os, "replace", fail_nth_replacement)
    with pytest.raises(TrafficlabError, match=f"replacement {failure_index}"):
        study.run_prerequisites(
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
    study.run_prerequisites(
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

    monkeypatch.setattr(study, "_commit_prerequisite_fsync", fail_nth_commit_fsync, raising=False)
    with pytest.raises(TrafficlabError, match=f"commit fsync {failure_index}"):
        study.run_prerequisites(
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


def test_prerequisite_rotation_target_reports_lstat_and_read_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Target inspection distinguishes an unreadable entry from a read failure without following it."""

    destination = tmp_path / "prerequisites.json"
    destination.write_bytes(b"canonical\n")
    original_lstat = Path.lstat

    def fail_target_lstat(path: Path) -> os.stat_result:
        if path == destination:
            raise OSError("simulated target lstat failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_target_lstat)
    with pytest.raises(ValueError, match="could not inspect rotation target"):
        study._read_regular_prerequisite_rotation_target(  # pyright: ignore[reportPrivateUsage]
            destination,
            name="rotation target",
        )

    monkeypatch.undo()
    original_read_bytes = Path.read_bytes

    def fail_target_read(path: Path) -> bytes:
        if path == destination:
            raise OSError("simulated target read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_target_read)
    with pytest.raises(ValueError, match="could not read rotation target"):
        study._read_regular_prerequisite_rotation_target(  # pyright: ignore[reportPrivateUsage]
            destination,
            name="rotation target",
        )


def test_prerequisite_rotation_stage_cleans_validator_and_descriptor_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A private staging file never survives validation or descriptor-wrapper failure."""

    destination = tmp_path / "prerequisites.json"

    def reject_stage(_stage: Path, _content: bytes) -> None:
        raise ValueError("simulated staged validation failure")

    with pytest.raises(ValueError, match="staged validation"):
        study._stage_prerequisite_rotation_file(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"canonical\n",
            validate=reject_stage,
        )
    assert not tuple(tmp_path.glob(".*.tmp"))

    closed: list[int] = []
    original_close = study.os.close

    def fail_fdopen(_descriptor: int, _mode: str) -> None:
        raise OSError("simulated staging fdopen failure")

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(study.os, "fdopen", fail_fdopen)
    monkeypatch.setattr(study.os, "close", record_close)
    with pytest.raises(OSError, match="staging fdopen failure"):
        study._stage_prerequisite_rotation_file(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"canonical\n",
            validate=lambda _stage, _content: None,
        )
    assert closed
    assert not tuple(tmp_path.glob(".*.tmp"))

    monkeypatch.undo()

    def fail_mkstemp(*_args: object, **_kwargs: object) -> tuple[int, str]:
        raise OSError("simulated staging mkstemp failure")

    monkeypatch.setattr(study.tempfile, "mkstemp", fail_mkstemp)
    with pytest.raises(OSError, match="staging mkstemp failure"):
        study._stage_prerequisite_rotation_file(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"canonical\n",
            validate=lambda _stage, _content: None,
        )

    monkeypatch.undo()
    original_unlink = Path.unlink

    def fail_staged_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.name.endswith(".tmp"):
            raise OSError("simulated staging unlink failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_staged_unlink)
    with pytest.raises(ValueError, match="staged validation failure"):
        study._stage_prerequisite_rotation_file(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"canonical\n",
            validate=reject_stage,
        )
    monkeypatch.undo()
    for temporary in tmp_path.glob(".*.tmp"):
        temporary.unlink()


def test_prerequisite_rotation_rollback_reports_a_durability_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rollback retains its failure diagnostic if its own directory fsync cannot complete."""

    destination = tmp_path / "prerequisites.json"
    destination.write_bytes(b"partially committed\n")

    def fail_fsync(_destination: Path) -> None:
        raise OSError("simulated rollback fsync failure")

    monkeypatch.setattr(study, "_commit_prerequisite_fsync", fail_fsync)
    target = study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
        kind="archive",
        destination=destination,
        stage=tmp_path / ".prerequisites.json.stage.tmp",
        backup=None,
        before_identity=None,
        target_identity=cast(study.JsonObject, identify_bytes(b"partially committed\n").as_dict()),
        must_be_absent=True,
    )
    failures, failed_targets = study._rollback_prerequisite_rotation([target])  # pyright: ignore[reportPrivateUsage]

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
    study.run_prerequisites(
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
        _committed: Sequence[study._PrerequisiteRotationTarget],  # pyright: ignore[reportPrivateUsage]
    ) -> tuple[list[str], list[study._PrerequisiteRotationTarget]]:  # pyright: ignore[reportPrivateUsage]
        return ["simulated rollback durability failure"], []

    monkeypatch.setattr(study, "_commit_prerequisite_fsync", fail_commit)
    monkeypatch.setattr(
        study,
        "_rollback_prerequisite_rotation",
        simulated_rollback_failure,
    )
    with pytest.raises(TrafficlabError, match="rollback failed after simulated primary commit failure"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )


def test_prerequisite_rotation_retains_its_journal_when_rollback_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleanup fault after a restored primary target leaves the journal for a later public recovery."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    study_root = repository / "examples" / "validation_study"
    canonical = study_root / "prerequisites.json"
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    original_commit_fsync = study._commit_prerequisite_fsync  # pyright: ignore[reportPrivateUsage]
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

    monkeypatch.setattr(study, "_commit_prerequisite_fsync", fail_canonical_commit)
    monkeypatch.setattr(Path, "unlink", fail_marker_stage_cleanup)
    with pytest.raises(TrafficlabError, match="rollback cleanup failed after") as raised:
        study.run_prerequisites(
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
    study.run_prerequisites(
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
    original_after_commit = study._after_prerequisite_rotation_commit  # pyright: ignore[reportPrivateUsage]
    original_unlink = Path.unlink
    original_fsync = study._fsync_prerequisite_rotation_directory  # pyright: ignore[reportPrivateUsage]

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

    monkeypatch.setattr(study, "_after_prerequisite_rotation_commit", mark_successful_marker_commit)
    monkeypatch.setattr(Path, "unlink", fail_selected_unlink)
    monkeypatch.setattr(study, "_fsync_prerequisite_rotation_directory", fail_selected_directory_fsync)

    if failure_kind == "baseexception":
        with pytest.raises(SimulatedCleanupCrash, match=f"{boundary} cleanup crash"):
            study.run_prerequisites(
                r5.url,
                r5.study_id,
                repository_root=repository,
                runner=r5,
                utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
            )
    else:
        with pytest.raises(TrafficlabError, match="retained recovery journal"):
            study.run_prerequisites(
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
    assert study.parse_prerequisite_results(canonical.read_bytes(), repository_root=repository).study_id == r5.study_id
    expected_r5_inventory = {
        ".": ("directory",),
        **{
            name: tree_inventory(r5_attempt)[name]
            for name in ("prerequisites.json", "prerequisites.raw.json", "prerequisites-success.json")
        },
    }

    enabled = False
    r6 = ScriptedPrerequisiteRunner(repository, study_id="study-r6")
    original_begin = study._begin_phase_attempt  # pyright: ignore[reportPrivateUsage]

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

    monkeypatch.setattr(study, "_begin_phase_attempt", assert_recovered_before_begin)
    with pytest.raises(TrafficlabError, match="success cleanup recovery inspection complete"):
        study.run_prerequisites(
            r6.url,
            r6.study_id,
            repository_root=repository,
            runner=r6,
            utc_now=lambda: datetime(2026, 8, 18, tzinfo=UTC),
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
    target = study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
        kind="archive",
        destination=destination,
        stage=staged,
        backup=None,
        before_identity=None,
        target_identity=cast(study.JsonObject, identify_bytes(content).as_dict()),
        must_be_absent=True,
    )
    original_unlink = Path.unlink

    def fail_staged_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path == staged:
            raise OSError("simulated pre-journal cleanup unlink failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_staged_unlink)
    assert (
        study._cleanup_prerequisite_rotation_staging(  # pyright: ignore[reportPrivateUsage]
            (target,),
            strict=False,
        )
        == []
    )
    assert staged.read_bytes() == content


@pytest.mark.parametrize("entry_kind", ("symlink", "fifo"))
def test_successful_prerequisite_marker_rejects_nonregular_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_kind: str,
) -> None:
    """A matching marker must be a canonical regular file, never an indirection or device."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    runner = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    prerequisite = repository / "examples" / "validation_study" / "prerequisites.json"
    marker = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / runner.study_id
        / "prerequisites-success.json"
    )
    marker_bytes = marker.read_bytes()
    marker.unlink()
    if entry_kind == "symlink":
        outside = repository / "outside-marker.json"
        outside.write_bytes(marker_bytes)
        marker.symlink_to(outside)
    else:
        os.mkfifo(marker)
        original_read_bytes = Path.read_bytes

        def forbid_fifo_read(path: Path) -> bytes:
            if path == marker:
                raise AssertionError("marker reader must reject a FIFO before opening it")
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", forbid_fifo_read)

    with pytest.raises(TrafficlabError, match="matching successful prerequisite marker"):
        study._require_successful_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id=runner.study_id,
            url=runner.url,
            prerequisite_content=prerequisite.read_bytes(),
        )


def test_successful_prerequisite_marker_and_legacy_archive_use_durable_regular_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal marker path and a bootstrap archive both bind regular canonical bytes durably."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    runner = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    prerequisite = repository / "examples" / "validation_study" / "prerequisites.json"
    study._require_successful_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository,
        study_id=runner.study_id,
        url=runner.url,
        prerequisite_content=prerequisite.read_bytes(),
    )

    archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / runner.study_id
        / "prerequisites.raw.json"
    )
    content = archive.read_bytes()
    archive.unlink()
    fsynced: list[Path] = []
    original_fsync = study._fsync_prerequisite_rotation_directory  # pyright: ignore[reportPrivateUsage]

    def record_fsync(destination: Path) -> None:
        fsynced.append(destination)
        original_fsync(destination)

    monkeypatch.setattr(study, "_fsync_prerequisite_rotation_directory", record_fsync)
    assert (
        study._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id=runner.study_id,
            content=content,
        )
        == content
    )
    assert archive in fsynced
    assert (
        study._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id=runner.study_id,
            content=content,
        )
        == content
    )


def test_prerequisite_rotation_journal_requires_a_stage_for_every_owned_target(tmp_path: Path) -> None:
    """A journal cannot omit a stage path before it records mutable transaction state."""

    target = study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
        kind="archive",
        destination=tmp_path / "archive.json",
        stage=None,
        backup=None,
        before_identity=None,
        target_identity=cast(study.JsonObject, identify_bytes(b"incoming\n").as_dict()),
        must_be_absent=True,
    )

    with pytest.raises(ValueError, match="requires every staged target"):
        study._render_prerequisite_rotation_journal(  # pyright: ignore[reportPrivateUsage]
            tmp_path,
            study_id="study-r5",
            targets=(target,),
        )


def test_prerequisite_rotation_recovery_allows_an_absent_attempt_directory(tmp_path: Path) -> None:
    """Recovery is a no-op before any prerequisite phase has ever allocated an attempt directory."""

    repository = tmp_path / "repository"
    repository.mkdir()
    study._recover_incomplete_prerequisite_rotations(repository)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("cleanup_mode", ("remove", "read-error", "mismatched", "link-failure"))
def test_prerequisite_rotation_exclusive_publication_preserves_or_removes_only_its_own_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_mode: str,
) -> None:
    """A post-link validation failure removes only verified bytes and retains uncertain recovery evidence."""

    destination = tmp_path / "prerequisites.raw.json"
    content = b"canonical prerequisite bytes\n"
    validation_calls = 0
    cleanup_read = False
    original_read = study._read_regular_prerequisite_rotation_target  # pyright: ignore[reportPrivateUsage]
    original_link = study.os.link

    def validate(persisted: bytes) -> None:
        nonlocal cleanup_read, validation_calls
        validation_calls += 1
        assert persisted == content
        if validation_calls == 2:
            cleanup_read = True
            raise ValueError("simulated post-link validation failure")

    def maybe_fail_cleanup_read(path: Path, *, name: str) -> bytes:
        if cleanup_mode == "read-error" and cleanup_read and path == destination:
            raise OSError("simulated uncertain post-link read")
        if cleanup_mode == "mismatched" and cleanup_read and path == destination:
            return b"different retained bytes\n"
        return original_read(path, name=name)

    def fail_before_link(source: str | Path, target: str | Path) -> None:
        if cleanup_mode == "link-failure":
            raise OSError("simulated exclusive publication collision")
        original_link(source, target)

    monkeypatch.setattr(study, "_read_regular_prerequisite_rotation_target", maybe_fail_cleanup_read)
    monkeypatch.setattr(study.os, "link", fail_before_link)
    expected_error = OSError if cleanup_mode == "link-failure" else ValueError
    expected_message = (
        "exclusive publication collision" if cleanup_mode == "link-failure" else "post-link validation failure"
    )
    with pytest.raises(expected_error, match=expected_message):
        study._publish_prerequisite_rotation_exclusive_file(  # pyright: ignore[reportPrivateUsage]
            destination,
            content,
            validate=validate,
            name="test prerequisite archive",
        )

    assert validation_calls == (1 if cleanup_mode == "link-failure" else 2)
    if cleanup_mode in {"read-error", "mismatched"}:
        assert destination.read_bytes() == content
    else:
        assert not destination.exists()
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_prerequisite_rotation_target_guard_paths_are_explicit(tmp_path: Path) -> None:
    """Missing stages/backups cannot silently turn journal recovery into arbitrary deletion."""

    incoming = cast(study.JsonObject, identify_bytes(b"incoming\n").as_dict())
    prior = cast(study.JsonObject, identify_bytes(b"prior\n").as_dict())
    destination = tmp_path / "target.json"
    destination.write_bytes(b"incoming\n")
    missing_stage = study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
        kind="archive",
        destination=destination,
        stage=None,
        backup=None,
        before_identity=None,
        target_identity=incoming,
        must_be_absent=True,
    )
    with pytest.raises(ValueError, match="retain its staged path"):
        study._restore_prerequisite_rotation_target(missing_stage)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="staged before publication"):
        study._publish_prerequisite_rotation_target(missing_stage)  # pyright: ignore[reportPrivateUsage]
    assert (
        study._cleanup_prerequisite_rotation_staging(  # pyright: ignore[reportPrivateUsage]
            (missing_stage,),
            strict=True,
        )
        == []
    )

    wrong_stage = tmp_path / ".target.json.wrong.tmp"
    wrong_stage.write_bytes(b"foreign staging bytes\n")
    wrong_stage_target = study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
        kind="archive",
        destination=destination,
        stage=wrong_stage,
        backup=None,
        before_identity=None,
        target_identity=incoming,
        must_be_absent=True,
    )
    cleanup_failures = study._cleanup_prerequisite_rotation_staging(  # pyright: ignore[reportPrivateUsage]
        (wrong_stage_target,),
        strict=True,
    )
    assert cleanup_failures == [
        f"{wrong_stage}: prerequisite rotation archive stage does not match its transaction-owned identity"
    ]
    assert wrong_stage.read_bytes() == b"foreign staging bytes\n"

    prior_target = study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
        kind="config-short",
        destination=destination,
        stage=tmp_path / ".target.json.stage.tmp",
        backup=None,
        before_identity=prior,
        target_identity=incoming,
        must_be_absent=False,
    )
    with pytest.raises(ValueError, match="prior bytes require a backup"):
        study._restore_prerequisite_rotation_target(prior_target)  # pyright: ignore[reportPrivateUsage]


def test_prerequisite_rotation_completion_rejects_semantically_invalid_target_bytes(tmp_path: Path) -> None:
    """Matching journal hashes alone never bless malformed prerequisite semantics as a completed rotation."""

    repository = tmp_path / "repository"
    repository.mkdir()
    targets: list[study._PrerequisiteRotationTarget] = []  # pyright: ignore[reportPrivateUsage]
    for kind, destination, must_be_absent in study._prerequisite_rotation_expected_targets(  # pyright: ignore[reportPrivateUsage]
        repository,
        "study-r5",
    ):
        content = b"not canonical prerequisite JSON\n" if kind == "root" else b"placeholder\n"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        stage = destination.parent / f".{destination.name}.complete.tmp"
        stage.write_bytes(content)
        targets.append(
            study._PrerequisiteRotationTarget(  # pyright: ignore[reportPrivateUsage]
                kind=kind,
                destination=destination,
                stage=stage,
                backup=None,
                before_identity=None,
                target_identity=cast(study.JsonObject, identify_bytes(content).as_dict()),
                must_be_absent=must_be_absent,
            )
        )

    assert not study._prerequisite_rotation_is_complete(  # pyright: ignore[reportPrivateUsage]
        repository,
        study_id="study-r5",
        targets=targets,
    )


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
        study._recover_incomplete_prerequisite_rotations(repository)  # pyright: ignore[reportPrivateUsage]


def test_prerequisite_rotation_retains_failed_restore_backup_with_its_recovery_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed rollback restore never deletes the only exact prior bytes."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
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
    original_fsync = study._commit_prerequisite_fsync  # pyright: ignore[reportPrivateUsage]
    original_replace = study.os.replace
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

    monkeypatch.setattr(study, "_commit_prerequisite_fsync", fail_canonical_commit)
    monkeypatch.setattr(study.os, "replace", fail_short_restore)
    with pytest.raises(TrafficlabError, match="rollback failed after") as raised:
        study.run_prerequisites(
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
        study.run_prerequisites(
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


@pytest.mark.parametrize("target_name", ("prerequisites.raw.json", "prerequisites-success.json"))
def test_prerequisite_rotation_rejects_late_absent_target_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_name: str,
) -> None:
    """A file created after staging is never overwritten at an absent archive or marker target."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
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
    collision = b"late foreign prerequisite collision\n"
    original_stage = study._stage_prerequisite_rotation_file  # pyright: ignore[reportPrivateUsage]

    def stage_then_collide(
        destination: Path,
        content: bytes,
        *,
        validate: Callable[[Path, bytes], None],
        suffix: str = ".tmp",
    ) -> Path:
        stage = original_stage(destination, content, validate=validate, suffix=suffix)
        if destination.name == target_name and suffix == ".tmp":
            destination.write_bytes(collision)
        return stage

    monkeypatch.setattr(study, "_stage_prerequisite_rotation_file", stage_then_collide)
    with pytest.raises(TrafficlabError, match="absent|exists|collision"):
        study.run_prerequisites(
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
    assert tree_inventory(r4_attempt) == r4_attempt_before
    target = study_root / ".study-work" / "attempts" / r5.study_id / target_name
    assert target.read_bytes() == collision
    assert not tuple(study_root.rglob(".*.tmp"))
    assert not tuple(study_root.rglob(".*.bak"))


def test_prerequisite_rotation_cleans_a_backup_created_before_later_stage_validation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every backup is transaction-owned before its paired incoming stage can fail."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
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
    original_stage = study._stage_prerequisite_rotation_file  # pyright: ignore[reportPrivateUsage]
    backup: Path | None = None

    def fail_short_incoming_stage(
        destination: Path,
        content: bytes,
        *,
        validate: Callable[[Path, bytes], None],
        suffix: str = ".tmp",
    ) -> Path:
        nonlocal backup
        if destination.name == "short.toml" and suffix == ".tmp":
            raise ValueError("simulated incoming stage validation failure")
        staged = original_stage(destination, content, validate=validate, suffix=suffix)
        if destination.name == "short.toml" and suffix == ".bak":
            backup = staged
        return staged

    monkeypatch.setattr(study, "_stage_prerequisite_rotation_file", fail_short_incoming_stage)
    with pytest.raises(TrafficlabError, match="incoming stage validation failure"):
        study.run_prerequisites(
            r5.url,
            r5.study_id,
            repository_root=repository,
            runner=r5,
            utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
        )

    assert backup is not None
    assert not backup.exists()
    canonical_after = {
        path.relative_to(study_root): path.read_bytes()
        for path in sorted(study_root.rglob("*"))
        if path.is_file() and ".study-work" not in path.parts
    }
    assert canonical_after == canonical_before
    assert tree_inventory(r4_attempt) == r4_attempt_before
    r5_attempt = study_root / ".study-work" / "attempts" / r5.study_id
    assert set(path.name for path in r5_attempt.iterdir()) == {"prerequisites.json"}
    assert not tuple(study_root.rglob(".*.tmp"))
    assert not tuple(study_root.rglob(".*.bak"))


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
    study.run_prerequisites(
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

    monkeypatch.setattr(study, "_after_prerequisite_rotation_commit", crash_after_commit, raising=False)
    with pytest.raises(SimulatedCrash, match=f"commit {commit_index}"):
        study.run_prerequisites(
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
    original_begin = study._begin_phase_attempt  # pyright: ignore[reportPrivateUsage]

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
                        study._canonical_json(  # pyright: ignore[reportPrivateUsage]
                            cast(
                                study.JsonObject,
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
                        study._canonical_json(  # pyright: ignore[reportPrivateUsage]
                            cast(
                                study.JsonObject,
                                {"phase": "prerequisites", "study_id": r5.study_id, "url": r5.url},
                            )
                        ),
                    ),
                }
            assert not tuple(study_root.rglob(".*.tmp"))
            assert not tuple(study_root.rglob(".*.bak"))
            raise ValueError("recovery inspection complete")
        return original_begin(root, study_id=study_id, url=url, phase=phase)

    monkeypatch.setattr(study, "_begin_phase_attempt", assert_recovered_before_begin)
    with pytest.raises(TrafficlabError, match="recovery inspection complete"):
        study.run_prerequisites(
            r6.url,
            r6.study_id,
            repository_root=repository,
            runner=r6,
            utc_now=lambda: datetime(2026, 8, 18, tzinfo=UTC),
        )


def test_collection_rejects_old_id_after_prerequisite_rotation_but_keeps_its_raw_archive(tmp_path: Path) -> None:
    """The collector accepts only the current canonical root, never an old ignored archive."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    r4 = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    study.run_prerequisites(
        r4.url,
        r4.study_id,
        repository_root=repository,
        runner=r4,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    r4_archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / r4.study_id
        / "prerequisites.raw.json"
    )
    r4_bytes = r4_archive.read_bytes()
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")
    study.run_prerequisites(
        r5.url,
        r5.study_id,
        repository_root=repository,
        runner=r5,
        utc_now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )
    canonical = repository / "examples" / "validation_study" / "prerequisites.json"

    def forbidden_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise AssertionError("old collection must fail before environment work")

    with pytest.raises(TrafficlabError, match="matching successful prerequisite marker"):
        study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
            repository,
            canonical,
            study_id=r4.study_id,
            url=r4.url,
            runner=cast(study.CommandRunner, forbidden_runner),
            require_successful_prerequisite=True,
        )

    assert r4_archive.read_bytes() == r4_bytes


def test_prerequisite_rotation_refuses_symlink_and_cleans_replacement_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a regular canonical prerequisite file is eligible for atomic rotation."""

    repository = tmp_path / "repository"
    repository.mkdir()
    destination = repository / "examples" / "validation_study" / "prerequisites.json"
    destination.parent.mkdir(parents=True)
    outside = repository / "outside.json"
    outside.write_bytes(b"outside\n")
    destination.symlink_to(outside)

    with pytest.raises(TrafficlabError, match="regular"):
        study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
            destination,
            valid_prerequisite(),
            repository_root=repository,
            replace_existing=True,
        )

    assert destination.is_symlink()
    assert outside.read_bytes() == b"outside\n"
    assert not tuple(destination.parent.glob(".prerequisites.json.*"))

    destination.unlink()
    destination.write_bytes(b"r4\n")

    def fail_replace(_source: str | Path, _target: str | Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(study.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
            destination,
            valid_prerequisite(),
            repository_root=repository,
            replace_existing=True,
        )

    assert destination.read_bytes() == b"r4\n"
    assert not tuple(destination.parent.glob(".prerequisites.json.*"))


def test_prerequisite_archive_refuses_a_preexisting_symlink(tmp_path: Path) -> None:
    """A raw prerequisite archive cannot follow an attacker-controlled attempt entry."""

    repository = tmp_path / "repository"
    archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / "study-r4"
        / "prerequisites.raw.json"
    )
    archive.parent.mkdir(parents=True)
    outside = repository / "outside.json"
    outside.write_bytes(b"canonical\n")
    archive.symlink_to(outside)

    with pytest.raises(ValueError, match="regular"):
        study._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id="study-r4",
            content=b"canonical\n",
        )

    assert archive.is_symlink()
    assert outside.read_bytes() == b"canonical\n"


def test_prerequisite_rotation_preserves_current_file_when_replacement_setup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacement setup cannot alter a current regular prerequisite publication."""

    repository = tmp_path / "repository"
    repository.mkdir()
    destination = repository / "examples" / "validation_study" / "prerequisites.json"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"r4\n")
    original_lstat = Path.lstat
    calls = 0

    def fail_second_destination_lstat(path: Path) -> os.stat_result:
        nonlocal calls
        if path == destination:
            calls += 1
            if calls == 3:
                raise OSError("simulated lstat failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_second_destination_lstat)
    with pytest.raises(ValueError, match="could not inspect"):
        study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
            destination,
            valid_prerequisite(),
            repository_root=repository,
            replace_existing=True,
        )

    assert destination.read_bytes() == b"r4\n"
    assert not tuple(destination.parent.glob(".prerequisites.json.*"))


def test_prerequisite_rotation_cleans_temp_when_replacement_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed descriptor wrapper leaves the old prerequisite bytes and no sibling temp."""

    repository = tmp_path / "repository"
    repository.mkdir()
    destination = repository / "examples" / "validation_study" / "prerequisites.json"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"r4\n")

    def fail_fdopen(_descriptor: int, _mode: str) -> None:
        raise OSError("simulated replacement fdopen failure")

    monkeypatch.setattr(study.os, "fdopen", fail_fdopen)
    with pytest.raises(OSError, match="replacement fdopen failure"):
        study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
            destination,
            valid_prerequisite(),
            repository_root=repository,
            replace_existing=True,
        )

    assert destination.read_bytes() == b"r4\n"
    assert not tuple(destination.parent.glob(".prerequisites.json.*"))


def test_checked_config_create_race_is_reported_without_a_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legacy exclusive-create path retains its race handling for first publication."""

    destination = tmp_path / "short.toml"
    original_open = Path.open

    def fail_config_create(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> object:
        if path == destination and mode == "xb":
            raise FileExistsError("simulated config create race")
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", fail_config_create)
    with pytest.raises(ValueError, match="config target already exists"):
        study._write_new_config(destination, b"[run]\n")  # pyright: ignore[reportPrivateUsage]

    assert not destination.exists()


def test_prerequisite_publication_rejects_noncanonical_validated_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prerequisite publication codec rejects a parser/render disagreement before link or replace."""

    repository = tmp_path / "repository"
    repository.mkdir()
    destination = repository / "prerequisites.json"
    original_render = study.render_prerequisite_results
    calls = 0

    def render_once_then_mismatch(value: study.PrerequisiteResults) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_render(value)
        return b"{}\n"

    monkeypatch.setattr(study, "render_prerequisite_results", render_once_then_mismatch)
    with pytest.raises(ValueError, match="persisted prerequisite JSON is not canonical"):
        study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
            destination,
            valid_prerequisite(),
            repository_root=repository,
        )

    assert not destination.exists()
    assert not tuple(destination.parent.glob(".prerequisites.json.*"))


def test_prerequisite_archive_reports_existing_archive_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archive read errors are never treated as a matching prior prerequisite document."""

    repository = tmp_path / "repository"
    archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / "study-r4"
        / "prerequisites.raw.json"
    )
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"canonical\n")
    original_read_bytes = Path.read_bytes

    def fail_archive_read(path: Path) -> bytes:
        if path == archive:
            raise OSError("simulated archive read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_archive_read)
    with pytest.raises(ValueError, match="could not read archived prerequisite document"):
        study._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id="study-r4",
            content=b"canonical\n",
        )


def test_prerequisite_archive_reports_existing_archive_lstat_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archive lstat failures cannot be mistaken for an absent attempt record."""

    repository = tmp_path / "repository"
    archive = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / "study-r4"
        / "prerequisites.raw.json"
    )
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"canonical\n")
    original_lstat = Path.lstat
    calls = 0

    def fail_second_archive_lstat(path: Path) -> os.stat_result:
        nonlocal calls
        if path == archive:
            calls += 1
            if calls == 2:
                raise OSError("simulated archive lstat failure")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_second_archive_lstat)
    with pytest.raises(ValueError, match="could not inspect archived prerequisite document"):
        study._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id="study-r4",
            content=b"canonical\n",
        )


def test_collect_cli_freezes_its_attempt_before_any_input_bridge_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every post-syntax collection failure consumes the study ID before bridge validation."""

    repository = tmp_path / "repository"
    repository.mkdir()
    marker = repository / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1" / "collection.json"
    candidate = repository / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"
    calls = 0

    def reject_bridge(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        assert marker.is_file()
        raise TrafficlabError("synthetic input bridge failure", corrective_action="preserve the attempt")

    monkeypatch.setattr(study, "_collection_inputs_from_prerequisites", reject_bridge)
    argv = (
        "collect",
        "--url",
        "https://downloads.example.test/object.bin",
        "--study-id",
        "study-1",
        "--prerequisites",
        "examples/validation_study/prerequisites.json",
    )

    assert study.main(argv, repository_root=repository) == 2
    assert marker.is_file()
    assert not candidate.exists()
    assert study.main(argv, repository_root=repository) == 2
    assert calls == 1


def test_public_prerequisites_then_collect_binds_the_raw_published_marker_before_transformation(tmp_path: Path) -> None:
    """The public phase transition checks schema-1 publication bytes before schema-3 retention."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    scripted = ScriptedPrerequisiteRunner(repository)
    now = datetime(2026, 8, 16, tzinfo=UTC)
    assert (
        study.main(
            ("prerequisites", "--url", scripted.url, "--study-id", scripted.study_id),
            repository_root=repository,
            runner=scripted,
            utc_now=lambda: now,
        )
        == 0
    )

    collection_builds: list[tuple[str, ...]] = []
    collection_cleanups: list[tuple[str, ...]] = []

    def collection_runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        command = tuple(argv)
        if command == ("git", "rev-parse", "HEAD^{tree}"):
            return subprocess.CompletedProcess(command, 0, stdout=b"d" * 40 + b"\n", stderr=b"")
        if command == (
            "docker",
            "image",
            "inspect",
            f"trafficlab-validation-{scripted.study_id}:collection-capture",
            "--format",
            "{{.Id}}",
        ):
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"not present\n")
        if command[:2] == ("docker", "build"):
            collection_builds.append(command)
            iidfile = Path(command[command.index("--iidfile") + 1])
            iidfile.write_text(f"{scripted.capture_id}\n", encoding="ascii")
            return subprocess.CompletedProcess(command, 0, stdout=b"rebuilt\n", stderr=b"")
        if command == ("docker", "image", "inspect", scripted.capture_id, "--format", "{{.Id}}"):
            return subprocess.CompletedProcess(command, 0, stdout=f"{scripted.capture_id}\n".encode(), stderr=b"")
        if command == (
            "docker",
            "image",
            "rm",
            "--force",
            f"trafficlab-validation-{scripted.study_id}:collection-capture",
        ):
            collection_cleanups.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=b"removed\n", stderr=b"")
        return scripted(argv, cwd=cwd, check=check, capture_output=capture_output, shell=shell, timeout=timeout)

    training_calls: list[Path] = []

    def stop_at_training(path: Path) -> RunResult:
        training_calls.append(path)
        raise ValueError("training callback reached")

    argv = (
        "collect",
        "--url",
        scripted.url,
        "--study-id",
        scripted.study_id,
        "--prerequisites",
        "examples/validation_study/prerequisites.json",
    )
    with pytest.raises(ValueError, match="training callback reached"):
        study.main(
            argv,
            repository_root=repository,
            runner=collection_runner,
            run=stop_at_training,
            capture=lambda _path: pytest.fail("held-out capture must not begin"),
        )

    prerequisite = repository / "examples" / "validation_study" / "prerequisites.json"
    attempt = repository / "examples" / "validation_study" / ".study-work" / "attempts" / scripted.study_id
    candidate = repository / "examples" / "validation_study" / "evidence" / ".candidates" / scripted.study_id
    success = cast(dict[str, object], json.loads((attempt / "prerequisites-success.json").read_text(encoding="utf-8")))
    assert success["prerequisites_identity"] == identify_bytes(prerequisite.read_bytes()).as_dict()
    assert (candidate / "prerequisites.json").read_bytes() != prerequisite.read_bytes()
    assert (attempt / "collection.json").is_file()
    assert (attempt / "frozen-protocol.json").is_file()
    assert len(training_calls) == 1
    assert collection_builds == [
        study.cold_capture_build_argv(  # pyright: ignore[reportPrivateUsage]
            f"trafficlab-validation-{scripted.study_id}:collection-capture",
            attempt / "collection-capture.iid",
        )
    ]
    assert collection_cleanups == [
        ("docker", "image", "rm", "--force", f"trafficlab-validation-{scripted.study_id}:collection-capture")
    ]
    assert not (attempt / "collection-capture.iid").exists()
    assert study.main(argv, repository_root=repository, runner=collection_runner) == 2
    assert len(training_calls) == 1
