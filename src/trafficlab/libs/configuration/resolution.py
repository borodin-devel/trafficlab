"""Bounded selected-TOML input and deterministic precedence resolution."""

from __future__ import annotations

import os
import stat
import tomllib
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import cast

from .errors import (
    ConfigurationSourceError,
    ConfigurationValidationError,
    InvalidConfigurationSchemaError,
)
from .values import (
    MAX_CONFIG_BYTES,
    ApplicationSpec,
    ConfigSelector,
    ConfigSource,
    ConfigurationSourceKind,
    ConfigurationValues,
    ResolvedConfiguration,
    freeze_settings,
)

_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW


def selected_source(spec: ApplicationSpec, selector: ConfigSelector) -> ConfigSource:
    """Resolve one explicit selected path without filesystem discovery."""

    if selector.config_file is not None:
        return ConfigSource(ConfigurationSourceKind.FILE, selector.config_file)
    if selector.config_dir is not None:
        return ConfigSource(
            ConfigurationSourceKind.FILE,
            selector.config_dir / spec.config_filename,
        )
    return ConfigSource(ConfigurationSourceKind.DEFAULTS)


def _read_selected_file(path: Path) -> Mapping[str, object]:
    try:
        descriptor = os.open(path, _READ_FLAGS)
    except OSError as error:
        raise ConfigurationSourceError(
            "selected configuration cannot be opened"
        ) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_CONFIG_BYTES:
            raise ConfigurationSourceError(
                "selected configuration has an unsafe type or size"
            )
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            descriptor = -1
            data = source.read(MAX_CONFIG_BYTES + 1)
            after = os.fstat(source.fileno())
        if len(data) > MAX_CONFIG_BYTES or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ConfigurationSourceError(
                "selected configuration changed while reading"
            )
    except ConfigurationSourceError:
        raise
    except OSError as error:
        raise ConfigurationSourceError(
            "selected configuration cannot be read"
        ) from error
    finally:
        if descriptor != -1:
            os.close(descriptor)
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationSourceError(
            "selected configuration is not valid TOML"
        ) from error
    return cast(Mapping[str, object], parsed)


def _validate_selected_values(
    spec: ApplicationSpec,
    selected: Mapping[str, object],
) -> Mapping[str, object]:
    try:
        frozen = freeze_settings(selected)
    except InvalidConfigurationSchemaError as error:
        raise ConfigurationValidationError(
            "selected configuration has invalid values"
        ) from error
    if not set(frozen).issubset(spec.defaults):
        raise ConfigurationValidationError(
            "selected configuration contains an unknown setting"
        )
    return frozen


def _validate_cli_values(
    spec: ApplicationSpec,
    cli_values: Mapping[str, object],
) -> Mapping[str, object]:
    try:
        frozen = freeze_settings(cli_values)
    except InvalidConfigurationSchemaError as error:
        raise ConfigurationValidationError(
            "command-line configuration has invalid values"
        ) from error
    if not set(frozen).issubset(spec.defaults):
        raise ConfigurationValidationError(
            "command-line configuration contains an unknown setting"
        )
    return frozen


def resolve_configuration(
    spec: ApplicationSpec,
    selector: ConfigSelector,
    cli_values: ConfigurationValues,
) -> ResolvedConfiguration:
    """Resolve defaults, selected TOML, then explicit CLI values once."""

    source = selected_source(spec, selector)
    selected: Mapping[str, object] = {}
    if source.kind is ConfigurationSourceKind.FILE:
        selected = _validate_selected_values(
            spec,
            _read_selected_file(cast(Path, source.path)),
        )
    cli = _validate_cli_values(spec, cli_values)
    values = dict(spec.defaults)
    values.update(selected)
    values.update(cli)
    try:
        validated = spec.validate(MappingProxyType(freeze_settings(values)))
        frozen = freeze_settings(validated)
    except (InvalidConfigurationSchemaError, TypeError, ValueError) as error:
        raise ConfigurationValidationError(
            "application configuration validation failed"
        ) from error
    if tuple(frozen) != tuple(spec.defaults):
        raise ConfigurationValidationError(
            "application configuration validation changed setting names"
        )
    return ResolvedConfiguration(frozen, source)
