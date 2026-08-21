import copy
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.artifacts as artifacts
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.artifacts import (
    CapturePublication,
    atomic_replace,
    create_run_directory,
    load_or_recover_capture_pair,
    publish_capture_pair,
)
from trafficlab.capture_validation import CaptureInspection
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import load_experiment
from trafficlab.errors import DeadlineExceededError, TrafficlabError
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata


def _config_with_run_directory(data: dict[str, object], run_directory: Path) -> ExperimentConfig:
    updated = copy.deepcopy(data)
    cast(dict[str, object], updated["run"])["directory"] = str(run_directory)
    return ExperimentConfig.model_validate(updated)


def test_atomic_replace_validates_fsynced_temporary_bytes_before_replacing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "checkpoint.json"
    destination.write_bytes(b"old\n")
    events: list[tuple[str, bytes]] = []
    real_replace = os.replace

    def validate(content: bytes) -> None:
        assert destination.read_bytes() == b"old\n"
        events.append(("validate", content))

    def observed_replace(source: str | Path, target: str | Path) -> None:
        events.append(("replace", Path(source).read_bytes()))
        real_replace(source, target)

    monkeypatch.setattr(artifacts.os, "replace", observed_replace)
    atomic_replace(destination, b"new\n", validator=validate)

    assert events == [("validate", b"new\n"), ("replace", b"new\n")]
    assert destination.read_bytes() == b"new\n"
    assert list(tmp_path.glob(".checkpoint.json.*.tmp")) == []


def test_atomic_replace_fsyncs_the_containing_directory_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "checkpoint.json"
    events: list[str] = []
    directory_descriptor: int | None = None
    real_open = os.open
    real_fsync = os.fsync
    real_close = os.close
    real_replace = os.replace

    def observed_open(path: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        nonlocal directory_descriptor
        descriptor = real_open(path, flags, mode)
        if Path(path) == tmp_path:
            directory_descriptor = descriptor
            events.append("open-directory")
        return descriptor

    def observed_fsync(descriptor: int) -> None:
        events.append("fsync-directory" if descriptor == directory_descriptor else "fsync-temporary")
        real_fsync(descriptor)

    def observed_replace(source: str | Path, target: str | Path) -> None:
        events.append("replace")
        real_replace(source, target)

    def observed_close(descriptor: int) -> None:
        if descriptor == directory_descriptor:
            events.append("close-directory")
        real_close(descriptor)

    monkeypatch.setattr(artifacts.os, "open", observed_open)
    monkeypatch.setattr(artifacts.os, "fsync", observed_fsync)
    monkeypatch.setattr(artifacts.os, "replace", observed_replace)
    monkeypatch.setattr(artifacts.os, "close", observed_close)
    atomic_replace(destination, b"new\n", validator=lambda _content: None)

    assert events == ["fsync-temporary", "replace", "open-directory", "fsync-directory", "close-directory"]
    assert destination.read_bytes() == b"new\n"


@pytest.mark.parametrize(
    ("failure", "reported_operation"),
    [("open", "open"), ("fsync", "fsync"), ("close", "close"), ("fsync_close", "fsync")],
)
def test_atomic_replace_reports_post_replace_directory_durability_failures(
    failure: str, reported_operation: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "checkpoint.json"
    destination.write_bytes(b"old\n")
    directory_descriptor: int | None = None
    real_open = os.open
    real_fsync = os.fsync
    real_close = os.close

    def failing_open(path: str | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        nonlocal directory_descriptor
        if Path(path) == tmp_path and failure == "open":
            raise OSError("injected directory open failure")
        descriptor = real_open(path, flags, mode)
        if Path(path) == tmp_path:
            directory_descriptor = descriptor
        return descriptor

    def failing_fsync(descriptor: int) -> None:
        if descriptor == directory_descriptor and failure in {"fsync", "fsync_close"}:
            raise OSError("injected directory fsync failure")
        real_fsync(descriptor)

    def failing_close(descriptor: int) -> None:
        if descriptor == directory_descriptor and failure in {"close", "fsync_close"}:
            real_close(descriptor)
            raise OSError("injected directory close failure")
        real_close(descriptor)

    monkeypatch.setattr(artifacts.os, "open", failing_open)
    monkeypatch.setattr(artifacts.os, "fsync", failing_fsync)
    monkeypatch.setattr(artifacts.os, "close", failing_close)
    with pytest.raises(
        TrafficlabError,
        match=rf"directory {reported_operation} failure.*destination may be present",
    ):
        atomic_replace(destination, b"new\n", validator=lambda _content: None)

    assert destination.read_bytes() == b"new\n"
    assert list(tmp_path.glob(".checkpoint.json.*.tmp")) == []


def test_atomic_replace_validation_failure_preserves_destination_and_cleans_owned_temporary(tmp_path: Path) -> None:
    destination = tmp_path / "ga_history.csv"
    destination.write_bytes(b"valid old\n")

    def reject(_content: bytes) -> None:
        raise TrafficlabError("injected validator failure", corrective_action="keep the old artifact")

    with pytest.raises(TrafficlabError, match="validator failure"):
        atomic_replace(destination, b"invalid new\n", validator=reject)

    assert destination.read_bytes() == b"valid old\n"
    assert list(tmp_path.glob(".ga_history.csv.*.tmp")) == []


def _accept_bytes(_content: bytes) -> None:
    return


@pytest.mark.parametrize(
    ("content", "validator", "match"),
    [
        ("content", _accept_bytes, "content"),
        (b"content", None, "validator"),
    ],
)
def test_atomic_replace_rejects_noncanonical_argument_types(content: Any, validator: Any, match: str) -> None:
    with pytest.raises(TypeError, match=match):
        atomic_replace(Path("checkpoint.json"), content, validator=validator)


def test_atomic_replace_read_failure_preserves_destination_and_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "checkpoint.json"
    destination.write_bytes(b"old\n")
    real_read_bytes = Path.read_bytes

    def fail_temporary_read(path: Path) -> bytes:
        if path.name.startswith(".checkpoint.json."):
            raise OSError("injected temporary read failure")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_temporary_read)
    with pytest.raises(TrafficlabError, match="temporary read failure"):
        atomic_replace(destination, b"new\n", validator=lambda _content: None)

    assert destination.read_bytes() == b"old\n"
    assert list(tmp_path.glob(".checkpoint.json.*.tmp")) == []


def test_atomic_replace_replace_failure_preserves_destination_and_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "checkpoint.json"
    destination.write_bytes(b"old\n")

    def fail_replace(_source: str | Path, _destination: str | Path) -> None:
        raise OSError("injected atomic replace failure")

    monkeypatch.setattr(artifacts.os, "replace", fail_replace)
    with pytest.raises(TrafficlabError, match="atomic replace failure"):
        atomic_replace(destination, b"new\n", validator=lambda _content: None)

    assert destination.read_bytes() == b"old\n"
    assert list(tmp_path.glob(".checkpoint.json.*.tmp")) == []


def test_atomic_replace_fsync_failure_cleans_only_owned_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "checkpoint.json"
    destination.write_bytes(b"old\n")

    def fail_fsync(_file_descriptor: int) -> None:
        raise OSError("injected checkpoint fsync failure")

    monkeypatch.setattr(artifacts.os, "fsync", fail_fsync)
    with pytest.raises(TrafficlabError, match="checkpoint fsync failure"):
        atomic_replace(destination, b"new\n", validator=lambda _content: None)

    assert destination.read_bytes() == b"old\n"
    assert list(tmp_path.glob(".checkpoint.json.*.tmp")) == []


def test_atomic_replace_temp_creation_failure_has_no_cleanup_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "checkpoint.json"
    destination.write_bytes(b"old\n")

    def fail_temporary(*_args: object, **_kwargs: object) -> object:
        raise OSError("injected checkpoint temp creation failure")

    monkeypatch.setattr(artifacts.tempfile, "NamedTemporaryFile", fail_temporary)
    with pytest.raises(TrafficlabError, match="temp creation failure"):
        atomic_replace(destination, b"new\n", validator=lambda _content: None)

    assert destination.read_bytes() == b"old\n"


def test_atomic_replace_reports_write_and_owned_temp_cleanup_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "checkpoint.json"
    destination.write_bytes(b"old\n")
    real_temporary = artifacts.tempfile.NamedTemporaryFile
    real_unlink = Path.unlink

    class FailingWriteTemporary:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._temporary = cast(Any, real_temporary)(*args, **kwargs)
            self.name = cast(str, self._temporary.name)

        def __enter__(self) -> Any:
            self._temporary.__enter__()
            return self

        def __exit__(self, *args: Any) -> object:
            return cast(object, self._temporary.__exit__(*args))

        def write(self, _content: bytes) -> int:
            raise OSError("injected checkpoint write failure")

    def fail_owned_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.name.startswith(".checkpoint.json."):
            raise OSError("injected checkpoint cleanup failure")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(artifacts.tempfile, "NamedTemporaryFile", FailingWriteTemporary)
    monkeypatch.setattr(Path, "unlink", fail_owned_unlink)
    with pytest.raises(TrafficlabError, match="write failure.*cleanup incomplete.*cleanup failure"):
        atomic_replace(destination, b"new\n", validator=lambda _content: None)

    assert destination.read_bytes() == b"old\n"
    assert len(list(tmp_path.glob(".checkpoint.json.*.tmp"))) == 1


def test_atomic_replace_post_replace_cleanup_sees_already_moved_temp_as_success(tmp_path: Path) -> None:
    destination = tmp_path / "checkpoint.json"

    def validate(content: bytes) -> None:
        assert content == b"new\n"

    atomic_replace(destination, b"new\n", validator=validate)
    assert destination.read_bytes() == b"new\n"


def test_create_run_directory_atomically_publishes_a_reloadable_snapshot(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directly writing the destination could expose a partial experiment snapshot."""
    config = _config_with_run_directory(valid_config_data, tmp_path / "runs" / "case")
    real_replace = os.replace
    replaced_sources: list[Path] = []

    def observed_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        assert source_path.is_file()
        assert not destination_path.exists()
        replaced_sources.append(source_path)
        real_replace(source_path, destination_path)

    monkeypatch.setattr(artifacts.os, "replace", observed_replace)

    run_path = create_run_directory(config)
    snapshot = load_experiment(run_path / "experiment.toml")

    assert run_path == config.run.directory
    assert snapshot == config
    assert [json.loads(line) for line in (run_path / "run.log").read_text(encoding="utf-8").splitlines()] == [
        {
            "event": "effective_config_published",
            "path": str(run_path / "experiment.toml"),
            "stage": "preflight",
        },
        {"event": "run_prepared", "path": str(run_path), "stage": "preflight"},
    ]
    assert {source.name.split(".")[1] for source in replaced_sources} == {"experiment", "run"}
    assert all(source.suffix == ".tmp" for source in replaced_sources)
    assert list(run_path.glob(".experiment.toml.*.tmp")) == []
    assert list(run_path.glob(".run.log.*.tmp")) == []


def test_existing_run_directory_is_never_replaced(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    """Accepting an existing destination could destroy or mix experiment artifacts."""
    run_directory = tmp_path / "runs" / "case"
    run_directory.mkdir(parents=True)
    sentinel = run_directory / "keep.txt"
    sentinel.write_text("existing", encoding="utf-8")
    config = _config_with_run_directory(valid_config_data, run_directory)

    with pytest.raises(TrafficlabError, match="already exists") as error:
        create_run_directory(config)

    assert error.value.corrective_action
    assert sentinel.read_text(encoding="utf-8") == "existing"
    assert set(run_directory.iterdir()) == {sentinel}


def test_snapshot_write_failure_removes_only_the_just_created_run_directory(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed fsync must not leave a false run or remove adjacent caller-owned files."""
    parent = tmp_path / "runs"
    parent.mkdir()
    sentinel = parent / "keep.txt"
    sentinel.write_text("caller-owned", encoding="utf-8")
    run_directory = parent / "case"
    config = _config_with_run_directory(valid_config_data, run_directory)

    def fail_fsync(_file_descriptor: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(artifacts.os, "fsync", fail_fsync)

    with pytest.raises(TrafficlabError, match="could not publish effective configuration") as error:
        create_run_directory(config)

    assert error.value.corrective_action
    assert not run_directory.exists()
    assert sentinel.read_text(encoding="utf-8") == "caller-owned"


def test_failed_publication_preserves_an_unowned_file_in_the_new_directory(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure cleanup must not recursively delete a file not created by this call."""
    run_directory = tmp_path / "runs" / "case"
    config = _config_with_run_directory(valid_config_data, run_directory)
    unowned = run_directory / "unowned.txt"

    def fail_replace(_source: str | Path, destination: str | Path) -> None:
        Path(destination).parent.joinpath(unowned.name).write_text("external", encoding="utf-8")
        raise OSError("injected replace failure")

    monkeypatch.setattr(artifacts.os, "replace", fail_replace)

    with pytest.raises(TrafficlabError, match="could not publish effective configuration"):
        create_run_directory(config)

    assert unowned.read_text(encoding="utf-8") == "external"
    assert set(run_directory.iterdir()) == {unowned}


def test_malformed_persisted_snapshot_is_rejected_and_cleaned_up(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trusting malformed bytes after fsync would publish an unreadable effective configuration."""
    run_directory = tmp_path / "runs" / "case"
    config = _config_with_run_directory(valid_config_data, run_directory)

    def render_malformed_snapshot(_config: ExperimentConfig) -> bytes:
        return b"[run\n"

    monkeypatch.setattr(artifacts, "render_effective_config", render_malformed_snapshot)

    with pytest.raises(TrafficlabError, match="persisted effective configuration is invalid"):
        create_run_directory(config)

    assert not run_directory.exists()


def test_model_changing_persisted_snapshot_is_rejected_and_cleaned_up(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trusting different valid bytes after fsync would publish the wrong effective experiment."""
    run_directory = tmp_path / "runs" / "case"
    config = _config_with_run_directory(valid_config_data, run_directory)
    changed_run = config.run.model_copy(update={"master_seed": 0})
    changed_config = config.model_copy(update={"run": changed_run})
    changed_snapshot = artifacts.render_effective_config(changed_config)

    def render_changed_snapshot(_config: ExperimentConfig) -> bytes:
        return changed_snapshot

    monkeypatch.setattr(artifacts, "render_effective_config", render_changed_snapshot)

    with pytest.raises(TrafficlabError, match="persisted effective configuration did not round-trip"):
        create_run_directory(config)

    assert not run_directory.exists()


def test_run_directory_creation_os_error_preserves_the_parent_path(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A non-directory parent must become a corrective package error without changing that path."""
    parent = tmp_path / "not-a-directory"
    parent.write_text("caller-owned", encoding="utf-8")
    config = _config_with_run_directory(valid_config_data, parent / "case")

    with pytest.raises(TrafficlabError, match="could not create run directory") as error:
        create_run_directory(config)

    assert error.value.corrective_action
    assert parent.read_text(encoding="utf-8") == "caller-owned"


def test_log_publication_failure_reports_cleanup_without_broad_deletion(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed owned-file unlink must leave visible evidence rather than trigger broad cleanup."""
    run_directory = tmp_path / "runs" / "case"
    config = _config_with_run_directory(valid_config_data, run_directory)
    real_unlink = Path.unlink
    real_fsync = os.fsync

    def fail_snapshot_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.name == "experiment.toml":
            raise OSError("injected unlink failure")
        real_unlink(path, missing_ok=missing_ok)

    fsync_calls = 0

    def fail_log_fsync(file_descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("injected log fsync failure")
        real_fsync(file_descriptor)

    monkeypatch.setattr(Path, "unlink", fail_snapshot_unlink)
    monkeypatch.setattr(artifacts.os, "fsync", fail_log_fsync)

    with pytest.raises(TrafficlabError, match="cleanup incomplete.*injected unlink failure"):
        create_run_directory(config)

    assert set(run_directory.iterdir()) == {run_directory / "experiment.toml"}
    assert list(run_directory.glob(".run.log.*.tmp")) == []


def _capture_sources(directory: Path, *, timestamp: float = 0.0) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    metadata_path = directory / "temporary-capture.json"
    pcapng_path = directory / "temporary-reference.pcapng"
    metadata_path.write_bytes(render_capture_metadata(metadata))
    pcapng_path.write_bytes(encode_pcapng((TraceEvent(timestamp, Direction.OUTBOUND, 14),), metadata))
    return metadata_path, pcapng_path


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
    real_directory_fsync = artifacts._fsync_containing_directory  # pyright: ignore[reportPrivateUsage]
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
    monkeypatch.setattr(artifacts, "_fsync_containing_directory", observed_directory_fsync)

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

    monkeypatch.setattr(artifacts, "_fsync_containing_directory", fail_directory_fsync)

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
