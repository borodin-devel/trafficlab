import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

import trafficlab.comparison.codec as comparison_codec
import trafficlab.comparison.metrics as comparison_metrics
import trafficlab.comparison.stage as comparison_stage
import trafficlab.generation.stage as generation_stage
from trafficlab.common.config import ExperimentConfig, SimilarityConfig
from trafficlab.common.config_io import render_effective_config
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import TraceEvent, TrafficTrace, parse_capture_metadata
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.generation.models.registry import load_best_model


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
    model_path.write_bytes((json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
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
