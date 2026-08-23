import copy
import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.comparison.codec as comparison_codec
import trafficlab.comparison.metrics as comparison_metrics
import trafficlab.comparison.publication as comparison_publication
import trafficlab.comparison.schema as comparison_schema
import trafficlab.comparison.stage as comparison_stage
import trafficlab.generation.stage as generation_stage
from tests.support.comparison import (
    EXPECTED_AGGREGATE_SCORE,
    comparison_log_records,
    prepare_comparison_run,
)
from trafficlab.common.config import ExperimentConfig, SimilarityConfig
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import CaptureMetadata, TraceEvent, TrafficTrace, parse_capture_metadata
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.comparison.similarity.common import SimilarityResult
from trafficlab.comparison.stage import compare_experiment
from trafficlab.generation.models.fitted_model import load_best_model


def _prepare_comparison_run(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    *,
    include_best_model: bool = True,
) -> tuple[Path, Path]:
    """Create one complete offline comparison input tree from checked scientific bytes."""
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / "run"
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / "experiment.toml"
    snapshot = render_effective_config(config)
    experiment_path.write_bytes(snapshot)
    run_directory.mkdir()
    (run_directory / "experiment.toml").write_bytes(snapshot)
    example_data = Path(__file__).parents[3] / "examples" / "data"
    names = ["capture.json", "reference.pcapng"]
    if include_best_model:
        names.append("models/best_model.json")
    for name in names:
        source = example_data / name
        destination = run_directory / source.name
        destination.write_bytes(source.read_bytes())
    if include_best_model:
        _write_current_generated(run_directory)
    return experiment_path, run_directory


def _write_current_generated(run_directory: Path) -> None:
    metadata = parse_capture_metadata(
        (run_directory / "capture.json").read_bytes(), source=run_directory / "capture.json"
    )
    best = load_best_model((run_directory / "best_model.json").read_bytes(), source=run_directory / "best_model.json")
    _, encoded = generation_stage.reproduce_generated_pcapng(best, metadata, clock=lambda: 0.0)
    (run_directory / "generated.pcapng").write_bytes(encoded.content)


def test_compare_public_boundary_classifies_a_missing_capture_input(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A missing comparison input is source evidence, not an infeasible metric result."""
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / "run"
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_bytes(render_effective_config(config))
    run_directory.mkdir()
    (run_directory / "experiment.toml").write_bytes(render_effective_config(config))

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_missing",
        "compare",
        "capture.json",
        "not_published",
    )


def test_compare_public_boundary_classifies_an_unreadable_capture_input(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable comparison source remains preserved artifact evidence rather than a metric failure."""
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / "run"
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / "experiment.toml"
    experiment_path.write_bytes(render_effective_config(config))
    run_directory.mkdir()
    (run_directory / "experiment.toml").write_bytes(render_effective_config(config))
    capture_path = run_directory / "capture.json"
    real_read_bytes = Path.read_bytes

    def unreadable(path: Path) -> bytes:
        if path == capture_path:
            raise PermissionError("injected denied capture")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_corrupt",
        "compare",
        "capture.json",
        "preserved",
    )


def test_compare_public_boundary_rejects_a_foreign_generated_capture_before_publication(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A parseable generated capture must still prove exact derivation from the current fitted model."""
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / "run"
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / "experiment.toml"
    snapshot = render_effective_config(config)
    experiment_path.write_bytes(snapshot)
    run_directory.mkdir()
    (run_directory / "experiment.toml").write_bytes(snapshot)
    example_data = Path(__file__).parents[3] / "examples" / "data"
    for source, destination in (
        (example_data / "capture.json", run_directory / "capture.json"),
        (example_data / "reference.pcapng", run_directory / "reference.pcapng"),
        (example_data / "models" / "best_model.json", run_directory / "best_model.json"),
    ):
        destination.write_bytes(source.read_bytes())
    foreign = (run_directory / "reference.pcapng").read_bytes()
    (run_directory / "generated.pcapng").write_bytes(foreign)

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "affected_evidence": "generated.pcapng",
        "authority": "primary",
        "corrective_action": "regenerate from the current fitted model",
        "detail": "generated.pcapng is foreign",
        "evidence_state": "preserved",
        "kind": "artifact_foreign",
        "stage": "compare",
    }
    assert (run_directory / "generated.pcapng").read_bytes() == foreign
    assert not (run_directory / "similarity.json").exists()
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_compare_requires_best_model_before_similarity_publication(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A trace without its fitted-model provenance is not a reusable comparison input."""
    experiment_path, run_directory = _prepare_comparison_run(valid_config_data, tmp_path, include_best_model=False)

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_missing",
        "compare",
        "best_model.json",
        "not_published",
    )
    assert not (run_directory / "similarity.json").exists()
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_compare_rejects_a_caller_configuration_that_differs_from_the_run_snapshot(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Comparison must not mix a caller configuration with another run's authoritative snapshot."""
    experiment_path, run_directory = _prepare_comparison_run(valid_config_data, tmp_path)
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    cast(dict[str, object], data["run"])["master_seed"] = 54321
    different_snapshot = ExperimentConfig.model_validate(data)
    (run_directory / "experiment.toml").write_bytes(render_effective_config(different_snapshot))

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_foreign",
        "compare",
        "experiment.toml",
        "preserved",
    )
    assert not (run_directory / "similarity.json").exists()


def test_compare_preserves_malformed_capture_metadata_before_model_reconstruction(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Malformed capture metadata remains a captured input corruption rather than model incompatibility."""
    experiment_path, run_directory = _prepare_comparison_run(valid_config_data, tmp_path)
    capture_path = run_directory / "capture.json"
    malformed = b"{}\n"
    capture_path.write_bytes(malformed)

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_corrupt",
        "compare",
        "capture.json",
        "preserved",
    )
    assert capture_path.read_bytes() == malformed
    assert not (run_directory / "similarity.json").exists()


def test_compare_successfully_logs_a_reconstructed_model_bound_result(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """The ordinary success path publishes only after exact model-bound reconstruction succeeds."""
    experiment_path, run_directory = _prepare_comparison_run(valid_config_data, tmp_path)

    result = comparison_stage.compare_experiment(experiment_path)

    assert result == comparison_codec.load_comparison_result(run_directory / "similarity.json")
    records = [json.loads(line) for line in (run_directory / "run.log").read_text(encoding="utf-8").splitlines()]
    assert records[-1]["event"] == "comparison_succeeded"
    assert records[-1]["reused"] is False


def test_compare_preserves_a_published_result_when_success_logging_fails(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-publication log durability cannot recast an already published comparison as absent."""
    experiment_path, run_directory = _prepare_comparison_run(valid_config_data, tmp_path)

    def fail_log(*_args: object, **_kwargs: object) -> None:
        raise TrafficlabError("injected run log failure", corrective_action="repair run.log storage")

    monkeypatch.setattr(comparison_stage, "append_run_log", fail_log)

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "publication_failed",
        "compare",
        "similarity.json",
        "preserved",
    )
    assert (run_directory / "similarity.json").is_file()


def test_compare_classifies_metric_infeasibility_after_model_reconstruction(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A metric source failure remains comparison evidence after provenance is fully validated."""
    experiment_path, run_directory = _prepare_comparison_run(valid_config_data, tmp_path)

    def infeasible(*_args: object, **_kwargs: object) -> ComparisonResult:
        raise TrafficlabError("autocorrelation requires more samples", corrective_action="correct samples or settings")

    monkeypatch.setattr(comparison_stage, "compare_traces", infeasible)

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "kind": "metric_infeasible",
        "stage": "compare",
        "detail": "autocorrelation requires more samples",
        "corrective_action": "correct samples or settings",
        "affected_evidence": "similarity.json",
        "evidence_state": "not_published",
        "authority": "primary",
    }
    assert not (run_directory / "similarity.json").exists()


@pytest.mark.parametrize("field", ("reference_identity", "capture_identity", "final_seed", "final_limits"))
def test_compare_rejects_foreign_best_model_provenance_before_similarity(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    """Coincident generated bytes do not make a foreign model reusable for comparison."""
    experiment_path, run_directory = _prepare_comparison_run(valid_config_data, tmp_path)
    model_path = run_directory / "best_model.json"
    document = cast(dict[str, object], json.loads(model_path.read_bytes()))
    if field in {"reference_identity", "capture_identity"}:
        identity = cast(dict[str, object], document[field])
        identity["sha256"] = "0" * 64
    elif field == "final_seed":
        document[field] = 54322
    else:
        limits = cast(dict[str, object], document[field])
        limits["max_packets"] = 20_001
    model_path.write_bytes((json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    generated_before = (run_directory / "generated.pcapng").read_bytes()

    def prohibit_similarity(*_args: object, **_kwargs: object) -> ComparisonResult:
        pytest.fail("foreign best-model provenance reached similarity evaluation")

    monkeypatch.setattr(comparison_stage, "compare_traces", prohibit_similarity)

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_foreign",
        "compare",
        "best_model.json",
        "preserved",
    )
    assert (run_directory / "generated.pcapng").read_bytes() == generated_before
    assert not (run_directory / "similarity.json").exists()
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_compare_classifies_a_malformed_best_model_as_corrupt_not_schema(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Only a typed schema error is scientific incompatibility; malformed bytes remain corruption evidence."""
    experiment_path, run_directory = _prepare_comparison_run(valid_config_data, tmp_path)
    model_path = run_directory / "best_model.json"
    malformed = b"{\n"
    model_path.write_bytes(malformed)

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_corrupt",
        "compare",
        "best_model.json",
        "preserved",
    )
    assert model_path.read_bytes() == malformed
    assert not (run_directory / "similarity.json").exists()


def test_compare_preserves_a_typed_best_model_schema_incompatibility(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A well-formed legacy schema retains its distinct scientific-semantics classification."""
    experiment_path, run_directory = _prepare_comparison_run(valid_config_data, tmp_path)
    model_path = run_directory / "best_model.json"
    document = cast(dict[str, object], json.loads(model_path.read_bytes()))
    document["scientific_artifact_schema"] = 1
    legacy = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    model_path.write_bytes(legacy)

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "scientific_semantics_incompatible",
        "compare",
        "best_model.json",
        "preserved",
    )
    assert model_path.read_bytes() == legacy
    assert not (run_directory / "similarity.json").exists()


def test_compare_preserves_an_unreadable_best_model_as_corrupt(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A readable-path failure remains best-model corruption rather than a semantic error."""
    experiment_path, run_directory = _prepare_comparison_run(valid_config_data, tmp_path)
    model_path = run_directory / "best_model.json"
    real_read_bytes = Path.read_bytes

    def unreadable(path: Path) -> bytes:
        if path == model_path:
            raise PermissionError("injected denied best model")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "artifact_corrupt",
        "compare",
        "best_model.json",
        "preserved",
    )
    assert not (run_directory / "similarity.json").exists()


def test_compare_classifies_reproduction_failure_without_claiming_schema_incompatibility(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed deterministic reconstruction is generation evidence, not a model-schema claim."""
    experiment_path, run_directory = _prepare_comparison_run(valid_config_data, tmp_path)

    def fail_reproduction(
        *_args: object, **_kwargs: object
    ) -> tuple[tuple[TraceEvent, ...], tuple[TraceEvent, ...], bytes]:
        raise TrafficlabError("final generation exceeded its limit", corrective_action="increase final limits")

    monkeypatch.setattr(comparison_stage, "reproduce_generated_pcapng", fail_reproduction)

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "generation_incomplete",
        "compare",
        "generated.pcapng",
        "not_published",
    )
    assert not (run_directory / "similarity.json").exists()


def test_compare_rejects_reference_mutation_before_similarity_publication(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Comparison cannot publish lineage after one authoritative input changes."""
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / "run"
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / "experiment.toml"
    snapshot = render_effective_config(config)
    experiment_path.write_bytes(snapshot)
    run_directory.mkdir()
    (run_directory / "experiment.toml").write_bytes(snapshot)
    example_data = Path(__file__).parents[3] / "examples" / "data"
    for source, destination in (
        (example_data / "capture.json", run_directory / "capture.json"),
        (example_data / "reference.pcapng", run_directory / "reference.pcapng"),
        (example_data / "models" / "best_model.json", run_directory / "best_model.json"),
        (example_data / "models" / "generated.pcapng", run_directory / "generated.pcapng"),
    ):
        destination.write_bytes(source.read_bytes())
    _write_current_generated(run_directory)
    reference_path = run_directory / "reference.pcapng"
    real_compare = comparison_metrics.compare_traces

    def compare_and_mutate(
        reference: tuple[TraceEvent, ...],
        generated: tuple[TraceEvent, ...],
        W: float,
        settings: SimilarityConfig,
    ) -> ComparisonResult:
        result = real_compare(reference, generated, W, settings)
        reference_path.write_bytes(reference_path.read_bytes() + b"changed after comparison")
        return result

    monkeypatch.setattr(comparison_stage, "compare_traces", compare_and_mutate)

    with pytest.raises(TrafficlabError, match="reference.pcapng changed during compare") as caught:
        comparison_stage.compare_experiment(experiment_path)

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.kind == "artifact_changed"
    assert not (run_directory / "similarity.json").exists()
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_compare_rejects_incompatible_best_model_before_similarity_publication(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Deterministic provenance failure remains owned by the best-model adapter."""
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / "run"
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / "experiment.toml"
    snapshot = render_effective_config(config)
    experiment_path.write_bytes(snapshot)
    run_directory.mkdir()
    (run_directory / "experiment.toml").write_bytes(snapshot)
    example_data = Path(__file__).parents[3] / "examples" / "data"
    for source, destination in (
        (example_data / "capture.json", run_directory / "capture.json"),
        (example_data / "reference.pcapng", run_directory / "reference.pcapng"),
        (example_data / "models" / "generated.pcapng", run_directory / "generated.pcapng"),
    ):
        destination.write_bytes(source.read_bytes())
    (run_directory / "best_model.json").write_bytes(b"{}\n")

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.kind == "scientific_semantics_incompatible"
    assert caught.value.failure_outcome.affected_evidence == "best_model.json"
    assert not (run_directory / "similarity.json").exists()


@pytest.mark.parametrize("input_name", ["reference.pcapng", "generated.pcapng"])
def test_compare_translates_each_pcap_parse_failure_before_publication(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_name: str,
) -> None:
    """Each PCAP input retains its own corruption classification at the public boundary."""
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / "run"
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    config = ExperimentConfig.model_validate(data)
    experiment_path = tmp_path / "experiment.toml"
    snapshot = render_effective_config(config)
    experiment_path.write_bytes(snapshot)
    run_directory.mkdir()
    (run_directory / "experiment.toml").write_bytes(snapshot)
    example_data = Path(__file__).parents[3] / "examples" / "data"
    for source, destination in (
        (example_data / "capture.json", run_directory / "capture.json"),
        (example_data / "reference.pcapng", run_directory / "reference.pcapng"),
        (example_data / "models" / "generated.pcapng", run_directory / "generated.pcapng"),
        (example_data / "models" / "best_model.json", run_directory / "best_model.json"),
    ):
        destination.write_bytes(source.read_bytes())
    _write_current_generated(run_directory)
    real_parse = read_pcapng_bytes

    def fail_selected(content: bytes, metadata: object, *, source: Path) -> TrafficTrace:
        if source.name == input_name:
            raise TrafficlabError("injected parse failure", corrective_action="restore valid PCAPNG bytes")
        return real_parse(content, cast(Any, metadata), source=source)

    monkeypatch.setattr(comparison_stage, "read_pcapng_bytes", fail_selected)

    with pytest.raises(TrafficlabError) as caught:
        comparison_stage.compare_experiment(experiment_path)

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.kind == "artifact_corrupt"
    assert caught.value.failure_outcome.affected_evidence == input_name
    assert not (run_directory / "similarity.json").exists()


def test_compare_experiment_rejects_a_caller_snapshot_mismatch_and_logs_it(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Using caller similarity settings after a run starts would make stored results contradict experiment.toml."""
    caller_path, run_directory, config = prepare_comparison_run(valid_config_data, tmp_path)
    changed = config.model_copy(update={"run": config.run.model_copy(update={"master_seed": 777})})
    caller_path.write_bytes(render_effective_config(changed))

    with pytest.raises(TrafficlabError, match="does not match the authoritative run snapshot"):
        compare_experiment(caller_path)

    assert not (run_directory / "similarity.json").exists()
    assert comparison_log_records(run_directory)[-1] == {
        "detail": f"caller configuration {caller_path} does not match the authoritative run snapshot",
        "event": "comparison_failed",
        "failure_kind": "evaluation_or_input",
        "failure_outcome": {
            "affected_evidence": "experiment.toml",
            "authority": "primary",
            "corrective_action": "use the exact experiment configuration that created this run",
            "detail": f"caller configuration {caller_path} does not match the authoritative run snapshot",
            "evidence_state": "preserved",
            "kind": "artifact_foreign",
            "stage": "compare",
        },
        "stage": "compare",
    }


def test_compare_experiment_rejects_when_input_paths_change_after_evaluation(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cached evaluated bytes cannot authorize publication after their source paths change."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)
    evaluated_sha256: dict[str, str] = {}

    def mutate_after_metadata_read(content: bytes, *, source: Path) -> CaptureMetadata:
        evaluated_sha256["capture_json"] = comparison_codec.sha256_bytes(content)
        source.write_bytes(b"changed metadata after read")
        return parse_capture_metadata(content, source=source)

    def mutate_after_pcapng_read(
        content: bytes,
        metadata: CaptureMetadata,
        *,
        source: Path,
    ) -> TrafficTrace:
        input_name = "reference_pcapng" if source.name == "reference.pcapng" else "generated_pcapng"
        evaluated_sha256[input_name] = comparison_codec.sha256_bytes(content)
        source.write_bytes(f"changed {source.name} after read".encode())
        return read_pcapng_bytes(content, metadata, source=source)

    monkeypatch.setattr(comparison_stage, "parse_capture_metadata", mutate_after_metadata_read)
    monkeypatch.setattr(comparison_stage, "read_pcapng_bytes", mutate_after_pcapng_read)

    with pytest.raises(TrafficlabError, match="capture.json changed during compare") as caught:
        compare_experiment(caller_path)

    assert set(evaluated_sha256) == {"capture_json", "reference_pcapng", "generated_pcapng"}
    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.kind == "artifact_changed"
    assert caught.value.failure_outcome.affected_evidence == "capture.json"
    assert not (run_directory / "similarity.json").exists()


def test_existing_similarity_is_not_replaced_and_publication_failure_is_logged(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Rerunning compare without explicit replacement must preserve a completed artifact byte for byte."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)
    existing = run_directory / "similarity.json"
    existing.write_bytes(b"caller-owned-result\n")

    with pytest.raises(TrafficlabError, match="already exists"):
        compare_experiment(caller_path)

    assert existing.read_bytes() == b"caller-owned-result\n"
    assert comparison_log_records(run_directory)[-1]["failure_kind"] == "publication"
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_existing_identical_similarity_is_reused_and_success_is_logged(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A coordinator retry must reuse the exact completed comparison and record that decision explicitly."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)

    first = compare_experiment(caller_path)
    first_content = (run_directory / "similarity.json").read_bytes()
    second = compare_experiment(caller_path)

    assert second == first
    assert (run_directory / "similarity.json").read_bytes() == first_content
    assert comparison_log_records(run_directory)[-2]["reused"] is False
    assert comparison_log_records(run_directory)[-1] == {
        "aggregate_score": first.aggregate_score,
        "event": "comparison_succeeded",
        "observation_window_seconds": first.observation_window_seconds,
        "path": str(run_directory / "similarity.json"),
        "reused": True,
        "stage": "compare",
    }
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_publication_collision_preserves_the_winner_and_cleans_only_its_temp(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A racing publisher must not be overwritten or deleted when the exclusive link loses."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)
    real_link = os.link
    winner = b"racing-winner\n"

    def collide(source: str | Path, destination: str | Path) -> None:
        destination_path = Path(destination)
        destination_path.write_bytes(winner)
        real_link(source, destination)

    monkeypatch.setattr(comparison_publication.os, "link", collide)

    with pytest.raises(TrafficlabError, match="already exists"):
        compare_experiment(caller_path)

    assert (run_directory / "similarity.json").read_bytes() == winner
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_publication_failure_reports_temp_cleanup_failure_without_removing_unowned_files(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup must stay bounded to the owned temporary name and retain the original publication error."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)
    unowned = run_directory / "unowned.txt"
    unowned.write_text("keep", encoding="utf-8")
    real_unlink = os.unlink
    unlink_attempts: list[Path] = []

    def fail_link(_source: str | Path, _destination: str | Path) -> None:
        raise OSError("injected link failure")

    def fail_temp_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        if Path(path).name.startswith(".similarity.json."):
            unlink_attempts.append(Path(path))
            raise OSError("injected temp cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(comparison_publication.os, "link", fail_link)
    monkeypatch.setattr(comparison_publication.os, "unlink", fail_temp_unlink)

    with pytest.raises(TrafficlabError, match="injected link failure.*cleanup incomplete.*temp cleanup failure"):
        compare_experiment(caller_path)

    assert unowned.read_text(encoding="utf-8") == "keep"
    assert not (run_directory / "similarity.json").exists()
    assert len(unlink_attempts) == 1
    assert len(list(run_directory.glob(".similarity.json.*.tmp"))) == 1


def test_post_link_temp_cleanup_failure_is_attempted_once_and_preserves_a_replacement(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrying cleanup after publication could delete a replacement created at the temporary name."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)
    real_unlink = os.unlink
    unlink_attempts: list[Path] = []
    replacement = b"unowned replacement\n"

    def replace_after_failed_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        path_object = Path(path)
        if path_object.name.startswith(".similarity.json."):
            unlink_attempts.append(path_object)
            if len(unlink_attempts) == 1:
                real_unlink(path, *args, **kwargs)
                path_object.write_bytes(replacement)
                raise OSError("injected post-link cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(comparison_publication.os, "unlink", replace_after_failed_unlink)

    with pytest.raises(
        TrafficlabError,
        match="similarity artifact was published.*owned temporary file cleanup failed.*post-link cleanup failure",
    ):
        compare_experiment(caller_path)

    assert len(unlink_attempts) == 1
    assert unlink_attempts[0].read_bytes() == replacement
    assert (
        comparison_codec.load_comparison_result(run_directory / "similarity.json").aggregate_score
        == EXPECTED_AGGREGATE_SCORE
    )
    assert comparison_log_records(run_directory)[-1]["failure_kind"] == "publication"


def test_valid_but_changed_rendered_result_is_rejected_before_temporary_publication(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema validation alone must not publish valid JSON that differs from the evaluated typed result."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)
    real_render = comparison_codec.render_comparison_result

    def render_different_result(result: comparison_schema.ComparisonResult) -> bytes:
        changed_document = result.as_dict()
        methods = cast(dict[str, object], changed_document["methods"])
        cast(dict[str, object], methods["autocorrelation"])["weight"] = 0.1
        cast(dict[str, object], methods["frame_size_ks"])["weight"] = 0.4
        changed_document["aggregate_score"] = sum(
            cast(float, method["score"]) * cast(float, method["weight"])
            for method in cast(dict[str, dict[str, object]], methods).values()
        )
        return real_render(comparison_schema.ComparisonResult.from_dict(changed_document))

    monkeypatch.setattr(comparison_publication, "render_comparison_result", render_different_result)

    with pytest.raises(TrafficlabError, match="rendered similarity artifact.*canonical evaluated result"):
        compare_experiment(caller_path)

    assert not (run_directory / "similarity.json").exists()
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_numeric_type_tampering_is_rejected_by_canonical_temporary_validation(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typed equality treats diagnostic integer 3 and float 3.0 alike, but their artifact bytes are not equivalent."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)
    real_load = comparison_codec.load_comparison_result

    def tamper_count_type(path: Path) -> comparison_schema.ComparisonResult:
        content = path.read_bytes()
        changed = content.replace(b'"reference_count": 5', b'"reference_count": 5.0', 1)
        assert changed != content
        path.write_bytes(changed)
        return real_load(path)

    monkeypatch.setattr(comparison_publication, "load_comparison_result", tamper_count_type)

    with pytest.raises(TrafficlabError, match="reference_count must be an integer"):
        compare_experiment(caller_path)

    assert not (run_directory / "similarity.json").exists()
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_serialization_failure_before_temp_creation_is_reported_without_cleanup_side_effects(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-write publication failure has no owned file to clean and must preserve adjacent files."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)
    unowned = run_directory / "unowned.txt"
    unowned.write_text("keep", encoding="utf-8")

    def fail_render(_result: comparison_schema.ComparisonResult) -> bytes:
        raise ValueError("injected serialization failure")

    monkeypatch.setattr(comparison_publication, "render_comparison_result", fail_render)

    with pytest.raises(TrafficlabError, match="injected serialization failure"):
        compare_experiment(caller_path)

    assert unowned.read_text(encoding="utf-8") == "keep"
    assert not (run_directory / "similarity.json").exists()
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_input_failure_remains_primary_when_failure_logging_also_fails(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secondary run-log error must not conceal which required comparison input was missing."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)
    (run_directory / "capture.json").unlink()

    def fail_log(_run_directory: Path, _record: object) -> None:
        raise TrafficlabError("injected logging failure", corrective_action="repair logging")

    monkeypatch.setattr(comparison_stage, "append_run_log", fail_log)

    with pytest.raises(TrafficlabError, match="capture metadata.*additionally.*injected logging failure") as error:
        compare_experiment(caller_path)

    assert error.value.corrective_action == "verify capture.json exists and is readable"


def test_comparison_result_assembly_failure_is_translated_and_logged(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid experiment must not leak a raw ValueError when retained component diagnostics break result invariants."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)

    def invalid_window_type(*_args: object) -> SimilarityResult:
        return SimilarityResult(1.0, {"observation_window_seconds": 3})

    monkeypatch.setattr(comparison_metrics, "frame_size_ks", invalid_window_type)

    with pytest.raises(TrafficlabError, match="invalid comparison result") as error:
        compare_experiment(caller_path)

    assert error.value.corrective_action == "report the comparison result assembly defect"
    assert comparison_log_records(run_directory)[-1] == {
        "detail": str(error.value),
        "event": "comparison_failed",
        "failure_kind": "evaluation_or_input",
        "failure_outcome": {
            "affected_evidence": "similarity.json",
            "authority": "primary",
            "corrective_action": error.value.corrective_action,
            "detail": str(error.value),
            "evidence_state": "not_published",
            "kind": "metric_infeasible",
            "stage": "compare",
        },
        "stage": "compare",
    }


def test_success_logging_failure_is_reported_after_the_valid_artifact_is_published(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stage must not claim complete success when its required diagnostic record was not durable."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)

    def fail_log(_run_directory: Path, _record: object) -> None:
        raise TrafficlabError("injected logging failure", corrective_action="repair logging")

    monkeypatch.setattr(comparison_stage, "append_run_log", fail_log)

    with pytest.raises(TrafficlabError, match="comparison result was published.*injected logging failure"):
        compare_experiment(caller_path)

    assert (
        comparison_codec.load_comparison_result(run_directory / "similarity.json").aggregate_score
        == EXPECTED_AGGREGATE_SCORE
    )


def test_missing_authoritative_snapshot_is_logged_as_an_input_failure(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Filename existence must never substitute for loading the authoritative effective configuration."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)
    (run_directory / "experiment.toml").unlink()

    with pytest.raises(TrafficlabError, match="experiment configuration"):
        compare_experiment(caller_path)

    assert comparison_log_records(run_directory)[-1]["failure_kind"] == "evaluation_or_input"


def test_invalid_capture_metadata_aborts_before_parsing_or_publication(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Direction classification must use the run's strict capture metadata rather than a permissive default."""
    caller_path, run_directory, _config = prepare_comparison_run(valid_config_data, tmp_path)
    (run_directory / "capture.json").write_text(
        json.dumps({"interface": "eth0", "target_mac": "02:42:ac:11:00:02", "extra": True}), encoding="utf-8"
    )

    with pytest.raises(TrafficlabError, match="invalid capture metadata"):
        compare_experiment(caller_path)

    assert not (run_directory / "similarity.json").exists()


def test_malformed_caller_toml_cannot_locate_a_run_or_append_a_log(tmp_path: Path) -> None:
    """Before caller configuration is loaded there is no authoritative location at which to report failure."""
    caller_path = tmp_path / "caller.toml"
    caller_path.write_text("[run\n", encoding="utf-8")

    with pytest.raises(TrafficlabError, match="invalid TOML"):
        compare_experiment(caller_path)

    assert not any(tmp_path.rglob("run.log"))
