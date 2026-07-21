"""Validate the complete Git index and tracked working tree."""

from __future__ import annotations

import subprocess
from pathlib import Path


def empty_tree_argv() -> tuple[str, ...]:
    """Return the object-format-aware empty-tree command vector."""
    return ("git", "hash-object", "-t", "tree", "--stdin")


def diff_check_argv(base_tree: str, *, cached: bool) -> tuple[str, ...]:
    """Return a complete-index or complete-worktree command vector."""
    if cached:
        return ("git", "diff", "--cached", "--check", base_tree)
    return ("git", "diff", "--check", base_tree)


def validate_whitespace(*, cwd: Path | None = None) -> int:
    """Run Git's complete-tree whitespace check and return its exit status."""
    empty_tree = subprocess.run(  # noqa: S603 - vector is fixed above.
        empty_tree_argv(),
        check=False,
        cwd=cwd,
        input="",
        stdout=subprocess.PIPE,
        text=True,
    )
    if empty_tree.returncode != 0:
        return empty_tree.returncode

    empty_tree_id = empty_tree.stdout.strip()
    commands = (
        diff_check_argv(empty_tree_id, cached=True),
        diff_check_argv(empty_tree_id, cached=False),
    )
    for command in commands:
        result = subprocess.run(  # noqa: S603 - builders constrain arguments.
            command,
            check=False,
            cwd=cwd,
        )
        if result.returncode != 0:
            return result.returncode
    return 0


def main() -> int:
    """Run the whitespace validator in the current repository."""
    return validate_whitespace()


if __name__ == "__main__":
    raise SystemExit(main())
