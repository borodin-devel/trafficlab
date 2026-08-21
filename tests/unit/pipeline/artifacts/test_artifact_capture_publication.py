import os
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.artifacts.capture as artifacts
import trafficlab.artifacts.io as artifact_io
from tests.support.artifacts import capture_sources as _capture_sources
from trafficlab.artifacts.capture import (
    CapturePublication,
    publish_capture_pair,
)
from trafficlab.capture.validation import CaptureInspection
from trafficlab.common.errors import DeadlineExceededError, TrafficlabError


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

    publication = publish_capture_pair(
        tmp_path / "missing.json",
        tmp_path / "missing.pcapng",
        run_directory,
        target_success=True,
        deadline=None,
        clock=lambda: 0.0,
    )

    assert publication.inspection.packet_count == 1
    assert publication.created_by_call is False
    assert publication.owned_identity is None
    assert {
        path.name: path.read_bytes() for path in (run_directory / "capture.json", run_directory / "reference.pcapng")
    } == existing_bytes


@pytest.mark.parametrize("existing_kind", ["incomplete", "invalid"], ids=["incomplete", "invalid"])
def test_publish_capture_pair_recovers_only_the_exact_invalid_artifact_pair(tmp_path: Path, existing_kind: str) -> None:
    """Recovery must replace the two known stage paths without deleting adjacent diagnostics."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    sentinel = run_directory / "keep.txt"
    sentinel.write_text("unowned", encoding="utf-8")
    (run_directory / "capture.json").write_bytes(
        sources[0].read_bytes() if existing_kind == "incomplete" else b"invalid"
    )
    if existing_kind == "invalid":
        (run_directory / "reference.pcapng").write_bytes(b"invalid")

    publication = publish_capture_pair(
        *sources,
        run_directory,
        target_success=True,
        deadline=None,
        clock=lambda: 0.0,
    )

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
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
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

    publish_capture_pair(
        *sources,
        run_directory,
        target_success=True,
        deadline=None,
        clock=lambda: 0.0,
    )

    assert operations == ["link:capture.json", "link:reference.pcapng", "fsync:reference.pcapng"]
    assert list(run_directory.glob(".capture-pair.*.tmp")) == []


def test_publish_capture_pair_directory_durability_failure_preserves_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()

    def fail_directory_fsync(_path: Path) -> None:
        raise TrafficlabError("injected capture directory fsync failure", corrective_action="repair storage")

    monkeypatch.setattr(artifact_io, "fsync_containing_directory", fail_directory_fsync)

    with pytest.raises(TrafficlabError, match="capture directory fsync failure") as caught:
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

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
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
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
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

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
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
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
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert (run_directory / "reference.pcapng").read_bytes() == winner
    assert len(cleaned) == 2
    assert len(set(cleaned)) == 2


def test_target_failure_publishes_only_deterministic_diagnostic_capture_files(tmp_path: Path) -> None:
    """A natural nonzero target status must not leave a reusable reference pair."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()

    publication = publish_capture_pair(
        *sources,
        run_directory,
        target_success=False,
        deadline=None,
        clock=lambda: 0.0,
    )

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

    publish_capture_pair(
        *sources,
        run_directory,
        target_success=False,
        deadline=None,
        clock=lambda: 0.0,
    )

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
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert error.value.corrective_action == "replace the capture output with a complete valid capture pair"
    assert len(attempts) == 1
    assert attempts[0].exists()


def test_publish_capture_pair_reports_post_publication_temp_cleanup_failure_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cleanup failure after both links must preserve the valid published pair and avoid retries."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    real_unlink = os.unlink
    attempts: list[Path] = []

    def fail_metadata_temp_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        path_object = Path(path)
        if path_object.name.startswith(".capture-pair.metadata."):
            attempts.append(path_object)
            raise OSError("injected post-publication cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(artifacts.os, "unlink", fail_metadata_temp_unlink)

    publication = publish_capture_pair(
        *sources,
        run_directory,
        target_success=True,
        deadline=None,
        clock=lambda: 0.0,
    )

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
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    valid = publish_capture_pair(
        *sources,
        run_directory,
        target_success=True,
        deadline=None,
        clock=lambda: 0.0,
    )

    with pytest.raises((TypeError, ValueError), match=error):
        CapturePublication(
            inspection=valid.inspection if error != "inspection" else cast(CaptureInspection, object()),
            created_by_call=created_by_call,
            owned_identity=cast(Any, owned_identity),
        )


@pytest.mark.parametrize("warnings", [[], ("",), (cast(str, 1),)])
def test_capture_publication_rejects_invalid_warning_collections(tmp_path: Path, warnings: object) -> None:
    """Warnings must remain ordered nonempty strings so lifecycle logging is deterministic."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    valid = publish_capture_pair(
        *sources,
        run_directory,
        target_success=True,
        deadline=None,
        clock=lambda: 0.0,
    )

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
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
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
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert (run_directory / "capture.json").read_bytes() == winner_bytes[0]
    assert (run_directory / "reference.pcapng").read_bytes() == winner_bytes[1]


def test_capture_publication_rollback_is_noop_for_reuse_and_rejects_wrong_type(tmp_path: Path) -> None:
    """The public rollback boundary must make the non-ownership branch explicit and strict."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    created = publish_capture_pair(
        *sources,
        run_directory,
        target_success=True,
        deadline=None,
        clock=lambda: 0.0,
    )
    reused = CapturePublication(created.inspection, False, None)
    before = {path.name: path.read_bytes() for path in run_directory.iterdir()}

    artifacts.rollback_capture_publication(run_directory, reused)

    assert {path.name: path.read_bytes() for path in run_directory.iterdir()} == before
    with pytest.raises(TypeError, match="publication"):
        artifacts.rollback_capture_publication(run_directory, cast(CapturePublication, object()))


def test_new_pair_publication_preserves_structured_deadline_failure(tmp_path: Path) -> None:
    """Publication translation must not erase the deadline type needed by lifecycle arbitration."""
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()

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
    sources = _capture_sources(tmp_path / "sources")
    run_directory = tmp_path / "run"
    run_directory.mkdir()
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
        publish_capture_pair(
            *sources,
            run_directory,
            target_success=True,
            deadline=None,
            clock=lambda: 0.0,
        )

    assert len(attempts) == 1
    assert attempts[0].exists()
