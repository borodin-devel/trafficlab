"""Public-boundary coverage for the canonical expected-failure matrix."""

import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

import trafficlab.capture as capture
import trafficlab.comparison as comparison
import trafficlab.fitting as fitting
import trafficlab.generation as generation
import trafficlab.genetic.strategy as strategy_module
import trafficlab.preflight as preflight
import trafficlab.study_evidence as study_evidence
from tests.support.fixture_paths import DIAGNOSTIC_FIXTURE_ROOT, PIPELINE_FIXTURE_ROOT
from trafficlab.compatibility import ContentIdentity, identify_bytes
from trafficlab.config import ExperimentConfig, FloatBounds
from trafficlab.config_io import render_effective_config
from trafficlab.docker_cli import CommandResult, ProjectInventory, ServiceState, load_capture_image_lock
from trafficlab.errors import FailureOutcome, TrafficlabError
from trafficlab.fitting import FitDependencies
from trafficlab.genetic.strategy import FitOutcome, StrategyContext, run_strategy
from trafficlab.genetic.types import METHOD_ORDER, Candidate, CandidateId, MethodTrialResult, TrialResult
from trafficlab.models.registry import load_best_model, render_best_model
from trafficlab.pcapng import encode_pcapng
from trafficlab.trace import CaptureMetadata, Direction, TraceEvent, render_capture_metadata

_FIXTURE = DIAGNOSTIC_FIXTURE_ROOT / "failure-outcomes.jsonl"
_ROOT = Path(__file__).parents[2]
_FIT_CHECKPOINT_FIXTURE = PIPELINE_FIXTURE_ROOT / "fit" / "checkpoint.json"
_MODEL_FIXTURE = PIPELINE_FIXTURE_ROOT / "models" / "best_model.json"

type _Scenario = Literal[
    "config_invalid",
    "docker_unavailable",
    "compose_incompatible",
    "target_image_unavailable",
    "capture_image_incompatible",
    "dumpcap_unavailable",
    "dumpcap_incompatible",
    "mount_source_unavailable",
    "mount_target_incompatible",
    "mounted_input_unavailable",
    "mounted_input_incompatible",
    "prerequisite_unavailable",
    "prerequisite_incompatible",
    "target_exit_23",
    "capture_exit_42_active",
    "capture_exit_42_after_target_0",
    "workload_timeout",
    "flush_timeout_after_target_0",
    "validation_total_timeout",
    "user_interrupt",
    "malformed_capture",
    "best_model_missing",
    "reference_changed",
    "foreign_generated",
    "stale_capture_pair",
    "checkpoint_corrupt",
    "checkpoint_schema",
    "best_model_schema",
    "metric_infeasible",
    "packet_limit",
    "best_model_collision",
    "accepted_collision",
    "similarity_durability",
    "cleanup_timeout_after_success",
    "target_23_cleanup_timeout",
    "workload_timeout_target_137",
    "flush_and_total_timeout",
    "target_23_capture_42_total_timeout",
]

_SCENARIOS: tuple[_Scenario, ...] = (
    "config_invalid",
    "docker_unavailable",
    "compose_incompatible",
    "target_image_unavailable",
    "capture_image_incompatible",
    "dumpcap_unavailable",
    "dumpcap_incompatible",
    "mount_source_unavailable",
    "mount_target_incompatible",
    "mounted_input_unavailable",
    "mounted_input_incompatible",
    "prerequisite_unavailable",
    "prerequisite_incompatible",
    "target_exit_23",
    "capture_exit_42_active",
    "capture_exit_42_after_target_0",
    "workload_timeout",
    "flush_timeout_after_target_0",
    "validation_total_timeout",
    "user_interrupt",
    "malformed_capture",
    "best_model_missing",
    "reference_changed",
    "foreign_generated",
    "stale_capture_pair",
    "checkpoint_corrupt",
    "checkpoint_schema",
    "best_model_schema",
    "metric_infeasible",
    "packet_limit",
    "best_model_collision",
    "accepted_collision",
    "similarity_durability",
    "cleanup_timeout_after_success",
    "target_23_cleanup_timeout",
    "workload_timeout_target_137",
    "flush_and_total_timeout",
    "target_23_capture_42_total_timeout",
)


@dataclass(frozen=True, slots=True)
class _BoundaryCase:
    """One primary outcome and all of its ordered fixture-defined secondaries."""

    scenario: _Scenario
    outcomes: tuple[FailureOutcome, ...]

    @property
    def primary(self) -> FailureOutcome:
        return self.outcomes[0]

    @property
    def identifier(self) -> str:
        return f"primitive-boundary-{self.scenario}"


def _fixture_outcomes() -> tuple[FailureOutcome, ...]:
    return tuple(FailureOutcome.from_json(line) for line in _FIXTURE.read_text(encoding="utf-8").splitlines() if line)


def _build_boundary_cases() -> tuple[_BoundaryCase, ...]:
    grouped: list[tuple[FailureOutcome, ...]] = []
    active: list[FailureOutcome] = []
    for outcome in _fixture_outcomes():
        if outcome.authority == "primary":
            if active:
                grouped.append(tuple(active))
            active = [outcome]
        else:
            if not active:
                raise AssertionError("a fixture secondary outcome must follow a primary outcome")
            active.append(outcome)
    if active:
        grouped.append(tuple(active))
    return tuple(
        _BoundaryCase(scenario=scenario, outcomes=outcomes)
        for scenario, outcomes in zip(_SCENARIOS, grouped, strict=True)
    )


_PUBLIC_BOUNDARY_CASES = _build_boundary_cases()


def _prepared(run_directory: Path) -> preflight.PreparedExperiment:
    config = cast(
        ExperimentConfig,
        SimpleNamespace(
            capture=SimpleNamespace(
                image="capture:test",
                total_timeout_seconds=5.0,
                readiness_timeout_seconds=1.0,
                workload_timeout_seconds=1.0,
                flush_timeout_seconds=1.0,
            ),
            target=SimpleNamespace(image="target:test", mounts=()),
            run=SimpleNamespace(directory=run_directory),
        ),
    )
    return preflight.PreparedExperiment(
        source=run_directory.parent / "experiment.toml",
        portable_config=config,
        config=config,
        report=preflight.PreflightReport(
            config=config,
            findings=(),
            environment_identity=preflight.CaptureEnvironmentIdentity(
                host_architecture="linux/amd64",
                target_reference="target:test",
                target_content_id="sha256:" + ("c" * 64),
                capture_reference="capture:test",
                capture_content_id="sha256:" + ("d" * 64),
                capture_tool_version="4.0.17",
            ),
        ),
        run_directory=run_directory,
    )


type _TreeKind = Literal["directory", "file", "symlink"]
type _TreeValue = tuple[_TreeKind, bytes]
type _TreeEntry = tuple[str, _TreeKind, bytes]
type _TreeInventory = tuple[bool, tuple[_TreeEntry, ...]]


def _tree_inventory(root: Path, *, excluded: frozenset[str] = frozenset()) -> _TreeInventory:
    """Snapshot a complete local tree without following links or omitting empty directories."""
    if not root.exists():
        return False, ()
    entries: list[_TreeEntry] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        if path.is_symlink():
            entries.append((relative, "symlink", str(path.readlink()).encode()))
        elif path.is_dir():
            entries.append((relative, "directory", b""))
        else:
            entries.append((relative, "file", path.read_bytes()))
    return True, tuple(entries)


def _scientific_inventory(run_directory: Path) -> _TreeInventory:
    """Retain every run artifact byte while allowing the failure ledger to append."""
    return _tree_inventory(run_directory, excluded=frozenset({"run.log"}))


def _temporary_residue(root: Path) -> tuple[str, ...]:
    if not root.exists():
        return ()
    return tuple(
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
        if path.name.endswith(".tmp") or path.name.startswith(".capture-")
    )


def _assert_adverse_inventory_unchanged(
    run_directory: Path,
    before: _TreeInventory,
    *,
    expected_new: dict[str, _TreeValue] | None = None,
) -> None:
    before_exists, before_entries = before
    expected = {path: (kind, content) for path, kind, content in before_entries}
    additions = {} if expected_new is None else expected_new
    assert expected.keys().isdisjoint(additions)
    expected.update(additions)
    after_exists, after_entries = _scientific_inventory(run_directory)
    assert after_exists == before_exists
    assert {path: (kind, content) for path, kind, content in after_entries} == expected
    assert _temporary_residue(run_directory) == ()


type _LogSnapshot = tuple[bool, bytes]


def _log_snapshot(run_directory: Path) -> _LogSnapshot:
    log_path = run_directory / "run.log"
    return log_path.exists(), log_path.read_bytes() if log_path.exists() else b""


def _reject_duplicate_log_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise AssertionError(f"run.log record contains duplicate key {key!r}")
        document[key] = value
    return document


def _strict_canonical_log_rows(content: bytes) -> tuple[dict[str, object], ...]:
    if not content:
        return ()
    lines = content.splitlines(keepends=True)
    assert b"".join(lines) == content
    records: list[dict[str, object]] = []
    for line in lines:
        assert line.endswith(b"\n") and line != b"\n"
        value = json.loads(line, object_pairs_hook=_reject_duplicate_log_keys)
        assert type(value) is dict
        record = cast(dict[str, object], value)
        canonical = (json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
        assert line == canonical
        records.append(record)
    return tuple(records)


def _canonical_log_bytes(records: tuple[dict[str, object], ...]) -> bytes:
    return b"".join(
        (json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
        for record in records
    )


def _assert_failure_log_suffix(
    run_directory: Path,
    before: _LogSnapshot,
    *,
    expected_records: tuple[dict[str, object], ...],
) -> None:
    before_exists, before_bytes = before
    after_exists, after_bytes = _log_snapshot(run_directory)
    assert after_exists
    assert after_bytes[: len(before_bytes)] == before_bytes
    assert before_exists or before_bytes == b""
    suffix = after_bytes[len(before_bytes) :]
    assert _strict_canonical_log_rows(suffix) == expected_records
    assert suffix == _canonical_log_bytes(expected_records)


def _assert_log_unchanged(run_directory: Path, before: _LogSnapshot) -> None:
    assert _log_snapshot(run_directory) == before


class _CompletedHandle:
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


class _PreflightClock:
    def __init__(self, scenario: _Scenario) -> None:
        self.scenario = scenario
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        if self.scenario == "dumpcap_incompatible" and self.calls > 20:
            return 100.0
        return 0.0


class _PreflightDocker:
    """Primitive command, state, and file effects for full public preflight."""

    def __init__(self, scenario: _Scenario, mount_source: Path | None) -> None:
        self.scenario = scenario
        self.mount_source = mount_source
        self.signaled = False
        lock = load_capture_image_lock(preflight._CAPTURE_IMAGE_LOCK_PATH)  # pyright: ignore[reportPrivateUsage]
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

    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> _CompletedHandle:
        del compose_path, project_name, deadline
        return _CompletedHandle()

    def project_inventory(self, compose_path: Path, project_name: str, *, deadline: float) -> ProjectInventory:
        del compose_path, project_name, deadline
        return ProjectInventory(())


_PREFLIGHT_LOG_FINDINGS: dict[_Scenario, tuple[str, ...]] = {
    "docker_unavailable": ("capture_image_lock", "docker_daemon"),
    "compose_incompatible": (
        "capture_image_lock",
        "docker_daemon",
        "capture_platform",
        "docker_compose",
    ),
    "target_image_unavailable": (
        "capture_image_lock",
        "docker_daemon",
        "capture_platform",
        "docker_compose",
        "target_image",
    ),
    "capture_image_incompatible": (
        "capture_image_lock",
        "docker_daemon",
        "capture_platform",
        "docker_compose",
        "target_image",
        "capture_image",
    ),
    "dumpcap_unavailable": (
        "capture_image_lock",
        "docker_daemon",
        "capture_platform",
        "docker_compose",
        "target_image",
        "capture_image",
        "compose_config",
        "network_probe",
        "probe_cleanup",
    ),
    "dumpcap_incompatible": (
        "capture_image_lock",
        "docker_daemon",
        "capture_platform",
        "docker_compose",
        "target_image",
        "capture_image",
        "compose_config",
        "network_probe",
        "probe_cleanup",
    ),
    "mount_source_unavailable": (
        "capture_image_lock",
        "docker_daemon",
        "capture_platform",
        "docker_compose",
        "target_image",
        "capture_image",
        "mounts",
    ),
    "mount_target_incompatible": (
        "capture_image_lock",
        "docker_daemon",
        "capture_platform",
        "docker_compose",
        "target_image",
        "capture_image",
        "compose_config",
    ),
    "prerequisite_unavailable": (
        "capture_image_lock",
        "docker_daemon",
        "capture_platform",
        "docker_compose",
        "target_image",
        "capture_image",
        "compose_config",
        "network_probe",
        "probe_cleanup",
    ),
    "prerequisite_incompatible": (
        "capture_image_lock",
        "docker_daemon",
        "capture_platform",
        "docker_compose",
        "target_image",
        "capture_image",
        "compose_config",
        "network_probe",
        "probe_cleanup",
    ),
}
_PREFLIGHT_ENVIRONMENT_LOG_SCENARIOS = frozenset(
    {
        "dumpcap_unavailable",
        "dumpcap_incompatible",
        "mount_source_unavailable",
        "mount_target_incompatible",
        "prerequisite_unavailable",
        "prerequisite_incompatible",
    }
)
_PREFLIGHT_SUCCESS_DETAILS = {
    "capture_image_lock": "capture base, Debian snapshot, packages, tool, and expected image ID are locked",
    "docker_daemon": "Docker daemon is reachable",
    "capture_platform": "Docker daemon executes the supported capture platform linux/amd64",
    "docker_compose": "Docker Compose plugin is available",
    "target_image": "target image is locally available",
    "capture_image": "capture image is locally available",
    "compose_config": "production Compose configuration is valid",
    "probe_cleanup": "disposable probe project was removed",
}
_PREFLIGHT_FAILURE_FINDING: dict[_Scenario, str] = {
    "docker_unavailable": "docker_daemon",
    "compose_incompatible": "docker_compose",
    "target_image_unavailable": "target_image",
    "capture_image_incompatible": "capture_image",
    "dumpcap_unavailable": "network_probe",
    "dumpcap_incompatible": "network_probe",
    "mount_source_unavailable": "mounts",
    "mount_target_incompatible": "compose_config",
    "prerequisite_unavailable": "network_probe",
    "prerequisite_incompatible": "network_probe",
}


def _expected_preflight_log_records(
    case: _BoundaryCase,
    config: ExperimentConfig,
    docker: _PreflightDocker,
) -> tuple[dict[str, object], ...]:
    failure_name = _PREFLIGHT_FAILURE_FINDING[case.scenario]
    records: list[dict[str, object]] = []
    for name in _PREFLIGHT_LOG_FINDINGS[case.scenario]:
        if name == failure_name:
            records.append(
                {
                    "detail": case.primary.detail,
                    "event": "preflight_check",
                    "failure_outcome": case.primary.as_dict(),
                    "name": name,
                    "ok": False,
                    "stage": "preflight",
                }
            )
        else:
            records.append(
                {
                    "detail": _PREFLIGHT_SUCCESS_DETAILS[name],
                    "event": "preflight_check",
                    "name": name,
                    "ok": True,
                    "stage": "preflight",
                }
            )
    if case.scenario in _PREFLIGHT_ENVIRONMENT_LOG_SCENARIOS:
        records.append(
            {
                "capture_content_id": docker.capture_id,
                "capture_reference": config.capture.image,
                "capture_tool_version": docker.capture_tool_version,
                "event": "capture_environment_identity",
                "host_architecture": docker.host_architecture,
                "stage": "preflight",
                "target_content_id": docker.target_id,
                "target_reference": config.target.image,
            }
        )
    return tuple(records)


def _run_preflight_case(
    case: _BoundaryCase,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    run_directory = tmp_path / "run"
    experiment_path = tmp_path / "experiment.toml"
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    target = cast(dict[str, object], data["target"])
    target["mounts"] = []
    if case.scenario == "target_image_unavailable":
        target["image"] = "example.invalid/app"
    mount_source: Path | None = None
    if case.scenario in {"mount_source_unavailable", "mount_target_incompatible"}:
        mount_source = tmp_path / "fixture-data"
        mount_source.write_bytes(b"fixture")
        target["mounts"] = [{"source": str(mount_source), "target": "/work/data", "read_only": True}]
    config = ExperimentConfig.model_validate(data)
    content = render_effective_config(config)
    if case.scenario == "config_invalid":
        content = re.sub(rb"argv = \[[\s\S]*?\]", b"argv = []", content, count=1)
    experiment_path.write_bytes(content)
    docker = _PreflightDocker(case.scenario, mount_source)
    clock = _PreflightClock(case.scenario)
    if case.scenario != "config_invalid":
        preflight.run_preflight(
            experiment_path,
            config_only=True,
            docker=cast(preflight.DockerPreflight, docker),
            clock=clock,
        )
    source_before = experiment_path.read_bytes()
    inventory_before = _scientific_inventory(run_directory)
    log_before = _log_snapshot(run_directory)

    with pytest.raises(TrafficlabError) as caught:
        preflight.run_preflight(
            experiment_path,
            config_only=case.scenario == "config_invalid",
            docker=cast(preflight.DockerPreflight, docker),
            clock=clock,
        )

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    if case.scenario == "config_invalid":
        _assert_log_unchanged(run_directory, log_before)
    else:
        _assert_failure_log_suffix(
            run_directory,
            log_before,
            expected_records=_expected_preflight_log_records(case, config, docker),
        )
    assert experiment_path.read_bytes() == source_before
    _assert_adverse_inventory_unchanged(run_directory, inventory_before)


def _render_snapshot(_config: object) -> bytes:
    return b"canonical snapshot"


_CAPTURE_FAILURE_SCENARIOS = frozenset(
    {
        "target_exit_23",
        "capture_exit_42_active",
        "capture_exit_42_after_target_0",
        "workload_timeout",
        "flush_timeout_after_target_0",
        "validation_total_timeout",
        "user_interrupt",
        "malformed_capture",
        "cleanup_timeout_after_success",
        "target_23_cleanup_timeout",
        "workload_timeout_target_137",
        "flush_and_total_timeout",
        "target_23_capture_42_total_timeout",
    }
)
_CAPTURE_DIAGNOSTIC_SCENARIOS = frozenset(
    {
        "target_exit_23",
        "workload_timeout",
        "user_interrupt",
        "target_23_cleanup_timeout",
        "workload_timeout_target_137",
    }
)
_CAPTURE_LOG_SCENARIOS = frozenset({"capture_exit_42_active", "capture_exit_42_after_target_0"})


@dataclass(frozen=True, slots=True)
class _CaptureFailureLog:
    detail: str
    failure_kind: str
    primary_status: int | None = None
    secondary_failures: tuple[tuple[str, str, int | None], ...] = ()


_CAPTURE_FAILURE_LOGS: dict[_Scenario, _CaptureFailureLog] = {
    "target_exit_23": _CaptureFailureLog(
        "target exited naturally with status 23",
        "target_nonzero_exit",
        23,
    ),
    "capture_exit_42_active": _CaptureFailureLog(
        "capture stopped during target workload",
        "capture_stopped",
    ),
    "capture_exit_42_after_target_0": _CaptureFailureLog(
        "capture stopped during target workload",
        "capture_stopped",
        secondary_failures=(("natural_target_status", "target was also observed naturally exited with status 0", 0),),
    ),
    "workload_timeout": _CaptureFailureLog("target workload timed out", "stage_timeout"),
    "flush_timeout_after_target_0": _CaptureFailureLog("capture flush timed out", "stage_timeout"),
    "validation_total_timeout": _CaptureFailureLog(
        "capture validation failed: capture inspection exceeded the total-run deadline",
        "total_timeout",
    ),
    "user_interrupt": _CaptureFailureLog("capture interrupted during target workload", "user_interruption"),
    "malformed_capture": _CaptureFailureLog(
        "capture validation failed: invalid PCAPNG: capture has no Interface Description Block",
        "validation_failed",
    ),
    "cleanup_timeout_after_success": _CaptureFailureLog(
        "cleanup command exceeded its deadline; project resources may remain",
        "cleanup_failed",
    ),
    "target_23_cleanup_timeout": _CaptureFailureLog(
        "target exited naturally with status 23",
        "target_nonzero_exit",
        23,
        (("cleanup_failed", "cleanup command exceeded its deadline; project resources may remain", None),),
    ),
    "workload_timeout_target_137": _CaptureFailureLog(
        "target workload timed out",
        "stage_timeout",
        secondary_failures=(
            (
                "induced_target_status",
                "target exited after Trafficlab requested termination with status 137",
                137,
            ),
        ),
    ),
    "flush_and_total_timeout": _CaptureFailureLog(
        "capture flush timed out",
        "stage_timeout",
        secondary_failures=(
            (
                "total_timeout",
                "capture total-run deadline expired during flush, so capture could not be killed",
                None,
            ),
        ),
    ),
    "target_23_capture_42_total_timeout": _CaptureFailureLog(
        "target exited naturally with status 23",
        "target_nonzero_exit",
        23,
        (
            ("capture_stopped", "capture stopped during target workload", None),
            ("total_timeout", "capture total-run deadline expired", None),
        ),
    ),
}


class _CaptureDocker:
    """Primitive service observations and capture bytes for the real lifecycle."""

    def __init__(self, scenario: _Scenario) -> None:
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
        self.cleanup_handle: _CompletedHandle | None = None
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

    def start_down(self, compose_path: Path, project_name: str, *, deadline: float) -> _CompletedHandle:
        del compose_path, project_name, deadline
        timeout = self.scenario in {"cleanup_timeout_after_success", "target_23_cleanup_timeout"}
        self.cleanup_handle = _CompletedHandle(timeout=timeout)
        return self.cleanup_handle

    def project_inventory(self, compose_path: Path, project_name: str, *, deadline: float) -> ProjectInventory:
        del compose_path, project_name, deadline
        return ProjectInventory(())

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
            self.total_emitted = True
            self.lifecycle_done = True
            return 11.0
        return 0.0


def _expected_capture_log_records(
    case: _BoundaryCase,
    docker: _CaptureDocker,
) -> tuple[dict[str, object], ...]:
    spec = _CAPTURE_FAILURE_LOGS[case.scenario]
    project_name = docker.project_name
    assert project_name is not None
    if spec.primary_status is not None:
        assert docker.target_exit_status == spec.primary_status
    for kind, _detail, status in spec.secondary_failures:
        if kind in {"natural_target_status", "induced_target_status"}:
            assert docker.target_exit_status == status
    if case.primary.status == 42 or any(outcome.status == 42 for outcome in case.outcomes[1:]):
        assert docker.capture_exit_status == 42

    records: list[dict[str, object]] = [
        {"event": "capture_project_created", "project_name": project_name, "stage": "capture"},
        {"event": "capture_ready", "project_name": project_name, "stage": "capture"},
    ]
    if case.scenario in _CAPTURE_LOG_SCENARIOS:
        records.append(
            {
                "detail": "capture diagnostics",
                "event": "capture_logs",
                "project_name": project_name,
                "stage": "capture",
            }
        )
    secondary_failures = [
        {"detail": detail, "kind": kind, "status": status} for kind, detail, status in spec.secondary_failures
    ]
    records.append(
        {
            "detail": spec.detail,
            "event": "capture_failed",
            "failure_kind": spec.failure_kind,
            "failure_outcome": case.primary.as_dict(),
            "primary_status": spec.primary_status,
            "secondary_details": [failure["detail"] for failure in secondary_failures],
            "secondary_failures": secondary_failures,
            "secondary_outcomes": [outcome.as_dict() for outcome in case.outcomes[1:]],
            "stage": "capture",
        }
    )
    return tuple(records)


def _capture_prepared(
    valid_config_data: dict[str, object], tmp_path: Path
) -> tuple[Path, preflight.PreparedExperiment]:
    run_directory = tmp_path / "run"
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    cast(dict[str, object], data["target"])["mounts"] = []
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_bytes(render_effective_config(config))
    prepared = preflight.open_or_prepare_experiment(experiment_path)
    environment = preflight.CaptureEnvironmentIdentity(
        host_architecture="linux/amd64",
        target_reference=prepared.config.target.image,
        target_content_id="sha256:" + ("c" * 64),
        capture_reference=prepared.config.capture.image,
        capture_content_id="sha256:" + ("d" * 64),
        capture_tool_version="4.0.17",
    )
    return run_directory, replace(prepared, report=replace(prepared.report, environment_identity=environment))


def _run_capture_boundary_case(
    case: _BoundaryCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    run_directory, prepared = _capture_prepared(valid_config_data, tmp_path)
    docker = _CaptureDocker(case.scenario)
    inventory_before = _scientific_inventory(run_directory)
    log_before = _log_snapshot(run_directory)

    def fixed_deadline(_clock: Callable[[], float], _seconds: float, *, stage: str) -> float:
        if case.scenario == "target_23_capture_42_total_timeout" and stage == "workload":
            return 20.0
        return 10.0 if stage in {"project creation", "total-run"} else 5.0

    monkeypatch.setattr(capture, "_future_deadline", fixed_deadline)
    with pytest.raises(TrafficlabError) as caught:
        capture.capture_prepared_experiment(
            prepared.source,
            prepared,
            docker=cast(capture.CaptureDocker, docker),
            clock=docker.clock,
            interruption=lambda: case.scenario == "user_interrupt" and docker.target_started,
        )

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    _assert_failure_log_suffix(
        run_directory,
        log_before,
        expected_records=_expected_capture_log_records(case, docker),
    )
    expected_new: dict[str, _TreeValue] = {}
    if case.scenario in _CAPTURE_DIAGNOSTIC_SCENARIOS:
        assert docker.created_metadata is not None
        assert docker.created_reference is not None
        expected_new = {
            "diagnostic-capture.json": ("file", docker.created_metadata),
            "diagnostic-reference.pcapng": ("file", docker.created_reference),
        }
    _assert_adverse_inventory_unchanged(run_directory, inventory_before, expected_new=expected_new)


def _run_capture_stale_boundary_case(
    case: _BoundaryCase,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """Drive valid-pair lineage rejection through the public no-Docker capture boundary."""
    run_directory = tmp_path / "run"
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    cast(dict[str, object], data["target"])["mounts"] = []
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_bytes(render_effective_config(config))
    prepared = preflight.open_or_prepare_experiment(experiment_path)
    environment = preflight.CaptureEnvironmentIdentity(
        host_architecture="linux/amd64",
        target_reference=prepared.config.target.image,
        target_content_id="sha256:" + ("c" * 64),
        capture_reference=prepared.config.capture.image,
        capture_content_id="sha256:" + ("d" * 64),
        capture_tool_version="4.0.17",
    )
    prepared = replace(prepared, report=replace(prepared.report, environment_identity=environment))
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    metadata_path = run_directory / "capture.json"
    reference_path = run_directory / "reference.pcapng"
    metadata_content = render_capture_metadata(metadata)
    original_reference = encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    metadata_path.write_bytes(metadata_content)
    reference_path.write_bytes(original_reference)
    capture._append_event(  # pyright: ignore[reportPrivateUsage]
        run_directory,
        "capture_published",
        **capture._capture_lineage(run_directory, environment),  # pyright: ignore[reportPrivateUsage]
        packet_count=1,
        path=str(reference_path),
        project_name="matrix",
        reused=False,
    )
    log_before = _log_snapshot(run_directory)
    changed_reference = encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 65),), metadata)
    reference_path.write_bytes(changed_reference)
    inventory_before = _scientific_inventory(run_directory)

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"stale public reuse touched Docker operation {name}")

    with pytest.raises(TrafficlabError) as caught:
        capture.capture_experiment(
            experiment_path,
            docker=cast(capture.CaptureDocker, NoDocker()),
            clock=lambda: 100.0,
            interruption=lambda: False,
        )

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    assert metadata_path.read_bytes() == metadata_content
    assert reference_path.read_bytes() == changed_reference
    _assert_log_unchanged(run_directory, log_before)
    _assert_adverse_inventory_unchanged(run_directory, inventory_before)


def _run_capture_mounted_input_boundary_case(
    case: _BoundaryCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """Drive mounted-input failure through public pair reuse without Docker."""
    run_directory = tmp_path / "run"
    mounted = tmp_path / "request.txt"
    mounted.write_bytes(b"request-v1")
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    cast(dict[str, object], data["target"])["mounts"] = [
        {"source": str(mounted), "target": "/work/request.txt", "read_only": True},
    ]
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_bytes(render_effective_config(config))
    prepared = preflight.open_or_prepare_experiment(experiment_path)
    mounted_inputs = capture._identify_mounted_inputs(prepared.config)  # pyright: ignore[reportPrivateUsage]
    environment = preflight.CaptureEnvironmentIdentity(
        host_architecture="linux/amd64",
        target_reference=prepared.config.target.image,
        target_content_id="sha256:" + ("c" * 64),
        capture_reference=prepared.config.capture.image,
        capture_content_id="sha256:" + ("d" * 64),
        capture_tool_version="4.0.17",
        mounted_inputs=mounted_inputs,
    )
    metadata = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
    metadata_path = run_directory / "capture.json"
    reference_path = run_directory / "reference.pcapng"
    metadata_content = render_capture_metadata(metadata)
    reference_content = encode_pcapng((TraceEvent(4.0, Direction.OUTBOUND, 64),), metadata)
    metadata_path.write_bytes(metadata_content)
    reference_path.write_bytes(reference_content)
    capture._append_event(  # pyright: ignore[reportPrivateUsage]
        run_directory,
        "capture_published",
        **capture._capture_lineage(run_directory, environment),  # pyright: ignore[reportPrivateUsage]
        packet_count=1,
        path=str(reference_path),
        project_name="matrix",
        reused=False,
    )
    log_before = _log_snapshot(run_directory)
    inventory_before = _scientific_inventory(run_directory)
    mutation = "remove" if case.scenario == "mounted_input_unavailable" else "change"
    real_run_preflight = capture.run_preflight
    mutated = False

    def mutate_after_local_preflight(
        path: Path,
        *,
        config_only: bool,
        docker: preflight.DockerPreflight | None,
        clock: Callable[[], float],
    ) -> preflight.PreparedExperiment:
        nonlocal mutated
        result = real_run_preflight(
            path,
            config_only=config_only,
            docker=docker,
            clock=clock,
        )
        if config_only and not mutated:
            if mutation == "remove":
                mounted.unlink()
            else:
                mounted.write_bytes(b"request-v2")
            mutated = True
        return result

    monkeypatch.setattr(capture, "run_preflight", mutate_after_local_preflight)

    docker_calls: list[str] = []

    class NoDocker:
        def __getattr__(self, name: str) -> object:
            docker_calls.append(name)
            raise AssertionError(f"mounted-input public reuse touched Docker operation {name}")

    with pytest.raises(TrafficlabError) as caught:
        capture.capture_experiment(
            experiment_path,
            docker=cast(capture.CaptureDocker, NoDocker()),
            clock=lambda: 100.0,
            interruption=lambda: False,
        )

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    assert caught.value.failure_outcome == case.primary
    assert metadata_path.read_bytes() == metadata_content
    assert reference_path.read_bytes() == reference_content
    _assert_log_unchanged(run_directory, log_before)
    assert docker_calls == []
    assert {path.name for path in run_directory.iterdir()} == {
        "capture.json",
        "experiment.toml",
        "reference.pcapng",
        "run.log",
    }
    _assert_adverse_inventory_unchanged(run_directory, inventory_before)


_FIT_METADATA = CaptureMetadata(interface="eth0", target_mac="02:42:ac:11:00:02")
_FIT_REFERENCE = (
    TraceEvent(10.0, Direction.OUTBOUND, 64),
    TraceEvent(11.0, Direction.INBOUND, 128),
    TraceEvent(12.0, Direction.OUTBOUND, 256),
)


def _fit_config(valid_config_data: dict[str, object], run_directory: Path) -> ExperimentConfig:
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    models = cast(dict[str, object], data["models"])
    models["enabled"] = ["poisson_empirical"]
    models["markov_renewal"] = None
    models["mmpp"] = None
    genetic = cast(dict[str, object], data["genetic"])
    genetic.update(
        population_size=2,
        generation_count=0,
        tournament_size=2,
        elite_count=1,
        trial_seeds=[101],
        resume=True,
    )
    base = ExperimentConfig.model_validate(data)
    poisson = base.models.poisson_empirical
    assert poisson is not None
    return base.model_copy(
        update={
            "models": base.models.model_copy(
                update={
                    "poisson_empirical": poisson.model_copy(update={"c_lambda": FloatBounds(lower=20.0, upper=21.0)})
                }
            )
        }
    )


def _fit_trial(seed: int) -> TrialResult:
    methods = tuple(MethodTrialResult(name, 0.75, {"literal": 0.75}) for name in METHOD_ORDER)
    return TrialResult(
        seed,
        0.75,
        cast(tuple[MethodTrialResult, MethodTrialResult, MethodTrialResult, MethodTrialResult], methods),
    )


def _fit_success_outcome(config: ExperimentConfig) -> FitOutcome:
    winner = Candidate(
        CandidateId(0, 0),
        "poisson_empirical",
        (20.5,),
        "valid",
        0.75,
        (_fit_trial(config.genetic.trial_seeds[0]),),
        None,
        (),
    )
    from trafficlab.genetic.population import derive_family_priority

    return FitOutcome(
        winner,
        (_fit_trial(config.run.final_seed),),
        0,
        "hard_limit",
        derive_family_priority(config.run.master_seed, config.models.enabled),
    )


def _fit_inputs(config: ExperimentConfig) -> dict[Path, bytes]:
    run_directory = config.run.directory
    return {
        run_directory / "experiment.toml": render_effective_config(config),
        run_directory / "capture.json": render_capture_metadata(_FIT_METADATA),
        run_directory / "reference.pcapng": encode_pcapng(_FIT_REFERENCE, _FIT_METADATA),
    }


def _fit_dependencies(
    config: ExperimentConfig,
    experiment_path: Path,
    inputs: dict[Path, bytes],
    strategy: Callable[[StrategyContext], FitOutcome],
) -> FitDependencies:
    prepared = preflight.PreparedExperiment(
        experiment_path,
        config,
        preflight.PreflightReport(config, ()),
        config.run.directory,
    )
    return FitDependencies(lambda _path: prepared, lambda path: inputs[path], strategy)


def _expected_fit_log_records(
    case: _BoundaryCase,
    experiment_path: Path,
    config: ExperimentConfig,
) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = [
        {"event": "fit_started", "experiment_path": str(experiment_path), "stage": "fit"}
    ]
    if case.scenario in {"best_model_collision", "reference_changed"}:
        records.extend(
            (
                {
                    "event": "checkpoint_ready",
                    "generation": 0,
                    "path": str(config.run.directory / "checkpoint.json"),
                    "stage": "fit",
                    "terminal_reason": "hard_limit",
                },
                {
                    "event": "final_validation_succeeded",
                    "family": "poisson_empirical",
                    "fitness": 0.75,
                    "seed": config.run.final_seed,
                    "stage": "fit",
                    "trial_count": 1,
                },
            )
        )
    if case.scenario == "checkpoint_corrupt":
        detail = "invalid checkpoint: Expecting property name enclosed in double quotes: line 2 column 1 (char 2)"
        corrective_action = "preserve the checkpoint and resume from a compatible complete generation"
    else:
        detail = case.primary.detail
        corrective_action = case.primary.corrective_action
    failure_record: dict[str, object] = {
        "corrective_action": corrective_action,
        "detail": detail,
        "event": "stage_failed",
        "failure_outcome": case.primary.as_dict(),
        "stage": "fit",
    }
    if case.outcomes[1:]:
        failure_record["secondary_outcomes"] = [outcome.as_dict() for outcome in case.outcomes[1:]]
    records.append(failure_record)
    return tuple(records)


def _run_fit_boundary_case(
    case: _BoundaryCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """Exercise real fit ownership from checkpoint and publisher source conditions."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    experiment_path = tmp_path / "experiment.toml"
    config = _fit_config(valid_config_data, run_directory)
    inputs = _fit_inputs(config)

    if case.scenario == "checkpoint_corrupt":
        checkpoint_path = run_directory / "checkpoint.json"
        checkpoint_path.write_bytes(b"{\n")

        def forbid_search_draws(*_args: object, **_kwargs: object) -> object:
            pytest.fail("malformed checkpoint bytes reached genetic search draws")

        monkeypatch.setattr(strategy_module, "initial_population", forbid_search_draws)
        dependencies = _fit_dependencies(config, experiment_path, inputs, run_strategy)
    elif case.scenario == "checkpoint_schema":
        checkpoint_path = run_directory / "checkpoint.json"
        document = cast(dict[str, object], json.loads(_FIT_CHECKPOINT_FIXTURE.read_bytes()))
        document["scientific_artifact_schema"] = 1
        incompatible = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
        checkpoint_path.write_bytes(incompatible)

        def forbid_search_draws(*_args: object, **_kwargs: object) -> object:
            pytest.fail("incompatible checkpoint schema reached genetic search draws")

        monkeypatch.setattr(strategy_module, "initial_population", forbid_search_draws)
        dependencies = _fit_dependencies(config, experiment_path, inputs, run_strategy)
    elif case.scenario == "best_model_collision":
        best_model_path = run_directory / "best_model.json"
        existing = load_best_model(_MODEL_FIXTURE.read_bytes(), source=_MODEL_FIXTURE)
        existing_best_model = render_best_model(replace(existing, final_seed=existing.final_seed + 1))
        best_model_path.write_bytes(existing_best_model)

        dependencies = _fit_dependencies(
            config,
            experiment_path,
            inputs,
            lambda _context: _fit_success_outcome(config),
        )
    elif case.scenario == "reference_changed":
        reference_path = run_directory / "reference.pcapng"
        original_reference = inputs[reference_path]
        changed_reference = original_reference + b"changed after fitting\n"
        reference_path.write_bytes(original_reference)
        reads = 0

        def read_bytes(path: Path) -> bytes:
            nonlocal reads
            if path == reference_path:
                reads += 1
                return original_reference if reads == 1 else changed_reference
            return inputs[path]

        prepared = preflight.PreparedExperiment(
            experiment_path,
            config,
            preflight.PreflightReport(config, ()),
            run_directory,
        )
        dependencies = FitDependencies(
            lambda _path: prepared,
            read_bytes,
            lambda _context: _fit_success_outcome(config),
        )
    else:
        raise AssertionError(f"unsupported primitive fit scenario {case.scenario!r}")
    inventory_before = _scientific_inventory(run_directory)
    log_before = _log_snapshot(run_directory)

    with pytest.raises(TrafficlabError) as caught:
        fitting.fit_experiment(experiment_path, dependencies=dependencies)

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    _assert_failure_log_suffix(
        run_directory,
        log_before,
        expected_records=_expected_fit_log_records(case, experiment_path, config),
    )
    _assert_adverse_inventory_unchanged(run_directory, inventory_before)


def test_fit_changed_reference_without_resume_uses_the_generic_recovery_action(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """The resume-specific canonical action does not leak into a fresh non-resume fit."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    experiment_path = tmp_path / "experiment.toml"
    base = _fit_config(valid_config_data, run_directory)
    config = base.model_copy(update={"genetic": base.genetic.model_copy(update={"resume": False})})
    inputs = _fit_inputs(config)
    reference_path = run_directory / "reference.pcapng"
    original_reference = inputs[reference_path]
    reference_path.write_bytes(original_reference)
    reads = 0

    def read_bytes(path: Path) -> bytes:
        nonlocal reads
        if path == reference_path:
            reads += 1
            return original_reference if reads == 1 else original_reference + b"changed after fitting\n"
        return inputs[path]

    prepared = preflight.PreparedExperiment(
        experiment_path,
        config,
        preflight.PreflightReport(config, ()),
        run_directory,
    )
    dependencies = FitDependencies(
        lambda _path: prepared,
        read_bytes,
        lambda _context: _fit_success_outcome(config),
    )

    with pytest.raises(TrafficlabError) as caught:
        fitting.fit_experiment(experiment_path, dependencies=dependencies)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "kind": "artifact_changed",
        "stage": "fit",
        "detail": "reference.pcapng changed during fit",
        "corrective_action": "restore the exact fitted inputs and rerun fit",
        "affected_evidence": "reference.pcapng",
        "evidence_state": "preserved",
        "authority": "primary",
    }
    assert reference_path.read_bytes() == original_reference


def _expected_generation_log_records(case: _BoundaryCase) -> tuple[dict[str, object], ...]:
    record: dict[str, object] = {
        "corrective_action": case.primary.corrective_action,
        "detail": case.primary.detail,
        "event": "stage_failed",
        "failure_outcome": case.primary.as_dict(),
        "stage": "generate",
    }
    if case.outcomes[1:]:
        record["secondary_outcomes"] = [outcome.as_dict() for outcome in case.outcomes[1:]]
    return (record,)


def _run_generation_boundary_case(
    case: _BoundaryCase,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """Exercise missing, incompatible, and limited generation through real files and models."""
    run_directory = tmp_path / "run"
    experiment_path = tmp_path / "experiment.toml"
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    cast(dict[str, object], data["target"])["mounts"] = []
    config = ExperimentConfig.model_validate(data)
    best_content = _MODEL_FIXTURE.read_bytes()
    if case.scenario == "packet_limit":
        best = load_best_model(best_content, source=_MODEL_FIXTURE)
        limits = best.final_limits.model_copy(update={"max_packets": 1})
        best_content = render_best_model(replace(best, final_limits=limits))
        trial_limits = config.generation.trial.model_copy(update={"max_packets": 1})
        config = config.model_copy(
            update={"generation": config.generation.model_copy(update={"trial": trial_limits, "final": limits})}
        )
    experiment_path.write_bytes(render_effective_config(config))
    prepared = preflight.open_or_prepare_experiment(experiment_path)
    capture_content = (_ROOT / "fixtures" / "examples" / "pipeline" / "capture.json").read_bytes()
    (run_directory / "capture.json").write_bytes(capture_content)
    if case.scenario == "best_model_schema":
        document = cast(dict[str, object], json.loads(best_content))
        document["scientific_artifact_schema"] = 1
        best_content = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if case.scenario != "best_model_missing":
        model_path = run_directory / "best_model.json"
        model_path.write_bytes(best_content)
    inventory_before = _scientific_inventory(run_directory)
    log_before = _log_snapshot(run_directory)

    with pytest.raises(TrafficlabError) as caught:
        generation.generate_experiment(prepared.source, clock=lambda: 0.0)

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    _assert_failure_log_suffix(
        run_directory,
        log_before,
        expected_records=_expected_generation_log_records(case),
    )
    _assert_adverse_inventory_unchanged(run_directory, inventory_before)


def test_generation_maps_missing_capture_after_a_validated_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The public generator retains the missing-capture primary outcome before generation."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    prepared = _prepared(run_directory)
    config = cast(Any, prepared.config)
    config.run.final_seed = 1
    config.models = SimpleNamespace(enabled=("poisson_empirical",), poisson_empirical=SimpleNamespace())
    final_limits = SimpleNamespace()
    config.generation = SimpleNamespace(final=final_limits)
    records: list[dict[str, object]] = []
    best = SimpleNamespace(
        family="poisson_empirical",
        gene_bounds={},
        capture_identity=ContentIdentity(size=0, sha256="0" * 64),
        final_seed=1,
        final_limits=final_limits,
        observation_window_seconds=1.0,
        fitted=object(),
    )

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    def read(path: Path, **_kwargs: object) -> bytes:
        if path.name == "best_model.json":
            return b"best model"
        raise TrafficlabError(
            "capture.json is missing",
            corrective_action="restore capture.json before generation",
        )

    class _Family:
        gene_names: tuple[str, ...] = ()

    def open_prepared(_path: Path) -> preflight.PreparedExperiment:
        return prepared

    def load_best(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return best

    def get_family(_name: str) -> _Family:
        return _Family()

    monkeypatch.setattr(generation, "open_or_prepare_experiment", open_prepared)
    monkeypatch.setattr(generation, "append_run_log", append)
    monkeypatch.setattr(generation, "_read_required_bytes", read)
    monkeypatch.setattr(generation, "load_best_model", load_best)
    monkeypatch.setattr(generation, "get_family", get_family)

    with pytest.raises(TrafficlabError) as caught:
        generation.generate_experiment(prepared.source)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "kind": "artifact_missing",
        "stage": "generate",
        "detail": "capture.json is missing",
        "corrective_action": "restore capture.json before generation",
        "affected_evidence": "capture.json",
        "evidence_state": "not_published",
        "authority": "primary",
    }
    assert records[-1]["failure_outcome"] == outcome.as_dict()


def test_generation_preserves_published_bytes_when_post_publication_parse_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Post-publication verification failure records preserved generated evidence."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    prepared = _prepared(run_directory)
    config = cast(Any, prepared.config)
    config.run.final_seed = 1
    config.models = SimpleNamespace(enabled=("poisson_empirical",), poisson_empirical=SimpleNamespace())
    final_limits = SimpleNamespace()
    config.generation = SimpleNamespace(final=final_limits)
    records: list[dict[str, object]] = []
    captured = b"capture metadata"
    best = SimpleNamespace(
        family="poisson_empirical",
        gene_bounds={},
        capture_identity=identify_bytes(captured),
        final_seed=1,
        final_limits=final_limits,
        observation_window_seconds=1.0,
        fitted=object(),
    )
    generated_path = run_directory / "generated.pcapng"

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    def read(path: Path, **_kwargs: object) -> bytes:
        return b"best model" if path.name == "best_model.json" else captured

    def publish(*_args: object, **_kwargs: object) -> object:
        generated_path.write_bytes(b"generated bytes")
        return SimpleNamespace(content=b"generated bytes", path=generated_path)

    def parse_failure(*_args: object, **_kwargs: object) -> tuple[()]:
        raise TrafficlabError(
            "generated bytes cannot be parsed",
            corrective_action="repair generated PCAPNG serialization",
        )

    class _Generated:
        @staticmethod
        def require_complete() -> tuple[()]:
            return ()

    class _Family:
        gene_names: tuple[str, ...] = ()

        @staticmethod
        def generate(*_args: object, **_kwargs: object) -> _Generated:
            return _Generated()

    def open_prepared(_path: Path) -> preflight.PreparedExperiment:
        return prepared

    def load_best(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return best

    def get_family(_name: str) -> _Family:
        return _Family()

    def parse_metadata(*_args: object, **_kwargs: object) -> object:
        return object()

    def quantize(*_args: object, **_kwargs: object) -> tuple[()]:
        return ()

    def encode(*_args: object, **_kwargs: object) -> bytes:
        return b"generated bytes"

    monkeypatch.setattr(generation, "open_or_prepare_experiment", open_prepared)
    monkeypatch.setattr(generation, "append_run_log", append)
    monkeypatch.setattr(generation, "_read_required_bytes", read)
    monkeypatch.setattr(generation, "load_best_model", load_best)
    monkeypatch.setattr(generation, "get_family", get_family)
    monkeypatch.setattr(generation, "parse_capture_metadata", parse_metadata)
    monkeypatch.setattr(generation, "quantize_generated_events", quantize)
    monkeypatch.setattr(generation, "encode_pcapng", encode)
    monkeypatch.setattr(generation, "publish_generated_pcapng", publish)
    monkeypatch.setattr(generation, "parse_pcapng_bytes", parse_failure)
    monkeypatch.setattr(generation, "render_effective_config", _render_snapshot)

    def identify_generation_input(path: Path) -> ContentIdentity:
        contents = {
            "experiment.toml": b"canonical snapshot",
            "best_model.json": b"best model",
            "capture.json": captured,
        }
        return identify_bytes(contents[path.name])

    monkeypatch.setattr(generation, "identify_file", identify_generation_input)

    with pytest.raises(TrafficlabError) as caught:
        generation.generate_experiment(prepared.source)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "kind": "artifact_corrupt",
        "stage": "generate",
        "detail": "generated bytes cannot be parsed",
        "corrective_action": "repair generated PCAPNG serialization",
        "affected_evidence": "generated.pcapng",
        "evidence_state": "preserved",
        "authority": "primary",
    }
    assert generated_path.read_bytes() == b"generated bytes"
    assert records[-1]["failure_outcome"] == outcome.as_dict()


def _run_comparison_boundary_case(
    case: _BoundaryCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """Exercise public comparison mapping from evaluation and publication sources."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    experiment_path = tmp_path / "experiment.toml"
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    if case.scenario == "metric_infeasible":
        similarity = cast(dict[str, object], data["similarity"])
        similarity["acf_lags"] = [100]
        similarity["acf_lag_weights"] = [1.0]
    config = ExperimentConfig.model_validate(data)
    snapshot = render_effective_config(config)
    experiment_path.write_bytes(snapshot)
    (run_directory / "experiment.toml").write_bytes(snapshot)
    example_data = _ROOT / "fixtures" / "examples" / "pipeline"
    for source, destination in (
        (example_data / "capture.json", run_directory / "capture.json"),
        (example_data / "reference.pcapng", run_directory / "reference.pcapng"),
        (example_data / "models" / "generated.pcapng", run_directory / "generated.pcapng"),
        (example_data / "models" / "best_model.json", run_directory / "best_model.json"),
    ):
        destination.write_bytes(source.read_bytes())
    records: list[dict[str, object]] = []

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    monkeypatch.setattr(comparison, "append_run_log", append)
    if case.scenario == "foreign_generated":
        generated_path = run_directory / "generated.pcapng"
        foreign_generated = (run_directory / "reference.pcapng").read_bytes()
        generated_path.write_bytes(foreign_generated)
    elif case.scenario == "metric_infeasible":
        pass
    elif case.scenario == "similarity_durability":

        def fail_fsync(_file_descriptor: int) -> None:
            raise OSError("injected similarity fsync failure")

        monkeypatch.setattr(comparison.os, "fsync", fail_fsync)
    else:
        raise AssertionError(f"unsupported primitive comparison scenario {case.scenario!r}")
    inventory_before = _scientific_inventory(run_directory)
    log_before = _log_snapshot(run_directory)

    with pytest.raises(TrafficlabError) as caught:
        comparison.compare_experiment(experiment_path)

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    detail = (
        f"could not publish similarity artifact {run_directory / 'similarity.json'}: injected similarity fsync failure"
        if case.scenario == "similarity_durability"
        else case.primary.detail
    )
    expected_record: dict[str, object] = {
        "detail": detail,
        "event": "comparison_failed",
        "failure_kind": "publication" if case.scenario == "similarity_durability" else "evaluation_or_input",
        "failure_outcome": case.primary.as_dict(),
        "stage": "compare",
    }
    if case.outcomes[1:]:
        expected_record["secondary_outcomes"] = [outcome.as_dict() for outcome in case.outcomes[1:]]
    assert records == [expected_record]
    _assert_log_unchanged(run_directory, log_before)
    _assert_adverse_inventory_unchanged(run_directory, inventory_before)


def _run_study_publication_case(case: _BoundaryCase, tmp_path: Path) -> None:
    """Exercise exclusive accepted-bundle publication from an occupied destination."""
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "manifest.json").write_bytes(b'{"files":[]}\n')
    evidence_root = tmp_path / "evidence"
    destination = evidence_root / "study-1"
    destination.mkdir(parents=True)
    retained = destination / "retained.txt"
    retained.write_bytes(b"accepted evidence\n")
    candidate_before = _tree_inventory(candidate)
    evidence_before = _tree_inventory(evidence_root)
    log_before = _log_snapshot(candidate)

    with pytest.raises(TrafficlabError) as caught:
        study_evidence.publish_accepted_bundle(candidate, evidence_root, "study-1", lambda _path: None)

    assert tuple(caught.value.failure_outcomes) == case.outcomes
    _assert_log_unchanged(candidate, log_before)
    assert _tree_inventory(candidate) == candidate_before
    assert _tree_inventory(evidence_root) == evidence_before
    assert tuple(evidence_root.glob(".study-1.*.tmp")) == ()
    assert _temporary_residue(evidence_root) == ()


@pytest.mark.parametrize(
    "content",
    (
        pytest.param(b'{"event":"first","event":"second"}\n', id="duplicate-key"),
        pytest.param(b'{"stage":"fit","event":"stage_failed"}\n', id="unsorted-keys"),
        pytest.param(b'{"event": "stage_failed"}\n', id="noncanonical-whitespace"),
        pytest.param(b'{"event":"stage_failed"}', id="missing-newline"),
    ),
)
def test_public_matrix_log_oracle_rejects_noncanonical_jsonl(content: bytes) -> None:
    with pytest.raises(AssertionError):
        _strict_canonical_log_rows(content)


@pytest.mark.parametrize("mutation", ("missing", "extra", "wrong"))
def test_public_matrix_log_oracle_rejects_wrong_outer_record(
    mutation: str,
    tmp_path: Path,
) -> None:
    expected: dict[str, object] = {
        "detail": "expected detail",
        "event": "stage_failed",
        "stage": "fit",
    }
    actual = copy.deepcopy(expected)
    if mutation == "missing":
        actual.pop("detail")
    elif mutation == "extra":
        actual["unexpected"] = True
    else:
        actual["detail"] = "wrong detail"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    (run_directory / "run.log").write_bytes(_canonical_log_bytes((actual,)))

    with pytest.raises(AssertionError):
        _assert_failure_log_suffix(
            run_directory,
            (False, b""),
            expected_records=(expected,),
        )


def test_public_boundary_case_registry_covers_each_authoritative_fixture_row_once() -> None:
    """Every checked fixture row belongs to one public-boundary primary/secondary case."""
    fixture_rows = tuple(json.loads(line) for line in _FIXTURE.read_text(encoding="utf-8").splitlines() if line)
    registry_rows = tuple(outcome.as_dict() for case in _PUBLIC_BOUNDARY_CASES for outcome in case.outcomes)

    assert len(fixture_rows) == 43
    assert len(_PUBLIC_BOUNDARY_CASES) == 38
    assert len(set(_SCENARIOS)) == 38
    assert registry_rows == fixture_rows
    assert all(case.identifier.startswith("primitive-boundary-") for case in _PUBLIC_BOUNDARY_CASES)


@pytest.mark.parametrize("case", _PUBLIC_BOUNDARY_CASES, ids=lambda case: case.identifier)
def test_public_boundaries_serialize_the_authoritative_failure_matrix(
    case: _BoundaryCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """A wrong owner, action, authority, state, or publication side effect breaks this matrix."""
    if case.scenario in {"mounted_input_unavailable", "mounted_input_incompatible"}:
        _run_capture_mounted_input_boundary_case(case, monkeypatch, tmp_path, valid_config_data)
    elif case.scenario in {
        "config_invalid",
        "docker_unavailable",
        "compose_incompatible",
        "target_image_unavailable",
        "capture_image_incompatible",
        "dumpcap_unavailable",
        "dumpcap_incompatible",
        "mount_source_unavailable",
        "mount_target_incompatible",
        "prerequisite_unavailable",
        "prerequisite_incompatible",
    }:
        _run_preflight_case(case, tmp_path, valid_config_data)
    elif case.scenario in _CAPTURE_FAILURE_SCENARIOS | {"stale_capture_pair"}:
        if case.scenario == "stale_capture_pair":
            _run_capture_stale_boundary_case(case, tmp_path, valid_config_data)
        else:
            _run_capture_boundary_case(case, monkeypatch, tmp_path, valid_config_data)
    elif case.scenario in {"checkpoint_corrupt", "checkpoint_schema", "best_model_collision", "reference_changed"}:
        _run_fit_boundary_case(case, monkeypatch, tmp_path, valid_config_data)
    elif case.scenario in {"best_model_missing", "best_model_schema", "packet_limit"}:
        _run_generation_boundary_case(case, tmp_path, valid_config_data)
    elif case.scenario in {"foreign_generated", "metric_infeasible", "similarity_durability"}:
        _run_comparison_boundary_case(case, monkeypatch, tmp_path, valid_config_data)
    elif case.scenario == "accepted_collision":
        _run_study_publication_case(case, tmp_path)
    else:
        raise AssertionError(f"unsupported public boundary scenario {case.scenario!r}")
