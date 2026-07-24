"""Imperative startup shell composing attempts, resolution, and launch records."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trafficlab.libs.lineage import FileIdentity

from .attempts import create_direct_attempt, validate_managed_attempt
from .errors import ConfigurationError
from .launch import render_launch_record, write_launch_record
from .resolution import resolve_configuration, selected_source
from .values import (
    ApplicationSpec,
    ConfigSelector,
    ConfigurationValues,
    ResolvedConfiguration,
)


@dataclass(frozen=True, slots=True)
class StartupSuccess:
    """Configuration accepted and immutable launch record published."""

    attempt_dir: Path
    configuration: ResolvedConfiguration
    launch: FileIdentity


@dataclass(frozen=True, slots=True)
class StartupFailure:
    """Configuration rejected after publishing a safe immutable failure record."""

    attempt_dir: Path
    error: ConfigurationError
    launch: FileIdentity


def start_configuration(
    spec: ApplicationSpec,
    selector: ConfigSelector,
    cli_values: ConfigurationValues,
    *,
    arguments: tuple[str, ...],
    managed_attempt: Path | None = None,
    managed_run_root: Path | None = None,
    cwd: Path | None = None,
    now: datetime | None = None,
    suffix: Callable[[], str] | None = None,
) -> StartupSuccess | StartupFailure:
    """Prepare an attempt, resolve settings, and record either terminal outcome."""

    if (managed_attempt is None) != (managed_run_root is None):
        raise ValueError("managed attempt and run root must be supplied together")
    if managed_attempt is not None and managed_run_root is not None:
        attempt = validate_managed_attempt(managed_attempt, managed_run_root)
    elif suffix is None:
        attempt = create_direct_attempt(
            spec.application, Path.cwd() if cwd is None else cwd, now=now
        )
    else:
        attempt = create_direct_attempt(
            spec.application,
            Path.cwd() if cwd is None else cwd,
            now=now,
            suffix=suffix,
        )
    source = selected_source(spec, selector)
    try:
        configuration = resolve_configuration(spec, selector, cli_values)
    except ConfigurationError as error:
        launch = write_launch_record(
            attempt,
            render_launch_record(
                application=spec.application,
                attempt_dir=attempt,
                arguments=arguments,
                source=source,
                failure=error,
            ),
        )
        return StartupFailure(attempt, error, launch)
    launch = write_launch_record(
        attempt,
        render_launch_record(
            application=spec.application,
            attempt_dir=attempt,
            arguments=arguments,
            source=configuration.source,
            resolved=configuration,
        ),
    )
    return StartupSuccess(attempt, configuration, launch)
