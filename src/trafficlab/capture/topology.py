"""Deterministic Docker Compose rendering for the production capture topology."""

from dataclasses import dataclass
from pathlib import Path

from trafficlab.common.config import ExperimentConfig, MountConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.json import render_json_document

_CAPTURE_OUTPUT_DIRECTORY = "/trafficlab"


@dataclass(frozen=True, slots=True)
class ComposePaths:
    """Project identity and the host directory exposed to the capture service."""

    project_name: str
    output_directory: Path


def _bind_mount(source: Path, target: str, *, read_only: bool) -> dict[str, object]:
    try:
        resolved_source = source.resolve()
    except (OSError, RuntimeError) as error:
        raise TrafficlabError(
            f"could not resolve Docker bind source {source}: {error}",
            corrective_action="verify Docker bind source paths can be resolved and retry",
        ) from error
    return {
        "type": "bind",
        "source": str(resolved_source),
        "target": target,
        "read_only": read_only,
    }


def _target_mount(mount: MountConfig) -> dict[str, object]:
    return _bind_mount(mount.source, mount.target, read_only=mount.read_only)


def render_production_compose(
    config: ExperimentConfig,
    paths: ComposePaths,
    *,
    target_image: str,
    capture_image: str,
) -> bytes:
    """Render the exact two-service production topology as deterministic JSON."""
    capture: dict[str, object] = {
        "image": capture_image,
        "cap_add": ["NET_RAW", "NET_ADMIN"],
        "volumes": [_bind_mount(paths.output_directory, _CAPTURE_OUTPUT_DIRECTORY, read_only=False)],
    }
    target: dict[str, object] = {
        "image": target_image,
        "command": list(config.target.argv),
        "environment": dict(config.target.environment),
        "working_dir": config.target.working_directory,
        "volumes": [_target_mount(mount) for mount in config.target.mounts],
        "network_mode": "service:capture",
        "init": True,
    }
    document: dict[str, object] = {
        "name": paths.project_name,
        "services": {"capture": capture, "target": target},
    }
    return render_json_document(document)


def write_production_compose(
    path: Path,
    config: ExperimentConfig,
    paths: ComposePaths,
    *,
    target_image: str,
    capture_image: str,
) -> None:
    """Write a production Compose document exactly as rendered."""
    rendered = render_production_compose(
        config,
        paths,
        target_image=target_image,
        capture_image=capture_image,
    )
    try:
        path.write_bytes(rendered)
    except OSError as error:
        raise TrafficlabError(
            f"could not write Compose file {path}: {error}",
            corrective_action="verify the Compose destination is writable",
        ) from error
