from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from trafficlab.common.compatibility import identify_bytes
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


def _read_artifact_with_sha256(path: Path, *, artifact_name: str) -> tuple[bytes, str]:
    content = _read_artifact_bytes(path, artifact_name=artifact_name)
    return content, identify_bytes(content).sha256


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


def _load_required_metadata(directory: Path) -> tuple[CaptureMetadata, str]:
    path = directory / "capture.json"
    try:
        content, sha256 = _read_artifact_with_sha256(path, artifact_name="capture.json")
        metadata = parse_capture_metadata(content, source=path)
    except TrafficlabError as error:
        raise _wrap_required_artifact_error(artifact_name="capture.json", source=error) from error
    return metadata, sha256


def _load_required_reference(directory: Path, metadata: CaptureMetadata) -> tuple[TrafficTrace, float, str]:
    path = directory / "reference.pcapng"
    try:
        content, sha256 = _read_artifact_with_sha256(path, artifact_name="reference.pcapng")
        trace = read_pcapng_bytes(content, metadata, source=path)
        normalized, window = normalize_reference(trace)
    except TrafficlabError as error:
        raise _wrap_required_artifact_error(artifact_name="reference.pcapng", source=error) from error
    return normalized, window, sha256


def _load_required_generated(directory: Path, metadata: CaptureMetadata, window: float) -> tuple[TrafficTrace, str]:
    path = directory / "generated.pcapng"
    try:
        content, sha256 = _read_artifact_with_sha256(path, artifact_name="generated.pcapng")
        trace = read_pcapng_bytes(content, metadata, source=path)
        aligned = align_generated(trace, window)
    except TrafficlabError as error:
        raise _wrap_required_artifact_error(artifact_name="generated.pcapng", source=error) from error
    return aligned, sha256


def _disable(unavailable: dict[str, str], aspect_ids: tuple[str, ...], reason: str) -> None:
    for identifier in aspect_ids:
        unavailable[identifier] = reason


def _load_optional_similarity(
    directory: Path,
    unavailable: dict[str, str],
) -> tuple[ComparisonResult | None, str | None]:
    path = directory / "similarity.json"
    if not path.exists():
        _disable(unavailable, _SIMILARITY_DEPENDENTS, "similarity.json is missing")
        return None, None
    try:
        content, sha256 = _read_artifact_with_sha256(path, artifact_name="similarity.json")
        return parse_comparison_result(content), sha256
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


def _load_optional_best_model(directory: Path) -> tuple[BestModel | None, str | None]:
    path = directory / "best_model.json"
    if not path.exists():
        return None, None
    try:
        content, sha256 = _read_artifact_with_sha256(path, artifact_name="best_model.json")
        return load_best_model(content, source=path), sha256
    except TrafficlabError:
        return None, None


def _load_optional_experiment(directory: Path) -> tuple[ExperimentConfig | None, str | None, str | None]:
    path = directory / "experiment.toml"
    if not path.exists():
        return None, None, "experiment.toml is missing"
    try:
        content, sha256 = _read_artifact_with_sha256(path, artifact_name="experiment.toml")
        return parse_experiment(content, source=path), sha256, None
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
        content, sha256 = _read_artifact_with_sha256(path, artifact_name="ga_history.csv")
        rows = parse_history_csv(content, frozenset(experiment.models.enabled))
        return rows, sha256
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
    metadata, capture_sha256 = _load_required_metadata(directory)
    reference, window, reference_sha256 = _load_required_reference(directory, metadata)
    generated, generated_sha256 = _load_required_generated(directory, metadata, window)

    unavailable: dict[str, str] = {}
    similarity, similarity_sha256 = _load_optional_similarity(directory, unavailable)
    best_model, best_model_sha256 = _load_optional_best_model(directory)
    experiment, _experiment_sha256, experiment_issue = _load_optional_experiment(directory)
    history, history_sha256 = _load_optional_history(directory, experiment, experiment_issue, unavailable)

    return DashboardRun(
        directory=directory,
        identities=ArtifactIdentities(
            reference_sha256=reference_sha256,
            generated_sha256=generated_sha256,
            capture_sha256=capture_sha256,
            similarity_sha256=similarity_sha256,
            best_model_sha256=best_model_sha256,
            history_sha256=history_sha256,
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
