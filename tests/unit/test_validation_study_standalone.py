"""Standalone Validation Study runner regressions."""

from __future__ import annotations

import importlib
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def test_standalone_runner_bootstraps_repository_before_late_auditor_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """The script-mode bootstrap must make its later auditor import resolvable."""

    path = [str(_ROOT / "scripts"), *(entry for entry in sys.path if Path(entry or ".").resolve() != _ROOT)]
    monkeypatch.setattr(sys, "path", path)

    namespace = runpy.run_path(str(_ROOT / "scripts" / "run_validation_study.py"), run_name="__trafficlab_standalone__")

    assert str(_ROOT) == sys.path[0]
    exec("from scripts import audit_validation_study as auditor", namespace)
    assert namespace["auditor"].__name__ == "scripts.audit_validation_study"


def test_package_runner_leaves_the_import_path_unchanged() -> None:
    """Package execution already has the repository package root available."""

    before = list(sys.path)

    module = importlib.import_module("scripts.run_validation_study")

    assert sys.path == before
    assert module.__package__ == "scripts"


def test_standalone_runner_resolves_the_collection_late_auditor_import() -> None:
    """Direct script execution must retain the package import used after collection completes."""

    program = f"""
import runpy
import sys
from pathlib import Path

root = Path({str(_ROOT)!r})
script = root / "scripts" / "run_validation_study.py"
sys.path[:] = [str(script.parent), *(entry for entry in sys.path if Path(entry or ".").resolve() != root)]
sys.argv = [str(script), "--help"]
try:
    runpy.run_path(str(script), run_name="__main__")
except SystemExit as error:
    assert error.code == 0
from scripts import audit_validation_study as auditor
assert auditor.__name__ == "scripts.audit_validation_study"
"""
    completed = subprocess.run(
        (sys.executable, "-c", program),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
