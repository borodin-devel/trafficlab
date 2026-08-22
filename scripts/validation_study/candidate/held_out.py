"""Held Out owner for Validation Study tooling."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scripts.validation_study.candidate.artifacts import (
    candidate_capture_lineage,
    stage_candidate_transfer_evidence,
    write_candidate_config_pair,
)
from scripts.validation_study.common import (
    JsonObject,
    candidate_identity,
    canonical_json,
    path_entry_exists,
    require,
    write_candidate_bytes,
)
from scripts.validation_study.records import HeldOutEvaluation
from scripts.validation_study.transfer import archive_transfer_evidence, prepare_transfer_scratch
from scripts.validation_study.workloads import config_with_run_directory, render_realized_config
from trafficlab.artifacts.io import append_run_log
from trafficlab.capture.lineage import CaptureResult
from trafficlab.common.compatibility import identify_bytes, require_compatible
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import encode_pcapng, read_pcapng_bytes
from trafficlab.common.trace import (
    align_generated,
    normalize_reference,
    parse_capture_metadata,
)
from trafficlab.comparison.codec import (
    render_comparison_result,
    similarity_settings_identity,
)
from trafficlab.comparison.metrics import compare_traces
from trafficlab.generation.models.fitted_model import load_best_model, runtime_fitted_model
from trafficlab.generation.models.registry import get_family

if TYPE_CHECKING:
    from scripts.validation_study.candidate.artifacts import CandidateTraining
    from scripts.validation_study.common import HeldOutCaptureRunner
    from scripts.validation_study.workloads import WorkloadSpec


def evaluate_study_held_out(
    *,
    model_content: bytes,
    model_source: Path,
    config: ExperimentConfig,
    capture_content: bytes,
    capture_source: Path,
    reference_content: bytes,
    reference_source: Path,
) -> HeldOutEvaluation:
    """Evaluate a retained training model against one independent held-out capture.

    This is intentionally a study-evidence boundary rather than a relaxation of
    the ordinary generate/compare lineage checks: the retained model keeps its
    training identities while this result records a separate held-out pair.
    """
    if type(config) is not ExperimentConfig:
        raise TypeError("config must be an ExperimentConfig")
    model = load_best_model(model_content, source=model_source)
    capture_identity = identify_bytes(capture_content)
    reference_identity = identify_bytes(reference_content)
    if reference_identity == model.reference_identity:
        raise TrafficlabError(
            "study held-out reference must be an independent held-out reference",
            corrective_action="capture a new held-out reference after freezing the training model",
        )
    require_compatible(
        {"final seed": model.final_seed, "final generation limits": model.final_limits},
        {"final seed": config.run.final_seed, "final generation limits": config.generation.final},
    )
    metadata = parse_capture_metadata(capture_content, source=capture_source)
    reference, W = normalize_reference(read_pcapng_bytes(reference_content, metadata, source=reference_source))
    raw_generated = (
        get_family(model.family)
        .generate(runtime_fitted_model(model), model.final_seed, W, model.final_limits)
        .require_complete()
    )
    encoded = encode_pcapng(raw_generated, metadata, observation_window_seconds=W)
    generated = encoded.trace
    generated_pcapng = encoded.content
    aligned = align_generated(generated, W)
    settings_identity = similarity_settings_identity(config.similarity)
    comparison = compare_traces(reference, aligned, W, config.similarity).with_input_identities(
        {
            "capture_json": capture_identity,
            "generated_pcapng": identify_bytes(generated_pcapng),
            "reference_pcapng": reference_identity,
            "similarity_settings": settings_identity,
        }
    )
    comparison_json = render_comparison_result(comparison)
    return HeldOutEvaluation(
        training_model=model,
        training_model_identity=identify_bytes(model_content),
        capture_identity=capture_identity,
        reference_identity=reference_identity,
        generated_identity=identify_bytes(generated_pcapng),
        similarity_settings_identity=settings_identity,
        generated_pcapng=generated_pcapng,
        comparison=comparison,
        comparison_json=comparison_json,
        seed=model.final_seed,
        observation_window_seconds=W,
    )


class CollectionCallbackValueError(Exception):
    """Carry an unexpected callback ValueError beyond collection normalization."""

    def __init__(self, error: ValueError) -> None:
        super().__init__(str(error))
        self.error = error


def collect_held_out(
    repository_root: Path,
    candidate: Path,
    attempt: Path,
    *,
    study_id: str,
    workload: WorkloadSpec,
    training: CandidateTraining,
    environment: Mapping[str, object],
    capture: HeldOutCaptureRunner,
    object_size_bytes: int,
) -> tuple[JsonObject, HeldOutEvaluation, CaptureResult]:
    directory = candidate / "held_out" / workload.name
    require(not path_entry_exists(directory), f"held-out directory already exists: {directory}")
    config = config_with_run_directory(training.config, directory)
    source = attempt / f"held-out-{workload.name}.toml"
    render_realized_config(config, source)
    prepared = prepare_transfer_scratch(repository_root, study_id, f"held-out-{workload.name}", workload)
    try:
        result = capture(source)
    except ValueError as error:
        raise CollectionCallbackValueError(error) from error
    require(
        result.run_directory == directory and (not result.reused),
        "held-out capture must publish one fresh non-reused capture pair",
    )
    responses = archive_transfer_evidence(
        repository_root, study_id, f"held-out-{workload.name}", workload, prepared, object_size_bytes=object_size_bytes
    )
    stage_candidate_transfer_evidence(
        repository_root, candidate, responses, scope="held_out", run_id=f"held-out-{workload.name}", workload=workload
    )
    experiment = directory / "experiment.toml"
    require(
        experiment.is_file() and (not experiment.is_symlink()), "held-out capture must retain its stage configuration"
    )
    experiment.unlink()
    capture_content = (directory / "capture.json").read_bytes()
    reference_content = (directory / "reference.pcapng").read_bytes()
    evaluation = evaluate_study_held_out(
        model_content=training.contents["best_model.json"],
        model_source=training.directory / "best_model.json",
        config=config,
        capture_content=capture_content,
        capture_source=directory / "capture.json",
        reference_content=reference_content,
        reference_source=directory / "reference.pcapng",
    )
    write_candidate_bytes(directory / "generated.pcapng", evaluation.generated_pcapng)
    write_candidate_bytes(directory / "similarity.json", evaluation.comparison_json)
    write_candidate_config_pair(config, directory / "portable.toml", directory / "realized.toml")
    append_run_log(directory, {"event": "held_out_evaluated", "stage": "compare", "workload": workload.name})
    record = cast(
        JsonObject,
        {
            "capture_identity": cast(JsonObject, evaluation.capture_identity.as_dict()),
            "capture_lineage": candidate_capture_lineage(capture_content, environment),
            "comparison_identity": candidate_identity(evaluation.comparison_json),
            "generated_identity": cast(JsonObject, evaluation.generated_identity.as_dict()),
            "observation_window_seconds": evaluation.observation_window_seconds,
            "reference_identity": cast(JsonObject, evaluation.reference_identity.as_dict()),
            "seed": evaluation.seed,
            "training_directory": f"training/{training.workload}/r{training.repeat}",
            "training_model_identity": cast(JsonObject, evaluation.training_model_identity.as_dict()),
            "workload": workload.name,
        },
    )
    write_candidate_bytes(directory / "record.json", canonical_json(record))
    return (
        cast(
            JsonObject,
            {
                "capture_lineage": candidate_capture_lineage(capture_content, environment),
                "directory": f"held_out/{workload.name}",
                "training_directory": f"training/{training.workload}/r{training.repeat}",
                "workload": workload.name,
            },
        ),
        evaluation,
        result,
    )
