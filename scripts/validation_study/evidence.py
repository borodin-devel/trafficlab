"""Evidence owner for Validation Study tooling."""

from __future__ import annotations

import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, cast

from scripts.validation_study.common import (
    ARTIFACT_NAMES,
    PRIMARY_ORDER,
    FrozenJsonValue,
    JsonObject,
    load_json,
    repository_relative_path,
    require,
    require_type,
    strict_float,
    strict_string,
    thaw_json,
)
from scripts.validation_study.records import run_record_from_document
from scripts.validation_study.results.codec import validate_run_evidence, validate_transfer_responses
from scripts.validation_study.results.reporting import (
    family_champions,
    sample_record,
    score_from_comparison,
    score_from_trial,
    select_winner,
)
from trafficlab.artifacts.io import FileIdentity, file_identity
from trafficlab.capture.validation import validate_capture_pair
from trafficlab.common.compatibility import ContentIdentity, identify_bytes
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import load_experiment, render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import encode_pcapng, read_pcapng_bytes
from trafficlab.common.trace import (
    CaptureMetadata,
    TrafficTrace,
    align_generated,
    normalize_reference,
    parse_capture_metadata,
)
from trafficlab.comparison.codec import (
    parse_comparison_result,
    render_comparison_result,
    sha256_bytes,
    similarity_settings_identity,
)
from trafficlab.comparison.diagnostics import MultiscaleDiagnostic
from trafficlab.comparison.metrics import compare_final_traces, compare_traces
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.checkpoint import CheckpointState, parse_checkpoint, render_history_csv
from trafficlab.fitting.genetic.evaluation import validate_evaluation_context
from trafficlab.fitting.genetic.strategy import StrategyContext, make_strategy_context
from trafficlab.fitting.genetic.types import TrialResult
from trafficlab.generation.models.fitted_model import (
    BestModel,
    load_best_model,
    render_best_model,
    runtime_fitted_model,
)
from trafficlab.generation.models.registry import get_family
from trafficlab.pipeline.types import RunResult
from trafficlab.pipeline.validation import validate_final_artifacts
from trafficlab.preflight.stage import open_or_prepare_experiment

if TYPE_CHECKING:
    from scripts.validation_study.common import JsonValue, WorkloadName
    from scripts.validation_study.records import StudyRunRecord, StudyRunSpec
    from scripts.validation_study.workloads import WorkloadSpec


@dataclass(frozen=True, slots=True)
class LoadedRunEvidence:
    config: ExperimentConfig
    context: StrategyContext
    metadata: CaptureMetadata
    contents: Mapping[str, bytes]
    artifact_sha256: JsonObject
    reference: TrafficTrace
    generated: TrafficTrace
    checkpoint: CheckpointState
    best_model: BestModel
    comparison: ComparisonResult
    log_records: tuple[JsonObject, ...]


def read_exact_artifact_set(run_directory: Path) -> dict[str, bytes]:
    try:
        entries = tuple(run_directory.iterdir())
        require(
            {entry.name for entry in entries} == set(ARTIFACT_NAMES),
            "successful run directory must contain exactly the documented nine artifacts",
        )
        require(
            all(entry.is_file() and (not entry.is_symlink()) for entry in entries),
            "every successful run artifact must be a regular non-symlink file",
        )
        return {name: (run_directory / name).read_bytes() for name in ARTIFACT_NAMES}
    except OSError as error:
        raise TrafficlabError(
            f"could not read complete Validation Study run evidence {run_directory}: {error}",
            corrective_action="preserve the run and inspect its exact nine artifact files",
        ) from error


def _artifact_identities(run_directory: Path) -> dict[str, FileIdentity]:
    identities: dict[str, FileIdentity] = {}
    for name in ARTIFACT_NAMES:
        identity = file_identity(
            run_directory / name,
            kind="Validation Study evidence artifact",
            corrective_action="preserve the run and inspect its exact nine artifact files",
        )
        if identity is None:
            raise ValueError(f"Validation Study evidence artifact is missing: {name}")
        identities[name] = identity
    return identities


def parse_run_log(content: bytes) -> tuple[JsonObject, ...]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"run.log must be valid UTF-8: {error}") from error
    require(text.endswith("\n"), "run.log must end with one newline")
    records = tuple(load_json(line.encode("utf-8")) for line in text.splitlines())
    require(bool(records), "run.log must contain records")
    return records


def load_persisted_run_evidence(spec: StudyRunSpec) -> LoadedRunEvidence:
    identities = _artifact_identities(spec.run_directory)
    contents = read_exact_artifact_set(spec.run_directory)
    config = load_experiment(spec.config_path)
    snapshot_path = spec.run_directory / "experiment.toml"
    snapshot_config = load_experiment(snapshot_path)
    require(config == snapshot_config, "realized config and run snapshot must load to the same experiment")
    require(
        contents["experiment.toml"] == render_effective_config(config),
        "experiment.toml must be the canonical effective configuration",
    )
    capture_path = spec.run_directory / "capture.json"
    reference_path = spec.run_directory / "reference.pcapng"
    inspection = validate_capture_pair(capture_path, reference_path, deadline=None)
    metadata = parse_capture_metadata(contents["capture.json"], source=capture_path)
    captured = read_pcapng_bytes(contents["reference.pcapng"], metadata, source=reference_path)
    require(inspection.packet_count == len(captured), "strict persisted reference packet counts must agree")
    reference, window = normalize_reference(captured)
    artifact_sha256: JsonObject = {name: sha256_bytes(contents[name]) for name in ARTIFACT_NAMES}
    context = make_strategy_context(
        config,
        reference,
        window,
        spec.run_directory,
        experiment_identity=ContentIdentity(
            size=len(contents["experiment.toml"]), sha256=cast(str, artifact_sha256["experiment.toml"])
        ),
        reference_identity=ContentIdentity(
            size=len(contents["reference.pcapng"]), sha256=cast(str, artifact_sha256["reference.pcapng"])
        ),
        capture_identity=ContentIdentity(
            size=len(contents["capture.json"]), sha256=cast(str, artifact_sha256["capture.json"])
        ),
    )
    checkpoint = parse_checkpoint(contents["checkpoint.json"], context.compatibility)
    require(
        contents["ga_history.csv"] == render_history_csv(checkpoint),
        "ga_history.csv must be the exact terminal checkpoint history projection",
    )
    best_path = spec.run_directory / "best_model.json"
    best_model = load_best_model(contents["best_model.json"], source=best_path)
    require(
        contents["best_model.json"] == render_best_model(best_model),
        "best_model.json must use its canonical production encoding",
    )
    generated_path = spec.run_directory / "generated.pcapng"
    generated = read_pcapng_bytes(contents["generated.pcapng"], metadata, source=generated_path)
    similarity = parse_comparison_result(contents["similarity.json"])
    require(
        contents["similarity.json"] == render_comparison_result(similarity),
        "similarity.json must use its canonical production encoding",
    )
    require(
        read_exact_artifact_set(spec.run_directory) == contents
        and _artifact_identities(spec.run_directory) == identities,
        "run artifacts must retain exact identities throughout evidence extraction",
    )
    return LoadedRunEvidence(
        config,
        context,
        metadata,
        MappingProxyType(contents),
        artifact_sha256,
        reference,
        generated,
        checkpoint,
        best_model,
        similarity,
        parse_run_log(contents["run.log"]),
    )


def _load_run_evidence(spec: StudyRunSpec, result: RunResult) -> LoadedRunEvidence:
    require_type(type(result) is RunResult, "run evidence source must be an exact RunResult")
    require(
        result.experiment_path == spec.config_path and result.run_directory == spec.run_directory,
        "run result paths must equal the selected study run",
    )
    prepared = open_or_prepare_experiment(spec.config_path)
    require(
        prepared.run_directory == spec.run_directory,
        "effective run directory must equal the selected study run directory",
    )
    validate_final_artifacts(prepared, result.capture, result.fit, result.generation, result.comparison)
    evidence = load_persisted_run_evidence(spec)
    require(
        len(evidence.reference) == result.capture.packet_count,
        "capture result and strict persisted reference packet counts must agree",
    )
    require(
        evidence.context.evaluation.window == result.fit.observation_window_seconds,
        "strict reference window must equal fitting evidence",
    )
    return evidence


def _direction_values(trace: TrafficTrace, *, bytes_: bool) -> JsonObject:
    outbound = trace.directions == 0
    inbound = trace.directions == 1
    return {
        "outbound": int(sum(int(value) for value in trace.frame_lengths[outbound])) if bytes_ else int(outbound.sum()),
        "inbound": int(sum(int(value) for value in trace.frame_lengths[inbound])) if bytes_ else int(inbound.sum()),
    }


def trace_summary(
    trace: TrafficTrace, result: ComparisonResult, *, role: Literal["reference", "generated"]
) -> JsonObject:
    require_type(type(trace) is TrafficTrace and bool(trace), "trace summary requires a canonical TrafficTrace")
    require(len(trace) >= 2, "trace summary requires at least two events")
    frame_lengths = tuple(int(value) for value in trace.frame_lengths)
    iats = tuple(float(value) for value in trace.iats())
    packet_totals = _direction_values(trace, bytes_=False)
    byte_totals = _direction_values(trace, bytes_=True)
    multiscale = result.methods["multiscale_rate"].diagnostics
    require_type(isinstance(multiscale, MultiscaleDiagnostic), "multiscale diagnostics must be typed")
    multiscale = cast(MultiscaleDiagnostic, multiscale)
    scales: list[JsonValue] = []
    for value in multiscale.scales:
        scale = value.model_dump(mode="json")
        totals_value = scale.get(f"{role}_totals")
        require_type(isinstance(totals_value, Mapping), "multiscale direction totals must be a mapping")
        totals = cast(Mapping[str, object], totals_value)
        scale_packets = cast(JsonObject, thaw_json(cast(FrozenJsonValue, totals["packet"])))
        scale_bytes = cast(JsonObject, thaw_json(cast(FrozenJsonValue, totals["byte"])))
        require(
            scale_packets == packet_totals and scale_bytes == byte_totals,
            f"{role} multiscale direction totals must equal the canonical trace",
        )
        scales.append(
            {
                "width_seconds": cast(float, scale["width_seconds"]),
                "bins_per_direction": cast(int, scale["bins_per_direction"]),
                "packet_totals": scale_packets,
                "byte_totals": scale_bytes,
            }
        )
    return {
        "packet_count": len(trace),
        "observation_window_seconds": result.observation_window_seconds,
        "packet_totals": packet_totals,
        "byte_totals": byte_totals,
        "frame_lengths": sample_record(frame_lengths, quantile_probability=0.95, zero_count=0),
        "iats": sample_record(iats, quantile_probability=0.95, zero_count=iats.count(0.0)),
        "scales": scales,
    }


def _comparison_equals_trial(comparison: ComparisonResult, trial: TrialResult) -> bool:
    return (
        comparison.aggregate_score == trial.aggregate_score
        and comparison.methods.keys() == tuple(method.name for method in trial.methods)
        and all(
            comparison.methods[method.name].score == method.score
            and comparison.methods[method.name].diagnostics.model_dump(mode="json")
            == thaw_json(cast(FrozenJsonValue, method.diagnostics))
            for method in trial.methods
        )
    )


def fresh_run_log_proofs(records: Sequence[JsonObject]) -> None:
    capture_records = tuple(record for record in records if record.get("event") == "capture_published")
    best_model_records = tuple(record for record in records if record.get("event") == "best_model_published")
    generated_records = tuple(record for record in records if record.get("event") == "generated_pcapng_published")
    comparison_records = tuple(record for record in records if record.get("event") == "comparison_succeeded")
    completed = tuple(record for record in records if record.get("event") == "run_completed")
    require(
        len(capture_records) == 1
        and capture_records[0].get("stage") == "capture"
        and (capture_records[0].get("reused") is False),
        "fresh run must contain one successful non-reused capture publication",
    )
    require(
        not any(str(record.get("event", "")).endswith("_reused") for record in records),
        "fresh run log must not contain a reused-stage event",
    )
    require(
        len(best_model_records) == 1 and len(generated_records) == 1,
        "fresh run must publish one new best model and one new generated PCAPNG",
    )
    require(
        len(comparison_records) == 1 and comparison_records[0].get("reused") is False,
        "fresh run must contain one successful non-reused comparison publication",
    )
    require(
        len(completed) == 1 and records[-1] == completed[0], "fresh run must end with exactly one run_completed record"
    )


def sole_final_trial(trials: Sequence[TrialResult]) -> TrialResult:
    values = cast(tuple[object, ...], trials)
    require(
        type(trials) is tuple and len(values) == 1 and (type(values[0]) is TrialResult),
        "fresh simulation evaluation must return exactly one TrialResult",
    )
    trial = cast(TrialResult, values[0])
    require(trial.seed == 97, "fresh simulation evaluation must use exact final seed 97")
    return trial


def _require_published_lineage(
    rebuilt: ComparisonResult,
    persisted: ComparisonResult,
    artifact_contents: Mapping[str, bytes],
    settings_identity: ContentIdentity,
) -> None:
    input_identities = rebuilt.input_identities
    require(input_identities is not None, "published comparison must carry exact input lineage")
    assert input_identities is not None
    expected = {
        "capture_json": identify_bytes(artifact_contents["capture.json"]),
        "reference_pcapng": identify_bytes(artifact_contents["reference.pcapng"]),
        "generated_pcapng": identify_bytes(artifact_contents["generated.pcapng"]),
        "similarity_settings": settings_identity,
    }
    require(
        input_identities.as_content_identities() == expected,
        "published comparison input lineage must match exact artifact identities",
    )
    require(rebuilt == persisted, "published comparison must equal strict persisted similarity evidence")


@dataclass(frozen=True, slots=True)
class _ReconstructedScience:
    fresh_simulation: TrialResult
    raw_events: TrafficTrace
    reparsed_events: TrafficTrace
    aligned_events: TrafficTrace
    published: ComparisonResult


def reconstruct_science(
    evidence: LoadedRunEvidence, fresh_simulation: TrialResult, *, generated_path: Path
) -> _ReconstructedScience:
    window = evidence.best_model.observation_window_seconds
    family = get_family(evidence.best_model.family)
    raw_trial = family.generate(
        runtime_fitted_model(evidence.best_model), 97, window, evidence.config.generation.trial
    ).require_complete()
    raw_final = family.generate(
        runtime_fitted_model(evidence.best_model), 97, window, evidence.config.generation.final
    ).require_complete()
    require(raw_trial == raw_final, "trial and final guards must produce one exact raw seed-97 sequence")
    raw_comparison = compare_traces(evidence.reference, raw_trial, window, evidence.config.similarity)
    require(
        _comparison_equals_trial(raw_comparison, fresh_simulation),
        "raw seed-97 comparison must equal the sole direct fresh simulation evaluation",
    )
    encoded = encode_pcapng(raw_trial, evidence.metadata, observation_window_seconds=window)
    rendered = encoded.content
    reparsed = encoded.trace
    require(
        rendered == evidence.contents["generated.pcapng"] and reparsed == evidence.generated,
        "generated artifact must equal reparsed Scapy seed-97 events",
    )
    aligned = align_generated(reparsed, window)
    settings_identity = similarity_settings_identity(evidence.config.similarity)
    published = compare_final_traces(
        evidence.reference,
        aligned,
        window,
        evidence.config.similarity,
        {
            "capture_json": identify_bytes(evidence.contents["capture.json"]),
            "reference_pcapng": identify_bytes(evidence.contents["reference.pcapng"]),
            "generated_pcapng": identify_bytes(evidence.contents["generated.pcapng"]),
            "similarity_settings": settings_identity,
        },
    )
    _require_published_lineage(published, evidence.comparison, evidence.contents, settings_identity)
    return _ReconstructedScience(fresh_simulation, raw_trial, reparsed, aligned, published)


def repository_path_record(path: Path, *, repository_root: Path, name: str) -> str:
    root = repository_root.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError(f"{name} must remain beneath the repository root") from error
    return repository_relative_path(relative, repository_root=root, name=name)


def _transfer_object_size(responses: Sequence[JsonObject]) -> int:
    sizes: set[int] = set()
    for response in responses:
        value = strict_string(response.get("content_range"), name="transfer content range")
        match = re.fullmatch("bytes \\d+-\\d+/(\\d+)", value)
        if match is None:
            raise ValueError("transfer content range must contain an exact object size")
        sizes.add(int(match.group(1)))
    require(len(sizes) == 1, "all transfer responses must name one object size")
    return next(iter(sizes))


def validate_transfer_archives(
    repository_root: Path, responses: Sequence[JsonObject], *, workload: WorkloadName, evidence_directory: str
) -> int:
    object_size = _transfer_object_size(responses)
    validate_transfer_responses(
        list(responses),
        repository_root=repository_root,
        workload=workload,
        evidence_directory=evidence_directory,
        object_size=object_size,
    )
    for response in responses:
        relative = cast(str, response["header_archive_path"])
        archive = repository_root / Path(*relative.split("/"))
        try:
            metadata = archive.lstat()
            content = archive.read_bytes()
        except OSError as error:
            raise ValueError(f"could not validate transfer header archive {relative}: {error}") from error
        require(stat.S_ISREG(metadata.st_mode), "transfer header archive must be a regular file")
        require(stat.S_IMODE(metadata.st_mode) == 384, "transfer header archive must retain mode 0600")
        require(
            sha256_bytes(content) == response["header_sha256"],
            "transfer header archive must match its recorded SHA-256",
        )
    return object_size


def extract_primary_record(
    repository_root: Path,
    spec: StudyRunSpec,
    workload: WorkloadSpec,
    result: RunResult,
    elapsed_seconds: float,
    transfer_responses: tuple[JsonObject, ...],
) -> StudyRunRecord:
    root = repository_root.resolve()
    require(
        1 <= spec.execution_order <= len(PRIMARY_ORDER)
        and PRIMARY_ORDER[spec.execution_order - 1] == (spec.execution_order, spec.run_id, spec.workload, spec.repeat),
        "primary extraction spec must equal one exact balanced-order entry",
    )
    require(spec.workload == workload.name, "primary workload must match its selected run spec")
    elapsed = strict_float(elapsed_seconds, name="primary run elapsed seconds", lower=0.0)
    require(elapsed > 0.0, "primary run elapsed seconds must be positive")
    evidence = _load_run_evidence(spec, result)
    require(
        evidence.config.target.argv == workload.argv
        and evidence.config.similarity.multiscale_widths_seconds == workload.multiscale_widths_seconds,
        "primary run config must equal its exact workload profile",
    )
    require(
        not result.capture.reused and (not result.fit.reused_best_model) and (not result.generation.reused),
        "primary run capture, best model, and generated output must all be fresh",
    )
    fresh_run_log_proofs(evidence.log_records)
    validate_evaluation_context(evidence.context.evaluation)
    fresh_simulation_trial = sole_final_trial(result.fit.outcome.final_trials)
    science = reconstruct_science(
        evidence, fresh_simulation_trial, generated_path=spec.run_directory / "generated.pcapng"
    )
    require(
        science.reparsed_events == result.generation.trace and science.published == result.comparison,
        "run result must equal the reconstructed generated artifact and published comparison",
    )
    config_path = repository_path_record(spec.config_path, repository_root=root, name="primary config path")
    run_directory = repository_path_record(spec.run_directory, repository_root=root, name="primary run directory")
    evidence_directory = repository_path_record(
        spec.transfer_evidence_directory, repository_root=root, name="primary transfer evidence directory"
    )
    object_size = validate_transfer_archives(
        root, transfer_responses, workload=spec.workload, evidence_directory=evidence_directory
    )
    document: JsonObject = {
        "execution_order": spec.execution_order,
        "run_id": spec.run_id,
        "key": {"workload": spec.workload, "repeat": spec.repeat},
        "config_path": config_path,
        "run_directory": run_directory,
        "transfer_evidence_directory": evidence_directory,
        "elapsed_seconds": elapsed,
        "reuse": {"capture": False, "best_model": False, "generated": False, "similarity": False},
        "cleanup_verified": True,
        "transfer_responses": list(transfer_responses),
        "artifact_sha256": evidence.artifact_sha256,
        "reference": trace_summary(evidence.reference, science.published, role="reference"),
        "generated": trace_summary(science.aligned_events, science.published, role="generated"),
        "family_champions": list(family_champions(evidence.checkpoint)),
        "winner": select_winner(evidence.checkpoint, evidence.best_model),
        "fresh_simulation": {
            "seed": 97,
            "score": score_from_trial(science.fresh_simulation),
            "source": "run_experiment_fit_outcome",
        },
        "published": {"seed": 97, "score": score_from_comparison(science.published)},
        "raw_sequence": {
            "seed": 97,
            "observation_window_seconds": evidence.best_model.observation_window_seconds,
            "trial_event_count": len(science.raw_events),
            "final_event_count": len(science.raw_events),
            "raw_events_equal": True,
            "fresh_simulation_score_reproduced": True,
            "reparsed_event_count": len(science.reparsed_events),
            "reparsed_matches_quantized": True,
        },
    }
    validate_run_evidence(
        document,
        repository_root=root,
        workload=spec.workload,
        evidence_directory=evidence_directory,
        object_size=object_size,
        fresh_simulation_source="run_experiment_fit_outcome",
    )
    return run_record_from_document(document)
