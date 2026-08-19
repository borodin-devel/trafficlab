"""Scientific gates for the test-only pymoo optimizer adoption probe."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from tests.scientific.probes.pymoo_optimizer import (
    CACHE_KEY_FIELDS,
    CHAMPION_SEED,
    CHECKPOINT_FIELDS,
    CONTINUOUS_INITIAL_SAMPLES,
    FAMILY_NAMES,
    GENERATION_LIMITS,
    INITIAL_EVALUATION_BUDGET,
    KNOWN_CASES,
    KNOWN_GENERATIONS,
    KNOWN_POPULATION_SIZE,
    KNOWN_TOLERANCES,
    MIXED_INITIAL_SAMPLES,
    PROHIBITED_SERIALIZERS,
    REPLAY_MISSING_FIELDS,
    SEARCH_SEED,
    SIMILARITY,
    TOTAL_GENERATIONS,
    TRIAL_SEEDS,
    WINDOW_SECONDS,
    ProbeEvidence,
    PublicStateSnapshot,
    VariableSpec,
    attempt_history_is_complete,
    build_probe_evidence,
    decide_probe,
    derive_gates,
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


def _objective(case_name: str, variables: Mapping[str, int | float] | tuple[float, float]) -> float:
    if case_name == "bounded_continuous_sphere":
        if isinstance(variables, tuple):
            return variables[0] ** 2 + variables[1] ** 2
        return float(variables["x0"]) ** 2 + float(variables["x1"]) ** 2
    assert not isinstance(variables, tuple)
    return (int(variables["count"]) - 3) ** 2 + (float(variables["scale"]) - 1.25) ** 2


def test_revised_known_case_policy_is_fixed_and_excludes_both_optima() -> None:
    """Seeding an analytical optimum would test retention rather than optimizer behavior."""
    assert KNOWN_POPULATION_SIZE == 20
    assert KNOWN_GENERATIONS == 40
    assert KNOWN_TOLERANCES == {
        "bounded_continuous_sphere": 0.001,
        "mixed_integer_real_quadratic": 0.001,
    }
    assert len(CONTINUOUS_INITIAL_SAMPLES) == KNOWN_POPULATION_SIZE
    assert len(MIXED_INITIAL_SAMPLES) == KNOWN_POPULATION_SIZE
    assert CONTINUOUS_INITIAL_SAMPLES[:4] == (
        (-1.8, -1.8),
        (-1.8, -0.9),
        (-1.8, 0.9),
        (-1.8, 1.8),
    )
    assert MIXED_INITIAL_SAMPLES[:4] == (
        {"count": 0, "scale": 0.6},
        {"count": 1, "scale": 0.8},
        {"count": 2, "scale": 1.0},
        {"count": 3, "scale": 1.5},
    )
    for case, samples in zip(KNOWN_CASES, (CONTINUOUS_INITIAL_SAMPLES, MIXED_INITIAL_SAMPLES), strict=True):
        tolerance = KNOWN_TOLERANCES[case["name"]]
        assert min(_objective(case["name"], sample) for sample in samples) > tolerance
        assert case["known_optimum"]["variables"] not in samples


def test_common_traffic_policy_remains_predeclared_and_family_neutral() -> None:
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
    assert PROHIBITED_SERIALIZERS == ("dill", "pickle", "cloudpickle")


def test_known_cases_start_outside_tolerance_and_converge_through_pymoo() -> None:
    evidence = build_probe_evidence()
    for case in evidence["known_cases"]:
        assert len(case["runs"]) == 2
        first, second = case["runs"]
        assert first == second
        assert first["initial_minimum_objective"] > case["tolerance"]
        assert first["objective"] <= case["tolerance"]
        assert first["evaluations"] == KNOWN_POPULATION_SIZE * KNOWN_GENERATIONS
        assert len(first["history"]) == KNOWN_GENERATIONS
        assert first["history"][0]["minimum_objective"] == first["initial_minimum_objective"]


def test_family_runs_retain_two_complete_repeat_histories_with_fresh_attempt_ids() -> None:
    evidence = build_probe_evidence()
    assert [item["family"] for item in evidence["families"]] == list(FAMILY_NAMES)
    first_instance_ids: set[int] = set()
    for family in evidence["families"]:
        assert len(family["runs"]) == 2
        first, second = family["runs"]
        first_instance_ids.add(first["optimizer_instance"])
        assert first["history"] == second["history"]
        for run in (first, second):
            assert run["initial_evaluations"] == INITIAL_EVALUATION_BUDGET
            assert run["total_attempts"] == INITIAL_EVALUATION_BUDGET * TOTAL_GENERATIONS
            assert run["objective_evaluations"] + run["cache_hits"] == run["total_attempts"]
            identifiers = [tuple(attempt["candidate"]["identifier"].values()) for attempt in run["history"]]
            assert len(identifiers) == len(set(identifiers)) == run["total_attempts"]
            assert [attempt["evaluation_index"] for attempt in run["history"]] == list(
                range(1, run["total_attempts"] + 1)
            )
            for attempt in run["history"]:
                assert set(attempt["candidate"]) == {
                    "identifier",
                    "family",
                    "genes",
                    "status",
                    "fitness",
                    "trials",
                    "invalid",
                    "duplicate_diagnostics",
                }
                assert set(attempt["cache_key_payload"]) == set(CACHE_KEY_FIELDS)
        assert first["cache_hits"] >= 1
    assert len(first_instance_ids) == len(FAMILY_NAMES)


def test_invalid_classification_runs_through_real_pymoo_adapter() -> None:
    invalid = build_probe_evidence()["invalid_classification"]
    assert invalid["execution"] == "pymoo.MixedVariableGA.next"
    assert invalid["pymoo_evaluations"] == 1
    assert invalid["limits"]["max_packets"] == 1
    assert len(invalid["history"]) == 1
    attempt = invalid["history"][0]
    assert attempt["candidate"]["status"] == "invalid"
    assert attempt["candidate"]["invalid"]["kind"] == "incomplete_generation"
    assert attempt["candidate"]["invalid"]["authority"] == "primary"
    assert attempt["objective"] == 1.0
    assert attempt["cache_hit"] is False


def test_every_gate_is_recomputed_from_measured_evidence() -> None:
    evidence = build_probe_evidence()
    model = ProbeEvidence.model_validate(evidence)
    assert derive_gates(model).model_dump(mode="json") == evidence["gates"]
    assert evidence["gates"] == {
        "known_optima": True,
        "deterministic_repeats": True,
        "family_fairness": True,
        "cache_and_diagnostics": True,
        "exact_public_state_replay": False,
        "production_loc_reduction": False,
    }
    fairness = evidence["fairness"]
    assert fairness["measured_family_set"] == list(FAMILY_NAMES)
    assert fairness["distinct_optimizer_instances"] == len(FAMILY_NAMES)
    assert fairness["champions_compared_after_attempts"] == INITIAL_EVALUATION_BUDGET * TOTAL_GENERATIONS
    assert [champion["fresh_seed"] for champion in fairness["champion_comparison"]] == [CHAMPION_SEED] * 3


def test_gate_derivation_rejects_each_bad_measurement_and_covers_positive_formulas() -> None:
    model = ProbeEvidence.model_validate(build_probe_evidence())

    known_case = model.known_cases[0]
    bad_known_run = known_case.runs[0].model_copy(update={"objective": known_case.tolerance + 1.0})
    bad_known_case = known_case.model_copy(update={"runs": (bad_known_run, known_case.runs[1])})
    bad_known = model.model_copy(update={"known_cases": (bad_known_case, model.known_cases[1])})
    assert derive_gates(bad_known).known_optima is False

    seeded_optimum = known_case.model_copy(
        update={
            "initial_sampling": (
                known_case.known_optimum.variables,
                *known_case.initial_sampling[1:],
            )
        }
    )
    seeded_model = model.model_copy(update={"known_cases": (seeded_optimum, model.known_cases[1])})
    assert derive_gates(seeded_model).known_optima is False

    family = model.families[0]
    changed_repeat = family.runs[1].model_copy(update={"history": family.runs[1].history[:-1]})
    repeat_family = family.model_copy(update={"runs": (family.runs[0], changed_repeat)})
    bad_repeat = model.model_copy(update={"families": (repeat_family, *model.families[1:])})
    assert derive_gates(bad_repeat).deterministic_repeats is False

    second_family = model.families[1]
    duplicate_instance = second_family.runs[0].model_copy(
        update={"optimizer_instance": model.families[0].runs[0].optimizer_instance}
    )
    unfair_family = second_family.model_copy(update={"runs": (duplicate_instance, second_family.runs[1])})
    unfair = model.model_copy(update={"families": (model.families[0], unfair_family, model.families[2])})
    assert derive_gates(unfair).family_fairness is False

    bad_attempt = family.runs[0].history[0].model_copy(update={"cache_key": "wrong"})
    bad_cache_run = family.runs[0].model_copy(update={"history": (bad_attempt, *family.runs[0].history[1:])})
    bad_cache_family = family.model_copy(update={"runs": (bad_cache_run, family.runs[1])})
    bad_cache = model.model_copy(update={"families": (bad_cache_family, *model.families[1:])})
    assert derive_gates(bad_cache).cache_and_diagnostics is False

    comparison = model.checkpoint.comparison.model_copy(
        update={
            "exact_history_identity": True,
            "resumed_history": model.checkpoint.comparison.uninterrupted_history,
        }
    )
    snapshot = model.checkpoint.snapshot.model_copy(update={"complete": True, "missing_fields": ()})
    replay_checkpoint = model.checkpoint.model_copy(update={"snapshot": snapshot, "comparison": comparison})
    replay_model = model.model_copy(update={"checkpoint": replay_checkpoint})
    assert derive_gates(replay_model).exact_public_state_replay is True

    measured_loc = model.production_loc.model_copy(update={"status": "measured", "estimated_reduction_percent": 40.0})
    loc_model = model.model_copy(update={"production_loc": measured_loc})
    assert derive_gates(loc_model).production_loc_reduction is True


def test_history_completeness_helper_covers_each_rejected_mutation() -> None:
    run = ProbeEvidence.model_validate(build_probe_evidence()).families[0].runs[0]
    assert attempt_history_is_complete(run) is True
    assert attempt_history_is_complete(run.model_copy(update={"history": run.history[:-1]})) is False
    wrong_index = run.history[0].model_copy(update={"evaluation_index": 2})
    assert attempt_history_is_complete(run.model_copy(update={"history": (wrong_index, *run.history[1:])})) is False
    wrong_generation = run.history[0].model_copy(update={"generation": 1})
    assert (
        attempt_history_is_complete(run.model_copy(update={"history": (wrong_generation, *run.history[1:])})) is False
    )
    reused_id = run.history[1].model_copy(update={"candidate": run.history[0].candidate})
    assert (
        attempt_history_is_complete(run.model_copy(update={"history": (run.history[0], reused_id, *run.history[2:])}))
        is False
    )
    assert attempt_history_is_complete(run.model_copy(update={"cache_hits": run.cache_hits + 1})) is False
    assert (
        attempt_history_is_complete(run.model_copy(update={"objective_evaluations": run.objective_evaluations + 1}))
        is False
    )
    bad_key = run.history[0].model_copy(update={"cache_key": "wrong"})
    assert attempt_history_is_complete(run.model_copy(update={"history": (bad_key, *run.history[1:])})) is False


def test_checkpoint_is_explicitly_incomplete_and_records_replay_inputs() -> None:
    checkpoint = build_probe_evidence()["checkpoint"]
    snapshot = checkpoint["snapshot"]
    assert PublicStateSnapshot.model_validate(snapshot).model_dump(mode="json") == snapshot
    assert tuple(snapshot) == CHECKPOINT_FIELDS
    assert snapshot["complete"] is False
    assert snapshot["missing_fields"] == list(REPLAY_MISSING_FIELDS)
    configuration = snapshot["configuration"]
    assert configuration["search_seed"] == SEARCH_SEED
    assert configuration["initial_sampling"]
    assert configuration["constructor"] == {
        "pop_size": INITIAL_EVALUATION_BUDGET,
        "n_offsprings": None,
        "sampling": "pymoo.core.population.Population",
        "mating": "pymoo.core.mixed.MixedVariableMating",
        "eliminate_duplicates": False,
        "survival": "pymoo.algorithms.soo.nonconvex.ga.FitnessSurvival",
        "output": "pymoo.util.display.single.SingleObjectiveOutput",
        "callback": "pymoo.core.callback.Callback",
        "display": "pymoo.util.display.display.Display",
        "archive": None,
        "return_least_infeasible": False,
        "save_history": False,
        "verbose": False,
        "evaluator": "pymoo.core.evaluator.Evaluator",
    }
    assert configuration["termination"] == {"kind": "n_gen", "value": TOTAL_GENERATIONS}
    assert [item["name"] for item in configuration["variables"]] == ["q1", "q2", "alpha", "r", "c_t"]
    assert checkpoint["comparison"]["exact_history_identity"] is False
    assert checkpoint["comparison"]["resumed_history"] is None
    assert all(not isinstance(item, bytes) for item in _walk(checkpoint))


def test_loc_gate_is_indeterminate_without_designing_the_rejected_adapter() -> None:
    loc = build_probe_evidence()["production_loc"]
    assert loc["status"] == "indeterminate"
    assert loc["estimated_reduction_percent"] is None
    assert loc["required_reduction_percent"] == 40.0
    assert loc["gate_passed"] is False
    assert loc["current_inventory"]
    assert "line-level" in loc["reason"]
    assert "rejected adapter" in loc["reason"]
    assert "maximum" not in loc


@pytest.mark.parametrize(
    "path",
    [
        ("known_cases",),
        ("families",),
        ("fairness",),
        ("checkpoint",),
        ("invalid_classification",),
        ("production_loc",),
        ("known_cases", 0),
        ("families", 0, "runs", 0, "history"),
    ],
)
def test_strict_root_rejects_deleted_evidence_sections(path: tuple[str | int, ...]) -> None:
    evidence = deepcopy(build_probe_evidence())
    parent: Any = evidence
    for key in path[:-1]:
        parent = parent[key]
    del parent[path[-1]]
    with pytest.raises(ValueError):
        validate_probe_evidence(evidence)


def test_validation_is_order_independent_but_recomputes_gates_and_decision() -> None:
    evidence = build_probe_evidence()
    evidence["gates"] = dict(reversed(tuple(evidence["gates"].items())))
    assert validate_probe_evidence(evidence) is evidence
    bad_gate = deepcopy(evidence)
    bad_gate["gates"]["known_optima"] = False
    with pytest.raises(ValueError, match="derived gates"):
        validate_probe_evidence(bad_gate)
    bad_decision = deepcopy(evidence)
    bad_decision["decision"]["failed_gates"] = []
    with pytest.raises(ValueError, match="decision"):
        validate_probe_evidence(bad_decision)
    unknown_gate = deepcopy(evidence)
    unknown_gate["gates"]["unexpected"] = True
    with pytest.raises(ValueError):
        validate_probe_evidence(unknown_gate)
    extra_root = deepcopy(evidence)
    extra_root["opaque_state"] = "forbidden"
    with pytest.raises(ValueError):
        validate_probe_evidence(extra_root)
    noncanonical = deepcopy(build_probe_evidence())
    noncanonical["policy"]["family_names"] = tuple(noncanonical["policy"]["family_names"])
    with pytest.raises(ValueError, match="canonical value form"):
        validate_probe_evidence(noncanonical)


def test_strict_schema_rejects_bad_bounds_cardinality_and_evidence_sets() -> None:
    with pytest.raises(ValidationError, match="exact integers"):
        VariableSpec(name="r", kind="integer", lower=1.0, upper=4.0)
    with pytest.raises(ValidationError, match="exact floats"):
        VariableSpec(name="x", kind="real", lower=1, upper=4)
    with pytest.raises(ValidationError, match="less than upper"):
        VariableSpec(name="x", kind="real", lower=2.0, upper=1.0)

    wrong_count = deepcopy(build_probe_evidence())
    wrong_count["families"][0]["runs"][0]["total_attempts"] += 1
    with pytest.raises(ValueError, match="history length"):
        validate_probe_evidence(wrong_count)
    wrong_known_set = deepcopy(build_probe_evidence())
    wrong_known_set["known_cases"].reverse()
    with pytest.raises(ValueError, match="known case set"):
        validate_probe_evidence(wrong_known_set)
    wrong_family_set = deepcopy(build_probe_evidence())
    wrong_family_set["families"].reverse()
    with pytest.raises(ValueError, match="traffic family set"):
        validate_probe_evidence(wrong_family_set)


def test_decision_requires_exact_boolean_gate_membership() -> None:
    passing: dict[str, object] = dict.fromkeys(
        (
            "known_optima",
            "deterministic_repeats",
            "family_fairness",
            "cache_and_diagnostics",
            "exact_public_state_replay",
            "production_loc_reduction",
        ),
        True,
    )
    assert decide_probe(passing)["outcome"] == "pass"
    assert decide_probe({**passing, "exact_public_state_replay": False})["failed_gates"] == [
        "exact_public_state_replay"
    ]
    assert decide_probe({})["outcome"] == "reject"
    assert decide_probe({**passing, "unexpected": True})["failed_gates"] == ["unknown:unexpected"]
    assert decide_probe({**passing, "known_optima": 1})["failed_gates"] == ["known_optima"]  # type: ignore[dict-item]


def test_canonical_evidence_is_deterministic_and_checkable(tmp_path: Path) -> None:
    evidence = build_probe_evidence()
    assert build_probe_evidence() == evidence
    assert validate_probe_evidence(evidence) is evidence
    rendered = render_probe_evidence(evidence)
    assert rendered.endswith(b"\n")
    assert json.loads(rendered) == evidence
    destination = tmp_path / "pymoo_cases.json"
    assert write_probe_evidence(destination, evidence, check=True) is False
    assert write_probe_evidence(destination, evidence, check=False) is True
    assert destination.read_bytes() == rendered
    assert write_probe_evidence(destination, evidence, check=True) is True
    destination.write_bytes(rendered + b" ")
    assert write_probe_evidence(destination, evidence, check=True) is False


def test_shared_runner_generates_and_checks_the_named_pymoo_probe(tmp_path: Path) -> None:
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
    checked = subprocess.run([*command, "--check"], cwd=tmp_path, check=False, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
