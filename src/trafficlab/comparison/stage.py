"""Traffic comparison stage ownership."""

from pathlib import Path

from trafficlab.artifacts.io import append_run_log
from trafficlab.common.compatibility import identify_bytes, identify_file, require_compatible
from trafficlab.common.config_io import load_experiment, render_effective_config
from trafficlab.common.errors import (
    FailureOutcome,
    TrafficlabError,
    append_failure_outcome,
    attach_failure_outcome,
    failure_outcome_from_error,
)
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.scientific_schema import ScientificArtifactSchemaError
from trafficlab.common.trace import (
    align_generated,
    normalize_reference,
    parse_capture_metadata,
)
from trafficlab.comparison.codec import (
    read_comparison_input,
    similarity_settings_identity,
)
from trafficlab.comparison.metrics import compare_final_traces
from trafficlab.comparison.publication import PublicationError, publish_comparison_result
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.generation.models.fitted_model import load_best_model
from trafficlab.generation.stage import reproduce_generated_pcapng


def _append_failure(run_directory: Path, primary: TrafficlabError, *, failure_kind: str) -> None:
    outcome = primary.failure_outcome
    if outcome is None:
        outcome_kind = "publication_failed" if failure_kind == "publication" else "metric_infeasible"
        outcome = failure_outcome_from_error(
            primary,
            kind=outcome_kind,
            stage="compare",
            affected_evidence="similarity.json",
            evidence_state="not_published",
        )
        primary.failure_outcomes = (outcome,)
        primary.failure_outcome = outcome
    try:
        record: dict[str, object] = {
            "detail": str(primary),
            "event": "comparison_failed",
            "failure_kind": failure_kind,
            "failure_outcome": outcome.as_dict(),
            "stage": "compare",
        }
        if primary.failure_outcomes[1:]:
            record["secondary_outcomes"] = [item.as_dict() for item in primary.failure_outcomes[1:]]
        append_run_log(run_directory, record)
    except TrafficlabError as logging_error:
        append_failure_outcome(
            primary,
            failure_outcome_from_error(
                logging_error,
                kind="publication_failed",
                stage="compare",
                affected_evidence="run.log",
                evidence_state="not_published",
                authority="secondary",
            ),
        )
        primary.args = (f"{primary}; additionally could not append comparison failure to run.log: {logging_error}",)


def compare_experiment(experiment_path: Path) -> ComparisonResult:
    """Compare one existing run using its matching authoritative configuration snapshot."""
    caller_config = load_experiment(experiment_path)
    run_directory = caller_config.run.directory
    output_path = run_directory / "similarity.json"
    try:
        snapshot_config = load_experiment(run_directory / "experiment.toml")
        if caller_config != snapshot_config:
            raise attach_failure_outcome(
                TrafficlabError(
                    f"caller configuration {experiment_path} does not match the authoritative run snapshot",
                    corrective_action="use the exact experiment configuration that created this run",
                ),
                kind="artifact_foreign",
                stage="compare",
                affected_evidence="experiment.toml",
                evidence_state="preserved",
            )
        metadata_path = run_directory / "capture.json"
        reference_path = run_directory / "reference.pcapng"
        generated_path = run_directory / "generated.pcapng"
        metadata_content = read_comparison_input(
            metadata_path,
            kind="capture metadata",
            corrective_action="verify capture.json exists and is readable",
        )
        try:
            metadata = parse_capture_metadata(metadata_content, source=metadata_path)
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="artifact_corrupt",
                stage="compare",
                affected_evidence="capture.json",
                evidence_state="preserved",
            ) from error
        reference_content = read_comparison_input(
            reference_path,
            kind="PCAPNG",
            corrective_action="verify the PCAPNG exists and is readable",
        )
        try:
            reference_trace = read_pcapng_bytes(reference_content, metadata, source=reference_path)
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="artifact_corrupt",
                stage="compare",
                affected_evidence="reference.pcapng",
                evidence_state="preserved",
            ) from error
        model_path = run_directory / "best_model.json"
        model_content = read_comparison_input(
            model_path,
            kind="best model",
            corrective_action="verify best_model.json is readable",
        )
        model_identity = identify_bytes(model_content)
        try:
            best = load_best_model(model_content, source=model_path)
        except ScientificArtifactSchemaError as error:
            raise attach_failure_outcome(
                error,
                kind="scientific_semantics_incompatible",
                stage="compare",
                affected_evidence="best_model.json",
                evidence_state="preserved",
            ) from error
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="artifact_corrupt",
                stage="compare",
                affected_evidence="best_model.json",
                evidence_state="preserved",
            ) from error
        try:
            require_compatible(
                {
                    "reference identity": best.reference_identity,
                    "capture identity": best.capture_identity,
                    "final seed": best.final_seed,
                    "final generation limits": best.final_limits,
                },
                {
                    "reference identity": identify_bytes(reference_content),
                    "capture identity": identify_bytes(metadata_content),
                    "final seed": snapshot_config.run.final_seed,
                    "final generation limits": snapshot_config.generation.final,
                },
            )
        except TrafficlabError as error:
            raise attach_failure_outcome(
                TrafficlabError(
                    f"best_model.json is incompatible with current comparison inputs: {error}",
                    corrective_action="restore the exact fitted model and matching reference, capture, final seed, and limits",
                ),
                kind="artifact_foreign",
                stage="compare",
                affected_evidence="best_model.json",
                evidence_state="preserved",
            ) from error
        try:
            _, expected_generated = reproduce_generated_pcapng(best, metadata, clock=lambda: 0.0)
            expected_generated_content = expected_generated.content
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="generation_incomplete",
                stage="compare",
                affected_evidence="generated.pcapng",
                evidence_state="not_published",
            ) from error
        generated_content = read_comparison_input(
            generated_path,
            kind="PCAPNG",
            corrective_action="verify the PCAPNG exists and is readable",
        )
        if generated_content != expected_generated_content:
            raise TrafficlabError(
                "generated.pcapng is foreign",
                corrective_action="regenerate from the current fitted model",
                failure_outcome=FailureOutcome(
                    kind="artifact_foreign",
                    stage="compare",
                    detail="generated.pcapng is foreign",
                    affected_evidence="generated.pcapng",
                    evidence_state="preserved",
                    corrective_action="regenerate from the current fitted model",
                    authority="primary",
                ),
            )
        try:
            generated_trace = read_pcapng_bytes(generated_content, metadata, source=generated_path)
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="artifact_corrupt",
                stage="compare",
                affected_evidence="generated.pcapng",
                evidence_state="preserved",
            ) from error
        try:
            reference, window = normalize_reference(reference_trace)
            generated = align_generated(generated_trace, window)
            result = compare_final_traces(
                reference,
                generated,
                window,
                snapshot_config.similarity,
                {
                    "capture_json": identify_bytes(metadata_content),
                    "generated_pcapng": identify_bytes(generated_content),
                    "reference_pcapng": identify_bytes(reference_content),
                    "similarity_settings": similarity_settings_identity(snapshot_config.similarity),
                },
            )
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="metric_infeasible",
                stage="compare",
                affected_evidence="similarity.json",
                evidence_state="not_published",
            ) from error
        authoritative_inputs = [
            (
                "experiment.toml",
                run_directory / "experiment.toml",
                identify_bytes(render_effective_config(snapshot_config)),
            ),
            ("capture.json", metadata_path, identify_bytes(metadata_content)),
            ("reference.pcapng", reference_path, identify_bytes(reference_content)),
            ("generated.pcapng", generated_path, identify_bytes(generated_content)),
        ]
        authoritative_inputs.append(("best_model.json", model_path, model_identity))
        for evidence, source_path, expected_identity in authoritative_inputs:
            try:
                require_compatible({evidence: expected_identity}, {evidence: identify_file(source_path)})
            except TrafficlabError as error:
                raise attach_failure_outcome(
                    TrafficlabError(
                        f"{evidence} changed during compare",
                        corrective_action="restore the exact comparison inputs and rerun compare",
                    ),
                    kind="artifact_changed",
                    stage="compare",
                    affected_evidence=evidence,
                    evidence_state="preserved",
                ) from error
        created_by_call = publish_comparison_result(output_path, result)
    except TrafficlabError as error:
        failure_kind = "publication" if isinstance(error, PublicationError) else "evaluation_or_input"
        _append_failure(run_directory, error, failure_kind=failure_kind)
        raise

    try:
        append_run_log(
            run_directory,
            {
                "aggregate_score": result.aggregate_score,
                "event": "comparison_succeeded",
                "observation_window_seconds": result.observation_window_seconds,
                "path": str(output_path),
                "reused": not created_by_call,
                "stage": "compare",
            },
        )
    except TrafficlabError as logging_error:
        error = TrafficlabError(
            f"comparison result was published at {output_path}, but success logging failed: {logging_error}",
            corrective_action=logging_error.corrective_action,
        )
        raise attach_failure_outcome(
            error,
            kind="publication_failed",
            stage="compare",
            affected_evidence="similarity.json",
            evidence_state="preserved",
        ) from logging_error
    return result
