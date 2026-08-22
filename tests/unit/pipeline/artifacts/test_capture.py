import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.artifacts.capture as artifacts
import trafficlab.artifacts.io as artifact_io
from tests.support.artifacts import capture_publication_setup
from tests.support.artifacts import capture_sources as _capture_sources
from trafficlab.artifacts.capture import (
    CapturePublication,
    load_or_recover_capture_pair,
    publish_capture_pair,
)
from trafficlab.capture.validation import CaptureInspection
from trafficlab.common.errors import DeadlineExceededError, TrafficlabError


def _publish_capture_sources(
    sources: tuple[Path, Path], run_directory: Path, *, target_success: bool = True
) -> CapturePublication:
    return publish_capture_pair(
        *sources,
        run_directory,
        target_success=target_success,
        deadline=None,
        clock=lambda: 0.0,
    )


def test_load_or_recover_capture_pair_reuses_only_a_stable_valid_pair(tmp_path: Path) -> None:
    """Returning an unverified or changed pair could let capture reuse the wrong workload evidence."""
    run_directory = tmp_path / "run"
    metadata_path, pcapng_path = _capture_sources(run_directory, timestamp=1.0)
    metadata_path.rename(run_directory / "capture.json")
    pcapng_path.rename(run_directory / "reference.pcapng")

    inspection = load_or_recover_capture_pair(run_directory, deadline=None, clock=lambda: 0.0)

    assert type(inspection) is CaptureInspection
    assert inspection.packet_count == 1


def test_load_or_recover_capture_pair_returns_none_for_an_absent_pair(tmp_path: Path) -> None:
    """An absent pair must request capture without creating recovery state."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()

    assert load_or_recover_capture_pair(run_directory, deadline=None, clock=lambda: 0.0) is None
    assert list(run_directory.iterdir()) == []


@pytest.mark.parametrize("existing", ["metadata", "invalid-pair"], ids=["incomplete", "invalid"])
def test_load_or_recover_capture_pair_removes_only_a_stable_invalid_pair(tmp_path: Path, existing: str) -> None:
    """Leaving invalid canonical names would make the subsequent exclusive publication fail."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    (run_directory / "capture.json").write_bytes(b"invalid")
    if existing == "invalid-pair":
        (run_directory / "reference.pcapng").write_bytes(b"invalid")
    sentinel = run_directory / "keep.txt"
    sentinel.write_bytes(b"caller-owned")

    assert load_or_recover_capture_pair(run_directory, deadline=None, clock=lambda: 0.0) is None
    assert {path.name for path in run_directory.iterdir()} == {"keep.txt"}
    assert sentinel.read_bytes() == b"caller-owned"


def test_load_or_recover_capture_pair_preserves_a_valid_replacement_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reuse must reject rather than return an inspection for bytes replaced during validation."""
    run_directory = tmp_path / "run"
    metadata_path, pcapng_path = _capture_sources(run_directory, timestamp=1.0)
    metadata_path.rename(run_directory / "capture.json")
    pcapng_path.rename(run_directory / "reference.pcapng")
    winner_metadata, winner_pcapng = _capture_sources(tmp_path / "winner", timestamp=2.0)
    winner_bytes = (winner_metadata.read_bytes(), winner_pcapng.read_bytes())
    real_validate = artifacts.validate_capture_pair

    def validate_then_replace(
        candidate_metadata: Path,
        candidate_pcapng: Path,
        *,
        deadline: float | None,
        clock: Callable[[], float],
    ) -> CaptureInspection:
        inspection = real_validate(candidate_metadata, candidate_pcapng, deadline=deadline, clock=clock)
        os.replace(winner_metadata, candidate_metadata)
        os.replace(winner_pcapng, candidate_pcapng)
        return inspection

    monkeypatch.setattr(artifacts, "validate_capture_pair", validate_then_replace)

    with pytest.raises(TrafficlabError, match="changed during valid-pair validation"):
        load_or_recover_capture_pair(run_directory, deadline=None, clock=lambda: 0.0)

    assert (run_directory / "capture.json").read_bytes() == winner_bytes[0]
    assert (run_directory / "reference.pcapng").read_bytes() == winner_bytes[1]


def test_load_or_recover_capture_pair_preserves_a_replacement_during_invalid_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery must not remove a pair installed after the invalid bytes were inspected."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    metadata_path = run_directory / "capture.json"
    pcapng_path = run_directory / "reference.pcapng"
    metadata_path.write_bytes(b"invalid")
    pcapng_path.write_bytes(b"invalid")
    winner_metadata, winner_pcapng = _capture_sources(tmp_path / "winner", timestamp=3.0)
    winner_bytes = (winner_metadata.read_bytes(), winner_pcapng.read_bytes())
    real_validate = artifacts.validate_capture_pair

    def reject_then_replace(
        candidate_metadata: Path,
        candidate_pcapng: Path,
        *,
        deadline: float | None,
        clock: Callable[[], float],
    ) -> CaptureInspection:
        try:
            return real_validate(candidate_metadata, candidate_pcapng, deadline=deadline, clock=clock)
        except TrafficlabError:
            os.replace(winner_metadata, candidate_metadata)
            os.replace(winner_pcapng, candidate_pcapng)
            raise

    monkeypatch.setattr(artifacts, "validate_capture_pair", reject_then_replace)

    with pytest.raises(TrafficlabError, match="changed during invalid-pair recovery"):
        load_or_recover_capture_pair(run_directory, deadline=None, clock=lambda: 0.0)

    assert metadata_path.read_bytes() == winner_bytes[0]
    assert pcapng_path.read_bytes() == winner_bytes[1]


def test_load_or_recover_capture_pair_propagates_the_exact_deadline(tmp_path: Path) -> None:
    """Dropping the caller's deadline would permit unbounded reuse validation."""
    run_directory = tmp_path / "run"
    metadata_path, pcapng_path = _capture_sources(run_directory)
    metadata_path.rename(run_directory / "capture.json")
    pcapng_path.rename(run_directory / "reference.pcapng")

    with pytest.raises(DeadlineExceededError, match="deadline"):
        load_or_recover_capture_pair(run_directory, deadline=4.0, clock=lambda: 4.0)


@pytest.mark.parametrize("failure", ["stat", "read"], ids=["identity", "validation"])
def test_load_or_recover_capture_pair_translates_raw_filesystem_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """A raw filesystem failure must preserve the pair and remain a package error."""
    run_directory = tmp_path / "run"
    metadata_path, pcapng_path = _capture_sources(run_directory)
    metadata_path.rename(run_directory / "capture.json")
    pcapng_path.rename(run_directory / "reference.pcapng")
    metadata_bytes = (run_directory / "capture.json").read_bytes()
    pcapng_bytes = (run_directory / "reference.pcapng").read_bytes()

    if failure == "stat":
        real_stat = Path.stat

        def fail_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
            if path == run_directory / "capture.json":
                raise OSError("injected reuse stat failure")
            return real_stat(path, follow_symlinks=follow_symlinks)

        monkeypatch.setattr(Path, "stat", fail_stat)
    else:

        def fail_read(*args: object, **kwargs: object) -> CaptureInspection:
            del args, kwargs
            raise OSError("injected reuse read failure")

        monkeypatch.setattr(artifacts, "validate_capture_pair", fail_read)

    with pytest.raises(TrafficlabError, match=f"reuse {failure} failure"):
        load_or_recover_capture_pair(run_directory, deadline=None, clock=lambda: 0.0)

    assert (run_directory / "capture.json").read_bytes() == metadata_bytes
    assert (run_directory / "reference.pcapng").read_bytes() == pcapng_bytes


def test_invalid_existing_pair_recovery_translates_quarantine_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure to remove a quarantined invalid artifact must retain it and stop publication."""
    sources, run_directory = capture_publication_setup(tmp_path)
    invalid_metadata = run_directory / "capture.json"
    invalid_metadata.write_bytes(b"invalid")
    real_unlink = os.unlink

    def fail_quarantine_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        path_object = Path(path)
        if path_object.parent.name.startswith(".capture-recovery."):
            raise OSError("injected quarantine unlink failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "unlink", fail_quarantine_unlink)

    with pytest.raises(TrafficlabError, match="quarantine unlink failure"):
        _publish_capture_sources(sources, run_directory, target_success=True)

    quarantined = list(run_directory.glob(".capture-recovery.*/*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"invalid"
    assert not invalid_metadata.exists()
    assert not (run_directory / "reference.pcapng").exists()


def test_existing_pair_deadline_expiry_preserves_both_artifacts(tmp_path: Path) -> None:
    """A budget expiry is not evidence that an existing capture pair is invalid."""
    run_directory = tmp_path / "run"
    existing_metadata, existing_pcapng = _capture_sources(run_directory)
    existing_metadata.rename(run_directory / "capture.json")
    existing_pcapng.rename(run_directory / "reference.pcapng")
    before = {path.name: path.read_bytes() for path in run_directory.iterdir()}

    with pytest.raises(DeadlineExceededError, match="deadline"):
        publish_capture_pair(
            tmp_path / "missing.json",
            tmp_path / "missing.pcapng",
            run_directory,
            target_success=True,
            deadline=1.0,
            clock=lambda: 1.0,
        )

    assert {path.name: path.read_bytes() for path in run_directory.iterdir()} == before


def test_invalid_pair_in_a_deadline_named_path_is_recovered_without_false_timeout(tmp_path: Path) -> None:
    """A pathname word must not classify an ordinary validation failure as deadline expiry."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "deadline-case"
    run_directory.mkdir()
    (run_directory / "capture.json").write_bytes(b"invalid")
    (run_directory / "reference.pcapng").write_bytes(b"invalid")

    publication = _publish_capture_sources(sources, run_directory, target_success=True)

    assert publication.inspection.packet_count == 1
    assert (
        artifacts.validate_capture_pair(
            run_directory / "capture.json",
            run_directory / "reference.pcapng",
            deadline=None,
            clock=lambda: 0.0,
        ).packet_count
        == 1
    )


def test_invalid_pair_recovery_preserves_a_concurrent_valid_race_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery must not unlink a valid pair that replaced the invalid files after validation."""
    sources, run_directory = capture_publication_setup(tmp_path)
    metadata_path = run_directory / "capture.json"
    pcapng_path = run_directory / "reference.pcapng"
    metadata_path.write_bytes(b"invalid")
    pcapng_path.write_bytes(b"invalid")
    winner_directory = tmp_path / "winner"
    winner_metadata, winner_pcapng = _capture_sources(winner_directory, timestamp=2.0)
    winner_bytes = (winner_metadata.read_bytes(), winner_pcapng.read_bytes())
    real_validate = artifacts.validate_capture_pair

    def validate_then_replace(
        candidate_metadata: Path,
        candidate_pcapng: Path,
        *,
        deadline: float | None,
        clock: Callable[[], float],
    ) -> CaptureInspection:
        try:
            return real_validate(candidate_metadata, candidate_pcapng, deadline=deadline, clock=clock)
        except TrafficlabError:
            os.replace(winner_metadata, metadata_path)
            os.replace(winner_pcapng, pcapng_path)
            raise

    monkeypatch.setattr(artifacts, "validate_capture_pair", validate_then_replace)

    with pytest.raises(TrafficlabError, match="changed during invalid-pair recovery"):
        _publish_capture_sources(sources, run_directory, target_success=True)

    assert metadata_path.read_bytes() == winner_bytes[0]
    assert pcapng_path.read_bytes() == winner_bytes[1]


def test_invalid_pair_recovery_translates_raw_identity_stat_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw identity-read error must not escape the artifact boundary."""
    sources, run_directory = capture_publication_setup(tmp_path)
    metadata_path = run_directory / "capture.json"
    metadata_path.write_bytes(b"invalid")
    real_stat = Path.stat

    def fail_capture_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == metadata_path:
            raise OSError("injected identity stat failure")
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", fail_capture_stat)

    with pytest.raises(TrafficlabError, match="could not inspect capture artifact.*identity stat failure") as error:
        _publish_capture_sources(sources, run_directory, target_success=True)

    assert error.value.corrective_action
    assert metadata_path.read_bytes() == b"invalid"


def test_target_failure_stale_pair_recovery_preserves_a_concurrent_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diagnostic publication must not delete a reusable pair installed during stale-pair recovery."""
    sources, run_directory = capture_publication_setup(tmp_path)
    metadata_path = run_directory / "capture.json"
    pcapng_path = run_directory / "reference.pcapng"
    metadata_path.write_bytes(b"stale")
    pcapng_path.write_bytes(b"stale")
    winner_metadata, winner_pcapng = _capture_sources(tmp_path / "winner", timestamp=4.0)
    winner_bytes = (winner_metadata.read_bytes(), winner_pcapng.read_bytes())
    real_stat = Path.stat
    metadata_stat_calls = 0

    def replace_before_recovery(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        nonlocal metadata_stat_calls
        if path == metadata_path:
            metadata_stat_calls += 1
            if metadata_stat_calls == 2:
                os.replace(winner_metadata, metadata_path)
                os.replace(winner_pcapng, pcapng_path)
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", replace_before_recovery)

    with pytest.raises(TrafficlabError, match="changed during invalid-pair recovery"):
        _publish_capture_sources(sources, run_directory, target_success=False)

    assert metadata_path.read_bytes() == winner_bytes[0]
    assert pcapng_path.read_bytes() == winner_bytes[1]
    assert not (run_directory / "diagnostic-capture.json").exists()
    assert not (run_directory / "diagnostic-reference.pcapng").exists()


@pytest.mark.parametrize("replacement_move", [1, 2], ids=["first-member", "second-member"])
@pytest.mark.parametrize("target_success", [True, False], ids=["successful-target", "failed-target"])
def test_capture_pair_recovery_restores_a_complete_winner_swapped_at_atomic_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_move: int,
    target_success: bool,
) -> None:
    """Atomic removal must restore both winner members even when the swap occurs inside that boundary."""
    sources, run_directory = capture_publication_setup(tmp_path)
    metadata_path = run_directory / "capture.json"
    pcapng_path = run_directory / "reference.pcapng"
    metadata_path.write_bytes(b"invalid metadata")
    pcapng_path.write_bytes(b"invalid pcapng")
    winner_metadata, winner_pcapng = _capture_sources(tmp_path / "winner", timestamp=5.0)
    winner_bytes = (winner_metadata.read_bytes(), winner_pcapng.read_bytes())
    real_rename = os.rename
    move_count = 0

    def replace_then_move(source: str | Path, destination: str | Path) -> None:
        nonlocal move_count
        move_count += 1
        if move_count == replacement_move:
            os.replace(winner_metadata, metadata_path)
            os.replace(winner_pcapng, pcapng_path)
        real_rename(source, destination)

    monkeypatch.setattr(artifacts.os, "rename", replace_then_move)

    with pytest.raises(TrafficlabError, match="changed during invalid-pair recovery"):
        _publish_capture_sources(sources, run_directory, target_success=target_success)

    assert metadata_path.read_bytes() == winner_bytes[0]
    assert pcapng_path.read_bytes() == winner_bytes[1]
    assert not (run_directory / "diagnostic-capture.json").exists()
    assert not (run_directory / "diagnostic-reference.pcapng").exists()


def test_recovery_conflict_preserves_newer_canonical_and_moved_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An occupied restore path must preserve both the newer canonical file and quarantined winner."""
    sources, run_directory = capture_publication_setup(tmp_path)
    metadata_path = run_directory / "capture.json"
    pcapng_path = run_directory / "reference.pcapng"
    metadata_path.write_bytes(b"invalid metadata")
    pcapng_path.write_bytes(b"invalid pcapng")
    winner_metadata, winner_pcapng = _capture_sources(tmp_path / "winner", timestamp=6.0)
    moved_winner = winner_metadata.read_bytes()
    pcapng_winner = winner_pcapng.read_bytes()
    real_rename = os.rename

    def replace_move_and_occupy(source: str | Path, destination: str | Path) -> None:
        os.replace(winner_metadata, metadata_path)
        os.replace(winner_pcapng, pcapng_path)
        real_rename(source, destination)
        metadata_path.write_bytes(b"still newer canonical")

    monkeypatch.setattr(artifacts.os, "rename", replace_move_and_occupy)

    with pytest.raises(TrafficlabError, match="canonical path.*is occupied.*preserved at"):
        _publish_capture_sources(sources, run_directory, target_success=True)

    assert metadata_path.read_bytes() == b"still newer canonical"
    assert pcapng_path.read_bytes() == pcapng_winner
    quarantined = list(run_directory.glob(".capture-recovery.*/*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == moved_winner


def test_recovery_restore_link_error_preserves_moved_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A restore-link failure must retain the moved winner at its reported quarantine path."""
    sources, run_directory = capture_publication_setup(tmp_path)
    metadata_path = run_directory / "capture.json"
    pcapng_path = run_directory / "reference.pcapng"
    metadata_path.write_bytes(b"invalid")
    pcapng_path.write_bytes(b"invalid")
    winner_metadata, winner_pcapng = _capture_sources(tmp_path / "winner", timestamp=7.0)
    moved_winner = winner_metadata.read_bytes()
    real_rename = os.rename
    real_link = os.link

    def replace_then_move(source: str | Path, destination: str | Path) -> None:
        os.replace(winner_metadata, metadata_path)
        os.replace(winner_pcapng, pcapng_path)
        real_rename(source, destination)

    def fail_recovery_link(source: str | Path, destination: str | Path) -> None:
        if Path(source).parent.name.startswith(".capture-recovery."):
            raise OSError("injected restore link failure")
        real_link(source, destination)

    monkeypatch.setattr(artifacts.os, "rename", replace_then_move)
    monkeypatch.setattr(artifacts.os, "link", fail_recovery_link)

    with pytest.raises(TrafficlabError, match="could not restore.*restore link failure.*preserved at"):
        _publish_capture_sources(sources, run_directory, target_success=True)

    quarantined = list(run_directory.glob(".capture-recovery.*/*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == moved_winner
    assert not metadata_path.exists()


@pytest.mark.parametrize("failure", ["unlink", "rmdir"], ids=["recovery-link", "recovery-directory"])
def test_recovery_restore_cleanup_failure_keeps_canonical_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Cleanup after exclusive restoration must never remove the restored canonical winner."""
    sources, run_directory = capture_publication_setup(tmp_path)
    metadata_path = run_directory / "capture.json"
    pcapng_path = run_directory / "reference.pcapng"
    metadata_path.write_bytes(b"invalid")
    pcapng_path.write_bytes(b"invalid")
    winner_metadata, winner_pcapng = _capture_sources(tmp_path / "winner", timestamp=8.0)
    winner_bytes = winner_metadata.read_bytes()
    real_rename = os.rename
    real_unlink = os.unlink
    real_rmdir = Path.rmdir

    def replace_then_move(source: str | Path, destination: str | Path) -> None:
        os.replace(winner_metadata, metadata_path)
        os.replace(winner_pcapng, pcapng_path)
        real_rename(source, destination)

    def maybe_fail_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        if failure == "unlink" and Path(path).parent.name.startswith(".capture-recovery."):
            raise OSError("injected recovery link cleanup failure")
        real_unlink(path, *args, **kwargs)

    def maybe_fail_rmdir(path: Path) -> None:
        if failure == "rmdir" and path.name.startswith(".capture-recovery."):
            raise OSError("injected recovery directory cleanup failure")
        real_rmdir(path)

    monkeypatch.setattr(artifacts.os, "rename", replace_then_move)
    monkeypatch.setattr(artifacts.os, "unlink", maybe_fail_unlink)
    monkeypatch.setattr(Path, "rmdir", maybe_fail_rmdir)

    with pytest.raises(
        TrafficlabError, match=f"recovery {'link' if failure == 'unlink' else 'directory'} cleanup failure"
    ):
        _publish_capture_sources(sources, run_directory, target_success=True)

    assert metadata_path.read_bytes() == winner_bytes


def test_recovery_translates_quarantine_creation_and_atomic_move_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raw quarantine preparation and atomic-move failures must remain artifact errors."""
    sources, run_directory = capture_publication_setup(tmp_path)
    metadata_path = run_directory / "capture.json"
    metadata_path.write_bytes(b"invalid")

    def fail_mkdtemp(*args: object, **kwargs: object) -> str:
        raise OSError("injected quarantine creation failure")

    monkeypatch.setattr(artifacts.tempfile, "mkdtemp", fail_mkdtemp)

    with pytest.raises(TrafficlabError, match="quarantine creation failure"):
        _publish_capture_sources(sources, run_directory, target_success=True)

    monkeypatch.undo()

    def fail_rename(_source: str | Path, _destination: str | Path) -> None:
        raise OSError("injected atomic move failure")

    monkeypatch.setattr(artifacts.os, "rename", fail_rename)

    with pytest.raises(TrafficlabError, match="atomic move failure"):
        _publish_capture_sources(sources, run_directory, target_success=True)

    assert metadata_path.read_bytes() == b"invalid"


def test_recovery_reports_empty_quarantine_directory_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty creator-owned quarantine that cannot be removed must be reported after invalid-file deletion."""
    sources, run_directory = capture_publication_setup(tmp_path)
    metadata_path = run_directory / "capture.json"
    metadata_path.write_bytes(b"invalid")
    real_rmdir = Path.rmdir

    def fail_recovery_rmdir(path: Path) -> None:
        if path.name.startswith(".capture-recovery."):
            raise OSError("injected empty quarantine cleanup failure")
        real_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", fail_recovery_rmdir)

    with pytest.raises(TrafficlabError, match="empty quarantine cleanup failure"):
        _publish_capture_sources(sources, run_directory, target_success=True)

    assert not metadata_path.exists()
    assert len(list(run_directory.glob(".capture-recovery.*"))) == 1


def test_publish_capture_pair_reuses_a_complete_valid_existing_pair_without_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry must not replace a valid reference, even when new source files are unusable."""
    run_directory = tmp_path / "run"
    existing_metadata, existing_pcapng = _capture_sources(run_directory, timestamp=1.0)
    existing_metadata.rename(run_directory / "capture.json")
    existing_pcapng.rename(run_directory / "reference.pcapng")
    existing_bytes = {
        path.name: path.read_bytes() for path in (run_directory / "capture.json", run_directory / "reference.pcapng")
    }

    def reject_link(_source: str | Path, _destination: str | Path) -> None:
        raise AssertionError("valid reuse must not publish")

    monkeypatch.setattr(artifacts.os, "link", reject_link)

    publication = _publish_capture_sources((tmp_path / "missing.json", tmp_path / "missing.pcapng"), run_directory)

    assert publication.inspection.packet_count == 1
    assert publication.created_by_call is False
    assert publication.owned_identity is None
    assert {
        path.name: path.read_bytes() for path in (run_directory / "capture.json", run_directory / "reference.pcapng")
    } == existing_bytes


@pytest.mark.parametrize("existing_kind", ["incomplete", "invalid"], ids=["incomplete", "invalid"])
def test_publish_capture_pair_recovers_only_the_exact_invalid_artifact_pair(tmp_path: Path, existing_kind: str) -> None:
    """Recovery must replace the two known stage paths without deleting adjacent diagnostics."""
    sources, run_directory = capture_publication_setup(tmp_path)
    sentinel = run_directory / "keep.txt"
    sentinel.write_text("unowned", encoding="utf-8")
    (run_directory / "capture.json").write_bytes(
        sources[0].read_bytes() if existing_kind == "incomplete" else b"invalid"
    )
    if existing_kind == "invalid":
        (run_directory / "reference.pcapng").write_bytes(b"invalid")

    publication = _publish_capture_sources(sources, run_directory, target_success=True)

    assert publication.inspection.packet_count == 1
    assert publication.created_by_call is True
    assert publication.owned_identity is not None
    assert sentinel.read_text(encoding="utf-8") == "unowned"
    assert set(path.name for path in run_directory.iterdir()) == {
        "capture.json",
        "reference.pcapng",
        "keep.txt",
    }


def test_publish_capture_pair_validates_temps_then_links_metadata_before_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publishing PCAPNG first could expose a reusable-looking reference without its metadata."""
    sources, run_directory = capture_publication_setup(tmp_path)
    real_link = os.link
    real_directory_fsync = artifact_io.fsync_containing_directory
    operations: list[str] = []

    def observed_link(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        assert source_path.name.startswith(".capture-pair.")
        operations.append(f"link:{Path(destination).name}")
        real_link(source, destination)

    def observed_directory_fsync(path: Path) -> None:
        operations.append(f"fsync:{path.name}")
        real_directory_fsync(path)

    monkeypatch.setattr(artifacts.os, "link", observed_link)
    monkeypatch.setattr(artifact_io, "fsync_containing_directory", observed_directory_fsync)

    _publish_capture_sources(sources, run_directory, target_success=True)

    assert operations == ["link:capture.json", "link:reference.pcapng", "fsync:reference.pcapng"]
    assert list(run_directory.glob(".capture-pair.*.tmp")) == []


def test_publish_capture_pair_directory_durability_failure_preserves_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory durability failure must preserve the fully linked capture pair."""
    sources, run_directory = capture_publication_setup(tmp_path)

    def fail_directory_fsync(_path: Path) -> None:
        raise TrafficlabError("injected capture directory fsync failure", corrective_action="repair storage")

    monkeypatch.setattr(artifact_io, "fsync_containing_directory", fail_directory_fsync)

    with pytest.raises(TrafficlabError, match="capture directory fsync failure") as caught:
        _publish_capture_sources(sources, run_directory, target_success=True)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "publication_failed",
        "capture",
        "capture pair",
        "preserved",
    )
    assert (
        artifacts.validate_capture_pair(
            run_directory / "capture.json",
            run_directory / "reference.pcapng",
            deadline=None,
            clock=lambda: 0.0,
        ).packet_count
        == 1
    )
    assert list(run_directory.glob(".capture-pair.*.tmp")) == []


def test_publish_capture_pair_failure_between_links_is_incomplete_and_not_reusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second-link failure must never cause metadata alone to be reported as reusable."""
    sources, run_directory = capture_publication_setup(tmp_path)
    real_link = os.link
    link_count = 0

    def fail_second_link(source: str | Path, destination: str | Path) -> None:
        nonlocal link_count
        link_count += 1
        if link_count == 2:
            raise OSError("injected reference publication failure")
        real_link(source, destination)

    monkeypatch.setattr(artifacts.os, "link", fail_second_link)

    with pytest.raises(TrafficlabError, match="reference publication failure"):
        _publish_capture_sources(sources, run_directory, target_success=True)

    assert (run_directory / "capture.json").is_file()
    assert not (run_directory / "reference.pcapng").exists()
    assert list(run_directory.glob(".capture-pair.*.tmp")) == []
    with pytest.raises(TrafficlabError, match="capture validation failed"):
        artifacts.validate_capture_pair(
            run_directory / "capture.json",
            run_directory / "reference.pcapng",
            deadline=None,
            clock=lambda: 0.0,
        )


def test_publish_capture_pair_collision_preserves_the_race_winner_and_cleans_each_temp_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exclusive publication must preserve a racing reference and never retry creator cleanup."""
    sources, run_directory = capture_publication_setup(tmp_path)
    real_link = os.link
    real_unlink = os.unlink
    cleaned: list[Path] = []
    winner = b"racing reference\n"
    link_count = 0

    def collide_on_reference(source: str | Path, destination: str | Path) -> None:
        nonlocal link_count
        link_count += 1
        destination_path = Path(destination)
        if link_count == 2:
            destination_path.write_bytes(winner)
        real_link(source, destination)

    def observed_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        path_object = Path(path)
        if path_object.name.startswith(".capture-pair."):
            cleaned.append(path_object)
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "link", collide_on_reference)
    monkeypatch.setattr(artifacts.os, "unlink", observed_unlink)

    with pytest.raises(TrafficlabError, match="already exists"):
        _publish_capture_sources(sources, run_directory, target_success=True)

    assert (run_directory / "reference.pcapng").read_bytes() == winner
    assert len(cleaned) == 2
    assert len(set(cleaned)) == 2


def test_target_failure_publishes_only_deterministic_diagnostic_capture_files(tmp_path: Path) -> None:
    """A natural nonzero target status must not leave a reusable reference pair."""
    sources, run_directory = capture_publication_setup(tmp_path)

    publication = _publish_capture_sources(sources, run_directory, target_success=False)

    assert publication.inspection.packet_count == 1
    assert publication.created_by_call is False
    assert publication.owned_identity is None
    assert set(path.name for path in run_directory.iterdir()) == {
        "diagnostic-capture.json",
        "diagnostic-reference.pcapng",
    }
    assert not (run_directory / "capture.json").exists()
    assert not (run_directory / "reference.pcapng").exists()


def test_target_failure_removes_only_stale_reusable_pair_before_publishing_diagnostics(tmp_path: Path) -> None:
    """A failed target attempt must not leave exact reusable stage names beside its diagnostics."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    existing_metadata, existing_pcapng = _capture_sources(run_directory, timestamp=1.0)
    existing_metadata.rename(run_directory / "capture.json")
    existing_pcapng.rename(run_directory / "reference.pcapng")
    sentinel = run_directory / "keep.txt"
    sentinel.write_text("unowned", encoding="utf-8")

    _publish_capture_sources(sources, run_directory, target_success=False)

    assert set(path.name for path in run_directory.iterdir()) == {
        "diagnostic-capture.json",
        "diagnostic-reference.pcapng",
        "keep.txt",
    }


def test_publish_capture_pair_requires_boolean_target_success(tmp_path: Path) -> None:
    """Truthy status values could accidentally publish a failed target as reusable."""
    sources = _capture_sources(tmp_path / "sources")

    with pytest.raises(TrafficlabError, match="target_success must be a boolean"):
        publish_capture_pair(
            *sources,
            tmp_path,
            target_success=cast(bool, 1),
            deadline=None,
            clock=lambda: 0.0,
        )


def test_publish_capture_pair_translates_missing_source_without_leaving_a_temp(tmp_path: Path) -> None:
    """A raw source-open error would omit stage recovery and could leave a false artifact."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()

    with pytest.raises(TrafficlabError, match="could not prepare capture artifact") as error:
        publish_capture_pair(
            tmp_path / "missing.json",
            tmp_path / "missing.pcapng",
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert error.value.corrective_action
    assert list(run_directory.iterdir()) == []


def test_publish_capture_pair_translates_validation_and_reports_temp_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation remains primary while each failed creator-temp cleanup is attempted once."""
    sources = _capture_sources(tmp_path / "sources")
    sources[1].write_bytes(b"invalid")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    real_unlink = os.unlink
    attempts: list[Path] = []

    def fail_metadata_temp_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        path_object = Path(path)
        if path_object.name.startswith(".capture-pair.metadata."):
            attempts.append(path_object)
            raise OSError("injected metadata temp cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "unlink", fail_metadata_temp_unlink)

    with pytest.raises(
        TrafficlabError,
        match="capture validation failed.*cleanup incomplete.*metadata temp cleanup failure",
    ) as error:
        _publish_capture_sources(sources, run_directory, target_success=True)

    assert error.value.corrective_action == "replace the capture output with a complete valid capture pair"
    assert len(attempts) == 1
    assert attempts[0].exists()


def test_publish_capture_pair_reports_post_publication_temp_cleanup_failure_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cleanup failure after both links must preserve the valid published pair and avoid retries."""
    sources, run_directory = capture_publication_setup(tmp_path)
    real_unlink = os.unlink
    attempts: list[Path] = []

    def fail_metadata_temp_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        path_object = Path(path)
        if path_object.name.startswith(".capture-pair.metadata."):
            attempts.append(path_object)
            raise OSError("injected post-publication cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "unlink", fail_metadata_temp_unlink)

    publication = _publish_capture_sources(sources, run_directory, target_success=True)

    assert len(attempts) == 1
    assert publication.created_by_call is True
    assert publication.owned_identity is not None
    assert publication.warnings == (
        f"could not remove owned temporary file {attempts[0]}: injected post-publication cleanup failure",
    )
    assert (
        artifacts.validate_capture_pair(
            run_directory / "capture.json",
            run_directory / "reference.pcapng",
            deadline=None,
            clock=lambda: 0.0,
        ).packet_count
        == 1
    )


@pytest.mark.parametrize(
    ("created_by_call", "owned_identity", "error"),
    [
        (False, None, "inspection"),
        (False, ((1, 2, 3, 4), (5, 6, 7, 8)), "reused publication"),
        (True, None, "created publication"),
        (True, ((1, 2, 3, 4), None), "owned_identity"),
        (True, ((-1, 2, 3, 4), (5, 6, 7, 8)), "owned_identity"),
        (cast(bool, 1), None, "created_by_call"),
    ],
)
def test_capture_publication_strictly_ties_ownership_to_exact_pair_identity(
    tmp_path: Path,
    created_by_call: bool,
    owned_identity: object,
    error: str,
) -> None:
    """Ambiguous ownership could make later rollback remove a reused or replaced pair."""
    sources, run_directory = capture_publication_setup(tmp_path)
    valid = _publish_capture_sources(sources, run_directory, target_success=True)

    with pytest.raises((TypeError, ValueError), match=error):
        CapturePublication(
            inspection=valid.inspection if error != "inspection" else cast(CaptureInspection, object()),
            created_by_call=created_by_call,
            owned_identity=cast(Any, owned_identity),
        )


@pytest.mark.parametrize("warnings", [[], ("",), (cast(str, 1),)])
def test_capture_publication_rejects_invalid_warning_collections(tmp_path: Path, warnings: object) -> None:
    """Warnings must remain ordered nonempty strings so lifecycle logging is deterministic."""
    sources, run_directory = capture_publication_setup(tmp_path)
    valid = _publish_capture_sources(sources, run_directory, target_success=True)

    with pytest.raises((TypeError, ValueError), match="warnings"):
        CapturePublication(
            inspection=valid.inspection,
            created_by_call=valid.created_by_call,
            owned_identity=valid.owned_identity,
            warnings=cast(Any, warnings),
        )


def test_publish_capture_pair_does_not_claim_a_replacement_installed_after_both_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ownership must derive from creator files, not canonical identities sampled after a race."""
    sources, run_directory = capture_publication_setup(tmp_path)
    winner_metadata, winner_pcapng = _capture_sources(tmp_path / "winner", timestamp=9.0)
    winner_bytes = (winner_metadata.read_bytes(), winner_pcapng.read_bytes())
    real_link = os.link
    link_count = 0

    def replace_after_second_link(source: str | Path, destination: str | Path) -> None:
        nonlocal link_count
        link_count += 1
        real_link(source, destination)
        if link_count == 2:
            os.replace(winner_metadata, run_directory / "capture.json")
            os.replace(winner_pcapng, run_directory / "reference.pcapng")

    monkeypatch.setattr(artifacts.os, "link", replace_after_second_link)

    with pytest.raises(TrafficlabError, match="changed during publication"):
        _publish_capture_sources(sources, run_directory, target_success=True)

    assert (run_directory / "capture.json").read_bytes() == winner_bytes[0]
    assert (run_directory / "reference.pcapng").read_bytes() == winner_bytes[1]


def test_capture_publication_rollback_is_noop_for_reuse_and_rejects_wrong_type(tmp_path: Path) -> None:
    """The public rollback boundary must make the non-ownership branch explicit and strict."""
    sources, run_directory = capture_publication_setup(tmp_path)
    created = _publish_capture_sources(sources, run_directory, target_success=True)
    reused = CapturePublication(created.inspection, False, None)
    before = {path.name: path.read_bytes() for path in run_directory.iterdir()}

    artifacts.rollback_capture_publication(run_directory, reused)

    assert {path.name: path.read_bytes() for path in run_directory.iterdir()} == before
    with pytest.raises(TypeError, match="publication"):
        artifacts.rollback_capture_publication(run_directory, cast(CapturePublication, object()))


def test_new_pair_publication_preserves_structured_deadline_failure(tmp_path: Path) -> None:
    """Publication translation must not erase the deadline type needed by lifecycle arbitration."""
    sources, run_directory = capture_publication_setup(tmp_path)

    with pytest.raises(DeadlineExceededError, match="deadline"):
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=1.0,
            clock=lambda: 1.0,
        )


def test_capture_temp_fsync_failure_reports_creator_cleanup_failure_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preparation failure must retain fsync as primary and report bounded owned-temp cleanup."""
    sources, run_directory = capture_publication_setup(tmp_path)
    real_unlink = os.unlink
    attempts: list[Path] = []

    def fail_fsync(_file_descriptor: int) -> None:
        raise OSError("injected capture temp fsync failure")

    def fail_temp_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        path_object = Path(path)
        if path_object.name.startswith(".capture-pair.metadata."):
            attempts.append(path_object)
            raise OSError("injected creator cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "fsync", fail_fsync)
    monkeypatch.setattr(artifacts.os, "unlink", fail_temp_unlink)

    with pytest.raises(
        TrafficlabError,
        match="capture temp fsync failure.*cleanup incomplete.*creator cleanup failure",
    ):
        _publish_capture_sources(sources, run_directory, target_success=True)

    assert len(attempts) == 1
    assert attempts[0].exists()
