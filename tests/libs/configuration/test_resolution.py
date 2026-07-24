"""Tests for deterministic selected-TOML configuration resolution."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from trafficlab.libs.configuration import (
    ApplicationSpec,
    ConfigSelector,
    ConfigurationSourceError,
    ConfigurationSourceKind,
    ConfigurationValidationError,
    InvalidConfigurationSelectorError,
    resolve_configuration,
)


def _spec(*, validate: object | None = None) -> ApplicationSpec:
    def default_validate(values: Mapping[str, object]) -> Mapping[str, object]:
        if type(values["count"]) is not int or values["count"] < 1:
            raise ValueError("count")
        return values

    return ApplicationSpec(
        application="10_capture",
        config_filename="10_capture.toml",
        defaults={"count": 1, "label": "default"},
        validate=default_validate if validate is None else validate,  # type: ignore[arg-type]
    )


@pytest.mark.unit
def test_resolution_precedence_is_defaults_file_then_explicit_cli(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "settings.toml"
    selected.write_text('count = 2\nlabel = "file"\n', encoding="utf-8")

    result = resolve_configuration(
        _spec(),
        ConfigSelector(config_file=selected),
        {"label": "cli"},
    )

    assert result.values == {"count": 2, "label": "cli"}
    assert result.source.kind is ConfigurationSourceKind.FILE
    assert result.source.path == selected


@pytest.mark.unit
def test_resolution_uses_defaults_without_implicit_discovery(tmp_path: Path) -> None:
    (tmp_path / "10_capture.toml").write_text("count = 9\n", encoding="utf-8")

    result = resolve_configuration(_spec(), ConfigSelector(), {})

    assert result.values == {"count": 1, "label": "default"}
    assert result.source.kind is ConfigurationSourceKind.DEFAULTS


@pytest.mark.unit
def test_directory_selector_uses_only_application_filename(tmp_path: Path) -> None:
    selected = tmp_path / "10_capture.toml"
    selected.write_text("count = 3\n", encoding="utf-8")

    result = resolve_configuration(_spec(), ConfigSelector(config_dir=tmp_path), {})

    assert result.values == {"count": 3, "label": "default"}
    assert result.source.path == selected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("contents", "error"),
    (
        pytest.param("unknown = 1\n", ConfigurationValidationError, id="unknown"),
        pytest.param("count = 0\n", ConfigurationValidationError, id="domain"),
        pytest.param("count = [1]\n", ConfigurationValidationError, id="type"),
        pytest.param(
            "[nested]\nvalue = 1\n", ConfigurationValidationError, id="nested"
        ),
        pytest.param("count =\n", ConfigurationSourceError, id="malformed"),
        pytest.param("[array]\n", ConfigurationValidationError, id="table"),
    ),
)
def test_resolution_rejects_invalid_selected_content(
    tmp_path: Path,
    contents: str,
    error: type[Exception],
) -> None:
    selected = tmp_path / "settings.toml"
    selected.write_text(contents, encoding="utf-8")

    with pytest.raises(error) as caught:
        resolve_configuration(_spec(), ConfigSelector(config_file=selected), {})

    assert str(caught.value).splitlines() == [str(caught.value)]


@pytest.mark.unit
def test_resolution_rejects_missing_directory_and_symlink_selected_sources(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.toml"
    folder = tmp_path / "folder"
    folder.mkdir()
    target = tmp_path / "target.toml"
    target.write_text("count = 2\n", encoding="utf-8")
    alias = tmp_path / "alias.toml"
    alias.symlink_to(target)

    for selected in (missing, folder, alias):
        with pytest.raises(ConfigurationSourceError):
            resolve_configuration(_spec(), ConfigSelector(config_file=selected), {})


@pytest.mark.unit
def test_resolution_rejects_unknown_cli_and_validator_shape() -> None:
    with pytest.raises(ConfigurationValidationError):
        resolve_configuration(_spec(), ConfigSelector(), {"unknown": 1})

    def missing_value(_: Mapping[str, object]) -> Mapping[str, object]:
        return {"count": 1}

    with pytest.raises(ConfigurationValidationError):
        resolve_configuration(_spec(validate=missing_value), ConfigSelector(), {})


@pytest.mark.unit
def test_resolution_rejects_invalid_cli_value_without_mutating_defaults() -> None:
    spec = _spec()

    with pytest.raises(ConfigurationValidationError):
        resolve_configuration(spec, ConfigSelector(), {"count": 0})

    assert spec.defaults == {"count": 1, "label": "default"}


@pytest.mark.unit
def test_resolution_requires_selector_paths_to_be_paths() -> None:
    with pytest.raises(InvalidConfigurationSelectorError):
        ConfigSelector(config_dir="configs")  # type: ignore[arg-type]
