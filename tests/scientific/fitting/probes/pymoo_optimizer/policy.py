"""Policy owner for Validation Study tooling."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import pymoo  # pyright: ignore[reportMissingTypeStubs]

from tests.scientific.fitting.probes.pymoo_optimizer.adapter import (
    MIXED_VARIABLE_GA,
    mating_settings,
    render_cache_key,
    repair_settings,
    variable_specs,
)
from tests.scientific.fitting.probes.pymoo_optimizer.schema import (
    CACHE_KEY_FIELDS,
    CHAMPION_SEED,
    CHECKPOINT_FIELDS,
    CONTINUOUS_INITIAL_SAMPLES,
    FAMILY_BOUNDS,
    FAMILY_NAMES,
    GATE_NAMES,
    GENERATION_LIMITS,
    INITIAL_EVALUATION_BUDGET,
    INITIAL_VALUES,
    INVALID_CASE_LIMITS,
    INVALID_OBJECTIVE,
    KNOWN_CASES,
    KNOWN_GENERATIONS,
    KNOWN_POPULATION_SIZE,
    KNOWN_TOLERANCES,
    MINIMUM_LOC_REDUCTION_PERCENT,
    MIXED_INITIAL_SAMPLES,
    PROHIBITED_SERIALIZERS,
    REPLAY_CHECKED_FIELDS,
    REPLAY_MISSING_FIELDS,
    SEARCH_SEED,
    SIMILARITY,
    TOTAL_GENERATIONS,
    TRIAL_SEEDS,
    WINDOW_SECONDS,
    GateRecord,
    LocFileRecord,
    PolicyRecord,
    ProductionLocEvidence,
    PublicPopulationState,
    RepairSettings,
)
from trafficlab.fitting.genetic.types import CandidateId
from trafficlab.generation.models.registry import REGISTRY

if TYPE_CHECKING:
    from tests.scientific.fitting.probes.pymoo_optimizer.schema import (
        FamilyRunRecord,
        JsonObject,
        KnownCaseEvidence,
        MatingSettings,
        ProbeEvidence,
        Scalars,
    )


def _count_sloc(path: Path) -> int:
    return sum(bool(line.strip()) and (not line.lstrip().startswith("#")) for line in path.read_text().splitlines())


def loc_inventory() -> ProductionLocEvidence:
    directory = Path(__file__).resolve().parents[5] / "src" / "trafficlab" / "fitting" / "genetic"
    inventory = tuple(
        LocFileRecord(path=path.relative_to(directory).as_posix(), sloc=_count_sloc(path))
        for path in sorted(directory.rglob("*.py"))
    )
    return ProductionLocEvidence(
        status="indeterminate",
        method="nonblank non-comment physical Python lines",
        current_inventory=inventory,
        current_sloc=sum(item.sloc for item in inventory),
        estimated_reduction_percent=None,
        required_reduction_percent=MINIMUM_LOC_REDUCTION_PERCENT,
        gate_passed=False,
        reason="exact line-level retained/removable attribution requires designing the rejected adapter; no defensible production reduction estimate exists after the replay rejection",
    )


def _expected_mating_settings() -> MatingSettings:
    algorithm = MIXED_VARIABLE_GA(pop_size=INITIAL_EVALUATION_BUDGET, eliminate_duplicates=False)
    return mating_settings(algorithm)


def _expected_algorithm_repair() -> RepairSettings:
    algorithm = MIXED_VARIABLE_GA(pop_size=INITIAL_EVALUATION_BUDGET, eliminate_duplicates=False)
    return cast(RepairSettings, repair_settings(algorithm.repair))


def _known_objective(case_name: str, variables: Mapping[str, int | float]) -> float:
    if case_name == "bounded_continuous_sphere":
        return float(variables["x0"]) ** 2 + float(variables["x1"]) ** 2
    return (int(variables["count"]) - 3) ** 2 + (float(variables["scale"]) - 1.25) ** 2


def attempt_history_is_complete(run: FamilyRunRecord) -> bool:
    """Recompute sequential identity, cache, and generation accounting for one run."""
    history = run.history
    if len(history) != run.total_attempts:
        return False
    if tuple(item.evaluation_index for item in history) != tuple(range(1, run.total_attempts + 1)):
        return False
    if any(item.generation != (item.evaluation_index - 1) // run.initial_evaluations for item in history):
        return False
    identifiers = tuple(
        (item.candidate.identifier.birth_generation, item.candidate.identifier.birth_index) for item in history
    )
    expected_ids = tuple(
        ((index - 1) // run.initial_evaluations, (index - 1) % run.initial_evaluations)
        for index in range(1, run.total_attempts + 1)
    )
    if identifiers != expected_ids:
        return False
    actual_hits = sum(item.cache_hit for item in history)
    if actual_hits != run.cache_hits:
        return False
    if run.objective_evaluations != run.total_attempts - actual_hits:
        return False
    misses: dict[str, tuple[JsonObject, float]] = {}
    for item in history:
        if item.cache_key != render_cache_key(item.cache_key_payload):
            return False
        payload = item.cache_key_payload
        candidate = item.candidate
        if not (
            payload.family == run.family == candidate.family
            and candidate.genes is not None
            and (payload.genes == candidate.genes)
            and (payload.observation_window_seconds == run.observation_window_seconds)
            and (payload.trial_seeds == run.trial_seeds)
            and (payload.generation_limits == run.generation_limits)
            and (payload.similarity == run.similarity)
        ):
            return False
        if candidate.status == "valid":
            if not (
                candidate.invalid is None
                and tuple(trial.seed for trial in candidate.trials) == run.trial_seeds
                and (item.objective == 1.0 - candidate.fitness)
            ):
                return False
        elif candidate.status == "invalid":
            if not (
                candidate.invalid is not None
                and candidate.fitness == 0.0
                and (not candidate.trials)
                and (item.objective == INVALID_OBJECTIVE)
            ):
                return False
        else:
            return False
        scientific_result = (item.candidate.model_dump(mode="json", exclude={"identifier"}), item.objective)
        if item.cache_hit:
            if misses.get(item.cache_key) != scientific_result:
                return False
        else:
            if item.cache_key in misses:
                return False
            misses[item.cache_key] = scientific_result
    return True


def _expected_known_initial(name: str) -> tuple[Scalars, ...]:
    if name == "bounded_continuous_sphere":
        return tuple(({"x0": left, "x1": right} for left, right in CONTINUOUS_INITIAL_SAMPLES))
    return tuple(dict(item) for item in MIXED_INITIAL_SAMPLES)


def _known_metadata_is_exact(case: KnownCaseEvidence) -> bool:
    specification = next(item for item in KNOWN_CASES if item["name"] == case.name)
    expected_definition = "sum_squares" if case.name == "bounded_continuous_sphere" else "integer_real_quadratic"
    return (
        case.objective_definition == expected_definition
        and case.seed == SEARCH_SEED
        and (case.variable_kinds == specification["variable_kinds"])
        and (case.bounds == specification["bounds"])
        and (case.known_optimum.model_dump(mode="json") == specification["known_optimum"])
        and (case.known_optimum.objective == _known_objective(case.name, case.known_optimum.variables))
        and (case.tolerance == KNOWN_TOLERANCES[case.name])
        and (case.population_size == KNOWN_POPULATION_SIZE)
        and (case.generations == KNOWN_GENERATIONS)
        and (case.initial_sampling == _expected_known_initial(case.name))
    )


def _known_variables_are_valid(case: KnownCaseEvidence, variables: Scalars) -> bool:
    if set(variables) != set(case.bounds):
        return False
    for name, value in variables.items():
        lower, upper = case.bounds[name]
        kind = case.variable_kinds[name]
        if kind == "integer" and type(value) is not int:
            return False
        if kind == "real" and type(value) is not float:
            return False
        if not lower <= value <= upper:
            return False
    return True


def _known_gate(evidence: ProbeEvidence) -> bool:
    for case in evidence.known_cases:
        if not _known_metadata_is_exact(case):
            return False
        declared_initial_objectives = tuple(_known_objective(case.name, item) for item in case.initial_sampling)
        declared_initial_minimum = min(declared_initial_objectives)
        for run in case.runs:
            objective = _known_objective(case.name, run.variables)
            observed_initial = run.history[0].population
            generations_valid = True
            for index, generation in enumerate(run.history):
                recomputed = tuple(_known_objective(case.name, item.variables) for item in generation.population)
                generations_valid = generations_valid and (
                    generation.generation == index
                    and generation.evaluation_count == (index + 1) * case.population_size
                    and (len(generation.population) == case.population_size)
                    and all(_known_variables_are_valid(case, item.variables) for item in generation.population)
                    and (tuple(item.objective for item in generation.population) == recomputed)
                    and (generation.minimum_objective == min(recomputed))
                )
            if not (
                declared_initial_minimum > case.tolerance
                and run.initial_minimum_objective == declared_initial_minimum
                and (tuple(item.variables for item in observed_initial) == case.initial_sampling)
                and (tuple(item.objective for item in observed_initial) == declared_initial_objectives)
                and (run.objective <= case.tolerance)
                and (run.objective == objective)
                and _known_variables_are_valid(case, run.variables)
                and (run.evaluations == case.population_size * case.generations)
                and (len(run.history) == case.generations)
                and (run.history[0].minimum_objective == run.initial_minimum_objective)
                and generations_valid
                and (run.objective == run.history[-1].minimum_objective)
                and any(
                    item.variables == run.variables and item.objective == run.objective
                    for item in run.history[-1].population
                )
            ):
                return False
    return True


def _repeat_gate(evidence: ProbeEvidence) -> bool:
    known_equal = all(case.runs[0] == case.runs[1] for case in evidence.known_cases)
    family_equal = all(
        family.runs[0].model_dump(mode="json", exclude={"optimizer_instance"})
        == family.runs[1].model_dump(mode="json", exclude={"optimizer_instance"})
        for family in evidence.families
    )
    return known_equal and family_equal


def _fairness_gate(evidence: ProbeEvidence) -> bool:
    first_runs = tuple(family.runs[0] for family in evidence.families)
    all_runs = tuple(run for family in evidence.families for run in family.runs)
    fairness = evidence.fairness
    controls_match = all(
        run.search_seed == evidence.policy.search_seed
        and run.observation_window_seconds == evidence.policy.window_seconds
        and (run.trial_seeds == evidence.policy.trial_seeds)
        and (run.generation_limits == evidence.policy.generation_limits)
        and (run.similarity == evidence.policy.similarity)
        and (run.initial_evaluations == evidence.policy.initial_evaluation_budget)
        and (run.total_attempts == evidence.policy.initial_evaluation_budget * evidence.policy.total_generations)
        and (run.optimizer_config_alias == f"pymoo.MixedVariableGA:{run.family}")
        and attempt_history_is_complete(run)
        for run in all_runs
    )
    family_configs_match = all(
        family.runs[0].variables == family.runs[1].variables
        and family.runs[0].initial_sampling == family.runs[1].initial_sampling
        and all(
            run.variables == variable_specs(REGISTRY[family.family], FAMILY_BOUNDS[family.family])
            and run.initial_sampling == tuple(dict(item) for item in INITIAL_VALUES[family.family])
            for run in family.runs
        )
        for family in evidence.families
    )
    instances = len({run.optimizer_instance for run in all_runs})
    canonical_instances = all(
        (
            tuple((run.optimizer_instance for run in family.runs)) == (family_index + 1, family_index + 4)
            for family_index, family in enumerate(evidence.families)
        )
    )
    champions_match = tuple(item.family for item in fairness.champion_comparison) == FAMILY_NAMES
    for family, champion in zip(evidence.families, fairness.champion_comparison, strict=True):
        first_run = family.runs[0]
        expected_attempt = min(first_run.history, key=lambda item: (item.objective, item.candidate.identifier))
        champions_match = champions_match and (
            all(
                run.best_candidate
                == min(run.history, key=lambda item: (item.objective, item.candidate.identifier)).candidate
                for run in family.runs
            )
            and champion.candidate == expected_attempt.candidate
            and (champion.candidate.status == "valid")
            and (champion.fresh_seed == evidence.policy.champion_seed)
            and (champion.search_attempts_completed == first_run.total_attempts)
            and (len(champion.trials) == 1)
            and (champion.trials[0].seed == evidence.policy.champion_seed)
            and (champion.fresh_fitness == champion.trials[0].aggregate_score)
        )
    ranking = tuple(
        item.family
        for item in sorted(
            fairness.champion_comparison, key=lambda item: (-item.fresh_fitness, FAMILY_NAMES.index(item.family))
        )
    )
    winner = next(item for item in fairness.champion_comparison if item.family == ranking[0])
    return (
        tuple(item.family for item in evidence.families) == FAMILY_NAMES
        and fairness.measured_family_set == FAMILY_NAMES
        and (instances == len(all_runs))
        and canonical_instances
        and (fairness.distinct_optimizer_instances == instances)
        and (fairness.common_search_seed == first_runs[0].search_seed)
        and (fairness.common_trial_seeds == first_runs[0].trial_seeds)
        and (fairness.common_champion_seed == fairness.champion_comparison[0].fresh_seed)
        and (fairness.common_window_seconds == first_runs[0].observation_window_seconds)
        and (fairness.common_generation_limits == first_runs[0].generation_limits)
        and (fairness.common_similarity == first_runs[0].similarity)
        and controls_match
        and family_configs_match
        and (fairness.equal_initial_budget == INITIAL_EVALUATION_BUDGET)
        and (fairness.equal_total_budget == INITIAL_EVALUATION_BUDGET * TOTAL_GENERATIONS)
        and (fairness.cache_key_fields == CACHE_KEY_FIELDS)
        and (fairness.champions_compared_after_attempts == INITIAL_EVALUATION_BUDGET * TOTAL_GENERATIONS)
        and champions_match
        and (fairness.champion_ranking == ranking)
        and (fairness.winner_family == ranking[0])
        and (fairness.winner == winner)
    )


def _cache_diagnostics_gate(evidence: ProbeEvidence) -> bool:
    runs = tuple(run for family in evidence.families for run in family.runs)
    histories_complete = all(attempt_history_is_complete(run) for run in runs)
    candidates_complete = all(
        attempt.candidate.family == run.family
        and attempt.cache_key_payload.family == run.family
        and (attempt.candidate.status == "invalid" or len(attempt.candidate.trials) == len(evidence.policy.trial_seeds))
        for run in runs
        for attempt in run.history
    )
    invalid = evidence.invalid_classification
    invalid_attempt = invalid.history[0]
    invalid_adapter = (
        invalid.execution == "pymoo.MixedVariableGA.next"
        and invalid.limits == evidence.policy.invalid_generation_limits
        and (invalid.pymoo_evaluations == 1)
        and (len(invalid.history) == 1)
        and (invalid_attempt.evaluation_index == 1)
        and (invalid_attempt.generation == 0)
        and (invalid_attempt.candidate.identifier == CandidateId(birth_generation=0, birth_index=0))
        and (invalid_attempt.cache_key_payload.family == invalid.family == invalid_attempt.candidate.family)
        and (invalid_attempt.candidate.genes is not None)
        and (invalid_attempt.cache_key_payload.genes == invalid_attempt.candidate.genes)
        and (invalid_attempt.cache_key_payload.observation_window_seconds == evidence.policy.window_seconds)
        and (invalid_attempt.cache_key_payload.trial_seeds == evidence.policy.trial_seeds)
        and (invalid_attempt.cache_key_payload.generation_limits == invalid.limits)
        and (invalid_attempt.cache_key_payload.similarity == evidence.policy.similarity)
        and (invalid_attempt.cache_key == render_cache_key(invalid_attempt.cache_key_payload))
        and (not invalid_attempt.cache_hit)
        and (invalid_attempt.candidate.status == "invalid")
        and (invalid_attempt.candidate.invalid is not None)
        and (invalid_attempt.candidate.fitness == 0.0)
        and (not invalid_attempt.candidate.trials)
        and (invalid_attempt.candidate.invalid.kind == "incomplete_generation")
        and (invalid_attempt.candidate.invalid.authority == "primary")
        and (invalid_attempt.candidate.invalid.seed == evidence.policy.trial_seeds[0])
        and (invalid_attempt.objective == INVALID_OBJECTIVE)
    )
    return histories_complete and candidates_complete and invalid_adapter


def _checkpoint_fields_are_linked(evidence: ProbeEvidence) -> bool:
    policy = evidence.policy
    snapshot = evidence.checkpoint.snapshot
    first_run = evidence.families[0].runs[0]
    configuration = snapshot.configuration
    initial_attempts = first_run.history[: policy.initial_evaluation_budget]
    expected_population = tuple(
        PublicPopulationState(
            variables={
                spec.name: value
                for spec, value in zip(first_run.variables, attempt.cache_key_payload.genes, strict=True)
            },
            objectives=(attempt.objective,),
            status=("F", "G", "H"),
        )
        for attempt in initial_attempts
    )
    initial_sections_linked = (
        len(initial_attempts) == policy.initial_evaluation_budget
        and tuple(attempt.generation for attempt in initial_attempts) == (0,) * policy.initial_evaluation_budget
        and (len(snapshot.population) == len(configuration.initial_sampling) == policy.initial_evaluation_budget)
        and (configuration.initial_sampling == tuple(item.variables for item in expected_population))
        and (snapshot.population == expected_population)
    )
    return (
        initial_sections_linked
        and snapshot.generation == 2
        and (snapshot.evaluation_count == policy.initial_evaluation_budget)
        and (snapshot.termination.kind == "MaximumGenerationTermination")
        and (snapshot.termination.progress == 1.0 / policy.total_generations)
        and (not snapshot.termination.has_terminated)
        and (configuration.constructor.pop_size == policy.initial_evaluation_budget)
        and (len(configuration.initial_sampling) == configuration.constructor.pop_size)
        and (len(configuration.variables) == len(first_run.variables))
    )


def semantic_root_is_consistent(evidence: ProbeEvidence) -> bool:
    """Cross-check every strict section against canonical policy and measured peers."""
    first_run = evidence.families[0].runs[0]
    snapshot = evidence.checkpoint.snapshot
    configuration = snapshot.configuration
    comparison = evidence.checkpoint.comparison
    return (
        evidence.policy == policy()
        and all(_known_metadata_is_exact(case) for case in evidence.known_cases)
        and (comparison.checked_fields == REPLAY_CHECKED_FIELDS)
        and (not snapshot.complete)
        and (snapshot.missing_fields == REPLAY_MISSING_FIELDS)
        and (not comparison.exact_history_identity)
        and (comparison.resumed_history is None)
        and (comparison.uninterrupted_history == first_run.history)
        and (configuration.family == first_run.family)
        and (configuration.search_seed == first_run.search_seed)
        and (configuration.variables == first_run.variables)
        and (configuration.initial_sampling == first_run.initial_sampling)
        and (configuration.mating == _expected_mating_settings())
        and (configuration.algorithm_repair == _expected_algorithm_repair())
        and _checkpoint_fields_are_linked(evidence)
        and (evidence.invalid_classification.limits == evidence.policy.invalid_generation_limits)
        and (evidence.production_loc == loc_inventory())
    )


def derive_gates(evidence: ProbeEvidence) -> GateRecord:
    """Derive every adoption gate exclusively from strict measured evidence."""
    comparison = evidence.checkpoint.comparison
    replay = (
        evidence.checkpoint.snapshot.complete
        and (not evidence.checkpoint.snapshot.missing_fields)
        and comparison.exact_history_identity
        and (comparison.resumed_history is not None)
        and (comparison.resumed_history == comparison.uninterrupted_history)
    )
    loc = evidence.production_loc
    loc_pass = (
        loc.status != "indeterminate"
        and loc.estimated_reduction_percent is not None
        and (loc.estimated_reduction_percent >= loc.required_reduction_percent)
    )
    return GateRecord(
        known_optima=_known_gate(evidence),
        deterministic_repeats=_repeat_gate(evidence),
        family_fairness=_fairness_gate(evidence),
        cache_and_diagnostics=_cache_diagnostics_gate(evidence),
        exact_public_state_replay=replay,
        production_loc_reduction=loc_pass,
    )


def decide_probe(gates: Mapping[str, object]) -> JsonObject:
    """Apply the immutable all-gates adoption rule without changing production."""
    unknown = sorted(set(gates) - set(GATE_NAMES))
    failed = (
        [f"unknown:{name}" for name in unknown]
        if unknown
        else [name for name in GATE_NAMES if type(gates.get(name)) is not bool or not gates[name]]
    )
    return {
        "outcome": "pass" if not failed else "reject",
        "failed_gates": failed,
        "production_changed": False,
        "production_strategy": "basic_generational",
    }


def policy() -> PolicyRecord:
    return PolicyRecord(
        production_changed=False,
        pymoo_version=cast(Literal["0.6.2"], pymoo.__version__),
        family_names=FAMILY_NAMES,
        independent_optimizer_per_family=True,
        categorical_family_variable=False,
        initial_evaluation_budget=INITIAL_EVALUATION_BUDGET,
        total_generations=TOTAL_GENERATIONS,
        search_seed=SEARCH_SEED,
        trial_seeds=TRIAL_SEEDS,
        champion_seed=CHAMPION_SEED,
        window_seconds=WINDOW_SECONDS,
        generation_limits=GENERATION_LIMITS,
        invalid_generation_limits=INVALID_CASE_LIMITS,
        similarity=SIMILARITY,
        cache_key_fields=CACHE_KEY_FIELDS,
        checkpoint_fields=CHECKPOINT_FIELDS,
        replay_checked_fields=REPLAY_CHECKED_FIELDS,
        checkpoint_encoding="canonical-json",
        prohibited_serializers=PROHIBITED_SERIALIZERS,
        minimum_loc_reduction_percent=MINIMUM_LOC_REDUCTION_PERCENT,
        known_population_size=KNOWN_POPULATION_SIZE,
        known_generations=KNOWN_GENERATIONS,
        known_tolerances=KNOWN_TOLERANCES,
    )
