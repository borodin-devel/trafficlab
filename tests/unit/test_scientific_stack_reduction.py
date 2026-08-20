"""Reproducible source-reduction inventory contracts."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts import measure_scientific_stack_reduction as reduction

_ROOT = Path(__file__).parents[2]
_EVIDENCE = _ROOT / "examples" / "scientific_stack" / "code_reduction.json"


def test_reduction_inventory_recomputes_exact_historical_gates() -> None:
    """Approximate file totals or double-counted phases could overstate the adopted-stack reduction."""
    evidence = reduction.build_reduction_evidence(_ROOT)
    categories = {item["name"]: item for item in evidence["categories"]}

    numpy = categories["numpy_loop_validation"]
    assert (numpy["before_lines"], numpy["after_lines"]) == (45, 5)
    assert numpy["reduction_percent"] == pytest.approx(88.88888888888889)
    assert numpy["threshold_percent"] == 25.0
    assert numpy["passed"] is True

    artifact = categories["artifact_schema_validation"]
    assert (artifact["before_lines"], artifact["after_lines"]) == (1482, 989)
    assert artifact["reduction_percent"] == pytest.approx(33.265856950067476)
    assert artifact["threshold_percent"] == 30.0
    assert artifact["passed"] is True
    assert [(phase["before_lines"], phase["after_lines"]) for phase in artifact["phases"]] == [
        (402, 199),
        (518, 311),
        (562, 479),
    ]

    phase_paths = [
        {item["path"] for side in ("before", "after") for item in phase[side]["functions"]}
        for phase in artifact["phases"]
    ]
    assert not (phase_paths[0] & phase_paths[1])
    assert not (phase_paths[0] & phase_paths[2])
    assert not (phase_paths[1] & phase_paths[2])
    assert all(
        not item["path"].startswith(("tests/", "examples/"))
        for category in evidence["categories"]
        for phase in category["phases"]
        for side in ("before", "after")
        for item in phase[side]["functions"]
    )


def test_checked_reduction_evidence_is_exact_and_fail_closed() -> None:
    """A stale stored percentage must not pass when its function inventory no longer recomputes."""
    content = _EVIDENCE.read_bytes()
    rebuilt = reduction.build_reduction_evidence(_ROOT)
    assert content == reduction.canonical_json_bytes(rebuilt)
    reduction.validate_reduction_evidence(rebuilt)

    corrupted = copy.deepcopy(rebuilt)
    corrupted["categories"][1]["after_lines"] -= 1
    with pytest.raises(ValueError, match="after total"):
        reduction.validate_reduction_evidence(corrupted)
