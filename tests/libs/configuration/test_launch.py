"""Tests for canonical immutable startup records."""

from pathlib import Path

import pytest

from trafficlab.libs.configuration import (
    ConfigSource,
    ConfigurationSourceKind,
    LaunchRecordConflictError,
    LaunchRecordError,
    ResolvedConfiguration,
    render_launch_record,
    write_launch_record,
)


@pytest.mark.unit
def test_launch_record_has_canonical_resolved_toml_bytes(tmp_path: Path) -> None:
    record = render_launch_record(
        application="10_capture",
        attempt_dir=tmp_path / "attempt",
        arguments=("--config-file", "settings.toml"),
        source=ConfigSource(ConfigurationSourceKind.FILE, Path("settings.toml")),
        resolved=ResolvedConfiguration(
            {"label": "value", "limits": {"count": 2}},
            ConfigSource(ConfigurationSourceKind.FILE, Path("settings.toml")),
        ),
    )

    assert record == (
        b'schema_version = 1\nstate = "resolved"\napplication = "10_capture"\n'
        b'attempt_path = "' + str(tmp_path / "attempt").encode() + b'"\n'
        b'arguments = ["--config-file", "settings.toml"]\n'
        b'source_kind = "file"\nsource_path = "settings.toml"\n'
        b'[configuration]\nlabel = "value"\n[configuration.limits]\ncount = 2\n'
    )


@pytest.mark.unit
def test_launch_record_uses_safe_failure_code_without_error_text(
    tmp_path: Path,
) -> None:
    record = render_launch_record(
        application="10_capture",
        attempt_dir=tmp_path / "attempt",
        arguments=("--config-file", "secret.toml"),
        source=ConfigSource(ConfigurationSourceKind.FILE, Path("secret.toml")),
        failure=LaunchRecordError("do not record secret"),
    )

    assert b'state = "failed"\n' in record
    assert b'failure_code = "launch_record"\n' in record
    assert b"do not record secret" not in record


@pytest.mark.unit
def test_launch_writer_publishes_private_immutable_identity(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    attempt.mkdir(mode=0o700)
    attempt.chmod(0o700)
    payload = b"schema_version = 1\n"

    identity = write_launch_record(attempt, payload, temp_name="launch.tmp")

    launch = attempt / "launch.toml"
    assert identity.path == str(launch)
    assert launch.read_bytes() == payload
    assert launch.stat().st_mode & 0o777 == 0o600
    assert launch.stat().st_nlink == 1
    with pytest.raises(LaunchRecordConflictError):
        write_launch_record(attempt, payload, temp_name="second.tmp")


@pytest.mark.unit
def test_launch_renderer_and_writer_reject_invalid_inputs(tmp_path: Path) -> None:
    with pytest.raises(LaunchRecordError):
        render_launch_record(
            application="capture",
            attempt_dir=tmp_path,
            arguments=(),
            source=ConfigSource(ConfigurationSourceKind.DEFAULTS),
        )
    with pytest.raises(LaunchRecordError):
        write_launch_record(tmp_path, b"", temp_name="../unsafe")
