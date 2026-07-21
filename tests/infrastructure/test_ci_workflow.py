"""Contract tests for the hosted quality adapter."""

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"


@pytest.mark.unit
def test_ci_uses_immutable_least_privilege_actions() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    action_refs = re.findall(r"uses: [^@\s]+@([^\s]+)", workflow)

    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
    assert "permissions:\n  contents: read\n" in workflow
    assert "persist-credentials: false" in workflow


@pytest.mark.unit
def test_ci_delegates_to_the_locked_repository_gate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "uv sync --locked --all-groups" in workflow
    assert "uv run --locked python tools/quality.py all" in workflow
    assert workflow.count("tools/quality.py all") == 1
    assert 'version: "0.11.25"' in workflow
    assert 'python-version: "3.12"' in workflow
    assert "enable-cache: true" in workflow
    assert 'cache-dependency-glob: "uv.lock"' in workflow
