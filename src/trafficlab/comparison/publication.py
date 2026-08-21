"""Traffic comparison publication ownership."""

import os
import tempfile
from pathlib import Path

from trafficlab.artifacts.io import fsync_published_artifact
from trafficlab.common.errors import (
    FailureOutcome,
    TrafficlabError,
)
from trafficlab.comparison.codec import (
    canonical_comparison_bytes,
    load_comparison_result,
    parse_comparison_result,
    render_comparison_result,
)
from trafficlab.comparison.schema import ComparisonResult


class PublicationError(TrafficlabError):
    """Internal marker used only to distinguish publication logging detail."""


type _EntryIdentity = tuple[int, int, int, int]


def _entry_identity(destination: Path) -> _EntryIdentity | None:
    try:
        status = destination.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)


def _publication_error(error: Exception, destination: Path, cleanup_error: BaseException | None) -> PublicationError:
    if isinstance(error, FileExistsError) and str(error).startswith("similarity artifact changed"):
        detail = f"{error}: {destination}"
        action = "preserve the replacement and retry comparison in a stable run directory"
    elif isinstance(error, FileExistsError):
        detail = f"similarity artifact already exists: {destination}"
        action = "preserve the existing result or start a new run directory"
    elif isinstance(error, TrafficlabError):
        detail = str(error)
        action = error.corrective_action
    else:
        detail = f"could not publish similarity artifact {destination}: {error}"
        action = "verify the run directory is writable and has available space"
    if cleanup_error is not None:
        detail = f"{detail}; cleanup incomplete: could not remove owned temporary file: {cleanup_error}"
    if isinstance(error, OSError) and not isinstance(error, FileExistsError):
        outcome_detail = "similarity.json durability check failed"
        outcome_action = "correct storage and rerun compare"
    else:
        outcome_detail = detail
        outcome_action = action
    if isinstance(error, TrafficlabError) and error.failure_outcomes:
        outcomes = error.failure_outcomes
    else:
        outcome = FailureOutcome(
            kind="publication_collision" if isinstance(error, FileExistsError) else "publication_failed",
            stage="compare",
            detail=outcome_detail,
            affected_evidence="similarity.json",
            evidence_state="preserved" if isinstance(error, FileExistsError) else "not_published",
            corrective_action=outcome_action,
            authority="primary",
        )
        outcomes = (outcome,)
    if cleanup_error is not None:
        outcomes = (
            *outcomes,
            FailureOutcome(
                kind="cleanup_failed",
                stage="compare",
                detail=f"owned temporary file cleanup failed: {cleanup_error}",
                affected_evidence="inventory",
                evidence_state="possibly_remaining",
                corrective_action="remove the owned temporary file after preserving diagnostics",
                authority="secondary",
            ),
        )
    return PublicationError(detail, corrective_action=action, failure_outcomes=outcomes)


def _existing_result_is_reusable(destination: Path, expected_content: bytes, *, missing_ok: bool) -> bool:
    """Read and strictly validate one existing publication candidate exactly once."""
    identity = _entry_identity(destination)
    try:
        existing_content = destination.read_bytes()
    except FileNotFoundError:
        if missing_ok:
            return False
        raise
    try:
        existing = parse_comparison_result(existing_content)
        canonical_content = canonical_comparison_bytes(existing)
    except ValueError as error:
        raise FileExistsError(f"existing similarity artifact is not reusable: {error}") from error
    if existing_content != canonical_content or canonical_content != expected_content:
        raise FileExistsError("existing similarity artifact differs from the expected canonical result")
    if _entry_identity(destination) != identity:
        raise FileExistsError("similarity artifact changed during exact reuse validation")
    return True


def publish_comparison_result(destination: Path, result: ComparisonResult) -> bool:
    """Fsync and exclusively publish, or strictly reuse, one canonical result."""
    temporary_path: Path | None = None
    created_by_call = False
    expected_error: OSError | ValueError | TrafficlabError | None = None
    unexpected_error: BaseException | None = None
    try:
        expected_content = canonical_comparison_bytes(result)
        content = render_comparison_result(result)
        if content != expected_content:
            raise ValueError("rendered similarity artifact does not match the canonical evaluated result")
        if _existing_result_is_reusable(destination, expected_content, missing_ok=True):
            created_by_call = False
        else:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            persisted = load_comparison_result(temporary_path)
            persisted_bytes = temporary_path.read_bytes()
            persisted_content = canonical_comparison_bytes(persisted)
            if persisted_bytes != persisted_content or persisted_content != expected_content:
                raise ValueError("temporary similarity artifact did not round-trip to the evaluated result")
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                _existing_result_is_reusable(destination, expected_content, missing_ok=False)
                created_by_call = False
            else:
                created_by_call = True
            fsync_published_artifact(
                destination,
                stage="compare",
                affected_evidence="similarity.json",
            )
    except (OSError, ValueError, TrafficlabError) as error:
        expected_error = error
    except BaseException as error:
        unexpected_error = error

    cleanup_error: BaseException | None = None
    if temporary_path is not None:
        try:
            os.unlink(temporary_path)
        except BaseException as error:
            cleanup_error = error

    if unexpected_error is not None:
        if cleanup_error is not None:
            unexpected_error.add_note(f"owned temporary file cleanup also failed: {cleanup_error}")
        raise unexpected_error
    if expected_error is not None:
        raise _publication_error(expected_error, destination, cleanup_error) from expected_error
    if cleanup_error is not None:
        if isinstance(cleanup_error, OSError):
            publication_state = "published" if created_by_call else "not published"
            detail = (
                f"similarity artifact was {publication_state} at {destination}, "
                f"but owned temporary file cleanup failed: {cleanup_error}"
            )
            raise PublicationError(
                detail,
                corrective_action=(
                    "preserve the published result and remove the reported temporary file if it is still owned"
                ),
                failure_outcomes=(
                    FailureOutcome(
                        kind="publication_failed",
                        stage="compare",
                        detail=detail,
                        affected_evidence="similarity.json",
                        evidence_state="preserved",
                        corrective_action=(
                            "preserve the published result and remove the reported temporary file if it is still owned"
                        ),
                        authority="primary",
                    ),
                    FailureOutcome(
                        kind="cleanup_failed",
                        stage="compare",
                        detail=f"owned temporary file cleanup failed: {cleanup_error}",
                        affected_evidence="inventory",
                        evidence_state="possibly_remaining",
                        corrective_action="remove the owned temporary file after preserving diagnostics",
                        authority="secondary",
                    ),
                ),
            ) from cleanup_error
        raise cleanup_error
    return created_by_call
