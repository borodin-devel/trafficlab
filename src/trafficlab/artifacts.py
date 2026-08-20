"""Reliable validation and publication primitives for experiment run artifacts."""

import json
import math
import os
import tempfile
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Literal, cast

import numpy as np
from pydantic import ValidationError

from trafficlab.capture_validation import CaptureInspection, validate_capture_pair
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import render_effective_config
from trafficlab.errors import DeadlineExceededError, FailureOutcome, TrafficlabError, attach_failure_outcome
from trafficlab.models.registry import load_best_model, render_best_model
from trafficlab.scapy_io import read_pcapng_bytes
from trafficlab.trace import CaptureMetadata, TraceEvent, TrafficTrace


def _artifact_error(detail: str) -> TrafficlabError:
    return TrafficlabError(
        detail,
        corrective_action="verify the artifact directory is writable and has available space",
    )


def _unlink_owned_temp(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as error:
        raise _artifact_error(f"could not remove owned temporary artifact {path}: {error}") from error


def _write_fsync_temp_sibling(path: Path, content: bytes) -> Path:
    # A same-directory sibling guarantees the later os.replace stays on one
    # filesystem.  fsync makes the file contents durable before its name can
    # become authoritative.
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        cleanup_error: TrafficlabError | None = None
        if temporary is not None:
            try:
                _unlink_owned_temp(temporary)
            except TrafficlabError as failure:
                cleanup_error = failure
        detail = f"could not write temporary artifact beside {path}: {error}"
        if cleanup_error is not None:
            detail = f"{detail}; cleanup incomplete: {cleanup_error}"
        raise _artifact_error(detail) from error
    assert temporary is not None
    return temporary


def _post_replace_error(path: Path, operation: str, error: OSError) -> TrafficlabError:
    return TrafficlabError(
        f"could not complete containing directory {operation} after atomically replacing artifact {path}: {error}; "
        "destination may be present",
        corrective_action=(
            "validate the destination before retrying; the rename completed, and cleanup remains limited to the "
            "owned same-directory temporary artifact"
        ),
    )


def _fsync_containing_directory(path: Path) -> None:
    # Replacing a file and persisting its directory entry are separate durability
    # steps.  Once replacement succeeds, failures here are reported as
    # post-publication errors and must never trigger deletion of the destination.
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path.parent, flags)
    except OSError as error:
        raise _post_replace_error(path, "open failure", error) from error
    fsync_error: OSError | None = None
    try:
        os.fsync(descriptor)
    except OSError as error:
        fsync_error = error
    try:
        os.close(descriptor)
    except OSError as error:
        if fsync_error is not None:
            combined = OSError(f"{fsync_error}; directory close also failed: {error}")
            raise _post_replace_error(path, "fsync failure", combined) from fsync_error
        raise _post_replace_error(path, "close failure", error) from error
    if fsync_error is not None:
        raise _post_replace_error(path, "fsync failure", fsync_error) from fsync_error


type ArtifactPublicationStage = Literal["preflight", "capture", "fit", "generate", "compare", "publication"]


def fsync_published_artifact(
    path: Path,
    *,
    stage: ArtifactPublicationStage,
    affected_evidence: str,
) -> None:
    """Persist one published directory entry while preserving it on durability failure."""
    try:
        _fsync_containing_directory(path)
    except TrafficlabError as error:
        published = TrafficlabError(
            f"{affected_evidence} was published at {path}, but containing directory durability failed: {error}",
            corrective_action=error.corrective_action,
        )
        raise attach_failure_outcome(
            published,
            kind="publication_failed",
            stage=stage,
            affected_evidence=affected_evidence,
            evidence_state="preserved",
        ) from error


def atomic_replace(path: Path, content: bytes, *, validator: Callable[[bytes], None]) -> None:
    """Fsync, validate, replace, then fsync the containing directory.

    A directory fsync failure is reported after rename: the destination may therefore
    contain the new bytes, while cleanup remains limited to this call's owned temporary.
    """
    if type(content) is not bytes:
        raise TypeError("atomic replacement content must be bytes")
    if not callable(validator):
        raise TypeError("atomic replacement validator must be callable")

    temporary = _write_fsync_temp_sibling(path, content)
    try:
        try:
            persisted = temporary.read_bytes()
        except OSError as error:
            raise _artifact_error(f"could not read temporary artifact beside {path}: {error}") from error
        validator(persisted)
        try:
            os.replace(temporary, path)
        except OSError as error:
            raise _artifact_error(f"could not atomically replace artifact {path}: {error}") from error
        _fsync_containing_directory(path)
    finally:
        _unlink_owned_temp(temporary)


def append_run_log(run_directory: Path, record: Mapping[str, object]) -> None:
    """Append one deterministic, flushed JSON record to an existing run's log."""
    try:
        encoded = (json.dumps(dict(record), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode(
            "utf-8"
        )
    except (TypeError, ValueError) as error:
        raise TrafficlabError(
            f"could not encode run log record: {error}",
            corrective_action="report the invalid run-log diagnostic record",
        ) from error
    log_path = run_directory / "run.log"
    try:
        with log_path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise TrafficlabError(
            f"could not append run log {log_path}: {error}",
            corrective_action="verify the run directory is writable and has available space",
        ) from error


@dataclass(frozen=True, slots=True)
class BestModelPublication:
    """Exact bytes and ownership status for one best-model publication."""

    path: Path
    content: bytes
    created_by_call: bool


def _validate_best_model_content(content: bytes, *, source: Path) -> None:
    model = load_best_model(content, source=source)
    if render_best_model(model) != content:
        raise TrafficlabError(
            f"best model {source} is not canonical",
            corrective_action="render best_model.json with the canonical fitted-model codec",
        )


def _best_model_entry_exists(destination: Path) -> bool:
    try:
        destination.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise TrafficlabError(
            f"could not inspect best model entry {destination}: {error}",
            corrective_action="verify the best_model.json directory entry is inspectable and retry fitting",
        ) from error
    return True


def _read_existing_best_model(destination: Path, expected_content: bytes | None) -> BestModelPublication | None:
    identity = _file_identity(
        destination,
        kind="best model entry",
        corrective_action="verify the exact best_model.json entry is inspectable and retry fitting",
    )
    try:
        existing_content = destination.read_bytes()
    except FileNotFoundError as error:
        if not _best_model_entry_exists(destination):
            return None
        raise TrafficlabError(
            f"existing best model entry {destination} is unreadable: {error}",
            corrective_action="preserve the existing entry or start a new run directory",
        ) from error
    except OSError as error:
        raise TrafficlabError(
            f"could not read best model {destination}: {error}",
            corrective_action="verify best_model.json is readable and retry fitting",
        ) from error
    _validate_best_model_content(existing_content, source=destination)
    if expected_content is None:
        return BestModelPublication(destination, existing_content, False)
    if existing_content != expected_content:
        raise attach_failure_outcome(
            TrafficlabError(
                "best_model.json already exists",
                corrective_action="choose a new run directory",
            ),
            kind="publication_collision",
            stage="fit",
            affected_evidence="best_model.json",
            evidence_state="preserved",
        )
    current_identity = _file_identity(
        destination,
        kind="best model entry",
        corrective_action="verify the exact best_model.json entry is inspectable and retry fitting",
    )
    if current_identity != identity:
        raise TrafficlabError(
            f"best_model entry changed during exact reuse validation: {destination}",
            corrective_action="preserve the replacement and retry fitting in a stable run directory",
        )
    return BestModelPublication(destination, existing_content, False)


def validate_existing_best_model(path: Path) -> None:
    """Reject a noncanonical occupied model before a fit can start strategy work."""
    _read_existing_best_model(path, None)


def _best_model_publication_error(
    error: TrafficlabError | OSError,
    destination: Path,
    cleanup_error: OSError | None,
) -> TrafficlabError:
    if isinstance(error, TrafficlabError):
        detail = str(error)
        action = error.corrective_action
        exit_code = error.exit_code
    else:
        detail = f"could not publish best model {destination}: {error}"
        action = "verify the run directory is writable and has available space"
        exit_code = 2
    if cleanup_error is not None:
        detail = f"{detail}; cleanup incomplete: could not remove owned temporary file: {cleanup_error}"
    outcomes = _publisher_outcomes(
        error,
        stage="fit",
        affected_evidence="best_model.json",
        detail=detail,
        corrective_action=action,
        cleanup_error=cleanup_error,
    )
    return TrafficlabError(detail, corrective_action=action, exit_code=exit_code, failure_outcomes=outcomes)


def _fsync_published_best_model(path: Path) -> None:
    """Classify a post-link durability failure after the destination already exists."""
    try:
        _fsync_containing_directory(path)
    except TrafficlabError as error:
        raise attach_failure_outcome(
            error,
            kind="publication_failed",
            stage="fit",
            affected_evidence="best_model.json",
            evidence_state="preserved",
        ) from error


def _publisher_outcomes(
    error: TrafficlabError | OSError,
    *,
    stage: str,
    affected_evidence: str,
    detail: str,
    corrective_action: str,
    cleanup_error: OSError | None,
) -> tuple[FailureOutcome, ...]:
    """Keep an owning publisher's exact primary state and append owned cleanup evidence."""
    if isinstance(error, TrafficlabError) and error.failure_outcomes:
        outcomes = error.failure_outcomes
    else:
        outcomes = (
            FailureOutcome.model_validate(
                {
                    "kind": "publication_collision" if isinstance(error, FileExistsError) else "publication_failed",
                    "stage": stage,
                    "detail": detail,
                    "affected_evidence": affected_evidence,
                    "evidence_state": "preserved" if isinstance(error, FileExistsError) else "not_published",
                    "corrective_action": corrective_action,
                    "authority": "primary",
                }
            ),
        )
    if cleanup_error is None:
        return outcomes
    return (
        *outcomes,
        FailureOutcome.model_validate(
            {
                "kind": "cleanup_failed",
                "stage": stage,
                "detail": f"owned temporary file cleanup failed: {cleanup_error}",
                "affected_evidence": "inventory",
                "evidence_state": "possibly_remaining",
                "corrective_action": "remove the owned temporary file after preserving diagnostics",
                "authority": "secondary",
            }
        ),
    )


def publish_best_model(path: Path, content: bytes) -> BestModelPublication:
    """Durably validate and exclusively publish or prove reuse of canonical best-model bytes."""
    _validate_best_model_content(content, source=path)
    existing = _read_existing_best_model(path, content)
    if existing is not None:
        return existing

    temporary_path: Path | None = None
    publication = BestModelPublication(path, content, True)
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        persisted_content = temporary_path.read_bytes()
        if persisted_content != content:
            raise TrafficlabError(
                "persisted temporary best model differs from the canonical content",
                corrective_action="verify reliable storage and retry fitting",
            )
        _validate_best_model_content(persisted_content, source=temporary_path)
        try:
            os.link(temporary_path, path)
        except FileExistsError as error:
            winner = _read_existing_best_model(path, content)
            if winner is None:
                raise TrafficlabError(
                    f"best_model publication race winner disappeared: {path}",
                    corrective_action="retry fitting after verifying the run directory is stable",
                ) from error
            publication = winner
            _fsync_published_best_model(path)
        else:
            _fsync_published_best_model(path)
    except BaseException as error:
        cleanup_error: OSError | None = None
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError as cleanup_failure:
                cleanup_error = cleanup_failure
        if isinstance(error, (TrafficlabError, OSError)):
            raise _best_model_publication_error(error, path, cleanup_error) from error
        raise

    assert temporary_path is not None
    try:
        os.unlink(temporary_path)
    except OSError as error:
        state = "published" if publication.created_by_call else "reused"
        detail = f"best model was {state} at {path}, but owned temporary file cleanup failed: {error}"
        action = "preserve the best model and remove the reported temporary file if it is still owned"
        raise TrafficlabError(
            detail,
            corrective_action=action,
            failure_outcomes=(
                FailureOutcome(
                    kind="publication_failed",
                    stage="fit",
                    detail=detail,
                    affected_evidence="best_model.json",
                    evidence_state="preserved",
                    corrective_action=action,
                    authority="primary",
                ),
                FailureOutcome(
                    kind="cleanup_failed",
                    stage="fit",
                    detail=f"owned temporary file cleanup failed: {error}",
                    affected_evidence="inventory",
                    evidence_state="possibly_remaining",
                    corrective_action="remove the owned temporary file after preserving diagnostics",
                    authority="secondary",
                ),
            ),
        ) from error
    return publication


@dataclass(frozen=True, slots=True)
class GeneratedPublication:
    """Exact content and ownership status for one generated capture publication."""

    path: Path
    created_by_call: bool
    content: bytes


def quantize_generated_events(
    events: Sequence[TraceEvent],
    observation_window_seconds: float,
) -> tuple[TraceEvent, ...]:
    """Map generated events to Scapy's emitted microseconds without crossing stored W."""
    return quantize_generated_trace(TrafficTrace.from_events(events), observation_window_seconds).to_events()


def quantize_generated_trace(trace: TrafficTrace, observation_window_seconds: float) -> TrafficTrace:
    """Quantize one generated trace to Scapy's truncating microsecond writer."""
    if (
        type(observation_window_seconds) is not float
        or not math.isfinite(observation_window_seconds)
        or observation_window_seconds <= 0.0
    ):
        raise TrafficlabError(
            "generated publication observation window must be a finite positive float",
            corrective_action="use the stored fitted-model observation window and retry generation",
        )
    scaled_window = observation_window_seconds * 1_000_000
    if not math.isfinite(scaled_window):
        raise TrafficlabError(
            "generated publication observation window exceeds the PCAPNG timestamp range",
            corrective_action="use a shorter observation window and retry generation",
        )
    if type(trace) is not TrafficTrace:
        raise TypeError("complete generated trace must be a TrafficTrace")
    if np.any(trace.timestamps > observation_window_seconds):
        raise TrafficlabError(
            "complete generated events contain a timestamp outside the stored observation window",
            corrective_action="report the traffic-model complete-window defect",
        )
    maximum_tick = math.floor(scaled_window)
    quantized = np.minimum(np.floor(trace.timestamps * 1_000_000), maximum_tick) / 1_000_000
    return TrafficTrace(
        np.asarray(quantized, dtype=np.float64),
        trace.directions,
        trace.frame_lengths,
    )


def _expected_generated_trace(expected: Sequence[TraceEvent] | TrafficTrace) -> TrafficTrace:
    return expected if type(expected) is TrafficTrace else TrafficTrace.from_events(expected)


def _validate_generated_content(
    content: bytes,
    *,
    source: Path,
    metadata: CaptureMetadata,
    expected_events: Sequence[TraceEvent] | TrafficTrace,
    observation_window_seconds: float,
) -> None:
    parsed = read_pcapng_bytes(content, metadata, source=source)
    if np.any(parsed.timestamps > observation_window_seconds):
        raise TrafficlabError(
            f"generated capture {source} contains a timestamp outside the stored observation window",
            corrective_action="preserve the artifact and retry generation in a new run directory",
        )
    if parsed != quantize_generated_trace(_expected_generated_trace(expected_events), observation_window_seconds):
        raise TrafficlabError(
            f"generated capture {source} does not contain the expected generated events",
            corrective_action="preserve the artifact and retry generation in a new run directory",
        )


def _read_existing_generated(
    destination: Path,
    expected_content: bytes,
    *,
    metadata: CaptureMetadata,
    expected_events: Sequence[TraceEvent] | TrafficTrace,
    observation_window_seconds: float,
) -> GeneratedPublication | None:
    identity = _file_identity(
        destination,
        kind="generated capture entry",
        corrective_action="verify the exact generated.pcapng entry is inspectable and retry generation",
    )
    try:
        existing_content = destination.read_bytes()
    except FileNotFoundError:
        current_identity = _file_identity(
            destination,
            kind="generated capture entry",
            corrective_action="verify the exact generated.pcapng entry is inspectable and retry generation",
        )
        if identity is None and current_identity is None:
            return None
        raise TrafficlabError(
            f"generated capture entry changed during exact reuse validation: {destination}",
            corrective_action="preserve the replacement and retry generation in a stable run directory",
        ) from None
    except OSError as error:
        raise TrafficlabError(
            f"could not read generated capture {destination}: {error}",
            corrective_action="verify generated.pcapng is readable and retry generation",
        ) from error
    if existing_content != expected_content:
        raise attach_failure_outcome(
            TrafficlabError(
                f"generated capture already exists: {destination}",
                corrective_action="preserve the existing artifact or start a new run directory",
            ),
            kind="publication_collision",
            stage="generate",
            affected_evidence="generated.pcapng",
            evidence_state="preserved",
        )
    _validate_generated_content(
        existing_content,
        source=destination,
        metadata=metadata,
        expected_events=expected_events,
        observation_window_seconds=observation_window_seconds,
    )
    current_identity = _file_identity(
        destination,
        kind="generated capture entry",
        corrective_action="verify the exact generated.pcapng entry is inspectable and retry generation",
    )
    if current_identity != identity:
        raise TrafficlabError(
            f"generated capture entry changed during exact reuse validation: {destination}",
            corrective_action="preserve the replacement and retry generation in a stable run directory",
        )
    return GeneratedPublication(destination, False, existing_content)


def _generated_publication_error(
    error: TrafficlabError | OSError,
    destination: Path,
    cleanup_error: OSError | None,
) -> TrafficlabError:
    if isinstance(error, TrafficlabError):
        detail = str(error)
        action = error.corrective_action
        exit_code = error.exit_code
    elif isinstance(error, FileExistsError):
        detail = f"generated capture already exists: {destination}"
        action = "preserve the existing artifact or start a new run directory"
        exit_code = 2
    else:
        detail = f"could not publish generated capture {destination}: {error}"
        action = "verify the run directory is writable and has available space"
        exit_code = 2
    if cleanup_error is not None:
        detail = f"{detail}; cleanup incomplete: could not remove owned temporary file: {cleanup_error}"
    outcomes = _publisher_outcomes(
        error,
        stage="generate",
        affected_evidence="generated.pcapng",
        detail=detail,
        corrective_action=action,
        cleanup_error=cleanup_error,
    )
    return TrafficlabError(detail, corrective_action=action, exit_code=exit_code, failure_outcomes=outcomes)


def publish_generated_pcapng(
    run_directory: Path,
    content: bytes,
    *,
    metadata: CaptureMetadata,
    expected_events: Sequence[TraceEvent] | TrafficTrace,
    observation_window_seconds: float,
) -> GeneratedPublication:
    """Durably validate and exclusively publish or prove reuse of generated PCAPNG bytes."""
    quantize_generated_trace(_expected_generated_trace(expected_events), observation_window_seconds)
    destination = run_directory / "generated.pcapng"
    existing = _read_existing_generated(
        destination,
        content,
        metadata=metadata,
        expected_events=expected_events,
        observation_window_seconds=observation_window_seconds,
    )
    if existing is not None:
        return existing

    temporary_path: Path | None = None
    publication = GeneratedPublication(destination, True, content)
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=run_directory,
            prefix=".generated.pcapng.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        persisted_content = temporary_path.read_bytes()
        if persisted_content != content:
            raise TrafficlabError(
                "persisted temporary generated capture differs from the encoded content",
                corrective_action="verify reliable storage and retry generation",
            )
        _validate_generated_content(
            persisted_content,
            source=temporary_path,
            metadata=metadata,
            expected_events=expected_events,
            observation_window_seconds=observation_window_seconds,
        )
        try:
            os.link(temporary_path, destination)
        except FileExistsError as error:
            winner = _read_existing_generated(
                destination,
                content,
                metadata=metadata,
                expected_events=expected_events,
                observation_window_seconds=observation_window_seconds,
            )
            if winner is None:
                raise TrafficlabError(
                    f"generated capture publication race winner disappeared: {destination}",
                    corrective_action="retry generation after verifying the run directory is stable",
                ) from error
            publication = winner
        fsync_published_artifact(
            destination,
            stage="generate",
            affected_evidence="generated.pcapng",
        )
    except BaseException as error:
        cleanup_error: OSError | None = None
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError as cleanup_failure:
                cleanup_error = cleanup_failure
        if isinstance(error, (TrafficlabError, OSError)):
            raise _generated_publication_error(error, destination, cleanup_error) from error
        raise

    assert temporary_path is not None
    try:
        os.unlink(temporary_path)
    except OSError as error:
        state = "published" if publication.created_by_call else "reused"
        detail = f"generated capture was {state} at {destination}, but owned temporary file cleanup failed: {error}"
        action = "preserve the generated capture and remove the reported temporary file if it is still owned"
        raise TrafficlabError(
            detail,
            corrective_action=action,
            failure_outcomes=(
                FailureOutcome(
                    kind="publication_failed",
                    stage="generate",
                    detail=detail,
                    affected_evidence="generated.pcapng",
                    evidence_state="preserved",
                    corrective_action=action,
                    authority="primary",
                ),
                FailureOutcome(
                    kind="cleanup_failed",
                    stage="generate",
                    detail=f"owned temporary file cleanup failed: {error}",
                    affected_evidence="inventory",
                    evidence_state="possibly_remaining",
                    corrective_action="remove the owned temporary file after preserving diagnostics",
                    authority="secondary",
                ),
            ),
        ) from error
    return publication


def _validate_persisted_snapshot(path: Path, expected: ExperimentConfig) -> None:
    try:
        text = path.read_bytes().decode("utf-8")
        reparsed = ExperimentConfig.model_validate(tomllib.loads(text))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise TrafficlabError(
            "persisted effective configuration is invalid",
            corrective_action="report the atomic configuration publication defect",
        ) from error
    if reparsed != expected:
        raise TrafficlabError(
            "persisted effective configuration did not round-trip",
            corrective_action="report the atomic configuration publication defect",
        )


def _clean_failed_publication(run_directory: Path, owned_files: list[Path]) -> str | None:
    cleanup_errors: list[str] = []
    for path in reversed(owned_files):
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            cleanup_errors.append(f"could not remove {path}: {error}")
    try:
        run_directory.rmdir()
    except OSError as error:
        cleanup_errors.append(f"could not remove {run_directory}: {error}")
    if cleanup_errors:
        return "; ".join(cleanup_errors)
    return None


def _publish_run_log(run_directory: Path, owned_files: list[Path]) -> None:
    """Atomically publish deterministic records for a prepared experiment run,
    the artifact boundary established by project configuration and local preflight.
    """
    log_path = run_directory / "run.log"
    records = (
        {
            "event": "effective_config_published",
            "path": str(run_directory / "experiment.toml"),
            "stage": "preflight",
        },
        {"event": "run_prepared", "path": str(run_directory), "stage": "preflight"},
    )
    content = "".join(f"{json.dumps(record, sort_keys=True, separators=(',', ':'))}\n" for record in records).encode(
        "utf-8"
    )
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=run_directory,
        prefix=".run.log.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary_path = Path(stream.name)
        owned_files.append(temporary_path)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())

    os.replace(temporary_path, log_path)
    owned_files.remove(temporary_path)
    owned_files.append(log_path)


def create_run_directory(config: ExperimentConfig) -> Path:
    """Create a run directory and atomically publish its realized configuration."""
    run_directory = config.run.directory
    try:
        run_directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise TrafficlabError(
            f"run directory already exists: {run_directory}",
            corrective_action="choose a new run.directory or deliberately remove the existing run",
        ) from error
    except OSError as error:
        raise TrafficlabError(
            f"could not create run directory {run_directory}: {error}",
            corrective_action="verify the configured run directory and its parent permissions",
        ) from error

    owned_files: list[Path] = []
    try:
        snapshot = render_effective_config(config)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=run_directory,
            prefix=".experiment.toml.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            owned_files.append(temporary_path)
            stream.write(snapshot)
            stream.flush()
            os.fsync(stream.fileno())

        _validate_persisted_snapshot(temporary_path, config)
        snapshot_path = run_directory / "experiment.toml"
        os.replace(temporary_path, snapshot_path)
        owned_files.remove(temporary_path)
        owned_files.append(snapshot_path)
        _publish_run_log(run_directory, owned_files)
    except Exception as error:
        cleanup_error = _clean_failed_publication(run_directory, owned_files)
        detail = f"could not publish effective configuration in {run_directory}: {error}"
        if cleanup_error is not None:
            detail = f"{detail}; cleanup incomplete: {cleanup_error}"
        raise TrafficlabError(
            detail,
            corrective_action="verify the run parent is writable and retry with a new run directory",
        ) from error

    return run_directory


def _copy_capture_temporary(source: Path, run_directory: Path, *, label: str) -> Path:
    temporary_path: Path | None = None
    try:
        with (
            source.open("rb") as input_stream,
            tempfile.NamedTemporaryFile(
                mode="wb",
                dir=run_directory,
                prefix=f".capture-pair.{label}.",
                suffix=".tmp",
                delete=False,
            ) as output_stream,
        ):
            temporary_path = Path(output_stream.name)
            while chunk := input_stream.read(64 * 1024):
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
    except OSError as error:
        cleanup_detail = _unlink_capture_temporary(temporary_path)
        detail = f"could not prepare capture artifact from {source}: {error}"
        if cleanup_detail is not None:
            detail = f"{detail}; cleanup incomplete: {cleanup_detail}"
        raise TrafficlabError(
            detail,
            corrective_action="verify the capture files and run directory are readable and writable",
        ) from error
    return temporary_path


def _unlink_capture_temporary(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        os.unlink(path)
    except OSError as error:
        return f"could not remove owned temporary file {path}: {error}"
    return None


type FileIdentity = tuple[int, int, int, int]
type CapturePairIdentity = tuple[FileIdentity | None, FileIdentity | None]


def _is_file_identity(value: object) -> bool:
    if type(value) is not tuple:
        return False
    components = cast(tuple[object, ...], value)
    return len(components) == 4 and all(type(component) is int and component >= 0 for component in components)


@dataclass(frozen=True, slots=True)
class CapturePublication:
    """Inspection plus exact ownership evidence for one capture publication call."""

    inspection: CaptureInspection
    created_by_call: bool
    owned_identity: CapturePairIdentity | None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.inspection) is not CaptureInspection:
            raise TypeError("inspection must be a CaptureInspection")
        if type(self.created_by_call) is not bool:
            raise TypeError("created_by_call must be a boolean")
        if type(self.warnings) is not tuple:
            raise TypeError("warnings must be a tuple")
        if not all(type(warning) is str and warning.strip() for warning in self.warnings):
            raise ValueError("warnings must contain nonempty strings")
        if self.created_by_call:
            identity = self.owned_identity
            if (
                type(identity) is not tuple
                or len(identity) != 2
                or not all(_is_file_identity(item) for item in identity)
            ):
                raise ValueError("a created publication requires an exact owned_identity pair")
        elif self.owned_identity is not None:
            raise ValueError("a reused publication cannot carry an owned_identity")


def _file_identity(
    path: Path,
    *,
    kind: str = "capture artifact",
    corrective_action: str = "verify the exact capture artifact paths and retry capture",
) -> FileIdentity | None:
    try:
        status = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise TrafficlabError(
            f"could not inspect {kind} {path}: {error}",
            corrective_action=corrective_action,
        ) from error
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)


def _capture_pair_identity(metadata_path: Path, pcapng_path: Path) -> CapturePairIdentity:
    return (_file_identity(metadata_path), _file_identity(pcapng_path))


def _recovery_error(detail: str) -> TrafficlabError:
    return TrafficlabError(
        detail,
        corrective_action="preserve the reported recovery artifacts, validate them, and retry capture if needed",
    )


def _restore_quarantined_artifact(canonical_path: Path, quarantine_path: Path, *, reason: str) -> None:
    try:
        os.link(quarantine_path, canonical_path)
    except FileExistsError as error:
        raise _recovery_error(
            f"{reason}; canonical path {canonical_path} is occupied; moved artifact preserved at {quarantine_path}"
        ) from error
    except OSError as error:
        raise _recovery_error(
            f"{reason}; could not restore {canonical_path}: {error}; moved artifact preserved at {quarantine_path}"
        ) from error

    try:
        os.unlink(quarantine_path)
    except OSError as error:
        raise _recovery_error(
            f"{reason}; restored {canonical_path}, but could not remove recovery link {quarantine_path}: {error}"
        ) from error

    try:
        quarantine_path.parent.rmdir()
    except OSError as error:
        raise _recovery_error(
            f"{reason}; restored {canonical_path}, but could not remove recovery directory "
            f"{quarantine_path.parent}: {error}"
        ) from error
    raise _recovery_error(reason)


def _recover_failed_capture_pair(
    metadata_path: Path,
    pcapng_path: Path,
    expected_identity: CapturePairIdentity,
) -> None:
    paths = (metadata_path, pcapng_path)
    current_identity = _capture_pair_identity(*paths)
    if current_identity != expected_identity:
        raise _recovery_error("capture pair changed during invalid-pair recovery")

    try:
        quarantine_directory = Path(tempfile.mkdtemp(dir=metadata_path.parent, prefix=".capture-recovery."))
    except OSError as error:
        raise TrafficlabError(
            f"could not create capture recovery quarantine in {metadata_path.parent}: {error}",
            corrective_action="verify the run directory is writable and retry capture",
        ) from error

    errors: list[str] = []
    quarantine_retained = False
    for index, (path, expected) in enumerate(zip(paths, expected_identity, strict=True)):
        if expected is None:
            continue
        quarantine_path = quarantine_directory / f"{index}-{path.name}"
        try:
            os.rename(path, quarantine_path)
        except OSError as error:
            errors.append(f"could not move capture artifact {path} into recovery quarantine: {error}")
            break
        moved_identity = _file_identity(quarantine_path)
        if moved_identity != expected:
            _restore_quarantined_artifact(
                path,
                quarantine_path,
                reason="capture pair changed during invalid-pair recovery",
            )
        try:
            os.unlink(quarantine_path)
        except OSError as error:
            errors.append(f"could not remove creator-owned recovery artifact {quarantine_path}: {error}")
            quarantine_retained = True
            break

    if not quarantine_retained:
        try:
            quarantine_directory.rmdir()
        except OSError as error:
            errors.append(f"could not remove creator-owned recovery directory {quarantine_directory}: {error}")
    if errors:
        raise TrafficlabError(
            "; ".join(errors),
            corrective_action="repair the exact failed capture artifact paths and retry capture",
        )


def _existing_capture(
    metadata_path: Path,
    pcapng_path: Path,
    *,
    deadline: float | None,
    clock: Callable[[], float],
) -> CaptureInspection | None:
    identity = _capture_pair_identity(metadata_path, pcapng_path)
    if identity == (None, None):
        return None
    if all(item is not None for item in identity):
        try:
            inspection = validate_capture_pair(metadata_path, pcapng_path, deadline=deadline, clock=clock)
        except DeadlineExceededError:
            raise
        except OSError as error:
            raise TrafficlabError(
                f"could not read capture pair for reuse: {error}",
                corrective_action="verify the exact capture artifact paths are readable and retry capture",
            ) from error
        except TrafficlabError:
            pass
        else:
            if _capture_pair_identity(metadata_path, pcapng_path) != identity:
                raise _recovery_error("capture pair changed during valid-pair validation")
            return inspection
    _recover_failed_capture_pair(metadata_path, pcapng_path, identity)
    return None


def load_or_recover_capture_pair(
    run_directory: Path,
    *,
    deadline: float | None,
    clock: Callable[[], float] = monotonic,
) -> CaptureInspection | None:
    """Reuse a stable valid capture pair or remove only a stable invalid pair."""
    return _existing_capture(
        run_directory / "capture.json",
        run_directory / "reference.pcapng",
        deadline=deadline,
        clock=clock,
    )


def remove_stable_capture_diagnostics(run_directory: Path) -> None:
    """Remove only the unchanged stable diagnostic capture identities."""
    metadata_path = run_directory / "diagnostic-capture.json"
    pcapng_path = run_directory / "diagnostic-reference.pcapng"
    identity = _capture_pair_identity(metadata_path, pcapng_path)
    if identity == (None, None):
        return
    _recover_failed_capture_pair(metadata_path, pcapng_path, identity)


def _capture_publication_error(error: Exception, destination: Path, cleanup_details: list[str]) -> TrafficlabError:
    if isinstance(error, FileExistsError):
        detail = f"capture artifact already exists: {destination}"
        action = "preserve the existing artifact and retry capture in a new run directory"
    elif isinstance(error, TrafficlabError):
        detail = str(error)
        action = error.corrective_action
    else:
        detail = f"could not publish capture artifact {destination}: {error}"
        action = "verify the run directory is writable and has available space"
    if cleanup_details:
        detail = f"{detail}; cleanup incomplete: {'; '.join(cleanup_details)}"
    error_type = DeadlineExceededError if isinstance(error, DeadlineExceededError) else TrafficlabError
    outcomes = error.failure_outcomes if isinstance(error, TrafficlabError) and error.failure_outcomes else None
    return error_type(detail, corrective_action=action, failure_outcomes=outcomes)


def publish_capture_pair(
    source_metadata_path: Path,
    source_pcapng_path: Path,
    run_directory: Path,
    *,
    target_success: bool,
    deadline: float | None,
    clock: Callable[[], float] = monotonic,
) -> CapturePublication:
    """Validate and exclusively publish a reusable or diagnostic capture pair."""
    if type(target_success) is not bool:
        raise TrafficlabError(
            "target_success must be a boolean",
            corrective_action="report whether the target exited successfully",
        )

    final_metadata = run_directory / "capture.json"
    final_pcapng = run_directory / "reference.pcapng"
    if target_success:
        existing = _existing_capture(final_metadata, final_pcapng, deadline=deadline, clock=clock)
        if existing is not None:
            return CapturePublication(inspection=existing, created_by_call=False, owned_identity=None)
        destinations = (final_metadata, final_pcapng)
    else:
        existing_identity = _capture_pair_identity(final_metadata, final_pcapng)
        _recover_failed_capture_pair(final_metadata, final_pcapng, existing_identity)
        destinations = (
            run_directory / "diagnostic-capture.json",
            run_directory / "diagnostic-reference.pcapng",
        )

    temporary_paths: list[Path] = []
    current_destination = destinations[0]
    try:
        temporary_metadata = _copy_capture_temporary(source_metadata_path, run_directory, label="metadata")
        temporary_paths.append(temporary_metadata)
        temporary_pcapng = _copy_capture_temporary(source_pcapng_path, run_directory, label="pcapng")
        temporary_paths.append(temporary_pcapng)
        inspection = validate_capture_pair(
            temporary_metadata,
            temporary_pcapng,
            deadline=deadline,
            clock=clock,
        )
        owned_identity = _capture_pair_identity(*temporary_paths)
        for temporary_path, destination in zip(temporary_paths, destinations, strict=True):
            current_destination = destination
            os.link(temporary_path, destination)
        if _capture_pair_identity(*destinations) != owned_identity:
            raise _recovery_error("capture pair changed during publication")
        fsync_published_artifact(
            destinations[-1],
            stage="capture",
            affected_evidence="capture pair",
        )
    except Exception as error:
        cleanup_details = [
            detail
            for temporary_path in temporary_paths
            if (detail := _unlink_capture_temporary(temporary_path)) is not None
        ]
        raise _capture_publication_error(error, current_destination, cleanup_details) from error

    cleanup_details = [
        detail
        for temporary_path in temporary_paths
        if (detail := _unlink_capture_temporary(temporary_path)) is not None
    ]
    return CapturePublication(
        inspection=inspection,
        created_by_call=target_success,
        owned_identity=owned_identity if target_success else None,
        warnings=tuple(cleanup_details),
    )


def rollback_capture_publication(run_directory: Path, publication: CapturePublication) -> None:
    """Withdraw only the unchanged reusable pair proven to be owned by this publication call."""
    if type(publication) is not CapturePublication:
        raise TypeError("publication must be a CapturePublication")
    if not publication.created_by_call:
        return
    assert publication.owned_identity is not None
    _recover_failed_capture_pair(
        run_directory / "capture.json",
        run_directory / "reference.pcapng",
        publication.owned_identity,
    )
