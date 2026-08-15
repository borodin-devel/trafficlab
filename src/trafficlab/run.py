"""Explicit in-process orchestration for one complete Trafficlab experiment."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from trafficlab.artifacts import FileIdentity, _file_identity, append_run_log  # pyright: ignore[reportPrivateUsage]
from trafficlab.capture import CaptureResult, capture_prepared_experiment
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.comparison import (
    ComparisonResult,
    compare_experiment,
    parse_comparison_result,
    render_comparison_result,
    sha256_bytes,
    similarity_settings_sha256,
)
from trafficlab.config_io import render_effective_config
from trafficlab.errors import EvidenceState, TrafficlabError, failure_outcome_from_error
from trafficlab.fitting import FitStageResult, fit_experiment
from trafficlab.generation import GenerationStageResult, generate_experiment
from trafficlab.genetic.checkpoint import parse_checkpoint, render_history_csv
from trafficlab.genetic.strategy import FitOutcome, make_strategy_context
from trafficlab.genetic.types import Candidate, TrialResult
from trafficlab.models.registry import BestModel, load_best_model, render_best_model
from trafficlab.pcapng import parse_pcapng_bytes
from trafficlab.preflight import PreparedExperiment, run_preflight
from trafficlab.trace import TraceEvent, normalize_reference, parse_capture_metadata

_SUCCESSFUL_RUN_NAMES = frozenset(
    {
        "best_model.json",
        "capture.json",
        "checkpoint.json",
        "experiment.toml",
        "ga_history.csv",
        "generated.pcapng",
        "reference.pcapng",
        "run.log",
        "similarity.json",
    }
)


@dataclass(frozen=True, slots=True)
class RunResult:
    """Validated results returned by the five complete-experiment stages."""

    experiment_path: Path
    run_directory: Path
    capture: CaptureResult
    fit: FitStageResult
    generation: GenerationStageResult
    comparison: ComparisonResult


@dataclass(frozen=True, slots=True)
class RunDependencies:
    """The five concrete stage boundaries used by the explicit coordinator."""

    preflight: Callable[[Path], PreparedExperiment]
    capture: Callable[[Path, PreparedExperiment], CaptureResult]
    fit: Callable[[Path], FitStageResult]
    generate: Callable[[Path], GenerationStageResult]
    compare: Callable[[Path], ComparisonResult]

    @classmethod
    def production(cls) -> Self:
        """Return the ordinary in-process stage functions with one full preflight."""
        return cls(_full_preflight, _capture_prepared, fit_experiment, generate_experiment, compare_experiment)


def _full_preflight(path: Path) -> PreparedExperiment:
    return run_preflight(path, config_only=False)


def _capture_prepared(path: Path, prepared: PreparedExperiment) -> CaptureResult:
    return capture_prepared_experiment(path, prepared)


def _invalid_stage_result(stage: str, detail: str) -> TrafficlabError:
    return TrafficlabError(
        f"{stage} returned invalid result: {detail}",
        corrective_action=f"rerun {stage} and report the invalid stage-result contract",
    )


def _read_stage_bytes(path: Path, *, stage: str, kind: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise _invalid_stage_result(stage, f"could not read {kind} {path}: {error}") from error


def _validate_preflight_result(
    experiment_path: Path,
    prepared: PreparedExperiment,
) -> None:
    if type(prepared) is not PreparedExperiment:
        raise _invalid_stage_result("preflight", "expected an exact PreparedExperiment")
    if prepared.source != experiment_path:
        raise _invalid_stage_result("preflight", "source path does not match the experiment path")
    if prepared.run_directory != prepared.config.run.directory or not prepared.run_directory.is_absolute():
        raise _invalid_stage_result("preflight", "run directory does not match the effective configuration")


def _validate_capture_result(result: CaptureResult, prepared: PreparedExperiment) -> None:
    if type(result) is not CaptureResult:
        raise _invalid_stage_result("capture", "expected an exact CaptureResult")
    if result.run_directory != prepared.run_directory:
        raise _invalid_stage_result("capture", "run_directory does not match preflight")
    if result.reference_path != prepared.run_directory / "reference.pcapng":
        raise _invalid_stage_result("capture", "reference_path is not the documented reference.pcapng")
    if result.target_status != 0:
        raise _invalid_stage_result("capture", "target_status is not zero")


def _validate_fit_result(
    result: FitStageResult,
    experiment_path: Path,
    prepared: PreparedExperiment,
) -> tuple[float, bytes, dict[str, str]]:
    if type(result) is not FitStageResult:
        raise _invalid_stage_result("fit", "expected an exact FitStageResult")
    if result.experiment_path != experiment_path or result.run_directory != prepared.run_directory:
        raise _invalid_stage_result("fit", "experiment or run path does not match preflight")
    if result.best_model_path != prepared.run_directory / "best_model.json":
        raise _invalid_stage_result("fit", "best_model_path is not the documented best_model.json")
    if type(result.best_model) is not BestModel:
        raise _invalid_stage_result("fit", "best_model is not an exact BestModel")
    window = result.observation_window_seconds
    if type(window) is not float or not math.isfinite(window) or window <= 0.0:
        raise _invalid_stage_result("fit", "observation window is not a finite positive float")
    if result.best_model.observation_window_seconds != window:
        raise _invalid_stage_result("fit", "observation window does not match best_model.json")
    if type(result.outcome) is not FitOutcome:
        raise _invalid_stage_result("fit", "outcome is not an exact FitOutcome")
    winner = result.outcome.winner
    if type(winner) is not Candidate:
        raise _invalid_stage_result("fit", "winner is not an exact Candidate")
    if winner.status != "valid" or winner.invalid is not None:
        raise _invalid_stage_result("fit", "winner is not a valid candidate")
    if type(winner.fitness) is not float or not math.isfinite(winner.fitness) or not 0.0 <= winner.fitness <= 1.0:
        raise _invalid_stage_result("fit", "winner fitness is not a finite float in [0, 1]")
    if type(winner.trials) is not tuple or any(type(trial) is not TrialResult for trial in winner.trials):
        raise _invalid_stage_result("fit", "winner trials are not an exact TrialResult tuple")
    if tuple(trial.seed for trial in winner.trials) != prepared.config.genetic.trial_seeds:
        raise _invalid_stage_result("fit", "winner trials did not use exactly the configured trial seeds")
    if type(result.outcome.final_trials) is not tuple or any(
        type(trial) is not TrialResult for trial in result.outcome.final_trials
    ):
        raise _invalid_stage_result("fit", "final trials are not an exact TrialResult tuple")
    if result.best_model.family != winner.family:
        raise _invalid_stage_result("fit", "winning family does not match best_model.json")
    if winner.genes != result.best_model.genes:
        raise _invalid_stage_result("fit", "winning genes do not match best_model.json")
    final_seeds = tuple(trial.seed for trial in result.outcome.final_trials)
    if final_seeds != (prepared.config.run.final_seed,):
        raise _invalid_stage_result("fit", "final validation did not use exactly the configured final seed")
    if type(result.reused_best_model) is not bool:
        raise _invalid_stage_result("fit", "best-model reuse evidence is not a boolean")

    capture_path = prepared.run_directory / "capture.json"
    reference_path = prepared.run_directory / "reference.pcapng"
    capture_content = _read_stage_bytes(capture_path, stage="fit", kind="capture metadata")
    reference_content = _read_stage_bytes(reference_path, stage="fit", kind="reference PCAPNG")
    capture_sha256 = sha256_bytes(capture_content)
    reference_sha256 = sha256_bytes(reference_content)
    if result.best_model.capture_sha256 != capture_sha256:
        raise _invalid_stage_result("fit", "best_model.json capture lineage does not match capture.json")
    if result.best_model.reference_sha256 != reference_sha256:
        raise _invalid_stage_result("fit", "best_model.json reference lineage does not match reference.pcapng")
    return (
        window,
        capture_content,
        {
            "capture_json": capture_sha256,
            "reference_pcapng": reference_sha256,
            "similarity_settings": similarity_settings_sha256(prepared.config.similarity),
        },
    )


def _validate_generation_result(
    result: GenerationStageResult,
    prepared: PreparedExperiment,
    observation_window_seconds: float,
    capture_content: bytes,
) -> str:
    if type(result) is not GenerationStageResult:
        raise _invalid_stage_result("generate", "expected an exact GenerationStageResult")
    if result.run_directory != prepared.run_directory:
        raise _invalid_stage_result("generate", "run_directory does not match preflight")
    if result.generated_path != prepared.run_directory / "generated.pcapng":
        raise _invalid_stage_result("generate", "generated_path is not the documented generated.pcapng")
    if result.seed != prepared.config.run.final_seed:
        raise _invalid_stage_result("generate", "generation did not use exactly the configured final seed")
    if (
        type(result.observation_window_seconds) is not float
        or result.observation_window_seconds != observation_window_seconds
    ):
        raise _invalid_stage_result("generate", "observation window does not match fitting")
    if type(result.events) is not tuple:
        raise _invalid_stage_result("generate", "events are not an exact event tuple")
    if any(type(event) is not TraceEvent for event in result.events):
        raise _invalid_stage_result("generate", "events contain a value that is not an exact TraceEvent")
    if type(result.reused) is not bool:
        raise _invalid_stage_result("generate", "generated-output reuse evidence is not a boolean")

    generated_content = _read_stage_bytes(result.generated_path, stage="generate", kind="generated PCAPNG")
    metadata_path = prepared.run_directory / "capture.json"
    try:
        metadata = parse_capture_metadata(capture_content, source=metadata_path)
        generated_events = parse_pcapng_bytes(generated_content, metadata, source=result.generated_path)
    except TrafficlabError as error:
        raise _invalid_stage_result("generate", f"could not validate generated output identity: {error}") from error
    if generated_events != result.events:
        raise _invalid_stage_result("generate", "generated output events do not match generated.pcapng")
    return sha256_bytes(generated_content)


def _validate_comparison_result(
    result: ComparisonResult,
    observation_window_seconds: float,
    expected_input_sha256: dict[str, str],
) -> None:
    if type(result) is not ComparisonResult:
        raise _invalid_stage_result("compare", "expected an exact ComparisonResult")
    if (
        type(result.observation_window_seconds) is not float
        or result.observation_window_seconds != observation_window_seconds
    ):
        raise _invalid_stage_result("compare", "observation window does not match fitting and generation")
    if result.input_sha256 != expected_input_sha256:
        raise _invalid_stage_result("compare", "input lineage does not match the validated stage outputs")


type _FinalOwner = Literal["preflight", "capture", "fit", "generate", "compare", "run"]
type _FinalIdentities = dict[Path, tuple[_FinalOwner, FileIdentity]]


class _FinalArtifactError(TrafficlabError):
    """Final validation failure carrying its artifact-owning stage."""

    owner: _FinalOwner

    def __init__(self, owner: _FinalOwner, detail: str) -> None:
        self.owner = owner
        super().__init__(
            f"final run artifact validation failed for {owner}: {detail}",
            corrective_action=f"preserve the existing artifacts, rerun {owner}, and retry the complete run",
        )


def _final_artifact_error(owner: _FinalOwner, detail: str) -> _FinalArtifactError:
    return _FinalArtifactError(owner, detail)


def _read_final_artifact(path: Path, *, owner: _FinalOwner, identities: _FinalIdentities) -> bytes:
    try:
        identity = _file_identity(
            path,
            kind="final artifact entry",
            corrective_action="verify the final run artifact entries are inspectable and retry the complete run",
        )
        content = path.read_bytes()
        current_identity = _file_identity(
            path,
            kind="final artifact entry",
            corrective_action="verify the final run artifact entries are inspectable and retry the complete run",
        )
    except (OSError, TrafficlabError) as error:
        raise _final_artifact_error(owner, f"could not read {path.name}: {error}") from error
    if identity is None or current_identity != identity:
        raise _final_artifact_error(owner, f"{path.name} changed during final validation")
    identities[path] = (owner, identity)
    return content


def _validate_successful_run_tree(run_directory: Path) -> None:
    try:
        names = frozenset(entry.name for entry in run_directory.iterdir())
    except OSError as error:
        raise _final_artifact_error("run", f"could not inspect the run directory: {error}") from error
    if names != _SUCCESSFUL_RUN_NAMES:
        missing = sorted(_SUCCESSFUL_RUN_NAMES - names)
        unexpected = sorted(names - _SUCCESSFUL_RUN_NAMES)
        raise _final_artifact_error(
            "run",
            f"directory entries are not the documented nine names; missing={missing!r}; unexpected={unexpected!r}",
        )


def _validate_final_run_log(content: bytes) -> None:
    try:
        text = content.decode("utf-8")
        if not text.endswith("\n"):
            raise ValueError("run.log must end with a newline")
        lines = text.splitlines()
        for line in lines:
            record = json.loads(line)
            if type(record) is not dict:
                raise ValueError("every run.log record must be a JSON object")
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if canonical != line:
                raise ValueError("every run.log record must use canonical sorted compact JSON")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise _final_artifact_error("preflight", f"run.log is invalid: {error}") from error


def _validate_final_artifacts(
    prepared: PreparedExperiment,
    capture: CaptureResult,
    fit: FitStageResult,
    generation: GenerationStageResult,
    comparison: ComparisonResult,
) -> None:
    """Strictly reload the complete documented run before publishing coordinator success."""
    run_directory = prepared.run_directory
    identities: _FinalIdentities = {}
    _validate_successful_run_tree(run_directory)

    snapshot_path = run_directory / "experiment.toml"
    snapshot_content = _read_final_artifact(snapshot_path, owner="preflight", identities=identities)
    if snapshot_content != render_effective_config(prepared.config):
        raise _final_artifact_error("preflight", "experiment.toml does not match the prepared effective configuration")
    _validate_final_run_log(_read_final_artifact(run_directory / "run.log", owner="preflight", identities=identities))

    capture_path = run_directory / "capture.json"
    reference_path = run_directory / "reference.pcapng"
    try:
        inspection = validate_capture_pair(capture_path, reference_path, deadline=None)
    except TrafficlabError as error:
        raise _final_artifact_error("capture", str(error)) from error
    if inspection.packet_count != capture.packet_count:
        raise _final_artifact_error("capture", "strict capture packet count does not match the capture result")
    capture_content = _read_final_artifact(capture_path, owner="capture", identities=identities)
    reference_content = _read_final_artifact(reference_path, owner="capture", identities=identities)
    try:
        metadata = parse_capture_metadata(capture_content, source=capture_path)
        reference_events = parse_pcapng_bytes(reference_content, metadata, source=reference_path)
        reference, window = normalize_reference(reference_events)
    except TrafficlabError as error:
        raise _final_artifact_error("capture", str(error)) from error
    if len(reference_events) != inspection.packet_count:
        raise _final_artifact_error("capture", "capture pair changed between strict validation and lineage loading")
    if window != fit.observation_window_seconds:
        raise _final_artifact_error("capture", "strict reference window does not match the fitting result")

    input_sha256 = {
        "capture_json": sha256_bytes(capture_content),
        "generated_pcapng": "",
        "reference_pcapng": sha256_bytes(reference_content),
        "similarity_settings": similarity_settings_sha256(prepared.config.similarity),
    }
    context = make_strategy_context(
        prepared.config,
        reference,
        window,
        run_directory,
        experiment_sha256=sha256_bytes(snapshot_content),
        reference_sha256=input_sha256["reference_pcapng"],
        capture_sha256=input_sha256["capture_json"],
    )
    checkpoint_content = _read_final_artifact(run_directory / "checkpoint.json", owner="fit", identities=identities)
    try:
        checkpoint = parse_checkpoint(checkpoint_content, context.compatibility)
    except TrafficlabError as error:
        raise _final_artifact_error("fit", str(error)) from error
    checkpoint_winner = {candidate.identifier: candidate for candidate in checkpoint.population}[
        checkpoint.best_identifier
    ]
    if (
        checkpoint_winner != fit.outcome.winner
        or checkpoint.generation != fit.outcome.generation
        or checkpoint.terminal_reason != fit.outcome.terminal_reason
    ):
        raise _final_artifact_error("fit", "checkpoint terminal state does not match the fitting result")
    history_content = _read_final_artifact(run_directory / "ga_history.csv", owner="fit", identities=identities)
    if history_content != render_history_csv(checkpoint):
        raise _final_artifact_error("fit", "ga_history.csv is not the exact checkpoint history projection")

    best_model_path = run_directory / "best_model.json"
    best_model_content = _read_final_artifact(best_model_path, owner="fit", identities=identities)
    try:
        best_model = load_best_model(best_model_content, source=best_model_path)
    except TrafficlabError as error:
        raise _final_artifact_error("fit", str(error)) from error
    if render_best_model(best_model) != best_model_content:
        raise _final_artifact_error("fit", "best_model.json is not canonical")
    if best_model != fit.best_model:
        raise _final_artifact_error("fit", "best_model.json does not match the fitting result")
    if (
        best_model.capture_sha256 != input_sha256["capture_json"]
        or best_model.reference_sha256 != input_sha256["reference_pcapng"]
        or best_model.observation_window_seconds != window
    ):
        raise _final_artifact_error("fit", "best_model.json lineage does not match the strict capture pair")

    generated_path = run_directory / "generated.pcapng"
    generated_content = _read_final_artifact(generated_path, owner="generate", identities=identities)
    try:
        generated_events = parse_pcapng_bytes(generated_content, metadata, source=generated_path)
    except TrafficlabError as error:
        raise _final_artifact_error("generate", str(error)) from error
    if generated_events != generation.events:
        raise _final_artifact_error("generate", "generated.pcapng does not match the generation result")
    input_sha256["generated_pcapng"] = sha256_bytes(generated_content)

    similarity_path = run_directory / "similarity.json"
    similarity_content = _read_final_artifact(similarity_path, owner="compare", identities=identities)
    try:
        persisted_comparison = parse_comparison_result(similarity_content)
    except ValueError as error:
        raise _final_artifact_error("compare", f"similarity.json is invalid: {error}") from error
    if render_comparison_result(persisted_comparison) != similarity_content:
        raise _final_artifact_error("compare", "similarity.json is not canonical")
    if persisted_comparison != comparison:
        raise _final_artifact_error("compare", "similarity.json does not match the comparison result")
    if persisted_comparison.input_sha256 != input_sha256:
        raise _final_artifact_error("compare", "similarity.json lineage does not match the strict final artifacts")
    for path, (owner, identity) in identities.items():
        try:
            current_identity = _file_identity(
                path,
                kind="final artifact entry",
                corrective_action="verify the final run artifact entries are inspectable and retry the complete run",
            )
        except TrafficlabError as error:
            raise _final_artifact_error(owner, f"could not inspect {path.name}: {error}") from error
        if current_identity != identity:
            raise _final_artifact_error(owner, f"{path.name} changed during final validation")
    _validate_successful_run_tree(run_directory)


def _append_run_failure(
    run_directory: Path,
    primary: TrafficlabError,
    *,
    failed_stage: str,
    completed_stages: tuple[str, ...],
) -> None:
    outcome_by_stage: dict[str, tuple[str, str, EvidenceState]] = {
        "capture": ("capture_malformed", "capture pair", "diagnostic_only"),
        "fit": ("artifact_corrupt", "best_model.json", "not_published"),
        "generate": ("generation_incomplete", "generated.pcapng", "not_published"),
        "compare": ("metric_infeasible", "similarity.json", "not_published"),
        "preflight": ("configuration_invalid", "run evidence", "not_published"),
        "run": ("artifact_corrupt", "run evidence", "preserved"),
    }
    outcome = primary.failure_outcome
    if outcome is None:
        kind, evidence, evidence_state = outcome_by_stage[failed_stage]
        outcome = failure_outcome_from_error(
            primary,
            kind=kind,
            stage=failed_stage,
            affected_evidence=evidence,
            evidence_state=evidence_state,
        )
    try:
        append_run_log(
            run_directory,
            {
                "completed_stages": list(completed_stages),
                "corrective_action": primary.corrective_action,
                "detail": str(primary),
                "event": "run_failed",
                "failed_stage": failed_stage,
                "failure_outcome": outcome.as_dict(),
                "stage": "run",
            },
        )
    except TrafficlabError as logging_error:
        primary.args = (f"{primary}; additionally could not append run failure to run.log: {logging_error}",)


def run_experiment(
    experiment_path: Path,
    *,
    dependencies: RunDependencies | None = None,
) -> RunResult:
    """Run and immediately validate preflight, capture, fit, generate, and compare."""
    active = dependencies or RunDependencies.production()
    prepared = active.preflight(experiment_path)
    _validate_preflight_result(experiment_path, prepared)

    current_stage = "capture"
    completed_stages: tuple[str, ...] = ("preflight",)
    try:
        capture = active.capture(experiment_path, prepared)
        _validate_capture_result(capture, prepared)
        completed_stages = (*completed_stages, "capture")

        current_stage = "fit"
        fit = active.fit(experiment_path)
        observation_window_seconds, capture_content, expected_input_sha256 = _validate_fit_result(
            fit, experiment_path, prepared
        )
        completed_stages = (*completed_stages, "fit")

        current_stage = "generate"
        generation = active.generate(experiment_path)
        expected_input_sha256["generated_pcapng"] = _validate_generation_result(
            generation,
            prepared,
            observation_window_seconds,
            capture_content,
        )
        completed_stages = (*completed_stages, "generate")

        current_stage = "compare"
        comparison = active.compare(experiment_path)
        _validate_comparison_result(comparison, observation_window_seconds, expected_input_sha256)
        completed_stages = (*completed_stages, "compare")
        _validate_final_artifacts(prepared, capture, fit, generation, comparison)

        append_run_log(
            prepared.run_directory,
            {
                "aggregate_score": comparison.aggregate_score,
                "event": "run_completed",
                "family": fit.outcome.winner.family,
                "fitness": fit.outcome.winner.fitness,
                "generated_packet_count": len(generation.events),
                "reference_packet_count": capture.packet_count,
                "run_directory": str(prepared.run_directory),
                "stage": "run",
            },
        )
    except TrafficlabError as error:
        failed_stage = error.owner if isinstance(error, _FinalArtifactError) else current_stage
        _append_run_failure(
            prepared.run_directory,
            error,
            failed_stage=failed_stage,
            completed_stages=completed_stages,
        )
        raise

    return RunResult(experiment_path, prepared.run_directory, capture, fit, generation, comparison)
