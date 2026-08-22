"""Exclusive best-model artifact validation and publication."""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from trafficlab.artifacts.io import file_identity, fsync_containing_directory, publisher_outcomes
from trafficlab.common.errors import FailureOutcome, TrafficlabError, attach_failure_outcome
from trafficlab.generation.models.fitted_model import load_best_model, render_best_model


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
    identity = file_identity(
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
    current_identity = file_identity(
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
    outcomes = publisher_outcomes(
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
        fsync_containing_directory(path)
    except TrafficlabError as error:
        raise attach_failure_outcome(
            error,
            kind="publication_failed",
            stage="fit",
            affected_evidence="best_model.json",
            evidence_state="preserved",
        ) from error


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
