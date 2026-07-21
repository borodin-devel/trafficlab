"""Tests for complete tracked-tree whitespace validation."""

import subprocess
import sys
from pathlib import Path

import pytest
from tools.validate_whitespace import diff_check_argv, empty_tree_argv

VALIDATOR = Path(__file__).resolve().parents[2] / "tools/validate_whitespace.py"


def _run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one fixed test-fixture Git argument vector."""
    return subprocess.run(  # noqa: S603 - test arguments are fixed at call sites.
        ("git", "-C", str(repository), *arguments),  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )


def _run_validator(repository: Path) -> subprocess.CompletedProcess[str]:
    """Run the repository validator against one isolated Git fixture."""
    return subprocess.run(  # noqa: S603 - the active interpreter is trusted.
        (sys.executable, str(VALIDATOR)),
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )


def _commit(repository: Path, relative_path: str, content: str) -> None:
    """Commit one UTF-8 fixture file without relying on ambient Git identity."""
    (repository / relative_path).write_text(content, encoding="utf-8")
    added = _run_git(repository, "add", "--", relative_path)
    assert added.returncode == 0, added.stderr
    committed = _run_git(
        repository,
        "-c",
        "user.name=Trafficlab Tests",
        "-c",
        "user.email=trafficlab-tests@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    assert committed.returncode == 0, committed.stderr


@pytest.mark.unit
def test_empty_tree_command_uses_git_stdin_argument_vector() -> None:
    """The empty tree is derived by Git without a shell or a fixed hash."""
    assert empty_tree_argv() == (
        "git",
        "hash-object",
        "-t",
        "tree",
        "--stdin",
    )


@pytest.mark.unit
def test_diff_commands_cover_complete_index_and_worktree() -> None:
    """Vectors check both complete prospective tracked snapshots."""
    assert diff_check_argv("derived-object-id", cached=True) == (
        "git",
        "diff",
        "--cached",
        "--check",
        "derived-object-id",
    )
    assert diff_check_argv("derived-object-id", cached=False) == (
        "git",
        "diff",
        "--check",
        "derived-object-id",
    )


@pytest.mark.integration
@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_validator_rejects_whitespace_already_committed_to_head(
    tmp_path: Path, object_format: str
) -> None:
    """A clean worktree cannot hide trailing whitespace committed in HEAD."""
    initialized = _run_git(
        tmp_path,
        "init",
        "--quiet",
        f"--object-format={object_format}",
    )
    assert initialized.returncode == 0, initialized.stderr
    _commit(tmp_path, "bad.txt", "committed trailing whitespace \n")

    old_worktree_only_check = _run_git(tmp_path, "diff", "--check")
    assert old_worktree_only_check.returncode == 0

    result = _run_validator(tmp_path)

    assert result.returncode == 2
    assert "bad.txt:1: trailing whitespace." in result.stdout


@pytest.mark.integration
def test_validator_rejects_whitespace_staged_after_a_clean_head(tmp_path: Path) -> None:
    """Staged changes are checked even when the committed baseline is clean."""
    initialized = _run_git(tmp_path, "init", "--quiet")
    assert initialized.returncode == 0, initialized.stderr
    _commit(tmp_path, "staged.txt", "clean\n")
    (tmp_path / "staged.txt").write_text(
        "staged trailing whitespace \n", encoding="utf-8"
    )
    added = _run_git(tmp_path, "add", "--", "staged.txt")
    assert added.returncode == 0, added.stderr

    result = _run_validator(tmp_path)

    assert result.returncode == 2
    assert "staged.txt:1: trailing whitespace." in result.stdout


@pytest.mark.integration
def test_validator_rejects_whitespace_unstaged_after_a_clean_head(
    tmp_path: Path,
) -> None:
    """The tracked working-tree snapshot is checked even when HEAD is clean."""
    initialized = _run_git(tmp_path, "init", "--quiet")
    assert initialized.returncode == 0, initialized.stderr
    _commit(tmp_path, "unstaged.txt", "clean\n")
    (tmp_path / "unstaged.txt").write_text(
        "unstaged trailing whitespace \n", encoding="utf-8"
    )

    result = _run_validator(tmp_path)

    assert result.returncode == 2
    assert "unstaged.txt:1: trailing whitespace." in result.stdout


@pytest.mark.integration
def test_validator_rejects_committed_whitespace_remaining_in_the_index(
    tmp_path: Path,
) -> None:
    """An unstaged cleanup cannot make the complete index snapshot clean."""
    initialized = _run_git(tmp_path, "init", "--quiet")
    assert initialized.returncode == 0, initialized.stderr
    _commit(tmp_path, "cleanup.txt", "committed trailing whitespace \n")
    (tmp_path / "cleanup.txt").write_text("clean\n", encoding="utf-8")

    result = _run_validator(tmp_path)

    assert result.returncode == 2
    assert "cleanup.txt:1: trailing whitespace." in result.stdout


@pytest.mark.integration
def test_validator_rejects_staged_whitespace_hidden_by_clean_worktree(
    tmp_path: Path,
) -> None:
    """An unstaged cleanup cannot conceal whitespace already in the index."""
    initialized = _run_git(tmp_path, "init", "--quiet")
    assert initialized.returncode == 0, initialized.stderr
    _commit(tmp_path, "hidden.txt", "clean\n")
    (tmp_path / "hidden.txt").write_text(
        "staged trailing whitespace \n", encoding="utf-8"
    )
    added = _run_git(tmp_path, "add", "--", "hidden.txt")
    assert added.returncode == 0, added.stderr
    (tmp_path / "hidden.txt").write_text("clean again\n", encoding="utf-8")

    result = _run_validator(tmp_path)

    assert result.returncode == 2
    assert "hidden.txt:1: trailing whitespace." in result.stdout
