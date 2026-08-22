"""Reference capture lineage ownership."""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import cast

from trafficlab.artifacts.capture import (
    load_or_recover_capture_pair,
    remove_stable_capture_diagnostics,
)
from trafficlab.capture.docker.image import CapturePlatform, load_capture_image_lock
from trafficlab.capture.failures import append_event
from trafficlab.capture.policy import (
    CaptureOutcome,
    record_mounted_input_incompatible,
    record_mounted_input_unavailable,
    record_snapshot_changed,
    record_validation_failure,
)
from trafficlab.common.compatibility import (
    ContentIdentity,
    identify_bytes,
    identify_directory,
    identify_file,
    require_compatible,
)
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_experiment, render_effective_config
from trafficlab.common.errors import (
    FailureOutcome,
    TrafficlabError,
    failure_outcome_from_error,
)
from trafficlab.preflight.types import (
    CaptureEnvironmentIdentity,
    MountedInputIdentity,
    PreparedExperiment,
)

_CAPTURE_IMAGE_LOCK_PATH = Path(__file__).resolve().parents[3] / "docker" / "capture" / "image-lock.json"

_CAPTURE_ENVIRONMENT_FIELDS = (
    "host_architecture",
    "target_reference",
    "target_content_id",
    "capture_reference",
    "capture_content_id",
    "capture_tool_version",
    "mounted_inputs",
)

_CONTENT_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


class _SnapshotChangedError(TrafficlabError):
    """The realized snapshot changed after capture preparation."""


class _MountedInputUnavailableError(TrafficlabError):
    """A regular-file mounted input disappeared before validation."""


class _MountedInputIncompatibleError(TrafficlabError):
    """A regular-file mounted input no longer has its recorded identity."""


def _capture_environment_document(identity: CaptureEnvironmentIdentity) -> dict[str, object]:
    return {
        "host_architecture": identity.host_architecture,
        "target_reference": identity.target_reference,
        "target_content_id": identity.target_content_id,
        "capture_reference": identity.capture_reference,
        "capture_content_id": identity.capture_content_id,
        "capture_tool_version": identity.capture_tool_version,
        "mounted_inputs": [item.as_dict() for item in identity.mounted_inputs],
    }


def _mounted_input_name(target: str) -> str:
    return PurePosixPath(target).name or target


def _mounted_input_error(target: str, *, unavailable: bool) -> TrafficlabError:
    name = _mounted_input_name(target)
    if unavailable:
        error = _MountedInputUnavailableError(
            f"mounted input {name} is unavailable",
            corrective_action="restore the named mounted input bytes",
        )
    else:
        error = _MountedInputIncompatibleError(
            f"mounted input {name} is incompatible",
            corrective_action="restore the declared mounted-input content identity",
        )
    outcome = failure_outcome_from_error(
        error,
        kind="docker_preflight_failed",
        stage="preflight",
        affected_evidence="capture evidence",
        evidence_state="not_published",
    )
    error.failure_outcomes = (outcome,)
    error.failure_outcome = outcome
    return error


def identify_mounted_inputs(config: ExperimentConfig) -> tuple[MountedInputIdentity, ...]:
    identities: list[MountedInputIdentity] = []
    for mount in config.target.mounts:
        if not mount.read_only:
            continue
        try:
            status = mount.source.stat(follow_symlinks=False)
        except OSError as error:
            raise _mounted_input_error(mount.target, unavailable=True) from error
        if stat.S_ISDIR(status.st_mode):
            identifier = identify_directory
        elif stat.S_ISREG(status.st_mode):
            identifier = identify_file
        else:
            raise _mounted_input_error(mount.target, unavailable=False)
        try:
            identity = identifier(mount.source)
        except TrafficlabError as error:
            try:
                current = mount.source.stat(follow_symlinks=False)
            except OSError:
                unavailable = True
            else:
                unavailable = stat.S_ISREG(current.st_mode) and isinstance(error.__cause__, OSError)
            raise _mounted_input_error(mount.target, unavailable=unavailable) from error
        identities.append(
            MountedInputIdentity(
                target=mount.target,
                read_only=mount.read_only,
                size=identity.size,
                sha256=identity.sha256,
            )
        )
    return tuple(identities)


def _require_matching_mounted_inputs(
    config: ExperimentConfig,
    expected: tuple[MountedInputIdentity, ...],
) -> tuple[MountedInputIdentity, ...]:
    current = identify_mounted_inputs(config)
    if current == expected:
        return current
    mismatch_index = next(
        (index for index, pair in enumerate(zip(expected, current, strict=False)) if pair[0] != pair[1]),
        min(len(expected), len(current)),
    )
    if mismatch_index < len(expected):
        target = expected[mismatch_index].target
    else:
        target = current[mismatch_index].target
    raise _mounted_input_error(target, unavailable=False)


def capture_lineage(
    run_directory: Path,
    environment_identity: CaptureEnvironmentIdentity,
    *,
    experiment_identity: ContentIdentity | None = None,
) -> dict[str, object]:
    if experiment_identity is None:
        experiment_identity = identify_file(run_directory / "experiment.toml")
    return {
        "experiment_identity": experiment_identity.as_dict(),
        "capture_identity": identify_file(run_directory / "capture.json").as_dict(),
        "reference_identity": identify_file(run_directory / "reference.pcapng").as_dict(),
        "capture_environment_identity": _capture_environment_document(environment_identity),
    }


def _require_unchanged_capture_snapshot(run_directory: Path, expected: ContentIdentity) -> None:
    try:
        require_compatible(
            {"experiment.toml": expected},
            {"experiment.toml": identify_file(run_directory / "experiment.toml")},
        )
    except TrafficlabError as error:
        changed = _SnapshotChangedError(
            "experiment.toml changed during capture",
            corrective_action="restore the prepared experiment snapshot and rerun capture",
        )
        outcome = failure_outcome_from_error(
            changed,
            kind="artifact_changed",
            stage="capture",
            affected_evidence="experiment.toml",
            evidence_state="preserved",
        )
        changed.failure_outcomes = (outcome,)
        changed.failure_outcome = outcome
        raise changed from error


def require_unchanged_capture_inputs(
    run_directory: Path,
    experiment_identity: ContentIdentity,
    config: ExperimentConfig,
    mounted_inputs: tuple[MountedInputIdentity, ...],
) -> None:
    _require_unchanged_capture_snapshot(run_directory, experiment_identity)
    _require_matching_mounted_inputs(config, mounted_inputs)


def _capture_pair_stale_error() -> TrafficlabError:
    error = TrafficlabError(
        "capture pair has another identity",
        corrective_action="select its matching run or a new run directory",
    )
    outcome = FailureOutcome(
        kind="artifact_stale",
        stage="capture",
        detail=str(error),
        affected_evidence="capture pair",
        evidence_state="preserved",
        corrective_action=error.corrective_action,
        authority="primary",
    )
    error.failure_outcomes = (outcome,)
    error.failure_outcome = outcome
    return error


def _parse_capture_environment(value: object) -> CaptureEnvironmentIdentity:
    if type(value) is not dict:
        raise TypeError("capture environment identity must be an object")
    document = cast(dict[str, object], value)
    if set(document) != set(_CAPTURE_ENVIRONMENT_FIELDS):
        raise ValueError("capture environment identity fields are not canonical")
    mounted_value = document["mounted_inputs"]
    if type(mounted_value) is not list:
        raise TypeError("mounted_inputs must be an array")
    mounted_inputs = tuple(MountedInputIdentity.from_dict(item) for item in cast(list[object], mounted_value))
    string_fields = _CAPTURE_ENVIRONMENT_FIELDS[:-1]
    if any(type(document[name]) is not str or not cast(str, document[name]).strip() for name in string_fields):
        raise ValueError("capture environment identity strings must be nonempty")
    if document["host_architecture"] != "linux/amd64":
        raise ValueError("capture environment architecture is not canonical")
    for name in ("target_content_id", "capture_content_id"):
        if _CONTENT_ID_PATTERN.fullmatch(cast(str, document[name])) is None:
            raise ValueError(f"{name} is not a canonical content ID")
    return CaptureEnvironmentIdentity(
        host_architecture=cast(CapturePlatform, document["host_architecture"]),
        target_reference=cast(str, document["target_reference"]),
        target_content_id=cast(str, document["target_content_id"]),
        capture_reference=cast(str, document["capture_reference"]),
        capture_content_id=cast(str, document["capture_content_id"]),
        capture_tool_version=cast(str, document["capture_tool_version"]),
        mounted_inputs=mounted_inputs,
    )


def _parse_capture_lineage(
    record: dict[str, object],
) -> tuple[dict[str, object], CaptureEnvironmentIdentity]:
    environment = record.get("capture_environment_identity")
    parsed_environment = _parse_capture_environment(environment)
    parsed: dict[str, object] = {
        "experiment_identity": ContentIdentity.from_dict(
            record.get("experiment_identity"), name="experiment"
        ).as_dict(),
        "capture_identity": ContentIdentity.from_dict(record.get("capture_identity"), name="capture").as_dict(),
        "reference_identity": ContentIdentity.from_dict(record.get("reference_identity"), name="reference").as_dict(),
        "capture_environment_identity": _capture_environment_document(parsed_environment),
    }
    return parsed, parsed_environment


def _require_matching_capture_lineage(
    run_directory: Path,
    config: ExperimentConfig,
    environment_identity: CaptureEnvironmentIdentity | None,
) -> None:
    try:
        log_text = (run_directory / "run.log").read_bytes().decode("utf-8", errors="strict")
        records = [json.loads(line) for line in log_text.splitlines()]
        publications: list[dict[str, object]] = []
        for record in records:
            if type(record) is not dict:
                raise TypeError("run log record must be an object")
            document = cast(dict[str, object], record)
            if document.get("event") == "capture_published":
                publications.append(document)
        if len(publications) != 1:
            raise ValueError("capture publication lineage must occur exactly once")
        actual, recorded_environment = _parse_capture_lineage(publications[0])
        if recorded_environment.target_reference != config.target.image:
            raise ValueError("capture target reference differs from the realized configuration")
        if recorded_environment.capture_reference != config.capture.image:
            raise ValueError("capture image reference differs from the realized configuration")
        current_mounted_inputs = _require_matching_mounted_inputs(config, recorded_environment.mounted_inputs)
        expected_environment = recorded_environment
        if environment_identity is None:
            # Config-only reuse deliberately avoids Docker.  The checked lock is
            # therefore the independent authority that prevents a syntactically
            # valid run log from claiming a different capture image or tool.
            lock = load_capture_image_lock(_CAPTURE_IMAGE_LOCK_PATH)
            if (recorded_environment.capture_content_id, recorded_environment.capture_tool_version) != (
                lock.expected_capture_image_id,
                lock.capture_tool_version,
            ):
                raise ValueError("capture environment does not match the checked image lock")
        else:
            expected_environment = replace(environment_identity, mounted_inputs=current_mounted_inputs)
        expected = capture_lineage(run_directory, expected_environment)
        require_compatible(expected, actual)
    except (_MountedInputUnavailableError, _MountedInputIncompatibleError):
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TrafficlabError, TypeError, ValueError) as error:
        raise _capture_pair_stale_error() from error


def record_capture_input_failure(outcome: CaptureOutcome, error: TrafficlabError) -> CaptureOutcome:
    if isinstance(error, _SnapshotChangedError):
        return record_snapshot_changed(outcome, str(error))
    if isinstance(error, _MountedInputUnavailableError):
        return record_mounted_input_unavailable(outcome, str(error))
    if isinstance(error, _MountedInputIncompatibleError):
        return record_mounted_input_incompatible(outcome, str(error))
    return record_validation_failure(outcome, str(error))


def _validate_prepared_capture(path: Path, prepared: PreparedExperiment) -> Path:
    if type(prepared) is not PreparedExperiment:
        raise TypeError("prepared must be a PreparedExperiment")
    if prepared.source != path:
        raise TrafficlabError(
            f"prepared experiment source {prepared.source} does not match capture source {path}",
            corrective_action="pass the exact PreparedExperiment returned for this experiment path",
        )
    caller_config = load_experiment(path)
    if caller_config != prepared.config:
        raise TrafficlabError(
            "prepared effective configuration does not match the capture experiment",
            corrective_action="rerun full preflight for the exact experiment configuration",
        )
    run_directory = prepared.run_directory
    if run_directory != prepared.config.run.directory:
        raise TrafficlabError(
            "prepared run directory does not match the effective configuration",
            corrective_action="rerun full preflight for the exact experiment configuration",
        )

    snapshot_path = run_directory / "experiment.toml"
    log_path = run_directory / "run.log"
    try:
        if snapshot_path.read_bytes() != render_effective_config(prepared.config):
            raise ValueError("experiment.toml bytes do not match the prepared effective configuration")
        log_text = log_path.read_bytes().decode("utf-8", errors="strict")
        if not log_text.endswith("\n"):
            raise ValueError("run.log is not newline terminated")
        records = [json.loads(line) for line in log_text.splitlines()]
        expected_initial = (
            {
                "event": "effective_config_published",
                "path": str(snapshot_path),
                "stage": "preflight",
            },
            {"event": "run_prepared", "path": str(run_directory), "stage": "preflight"},
        )
        if len(records) < 2 or tuple(records[:2]) != expected_initial:
            raise ValueError("run.log does not contain the required initial records")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TrafficlabError, ValueError) as error:
        raise TrafficlabError(
            f"prepared capture inputs are not reusable: {error}",
            corrective_action="restore the exact prepared experiment snapshot and initial run log",
        ) from error
    return run_directory


def try_reuse_prepared_capture[CaptureResultT](
    path: Path,
    prepared: PreparedExperiment,
    *,
    clock: Callable[[], float],
    result_factory: Callable[[Path, Path, int, int, bool], CaptureResultT],
) -> tuple[CaptureResultT | None, Path, ContentIdentity]:
    run_directory = _validate_prepared_capture(path, prepared)
    experiment_identity = identify_bytes(render_effective_config(prepared.config))
    existing = load_or_recover_capture_pair(run_directory, deadline=None, clock=clock)
    if existing is None:
        return None, run_directory, experiment_identity
    _require_matching_capture_lineage(
        run_directory,
        prepared.config,
        prepared.report.environment_identity,
    )
    remove_stable_capture_diagnostics(run_directory)
    reference_path = run_directory / "reference.pcapng"
    result = result_factory(
        run_directory,
        reference_path,
        existing.packet_count,
        0,
        True,
    )
    append_event(
        run_directory,
        "capture_reused",
        packet_count=existing.packet_count,
        path=str(reference_path),
        reused=True,
    )
    return result, run_directory, experiment_identity
