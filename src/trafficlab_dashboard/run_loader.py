from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from trafficlab.common.compatibility import ContentIdentity, identify_bytes
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.config_io import parse_experiment
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import (
    CaptureMetadata,
    TrafficTrace,
    align_generated,
    normalize_reference,
    parse_capture_metadata,
)
from trafficlab.comparison.codec import parse_comparison_result
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.checkpoint import parse_history_csv
from trafficlab.fitting.genetic.types import HistoryRow
from trafficlab.generation.models import BestModel, load_best_model
from trafficlab_dashboard.run_data import ArtifactIdentities, DashboardRun

_SIMILARITY_DEPENDENTS = ("similarity_scores", "multiscale_discrepancy")
_GA_HISTORY_DEPENDENTS = ("ga_fitness_history",)


def _read_artifact_bytes(path: Path, *, artifact_name: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise TrafficlabError(
            f"could not read {artifact_name} artifact {path}: {error}",
            corrective_action=f"verify {artifact_name} exists and is readable",
        ) from error


def _read_artifact_with_identity(path: Path, *, artifact_name: str) -> tuple[bytes, ContentIdentity]:
    content = _read_artifact_bytes(path, artifact_name=artifact_name)
    return content, identify_bytes(content)


def _wrap_required_artifact_error(
    *,
    artifact_name: str,
    source: TrafficlabError,
) -> TrafficlabError:
    return TrafficlabError(
        f"invalid required artifact {artifact_name}: {source}",
        corrective_action=source.corrective_action,
        exit_code=source.exit_code,
        failure_outcomes=source.failure_outcomes,
    )


def _load_required_metadata(directory: Path) -> tuple[CaptureMetadata, ContentIdentity]:
    path = directory / "capture.json"
    try:
        content, identity = _read_artifact_with_identity(path, artifact_name="capture.json")
        metadata = parse_capture_metadata(content, source=path)
    except TrafficlabError as error:
        raise _wrap_required_artifact_error(artifact_name="capture.json", source=error) from error
    return metadata, identity


def _load_required_reference(directory: Path, metadata: CaptureMetadata) -> tuple[TrafficTrace, float, ContentIdentity]:
    path = directory / "reference.pcapng"
    try:
        content, identity = _read_artifact_with_identity(path, artifact_name="reference.pcapng")
        trace = read_pcapng_bytes(content, metadata, source=path)
        normalized, window = normalize_reference(trace)
    except TrafficlabError as error:
        raise _wrap_required_artifact_error(artifact_name="reference.pcapng", source=error) from error
    return normalized, window, identity


def _load_required_generated(
    directory: Path, metadata: CaptureMetadata, window: float
) -> tuple[TrafficTrace, ContentIdentity]:
    path = directory / "generated.pcapng"
    try:
        content, identity = _read_artifact_with_identity(path, artifact_name="generated.pcapng")
        trace = read_pcapng_bytes(content, metadata, source=path)
        aligned = align_generated(trace, window)
    except TrafficlabError as error:
        raise _wrap_required_artifact_error(artifact_name="generated.pcapng", source=error) from error
    return aligned, identity


def _disable(unavailable: dict[str, str], aspect_ids: tuple[str, ...], reason: str) -> None:
    for identifier in aspect_ids:
        unavailable[identifier] = reason


def _identity_mismatch(stored: object, actual: ContentIdentity) -> bool:
    return getattr(stored, "size", None) != actual.size or getattr(stored, "sha256", None) != actual.sha256


def _similarity_lineage_issue(
    similarity: ComparisonResult,
    *,
    capture_identity: ContentIdentity,
    reference_identity: ContentIdentity,
    generated_identity: ContentIdentity,
    window: float,
) -> str | None:
    identities = similarity.input_identities
    if identities is None:
        return "similarity.json has no canonical input identities"
    for field, artifact_name, actual in (
        ("capture_json", "capture.json", capture_identity),
        ("reference_pcapng", "reference.pcapng", reference_identity),
        ("generated_pcapng", "generated.pcapng", generated_identity),
    ):
        if _identity_mismatch(identities[field], actual):
            return f"{artifact_name} content identity does not match the loaded artifact bytes"
    if similarity.observation_window_seconds != window:
        return "observation_window_seconds does not match the loaded reference.pcapng window"
    return None


def _best_model_lineage_issue(
    best_model: BestModel,
    *,
    capture_identity: ContentIdentity,
    reference_identity: ContentIdentity,
    window: float,
) -> str | None:
    if best_model.capture_identity != capture_identity:
        return "capture.json content identity does not match the loaded artifact bytes"
    if best_model.reference_identity != reference_identity:
        return "reference.pcapng content identity does not match the loaded artifact bytes"
    if best_model.observation_window_seconds != window:
        return "observation_window_seconds does not match the loaded reference.pcapng window"
    return None


def _load_optional_similarity(
    directory: Path,
    unavailable: dict[str, str],
    *,
    capture_identity: ContentIdentity,
    reference_identity: ContentIdentity,
    generated_identity: ContentIdentity,
    window: float,
) -> tuple[ComparisonResult | None, str | None]:
    path = directory / "similarity.json"
    if not path.exists():
        _disable(unavailable, _SIMILARITY_DEPENDENTS, "similarity.json is missing")
        return None, None
    try:
        content, identity = _read_artifact_with_identity(path, artifact_name="similarity.json")
        similarity = parse_comparison_result(content)
        issue = _similarity_lineage_issue(
            similarity,
            capture_identity=capture_identity,
            reference_identity=reference_identity,
            generated_identity=generated_identity,
            window=window,
        )
        if issue is not None:
            _disable(
                unavailable,
                _SIMILARITY_DEPENDENTS,
                f"similarity.json is unavailable: foreign artifact: {issue}; rerun compare for this run",
            )
            return None, None
        return similarity, identity.sha256
    except TrafficlabError as error:
        _disable(unavailable, _SIMILARITY_DEPENDENTS, f"similarity.json is unavailable: {error}")
        return None, None
    except ValueError as error:
        _disable(
            unavailable,
            _SIMILARITY_DEPENDENTS,
            f"similarity.json is unavailable: invalid similarity artifact {path}: {error}",
        )
        return None, None


def _load_optional_best_model(
    directory: Path,
    unavailable: dict[str, str],
    *,
    capture_identity: ContentIdentity,
    reference_identity: ContentIdentity,
    window: float,
) -> tuple[BestModel | None, str | None]:
    path = directory / "best_model.json"
    if not path.exists():
        return None, None
    try:
        content, identity = _read_artifact_with_identity(path, artifact_name="best_model.json")
        best_model = load_best_model(content, source=path)
        issue = _best_model_lineage_issue(
            best_model,
            capture_identity=capture_identity,
            reference_identity=reference_identity,
            window=window,
        )
        if issue is not None:
            unavailable["best_model"] = (
                f"best_model.json is unavailable: foreign artifact: {issue}; rerun fit for this run"
            )
            return None, None
        return best_model, identity.sha256
    except TrafficlabError as error:
        unavailable["best_model"] = f"best_model.json is unavailable: {error}; rerun fit for this run"
        return None, None


def _load_optional_experiment(directory: Path) -> tuple[ExperimentConfig | None, str | None, str | None]:
    path = directory / "experiment.toml"
    if not path.exists():
        return None, None, "experiment.toml is missing"
    try:
        content, identity = _read_artifact_with_identity(path, artifact_name="experiment.toml")
        return parse_experiment(content, source=path), identity.sha256, None
    except TrafficlabError as error:
        return None, None, f"experiment.toml is unavailable: {error}"


def _load_optional_history(
    directory: Path,
    experiment: ExperimentConfig | None,
    experiment_issue: str | None,
    unavailable: dict[str, str],
) -> tuple[tuple[HistoryRow, ...] | None, str | None]:
    path = directory / "ga_history.csv"
    if experiment is None:
        _disable(
            unavailable,
            _GA_HISTORY_DEPENDENTS,
            experiment_issue or "ga_history.csv requires a valid experiment.toml",
        )
        return None, None
    if not path.exists():
        _disable(unavailable, _GA_HISTORY_DEPENDENTS, "ga_history.csv is missing")
        return None, None
    try:
        content, identity = _read_artifact_with_identity(path, artifact_name="ga_history.csv")
        rows = parse_history_csv(
            content,
            frozenset(experiment.models.enabled),
            population_size=experiment.genetic.population_size,
            generation_count=experiment.genetic.generation_count,
        )
        return rows, identity.sha256
    except TrafficlabError as error:
        _disable(unavailable, _GA_HISTORY_DEPENDENTS, f"ga_history.csv is unavailable: {error}")
        return None, None
    except ValueError as error:
        _disable(
            unavailable,
            _GA_HISTORY_DEPENDENTS,
            f"ga_history.csv is unavailable: invalid history artifact {path}: {error}",
        )
        return None, None


def load_dashboard_run(directory: Path) -> DashboardRun:
    metadata, capture_identity = _load_required_metadata(directory)
    reference, window, reference_identity = _load_required_reference(directory, metadata)
    generated, generated_identity = _load_required_generated(directory, metadata, window)

    unavailable: dict[str, str] = {}
    similarity, similarity_sha256 = _load_optional_similarity(
        directory,
        unavailable,
        capture_identity=capture_identity,
        reference_identity=reference_identity,
        generated_identity=generated_identity,
        window=window,
    )
    best_model, best_model_sha256 = _load_optional_best_model(
        directory,
        unavailable,
        capture_identity=capture_identity,
        reference_identity=reference_identity,
        window=window,
    )
    experiment, experiment_sha256, experiment_issue = _load_optional_experiment(directory)
    history, history_sha256 = _load_optional_history(directory, experiment, experiment_issue, unavailable)

    return DashboardRun(
        directory=directory,
        identities=ArtifactIdentities(
            reference_sha256=reference_identity.sha256,
            generated_sha256=generated_identity.sha256,
            capture_sha256=capture_identity.sha256,
            similarity_sha256=similarity_sha256,
            best_model_sha256=best_model_sha256,
            history_sha256=history_sha256,
            experiment_sha256=experiment_sha256,
        ),
        metadata=metadata,
        reference=reference,
        generated=generated,
        window=window,
        similarity=similarity,
        best_model=best_model,
        history=history,
        experiment=experiment,
        unavailable=MappingProxyType(unavailable),
    )
