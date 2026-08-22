"""Evidence owner for Validation Study tooling."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING

from tests.scientific.fitting.probes.mmpp_likelihood.fit import (
    decide_probe,
    fit_mmpp_likelihood,
    generate_trace,
    likelihood_history_records,
    likelihood_optimizer_policy,
    rates_list,
    seed_limit_record,
    simulation_distance_fit,
    simulation_history_records,
    simulation_optimizer_policy,
    trace_observations,
)
from tests.scientific.fitting.probes.mmpp_likelihood.likelihood import COMMON_START_RATES, mmpp_log_likelihood
from tests.scientific.fitting.probes.mmpp_likelihood.schema import (
    EVALUATION_BUDGET,
    EXTREME_CASES,
    HAND_ABSOLUTE_TOLERANCE,
    HAND_CASES,
    HELD_OUT_WINDOW_SECONDS,
    OPTIMIZER_STARTS,
    PROBE_BOUNDS,
    RECOVERY_SEEDS,
    RECOVERY_TOLERANCES,
    TRAINING_WINDOW_SECONDS,
    TRIAL_PLANS,
    TRUE_RATES,
)

if TYPE_CHECKING:
    from tests.scientific.fitting.probes.mmpp_likelihood.schema import (
        AggregateGates,
        ExtremeRecord,
        HandRecord,
        PolicyRecord,
        ProbeEvidence,
        TrialPlan,
        TrialRecord,
    )


def _hand_records() -> list[HandRecord]:
    records: list[HandRecord] = []
    for case in HAND_CASES:
        observed = mmpp_log_likelihood(case["iats"], case["terminal_silence"], case["rates"])
        error = abs(observed - case["expected_log_likelihood"])
        records.append(
            {
                "name": case["name"],
                "rates": rates_list(case["rates"]),
                "iats": list(case["iats"]),
                "terminal_silence": case["terminal_silence"],
                "expected_log_likelihood": case["expected_log_likelihood"],
                "observed_log_likelihood": observed,
                "absolute_error": error,
                "passed": error <= HAND_ABSOLUTE_TOLERANCE,
            }
        )
    return records


def _extreme_records() -> list[ExtremeRecord]:
    records: list[ExtremeRecord] = []
    for case in EXTREME_CASES:
        observed = mmpp_log_likelihood(case["iats"], case["terminal_silence"], case["rates"])
        records.append(
            {
                "name": case["name"],
                "rates": rates_list(case["rates"]),
                "iats": list(case["iats"]),
                "terminal_silence": case["terminal_silence"],
                "observed_log_likelihood": observed,
                "passed": math.isfinite(observed),
            }
        )
    return records


def _trial(plan: TrialPlan) -> TrialRecord:
    seed = plan.training_data_seed
    training = generate_trace(TRUE_RATES, seed=seed, window=TRAINING_WINDOW_SECONDS)
    training_iats, training_terminal = trace_observations(training, TRAINING_WINDOW_SECONDS)
    likelihood = fit_mmpp_likelihood(training_iats, training_terminal, PROBE_BOUNDS, seed=plan.likelihood_search_seed)
    simulation_window = float(training.timestamps[-1])
    simulation = simulation_distance_fit(
        training,
        window=simulation_window,
        seed=plan.production_search_seed,
        trial_seeds=plan.production_selection_trial_seeds,
        starts=likelihood.starts,
    )
    held_out_seed = plan.held_out_data_seed
    held_out = generate_trace(TRUE_RATES, seed=held_out_seed, window=HELD_OUT_WINDOW_SECONDS)
    held_out_iats, held_out_terminal = trace_observations(held_out, HELD_OUT_WINDOW_SECONDS)
    likelihood_held_out = mmpp_log_likelihood(held_out_iats, held_out_terminal, likelihood.rates)
    simulation_held_out = mmpp_log_likelihood(held_out_iats, held_out_terminal, simulation.rates)
    log_errors = tuple(
        (abs(math.log(observed / expected)) for observed, expected in zip(likelihood.rates, TRUE_RATES, strict=True))
    )
    recovery = all((error <= tolerance for error, tolerance in zip(log_errors, RECOVERY_TOLERANCES, strict=True)))
    equal_budget = likelihood.evaluations == simulation.evaluations == EVALUATION_BUDGET
    held_out_gate = likelihood_held_out >= simulation_held_out - 1e-12
    return {
        "seed": seed,
        "true_rates": rates_list(TRUE_RATES),
        "seed_limit_plan": seed_limit_record(plan),
        "training_window_seconds": TRAINING_WINDOW_SECONDS,
        "simulation_reference_window_seconds": simulation_window,
        "training_event_count": len(training),
        "likelihood_fit": {
            "rates": rates_list(likelihood.rates),
            "starts": [rates_list(start) for start in likelihood.starts],
            "evaluations": likelihood.evaluations,
            "termination": likelihood.termination,
            "training_log_likelihood": likelihood.log_likelihood,
            "history": likelihood_history_records(likelihood.history),
        },
        "simulation_distance_fit": {
            "rates": rates_list(simulation.rates),
            "starts": [rates_list(start) for start in simulation.starts],
            "evaluations": simulation.evaluations,
            "termination": simulation.termination,
            "training_fitness": simulation.fitness,
            "history": simulation_history_records(simulation.history),
        },
        "held_out": {
            "seed": held_out_seed,
            "iats": list(held_out_iats),
            "terminal_silence": held_out_terminal,
            "likelihood_fit_log_likelihood": likelihood_held_out,
            "simulation_distance_fit_log_likelihood": simulation_held_out,
        },
        "log_rate_errors": list(log_errors),
        "gates": {"recovery": recovery, "equal_evaluation_budget": equal_budget, "held_out_likelihood": held_out_gate},
    }


def build_probe_evidence() -> ProbeEvidence:
    """Run every predeclared gate and return complete machine-readable evidence."""
    hand = _hand_records()
    extreme = _extreme_records()
    trials = [_trial(plan) for plan in TRIAL_PLANS]
    gates: AggregateGates = {
        "hand_likelihood": all(record["passed"] for record in hand),
        "extreme_finite": all(record["passed"] for record in extreme),
        "synthetic_recovery": all(record["gates"]["recovery"] for record in trials),
        "equal_evaluation_budget": all(record["gates"]["equal_evaluation_budget"] for record in trials),
        "held_out_likelihood": all(record["gates"]["held_out_likelihood"] for record in trials),
    }
    policy: PolicyRecord = {
        "production_changed": False,
        "rng": "numpy.random.Generator/PCG64",
        "true_rates": rates_list(TRUE_RATES),
        "rate_bounds": {
            "q01": list(PROBE_BOUNDS.q01),
            "q10": list(PROBE_BOUNDS.q10),
            "lambda0": list(PROBE_BOUNDS.lambda0),
            "lambda1": list(PROBE_BOUNDS.lambda1),
        },
        "recovery_seeds": list(RECOVERY_SEEDS),
        "recovery_log_rate_tolerances": rates_list(RECOVERY_TOLERANCES),
        "evaluation_budget": EVALUATION_BUDGET,
        "likelihood_optimizer": likelihood_optimizer_policy(),
        "simulation_distance_optimizer": simulation_optimizer_policy(),
        "optimizer_starts": [list(start) for start in OPTIMIZER_STARTS],
        "common_initial_rates": [rates_list(rates) for rates in COMMON_START_RATES],
        "training_window_seconds": TRAINING_WINDOW_SECONDS,
        "held_out_window_seconds": HELD_OUT_WINDOW_SECONDS,
    }
    return {
        "schema_version": 3,
        "probe": "scipy_two_state_mmpp_likelihood",
        "policy": policy,
        "hand_cases": hand,
        "extreme_cases": extreme,
        "trials": trials,
        "gates": gates,
        "decision": decide_probe(gates),
    }


def validate_probe_evidence(evidence: ProbeEvidence) -> ProbeEvidence:
    """Reject optimizer or per-trial policy drift before canonical rendering."""
    if evidence["schema_version"] != 3:
        raise ValueError("probe evidence schema version does not match the canonical optimizer policy")
    if evidence["policy"]["likelihood_optimizer"] != likelihood_optimizer_policy():
        raise ValueError("likelihood optimizer policy does not match executed controls")
    if evidence["policy"]["simulation_distance_optimizer"] != simulation_optimizer_policy():
        raise ValueError("simulation optimizer policy does not match executed controls")
    if len(evidence["trials"]) != len(TRIAL_PLANS):
        raise ValueError("trial seed/limit plan count does not match the executed trials")
    for trial, plan in zip(evidence["trials"], TRIAL_PLANS, strict=True):
        if trial["seed_limit_plan"] != seed_limit_record(plan):
            raise ValueError("trial seed/limit plan does not match executed controls")
    return evidence


def render_probe_evidence(evidence: ProbeEvidence) -> bytes:
    """Render canonical UTF-8 JSON with sorted compact keys and one final newline."""
    validated = validate_probe_evidence(evidence)
    return (json.dumps(validated, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def write_probe_evidence(destination: Path, evidence: ProbeEvidence, *, check: bool) -> bool:
    """Write canonical evidence, or compare it byte-for-byte without mutation."""
    rendered = render_probe_evidence(evidence)
    if check:
        try:
            return destination.read_bytes() == rendered
        except FileNotFoundError:
            return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(rendered)
    return True
