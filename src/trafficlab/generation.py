"""Authoritative final-model generation and generated-capture publication stage."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from trafficlab.artifacts import append_run_log, publish_generated_pcapng, quantize_generated_events
from trafficlab.errors import TrafficlabError
from trafficlab.models.registry import get_family, load_best_model
from trafficlab.pcapng import encode_pcapng, parse_pcapng_bytes
from trafficlab.preflight import open_or_prepare_experiment
from trafficlab.trace import TraceEvent, parse_capture_metadata


@dataclass(frozen=True, slots=True)
class GenerationStageResult:
    """Validated final trace and the authoritative artifact that contains it."""

    run_directory: Path
    generated_path: Path
    events: tuple[TraceEvent, ...]
    seed: int
    observation_window_seconds: float
    reused: bool


def _read_required_bytes(path: Path, *, kind: str, corrective_action: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise TrafficlabError(
            f"could not read {kind} {path}: {error}",
            corrective_action=corrective_action,
        ) from error


def _append_failure(run_directory: Path, primary: TrafficlabError) -> None:
    try:
        append_run_log(
            run_directory,
            {
                "corrective_action": primary.corrective_action,
                "detail": str(primary),
                "event": "stage_failed",
                "stage": "generate",
            },
        )
    except TrafficlabError as logging_error:
        raise TrafficlabError(
            f"{primary}; additionally could not append generation failure to run.log: {logging_error}",
            corrective_action=primary.corrective_action,
            exit_code=primary.exit_code,
        ) from primary


def generate_experiment(
    path: Path,
    *,
    clock: Callable[[], float] = monotonic,
) -> GenerationStageResult:
    """Generate one final trace from an authoritative prepared run's stored fitted model."""
    prepared = open_or_prepare_experiment(path)
    run_directory = prepared.run_directory
    config = prepared.config
    model_path = run_directory / "best_model.json"
    capture_path = run_directory / "capture.json"
    try:
        model_content = _read_required_bytes(
            model_path,
            kind="best model",
            corrective_action="verify best_model.json exists and is readable",
        )
        best = load_best_model(model_content, source=model_path)
        if best.family not in config.models.enabled:
            raise TrafficlabError(
                f"stored model family {best.family!r} is not enabled in the authoritative configuration",
                corrective_action="use a best model fitted under the authoritative enabled model families",
            )
        family = get_family(best.family)
        configured_bounds = getattr(config.models, best.family)
        if configured_bounds is None:
            raise TrafficlabError(
                f"stored model family {best.family!r} has no authoritative bounds",
                corrective_action="restore the enabled family's authoritative model bounds",
            )
        expected_bounds = {name: getattr(configured_bounds, name) for name in family.gene_names}
        if best.gene_bounds != expected_bounds:
            raise TrafficlabError(
                f"stored {best.family} model bounds do not match the authoritative configuration",
                corrective_action="use a best model fitted with the exact authoritative family bounds",
            )

        capture_content = _read_required_bytes(
            capture_path,
            kind="capture metadata",
            corrective_action="verify capture.json exists and is readable",
        )
        metadata = parse_capture_metadata(capture_content, source=capture_path)
        capture_sha256 = hashlib.sha256(capture_content).hexdigest()
        if capture_sha256 != best.capture_sha256:
            raise TrafficlabError(
                "capture.json SHA-256 does not match the stored best model",
                corrective_action="restore the exact capture.json used to fit best_model.json",
            )

        events = family.generate(
            best.fitted,
            config.run.final_seed,
            best.observation_window_seconds,
            config.generation.final,
            clock=clock,
        ).require_complete()
        rendered_events = quantize_generated_events(events, best.observation_window_seconds)
        content = encode_pcapng(rendered_events, metadata)
        publication = publish_generated_pcapng(
            run_directory,
            content,
            metadata=metadata,
            expected_events=events,
            observation_window_seconds=best.observation_window_seconds,
        )
        parsed_events = parse_pcapng_bytes(publication.content, metadata, source=publication.path)
        if any(event.timestamp < 0.0 or event.timestamp > best.observation_window_seconds for event in parsed_events):
            raise TrafficlabError(
                "generated PCAPNG contains a timestamp outside the stored observation window",
                corrective_action="report the generated PCAPNG window-validation defect",
            )
        if parsed_events != rendered_events:
            raise TrafficlabError(
                "generated PCAPNG did not round-trip to the complete generated events",
                corrective_action="report the generated PCAPNG round-trip defect",
            )
        result = GenerationStageResult(
            run_directory=run_directory,
            generated_path=publication.path,
            events=parsed_events,
            seed=config.run.final_seed,
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
        raise TrafficlabError(
            f"generated capture was published or reused at {result.generated_path}, "
            f"but success logging failed: {logging_error}",
            corrective_action=logging_error.corrective_action,
        ) from logging_error
    return result
