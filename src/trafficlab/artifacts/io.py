"""Durable low-level file publication and run-log primitives."""

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, cast

from trafficlab.common.errors import FailureOutcome, TrafficlabError, attach_failure_outcome


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


def fsync_containing_directory(path: Path) -> None:
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
        fsync_containing_directory(path)
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
        fsync_containing_directory(path)
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


type FileIdentity = tuple[int, int, int, int]


def is_file_identity(value: object) -> bool:
    if type(value) is not tuple:
        return False
    components = cast(tuple[object, ...], value)
    return len(components) == 4 and all(type(component) is int and component >= 0 for component in components)


def file_identity(
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


def publisher_outcomes(
    error: TrafficlabError | OSError,
    *,
    stage: str,
    affected_evidence: str,
    detail: str,
    corrective_action: str,
    cleanup_error: OSError | None,
) -> tuple[FailureOutcome, ...]:
    """Keep an owning publisher's primary state and append owned cleanup evidence."""
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
