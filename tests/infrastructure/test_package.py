"""Tests for the installable package scaffold."""

import pytest

import trafficlab


@pytest.mark.unit
def test_package_exposes_version() -> None:
    """The installed package exposes the version used by setuptools."""
    assert trafficlab.__version__ == "0.1.0"
