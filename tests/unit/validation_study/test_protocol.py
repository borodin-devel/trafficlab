from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import tomllib
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import pytest
import tomli_w

from scripts import run_validation_study as study
from tests.fixtures.paths import PRE_USER_AGENT_R6_FIXTURE
from tests.support.validation_study import (
    CAPTURE_DOCKERFILE,
    CAPTURE_SCRIPT,
    HASH,
    ROOT,
    OfflinePrimaryBaseline,
    ScriptedPrerequisiteRunner,
    StudyIdentityRunner,
    changed_config_paths,
    frozen,
    materialize_offline_primary_baseline,
    response_headers,
    study_result_value,
    terminal_checkpoint_and_best,
    trial_result,
    valid_prerequisite,
    valid_result_document,
    write_checked_configs,
    write_prerequisite_repository_inputs,
    write_retained_prerequisite_evidence,
)
from trafficlab import USER_AGENT
from trafficlab.common.compatibility import ContentIdentity, identify_bytes
from trafficlab.common.config import GenerationLimits, SimilarityConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent, TrafficTrace, align_generated, normalize_reference
from trafficlab.comparison.stage import ComparisonResult, compare_traces
from trafficlab.fitting.genetic.evaluation import (
    ValidatedEvaluationContext,
    evaluate_candidate,
    validate_evaluation_context,
)
from trafficlab.fitting.genetic.population import derive_family_priority, initial_population
from trafficlab.fitting.genetic.strategy import make_strategy_context
from trafficlab.fitting.genetic.types import Candidate, TrialResult, rebuild_genetic_record
from trafficlab.generation.models.common import FittedModel, GenerationResult, make_rng
from trafficlab.generation.models.registry import get_family

_PRE_USER_AGENT_R6_FIXTURE = PRE_USER_AGENT_R6_FIXTURE


def install_pre_user_agent_r6_predecessor(repository_root: Path) -> tuple[Path, bytes, dict[str, str]]:
    """Install the one retained pre-User-Agent prerequisite publication verbatim."""

    fixture = _PRE_USER_AGENT_R6_FIXTURE
    content = (fixture / "prerequisites.raw.json").read_bytes()
    source = cast(dict[str, str], json.loads((fixture / "source.json").read_text(encoding="utf-8")))
    document = cast(dict[str, str], json.loads(content))
    source["study_id"] = document["study_id"]
    source["url"] = document["url"]
    root = repository_root / "examples" / "validation_study" / "prerequisites.json"
    attempt = root.parent / ".study-work" / "attempts" / source["study_id"]
    evidence = root.parent / ".study-work" / "evidence" / source["study_id"] / "00-prerequisites"
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_bytes(content)
    attempt.mkdir(parents=True)
    shutil.copy2(fixture / "prerequisites.raw.json", attempt / "prerequisites.raw.json")
    shutil.copy2(fixture / "prerequisites-success.json", attempt / "prerequisites-success.json")
    shutil.copytree(fixture / "evidence", evidence)
    return root, content, source


def contains_none(value: object) -> bool:
    if type(value) is dict:
        return any(contains_none(item) for item in cast(dict[object, object], value).values())
    if type(value) is list:
        return any(contains_none(item) for item in cast(list[object], value))
    return value is None


def expected_base_config(
    repository_root: Path,
    workload: str,
    *,
    url: str = "https://downloads.example.test/object.bin",
    study_id: str = "study-1",
    capture_image_id: str = f"sha256:{'d' * 64}",
) -> dict[str, object]:
    specs = {spec.name: spec for spec in study.workload_specs(url)}
    spec = specs[cast(study.WorkloadName, workload)]
    first_run = {
        "short": "01-short-r1",
        "streaming": "02-streaming-r1",
        "bursty": "03-bursty-r1",
    }[workload]
    return {
        "run": {
            "directory": (repository_root / "runs" / "validation_study" / study_id / first_run).resolve(),
            "minimum_free_bytes": 1_048_576,
            "master_seed": 73,
            "final_seed": 97,
        },
        "target": {
            "image": study.TARGET_REFERENCE,
            "argv": spec.argv,
            "environment": {},
            "working_directory": "/",
            "mounts": (
                {
                    "source": (
                        repository_root / "examples" / "validation_study" / ".study-work" / "mount" / study_id
                    ).resolve(),
                    "target": "/trafficlab-study",
                    "read_only": False,
                },
            ),
        },
        "capture": {
            "image": capture_image_id,
            "network_probe_url": url,
            "readiness_timeout_seconds": 10.0,
            "workload_timeout_seconds": spec.workload_timeout_seconds,
            "flush_timeout_seconds": 5.0,
            "total_timeout_seconds": spec.total_timeout_seconds,
        },
        "generation": {
            "trial": {"max_packets": 25_000, "max_output_bytes": 40_000_000, "max_wall_seconds": 5.0},
            "final": {"max_packets": 50_000, "max_output_bytes": 80_000_000, "max_wall_seconds": 10.0},
        },
        "genetic": {
            "population_size": 6,
            "generation_count": 2,
            "tournament_size": 2,
            "elite_count": 1,
            "trial_seeds": (17, 29),
            "duplicate_mutation_attempts": 3,
            "early_stopping_generations": 0,
            "early_stopping_tolerance": 0.0,
            "resume": True,
        },
        "models": {
            "enabled": ("poisson_empirical", "markov_renewal", "mmpp"),
            "poisson_empirical": {
                "crossover_probability": 0.9,
                "mutation_probability": 1.0,
                "mutation_scale": 0.1,
                "c_lambda": {"lower": 0.25, "upper": 4.0},
            },
            "markov_renewal": {
                "crossover_probability": 0.9,
                "mutation_probability": 0.2,
                "mutation_scale": 0.1,
                "q1": {"lower": 0.1, "upper": 0.4},
                "q2": {"lower": 0.6, "upper": 0.9},
                "alpha": {"lower": 0.0, "upper": 2.0},
                "r": {"lower": 1, "upper": 8},
                "c_t": {"lower": 0.25, "upper": 4.0},
            },
            "mmpp": {
                "crossover_probability": 0.9,
                "mutation_probability": 0.25,
                "mutation_scale": 0.1,
                "q01": {"lower": 0.01, "upper": 10.0},
                "q10": {"lower": 0.01, "upper": 10.0},
                "lambda0": {"lower": 10.0, "upper": 100.0},
                "lambda1": {"lower": 0.1, "upper": 1000.0},
            },
        },
        "similarity": {
            "iat_diagnostic_quantile": 0.95,
            "acf_lags": (1,),
            "acf_lag_weights": (1.0,),
            "acf_iat_weight": 0.5,
            "acf_size_weight": 0.5,
            "multiscale_widths_seconds": spec.multiscale_widths_seconds,
            "multiscale_scale_weights": (0.5, 0.5),
            "multiscale_packet_weight": 0.5,
            "multiscale_byte_weight": 0.5,
            "max_direction_bin_cells": 100_000,
            "method_weights": {
                "frame_size_ks": 0.25,
                "iat_ks": 0.25,
                "autocorrelation": 0.25,
                "multiscale_rate": 0.25,
            },
        },
    }


def install_prerequisite_failure(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if mutation == "wrong-python":
        monkeypatch.setattr(study.platform, "python_version", lambda: "3.12.4")
    if mutation == "config-publication-failed":
        original_fsync = study._commit_prerequisite_fsync  # pyright: ignore[reportPrivateUsage]
        calls = 0

        def fail_second_config(destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("simulated config publication failure")
            original_fsync(destination)

        monkeypatch.setattr(study, "_commit_prerequisite_fsync", fail_second_config)


def natural_variation_inputs(
    tmp_path: Path,
) -> tuple[
    tuple[study.StudyRunRecord, ...],
    dict[tuple[study.WorkloadName, int], TrafficTrace],
    dict[study.WorkloadName, SimilarityConfig],
    dict[str, object],
]:
    document = valid_result_document(tmp_path)
    records = study_result_value(document).runs
    url = "https://downloads.example.test/object.bin"
    traces: dict[tuple[study.WorkloadName, int], TrafficTrace] = {}
    settings: dict[study.WorkloadName, SimilarityConfig] = {}
    for workload in study.workload_specs(url):
        config = study.build_base_config(
            workload,
            repository_root=tmp_path,
            study_id="study-1",
            url=url,
            capture_image_id=f"sha256:{'d' * 64}",
        )
        settings[workload.name] = config.similarity
        for repeat in (1, 2, 3):
            start = float(10 * repeat)
            traces[(workload.name, repeat)] = TrafficTrace.from_events(
                (
                    TraceEvent(start, Direction.OUTBOUND, 60 + repeat),
                    TraceEvent(start + 0.25, Direction.INBOUND, 100 + repeat),
                    TraceEvent(start + 1.0, Direction.OUTBOUND, 180 + repeat),
                    TraceEvent(start + float(repeat + 2), Direction.INBOUND, 260 + repeat),
                )
            )
    return records, traces, settings, document


def test_family_champions_use_terminal_valid_candidates_stable_ids_and_selection_means(tmp_path: Path) -> None:
    state, _best, _comparison = terminal_checkpoint_and_best(tmp_path)

    champions = study._family_champions(state)  # pyright: ignore[reportPrivateUsage]

    assert tuple(item["family"] for item in champions) == study.FAMILY_ORDER
    assert champions[0]["candidate_id"] == {"birth_generation": 2, "birth_index": 0}
    assert champions[0]["selection_seeds"] == [17, 29]
    assert champions[0]["selection_fitness"] == 0.6
    assert champions[0]["selection_score"] == {
        "aggregate": 0.6,
        "methods": {name: 0.6 for name in study.PUBLISHED_METHOD_ORDER},
    }
    assert champions[1]["selection_fitness"] == 0.7
    assert champions[2]["selection_fitness"] == 0.9


def test_winner_fresh_simulation_and_published_records_remain_distinct(tmp_path: Path) -> None:
    state, best, comparison = terminal_checkpoint_and_best(tmp_path)
    final_trial = trial_result(97, 0.75)

    winner = study._winner(state, best)  # pyright: ignore[reportPrivateUsage]
    fresh_simulation = {
        "seed": final_trial.seed,
        "score": study._score_from_trial(final_trial),  # pyright: ignore[reportPrivateUsage]
        "source": "run_experiment_fit_outcome",
    }
    published = {
        "seed": 97,
        "score": study._score_from_comparison(comparison),  # pyright: ignore[reportPrivateUsage]
    }

    assert winner == {
        "family": "poisson_empirical",
        "candidate_id": {"birth_generation": 2, "birth_index": 5},
        "genes": [1.0],
        "selection_fitness": 0.9,
    }
    assert fresh_simulation == {
        "seed": 97,
        "score": {"aggregate": 0.75, "methods": {name: 0.75 for name in study.PUBLISHED_METHOD_ORDER}},
        "source": "run_experiment_fit_outcome",
    }
    assert published["score"] == {"aggregate": 1.0, "methods": {name: 1.0 for name in study.PUBLISHED_METHOD_ORDER}}
    assert fresh_simulation != published


def test_trace_summary_uses_canonical_events_and_multiscale_direction_totals(tmp_path: Path) -> None:
    config = study.build_base_config(
        study.workload_specs("https://downloads.example.test/object.bin")[0],
        repository_root=tmp_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        capture_image_id=f"sha256:{'d' * 64}",
    )
    reference = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.OUTBOUND, 60),
            TraceEvent(0.0, Direction.INBOUND, 100),
            TraceEvent(1.0, Direction.OUTBOUND, 200),
            TraceEvent(3.0, Direction.INBOUND, 300),
        )
    )
    generated = TrafficTrace.from_events(
        (
            TraceEvent(0.0, Direction.INBOUND, 80),
            TraceEvent(0.5, Direction.OUTBOUND, 120),
            TraceEvent(1.5, Direction.INBOUND, 160),
            TraceEvent(3.0, Direction.OUTBOUND, 240),
        )
    )
    comparison = compare_traces(reference, generated, 3.0, config.similarity)

    reference_summary = study._trace_summary(  # pyright: ignore[reportPrivateUsage]
        reference, comparison, role="reference"
    )
    generated_summary = study._trace_summary(  # pyright: ignore[reportPrivateUsage]
        generated, comparison, role="generated"
    )

    assert reference_summary == {
        "packet_count": 4,
        "observation_window_seconds": 3.0,
        "packet_totals": {"outbound": 2, "inbound": 2},
        "byte_totals": {"outbound": 260, "inbound": 400},
        "frame_lengths": {
            "count": 4,
            "minimum": 60.0,
            "median": 150.0,
            "quantile_probability": 0.95,
            "quantile": 300.0,
            "maximum": 300.0,
            "zero_count": 0,
        },
        "iats": {
            "count": 3,
            "minimum": 0.0,
            "median": 1.0,
            "quantile_probability": 0.95,
            "quantile": 2.0,
            "maximum": 2.0,
            "zero_count": 1,
        },
        "scales": [
            {
                "width_seconds": width,
                "bins_per_direction": bins,
                "packet_totals": {"outbound": 2, "inbound": 2},
                "byte_totals": {"outbound": 260, "inbound": 400},
            }
            for width, bins in ((0.001, 3000), (0.01, 300))
        ],
    }
    assert generated_summary["packet_totals"] == {"outbound": 2, "inbound": 2}
    assert generated_summary["byte_totals"] == {"outbound": 360, "inbound": 240}
    assert generated_summary["frame_lengths"] == {
        "count": 4,
        "minimum": 80.0,
        "median": 140.0,
        "quantile_probability": 0.95,
        "quantile": 240.0,
        "maximum": 240.0,
        "zero_count": 0,
    }


def test_offline_primary_baseline_materializes_independent_regular_copies(
    tmp_path: Path,
    offline_primary_baselines: dict[str, OfflinePrimaryBaseline],
) -> None:
    """Each extraction mutation starts from an independent regular-file primary tree."""

    baseline = offline_primary_baselines["short"]
    first_root, first_result, first_spec, _first_workload, _first_responses = materialize_offline_primary_baseline(
        baseline
    )
    first_log = first_spec.run_directory / "run.log"
    original_log = first_log.read_bytes()
    first_log.write_bytes(b"mutated\n")

    second_root, second_result, second_spec, _second_workload, _second_responses = materialize_offline_primary_baseline(
        baseline
    )
    first_config = study.load_experiment(first_spec.config_path)
    second_config = study.load_experiment(second_spec.config_path)

    assert first_root == second_root
    assert first_result.run_directory.is_relative_to(first_root)
    assert second_result.run_directory.is_relative_to(second_root)
    assert second_result.capture.reference_path.is_relative_to(second_root)
    assert second_result.fit.best_model_path.is_relative_to(second_root)
    assert second_result.generation.generated_path.is_relative_to(second_root)
    assert second_spec.config_path.is_relative_to(second_root)
    assert second_spec.transfer_evidence_directory.is_relative_to(second_root)
    assert first_config.run.directory == first_spec.run_directory
    assert first_config.target.mounts[0].source.is_relative_to(first_root)
    assert second_config.run.directory == second_spec.run_directory
    assert second_config.target.mounts[0].source.is_relative_to(second_root)
    assert (second_spec.run_directory / "run.log").read_bytes() == original_log


def test_primary_extraction_reloads_nine_artifacts_and_proves_raw_quantized_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    offline_primary_baselines: dict[str, OfflinePrimaryBaseline],
) -> None:
    repository_root, result, spec, workload, transfer_responses = materialize_offline_primary_baseline(
        offline_primary_baselines["short"]
    )
    authoritative_trial = result.fit.outcome.final_trials[0]
    observed_trials: list[TrialResult] = []
    real_reconstruct = study._reconstruct_science  # pyright: ignore[reportPrivateUsage]

    def reconstruct(
        evidence: object,
        fresh_simulation: TrialResult,
        *,
        generated_path: Path,
    ) -> object:
        observed_trials.append(fresh_simulation)
        return real_reconstruct(evidence, fresh_simulation, generated_path=generated_path)  # type: ignore[arg-type]

    monkeypatch.setattr(study, "_reconstruct_science", reconstruct)

    def reject_evaluate(
        _candidate: Candidate,
        _context: ValidatedEvaluationContext,
        _seed: int,
    ) -> tuple[TrialResult, ...]:
        raise AssertionError("primary reevaluation")

    monkeypatch.setattr(study, "evaluate_final", reject_evaluate)

    record = study.extract_primary_record(
        repository_root,
        spec,
        workload,
        result,
        1.25,
        transfer_responses,
    )

    assert tuple(item["family"] for item in record.family_champions) == study.FAMILY_ORDER
    assert record.reuse == {"capture": False, "best_model": False, "generated": False, "similarity": False}
    assert record.cleanup_verified is True
    assert set(record.artifact_sha256) == set(study.ARTIFACT_NAMES)
    assert record.fresh_simulation["source"] == "run_experiment_fit_outcome"
    assert observed_trials == [authoritative_trial]
    assert observed_trials[0] is authoritative_trial
    assert record.raw_sequence == {
        "seed": 97,
        "observation_window_seconds": 10.0,
        "trial_event_count": len(result.fit.outcome.final_trials) and len(result.generation.trace),
        "final_event_count": len(result.generation.trace),
        "raw_events_equal": True,
        "fresh_simulation_score_reproduced": True,
        "reparsed_event_count": len(result.generation.trace),
        "reparsed_matches_quantized": True,
    }
    assert sorted(path.name for path in spec.run_directory.iterdir()) == sorted(study.ARTIFACT_NAMES)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-artifact",
        "tenth-run-entry",
        "reused-stage",
        "checkpoint-mismatch",
        "history-mismatch",
        "best-model-mismatch",
        "held-out-wrong-seed",
        "raw-trial-final-differ",
        "raw-score-differ",
        "quantized-events-differ",
        "similarity-lineage-differ",
        "cleanup-not-proven",
    ],
)
def test_run_extraction_rejects_missing_malformed_inconsistent_or_reused_evidence(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    offline_primary_baselines: dict[str, OfflinePrimaryBaseline],
) -> None:
    repository_root, result, spec, workload, transfer_responses = materialize_offline_primary_baseline(
        offline_primary_baselines["short"]
    )

    if mutation == "missing-artifact":
        (spec.run_directory / "run.log").unlink()
    elif mutation == "tenth-run-entry":
        (spec.run_directory / "unexpected").write_bytes(b"unexpected")
    elif mutation == "reused-stage":
        result = replace(result, capture=replace(result.capture, reused=True))
    elif mutation == "checkpoint-mismatch":
        result = replace(result, fit=replace(result.fit, outcome=replace(result.fit.outcome, generation=99)))
    elif mutation == "history-mismatch":
        with (spec.run_directory / "ga_history.csv").open("ab") as stream:
            stream.write(b"stale\n")
    elif mutation == "best-model-mismatch":
        with (spec.run_directory / "best_model.json").open("ab") as stream:
            stream.write(b" ")
    elif mutation == "held-out-wrong-seed":
        trial = rebuild_genetic_record(result.fit.outcome.final_trials[0], seed=17)
        result = replace(result, fit=replace(result.fit, outcome=replace(result.fit.outcome, final_trials=(trial,))))
    elif mutation == "raw-trial-final-differ":
        original_family = get_family(result.fit.best_model.family)

        class DifferingFinalFamily:
            def __getattr__(self, name: str) -> object:
                return getattr(original_family, name)

            def generate(
                self,
                model: FittedModel,
                seed: int,
                W: float,
                limits: GenerationLimits,
            ) -> GenerationResult:
                generated = original_family.generate(model, seed, W, limits)
                if limits == study.load_experiment(spec.config_path).generation.final:
                    first, *remaining = generated.trace.to_events()
                    changed = TraceEvent(first.timestamp, first.direction, first.frame_length + 1)
                    return replace(generated, trace=TrafficTrace.from_events((changed, *remaining)))
                return generated

        def differing_family(_name: str) -> Any:
            return DifferingFinalFamily()

        monkeypatch.setattr(
            study,
            "get_family",
            differing_family,
            raising=False,
        )
    elif mutation == "raw-score-differ":
        original = result.fit.outcome.final_trials[0]
        aggregate = 0.0 if original.aggregate_score != 0.0 else 1.0
        trial = rebuild_genetic_record(original, aggregate_score=aggregate)
        result = replace(result, fit=replace(result.fit, outcome=replace(result.fit.outcome, final_trials=(trial,))))
    elif mutation == "quantized-events-differ":
        first, *remaining = result.generation.trace.to_events()
        changed = TraceEvent(first.timestamp, first.direction, first.frame_length + 1)
        result = replace(
            result,
            generation=replace(result.generation, trace=TrafficTrace.from_events((changed, *remaining))),
        )
    elif mutation == "similarity-lineage-differ":
        assert result.comparison.input_identities is not None
        identities = result.comparison.input_identities.as_content_identities()
        identities["capture_json"] = ContentIdentity(size=identities["capture_json"].size, sha256="0" * 64)
        result = replace(result, comparison=result.comparison.with_input_identities(identities))
    elif mutation == "cleanup-not-proven":
        run_log = spec.run_directory / "run.log"
        records = [json.loads(line) for line in run_log.read_text().splitlines()]
        next(record for record in records if record.get("event") == "capture_published")["event"] = "capture_missing"
        run_log.write_text(
            "".join(f"{json.dumps(record, sort_keys=True, separators=(',', ':'))}\n" for record in records)
        )

    with pytest.raises((TrafficlabError, TypeError, ValueError)):
        study.extract_primary_record(
            repository_root,
            spec,
            workload,
            result,
            1.25,
            transfer_responses,
        )


def test_natural_variation_compares_each_pair_in_both_directions_and_averages_scores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records, traces, settings, document = natural_variation_inputs(tmp_path)
    calls: list[
        tuple[
            TrafficTrace,
            TrafficTrace,
            float,
            SimilarityConfig,
            ComparisonResult,
        ]
    ] = []

    def comparison_spy(
        reference: TrafficTrace,
        generated: TrafficTrace,
        window: float,
        config: SimilarityConfig,
    ) -> ComparisonResult:
        comparison = compare_traces(reference, generated, window, config)
        calls.append((reference, generated, window, config, comparison))
        return comparison

    monkeypatch.setattr(study, "compare_traces", comparison_spy)

    variation = study.natural_variation(records, traces, settings)

    workloads: tuple[study.WorkloadName, ...] = ("short", "streaming", "bursty")
    pairs = ((1, 2), (1, 3), (2, 3))
    assert len(calls) == 18
    call_index = 0
    expected_natural = cast(list[dict[str, object]], document["natural_variation"])
    for workload_index, workload in enumerate(workloads):
        record = variation[workload_index]
        assert record["workload"] == workload
        assert record["reference_descriptors"] == expected_natural[workload_index]["reference_descriptors"]
        result_pairs = cast(list[study.JsonValue], record["pairs"])
        assert (
            tuple(
                (
                    cast(dict[str, study.JsonValue], item)["left_repeat"],
                    cast(dict[str, study.JsonValue], item)["right_repeat"],
                )
                for item in result_pairs
            )
            == pairs
        )
        for pair_index, (left, right) in enumerate(pairs):
            pair = cast(study.JsonObject, result_pairs[pair_index])
            for source_repeat, generated_repeat, field in (
                (left, right, "forward"),
                (right, left, "reverse"),
            ):
                expected_reference, expected_window = normalize_reference(traces[(workload, source_repeat)])
                expected_generated = align_generated(traces[(workload, generated_repeat)], expected_window)
                actual_reference, actual_generated, actual_window, actual_settings, comparison = calls[call_index]
                assert (actual_reference, actual_generated, actual_window) == (
                    expected_reference,
                    expected_generated,
                    expected_window,
                )
                assert actual_settings is settings[workload]
                assert pair[field] == study._score_from_comparison(  # pyright: ignore[reportPrivateUsage]
                    comparison
                )
                call_index += 1
            assert pair["symmetric"] == study._average_score(  # pyright: ignore[reportPrivateUsage]
                cast(study.JsonObject, pair["forward"]),
                cast(study.JsonObject, pair["reverse"]),
            )


def test_workload_summaries_recompute_runtime_family_score_variance_and_winner_counts(tmp_path: Path) -> None:
    records, _traces, _settings, document = natural_variation_inputs(tmp_path)

    summaries = study.workload_summaries(tuple(reversed(records)))

    assert summaries == tuple(cast(list[study.JsonObject], document["workload_summaries"]))
    short = summaries[0]
    assert short["runtime"] == study.descriptive_statistics((1.0, 2.0, 3.0))
    runtime_interval = cast(dict[str, object], cast(dict[str, object], short["runtime"])["bootstrap"])
    assert runtime_interval["confidence_level"] == 0.95
    assert runtime_interval["generator"] == "PCG64"
    assert runtime_interval["method"] == "percentile"
    assert runtime_interval["n_resamples"] == 10_000
    assert runtime_interval["sample_size"] == 3
    assert runtime_interval["statistic"] == "mean"
    families = cast(study.JsonObject, short["family_champions"])
    poisson = cast(study.JsonObject, families["poisson_empirical"])
    assert poisson["selection_fitness"] == study.descriptive_statistics((0.61, 0.62, 0.63))
    assert short["winner_counts"] == {"markov_renewal": 0, "mmpp": 0, "poisson_empirical": 3}


def test_natural_variation_propagates_metric_precondition_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records, traces, settings, _document = natural_variation_inputs(tmp_path)
    failure = TrafficlabError(
        "natural comparison precondition failed",
        corrective_action="retain the reference evidence",
    )

    def failing_comparison(
        reference: TrafficTrace,
        generated: TrafficTrace,
        window: float,
        config: SimilarityConfig,
    ) -> ComparisonResult:
        del reference, generated, window, config
        raise failure

    monkeypatch.setattr(study, "compare_traces", failing_comparison)

    with pytest.raises(TrafficlabError, match="natural comparison precondition failed") as captured:
        study.natural_variation(records, traces, settings)

    assert captured.value is failure


def test_study_id_url_repository_path_and_utc_validators_are_exact(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    assert study.validate_study_id("study-1") == "study-1"
    assert study.validate_endpoint_url("https://downloads.example.test/object.bin") == (
        "https://downloads.example.test/object.bin"
    )
    assert (
        study._repository_relative_path(  # pyright: ignore[reportPrivateUsage]
            "evidence/study-1/file", repository_root=repository_root, name="evidence path"
        )
        == "evidence/study-1/file"
    )
    assert (
        study._utc_timestamp(  # pyright: ignore[reportPrivateUsage]
            "2026-08-13T12:00:00Z", name="created time"
        )
        == "2026-08-13T12:00:00Z"
    )

    for value in ("", "Study-1", "study_1", "-study", "a" * 33):
        with pytest.raises(ValueError, match="study ID"):
            study.validate_study_id(value)
    for value in (
        "http://downloads.example.test/object.bin",
        "https://user@downloads.example.test/object.bin",
        "https://downloads.example.test/object.bin?token=x",
        "https://downloads.example.test/object.bin#fragment",
        "https://127.0.0.1/object.bin",
        "https:///object.bin",
    ):
        with pytest.raises(ValueError, match="URL"):
            study.validate_endpoint_url(value)
    for value in (
        "/evidence/study-1/file",
        "evidence\\study-1\\file",
        "evidence//file",
        "evidence/./file",
        "evidence/../file",
        "",
    ):
        with pytest.raises(ValueError, match="repository-relative|nonempty"):
            study._repository_relative_path(  # pyright: ignore[reportPrivateUsage]
                value, repository_root=repository_root, name="evidence path"
            )
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository_root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="repository-relative"):
        study._repository_relative_path(  # pyright: ignore[reportPrivateUsage]
            "escape/file", repository_root=repository_root, name="evidence path"
        )
    for value in (
        "2026-08-13T12:00:00+00:00",
        "2026-08-13T12:00:00z",
        "2026-08-13T12:00:00",
        "2026-02-30T12:00:00Z",
    ):
        with pytest.raises(ValueError, match="UTC RFC 3339"):
            study._utc_timestamp(value, name="created time")  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(ValueError, match="integer"):
        study._strict_int(True, name="count")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="float"):
        study._strict_float(1, name="score")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match=r"\[0.0, 1.0\]"):
        study._strict_float(1.1, name="score", lower=0.0, upper=1.0)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="finite"):
        study._strict_float(math.inf, name="score")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="SHA-256"):
        study._sha256("A" * 64, name="hash")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="duplicate JSON key"):
        study._load_json(b'{"value":1,"value":2}\n')  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="invalid JSON constant"):
        study._load_json(b'{"value":NaN}\n')  # pyright: ignore[reportPrivateUsage]


def test_endpoint_contract_rejects_noncredential_free_https_object_urls() -> None:
    assert study.validate_endpoint_url("https://downloads.example.test/object.bin") == (
        "https://downloads.example.test/object.bin"
    )
    for value in [
        "http://example.test/object",
        "https://user@example.test/object",
        "https://example.test/object?query=1",
        "https://example.test/object#fragment",
        "https://127.0.0.1/object",
        "https:///object",
    ]:
        with pytest.raises(ValueError, match="credential-free HTTPS.*DNS hostname"):
            study.validate_endpoint_url(value)


def test_workload_specs_expand_exact_short_streaming_and_eight_bursty_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://downloads.example.test/object.bin"
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert USER_AGENT == f"{metadata['name']}/{metadata['version']} (+{metadata['urls']['Repository']})"
    common = (
        "--fail",
        "--silent",
        "--show-error",
        "--location",
        "--max-redirs",
        "3",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--http1.1",
        "--user-agent",
        USER_AGENT,
        "--connect-timeout",
        "15",
    )
    short_argv = (
        *common,
        "--max-time",
        "30",
        "--limit-rate",
        "4M",
        "--range",
        "0-1048575",
        "--max-filesize",
        "1048576",
        "--dump-header",
        "/trafficlab-study/short.headers",
        "--output",
        "/dev/null",
        "--url",
        url,
    )
    streaming_argv = (
        *common,
        "--max-time",
        "40",
        "--limit-rate",
        "256K",
        "--range",
        "0-4194303",
        "--max-filesize",
        "4194304",
        "--dump-header",
        "/trafficlab-study/streaming.headers",
        "--output",
        "/dev/null",
        "--url",
        url,
    )
    starts = (0, 524288, 1048576, 1572864, 2097152, 2621440, 3145728, 3670016)
    bursty_groups: list[str] = []
    for index, start in enumerate(starts):
        if index:
            bursty_groups.append("--next")
        bursty_groups.extend(
            (
                *common,
                "--max-time",
                "30",
                "--range",
                f"{start}-{start + 32767}",
                "--max-filesize",
                "32768",
                "--dump-header",
                f"/trafficlab-study/bursty-{index}.headers",
                "--output",
                "/dev/null",
                "--url",
                url,
            )
        )
    bursty_argv = ("--parallel", "--parallel-max", "4", "--fail-early", *bursty_groups)

    specs = study.workload_specs(url)

    assert specs == (
        study.WorkloadSpec("short", short_argv, ((0, 1048575, "short.headers"),), 35.0, 90.0, (0.001, 0.01)),
        study.WorkloadSpec(
            "streaming",
            streaming_argv,
            ((0, 4194303, "streaming.headers"),),
            50.0,
            120.0,
            (0.25, 1.0),
        ),
        study.WorkloadSpec(
            "bursty",
            bursty_argv,
            tuple((start, start + 32767, f"bursty-{index}.headers") for index, start in enumerate(starts)),
            35.0,
            90.0,
            (0.001, 0.01),
        ),
    )
    assert len(specs[2].transfers) == 8
    assert len({filename for _start, _end, filename in specs[2].transfers}) == 8
    assert specs[2].argv[:4] == ("--parallel", "--parallel-max", "4", "--fail-early")
    assert specs[2].argv.count("--next") == 7
    assert specs[2].argv[-1] == url
    assert all("sh" not in spec.argv and "-c" not in spec.argv for spec in specs)
    legacy_short = replace(
        specs[0],
        argv=tuple(
            "0-262143" if item == "0-1048575" else "262144" if item == "1048576" else item for item in specs[0].argv
        ),
        transfers=((0, 262143, "short.headers"),),
    )
    with pytest.raises(ValueError, match="exact HTTPS-only curl profile oracle"):
        study._validate_workload_specs(  # pyright: ignore[reportPrivateUsage]
            (legacy_short, specs[1], specs[2]),
            url=url,
        )
    capability_argv = study._expected_capability_argv("study-1", url)  # pyright: ignore[reportPrivateUsage]
    capability_user_agent = capability_argv.index("--user-agent")
    assert capability_argv[capability_user_agent : capability_user_agent + 2] == ("--user-agent", USER_AGENT)

    monkeypatch.setattr(study, "CURL_COMMON", (*common[:-1], "--proto-redir", "=http"))
    with pytest.raises(ValueError, match="exact HTTPS-only curl profile"):
        study.workload_specs(url)


def test_validation_study_mmpp_bounds_retain_a_valid_candidate_for_a_short_observation_window(tmp_path: Path) -> None:
    url = "https://downloads.example.test/object.bin"
    config = study.build_base_config(
        study.workload_specs(url)[0],
        repository_root=tmp_path,
        study_id="study-1",
        url=url,
        capture_image_id=f"sha256:{'d' * 64}",
    )
    window = 0.7874600887298584
    reference = TrafficTrace.from_events(
        tuple(
            TraceEvent(
                window * index / 176,
                Direction.OUTBOUND if index % 2 == 0 else Direction.INBOUND,
                60 if index % 2 == 0 else 100,
            )
            for index in range(177)
        )
    )
    context = make_strategy_context(
        config,
        reference,
        window,
        tmp_path / "run",
        experiment_identity=ContentIdentity(size=1, sha256="a" * 64),
        reference_identity=ContentIdentity(size=2, sha256="b" * 64),
        capture_identity=ContentIdentity(size=3, sha256="c" * 64),
    )
    validated = validate_evaluation_context(context.evaluation)
    pending = initial_population(
        derive_family_priority(config.run.master_seed, config.models.enabled),
        population_size=config.genetic.population_size,
        bounds=validated.bounds,
        reference=validated.reference,
        rng=make_rng(config.run.master_seed),
    )
    evaluated = tuple(evaluate_candidate(candidate, validated) for candidate in pending)

    assert any(candidate.family == "mmpp" and candidate.status == "valid" for candidate in evaluated)


@pytest.mark.parametrize("workload", ["short", "streaming", "bursty"])
def test_base_config_contains_every_locked_value_and_only_profile_differences(
    workload: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    url = "https://downloads.example.test/object.bin"
    capture_image_id = f"sha256:{'d' * 64}"
    specs = {spec.name: spec for spec in study.workload_specs(url)}

    config = study.build_base_config(
        specs[cast(study.WorkloadName, workload)],
        repository_root=repository_root,
        study_id="study-1",
        url=url,
        capture_image_id=capture_image_id,
    )

    assert config.model_dump(mode="python") == expected_base_config(repository_root, workload)
    all_configs = {
        name: study.build_base_config(
            spec,
            repository_root=repository_root,
            study_id="study-1",
            url=url,
            capture_image_id=capture_image_id,
        ).model_dump(mode="python")
        for name, spec in specs.items()
    }
    assert changed_config_paths(all_configs["short"], all_configs["streaming"]) == {
        "run.directory",
        "target.argv",
        "capture.workload_timeout_seconds",
        "capture.total_timeout_seconds",
        "similarity.multiscale_widths_seconds",
    }
    assert changed_config_paths(all_configs["short"], all_configs["bursty"]) == {
        "run.directory",
        "target.argv",
    }


def test_checked_and_realized_configs_reload_to_exact_absolute_oracles(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    prerequisite, contents = write_checked_configs(repository_root)

    validated = study.validate_base_configs(repository_root, prerequisite)

    assert tuple(validated) == ("short", "streaming", "bursty")
    for name, config in validated.items():
        assert config.model_dump(mode="python") == expected_base_config(repository_root, name)
        assert hashlib.sha256(contents[name]).hexdigest() == prerequisite.config_sha256[name]
    portable = tomllib.loads(contents["short"].decode())
    assert cast(dict[str, object], portable["run"])["directory"] == "../../../runs/validation_study/study-1/01-short-r1"
    target = cast(dict[str, object], portable["target"])
    mount = cast(list[dict[str, object]], target["mounts"])[0]
    assert mount["source"] == "../.study-work/mount/study-1"

    realized_directory = (repository_root / "runs" / "validation_study" / "study-1" / "10-streaming-r2").resolve()
    realized = study._config_with_run_directory(  # pyright: ignore[reportPrivateUsage]
        validated["streaming"], realized_directory
    )
    realized_path = repository_root / "runs" / "validation_study" / "study-1" / "realized-configs" / "streaming.toml"
    rendered = study._render_realized_config(realized, realized_path)  # pyright: ignore[reportPrivateUsage]
    assert realized_path.read_bytes() == rendered
    assert study.load_experiment(realized_path) == realized
    assert str(realized_directory) in rendered.decode()

    with pytest.raises(ValueError, match="already exists"):
        study.render_checked_base_config(
            validated["short"],
            repository_root / "examples" / "validation_study" / "configs" / "short.toml",
            repository_root,
        )
    with pytest.raises(ValueError, match="already exists"):
        study._render_realized_config(realized, realized_path)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-capability-header",
        "tamper-capability.headers",
        "tamper-capability.stdout",
        "tamper-capability.stderr",
        "tamper-capability.cid",
        "tamper-capture.iid",
        "tamper-docker.stdout",
        "tamper-docker.stderr",
        "tamper-docker.xml",
        "tamper-internet.stdout",
        "tamper-internet.stderr",
        "tamper-internet.xml",
        "evidence-mode",
        "evidence-read-error",
        "non-ascii-cid",
        "invalid-junit",
        "junit-counts",
        "cid-record",
        "dockerfile-source",
        "capture-script-source",
    ],
)
def test_retained_prerequisite_evidence_reopens_hashes_and_crosschecks_every_authority(
    mutation: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    prerequisite, _contents = write_checked_configs(repository_root)
    prerequisite = write_retained_prerequisite_evidence(repository_root, prerequisite)
    evidence = (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / prerequisite.study_id
        / "00-prerequisites"
    )
    if mutation == "missing-capability-header":
        (evidence / "capability.headers").unlink()
    elif mutation.startswith("tamper-"):
        name = mutation.removeprefix("tamper-")
        (evidence / name).write_bytes((evidence / name).read_bytes() + b"changed")
    elif mutation == "evidence-mode":
        (evidence / "internet.stderr").chmod(0o644)
    elif mutation == "evidence-read-error":
        (repository_root / "docker" / "capture" / "Dockerfile").unlink()
    elif mutation == "non-ascii-cid":
        (evidence / "capability.cid").write_bytes(b"\xff\n")
    elif mutation in {"invalid-junit", "junit-counts"}:
        junit = (
            b"not XML"
            if mutation == "invalid-junit"
            else (
                b'<testsuites tests="3" failures="0" errors="0" skipped="0">'
                b'<testsuite tests="3" failures="0" errors="0" skipped="0"/></testsuites>'
            )
        )
        (evidence / "docker.xml").write_bytes(junit)
        commands = [
            cast(study.JsonObject, study._thaw_json(command))  # pyright: ignore[reportPrivateUsage]
            for command in prerequisite.commands
        ]
        commands[0]["junit_sha256"] = hashlib.sha256(junit).hexdigest()
        prerequisite = replace(prerequisite, commands=(frozen(commands[0]), frozen(commands[1])))
    elif mutation == "cid-record":
        capability = cast(
            study.JsonObject,
            study._thaw_json(prerequisite.capability),  # pyright: ignore[reportPrivateUsage]
        )
        capability["container_id"] = "SHORT"
        prerequisite = replace(prerequisite, capability=frozen(capability))
    elif mutation == "dockerfile-source":
        (repository_root / "docker" / "capture" / "Dockerfile").write_bytes(b"changed\n")
    elif mutation == "capture-script-source":
        (repository_root / "docker" / "capture" / "capture.sh").write_bytes(b"changed\n")

    with pytest.raises((TrafficlabError, ValueError)):
        study._validate_prerequisite_evidence(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            prerequisite,
        )


def test_retained_prerequisite_evidence_accepts_exact_local_files(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    prerequisite, _contents = write_checked_configs(repository_root)
    prerequisite = write_retained_prerequisite_evidence(repository_root, prerequisite)

    study._validate_prerequisite_evidence(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        prerequisite,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-capture-image",
        "disabled-family",
        "changed-operator",
        "final-seed-reused",
        "wrong-mount",
        "wrong-profile-argv",
        "unexpected-config-difference",
        "existing-run-directory",
        "missing-checked-config",
    ],
)
def test_config_validation_rejects_every_protocol_change(mutation: str, tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    prerequisite, contents = write_checked_configs(repository_root)
    short_path = repository_root / "examples" / "validation_study" / "configs" / "short.toml"
    short_config = study.build_base_config(
        study.workload_specs(prerequisite.url)[0],
        repository_root=repository_root,
        study_id=prerequisite.study_id,
        url=prerequisite.url,
        capture_image_id=cast(str, prerequisite.images["capture_image_id"]),
    )

    if mutation == "missing-checked-config":
        short_path.unlink()
    elif mutation == "existing-run-directory":
        short_config.run.directory.mkdir(parents=True)
    else:
        document = tomllib.loads(contents["short"].decode())
        run = cast(dict[str, object], document["run"])
        target = cast(dict[str, object], document["target"])
        capture = cast(dict[str, object], document["capture"])
        models = cast(dict[str, object], document["models"])
        if mutation == "wrong-capture-image":
            capture["image"] = f"sha256:{'e' * 64}"
        elif mutation == "disabled-family":
            models["enabled"] = ["poisson_empirical", "markov_renewal"]
            models.pop("mmpp")
        elif mutation == "changed-operator":
            cast(dict[str, object], models["poisson_empirical"])["mutation_scale"] = 0.2
        elif mutation == "final-seed-reused":
            run["final_seed"] = 17
        elif mutation == "wrong-mount":
            cast(list[dict[str, object]], target["mounts"])[0]["source"] = "../.study-work/mount/other"
        elif mutation == "wrong-profile-argv":
            target["argv"] = ["--url", prerequisite.url]
        elif mutation == "unexpected-config-difference":
            run["master_seed"] = 74
        mutated = tomli_w.dumps(document).encode()
        short_path.write_bytes(mutated)
        hashes = dict(prerequisite.config_sha256)
        hashes["short"] = hashlib.sha256(mutated).hexdigest()
        prerequisite = replace(prerequisite, config_sha256=frozen(hashes))

    with pytest.raises((ValueError, TrafficlabError)):
        study.validate_base_configs(repository_root, prerequisite)


def test_scratch_files_are_exclusive_regular_0666_and_archives_are_sibling_0600(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    workload = study.workload_specs("https://downloads.example.test/object.bin")[0]
    mount_directory = repository_root / "examples" / "validation_study" / ".study-work" / "mount" / "study-1"
    mount_directory.mkdir(parents=True)
    scratch = mount_directory / "short.headers"
    scratch.write_bytes(b"stale")
    run_directory = repository_root / "runs" / "validation_study" / "study-1" / "01-short-r1"
    run_directory.mkdir(parents=True)
    for name in study.ARTIFACT_NAMES:
        (run_directory / name).write_bytes(b"artifact")

    prepared = study.prepare_transfer_scratch(repository_root, "study-1", "01-short-r1", workload)

    assert tuple(prepared) == ("short.headers",)
    path, inode = prepared["short.headers"]
    assert path == scratch
    assert inode == path.lstat().st_ino
    assert stat.S_ISREG(path.lstat().st_mode)
    assert stat.S_IMODE(path.lstat().st_mode) == 0o666
    assert path.read_bytes() == b""
    header_bytes = response_headers(0, 1048575)
    path.write_bytes(header_bytes)

    responses = study.archive_transfer_evidence(
        repository_root,
        "study-1",
        "01-short-r1",
        workload,
        prepared,
        object_size_bytes=4_194_304,
    )

    archive = (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / "study-1"
        / "01-short-r1"
        / "short.headers"
    )
    assert responses == (
        {
            "transfer_index": 0,
            "requested_start": 0,
            "requested_end": 1048575,
            "status": 206,
            "content_length": 1048576,
            "content_range": "bytes 0-1048575/4194304",
            "header_archive_path": "examples/validation_study/.study-work/evidence/study-1/01-short-r1/short.headers",
            "header_sha256": hashlib.sha256(header_bytes).hexdigest(),
            "scratch_precreate_mode": 438,
            "archive_mode": 384,
            "inode_preserved": True,
        },
    )
    assert archive.read_bytes() == header_bytes
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert not path.exists()
    assert not archive.is_relative_to(run_directory)
    assert set(item.name for item in run_directory.iterdir()) == set(study.ARTIFACT_NAMES)

    path.symlink_to(repository_root / "outside")
    with pytest.raises(ValueError, match="symlink|regular"):
        study.prepare_transfer_scratch(repository_root, "study-1", "02-short-r2", workload)
    assert path.is_symlink()


def test_range_header_parser_validates_redirect_chain_final_status_range_and_length(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    workload = study.workload_specs("https://downloads.example.test/object.bin")[1]
    prepared = study.prepare_transfer_scratch(repository_root, "study-1", "02-streaming-r1", workload)
    redirect = b"HTTP/1.1 302 Found\r\nLocation: /stable/object.bin\r\nContent-Length: 0\r\n\r\n"
    header_bytes = response_headers(0, 4194303, prefix=redirect)
    prepared["streaming.headers"][0].write_bytes(header_bytes)

    responses = study.archive_transfer_evidence(
        repository_root,
        "study-1",
        "02-streaming-r1",
        workload,
        prepared,
        object_size_bytes=4_194_304,
    )

    assert responses[0]["status"] == 206
    assert responses[0]["content_range"] == "bytes 0-4194303/4194304"
    assert responses[0]["content_length"] == 4_194_304
    assert responses[0]["header_sha256"] == hashlib.sha256(header_bytes).hexdigest()


@pytest.mark.parametrize(
    "mutation",
    [
        "symlink",
        "replacement-inode",
        "empty-header",
        "duplicate-status",
        "duplicate-content-range",
        "wrong-total",
        "range-ignored-200",
        "wrong-content-length",
        "credential-redirect",
        "http-redirect",
        "archive-exists",
    ],
)
def test_transfer_evidence_rejects_unsafe_or_inexact_headers(mutation: str, tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    workload = study.workload_specs("https://downloads.example.test/object.bin")[0]
    mount_directory = repository_root / "examples" / "validation_study" / ".study-work" / "mount" / "study-1"
    scratch = mount_directory / "short.headers"
    if mutation == "symlink":
        mount_directory.mkdir(parents=True)
        scratch.symlink_to(repository_root / "outside")
        with pytest.raises(ValueError, match="symlink|regular"):
            study.prepare_transfer_scratch(repository_root, "study-1", "01-short-r1", workload)
        assert scratch.is_symlink()
        return

    prepared = study.prepare_transfer_scratch(repository_root, "study-1", "01-short-r1", workload)
    valid = response_headers(0, 1048575)
    invalid_headers = {
        "empty-header": b"",
        "duplicate-status": b"HTTP/1.1 206 Response\r\n" + valid,
        "duplicate-content-range": valid.replace(
            b"Content-Length:", b"Content-Range: bytes 0-1048575/4194304\r\nContent-Length:"
        ),
        "wrong-total": response_headers(0, 1048575, total=4_194_305),
        "range-ignored-200": response_headers(0, 1048575, status=200),
        "wrong-content-length": response_headers(0, 1048575, length=1048575),
        "credential-redirect": response_headers(
            0,
            1048575,
            prefix=b"HTTP/1.1 302 Found\r\nLocation: https://user@example.test/object\r\n\r\n",
        ),
        "http-redirect": response_headers(
            0,
            1048575,
            prefix=b"HTTP/1.1 302 Found\r\nLocation: http://example.test/object\r\n\r\n",
        ),
    }
    if mutation == "replacement-inode":
        replacement = mount_directory / "replacement.headers"
        replacement.write_bytes(valid)
        os.chmod(replacement, 0o666)
        os.replace(replacement, scratch)
    elif mutation == "archive-exists":
        scratch.write_bytes(valid)
        archive = (
            repository_root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "evidence"
            / "study-1"
            / "01-short-r1"
            / "short.headers"
        )
        archive.write_bytes(b"existing")
    else:
        scratch.write_bytes(invalid_headers[mutation])

    original = scratch.read_bytes()
    with pytest.raises(ValueError):
        study.archive_transfer_evidence(
            repository_root,
            "study-1",
            "01-short-r1",
            workload,
            prepared,
            object_size_bytes=4_194_304,
        )
    assert scratch.read_bytes() == original
    archive = (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / "study-1"
        / "01-short-r1"
        / "short.headers"
    )
    if mutation == "archive-exists":
        assert archive.read_bytes() == b"existing"
    else:
        assert archive.read_bytes() == original
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600


def test_median_quantile_and_descriptive_statistics_use_published_formulas() -> None:
    assert (
        study._sample_record(  # pyright: ignore[reportPrivateUsage]
            [1.0, 3.0, 5.0], quantile_probability=0.95, zero_count=0
        )["median"]
        == 3.0
    )
    assert (
        study._sample_record(  # pyright: ignore[reportPrivateUsage]
            [1.0, 3.0, 5.0, 9.0], quantile_probability=0.95, zero_count=0
        )["median"]
        == 4.0
    )
    assert (
        study._sample_record(  # pyright: ignore[reportPrivateUsage]
            [1.0, 3.0, 5.0, 9.0], quantile_probability=0.5, zero_count=0
        )["quantile"]
        == 3.0
    )
    descriptive = study.descriptive_statistics([1, 2, 3])
    assert descriptive == {
        "bootstrap": descriptive["bootstrap"],
        "count": 3,
        "mean": 2.0,
        "minimum": 1.0,
        "maximum": 3.0,
        "range": 2.0,
        "sample_variance": 1.0,
        "sample_standard_deviation": 1.0,
    }
    bootstrap = cast(dict[str, object], descriptive["bootstrap"])
    assert bootstrap["seed"] == study._BOOTSTRAP_SEED  # pyright: ignore[reportPrivateUsage]
    assert bootstrap["lower_bound"] == 1.0
    assert bootstrap["upper_bound"] == 3.0

    for values in ([], [1, 2], [1, 2, 3, 4], [1, True, 3], [1, math.nan, 3]):
        with pytest.raises(ValueError):
            study.descriptive_statistics(values)
    with pytest.raises(ValueError, match="nonempty"):
        study._sample_record([], quantile_probability=0.95, zero_count=0)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="zero count"):
        study._sample_record(  # pyright: ignore[reportPrivateUsage]
            [1.0, 2.0], quantile_probability=0.95, zero_count=3
        )


def test_candidate_sample_summary_retains_bootstrap_and_rejects_invalid_inputs() -> None:
    summary = study._candidate_sample_summary(  # pyright: ignore[reportPrivateUsage]
        [1.0, 2.0, 3.0], name="training runtime"
    )
    bootstrap = cast(dict[str, object], summary["bootstrap"])
    assert summary["mean"] == 2.0
    assert summary["sample_variance"] == 1.0
    assert bootstrap["seed"] == 20_260_819
    assert bootstrap["sample_size"] == 3
    assert bootstrap["n_resamples"] == 10_000

    with pytest.raises(ValueError, match="exactly three"):
        study._candidate_sample_summary([1.0, 2.0], name="training runtime")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="finite"):
        study._candidate_sample_summary(  # pyright: ignore[reportPrivateUsage]
            [1.0, math.nan, 3.0], name="training runtime"
        )


def test_prerequisite_codec_round_trips_exact_canonical_schema(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    value = valid_prerequisite()

    rendered = study.render_prerequisite_results(value)
    parsed = study.parse_prerequisite_results(rendered, repository_root=repository_root)

    assert study.render_prerequisite_results(parsed) == rendered
    assert rendered.endswith(b"\n")
    assert not rendered.endswith(b" \n")
    assert b": " not in rendered
    assert b", " not in rendered
    decoded = json.loads(rendered)
    assert not contains_none(decoded)
    assert tuple(decoded) == tuple(sorted(decoded))
    assert tuple(decoded["commands"][0]) == tuple(sorted(decoded["commands"][0]))
    with pytest.raises(TypeError):
        cast(dict[str, object], parsed.capability)["status"] = 200

    destination = repository_root / "examples" / "validation_study" / "prerequisites.json"
    destination.parent.mkdir(parents=True)
    study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
        destination, value, repository_root=repository_root
    )
    assert destination.read_bytes() == rendered


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unknown-root", "exact keys"),
        ("duplicate-key", "duplicate JSON key"),
        ("wrong-command-order", "docker_matrix"),
        ("skipped-test", "skipped"),
        ("wrong-image", "target reference"),
        ("wrong-capability-mode", "canary file mode"),
        ("wrong-container-id", "lowercase container ID"),
        ("path-escape", "repository-relative"),
        ("nan", "invalid JSON constant"),
    ],
)
def test_prerequisite_codec_rejects_each_contract_violation(
    mutation: str,
    message: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    rendered = study.render_prerequisite_results(valid_prerequisite())
    document = cast(dict[str, object], json.loads(rendered))

    if mutation == "unknown-root":
        document["unknown"] = "value"
        invalid = json.dumps(document, separators=(",", ":")).encode()
    elif mutation == "duplicate-key":
        invalid = rendered.replace(b'{"capability":', b'{"schema_version":1,"capability":', 1)
    elif mutation == "nan":
        invalid = rendered.replace(b'"schema_version":1', b'"schema_version":NaN', 1)
    else:
        mutated = copy.deepcopy(document)
        commands = cast(list[dict[str, object]], mutated["commands"])
        capability = cast(dict[str, object], mutated["capability"])
        images = cast(dict[str, object], mutated["images"])
        if mutation == "wrong-command-order":
            mutated["commands"] = list(reversed(commands))
        elif mutation == "skipped-test":
            tests = cast(dict[str, object], commands[0]["tests"])
            tests["passed"] = 1
            tests["skipped"] = 1
        elif mutation == "wrong-image":
            images["target_reference"] = "curlimages/curl:latest"
        elif mutation == "wrong-capability-mode":
            capability["canary_file_mode"] = 384
        elif mutation == "wrong-container-id":
            capability["container_id"] = "ABC123"
        elif mutation == "path-escape":
            capability["mount_source"] = "../escape"
        invalid = json.dumps(mutated, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match=message):
        study.parse_prerequisite_results(invalid, repository_root=repository_root)


def test_prerequisite_codec_rejects_changed_derived_capability_range(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    rendered = study.render_prerequisite_results(valid_prerequisite())
    document = cast(dict[str, object], json.loads(rendered))
    capability = cast(dict[str, object], document["capability"])
    capability["content_range"] = "bytes 0-0/4194305"
    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match="content range"):
        study.parse_prerequisite_results(invalid, repository_root=repository_root)


def test_prerequisite_codec_accepts_a_valid_credential_free_https_final_redirect(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    rendered = study.render_prerequisite_results(valid_prerequisite())
    document = cast(dict[str, object], json.loads(rendered))
    capability = cast(dict[str, object], document["capability"])
    capability["final_url"] = "https://cdn.example.test/object.bin"
    capability["redirect_count"] = 1
    redirected = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    parsed = study.parse_prerequisite_results(redirected, repository_root=repository_root)

    assert parsed.capability["final_url"] == "https://cdn.example.test/object.bin"
    assert parsed.capability["redirect_count"] == 1


@pytest.mark.parametrize("kind", ["prerequisites", "results"])
def test_official_publication_collision_preserves_winner_and_cleans_private_temp(
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    destination = repository_root / "examples" / "validation_study" / f"{kind}.json"
    destination.parent.mkdir(parents=True)
    winner = b"concurrent publisher\n"
    linked_sources: list[Path] = []

    def collide(source: str | Path, target: str | Path, *_args: object, **_kwargs: object) -> None:
        temporary = Path(source)
        linked_sources.append(temporary)
        assert temporary.parent == destination.parent
        assert stat.S_IMODE(temporary.stat().st_mode) == 0o600
        Path(target).write_bytes(winner)
        raise FileExistsError("simulated publication race")

    monkeypatch.setattr(study.os, "link", collide)
    if kind == "prerequisites":
        prerequisite_value = valid_prerequisite()

        def publish() -> None:
            study._publish_prerequisites(  # pyright: ignore[reportPrivateUsage]
                destination,
                prerequisite_value,
                repository_root=repository_root,
            )

    else:
        result_value = study_result_value(valid_result_document(repository_root))

        def publish() -> None:
            study._publish_results(  # pyright: ignore[reportPrivateUsage]
                destination,
                result_value,
                repository_root=repository_root,
            )

    with pytest.raises(TrafficlabError, match="already exists"):
        publish()

    assert destination.read_bytes() == winner
    assert len(linked_sources) == 1
    assert not tuple(destination.parent.glob(f".{destination.name}.*"))


def test_support_publication_refuses_an_existing_target_before_creating_a_temp(tmp_path: Path) -> None:
    destination = tmp_path / "results.json"
    destination.write_bytes(b"winner\n")

    with pytest.raises(TrafficlabError, match="already exists"):
        study._publish_support_json(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"candidate\n",
            validate=lambda _content: None,
        )

    assert destination.read_bytes() == b"winner\n"
    assert not tuple(tmp_path.glob(".results.json.*"))


def test_support_publication_closes_and_cleans_a_temp_when_fdopen_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "results.json"

    def fail_fdopen(_descriptor: int, _mode: str) -> None:
        raise OSError("simulated fdopen failure")

    monkeypatch.setattr(study.os, "fdopen", fail_fdopen)
    with pytest.raises(OSError, match="fdopen"):
        study._publish_support_json(  # pyright: ignore[reportPrivateUsage]
            destination,
            b"candidate\n",
            validate=lambda _content: None,
        )

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".results.json.*"))


def test_prerequisite_commands_are_exact_guarded_serial_argv_with_relative_projection(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    study_id = "study-1"
    url = "https://downloads.example.test/object.bin"
    evidence = f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites"
    docker = (
        "scripts/run_bounded.sh",
        "--memory-high",
        "2G",
        "--memory-max",
        "3G",
        "--swap-max",
        "512M",
        "--wall-time",
        "20m",
        "--kill-after",
        "10s",
        "--",
        "uv",
        "run",
        "--locked",
        "pytest",
        "-vv",
        "-n",
        "0",
        "-m",
        "docker",
        "--capture-image",
        f"trafficlab-validation-{study_id}:capture",
        "--junitxml",
        f"{evidence}/docker.xml",
    )
    internet = (
        "scripts/run_bounded.sh",
        "--memory-high",
        "2G",
        "--memory-max",
        "3G",
        "--swap-max",
        "512M",
        "--wall-time",
        "10m",
        "--kill-after",
        "10s",
        "--",
        "uv",
        "run",
        "--locked",
        "pytest",
        "-vv",
        "-n",
        "0",
        "-m",
        "internet",
        "--capture-image",
        f"trafficlab-validation-{study_id}:capture",
        "--internet-url",
        url,
        "--junitxml",
        f"{evidence}/internet.xml",
    )

    assert study._docker_matrix_argv(study_id) == docker  # pyright: ignore[reportPrivateUsage]
    assert study._internet_smoke_argv(study_id, url) == internet  # pyright: ignore[reportPrivateUsage]
    for kind, checked in (("docker_matrix", docker), ("internet_smoke", internet)):
        live: list[str] = list(checked)
        live[-1] = str(repository_root / checked[-1])
        assert study._live_argv(  # pyright: ignore[reportPrivateUsage]
            cast(study.PrerequisiteCommandKind, kind), checked, repository_root=repository_root
        ) == tuple(live)
        assert (
            study._project_command_argv(  # pyright: ignore[reportPrivateUsage]
                cast(study.PrerequisiteCommandKind, kind), live, repository_root=repository_root
            )
            == checked
        )
        tampered = list(checked)
        tampered[-2] = "--xml"
        with pytest.raises(ValueError, match="exact"):
            study._live_argv(  # pyright: ignore[reportPrivateUsage]
                cast(study.PrerequisiteCommandKind, kind), tampered, repository_root=repository_root
            )


@pytest.mark.parametrize(
    "invalid",
    [
        b'<testsuite tests="0" failures="0" errors="0" skipped="0"/>',
        b'<testsuite tests="2" failures="0" errors="0" skipped="1"/>',
        b'<testsuite tests="2" failures="1" errors="0" skipped="0"/>',
        b"not xml",
    ],
)
def test_junit_parser_requires_positive_all_passed_selection(invalid: bytes) -> None:
    assert study._parse_junit_counts(  # pyright: ignore[reportPrivateUsage]
        b'<testsuites tests="3" failures="0" errors="0" skipped="0">'
        b'<testsuite tests="3" failures="0" errors="0" skipped="0"/></testsuites>'
    ) == {"total": 3, "passed": 3, "failed": 0, "errors": 0, "skipped": 0}
    with pytest.raises(ValueError, match="JUnit|test"):
        study._parse_junit_counts(invalid)  # pyright: ignore[reportPrivateUsage]


def test_capability_records_digest_ids_default_user_range_canary_modes_and_cleanup(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root)
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    result = study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: now,
    )

    assert result.git_commit == "c" * 40
    assert result.tools == {
        "python_version": "3.12.3",
        "trafficlab_version": "0.1.0",
        "docker_engine_version": "27.0.0",
        "docker_compose_version": "2.29.0",
        "host_architecture": study.platform.machine(),
        "kernel_release": study.platform.release(),
        "platform": study.platform.platform(),
        "python_implementation": "CPython",
        "uv_lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
    }
    assert result.images == {
        "target_reference": study.TARGET_REFERENCE,
        "target_image_id": runner.target_id,
        "target_repo_digests": tuple(sorted(("curlimages/curl@sha256:" + "f" * 64, study.TARGET_REFERENCE))),
        "target_config_user": "curl_user",
        "capture_image_id": runner.capture_id,
        "capture_dockerfile_sha256": hashlib.sha256(CAPTURE_DOCKERFILE).hexdigest(),
        "capture_script_sha256": hashlib.sha256(CAPTURE_SCRIPT).hexdigest(),
    }
    capability = result.capability
    assert capability["status"] == 206
    assert capability["object_size_bytes"] == 4_194_304
    assert capability["redirect_count"] == 1
    assert capability["final_url"] == runner.final_url
    assert capability["container_id"] == runner.container_id
    assert capability["used_image_default_user"] is True
    assert capability["container_cleanup_verified"] is True
    assert capability["mount_directory_mode"] == 0o755
    assert capability["canary_file_mode"] == 0o666
    assert capability["canary_archive_mode"] == 0o600
    assert (
        capability["stdout_sha256"]
        == hashlib.sha256(f"status=206\nsize=1\nurl={runner.final_url}\nredirects=1\n".encode()).hexdigest()
    )
    assert capability["stderr_sha256"] == hashlib.sha256(b"curl diagnostic\n").hexdigest()
    assert stat.S_IMODE((runner.evidence / "capability.cid").stat().st_mode) == 0o600
    assert stat.S_IMODE((runner.evidence / "capability.headers").stat().st_mode) == 0o600
    assert stat.S_IMODE((runner.evidence / "capability.stdout").stat().st_mode) == 0o600
    assert stat.S_IMODE((runner.evidence / "capability.stderr").stat().st_mode) == 0o600
    assert not (runner.mount / ".capability.headers").exists()
    assert [command["tests"] for command in result.commands] == [
        frozen({"total": 7, "passed": 7, "failed": 0, "errors": 0, "skipped": 0}),
        frozen({"total": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0}),
    ]
    docker_live = study._live_argv(  # pyright: ignore[reportPrivateUsage]
        "docker_matrix",
        study._docker_matrix_argv(runner.study_id),  # pyright: ignore[reportPrivateUsage]
        repository_root=repository_root,
    )
    internet_live = study._live_argv(  # pyright: ignore[reportPrivateUsage]
        "internet_smoke",
        study._internet_smoke_argv(runner.study_id, runner.url),  # pyright: ignore[reportPrivateUsage]
        repository_root=repository_root,
    )
    assert [command for command, _timeout in runner.calls] == [
        ("git", "rev-parse", "HEAD"),
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        (
            "git",
            "check-ignore",
            "-z",
            "--stdin",
        ),
        ("docker", "version", "--format", "{{.Server.Version}}"),
        ("docker", "compose", "version", "--short"),
        ("docker", "image", "pull", study.TARGET_REFERENCE),
        ("docker", "image", "inspect", study.TARGET_REFERENCE),
        study.cold_capture_build_argv(  # pyright: ignore[reportPrivateUsage]
            f"trafficlab-validation-{runner.study_id}:capture",
            runner.evidence / "capture.iid",
        ),
        (
            "docker",
            "container",
            "ls",
            "-a",
            "--filter",
            f"name=^/{runner.capability_name}$",
            "--format",
            "{{.ID}}",
        ),
        runner.expected_capability(),
        (
            "docker",
            "container",
            "ls",
            "-a",
            "--filter",
            f"id={runner.container_id}",
            "--format",
            "{{.ID}}",
        ),
        (
            "docker",
            "container",
            "ls",
            "-a",
            "--filter",
            f"name=^/{runner.capability_name}$",
            "--format",
            "{{.ID}}",
        ),
        docker_live,
        internet_live,
        ("docker", "image", "rm", "--force", f"trafficlab-validation-{runner.study_id}:capture"),
    ]
    assert [timeout for _command, timeout in runner.calls] == [
        20.0,
        20.0,
        20.0,
        20.0,
        20.0,
        300.0,
        300.0,
        300.0,
        20.0,
        45.0,
        20.0,
        20.0,
        1230.0,
        630.0,
        300.0,
    ]
    for command, prefix, stdout, stderr in (
        (result.commands[0], "docker", b"docker pass\n", b""),
        (result.commands[1], "internet", b"internet pass\n", b""),
    ):
        junit = (runner.evidence / f"{prefix}.xml").read_bytes()
        assert command["stdout_sha256"] == hashlib.sha256(stdout).hexdigest()
        assert command["stderr_sha256"] == hashlib.sha256(stderr).hexdigest()
        assert command["junit_sha256"] == hashlib.sha256(junit).hexdigest()
        for suffix in ("stdout", "stderr", "xml"):
            assert stat.S_IMODE((runner.evidence / f"{prefix}.{suffix}").stat().st_mode) == 0o600
    prerequisite_path = repository_root / "examples" / "validation_study" / "prerequisites.json"
    assert study.parse_prerequisite_results(prerequisite_path.read_bytes(), repository_root=repository_root) == result
    for name, content_hash in result.config_sha256.items():
        config_path = repository_root / "examples" / "validation_study" / "configs" / f"{name}.toml"
        assert hashlib.sha256(config_path.read_bytes()).hexdigest() == content_hash
        assert study.load_experiment(config_path).capture.image == runner.capture_id


def test_prerequisite_rotation_preserves_the_one_checked_pre_user_agent_r6_predecessor(
    tmp_path: Path,
) -> None:
    """Only the retained r6 raw evidence can bridge the short-lived no-User-Agent format."""

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    canonical, predecessor_content, source = install_pre_user_agent_r6_predecessor(repository_root)
    assert identify_bytes(predecessor_content).as_dict() == {
        "sha256": "a6cb727911ad19333c2faffa09e7f8e246750c8524b04c8cac13f3402672d275",
        "size": 5662,
    }
    with pytest.raises(ValueError, match="capability argv"):
        study.parse_prerequisite_results(predecessor_content, repository_root=repository_root)

    runner = ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    runner.git_trees[f"{source['git_commit']}^{{tree}}"] = f"{source['git_tree']}\n".encode("ascii")
    result = study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )

    published = canonical.read_bytes()
    parsed = study.parse_prerequisite_results(published, repository_root=repository_root)
    captured_live_argv = next(
        command
        for command, _timeout in runner.calls
        if command[:2] == ("docker", "run") and f"trafficlab-validation-study-capability-{runner.study_id}" in command
    )
    projected_argv = list(captured_live_argv)
    projected_argv[8] = str((runner.evidence / "capability.cid").relative_to(repository_root))
    projected_argv[12] = f"type=bind,src={runner.mount.relative_to(repository_root)},dst=/trafficlab-study"

    assert parsed == result
    assert cast(tuple[str, ...], parsed.capability["argv"]) == tuple(projected_argv)


def test_prerequisite_rotation_recreates_the_checked_r6_archive_when_the_legacy_root_lacks_one(
    tmp_path: Path,
) -> None:
    """The exact predecessor remains recoverable when its original raw archive was not yet retained."""

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    canonical, predecessor_content, source = install_pre_user_agent_r6_predecessor(repository_root)
    archive = canonical.parent / ".study-work" / "attempts" / source["study_id"] / "prerequisites.raw.json"
    archive.unlink()
    runner = ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    runner.git_trees[f"{source['git_commit']}^{{tree}}"] = f"{source['git_tree']}\n".encode("ascii")

    study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )

    assert archive.read_bytes() == predecessor_content


def test_prerequisite_rotation_rejects_an_arbitrary_pre_user_agent_schema_one_predecessor(tmp_path: Path) -> None:
    """A synthetic schema-1 projection cannot opt in to the r6-only rotation exception."""

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    prior_runner = ScriptedPrerequisiteRunner(repository_root, study_id="study-r6")
    study.run_prerequisites(
        prior_runner.url,
        prior_runner.study_id,
        repository_root=repository_root,
        runner=prior_runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )
    study_root = repository_root / "examples" / "validation_study"
    canonical = study_root / "prerequisites.json"
    prior_archive = study_root / ".study-work" / "attempts" / prior_runner.study_id / "prerequisites.raw.json"
    prior_marker = prior_archive.with_name("prerequisites-success.json")
    legacy = cast(dict[str, object], json.loads(canonical.read_text(encoding="utf-8")))
    capability = cast(dict[str, object], legacy["capability"])
    argv = cast(list[str], capability["argv"])
    user_agent = argv.index("--user-agent")
    del argv[user_agent : user_agent + 2]
    legacy_content = json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    canonical.write_bytes(legacy_content)
    prior_archive.write_bytes(legacy_content)
    marker = cast(dict[str, object], json.loads(prior_marker.read_text(encoding="utf-8")))
    marker["prerequisites_identity"] = study.identify_bytes(legacy_content).as_dict()
    prior_marker.write_bytes(json.dumps(marker, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")

    runner = ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    with pytest.raises(TrafficlabError, match="preserved pre-User-Agent r6 predecessor"):
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    assert canonical.read_bytes() == legacy_content


def test_prerequisite_rotation_rejects_an_unreadable_retained_r6_evidence_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I/O failures while pinning the fixed retained evidence remain a canonical rotation rejection."""

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    canonical, _predecessor_content, source = install_pre_user_agent_r6_predecessor(repository_root)
    evidence = canonical.parent / ".study-work" / "evidence" / source["study_id"] / "00-prerequisites"
    runner = ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    runner.git_trees[f"{source['git_commit']}^{{tree}}"] = f"{source['git_tree']}\n".encode("ascii")
    original_iterdir = Path.iterdir

    def fail_preserved_evidence_iterdir(path: Path) -> Any:
        if path == evidence:
            raise OSError("simulated retained evidence read failure")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_preserved_evidence_iterdir)
    before = canonical.read_bytes()
    with pytest.raises(TrafficlabError, match="preserved pre-User-Agent r6 predecessor"):
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    assert canonical.read_bytes() == before


@pytest.mark.parametrize("mutation", ("study_id", "url", "source", "tree", "raw", "marker", "evidence"))
def test_prerequisite_rotation_rejects_each_mutation_of_the_preserved_r6_predecessor(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Every identity component of the exact compatibility bridge remains independently pinned."""

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    canonical, predecessor_content, source = install_pre_user_agent_r6_predecessor(repository_root)
    attempt = canonical.parent / ".study-work" / "attempts" / source["study_id"]
    evidence = canonical.parent / ".study-work" / "evidence" / source["study_id"] / "00-prerequisites"
    runner = ScriptedPrerequisiteRunner(repository_root, study_id="study-r7")
    runner.git_trees[f"{source['git_commit']}^{{tree}}"] = f"{source['git_tree']}\n".encode("ascii")

    if mutation in {"study_id", "url", "source"}:
        document = cast(dict[str, object], json.loads(predecessor_content))
        document[mutation if mutation != "source" else "git_commit"] = (
            "study-r6"
            if mutation == "study_id"
            else "https://example.test/other.bin"
            if mutation == "url"
            else "0" * 40
        )
        canonical.write_bytes(json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
    elif mutation == "tree":
        runner.git_trees[f"{source['git_commit']}^{{tree}}"] = b"0" * 40 + b"\n"
    elif mutation == "raw":
        canonical.write_bytes(predecessor_content + b" ")
    elif mutation == "marker":
        (attempt / "prerequisites-success.json").write_bytes(b"{}\n")
    else:
        (evidence / "capability.headers").write_bytes(b"mutated retained evidence\n")

    before = canonical.read_bytes()
    with pytest.raises(TrafficlabError, match="preserved pre-User-Agent r6 predecessor"):
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    assert canonical.read_bytes() == before


def test_prerequisites_remove_the_shared_capture_tag_after_a_guarded_test_failure(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, "docker-matrix-failed")

    with pytest.raises(TrafficlabError, match="docker_matrix guarded pytest failed"):
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    commands = [command for command, _timeout in runner.calls]
    shared_tag = f"trafficlab-validation-{runner.study_id}:capture"
    assert commands.count(study.cold_capture_build_argv(shared_tag, runner.evidence / "capture.iid")) == 1  # pyright: ignore[reportPrivateUsage]
    assert commands[-1] == ("docker", "image", "rm", "--force", shared_tag)


def test_prerequisite_cleanup_does_not_replace_its_guarded_test_failure(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, "docker-matrix-failed-cleanup-failed")

    with pytest.raises(TrafficlabError, match="docker_matrix guarded pytest failed") as captured:
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    assert captured.value.__notes__ == [
        "prerequisite capture image cleanup failed: could not remove owned prerequisite capture image: cleanup failed"
    ]
    assert [command for command, _timeout in runner.calls][-1] == (
        "docker",
        "image",
        "rm",
        "--force",
        f"trafficlab-validation-{runner.study_id}:capture",
    )


def test_prerequisites_preserve_an_arbitrary_primary_when_shared_capture_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interruption keeps ownership cleanup as an ordered secondary diagnostic."""

    class ControlledAbort(BaseException):
        pass

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, "capture-image-cleanup-failed")

    def abort(*_args: object, **_kwargs: object) -> study.JsonObject:
        raise ControlledAbort("controlled abort")

    monkeypatch.setattr(study, "_run_prerequisite_test", abort)
    with pytest.raises(ControlledAbort) as captured:
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    assert captured.value.__notes__ == [
        "prerequisite capture image cleanup failed: could not remove owned prerequisite capture image: cleanup failed"
    ]
    assert [command for command, _timeout in runner.calls][-1] == (
        "docker",
        "image",
        "rm",
        "--force",
        f"trafficlab-validation-{runner.study_id}:capture",
    )


@pytest.mark.parametrize("entry_kind", ("regular", "symlink", "fifo"))
@pytest.mark.parametrize(
    ("protocol", "expected"),
    (
        ("valid", "ignored prerequisite worktree entry is not permitted"),
        ("truncated", "ignored prerequisite paths must be terminal NUL-delimited"),
        ("nonempty-no-match", "ignored prerequisite paths must be empty for no-match status"),
        ("empty-match", "ignored prerequisite paths must be nonempty for match status"),
        ("nonzero", "could not resolve ignored prerequisite paths"),
    ),
)
def test_prerequisites_reject_local_exclude_ignored_worktree_entries_before_docker(
    tmp_path: Path,
    entry_kind: str,
    protocol: str,
    expected: str,
) -> None:
    """Ignored source entries use the same strict Git-NUL boundary as accepted evidence."""

    if entry_kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("nonregular FIFO entries require POSIX")
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    relative = f"locally-excluded-{entry_kind}"
    entry = repository_root / relative
    exclude = repository_root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(f"{relative}\n", encoding="utf-8")
    if entry_kind == "regular":
        entry.write_text("ignored foreign source\n", encoding="utf-8")
    elif entry_kind == "symlink":
        entry.symlink_to("source.py")
    else:
        os.mkfifo(entry)
    runner = ScriptedPrerequisiteRunner(repository_root)
    runner.ignored_worktree_paths = frozenset({relative})
    runner.ignored_worktree_protocol = protocol

    with pytest.raises(TrafficlabError, match=expected):
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    commands = [command for command, _timeout in runner.calls]
    assert any(command == ("git", "check-ignore", "-z", "--stdin") for command in commands)
    assert not any(command[:2] == ("docker", "version") for command in commands)
    assert (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / runner.study_id
        / "prerequisites.json"
    ).is_file()


def test_prerequisites_do_not_publish_success_when_shared_capture_cleanup_fails(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, "capture-image-cleanup-failed")

    with pytest.raises(TrafficlabError, match="remove owned prerequisite capture image"):
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    assert not (repository_root / "examples" / "validation_study" / "prerequisites.json").exists()
    assert [command for command, _timeout in runner.calls][-1] == (
        "docker",
        "image",
        "rm",
        "--force",
        f"trafficlab-validation-{runner.study_id}:capture",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "dirty-tree",
        "wrong-python",
        "target-digest-absent",
        "capture-iid-tag",
        "capture-iid-missing",
        "preexisting-name",
        "preexisting-cid",
        "capability-daemon-error",
        "capability-lingering-unowned",
        "capability-timeout-owned",
        "capability-timeout-unowned",
        "canary-not-written",
        "canary-replaced",
        "wrong-write-out",
        "range-ignored",
        "oversize-object",
        "docker-matrix-failed",
        "internet-skipped",
        "config-publication-failed",
    ],
)
def test_prerequisites_stop_at_first_failure_preserve_primary_and_publish_no_valid_json(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, mutation)
    install_prerequisite_failure(mutation, monkeypatch)

    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    with pytest.raises(TrafficlabError, match="prerequisite validation failed") as captured:
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: now,
        )

    assert "restart with a new study ID" in captured.value.corrective_action
    prerequisite_path = repository_root / "examples" / "validation_study" / "prerequisites.json"
    assert not prerequisite_path.exists()
    config_directory = repository_root / "examples" / "validation_study" / "configs"
    assert not config_directory.exists() or not tuple(config_directory.glob("*.toml"))
    commands = [command for command, _timeout in runner.calls]
    docker_guard = study._live_argv(  # pyright: ignore[reportPrivateUsage]
        "docker_matrix",
        study._docker_matrix_argv(runner.study_id),  # pyright: ignore[reportPrivateUsage]
        repository_root=repository_root,
    )
    internet_guard = study._live_argv(  # pyright: ignore[reportPrivateUsage]
        "internet_smoke",
        study._internet_smoke_argv(runner.study_id, runner.url),  # pyright: ignore[reportPrivateUsage]
        repository_root=repository_root,
    )
    forbidden_prefixes: list[tuple[str, ...]] = list(
        {
            "dirty-tree": (("docker", "version"),),
            "wrong-python": (("docker", "version"),),
            "target-digest-absent": (("docker", "build"),),
            "capture-iid-tag": (("docker", "container", "inspect"),),
            "capture-iid-missing": (("docker", "container", "inspect"),),
            "preexisting-name": (("docker", "run", "--rm"),),
            "preexisting-cid": (("docker", "run", "--rm"),),
        }.get(mutation, ())
    )
    if mutation not in {"docker-matrix-failed", "internet-skipped", "config-publication-failed"}:
        forbidden_prefixes.append(docker_guard)
    if mutation not in {"internet-skipped", "config-publication-failed"}:
        forbidden_prefixes.append(internet_guard)
    for prefix in forbidden_prefixes:
        assert not any(command[: len(prefix)] == prefix for command in commands)
    if mutation == "capability-timeout-owned":
        assert ("docker", "container", "rm", "--force", runner.container_id) in commands
        assert runner.container_running is False
    if mutation in {"capability-timeout-unowned", "capability-lingering-unowned"}:
        assert not any(command[:3] == ("docker", "container", "rm") for command in commands)
        assert runner.container_running is True
        assert runner.container_id in str(captured.value)
    if mutation == "capability-daemon-error":
        assert "daemon" in str(captured.value).lower()
    if mutation.startswith("capability-timeout"):
        assert "timed out" in str(captured.value).lower()
    evidence = runner.evidence
    if mutation in {
        "canary-not-written",
        "canary-replaced",
        "wrong-write-out",
        "range-ignored",
        "oversize-object",
        "docker-matrix-failed",
        "internet-skipped",
        "config-publication-failed",
    }:
        assert evidence.is_dir()
    if mutation in {
        "capability-timeout-owned",
        "capability-timeout-unowned",
        "canary-not-written",
        "canary-replaced",
        "wrong-write-out",
        "range-ignored",
        "oversize-object",
    }:
        canary = runner.mount / ".capability.headers"
        archive = evidence / "capability.headers"
        assert archive.read_bytes() == canary.read_bytes()
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600


def test_prerequisites_wrap_invalid_study_id_without_attempt_preservation(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()

    with pytest.raises(TrafficlabError, match="prerequisite validation failed"):
        study.run_prerequisites(
            "https://downloads.example.test/object.bin",
            "INVALID_ID",
            repository_root=repository_root,
            runner=StudyIdentityRunner(repository_root),
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    assert not (repository_root / "examples" / "validation_study" / ".study-work").exists()


def test_capability_normal_exit_proves_exact_full_id_and_anchored_name_absent(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root)

    result = study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )

    commands = [command for command, _timeout in runner.calls]
    id_listing = (
        "docker",
        "container",
        "ls",
        "-a",
        "--filter",
        f"id={runner.container_id}",
        "--format",
        "{{.ID}}",
    )
    name_listing = (
        "docker",
        "container",
        "ls",
        "-a",
        "--filter",
        f"name=^/{runner.capability_name}$",
        "--format",
        "{{.ID}}",
    )
    assert result.capability["container_cleanup_verified"] is True
    assert commands.count(id_listing) == 1
    assert commands.count(name_listing) == 2


def test_capability_removes_only_a_lingering_exact_owned_id_after_success(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, "capability-lingering-owned")

    result = study.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )

    commands = [command for command, _timeout in runner.calls]
    assert result.capability["container_cleanup_verified"] is True
    assert ("docker", "container", "rm", "--force", runner.container_id) in commands
    assert (
        commands.count(
            (
                "docker",
                "container",
                "ls",
                "-a",
                "--filter",
                f"id={runner.container_id}",
                "--format",
                "{{.ID}}",
            )
        )
        == 2
    )
    assert (
        commands.count(
            (
                "docker",
                "container",
                "ls",
                "-a",
                "--filter",
                f"name=^/{runner.capability_name}$",
                "--format",
                "{{.ID}}",
            )
        )
        == 2
    )
    assert runner.container_running is False


@pytest.mark.parametrize(
    "mutation",
    [
        "capability-post-id-daemon-error",
        "capability-post-name-daemon-error",
        "capability-name-reclaimed",
        "capability-lingering-owned-name-reclaimed",
    ],
)
def test_capability_cleanup_fails_closed_for_each_listing_and_an_unrelated_name_reclaimer(
    mutation: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, mutation)

    with pytest.raises(TrafficlabError, match="prerequisite validation failed") as captured:
        study.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    commands = [command for command, _timeout in runner.calls]
    unrelated_id = "f" * 64
    assert not any(command == ("docker", "container", "rm", "--force", unrelated_id) for command in commands)
    if mutation == "capability-lingering-owned-name-reclaimed":
        assert ("docker", "container", "rm", "--force", runner.container_id) in commands
    else:
        assert not any(command[:3] == ("docker", "container", "rm") for command in commands)
    if "daemon-error" in mutation:
        assert "daemon unavailable" in str(captured.value)
    else:
        assert "still exists" in str(captured.value)
    assert (runner.evidence / "capability.stdout").is_file()
    assert (runner.evidence / "capability.stderr").is_file()
    assert (runner.evidence / "capability.headers").is_file()
    assert not (repository_root / "examples" / "validation_study" / "prerequisites.json").exists()


def test_capability_absence_helpers_reject_invalid_daemon_evidence_and_report_absence(tmp_path: Path) -> None:
    container_id = "e" * 64

    def invalid_utf8(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, check, capture_output, shell, timeout
        return subprocess.CompletedProcess(tuple(argv), 0, stdout=b"\xff", stderr=b"")

    with pytest.raises(ValueError, match="UTF-8"):
        study._container_listing(  # pyright: ignore[reportPrivateUsage]
            tmp_path,
            f"id={container_id}",
            runner=invalid_utf8,
        )

    def invalid_inspect(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, check, capture_output, shell, timeout
        command = tuple(argv)
        stdout = f"{container_id}\n".encode() if command[:4] == ("docker", "container", "ls", "-a") else b"not JSON"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    with pytest.raises(ValueError, match="must return JSON"):
        study._remove_owned_capability_if_present(  # pyright: ignore[reportPrivateUsage]
            repository_root=tmp_path,
            study_id="study-1",
            capability_name="trafficlab-validation-study-capability-study-1",
            container_id=container_id,
            runner=invalid_inspect,
        )

    cid = tmp_path / "capability.cid"
    cid.write_text(f"{container_id}\n", encoding="ascii")
    cid.chmod(0o600)

    def absent(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, check, capture_output, shell, timeout
        return subprocess.CompletedProcess(tuple(argv), 0, stdout=b"", stderr=b"")

    diagnostic = study._cleanup_failed_capability(  # pyright: ignore[reportPrivateUsage]
        repository_root=tmp_path,
        study_id="study-1",
        capability_name="trafficlab-validation-study-capability-study-1",
        capability_cid=cid,
        runner=absent,
    )
    assert diagnostic == f"capability container {container_id} is absent"

    cid.unlink()
    unreadable = study._cleanup_failed_capability(  # pyright: ignore[reportPrivateUsage]
        repository_root=tmp_path,
        study_id="study-1",
        capability_name="trafficlab-validation-study-capability-study-1",
        capability_cid=cid,
        runner=absent,
    )
    assert "could not read the exclusive CID" in unreadable


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("capability-start-error", "could not start"),
        ("capability-nonzero", "failed with status 7"),
        ("capability-missing-cid", "could not read capability CID"),
    ],
)
def test_capability_failure_boundaries_retain_exact_context(
    mutation: str,
    message: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    runner = ScriptedPrerequisiteRunner(repository_root, mutation)
    runner.evidence.mkdir(parents=True)
    runner.mount.mkdir(parents=True)

    with pytest.raises(ValueError, match=message):
        study._prepare_capability(  # pyright: ignore[reportPrivateUsage]
            repository_root=repository_root,
            study_id=runner.study_id,
            url=runner.url,
            evidence_directory=runner.evidence,
            mount_directory=runner.mount,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )


def test_prerequisite_cli_requires_exact_subcommand_arguments_and_reports_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reject_calls: list[tuple[str, ...]] = []

    def reject_runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, check, capture_output, shell, timeout
        reject_calls.append(tuple(argv))
        raise AssertionError("invalid CLI input must not run a command")

    assert study.main([], repository_root=tmp_path, runner=reject_runner) == 2
    assert "usage:" in capsys.readouterr().err
    invalid_arguments = (
        ["prerequisites"],
        ["prerequisites", "--url", "https://downloads.example.test/object.bin"],
        ["prerequisites", "--study-id", "study-1"],
        ["prerequisites", "--url", "http://example.test/object", "--study-id", "study-1"],
        ["prerequisites", "--url", "https://downloads.example.test/object.bin", "--study-id", "INVALID"],
        [
            "prerequisites",
            "--url",
            "https://downloads.example.test/object.bin",
            "--study-id",
            "study-1",
            "extra",
        ],
    )
    for arguments in invalid_arguments:
        assert study.main(arguments, repository_root=tmp_path, runner=reject_runner) == 2
        assert capsys.readouterr().err
    assert reject_calls == []

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, "dirty-tree")
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    assert (
        study.main(
            ["prerequisites", "--url", runner.url, "--study-id", runner.study_id],
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: now,
        )
        == 2
    )
    error = capsys.readouterr().err.strip()
    assert error.startswith("validation-study: Validation Study prerequisite validation failed:")
    assert "; preserve the ignored evidence" in error


def test_result_codec_rejects_nonoracle_workload_argv(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = valid_result_document(repository_root)
    protocol = cast(dict[str, object], document["protocol"])
    workload = cast(list[dict[str, object]], protocol["workloads"])[0]
    workload["argv"] = ["--url", "https://downloads.example.test/object.bin"]
    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match="workload definition"):
        study.parse_study_results(invalid, repository_root=repository_root)


def test_result_codec_rejects_a_nonbest_family_champion_as_winner(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = valid_result_document(repository_root)
    run = cast(list[dict[str, object]], document["runs"])[0]
    champion = cast(list[dict[str, object]], run["family_champions"])[0]
    run["winner"] = {
        key: copy.deepcopy(champion[key]) for key in ("family", "candidate_id", "genes", "selection_fitness")
    }
    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match="overall best"):
        study.parse_study_results(invalid, repository_root=repository_root)


def test_result_codec_round_trips_nine_runs_reproduction_and_recomputed_summaries(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = valid_result_document(repository_root)
    value = study_result_value(document)

    rendered = study.render_study_results(value)
    parsed = study.parse_study_results(rendered, repository_root=repository_root)

    assert study.render_study_results(parsed) == rendered
    assert len(parsed.runs) == 9
    assert tuple((run.execution_order, run.run_id) for run in parsed.runs) == tuple(
        (order, run_id) for order, run_id, _workload, _repeat in study.PRIMARY_ORDER
    )
    assert len(parsed.reproduction.document) == 27
    assert rendered.endswith(b"\n")
    assert b": " not in rendered
    assert not contains_none(json.loads(rendered))
    destination = repository_root / "examples" / "validation_study" / "results.json"
    destination.parent.mkdir(parents=True)
    study._publish_results(destination, value, repository_root=repository_root)  # pyright: ignore[reportPrivateUsage]
    assert destination.read_bytes() == rendered


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-primary-order",
        "duplicate-run-key",
        "missing-family",
        "wrong-method-order",
        "nullable-value",
        "stale-statistic",
        "wrong-pair-average",
        "winner-count-mismatch",
        "wrong-reproduction-source",
        "extra-artifact-hash",
        "true-reuse",
        "wrong-guard",
    ],
)
def test_result_codec_rejects_nested_schema_and_cross_record_inconsistency(
    mutation: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = valid_result_document(repository_root)
    runs = cast(list[dict[str, object]], document["runs"])
    protocol = cast(dict[str, object], document["protocol"])
    summaries = cast(list[dict[str, object]], document["workload_summaries"])
    natural = cast(list[dict[str, object]], document["natural_variation"])
    reproduction = cast(dict[str, object], document["reproduction"])

    if mutation == "wrong-primary-order":
        runs[0], runs[1] = runs[1], runs[0]
    elif mutation == "duplicate-run-key":
        runs[1]["key"] = copy.deepcopy(runs[0]["key"])
    elif mutation == "missing-family":
        cast(list[object], runs[0]["family_champions"]).pop()
    elif mutation == "wrong-method-order":
        protocol["methods"] = list(reversed(cast(list[object], protocol["methods"])))
    elif mutation == "nullable-value":
        runs[0]["elapsed_seconds"] = None
    elif mutation == "stale-statistic":
        cast(dict[str, object], summaries[0]["runtime"])["mean"] = 99.0
    elif mutation == "wrong-pair-average":
        first_pair = cast(list[dict[str, object]], natural[0]["pairs"])[0]
        cast(dict[str, object], first_pair["symmetric"])["aggregate"] = 0.0
    elif mutation == "winner-count-mismatch":
        cast(dict[str, object], summaries[0]["winner_counts"])["mmpp"] = 2
    elif mutation == "wrong-reproduction-source":
        reproduction["source_key"] = {"workload": "short", "repeat": 2}
    elif mutation == "extra-artifact-hash":
        cast(dict[str, object], runs[0]["artifact_sha256"])["extra"] = HASH
    elif mutation == "true-reuse":
        cast(dict[str, object], runs[0]["reuse"])["capture"] = True
    elif mutation == "wrong-guard":
        cast(list[str], reproduction["guard_command"]).pop()

    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(ValueError):
        study.parse_study_results(invalid, repository_root=repository_root)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("integer-gene", "exact.*float"),
        ("escaping-path", "repository-relative"),
        ("score-over-one", r"\[0.0, 1.0\]"),
        ("wrong-trace-count", "packet totals"),
        ("wrong-artifact-set", "exact keys"),
        ("raw-window-lineage", "observation windows"),
        ("raw-count-lineage", "event counts"),
    ],
)
def test_result_codec_rejects_scalar_path_gene_trace_and_artifact_violations(
    mutation: str,
    message: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = valid_result_document(repository_root)
    run = cast(list[dict[str, object]], document["runs"])[0]
    if mutation == "integer-gene":
        champion = cast(list[dict[str, object]], run["family_champions"])[2]
        champion["genes"] = [1]
    elif mutation == "escaping-path":
        run["config_path"] = "../escape.toml"
    elif mutation == "score-over-one":
        fresh_simulation = cast(dict[str, object], run["fresh_simulation"])
        cast(dict[str, object], fresh_simulation["score"])["aggregate"] = 1.1
    elif mutation == "wrong-trace-count":
        reference = cast(dict[str, object], run["reference"])
        cast(dict[str, object], reference["packet_totals"])["outbound"] = 99
    elif mutation == "wrong-artifact-set":
        cast(dict[str, object], run["artifact_sha256"]).pop("run.log")
    elif mutation == "raw-window-lineage":
        generated = cast(dict[str, object], run["generated"])
        generated["observation_window_seconds"] = 99.0
    elif mutation == "raw-count-lineage":
        raw_sequence = cast(dict[str, object], run["raw_sequence"])
        raw_sequence["trial_event_count"] = 99
        raw_sequence["final_event_count"] = 99
    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match=message):
        study.parse_study_results(invalid, repository_root=repository_root)
