"""Contract tests for the hosted quality adapter."""

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
CHECKOUT_REF = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_UV_REF = "astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990"
EXPECTED_WORKFLOW = f"""\
name: CI

on:
  pull_request:
  push:
    branches:
      - main

permissions:
  contents: read

jobs:
  quality:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    steps:
      - name: Check out source
        uses: {CHECKOUT_REF} # v7.0.1
        with:
          persist-credentials: false
      - name: Install uv and Python
        uses: {SETUP_UV_REF} # v8.3.2
        with:
          version: "0.11.25"
          python-version: "3.12"
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - name: Synchronize locked environment
        run: uv sync --locked --all-groups
      - name: Run mandatory gates
        run: uv run --locked python tools/quality.py all
"""


@pytest.mark.unit
def test_ci_locks_exact_security_and_job_topology() -> None:
    """Any trigger, permission, job, step, or option drift is policy drift."""
    assert WORKFLOW.read_text(encoding="utf-8") == EXPECTED_WORKFLOW


@pytest.mark.unit
def test_ci_uses_exact_ordered_action_revisions() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    action_refs = re.findall(
        r"^[ \t]+uses:[ \t]+([^ \t\r\n#]+)", workflow, flags=re.MULTILINE
    )

    assert tuple(action_refs) == (CHECKOUT_REF, SETUP_UV_REF)


@pytest.mark.unit
def test_ci_runs_only_the_locked_repository_gate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    run_commands = re.findall(r"^[ \t]+run:[ \t]+(.+)$", workflow, flags=re.MULTILINE)

    assert tuple(run_commands) == (
        "uv sync --locked --all-groups",
        "uv run --locked python tools/quality.py all",
    )
