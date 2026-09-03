"""Direct state checkpoint behavior tests."""

import json
import math
from typing import Any, cast

import pytest

import trafficlab.fitting.genetic.checkpoint.codec as checkpoint_codec
import trafficlab.fitting.genetic.checkpoint.schema as checkpoint_schema
import trafficlab.fitting.genetic.checkpoint.state as checkpoint_state
from tests.support.checkpoint import (
    COMPATIBILITY,
    FAMILIES,
    GENETIC,
    MARKOV_MODEL_DIAGNOSTICS,
    MMPP_ROW,
    MMPP_TRIAL,
    OVERALL_ROW,
    POISSON_ROW,
    POISSON_TRIAL,
    POPULATION,
    VALID_STATE,
    build_trial,
    changed_checkpoint,
    checkpoint_bytes_with_early_limit,
    decoded_checkpoint,
    encoded_checkpoint,
    markov_state,
    mutated_checkpoint_document,
    replace,
    state_at,
    state_with_generation_best_history,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.fitting.genetic.checkpoint import (
    CheckpointState,
    encode_rng_state,
    parse_checkpoint,
    render_checkpoint,
    summarize_generation,
)
from trafficlab.fitting.genetic.population import rank_candidates
from trafficlab.fitting.genetic.types import (
    Candidate,
    CandidateFailure,
    CandidateId,
    HistoryRow,
)
from trafficlab.generation.models.common import make_rng


def test_checkpoint_state_priority_must_match_its_compatibility() -> None:
    """A decoded state cannot silently substitute another complete priority ordering."""
    state = replace(VALID_STATE, family_priority=tuple(reversed(VALID_STATE.family_priority)))

    with pytest.raises(TrafficlabError, match="state family_priority"):
        render_checkpoint(state)


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
        render_checkpoint(markov_state((0.8, 0.2, 0.0, 5, 0.1)))

    boundary_markov = markov_state((0.1, 0.9, 0.0, 5, 0.1))
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

    document = decoded_checkpoint()
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
        parse_checkpoint(encoded_checkpoint(document), compatibility)


def test_public_checkpoint_codec_accepts_exact_registered_orders_for_all_families() -> None:
    assert parse_checkpoint(render_checkpoint(VALID_STATE), COMPATIBILITY) == VALID_STATE
    markov = markov_state((0.1, 0.9, 0.0, 5, 0.1))
    assert parse_checkpoint(render_checkpoint(markov), markov.compatibility) == markov


def test_checkpoint_rejects_duplicate_and_nonlexical_family_metadata() -> None:
    duplicate = decoded_checkpoint()
    families = cast(list[object], duplicate["families"])
    families[1] = families[0]
    with pytest.raises(TrafficlabError, match="duplicate family"):
        parse_checkpoint(encoded_checkpoint(duplicate), COMPATIBILITY)

    reversed_families = decoded_checkpoint()
    cast(list[object], reversed_families["families"]).reverse()
    with pytest.raises(TrafficlabError, match="lexical"):
        parse_checkpoint(encoded_checkpoint(reversed_families), COMPATIBILITY)


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
        parse_checkpoint(changed_checkpoint(path, value), COMPATIBILITY)


def test_checkpoint_recomputes_method_aggregate_candidate_fitness_and_history() -> None:
    cases = (
        (("population", 0, "trials", 0, "aggregate_score"), 0.41, "aggregate"),
        (("population", 0, "fitness"), 0.41, "fitness"),
        (("history", 0, "mean_fitness"), 0.41, "history"),
        (("history", 2, "best_identifier"), [0, 0], "history"),
    )
    for path, value, match in cases:
        with pytest.raises(TrafficlabError, match=match):
            parse_checkpoint(changed_checkpoint(path, value), COMPATIBILITY)


def test_checkpoint_rejects_valid_invalid_and_duplicate_trial_seed_inconsistencies() -> None:
    valid_with_invalid = mutated_checkpoint_document(
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
    with pytest.raises(TrafficlabError, match=r"valid\.invalid"):
        parse_checkpoint(encoded_checkpoint(valid_with_invalid), COMPATIBILITY)

    invalid_nonzero = mutated_checkpoint_document(("population", 1, "fitness"), 0.1)
    with pytest.raises(TrafficlabError, match=r"invalid\.fitness"):
        parse_checkpoint(encoded_checkpoint(invalid_nonzero), COMPATIBILITY)

    duplicate_seed = decoded_checkpoint()
    trial = cast(
        dict[str, object],
        cast(list[object], cast(dict[str, object], cast(list[object], duplicate_seed["population"])[0])["trials"])[0],
    )
    cast(list[object], cast(dict[str, object], cast(list[object], duplicate_seed["population"])[0])["trials"]).append(
        trial
    )
    with pytest.raises(TrafficlabError, match="trial seeds"):
        parse_checkpoint(encoded_checkpoint(duplicate_seed), COMPATIBILITY)


def test_checkpoint_accepts_hard_limit_and_early_stop_and_rejects_terminal_inconsistencies() -> None:
    hard = state_at(2, generation_count=2, terminal_reason="hard_limit")
    early = state_at(
        2,
        generation_count=3,
        early_stopping_generations=2,
        consecutive_stagnation=2,
        terminal_reason="early_stop",
    )
    assert parse_checkpoint(render_checkpoint(hard), hard.compatibility) == hard
    assert parse_checkpoint(render_checkpoint(early), early.compatibility) == early

    inconsistent = (
        state_at(1, generation_count=2, terminal_reason="hard_limit"),
        state_at(2, generation_count=2, early_stopping_generations=2, consecutive_stagnation=2),
        state_at(
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
    state = state_with_generation_best_history(
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
            parse_checkpoint(encoded_checkpoint(document), state.compatibility)


def test_checkpoint_rejects_best_that_disagrees_with_the_retained_history_winner() -> None:
    """An equal later generation best must not replace the winner retained from earlier history."""
    state = state_at(1, generation_count=2)
    earlier_mmpp = replace(MMPP_ROW, best_fitness=0.5)
    earlier_overall = replace(OVERALL_ROW, best_identifier=CandidateId(birth_generation=0, birth_index=0))
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
    state = state_with_generation_best_history(
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
    state = state_with_generation_best_history(
        best_fitnesses,
        generation_count=len(best_fitnesses),
        early_stopping_generations=2,
        early_stopping_tolerance=0.03125,
        consecutive_stagnation=counter,
        terminal_reason=terminal_reason,
    )
    content = checkpoint_bytes_with_early_limit(state)

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
    state = state_with_generation_best_history(
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
                kind="fit",
                seed=None,
                detail="bad",
                stage="fit",
                affected_evidence="candidate model",
                evidence_state="diagnostic_only",
                corrective_action="repair the candidate model",
                authority="primary",
            ),
        )
        for candidate in POPULATION
    )
    rows = summarize_generation(
        0,
        tied,
        ("mmpp", "poisson_empirical"),
        family_priority=COMPATIBILITY.family_priority,
    )
    assert rows[-1].best_identifier == CandidateId(birth_generation=0, birth_index=0)
    assert rows[-1].valid_count == 0
    with pytest.raises(TrafficlabError, match="empty population"):
        summarize_generation(
            0,
            (),
            ("mmpp", "poisson_empirical"),
            family_priority=COMPATIBILITY.family_priority,
        )
    with pytest.raises(TrafficlabError, match="unique and lexical"):
        summarize_generation(
            0,
            POPULATION,
            ("poisson_empirical", "mmpp"),
            family_priority=COMPATIBILITY.family_priority,
        )
    with pytest.raises(TrafficlabError, match="family priority"):
        summarize_generation(
            0,
            POPULATION,
            ("mmpp", "poisson_empirical"),
            family_priority=("mmpp", "mmpp"),
        )


def test_checkpoint_priority_ties_unify_current_history_and_retained_winners() -> None:
    """A later higher-priority catch-up and two equal improvers beat lexical IDs everywhere."""
    priority = ("mmpp", "poisson_empirical")
    genetic = replace(GENETIC, generation_count=1, early_stopping_generations=1)
    compatibility = replace(COMPATIBILITY, genetic=genetic, family_priority=priority)
    prior_mmpp = replace(POPULATION[0], identifier=CandidateId(birth_generation=0, birth_index=2))
    prior_poisson = replace(
        POPULATION[2],
        identifier=CandidateId(birth_generation=0, birth_index=0),
        fitness=MMPP_TRIAL.aggregate_score,
        trials=(MMPP_TRIAL,),
    )
    invalid = replace(POPULATION[1], identifier=CandidateId(birth_generation=0, birth_index=1))
    prior_population = (prior_mmpp, invalid, prior_poisson)
    current_mmpp = replace(
        prior_mmpp,
        fitness=POISSON_TRIAL.aggregate_score,
        trials=(POISSON_TRIAL,),
    )
    current_poisson = replace(
        prior_poisson,
        fitness=POISSON_TRIAL.aggregate_score,
        trials=(POISSON_TRIAL,),
    )
    current_population = (current_mmpp, invalid, current_poisson)
    history = summarize_generation(
        0,
        prior_population,
        ("mmpp", "poisson_empirical"),
        family_priority=priority,
    ) + summarize_generation(
        1,
        current_population,
        ("mmpp", "poisson_empirical"),
        family_priority=priority,
    )
    state = CheckpointState(
        compatibility=compatibility,
        generation=1,
        population=current_population,
        history=history,
        rng_state=encode_rng_state(make_rng(73)),
        best_identifier=current_mmpp.identifier,
        best_fitness=current_mmpp.fitness,
        consecutive_stagnation=0,
        terminal_reason="hard_limit",
        family_priority=priority,
    )

    assert history[2].best_identifier == prior_mmpp.identifier
    assert history[5].best_identifier == current_mmpp.identifier
    assert parse_checkpoint(render_checkpoint(state), compatibility) == state


def test_generation_summary_uses_the_same_grouped_mean_arithmetic_as_validation() -> None:
    literal_scores: tuple[float, float, float, float, float, float, float] = (
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
            identifier=CandidateId(birth_generation=0, birth_index=index),
            family="mmpp" if index < 3 else "poisson_empirical",
            genes=(1.0, 2.0, 3.0, 4.0) if index < 3 else (1.0,),
            status="valid",
            fitness=trial.aggregate_score,
            trials=(trial,),
            invalid=None,
            duplicate_diagnostics=(),
        )
        for index, score in enumerate(literal_scores)
        for trial in (build_trial(7, (score, score, score, score, score, score, score, score)),)
    )
    rows = summarize_generation(
        0,
        candidates,
        ("mmpp", "poisson_empirical"),
        family_priority=COMPATIBILITY.family_priority,
    )
    direct_mean = math.fsum(candidate.fitness for candidate in candidates) / len(candidates)
    grouped_mean = math.fsum(row.mean_fitness * row.candidate_count for row in rows[:-1]) / len(candidates)
    assert direct_mean != grouped_mean
    assert rows[-1].mean_fitness == grouped_mean
    with pytest.raises(TrafficlabError, match="has no candidate"):
        summarize_generation(
            0,
            candidates[:3],
            ("mmpp", "poisson_empirical"),
            family_priority=COMPATIBILITY.family_priority,
        )

    compatibility = replace(COMPATIBILITY, genetic=replace(GENETIC, population_size=7))
    winner = rank_candidates(candidates, family_priority=compatibility.family_priority)[0]
    state = CheckpointState(
        compatibility=compatibility,
        generation=0,
        population=candidates,
        history=rows,
        rng_state=encode_rng_state(make_rng(73)),
        best_identifier=winner.identifier,
        best_fitness=winner.fitness,
        consecutive_stagnation=0,
        terminal_reason="running",
        family_priority=compatibility.family_priority,
    )
    assert parse_checkpoint(render_checkpoint(state), compatibility) == state


def test_generation_summary_canonicalizes_rounded_mean_below_exact_valid_ceiling() -> None:
    score = 0.7922045605901253
    trial = build_trial(7, (score, score, score, score, score, score, score, score))
    valid = replace(POPULATION[0], fitness=trial.aggregate_score, trials=(trial,))
    invalid = replace(POPULATION[1], family="mmpp")
    rows = summarize_generation(
        0,
        (valid, invalid, replace(invalid, identifier=CandidateId(birth_generation=0, birth_index=2))),
        ("mmpp",),
        family_priority=("mmpp",),
    )

    for row in rows:
        mean_numerator, mean_denominator = row.mean_fitness.as_integer_ratio()
        best_numerator, best_denominator = row.best_fitness.as_integer_ratio()
        assert (
            mean_numerator * row.candidate_count * best_denominator
            <= best_numerator * row.valid_count * mean_denominator
        )


def test_integer_coordinate_bounds_and_gene_are_preserved_as_exact_integers() -> None:
    record = checkpoint_schema.IntegerCoordinateRecord.model_validate(
        {"name": "r", "kind": "integer", "lower": 1, "upper": 5}
    )
    coordinate = checkpoint_codec._coordinate_from_record(record)  # pyright: ignore[reportPrivateUsage]
    assert coordinate.bounds.lower == 1
    assert type(coordinate.bounds.lower) is int
    assert (
        checkpoint_state._parse_gene(  # pyright: ignore[reportPrivateUsage]
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
        replace(
            VALID_STATE,
            population=(
                replace(
                    candidate,
                    family="markov_renewal",
                    trials=(replace(MMPP_TRIAL, model_diagnostics=MARKOV_MODEL_DIAGNOSTICS),),
                ),
                *POPULATION[1:],
            ),
        ),
        replace(
            VALID_STATE,
            population=(replace(candidate, identifier=CandidateId(birth_generation=1, birth_index=0)), *POPULATION[1:]),
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
                        kind="fit",
                        seed=None,
                        detail="bad",
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
            VALID_STATE,
            population=(
                POPULATION[0],
                replace(POPULATION[1], identifier=CandidateId(birth_generation=0, birth_index=0)),
                POPULATION[2],
            ),
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
        replace(VALID_STATE, best_identifier=CandidateId(birth_generation=9, birth_index=9)),
        replace(VALID_STATE, best_fitness=0.4),
        replace(
            VALID_STATE,
            best_identifier=CandidateId(birth_generation=0, birth_index=0),
            best_fitness=MMPP_TRIAL.aggregate_score,
        ),
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
        (
            MMPP_ROW,
            POISSON_ROW,
            replace(OVERALL_ROW, best_identifier=CandidateId(birth_generation=0, birth_index=0), best_fitness=0.4),
        ),
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
    state = state_at(1, generation_count=2)

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
        with_first_block(replace(MMPP_ROW, best_identifier=CandidateId(birth_generation=1, birth_index=0))),
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
        with pytest.raises(TrafficlabError, match=r"history.*valid_count"):
            parse_checkpoint(encoded_checkpoint(document), state.compatibility)


def test_noncurrent_history_accounts_exactly_for_zero_fitness_invalid_candidates() -> None:
    state = state_at(1, generation_count=2)

    def with_old_mmpp(mmpp: HistoryRow) -> CheckpointState:
        poisson = replace(POISSON_ROW, candidate_count=1)
        family_rows = (mmpp, poisson)
        winner = min(family_rows, key=lambda row: (-row.best_fitness, row.best_identifier))
        overall = HistoryRow(
            generation=0,
            scope="overall",
            family=None,
            candidate_count=3,
            valid_count=sum(row.valid_count for row in family_rows),
            best_fitness=winner.best_fitness,
            mean_fitness=math.fsum(row.mean_fitness * row.candidate_count for row in family_rows) / 3,
            best_identifier=winner.best_identifier,
        )
        return replace(state, history=(mmpp, poisson, overall, *state.history[3:]))

    invalid = (
        HistoryRow(
            generation=0,
            scope="family",
            family="mmpp",
            candidate_count=2,
            valid_count=0,
            best_fitness=0.5,
            mean_fitness=0.0,
            best_identifier=CandidateId(birth_generation=0, birth_index=0),
        ),
        HistoryRow(
            generation=0,
            scope="family",
            family="mmpp",
            candidate_count=2,
            valid_count=0,
            best_fitness=0.1,
            mean_fitness=0.1,
            best_identifier=CandidateId(birth_generation=0, birth_index=0),
        ),
        HistoryRow(
            generation=0,
            scope="family",
            family="mmpp",
            candidate_count=2,
            valid_count=1,
            best_fitness=0.5,
            mean_fitness=0.4,
            best_identifier=CandidateId(birth_generation=0, birth_index=0),
        ),
    )
    for row in invalid:
        with pytest.raises(TrafficlabError, match="history.*valid|history.*feasible"):
            render_checkpoint(with_old_mmpp(row))

    valid_boundaries = (
        HistoryRow(
            generation=0,
            scope="family",
            family="mmpp",
            candidate_count=2,
            valid_count=0,
            best_fitness=0.0,
            mean_fitness=0.0,
            best_identifier=CandidateId(birth_generation=0, birth_index=0),
        ),
        HistoryRow(
            generation=0,
            scope="family",
            family="mmpp",
            candidate_count=2,
            valid_count=1,
            best_fitness=0.4,
            mean_fitness=0.2,
            best_identifier=CandidateId(birth_generation=0, birth_index=0),
        ),
        HistoryRow(
            generation=0,
            scope="family",
            family="mmpp",
            candidate_count=2,
            valid_count=2,
            best_fitness=0.4,
            mean_fitness=0.4,
            best_identifier=CandidateId(birth_generation=0, birth_index=0),
        ),
    )
    for row in valid_boundaries:
        candidate = with_old_mmpp(row)
        assert parse_checkpoint(render_checkpoint(candidate), candidate.compatibility) == candidate
