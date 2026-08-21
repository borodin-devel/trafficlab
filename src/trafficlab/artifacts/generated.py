"""Generated trace and PCAPNG artifact validation and publication."""

import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from trafficlab.artifacts.io import file_identity, fsync_published_artifact, publisher_outcomes
from trafficlab.common.errors import FailureOutcome, TrafficlabError, attach_failure_outcome
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import CaptureMetadata, TrafficTrace


@dataclass(frozen=True, slots=True)
class GeneratedPublication:
    """Exact content and ownership status for one generated capture publication."""

    path: Path
    created_by_call: bool
    content: bytes


def _validate_expected_generated_trace(trace: TrafficTrace, observation_window_seconds: float) -> None:
    """Validate the exact trace already reparsed from the bytes being published."""
    if type(trace) is not TrafficTrace:
        raise TypeError("expected trace must be a TrafficTrace")
    if (
        type(observation_window_seconds) is not float
        or not math.isfinite(observation_window_seconds)
        or observation_window_seconds <= 0.0
    ):
        raise TrafficlabError(
            "generated publication observation window must be a finite positive float",
            corrective_action="use the stored fitted-model observation window and retry generation",
        )
    if len(trace) > 0 and float(trace.timestamps[-1]) > observation_window_seconds:
        raise TrafficlabError(
            "expected generated trace contains a timestamp outside the stored observation window",
            corrective_action="report the traffic-model complete-window defect",
        )


def _validate_generated_content(
    content: bytes,
    *,
    source: Path,
    metadata: CaptureMetadata,
    expected_trace: TrafficTrace,
    observation_window_seconds: float,
) -> None:
    parsed = read_pcapng_bytes(content, metadata, source=source)
    if len(parsed) > 0 and float(parsed.timestamps[-1]) > observation_window_seconds:
        raise TrafficlabError(
            f"generated capture {source} contains a timestamp outside the stored observation window",
            corrective_action="preserve the artifact and retry generation in a new run directory",
        )
    if parsed != expected_trace:
        raise TrafficlabError(
            f"generated capture {source} does not contain the expected reparsed trace",
            corrective_action="preserve the artifact and retry generation in a new run directory",
        )


def _read_existing_generated(
    destination: Path,
    expected_content: bytes,
    *,
    metadata: CaptureMetadata,
    expected_trace: TrafficTrace,
    observation_window_seconds: float,
) -> GeneratedPublication | None:
    identity = file_identity(
        destination,
        kind="generated capture entry",
        corrective_action="verify the exact generated.pcapng entry is inspectable and retry generation",
    )
    try:
        existing_content = destination.read_bytes()
    except FileNotFoundError:
        current_identity = file_identity(
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
        expected_trace=expected_trace,
        observation_window_seconds=observation_window_seconds,
    )
    current_identity = file_identity(
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
    outcomes = publisher_outcomes(
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
    expected_trace: TrafficTrace,
    observation_window_seconds: float,
) -> GeneratedPublication:
    """Durably validate and exclusively publish or prove reuse of generated PCAPNG bytes."""
    _validate_expected_generated_trace(expected_trace, observation_window_seconds)
    destination = run_directory / "generated.pcapng"
    existing = _read_existing_generated(
        destination,
        content,
        metadata=metadata,
        expected_trace=expected_trace,
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
            expected_trace=expected_trace,
            observation_window_seconds=observation_window_seconds,
        )
        try:
            os.link(temporary_path, destination)
        except FileExistsError as error:
            winner = _read_existing_generated(
                destination,
                content,
                metadata=metadata,
                expected_trace=expected_trace,
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
