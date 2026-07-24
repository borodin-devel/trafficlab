"""Tests for application startup configuration orchestration."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trafficlab.libs.configuration import (
    ApplicationSpec,
    ConfigSelector,
    StartupFailure,
    StartupSuccess,
    start_configuration,
)


def _spec() -> ApplicationSpec:
    return ApplicationSpec(
        application="10_capture",
        config_filename="10_capture.toml",
        defaults={"count": 1},
        validate=lambda values: values,
    )


@pytest.mark.unit
def test_start_configuration_returns_effective_values_and_launch_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "managed"
    attempt = root / "assigned"
    root.mkdir(mode=0o700)
    attempt.mkdir(mode=0o700)
    root.chmod(0o700)
    attempt.chmod(0o700)

    result = start_configuration(
        _spec(),
        ConfigSelector(),
        {"count": 2},
        arguments=("--count", "2"),
        managed_attempt=attempt,
        managed_run_root=root,
    )

    assert isinstance(result, StartupSuccess)
    assert result.configuration.values == {"count": 2}
    assert result.launch.path == str(attempt / "launch.toml")
    assert b'state = "resolved"\n' in (attempt / "launch.toml").read_bytes()


@pytest.mark.unit
def test_start_configuration_records_failure_in_created_direct_attempt(
    tmp_path: Path,
) -> None:
    result = start_configuration(
        _spec(),
        ConfigSelector(),
        {"unknown": 2},
        arguments=("--unknown", "2"),
        cwd=tmp_path,
        now=datetime(2026, 7, 23, tzinfo=UTC),
        suffix=lambda: "suffix",
    )

    assert isinstance(result, StartupFailure)
    assert result.attempt_dir.name == "10_capture_20260723T000000_Z_suffix"
    assert b'state = "failed"\n' in (result.attempt_dir / "launch.toml").read_bytes()


@pytest.mark.unit
def test_start_configuration_requires_both_managed_boundary_arguments(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError):
        start_configuration(
            _spec(),
            ConfigSelector(),
            {},
            arguments=(),
            managed_attempt=tmp_path,
        )
