"""Initial experiment run-directory publication."""

import json
import os
import secrets
import stat
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, cast

from pydantic import ValidationError

from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.errors import TrafficlabError

_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY
_BOUND_DIRECTORY_OPEN_FLAGS = _DIRECTORY_OPEN_FLAGS | os.O_NOFOLLOW
_TEMPORARY_OPEN_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW


def _open_bound_parent(path: Path, *, configuration_guard: Callable[[], None] | None) -> int:
    if not path.is_absolute():
        raise OSError(f"directory path is not absolute: {path}")
    missing_components: list[str] = []
    candidate = path
    while True:
        try:
            descriptor = os.open(candidate, _DIRECTORY_OPEN_FLAGS)
            break
        except FileNotFoundError:
            if candidate == candidate.parent:
                raise
            missing_components.append(candidate.name)
            candidate = candidate.parent
    try:
        if configuration_guard is not None:
            configuration_guard()
        for component in reversed(missing_components):
            try:
                os.mkdir(component, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(component, _BOUND_DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        bound = os.fstat(descriptor)
        visible = path.stat()
        if not stat.S_ISDIR(visible.st_mode) or (bound.st_dev, bound.st_ino) != (visible.st_dev, visible.st_ino):
            raise OSError(f"run parent changed before directory creation: {path}")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _require_visible_run_directory(run_directory: Path, run_descriptor: int) -> None:
    bound = os.fstat(run_descriptor)
    visible = run_directory.stat(follow_symlinks=False)
    if not stat.S_ISDIR(visible.st_mode) or (bound.st_dev, bound.st_ino) != (visible.st_dev, visible.st_ino):
        raise OSError(f"run path changed during directory publication: {run_directory}")


def _create_bound_run_directory(
    run_directory: Path, *, configuration_guard: Callable[[], None] | None
) -> tuple[int, int]:
    parent_descriptor = _open_bound_parent(run_directory.parent, configuration_guard=configuration_guard)
    created = False
    run_descriptor: int | None = None
    try:
        os.mkdir(run_directory.name, dir_fd=parent_descriptor)
        created = True
        run_descriptor = os.open(run_directory.name, _BOUND_DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor)
        _require_visible_run_directory(run_directory, run_descriptor)
    except BaseException:
        if run_descriptor is not None:
            os.close(run_descriptor)
        if created:
            try:
                os.rmdir(run_directory.name, dir_fd=parent_descriptor)
            except OSError:
                pass
        os.close(parent_descriptor)
        raise
    assert run_descriptor is not None
    return parent_descriptor, run_descriptor


def _open_bound_temporary(run_descriptor: int, *, prefix: str) -> tuple[str, BinaryIO]:
    for _attempt in range(128):
        name = f"{prefix}{secrets.token_hex(8)}.tmp"
        try:
            descriptor = os.open(name, _TEMPORARY_OPEN_FLAGS, 0o600, dir_fd=run_descriptor)
        except FileExistsError:
            continue
        try:
            return name, cast(BinaryIO, os.fdopen(descriptor, "wb"))
        except BaseException:
            os.close(descriptor)
            raise
    raise OSError(f"could not allocate a unique temporary name with prefix {prefix}")


def _read_bound_bytes(run_descriptor: int, name: str) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=run_descriptor)
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read()


def _validate_persisted_snapshot(content: bytes, expected: ExperimentConfig) -> None:
    try:
        text = content.decode("utf-8")
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


def _clean_failed_publication(
    run_directory: Path,
    parent_descriptor: int,
    run_descriptor: int,
    owned_files: list[str],
) -> str | None:
    cleanup_errors: list[str] = []
    for name in reversed(owned_files):
        try:
            os.unlink(name, dir_fd=run_descriptor)
        except FileNotFoundError:
            pass
        except OSError as error:
            cleanup_errors.append(f"could not remove {run_directory / name}: {error}")
    try:
        os.rmdir(run_directory.name, dir_fd=parent_descriptor)
    except OSError as error:
        cleanup_errors.append(f"could not remove {run_directory}: {error}")
    if cleanup_errors:
        return "; ".join(cleanup_errors)
    return None


def _publish_run_log(run_directory: Path, run_descriptor: int, owned_files: list[str]) -> None:
    """Atomically publish deterministic records for a prepared experiment run,
    the artifact boundary established by project configuration and local preflight.
    """
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
    temporary_name, stream = _open_bound_temporary(run_descriptor, prefix=".run.log.")
    owned_files.append(temporary_name)
    with stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())

    os.replace(temporary_name, "run.log", src_dir_fd=run_descriptor, dst_dir_fd=run_descriptor)
    owned_files.remove(temporary_name)
    owned_files.append("run.log")


def create_run_directory(config: ExperimentConfig, *, configuration_guard: Callable[[], None] | None = None) -> Path:
    """Create a run directory and atomically publish its realized configuration."""
    run_directory = config.run.directory
    try:
        parent_descriptor, run_descriptor = _create_bound_run_directory(
            run_directory,
            configuration_guard=configuration_guard,
        )
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

    owned_files: list[str] = []
    try:
        snapshot = render_effective_config(config)
        temporary_name, stream = _open_bound_temporary(run_descriptor, prefix=".experiment.toml.")
        owned_files.append(temporary_name)
        with stream:
            stream.write(snapshot)
            stream.flush()
            os.fsync(stream.fileno())

        _validate_persisted_snapshot(_read_bound_bytes(run_descriptor, temporary_name), config)
        os.replace(temporary_name, "experiment.toml", src_dir_fd=run_descriptor, dst_dir_fd=run_descriptor)
        owned_files.remove(temporary_name)
        owned_files.append("experiment.toml")
        _publish_run_log(run_directory, run_descriptor, owned_files)
        _require_visible_run_directory(run_directory, run_descriptor)
    except Exception as error:
        cleanup_error = _clean_failed_publication(
            run_directory,
            parent_descriptor,
            run_descriptor,
            owned_files,
        )
        detail = f"could not publish effective configuration in {run_directory}: {error}"
        if cleanup_error is not None:
            detail = f"{detail}; cleanup incomplete: {cleanup_error}"
        raise TrafficlabError(
            detail,
            corrective_action="verify the run parent is writable and retry with a new run directory",
        ) from error
    finally:
        os.close(run_descriptor)
        os.close(parent_descriptor)

    return run_directory
