"""Full pipeline stage ownership."""

from __future__ import annotations

from pathlib import Path

from trafficlab.artifacts.io import append_run_log
from trafficlab.common.errors import (
    EvidenceState,
    TrafficlabError,
    append_failure_outcome,
    attach_failure_outcome,
    failure_outcome_from_error,
)
from trafficlab.pipeline.types import RunDependencies, RunResult
from trafficlab.pipeline.validation import (
    FinalArtifactError,
    validate_capture_result,
    validate_comparison_result,
    validate_final_artifacts,
    validate_fit_result,
    validate_generation_result,
    validate_preflight_result,
)


def _append_run_failure(
    run_directory: Path,
    primary: TrafficlabError,
    *,
    failed_stage: str,
    completed_stages: tuple[str, ...],
) -> None:
    outcome_by_stage: dict[str, tuple[str, str, str, EvidenceState]] = {
        "capture": ("capture_malformed", "capture", "capture pair", "diagnostic_only"),
        "fit": ("artifact_corrupt", "fit", "fit inputs", "preserved"),
        "generate": ("generation_incomplete", "generate", "generated.pcapng", "not_published"),
        "compare": ("metric_infeasible", "compare", "similarity.json", "not_published"),
        "preflight": ("configuration_invalid", "preflight", "run evidence", "not_published"),
        "run": ("artifact_corrupt", "publication", "run evidence", "preserved"),
    }
    outcome = primary.failure_outcome
    if outcome is None:
        kind, canonical_stage, evidence, evidence_state = outcome_by_stage[failed_stage]
        outcome = failure_outcome_from_error(
            primary,
            kind=kind,
            stage=canonical_stage,
            affected_evidence=evidence,
            evidence_state=evidence_state,
        )
        primary.failure_outcomes = (outcome,)
        primary.failure_outcome = outcome
    secondary_outcomes = primary.failure_outcomes[1:]
    try:
        record: dict[str, object] = {
            "completed_stages": list(completed_stages),
            "corrective_action": primary.corrective_action,
            "detail": str(primary),
            "event": "run_failed",
            "failed_stage": failed_stage,
            "failure_outcome": outcome.as_dict(),
            "stage": "run",
        }
        if secondary_outcomes:
            record["secondary_outcomes"] = [item.as_dict() for item in secondary_outcomes]
        append_run_log(run_directory, record)
    except TrafficlabError as logging_error:
        append_failure_outcome(
            primary,
            failure_outcome_from_error(
                logging_error,
                kind="publication_failed",
                stage=outcome.stage,
                affected_evidence="run.log",
                evidence_state="not_published",
                authority="secondary",
            ),
        )
        primary.args = (f"{primary}; additionally could not append run failure to run.log: {logging_error}",)


def run_experiment(
    experiment_path: Path,
    *,
    dependencies: RunDependencies | None = None,
) -> RunResult:
    """Run and immediately validate preflight, capture, fit, generate, and compare."""
    active = dependencies or RunDependencies.production()
    prepared = active.preflight(experiment_path)
    validate_preflight_result(experiment_path, prepared)

    current_stage = "capture"
    completed_stages: tuple[str, ...] = ("preflight",)
    try:
        capture = active.capture(experiment_path, prepared)
        validate_capture_result(capture, prepared)
        completed_stages = (*completed_stages, "capture")

        current_stage = "fit"
        fit = active.fit(experiment_path)
        observation_window_seconds, capture_content, expected_input_identities = validate_fit_result(
            fit, experiment_path, prepared
        )
        completed_stages = (*completed_stages, "fit")

        current_stage = "generate"
        generation = active.generate(experiment_path)
        expected_input_identities["generated_pcapng"] = validate_generation_result(
            generation,
            prepared,
            observation_window_seconds,
            capture_content,
        )
        completed_stages = (*completed_stages, "generate")

        current_stage = "compare"
        comparison = active.compare(experiment_path)
        validate_comparison_result(comparison, observation_window_seconds, expected_input_identities)
        completed_stages = (*completed_stages, "compare")
        current_stage = "run"
        validate_final_artifacts(prepared, capture, fit, generation, comparison)

        try:
            append_run_log(
                prepared.run_directory,
                {
                    "aggregate_score": comparison.aggregate_score,
                    "event": "run_completed",
                    "family": fit.outcome.winner.family,
                    "fitness": fit.outcome.winner.fitness,
                    "generated_packet_count": len(generation.trace),
                    "reference_packet_count": capture.packet_count,
                    "run_directory": str(prepared.run_directory),
                    "stage": "run",
                },
            )
        except TrafficlabError as logging_error:
            raise attach_failure_outcome(
                TrafficlabError(
                    f"final run completion logging failed: {logging_error}",
                    corrective_action=logging_error.corrective_action,
                ),
                kind="publication_failed",
                stage="publication",
                affected_evidence="run.log",
                evidence_state="preserved",
            ) from logging_error
    except TrafficlabError as error:
        failed_stage = error.owner if isinstance(error, FinalArtifactError) else current_stage
        _append_run_failure(
            prepared.run_directory,
            error,
            failed_stage=failed_stage,
            completed_stages=completed_stages,
        )
        raise

    return RunResult(experiment_path, prepared.run_directory, capture, fit, generation, comparison)
