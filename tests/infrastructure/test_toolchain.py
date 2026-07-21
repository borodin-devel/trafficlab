"""Integration tests for locked development-tool dependencies."""

import os
import subprocess
import sys

import pytest


@pytest.mark.integration
def test_pyright_has_locked_node_runtime() -> None:
    """Pyright must launch without ambient Node or a runtime download."""
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": "",
            "PYRIGHT_PYTHON_GLOBAL_NODE": "0",
            "PYRIGHT_PYTHON_NODEJS_WHEEL": "1",
        }
    )

    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--version"],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("pyright ")
