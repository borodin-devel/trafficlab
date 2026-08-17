"""Audit-gated, exclusive publication of accepted validation-study evidence."""

from __future__ import annotations

import ctypes
import errno
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from trafficlab.errors import FailureOutcome, TrafficlabError, attach_failure_outcome

type BundleAudit = Callable[[Path], None]

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_SAFE_STUDY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*", flags=re.ASCII)


class AcceptedBundlePublicationError(TrafficlabError):
    """A post-rename durability failure whose accepted destination is preserved."""

    def __init__(self, destination: Path, error: OSError) -> None:
        super().__init__(
            f"accepted evidence destination was preserved after a post-rename durability failure at "
            f"{destination}: {error}",
            corrective_action=(
                "preserve and validate the accepted destination; do not retry publication under the occupied study ID"
            ),
        )
        self.destination = destination
        self.evidence_state: Literal["preserved"] = "preserved"
        attach_failure_outcome(
            self,
            kind="publication_failed",
            stage="publication",
            affected_evidence="accepted evidence bundle",
            evidence_state="preserved",
        )


def _publication_error(detail: str) -> TrafficlabError:
    return TrafficlabError(
        f"could not publish accepted evidence bundle: {detail}",
        corrective_action="preserve the candidate, correct the local filesystem failure, and retry publication",
    )


def _collision(destination: Path) -> TrafficlabError:
    return TrafficlabError(
        f"publication_collision: accepted evidence bundle already exists at {destination}",
        corrective_action="choose a new study ID; accepted evidence bundles are immutable",
        failure_outcome=FailureOutcome(
            kind="publication_collision",
            stage="publication",
            detail="accepted bundle already exists",
            affected_evidence="candidate accepted evidence bundle",
            evidence_state="not_published",
            corrective_action="choose a new study ID",
            authority="primary",
        ),
    )


def _validate_study_id(study_id: object) -> str:
    if type(study_id) is not str or _SAFE_STUDY_ID.fullmatch(study_id) is None:
        raise TrafficlabError(
            "invalid accepted evidence study ID: use one visible ASCII path component",
            corrective_action="use letters, digits, dots, underscores, and hyphens beginning with a letter or digit",
        )
    return study_id


def _validate_candidate(candidate: object) -> Path:
    if not isinstance(candidate, Path):
        raise TypeError("candidate must be a pathlib.Path")
    try:
        candidate_mode = candidate.lstat().st_mode
    except OSError as error:
        raise TrafficlabError(
            f"accepted evidence candidate is not a readable regular directory: {candidate}",
            corrective_action="prepare and audit a local candidate directory before publication",
        ) from error
    if not stat.S_ISDIR(candidate_mode):
        raise TrafficlabError(
            f"accepted evidence candidate must be a regular directory: {candidate}",
            corrective_action="prepare and audit a local candidate directory before publication",
        )
    return candidate


def _fsync_open_path(path: Path, *, directory: bool) -> None:
    flags = os.O_RDONLY
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    else:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    for directory, _directory_names, file_names in os.walk(root, topdown=False, followlinks=False):
        directory_path = Path(directory)
        for file_name in file_names:
            file_path = directory_path / file_name
            if stat.S_ISREG(file_path.lstat().st_mode):
                _fsync_open_path(file_path, directory=False)
        _fsync_open_path(directory_path, directory=True)


def _rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish one directory without replacing an existing name.

    Trafficlab's supported execution environment is Linux. libc's renameat2
    exposes the kernel's no-replace primitive without adding a dependency.
    """

    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as error:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable on this supported local filesystem") from error
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(source),
        _AT_FDCWD,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination)


def _cleanup_temporary(temporary: Path) -> str | None:
    try:
        shutil.rmtree(temporary)
    except FileNotFoundError:
        return None
    except OSError as error:
        return str(error)
    except BaseException as error:
        return f"{type(error).__name__}: {error}"
    return None


def _note_cleanup_failure(error: BaseException, cleanup_error: str | None) -> None:
    if cleanup_error is not None:
        error.add_note(f"temporary staging cleanup also failed: {cleanup_error}")


def publish_accepted_bundle(
    candidate: Path,
    evidence_root: Path,
    study_id: str,
    audit: BundleAudit,
) -> Path:
    """Audit, durably stage, and exclusively publish one accepted evidence bundle."""

    checked_study_id = _validate_study_id(study_id)
    checked_candidate = _validate_candidate(candidate)
    if not callable(audit):
        raise TypeError("audit must be callable")

    destination = evidence_root / checked_study_id
    audit(checked_candidate)

    try:
        evidence_root.mkdir(parents=True, exist_ok=True)
        root_mode = evidence_root.lstat().st_mode
        if not stat.S_ISDIR(root_mode):
            raise OSError(errno.ENOTDIR, "evidence root is not a regular directory", evidence_root)
        _fsync_open_path(evidence_root.parent, directory=True)
        temporary_container = Path(tempfile.mkdtemp(prefix=f".{checked_study_id}.", suffix=".tmp", dir=evidence_root))
    except OSError as error:
        raise _publication_error(str(error)) from error

    temporary = temporary_container / checked_study_id

    try:
        shutil.copytree(checked_candidate, temporary, symlinks=True)
        _fsync_tree(temporary_container)
    except OSError as error:
        cleanup_error = _cleanup_temporary(temporary_container)
        detail = str(error)
        if cleanup_error is not None:
            detail = f"{detail}; temporary cleanup also failed: {cleanup_error}"
        raise _publication_error(detail) from error
    except BaseException as error:
        _note_cleanup_failure(error, _cleanup_temporary(temporary_container))
        raise

    try:
        audit(temporary)
    except BaseException as error:
        _note_cleanup_failure(error, _cleanup_temporary(temporary_container))
        raise

    try:
        _rename_noreplace(temporary, destination)
    except OSError as error:
        cleanup_error = _cleanup_temporary(temporary_container)
        if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            collision = _collision(destination)
            _note_cleanup_failure(collision, cleanup_error)
            raise collision from error
        detail = str(error)
        if cleanup_error is not None:
            detail = f"{detail}; temporary cleanup also failed: {cleanup_error}"
        raise _publication_error(detail) from error
    except BaseException as error:
        _note_cleanup_failure(error, _cleanup_temporary(temporary_container))
        raise

    try:
        temporary_container.rmdir()
        _fsync_open_path(evidence_root, directory=True)
    except OSError as error:
        _note_cleanup_failure(error, _cleanup_temporary(temporary_container))
        raise AcceptedBundlePublicationError(destination, error) from error
    except BaseException as error:
        _note_cleanup_failure(error, _cleanup_temporary(temporary_container))
        raise

    return destination
