"""Evidence owner for Validation Study tooling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from tests.scientific.fitting.probes.pymoo_optimizer.adapter import (
    champion,
    evaluation_context,
    run_family,
    run_invalid_adapter,
    run_known_case,
)
from tests.scientific.fitting.probes.pymoo_optimizer.policy import (
    decide_probe,
    derive_gates,
    loc_inventory,
    policy,
    semantic_root_is_consistent,
)
from tests.scientific.fitting.probes.pymoo_optimizer.schema import (
    CACHE_KEY_FIELDS,
    CONTINUOUS_INITIAL_SAMPLES,
    FAMILY_NAMES,
    GATE_NAMES,
    KNOWN_CASES,
    KNOWN_GENERATIONS,
    KNOWN_POPULATION_SIZE,
    KNOWN_TOLERANCES,
    MIXED_INITIAL_SAMPLES,
    REPLAY_CHECKED_FIELDS,
    SEARCH_SEED,
    CheckpointEvidence,
    DecisionRecord,
    FairnessEvidence,
    FamilyEvidence,
    GateRecord,
    KnownCaseEvidence,
    KnownOptimumRecord,
    ProbeEvidence,
    PublicApiProof,
    ReplayComparison,
)
from trafficlab.common.config import (
    FamilyName,
)

if TYPE_CHECKING:
    from tests.scientific.fitting.probes.pymoo_optimizer.adapter import FamilyExecution
    from tests.scientific.fitting.probes.pymoo_optimizer.schema import JsonObject


def _known_evidence() -> tuple[KnownCaseEvidence, KnownCaseEvidence]:
    output: list[KnownCaseEvidence] = []
    samples = (CONTINUOUS_INITIAL_SAMPLES, MIXED_INITIAL_SAMPLES)
    for specification, initial in zip(KNOWN_CASES, samples, strict=True):
        name = specification["name"]
        run_one = run_known_case(name)
        run_two = run_known_case(name)
        normalized_initial = tuple(
            {"x0": item[0], "x1": item[1]} if isinstance(item, tuple) else dict(item) for item in initial
        )
        output.append(
            KnownCaseEvidence(
                name=cast(Any, name),
                objective_definition="sum_squares" if name == "bounded_continuous_sphere" else "integer_real_quadratic",
                seed=SEARCH_SEED,
                variable_kinds=cast(Any, specification["variable_kinds"]),
                bounds=cast(Any, specification["bounds"]),
                known_optimum=KnownOptimumRecord.model_validate(specification["known_optimum"]),
                tolerance=KNOWN_TOLERANCES[name],
                population_size=KNOWN_POPULATION_SIZE,
                generations=KNOWN_GENERATIONS,
                initial_sampling=normalized_initial,
                runs=(run_one, run_two),
            )
        )
    return cast(tuple[KnownCaseEvidence, KnownCaseEvidence], tuple(output))


def _checkpoint(execution: FamilyExecution) -> CheckpointEvidence:
    return CheckpointEvidence(
        encoding="canonical-json",
        snapshot=execution.checkpoint,
        comparison=ReplayComparison(
            exact_history_identity=False,
            checked_fields=REPLAY_CHECKED_FIELDS,
            uninterrupted_history=execution.run.history,
            resumed_history=None,
            reason="pymoo 0.6.2 documents ask/tell observation but no transparent restore API; its checkpoint guide restores only a serialized whole Algorithm object",
        ),
        public_api_proof=PublicApiProof(
            installed_algorithm_methods=("setup", "has_next", "next", "ask", "tell", "result"),
            installed_observable_state=("pop", "n_gen", "evaluator.n_eval", "termination", "random_state"),
            documented_checkpoint_transport="dill algorithm serialization",
            opaque_transport_allowed=False,
            transparent_restore_api=None,
            checkpoint_documentation="https://pymoo.org/misc/checkpoint.html",
            usage_documentation="https://pymoo.org/algorithms/usage.html",
        ),
    )


def build_probe_evidence() -> JsonObject:
    """Execute every bounded optimizer gate and return strict canonical evidence."""
    known = _known_evidence()
    context = evaluation_context()
    first = [run_family(name, context) for name in FAMILY_NAMES]
    second = [run_family(name, context) for name in FAMILY_NAMES]
    all_executions = (*first, *second)
    identity_ordinals = {id(item.algorithm): index + 1 for index, item in enumerate(all_executions)}
    families: list[FamilyEvidence] = []
    for first_run, second_run in zip(first, second, strict=True):
        run_one = first_run.run.model_copy(update={"optimizer_instance": identity_ordinals[id(first_run.algorithm)]})
        run_two = second_run.run.model_copy(update={"optimizer_instance": identity_ordinals[id(second_run.algorithm)]})
        families.append(FamilyEvidence(family=run_one.family, runs=(run_one, run_two)))
    family_tuple = cast(tuple[FamilyEvidence, FamilyEvidence, FamilyEvidence], tuple(families))
    champions = tuple(champion(item, context) for item in first)
    winner = max(champions, key=lambda item: (item.fresh_fitness, -FAMILY_NAMES.index(item.family)))
    ranking = cast(
        tuple[FamilyName, ...],
        tuple(
            item.family
            for item in sorted(champions, key=lambda item: (-item.fresh_fitness, FAMILY_NAMES.index(item.family)))
        ),
    )
    fairness = FairnessEvidence(
        measured_family_set=tuple(item.family for item in family_tuple),
        distinct_optimizer_instances=len({run.optimizer_instance for item in family_tuple for run in item.runs}),
        common_search_seed=family_tuple[0].runs[0].search_seed,
        common_trial_seeds=family_tuple[0].runs[0].trial_seeds,
        common_champion_seed=champions[0].fresh_seed,
        common_window_seconds=family_tuple[0].runs[0].observation_window_seconds,
        common_generation_limits=family_tuple[0].runs[0].generation_limits,
        common_similarity=family_tuple[0].runs[0].similarity,
        equal_initial_budget=min(item.runs[0].initial_evaluations for item in family_tuple),
        equal_total_budget=min(item.runs[0].total_attempts for item in family_tuple),
        cache_key_fields=CACHE_KEY_FIELDS,
        champions_compared_after_attempts=min(item.search_attempts_completed for item in champions),
        champion_comparison=champions,
        champion_ranking=ranking,
        winner_family=winner.family,
        winner=winner,
    )
    placeholder = GateRecord(
        known_optima=False,
        deterministic_repeats=False,
        family_fairness=False,
        cache_and_diagnostics=False,
        exact_public_state_replay=False,
        production_loc_reduction=False,
    )
    provisional = ProbeEvidence(
        schema_version=3,
        probe="pymoo_optimizer",
        policy=policy(),
        known_cases=known,
        families=family_tuple,
        fairness=fairness,
        invalid_classification=run_invalid_adapter(),
        checkpoint=_checkpoint(first[0]),
        production_loc=loc_inventory(),
        gates=placeholder,
        decision=DecisionRecord(
            outcome="reject",
            failed_gates=GATE_NAMES,
            production_changed=False,
            production_strategy="basic_generational",
        ),
    )
    gates = derive_gates(provisional)
    decision = DecisionRecord.model_validate(decide_probe(gates.model_dump(mode="python")))
    evidence = provisional.model_copy(update={"gates": gates, "decision": decision})
    return evidence.model_dump(mode="json")


def validate_probe_evidence(evidence: JsonObject) -> JsonObject:
    """Strictly parse every section and recompute both gates and decision."""
    try:
        parsed = ProbeEvidence.model_validate(evidence)
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid strict pymoo probe evidence: {error}") from error
    if not semantic_root_is_consistent(parsed):
        raise ValueError("pymoo probe cross-section semantics do not match canonical measured evidence")
    derived = derive_gates(parsed)
    if parsed.gates != derived:
        raise ValueError("pymoo probe stored gates do not match derived gates")
    expected_decision = DecisionRecord.model_validate(decide_probe(derived.model_dump(mode="python")))
    if parsed.decision != expected_decision:
        raise ValueError("pymoo probe decision does not match derived gates")
    if parsed.model_dump(mode="json") != evidence:
        raise ValueError("pymoo probe evidence is not in strict canonical value form")
    return evidence


def render_probe_evidence(evidence: JsonObject) -> bytes:
    validated = validate_probe_evidence(evidence)
    return (json.dumps(validated, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def write_probe_evidence(destination: Path, evidence: JsonObject, *, check: bool) -> bool:
    rendered = render_probe_evidence(evidence)
    if check:
        try:
            return destination.read_bytes() == rendered
        except FileNotFoundError:
            return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(rendered)
    return True
