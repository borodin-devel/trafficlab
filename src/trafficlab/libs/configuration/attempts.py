"""Filesystem shell for managed and direct application attempt directories."""

from __future__ import annotations

import os
import posixpath
import re
import secrets
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .errors import AttemptDirectoryError

_APPLICATION = re.compile(r"[0-9]{2}_[a-z][a-z0-9_]*")
_SUFFIX = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _canonical_absolute(path: object) -> Path:
    if not isinstance(path, Path):
        raise AttemptDirectoryError("attempt path must be a Path")
    text = path.as_posix()
    if (
        not path.is_absolute()
        or text.startswith("//")
        or posixpath.normpath(text) != text
        or any(part in {".", ".."} for part in text.split("/"))
    ):
        raise AttemptDirectoryError("attempt path must be canonical and absolute")
    return path


def _require_private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise AttemptDirectoryError("attempt path cannot be inspected") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise AttemptDirectoryError("attempt directory filesystem envelope is unsafe")


def validate_managed_attempt(attempt_dir: Path, managed_run_root: Path) -> Path:
    """Accept only an assigned empty private attempt within its managed root."""

    attempt = _canonical_absolute(attempt_dir)
    root = _canonical_absolute(managed_run_root)
    try:
        relative = attempt.relative_to(root)
    except ValueError as error:
        raise AttemptDirectoryError("managed attempt escapes its run root") from error
    if not relative.parts:
        raise AttemptDirectoryError("managed attempt must be below its run root")
    _require_private_directory(root)
    current = root
    for component in relative.parts:
        current /= component
        _require_private_directory(current)
    try:
        if os.listdir(attempt):
            raise AttemptDirectoryError("managed attempt must be empty")
    except AttemptDirectoryError:
        raise
    except OSError as error:
        raise AttemptDirectoryError("managed attempt cannot be read") from error
    return attempt


def create_direct_attempt(
    application: str,
    cwd: Path,
    *,
    now: datetime | None = None,
    suffix: Callable[[], str] = lambda: secrets.token_urlsafe(8),
) -> Path:
    """Atomically create one collision-resistant private direct attempt."""

    application_value = cast(object, application)
    if (
        not isinstance(application_value, str)
        or _APPLICATION.fullmatch(application_value) is None
    ):
        raise AttemptDirectoryError("application must be numbered snake case")
    working_directory = _canonical_absolute(cwd)
    if not callable(suffix):
        raise AttemptDirectoryError("attempt suffix source must be callable")
    timestamp = (datetime.now(UTC) if now is None else now.astimezone(UTC)).strftime(
        "%Y%m%dT%H%M%S"
    )
    run_root = working_directory / "run"
    try:
        run_root.mkdir(mode=0o700, exist_ok=True)
        _require_private_directory(run_root)
    except OSError as error:
        raise AttemptDirectoryError("direct run root cannot be created") from error
    for _ in range(128):
        generated = cast(object, suffix())
        if not isinstance(generated, str) or _SUFFIX.fullmatch(generated) is None:
            raise AttemptDirectoryError("attempt suffix is unsafe")
        attempt = run_root / f"{application}_{timestamp}_Z_{generated}"
        try:
            attempt.mkdir(mode=0o700)
            attempt.chmod(0o700)
        except FileExistsError:
            continue
        except OSError as error:
            raise AttemptDirectoryError("direct attempt cannot be created") from error
        return validate_managed_attempt(attempt, run_root)
    raise AttemptDirectoryError("direct attempt collision limit reached")
