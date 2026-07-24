"""Tests for immutable configuration contracts."""

from collections.abc import Callable, Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from trafficlab.libs.configuration import (
    ApplicationSpec,
    ConfigSelector,
    ConfigSource,
    ConfigurationSourceKind,
    InvalidConfigurationSchemaError,
    InvalidConfigurationSelectorError,
    ResolvedConfiguration,
    UnsafeConfigurationRecordError,
)


def _validate(values: Mapping[str, object]) -> Mapping[str, object]:
    return values


@pytest.mark.unit
def test_application_spec_freezes_valid_toml_values() -> None:
    spec = ApplicationSpec(
        application="10_capture",
        config_filename="10_capture.toml",
        defaults={"limits": {"count": 3}, "ports": [80, 443]},
        validate=_validate,
    )

    assert spec.application == "10_capture"
    assert spec.config_filename == "10_capture.toml"
    assert spec.defaults == {"limits": {"count": 3}, "ports": (80, 443)}
    assert not hasattr(spec, "__dict__")
    with pytest.raises(FrozenInstanceError):
        spec.application = "20_convert"  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("application", "filename", "defaults", "validate"),
    (
        pytest.param("capture", "10_capture.toml", {}, _validate, id="app-prefix"),
        pytest.param("10_capture", "capture.toml", {}, _validate, id="wrong-file"),
        pytest.param(
            "10_capture", "10_capture.toml", {"bad-key": 1}, _validate, id="key"
        ),
        pytest.param(
            "10_capture",
            "10_capture.toml",
            {"value": float("nan")},
            _validate,
            id="nan",
        ),
        pytest.param(
            "10_capture",
            "10_capture.toml",
            {"value": object()},
            _validate,
            id="object",
        ),
        pytest.param(
            "10_capture",
            "10_capture.toml",
            {"value": None},
            _validate,
            id="none",
        ),
        pytest.param(
            "10_capture", "10_capture.toml", {"value": 1}, object(), id="hook"
        ),
    ),
)
def test_application_spec_rejects_invalid_runtime_values(
    application: object,
    filename: object,
    defaults: object,
    validate: object,
) -> None:
    with pytest.raises(InvalidConfigurationSchemaError) as caught:
        ApplicationSpec(
            application=cast(str, application),
            config_filename=cast(str, filename),
            defaults=cast(Mapping[str, object], defaults),
            validate=cast(
                Callable[[Mapping[str, object]], Mapping[str, object]], validate
            ),
        )

    assert str(caught.value).splitlines() == [str(caught.value)]


@pytest.mark.unit
def test_secret_marked_schema_is_rejected_until_safe_recording_is_owned() -> None:
    with pytest.raises(UnsafeConfigurationRecordError):
        ApplicationSpec(
            application="10_capture",
            config_filename="10_capture.toml",
            defaults={"token": "top-secret"},
            validate=_validate,
            secret_keys=frozenset({"token"}),
        )


@pytest.mark.unit
def test_selector_accepts_only_one_explicit_source() -> None:
    selected = ConfigSelector(config_file=Path("settings.toml"))
    directory = ConfigSelector(config_dir=Path(".configs"))
    defaults = ConfigSelector()

    assert selected.config_file == Path("settings.toml")
    assert directory.config_dir == Path(".configs")
    assert defaults.config_file is None
    assert defaults.config_dir is None


@pytest.mark.unit
def test_selector_rejects_conflicting_or_wrong_runtime_sources() -> None:
    with pytest.raises(InvalidConfigurationSelectorError):
        ConfigSelector(config_file=Path("a.toml"), config_dir=Path("configs"))
    with pytest.raises(InvalidConfigurationSelectorError):
        ConfigSelector(config_file="a.toml")  # type: ignore[arg-type]


@pytest.mark.unit
def test_source_and_resolved_configuration_are_immutable() -> None:
    source = ConfigSource(ConfigurationSourceKind.FILE, Path("settings.toml"))
    resolved = ResolvedConfiguration({"count": 2}, source)

    assert source.kind is ConfigurationSourceKind.FILE
    assert source.path == Path("settings.toml")
    assert resolved.values == {"count": 2}
    with pytest.raises(FrozenInstanceError):
        resolved.source = source  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kind", "path"),
    (
        pytest.param(
            ConfigurationSourceKind.DEFAULTS,
            Path("settings.toml"),
            id="defaults-path",
        ),
        pytest.param(ConfigurationSourceKind.FILE, None, id="file-path"),
        pytest.param("file", Path("settings.toml"), id="kind"),
    ),
)
def test_source_rejects_invalid_shape(kind: object, path: object) -> None:
    with pytest.raises(InvalidConfigurationSelectorError):
        ConfigSource(kind, path)  # type: ignore[arg-type]
