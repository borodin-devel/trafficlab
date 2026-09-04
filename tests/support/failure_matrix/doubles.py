import json
from dataclasses import dataclass
from pathlib import Path

import trafficlab.preflight.docker as preflight_docker
from tests.support.failure_matrix.cases import Scenario
from tests.support.scapy_fixtures import encode_events as encode_pcapng
from trafficlab.capture.docker.image import load_capture_image_lock
from trafficlab.capture.docker.types import CommandResult, ServiceState
from trafficlab.common.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata


def runtime_model_double(_best: object) -> object:
    return object()


class CompletedHandle:
    def __init__(self, *, timeout: bool = False) -> None:
        self.timeout = timeout
        self.waited = False

    def wait(self, *, timeout: float) -> CommandResult | None:
        del timeout
        self.waited = True
        return None if self.timeout else CommandResult(0, "", "")

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    def reap(self) -> bool:
        return True


class PreflightClock:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        if self.scenario == "dumpcap_incompatible" and self.calls > 20:
            return 100.0
        return 0.0


class PreflightDocker:
    """Primitive command, state, and file effects for full public preflight."""

    def __init__(self, scenario: Scenario, mount_source: Path | None) -> None:
        self.scenario = scenario
        self.mount_source = mount_source
        self.signaled = False
        lock = load_capture_image_lock(preflight_docker._CAPTURE_IMAGE_LOCK_PATH)  # pyright: ignore[reportPrivateUsage]
        self.capture_id = lock.expected_capture_image_id
        self.target_id = "sha256:" + ("c" * 64)
        self.host_architecture = "linux/amd64"
        self.capture_tool_version = "4.0.17"

    @staticmethod
    def _result(returncode: int = 0, stdout: str = "") -> CommandResult:
        return CommandResult(returncode, stdout, "command failed" if returncode else "")

    def info(self, *, deadline: float) -> CommandResult:
        del deadline
        if self.mount_source is not None and self.scenario == "mount_source_unavailable":
            self.mount_source.unlink()
        if self.scenario == "docker_unavailable":
            return self._result(1)
        return self._result(stdout='{"OSType":"linux","Architecture":"x86_64"}')

    def compose_version(self, *, deadline: float) -> CommandResult:
        del deadline
        version = '{"version":"v1.29.2"}' if self.scenario == "compose_incompatible" else '{"version":"v5.4.0"}'
        return self._result(stdout=version)

    def image_inspect(self, image: str, *, deadline: float) -> CommandResult:
        del deadline
        if self.scenario == "target_image_unavailable" and "example.invalid/app" in image:
            return self._result(1)
        content_id = self.capture_id if "capture" in image else self.target_id
        if self.scenario == "capture_image_incompatible" and "capture" in image:
            content_id = "sha256:" + ("e" * 64)
        return self._result(
            stdout=json.dumps(
                [
                    {
                        "Id": content_id,
                        "RepoDigests": [],
                        "RepoTags": [image],
                        "Os": "linux",
                        "Architecture": "amd64",
                    }
                ]
            )
        )

    def image_pull(self, image: str, *, deadline: float) -> CommandResult:
        del image, deadline
        return self._result(1 if self.scenario == "target_image_unavailable" else 0)

    def config(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        del compose_path, project_name, deadline
        return self._result(1 if self.scenario == "mount_target_incompatible" else 0)

    def create_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        del project_name, deadline
        output = compose_path.parent / "probe-output"
        if self.scenario != "dumpcap_unavailable":
            metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
            (output / "capture.json").write_bytes(render_capture_metadata(metadata))
            pcap = (
                b"not-pcapng"
                if self.scenario == "dumpcap_incompatible"
                else encode_pcapng((TraceEvent(1.0, Direction.OUTBOUND, 64),), metadata)
            )
            (output / "reference.pcapng.tmp").write_bytes(pcap)
        return self._result()

    def start_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        del compose_path, project_name, deadline
        return self._result()

    def start_target(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        del compose_path, project_name, deadline
        return self._result()

    def service_state(
        self, compose_path: Path, project_name: str, service: str, *, deadline: float
    ) -> ServiceState | None:
        del compose_path, project_name, deadline
        if service == "capture":
            if self.scenario == "dumpcap_unavailable":
                return ServiceState("capture", "capture", "capture", "exited", 127)
            return ServiceState("capture", "capture", "capture", "exited" if self.signaled else "running", 0)
        if self.scenario == "prerequisite_unavailable":
            return ServiceState("target", "target", "target", "exited", 7)
        if self.scenario == "prerequisite_incompatible":
            return ServiceState("target", "target", "target", "dead", 0)
        return ServiceState("target", "target", "target", "exited", 0)

    def signal_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        del compose_path, project_name, deadline
        self.signaled = True
        return self._result()

    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> CompletedHandle:
        del compose_path, project_name, deadline
        return CompletedHandle()


@dataclass(frozen=True, slots=True)
class CaptureFailureLog:
    detail: str
    failure_kind: str
    primary_status: int | None = None
    secondary_failures: tuple[tuple[str, str, int | None], ...] = ()


class CaptureDocker:
    """Primitive service observations and capture bytes for the real lifecycle."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.target_started = False
        self.target_killed = False
        self.capture_signaled = False
        self.capture_killed = False
        self.target_observed = False
        self.workload_clock_observed = False
        self.flush_done = False
        self.lifecycle_done = False
        self.total_pending = False
        self.total_emitted = False
        self.post_flush_clock_calls = 0
        self.cleanup_handle: CompletedHandle | None = None
        self.created_metadata: bytes | None = None
        self.created_reference: bytes | None = None
        self.project_name: str | None = None
        self.target_exit_status: int | None = None
        self.capture_exit_status: int | None = None

    @staticmethod
    def _result() -> CommandResult:
        return CommandResult(0, "", "")

    def create_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        del deadline
        assert self.project_name is None or self.project_name == project_name
        self.project_name = project_name
        metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
        self.created_metadata = render_capture_metadata(metadata)
        (compose_path.parent / "capture.json").write_bytes(self.created_metadata)
        valid_content = encode_pcapng((TraceEvent(1.0, Direction.OUTBOUND, 64),), metadata)
        self.created_reference = valid_content
        content = valid_content[:28] if self.scenario == "malformed_capture" else valid_content
        (compose_path.parent / "reference.pcapng.tmp").write_bytes(content)
        return self._result()

    def start_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        del compose_path, project_name, deadline
        return self._result()

    def start_target(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        del compose_path, project_name, deadline
        self.target_started = True
        return self._result()

    def kill_target(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        del compose_path, project_name, deadline
        self.target_killed = True
        self.lifecycle_done = True
        return self._result()

    def signal_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        del compose_path, project_name, deadline
        self.capture_signaled = True
        return self._result()

    def kill_capture(self, compose_path: Path, project_name: str, *, deadline: float) -> CommandResult:
        del compose_path, project_name, deadline
        self.capture_killed = True
        self.lifecycle_done = True
        return self._result()

    def service_state(
        self, compose_path: Path, project_name: str, service: str, *, deadline: float
    ) -> ServiceState | None:
        del compose_path, project_name, deadline
        if service == "target":
            if self.target_killed:
                if self.scenario == "workload_timeout_target_137":
                    self.target_exit_status = 137
                    return ServiceState("target", "target", "target", "exited", 137)
                return None
            if self.scenario in {
                "target_exit_23",
                "target_23_cleanup_timeout",
                "target_23_capture_42_total_timeout",
            }:
                self.target_observed = True
                self.target_exit_status = 23
                return ServiceState("target", "target", "target", "exited", 23)
            if self.scenario in {
                "capture_exit_42_after_target_0",
                "flush_timeout_after_target_0",
                "validation_total_timeout",
                "malformed_capture",
                "cleanup_timeout_after_success",
                "flush_and_total_timeout",
            }:
                self.target_observed = True
                self.target_exit_status = 0
                return ServiceState("target", "target", "target", "exited", 0)
            return None
        if not self.target_started:
            return ServiceState("capture", "capture", "capture", "running", 0)
        if self.scenario in {
            "capture_exit_42_active",
            "capture_exit_42_after_target_0",
            "target_23_capture_42_total_timeout",
        }:
            self.lifecycle_done = self.scenario != "target_23_capture_42_total_timeout"
            if self.scenario == "target_23_capture_42_total_timeout":
                self.total_pending = True
            self.capture_exit_status = 42
            return ServiceState("capture", "capture", "capture", "exited", 42)
        if self.capture_signaled:
            self.flush_done = True
            self.lifecycle_done = self.scenario not in {
                "validation_total_timeout",
                "cleanup_timeout_after_success",
                "target_23_cleanup_timeout",
            }
            self.capture_exit_status = 0
            return ServiceState("capture", "capture", "capture", "exited", 0)
        return ServiceState("capture", "capture", "capture", "running", 0)

    def service_logs(self, compose_path: Path, project_name: str, service: str, *, deadline: float) -> str:
        del compose_path, project_name, service, deadline
        return "capture diagnostics"

    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> CompletedHandle:
        del compose_path, project_name, deadline
        timeout = self.scenario in {"cleanup_timeout_after_success", "target_23_cleanup_timeout"}
        self.cleanup_handle = CompletedHandle(timeout=timeout)
        return self.cleanup_handle

    def clock(self) -> float:
        if self.cleanup_handle is not None and self.cleanup_handle.timeout and self.cleanup_handle.waited:
            return 11.0
        if self.total_pending and not self.total_emitted:
            self.total_emitted = True
            self.lifecycle_done = True
            return 11.0
        if (
            self.scenario in {"workload_timeout", "workload_timeout_target_137"}
            and self.target_started
            and not self.lifecycle_done
        ):
            return 6.0
        if (
            self.scenario in {"flush_timeout_after_target_0", "flush_and_total_timeout"}
            and self.target_observed
            and not self.lifecycle_done
        ):
            if not self.workload_clock_observed:
                self.workload_clock_observed = True
                return 0.0
            self.lifecycle_done = True
            return 11.0 if self.scenario == "flush_and_total_timeout" else 6.0
        if self.scenario == "validation_total_timeout" and self.flush_done and not self.total_emitted:
            self.post_flush_clock_calls += 1
            if self.post_flush_clock_calls >= 7:
                self.total_emitted = True
                self.lifecycle_done = True
                return 11.0
        return 0.0
