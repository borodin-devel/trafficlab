"""Disposable Docker Compose capture and network feature probe."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from trafficlab import USER_AGENT
from trafficlab.capture.topology import ComposePaths, render_production_compose
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng
from trafficlab.common.trace import load_capture_metadata
from trafficlab.preflight.types import DockerPreflight

if TYPE_CHECKING:
    from trafficlab.capture.docker.types import ServiceState

_DeadlineChecker = Callable[[float, Callable[[], float]], float]


def render_probe_compose(
    config: ExperimentConfig,
    paths: ComposePaths,
    *,
    capture_image: str,
) -> bytes:
    document = cast(
        dict[str, object],
        json.loads(
            render_production_compose(
                config,
                paths,
                target_image=capture_image,
                capture_image=capture_image,
            )
        ),
    )
    services = cast(dict[str, object], document["services"])
    target = cast(dict[str, object], services["target"])
    target.clear()
    target.update(
        {
            "command": [],
            "entrypoint": [
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--user-agent",
                USER_AGENT,
                "--connect-timeout",
                f"{config.capture.total_timeout_seconds:g}",
                "--max-time",
                f"{config.capture.total_timeout_seconds:g}",
                "--range",
                "0-0",
                "--output",
                "/dev/null",
                config.capture.network_probe_url,
            ],
            "image": capture_image,
            "init": True,
            "network_mode": "service:capture",
        }
    )
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_probe_compose(path: Path, content: bytes) -> None:
    try:
        path.write_bytes(content)
    except OSError as error:
        raise TrafficlabError(
            f"could not write preflight Compose file {path}: {error}",
            corrective_action="verify the run parent is writable and retry",
        ) from error


def _capture_ready(output: Path) -> bool:
    metadata_path = output / "capture.json"
    capture_path = output / "reference.pcapng.tmp"
    if not metadata_path.exists() or not capture_path.exists():
        return False
    load_capture_metadata(metadata_path)
    try:
        header = capture_path.read_bytes()[:28]
    except OSError as error:
        raise TrafficlabError(
            f"could not inspect preflight capture header: {error}",
            corrective_action="verify the capture image can write its output bind mount",
        ) from error
    if len(header) < 28 or header[:4] != b"\x0a\x0d\x0d\x0a":
        raise TrafficlabError(
            "dumpcap version is incompatible",
            corrective_action="restore the declared capture-tool version",
        )
    return True


def _wait_capture_ready(
    compose: DockerPreflight,
    compose_path: Path,
    project_name: str,
    output: Path,
    *,
    deadline: float,
    clock: Callable[[], float],
    observed: dict[str, ServiceState],
    require_deadline: _DeadlineChecker,
) -> None:
    while True:
        require_deadline(deadline, clock)
        state = compose.service_state(compose_path, project_name, "capture", deadline=deadline)
        if state is not None:
            observed[state.service] = state
        if state is None or state.state != "running":
            raise TrafficlabError(
                "dumpcap is unavailable",
                corrective_action="install the declared capture tool in the capture image",
            )
        if _capture_ready(output):
            return


def _wait_target(
    compose: DockerPreflight,
    compose_path: Path,
    project_name: str,
    *,
    deadline: float,
    clock: Callable[[], float],
    observed: dict[str, ServiceState],
    require_deadline: _DeadlineChecker,
) -> None:
    while True:
        require_deadline(deadline, clock)
        state = compose.service_state(compose_path, project_name, "target", deadline=deadline)
        if state is not None:
            observed[state.service] = state
        if state is None or state.state == "running":
            continue
        if state.state != "exited":
            raise TrafficlabError(
                "capture prerequisite is incompatible",
                corrective_action="satisfy the named prerequisite compatibility contract",
            )
        if state.exit_code != 0:
            raise TrafficlabError(
                "capture prerequisite is unavailable",
                corrective_action="make the named prerequisite available",
            )
        return


def _finish_capture_probe(
    compose: DockerPreflight,
    compose_path: Path,
    project_name: str,
    output: Path,
    *,
    deadline: float,
    clock: Callable[[], float],
    observed: dict[str, ServiceState],
    require_deadline: _DeadlineChecker,
) -> None:
    require_deadline(deadline, clock)
    state = compose.service_state(compose_path, project_name, "capture", deadline=deadline)
    if state is not None:
        observed[state.service] = state
    if state is None or state.state != "running":
        raise TrafficlabError(
            "capture probe stopped unexpectedly during the network request",
            corrective_action="verify the capture image can keep dumpcap running on eth0",
        )
    compose.signal_capture(compose_path, project_name, deadline=deadline)
    while True:
        require_deadline(deadline, clock)
        state = compose.service_state(compose_path, project_name, "capture", deadline=deadline)
        if state is not None:
            observed[state.service] = state
        if state is not None and state.state == "running":
            continue
        if state is None or state.state != "exited" or state.exit_code != 0:
            raise TrafficlabError(
                "capture probe did not flush successfully",
                corrective_action="verify dumpcap handles SIGINT and writes a complete PCAPNG",
            )
        break
    metadata = load_capture_metadata(output / "capture.json")
    trace = read_pcapng(output / "reference.pcapng.tmp", metadata, deadline=deadline, clock=clock)
    if not trace:
        raise TrafficlabError(
            "network probe completed without captured Ethernet traffic",
            corrective_action="verify the probe endpoint is reached through capture service eth0",
        )


def run_probe(
    config: ExperimentConfig,
    compose: DockerPreflight,
    compose_path: Path,
    project_name: str,
    output: Path,
    *,
    deadline: float,
    clock: Callable[[], float],
    observed: dict[str, ServiceState],
    require_deadline: _DeadlineChecker,
) -> None:
    compose.start_capture(compose_path, project_name, deadline=deadline)
    _wait_capture_ready(
        compose,
        compose_path,
        project_name,
        output,
        deadline=deadline,
        clock=clock,
        observed=observed,
        require_deadline=require_deadline,
    )
    compose.start_target(compose_path, project_name, deadline=deadline)
    _wait_target(
        compose,
        compose_path,
        project_name,
        deadline=deadline,
        clock=clock,
        observed=observed,
        require_deadline=require_deadline,
    )
    _finish_capture_probe(
        compose,
        compose_path,
        project_name,
        output,
        deadline=deadline,
        clock=clock,
        observed=observed,
        require_deadline=require_deadline,
    )
