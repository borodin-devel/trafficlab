from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.preflight.stage import PreflightFinding, check_local


@dataclass(frozen=True)
class _Disk:
    free: int


def _config(
    valid_config_data: dict[str, object],
    *,
    run_directory: Path | None = None,
    mounts: list[dict[str, object]] | None = None,
    minimum_free_bytes: int | None = None,
) -> ExperimentConfig:
    data = dict(valid_config_data)
    run_data = dict(cast(dict[str, object], data["run"]))
    target_data = dict(cast(dict[str, object], data["target"]))
    if run_directory is not None:
        run_data["directory"] = str(run_directory)
    if minimum_free_bytes is not None:
        run_data["minimum_free_bytes"] = minimum_free_bytes
    if mounts is not None:
        target_data["mounts"] = mounts
    data["run"] = run_data
    data["target"] = target_data
    return ExperimentConfig.model_validate(data)


def test_valid_configuration_reports_three_successful_findings(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    config = _config(valid_config_data, run_directory=tmp_path / "new-run")

    report = check_local(config, disk_usage=lambda path: _Disk(free=2_000_000), writable=lambda path: True)

    assert report.config == config
    assert report.findings == (
        PreflightFinding("mounts", True, "all mount sources exist"),
        PreflightFinding("run_directory", True, "run directory is absent and its parent is writable"),
        PreflightFinding("free_space", True, "available free space is sufficient"),
    )
    report.require_success()


def test_missing_mount_source_is_a_direct_failure(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    config = _config(
        valid_config_data,
        run_directory=tmp_path / "new-run",
        mounts=[{"source": str(missing), "target": "/input", "read_only": True}],
    )

    report = check_local(config, disk_usage=lambda path: _Disk(free=2_000_000), writable=lambda path: True)

    finding = report.findings[0]
    assert finding.name == "mounts"
    assert not finding.ok
    assert finding.detail == "mount source missing is unavailable"
    assert finding.corrective_action == "make the named host source available to Docker"
    with pytest.raises(TrafficlabError, match="mounts:"):
        report.require_success()


def test_existing_run_directory_is_a_direct_failure(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    run_directory = tmp_path / "existing"
    run_directory.mkdir()
    config = _config(valid_config_data, run_directory=run_directory)

    report = check_local(config, disk_usage=lambda path: _Disk(free=2_000_000), writable=lambda path: True)

    finding = report.findings[1]
    assert finding.name == "run_directory"
    assert not finding.ok
    assert "already exists" in finding.detail
    with pytest.raises(TrafficlabError, match="run_directory:"):
        report.require_success()


def test_non_directory_nearest_existing_parent_is_a_direct_failure(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    parent = tmp_path / "not-a-directory"
    parent.write_text("file")
    config = _config(valid_config_data, run_directory=parent / "child")

    report = check_local(config, disk_usage=lambda path: _Disk(free=2_000_000), writable=lambda path: True)

    finding = report.findings[1]
    assert not finding.ok
    assert "not a directory" in finding.detail


def test_unwritable_nearest_existing_parent_is_a_direct_failure(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    parent = tmp_path / "runs"
    config = _config(valid_config_data, run_directory=parent / "child")
    seen: list[Path] = []

    def writable(path: Path) -> bool:
        seen.append(path)
        return False

    report = check_local(config, disk_usage=lambda path: _Disk(free=2_000_000), writable=writable)

    finding = report.findings[1]
    assert not finding.ok
    assert "not writable" in finding.detail
    assert seen == [tmp_path]


def test_nearest_existing_parent_is_used_for_disk_usage(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    run_directory = tmp_path / "missing" / "new-run"
    config = _config(valid_config_data, run_directory=run_directory)
    seen: list[Path] = []

    def disk_usage(path: Path) -> _Disk:
        seen.append(path)
        return _Disk(free=2_000_000)

    check_local(config, disk_usage=disk_usage, writable=lambda path: True)

    assert seen == [tmp_path]


def test_insufficient_space_is_a_direct_failure(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    config = _config(valid_config_data, run_directory=tmp_path / "new-run", minimum_free_bytes=100)

    report = check_local(config, disk_usage=lambda path: _Disk(free=99), writable=lambda path: True)

    finding = report.findings[2]
    assert finding.name == "free_space"
    assert not finding.ok
    assert "99" in finding.detail
    with pytest.raises(TrafficlabError, match="free_space:"):
        report.require_success()


def test_all_independent_failures_are_reported_together(valid_config_data: dict[str, object], tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    existing = tmp_path / "existing"
    existing.mkdir()
    config = _config(
        valid_config_data,
        run_directory=existing,
        mounts=[{"source": str(missing), "target": "/input", "read_only": True}],
        minimum_free_bytes=100,
    )

    report = check_local(config, disk_usage=lambda path: _Disk(free=99), writable=lambda path: True)

    assert [finding.name for finding in report.findings] == ["mounts", "run_directory", "free_space"]
    assert all(not finding.ok for finding in report.findings)
    with pytest.raises(TrafficlabError, match="mounts:.*run_directory:.*free_space:"):
        report.require_success()
