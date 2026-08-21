"""Initial experiment run-directory publication."""

import json
import os
import tempfile
import tomllib
from pathlib import Path

from pydantic import ValidationError

from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.errors import TrafficlabError


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
        run_directory.mkdir(parents=True, exist_ok=False)
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
