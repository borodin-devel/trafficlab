"""Tests for managed and direct attempt-directory boundaries."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trafficlab.libs.configuration import (
    AttemptDirectoryError,
    create_direct_attempt,
    validate_managed_attempt,
)


def _private(path: Path) -> None:
    path.mkdir(mode=0o700)
    path.chmod(0o700)


@pytest.mark.unit
def test_managed_attempt_accepts_empty_private_child(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    attempt = root / "assigned"
    _private(root)
    _private(attempt)

    assert validate_managed_attempt(attempt, root) == attempt


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["file", "contents", "mode", "escape", "symlink"])
def test_managed_attempt_rejects_unsafe_boundary(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "managed"
    _private(root)
    attempt = root / "assigned"
    _private(attempt)
    if kind == "file":
        attempt.rmdir()
        attempt.write_text("not a directory", encoding="utf-8")
    elif kind == "contents":
        (attempt / "leftover").write_text("x", encoding="utf-8")
    elif kind == "mode":
        attempt.chmod(0o755)
    elif kind == "escape":
        attempt = tmp_path / "escape"
        _private(attempt)
    else:
        attempt.rmdir()
        attempt.symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    with pytest.raises(AttemptDirectoryError) as caught:
        validate_managed_attempt(attempt, root)

    assert str(caught.value).splitlines() == [str(caught.value)]


@pytest.mark.unit
def test_direct_attempt_uses_utc_shape_private_mode_and_collision_retry(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    _private(run)
    collision = run / "10_capture_20260723T010203_Z_first"
    _private(collision)
    suffixes = iter(("first", "second"))

    attempt = create_direct_attempt(
        "10_capture",
        tmp_path,
        now=datetime(2026, 7, 23, 1, 2, 3, tzinfo=UTC),
        suffix=lambda: next(suffixes),
    )

    assert attempt == run / "10_capture_20260723T010203_Z_second"
    assert attempt.is_dir()
    assert attempt.stat().st_mode & 0o777 == 0o700


@pytest.mark.unit
def test_direct_attempt_rejects_unsafe_working_directory_or_suffix(
    tmp_path: Path,
) -> None:
    with pytest.raises(AttemptDirectoryError):
        create_direct_attempt("10_capture", Path("relative"))
    with pytest.raises(AttemptDirectoryError):
        create_direct_attempt("10_capture", tmp_path, suffix=lambda: "bad/path")
