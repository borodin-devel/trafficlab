"""Shared immutable checkpoint fixtures and mutation builders."""

import json
import math
import platform
from dataclasses import replace as replace_dataclass
from typing import Any, cast

from pydantic import BaseModel

from trafficlab.common.compatibility import ContentIdentity
from trafficlab.common.config import (
    C2stSettings,
    DispersionSettings,
    FloatBounds,
    GenerationLimits,
    IntegerBounds,
    MethodWeights,
    PostfitSettings,
    SimilarityConfig,
    TransitionSettings,
)
from trafficlab.fitting.genetic.checkpoint import (
    CheckpointCompatibility,
    CheckpointState,
    FamilyCheckpointSpec,
    GeneticCheckpointSettings,
    encode_rng_state,
    render_checkpoint,
)
from trafficlab.fitting.genetic.coordinates import GeneCoordinate
from trafficlab.fitting.genetic.types import (
    METHOD_ORDER,
    Candidate,
    CandidateFailure,
    CandidateId,
    DuplicateDiagnostic,
    HistoryRow,
    MethodTrialResult,
    TrialResult,
)
from trafficlab.generation.models.common import make_rng

MARKOV_MODEL_DIAGNOSTICS = {
    "timing_tier_transition_count": 1,
    "timing_tier_source_count": 2,
    "timing_tier_global_count": 3,
    "uniform_unobserved_row_count": 1,
}

SIMILARITY = SimilarityConfig(
    iat_diagnostic_quantile=0.5,
    acf_lags=(1,),
    acf_lag_weights=(1.0,),
    acf_iat_weight=0.5,
    acf_size_weight=0.5,
    multiscale_widths_seconds=(1.0,),
    multiscale_scale_weights=(1.0,),
    multiscale_packet_weight=0.75,
    multiscale_byte_weight=0.25,
    max_direction_bin_cells=20,
    cvm_iat_weight=0.25,
    cvm_size_weight=0.75,
    ad_iat_weight=0.75,
    ad_size_weight=0.25,
    js_iat_bin_count=7,
    js_iat_weight=0.6,
    js_mark_weight=0.4,
    mmd_feature_count=12,
    mmd_seed=42,
    mmd_scale_floor=0.01,
    method_weights=MethodWeights(
        frame_size_ks=0.1,
        iat_ks=0.15,
        autocorrelation=0.05,
        multiscale_rate=0.2,
        cramer_von_mises=0.05,
        anderson_darling=0.1,
        jensen_shannon=0.15,
        approximate_mmd=0.2,
    ),
    postfit=PostfitSettings(
        dispersion=DispersionSettings(
            widths_seconds=(0.5, 1.0),
            scale_weights=(0.5, 0.5),
            fano_weight=0.5,
            allan_weight=0.5,
        ),
        transition=TransitionSettings(
            size_bin_count=2,
            iat_bin_count=2,
            pseudocount=0.5,
            occupancy_weight=0.34,
            transition_rows_weight=0.33,
            runs_weight=0.33,
        ),
        c2st=C2stSettings(
            feature_version="window-v1",
            window_width_seconds=0.25,
            fold_count=2,
            guard_window_count=1,
            maximum_window_count=64,
            l2_regularization=1.0,
            maximum_iterations=100,
            tolerance=1e-9,
        ),
    ),
)


def build_trial(seed: int, scores: tuple[float, float, float, float, float, float, float, float]) -> TrialResult:
    methods = tuple(
        MethodTrialResult(
            name=name, score=score, diagnostics={"nested": [{"finite": score, "enabled": True}], "empty": None}
        )
        for name, score in zip(METHOD_ORDER, scores, strict=True)
    )
    aggregate = math.fsum(
        (
            scores[0] * 0.05,
            scores[1] * 0.1,
            scores[2] * 0.15,
            scores[3] * 0.2,
            scores[4] * 0.05,
            scores[5] * 0.1,
            scores[6] * 0.15,
            scores[7] * 0.2,
        )
    )
    return TrialResult(seed=seed, aggregate_score=aggregate, methods=cast(Any, methods))


MMPP_TRIAL = build_trial(7, (0.8, 0.6, 0.4, 0.2, 0.8, 0.6, 0.4, 0.2))

POISSON_TRIAL = build_trial(7, (0.9, 0.7, 0.5, 0.3, 0.9, 0.7, 0.5, 0.3))

POPULATION = (
    Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=0),
        family="mmpp",
        genes=(1.0, 2.0, 3.0, 4.0),
        status="valid",
        fitness=MMPP_TRIAL.aggregate_score,
        trials=(MMPP_TRIAL,),
        invalid=None,
        duplicate_diagnostics=(),
    ),
    Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=1),
        family="poisson_empirical",
        genes=None,
        status="invalid",
        fitness=0.0,
        trials=(),
        invalid=CandidateFailure(
            kind="repair",
            seed=None,
            detail="no canonical genes",
            stage="fit",
            affected_evidence="candidate genes",
            evidence_state="diagnostic_only",
            corrective_action="provide canonical candidate genes",
            authority="primary",
        ),
        duplicate_diagnostics=(DuplicateDiagnostic(attempt=0, outcome="exhausted", detail="source-equal child"),),
    ),
    Candidate(
        identifier=CandidateId(birth_generation=0, birth_index=2),
        family="poisson_empirical",
        genes=(1.0,),
        status="valid",
        fitness=POISSON_TRIAL.aggregate_score,
        trials=(POISSON_TRIAL,),
        invalid=None,
        duplicate_diagnostics=(),
    ),
)

MMPP_ROW = HistoryRow(
    generation=0,
    scope="family",
    family="mmpp",
    candidate_count=1,
    valid_count=1,
    best_fitness=MMPP_TRIAL.aggregate_score,
    mean_fitness=MMPP_TRIAL.aggregate_score,
    best_identifier=CandidateId(birth_generation=0, birth_index=0),
)

POISSON_ROW = HistoryRow(
    generation=0,
    scope="family",
    family="poisson_empirical",
    candidate_count=2,
    valid_count=1,
    best_fitness=POISSON_TRIAL.aggregate_score,
    mean_fitness=POISSON_TRIAL.aggregate_score / 2.0,
    best_identifier=CandidateId(birth_generation=0, birth_index=2),
)

OVERALL_ROW = HistoryRow(
    generation=0,
    scope="overall",
    family=None,
    candidate_count=3,
    valid_count=2,
    best_fitness=POISSON_TRIAL.aggregate_score,
    mean_fitness=math.fsum(candidate.fitness for candidate in POPULATION) / 3.0,
    best_identifier=CandidateId(birth_generation=0, birth_index=2),
)

FAMILIES = (
    FamilyCheckpointSpec(
        name="mmpp",
        gene_order=("q01", "q10", "lambda0", "lambda1"),
        coordinates=(
            GeneCoordinate("q01", "log", FloatBounds(lower=0.1, upper=10.0)),
            GeneCoordinate("q10", "log", FloatBounds(lower=0.1, upper=10.0)),
            GeneCoordinate("lambda0", "log", FloatBounds(lower=0.1, upper=10.0)),
            GeneCoordinate("lambda1", "log", FloatBounds(lower=0.1, upper=10.0)),
        ),
        crossover_probability=0.8,
        mutation_probability=0.3,
        mutation_scale=0.2,
    ),
    FamilyCheckpointSpec(
        name="poisson_empirical",
        gene_order=("c_lambda",),
        coordinates=(GeneCoordinate("c_lambda", "log", FloatBounds(lower=0.25, upper=4.0)),),
        crossover_probability=0.9,
        mutation_probability=1.0,
        mutation_scale=0.1,
    ),
)

GENETIC = GeneticCheckpointSettings(
    master_seed=73,
    final_seed=101,
    population_size=3,
    generation_count=2,
    tournament_size=2,
    elite_count=1,
    duplicate_mutation_attempts=1,
    early_stopping_generations=2,
    early_stopping_tolerance=0.0,
    resume=True,
)

COMPATIBILITY = CheckpointCompatibility(
    scientific_artifact_schema=5,
    experiment_identity=ContentIdentity(size=101, sha256="a" * 64),
    reference_identity=ContentIdentity(size=102, sha256="b" * 64),
    capture_identity=ContentIdentity(size=103, sha256="c" * 64),
    observation_window_seconds=2.0,
    trial_seeds=(7,),
    trial_limits=GenerationLimits(max_packets=1_000, max_output_bytes=2_000, max_wall_seconds=3.0),
    families=FAMILIES,
    family_priority=("mmpp", "poisson_empirical"),
    genetic=GENETIC,
    similarity=SIMILARITY,
    python_version=platform.python_version(),
    rng_engine="numpy.random.Generator/PCG64",
)

VALID_STATE = CheckpointState(
    compatibility=COMPATIBILITY,
    generation=0,
    population=POPULATION,
    history=(MMPP_ROW, POISSON_ROW, OVERALL_ROW),
    rng_state=encode_rng_state(make_rng(73)),
    best_identifier=CandidateId(birth_generation=0, birth_index=2),
    best_fitness=POISSON_TRIAL.aggregate_score,
    consecutive_stagnation=0,
    terminal_reason="running",
    family_priority=("mmpp", "poisson_empirical"),
)


def replace[Record](record: Record, **changes: object) -> Record:
    """Build deliberate corrupt model states for renderer/cross-policy tests only."""
    if isinstance(record, BaseModel):
        values = {name: getattr(record, name) for name in type(record).model_fields}
        values.update(changes)
        return cast(Record, type(record).model_construct(**values))
    return cast(Record, replace_dataclass(cast(Any, record), **changes))


def decoded_checkpoint(content: bytes | None = None) -> dict[str, object]:
    return json.loads(content or render_checkpoint(VALID_STATE))


def encoded_checkpoint(data: object) -> bytes:
    return (json.dumps(data, sort_keys=True, indent=2, allow_nan=True) + "\n").encode()


def changed_checkpoint(path: tuple[str | int, ...], value: object) -> bytes:
    root = decoded_checkpoint()
    data: object = root
    for part in path[:-1]:
        data = (
            cast(dict[str, object], data)[part]  # pyright: ignore[reportUnnecessaryCast]
            if isinstance(part, str)
            else cast(list[object], data)[part]
        )
    final = path[-1]
    if isinstance(final, str):
        cast(dict[str, object], data)[final] = value
    else:
        cast(list[object], data)[final] = value
    return encoded_checkpoint(root)


def mutated_checkpoint_document(path: tuple[str | int, ...], value: object) -> dict[str, object]:
    return decoded_checkpoint(changed_checkpoint(path, value))


def checkpoint_without(path: tuple[str | int, ...]) -> bytes:
    root = decoded_checkpoint()
    current: object = root
    for part in path[:-1]:
        current = (
            cast(dict[str, object], current)[part]  # pyright: ignore[reportUnnecessaryCast]
            if isinstance(part, str)
            else cast(list[object], current)[part]
        )
    final = path[-1]
    assert isinstance(final, str)
    del cast(dict[str, object], current)[final]
    return encoded_checkpoint(root)


def history_for_generation(generation: int) -> tuple[HistoryRow, ...]:
    rows: list[HistoryRow] = []
    for current in range(generation + 1):
        rows.extend(replace(row, generation=current) for row in (MMPP_ROW, POISSON_ROW, OVERALL_ROW))
    return tuple(rows)


def candidate_update(**changes: object) -> bytes:
    data = decoded_checkpoint()
    candidate = cast(dict[str, object], cast(list[object], data["population"])[0])
    candidate.update(changes)
    return encoded_checkpoint(data)


def state_at(
    generation: int,
    *,
    generation_count: int,
    early_stopping_generations: int = 0,
    consecutive_stagnation: int | None = None,
    terminal_reason: str = "running",
) -> CheckpointState:
    genetic = replace(
        GENETIC,
        generation_count=generation_count,
        early_stopping_generations=early_stopping_generations,
    )
    compatibility = replace(COMPATIBILITY, genetic=genetic)
    return replace(
        VALID_STATE,
        compatibility=compatibility,
        generation=generation,
        history=history_for_generation(generation),
        consecutive_stagnation=generation if consecutive_stagnation is None else consecutive_stagnation,
        terminal_reason=cast(Any, terminal_reason),
    )


def state_with_generation_best_history(
    best_fitnesses: tuple[float, ...],
    *,
    generation_count: int,
    early_stopping_generations: int,
    early_stopping_tolerance: float,
    consecutive_stagnation: int,
    terminal_reason: str,
) -> CheckpointState:
    """Build literal overall-generation best evidence ending at the current population's 0.5 winner."""
    assert best_fitnesses and best_fitnesses[-1] == POISSON_TRIAL.aggregate_score
    history: list[HistoryRow] = []
    for generation, best_fitness in enumerate(best_fitnesses):
        mmpp = replace(MMPP_ROW, generation=generation)
        poisson = replace(
            POISSON_ROW,
            generation=generation,
            best_fitness=best_fitness,
            mean_fitness=best_fitness / 2.0,
        )
        history.extend(
            (
                mmpp,
                poisson,
                HistoryRow(
                    generation=generation,
                    scope="overall",
                    family=None,
                    candidate_count=3,
                    valid_count=2,
                    best_fitness=best_fitness,
                    mean_fitness=math.fsum((mmpp.mean_fitness, poisson.mean_fitness * 2)) / 3.0,
                    best_identifier=CandidateId(birth_generation=0, birth_index=2),
                ),
            )
        )
    genetic = replace(
        GENETIC,
        generation_count=generation_count,
        early_stopping_generations=early_stopping_generations,
        early_stopping_tolerance=early_stopping_tolerance,
    )
    return replace(
        VALID_STATE,
        compatibility=replace(COMPATIBILITY, genetic=genetic),
        generation=len(best_fitnesses) - 1,
        history=tuple(history),
        consecutive_stagnation=consecutive_stagnation,
        terminal_reason=cast(Any, terminal_reason),
    )


def checkpoint_bytes_with_early_limit(state: CheckpointState) -> bytes:
    """Render the same lineage with early stop disabled, then restore the target public JSON fields."""
    genetic = state.compatibility.genetic
    assert genetic.early_stopping_generations > 0
    disabled_genetic = replace(genetic, early_stopping_generations=0)
    disabled = replace(
        state,
        compatibility=replace(state.compatibility, genetic=disabled_genetic),
        terminal_reason="hard_limit" if state.generation == genetic.generation_count else "running",
    )
    document = cast(dict[str, object], json.loads(render_checkpoint(disabled)))
    cast(dict[str, object], document["genetic"])["early_stopping_generations"] = genetic.early_stopping_generations
    document["terminal_reason"] = state.terminal_reason
    return encoded_checkpoint(document)


def markov_state(genes: tuple[float, float, float, int, float]) -> CheckpointState:
    markov = FamilyCheckpointSpec(
        name="markov_renewal",
        gene_order=("q1", "q2", "alpha", "r", "c_t"),
        coordinates=(
            GeneCoordinate("q1", "linear", FloatBounds(lower=0.1, upper=0.8)),
            GeneCoordinate("q2", "linear", FloatBounds(lower=0.2, upper=0.9)),
            GeneCoordinate("alpha", "linear", FloatBounds(lower=0.0, upper=1.0)),
            GeneCoordinate("r", "integer", IntegerBounds(lower=1, upper=5)),
            GeneCoordinate("c_t", "log", FloatBounds(lower=0.1, upper=10.0)),
        ),
        crossover_probability=0.8,
        mutation_probability=0.3,
        mutation_scale=0.2,
    )
    compatibility = replace(
        COMPATIBILITY,
        families=(markov, FAMILIES[1]),
        family_priority=("markov_renewal", "poisson_empirical"),
    )
    markovtrial = replace(MMPP_TRIAL, model_diagnostics=MARKOV_MODEL_DIAGNOSTICS)
    population = (
        replace(POPULATION[0], family="markov_renewal", genes=genes, trials=(markovtrial,)),
        *POPULATION[1:],
    )
    markov_row = replace(MMPP_ROW, family="markov_renewal")
    return replace(
        VALID_STATE,
        compatibility=compatibility,
        population=population,
        history=(markov_row, POISSON_ROW, OVERALL_ROW),
        family_priority=compatibility.family_priority,
    )
