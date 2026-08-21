"""Host-only preflight checks for realized experiment configuration."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from trafficlab.common.config import ExperimentConfig
from trafficlab.preflight.types import DiskUsage, PreflightFinding, PreflightReport, Writable


def default_writable(path: Path) -> bool:
    """Return whether the current process can write to *path*."""
    return os.access(path, os.W_OK)


def _nearest_existing_parent(path: Path) -> Path:
    parent = path.parent
    while not parent.exists():
        parent = parent.parent
    return parent


def check_mounts(config: ExperimentConfig) -> PreflightFinding:
    missing = [mount.source for mount in config.target.mounts if not mount.source.exists()]
    if missing:
        return PreflightFinding(
            "mounts",
            False,
            f"mount source {missing[0].name} is unavailable",
            "make the named host source available to Docker",
        )
    return PreflightFinding("mounts", True, "all mount sources exist")


def _check_run_directory(config: ExperimentConfig, writable: Writable) -> PreflightFinding:
    run_directory = config.run.directory
    if run_directory.exists():
        return PreflightFinding("run_directory", False, f"run directory already exists: {run_directory}")

    parent = _nearest_existing_parent(run_directory)
    if not parent.is_dir():
        return PreflightFinding("run_directory", False, f"nearest existing parent is not a directory: {parent}")
    if not writable(parent):
        return PreflightFinding("run_directory", False, f"nearest existing parent is not writable: {parent}")
    return PreflightFinding("run_directory", True, "run directory is absent and its parent is writable")


def check_free_space(config: ExperimentConfig, disk_usage: DiskUsage) -> PreflightFinding:
    parent = _nearest_existing_parent(config.run.directory)
    try:
        available = disk_usage(parent).free
    except OSError as error:
        return PreflightFinding("free_space", False, f"could not inspect free space at {parent}: {error}")
    minimum = config.run.minimum_free_bytes
    if available < minimum:
        return PreflightFinding(
            "free_space",
            False,
            f"available free space at {parent} is {available} bytes; requires at least {minimum} bytes",
        )
    return PreflightFinding("free_space", True, "available free space is sufficient")


def check_local(
    config: ExperimentConfig,
    *,
    disk_usage: DiskUsage = shutil.disk_usage,
    writable: Writable = default_writable,
) -> PreflightReport:
    """Evaluate all independent local checks for a validated configuration."""
    findings = (
        check_mounts(config),
        _check_run_directory(config, writable),
        check_free_space(config, disk_usage),
    )
    return PreflightReport(config=config, findings=findings)
