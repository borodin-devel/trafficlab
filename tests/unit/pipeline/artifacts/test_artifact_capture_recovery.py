import os
from collections.abc import Callable
from pathlib import Path

import pytest

import trafficlab.artifacts.capture as artifacts
from tests.support.artifacts import capture_sources as _capture_sources
from trafficlab.artifacts.capture import (
    load_or_recover_capture_pair,
    publish_capture_pair,
)
from trafficlab.capture.validation import CaptureInspection
from trafficlab.common.errors import DeadlineExceededError, TrafficlabError


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
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
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
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

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

    publication = publish_capture_pair(
        *sources,
        run_directory,
        target_success=True,
        deadline=None,
        clock=lambda: 0.0,
    )

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
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
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
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert metadata_path.read_bytes() == winner_bytes[0]
    assert pcapng_path.read_bytes() == winner_bytes[1]


def test_invalid_pair_recovery_translates_raw_identity_stat_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw identity-read error must not escape the artifact boundary."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    metadata_path = run_directory / "capture.json"
    metadata_path.write_bytes(b"invalid")
    real_stat = Path.stat

    def fail_capture_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == metadata_path:
            raise OSError("injected identity stat failure")
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", fail_capture_stat)

    with pytest.raises(TrafficlabError, match="could not inspect capture artifact.*identity stat failure") as error:
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert error.value.corrective_action
    assert metadata_path.read_bytes() == b"invalid"


def test_target_failure_stale_pair_recovery_preserves_a_concurrent_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diagnostic publication must not delete a reusable pair installed during stale-pair recovery."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
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
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=False,
            deadline=None,
            clock=lambda: 0.0,
        )

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
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
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
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=target_success,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert metadata_path.read_bytes() == winner_bytes[0]
    assert pcapng_path.read_bytes() == winner_bytes[1]
    assert not (run_directory / "diagnostic-capture.json").exists()
    assert not (run_directory / "diagnostic-reference.pcapng").exists()


def test_recovery_conflict_preserves_newer_canonical_and_moved_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An occupied restore path must preserve both the newer canonical file and quarantined winner."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
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
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert metadata_path.read_bytes() == b"still newer canonical"
    assert pcapng_path.read_bytes() == pcapng_winner
    quarantined = list(run_directory.glob(".capture-recovery.*/*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == moved_winner


def test_recovery_restore_link_error_preserves_moved_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A restore-link failure must retain the moved winner at its reported quarantine path."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
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
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    quarantined = list(run_directory.glob(".capture-recovery.*/*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == moved_winner
    assert not metadata_path.exists()


@pytest.mark.parametrize("failure", ["unlink", "rmdir"], ids=["recovery-link", "recovery-directory"])
def test_recovery_restore_cleanup_failure_keeps_canonical_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Cleanup after exclusive restoration must never remove the restored canonical winner."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
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
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert metadata_path.read_bytes() == winner_bytes


def test_recovery_translates_quarantine_creation_and_atomic_move_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raw quarantine preparation and atomic-move failures must remain artifact errors."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    metadata_path = run_directory / "capture.json"
    metadata_path.write_bytes(b"invalid")

    def fail_mkdtemp(*args: object, **kwargs: object) -> str:
        raise OSError("injected quarantine creation failure")

    monkeypatch.setattr(artifacts.tempfile, "mkdtemp", fail_mkdtemp)

    with pytest.raises(TrafficlabError, match="quarantine creation failure"):
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    monkeypatch.undo()

    def fail_rename(_source: str | Path, _destination: str | Path) -> None:
        raise OSError("injected atomic move failure")

    monkeypatch.setattr(artifacts.os, "rename", fail_rename)

    with pytest.raises(TrafficlabError, match="atomic move failure"):
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert metadata_path.read_bytes() == b"invalid"


def test_recovery_reports_empty_quarantine_directory_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty creator-owned quarantine that cannot be removed must be reported after invalid-file deletion."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    metadata_path = run_directory / "capture.json"
    metadata_path.write_bytes(b"invalid")
    real_rmdir = Path.rmdir

    def fail_recovery_rmdir(path: Path) -> None:
        if path.name.startswith(".capture-recovery."):
            raise OSError("injected empty quarantine cleanup failure")
        real_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", fail_recovery_rmdir)

    with pytest.raises(TrafficlabError, match="empty quarantine cleanup failure"):
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert not metadata_path.exists()
    assert len(list(run_directory.glob(".capture-recovery.*"))) == 1
