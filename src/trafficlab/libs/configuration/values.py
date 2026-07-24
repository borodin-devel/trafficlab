"""Immutable functional-core values for shared configuration."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import cast

from .errors import (
    InvalidConfigurationSchemaError,
    InvalidConfigurationSelectorError,
    UnsafeConfigurationRecordError,
)

MAX_CONFIG_BYTES = 1_048_576
LAUNCH_SCHEMA_VERSION = 1

_APPLICATION = re.compile(r"[0-9]{2}_[a-z][a-z0-9_]*")
_SETTING = re.compile(r"[a-z][a-z0-9_]*")

type ConfigurationValues = Mapping[str, object]
type ValidationHook = Callable[[ConfigurationValues], ConfigurationValues]


def freeze_values(value: object) -> object:
    """Return an immutable TOML-compatible value or raise a typed error."""

    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        raise InvalidConfigurationSchemaError("configuration float must be finite")
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        items = cast(Mapping[object, object], value).items()
        for key, nested in items:
            if not isinstance(key, str) or _SETTING.fullmatch(key) is None:
                raise InvalidConfigurationSchemaError(
                    "configuration key must be lowercase snake case"
                )
            frozen[key] = freeze_values(nested)
        return MappingProxyType(dict(sorted(frozen.items())))
    if isinstance(value, (list, tuple)):
        return tuple(freeze_values(item) for item in cast(tuple[object, ...], value))
    raise InvalidConfigurationSchemaError("configuration value is not TOML-compatible")


def freeze_settings(value: object) -> Mapping[str, object]:
    """Freeze one application's named settings without accepting aliases."""

    if not isinstance(value, Mapping):
        raise InvalidConfigurationSchemaError(
            "configuration defaults must be a mapping"
        )
    frozen = freeze_values(cast(Mapping[object, object], value))
    if not isinstance(frozen, Mapping):  # Defensive narrowing for static analysis.
        raise InvalidConfigurationSchemaError(
            "configuration defaults must be a mapping"
        )
    return cast(Mapping[str, object], frozen)


class ConfigurationSourceKind(StrEnum):
    """The single source that contributed settings before CLI precedence."""

    DEFAULTS = "defaults"
    FILE = "file"


@dataclass(frozen=True, slots=True)
class ApplicationSpec:
    """Application-owned settings defaults and domain-validation hook."""

    application: str
    config_filename: str
    defaults: ConfigurationValues
    validate: ValidationHook
    secret_keys: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        application = cast(object, self.application)
        filename = cast(object, self.config_filename)
        if (
            not isinstance(application, str)
            or _APPLICATION.fullmatch(application) is None
        ):
            raise InvalidConfigurationSchemaError(
                "application must be numbered snake case"
            )
        expected_filename = f"{application}.toml"
        if filename != expected_filename:
            raise InvalidConfigurationSchemaError(
                "configuration filename must match application identity"
            )
        if not callable(cast(object, self.validate)):
            raise InvalidConfigurationSchemaError(
                "configuration validation hook must be callable"
            )
        frozen = freeze_settings(cast(object, self.defaults))
        object.__setattr__(self, "defaults", frozen)
        secret_keys = cast(object, self.secret_keys)
        if not isinstance(secret_keys, frozenset) or not all(
            isinstance(key, str) and key in frozen
            for key in cast(frozenset[object], secret_keys)
        ):
            raise InvalidConfigurationSchemaError(
                "secret keys must be a frozen subset of configuration defaults"
            )
        if secret_keys:
            raise UnsafeConfigurationRecordError(
                "secret-bearing settings need an owner-defined recording rule"
            )


@dataclass(frozen=True, slots=True)
class ConfigSelector:
    """At most one explicit application configuration selector."""

    config_file: Path | None = None
    config_dir: Path | None = None

    def __post_init__(self) -> None:
        config_file = cast(object, self.config_file)
        config_dir = cast(object, self.config_dir)
        if not isinstance(config_file, (Path, type(None))) or not isinstance(
            config_dir, (Path, type(None))
        ):
            raise InvalidConfigurationSelectorError(
                "configuration selector must be a Path"
            )
        if config_file is not None and config_dir is not None:
            raise InvalidConfigurationSelectorError(
                "config-file and config-dir are mutually exclusive"
            )


@dataclass(frozen=True, slots=True)
class ConfigSource:
    """Resolved defaults-only or selected-file configuration source."""

    kind: ConfigurationSourceKind
    path: Path | None = None

    def __post_init__(self) -> None:
        kind = cast(object, self.kind)
        path = cast(object, self.path)
        if not isinstance(kind, ConfigurationSourceKind):
            raise InvalidConfigurationSelectorError(
                "configuration source kind is invalid"
            )
        if not isinstance(path, (Path, type(None))):
            raise InvalidConfigurationSelectorError(
                "configuration source path must be a Path"
            )
        if (kind is ConfigurationSourceKind.DEFAULTS) != (path is None):
            raise InvalidConfigurationSelectorError(
                "configuration source kind and path disagree"
            )


@dataclass(frozen=True, slots=True)
class ResolvedConfiguration:
    """Immutable effective settings and the selected configuration source."""

    values: ConfigurationValues
    source: ConfigSource

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.source), ConfigSource):
            raise InvalidConfigurationSelectorError(
                "resolved configuration source is invalid"
            )
        object.__setattr__(self, "values", freeze_settings(cast(object, self.values)))
