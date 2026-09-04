"""Science owner for Validation Study tooling."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from itertools import combinations
from pathlib import Path
from statistics import variance
from typing import TYPE_CHECKING, cast

from scripts.validation_study.audit import profile_checks
from scripts.validation_study.audit.artifacts import (
    config_pair,
    fixture_profile,
    reconstruct_held_out_trace,
    require_held_out_log_lineage,
    require_training_log_lineage,
)
from scripts.validation_study.audit.common import (
    FIXTURE_STUDY_ID,
    FIXTURE_URL,
    WORKLOADS,
    Training,
    artifact_identity,
    canonical_jsonl,
    exact,
    fail,
    frozen_workload_profiles,
    mean,
    parse_json_object,
    parse_run_log_records,
    read_regular,
    relative_path,
    repeat_number,
    require_directory,
    string,
    training_runtime,
    workload_name,
)
from scripts.validation_study.audit.environment import config_semantics
from scripts.validation_study.audit.report_values import (
    comparison_score,
)
from scripts.validation_study.audit.report_values import (
    sample_summary as _sample_summary,
)
from scripts.validation_study.audit.report_values import (
    winner_family as _winner_family,
)
from scripts.validation_study.common import ARTIFACT_NAMES, MODEL_FAMILIES, PUBLISHED_METHOD_ORDER
from trafficlab.capture.validation import validate_capture_pair
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_configuration_pair, render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import align_generated, normalize_reference, parse_capture_metadata
from trafficlab.comparison.codec import parse_comparison_result, render_comparison_result, similarity_settings_identity
from trafficlab.comparison.metrics import compare_final_traces, compare_traces
from trafficlab.fitting.genetic.checkpoint import parse_checkpoint, render_history_csv
from trafficlab.fitting.genetic.population import rank_candidates
from trafficlab.fitting.genetic.strategy import make_strategy_context
from trafficlab.generation.models.fitted_model import load_best_model, render_best_model
from trafficlab.generation.stage import reproduce_generated_pcapng

if TYPE_CHECKING:
    from scripts.validation_study.records import HeldOutEvaluation

_capture_lineage = profile_checks.capture_lineage
_require_config_images = profile_checks.require_config_images
_require_config_workload_argv = profile_checks.require_config_workload_argv
_require_frozen_profile = profile_checks.require_frozen_profile


def _validation_profile(*, workload: str, url: str, environment: Mapping[str, object]) -> ExperimentConfig:
    """Independently reconstruct one non-operational frozen Validation Study profile."""
    spec = frozen_workload_profiles(url)[workload]
    return ExperimentConfig.model_validate(
        {
            "run": {"directory": Path("."), "minimum_free_bytes": 1048576, "master_seed": 73, "final_seed": 97},
            "target": {
                "image": environment["target_image_reference"],
                "argv": spec.argv,
                "environment": {},
                "working_directory": "/",
                "mounts": ({"source": Path("."), "target": "/trafficlab-study", "read_only": False},),
            },
            "capture": {
                "image": environment["capture_image_reference"],
                "network_probe_url": url,
                "readiness_timeout_seconds": 10.0,
                "workload_timeout_seconds": spec.workload_timeout_seconds,
                "flush_timeout_seconds": 5.0,
                "total_timeout_seconds": spec.total_timeout_seconds,
            },
            "generation": {
                "trial": {"max_packets": 25000, "max_output_bytes": 40000000, "max_wall_seconds": 5.0},
                "final": {"max_packets": 50000, "max_output_bytes": 80000000, "max_wall_seconds": 10.0},
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
                "enabled": MODEL_FAMILIES,
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
                "max_direction_bin_cells": 100000,
                "cvm_iat_weight": 0.5,
                "cvm_size_weight": 0.5,
                "cvm_global_weight": 0.5,
                "cvm_uplink_weight": 0.25,
                "cvm_downlink_weight": 0.25,
                "ad_iat_weight": 0.5,
                "ad_size_weight": 0.5,
                "ad_global_weight": 0.5,
                "ad_uplink_weight": 0.25,
                "ad_downlink_weight": 0.25,
                "js_iat_bin_count": 8,
                "js_iat_weight": 0.5,
                "js_mark_weight": 0.5,
                "mmd_feature_count": 16,
                "mmd_seed": 2026,
                "mmd_scale_floor": 0.001,
                "method_weights": {
                    "frame_size_ks": 0.125,
                    "iat_ks": 0.125,
                    "autocorrelation": 0.125,
                    "multiscale_rate": 0.125,
                    "cramer_von_mises": 0.125,
                    "anderson_darling": 0.125,
                    "jensen_shannon": 0.125,
                    "approximate_mmd": 0.125,
                },
                "postfit": {
                    "dispersion": {
                        "widths_seconds": (0.25, 1.0),
                        "scale_weights": (0.5, 0.5),
                        "fano_weight": 0.5,
                        "allan_weight": 0.5,
                    },
                    "transition": {
                        "size_bin_count": 2,
                        "iat_bin_count": 2,
                        "pseudocount": 0.5,
                        "occupancy_weight": 0.34,
                        "transition_rows_weight": 0.33,
                        "runs_weight": 0.33,
                    },
                    "c2st": {
                        "feature_version": "window-v1",
                        "window_width_seconds": 0.25,
                        "fold_count": 3,
                        "guard_window_count": 1,
                        "maximum_window_count": 4096,
                        "l2_regularization": 1.0,
                        "maximum_iterations": 200,
                        "tolerance": 1e-9,
                    },
                },
            },
        }
    )


def load_frozen_profiles(
    repository: Path, *, environment: Mapping[str, object], protocol: Mapping[str, object], url: str
) -> dict[str, ExperimentConfig]:
    """Reconstruct the source-owned profile for every retained workload."""
    study_id = string(protocol["study_id"], name="protocol study ID")
    source_commit = string(environment["source_commit"], name="environment source_commit")
    if study_id == FIXTURE_STUDY_ID:
        if url != FIXTURE_URL:
            fail(
                "artifact_foreign",
                "protocol.json",
                "fixture-study must use its exact frozen URL",
                "restore the deterministic fixture protocol",
            )
        profiles = {
            workload: fixture_profile(
                repository, source_commit=source_commit, workload=workload, url=url, environment=environment
            )
            for workload in WORKLOADS
        }
    else:
        profiles = {
            workload: _validation_profile(workload=workload, url=url, environment=environment) for workload in WORKLOADS
        }
    for workload, profile in profiles.items():
        if tuple(profile.models.enabled) != MODEL_FAMILIES:
            fail(
                "scientific_semantics_incompatible",
                f"frozen-profile/{workload}",
                "frozen profile must enable exactly poisson_empirical, markov_renewal, and mmpp",
                "restore the complete frozen model-family profile",
            )
    return profiles


def rebuild_training(
    bundle: Path,
    value: object,
    *,
    protocol: dict[str, object],
    environment: Mapping[str, object],
    frozen_profiles: Mapping[str, ExperimentConfig],
    url: str,
) -> Training:
    document = exact(
        value,
        (
            "directory",
            "capture_lineage",
            "portable_config",
            "portable_config_identity",
            "realized_config",
            "realized_config_identity",
            "reference_identity",
            "repeat",
            "run_config_identity",
            "workload",
        ),
        name="training record",
    )
    workload = workload_name(document["workload"], name="training workload")
    repeat = repeat_number(document["repeat"], name="training repeat")
    directory_relative = relative_path(document["directory"], name="training directory")
    expected_directory = f"training/{workload}/r{repeat}"
    if directory_relative != expected_directory:
        fail(
            "artifact_foreign",
            directory_relative,
            "training directory does not match its workload and repeat",
            "restore canonical index",
        )
    directory = require_directory(bundle / directory_relative, name=directory_relative)
    portable = relative_path(document["portable_config"], name="training portable configuration")
    realized = relative_path(document["realized_config"], name="training realized configuration")
    expected_portable = f"configs/training-{workload}-r{repeat}.portable.toml"
    expected_realized = f"configs/training-{workload}-r{repeat}.realized.toml"
    if (portable, realized) != (expected_portable, expected_realized):
        fail(
            "artifact_foreign",
            directory_relative,
            "training configuration paths are not canonical",
            "restore matching configuration paths",
        )
    config, _ = config_pair(bundle, portable, realized, directory=directory, name=directory_relative)
    _require_config_images(config, environment, affected=directory_relative)
    _require_config_workload_argv(config, workload=workload, url=url, affected=directory_relative)
    _require_frozen_profile(config, frozen_profiles[workload], affected=directory_relative)
    if config.run.final_seed != protocol["final_seed"] or tuple(config.genetic.trial_seeds) != tuple(
        cast(list[int], protocol["selection_seeds"])
    ):
        fail(
            "scientific_semantics_incompatible",
            directory_relative,
            "training configuration does not match frozen seeds",
            "restore frozen configuration",
        )
    contents = {
        artifact: read_regular(directory / artifact, affected=f"{directory_relative}/{artifact}")
        for artifact in ARTIFACT_NAMES
    }
    try:
        pair = load_configuration_pair(directory / "experiment.toml")
    except TrafficlabError as error:
        fail(
            "artifact_corrupt",
            f"{directory_relative}/experiment.toml",
            f"run configuration is invalid: {error}",
            "restore canonical run configuration",
        )
    if render_effective_config(pair.portable) != contents["experiment.toml"] or config_semantics(
        pair.realized
    ) != config_semantics(config):
        fail(
            "artifact_foreign",
            f"{directory_relative}/experiment.toml",
            "run configuration does not match retained configuration pair",
            "restore matching run configuration",
        )
    canonical_jsonl(contents["run.log"], name=f"{directory_relative}/run.log")
    run_log_records = parse_run_log_records(contents["run.log"], name=f"{directory_relative}/run.log")
    runtime_seconds = training_runtime(
        contents["run.log"], name=f"{directory_relative}/run.log", workload=workload, repeat=repeat
    )
    try:
        inspection = validate_capture_pair(directory / "capture.json", directory / "reference.pcapng", deadline=None)
        metadata = parse_capture_metadata(contents["capture.json"], source=directory / "capture.json")
        reference, window = normalize_reference(
            read_pcapng_bytes(contents["reference.pcapng"], metadata, source=directory / "reference.pcapng")
        )
        context = make_strategy_context(
            config,
            reference,
            window,
            directory,
            experiment_identity=identify_bytes(contents["experiment.toml"]),
            reference_identity=identify_bytes(contents["reference.pcapng"]),
            capture_identity=identify_bytes(contents["capture.json"]),
        )
        checkpoint = parse_checkpoint(contents["checkpoint.json"], context.compatibility)
        best = load_best_model(contents["best_model.json"], source=directory / "best_model.json")
        _, generated = reproduce_generated_pcapng(best, metadata)
        parsed_generated = read_pcapng_bytes(
            contents["generated.pcapng"], metadata, source=directory / "generated.pcapng"
        )
    except TrafficlabError as error:
        fail(
            "artifact_corrupt",
            directory_relative,
            f"training artifact reconstruction failed: {error}",
            "restore matching retained training artifacts",
        )
    if document["capture_lineage"] != _capture_lineage(contents["capture.json"], environment):
        fail(
            "artifact_foreign",
            directory_relative,
            "training capture lineage does not match retained capture bytes and environment",
            "restore matching training capture lineage",
        )
    if (
        inspection.packet_count != len(reference)
        or render_history_csv(checkpoint) != contents["ga_history.csv"]
        or render_best_model(best) != contents["best_model.json"]
    ):
        fail(
            "artifact_foreign",
            directory_relative,
            "training artifacts are not their canonical projections",
            "restore canonical training artifacts",
        )
    candidate = rank_candidates(checkpoint.population, family_priority=checkpoint.family_priority)[0]
    if (
        (candidate.family, candidate.genes) != (best.family, best.genes)
        or best.reference_identity != identify_bytes(contents["reference.pcapng"])
        or best.capture_identity != identify_bytes(contents["capture.json"])
    ):
        fail(
            "artifact_foreign",
            directory_relative,
            "checkpoint winner and retained best model disagree",
            "restore matching checkpoint and best model",
        )
    if (
        best.final_seed != config.run.final_seed
        or best.final_limits != config.generation.final
        or best.observation_window_seconds != window
    ):
        fail(
            "scientific_semantics_incompatible",
            directory_relative,
            "best model final controls do not match normalized training reference",
            "restore frozen training evidence",
        )
    if generated.content != contents["generated.pcapng"] or parsed_generated != generated.trace:
        fail(
            "artifact_foreign",
            f"{directory_relative}/generated.pcapng",
            "generated trace does not reproduce from the retained model",
            "restore matching generated trace",
        )
    settings_identity = similarity_settings_identity(config.similarity)
    expected_comparison = compare_final_traces(
        reference,
        align_generated(generated.trace, window),
        window,
        config.similarity,
        {
            "capture_json": identify_bytes(contents["capture.json"]),
            "generated_pcapng": identify_bytes(contents["generated.pcapng"]),
            "reference_pcapng": identify_bytes(contents["reference.pcapng"]),
            "similarity_settings": settings_identity,
        },
    )
    try:
        persisted_comparison = parse_comparison_result(contents["similarity.json"])
    except (TrafficlabError, ValueError) as error:
        fail(
            "artifact_corrupt",
            f"{directory_relative}/similarity.json",
            f"comparison is invalid: {error}",
            "restore canonical comparison",
        )
    if (
        render_comparison_result(persisted_comparison) != contents["similarity.json"]
        or persisted_comparison != expected_comparison
    ):
        fail(
            "artifact_foreign",
            f"{directory_relative}/similarity.json",
            "comparison does not match reconstructed inputs",
            "restore matching comparison evidence",
        )
    identities = {
        "portable_config_identity": artifact_identity(read_regular(bundle / portable, affected=portable)),
        "realized_config_identity": artifact_identity(read_regular(bundle / realized, affected=realized)),
        "reference_identity": artifact_identity(contents["reference.pcapng"]),
        "run_config_identity": artifact_identity(contents["experiment.toml"]),
    }
    if any((document[name] != identity for name, identity in identities.items())):
        fail(
            "artifact_foreign",
            directory_relative,
            "training index identities do not match retained bytes",
            "restore matching index identities",
        )
    require_training_log_lineage(
        run_log_records,
        name=f"{directory_relative}/run.log",
        environment=environment,
        contents=contents,
        reference_count=len(reference),
        generated_count=len(generated.trace),
        checkpoint=checkpoint,
        best=best,
        comparison=persisted_comparison,
        window=window,
    )
    return Training(
        workload,
        repeat,
        directory,
        contents,
        config,
        reference,
        window,
        runtime_seconds,
        checkpoint,
        best,
        persisted_comparison,
    )


def rebuild_held_out(
    bundle: Path,
    value: object,
    training: Training,
    *,
    final_seed: int,
    training_references: set[str],
    environment: Mapping[str, object],
    frozen_profiles: Mapping[str, ExperimentConfig],
) -> tuple[str, set[str], HeldOutEvaluation]:
    document = exact(
        value, ("capture_lineage", "directory", "training_directory", "workload"), name="held-out index record"
    )
    workload = workload_name(document["workload"], name="held-out workload")
    directory_relative = relative_path(document["directory"], name="held-out directory")
    expected_directory = f"held_out/{workload}"
    if (workload, directory_relative, document["training_directory"]) != (
        training.workload,
        expected_directory,
        f"training/{training.workload}/r{training.repeat}",
    ):
        fail(
            "artifact_foreign",
            directory_relative,
            "held-out record does not bind its frozen training model",
            "restore matching held-out evidence",
        )
    directory = require_directory(bundle / directory_relative, name=directory_relative)
    portable = f"{directory_relative}/portable.toml"
    realized = f"{directory_relative}/realized.toml"
    config, config_paths = config_pair(bundle, portable, realized, directory=directory, name=directory_relative)
    _require_config_images(config, environment, affected=directory_relative)
    _require_frozen_profile(config, frozen_profiles[workload], affected=directory_relative)
    if config_semantics(config) != config_semantics(training.config) or config.run.final_seed != final_seed:
        fail(
            "scientific_semantics_incompatible",
            directory_relative,
            "held-out configuration does not match frozen training controls",
            "restore matching held-out configuration",
        )
    names = ("capture.json", "reference.pcapng", "generated.pcapng", "similarity.json", "record.json", "run.log")
    contents = {name: read_regular(directory / name, affected=f"{directory_relative}/{name}") for name in names}
    canonical_jsonl(contents["run.log"], name=f"{directory_relative}/run.log")
    run_log_records = parse_run_log_records(contents["run.log"], name=f"{directory_relative}/run.log")
    reference_identity = identify_bytes(contents["reference.pcapng"])
    if reference_identity.sha256 in training_references:
        fail(
            "artifact_foreign",
            f"{directory_relative}/reference.pcapng",
            "held-out reference is not independent from training captures",
            "capture a new held-out reference",
        )
    try:
        evaluation = reconstruct_held_out_trace(
            training,
            config=config,
            capture_content=contents["capture.json"],
            capture_source=directory / "capture.json",
            reference_content=contents["reference.pcapng"],
            reference_source=directory / "reference.pcapng",
        )
        persisted = parse_comparison_result(contents["similarity.json"])
    except TrafficlabError as error:
        fail(
            "artifact_corrupt",
            directory_relative,
            f"held-out reconstruction failed: {error}",
            "restore matching held-out evidence",
        )
    if document["capture_lineage"] != _capture_lineage(contents["capture.json"], environment):
        fail(
            "artifact_foreign",
            directory_relative,
            "held-out capture lineage does not match retained capture bytes and environment",
            "restore matching held-out capture lineage",
        )
    if (
        evaluation.generated_pcapng != contents["generated.pcapng"]
        or evaluation.comparison_json != contents["similarity.json"]
        or persisted != evaluation.comparison
    ):
        fail(
            "artifact_foreign",
            directory_relative,
            "held-out outputs do not reproduce from the frozen training model",
            "restore matching held-out outputs",
        )
    record = exact(
        parse_json_object(contents["record.json"], name=f"{directory_relative}/record.json"),
        (
            "capture_identity",
            "capture_lineage",
            "comparison_identity",
            "generated_identity",
            "observation_window_seconds",
            "reference_identity",
            "seed",
            "training_directory",
            "training_model_identity",
            "workload",
        ),
        name=f"{directory_relative}/record.json",
    )
    expected = {
        "capture_identity": evaluation.capture_identity.as_dict(),
        "capture_lineage": _capture_lineage(contents["capture.json"], environment),
        "comparison_identity": artifact_identity(contents["similarity.json"]),
        "generated_identity": evaluation.generated_identity.as_dict(),
        "observation_window_seconds": evaluation.observation_window_seconds,
        "reference_identity": evaluation.reference_identity.as_dict(),
        "seed": final_seed,
        "training_directory": f"training/{training.workload}/r{training.repeat}",
        "training_model_identity": evaluation.training_model_identity.as_dict(),
        "workload": workload,
    }
    if record != expected:
        fail(
            "artifact_foreign",
            f"{directory_relative}/record.json",
            "held-out record does not match reconstructed evidence",
            "restore matching held-out record",
        )
    require_held_out_log_lineage(
        run_log_records,
        name=f"{directory_relative}/run.log",
        workload=workload,
        environment=environment,
        capture=contents["capture.json"],
        reference=contents["reference.pcapng"],
        experiment=read_regular(bundle / realized, affected=realized),
    )
    return (directory_relative, config_paths | {f"{directory_relative}/{name}" for name in names}, evaluation)


def _controlled_weight_analysis(training: Sequence[Training]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    alternate_weights = {
        "autocorrelation": 0.2,
        "frame_size_ks": 0.4,
        "iat_ks": 0.2,
        "multiscale_rate": 0.2,
        "cramer_von_mises": 0.0,
        "anderson_darling": 0.0,
        "jensen_shannon": 0.0,
        "approximate_mmd": 0.0,
    }
    for workload in WORKLOADS:
        group = [item for item in training if item.workload == workload]
        selected = min(group, key=lambda item: (-item.checkpoint.best_fitness, item.repeat))
        baseline_weights = cast(dict[str, object], selected.config.similarity.method_weights.model_dump(mode="json"))
        if baseline_weights != {method: 0.125 for method in PUBLISHED_METHOD_ORDER}:
            fail(
                "scientific_semantics_incompatible",
                "report_inputs.json",
                "controlled weight analysis requires the frozen equal-weight baseline",
                "restore frozen similarity controls",
            )
        score = comparison_score(selected.comparison)
        components = cast(dict[str, object], score["methods"])
        rendered = parse_json_object(render_comparison_result(selected.comparison), name="controlled comparison")
        methods = cast(dict[str, object], rendered["methods"])
        rows.append(
            {
                "alternative_aggregate": math.fsum(
                    alternate_weights[method] * cast(float, components[method]) for method in PUBLISHED_METHOD_ORDER
                ),
                "alternative_weights": alternate_weights,
                "baseline_aggregate": score["aggregate"],
                "baseline_weights": baseline_weights,
                "components": components,
                "diagnostics": {
                    method: cast(dict[str, object], methods[method])["diagnostics"] for method in PUBLISHED_METHOD_ORDER
                },
                "executed_methods": list(PUBLISHED_METHOD_ORDER),
                "training_directory": f"training/{selected.workload}/r{selected.repeat}",
                "workload": workload,
            }
        )
    return rows


def _invalid_chromosome_diagnostics(training: Sequence[Training]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in training:
        invalid: list[object] = []
        for candidate in item.checkpoint.population:
            if candidate.status != "invalid":
                continue
            failure = candidate.invalid
            if failure is None:
                fail(
                    "artifact_corrupt",
                    "report_inputs.json",
                    "invalid candidate must retain a classified failure",
                    "restore invalid-chromosome diagnostics",
                )
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
            {
                "invalid_candidates": invalid,
                "trial_limits": item.config.generation.trial.model_dump(mode="json"),
                "training_directory": f"training/{item.workload}/r{item.repeat}",
                "workload": item.workload,
                "repeat": item.repeat,
            }
        )
    return rows


def rebuild_report_inputs(training: Sequence[Training], held: Mapping[str, HeldOutEvaluation]) -> dict[str, object]:
    fresh_rows: list[dict[str, object]] = []
    training_rows: list[dict[str, object]] = []
    variation_rows: list[dict[str, object]] = []
    held_rows: list[dict[str, object]] = []
    for workload in WORKLOADS:
        group = tuple(item for item in training if item.workload == workload)
        fresh_rows.append({"score": mean([comparison_score(item.comparison) for item in group]), "workload": workload})
        training_rows.append(
            {
                "runtime_seconds": _sample_summary(
                    [item.runtime_seconds for item in group], name="training runtime variance"
                ),
                "selection_fitness": _sample_summary(
                    [item.checkpoint.best_fitness for item in group], name="training selection variance"
                ),
                "winner_family_count_variance": variance(
                    [
                        sum(_winner_family(item) == family for item in group)
                        for family in ("markov_renewal", "mmpp", "poisson_empirical")
                    ]
                ),
                "winner_family_counts": {
                    family: sum(_winner_family(item) == family for item in group)
                    for family in ("markov_renewal", "mmpp", "poisson_empirical")
                },
                "workload": workload,
            }
        )
        pairs: list[dict[str, object]] = []
        for left, right in combinations(group, 2):
            if similarity_settings_identity(left.config.similarity) != similarity_settings_identity(
                right.config.similarity
            ):
                fail(
                    "scientific_semantics_incompatible",
                    "report_inputs.json",
                    "natural variation requires common similarity settings",
                    "restore common protocol controls before comparing natural variation",
                )
            left_reference, forward_window = normalize_reference(left.reference)
            right_reference, reverse_window = normalize_reference(right.reference)
            forward = comparison_score(
                compare_traces(
                    left_reference,
                    align_generated(right.reference, forward_window),
                    forward_window,
                    left.config.similarity,
                )
            )
            reverse = comparison_score(
                compare_traces(
                    right_reference,
                    align_generated(left.reference, reverse_window),
                    reverse_window,
                    right.config.similarity,
                )
            )
            pairs.append(
                {
                    "forward": forward,
                    "left_repeat": left.repeat,
                    "reverse": reverse,
                    "right_repeat": right.repeat,
                    "symmetric_mean": mean((forward, reverse)),
                }
            )
        variation_rows.append(
            {
                "pairs": pairs,
                "symmetric_mean": mean([cast(dict[str, object], pair["symmetric_mean"]) for pair in pairs]),
                "workload": workload,
            }
        )
        held_rows.append(
            {
                "observation_window_seconds": held[workload].observation_window_seconds,
                "score": comparison_score(held[workload].comparison),
                "workload": workload,
            }
        )
    return {
        "controlled_weight_analysis": _controlled_weight_analysis(training),
        "formula": "arithmetic_mean",
        "fresh_simulation": fresh_rows,
        "held_out": held_rows,
        "invalid_chromosome_diagnostics": _invalid_chromosome_diagnostics(training),
        "natural_variation": variation_rows,
        "runtime_winner_variance": training_rows,
        "training": training_rows,
    }
