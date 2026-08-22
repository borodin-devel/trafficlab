from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.artifacts.generated as artifact_module
import trafficlab.artifacts.io as artifact_io
import trafficlab.generation.stage as generation_module
from tests.support.generation import (
    authoritative_trace,
    log_records,
    prepare_stage_run,
)
from tests.support.scapy_fixtures import encode_events as encode_legacy_pcapng
from trafficlab.artifacts.generated import publish_generated_pcapng
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import (
    CaptureMetadata,
    Direction,
    TraceEvent,
    TrafficTrace,
)
from trafficlab.generation.stage import generate_experiment

pytestmark = pytest.mark.integration


def test_stage_rejects_a_post_publication_round_trip_mismatch(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stage result must expose events parsed from the exact published bytes, not pre-render values."""
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    real_parse = read_pcapng_bytes

    def change_stage_parse(
        content: bytes,
        metadata: CaptureMetadata,
        *,
        source: Path,
    ) -> TrafficTrace:
        parsed = real_parse(content, metadata, source=source)
        return parsed[:-1]

    monkeypatch.setattr(generation_module, "read_pcapng_bytes", change_stage_parse)

    with pytest.raises(TrafficlabError, match="round-trip"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert (run_directory / "generated.pcapng").exists()
    assert log_records(run_directory)[-1]["event"] == "stage_failed"


def test_stage_rejects_a_post_publication_timestamp_above_stored_window(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stage must independently enforce parsed timestamps inside stored W."""
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    real_parse = read_pcapng_bytes

    def move_stage_parse_outside_window(
        content: bytes,
        metadata: CaptureMetadata,
        *,
        source: Path,
    ) -> TrafficTrace:
        parsed = real_parse(content, metadata, source=source)
        return TrafficTrace.from_events(
            (*parsed[:-1].to_events(), TraceEvent(10.000000001, parsed[-1].direction, parsed[-1].frame_length))
        )

    monkeypatch.setattr(generation_module, "read_pcapng_bytes", move_stage_parse_outside_window)

    with pytest.raises(TrafficlabError, match="outside.*observation window"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert (run_directory / "generated.pcapng").exists()
    assert log_records(run_directory)[-1]["event"] == "stage_failed"


def test_stage_rejects_and_preserves_a_different_existing_output(
    valid_config_data: dict[str, object],
    tmp_path: Path,
) -> None:
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    destination = run_directory / "generated.pcapng"
    destination.write_bytes(b"preserve")

    with pytest.raises(TrafficlabError, match="already exists"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert destination.read_bytes() == b"preserve"


def test_stage_success_log_failure_leaves_output_reusable(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    real_append = generation_module.append_run_log

    def fail_success(_run_directory: Path, record: object) -> None:
        assert cast(dict[str, object], record)["event"] == "generated_pcapng_published"
        raise TrafficlabError("success log failure", corrective_action="repair log")

    monkeypatch.setattr(generation_module, "append_run_log", fail_success)
    with pytest.raises(TrafficlabError, match="published.*success logging failed"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    published = (run_directory / "generated.pcapng").read_bytes()
    monkeypatch.setattr(generation_module, "append_run_log", real_append)
    retried = generate_experiment(experiment_path, clock=lambda: 0.0)

    assert retried.reused is True
    assert retried.generated_path.read_bytes() == published


def test_publication_rejects_raw_event_compatibility_input(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()

    with pytest.raises(TypeError, match="expected trace must be a TrafficTrace"):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=cast(TrafficTrace, generated_events),
            observation_window_seconds=1.0,
        )


def test_publication_fsyncs_and_validates_owned_temp_before_exclusive_link(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linking before durable parse validation could expose truncated or semantically changed bytes."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    real_fsync = os.fsync
    real_directory_fsync = artifact_io.fsync_containing_directory
    real_parse = read_pcapng_bytes
    real_link = os.link
    operations: list[str] = []

    def observe_fsync(file_descriptor: int) -> None:
        operations.append("fsync")
        real_fsync(file_descriptor)

    def observe_parse(
        content: bytes,
        capture_metadata: CaptureMetadata,
        *,
        source: Path,
    ) -> TrafficTrace:
        assert content == encoded
        assert source.name.startswith(".generated.pcapng.")
        operations.append("parse")
        return real_parse(content, capture_metadata, source=source)

    def observe_link(source: str | Path, destination: str | Path) -> None:
        assert operations == ["fsync", "parse"]
        operations.append("link")
        real_link(source, destination)

    def observe_directory_fsync(path: Path) -> None:
        assert operations == ["fsync", "parse", "link"]
        operations.append("fsync-directory")
        real_directory_fsync(path)

    monkeypatch.setattr(artifact_module.os, "fsync", observe_fsync)
    monkeypatch.setattr(artifact_module, "read_pcapng_bytes", observe_parse)
    monkeypatch.setattr(artifact_module.os, "link", observe_link)
    monkeypatch.setattr(artifact_io, "fsync_containing_directory", observe_directory_fsync)

    publication = publish_generated_pcapng(
        run_directory,
        encoded,
        metadata=metadata,
        expected_trace=authoritative_trace(generated_events, metadata),
        observation_window_seconds=1.0,
    )

    assert publication.path == run_directory / "generated.pcapng"
    assert publication.created_by_call is True
    assert publication.content == encoded
    assert operations == ["fsync", "parse", "link", "fsync-directory", "fsync"]
    assert publication.path.read_bytes() == encoded
    assert list(run_directory.glob(".generated.pcapng.*.tmp")) == []


def test_generated_directory_durability_failure_preserves_published_capture(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    destination = run_directory / "generated.pcapng"

    def fail_directory_fsync(_path: Path) -> None:
        raise TrafficlabError("injected generated directory fsync failure", corrective_action="repair storage")

    monkeypatch.setattr(artifact_io, "fsync_containing_directory", fail_directory_fsync)

    with pytest.raises(TrafficlabError, match="generated directory fsync failure") as caught:
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "publication_failed",
        "generate",
        "generated.pcapng",
        "preserved",
    )
    assert destination.read_bytes() == encoded
    assert list(run_directory.glob(".generated.pcapng.*.tmp")) == []


def test_existing_identical_generated_capture_is_read_once_and_reused(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reopening an existing artifact could validate bytes different from those returned to the caller."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    destination = run_directory / "generated.pcapng"
    destination.write_bytes(encoded)
    real_read_bytes = Path.read_bytes
    destination_reads = 0

    def count_reads(path: Path) -> bytes:
        nonlocal destination_reads
        if path == destination:
            destination_reads += 1
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", count_reads)

    publication = publish_generated_pcapng(
        run_directory,
        encoded,
        metadata=metadata,
        expected_trace=authoritative_trace(generated_events, metadata),
        observation_window_seconds=1.0,
    )

    assert publication.created_by_call is False
    assert publication.content == encoded
    assert destination_reads == 1


def test_generated_reuse_preserves_an_entry_that_appears_during_an_absent_read(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent read cannot authorize publication after another owner creates the canonical entry."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    destination = run_directory / "generated.pcapng"
    replacement = b"concurrent replacement"
    real_read_bytes = Path.read_bytes

    def create_during_absent_read(path: Path) -> bytes:
        if path == destination:
            destination.write_bytes(replacement)
            raise FileNotFoundError("simulated absent read")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", create_during_absent_read)

    with pytest.raises(TrafficlabError, match="changed during.*validation"):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )

    assert real_read_bytes(destination) == replacement
    assert list(run_directory.glob(".generated.pcapng.*.tmp")) == []


def test_generated_reuse_translates_an_unreadable_existing_entry(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A filesystem read failure must preserve the canonical entry and remain actionable."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    destination = run_directory / "generated.pcapng"
    destination.write_bytes(encoded)
    real_read_bytes = Path.read_bytes

    def fail_destination_read(path: Path) -> bytes:
        if path == destination:
            raise PermissionError("simulated unreadable entry")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_destination_read)

    with pytest.raises(TrafficlabError, match="could not read generated capture.*simulated unreadable entry"):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )

    assert real_read_bytes(destination) == encoded
    assert list(run_directory.glob(".generated.pcapng.*.tmp")) == []


def test_existing_different_generated_capture_is_preserved(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
) -> None:
    """A retry must never replace a canonical path owned by another result."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    destination = run_directory / "generated.pcapng"
    destination.write_bytes(b"unrelated")

    with pytest.raises(TrafficlabError, match="already exists"):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )

    assert destination.read_bytes() == b"unrelated"
    assert list(run_directory.iterdir()) == [destination]


def test_existing_equal_bytes_with_wrong_expected_trace_are_preserved_and_rejected(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
) -> None:
    """Byte identity alone must not reuse output for a different requested trace."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    destination = run_directory / "generated.pcapng"
    destination.write_bytes(encoded)
    different = generated_events[:-1]

    with pytest.raises(TrafficlabError, match="expected reparsed trace"):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(different, metadata),
            observation_window_seconds=1.0,
        )

    assert destination.read_bytes() == encoded


def test_existing_equal_malformed_bytes_are_preserved_and_rejected(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
) -> None:
    """Equal malformed bytes must not become reusable merely because the caller supplied them too."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    destination = run_directory / "generated.pcapng"
    malformed = b"not pcapng"
    destination.write_bytes(malformed)

    with pytest.raises(TrafficlabError, match="invalid PCAPNG"):
        publish_generated_pcapng(
            run_directory,
            malformed,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )

    assert destination.read_bytes() == malformed


def test_publication_accepts_scapy_truncation_inside_the_stored_window(
    tmp_path: Path,
    metadata: CaptureMetadata,
) -> None:
    """Scapy truncation keeps a binary-derived endpoint inside its stored window."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    window = 3 / 1024
    events = (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(window, Direction.INBOUND, 80),
    )
    content = encode_legacy_pcapng(events, metadata)
    expected_trace = read_pcapng_bytes(content, metadata, source=Path("expected.pcapng"))

    publication = publish_generated_pcapng(
        run_directory,
        content,
        metadata=metadata,
        expected_trace=expected_trace,
        observation_window_seconds=window,
    )

    parsed = read_pcapng_bytes(publication.content, metadata, source=publication.path)
    assert parsed.timestamps[-1] == 0.002929
    assert parsed.timestamps[-1] <= window

    assert (run_directory / "generated.pcapng").read_bytes() == content
    assert list(run_directory.glob(".generated.pcapng.*.tmp")) == []


@pytest.mark.parametrize("window", [True, float("inf"), 0.0])
def test_publication_rejects_invalid_stored_window(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    window: object,
) -> None:
    """A malformed stored W cannot define a safe closed publication interval."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()

    with pytest.raises(TrafficlabError, match="finite positive float"):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=cast(float, window),
        )


def test_publication_rejects_complete_events_outside_stored_window(
    tmp_path: Path,
    metadata: CaptureMetadata,
) -> None:
    """Publication must defend against a model incorrectly declaring out-of-window events complete."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    events = (TraceEvent(1.1, Direction.OUTBOUND, 60),)

    with pytest.raises(TrafficlabError, match="expected generated trace.*outside"):
        publish_generated_pcapng(
            run_directory,
            encode_legacy_pcapng(events, metadata),
            metadata=metadata,
            expected_trace=TrafficTrace.from_events(events),
            observation_window_seconds=1.0,
        )


@pytest.mark.parametrize("collision", [False, True], ids=["existing", "link-race-winner"])
def test_generated_reuse_rejects_an_entry_replaced_immediately_after_its_validation_read(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
    collision: bool,
) -> None:
    """Validated generated bytes cannot authorize reuse of a subsequently replaced canonical entry."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    destination = run_directory / "generated.pcapng"
    replacement = encode_legacy_pcapng((TraceEvent(0.0, Direction.OUTBOUND, 512),), metadata)
    if not collision:
        destination.write_bytes(encoded)

    real_read_bytes = Path.read_bytes
    real_link = os.link
    replaced = False

    def replace_after_read(path: Path) -> bytes:
        nonlocal replaced
        content = real_read_bytes(path)
        if path == destination and not replaced:
            replacement_path = run_directory / "replacement-generated.pcapng"
            replacement_path.write_bytes(replacement)
            os.replace(replacement_path, destination)
            replaced = True
        return content

    def collide(source: str | Path, target: str | Path) -> None:
        Path(target).write_bytes(encoded)
        real_link(source, target)

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    if collision:
        monkeypatch.setattr(artifact_module.os, "link", collide)

    with pytest.raises(TrafficlabError, match="changed during.*validation"):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )

    assert replaced is True
    assert real_read_bytes(destination) == replacement
    assert list(run_directory.glob(".generated.pcapng.*.tmp")) == []


@pytest.mark.parametrize("winner_is_expected", [True, False], ids=["identical-winner", "different-winner"])
def test_publication_link_race_preserves_and_validates_the_winner(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
    winner_is_expected: bool,
) -> None:
    """A losing hard-link race must preserve its winner and reuse only proven identical output."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    destination = run_directory / "generated.pcapng"
    winner = encoded if winner_is_expected else b"racing winner"
    real_link = os.link

    def collide(source: str | Path, target: str | Path) -> None:
        Path(target).write_bytes(winner)
        real_link(source, target)

    monkeypatch.setattr(artifact_module.os, "link", collide)

    if winner_is_expected:
        publication = publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )
        assert publication.created_by_call is False
        assert publication.content == encoded
    else:
        with pytest.raises(TrafficlabError, match="already exists"):
            publish_generated_pcapng(
                run_directory,
                encoded,
                metadata=metadata,
                expected_trace=authoritative_trace(generated_events, metadata),
                observation_window_seconds=1.0,
            )

    assert destination.read_bytes() == winner
    assert list(run_directory.glob(".generated.pcapng.*.tmp")) == []


def test_generated_publication_reports_a_disappearing_collision_winner_without_assertion(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A race winner that disappears before validation is actionable, never an AssertionError."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    destination = run_directory / "generated.pcapng"
    real_link = os.link

    def disappear(source: str | Path, target: str | Path) -> None:
        target_path = Path(target)
        target_path.write_bytes(encoded)
        try:
            real_link(source, target)
        except FileExistsError:
            target_path.unlink()
            raise

    monkeypatch.setattr(artifact_module.os, "link", disappear)

    with pytest.raises(TrafficlabError, match="publication race winner disappeared"):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )

    assert not destination.exists()
    assert list(run_directory.glob(".generated.pcapng.*.tmp")) == []


@pytest.mark.parametrize("failure", ["fsync", "parse", "link"], ids=["temp-fsync", "temp-parse", "exclusive-link"])
def test_publication_failure_never_creates_canonical_and_cleans_owned_temp(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Any pre-publication failure must leave no canonical output or owned temporary residue."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    adjacent = run_directory / "keep.txt"
    adjacent.write_text("keep", encoding="utf-8")
    expected_trace = authoritative_trace(generated_events, metadata)

    if failure == "fsync":

        def fail_fsync(_descriptor: int) -> None:
            raise OSError("fsync")

        monkeypatch.setattr(artifact_module.os, "fsync", fail_fsync)
    elif failure == "parse":

        def fail_parse(
            _content: bytes,
            _metadata: CaptureMetadata,
            *,
            source: Path,
        ) -> TrafficTrace:
            del source
            raise TrafficlabError("parse", corrective_action="repair")

        monkeypatch.setattr(artifact_module, "read_pcapng_bytes", fail_parse)
    else:

        def fail_link(_source: str | Path, _destination: str | Path) -> None:
            raise OSError("link")

        monkeypatch.setattr(artifact_module.os, "link", fail_link)

    with pytest.raises(TrafficlabError, match=failure):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=expected_trace,
            observation_window_seconds=1.0,
        )

    assert not (run_directory / "generated.pcapng").exists()
    assert adjacent.read_text(encoding="utf-8") == "keep"
    assert list(run_directory.glob(".generated.pcapng.*.tmp")) == []


def test_publication_reraises_unexpected_exception_unchanged_after_temp_cleanup(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normalizing a programming exception would hide its identity and make a defect look operational."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    sentinel = RuntimeError("unexpected publication sentinel")

    def fail_unexpected(
        _content: bytes,
        _metadata: CaptureMetadata,
        *,
        source: Path,
    ) -> TrafficTrace:
        assert source.name.startswith(".generated.pcapng.")
        raise sentinel

    monkeypatch.setattr(artifact_module, "read_pcapng_bytes", fail_unexpected)

    with pytest.raises(RuntimeError) as raised:
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )

    assert raised.value is sentinel
    assert not (run_directory / "generated.pcapng").exists()
    assert list(run_directory.glob(".generated.pcapng.*.tmp")) == []


def test_publication_translates_expected_oserror_with_actionable_package_error(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filesystem publication failures remain expected boundary errors with a corrective action."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()

    def fail_link(_source: str | Path, _destination: str | Path) -> None:
        raise OSError("expected link failure")

    monkeypatch.setattr(artifact_module.os, "link", fail_link)

    with pytest.raises(TrafficlabError, match="expected link failure") as raised:
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )

    assert raised.value.corrective_action == "verify the run directory is writable and has available space"
    assert list(run_directory.glob(".generated.pcapng.*.tmp")) == []


def test_publication_translates_temp_creation_oserror_without_cleanup_target(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure before temp ownership exists must remain actionable without attempting broad cleanup."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()

    def fail_temporary(*_args: object, **_kwargs: object) -> object:
        raise OSError("temporary creation failure")

    monkeypatch.setattr(artifact_module.tempfile, "NamedTemporaryFile", fail_temporary)

    with pytest.raises(TrafficlabError, match="temporary creation failure"):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )

    assert list(run_directory.iterdir()) == []


def test_publication_stream_write_failure_cleans_only_the_owned_temp(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed stream write must not leave a partial canonical artifact or delete adjacent files."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    adjacent = run_directory / "keep.txt"
    adjacent.write_text("keep", encoding="utf-8")
    real_named_temporary = artifact_module.tempfile.NamedTemporaryFile

    class FailingWriteTemporary:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._temporary: Any = cast(Any, real_named_temporary)(*args, **kwargs)
            self.name = cast(str, self._temporary.name)

        def __enter__(self) -> FailingWriteTemporary:
            self._temporary.__enter__()
            return self

        def __exit__(self, *args: Any) -> object:
            return cast(object, self._temporary.__exit__(*args))

        def write(self, _content: bytes) -> int:
            raise OSError("injected stream write failure")

    monkeypatch.setattr(artifact_module.tempfile, "NamedTemporaryFile", FailingWriteTemporary)

    with pytest.raises(TrafficlabError, match="stream write failure"):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )

    assert not (run_directory / "generated.pcapng").exists()
    assert adjacent.read_text(encoding="utf-8") == "keep"
    assert list(run_directory.glob(".generated.pcapng.*.tmp")) == []


def test_changed_persisted_temp_bytes_are_rejected_before_publication(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validating the input buffer instead of persisted temp bytes could publish a damaged write."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    real_read_bytes = Path.read_bytes

    def change_temp(path: Path) -> bytes:
        content = real_read_bytes(path)
        if path.name.startswith(".generated.pcapng."):
            return content + b"changed"
        return content

    monkeypatch.setattr(Path, "read_bytes", change_temp)

    with pytest.raises(TrafficlabError, match="persisted temporary generated capture differs"):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )

    assert not (run_directory / "generated.pcapng").exists()
    assert list(run_directory.glob(".generated.pcapng.*.tmp")) == []


def test_post_link_cleanup_failure_preserves_output_and_reports_only_owned_temp(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleanup failure after linking must not withdraw output or touch an adjacent unowned file."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    adjacent = run_directory / "keep.txt"
    adjacent.write_text("keep", encoding="utf-8")
    real_unlink = os.unlink
    attempts: list[Path] = []

    def fail_owned_temp(path: str | Path, *args: object, **kwargs: object) -> None:
        candidate = Path(path)
        if candidate.name.startswith(".generated.pcapng."):
            attempts.append(candidate)
            raise OSError("cleanup")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(artifact_module.os, "unlink", fail_owned_temp)

    with pytest.raises(TrafficlabError, match="published.*cleanup"):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=authoritative_trace(generated_events, metadata),
            observation_window_seconds=1.0,
        )

    assert (run_directory / "generated.pcapng").read_bytes() == encoded
    assert adjacent.read_text(encoding="utf-8") == "keep"
    assert len(attempts) == 1
    assert attempts[0].exists()


def test_link_and_temp_cleanup_failures_preserve_both_diagnostics_and_adjacent_files(
    tmp_path: Path,
    metadata: CaptureMetadata,
    generated_events: tuple[TraceEvent, ...],
    encoded: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup diagnostics must not replace the primary link failure or broaden deletion scope."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    adjacent = run_directory / "keep.txt"
    adjacent.write_text("keep", encoding="utf-8")
    expected_trace = authoritative_trace(generated_events, metadata)

    def fail_link(_source: str | Path, _destination: str | Path) -> None:
        raise OSError("primary link failure")

    def fail_unlink(path: str | Path) -> None:
        assert Path(path).name.startswith(".generated.pcapng.")
        raise OSError("secondary temp cleanup failure")

    monkeypatch.setattr(artifact_module.os, "link", fail_link)
    monkeypatch.setattr(artifact_module.os, "unlink", fail_unlink)

    with pytest.raises(TrafficlabError, match="primary link failure.*cleanup incomplete.*secondary temp cleanup"):
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=expected_trace,
            observation_window_seconds=1.0,
        )

    assert not (run_directory / "generated.pcapng").exists()
    assert adjacent.read_text(encoding="utf-8") == "keep"
    assert len(list(run_directory.glob(".generated.pcapng.*.tmp"))) == 1
