"""TOML loading and deterministic rendering for experiment configurations."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import ValidationError

from trafficlab.config import ExperimentConfig
from trafficlab.errors import TrafficlabError


@dataclass(frozen=True, slots=True)
class ConfigurationPair:
    """One portable configuration and its host-path-realized counterpart."""

    portable: ExperimentConfig
    realized: ExperimentConfig


def _format_validation_errors(error: ValidationError) -> str:
    messages: list[str] = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail["loc"])
        messages.append(f"{location}: {detail['msg']}")
    return "; ".join(messages)


def _resolve_source_path(path: Path, source_directory: Path) -> Path:
    if path.is_absolute():
        return path
    return (source_directory / path).resolve()


def realize_configuration(portable: ExperimentConfig, config_directory: Path) -> ExperimentConfig:
    """Resolve only host path values from a portable validated configuration."""
    try:
        data: dict[str, Any] = portable.model_dump(mode="python")
        run = data["run"]
        run["directory"] = _resolve_source_path(run["directory"], config_directory)
        target = data["target"]
        for mount in target["mounts"]:
            mount["source"] = _resolve_source_path(mount["source"], config_directory)
        return ExperimentConfig.model_validate(data)
    except (OSError, RuntimeError, ValidationError) as error:
        raise TrafficlabError(
            f"could not resolve experiment paths from {config_directory}: {error}",
            corrective_action="verify the configured host paths can be resolved and retry",
        ) from error


def load_configuration_pair(path: Path) -> ConfigurationPair:
    """Load a portable experiment and realize its host paths relative to its TOML file."""
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except UnicodeDecodeError as error:
        raise TrafficlabError(
            f"experiment configuration {path} is not valid UTF-8: {error}",
            corrective_action="save the experiment file as valid UTF-8 and retry",
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise TrafficlabError(
            f"invalid TOML in experiment configuration {path}: {error}",
            corrective_action="correct the TOML syntax and retry",
        ) from error
    except OSError as error:
        raise TrafficlabError(
            f"could not read experiment configuration {path}: {error}",
            corrective_action="verify the experiment file exists and is readable",
        ) from error

    try:
        portable = ExperimentConfig.model_validate(data)
    except ValidationError as error:
        raise TrafficlabError(
            f"invalid experiment configuration {path}: {_format_validation_errors(error)}",
            corrective_action="correct the reported configuration values and retry",
        ) from error

    try:
        return ConfigurationPair(
            portable=portable,
            realized=realize_configuration(portable, path.parent.resolve()),
        )
    except (OSError, RuntimeError) as error:
        raise TrafficlabError(
            f"could not resolve experiment paths from {path}: {error}",
            corrective_action="verify the configured host paths can be resolved and retry",
        ) from error


def load_experiment(path: Path) -> ExperimentConfig:
    """Load one experiment in the realized form for compatibility."""
    return load_configuration_pair(path).realized


def render_effective_config(config: ExperimentConfig) -> bytes:
    """Render deterministic TOML only when it validates back to the same model."""
    data: dict[str, Any] = config.model_dump(mode="json", exclude_none=True)
    text = tomli_w.dumps(data)
    reparsed = tomllib.loads(text)
    if ExperimentConfig.model_validate(reparsed) != config:
        raise TrafficlabError(
            "effective configuration did not round-trip",
            corrective_action="report the deterministic configuration renderer defect",
        )
    return text.encode("utf-8")
