"""Shared scientific-stack probe runner selection contract."""

import hashlib
from pathlib import Path

import pytest

from scripts.run_scientific_stack_probes import main, selected_probe_names


def test_all_selection_runs_each_probe_once_in_stable_order() -> None:
    """A missing or duplicated candidate would make the final adoption record incomplete."""
    assert selected_probe_names("all") == ("mmpp", "pymoo", "pymoo-v5")
    assert selected_probe_names("pymoo") == ("pymoo",)
    assert selected_probe_names("pymoo-v5") == ("pymoo-v5",)

    with pytest.raises(ValueError, match="unknown scientific-stack probe"):
        selected_probe_names("scapy")


def test_unknown_probe_selection_fails_closed() -> None:
    """An unknown candidate must not silently fall back to the historical default."""
    with pytest.raises(ValueError, match="unknown scientific-stack probe"):
        selected_probe_names("unknown")


def test_historical_pymoo_probe_is_exact_and_read_only(capsys: pytest.CaptureFixture[str]) -> None:
    """Current generators must neither reinterpret nor overwrite the accepted schema-three snapshot."""
    historical = Path(__file__).resolve().parents[3] / "examples" / "scientific_stack" / "pymoo_cases.json"
    before = historical.read_bytes()

    assert len(before) == 2_026_258
    assert hashlib.sha256(before).hexdigest() == "6985ec0f1291b675f240cf2f7a32e90ac16bad6be3f3978968b82f24a56f486e"
    assert main(["--probe", "pymoo", "--check"]) == 0
    assert main(["--probe", "pymoo"]) == 2
    assert historical.read_bytes() == before
    assert "immutable" in capsys.readouterr().err
