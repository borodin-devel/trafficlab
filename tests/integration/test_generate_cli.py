from __future__ import annotations

import builtins
import copy
import hashlib
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import trafficlab.artifacts as artifact_module
import trafficlab.cli as cli_module
import trafficlab.generation.stage as generation_module
from tests.fixtures.paths import PIPELINE_FIXTURE_ROOT
from tests.support.scapy_fixtures import encode_events as encode_legacy_pcapng
from tests.support.scapy_fixtures import encode_precise_events
from trafficlab.artifacts import create_run_directory, publish_generated_pcapng
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import encode_pcapng, read_pcapng_bytes
from trafficlab.common.trace import (
    CaptureMetadata,
    Direction,
    TraceEvent,
    TrafficTrace,
    normalize_reference,
    parse_capture_metadata,
)
from trafficlab.generation.models.common import GenerationResult, ModelFamily
from trafficlab.generation.models.registry import (
    BestModel,
    get_family,
    load_best_model,
    make_best_model,
    render_best_model,
    runtime_fitted_model,
)
from trafficlab.generation.stage import GenerationStageResult, generate_experiment
from trafficlab.preflight.stage import PreparedExperiment

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).parents[2]
_EXAMPLE_DATA = PIPELINE_FIXTURE_ROOT
_MODEL_BYTES = (_EXAMPLE_DATA / "models" / "best_model.json").read_bytes()
_CAPTURE_BYTES = (_EXAMPLE_DATA / "capture.json").read_bytes()


@pytest.fixture
def metadata() -> CaptureMetadata:
    return CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")


@pytest.fixture
def generated_events() -> tuple[TraceEvent, ...]:
    return (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(0.3333333336, Direction.INBOUND, 80),
        TraceEvent(1.0, Direction.OUTBOUND, 100),
    )


@pytest.fixture
def encoded(metadata: CaptureMetadata, generated_events: tuple[TraceEvent, ...]) -> bytes:
    return encode_pcapng(TrafficTrace.from_events(generated_events), metadata, observation_window_seconds=1.0).content


def _authoritative_trace(
    events: tuple[TraceEvent, ...], metadata: CaptureMetadata, *, window: float = 1.0
) -> TrafficTrace:
    return encode_pcapng(
        TrafficTrace.from_events(events),
        metadata,
        observation_window_seconds=window,
    ).trace


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


def _expected_scapy_final_content(config: ExperimentConfig) -> bytes:
    metadata = parse_capture_metadata(_CAPTURE_BYTES, source=Path("capture.json"))
    best = load_best_model(_MODEL_BYTES, source=Path("best_model.json"))
    reproduced = (
        get_family(best.family)
        .generate(
            runtime_fitted_model(best),
            config.run.final_seed,
            best.observation_window_seconds,
            config.generation.final,
            clock=lambda: 0.0,
        )
        .require_complete()
    )
    return encode_pcapng(
        reproduced,
        metadata,
        observation_window_seconds=best.observation_window_seconds,
    ).content


def _prepare_stage_run(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    *,
    name: str = "run",
) -> tuple[Path, Path, ExperimentConfig]:
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / name
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / f"{name}.toml"
    experiment_path.write_bytes(render_effective_config(config))
    create_run_directory(config)
    (run_directory / "best_model.json").write_bytes(_MODEL_BYTES)
    (run_directory / "capture.json").write_bytes(_CAPTURE_BYTES)
    return experiment_path, run_directory, config


def _log_records(run_directory: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (run_directory / "run.log").read_text(encoding="utf-8").splitlines()]


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
    real_directory_fsync = artifact_module._fsync_containing_directory  # pyright: ignore[reportPrivateUsage]
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
    monkeypatch.setattr(artifact_module, "_fsync_containing_directory", observe_directory_fsync)

    publication = publish_generated_pcapng(
        run_directory,
        encoded,
        metadata=metadata,
        expected_trace=_authoritative_trace(generated_events, metadata),
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

    monkeypatch.setattr(artifact_module, "_fsync_containing_directory", fail_directory_fsync)

    with pytest.raises(TrafficlabError, match="generated directory fsync failure") as caught:
        publish_generated_pcapng(
            run_directory,
            encoded,
            metadata=metadata,
            expected_trace=_authoritative_trace(generated_events, metadata),
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
        expected_trace=_authoritative_trace(generated_events, metadata),
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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
            expected_trace=_authoritative_trace(different, metadata),
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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
                expected_trace=_authoritative_trace(generated_events, metadata),
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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
    expected_trace = _authoritative_trace(generated_events, metadata)

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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
            expected_trace=_authoritative_trace(generated_events, metadata),
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
    expected_trace = _authoritative_trace(generated_events, metadata)

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


def test_stage_uses_authoritative_preparation_single_read_lineage_and_no_reference_open(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bypassing preparation or reopening lineage paths could generate from a non-authoritative snapshot."""
    experiment_path, run_directory, config = _prepare_stage_run(valid_config_data, tmp_path)
    best_path = run_directory / "best_model.json"
    capture_path = run_directory / "capture.json"
    reference_path = run_directory / "reference.pcapng"
    real_prepare = generation_module.open_or_prepare_experiment
    real_read_bytes = Path.read_bytes
    real_open = Path.open
    prepared_calls: list[Path] = []
    reads = {best_path: 0, capture_path: 0}

    def observe_prepare(path: Path) -> PreparedExperiment:
        prepared_calls.append(path)
        return real_prepare(path)

    def count_input_reads(path: Path) -> bytes:
        if path in reads:
            reads[path] += 1
        return real_read_bytes(path)

    def reject_reference_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == reference_path:
            raise AssertionError("generation opened reference.pcapng")
        return cast(Any, real_open(path, *args, **kwargs))

    monkeypatch.setattr(generation_module, "open_or_prepare_experiment", observe_prepare)
    monkeypatch.setattr(Path, "read_bytes", count_input_reads)
    monkeypatch.setattr(Path, "open", reject_reference_open)

    result = generate_experiment(experiment_path, clock=lambda: 0.0)

    assert prepared_calls == [experiment_path]
    assert reads == {best_path: 1, capture_path: 1}
    assert result.run_directory == run_directory
    assert result.seed == config.run.final_seed
    assert result.observation_window_seconds == 10.0
    assert result.generated_path == run_directory / "generated.pcapng"
    assert result.reused is False
    assert result.trace == read_pcapng_bytes(
        result.generated_path.read_bytes(),
        parse_capture_metadata(_CAPTURE_BYTES, source=capture_path),
        source=result.generated_path,
    )
    assert not reference_path.exists()


def test_stage_hashes_parses_and_rechecks_the_same_model_and_capture_bytes(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hashing and parsing use cached bytes, then publication rechecks their authoritative paths."""
    experiment_path, _run_directory, _config = _prepare_stage_run(valid_config_data, tmp_path)
    model_seen: list[bytes] = []
    capture_seen: list[bytes] = []
    real_load = load_best_model
    real_parse = parse_capture_metadata

    def observe_model(content: bytes, *, source: Path) -> BestModel:
        model_seen.append(content)
        return real_load(content, source=source)

    def observe_capture(content: bytes, *, source: Path) -> CaptureMetadata:
        capture_seen.append(content)
        return real_parse(content, source=source)

    monkeypatch.setattr(generation_module, "load_best_model", observe_model)
    monkeypatch.setattr(generation_module, "parse_capture_metadata", observe_capture)

    result = generate_experiment(experiment_path, clock=lambda: 0.0)

    assert model_seen == [_MODEL_BYTES]
    assert capture_seen == [_CAPTURE_BYTES]
    assert len(result.trace)
    assert (
        hashlib.sha256(capture_seen[0]).hexdigest()
        == load_best_model(model_seen[0], source=Path("observed-best_model.json")).capture_sha256
    )


def test_stage_uses_only_stored_family_window_and_configured_final_seed_and_limits(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refitting, recomputing W, or using trial settings would make final output diverge from the winning model."""
    experiment_path, _run_directory, config = _prepare_stage_run(valid_config_data, tmp_path)
    best = load_best_model(_MODEL_BYTES, source=Path("best_model.json"))
    real_family = get_family(best.family)
    calls: list[tuple[object, int, float, object, object]] = []

    def observe_generate(
        model: object,
        seed: int,
        W: float,
        limits: object,
        *,
        clock: Callable[[], float],
    ) -> GenerationResult:
        calls.append((model, seed, W, limits, clock))
        return real_family.generate(runtime_fitted_model(best), seed, W, config.generation.final, clock=clock)

    observed_family = cast(
        ModelFamily,
        SimpleNamespace(
            name=real_family.name,
            gene_names=real_family.gene_names,
            generate=observe_generate,
        ),
    )

    def observed_get_family(name: str) -> ModelFamily:
        assert name == best.family
        return observed_family

    monkeypatch.setattr(generation_module, "get_family", observed_get_family)

    def supplied_clock() -> float:
        return 0.0

    result = generate_experiment(experiment_path, clock=supplied_clock)

    assert result.observation_window_seconds == best.observation_window_seconds
    assert calls == [
        (
            runtime_fitted_model(best),
            config.run.final_seed,
            best.observation_window_seconds,
            config.generation.final,
            supplied_clock,
        )
    ]


@pytest.mark.parametrize("change", ["final-seed", "final-limits"])
def test_stage_rejects_generation_policy_drift_before_family_or_rng_use(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """Mutable configuration cannot silently replace the seed or guards retained by best_model.json."""
    data = copy.deepcopy(valid_config_data)
    if change == "final-seed":
        run = cast(dict[str, object], data["run"])
        run["final_seed"] = cast(int, run["final_seed"]) + 100_000
    else:
        generation = cast(dict[str, object], data["generation"])
        final = cast(dict[str, object], generation["final"])
        final["max_packets"] = cast(int, final["max_packets"]) + 1
    experiment_path, run_directory, _config = _prepare_stage_run(data, tmp_path)

    def forbidden_family(_name: str) -> ModelFamily:
        raise AssertionError("incompatible generation policy reached the model family or RNG")

    monkeypatch.setattr(generation_module, "get_family", forbidden_family)

    with pytest.raises(TrafficlabError, match="generation policy") as caught:
        generate_experiment(experiment_path, clock=lambda: 0.0)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "scientific_semantics_incompatible",
        "generate",
        "best_model.json",
        "preserved",
    )
    assert not (run_directory / "generated.pcapng").exists()


def test_stage_rejects_best_model_mutation_before_generated_publication(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generation cannot publish bytes after its authoritative fitted model changes."""
    experiment_path, run_directory, _config = _prepare_stage_run(valid_config_data, tmp_path)
    model_path = run_directory / "best_model.json"
    real_reproduce = generation_module.reproduce_generated_pcapng

    def reproduce_and_mutate(*args: Any, **kwargs: Any) -> object:
        result = real_reproduce(*args, **kwargs)
        model_path.write_bytes(model_path.read_bytes() + b"changed after generation")
        return result

    monkeypatch.setattr(generation_module, "reproduce_generated_pcapng", reproduce_and_mutate)

    with pytest.raises(TrafficlabError, match="best_model.json changed during generate") as caught:
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.kind == "artifact_changed"
    assert not (run_directory / "generated.pcapng").exists()


def test_stage_keeps_a_binary_resolution_endpoint_inside_its_stored_window(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nearest-nanosecond rendering must not move a valid binary-derived endpoint beyond stored W."""
    experiment_path, run_directory, config = _prepare_stage_run(valid_config_data, tmp_path)
    metadata = parse_capture_metadata(_CAPTURE_BYTES, source=run_directory / "capture.json")
    binary_reference = encode_precise_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(3 / 1024, Direction.INBOUND, 80),
        ),
        metadata,
        resolution=0x8A,
    )
    parsed_reference = read_pcapng_bytes(binary_reference, metadata, source=Path("binary-reference.pcapng"))
    reference, window = normalize_reference(parsed_reference)
    assert window == 3 / 1024

    family = get_family("poisson_empirical")
    bounds = config.models.poisson_empirical
    assert bounds is not None
    artifact = make_best_model(
        family,
        reference,
        (1.0,),
        reference_identity=identify_bytes(binary_reference),
        capture_identity=identify_bytes(_CAPTURE_BYTES),
        final_seed=config.run.final_seed,
        final_limits=config.generation.final,
        W=window,
        bounds=bounds,
    )
    (run_directory / "best_model.json").write_bytes(render_best_model(artifact))

    def generate_endpoint(
        _model: object,
        _seed: int,
        W: float,
        _limits: object,
        *,
        clock: Callable[[], float],
    ) -> GenerationResult:
        assert clock() == 0.0
        return GenerationResult(
            True,
            TrafficTrace.from_events(
                (
                    TraceEvent(0.0, Direction.OUTBOUND, 60),
                    TraceEvent(W, Direction.INBOUND, 80),
                )
            ),
        )

    endpoint_family = cast(
        ModelFamily,
        SimpleNamespace(name=family.name, gene_names=family.gene_names, generate=generate_endpoint),
    )

    def get_endpoint_family(_name: str) -> ModelFamily:
        return endpoint_family

    monkeypatch.setattr(generation_module, "get_family", get_endpoint_family)

    result = generate_experiment(experiment_path, clock=lambda: 0.0)

    assert result.observation_window_seconds == window
    assert result.trace[-1].timestamp == 0.002929
    assert all(0.0 <= timestamp <= window for timestamp in result.trace.timestamps)


@pytest.mark.parametrize(
    "defect",
    ["disabled", "missing-bounds", "bounds"],
    ids=["stored-family-disabled", "enabled-family-bounds-absent", "stored-bounds-mismatch"],
)
def test_stage_rejects_model_outside_authoritative_family_configuration(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
) -> None:
    """A stored model is valid only under the exact enabled family bounds that produced it."""
    data = copy.deepcopy(valid_config_data)
    models = cast(dict[str, object], data["models"])
    if defect == "disabled":
        models["enabled"] = ["markov_renewal", "mmpp"]
        models.pop("poisson_empirical")
    elif defect == "bounds":
        poisson = cast(dict[str, object], models["poisson_empirical"])
        cast(dict[str, object], poisson["c_lambda"])["upper"] = 5.0
    experiment_path, run_directory, config = _prepare_stage_run(data, tmp_path)
    if defect == "missing-bounds":
        invalid_models = config.models.model_copy(update={"poisson_empirical": None})
        invalid_config = config.model_copy(update={"models": invalid_models})
        prepared = cast(
            PreparedExperiment,
            SimpleNamespace(run_directory=run_directory, config=invalid_config),
        )

        def prepare_invalid(_path: Path) -> PreparedExperiment:
            return prepared

        monkeypatch.setattr(generation_module, "open_or_prepare_experiment", prepare_invalid)

    with pytest.raises(TrafficlabError, match="enabled" if defect == "disabled" else "bounds"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert not (run_directory / "generated.pcapng").exists()
    assert _log_records(run_directory)[-1]["event"] == "stage_failed"


@pytest.mark.parametrize("defect", ["missing", "invalid"], ids=["missing-model", "invalid-model"])
def test_stage_reports_missing_or_invalid_model_as_direct_generation_error(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    defect: str,
) -> None:
    experiment_path, run_directory, _config = _prepare_stage_run(valid_config_data, tmp_path)
    model_path = run_directory / "best_model.json"
    if defect == "missing":
        model_path.unlink()
    else:
        model_path.write_bytes(b"not JSON")

    expected_detail = "best_model.json is missing" if defect == "missing" else "best model"
    with pytest.raises(TrafficlabError, match=expected_detail) as error:
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert _log_records(run_directory)[-1] == {
        "corrective_action": error.value.corrective_action,
        "detail": str(error.value),
        "event": "stage_failed",
        "failure_outcome": {
            "affected_evidence": "best_model.json",
            "authority": "primary",
            "corrective_action": error.value.corrective_action,
            "detail": str(error.value),
            "evidence_state": "not_published" if defect == "missing" else "preserved",
            "kind": "artifact_missing" if defect == "missing" else "artifact_corrupt",
            "stage": "generate",
        },
        "stage": "generate",
    }


def test_stage_reports_a_missing_capture_metadata_file_through_the_real_reader(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    experiment_path, run_directory, _config = _prepare_stage_run(valid_config_data, tmp_path)
    (run_directory / "capture.json").unlink()

    with pytest.raises(TrafficlabError, match="could not read capture metadata") as error:
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert error.value.failure_outcome is not None
    assert error.value.failure_outcome.affected_evidence == "capture.json"
    assert error.value.failure_outcome.evidence_state == "not_published"
    assert not (run_directory / "generated.pcapng").exists()


def test_stage_rejects_incompatible_model_schema_before_generation(
    valid_config_data: dict[str, object],
    tmp_path: Path,
) -> None:
    """An old fitted-model schema is preserved scientific evidence, not corrupt input."""
    experiment_path, run_directory, _config = _prepare_stage_run(valid_config_data, tmp_path)
    model_path = run_directory / "best_model.json"
    document = cast(dict[str, object], json.loads(model_path.read_bytes()))
    document["scientific_artifact_schema"] = 1
    incompatible = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    model_path.write_bytes(incompatible)

    with pytest.raises(TrafficlabError, match="best model schema is incompatible") as raised:
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert model_path.read_bytes() == incompatible
    assert not (run_directory / "generated.pcapng").exists()
    assert raised.value.failure_outcome is not None
    assert raised.value.failure_outcome.as_dict() == {
        "affected_evidence": "best_model.json",
        "authority": "primary",
        "corrective_action": "refit under the current schema",
        "detail": "best model schema is incompatible",
        "evidence_state": "preserved",
        "kind": "scientific_semantics_incompatible",
        "stage": "generate",
    }
    assert _log_records(run_directory)[-1]["failure_outcome"] == raised.value.failure_outcome.as_dict()


@pytest.mark.parametrize("defect", ["capture-hash", "metadata"], ids=["capture-hash-mismatch", "malformed-metadata"])
def test_stage_rejects_invalid_capture_lineage_before_generation(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    defect: str,
) -> None:
    experiment_path, run_directory, _config = _prepare_stage_run(valid_config_data, tmp_path)
    capture_path = run_directory / "capture.json"
    capture_path.write_bytes(
        b'{"interface":"eth0","target_mac":"02:42:ac:11:00:04"}' if defect == "capture-hash" else b"{"
    )

    with pytest.raises(TrafficlabError, match="capture" if defect == "capture-hash" else "JSON"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert not (run_directory / "generated.pcapng").exists()


def test_stage_rejects_incomplete_generation_without_publication(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_path, run_directory, _config = _prepare_stage_run(valid_config_data, tmp_path)
    best = load_best_model(_MODEL_BYTES, source=Path("best_model.json"))
    family = get_family(best.family)

    def incomplete_generate(*_args: Any, **_kwargs: Any) -> GenerationResult:
        return GenerationResult(False, TrafficTrace.from_events(()), "max_packets")

    incomplete = cast(
        ModelFamily,
        SimpleNamespace(
            name=family.name,
            gene_names=family.gene_names,
            generate=incomplete_generate,
        ),
    )

    def get_incomplete_family(_name: str) -> ModelFamily:
        return incomplete

    monkeypatch.setattr(generation_module, "get_family", get_incomplete_family)

    with pytest.raises(TrafficlabError, match="generation exceeded the configured packet limit"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert not (run_directory / "generated.pcapng").exists()


def test_stage_rejects_a_post_publication_round_trip_mismatch(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stage result must expose events parsed from the exact published bytes, not pre-render values."""
    experiment_path, run_directory, _config = _prepare_stage_run(valid_config_data, tmp_path)
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
    assert _log_records(run_directory)[-1]["event"] == "stage_failed"


def test_stage_rejects_a_post_publication_timestamp_above_stored_window(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stage must independently enforce parsed timestamps inside stored W."""
    experiment_path, run_directory, _config = _prepare_stage_run(valid_config_data, tmp_path)
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
    assert _log_records(run_directory)[-1]["event"] == "stage_failed"


def test_stage_rejects_and_preserves_a_different_existing_output(
    valid_config_data: dict[str, object],
    tmp_path: Path,
) -> None:
    experiment_path, run_directory, _config = _prepare_stage_run(valid_config_data, tmp_path)
    destination = run_directory / "generated.pcapng"
    destination.write_bytes(b"preserve")

    with pytest.raises(TrafficlabError, match="already exists"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert destination.read_bytes() == b"preserve"


def test_stage_success_logs_exact_publication_and_reuse_records(
    valid_config_data: dict[str, object],
    tmp_path: Path,
) -> None:
    experiment_path, run_directory, config = _prepare_stage_run(valid_config_data, tmp_path)

    published = generate_experiment(experiment_path, clock=lambda: 0.0)
    reused = generate_experiment(experiment_path, clock=lambda: 0.0)

    assert published.reused is False
    assert reused.reused is True
    common = {
        "observation_window_seconds": 10.0,
        "packet_count": len(published.trace),
        "path": str(run_directory / "generated.pcapng"),
        "seed": config.run.final_seed,
        "stage": "generate",
    }
    assert _log_records(run_directory)[-2:] == [
        {"event": "generated_pcapng_published", **common},
        {"event": "generated_pcapng_reused", **common},
    ]


def test_stage_failure_log_wrapper_preserves_primary_error_contract(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_path, run_directory, _config = _prepare_stage_run(valid_config_data, tmp_path)
    (run_directory / "best_model.json").unlink()
    primary = TrafficlabError("primary", corrective_action="primary action", exit_code=7)
    real_read = generation_module._read_required_bytes  # pyright: ignore[reportPrivateUsage]

    def fail_model(path: Path, *, kind: str, corrective_action: str) -> bytes:
        if path.name == "best_model.json":
            raise primary
        return real_read(path, kind=kind, corrective_action=corrective_action)

    def fail_log(_run_directory: Path, _record: object) -> None:
        raise TrafficlabError("secondary log failure", corrective_action="repair log")

    monkeypatch.setattr(generation_module, "_read_required_bytes", fail_model)
    monkeypatch.setattr(generation_module, "append_run_log", fail_log)

    with pytest.raises(TrafficlabError, match="primary.*secondary log failure") as raised:
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert raised.value.corrective_action == "primary action"
    assert raised.value.exit_code == 7


def test_stage_success_log_failure_leaves_output_reusable(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_path, run_directory, _config = _prepare_stage_run(valid_config_data, tmp_path)
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


def test_cli_generate_injected_dispatch_prints_exact_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Generate must dispatch one in-process call and report its validated packet count and artifact path."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    calls: list[Path] = []
    result = GenerationStageResult(
        run_directory=run_directory,
        generated_path=run_directory / "generated.pcapng",
        trace=TrafficTrace.from_events(
            (TraceEvent(0.0, Direction.OUTBOUND, 60), TraceEvent(1.0, Direction.INBOUND, 80))
        ),
        seed=54321,
        observation_window_seconds=1.0,
        reused=False,
    )

    def generate(path: Path) -> GenerationStageResult:
        calls.append(path)
        return result

    assert cli_module.main(["generate", str(experiment_path)], generate=generate) == 0

    captured = capsys.readouterr()
    assert calls == [experiment_path]
    assert captured.out == f"generate: packets=2 output={run_directory / 'generated.pcapng'}\n"
    assert captured.err == ""


def test_cli_generate_rejects_config_only_without_dispatch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Generation is never a configuration-only operation."""
    calls: list[Path] = []

    def generate(path: Path) -> GenerationStageResult:
        calls.append(path)
        raise AssertionError("invalid generate invocation dispatched")

    assert cli_module.main(["generate", str(tmp_path / "experiment.toml"), "--config-only"], generate=generate) == 2

    captured = capsys.readouterr()
    assert calls == []
    assert captured.out == ""
    assert "unrecognized arguments: --config-only" in captured.err


def test_cli_generate_formats_structured_errors_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failure = TrafficlabError("generation failed", corrective_action="repair the run", exit_code=9)

    def generate(_path: Path) -> GenerationStageResult:
        raise failure

    assert cli_module.main(["generate", str(tmp_path / "experiment.toml")], generate=generate) == 9

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "generate: generation failed; repair the run\n"
    assert "Traceback" not in captured.err


def test_cli_existing_command_isolated_from_generation_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Registering generate must not eagerly import its model and PCAPNG stage for existing commands."""
    real_import = builtins.__import__
    imported_generation: list[str] = []

    def observe_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ):
        if name == "trafficlab.generation.stage" or name.startswith("trafficlab.generation.stage."):
            imported_generation.append(name)
        return real_import(name, globals, locals, fromlist, level)

    prepared = cast(
        PreparedExperiment,
        SimpleNamespace(run_directory=tmp_path / "run"),
    )
    monkeypatch.setattr(builtins, "__import__", observe_import)
    reloaded = importlib.reload(cli_module)

    def prepare(_path: Path) -> PreparedExperiment:
        return prepared

    assert (
        reloaded.main(
            ["preflight", str(tmp_path / "experiment.toml"), "--config-only"],
            prepare=prepare,
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out == f"preflight: prepared {tmp_path / 'run'}\n"
    assert captured.err == ""
    assert imported_generation == []


def test_cli_default_generate_lazily_imports_and_runs_stage(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    experiment_path, run_directory, _config = _prepare_stage_run(valid_config_data, tmp_path)
    real_import = builtins.__import__
    imported_generation: list[str] = []

    def observe_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ):
        if name == "trafficlab.generation.stage":
            imported_generation.append(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", observe_import)

    assert cli_module.main(["generate", str(experiment_path)]) == 0

    captured = capsys.readouterr()
    parsed = read_pcapng_bytes(
        (run_directory / "generated.pcapng").read_bytes(),
        parse_capture_metadata(_CAPTURE_BYTES, source=run_directory / "capture.json"),
        source=run_directory / "generated.pcapng",
    )
    assert imported_generation == ["trafficlab.generation.stage"]
    assert captured.out == f"generate: packets={len(parsed)} output={run_directory / 'generated.pcapng'}\n"
    assert captured.err == ""


def test_cli_generated_capture_matches_scapy_output_and_final_settings(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The public command and fixture generator must reproduce one byte-stable final-seed capture."""
    experiment_path, run_directory, config = _prepare_stage_run(valid_config_data, tmp_path)
    assert cli_module.main(["generate", str(experiment_path)]) == 0

    captured = capsys.readouterr()
    generated_content = (run_directory / "generated.pcapng").read_bytes()
    metadata = parse_capture_metadata(_CAPTURE_BYTES, source=run_directory / "capture.json")
    parsed = read_pcapng_bytes(generated_content, metadata, source=run_directory / "generated.pcapng")
    best = load_best_model(_MODEL_BYTES, source=run_directory / "best_model.json")

    assert generated_content == _expected_scapy_final_content(config)
    assert parsed
    assert all(0.0 <= event.timestamp <= best.observation_window_seconds for event in parsed)
    assert captured.out == f"generate: packets={len(parsed)} output={run_directory / 'generated.pcapng'}\n"
    assert captured.err == ""


def test_installed_generate_reproduces_checked_fixture_from_isolated_working_directory(
    valid_config_data: dict[str, object],
    tmp_path: Path,
) -> None:
    """The installed entry point must not depend on a source checkout import or a process-only test injection."""
    experiment_path, run_directory, config = _prepare_stage_run(valid_config_data, tmp_path)
    working_directory = tmp_path / "installed-entry-cwd"
    source_shadow = working_directory / "src" / "trafficlab"
    source_shadow.mkdir(parents=True)
    (source_shadow / "__init__.py").write_text(
        'raise RuntimeError("installed entry point imported a working-directory source shadow")\n',
        encoding="utf-8",
    )
    installed_script = Path(sys.executable).with_name("trafficlab")
    environment = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ}

    completed = subprocess.run(
        [str(installed_script), "generate", str(experiment_path)],
        check=False,
        capture_output=True,
        text=True,
        cwd=working_directory,
        env=environment,
    )

    generated = (run_directory / "generated.pcapng").read_bytes()
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"generate: packets=3 output={run_directory / 'generated.pcapng'}\n"
    assert completed.stderr == ""
    assert generated == _expected_scapy_final_content(config)
