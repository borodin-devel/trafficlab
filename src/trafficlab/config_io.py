"""TOML loading and deterministic rendering for experiment configurations."""

import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import ValidationError

from trafficlab.config import ExperimentConfig
from trafficlab.errors import TrafficlabError


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


def load_experiment(path: Path) -> ExperimentConfig:
    """Load one experiment and resolve its host paths relative to its TOML file."""
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
        config = ExperimentConfig.model_validate(data)
    except ValidationError as error:
        raise TrafficlabError(
            f"invalid experiment configuration {path}: {_format_validation_errors(error)}",
            corrective_action="correct the reported configuration values and retry",
        ) from error

    try:
        source_directory = path.parent.resolve()
        run = config.run.model_copy(update={"directory": _resolve_source_path(config.run.directory, source_directory)})
        mounts = tuple(
            mount.model_copy(update={"source": _resolve_source_path(mount.source, source_directory)})
            for mount in config.target.mounts
        )
        target = config.target.model_copy(update={"mounts": mounts})
        return config.model_copy(update={"run": run, "target": target})
    except (OSError, RuntimeError) as error:
        raise TrafficlabError(
            f"could not resolve experiment paths from {path}: {error}",
            corrective_action="verify the configured host paths can be resolved and retry",
        ) from error


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
