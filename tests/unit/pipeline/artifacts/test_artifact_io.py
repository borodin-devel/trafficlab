import os
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.artifacts.io as artifacts
from trafficlab.artifacts.io import (
    atomic_replace,
)
from trafficlab.common.errors import TrafficlabError


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
