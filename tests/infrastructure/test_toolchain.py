"""Integration tests for locked development-tool dependencies."""

from importlib.util import find_spec

import pytest


@pytest.mark.integration
def test_pyright_has_locked_node_runtime() -> None:
    """Pyright must not depend on ambient Node or a runtime download."""
    assert find_spec("nodejs_wheel") is not None
