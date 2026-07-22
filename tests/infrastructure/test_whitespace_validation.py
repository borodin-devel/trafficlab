"""Tests for complete tracked-tree whitespace validation."""

import subprocess
import sys
from pathlib import Path

import pytest
import tools.validate_whitespace as whitespace_validation
from tools.validate_whitespace import (
    diff_check_argv,
    empty_tree_argv,
    validate_whitespace,
)

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


@pytest.mark.unit
def test_whitespace_issues_match_git_default_policy() -> None:
    """The pure byte policy reports every repository-owned Git default."""
    scanner = getattr(whitespace_validation, "whitespace_issues", None)
    assert callable(scanner)

    assert scanner(b"text\r\n \tindent\n<<<<<<< HEAD\n=======\n>>>>>>> branch\n\n") == (
        (1, "trailing whitespace."),
        (2, "space before tab in indent."),
        (3, "leftover conflict marker"),
        (4, "leftover conflict marker"),
        (5, "leftover conflict marker"),
        (6, "new blank line at EOF."),
    )
    assert scanner(b"clean\n \n") == (
        (2, "trailing whitespace."),
        (2, "new blank line at EOF."),
    )
    assert scanner(b"clean\n\n\n\n") == ((2, "new blank line at EOF."),)
    assert scanner(b"======= branch\n") == ((1, "leftover conflict marker"),)
    assert scanner(b"========\n") == ()


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
@pytest.mark.parametrize(
    ("relative_path", "content", "diagnostic"),
    [
        (
            "carriage-return.txt",
            "text\r\n",
            "carriage-return.txt:1: trailing whitespace.",
        ),
        (
            "space-before-tab.txt",
            " \tindented\n",
            "space-before-tab.txt:1: space before tab in indent.",
        ),
        (
            "blank-at-eof.txt",
            "content\n\n",
            "blank-at-eof.txt:2: new blank line at EOF.",
        ),
        (
            "conflict-marker.txt",
            "<<<<<<< HEAD\ncontent\n",
            "conflict-marker.txt:1: leftover conflict marker",
        ),
    ],
)
def test_validator_enforces_git_default_whitespace_issue_families(
    tmp_path: Path,
    relative_path: str,
    content: str,
    diagnostic: str,
) -> None:
    initialized = _run_git(tmp_path, "init", "--quiet")
    assert initialized.returncode == 0, initialized.stderr
    _commit(tmp_path, relative_path, content)

    result = _run_validator(tmp_path)

    assert result.returncode == 2
    assert diagnostic in result.stdout


@pytest.mark.integration
def test_validator_ignores_whitespace_disabling_gitattributes(
    tmp_path: Path,
) -> None:
    """Repository policy cannot be disabled by tracked Git attributes."""
    initialized = _run_git(tmp_path, "init", "--quiet")
    assert initialized.returncode == 0, initialized.stderr
    _commit(tmp_path, ".gitattributes", "* -whitespace\n")
    _commit(tmp_path, "bad.txt", "committed trailing whitespace \n")

    git_check = _run_git(tmp_path, "show", "--check", "--format=", "HEAD")
    assert git_check.returncode == 0
    assert git_check.stdout == ""

    result = _run_validator(tmp_path)

    assert result.returncode == 2
    assert "bad.txt:1: trailing whitespace." in result.stdout


@pytest.mark.integration
def test_validator_does_not_follow_symlinked_worktree_path_ancestor(
    tmp_path: Path,
) -> None:
    """A tracked file cannot redirect the byte scan through a directory link."""
    initialized = _run_git(tmp_path, "init", "--quiet")
    assert initialized.returncode == 0, initialized.stderr
    tracked = tmp_path / "tracked"
    tracked.mkdir()
    _commit(tmp_path, "tracked/file.txt", "clean\n")
    (tracked / "file.txt").unlink()
    tracked.rmdir()
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "file.txt").write_text(
        "outside trailing whitespace \n", encoding="utf-8"
    )
    tracked.symlink_to(replacement, target_is_directory=True)

    result = _run_validator(tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.integration
def test_validator_rejects_explicit_cwd_with_symlinked_ancestor(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The repository root is opened without following any supplied component."""
    real_parent = tmp_path / "real"
    repository = real_parent / "repository"
    repository.mkdir(parents=True)
    initialized = _run_git(repository, "init", "--quiet")
    assert initialized.returncode == 0, initialized.stderr
    _commit(repository, "clean.txt", "clean\n")
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)

    assert validate_whitespace(cwd=alias / "repository") == 2
    assert "symlink" in capsys.readouterr().err


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
