"""Source-bound offline validation for retained scientific-stack evidence."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import cast


def _recorded_source_commit(evidence_path: Path) -> str:
    """Read the immutable evidence's source commit without parsing its old artifacts."""
    try:
        document_value = cast(object, json.loads(evidence_path.read_bytes()))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"scientific-stack evidence is unreadable: {error}") from error
    if not isinstance(document_value, dict):
        raise ValueError("scientific-stack evidence must be a JSON object")
    document = cast(dict[str, object], document_value)
    source_value = document.get("source")
    if not isinstance(source_value, dict):
        raise ValueError("scientific-stack evidence is missing its source record")
    source = cast(dict[str, object], source_value)
    commit = source.get("commit")
    if type(commit) is not str or len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("scientific-stack evidence source commit is invalid")
    return commit


def _copy_regular_tree(source: Path, destination: Path) -> None:
    """Copy one retained evidence tree without preserving symlinks or hardlinks."""
    if source.is_symlink() or not source.is_dir():
        raise ValueError("scientific-stack evidence directory must be a regular directory")
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError("historical clone evidence destination must be a regular directory")
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for path in sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix().encode("utf-8")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_symlink():
            raise ValueError(f"scientific-stack evidence must not contain symlinks: {relative}")
        if path.is_dir():
            target.mkdir()
        elif path.is_file():
            shutil.copy2(path, target)
        else:
            raise ValueError(f"scientific-stack evidence must contain regular files: {relative}")


def _run_historical_command(command: tuple[str, ...], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True, env=env)
    if completed.returncode != 0:
        output = completed.stderr.strip() or completed.stdout.strip() or "no command output"
        raise ValueError(f"historical scientific-stack check failed: {output}")


def verify_source_bound_evidence(evidence_path: Path, *, repository: Path) -> str:
    """Validate schema-four evidence only with the checker from its recorded source commit."""
    repository_root = repository.resolve()
    try:
        relative_directory = evidence_path.resolve().parent.relative_to(repository_root)
    except ValueError as error:
        raise ValueError("scientific-stack evidence must be inside the repository") from error
    commit = _recorded_source_commit(evidence_path)
    with tempfile.TemporaryDirectory(prefix="trafficlab-scientific-stack-") as temporary:
        clone = Path(temporary) / "source"
        _run_historical_command(
            (
                "git",
                "clone",
                "--no-checkout",
                "--no-local",
                "--no-hardlinks",
                str(repository_root),
                str(clone),
            ),
            cwd=repository_root,
        )
        _run_historical_command(("git", "checkout", "--detach", commit), cwd=clone)
        _copy_regular_tree(evidence_path.resolve().parent, clone / relative_directory)
        _run_historical_command(
            (
                "uv",
                "run",
                "--offline",
                "--locked",
                "--active",
                "--no-project",
                "python",
                "scripts/check_scientific_stack_example.py",
                "--check",
            ),
            cwd=clone,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    item for item in (str(clone / "src"), os.environ.get("PYTHONPATH")) if item
                ),
            },
        )
    return commit


def verify_historical_pymoo_evidence(evidence_path: Path, *, repository: Path, source_commit: str) -> str:
    """Run the immutable pymoo snapshot through its source revision's checker."""
    repository_root = repository.resolve()
    evidence = evidence_path.resolve()
    try:
        relative = evidence.relative_to(repository_root)
    except ValueError as error:
        raise ValueError("historical pymoo evidence must be inside the repository") from error
    if (
        type(source_commit) is not str
        or len(source_commit) != 40
        or any(character not in "0123456789abcdef" for character in source_commit)
    ):
        raise ValueError("historical pymoo source commit is invalid")
    if evidence.is_symlink() or not evidence.is_file():
        raise ValueError("historical pymoo evidence must be a regular file")
    with tempfile.TemporaryDirectory(prefix="trafficlab-pymoo-history-") as temporary:
        clone = Path(temporary) / "source"
        _run_historical_command(
            (
                "git",
                "clone",
                "--no-checkout",
                "--no-local",
                "--no-hardlinks",
                str(repository_root),
                str(clone),
            ),
            cwd=repository_root,
        )
        _run_historical_command(("git", "checkout", "--detach", source_commit), cwd=clone)
        destination = clone / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(evidence, destination)
        _run_historical_command(
            (
                "uv",
                "run",
                "--offline",
                "--locked",
                "--active",
                "--no-project",
                "python",
                "scripts/run_scientific_stack_probes.py",
                "--probe",
                "pymoo",
                "--check",
            ),
            cwd=clone,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    item for item in (str(clone / "src"), os.environ.get("PYTHONPATH")) if item
                ),
            },
        )
    return source_commit
