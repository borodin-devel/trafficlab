import copy
import json
import os
import stat
from pathlib import Path
from typing import cast

import pytest

import trafficlab.artifacts.run_directory as artifacts
from trafficlab.artifacts.run_directory import (
    create_run_directory,
)
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_experiment
from trafficlab.common.errors import TrafficlabError


def _config_with_run_directory(data: dict[str, object], run_directory: Path) -> ExperimentConfig:
    updated = copy.deepcopy(data)
    cast(dict[str, object], updated["run"])["directory"] = str(run_directory)
    return ExperimentConfig.model_validate(updated)


def test_create_run_directory_atomically_publishes_a_reloadable_snapshot(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directly writing the destination could expose a partial experiment snapshot."""
    config = _config_with_run_directory(valid_config_data, tmp_path / "runs" / "case")
    real_replace = os.replace
    replaced_sources: list[Path] = []

    def observed_replace(
        source: str | Path,
        destination: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        assert src_dir_fd is not None and dst_dir_fd == src_dir_fd
        assert stat.S_ISREG(os.stat(source, dir_fd=src_dir_fd, follow_symlinks=False).st_mode)
        with pytest.raises(FileNotFoundError):
            os.stat(destination, dir_fd=dst_dir_fd, follow_symlinks=False)
        replaced_sources.append(source_path)
        real_replace(
            source_path,
            destination_path,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

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


def test_create_run_directory_keeps_publication_bound_after_parent_swap(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-create parent swap must never redirect config or log writes into a source tree."""
    source = tmp_path / "source"
    source.mkdir()
    sentinel = source / "source.pcap"
    sentinel.write_bytes(b"source-bytes")
    safe_parent = tmp_path / "safe-parent"
    safe_parent.mkdir()
    displaced_parent = tmp_path / "displaced-safe-parent"
    config = _config_with_run_directory(valid_config_data, safe_parent / source.name)
    real_render = artifacts.render_effective_config

    def swap_parent_before_publication(current: ExperimentConfig) -> bytes:
        safe_parent.rename(displaced_parent)
        safe_parent.symlink_to(tmp_path, target_is_directory=True)
        return real_render(current)

    monkeypatch.setattr(artifacts, "render_effective_config", swap_parent_before_publication)

    with pytest.raises(TrafficlabError, match="run path changed"):
        create_run_directory(config)

    assert {path.name: path.read_bytes() for path in source.iterdir()} == {sentinel.name: b"source-bytes"}
    assert not (displaced_parent / source.name).exists()


def test_create_run_directory_supports_a_stable_symlink_parent(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Ordinary preflight may publish beneath a stable configured parent alias."""
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    alias_parent = tmp_path / "alias-parent"
    alias_parent.symlink_to(real_parent, target_is_directory=True)
    run_directory = alias_parent / "case"
    config = _config_with_run_directory(valid_config_data, run_directory)

    result = create_run_directory(config)

    assert result == run_directory
    assert load_experiment(real_parent / "case" / "experiment.toml") == config
    assert (real_parent / "case" / "run.log").is_file()


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

    def fail_replace(
        _source: str | Path,
        _destination: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        del src_dir_fd
        assert dst_dir_fd is not None
        descriptor = os.open(unowned.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dst_dir_fd)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(b"external")
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
    real_unlink = os.unlink
    real_fsync = os.fsync

    def fail_snapshot_unlink(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], *, dir_fd: int | None = None
    ) -> None:
        if Path(os.fsdecode(path)).name == "experiment.toml":
            raise OSError("injected unlink failure")
        real_unlink(path, dir_fd=dir_fd)

    fsync_calls = 0

    def fail_log_fsync(file_descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("injected log fsync failure")
        real_fsync(file_descriptor)

    monkeypatch.setattr(artifacts.os, "unlink", fail_snapshot_unlink)
    monkeypatch.setattr(artifacts.os, "fsync", fail_log_fsync)

    with pytest.raises(TrafficlabError, match="cleanup incomplete.*injected unlink failure"):
        create_run_directory(config)

    assert set(run_directory.iterdir()) == {run_directory / "experiment.toml"}
    assert list(run_directory.glob(".run.log.*.tmp")) == []
