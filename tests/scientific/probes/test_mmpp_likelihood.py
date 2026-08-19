"""Scientific gates for the test-only SciPy two-state MMPP likelihood probe."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from tests.scientific.probes import mmpp_likelihood as probe
from tests.scientific.probes.mmpp_likelihood import (
    EVALUATION_BUDGET,
    EXTREME_CASES,
    HAND_CASES,
    OPTIMIZER_STARTS,
    PROBE_BOUNDS,
    RECOVERY_SEEDS,
    RECOVERY_TOLERANCES,
    TRUE_RATES,
    build_probe_evidence,
    decide_probe,
    decode_rates,
    fit_mmpp_likelihood,
    mmpp_log_likelihood,
    render_probe_evidence,
    write_probe_evidence,
)
from trafficlab.genetic.types import Candidate, CandidateFailure, CandidateId, FamilyPriority


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
    assert first.evaluations == EVALUATION_BUDGET
    assert first.evaluations <= EVALUATION_BUDGET
    assert math.isfinite(first.log_likelihood)
    assert first.rates[2] < first.rates[3]


def test_probe_policy_is_predeclared_before_results() -> None:
    """Changing seeds, tolerances, starts, or budgets after seeing outcomes invalidates the probe."""
    assert TRUE_RATES == (0.7, 1.9, 1.2, 7.5)
    assert RECOVERY_SEEDS == (4101, 4201, 4301)
    assert RECOVERY_TOLERANCES == (1.0, 1.0, 0.5, 0.35)
    assert EVALUATION_BUDGET == 120
    assert len(OPTIMIZER_STARTS) == 8


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
    reference = probe._generate_trace(TRUE_RATES, seed=99, window=20.0)  # pyright: ignore[reportPrivateUsage]

    def invalid_rank(_candidates: Sequence[Candidate], *, family_priority: FamilyPriority) -> tuple[Candidate, ...]:
        del family_priority
        return (invalid,)

    monkeypatch.setattr(probe, "SIMULATION_GENERATIONS", 0)
    monkeypatch.setattr(probe, "rank_candidates", invalid_rank)
    with pytest.raises(AssertionError, match="no valid winner"):
        probe._simulation_distance_fit(  # pyright: ignore[reportPrivateUsage]
            reference,
            window=float(reference.timestamps[-1]),
            seed=199,
        )


def test_decision_passes_only_when_every_gate_passes() -> None:
    """A single failed scientific gate must deterministically reject production adoption."""
    assert decide_probe({"hand": True, "extreme": True, "recovery": True, "equal_budget": True}) == {
        "outcome": "pass",
        "failed_gates": [],
        "production_changed": False,
    }
    assert decide_probe({"hand": True, "extreme": False, "recovery": True, "equal_budget": False}) == {
        "outcome": "reject",
        "failed_gates": ["equal_budget", "extreme"],
        "production_changed": False,
    }


def test_probe_evidence_records_recovery_holdouts_and_equal_cost() -> None:
    """Missing held-out inputs or unequal evaluations would make the comparison unauditable."""
    evidence = build_probe_evidence()
    assert evidence["probe"] == "scipy_two_state_mmpp_likelihood"
    assert evidence["policy"]["production_changed"] is False
    trials = evidence["trials"]
    assert len(trials) == len(RECOVERY_SEEDS)
    for trial in trials:
        assert trial["simulation_reference_window_seconds"] < trial["training_window_seconds"]
        assert trial["likelihood_fit"]["evaluations"] == EVALUATION_BUDGET
        assert trial["simulation_distance_fit"]["evaluations"] == EVALUATION_BUDGET
        assert trial["held_out"]["iats"]
        assert trial["held_out"]["terminal_silence"] >= 0.0
        assert set(trial["gates"]) == {"equal_evaluation_budget", "held_out_likelihood", "recovery"}
    assert evidence["decision"] == decide_probe(evidence["gates"])


def test_canonical_evidence_bytes_and_check_mode(tmp_path: Path) -> None:
    """Nondeterministic evidence or a non-checking runner cannot guard the checked fixture."""
    evidence = build_probe_evidence()
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
    repository = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        [sys.executable, str(repository / "scripts" / "run_scientific_stack_probes.py"), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--check" in completed.stdout
