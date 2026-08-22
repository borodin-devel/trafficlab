"""Full pipeline validation ownership."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from trafficlab.artifacts.io import FileIdentity, file_identity
from trafficlab.capture.lineage import CaptureResult
from trafficlab.capture.validation import validate_capture_pair
from trafficlab.common.compatibility import ContentIdentity, identify_bytes, require_compatible
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.errors import (
    TrafficlabError,
    attach_failure_outcome,
)
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.scientific_schema import ScientificArtifactSchemaError
from trafficlab.common.trace import TrafficTrace, normalize_reference, parse_capture_metadata
from trafficlab.comparison.codec import parse_comparison_result, render_comparison_result, similarity_settings_identity
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.checkpoint import parse_checkpoint, render_history_csv
from trafficlab.fitting.genetic.strategy import FitOutcome, make_strategy_context
from trafficlab.fitting.genetic.types import Candidate, TrialResult
from trafficlab.fitting.stage import FitStageResult
from trafficlab.generation.models.fitted_model import (
    BestModel,
    load_best_model,
    render_best_model,
)
from trafficlab.generation.stage import GenerationStageResult
from trafficlab.preflight.types import PreparedExperiment

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


def validate_preflight_result(
    experiment_path: Path,
    prepared: PreparedExperiment,
) -> None:
    if type(prepared) is not PreparedExperiment:
        raise _invalid_stage_result("preflight", "expected an exact PreparedExperiment")
    if prepared.source != experiment_path:
        raise _invalid_stage_result("preflight", "source path does not match the experiment path")
    if prepared.run_directory != prepared.config.run.directory or not prepared.run_directory.is_absolute():
        raise _invalid_stage_result("preflight", "run directory does not match the effective configuration")


def validate_capture_result(result: CaptureResult, prepared: PreparedExperiment) -> None:
    if type(result) is not CaptureResult:
        raise _invalid_stage_result("capture", "expected an exact CaptureResult")
    if result.run_directory != prepared.run_directory:
        raise _invalid_stage_result("capture", "run_directory does not match preflight")
    if result.reference_path != prepared.run_directory / "reference.pcapng":
        raise _invalid_stage_result("capture", "reference_path is not the documented reference.pcapng")
    if result.target_status != 0:
        raise _invalid_stage_result("capture", "target_status is not zero")


def validate_fit_result(
    result: FitStageResult,
    experiment_path: Path,
    prepared: PreparedExperiment,
) -> tuple[float, bytes, dict[str, ContentIdentity]]:
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
    capture_identity = identify_bytes(capture_content)
    reference_identity = identify_bytes(reference_content)
    try:
        require_compatible(
            {
                "capture lineage": capture_identity,
                "reference lineage": reference_identity,
                "final seed": prepared.config.run.final_seed,
                "final generation limits": prepared.config.generation.final,
            },
            {
                "capture lineage": result.best_model.capture_identity,
                "reference lineage": result.best_model.reference_identity,
                "final seed": result.best_model.final_seed,
                "final generation limits": result.best_model.final_limits,
            },
        )
    except TrafficlabError as error:
        raise _invalid_stage_result("fit", f"best_model.json lineage is incompatible: {error}") from error
    return (
        window,
        capture_content,
        {
            "capture_json": capture_identity,
            "reference_pcapng": reference_identity,
            "similarity_settings": similarity_settings_identity(prepared.config.similarity),
        },
    )


def validate_generation_result(
    result: GenerationStageResult,
    prepared: PreparedExperiment,
    observation_window_seconds: float,
    capture_content: bytes,
) -> ContentIdentity:
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
    if type(result.trace) is not TrafficTrace:
        raise _invalid_stage_result("generate", "trace is not an exact TrafficTrace")
    if type(result.reused) is not bool:
        raise _invalid_stage_result("generate", "generated-output reuse evidence is not a boolean")

    generated_content = _read_stage_bytes(result.generated_path, stage="generate", kind="generated PCAPNG")
    metadata_path = prepared.run_directory / "capture.json"
    try:
        metadata = parse_capture_metadata(capture_content, source=metadata_path)
        generated_trace = read_pcapng_bytes(generated_content, metadata, source=result.generated_path)
    except TrafficlabError as error:
        raise _invalid_stage_result("generate", f"could not validate generated output identity: {error}") from error
    if generated_trace != result.trace:
        raise _invalid_stage_result("generate", "generated output trace does not match generated.pcapng")
    return identify_bytes(generated_content)


def validate_comparison_result(
    result: ComparisonResult,
    observation_window_seconds: float,
    expected_input_identities: dict[str, ContentIdentity],
) -> None:
    if type(result) is not ComparisonResult:
        raise _invalid_stage_result("compare", "expected an exact ComparisonResult")
    if (
        type(result.observation_window_seconds) is not float
        or result.observation_window_seconds != observation_window_seconds
    ):
        raise _invalid_stage_result("compare", "observation window does not match fitting and generation")
    if result.input_identities is None:
        raise _invalid_stage_result("compare", "input lineage is absent")
    try:
        require_compatible(expected_input_identities, result.input_identities.as_content_identities())
    except TrafficlabError as error:
        raise _invalid_stage_result("compare", f"input lineage is incompatible: {error}") from error


type _FinalOwner = Literal["preflight", "capture", "fit", "generate", "compare", "run"]

type _FinalIdentities = dict[Path, tuple[_FinalOwner, FileIdentity]]


class FinalArtifactError(TrafficlabError):
    """Final validation failure carrying its artifact-owning stage."""

    owner: _FinalOwner

    def __init__(
        self,
        owner: _FinalOwner,
        detail: str,
        *,
        originating_error: TrafficlabError | None = None,
    ) -> None:
        self.owner = owner
        super().__init__(
            f"final run artifact validation failed for {owner}: {detail}",
            corrective_action=(
                originating_error.corrective_action
                if originating_error is not None
                else f"preserve the existing artifacts, rerun {owner}, and retry the complete run"
            ),
            failure_outcomes=(originating_error.failure_outcomes if originating_error is not None else None),
        )


def _final_artifact_error(
    owner: _FinalOwner,
    detail: str,
    *,
    originating_error: TrafficlabError | None = None,
) -> FinalArtifactError:
    return FinalArtifactError(owner, detail, originating_error=originating_error)


def _read_final_artifact(path: Path, *, owner: _FinalOwner, identities: _FinalIdentities) -> bytes:
    try:
        identity = file_identity(
            path,
            kind="final artifact entry",
            corrective_action="verify the final run artifact entries are inspectable and retry the complete run",
        )
        content = path.read_bytes()
        current_identity = file_identity(
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


def validate_final_artifacts(
    prepared: PreparedExperiment,
    capture: CaptureResult,
    fit: FitStageResult,
    generation: GenerationStageResult,
    comparison: ComparisonResult,
) -> None:
    """Strictly reload the complete documented run before publishing coordinator success."""
    # Stage return objects are not publication authority: files may have changed
    # after a stage validated them.  Reloading every artifact here binds the
    # final success event to one mutually compatible on-disk snapshot.
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
        reference_trace = read_pcapng_bytes(reference_content, metadata, source=reference_path)
        reference, window = normalize_reference(reference_trace)
    except TrafficlabError as error:
        raise _final_artifact_error("capture", str(error)) from error
    if len(reference_trace) != inspection.packet_count:
        raise _final_artifact_error("capture", "capture pair changed between strict validation and lineage loading")
    if window != fit.observation_window_seconds:
        raise _final_artifact_error("capture", "strict reference window does not match the fitting result")

    input_identities = {
        "capture_json": identify_bytes(capture_content),
        "generated_pcapng": ContentIdentity(size=0, sha256="0" * 64),
        "reference_pcapng": identify_bytes(reference_content),
        "similarity_settings": similarity_settings_identity(prepared.config.similarity),
    }
    context = make_strategy_context(
        prepared.config,
        reference,
        window,
        run_directory,
        experiment_identity=identify_bytes(snapshot_content),
        reference_identity=identify_bytes(reference_content),
        capture_identity=identify_bytes(capture_content),
    )
    checkpoint_content = _read_final_artifact(run_directory / "checkpoint.json", owner="fit", identities=identities)
    try:
        checkpoint = parse_checkpoint(checkpoint_content, context.compatibility)
    except ScientificArtifactSchemaError as error:
        attach_failure_outcome(
            error,
            kind="scientific_semantics_incompatible",
            stage="fit",
            affected_evidence="checkpoint.json",
            evidence_state="preserved",
        )
        raise _final_artifact_error("fit", str(error), originating_error=error) from error
    except TrafficlabError as error:
        raise _final_artifact_error("fit", str(error), originating_error=error) from error
    checkpoint_winner = {candidate.identifier: candidate for candidate in checkpoint.population}[
        checkpoint.best_identifier
    ]
    if (
        checkpoint_winner != fit.outcome.winner
        or checkpoint.generation != fit.outcome.generation
        or checkpoint.terminal_reason != fit.outcome.terminal_reason
        or checkpoint.family_priority != fit.outcome.family_priority
    ):
        raise _final_artifact_error("fit", "checkpoint terminal state does not match the fitting result")
    history_content = _read_final_artifact(run_directory / "ga_history.csv", owner="fit", identities=identities)
    if history_content != render_history_csv(checkpoint):
        raise _final_artifact_error("fit", "ga_history.csv is not the exact checkpoint history projection")

    best_model_path = run_directory / "best_model.json"
    best_model_content = _read_final_artifact(best_model_path, owner="fit", identities=identities)
    try:
        best_model = load_best_model(best_model_content, source=best_model_path)
    except ScientificArtifactSchemaError as error:
        attach_failure_outcome(
            error,
            kind="scientific_semantics_incompatible",
            stage="fit",
            affected_evidence="best_model.json",
            evidence_state="preserved",
        )
        raise _final_artifact_error("fit", str(error), originating_error=error) from error
    except TrafficlabError as error:
        raise _final_artifact_error("fit", str(error), originating_error=error) from error
    if render_best_model(best_model) != best_model_content:
        raise _final_artifact_error("fit", "best_model.json is not canonical")
    if best_model != fit.best_model:
        raise _final_artifact_error("fit", "best_model.json does not match the fitting result")
    if (
        best_model.capture_identity != input_identities["capture_json"]
        or best_model.reference_identity != input_identities["reference_pcapng"]
        or best_model.final_seed != prepared.config.run.final_seed
        or best_model.final_limits != prepared.config.generation.final
        or best_model.observation_window_seconds != window
    ):
        raise _final_artifact_error("fit", "best_model.json lineage does not match the strict capture pair")

    generated_path = run_directory / "generated.pcapng"
    generated_content = _read_final_artifact(generated_path, owner="generate", identities=identities)
    try:
        generated_trace = read_pcapng_bytes(generated_content, metadata, source=generated_path)
    except TrafficlabError as error:
        raise _final_artifact_error("generate", str(error)) from error
    if generated_trace != generation.trace:
        raise _final_artifact_error("generate", "generated.pcapng does not match the generation result")
    input_identities["generated_pcapng"] = identify_bytes(generated_content)

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
    if (
        persisted_comparison.input_identities is None
        or persisted_comparison.input_identities.as_content_identities() != input_identities
    ):
        raise _final_artifact_error("compare", "similarity.json lineage does not match the strict final artifacts")
    for path, (owner, identity) in identities.items():
        try:
            current_identity = file_identity(
                path,
                kind="final artifact entry",
                corrective_action="verify the final run artifact entries are inspectable and retry the complete run",
            )
        except TrafficlabError as error:
            raise _final_artifact_error(owner, f"could not inspect {path.name}: {error}") from error
        if current_identity != identity:
            raise _final_artifact_error(owner, f"{path.name} changed during final validation")
    _validate_successful_run_tree(run_directory)
