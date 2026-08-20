"""Shared scientific-stack probe runner selection contract."""

import pytest

from scripts.run_scientific_stack_probes import selected_probe_names


def test_all_selection_runs_each_probe_once_in_stable_order() -> None:
    """A missing or duplicated candidate would make the final adoption record incomplete."""
    assert selected_probe_names("all") == ("mmpp", "pymoo", "scapy")
    assert selected_probe_names("pymoo") == ("pymoo",)


def test_unknown_probe_selection_fails_closed() -> None:
    """An unknown candidate must not silently fall back to the historical default."""
    with pytest.raises(ValueError, match="unknown scientific-stack probe"):
        selected_probe_names("unknown")
