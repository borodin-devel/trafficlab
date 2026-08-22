"""Scientific gates for the test-only SciPy two-state MMPP likelihood probe."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from tests.scientific.fitting.probes.mmpp_likelihood import fit as probe
from tests.scientific.fitting.probes.mmpp_likelihood import likelihood as likelihood_probe
from tests.scientific.fitting.probes.mmpp_likelihood import schema as mmpp_schema
from tests.scientific.fitting.probes.mmpp_likelihood.evidence import (
    build_probe_evidence,
    render_probe_evidence,
    validate_probe_evidence,
    write_probe_evidence,
)
from tests.scientific.fitting.probes.mmpp_likelihood.fit import decide_probe, fit_mmpp_likelihood
from tests.scientific.fitting.probes.mmpp_likelihood.likelihood import (
    COMMON_START_RATES,
    decode_rates,
    likelihood_evaluation_count,
    mmpp_log_likelihood,
    simulation_evaluation_count,
)
from tests.scientific.fitting.probes.mmpp_likelihood.schema import (
    AGGREGATE_GATE_NAMES,
    EVALUATION_BUDGET,
    EXTREME_CASES,
    HAND_CASES,
    INVALID_OBJECTIVE,
    LIKELIHOOD_ATOL,
    LIKELIHOOD_POLISH,
    LIKELIHOOD_TOL,
    LIKELIHOOD_UPDATING,
    LIKELIHOOD_WORKERS,
    OPTIMIZER_STARTS,
    PROBE_BOUNDS,
    PRODUCTION_BOUNDS,
    PRODUCTION_DUPLICATE_MUTATION_ATTEMPTS,
    PRODUCTION_ELITE_COUNT,
    PRODUCTION_GENERATIONS,
    PRODUCTION_POPULATION_SIZE,
    PRODUCTION_TOURNAMENT_SIZE,
    RECOVERY_SEEDS,
    RECOVERY_TOLERANCES,
    TRIAL_PLANS,
    TRUE_RATES,
    LikelihoodEvaluation,
    SimulationCandidateHistory,
    SimulationGenerationHistory,
)
from trafficlab.fitting.genetic.types import Candidate, CandidateFailure, CandidateId, FamilyPriority


def test_hand_likelihoods_match_independent_high_precision_literals() -> None:
    """A wrong initial law, matrix order, or censoring term changes these literals."""
    assert HAND_CASES == (
        {
            "name": "arrival_epoch_two_iats_with_censoring",
            "rates": (1.0, 3.0, 1.0, 9.0),
            "iats": (0.2, 0.7),
            "terminal_silence": 0.4,
            "expected_log_likelihood": -2.2108555447313237991950094200043425283,
        },
        {
            "name": "zero_iat_without_terminal_silence",
            "rates": (0.5, 2.0, 3.0, 5.0),
            "iats": (0.0, 0.125),
            "terminal_silence": 0.0,
            "expected_log_likelihood": 2.0977643189333293923625447526341699828,
        },
    )
    for case in HAND_CASES:
        observed = mmpp_log_likelihood(case["iats"], case["terminal_silence"], case["rates"])
        assert observed == pytest.approx(case["expected_log_likelihood"], rel=0.0, abs=1e-12)


def test_terminal_silence_is_an_explicit_survival_factor() -> None:
    """Dropping right censoring would make both likelihood calls identical."""
    rates = (1.0, 3.0, 1.0, 9.0)
    uncensored = mmpp_log_likelihood((0.2, 0.7), 0.0, rates)
    censored = mmpp_log_likelihood((0.2, 0.7), 0.4, rates)
    assert censored < uncensored
    assert censored == pytest.approx(-2.2108555447313238, rel=0.0, abs=1e-12)


def test_extreme_valid_rates_have_finite_scaled_likelihoods() -> None:
    """Omitting per-arrival scaling underflows or overflows these declared cases."""
    assert EXTREME_CASES == (
        {
            "name": "small_rates_long_intervals",
            "rates": (1e-8, 2e-8, 1e-6, 5e-6),
            "iats": (0.0, 100_000.0, 200_000.0),
            "terminal_silence": 300_000.0,
        },
        {
            "name": "large_rates_short_intervals",
            "rates": (100_000.0, 200_000.0, 500_000.0, 2_000_000.0),
            "iats": (1e-7, 2e-6, 0.0),
            "terminal_silence": 1e-6,
        },
        {
            "name": "widely_separated_rates",
            "rates": (1e-3, 1e3, 1e-2, 1e4),
            "iats": (1e-4, 0.1, 1.0),
            "terminal_silence": 0.25,
        },
    )
    for case in EXTREME_CASES:
        assert math.isfinite(mmpp_log_likelihood(case["iats"], case["terminal_silence"], case["rates"]))


@pytest.mark.parametrize(
    ("iats", "terminal_silence", "rates"),
    [
        ((-0.1,), 0.0, (1.0, 3.0, 1.0, 9.0)),
        ((math.nan,), 0.0, (1.0, 3.0, 1.0, 9.0)),
        ((0.1,), -0.1, (1.0, 3.0, 1.0, 9.0)),
        ((0.1,), math.inf, (1.0, 3.0, 1.0, 9.0)),
        ((0.1,), 0.0, (0.0, 3.0, 1.0, 9.0)),
        ((0.1,), 0.0, (1.0, 3.0, 9.0, 1.0)),
    ],
)
def test_likelihood_rejects_invalid_observations_and_rates(
    iats: tuple[float, ...], terminal_silence: float, rates: tuple[float, float, float, float]
) -> None:
    """Invalid observations and unordered rates have no finite MMPP likelihood."""
    with pytest.raises(ValueError):
        mmpp_log_likelihood(iats, terminal_silence, rates)


def test_transformed_decoder_preserves_finite_named_bounds_and_rate_gap() -> None:
    """Optimization coordinates must never decode to a nonpositive or label-swapped model."""
    assert PROBE_BOUNDS.q01 == (0.1, 4.0)
    assert PROBE_BOUNDS.q10 == (0.2, 6.0)
    assert PROBE_BOUNDS.lambda0 == (0.5, 3.0)
    assert PROBE_BOUNDS.lambda1 == (4.0, 12.0)
    for coordinates in ((0.0, 0.0, 0.0, 0.0), (1.0, 1.0, 1.0, 1.0), *OPTIMIZER_STARTS):
        q01, q10, lambda0, lambda1 = decode_rates(coordinates, PROBE_BOUNDS)
        assert PROBE_BOUNDS.q01[0] <= q01 <= PROBE_BOUNDS.q01[1]
        assert PROBE_BOUNDS.q10[0] <= q10 <= PROBE_BOUNDS.q10[1]
        assert PROBE_BOUNDS.lambda0[0] <= lambda0 <= PROBE_BOUNDS.lambda0[1]
        assert PROBE_BOUNDS.lambda1[0] <= lambda1 <= PROBE_BOUNDS.lambda1[1]
        assert 0.0 < lambda0 < lambda1


def test_optimizer_is_deterministic_and_capped_at_the_predeclared_budget() -> None:
    """A changed start population, RNG engine, or evaluation cap changes the fitted result."""
    iats = (0.03, 0.8, 0.12, 0.05, 0.7, 0.2, 0.04, 0.6, 0.15, 0.09) * 4
    first = fit_mmpp_likelihood(iats, 0.25, PROBE_BOUNDS, seed=RECOVERY_SEEDS[0])
    second = fit_mmpp_likelihood(iats, 0.25, PROBE_BOUNDS, seed=RECOVERY_SEEDS[0])
    assert first == second
    assert likelihood_evaluation_count(first.history) == EVALUATION_BUDGET
    assert first.evaluations == len(first.history) == EVALUATION_BUDGET
    assert first.evaluations <= EVALUATION_BUDGET
    assert math.isfinite(first.log_likelihood)
    assert first.rates[2] < first.rates[3]
    assert tuple(item.rates for item in first.history[: len(OPTIMIZER_STARTS)]) == first.starts == COMMON_START_RATES
    assert tuple(item.evaluation_index for item in first.history) == tuple(range(1, EVALUATION_BUDGET + 1))
    for item in first.history:
        assert item.rates == decode_rates(item.coordinates, PROBE_BOUNDS)
        assert item.status == "valid"
        assert item.objective is not None
        assert item.log_likelihood is not None
        assert item.objective == -item.log_likelihood
        assert item.failure is None


def test_optimizer_history_retains_invalid_objective_evaluations(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed likelihood call still needs a finite objective event and failure classification."""

    def fail_exponential(_matrix: object) -> object:
        raise ValueError("controlled matrix failure")

    monkeypatch.setattr(likelihood_probe, "expm", fail_exponential)
    result = fit_mmpp_likelihood((0.1,), 0.2, PROBE_BOUNDS, seed=RECOVERY_SEEDS[0])
    assert result.history
    assert result.evaluations == likelihood_evaluation_count(result.history)
    for item in result.history:
        assert item.status == "invalid"
        assert item.rates == decode_rates(item.coordinates, PROBE_BOUNDS)
        assert item.objective == INVALID_OBJECTIVE
        assert item.log_likelihood is None
        assert item.failure == "ValueError: controlled matrix failure"


def test_probe_policy_is_predeclared_before_results() -> None:
    """Changing seeds, tolerances, starts, or budgets after seeing outcomes invalidates the probe."""
    assert TRUE_RATES == (0.7, 1.9, 1.2, 7.5)
    assert RECOVERY_SEEDS == (4101, 4201, 4301)
    assert RECOVERY_TOLERANCES == (1.0, 1.0, 0.5, 0.35)
    assert EVALUATION_BUDGET == 120
    assert len(OPTIMIZER_STARTS) == 8
    assert len(COMMON_START_RATES) == len(OPTIMIZER_STARTS)
    assert all(rates[2] < rates[3] for rates in COMMON_START_RATES)
    assert tuple(plan.training_data_seed for plan in TRIAL_PLANS) == RECOVERY_SEEDS
    assert tuple(plan.likelihood_search_seed for plan in TRIAL_PLANS) == (14101, 14201, 14301)
    assert tuple(plan.production_search_seed for plan in TRIAL_PLANS) == (24101, 24201, 24301)
    assert tuple(plan.production_selection_trial_seeds for plan in TRIAL_PLANS) == (
        (104101,),
        (104201,),
        (104301,),
    )
    assert tuple(plan.held_out_data_seed for plan in TRIAL_PLANS) == (34101, 34201, 34301)
    assert not hasattr(mmpp_schema, "expm")
    assert not hasattr(mmpp_schema, "differential_evolution")
    assert callable(likelihood_probe.expm)
    assert callable(probe.differential_evolution)


def test_simulation_distance_fit_rejects_an_invalid_final_population(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returning an invalid production-search winner must abort instead of fabricating rates."""
    invalid = Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=0),
        family="mmpp",
        genes=None,
        status="invalid",
        fitness=0.0,
        trials=(),
        invalid=CandidateFailure(
            kind="fit",
            seed=None,
            detail="controlled invalid winner",
            stage="fit",
            affected_evidence="candidate model",
            evidence_state="diagnostic_only",
            corrective_action="retain the valid-winner guard",
            authority="primary",
        ),
        duplicate_diagnostics=(),
    )
    reference = probe.generate_trace(TRUE_RATES, seed=99, window=20.0)

    def invalid_rank(_candidates: Sequence[Candidate], *, family_priority: FamilyPriority) -> tuple[Candidate, ...]:
        del family_priority
        return (invalid,)

    monkeypatch.setattr(probe, "PRODUCTION_GENERATIONS", 0)
    monkeypatch.setattr(probe, "rank_candidates", invalid_rank)
    with pytest.raises(AssertionError, match="no valid winner"):
        probe.simulation_distance_fit(
            reference,
            window=float(reference.timestamps[-1]),
            seed=199,
            trial_seeds=(299,),
            starts=COMMON_START_RATES,
        )


def test_decision_passes_only_for_exactly_the_mandated_true_gates() -> None:
    """Missing, unknown, false, or non-boolean gates must deterministically reject adoption."""
    passing: dict[str, bool] = dict.fromkeys(AGGREGATE_GATE_NAMES, True)
    assert decide_probe(passing) == {
        "outcome": "pass",
        "failed_gates": [],
        "production_changed": False,
    }
    assert decide_probe({**passing, "extreme_finite": False, "equal_evaluation_budget": False}) == {
        "outcome": "reject",
        "failed_gates": ["extreme_finite", "equal_evaluation_budget"],
        "production_changed": False,
    }
    assert decide_probe({}) == {
        "outcome": "reject",
        "failed_gates": list(AGGREGATE_GATE_NAMES),
        "production_changed": False,
    }
    assert decide_probe({**passing, "unexpected": True}) == {
        "outcome": "reject",
        "failed_gates": ["unknown:unexpected"],
        "production_changed": False,
    }
    assert decide_probe({**passing, "hand_likelihood": 1}) == {  # type: ignore[dict-item]
        "outcome": "reject",
        "failed_gates": ["hand_likelihood"],
        "production_changed": False,
    }


def test_history_count_helpers_require_complete_contiguous_evaluation_indexes() -> None:
    """Independent counters, missing events, and duplicate indexes must not satisfy equal cost."""
    likelihood_item = LikelihoodEvaluation(
        evaluation_index=1,
        coordinates=OPTIMIZER_STARTS[0],
        rates=COMMON_START_RATES[0],
        objective=1.0,
        log_likelihood=-1.0,
        status="valid",
        failure=None,
    )
    simulation_item = SimulationCandidateHistory(
        generation=0,
        candidate_id=(0, 0),
        genes=COMMON_START_RATES[0],
        status="valid",
        fitness=0.5,
        failure=None,
        evaluation_index=1,
    )
    generation = SimulationGenerationHistory(generation=0, candidates=(simulation_item,))
    assert likelihood_evaluation_count((likelihood_item,)) == 1
    assert simulation_evaluation_count((generation,)) == 1
    with pytest.raises(ValueError, match="nonempty"):
        likelihood_evaluation_count(())
    with pytest.raises(ValueError, match="contiguous"):
        likelihood_evaluation_count((replace(likelihood_item, evaluation_index=2),))
    with pytest.raises(ValueError, match="nonempty"):
        simulation_evaluation_count(())
    with pytest.raises(ValueError, match="evaluation events"):
        simulation_evaluation_count(
            (SimulationGenerationHistory(generation=0, candidates=(replace(simulation_item, evaluation_index=None),)),)
        )
    duplicate = replace(simulation_item, candidate_id=(0, 1))
    with pytest.raises(ValueError, match="contiguous"):
        simulation_evaluation_count(
            (SimulationGenerationHistory(generation=0, candidates=(simulation_item, duplicate)),)
        )


def test_simulation_history_requires_one_event_slot_per_candidate() -> None:
    """Dropping a candidate event slot would make the retained objective count unauditable."""
    with pytest.raises(ValueError, match="equal length"):
        probe._simulation_generation_history(0, (), (1,))  # pyright: ignore[reportPrivateUsage]


def test_probe_evidence_records_recovery_holdouts_and_equal_cost() -> None:
    """Missing held-out inputs or unequal evaluations would make the comparison unauditable."""
    evidence = build_probe_evidence()
    assert evidence["schema_version"] == 3
    assert evidence["probe"] == "scipy_two_state_mmpp_likelihood"
    assert evidence["policy"]["production_changed"] is False
    assert evidence["policy"]["common_initial_rates"] == [list(rates) for rates in COMMON_START_RATES]
    assert evidence["policy"]["likelihood_optimizer"] == {
        "method": "scipy.optimize.differential_evolution",
        "population_size": len(OPTIMIZER_STARTS),
        "generations": 14,
        "tol": LIKELIHOOD_TOL,
        "atol": LIKELIHOOD_ATOL,
        "polish": LIKELIHOOD_POLISH,
        "updating": LIKELIHOOD_UPDATING,
        "workers": LIKELIHOOD_WORKERS,
    }
    assert evidence["policy"]["simulation_distance_optimizer"] == {
        "method": "trafficlab production genetic operators and similarity",
        "population_size": PRODUCTION_POPULATION_SIZE,
        "generations": PRODUCTION_GENERATIONS,
        "elite_count": PRODUCTION_ELITE_COUNT,
        "tournament_size": PRODUCTION_TOURNAMENT_SIZE,
        "duplicate_mutation_attempts": PRODUCTION_DUPLICATE_MUTATION_ATTEMPTS,
        "crossover_probability": PRODUCTION_BOUNDS.crossover_probability,
        "mutation_probability": PRODUCTION_BOUNDS.mutation_probability,
        "mutation_scale": PRODUCTION_BOUNDS.mutation_scale,
    }
    assert set(evidence["gates"]) == set(AGGREGATE_GATE_NAMES)
    trials = evidence["trials"]
    assert len(trials) == len(RECOVERY_SEEDS)
    for trial in trials:
        assert trial["simulation_reference_window_seconds"] < trial["training_window_seconds"]
        likelihood_fit = trial["likelihood_fit"]
        simulation_fit = trial["simulation_distance_fit"]
        assert likelihood_fit["starts"] == simulation_fit["starts"] == [list(rates) for rates in COMMON_START_RATES]
        assert likelihood_fit["evaluations"] == len(likelihood_fit["history"]) == EVALUATION_BUDGET
        simulation_events = [
            candidate
            for generation in simulation_fit["history"]
            for candidate in generation["candidates"]
            if candidate["evaluation_index"] is not None
        ]
        assert simulation_fit["evaluations"] == len(simulation_events) == EVALUATION_BUDGET
        assert [item["evaluation_index"] for item in likelihood_fit["history"]] == list(range(1, EVALUATION_BUDGET + 1))
        assert [item["evaluation_index"] for item in simulation_events] == list(range(1, EVALUATION_BUDGET + 1))
        assert simulation_fit["history"][0]["generation"] == 0
        assert simulation_fit["history"][-1]["generation"] == 16
        assert [candidate["genes"] for candidate in simulation_fit["history"][0]["candidates"]] == [
            list(rates) for rates in COMMON_START_RATES
        ]
        seed_plan = trial["seed_limit_plan"]
        assert set(seed_plan) == {
            "training_data_seed",
            "likelihood_search_seed",
            "production_search_seed",
            "production_selection_trial_seeds",
            "production_final_seed",
            "held_out_data_seed",
            "training_observation_window_seconds",
            "held_out_observation_window_seconds",
            "generation_limits",
        }
        assert seed_plan["training_data_seed"] == trial["seed"]
        assert seed_plan["production_selection_trial_seeds"]
        assert seed_plan["production_final_seed"] is None
        assert seed_plan["training_observation_window_seconds"] == 180.0
        assert seed_plan["held_out_observation_window_seconds"] == 120.0
        assert seed_plan["generation_limits"] == {
            "max_packets": 10_000,
            "max_output_bytes": 10_000_000,
            "max_wall_seconds": 5.0,
        }
        assert trial["held_out"]["iats"]
        assert trial["held_out"]["terminal_silence"] >= 0.0
        assert set(trial["gates"]) == {"equal_evaluation_budget", "held_out_likelihood", "recovery"}
    assert evidence["gates"]["equal_evaluation_budget"] is all(
        trial["gates"]["equal_evaluation_budget"] for trial in trials
    )
    assert evidence["gates"]["held_out_likelihood"] is all(trial["gates"]["held_out_likelihood"] for trial in trials)
    assert evidence["decision"] == decide_probe(evidence["gates"])


def test_canonical_evidence_bytes_and_check_mode(tmp_path: Path) -> None:
    """Nondeterministic evidence or a non-checking runner cannot guard the checked fixture."""
    evidence = build_probe_evidence()
    assert build_probe_evidence() == evidence
    assert validate_probe_evidence(evidence) is evidence
    mismatched_schema = deepcopy(evidence)
    mismatched_schema["schema_version"] = 2
    with pytest.raises(ValueError, match="schema version"):
        validate_probe_evidence(mismatched_schema)
    mismatched_likelihood = deepcopy(evidence)
    mismatched_likelihood["policy"]["likelihood_optimizer"]["tol"] = 0.1
    with pytest.raises(ValueError, match="likelihood optimizer policy"):
        validate_probe_evidence(mismatched_likelihood)
    mismatched_simulation = deepcopy(evidence)
    mismatched_simulation["policy"]["simulation_distance_optimizer"]["mutation_scale"] = 0.2
    with pytest.raises(ValueError, match="simulation optimizer policy"):
        validate_probe_evidence(mismatched_simulation)
    mismatched_trial_count = deepcopy(evidence)
    mismatched_trial_count["trials"].pop()
    with pytest.raises(ValueError, match="plan count"):
        validate_probe_evidence(mismatched_trial_count)
    mismatched_trial = deepcopy(evidence)
    mismatched_trial["trials"][0]["seed_limit_plan"]["generation_limits"]["max_packets"] = 9_999
    with pytest.raises(ValueError, match="trial seed/limit plan"):
        validate_probe_evidence(mismatched_trial)
    rendered = render_probe_evidence(evidence)
    assert rendered.endswith(b"\n")
    assert json.loads(rendered) == evidence
    destination = tmp_path / "mmpp_cases.json"
    assert write_probe_evidence(destination, evidence, check=False) is True
    assert destination.read_bytes() == rendered
    assert write_probe_evidence(destination, evidence, check=True) is True
    destination.write_bytes(rendered + b" ")
    assert write_probe_evidence(destination, evidence, check=True) is False


def test_probe_runner_bootstraps_the_repository_from_any_working_directory(tmp_path: Path) -> None:
    """Direct script execution must resolve the test-only probe without PYTHONPATH help."""
    repository = Path(__file__).resolve().parents[4]
    completed = subprocess.run(
        [sys.executable, str(repository / "scripts" / "run_scientific_stack_probes.py"), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--check" in completed.stdout
