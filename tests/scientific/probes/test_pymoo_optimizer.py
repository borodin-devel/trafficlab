"""Scientific gates for the test-only pymoo optimizer adoption probe."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from tests.scientific.probes.pymoo_optimizer import (
    CACHE_KEY_FIELDS,
    CHAMPION_SEED,
    CHECKPOINT_FIELDS,
    FAMILY_NAMES,
    GENERATION_LIMITS,
    INITIAL_EVALUATION_BUDGET,
    KNOWN_CASES,
    PROHIBITED_SERIALIZERS,
    SEARCH_SEED,
    SIMILARITY,
    TOTAL_GENERATIONS,
    TRIAL_SEEDS,
    WINDOW_SECONDS,
    PublicStateSnapshot,
    build_probe_evidence,
    decide_probe,
    render_probe_evidence,
    validate_probe_evidence,
    write_probe_evidence,
)


def _walk(value: object) -> tuple[object, ...]:
    if isinstance(value, dict):
        children = cast(Mapping[object, object], value).values()
        return (cast(object, value), *(item for child in children for item in _walk(child)))
    if isinstance(value, list):
        children = cast(Sequence[object], value)
        return (cast(object, value), *(item for child in children for item in _walk(child)))
    return (value,)


def test_probe_policy_is_predeclared_before_optimizer_results() -> None:
    """Changing budgets, seeds, cache identity, or gates after observing runs invalidates the probe."""
    assert FAMILY_NAMES == ("markov_renewal", "mmpp", "poisson_empirical")
    assert INITIAL_EVALUATION_BUDGET == 4
    assert TOTAL_GENERATIONS == 2
    assert SEARCH_SEED == 6053
    assert TRIAL_SEEDS == (17, 29)
    assert CHAMPION_SEED == 43
    assert WINDOW_SECONDS == 6.0
    assert GENERATION_LIMITS.model_dump(mode="json") == {
        "max_packets": 1_000,
        "max_output_bytes": 1_000_000,
        "max_wall_seconds": 5.0,
    }
    assert SIMILARITY.method_weights.model_dump(mode="json") == {
        "frame_size_ks": 0.25,
        "iat_ks": 0.25,
        "autocorrelation": 0.25,
        "multiscale_rate": 0.25,
    }
    assert CACHE_KEY_FIELDS == (
        "family",
        "genes",
        "observation_window_seconds",
        "trial_seeds",
        "generation_limits",
        "similarity",
    )
    assert CHECKPOINT_FIELDS == (
        "population",
        "generation",
        "evaluation_count",
        "termination",
        "configuration",
        "pymoo_version",
        "rng",
    )
    assert PROHIBITED_SERIALIZERS == ("dill", "pickle", "cloudpickle")
    assert KNOWN_CASES == (
        {
            "name": "bounded_continuous_sphere",
            "variable_kinds": {"x0": "real", "x1": "real"},
            "bounds": {"x0": (-2.0, 2.0), "x1": (-2.0, 2.0)},
            "known_optimum": {"variables": {"x0": 0.0, "x1": 0.0}, "objective": 0.0},
        },
        {
            "name": "mixed_integer_real_quadratic",
            "variable_kinds": {"count": "integer", "scale": "real"},
            "bounds": {"count": (0, 6), "scale": (0.5, 2.0)},
            "known_optimum": {"variables": {"count": 3, "scale": 1.25}, "objective": 0.0},
        },
    )


def test_known_optima_and_repeated_runs_are_exact() -> None:
    """A wrong variable type, bound, objective, or seed loses a predeclared analytical optimum."""
    evidence = build_probe_evidence()
    assert [case["name"] for case in evidence["known_cases"]] == [case["name"] for case in KNOWN_CASES]
    for case in evidence["known_cases"]:
        assert case["objective"] == 0.0
        assert case["variables"] == case["known_optimum"]["variables"]
        assert case["evaluations"] == INITIAL_EVALUATION_BUDGET * TOTAL_GENERATIONS
        assert case["repeat_history_equal"] is True
        assert case["passed"] is True


def test_each_traffic_family_has_an_independent_mixed_type_adapter_and_equal_budget() -> None:
    """One categorical family search or unequal initial work would reintroduce family-order bias."""
    evidence = build_probe_evidence()
    family_runs = evidence["family_runs"]
    assert [run["family"] for run in family_runs] == list(FAMILY_NAMES)
    assert len({run["optimizer_id"] for run in family_runs}) == len(FAMILY_NAMES)
    assert all(run["initial_evaluations"] == INITIAL_EVALUATION_BUDGET for run in family_runs)
    assert all(run["total_attempts"] == INITIAL_EVALUATION_BUDGET * TOTAL_GENERATIONS for run in family_runs)
    assert all(run["repeat_history_equal"] is True for run in family_runs)
    assert [run["variable_kinds"] for run in family_runs] == [
        {"q1": "real", "q2": "real", "alpha": "real", "r": "integer", "c_t": "real"},
        {"q01": "real", "q10": "real", "lambda0": "real", "lambda1": "real"},
        {"c_lambda": "real"},
    ]
    assert all("family" not in run["variable_kinds"] for run in family_runs)
    fairness = evidence["fairness"]
    assert fairness["independent_optimizer_count"] == 3
    assert fairness["categorical_family_variable"] is False
    assert fairness["initial_evaluations_by_family"] == dict.fromkeys(FAMILY_NAMES, INITIAL_EVALUATION_BUDGET)
    assert fairness["common_search_seed"] == SEARCH_SEED
    assert fairness["common_trial_seeds"] == list(TRIAL_SEEDS)
    assert fairness["common_champion_seed"] == CHAMPION_SEED
    assert fairness["common_window_seconds"] == WINDOW_SECONDS
    assert fairness["common_generation_limits"] == GENERATION_LIMITS.model_dump(mode="json")
    assert fairness["common_similarity"] == SIMILARITY.model_dump(mode="json")
    assert [item["family"] for item in fairness["champion_comparison"]] == list(FAMILY_NAMES)
    assert fairness["winner_family"] in FAMILY_NAMES


def test_trafficlab_cache_invalid_classification_and_diagnostics_remain_owned() -> None:
    """Counting only pymoo evaluations could hide duplicate work or erase Trafficlab failure evidence."""
    evidence = build_probe_evidence()
    assert evidence["fairness"]["cache_key_fields"] == list(CACHE_KEY_FIELDS)
    for run in evidence["family_runs"]:
        assert run["cache_hits"] >= 1
        assert run["objective_evaluations"] + run["cache_hits"] == run["total_attempts"]
        assert all(item["cache_key"] for item in run["history"])
        assert all(item["status"] in {"valid", "invalid"} for item in run["history"])
        assert all(item["trials"] is not None for item in run["history"])
    invalid = evidence["invalid_classification_case"]
    assert invalid["family"] == "poisson_empirical"
    assert invalid["status"] == "invalid"
    assert invalid["objective"] == 1.0
    assert invalid["failure"]["kind"] == "incomplete_generation"
    assert invalid["failure"]["authority"] == "primary"


def test_public_snapshot_is_transparent_and_replay_reject_is_precise() -> None:
    """A population-only warm start must not be reported as an exact resumable checkpoint."""
    evidence = build_probe_evidence()
    checkpoint = evidence["checkpoint"]
    snapshot = checkpoint["snapshot"]
    assert PublicStateSnapshot.model_validate(snapshot).model_dump(mode="json") == snapshot
    extra_snapshot = deepcopy(snapshot)
    extra_snapshot["opaque_algorithm"] = "forbidden"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PublicStateSnapshot.model_validate(extra_snapshot)
    wrong_generation = deepcopy(snapshot)
    wrong_generation["generation"] = True
    with pytest.raises(ValidationError, match="int_type"):
        PublicStateSnapshot.model_validate(wrong_generation)
    assert tuple(snapshot) == CHECKPOINT_FIELDS
    assert snapshot["pymoo_version"] == "0.6.2"
    assert snapshot["generation"] == 2
    assert snapshot["evaluation_count"] == INITIAL_EVALUATION_BUDGET
    assert snapshot["population"]
    assert all(set(item) == {"variables", "objectives", "status"} for item in snapshot["population"])
    assert snapshot["rng"]["engine"] == "PCG64"
    assert snapshot["rng"]["state"]["bit_generator"] == "PCG64"
    assert snapshot["termination"] == {
        "kind": "MaximumGenerationTermination",
        "progress": 0.5,
        "has_terminated": False,
    }
    comparison = checkpoint["comparison"]
    assert comparison["exact_history_identity"] is False
    assert comparison["uninterrupted_history"]
    assert comparison["resumed_history"] is None
    assert comparison["checked_fields"] == [
        "evaluation_index",
        "family",
        "genes",
        "objective",
        "fitness",
        "status",
        "failure",
        "trials",
        "cache_key",
        "cache_hit",
    ]
    assert comparison["missing_public_restore_fields"] == [
        "algorithm initialization/iteration state",
        "operator state",
        "termination restoration",
    ]
    assert checkpoint["public_api_proof"]["documented_checkpoint_transport"] == "dill algorithm serialization"
    assert checkpoint["public_api_proof"]["opaque_transport_allowed"] is False
    assert checkpoint["public_api_proof"]["transparent_restore_api"] is None
    assert all(not isinstance(item, bytes) for item in _walk(checkpoint))
    assert checkpoint["encoding"] == "canonical-json"


def test_loc_inventory_proves_the_forty_percent_gate_cannot_pass() -> None:
    """Removing diagnostics from the denominator must not manufacture a code-reduction pass."""
    loc = build_probe_evidence()["production_loc"]
    assert loc["current_files"] == [
        "__init__.py",
        "checkpoint.py",
        "coordinates.py",
        "evaluation.py",
        "operators.py",
        "population.py",
        "strategy.py",
        "types.py",
    ]
    assert loc["required_retained_files"] == ["__init__.py", "checkpoint.py", "evaluation.py", "types.py"]
    assert loc["candidate_removable_files"] == ["coordinates.py", "operators.py", "population.py", "strategy.py"]
    assert loc["current_sloc"] == loc["required_retained_sloc"] + loc["candidate_removable_sloc"]
    assert loc["adapter_sloc_assumption"] == 0
    assert loc["estimated_replacement_sloc"] == loc["required_retained_sloc"]
    assert loc["estimated_reduction_percent"] == loc["maximum_reduction_percent_before_adapter"]
    assert loc["estimate_kind"] == "optimistic upper bound assuming a zero-line adapter"
    assert loc["maximum_reduction_percent_before_adapter"] < 40.0
    assert loc["gate_passed"] is False


def test_decision_requires_every_predeclared_gate() -> None:
    """Replay or LOC failure must deterministically retain the production strategy."""
    passing = {
        "known_optima": True,
        "deterministic_repeats": True,
        "family_fairness": True,
        "cache_and_diagnostics": True,
        "exact_public_state_replay": True,
        "production_loc_reduction": True,
    }
    assert decide_probe(passing)["outcome"] == "pass"
    assert decide_probe({**passing, "exact_public_state_replay": False}) == {
        "outcome": "reject",
        "failed_gates": ["exact_public_state_replay"],
        "production_changed": False,
        "production_strategy": "basic_generational",
    }
    assert decide_probe({})["failed_gates"] == list(passing)
    assert decide_probe({**passing, "unexpected": True})["failed_gates"] == ["unknown:unexpected"]
    assert decide_probe({**passing, "known_optima": 1})["failed_gates"] == ["known_optima"]  # type: ignore[dict-item]


def test_evidence_is_deterministic_strict_and_contains_no_opaque_bytes(tmp_path: Path) -> None:
    """Policy drift, mutable output, or binary algorithm state would make the fixture unauditable."""
    evidence = build_probe_evidence()
    assert build_probe_evidence() == evidence
    assert evidence["gates"] == {
        "known_optima": True,
        "deterministic_repeats": True,
        "family_fairness": True,
        "cache_and_diagnostics": True,
        "exact_public_state_replay": False,
        "production_loc_reduction": False,
    }
    assert evidence["decision"] == {
        "outcome": "reject",
        "failed_gates": ["exact_public_state_replay", "production_loc_reduction"],
        "production_changed": False,
        "production_strategy": "basic_generational",
    }
    assert all(not isinstance(item, bytes) for item in _walk(evidence))
    assert validate_probe_evidence(evidence) is evidence
    bad_budget = deepcopy(evidence)
    bad_budget["policy"]["initial_evaluation_budget"] += 1
    with pytest.raises(ValueError, match="policy"):
        validate_probe_evidence(bad_budget)
    bad_decision = deepcopy(evidence)
    bad_decision["decision"]["outcome"] = "pass"
    with pytest.raises(ValueError, match="decision"):
        validate_probe_evidence(bad_decision)
    rendered = render_probe_evidence(evidence)
    assert rendered.endswith(b"\n")
    assert json.loads(rendered) == evidence
    destination = tmp_path / "pymoo_cases.json"
    assert write_probe_evidence(destination, evidence, check=False) is True
    assert destination.read_bytes() == rendered
    assert write_probe_evidence(destination, evidence, check=True) is True
    destination.write_bytes(rendered + b" ")
    assert write_probe_evidence(destination, evidence, check=True) is False


def test_shared_runner_generates_and_checks_the_named_pymoo_probe(tmp_path: Path) -> None:
    """A runner wired only to the earlier MMPP probe cannot reproduce this decision fixture."""
    repository = Path(__file__).resolve().parents[3]
    destination = tmp_path / "pymoo_cases.json"
    command = [
        sys.executable,
        str(repository / "scripts" / "run_scientific_stack_probes.py"),
        "--probe",
        "pymoo",
        "--output",
        str(destination),
    ]
    generated = subprocess.run(command, cwd=tmp_path, check=False, capture_output=True, text=True)
    assert generated.returncode == 0, generated.stderr
    assert json.loads(destination.read_bytes())["probe"] == "pymoo_optimizer"
    checked = subprocess.run([*command, "--check"], cwd=tmp_path, check=False, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
