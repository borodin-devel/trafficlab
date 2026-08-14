"""Prepared-input genetic fitting stage and final best-model publication."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from trafficlab.artifacts import append_run_log, publish_best_model
from trafficlab.comparison import sha256_bytes
from trafficlab.config_io import render_effective_config
from trafficlab.errors import TrafficlabError
from trafficlab.genetic.strategy import FitOutcome, StrategyContext, make_strategy_context, run_strategy
from trafficlab.models.registry import BestModel, get_family, make_best_model, render_best_model
from trafficlab.pcapng import parse_pcapng_bytes
from trafficlab.preflight import PreparedExperiment, open_or_prepare_experiment
from trafficlab.trace import normalize_reference, parse_capture_metadata


@dataclass(frozen=True, slots=True)
class FitStageResult:
    """Final fitting evidence and the authoritative self-contained winner artifact."""

    experiment_path: Path
    run_directory: Path
    best_model_path: Path
    best_model: BestModel
    outcome: FitOutcome
    observation_window_seconds: float
    reused_best_model: bool


@dataclass(frozen=True, slots=True)
class FitDependencies:
    """Injected prepared-input and strategy boundaries for deterministic stage tests."""

    open_or_prepare: Callable[[Path], PreparedExperiment]
    read_bytes: Callable[[Path], bytes]
    strategy: Callable[[StrategyContext], FitOutcome]

    @classmethod
    def production(cls) -> Self:
        """Return the real offline fitting dependencies."""
        return cls(open_or_prepare_experiment, read_fit_input, run_strategy)


def read_fit_input(path: Path) -> bytes:
    """Read one prepared fit input exactly once through the package error boundary."""
    try:
        return path.read_bytes()
    except OSError as error:
        raise TrafficlabError(
            f"could not read fit input {path}: {error}",
            corrective_action="verify the prepared fit inputs exist and are readable",
        ) from error


def _append_failure(run_directory: Path, primary: TrafficlabError) -> None:
    try:
        append_run_log(
            run_directory,
            {
                "corrective_action": primary.corrective_action,
                "detail": str(primary),
                "event": "stage_failed",
                "stage": "fit",
            },
        )
    except TrafficlabError as logging_error:
        raise TrafficlabError(
            f"{primary}; additionally could not append fitting failure to run.log: {logging_error}",
            corrective_action=primary.corrective_action,
            exit_code=primary.exit_code,
        ) from primary


def fit_experiment(
    experiment_path: Path,
    *,
    dependencies: FitDependencies | None = None,
) -> FitStageResult:
    """Fit or resume one prepared experiment and publish only its freshly validated winner."""
    active = dependencies or FitDependencies.production()
    prepared = active.open_or_prepare(experiment_path)
    run_directory = prepared.run_directory
    append_run_log(
        run_directory,
        {
            "event": "fit_started",
            "experiment_path": str(experiment_path),
            "stage": "fit",
        },
    )
    try:
        snapshot_path = run_directory / "experiment.toml"
        capture_path = run_directory / "capture.json"
        reference_path = run_directory / "reference.pcapng"
        snapshot_bytes = active.read_bytes(snapshot_path)
        if snapshot_bytes != render_effective_config(prepared.config):
            raise TrafficlabError(
                f"authoritative experiment snapshot {snapshot_path} does not match the prepared effective configuration",
                corrective_action="restore experiment.toml to the exact prepared effective configuration",
            )
        capture_bytes = active.read_bytes(capture_path)
        reference_bytes = active.read_bytes(reference_path)

        metadata = parse_capture_metadata(capture_bytes, source=capture_path)
        try:
            reference_events = parse_pcapng_bytes(reference_bytes, metadata, source=reference_path)
        except TrafficlabError as error:
            raise TrafficlabError(
                f"invalid reference PCAPNG {reference_path}: {error}",
                corrective_action=error.corrective_action,
                exit_code=error.exit_code,
            ) from error
        reference, window = normalize_reference(reference_events)
        context = make_strategy_context(
            prepared.config,
            reference,
            window,
            run_directory,
            experiment_sha256=sha256_bytes(snapshot_bytes),
            reference_sha256=sha256_bytes(reference_bytes),
            capture_sha256=sha256_bytes(capture_bytes),
        )
        outcome = active.strategy(context)
        append_run_log(
            run_directory,
            {
                "event": "checkpoint_ready",
                "generation": outcome.generation,
                "path": str(run_directory / "checkpoint.json"),
                "stage": "fit",
                "terminal_reason": outcome.terminal_reason,
            },
        )
        append_run_log(
            run_directory,
            {
                "event": "final_validation_succeeded",
                "family": outcome.winner.family,
                "fitness": outcome.winner.fitness,
                "seed": prepared.config.run.final_seed,
                "stage": "fit",
                "trial_count": len(outcome.final_trials),
            },
        )

        winner_family = get_family(outcome.winner.family)
        winner_bounds = getattr(prepared.config.models, outcome.winner.family)
        if winner_bounds is None or outcome.winner.genes is None:
            raise AssertionError("validated winner must have configured bounds and canonical genes")
        best = make_best_model(
            family=winner_family,
            reference=reference,
            genes=outcome.winner.genes,
            reference_sha256=context.compatibility.reference_sha256,
            capture_sha256=context.compatibility.capture_sha256,
            W=window,
            bounds=winner_bounds,
        )
        if best.genes != outcome.winner.genes:
            raise AssertionError("artifact construction must retain the same canonical winner genes")
        publication = publish_best_model(run_directory / "best_model.json", render_best_model(best))
        result = FitStageResult(
            experiment_path,
            run_directory,
            publication.path,
            best,
            outcome,
            window,
            not publication.created_by_call,
        )
    except TrafficlabError as error:
        _append_failure(run_directory, error)
        raise

    event = "best_model_reused" if result.reused_best_model else "best_model_published"
    try:
        append_run_log(
            run_directory,
            {
                "event": event,
                "family": result.best_model.family,
                "fitness": result.outcome.winner.fitness,
                "observation_window_seconds": result.observation_window_seconds,
                "path": str(result.best_model_path),
                "reference_sha256": result.best_model.reference_sha256,
                "stage": "fit",
            },
        )
    except TrafficlabError as logging_error:
        state = "reused" if result.reused_best_model else "published"
        raise TrafficlabError(
            f"best model was {state} at {result.best_model_path}, but success logging failed: {logging_error}",
            corrective_action=logging_error.corrective_action,
        ) from logging_error
    return result
