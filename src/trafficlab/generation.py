"""Authoritative final-model generation and generated-capture publication stage."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from trafficlab.artifacts import append_run_log, publish_generated_pcapng, quantize_generated_events
from trafficlab.compatibility import identify_bytes, identify_file, require_compatible
from trafficlab.config_io import render_effective_config
from trafficlab.errors import (
    TrafficlabError,
    append_failure_outcome,
    attach_failure_outcome,
    failure_outcome_from_error,
)
from trafficlab.models.registry import BestModel, get_family, load_best_model
from trafficlab.pcapng import encode_pcapng, parse_pcapng_bytes
from trafficlab.preflight import open_or_prepare_experiment
from trafficlab.scientific_schema import ScientificArtifactSchemaError
from trafficlab.trace import CaptureMetadata, TraceEvent, parse_capture_metadata


@dataclass(frozen=True, slots=True)
class GenerationStageResult:
    """Validated final trace and the authoritative artifact that contains it."""

    run_directory: Path
    generated_path: Path
    events: tuple[TraceEvent, ...]
    seed: int
    observation_window_seconds: float
    reused: bool


def reproduce_generated_pcapng(
    best: BestModel,
    metadata: CaptureMetadata,
    *,
    clock: Callable[[], float] = monotonic,
) -> tuple[tuple[TraceEvent, ...], tuple[TraceEvent, ...], bytes]:
    """Reproduce the exact final trace and PCAPNG bytes bound into a best model."""
    family = get_family(best.family)
    events = family.generate(
        best.fitted,
        best.final_seed,
        best.observation_window_seconds,
        best.final_limits,
        clock=clock,
    ).require_complete()
    rendered_events = quantize_generated_events(events, best.observation_window_seconds)
    return events, rendered_events, encode_pcapng(rendered_events, metadata)


def _read_required_bytes(path: Path, *, kind: str, corrective_action: str) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as error:
        raise attach_failure_outcome(
            TrafficlabError(
                f"could not read {kind} {path}: {error}",
                corrective_action=corrective_action,
            ),
            kind="artifact_missing",
            stage="generate",
            affected_evidence=path.name,
            evidence_state="not_published",
        ) from error
    except OSError as error:
        raise attach_failure_outcome(
            TrafficlabError(
                f"could not read {kind} {path}: {error}",
                corrective_action=corrective_action,
            ),
            kind="artifact_corrupt",
            stage="generate",
            affected_evidence=path.name,
            evidence_state="preserved",
        ) from error


def _append_failure(run_directory: Path, primary: TrafficlabError) -> None:
    outcome = primary.failure_outcome
    if outcome is None:
        outcome = failure_outcome_from_error(
            primary,
            kind="artifact_corrupt",
            stage="generate",
            affected_evidence="generation inputs",
            evidence_state="preserved",
        )
        primary.failure_outcomes = (outcome,)
        primary.failure_outcome = outcome
    try:
        record: dict[str, object] = {
            "corrective_action": primary.corrective_action,
            "detail": str(primary),
            "event": "stage_failed",
            "failure_outcome": outcome.as_dict(),
            "stage": "generate",
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
                stage="generate",
                affected_evidence="run.log",
                evidence_state="not_published",
                authority="secondary",
            ),
        )
        primary.args = (f"{primary}; additionally could not append generation failure to run.log: {logging_error}",)


def generate_experiment(
    path: Path,
    *,
    clock: Callable[[], float] = monotonic,
) -> GenerationStageResult:
    """Generate one final trace from an authoritative prepared run's stored fitted model."""
    prepared = open_or_prepare_experiment(path)
    run_directory = prepared.run_directory
    config = prepared.config
    snapshot_path = run_directory / "experiment.toml"
    model_path = run_directory / "best_model.json"
    capture_path = run_directory / "capture.json"
    try:
        try:
            model_content = _read_required_bytes(
                model_path,
                kind="best model",
                corrective_action="verify best_model.json exists and is readable",
            )
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="artifact_missing",
                stage="generate",
                affected_evidence="best_model.json",
                evidence_state="not_published",
            ) from error
        try:
            best = load_best_model(model_content, source=model_path)
        except ScientificArtifactSchemaError as error:
            raise attach_failure_outcome(
                error,
                kind="scientific_semantics_incompatible",
                stage="generate",
                affected_evidence="best_model.json",
                evidence_state="preserved",
            ) from error
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="artifact_corrupt",
                stage="generate",
                affected_evidence="best_model.json",
                evidence_state="preserved",
            ) from error
        if best.family not in config.models.enabled:
            raise attach_failure_outcome(
                TrafficlabError(
                    f"stored model family {best.family!r} is not enabled in the authoritative configuration",
                    corrective_action="use a best model fitted under the authoritative enabled model families",
                ),
                kind="scientific_semantics_incompatible",
                stage="generate",
                affected_evidence="best_model.json",
                evidence_state="preserved",
            )
        try:
            require_compatible(
                {
                    "final seed": best.final_seed,
                    "final generation limits": best.final_limits,
                },
                {
                    "final seed": config.run.final_seed,
                    "final generation limits": config.generation.final,
                },
            )
        except TrafficlabError as error:
            raise attach_failure_outcome(
                TrafficlabError(
                    f"stored best-model generation policy does not match the authoritative configuration: {error}",
                    corrective_action="use the exact final seed and limits retained by the fitted model",
                ),
                kind="scientific_semantics_incompatible",
                stage="generate",
                affected_evidence="best_model.json",
                evidence_state="preserved",
            ) from error
        family = get_family(best.family)
        configured_bounds = getattr(config.models, best.family)
        if configured_bounds is None:
            raise attach_failure_outcome(
                TrafficlabError(
                    f"stored model family {best.family!r} has no authoritative bounds",
                    corrective_action="restore the enabled family's authoritative model bounds",
                ),
                kind="scientific_semantics_incompatible",
                stage="generate",
                affected_evidence="best_model.json",
                evidence_state="preserved",
            )
        expected_bounds = {name: getattr(configured_bounds, name) for name in family.gene_names}
        if best.gene_bounds != expected_bounds:
            raise attach_failure_outcome(
                TrafficlabError(
                    f"stored {best.family} model bounds do not match the authoritative configuration",
                    corrective_action="use a best model fitted with the exact authoritative family bounds",
                ),
                kind="scientific_semantics_incompatible",
                stage="generate",
                affected_evidence="best_model.json",
                evidence_state="preserved",
            )

        try:
            capture_content = _read_required_bytes(
                capture_path,
                kind="capture metadata",
                corrective_action="verify capture.json exists and is readable",
            )
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="artifact_missing",
                stage="generate",
                affected_evidence="capture.json",
                evidence_state="not_published",
            ) from error
        try:
            metadata = parse_capture_metadata(capture_content, source=capture_path)
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="artifact_corrupt",
                stage="generate",
                affected_evidence="capture.json",
                evidence_state="preserved",
            ) from error
        capture_identity = identify_bytes(capture_content)
        if capture_identity != best.capture_identity:
            raise attach_failure_outcome(
                TrafficlabError(
                    "capture.json identity does not match the stored best model",
                    corrective_action="restore the exact capture.json used to fit best_model.json",
                ),
                kind="artifact_foreign",
                stage="generate",
                affected_evidence="capture.json",
                evidence_state="preserved",
            )

        try:
            events, rendered_events, content = reproduce_generated_pcapng(best, metadata, clock=clock)
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="generation_incomplete",
                stage="generate",
                affected_evidence="generated.pcapng",
                evidence_state="not_published",
            ) from error
        for evidence, source_path, expected_identity in (
            ("experiment.toml", snapshot_path, identify_bytes(render_effective_config(config))),
            ("best_model.json", model_path, identify_bytes(model_content)),
            ("capture.json", capture_path, capture_identity),
        ):
            try:
                require_compatible({evidence: expected_identity}, {evidence: identify_file(source_path)})
            except TrafficlabError as error:
                raise attach_failure_outcome(
                    TrafficlabError(
                        f"{evidence} changed during generate",
                        corrective_action="restore the exact generation inputs and rerun generate",
                    ),
                    kind="artifact_changed",
                    stage="generate",
                    affected_evidence=evidence,
                    evidence_state="preserved",
                ) from error
        try:
            publication = publish_generated_pcapng(
                run_directory,
                content,
                metadata=metadata,
                expected_events=events,
                observation_window_seconds=best.observation_window_seconds,
            )
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="publication_failed",
                stage="generate",
                affected_evidence="generated.pcapng",
                evidence_state="not_published",
            ) from error
        try:
            parsed_events = parse_pcapng_bytes(publication.content, metadata, source=publication.path)
        except TrafficlabError as error:
            raise attach_failure_outcome(
                error,
                kind="artifact_corrupt",
                stage="generate",
                affected_evidence="generated.pcapng",
                evidence_state="preserved",
            ) from error
        if any(event.timestamp < 0.0 or event.timestamp > best.observation_window_seconds for event in parsed_events):
            raise attach_failure_outcome(
                TrafficlabError(
                    "generated PCAPNG contains a timestamp outside the stored observation window",
                    corrective_action="report the generated PCAPNG window-validation defect",
                ),
                kind="artifact_corrupt",
                stage="generate",
                affected_evidence="generated.pcapng",
                evidence_state="preserved",
            )
        if parsed_events != rendered_events:
            raise attach_failure_outcome(
                TrafficlabError(
                    "generated PCAPNG did not round-trip to the complete generated events",
                    corrective_action="report the generated PCAPNG round-trip defect",
                ),
                kind="artifact_corrupt",
                stage="generate",
                affected_evidence="generated.pcapng",
                evidence_state="preserved",
            )
        result = GenerationStageResult(
            run_directory=run_directory,
            generated_path=publication.path,
            events=parsed_events,
            seed=best.final_seed,
            observation_window_seconds=best.observation_window_seconds,
            reused=not publication.created_by_call,
        )
    except TrafficlabError as error:
        _append_failure(run_directory, error)
        raise

    try:
        append_run_log(
            run_directory,
            {
                "event": "generated_pcapng_reused" if result.reused else "generated_pcapng_published",
                "observation_window_seconds": result.observation_window_seconds,
                "packet_count": len(result.events),
                "path": str(result.generated_path),
                "seed": result.seed,
                "stage": "generate",
            },
        )
    except TrafficlabError as logging_error:
        error = TrafficlabError(
            f"generated capture was published or reused at {result.generated_path}, "
            f"but success logging failed: {logging_error}",
            corrective_action=logging_error.corrective_action,
        )
        raise attach_failure_outcome(
            error,
            kind="publication_failed",
            stage="generate",
            affected_evidence="generated.pcapng",
            evidence_state="preserved",
        ) from logging_error
    return result
