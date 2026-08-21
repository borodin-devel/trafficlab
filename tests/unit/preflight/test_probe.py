from pathlib import Path
from typing import cast

import pytest

import trafficlab.preflight.probe as probe_module
from trafficlab.capture.docker.types import ServiceState
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import CaptureMetadata, render_capture_metadata
from trafficlab.preflight.docker import require_deadline
from trafficlab.preflight.types import DockerPreflight


def test_capture_readiness_distinguishes_missing_and_unreadable_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "probe"
    output.mkdir()
    assert probe_module._capture_ready(output) is False  # pyright: ignore[reportPrivateUsage]

    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    (output / "capture.json").write_bytes(render_capture_metadata(metadata))
    capture_path = output / "reference.pcapng.tmp"
    capture_path.write_bytes(b"x" * 28)
    real_read_bytes = Path.read_bytes

    def fail_capture_read(path: Path) -> bytes:
        if path == capture_path:
            raise OSError("injected header read failure")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_capture_read)
    with pytest.raises(TrafficlabError, match="could not inspect preflight capture header"):
        probe_module._capture_ready(output)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("states", [(None,), ("running", "exited")], ids=["missing", "not-ready-then-exited"])
def test_capture_readiness_rejects_a_missing_or_stopped_service(tmp_path: Path, states: tuple[str | None, ...]) -> None:
    output = tmp_path / "probe"
    output.mkdir()

    class States:
        def __init__(self) -> None:
            self.values = iter(states)

        def service_state(
            self, compose_path: Path, project_name: str, service: str, *, deadline: float
        ) -> ServiceState | None:
            del compose_path, project_name, service, deadline
            state = next(self.values)
            if state is None:
                return None
            return ServiceState("capture", "capture", "capture", state, 127 if state == "exited" else 0)

    with pytest.raises(TrafficlabError, match="dumpcap is unavailable"):
        probe_module._wait_capture_ready(  # pyright: ignore[reportPrivateUsage]
            cast(DockerPreflight, States()),
            tmp_path / "compose.json",
            "probe",
            output,
            deadline=2.0,
            clock=lambda: 1.0,
            observed={},
            require_deadline=require_deadline,
        )


def test_target_probe_waits_through_absent_and_running_states(tmp_path: Path) -> None:
    states = iter(
        (
            None,
            ServiceState("target", "target", "target", "running", 0),
            ServiceState("target", "target", "target", "exited", 0),
        )
    )

    class States:
        def service_state(
            self, compose_path: Path, project_name: str, service: str, *, deadline: float
        ) -> ServiceState | None:
            del compose_path, project_name, service, deadline
            return next(states)

    observed: dict[str, ServiceState] = {}
    probe_module._wait_target(  # pyright: ignore[reportPrivateUsage]
        cast(DockerPreflight, States()),
        tmp_path / "compose.json",
        "probe",
        deadline=2.0,
        clock=lambda: 1.0,
        observed=observed,
        require_deadline=require_deadline,
    )

    assert observed["target"].state == "exited"
