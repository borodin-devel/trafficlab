import json
from pathlib import Path
from typing import Literal, cast

from tests.support.failure_matrix.cases import BoundaryCase, Scenario
from tests.support.failure_matrix.doubles import CaptureDocker, CaptureFailureLog, PreflightDocker
from trafficlab.common.config import ExperimentConfig

type TreeKind = Literal["directory", "file", "symlink"]

type TreeValue = tuple[TreeKind, bytes]

type TreeEntry = tuple[str, TreeKind, bytes]

type TreeInventory = tuple[bool, tuple[TreeEntry, ...]]


def tree_inventory(root: Path, *, excluded: frozenset[str] = frozenset()) -> TreeInventory:
    """Snapshot a complete local tree without following links or omitting empty directories."""
    if not root.exists():
        return False, ()
    entries: list[TreeEntry] = []
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


def scientific_inventory(run_directory: Path) -> TreeInventory:
    """Retain every run artifact byte while allowing the failure ledger to append."""
    return tree_inventory(run_directory, excluded=frozenset({"run.log"}))


def temporary_residue(root: Path) -> tuple[str, ...]:
    if not root.exists():
        return ()
    return tuple(
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
        if path.name.endswith(".tmp") or path.name.startswith(".capture-")
    )


def assert_adverse_inventory_unchanged(
    run_directory: Path,
    before: TreeInventory,
    *,
    expected_new: dict[str, TreeValue] | None = None,
) -> None:
    before_exists, before_entries = before
    expected = {path: (kind, content) for path, kind, content in before_entries}
    additions = {} if expected_new is None else expected_new
    assert expected.keys().isdisjoint(additions)
    expected.update(additions)
    after_exists, after_entries = scientific_inventory(run_directory)
    assert after_exists == before_exists
    assert {path: (kind, content) for path, kind, content in after_entries} == expected
    assert temporary_residue(run_directory) == ()


type LogSnapshot = tuple[bool, bytes]


def log_snapshot(run_directory: Path) -> LogSnapshot:
    log_path = run_directory / "run.log"
    return log_path.exists(), log_path.read_bytes() if log_path.exists() else b""


def reject_duplicate_log_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise AssertionError(f"run.log record contains duplicate key {key!r}")
        document[key] = value
    return document


def strict_canonical_log_rows(content: bytes) -> tuple[dict[str, object], ...]:
    if not content:
        return ()
    lines = content.splitlines(keepends=True)
    assert b"".join(lines) == content
    records: list[dict[str, object]] = []
    for line in lines:
        assert line.endswith(b"\n") and line != b"\n"
        value = json.loads(line, object_pairs_hook=reject_duplicate_log_keys)
        assert type(value) is dict
        record = cast(dict[str, object], value)
        canonical = (json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
        assert line == canonical
        records.append(record)
    return tuple(records)


def canonical_log_bytes(records: tuple[dict[str, object], ...]) -> bytes:
    return b"".join(
        (json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
        for record in records
    )


def assert_failure_log_suffix(
    run_directory: Path,
    before: LogSnapshot,
    *,
    expected_records: tuple[dict[str, object], ...],
) -> None:
    before_exists, before_bytes = before
    after_exists, after_bytes = log_snapshot(run_directory)
    assert after_exists
    assert after_bytes[: len(before_bytes)] == before_bytes
    assert before_exists or before_bytes == b""
    suffix = after_bytes[len(before_bytes) :]
    assert strict_canonical_log_rows(suffix) == expected_records
    assert suffix == canonical_log_bytes(expected_records)


def assert_log_unchanged(run_directory: Path, before: LogSnapshot) -> None:
    assert log_snapshot(run_directory) == before


PREFLIGHT_LOG_FINDINGS: dict[Scenario, tuple[str, ...]] = {
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

PREFLIGHT_ENVIRONMENT_LOG_SCENARIOS = frozenset(
    {
        "dumpcap_unavailable",
        "dumpcap_incompatible",
        "mount_source_unavailable",
        "mount_target_incompatible",
        "prerequisite_unavailable",
        "prerequisite_incompatible",
    }
)

PREFLIGHT_SUCCESS_DETAILS = {
    "capture_image_lock": "capture base, Debian snapshot, packages, tool, and expected image ID are locked",
    "docker_daemon": "Docker daemon is reachable",
    "capture_platform": "Docker daemon executes the supported capture platform linux/amd64",
    "docker_compose": "Docker Compose plugin is available",
    "target_image": "target image is locally available",
    "capture_image": "capture image is locally available",
    "compose_config": "production Compose configuration is valid",
    "probe_cleanup": "disposable probe project was removed",
}

PREFLIGHT_FAILURE_FINDING: dict[Scenario, str] = {
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


def expected_preflight_log_records(
    case: BoundaryCase,
    config: ExperimentConfig,
    docker: PreflightDocker,
) -> tuple[dict[str, object], ...]:
    failure_name = PREFLIGHT_FAILURE_FINDING[case.scenario]
    records: list[dict[str, object]] = []
    for name in PREFLIGHT_LOG_FINDINGS[case.scenario]:
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
                    "detail": PREFLIGHT_SUCCESS_DETAILS[name],
                    "event": "preflight_check",
                    "name": name,
                    "ok": True,
                    "stage": "preflight",
                }
            )
    if case.scenario in PREFLIGHT_ENVIRONMENT_LOG_SCENARIOS:
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


CAPTURE_DIAGNOSTIC_SCENARIOS = frozenset(
    {
        "target_exit_23",
        "workload_timeout",
        "user_interrupt",
        "target_23_cleanup_timeout",
        "workload_timeout_target_137",
    }
)

CAPTURE_LOG_SCENARIOS = frozenset({"capture_exit_42_active", "capture_exit_42_after_target_0"})

CAPTURE_FAILURE_LOGS: dict[Scenario, CaptureFailureLog] = {
    "target_exit_23": CaptureFailureLog(
        "target exited naturally with status 23",
        "target_nonzero_exit",
        23,
    ),
    "capture_exit_42_active": CaptureFailureLog(
        "capture stopped during target workload",
        "capture_stopped",
    ),
    "capture_exit_42_after_target_0": CaptureFailureLog(
        "capture stopped during target workload",
        "capture_stopped",
        secondary_failures=(("natural_target_status", "target was also observed naturally exited with status 0", 0),),
    ),
    "workload_timeout": CaptureFailureLog("target workload timed out", "stage_timeout"),
    "flush_timeout_after_target_0": CaptureFailureLog("capture flush timed out", "stage_timeout"),
    "validation_total_timeout": CaptureFailureLog(
        "capture validation failed: capture inspection exceeded the total-run deadline",
        "total_timeout",
    ),
    "user_interrupt": CaptureFailureLog("capture interrupted during target workload", "user_interruption"),
    "malformed_capture": CaptureFailureLog(
        "capture validation failed: invalid PCAPNG: Scapy observed 0 interfaces; expected exactly one",
        "validation_failed",
    ),
    "cleanup_timeout_after_success": CaptureFailureLog(
        "cleanup command exceeded its deadline; project resources may remain",
        "cleanup_failed",
    ),
    "target_23_cleanup_timeout": CaptureFailureLog(
        "target exited naturally with status 23",
        "target_nonzero_exit",
        23,
        (("cleanup_failed", "cleanup command exceeded its deadline; project resources may remain", None),),
    ),
    "workload_timeout_target_137": CaptureFailureLog(
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
    "flush_and_total_timeout": CaptureFailureLog(
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
    "target_23_capture_42_total_timeout": CaptureFailureLog(
        "target exited naturally with status 23",
        "target_nonzero_exit",
        23,
        (
            ("capture_stopped", "capture stopped during target workload", None),
            ("total_timeout", "capture total-run deadline expired", None),
        ),
    ),
}


def expected_capture_log_records(
    case: BoundaryCase,
    docker: CaptureDocker,
) -> tuple[dict[str, object], ...]:
    spec = CAPTURE_FAILURE_LOGS[case.scenario]
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
    if case.scenario in CAPTURE_LOG_SCENARIOS:
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


def expected_fit_log_records(
    case: BoundaryCase,
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


def expected_generation_log_records(case: BoundaryCase) -> tuple[dict[str, object], ...]:
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
