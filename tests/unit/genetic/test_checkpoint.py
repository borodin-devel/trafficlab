import json
import math
import platform
from dataclasses import replace
from pathlib import Path
from random import Random
from typing import Any, cast

import pytest

import trafficlab.genetic.checkpoint as checkpoint
from trafficlab.config import (
    FloatBounds,
    GenerationLimits,
    IntegerBounds,
    MethodWeights,
    MmppConfig,
    SimilarityConfig,
)
from trafficlab.errors import TrafficlabError
from trafficlab.genetic.checkpoint import (
    CheckpointCompatibility,
    CheckpointState,
    FamilyCheckpointSpec,
    GeneticCheckpointSettings,
    decode_rng_state,
    encode_rng_state,
    load_checkpoint,
    load_generation,
    parse_checkpoint,
    publish_checkpoint,
    publish_generation,
    publish_history_csv,
    render_checkpoint,
    render_history_csv,
    summarize_generation,
    validate_compatibility,
)
from trafficlab.genetic.coordinates import GeneCoordinate
from trafficlab.genetic.evaluation import EvaluationContext, evaluate_candidate, validate_evaluation_context
from trafficlab.genetic.operators import ReproductionContext, reproduce_child
from trafficlab.genetic.types import (
    METHOD_ORDER,
    Candidate,
    CandidateFailure,
    CandidateId,
    DuplicateDiagnostic,
    HistoryRow,
    MethodTrialResult,
    TrialResult,
)
from trafficlab.models.common import Genes
from trafficlab.models.registry import MMPP_FAMILY
from trafficlab.trace import Direction, TraceEvent

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
    method_weights=MethodWeights(
        frame_size_ks=0.2,
        iat_ks=0.3,
        autocorrelation=0.1,
        multiscale_rate=0.4,
    ),
)


def _trial(seed: int, scores: tuple[float, float, float, float]) -> TrialResult:
    methods = tuple(
        MethodTrialResult(name, score, {"nested": [{"finite": score, "enabled": True}], "empty": None})
        for name, score in zip(METHOD_ORDER, scores, strict=True)
    )
    aggregate = math.fsum(
        (
            scores[0] * 0.1,
            scores[1] * 0.2,
            scores[2] * 0.3,
            scores[3] * 0.4,
        )
    )
    return TrialResult(seed, aggregate, cast(Any, methods))


MMPP_TRIAL = _trial(7, (0.8, 0.6, 0.4, 0.2))
POISSON_TRIAL = _trial(7, (0.9, 0.7, 0.5, 0.3))
POPULATION = (
    Candidate(
        CandidateId(0, 0), "mmpp", (1.0, 2.0, 3.0, 4.0), "valid", MMPP_TRIAL.aggregate_score, (MMPP_TRIAL,), None, ()
    ),
    Candidate(
        CandidateId(0, 1),
        "poisson_empirical",
        None,
        "invalid",
        0.0,
        (),
        CandidateFailure(
            "repair",
            None,
            "no canonical genes",
            stage="fit",
            affected_evidence="candidate genes",
            evidence_state="diagnostic_only",
            corrective_action="provide canonical candidate genes",
            authority="primary",
        ),
        (DuplicateDiagnostic(0, "exhausted", "source-equal child"),),
    ),
    Candidate(
        CandidateId(0, 2),
        "poisson_empirical",
        (1.0,),
        "valid",
        POISSON_TRIAL.aggregate_score,
        (POISSON_TRIAL,),
        None,
        (),
    ),
)
MMPP_ROW = HistoryRow(
    0, "family", "mmpp", 1, 1, MMPP_TRIAL.aggregate_score, MMPP_TRIAL.aggregate_score, CandidateId(0, 0)
)
POISSON_ROW = HistoryRow(
    0,
    "family",
    "poisson_empirical",
    2,
    1,
    POISSON_TRIAL.aggregate_score,
    POISSON_TRIAL.aggregate_score / 2.0,
    CandidateId(0, 2),
)
OVERALL_ROW = HistoryRow(
    0,
    "overall",
    None,
    3,
    2,
    POISSON_TRIAL.aggregate_score,
    math.fsum(candidate.fitness for candidate in POPULATION) / 3.0,
    CandidateId(0, 2),
)
FAMILIES = (
    FamilyCheckpointSpec(
        "mmpp",
        ("q01", "q10", "lambda0", "lambda1"),
        (
            GeneCoordinate("q01", "log", FloatBounds(lower=0.1, upper=10.0)),
            GeneCoordinate("q10", "log", FloatBounds(lower=0.1, upper=10.0)),
            GeneCoordinate("lambda0", "log", FloatBounds(lower=0.1, upper=10.0)),
            GeneCoordinate("lambda1", "log", FloatBounds(lower=0.1, upper=10.0)),
        ),
        0.8,
        0.3,
        0.2,
    ),
    FamilyCheckpointSpec(
        "poisson_empirical",
        ("c_lambda",),
        (GeneCoordinate("c_lambda", "log", FloatBounds(lower=0.25, upper=4.0)),),
        0.9,
        1.0,
        0.1,
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
    experiment_sha256="a" * 64,
    reference_sha256="b" * 64,
    capture_sha256="c" * 64,
    observation_window_seconds=2.0,
    trial_seeds=(7,),
    families=FAMILIES,
    genetic=GENETIC,
    similarity=SIMILARITY,
    python_version=platform.python_version(),
    rng_engine="python.random.Random/MT19937",
)
VALID_STATE = CheckpointState(
    COMPATIBILITY,
    0,
    POPULATION,
    (MMPP_ROW, POISSON_ROW, OVERALL_ROW),
    encode_rng_state(Random(73).getstate()),
    CandidateId(0, 2),
    POISSON_TRIAL.aggregate_score,
    0,
    "running",
)


def _decoded(content: bytes | None = None) -> dict[str, object]:
    return json.loads(content or render_checkpoint(VALID_STATE))


def _encoded(data: object) -> bytes:
    return (json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=True) + "\n").encode()


def _changed(path: tuple[str | int, ...], value: object) -> bytes:
    root = _decoded()
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
    return _encoded(root)


def _mutated(path: tuple[str | int, ...], value: object) -> dict[str, object]:
    return _decoded(_changed(path, value))


def _without(path: tuple[str | int, ...]) -> bytes:
    root = _decoded()
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
    return _encoded(root)


def _history_for_generation(generation: int) -> tuple[HistoryRow, ...]:
    rows: list[HistoryRow] = []
    for current in range(generation + 1):
        rows.extend(replace(row, generation=current) for row in (MMPP_ROW, POISSON_ROW, OVERALL_ROW))
    return tuple(rows)


def _candidate_update(**changes: object) -> bytes:
    data = _decoded()
    candidate = cast(dict[str, object], cast(list[object], data["population"])[0])
    candidate.update(changes)
    return _encoded(data)


def _state_at(
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
        history=_history_for_generation(generation),
        consecutive_stagnation=generation if consecutive_stagnation is None else consecutive_stagnation,
        terminal_reason=cast(Any, terminal_reason),
    )


def _state_with_generation_best_history(
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
                    generation,
                    "overall",
                    None,
                    3,
                    2,
                    best_fitness,
                    math.fsum((mmpp.mean_fitness, poisson.mean_fitness * 2)) / 3.0,
                    CandidateId(0, 2),
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


def _checkpoint_bytes_with_early_limit(state: CheckpointState) -> bytes:
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
    return _encoded(document)


def _markov_state(genes: tuple[float, float, float, int, float]) -> CheckpointState:
    markov = FamilyCheckpointSpec(
        "markov_renewal",
        ("q1", "q2", "alpha", "r", "c_t"),
        (
            GeneCoordinate("q1", "linear", FloatBounds(lower=0.1, upper=0.8)),
            GeneCoordinate("q2", "linear", FloatBounds(lower=0.2, upper=0.9)),
            GeneCoordinate("alpha", "linear", FloatBounds(lower=0.0, upper=1.0)),
            GeneCoordinate("r", "integer", IntegerBounds(lower=1, upper=5)),
            GeneCoordinate("c_t", "log", FloatBounds(lower=0.1, upper=10.0)),
        ),
        0.8,
        0.3,
        0.2,
    )
    compatibility = replace(COMPATIBILITY, families=(markov, FAMILIES[1]))
    population = (replace(POPULATION[0], family="markov_renewal", genes=genes), *POPULATION[1:])
    markov_row = replace(MMPP_ROW, family="markov_renewal")
    return replace(
        VALID_STATE,
        compatibility=compatibility,
        population=population,
        history=(markov_row, POISSON_ROW, OVERALL_ROW),
    )


def test_rng_state_round_trip_reproduces_all_next_primitives() -> None:
    rng = Random(73)
    _ = (rng.random(), rng.randrange(9), rng.normalvariate(0.0, 0.1))
    restored = decode_rng_state(encode_rng_state(rng.getstate()))
    clone = Random()
    clone.setstate(restored)
    assert (clone.random(), clone.randrange(9), clone.normalvariate(0.0, 0.1)) == (
        rng.random(),
        rng.randrange(9),
        rng.normalvariate(0.0, 0.1),
    )


def test_rng_codec_requires_the_exact_runtime_state_version() -> None:
    current = Random().getstate()
    assert decode_rng_state(encode_rng_state(current)) == current

    version, internal, gaussian = current
    with pytest.raises(TrafficlabError, match="state_version"):
        encode_rng_state((version + 1, internal, gaussian))


def test_checkpoint_round_trip_is_canonical_and_preserves_frozen_nested_diagnostics() -> None:
    content = render_checkpoint(VALID_STATE)
    loaded = parse_checkpoint(content, COMPATIBILITY)
    assert loaded == VALID_STATE
    assert content.endswith(b"\n")
    decoded = json.loads(content)
    assert content == (json.dumps(decoded, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert b'"schema"' not in content
    assert tuple(method.name for method in loaded.population[0].trials[0].methods) == METHOD_ORDER
    with pytest.raises(TypeError):
        cast(dict[str, object], loaded.population[0].trials[0].methods[0].diagnostics)["changed"] = True


def test_repair_failed_offspring_round_trips_without_unvalidated_genes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real repair-invalid child must remain scientific evidence instead of aborting checkpoint publication."""
    reference = (
        TraceEvent(0.0, Direction.OUTBOUND, 64),
        TraceEvent(1.0, Direction.INBOUND, 128),
        TraceEvent(2.0, Direction.OUTBOUND, 256),
    )
    bounds = MmppConfig(
        crossover_probability=0.8,
        mutation_probability=0.3,
        mutation_scale=0.2,
        q01=FloatBounds(lower=0.1, upper=10.0),
        q10=FloatBounds(lower=0.1, upper=10.0),
        lambda0=FloatBounds(lower=0.1, upper=10.0),
        lambda1=FloatBounds(lower=0.1, upper=10.0),
    )
    evaluation = validate_evaluation_context(
        EvaluationContext(
            reference=reference,
            window=2.0,
            families={"mmpp": MMPP_FAMILY},
            bounds={"mmpp": bounds},
            trial_seeds=(7,),
            trial_limits=GenerationLimits(max_packets=20, max_output_bytes=20_000, max_wall_seconds=2.0),
            similarity=SIMILARITY,
        )
    )
    parent = POPULATION[0]
    other = replace(parent, identifier=CandidateId(0, 3), genes=(2.0, 3.0, 1.0, 2.0))

    def fail_repair(*_args: object, **_kwargs: object) -> Genes:
        raise TrafficlabError("offspring repair failed", corrective_action="retain invalid evidence")

    monkeypatch.setattr(type(MMPP_FAMILY), "repair", fail_repair)
    rng = Random(35)
    child = reproduce_child(
        parent,
        other,
        context=ReproductionContext(
            reference=reference,
            family_bounds={"mmpp": bounds},
            duplicate_mutation_attempts=1,
        ),
        identifier=CandidateId(1, 0),
        rng=rng,
    )
    evaluated_child = evaluate_candidate(child, evaluation)
    current_population = (parent, evaluated_child, POPULATION[2])
    history = VALID_STATE.history + summarize_generation(
        1,
        current_population,
        ("mmpp", "poisson_empirical"),
    )
    state = replace(
        VALID_STATE,
        generation=1,
        population=current_population,
        history=history,
        rng_state=encode_rng_state(rng.getstate()),
        consecutive_stagnation=1,
    )

    loaded = parse_checkpoint(render_checkpoint(state), COMPATIBILITY)
    stored = next(candidate for candidate in loaded.population if candidate.identifier == CandidateId(1, 0))
    assert (stored.status, stored.genes, stored.fitness, stored.trials) == ("invalid", None, 0.0, ())
    assert stored.invalid == CandidateFailure(
        "repair",
        None,
        "offspring repair failed",
        stage="fit",
        affected_evidence="candidate genes",
        evidence_state="diagnostic_only",
        corrective_action="retain invalid evidence",
        authority="primary",
    )
    assert loaded.history[-1].valid_count == 2


def test_checkpoint_round_trip_preserves_candidate_failure_scientific_diagnostics() -> None:
    """Candidate-invalid provenance is exact checkpoint evidence, not an in-memory-only detail."""
    failure = CandidateFailure(
        "incomplete_generation",
        7,
        "max_packets",
        stage="generate",
        affected_evidence="candidate trace",
        evidence_state="not_published",
        corrective_action="increase generation limits or repair the candidate model",
        authority="primary",
    )
    state = replace(VALID_STATE, population=(POPULATION[0], replace(POPULATION[1], invalid=failure), POPULATION[2]))

    document = _decoded(render_checkpoint(state))
    assert cast(list[dict[str, object]], document["population"])[1]["invalid"] == {
        "kind": "incomplete_generation",
        "seed": 7,
        "detail": "max_packets",
        "stage": "generate",
        "affected_evidence": "candidate trace",
        "evidence_state": "not_published",
        "corrective_action": "increase generation limits or repair the candidate model",
        "authority": "primary",
    }
    assert parse_checkpoint(render_checkpoint(state), COMPATIBILITY).population[1].invalid == failure

    del cast(dict[str, object], cast(list[dict[str, object]], document["population"])[1]["invalid"])["authority"]
    with pytest.raises(TrafficlabError, match="candidate invalid diagnostic"):
        parse_checkpoint(_encoded(document), COMPATIBILITY)


def test_checkpoint_rejects_non_null_gaussian_cache_and_duplicate_candidate_ids() -> None:
    with pytest.raises(TrafficlabError, match="gauss_next"):
        parse_checkpoint(_changed(("rng", "state", "gauss_next"), 0.5), COMPATIBILITY)

    duplicate = _decoded()
    population = cast(list[dict[str, object]], duplicate["population"])
    population[1]["identifier"] = population[0]["identifier"]
    with pytest.raises(TrafficlabError, match="duplicate candidate"):
        parse_checkpoint(_encoded(duplicate), COMPATIBILITY)


@pytest.mark.parametrize(
    "content",
    [
        _candidate_update(extra=1),
        _candidate_update(fitness=True),
        _candidate_update(fitness=math.nan),
        _candidate_update(trials={}),
    ],
)
def test_checkpoint_rejects_nested_shape_type_and_number_errors(content: bytes) -> None:
    with pytest.raises(TrafficlabError, match="checkpoint"):
        parse_checkpoint(content, COMPATIBILITY)


def test_checkpoint_rejects_duplicate_json_keys() -> None:
    content = render_checkpoint(VALID_STATE)
    duplicate_key = content.replace(b'{"best":', b'{"best":null,"best":', 1)
    with pytest.raises(TrafficlabError, match="duplicate JSON key"):
        parse_checkpoint(duplicate_key, COMPATIBILITY)


@pytest.mark.parametrize(
    "content",
    [
        b"\xff",
        b"{",
        b"[]\n",
        b'{"experiment_sha256":NaN}\n',
        _without(("capture_sha256",)),
        _encoded({**_decoded(), "unknown": 1}),
    ],
)
def test_checkpoint_rejects_encoding_syntax_root_shape_and_exact_key_errors(content: bytes) -> None:
    with pytest.raises(TrafficlabError, match="checkpoint"):
        parse_checkpoint(content, COMPATIBILITY)


def test_checkpoint_rejects_noncanonical_but_equivalent_json() -> None:
    canonical = render_checkpoint(VALID_STATE)
    data = json.loads(canonical)
    noncanonical = json.dumps(data, indent=2).encode()
    with pytest.raises(TrafficlabError, match="canonical"):
        parse_checkpoint(noncanonical, COMPATIBILITY)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("reference_sha256",), "A" * 64),
        (("capture_sha256",), "short"),
        (("observation_window_seconds",), 2),
        (("observation_window_seconds",), 0.0),
        (("trial_seeds",), []),
        (("trial_seeds",), [7, 7]),
        (("trial_seeds",), [True]),
        (("families",), []),
        (("families", 0, "name"), "unknown"),
        (("families", 0, "gene_order"), []),
        (("families", 0, "gene_order"), ["q01", "q01", "lambda0", "lambda1"]),
        (("families", 0, "coordinates"), {}),
        (("families", 0, "coordinates", 0, "kind"), "curved"),
        (("families", 0, "coordinates", 0, "lower"), 0),
        (("families", 0, "coordinates", 0, "upper"), math.inf),
        (("families", 0, "operators", "mutation_probability"), True),
        (("families", 0, "operators", "mutation_scale"), 0.0),
        (("genetic", "population_size"), True),
        (("genetic", "tournament_size"), 4),
        (("genetic", "elite_count"), 3),
        (("genetic", "early_stopping_generations"), 3),
        (("genetic", "early_stopping_tolerance"), 0),
        (("genetic", "resume"), 1),
        (("similarity", "method_weights", "frame_size_ks"), 1),
        (("similarity", "method_weights", "frame_size_ks"), 0.5),
        (("similarity", "acf_lags"), [True]),
        (("similarity", "multiscale_widths_seconds"), [0.0]),
        (("rng", "engine"), "other"),
        (("rng", "python_version"), ""),
        (("rng", "state", "state_version"), True),
        (("rng", "state", "mt_state"), [0] * 623),
        (("rng", "state", "mt_state", 0), -1),
        (("rng", "state", "index"), 625),
    ],
)
def test_checkpoint_rejects_strict_compatibility_metadata_and_rng_corruption(
    path: tuple[str | int, ...], value: object
) -> None:
    with pytest.raises(TrafficlabError, match="checkpoint"):
        parse_checkpoint(_changed(path, value), COMPATIBILITY)


def test_checkpoint_rejects_noncanonical_mmpp_and_markov_genes_but_accepts_boundaries() -> None:
    invalid_mmpp = replace(
        VALID_STATE,
        population=(replace(POPULATION[0], genes=(1.0, 2.0, 4.0, 3.0)), *POPULATION[1:]),
    )
    with pytest.raises(TrafficlabError, match="lambda0.*lambda1"):
        render_checkpoint(invalid_mmpp)

    boundary_mmpp = replace(
        VALID_STATE,
        population=(replace(POPULATION[0], genes=(0.1, 10.0, 0.1, 10.0)), *POPULATION[1:]),
    )
    assert parse_checkpoint(render_checkpoint(boundary_mmpp), COMPATIBILITY) == boundary_mmpp

    with pytest.raises(TrafficlabError, match="q1.*q2"):
        render_checkpoint(_markov_state((0.8, 0.2, 0.0, 5, 0.1)))

    boundary_markov = _markov_state((0.1, 0.9, 0.0, 5, 0.1))
    assert parse_checkpoint(render_checkpoint(boundary_markov), boundary_markov.compatibility) == boundary_markov

    duplicate_invalid_trials = replace(POPULATION[1], trials=(MMPP_TRIAL, MMPP_TRIAL))
    with pytest.raises(TrafficlabError, match="duplicate trial seed"):
        render_checkpoint(replace(VALID_STATE, population=(POPULATION[0], duplicate_invalid_trials, POPULATION[2])))


@pytest.mark.parametrize("mutation", ["shortened", "reordered"])
def test_public_checkpoint_codec_rejects_nonregistered_family_gene_metadata(
    mutation: str,
) -> None:
    mmpp = FAMILIES[0]
    if mutation == "shortened":
        gene_order = mmpp.gene_order[:-1]
        coordinates = mmpp.coordinates[:-1]
        genes = cast(tuple[float, ...], POPULATION[0].genes)[:-1]
    else:
        gene_order = (mmpp.gene_order[1], mmpp.gene_order[0], *mmpp.gene_order[2:])
        coordinates = (mmpp.coordinates[1], mmpp.coordinates[0], *mmpp.coordinates[2:])
        original = cast(tuple[float, ...], POPULATION[0].genes)
        genes = (original[1], original[0], *original[2:])
    malformed = replace(mmpp, gene_order=gene_order, coordinates=coordinates)
    compatibility = replace(COMPATIBILITY, families=(malformed, FAMILIES[1]))
    state = replace(
        VALID_STATE,
        compatibility=compatibility,
        population=(replace(POPULATION[0], genes=genes), *POPULATION[1:]),
    )

    with pytest.raises(TrafficlabError, match="registered gene order"):
        render_checkpoint(state)

    document = _decoded()
    stored_family = cast(list[dict[str, object]], document["families"])[0]
    stored_family["gene_order"] = list(gene_order)
    stored_family["coordinates"] = [
        {
            "name": coordinate.name,
            "kind": coordinate.kind,
            "lower": coordinate.bounds.lower,
            "upper": coordinate.bounds.upper,
        }
        for coordinate in coordinates
    ]
    cast(list[dict[str, object]], document["population"])[0]["genes"] = list(genes)
    with pytest.raises(TrafficlabError, match="registered gene order"):
        parse_checkpoint(_encoded(document), compatibility)


def test_public_checkpoint_codec_accepts_exact_registered_orders_for_all_families() -> None:
    assert parse_checkpoint(render_checkpoint(VALID_STATE), COMPATIBILITY) == VALID_STATE
    markov = _markov_state((0.1, 0.9, 0.0, 5, 0.1))
    assert parse_checkpoint(render_checkpoint(markov), markov.compatibility) == markov


def test_checkpoint_rejects_duplicate_and_nonlexical_family_metadata() -> None:
    duplicate = _decoded()
    families = cast(list[object], duplicate["families"])
    families[1] = families[0]
    with pytest.raises(TrafficlabError, match="duplicate family"):
        parse_checkpoint(_encoded(duplicate), COMPATIBILITY)

    reversed_families = _decoded()
    cast(list[object], reversed_families["families"]).reverse()
    with pytest.raises(TrafficlabError, match="lexical"):
        parse_checkpoint(_encoded(reversed_families), COMPATIBILITY)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("population", 0, "identifier"), [0]),
        (("population", 0, "identifier"), [True, 0]),
        (("population", 0, "family"), "markov_renewal"),
        (("population", 0, "genes"), {}),
        (("population", 0, "genes"), [1.0]),
        (("population", 0, "genes", 0), 1),
        (("population", 0, "genes", 0), 100.0),
        (("population", 0, "status"), "pending"),
        (("population", 0, "fitness"), -0.1),
        (("population", 0, "trials"), {}),
        (("population", 0, "trials", 0, "seed"), True),
        (("population", 0, "trials", 0, "methods"), []),
        (("population", 0, "trials", 0, "methods", 0, "name"), "frame_size_ks"),
        (("population", 0, "trials", 0, "methods", 0, "score"), True),
        (("population", 0, "trials", 0, "methods", 0, "diagnostics"), []),
        (("population", 1, "invalid"), None),
        (("population", 1, "invalid", "kind"), "unknown"),
        (("population", 1, "invalid", "seed"), True),
        (("population", 1, "invalid", "detail"), ""),
        (("population", 1, "duplicate_diagnostics"), {}),
        (("population", 1, "duplicate_diagnostics", 0, "attempt"), True),
        (("population", 1, "duplicate_diagnostics", 0, "outcome"), "other"),
        (("population", 1, "duplicate_diagnostics", 0, "detail"), ""),
        (("history", 0, "generation"), True),
        (("history", 0, "scope"), "other"),
        (("history", 0, "family"), None),
        (("history", 2, "family"), "mmpp"),
        (("history", 0, "candidate_count"), 0),
        (("history", 0, "valid_count"), True),
        (("history", 0, "best_fitness"), True),
        (("history", 0, "best_identifier"), [0]),
        (("best", "identifier"), [9, 9]),
        (("best", "fitness"), True),
        (("consecutive_stagnation",), True),
        (("terminal_reason",), "stopped"),
    ],
)
def test_checkpoint_rejects_strict_population_history_best_and_terminal_corruption(
    path: tuple[str | int, ...], value: object
) -> None:
    with pytest.raises(TrafficlabError, match="checkpoint"):
        parse_checkpoint(_changed(path, value), COMPATIBILITY)


def test_checkpoint_recomputes_method_aggregate_candidate_fitness_and_history() -> None:
    cases = (
        (("population", 0, "trials", 0, "aggregate_score"), 0.41, "aggregate"),
        (("population", 0, "fitness"), 0.41, "fitness"),
        (("history", 0, "mean_fitness"), 0.41, "history"),
        (("history", 2, "best_identifier"), [0, 0], "history"),
    )
    for path, value, match in cases:
        with pytest.raises(TrafficlabError, match=match):
            parse_checkpoint(_changed(path, value), COMPATIBILITY)


def test_checkpoint_rejects_valid_invalid_and_duplicate_trial_seed_inconsistencies() -> None:
    valid_with_invalid = _mutated(
        ("population", 0, "invalid"),
        {
            "kind": "fit",
            "seed": None,
            "detail": "bad",
            "stage": "fit",
            "affected_evidence": "candidate model",
            "evidence_state": "diagnostic_only",
            "corrective_action": "repair the candidate model",
            "authority": "primary",
        },
    )
    with pytest.raises(TrafficlabError, match="valid candidate invalid"):
        parse_checkpoint(_encoded(valid_with_invalid), COMPATIBILITY)

    invalid_nonzero = _mutated(("population", 1, "fitness"), 0.1)
    with pytest.raises(TrafficlabError, match="invalid candidate fitness"):
        parse_checkpoint(_encoded(invalid_nonzero), COMPATIBILITY)

    duplicate_seed = _decoded()
    trial = cast(
        dict[str, object],
        cast(list[object], cast(dict[str, object], cast(list[object], duplicate_seed["population"])[0])["trials"])[0],
    )
    cast(list[object], cast(dict[str, object], cast(list[object], duplicate_seed["population"])[0])["trials"]).append(
        trial
    )
    with pytest.raises(TrafficlabError, match="trial seeds"):
        parse_checkpoint(_encoded(duplicate_seed), COMPATIBILITY)


def test_checkpoint_accepts_hard_limit_and_early_stop_and_rejects_terminal_inconsistencies() -> None:
    hard = _state_at(2, generation_count=2, terminal_reason="hard_limit")
    early = _state_at(
        2,
        generation_count=3,
        early_stopping_generations=2,
        consecutive_stagnation=2,
        terminal_reason="early_stop",
    )
    assert parse_checkpoint(render_checkpoint(hard), hard.compatibility) == hard
    assert parse_checkpoint(render_checkpoint(early), early.compatibility) == early

    inconsistent = (
        _state_at(1, generation_count=2, terminal_reason="hard_limit"),
        _state_at(2, generation_count=2, early_stopping_generations=2, consecutive_stagnation=2),
        _state_at(
            1,
            generation_count=2,
            early_stopping_generations=0,
            consecutive_stagnation=1,
            terminal_reason="early_stop",
        ),
    )
    for state in inconsistent:
        with pytest.raises(TrafficlabError, match="checkpoint"):
            render_checkpoint(state)


def test_checkpoint_rejects_smaller_and_larger_stagnation_counters_than_history() -> None:
    """A range-valid counter cannot be tampered independently of overall generation-best history."""
    state = _state_with_generation_best_history(
        (0.4375, 0.5, 0.5),
        generation_count=2,
        early_stopping_generations=2,
        early_stopping_tolerance=0.03125,
        consecutive_stagnation=1,
        terminal_reason="hard_limit",
    )
    content = render_checkpoint(state)
    assert parse_checkpoint(content, state.compatibility) == state

    for counter in (0, 2):
        document = cast(dict[str, object], json.loads(content))
        document["consecutive_stagnation"] = counter
        with pytest.raises(TrafficlabError, match="consecutive_stagnation.*history"):
            parse_checkpoint(_encoded(document), state.compatibility)


def test_checkpoint_rejects_best_that_disagrees_with_the_retained_history_winner() -> None:
    """An equal later generation best must not replace the winner retained from earlier history."""
    state = _state_at(1, generation_count=2)
    earlier_mmpp = replace(MMPP_ROW, best_fitness=0.5)
    earlier_overall = replace(OVERALL_ROW, best_identifier=CandidateId(0, 0))
    inconsistent = replace(
        state,
        history=(earlier_mmpp, POISSON_ROW, earlier_overall, *state.history[3:]),
    )

    with pytest.raises(TrafficlabError, match="retained history winner"):
        render_checkpoint(inconsistent)


@pytest.mark.parametrize(
    ("best_fitnesses", "generation_count", "early_limit", "counter", "terminal_reason"),
    [
        ((0.5,), 0, 0, 0, "hard_limit"),
        ((0.4375, 0.46875, 0.5), 3, 2, 2, "early_stop"),
        ((0.4375, 0.5, 0.5), 2, 1, 1, "hard_limit"),
    ],
)
def test_checkpoint_recomputes_generation_zero_tolerance_and_terminal_history(
    best_fitnesses: tuple[float, ...],
    generation_count: int,
    early_limit: int,
    counter: int,
    terminal_reason: str,
) -> None:
    """The codec must mirror strategy's exact <= tolerance stagnation and > tolerance reset rules."""
    state = _state_with_generation_best_history(
        best_fitnesses,
        generation_count=generation_count,
        early_stopping_generations=early_limit,
        early_stopping_tolerance=0.03125,
        consecutive_stagnation=counter,
        terminal_reason=terminal_reason,
    )

    assert parse_checkpoint(render_checkpoint(state), state.compatibility) == state


@pytest.mark.parametrize(
    ("best_fitnesses", "counter", "terminal_reason"),
    [
        ((0.4375, 0.4375, 0.4375, 0.5), 0, "running"),
        ((0.5, 0.5, 0.5, 0.5), 3, "early_stop"),
        ((0.5, 0.5, 0.5, 0.4375, 0.5), 4, "early_stop"),
    ],
)
def test_checkpoint_rejects_history_continuing_after_an_earlier_early_stop(
    best_fitnesses: tuple[float, ...],
    counter: int,
    terminal_reason: str,
) -> None:
    """A later improvement, tie, or decrease cannot revive a lineage that already terminated."""
    state = _state_with_generation_best_history(
        best_fitnesses,
        generation_count=len(best_fitnesses),
        early_stopping_generations=2,
        early_stopping_tolerance=0.03125,
        consecutive_stagnation=counter,
        terminal_reason=terminal_reason,
    )
    content = _checkpoint_bytes_with_early_limit(state)

    with pytest.raises(TrafficlabError, match="history.*early.stop"):
        render_checkpoint(state)
    with pytest.raises(TrafficlabError, match="history.*early.stop"):
        parse_checkpoint(content, state.compatibility)


@pytest.mark.parametrize(
    ("best_fitnesses", "generation_count", "early_limit", "counter", "terminal_reason"),
    [
        ((0.4375, 0.46875, 0.5), 3, 2, 2, "early_stop"),
        ((0.4375, 0.46875, 0.5), 2, 2, 2, "hard_limit"),
        ((0.4375, 0.4375, 0.5), 3, 2, 0, "running"),
        ((0.5, 0.5, 0.5, 0.5), 4, 0, 3, "running"),
        ((0.5,), 0, 0, 0, "hard_limit"),
    ],
)
def test_checkpoint_accepts_history_through_its_first_terminal_boundary(
    best_fitnesses: tuple[float, ...],
    generation_count: int,
    early_limit: int,
    counter: int,
    terminal_reason: str,
) -> None:
    """Current early/hard boundaries, disabled early stop, reset-before-limit, and G0 remain valid."""
    state = _state_with_generation_best_history(
        best_fitnesses,
        generation_count=generation_count,
        early_stopping_generations=early_limit,
        early_stopping_tolerance=0.03125,
        consecutive_stagnation=counter,
        terminal_reason=terminal_reason,
    )

    assert parse_checkpoint(render_checkpoint(state), state.compatibility) == state


def test_summarize_generation_uses_stable_identifier_tie_and_rejects_invalid_input() -> None:
    tied = tuple(
        replace(
            candidate,
            fitness=0.0,
            status="invalid",
            trials=(),
            invalid=CandidateFailure(
                "fit",
                None,
                "bad",
                stage="fit",
                affected_evidence="candidate model",
                evidence_state="diagnostic_only",
                corrective_action="repair the candidate model",
                authority="primary",
            ),
        )
        for candidate in POPULATION
    )
    rows = summarize_generation(0, tied, ("mmpp", "poisson_empirical"))
    assert rows[-1].best_identifier == CandidateId(0, 0)
    assert rows[-1].valid_count == 0
    with pytest.raises(TrafficlabError, match="empty population"):
        summarize_generation(0, (), ("mmpp", "poisson_empirical"))
    with pytest.raises(TrafficlabError, match="unique and lexical"):
        summarize_generation(0, POPULATION, ("poisson_empirical", "mmpp"))


def test_generation_summary_uses_the_same_grouped_mean_arithmetic_as_validation() -> None:
    literal_scores = (
        0.1151174344528102,
        0.24536013787059519,
        0.708159688965432,
        0.026130858846648564,
        0.18289186256684864,
        0.36189658094867017,
        0.480044697104146,
    )
    candidates = tuple(
        Candidate(
            CandidateId(0, index),
            "mmpp" if index < 3 else "poisson_empirical",
            (1.0, 2.0, 3.0, 4.0) if index < 3 else (1.0,),
            "valid",
            trial.aggregate_score,
            (trial,),
            None,
            (),
        )
        for index, score in enumerate(literal_scores)
        for trial in (_trial(7, (score, score, score, score)),)
    )
    rows = summarize_generation(0, candidates, ("mmpp", "poisson_empirical"))
    direct_mean = math.fsum(candidate.fitness for candidate in candidates) / len(candidates)
    grouped_mean = math.fsum(row.mean_fitness * row.candidate_count for row in rows[:-1]) / len(candidates)
    assert direct_mean != grouped_mean
    assert rows[-1].mean_fitness == grouped_mean
    with pytest.raises(TrafficlabError, match="has no candidate"):
        summarize_generation(0, candidates[:3], ("mmpp", "poisson_empirical"))

    compatibility = replace(COMPATIBILITY, genetic=replace(GENETIC, population_size=7))
    winner = min(candidates, key=lambda candidate: (-candidate.fitness, candidate.identifier))
    state = CheckpointState(
        compatibility,
        0,
        candidates,
        rows,
        encode_rng_state(Random(73).getstate()),
        winner.identifier,
        winner.fitness,
        0,
        "running",
    )
    assert parse_checkpoint(render_checkpoint(state), compatibility) == state


def test_rng_codec_rejects_wrong_outer_internal_word_index_and_gaussian_shapes() -> None:
    valid = Random(73).getstate()
    version, internal, _ = valid
    cases: tuple[object, ...] = (
        [],
        (version, tuple(internal[:-1]), None),
        (True, internal, None),
        (version, (True, *internal[1:]), None),
        (version, (*internal[:-1], 625), None),
        (version, internal, 0.5),
    )
    for state in cases:
        with pytest.raises(TrafficlabError, match="checkpoint"):
            encode_rng_state(state)

    with pytest.raises(TrafficlabError, match="rng state"):
        decode_rng_state(cast(Any, None))


def test_compatibility_reports_each_scientifically_relevant_difference_specifically() -> None:
    renamed_coordinate = replace(FAMILIES[0].coordinates[0], name="renamed")
    renamed_family = replace(
        FAMILIES[0],
        gene_order=("renamed", *FAMILIES[0].gene_order[1:]),
        coordinates=(renamed_coordinate, *FAMILIES[0].coordinates[1:]),
    )
    wider_coordinate = replace(
        FAMILIES[0].coordinates[0],
        bounds=FloatBounds(lower=0.05, upper=10.0),
    )
    wider_family = replace(FAMILIES[0], coordinates=(wider_coordinate, *FAMILIES[0].coordinates[1:]))
    changed_operator = replace(FAMILIES[0], mutation_probability=0.4)
    changed_similarity = SIMILARITY.model_copy(update={"iat_diagnostic_quantile": 0.6})
    cases = (
        (replace(COMPATIBILITY, experiment_sha256="d" * 64), "experiment snapshot SHA-256"),
        (replace(COMPATIBILITY, reference_sha256="d" * 64), "reference SHA-256"),
        (replace(COMPATIBILITY, capture_sha256="d" * 64), "capture SHA-256"),
        (replace(COMPATIBILITY, observation_window_seconds=3.0), "observation window"),
        (replace(COMPATIBILITY, trial_seeds=(8,)), "trial seeds"),
        (replace(COMPATIBILITY, families=(FAMILIES[1],)), "family names"),
        (replace(COMPATIBILITY, families=(renamed_family, FAMILIES[1])), "gene order"),
        (replace(COMPATIBILITY, families=(wider_family, FAMILIES[1])), "coordinate metadata"),
        (replace(COMPATIBILITY, families=(changed_operator, FAMILIES[1])), "operator values"),
        (replace(COMPATIBILITY, genetic=replace(GENETIC, final_seed=102)), "genetic setting final_seed"),
        (replace(COMPATIBILITY, similarity=changed_similarity), "similarity settings"),
        (replace(COMPATIBILITY, python_version="0.0.0"), "Python version"),
    )
    for changed, match in cases:
        with pytest.raises(TrafficlabError, match=match):
            validate_compatibility(COMPATIBILITY, changed)

    with pytest.raises(TrafficlabError, match="invalid checkpoint"):
        validate_compatibility(COMPATIBILITY, cast(Any, None))


def test_render_rejects_malformed_family_genetic_and_compatibility_instances() -> None:
    mmpp = FAMILIES[0]
    bad_coordinate_kind = replace(mmpp.coordinates[0], kind=cast(Any, "curved"))
    bad_integer_bounds = replace(mmpp.coordinates[0], kind="integer")
    bad_continuous_bounds = replace(
        mmpp.coordinates[0],
        bounds=cast(Any, IntegerBounds(lower=1, upper=2)),
    )
    bad_log_lower = replace(mmpp.coordinates[0], bounds=FloatBounds(lower=-1.0, upper=2.0))
    family_cases: tuple[Any, ...] = (
        None,
        replace(mmpp, gene_order=cast(Any, [])),
        replace(mmpp, gene_order=("", *mmpp.gene_order[1:])),
        replace(mmpp, gene_order=("q01", "q01", "lambda0", "lambda1")),
        replace(mmpp, coordinates=cast(Any, [])),
        replace(mmpp, coordinates=(cast(Any, None), *mmpp.coordinates[1:])),
        replace(mmpp, coordinates=(bad_coordinate_kind, *mmpp.coordinates[1:])),
        replace(mmpp, coordinates=(bad_integer_bounds, *mmpp.coordinates[1:])),
        replace(mmpp, coordinates=(bad_continuous_bounds, *mmpp.coordinates[1:])),
        replace(mmpp, coordinates=(bad_log_lower, *mmpp.coordinates[1:])),
        replace(mmpp, coordinates=mmpp.coordinates[:-1]),
        replace(mmpp, gene_order=("renamed", *mmpp.gene_order[1:])),
        replace(mmpp, crossover_probability=cast(Any, True)),
        replace(mmpp, mutation_scale=0.0),
    )
    for family in family_cases:
        compatibility = replace(COMPATIBILITY, families=cast(Any, (family, FAMILIES[1])))
        with pytest.raises(TrafficlabError, match="checkpoint"):
            render_checkpoint(replace(VALID_STATE, compatibility=compatibility))

    genetic_cases: tuple[Any, ...] = (
        None,
        replace(GENETIC, tournament_size=4),
        replace(GENETIC, elite_count=3),
        replace(GENETIC, population_size=2),
        replace(GENETIC, early_stopping_generations=3),
        replace(GENETIC, final_seed=7),
    )
    for genetic in genetic_cases:
        compatibility = replace(COMPATIBILITY, genetic=genetic)
        with pytest.raises(TrafficlabError, match="checkpoint"):
            render_checkpoint(replace(VALID_STATE, compatibility=compatibility))

    compatibility_cases: tuple[Any, ...] = (
        None,
        replace(COMPATIBILITY, trial_seeds=cast(Any, [])),
        replace(COMPATIBILITY, trial_seeds=()),
        replace(COMPATIBILITY, trial_seeds=(7, 7)),
        replace(COMPATIBILITY, families=cast(Any, [])),
        replace(COMPATIBILITY, families=()),
        replace(COMPATIBILITY, families=tuple(reversed(FAMILIES))),
        replace(COMPATIBILITY, families=(FAMILIES[0], FAMILIES[0])),
        replace(COMPATIBILITY, similarity=cast(Any, None)),
        replace(COMPATIBILITY, python_version=""),
        replace(COMPATIBILITY, rng_engine=cast(Any, "other")),
    )
    for compatibility in compatibility_cases:
        with pytest.raises(TrafficlabError, match="checkpoint"):
            render_checkpoint(replace(VALID_STATE, compatibility=compatibility))


def test_integer_coordinate_bounds_and_gene_are_preserved_as_exact_integers() -> None:
    coordinate = checkpoint._parse_coordinate(  # pyright: ignore[reportPrivateUsage]
        {"name": "r", "kind": "integer", "lower": 1, "upper": 5},
        family="markov_renewal",
    )
    assert coordinate.bounds.lower == 1
    assert type(coordinate.bounds.lower) is int
    assert (
        checkpoint._parse_gene(  # pyright: ignore[reportPrivateUsage]
            3, coordinate, family="markov_renewal"
        )
        == 3
    )


def test_render_rejects_population_and_best_state_inconsistencies() -> None:
    candidate = POPULATION[0]
    duplicate_trial_candidate = replace(candidate, trials=(MMPP_TRIAL, MMPP_TRIAL))
    cases = (
        cast(Any, None),
        replace(VALID_STATE, population=cast(Any, [])),
        replace(VALID_STATE, population=POPULATION[:-1]),
        replace(VALID_STATE, population=(cast(Any, None), *POPULATION[1:])),
        replace(VALID_STATE, population=(replace(candidate, family="markov_renewal"), *POPULATION[1:])),
        replace(
            VALID_STATE,
            population=(replace(candidate, identifier=CandidateId(1, 0)), *POPULATION[1:]),
        ),
        replace(VALID_STATE, population=(replace(candidate, status="pending"), *POPULATION[1:])),
        replace(VALID_STATE, population=(replace(candidate, genes=(1.0,)), *POPULATION[1:])),
        replace(VALID_STATE, population=(replace(candidate, genes=None), *POPULATION[1:])),
        replace(
            VALID_STATE,
            population=(
                replace(
                    candidate,
                    invalid=CandidateFailure(
                        "fit",
                        None,
                        "bad",
                        stage="fit",
                        affected_evidence="candidate model",
                        evidence_state="diagnostic_only",
                        corrective_action="repair the candidate model",
                        authority="primary",
                    ),
                ),
                *POPULATION[1:],
            ),
        ),
        replace(VALID_STATE, population=(duplicate_trial_candidate, *POPULATION[1:])),
        replace(
            VALID_STATE, population=(POPULATION[0], replace(POPULATION[1], identifier=CandidateId(0, 0)), POPULATION[2])
        ),
        replace(
            VALID_STATE,
            population=(
                POPULATION[0],
                replace(POPULATION[1], family="mmpp", genes=(1.0, 2.0, 3.0, 4.0)),
                replace(POPULATION[2], family="mmpp", genes=(1.0, 2.0, 3.0, 4.0)),
            ),
        ),
        replace(VALID_STATE, history=cast(Any, [])),
        replace(VALID_STATE, history=(cast(Any, None), *VALID_STATE.history[1:])),
        replace(VALID_STATE, best_identifier=CandidateId(9, 9)),
        replace(VALID_STATE, best_fitness=0.4),
        replace(VALID_STATE, best_identifier=CandidateId(0, 0), best_fitness=MMPP_TRIAL.aggregate_score),
        replace(VALID_STATE, consecutive_stagnation=1),
        replace(VALID_STATE, terminal_reason=cast(Any, "other")),
        replace(VALID_STATE, generation=3),
        replace(VALID_STATE, rng_state=cast(Any, None)),
    )
    for state in cases:
        with pytest.raises(TrafficlabError, match="checkpoint"):
            render_checkpoint(state)


def test_render_rejects_each_history_block_arithmetic_and_shape_inconsistency() -> None:
    cases = (
        (),
        (POISSON_ROW, MMPP_ROW, OVERALL_ROW),
        (replace(MMPP_ROW, candidate_count=2, valid_count=2), POISSON_ROW, OVERALL_ROW),
        (replace(MMPP_ROW, valid_count=0, best_fitness=0.0, mean_fitness=0.0), POISSON_ROW, OVERALL_ROW),
        (
            replace(MMPP_ROW, candidate_count=2, mean_fitness=0.2),
            POISSON_ROW,
            replace(OVERALL_ROW, candidate_count=4, mean_fitness=0.225),
        ),
        (MMPP_ROW, POISSON_ROW, replace(OVERALL_ROW, best_identifier=CandidateId(0, 0), best_fitness=0.4)),
        (MMPP_ROW, POISSON_ROW, replace(OVERALL_ROW, mean_fitness=0.31)),
        (
            replace(MMPP_ROW, best_fitness=0.3, mean_fitness=0.3),
            POISSON_ROW,
            replace(OVERALL_ROW, mean_fitness=math.fsum((0.3, 0.5)) / 3.0),
        ),
    )
    for history in cases:
        with pytest.raises(TrafficlabError, match="history"):
            render_checkpoint(replace(VALID_STATE, history=cast(Any, history)))


def test_every_historical_row_must_be_feasible_even_when_its_overall_row_is_adjusted() -> None:
    state = _state_at(1, generation_count=2)

    def with_first_block(family_row: HistoryRow, *, overall_valid: int = 2) -> CheckpointState:
        overall = replace(
            OVERALL_ROW,
            valid_count=overall_valid,
            mean_fitness=math.fsum((family_row.mean_fitness * family_row.candidate_count, POISSON_ROW.mean_fitness * 2))
            / 3,
        )
        return replace(state, history=(family_row, POISSON_ROW, overall, *state.history[3:]))

    corruptions = (
        with_first_block(replace(MMPP_ROW, mean_fitness=MMPP_ROW.best_fitness + 0.01)),
        with_first_block(replace(MMPP_ROW, best_identifier=CandidateId(1, 0))),
    )
    for corrupted in corruptions:
        with pytest.raises(TrafficlabError, match="history"):
            render_checkpoint(corrupted)

    assert parse_checkpoint(render_checkpoint(state), state.compatibility) == state

    impossible_count = replace(MMPP_ROW)
    object.__setattr__(impossible_count, "valid_count", 2)
    with pytest.raises(TrafficlabError, match="history valid_count"):
        render_checkpoint(with_first_block(impossible_count, overall_valid=3))

    for family_valid, overall_valid in ((2, 3), (-1, 0)):
        document = json.loads(render_checkpoint(state))
        history = cast(list[dict[str, object]], document["history"])
        history[0]["valid_count"] = family_valid
        history[2]["valid_count"] = overall_valid
        with pytest.raises(TrafficlabError, match="history valid[_ ]count|history counts"):
            parse_checkpoint(_encoded(document), state.compatibility)


def test_noncurrent_history_accounts_exactly_for_zero_fitness_invalid_candidates() -> None:
    state = _state_at(1, generation_count=2)

    def with_old_mmpp(mmpp: HistoryRow) -> CheckpointState:
        poisson = replace(POISSON_ROW, candidate_count=1)
        family_rows = (mmpp, poisson)
        winner = min(family_rows, key=lambda row: (-row.best_fitness, row.best_identifier))
        overall = HistoryRow(
            0,
            "overall",
            None,
            3,
            sum(row.valid_count for row in family_rows),
            winner.best_fitness,
            math.fsum(row.mean_fitness * row.candidate_count for row in family_rows) / 3,
            winner.best_identifier,
        )
        return replace(state, history=(mmpp, poisson, overall, *state.history[3:]))

    invalid = (
        HistoryRow(0, "family", "mmpp", 2, 0, 0.5, 0.0, CandidateId(0, 0)),
        HistoryRow(0, "family", "mmpp", 2, 0, 0.1, 0.1, CandidateId(0, 0)),
        HistoryRow(0, "family", "mmpp", 2, 1, 0.5, 0.4, CandidateId(0, 0)),
    )
    for row in invalid:
        with pytest.raises(TrafficlabError, match="history.*valid|history.*feasible"):
            render_checkpoint(with_old_mmpp(row))

    valid_boundaries = (
        HistoryRow(0, "family", "mmpp", 2, 0, 0.0, 0.0, CandidateId(0, 0)),
        HistoryRow(0, "family", "mmpp", 2, 1, 0.4, 0.2, CandidateId(0, 0)),
        HistoryRow(0, "family", "mmpp", 2, 2, 0.4, 0.4, CandidateId(0, 0)),
    )
    for row in valid_boundaries:
        candidate = with_old_mmpp(row)
        assert parse_checkpoint(render_checkpoint(candidate), candidate.compatibility) == candidate


def test_checkpoint_and_history_publication_round_trip_through_real_atomic_replace(tmp_path: Path) -> None:
    publish_checkpoint(tmp_path / "checkpoint.json", VALID_STATE)
    assert load_checkpoint(tmp_path / "checkpoint.json", COMPATIBILITY) == VALID_STATE
    publish_history_csv(tmp_path / "ga_history.csv", VALID_STATE)
    assert (tmp_path / "ga_history.csv").read_bytes() == render_history_csv(VALID_STATE)


def test_checkpoint_load_and_generation_history_repair_preserve_authoritative_bytes(tmp_path: Path) -> None:
    with pytest.raises(TrafficlabError, match="could not read checkpoint"):
        load_checkpoint(tmp_path / "missing.json", COMPATIBILITY)

    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_bytes(render_checkpoint(VALID_STATE))
    authoritative = checkpoint_path.read_bytes()
    history_path = tmp_path / "ga_history.csv"
    for existing in (None, b"stale\n", render_history_csv(VALID_STATE)):
        history_path.unlink(missing_ok=True)
        if existing is not None:
            history_path.write_bytes(existing)
        assert load_generation(tmp_path, COMPATIBILITY) == VALID_STATE
        assert checkpoint_path.read_bytes() == authoritative
        assert history_path.read_bytes() == render_history_csv(VALID_STATE)


def test_history_repair_read_or_publication_failure_never_changes_valid_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_bytes(render_checkpoint(VALID_STATE))
    authoritative = checkpoint_path.read_bytes()
    real_read = Path.read_bytes

    def fail_history_read(path: Path) -> bytes:
        if path.name == "ga_history.csv":
            raise OSError("injected history read failure")
        return real_read(path)

    monkeypatch.setattr(Path, "read_bytes", fail_history_read)
    with pytest.raises(TrafficlabError, match="history read failure"):
        load_generation(tmp_path, COMPATIBILITY)
    assert checkpoint_path.read_bytes() == authoritative

    monkeypatch.undo()

    def fail_history_publication(_path: Path, _state: CheckpointState) -> None:
        raise TrafficlabError("injected history publication failure", corrective_action="preserve checkpoint")

    monkeypatch.setattr(checkpoint, "publish_history_csv", fail_history_publication)
    with pytest.raises(TrafficlabError, match="history publication failure"):
        load_generation(tmp_path, COMPATIBILITY)
    assert checkpoint_path.read_bytes() == authoritative


@pytest.mark.parametrize(
    "content",
    [
        b"\xff",
        b"wrong\n",
        b"generation,scope,family,candidate_count,valid_count,best_fitness,mean_fitness,"
        b"best_birth_generation,best_birth_index\n0,family,mmpp\n",
        render_history_csv(VALID_STATE).replace(b",family,mmpp,", b",other,mmpp,", 1),
        render_history_csv(VALID_STATE).replace(b",overall,,", b",overall,mmpp,", 1),
        render_history_csv(VALID_STATE).replace(b",family,mmpp,", b",family,markov_renewal,", 1),
        render_history_csv(VALID_STATE).replace(b"0,family", b"00,family", 1),
        render_history_csv(VALID_STATE).replace(b",0.4,0.4,", b",nan,0.4,", 1),
    ],
)
def test_history_csv_validator_rejects_malformed_header_rows_scalars_and_families(content: bytes) -> None:
    with pytest.raises(ValueError):
        checkpoint._parse_history_csv(  # pyright: ignore[reportPrivateUsage]
            content, frozenset(("mmpp", "poisson_empirical"))
        )


def test_checkpoint_atomic_wrapper_rejects_changed_persisted_temporary_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def corrupt(_path: Path, _content: bytes, *, validator: Any) -> None:
        validator(b"changed\n")

    monkeypatch.setattr(checkpoint, "_atomic_replace", corrupt)
    with pytest.raises(TrafficlabError, match="persisted temporary artifact"):
        checkpoint.atomic_replace(tmp_path / "checkpoint.json", b"expected\n")


def test_render_history_csv_rejects_a_projection_that_does_not_reconstruct_exact_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def return_no_rows(_content: bytes, _families: frozenset[Any]) -> tuple[HistoryRow, ...]:
        return ()

    monkeypatch.setattr(checkpoint, "_parse_history_csv", return_no_rows)
    with pytest.raises(TrafficlabError, match="reconstruct"):
        render_history_csv(VALID_STATE)


def test_checkpoint_publishes_before_derived_history_repair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def record(path: Path, _content: bytes) -> None:
        calls.append(path.name)

    monkeypatch.setattr(checkpoint, "atomic_replace", record)
    publish_generation(tmp_path, VALID_STATE)
    assert calls == ["checkpoint.json", "ga_history.csv"]


def test_history_rows_have_exact_header_lexical_family_rows_then_overall(tmp_path: Path) -> None:
    publish_history_csv(tmp_path / "ga_history.csv", VALID_STATE)
    assert (tmp_path / "ga_history.csv").read_text() == (
        "generation,scope,family,candidate_count,valid_count,best_fitness,mean_fitness,"
        "best_birth_generation,best_birth_index\n"
        f"0,family,mmpp,1,1,{MMPP_TRIAL.aggregate_score!r},{MMPP_TRIAL.aggregate_score!r},0,0\n"
        f"0,family,poisson_empirical,2,1,{POISSON_TRIAL.aggregate_score!r},"
        f"{(POISSON_TRIAL.aggregate_score / 2.0)!r},0,2\n"
        f"0,overall,,3,2,{POISSON_TRIAL.aggregate_score!r},{OVERALL_ROW.mean_fitness!r},0,2\n"
    )
    assert (tmp_path / "ga_history.csv").read_bytes() == render_history_csv(VALID_STATE)


def test_experiment_hash_mismatch_precedes_redundant_operator_mismatch() -> None:
    data = _decoded()
    data["experiment_sha256"] = "d" * 64
    operators = cast(dict[str, object], cast(list[dict[str, object]], data["families"])[0]["operators"])
    operators["mutation_probability"] = 0.4
    with pytest.raises(TrafficlabError, match="experiment snapshot SHA-256"):
        parse_checkpoint(_encoded(data), COMPATIBILITY)


def test_experiment_hash_mismatch_precedes_rng_engine_and_engine_mismatch_is_specific() -> None:
    engine_only = _decoded()
    cast(dict[str, object], engine_only["rng"])["engine"] = "alternate.random/MT19937"
    with pytest.raises(TrafficlabError, match="RNG engine"):
        parse_checkpoint(_encoded(engine_only), COMPATIBILITY)

    engine_and_experiment = cast(dict[str, object], json.loads(_encoded(engine_only)))
    engine_and_experiment["experiment_sha256"] = "d" * 64
    with pytest.raises(TrafficlabError, match="experiment snapshot SHA-256"):
        parse_checkpoint(_encoded(engine_and_experiment), COMPATIBILITY)


def test_operator_mismatch_is_specific_and_checkpoint_is_not_rewritten(tmp_path: Path) -> None:
    data = _decoded()
    operators = cast(dict[str, object], cast(list[dict[str, object]], data["families"])[0]["operators"])
    operators["mutation_probability"] = 0.4
    path = tmp_path / "checkpoint.json"
    path.write_bytes(_encoded(data))
    before = path.read_bytes()
    with pytest.raises(TrafficlabError, match="operator values for family mmpp"):
        load_checkpoint(path, COMPATIBILITY)
    assert path.read_bytes() == before
