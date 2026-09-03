"""Source-bound validation contracts for retained scientific-stack evidence."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from scripts import check_scientific_stack_example as example_run
from scripts import scientific_stack_source_bound as source_bound

_ROOT = Path(__file__).parents[3]
_SCIENTIFIC_STACK = _ROOT / "examples" / "scientific_stack"
_EVIDENCE = _SCIENTIFIC_STACK / "example_run.json"


def _copied_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    shutil.copytree(_SCIENTIFIC_STACK, repository / "examples" / "scientific_stack")
    return repository


def test_source_bound_check_detaches_at_the_recorded_commit_and_uses_regular_evidence_copies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Current code must delegate schema-four verification to its recorded source without linking mutable bytes."""
    repository = _copied_repository(tmp_path)
    evidence_path = repository / "examples" / "scientific_stack" / "example_run.json"
    recorded_commit = json.loads(evidence_path.read_bytes())["source"]["commit"]
    calls: list[tuple[str, ...]] = []
    environments: list[dict[str, str]] = []
    clone_roots: list[Path] = []

    def run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:2] == ("uv", "run"):
            environments.append(cast(dict[str, str], kwargs["env"]))
        if command[:2] == ("git", "clone"):
            Path(command[-1]).mkdir(parents=True)
        elif command[:2] == ("uv", "run"):
            clone = cast(Path, kwargs["cwd"])
            clone_roots.append(clone)
            copied = clone / "examples" / "scientific_stack" / "example_run.json"
            assert copied.read_bytes() == evidence_path.read_bytes()
            assert copied.stat().st_ino != evidence_path.stat().st_ino
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(source_bound.subprocess, "run", run)

    assert example_run.verify_source_bound_evidence(evidence_path, repository=repository) == recorded_commit

    assert calls[0][:-1] == (
        "git",
        "clone",
        "--no-checkout",
        "--no-local",
        "--no-hardlinks",
        str(repository.resolve()),
    )
    assert calls[1] == ("git", "checkout", "--detach", recorded_commit)
    assert calls[2] == (
        "uv",
        "run",
        "--offline",
        "--locked",
        "--active",
        "--no-project",
        "python",
        "scripts/check_scientific_stack_example.py",
        "--check",
    )
    assert environments[0]["PYTHONPATH"].split(":")[0] == str(clone_roots[0] / "src")


def test_source_bound_check_rejects_an_evidence_path_outside_its_repository(tmp_path: Path) -> None:
    """The delegated copy may only occupy the evidence directory relative to the cloned repository root."""
    with pytest.raises(ValueError, match="inside the repository"):
        example_run.verify_source_bound_evidence(_EVIDENCE, repository=tmp_path)


def test_source_bound_check_reports_clone_or_historical_checker_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed isolated checkout cannot be mistaken for successful historical verification."""
    repository = _copied_repository(tmp_path)
    evidence_path = repository / "examples" / "scientific_stack" / "example_run.json"

    def fail(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="failure sentinel")

    monkeypatch.setattr(source_bound.subprocess, "run", fail)

    with pytest.raises(ValueError, match="failure sentinel"):
        example_run.verify_source_bound_evidence(evidence_path, repository=repository)
