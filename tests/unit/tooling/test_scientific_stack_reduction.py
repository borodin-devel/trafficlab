"""Reproducible source-reduction inventory contracts."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts import measure_scientific_stack_reduction as reduction

_ROOT = Path(__file__).parents[3]
_EVIDENCE = _ROOT / "examples" / "scientific_stack" / "code_reduction.json"


def test_reduction_inventory_recomputes_exact_historical_gates() -> None:
    """Approximate file totals or double-counted phases could overstate the adopted-stack reduction."""
    evidence = reduction.build_reduction_evidence(_ROOT)
    categories = {item["name"]: item for item in evidence["categories"]}

    numpy = categories["numpy_loop_validation"]
    assert (numpy["before_lines"], numpy["after_lines"]) == (398, 287)
    assert numpy["reduction_percent"] == pytest.approx(27.889447236180903)
    assert numpy["threshold_percent"] == 25.0
    assert numpy["passed"] is True
    phase = numpy["phases"][0]
    assert phase["measurement"] == "loop_and_validation_statements"
    assert phase["measurement_definition"] == (
        "unique ast.stmt lines in complete explicitly named migrated functions, including nested loop bodies and "
        "straight-line custom validation"
    )
    before_names = {item["function"] for item in phase["before"]["functions"]}
    after_names = {item["function"] for item in phase["after"]["functions"]}
    assert {"_validated_events", "validate_fit_inputs", "_validated_trace"} <= before_names
    assert {"TrafficTrace.__post_init__", "validate_fit_inputs"} <= after_names

    artifact = categories["artifact_schema_validation"]
    assert (artifact["before_lines"], artifact["after_lines"]) == (1100, 684)
    assert artifact["reduction_percent"] == pytest.approx(37.81818181818182)
    assert artifact["threshold_percent"] == 30.0
    assert artifact["passed"] is True
    assert [(phase["before_lines"], phase["after_lines"]) for phase in artifact["phases"]] == [
        (402, 199),
        (518, 311),
        (180, 174),
    ]
    assert {phase["measurement"] for phase in artifact["phases"]} == {"ast_statements"}

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
    after_revision = reduction._stored_numpy_after_revision(content)  # pyright: ignore[reportPrivateUsage]
    rebuilt = reduction.build_reduction_evidence(_ROOT, numpy_after_revision=after_revision)
    assert content == reduction.canonical_json_bytes(rebuilt)
    reduction.validate_reduction_evidence(rebuilt)

    corrupted = copy.deepcopy(rebuilt)
    corrupted["categories"][1]["after_lines"] -= 1
    with pytest.raises(ValueError, match="after total"):
        reduction.validate_reduction_evidence(corrupted)

    mixed_metric = copy.deepcopy(rebuilt)
    mixed_metric["categories"][1]["phases"][2]["measurement"] = "loop_body_statements"
    with pytest.raises(ValueError, match="same AST statement-line metric"):
        reduction.validate_reduction_evidence(mixed_metric)


def test_reduction_check_compares_relocated_markov_function_asts() -> None:
    """A moved measured function must retain the historical AST, not merely its name."""
    relocated = reduction._NUMPY_CURRENT_RELOCATIONS  # pyright: ignore[reportPrivateUsage]
    assert relocated == {
        ("src/trafficlab/comparison/similarity/autocorrelation.py", "_sample_autocorrelations"): (
            "src/trafficlab/comparison/similarity/autocorrelation.py",
            "sample_autocorrelations",
        ),
        ("src/trafficlab/comparison/similarity/multiscale.py", "_binned_trace_features"): (
            "src/trafficlab/comparison/similarity/multiscale.py",
            "binned_direction_features",
        ),
        ("src/trafficlab/generation/models/markov_renewal.py", "_fit_trace"): (
            "src/trafficlab/generation/models/markov_renewal/model.py",
            "fit_trace",
        ),
        ("src/trafficlab/generation/models/markov_renewal.py", "encode_markov_states"): (
            "src/trafficlab/generation/models/markov_renewal/model.py",
            "encode_markov_states",
        ),
        ("src/trafficlab/generation/models/markov_renewal.py", "transition_count_matrix"): (
            "src/trafficlab/generation/models/markov_renewal/model.py",
            "transition_count_matrix",
        ),
        ("src/trafficlab/generation/models/markov_renewal.py", "type7_boundaries"): (
            "src/trafficlab/generation/models/markov_renewal/parameters.py",
            "type7_boundaries",
        ),
    }
    reduction._verify_numpy_sources_match_revision(  # pyright: ignore[reportPrivateUsage]
        _ROOT,
        reduction._full_revision(_ROOT, reduction._NUMPY_AFTER),  # pyright: ignore[reportPrivateUsage]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "root",
        "policy",
        "categories",
        "numpy_metric",
        "phase_before",
        "phase_after",
        "overlap",
        "category_before",
        "category_after",
        "totals",
        "threshold",
        "percentage",
        "passed",
        "decision",
    ],
)
def test_reduction_validator_rejects_each_metric_and_arithmetic_mutation(mutation: str) -> None:
    evidence = reduction.build_reduction_evidence(_ROOT)
    if mutation == "root":
        evidence["unknown"] = True
    elif mutation == "policy":
        evidence["schema_version"] = 2
    elif mutation == "categories":
        evidence["categories"].reverse()
    elif mutation == "numpy_metric":
        evidence["categories"][0]["phases"][0]["measurement"] = "loop_body_statements"
    elif mutation == "phase_before":
        evidence["categories"][0]["phases"][0]["before_lines"] += 1
    elif mutation == "phase_after":
        evidence["categories"][0]["phases"][0]["after_lines"] += 1
    elif mutation == "overlap":
        artifact = evidence["categories"][1]
        for side in ("before", "after"):
            artifact["phases"][1][side]["functions"].append(
                {
                    "executable_lines": [],
                    "function": "z_overlap_marker",
                    "line_count": 0,
                    "path": "src/trafficlab/models/registry.py",
                }
            )
    elif mutation == "category_before":
        evidence["categories"][0]["before_lines"] += 1
    elif mutation == "category_after":
        evidence["categories"][0]["after_lines"] += 1
    elif mutation == "totals":
        category = evidence["categories"][0]
        side = category["phases"][0]["before"]
        for item in side["functions"]:
            item["executable_lines"] = []
            item["line_count"] = 0
        side["total_lines"] = 0
        category["phases"][0]["before_lines"] = 0
        category["before_lines"] = 0
    elif mutation == "threshold":
        evidence["categories"][0]["threshold_percent"] = 0.0
    elif mutation == "percentage":
        evidence["categories"][0]["reduction_percent"] = 0.0
    elif mutation == "passed":
        evidence["categories"][0]["passed"] = False
    else:
        evidence["decision"]["passed"] = False

    with pytest.raises(ValueError):
        reduction.validate_reduction_evidence(evidence)


def test_reduction_inventory_rejects_a_missing_named_function() -> None:
    with pytest.raises(ValueError, match="missing functions"):
        reduction._inventory(  # pyright: ignore[reportPrivateUsage]
            _ROOT,
            "7b6094a9f47458b8de8d45deca64bc71170c62fd",
            {"scripts/audit_validation_study.py": ("missing_function",)},
            measurement="ast_statements",
        )
