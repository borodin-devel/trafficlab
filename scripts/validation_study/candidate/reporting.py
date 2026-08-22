"""Candidate selection and report-input calculations."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from statistics import fmean, variance
from typing import TYPE_CHECKING, cast

from scripts.validation_study.candidate.artifacts import CandidateTraining
from scripts.validation_study.common import (
    BOOTSTRAP_SEED,
    FAMILY_ORDER,
    PUBLISHED_METHOD_ORDER,
    JsonObject,
    JsonValue,
    load_json,
    require,
)
from trafficlab.common.config import FamilyName
from trafficlab.common.statistics import bootstrap_interval
from trafficlab.common.trace import (
    align_generated,
    normalize_reference,
)
from trafficlab.comparison.codec import render_comparison_result, similarity_settings_identity
from trafficlab.comparison.metrics import compare_traces
from trafficlab.comparison.schema import ComparisonResult

if TYPE_CHECKING:
    from scripts.validation_study.common import WorkloadName
    from scripts.validation_study.records import HeldOutEvaluation


def _candidate_score(result: ComparisonResult) -> JsonObject:
    return cast(
        JsonObject,
        {
            "aggregate": result.aggregate_score,
            "methods": cast(JsonObject, {method: result.methods[method].score for method in PUBLISHED_METHOD_ORDER}),
        },
    )


def _candidate_score_mean(scores: Sequence[JsonObject]) -> JsonObject:
    require(bool(scores), "candidate score mean requires observations")
    methods = [cast(JsonObject, score["methods"]) for score in scores]
    return cast(
        JsonObject,
        {
            "aggregate": fmean(cast(float, score["aggregate"]) for score in scores),
            "methods": cast(
                JsonObject,
                {method: fmean(cast(float, item[method]) for item in methods) for method in PUBLISHED_METHOD_ORDER},
            ),
        },
    )


def _candidate_sample_summary(values: Sequence[float], *, name: str) -> JsonObject:
    require(len(values) == 3, f"{name} requires exactly three observations")
    require(all(math.isfinite(value) and value >= 0.0 for value in values), f"{name} values must be finite")
    return {
        "bootstrap": cast(JsonValue, bootstrap_interval(values, seed=BOOTSTRAP_SEED).as_dict()),
        "mean": fmean(values),
        "sample_variance": variance(values),
    }


def _candidate_winner_family(training: CandidateTraining) -> FamilyName:
    winner = next(
        candidate
        for candidate in training.checkpoint.population
        if candidate.identifier == training.checkpoint.best_identifier
    )
    return winner.family


def candidate_natural_variation(training: Sequence[CandidateTraining]) -> JsonObject:
    require(len(training) == 3, "natural variation requires exactly three training records")
    settings = similarity_settings_identity(training[0].config.similarity)
    require(
        all(similarity_settings_identity(item.config.similarity) == settings for item in training),
        "natural variation requires common frozen similarity settings",
    )
    pairs: list[JsonValue] = []
    for left_index in range(3):
        for right_index in range(left_index + 1, 3):
            left = training[left_index]
            right = training[right_index]
            left_reference, forward_window = normalize_reference(left.reference)
            right_reference, reverse_window = normalize_reference(right.reference)
            forward = _candidate_score(
                compare_traces(
                    left_reference,
                    align_generated(right.reference, forward_window),
                    forward_window,
                    left.config.similarity,
                )
            )
            reverse = _candidate_score(
                compare_traces(
                    right_reference,
                    align_generated(left.reference, reverse_window),
                    reverse_window,
                    right.config.similarity,
                )
            )
            pairs.append(
                cast(
                    JsonObject,
                    {
                        "forward": forward,
                        "left_repeat": left.repeat,
                        "reverse": reverse,
                        "right_repeat": right.repeat,
                        "symmetric_mean": _candidate_score_mean((forward, reverse)),
                    },
                )
            )
    return cast(
        JsonObject,
        {
            "pairs": pairs,
            "symmetric_mean": _candidate_score_mean(
                [cast(JsonObject, cast(JsonObject, pair)["symmetric_mean"]) for pair in pairs]
            ),
            "workload": training[0].workload,
        },
    )


def _candidate_controlled_weight_analysis(training: Sequence[CandidateTraining]) -> list[JsonValue]:
    """Reweight fixed completed metrics without changing diagnostics or execution."""
    rows: list[JsonValue] = []
    alternate_weights: JsonObject = {
        "autocorrelation": 0.2,
        "frame_size_ks": 0.4,
        "iat_ks": 0.2,
        "multiscale_rate": 0.2,
    }
    for workload in ("short", "streaming", "bursty"):
        group = [item for item in training if item.workload == workload]
        selected = min(group, key=lambda item: (-item.checkpoint.best_fitness, item.repeat))
        baseline_weights = selected.config.similarity.method_weights.model_dump(mode="json")
        require(
            baseline_weights == {method: 0.25 for method in PUBLISHED_METHOD_ORDER},
            "controlled weight analysis requires the frozen equal-weight baseline",
        )
        scores = _candidate_score(selected.comparison)
        components = cast(JsonObject, scores["methods"])
        alternate_aggregate = math.fsum(
            cast(float, alternate_weights[method]) * cast(float, components[method])
            for method in PUBLISHED_METHOD_ORDER
        )
        rendered_comparison = load_json(render_comparison_result(selected.comparison))
        rendered_methods = cast(JsonObject, rendered_comparison["methods"])
        diagnostics = {
            method: cast(JsonObject, rendered_methods[method])["diagnostics"] for method in PUBLISHED_METHOD_ORDER
        }
        rows.append(
            cast(
                JsonObject,
                {
                    "alternative_aggregate": alternate_aggregate,
                    "alternative_weights": alternate_weights,
                    "baseline_aggregate": scores["aggregate"],
                    "baseline_weights": baseline_weights,
                    "components": components,
                    "diagnostics": diagnostics,
                    "executed_methods": list(PUBLISHED_METHOD_ORDER),
                    "training_directory": f"training/{selected.workload}/r{selected.repeat}",
                    "workload": workload,
                },
            )
        )
    return rows


def _candidate_invalid_chromosome_diagnostics(training: Sequence[CandidateTraining]) -> list[JsonValue]:
    """Retain classified infeasibility with its declared genes, settings, and limits."""
    rows: list[JsonValue] = []
    workload_order: dict[WorkloadName, int] = {"short": 0, "streaming": 1, "bursty": 2}
    for item in sorted(training, key=lambda item: (workload_order[item.workload], item.repeat)):
        invalid: list[object] = []
        for candidate in item.checkpoint.population:
            if candidate.status != "invalid":
                continue
            failure = candidate.invalid
            require(failure is not None, "invalid candidate must retain its classified failure")
            assert failure is not None
            invalid.append(
                {
                    "affected_evidence": failure.affected_evidence,
                    "authority": failure.authority,
                    "corrective_action": failure.corrective_action,
                    "detail": failure.detail,
                    "evidence_state": failure.evidence_state,
                    "family": candidate.family,
                    "genes": list(candidate.genes) if candidate.genes is not None else None,
                    "identifier": {
                        "birth_generation": candidate.identifier.birth_generation,
                        "birth_index": candidate.identifier.birth_index,
                    },
                    "kind": failure.kind,
                    "seed": failure.seed,
                    "stage": failure.stage,
                }
            )
        rows.append(
            cast(
                JsonObject,
                {
                    "invalid_candidates": cast(JsonValue, invalid),
                    "trial_limits": cast(JsonObject, item.config.generation.trial.model_dump(mode="json")),
                    "training_directory": f"training/{item.workload}/r{item.repeat}",
                    "workload": item.workload,
                    "repeat": item.repeat,
                },
            )
        )
    return rows


def candidate_report_inputs(
    training: Sequence[CandidateTraining],
    held_out: Mapping[WorkloadName, HeldOutEvaluation],
    *,
    natural_variation: Sequence[JsonObject],
) -> JsonObject:
    fresh_simulation: list[JsonValue] = []
    held_out_scores: list[JsonValue] = []
    training_scores: list[JsonValue] = []
    for workload in ("short", "streaming", "bursty"):
        group = [item for item in training if item.workload == workload]
        require(len(group) == 3, f"report inputs require three {workload} training records")
        fresh_simulation.append(
            cast(
                JsonObject,
                {
                    "score": _candidate_score_mean([_candidate_score(item.comparison) for item in group]),
                    "workload": workload,
                },
            )
        )
        training_scores.append(
            cast(
                JsonObject,
                {
                    "runtime_seconds": _candidate_sample_summary(
                        [item.runtime_seconds for item in group], name="training runtime variance"
                    ),
                    "selection_fitness": _candidate_sample_summary(
                        [item.checkpoint.best_fitness for item in group], name="training selection variance"
                    ),
                    "winner_family_count_variance": variance(
                        [sum(_candidate_winner_family(item) == family for item in group) for family in FAMILY_ORDER]
                    ),
                    "winner_family_counts": cast(
                        JsonObject,
                        {
                            family: sum(_candidate_winner_family(item) == family for item in group)
                            for family in FAMILY_ORDER
                        },
                    ),
                    "workload": workload,
                },
            )
        )
        held_out_scores.append(
            cast(
                JsonObject,
                {
                    "observation_window_seconds": held_out[workload].observation_window_seconds,
                    "score": _candidate_score(held_out[workload].comparison),
                    "workload": workload,
                },
            )
        )
    return {
        "controlled_weight_analysis": _candidate_controlled_weight_analysis(training),
        "formula": "arithmetic_mean",
        "fresh_simulation": fresh_simulation,
        "held_out": held_out_scores,
        "invalid_chromosome_diagnostics": _candidate_invalid_chromosome_diagnostics(training),
        "natural_variation": list(natural_variation),
        "runtime_winner_variance": training_scores,
        "training": training_scores,
    }
