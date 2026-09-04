"""Initial experiment run-directory publication."""

import json
import os
import stat
import tempfile
import tomllib
from pathlib import Path

from pydantic import ValidationError

from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.errors import TrafficlabError

_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_directory_nofollow(path: Path, *, create_missing: bool) -> int:
    if not path.is_absolute():
        raise OSError(f"directory path is not absolute: {path}")
    descriptor = os.open(path.anchor, _DIRECTORY_OPEN_FLAGS)
    try:
        for component in path.parts[1:]:
            try:
                child = os.open(component, _DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create_missing:
                    raise
                try:
                    os.mkdir(component, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(component, _DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _create_bound_run_directory(run_directory: Path) -> None:
    parent_descriptor = _open_directory_nofollow(run_directory.parent, create_missing=True)
    created = False
    try:
        bound_parent = os.fstat(parent_descriptor)
        visible_parent = run_directory.parent.stat(follow_symlinks=False)
        if not stat.S_ISDIR(visible_parent.st_mode) or (bound_parent.st_dev, bound_parent.st_ino) != (
            visible_parent.st_dev,
            visible_parent.st_ino,
        ):
            raise OSError(f"run parent changed before directory creation: {run_directory.parent}")
        os.mkdir(run_directory.name, dir_fd=parent_descriptor)
        created = True
        bound_run = os.stat(run_directory.name, dir_fd=parent_descriptor, follow_symlinks=False)
        visible_run = run_directory.stat(follow_symlinks=False)
        if not stat.S_ISDIR(visible_run.st_mode) or (bound_run.st_dev, bound_run.st_ino) != (
            visible_run.st_dev,
            visible_run.st_ino,
        ):
            raise OSError(f"run path changed during directory creation: {run_directory}")
    except BaseException:
        if created:
            try:
                os.rmdir(run_directory.name, dir_fd=parent_descriptor)
            except OSError:
                pass
        raise
    finally:
        os.close(parent_descriptor)


def _validate_persisted_snapshot(path: Path, expected: ExperimentConfig) -> None:
    try:
        text = path.read_bytes().decode("utf-8")
        reparsed = ExperimentConfig.model_validate(tomllib.loads(text))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise TrafficlabError(
            "persisted effective configuration is invalid",
            corrective_action="report the atomic configuration publication defect",
        ) from error
    if reparsed != expected:
        raise TrafficlabError(
            "persisted effective configuration did not round-trip",
            corrective_action="report the atomic configuration publication defect",
        )


def _clean_failed_publication(run_directory: Path, owned_files: list[Path]) -> str | None:
    cleanup_errors: list[str] = []
    for path in reversed(owned_files):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            cleanup_errors.append(f"could not remove {path}: {error}")
    try:
        run_directory.rmdir()
    except OSError as error:
        cleanup_errors.append(f"could not remove {run_directory}: {error}")
    if cleanup_errors:
        return "; ".join(cleanup_errors)
    return None


def _publish_run_log(run_directory: Path, owned_files: list[Path]) -> None:
    """Atomically publish deterministic records for a prepared experiment run,
    the artifact boundary established by project configuration and local preflight.
    """
    log_path = run_directory / "run.log"
    records = (
        {
            "event": "effective_config_published",
            "path": str(run_directory / "experiment.toml"),
            "stage": "preflight",
        },
        {"event": "run_prepared", "path": str(run_directory), "stage": "preflight"},
    )
    content = "".join(f"{json.dumps(record, sort_keys=True, separators=(',', ':'))}\n" for record in records).encode(
        "utf-8"
    )
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=run_directory,
        prefix=".run.log.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary_path = Path(stream.name)
        owned_files.append(temporary_path)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())

    os.replace(temporary_path, log_path)
    owned_files.remove(temporary_path)
    owned_files.append(log_path)


def create_run_directory(config: ExperimentConfig) -> Path:
    """Create a run directory and atomically publish its realized configuration."""
    run_directory = config.run.directory
    try:
        _create_bound_run_directory(run_directory)
    except FileExistsError as error:
        raise TrafficlabError(
            f"run directory already exists: {run_directory}",
            corrective_action="choose a new run.directory or deliberately remove the existing run",
        ) from error
    except OSError as error:
        raise TrafficlabError(
            f"could not create run directory {run_directory}: {error}",
            corrective_action="verify the configured run directory and its parent permissions",
        ) from error

    owned_files: list[Path] = []
    try:
        snapshot = render_effective_config(config)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=run_directory,
            prefix=".experiment.toml.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            owned_files.append(temporary_path)
            stream.write(snapshot)
            stream.flush()
            os.fsync(stream.fileno())

        _validate_persisted_snapshot(temporary_path, config)
        snapshot_path = run_directory / "experiment.toml"
        os.replace(temporary_path, snapshot_path)
        owned_files.remove(temporary_path)
        owned_files.append(snapshot_path)
        _publish_run_log(run_directory, owned_files)
    except Exception as error:
        cleanup_error = _clean_failed_publication(run_directory, owned_files)
        detail = f"could not publish effective configuration in {run_directory}: {error}"
        if cleanup_error is not None:
            detail = f"{detail}; cleanup incomplete: {cleanup_error}"
        raise TrafficlabError(
            detail,
            corrective_action="verify the run parent is writable and retry with a new run directory",
        ) from error

    return run_directory
