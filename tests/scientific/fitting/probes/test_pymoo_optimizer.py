"""Scientific gates for the test-only pymoo optimizer adoption probe."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from pydantic import ValidationError

from tests.scientific.fitting.probes.pymoo_optimizer import adapter as pymoo_adapter
from tests.scientific.fitting.probes.pymoo_optimizer import policy as pymoo_policy
from tests.scientific.fitting.probes.pymoo_optimizer import schema as pymoo_schema
from tests.scientific.fitting.probes.pymoo_optimizer.evidence import (
    build_probe_evidence,
    render_probe_evidence,
    validate_probe_evidence,
    write_probe_evidence,
)
from tests.scientific.fitting.probes.pymoo_optimizer.policy import (
    attempt_history_is_complete,
    decide_probe,
    derive_gates,
    semantic_root_is_consistent,
)
from tests.scientific.fitting.probes.pymoo_optimizer.schema import (
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
        assert case["seed"] == SEARCH_SEED
        assert case["objective_definition"] in {"sum_squares", "integer_real_quadratic"}
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
    assert fairness["distinct_optimizer_instances"] == len(FAMILY_NAMES) * 2
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
    hit_index = next(index for index, item in enumerate(run.history) if item.cache_hit)
    hit = run.history[hit_index]
    changed_candidate = hit.candidate.model_copy(update={"fitness": 0.0})
    changed_result = hit.model_copy(update={"candidate": changed_candidate})
    changed_history = (*run.history[:hit_index], changed_result, *run.history[hit_index + 1 :])
    assert attempt_history_is_complete(run.model_copy(update={"history": changed_history})) is False
    duplicate_miss = hit.model_copy(update={"cache_hit": False})
    duplicate_history = (*run.history[:hit_index], duplicate_miss, *run.history[hit_index + 1 :])
    duplicate_run = run.model_copy(
        update={
            "history": duplicate_history,
            "cache_hits": run.cache_hits - 1,
            "objective_evaluations": run.objective_evaluations + 1,
        }
    )
    assert attempt_history_is_complete(duplicate_run) is False

    invalid_source = ProbeEvidence.model_validate(build_probe_evidence()).invalid_classification.history[0].candidate
    invalid_candidate = run.history[0].candidate.model_copy(
        update={
            "status": "invalid",
            "fitness": 0.0,
            "trials": (),
            "invalid": invalid_source.invalid,
        }
    )
    invalid_attempt = run.history[0].model_copy(update={"candidate": invalid_candidate, "objective": 1.0})
    assert attempt_history_is_complete(run.model_copy(update={"history": (invalid_attempt, *run.history[1:])})) is False
    invalid_wrong_objective = invalid_attempt.model_copy(update={"objective": 0.5})
    assert (
        attempt_history_is_complete(run.model_copy(update={"history": (invalid_wrong_objective, *run.history[1:])}))
        is False
    )

    pending_candidate = run.history[-1].candidate.model_copy(
        update={"status": "pending", "fitness": 0.0, "trials": (), "invalid": None}
    )
    pending_attempt = run.history[-1].model_copy(update={"candidate": pending_candidate, "objective": 1.0})
    assert (
        attempt_history_is_complete(run.model_copy(update={"history": (*run.history[:-1], pending_attempt)})) is False
    )

    linked_mismatch_candidate = hit.candidate.model_copy(update={"fitness": hit.candidate.fitness - 0.01})
    linked_mismatch = hit.model_copy(
        update={"candidate": linked_mismatch_candidate, "objective": 1.0 - linked_mismatch_candidate.fitness}
    )
    linked_history = (*run.history[:hit_index], linked_mismatch, *run.history[hit_index + 1 :])
    assert attempt_history_is_complete(run.model_copy(update={"history": linked_history})) is False


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
        "advance_after_initial_infill": False,
        "algorithm_duplicate_elimination": "pymoo.core.duplicate.NoDuplicateElimination",
    }
    assert configuration["initialization"] == {
        "sampling": "pymoo.core.population.Population",
        "repair": "pymoo.core.repair.NoRepair",
        "duplicate_elimination": "pymoo.core.duplicate.NoDuplicateElimination",
    }
    assert configuration["mating"]["selection"] == "pymoo.operators.selection.rnd.RandomSelection"
    assert configuration["mating"]["repair"] == "pymoo.core.repair.NoRepair"
    assert configuration["mating"]["duplicate_elimination"] == {
        "operator_class": "pymoo.core.mixed.MixedVariableDuplicateElimination",
        "epsilon": 1e-16,
    }
    assert configuration["mating"]["n_max_iterations"] == 100
    assert [item["variable_type"] for item in configuration["mating"]["crossover"]] == [
        "pymoo.core.variable.Binary",
        "pymoo.core.variable.Real",
        "pymoo.core.variable.Integer",
        "pymoo.core.variable.Choice",
    ]
    assert [item["variable_type"] for item in configuration["mating"]["mutation"]] == [
        "pymoo.core.variable.Binary",
        "pymoo.core.variable.Real",
        "pymoo.core.variable.Integer",
        "pymoo.core.variable.Choice",
    ]
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
    assert [item["path"] for item in loc["current_inventory"]] == [
        "__init__.py",
        "checkpoint/__init__.py",
        "checkpoint/codec.py",
        "checkpoint/compatibility.py",
        "checkpoint/history.py",
        "checkpoint/schema.py",
        "checkpoint/state.py",
        "coordinates.py",
        "evaluation.py",
        "operators.py",
        "population.py",
        "strategy.py",
        "types.py",
    ]
    assert loc["current_sloc"] == 3_163
    assert "line-level" in loc["reason"]
    assert "rejected adapter" in loc["reason"]
    assert "maximum" not in loc


def test_public_mating_configuration_serializes_exact_classes_and_scalars() -> None:
    mating = build_probe_evidence()["checkpoint"]["snapshot"]["configuration"]["mating"]
    assert [(item["variable_type"], item["operator_class"], item["settings"]) for item in mating["crossover"]] == [
        (
            "pymoo.core.variable.Binary",
            "pymoo.operators.crossover.ux.UX",
            {"prob": 0.9, "n_parents": 2, "n_offsprings": 2},
        ),
        (
            "pymoo.core.variable.Real",
            "pymoo.operators.crossover.sbx.SBX",
            {
                "prob": 0.9,
                "n_parents": 2,
                "n_offsprings": 2,
                "prob_var": 0.5,
                "eta": 15,
                "prob_bin": 0.5,
                "prob_exch": 1.0,
            },
        ),
        (
            "pymoo.core.variable.Integer",
            "pymoo.operators.crossover.sbx.SBX",
            {
                "prob": 0.9,
                "n_parents": 2,
                "n_offsprings": 2,
                "prob_var": 0.5,
                "eta": 15,
                "prob_bin": 0.5,
                "prob_exch": 1.0,
            },
        ),
        (
            "pymoo.core.variable.Choice",
            "pymoo.operators.crossover.ux.UX",
            {"prob": 0.9, "n_parents": 2, "n_offsprings": 2},
        ),
    ]
    assert [(item["variable_type"], item["operator_class"], item["settings"]) for item in mating["mutation"]] == [
        (
            "pymoo.core.variable.Binary",
            "pymoo.operators.mutation.bitflip.BFM",
            {"prob": 1.0, "prob_var": None},
        ),
        (
            "pymoo.core.variable.Real",
            "pymoo.operators.mutation.pm.PM",
            {"prob": 0.9, "prob_var": None, "eta": 20, "at_least_once": False},
        ),
        (
            "pymoo.core.variable.Integer",
            "pymoo.operators.mutation.pm.PM",
            {"prob": 0.9, "prob_var": None, "eta": 20, "at_least_once": False},
        ),
        (
            "pymoo.core.variable.Choice",
            "pymoo.operators.mutation.rm.ChoiceRandomMutation",
            {"prob": 1.0, "prob_var": None},
        ),
    ]


def test_public_config_scalar_and_class_serializers_cover_supported_values() -> None:
    class Getter:
        def __init__(self, value: object) -> None:
            self.value = value

        def get(self) -> object:
            return self.value

    assert pymoo_adapter._qualified_name(Getter) == f"{__name__}.Getter"  # pyright: ignore[reportPrivateUsage]
    assert pymoo_adapter._qualified_name(Getter(1)) == f"{__name__}.Getter"  # pyright: ignore[reportPrivateUsage]
    for value in (None, 1, 1.5, False, np.int64(2), np.float64(2.5)):
        assert pymoo_adapter._operator_scalar(Getter(value)) == value  # pyright: ignore[reportPrivateUsage]
        assert pymoo_adapter._operator_scalar(value) == value  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(TypeError, match="deterministic scalar"):
        pymoo_adapter._operator_scalar(Getter(object()))  # pyright: ignore[reportPrivateUsage]
    assert not hasattr(pymoo_schema, "PYMOO_GA_CLASS")
    assert not hasattr(pymoo_schema, "MIXED_VARIABLE_GA")
    assert not hasattr(pymoo_schema, "POPULATION")
    assert callable(pymoo_adapter.PYMOO_GA_CLASS)
    assert callable(pymoo_adapter.MIXED_VARIABLE_GA)


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


def test_adversarial_second_run_champion_invalid_and_cache_mutations_are_rejected() -> None:
    mutations: list[dict[str, Any]] = []

    second_seed = deepcopy(build_probe_evidence())
    second_seed["families"][0]["runs"][1]["search_seed"] += 1
    second_seed_model = ProbeEvidence.model_validate(second_seed)
    assert derive_gates(second_seed_model).deterministic_repeats is False
    assert derive_gates(second_seed_model).family_fairness is False
    mutations.append(second_seed)

    second_config = deepcopy(build_probe_evidence())
    second_config["families"][0]["runs"][1]["observation_window_seconds"] = 5.5
    second_config_model = ProbeEvidence.model_validate(second_config)
    assert derive_gates(second_config_model).deterministic_repeats is False
    assert derive_gates(second_config_model).family_fairness is False
    mutations.append(second_config)

    early_champion = deepcopy(build_probe_evidence())
    early_champion["fairness"]["champion_comparison"][0]["search_attempts_completed"] = INITIAL_EVALUATION_BUDGET
    assert derive_gates(ProbeEvidence.model_validate(early_champion)).family_fairness is False
    mutations.append(early_champion)

    wrong_winner = deepcopy(build_probe_evidence())
    wrong_winner["fairness"]["winner_family"] = "poisson_empirical"
    assert derive_gates(ProbeEvidence.model_validate(wrong_winner)).family_fairness is False
    mutations.append(wrong_winner)

    wrong_champion = deepcopy(build_probe_evidence())
    wrong_champion["fairness"]["champion_comparison"][0]["fresh_fitness"] = 0.0
    assert derive_gates(ProbeEvidence.model_validate(wrong_champion)).family_fairness is False
    mutations.append(wrong_champion)

    wrong_selected_winner = deepcopy(build_probe_evidence())
    wrong_selected_winner["fairness"]["winner"]["fresh_fitness"] = 0.0
    assert derive_gates(ProbeEvidence.model_validate(wrong_selected_winner)).family_fairness is False
    mutations.append(wrong_selected_winner)

    wrong_ranking = deepcopy(build_probe_evidence())
    wrong_ranking["fairness"]["champion_ranking"].reverse()
    assert derive_gates(ProbeEvidence.model_validate(wrong_ranking)).family_fairness is False
    mutations.append(wrong_ranking)

    wrong_invalid_limits = deepcopy(build_probe_evidence())
    wrong_invalid_limits["invalid_classification"]["limits"]["max_packets"] = 2
    assert derive_gates(ProbeEvidence.model_validate(wrong_invalid_limits)).cache_and_diagnostics is False
    mutations.append(wrong_invalid_limits)

    wrong_invalid_failure = deepcopy(build_probe_evidence())
    wrong_invalid_failure["invalid_classification"]["history"][0]["candidate"]["invalid"]["kind"] = "generation"
    assert derive_gates(ProbeEvidence.model_validate(wrong_invalid_failure)).cache_and_diagnostics is False
    mutations.append(wrong_invalid_failure)

    unlinked_cache_hit = deepcopy(build_probe_evidence())
    history = unlinked_cache_hit["families"][0]["runs"][0]["history"]
    hit = next(item for item in history if item["cache_hit"])
    hit["cache_key_payload"]["genes"][0] += 0.001
    hit["cache_key"] = json.dumps(
        hit["cache_key_payload"],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert derive_gates(ProbeEvidence.model_validate(unlinked_cache_hit)).cache_and_diagnostics is False
    mutations.append(unlinked_cache_hit)

    for evidence in mutations:
        with pytest.raises(ValueError):
            validate_probe_evidence(evidence)


def test_adversarial_policy_known_checkpoint_and_loc_mutations_are_rejected() -> None:
    mutations: list[dict[str, Any]] = []

    reordered_policy = deepcopy(build_probe_evidence())
    reordered_policy["policy"]["family_names"].reverse()
    assert semantic_root_is_consistent(ProbeEvidence.model_validate(reordered_policy)) is False
    mutations.append(reordered_policy)

    wrong_known_kind = deepcopy(build_probe_evidence())
    wrong_known_kind["known_cases"][0]["variable_kinds"]["x1"] = "integer"
    assert semantic_root_is_consistent(ProbeEvidence.model_validate(wrong_known_kind)) is False
    mutations.append(wrong_known_kind)

    wrong_known_bound = deepcopy(build_probe_evidence())
    wrong_known_bound["known_cases"][0]["bounds"]["x0"] = [-3.0, 2.0]
    assert semantic_root_is_consistent(ProbeEvidence.model_validate(wrong_known_bound)) is False
    mutations.append(wrong_known_bound)

    wrong_known_optimum = deepcopy(build_probe_evidence())
    wrong_known_optimum["known_cases"][0]["known_optimum"]["variables"]["x0"] = 0.5
    assert semantic_root_is_consistent(ProbeEvidence.model_validate(wrong_known_optimum)) is False
    mutations.append(wrong_known_optimum)

    wrong_known_tolerance = deepcopy(build_probe_evidence())
    wrong_known_tolerance["known_cases"][0]["tolerance"] = 0.002
    assert semantic_root_is_consistent(ProbeEvidence.model_validate(wrong_known_tolerance)) is False
    mutations.append(wrong_known_tolerance)

    wrong_known_seed = deepcopy(build_probe_evidence())
    wrong_known_seed["known_cases"][0]["seed"] += 1
    assert semantic_root_is_consistent(ProbeEvidence.model_validate(wrong_known_seed)) is False
    mutations.append(wrong_known_seed)

    wrong_known_definition = deepcopy(build_probe_evidence())
    wrong_known_definition["known_cases"][0]["objective_definition"] = "integer_real_quadratic"
    assert semantic_root_is_consistent(ProbeEvidence.model_validate(wrong_known_definition)) is False
    mutations.append(wrong_known_definition)

    wrong_known_evaluations = deepcopy(build_probe_evidence())
    wrong_known_evaluations["known_cases"][0]["runs"][0]["evaluations"] -= 1
    assert derive_gates(ProbeEvidence.model_validate(wrong_known_evaluations)).known_optima is False
    mutations.append(wrong_known_evaluations)

    empty_checked_fields = deepcopy(build_probe_evidence())
    empty_checked_fields["checkpoint"]["comparison"]["checked_fields"] = []
    assert semantic_root_is_consistent(ProbeEvidence.model_validate(empty_checked_fields)) is False
    mutations.append(empty_checked_fields)

    missing_capability = deepcopy(build_probe_evidence())
    missing_capability["checkpoint"]["snapshot"]["missing_fields"].pop()
    assert semantic_root_is_consistent(ProbeEvidence.model_validate(missing_capability)) is False
    mutations.append(missing_capability)

    wrong_operator_setting = deepcopy(build_probe_evidence())
    wrong_operator_setting["checkpoint"]["snapshot"]["configuration"]["mating"]["crossover"][1]["settings"]["eta"] = 16
    assert semantic_root_is_consistent(ProbeEvidence.model_validate(wrong_operator_setting)) is False
    mutations.append(wrong_operator_setting)

    wrong_loc_total = deepcopy(build_probe_evidence())
    wrong_loc_total["production_loc"]["current_sloc"] += 1
    assert semantic_root_is_consistent(ProbeEvidence.model_validate(wrong_loc_total)) is False
    mutations.append(wrong_loc_total)

    wrong_loc_path = deepcopy(build_probe_evidence())
    wrong_loc_path["production_loc"]["current_inventory"][0]["path"] = "other.py"
    assert semantic_root_is_consistent(ProbeEvidence.model_validate(wrong_loc_path)) is False
    mutations.append(wrong_loc_path)

    for evidence in mutations:
        with pytest.raises(ValueError):
            validate_probe_evidence(evidence)


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


def test_known_variable_semantic_helper_covers_names_types_and_bounds() -> None:
    model = ProbeEvidence.model_validate(build_probe_evidence())
    continuous, mixed = model.known_cases
    assert (
        pymoo_policy._known_variables_are_valid(  # pyright: ignore[reportPrivateUsage]
            continuous, continuous.runs[0].variables
        )
        is True
    )
    assert pymoo_policy._known_variables_are_valid(continuous, {"x0": 0.0}) is False  # pyright: ignore[reportPrivateUsage]
    assert pymoo_policy._known_variables_are_valid(continuous, {"x0": 0, "x1": 0.0}) is False  # pyright: ignore[reportPrivateUsage]
    assert pymoo_policy._known_variables_are_valid(mixed, {"count": 3.0, "scale": 1.25}) is False  # pyright: ignore[reportPrivateUsage]
    assert pymoo_policy._known_variables_are_valid(continuous, {"x0": 3.0, "x1": 0.0}) is False  # pyright: ignore[reportPrivateUsage]


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
    assert rendered == (json.dumps(evidence, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    assert json.loads(rendered) == evidence
    destination = tmp_path / "pymoo_cases.json"
    assert write_probe_evidence(destination, evidence, check=True) is False
    assert write_probe_evidence(destination, evidence, check=False) is True
    assert destination.read_bytes() == rendered
    assert write_probe_evidence(destination, evidence, check=True) is True
    destination.write_bytes(rendered + b" ")
    assert write_probe_evidence(destination, evidence, check=True) is False


def test_shared_runner_generates_and_checks_the_named_pymoo_probe(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[4]
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


def test_round3_shifted_optimizer_ordinals_are_rejected() -> None:
    evidence = deepcopy(build_probe_evidence())
    for family in evidence["families"]:
        for run in family["runs"]:
            run["optimizer_instance"] += 10
    with pytest.raises(ValueError):
        validate_probe_evidence(evidence)


def test_round3_drifting_both_mmpp_variable_configs_is_rejected() -> None:
    evidence = deepcopy(build_probe_evidence())
    mmpp = next(item for item in evidence["families"] if item["family"] == "mmpp")
    for run in mmpp["runs"]:
        run["variables"][0]["lower"] = 0.2
    with pytest.raises(ValueError):
        validate_probe_evidence(evidence)


def test_round3_cache_payload_control_drift_is_rejected() -> None:
    evidence = deepcopy(build_probe_evidence())
    runs = evidence["families"][0]["runs"]
    for run in runs:
        for attempt in run["history"]:
            attempt["cache_key_payload"]["observation_window_seconds"] = 5.5
            attempt["cache_key"] = json.dumps(
                attempt["cache_key_payload"],
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
    evidence["checkpoint"]["comparison"]["uninterrupted_history"] = deepcopy(runs[0]["history"])
    with pytest.raises(ValueError):
        validate_probe_evidence(evidence)


def test_round3_objective_fitness_mismatch_is_rejected() -> None:
    evidence = deepcopy(build_probe_evidence())
    runs = evidence["families"][0]["runs"]
    for run in runs:
        run["history"][-1]["objective"] += 0.01
    evidence["checkpoint"]["comparison"]["uninterrupted_history"] = deepcopy(runs[0]["history"])
    with pytest.raises(ValueError):
        validate_probe_evidence(evidence)


def test_round3_replaced_invalid_cache_payload_is_rejected() -> None:
    evidence = deepcopy(build_probe_evidence())
    attempt = evidence["invalid_classification"]["history"][0]
    attempt["cache_key_payload"]["family"] = "mmpp"
    attempt["cache_key"] = json.dumps(
        attempt["cache_key_payload"],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    with pytest.raises(ValueError):
        validate_probe_evidence(evidence)


def test_round3_later_known_generation_objective_corruption_is_rejected() -> None:
    evidence = deepcopy(build_probe_evidence())
    for run in evidence["known_cases"][0]["runs"]:
        run["history"][1]["population"][0]["objective"] += 1.0
    with pytest.raises(ValueError):
        validate_probe_evidence(evidence)


def test_round3_checkpoint_generation_drift_is_rejected() -> None:
    evidence = deepcopy(build_probe_evidence())
    evidence["checkpoint"]["snapshot"]["generation"] += 1
    with pytest.raises(ValueError):
        validate_probe_evidence(evidence)


def test_round3_constructor_population_size_drift_is_rejected() -> None:
    evidence = deepcopy(build_probe_evidence())
    evidence["checkpoint"]["snapshot"]["configuration"]["constructor"]["pop_size"] += 1
    with pytest.raises(ValueError):
        validate_probe_evidence(evidence)


def test_round3_operator_vtype_and_repair_configuration_is_complete() -> None:
    configuration = build_probe_evidence()["checkpoint"]["snapshot"]["configuration"]
    assert configuration["algorithm_repair"] == {
        "operator_class": "pymoo.core.repair.NoRepair",
        "name": "NoRepair",
        "vtype": None,
        "repair": None,
    }
    for group in ("crossover", "mutation"):
        operators = configuration["mating"][group]
        for operator in operators:
            if operator["variable_type"] == "pymoo.core.variable.Integer":
                assert operator["vtype"] == "builtins.float"
                assert operator["repair"] == {
                    "operator_class": "pymoo.operators.repair.rounding.RoundingRepair",
                    "name": "RoundingRepair",
                    "vtype": None,
                    "repair": None,
                }
            else:
                assert operator["vtype"] is None
                assert operator["repair"] is None


def test_round4_invalid_adapter_nonzero_fitness_is_rejected() -> None:
    evidence = deepcopy(build_probe_evidence())
    evidence["invalid_classification"]["history"][0]["candidate"]["fitness"] = 0.25
    with pytest.raises(ValueError):
        validate_probe_evidence(evidence)


def test_round4_invalid_adapter_trials_are_rejected() -> None:
    with_trials = deepcopy(build_probe_evidence())
    poisson_trial = with_trials["families"][2]["runs"][0]["history"][0]["candidate"]["trials"][0]
    with_trials["invalid_classification"]["history"][0]["candidate"]["trials"] = [poisson_trial]
    with pytest.raises(ValueError):
        validate_probe_evidence(with_trials)


def test_round4_invalid_adapter_secondary_failure_is_rejected() -> None:
    secondary = deepcopy(build_probe_evidence())
    secondary["invalid_classification"]["history"][0]["candidate"]["invalid"]["authority"] = "secondary"
    with pytest.raises(ValueError):
        validate_probe_evidence(secondary)


def test_round4_checkpoint_population_must_match_declared_initial_sampling() -> None:
    evidence = deepcopy(build_probe_evidence())
    runs = evidence["families"][0]["runs"]
    for run in runs:
        for index in (0, 1):
            attempt = run["history"][index]
            attempt["candidate"]["genes"][0] = 0.21
            attempt["cache_key_payload"]["genes"][0] = 0.21
            attempt["cache_key"] = json.dumps(
                attempt["cache_key_payload"],
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
    evidence["checkpoint"]["comparison"]["uninterrupted_history"] = deepcopy(runs[0]["history"])
    for index in (0, 1):
        evidence["checkpoint"]["snapshot"]["population"][index]["variables"]["q1"] = 0.21

    assert evidence["checkpoint"]["snapshot"]["configuration"]["initial_sampling"][0]["q1"] == 0.2
    with pytest.raises(ValueError):
        validate_probe_evidence(evidence)
