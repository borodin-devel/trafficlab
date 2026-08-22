"""Rotation behavior."""

from __future__ import annotations

import json
import os
import tempfile as tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

import scripts.validation_study.common as vs_common
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.prerequisites.run as vs_prereq_run
import scripts.validation_study.rotation.run as vs_rotation_run
import scripts.validation_study.rotation.schema as vs_rotation_schema
import scripts.validation_study.workloads as vs_workloads
from tests.support.validation_study.artifacts import tree_inventory
from tests.support.validation_study.builders import valid_prerequisite
from tests.support.validation_study.runners import ScriptedPrerequisiteRunner, write_prerequisite_repository_inputs
from tests.unit.validation.study.prerequisites._support import (
    write_legacy_prerequisite_root,
)
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.errors import TrafficlabError


def test_rotation_bootstraps_a_matching_legacy_raw_archive_before_replacement(tmp_path: Path) -> None:
    """A schema-1 root and success marker gain a forensic archive before r5 overwrites them."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    root, r4_bytes = write_legacy_prerequisite_root(repository)
    r4_archive = root.parent / ".study-work" / "attempts" / "study-r4" / "prerequisites.raw.json"
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")

    vs_prereq_run.run_prerequisites(
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
    marker.write_bytes(vs_common.canonical_json(cast(vs_common.JsonObject, marker_value)))
    r5 = ScriptedPrerequisiteRunner(repository, study_id="study-r5")

    with pytest.raises(TrafficlabError, match="matching successful prerequisite marker"):
        vs_prereq_run.run_prerequisites(
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
        vs_prereq_run.run_prerequisites(
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
        vs_prereq_run.run_prerequisites(
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
    vs_prereq_run.run_prerequisites(
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
        vs_prereq_run.run_prerequisites(
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
    vs_prereq_run.run_prerequisites(
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
    result = vs_prereq_run.run_prerequisites(
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
    assert vs_prereq_codec.parse_prerequisite_results(r5_bytes, repository_root=repository) == result
    assert r4_archive.read_bytes() == r4_bytes
    assert r5_archive.read_bytes() == r5_bytes
    r5_marker = json.loads((r5_archive.with_name("prerequisites-success.json")).read_text(encoding="utf-8"))
    assert r5_marker["prerequisites_identity"] == identify_bytes(r5_archive.read_bytes()).as_dict()
    assert vs_rotation_run.validate_base_configs(repository, result) == {
        name: vs_workloads.build_base_config(
            workload,
            repository_root=repository,
            study_id=r5.study_id,
            url=r5.url,
            capture_image_id=r5.capture_id,
        )
        for name, workload in ((workload.name, workload) for workload in vs_workloads.workload_specs(r5.url))
    }


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
        vs_common.read_regular_prerequisite_rotation_target(
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
        vs_common.read_regular_prerequisite_rotation_target(
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
        vs_rotation_run._stage_prerequisite_rotation_file(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"canonical\n",
            validate=reject_stage,
        )
    assert not tuple(tmp_path.glob(".*.tmp"))

    closed: list[int] = []
    original_close = os.close

    def fail_fdopen(_descriptor: int, _mode: str) -> None:
        raise OSError("simulated staging fdopen failure")

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    monkeypatch.setattr(os, "close", record_close)
    with pytest.raises(OSError, match="staging fdopen failure"):
        vs_rotation_run._stage_prerequisite_rotation_file(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"canonical\n",
            validate=lambda _stage, _content: None,
        )
    assert closed
    assert not tuple(tmp_path.glob(".*.tmp"))

    monkeypatch.undo()

    def fail_mkstemp(*_args: object, **_kwargs: object) -> tuple[int, str]:
        raise OSError("simulated staging mkstemp failure")

    monkeypatch.setattr(tempfile, "mkstemp", fail_mkstemp)
    with pytest.raises(OSError, match="staging mkstemp failure"):
        vs_rotation_run._stage_prerequisite_rotation_file(  # pyright: ignore[reportPrivateUsage]
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
        vs_rotation_run._stage_prerequisite_rotation_file(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"canonical\n",
            validate=reject_stage,
        )
    monkeypatch.undo()
    for temporary in tmp_path.glob(".*.tmp"):
        temporary.unlink()


def test_successful_prerequisite_marker_and_legacy_archive_use_durable_regular_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal marker path and a bootstrap archive both bind regular canonical bytes durably."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    runner = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    vs_prereq_run.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    prerequisite = repository / "examples" / "validation_study" / "prerequisites.json"
    vs_rotation_run.require_successful_prerequisite_attempt(
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
    original_fsync = vs_rotation_run._fsync_prerequisite_rotation_directory  # pyright: ignore[reportPrivateUsage]

    def record_fsync(destination: Path) -> None:
        fsynced.append(destination)
        original_fsync(destination)

    monkeypatch.setattr(vs_rotation_run, "_fsync_prerequisite_rotation_directory", record_fsync)
    assert (
        vs_rotation_run._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id=runner.study_id,
            content=content,
        )
        == content
    )
    assert archive in fsynced
    assert (
        vs_rotation_run._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id=runner.study_id,
            content=content,
        )
        == content
    )


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
    original_read = vs_common.read_regular_prerequisite_rotation_target
    original_link = os.link

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

    monkeypatch.setattr(vs_rotation_run, "read_regular_prerequisite_rotation_target", maybe_fail_cleanup_read)
    monkeypatch.setattr(os, "link", fail_before_link)
    expected_error = OSError if cleanup_mode == "link-failure" else ValueError
    expected_message = (
        "exclusive publication collision" if cleanup_mode == "link-failure" else "post-link validation failure"
    )
    with pytest.raises(expected_error, match=expected_message):
        vs_rotation_run._publish_prerequisite_rotation_exclusive_file(  # pyright: ignore[reportPrivateUsage]
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

    incoming = cast(vs_common.JsonObject, identify_bytes(b"incoming\n").as_dict())
    prior = cast(vs_common.JsonObject, identify_bytes(b"prior\n").as_dict())
    destination = tmp_path / "target.json"
    destination.write_bytes(b"incoming\n")
    missing_stage = vs_rotation_schema.PrerequisiteRotationTarget(
        kind="archive",
        destination=destination,
        stage=None,
        backup=None,
        before_identity=None,
        target_identity=incoming,
        must_be_absent=True,
    )
    with pytest.raises(ValueError, match="retain its staged path"):
        vs_rotation_run._restore_prerequisite_rotation_target(missing_stage)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="staged before publication"):
        vs_rotation_run._publish_prerequisite_rotation_target(missing_stage)  # pyright: ignore[reportPrivateUsage]
    assert (
        vs_rotation_run._cleanup_prerequisite_rotation_staging(  # pyright: ignore[reportPrivateUsage]
            (missing_stage,),
            strict=True,
        )
        == []
    )

    wrong_stage = tmp_path / ".target.json.wrong.tmp"
    wrong_stage.write_bytes(b"foreign staging bytes\n")
    wrong_stage_target = vs_rotation_schema.PrerequisiteRotationTarget(
        kind="archive",
        destination=destination,
        stage=wrong_stage,
        backup=None,
        before_identity=None,
        target_identity=incoming,
        must_be_absent=True,
    )
    cleanup_failures = vs_rotation_run._cleanup_prerequisite_rotation_staging(  # pyright: ignore[reportPrivateUsage]
        (wrong_stage_target,),
        strict=True,
    )
    assert cleanup_failures == [
        f"{wrong_stage}: prerequisite rotation archive stage does not match its transaction-owned identity"
    ]
    assert wrong_stage.read_bytes() == b"foreign staging bytes\n"

    prior_target = vs_rotation_schema.PrerequisiteRotationTarget(
        kind="config-short",
        destination=destination,
        stage=tmp_path / ".target.json.stage.tmp",
        backup=None,
        before_identity=prior,
        target_identity=incoming,
        must_be_absent=False,
    )
    with pytest.raises(ValueError, match="prior bytes require a backup"):
        vs_rotation_run._restore_prerequisite_rotation_target(  # pyright: ignore[reportPrivateUsage]
            prior_target
        )


def test_prerequisite_rotation_completion_rejects_semantically_invalid_target_bytes(tmp_path: Path) -> None:
    """Matching journal hashes alone never bless malformed prerequisite semantics as a completed rotation."""

    repository = tmp_path / "repository"
    repository.mkdir()
    targets: list[vs_rotation_schema.PrerequisiteRotationTarget] = []
    for kind, destination, must_be_absent in vs_rotation_schema.prerequisite_rotation_expected_targets(
        repository,
        "study-r5",
    ):
        content = b"not canonical prerequisite JSON\n" if kind == "root" else b"placeholder\n"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        stage = destination.parent / f".{destination.name}.complete.tmp"
        stage.write_bytes(content)
        targets.append(
            vs_rotation_schema.PrerequisiteRotationTarget(
                kind=kind,
                destination=destination,
                stage=stage,
                backup=None,
                before_identity=None,
                target_identity=cast(vs_common.JsonObject, identify_bytes(content).as_dict()),
                must_be_absent=must_be_absent,
            )
        )

    assert not vs_rotation_run._prerequisite_rotation_is_complete(  # pyright: ignore[reportPrivateUsage]
        repository,
        study_id="study-r5",
        targets=targets,
    )


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
    collision = b"late foreign prerequisite collision\n"
    original_stage = vs_rotation_run._stage_prerequisite_rotation_file  # pyright: ignore[reportPrivateUsage]

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

    monkeypatch.setattr(vs_rotation_run, "_stage_prerequisite_rotation_file", stage_then_collide)
    with pytest.raises(TrafficlabError, match="absent|exists|collision"):
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
    original_stage = vs_rotation_run._stage_prerequisite_rotation_file  # pyright: ignore[reportPrivateUsage]
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

    monkeypatch.setattr(vs_rotation_run, "_stage_prerequisite_rotation_file", fail_short_incoming_stage)
    with pytest.raises(TrafficlabError, match="incoming stage validation failure"):
        vs_prereq_run.run_prerequisites(
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
        vs_prereq_run.publish_prerequisites(
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

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        vs_prereq_run.publish_prerequisites(
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
        vs_rotation_run._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
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
        vs_prereq_run.publish_prerequisites(
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

    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    with pytest.raises(OSError, match="replacement fdopen failure"):
        vs_prereq_run.publish_prerequisites(
            destination,
            valid_prerequisite(),
            repository_root=repository,
            replace_existing=True,
        )

    assert destination.read_bytes() == b"r4\n"
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
        vs_rotation_run._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
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
        vs_rotation_run._archive_prerequisite_raw_document(  # pyright: ignore[reportPrivateUsage]
            repository,
            study_id="study-r4",
            content=b"canonical\n",
        )
