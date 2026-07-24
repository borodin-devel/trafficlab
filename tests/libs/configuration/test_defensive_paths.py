# pyright: reportArgumentType=false, reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false
"""Focused tests for defensive Configuration boundary paths."""

import errno
import os
from pathlib import Path

import pytest

from trafficlab.libs.configuration import (
    ApplicationSpec,
    AttemptDirectoryError,
    ConfigSelector,
    ConfigSource,
    ConfigurationSourceError,
    ConfigurationSourceKind,
    ConfigurationValidationError,
    InvalidConfigurationSchemaError,
    InvalidConfigurationSelectorError,
    LaunchRecordError,
    ResolvedConfiguration,
    attempts,
    launch,
    resolution,
    service,
    values,
)


def _spec() -> ApplicationSpec:
    return ApplicationSpec(
        "10_capture", "10_capture.toml", {"value": 1}, lambda item: item
    )


@pytest.mark.unit
def test_values_defensive_rejections_and_finite_float() -> None:
    assert values.freeze_values(1.5) == 1.5
    with pytest.raises(InvalidConfigurationSchemaError):
        values.freeze_settings("wrong")
    with pytest.raises(InvalidConfigurationSchemaError):
        ApplicationSpec(
            "10_capture",
            "10_capture.toml",
            {"value": 1},
            lambda x: x,
            frozenset({"missing"}),
        )
    with pytest.raises(InvalidConfigurationSelectorError):
        ConfigSource(ConfigurationSourceKind.FILE, None)
    with pytest.raises(InvalidConfigurationSelectorError):
        ResolvedConfiguration({"value": 1}, object())  # type: ignore[arg-type]
    with pytest.raises(InvalidConfigurationSelectorError):
        ConfigSource(ConfigurationSourceKind.FILE, "wrong")  # type: ignore[arg-type]


@pytest.mark.unit
def test_attempt_defensive_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AttemptDirectoryError):
        attempts._canonical_absolute("wrong")
    with pytest.raises(AttemptDirectoryError):
        attempts._require_private_directory(tmp_path / "missing")
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    with pytest.raises(AttemptDirectoryError):
        attempts.validate_managed_attempt(root, root)
    monkeypatch.setattr(
        attempts.os, "listdir", lambda _: (_ for _ in ()).throw(OSError(errno.EIO, "x"))
    )
    child = root / "child"
    child.mkdir(mode=0o700)
    child.chmod(0o700)
    with pytest.raises(AttemptDirectoryError):
        attempts.validate_managed_attempt(child, root)
    with pytest.raises(AttemptDirectoryError):
        attempts.create_direct_attempt("bad", tmp_path)
    with pytest.raises(AttemptDirectoryError):
        attempts.create_direct_attempt("10_capture", tmp_path, suffix=object())  # type: ignore[arg-type]


@pytest.mark.unit
def test_launch_and_resolution_defensive_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert launch._toml_value(True) == "true"
    with pytest.raises(LaunchRecordError):
        launch._toml_value(object())
    source = ConfigSource(ConfigurationSourceKind.DEFAULTS)
    other = ResolvedConfiguration({"value": 1}, source)
    with pytest.raises(LaunchRecordError):
        launch.render_launch_record(
            application="10_capture",
            attempt_dir=tmp_path,
            arguments=(),
            source=ConfigSource(ConfigurationSourceKind.DEFAULTS),
            resolved=other,
            failure=LaunchRecordError("x"),
        )
    with pytest.raises(LaunchRecordError):
        launch.render_launch_record(
            application="10_capture",
            attempt_dir=tmp_path,
            arguments=(),
            source=source,
            resolved=ResolvedConfiguration(
                {"value": 1}, ConfigSource(ConfigurationSourceKind.FILE, Path("x"))
            ),
        )
    monkeypatch.setattr(launch.os, "write", lambda *_: 0)
    with pytest.raises(OSError):
        launch._write_all(1, b"x")
    assert launch._failure_code(ConfigurationSourceError("x")) == "configuration_source"
    assert launch._failure_code(AttemptDirectoryError("x")) == "configuration"
    with pytest.raises(ConfigurationValidationError):
        resolution._validate_selected_values(_spec(), {"value": object()})
    with pytest.raises(ConfigurationValidationError):
        resolution._validate_cli_values(_spec(), {"value": object()})
    monkeypatch.setattr(
        resolution,
        "freeze_settings",
        lambda _: (_ for _ in ()).throw(InvalidConfigurationSchemaError("x")),
    )
    with pytest.raises(ConfigurationValidationError):
        resolution._validate_selected_values(_spec(), {"value": 1})


@pytest.mark.unit
def test_writer_and_direct_service_error_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    attempt.chmod(0o700)
    monkeypatch.setattr(
        launch.os, "open", lambda *_: (_ for _ in ()).throw(OSError(errno.EIO, "x"))
    )
    with pytest.raises(LaunchRecordError):
        launch.write_launch_record(attempt, b"x")


@pytest.mark.unit
def test_remaining_defensive_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(values, "freeze_values", lambda _: 1)
    with pytest.raises(InvalidConfigurationSchemaError):
        values.freeze_settings({"value": 1})
    monkeypatch.undo()
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    attempt.chmod(0o700)
    monkeypatch.setattr(
        launch, "snapshot_external_file", lambda _: (_ for _ in ()).throw(OSError("x"))
    )
    with pytest.raises(LaunchRecordError):
        launch.write_launch_record(attempt, b"x", temp_name="one.tmp")
    selected = tmp_path / "large.toml"
    selected.write_bytes(b"#" * (values.MAX_CONFIG_BYTES + 1))
    with pytest.raises(ConfigurationSourceError):
        resolution.resolve_configuration(
            _spec(), ConfigSelector(config_file=selected), {}
        )


@pytest.mark.unit
def test_remaining_attempt_resolution_and_service_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patched:
        patched.setattr(
            attempts,
            "_require_private_directory",
            lambda _: (_ for _ in ()).throw(OSError(errno.EIO, "x")),
        )
        with pytest.raises(AttemptDirectoryError):
            attempts.create_direct_attempt("10_capture", tmp_path, suffix=lambda: "x")

    original_mkdir = Path.mkdir

    def fail_attempt(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.startswith("10_capture_"):
            raise OSError(errno.EIO, "x")
        original_mkdir(path, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "mkdir", fail_attempt)
        with pytest.raises(AttemptDirectoryError):
            attempts.create_direct_attempt("10_capture", tmp_path, suffix=lambda: "x")

    def collide_attempt(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.startswith("10_capture_"):
            raise FileExistsError(errno.EEXIST, "x")
        original_mkdir(path, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "mkdir", collide_attempt)
        with pytest.raises(AttemptDirectoryError, match="collision"):
            attempts.create_direct_attempt("10_capture", tmp_path, suffix=lambda: "x")

    direct = service.start_configuration(
        _spec(), ConfigSelector(), {}, arguments=(), cwd=tmp_path
    )
    assert direct.attempt_dir.parent == tmp_path / "run"

    selected = tmp_path / "changed.toml"
    selected.write_text("value = 1\n", encoding="utf-8")
    original_fstat = resolution.os.fstat
    count = 0

    def changed_fstat(descriptor: int) -> os.stat_result:
        nonlocal count
        metadata = original_fstat(descriptor)
        count += 1
        if count == 2:
            return os.stat_result(
                (
                    metadata.st_mode,
                    metadata.st_ino,
                    metadata.st_dev,
                    metadata.st_nlink,
                    metadata.st_uid,
                    metadata.st_gid,
                    metadata.st_size,
                    metadata.st_atime,
                    metadata.st_mtime + 1,
                    metadata.st_ctime,
                )
            )
        return metadata

    monkeypatch.setattr(resolution.os, "fstat", changed_fstat)
    with pytest.raises(ConfigurationSourceError, match="changed"):
        resolution.resolve_configuration(
            _spec(), ConfigSelector(config_file=selected), {}
        )
    selected.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        resolution.os, "fstat", lambda _: (_ for _ in ()).throw(OSError(errno.EIO, "x"))
    )
    with pytest.raises(ConfigurationSourceError):
        resolution.resolve_configuration(
            _spec(), ConfigSelector(config_file=selected), {}
        )
