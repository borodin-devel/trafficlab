import copy
import json
import math
import os
from collections.abc import Callable
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import pytest

import trafficlab.comparison as comparison
from trafficlab.comparison import (
    ComparisonResult,
    MethodComparison,
    compare_traces,
    parse_comparison_result,
    render_comparison_result,
)
from trafficlab.compatibility import ContentIdentity
from trafficlab.config import ExperimentConfig, SimilarityConfig
from trafficlab.config_io import render_effective_config
from trafficlab.errors import TrafficlabError
from trafficlab.similarity import SimilarityResult
from trafficlab.trace import Direction, TraceEvent


def _settings(data: dict[str, object]) -> SimilarityConfig:
    return ExperimentConfig.model_validate(data).similarity


def _trace() -> tuple[TraceEvent, ...]:
    return (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(1.0, Direction.INBOUND, 80),
        TraceEvent(3.0, Direction.OUTBOUND, 100),
    )


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
    example_data = Path(__file__).parents[2] / "examples" / "data"
    names = ["capture.json", "reference.pcapng", "models/generated.pcapng"]
    if include_best_model:
        names.append("models/best_model.json")
    for name in names:
        source = example_data / name
        destination = run_directory / source.name
        destination.write_bytes(source.read_bytes())
    return experiment_path, run_directory


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
        comparison.compare_experiment(experiment_path)

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
        comparison.compare_experiment(experiment_path)

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
    example_data = Path(__file__).parents[2] / "examples" / "data"
    for source, destination in (
        (example_data / "capture.json", run_directory / "capture.json"),
        (example_data / "reference.pcapng", run_directory / "reference.pcapng"),
        (example_data / "models" / "best_model.json", run_directory / "best_model.json"),
    ):
        destination.write_bytes(source.read_bytes())
    foreign = (run_directory / "reference.pcapng").read_bytes()
    (run_directory / "generated.pcapng").write_bytes(foreign)

    with pytest.raises(TrafficlabError) as caught:
        comparison.compare_experiment(experiment_path)

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
        comparison.compare_experiment(experiment_path)

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
        comparison.compare_experiment(experiment_path)

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
        comparison.compare_experiment(experiment_path)

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

    result = comparison.compare_experiment(experiment_path)

    assert result == comparison.load_comparison_result(run_directory / "similarity.json")
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

    monkeypatch.setattr(comparison, "append_run_log", fail_log)

    with pytest.raises(TrafficlabError) as caught:
        comparison.compare_experiment(experiment_path)

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

    monkeypatch.setattr(comparison, "compare_traces", infeasible)

    with pytest.raises(TrafficlabError) as caught:
        comparison.compare_experiment(experiment_path)

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

    monkeypatch.setattr(comparison, "compare_traces", prohibit_similarity)

    with pytest.raises(TrafficlabError) as caught:
        comparison.compare_experiment(experiment_path)

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
        comparison.compare_experiment(experiment_path)

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
        comparison.compare_experiment(experiment_path)

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
        comparison.compare_experiment(experiment_path)

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

    monkeypatch.setattr(comparison, "reproduce_generated_pcapng", fail_reproduction)

    with pytest.raises(TrafficlabError) as caught:
        comparison.compare_experiment(experiment_path)

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
    example_data = Path(__file__).parents[2] / "examples" / "data"
    for source, destination in (
        (example_data / "capture.json", run_directory / "capture.json"),
        (example_data / "reference.pcapng", run_directory / "reference.pcapng"),
        (example_data / "models" / "best_model.json", run_directory / "best_model.json"),
        (example_data / "models" / "generated.pcapng", run_directory / "generated.pcapng"),
    ):
        destination.write_bytes(source.read_bytes())
    reference_path = run_directory / "reference.pcapng"
    real_compare = comparison.compare_traces

    def compare_and_mutate(
        reference: tuple[TraceEvent, ...],
        generated: tuple[TraceEvent, ...],
        W: float,
        settings: SimilarityConfig,
    ) -> ComparisonResult:
        result = real_compare(reference, generated, W, settings)
        reference_path.write_bytes(reference_path.read_bytes() + b"changed after comparison")
        return result

    monkeypatch.setattr(comparison, "compare_traces", compare_and_mutate)

    with pytest.raises(TrafficlabError, match="reference.pcapng changed during compare") as caught:
        comparison.compare_experiment(experiment_path)

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
    example_data = Path(__file__).parents[2] / "examples" / "data"
    for source, destination in (
        (example_data / "capture.json", run_directory / "capture.json"),
        (example_data / "reference.pcapng", run_directory / "reference.pcapng"),
        (example_data / "models" / "generated.pcapng", run_directory / "generated.pcapng"),
    ):
        destination.write_bytes(source.read_bytes())
    (run_directory / "best_model.json").write_bytes(b"{}\n")

    with pytest.raises(TrafficlabError) as caught:
        comparison.compare_experiment(experiment_path)

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
    example_data = Path(__file__).parents[2] / "examples" / "data"
    for source, destination in (
        (example_data / "capture.json", run_directory / "capture.json"),
        (example_data / "reference.pcapng", run_directory / "reference.pcapng"),
        (example_data / "models" / "generated.pcapng", run_directory / "generated.pcapng"),
        (example_data / "models" / "best_model.json", run_directory / "best_model.json"),
    ):
        destination.write_bytes(source.read_bytes())
    real_parse = comparison.parse_pcapng_bytes

    def fail_selected(content: bytes, metadata: object, *, source: Path) -> tuple[TraceEvent, ...]:
        if source.name == input_name:
            raise TrafficlabError("injected parse failure", corrective_action="restore valid PCAPNG bytes")
        return real_parse(content, cast(Any, metadata), source=source)

    monkeypatch.setattr(comparison, "parse_pcapng_bytes", fail_selected)

    with pytest.raises(TrafficlabError) as caught:
        comparison.compare_experiment(experiment_path)

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.kind == "artifact_corrupt"
    assert caught.value.failure_outcome.affected_evidence == input_name
    assert not (run_directory / "similarity.json").exists()


def _add_top_level_field(document: dict[str, object]) -> None:
    document["unexpected"] = 1


def _remove_iat_method(document: dict[str, object]) -> None:
    cast(dict[str, object], document["methods"]).pop("iat_ks")


def _shorten_capture_hash(document: dict[str, object]) -> None:
    inputs = cast(dict[str, object], document["input_identities"])
    cast(dict[str, object], inputs["capture_json"])["sha256"] = "short"


def _add_method_field(document: dict[str, object]) -> None:
    methods = cast(dict[str, object], document["methods"])
    cast(dict[str, object], methods["iat_ks"])["unknown"] = 1


def _make_window_an_integer(document: dict[str, object]) -> None:
    document["observation_window_seconds"] = 3


def _change_diagnostic_window(document: dict[str, object]) -> None:
    methods = cast(dict[str, object], document["methods"])
    method = cast(dict[str, object], methods["iat_ks"])
    cast(dict[str, object], method["diagnostics"])["observation_window_seconds"] = 4.0


def _change_method_weight(document: dict[str, object]) -> None:
    methods = cast(dict[str, object], document["methods"])
    cast(dict[str, object], methods["iat_ks"])["weight"] = 0.5


def _change_aggregate(document: dict[str, object]) -> None:
    document["aggregate_score"] = 0.75


def _remove_input_identity(document: dict[str, object]) -> None:
    cast(dict[str, object], document["input_identities"]).pop("capture_json")


def _make_input_identity_non_string(document: dict[str, object]) -> None:
    cast(dict[str, object], document["input_identities"])["capture_json"] = 1


def _make_input_identity_size_boolean(document: dict[str, object]) -> None:
    inputs = cast(dict[str, object], document["input_identities"])
    cast(dict[str, object], inputs["capture_json"])["size"] = True


def _add_input_identity_field(document: dict[str, object]) -> None:
    inputs = cast(dict[str, object], document["input_identities"])
    cast(dict[str, object], inputs["capture_json"])["path"] = "capture.json"


def _valid_result_document() -> dict[str, object]:
    fixture = Path(__file__).parents[2] / "examples" / "data" / "similarity.json"
    return cast(dict[str, object], json.loads(fixture.read_bytes()))


def _valid_result() -> ComparisonResult:
    return ComparisonResult.from_dict(_valid_result_document())


def test_similarity_artifact_retains_exact_nested_input_content_identities() -> None:
    """Digest-only lineage cannot prove the authoritative bytes used by comparison."""
    document = _valid_result_document()
    identities = cast(dict[str, object], document["input_identities"])

    assert tuple(identities) == (
        "capture_json",
        "generated_pcapng",
        "reference_pcapng",
        "similarity_settings",
    )
    assert all(set(cast(dict[str, object], value)) == {"size", "sha256"} for value in identities.values())
    assert "input_sha256" not in document


def _method_document(document: dict[str, object], method_name: str) -> tuple[dict[str, object], dict[str, object]]:
    methods = cast(dict[str, object], document["methods"])
    method = cast(dict[str, object], methods[method_name])
    return method, cast(dict[str, object], method["diagnostics"])


def _corrupt_method_diagnostics(document: dict[str, object], method_name: str, corruption: str) -> None:
    method, diagnostics = _method_document(document, method_name)
    discrepancy_name = "distance" if method_name in ("frame_size_ks", "iat_ks") else "discrepancy"
    if corruption == "missing":
        diagnostics.pop(discrepancy_name)
    elif corruption == "extra":
        diagnostics["unexpected"] = 0
    elif corruption == "nonfinite":
        diagnostics[discrepancy_name] = math.nan
    elif corruption == "bool-alias":
        if method_name == "frame_size_ks":
            diagnostics["reference_count"] = True
        elif method_name == "iat_ks":
            diagnostics["reference_iat_count"] = True
        elif method_name == "autocorrelation":
            cast(list[object], diagnostics["lags"])[0] = True
        else:
            diagnostics["total_direction_bin_cells"] = True
    elif corruption == "int-alias":
        if method_name in ("frame_size_ks", "iat_ks"):
            diagnostics["distance"] = 0
        elif method_name == "autocorrelation":
            diagnostics["discrepancy"] = 0
        else:
            cast(list[object], diagnostics["widths"])[0] = 1
    elif corruption == "out-of-range":
        if method_name in ("frame_size_ks", "iat_ks"):
            diagnostics["distance"] = 1.1
        elif method_name == "autocorrelation":
            feature = cast(dict[str, object], diagnostics["iat"])
            cast(list[object], feature["reference_acf"])[0] = 1.1
        else:
            features = cast(dict[str, object], diagnostics["feature_discrepancies"])
            features["packet"] = 1.1
    elif corruption == "inconsistent-count":
        if method_name == "frame_size_ks":
            diagnostics["reference_minimum_length"] = 141
        elif method_name == "iat_ks":
            diagnostics["reference_zero_iat_count"] = 5
        elif method_name == "autocorrelation":
            cast(dict[str, object], diagnostics["iat"])["reference_sample_count"] = 1
        else:
            diagnostics["total_direction_bin_cells"] = 221
    elif corruption == "inconsistent-length":
        if method_name == "frame_size_ks":
            diagnostics["reference_count"] = 0
        elif method_name == "iat_ks":
            diagnostics["generated_iat_count"] = 0
        elif method_name == "autocorrelation":
            cast(list[object], cast(dict[str, object], diagnostics["size"])["generated_acf"]).pop()
        else:
            cast(list[object], diagnostics["scale_discrepancies"]).pop()
    elif corruption == "score-discrepancy":
        method["score"] = 0.123
    elif corruption == "internal-discrepancy":
        if method_name in ("frame_size_ks", "iat_ks"):
            diagnostics["distance"] = 0.123
        elif method_name == "autocorrelation":
            diagnostics["discrepancy"] = 0.123
        else:
            diagnostics["discrepancy"] = 0.123
    else:
        raise AssertionError(f"unknown corruption {corruption}")


def _remove_acf_vector(diagnostics: dict[str, object]) -> object:
    return cast(dict[str, object], diagnostics["iat"]).pop("generated_acf")


def _add_acf_feature_weight(diagnostics: dict[str, object]) -> None:
    cast(dict[str, object], diagnostics["feature_weights"])["unexpected"] = 0.0


def _remove_multiscale_packet_totals(diagnostics: dict[str, object]) -> object:
    scale = cast(dict[str, object], cast(list[object], diagnostics["scales"])[0])
    return cast(dict[str, object], scale["reference_totals"]).pop("packet")


def _add_multiscale_scale_feature(diagnostics: dict[str, object]) -> None:
    scale = cast(dict[str, object], cast(list[object], diagnostics["scales"])[0])
    cast(dict[str, object], scale["feature_discrepancies"])["unexpected"] = 0.0


def test_compare_traces_uses_every_setting_and_retains_exact_component_results(
    valid_config_data: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping a method, its weight, its settings, or diagnostics would change the configured comparison."""
    data = copy.deepcopy(valid_config_data)
    similarity_data = cast(dict[str, object], data["similarity"])
    similarity_data["method_weights"] = {
        "frame_size_ks": 0.1,
        "iat_ks": 0.2,
        "autocorrelation": 0.3,
        "multiscale_rate": 0.4,
    }
    settings = _settings(data)
    reference = _trace()
    generated = _trace()
    window = 3.0
    calls: list[tuple[str, tuple[object, ...]]] = []

    def frame_size(reference_arg: object, generated_arg: object, window_arg: object) -> SimilarityResult:
        calls.append(("frame_size_ks", (reference_arg, generated_arg, window_arg)))
        return SimilarityResult(0.2, {"method": "frame", "observation_window_seconds": 3.0})

    def iat(reference_arg: object, generated_arg: object, window_arg: object, quantile: object) -> SimilarityResult:
        calls.append(("iat_ks", (reference_arg, generated_arg, window_arg, quantile)))
        return SimilarityResult(0.4, {"method": "iat", "observation_window_seconds": 3.0})

    def acf(*args: object) -> SimilarityResult:
        calls.append(("autocorrelation", args))
        return SimilarityResult(0.6, {"method": "acf", "observation_window_seconds": 3.0})

    def multiscale(*args: object) -> SimilarityResult:
        calls.append(("multiscale_rate", args))
        return SimilarityResult(0.8, {"method": "multiscale", "observation_window_seconds": 3.0})

    monkeypatch.setattr(comparison, "frame_size_ks", frame_size)
    monkeypatch.setattr(comparison, "iat_ks", iat)
    monkeypatch.setattr(comparison, "autocorrelation_similarity", acf)
    monkeypatch.setattr(comparison, "multiscale_rate_similarity", multiscale)

    result = compare_traces(reference, generated, window, settings)

    assert isinstance(result, ComparisonResult)
    assert result.aggregate_score == pytest.approx(0.6)
    assert result.observation_window_seconds == 3.0
    assert result.input_sha256 is None
    assert tuple(result.methods) == ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")
    assert {name: method.score for name, method in result.methods.items()} == {
        "autocorrelation": 0.6,
        "frame_size_ks": 0.2,
        "iat_ks": 0.4,
        "multiscale_rate": 0.8,
    }
    assert {name: method.weight for name, method in result.methods.items()} == {
        "autocorrelation": 0.3,
        "frame_size_ks": 0.1,
        "iat_ks": 0.2,
        "multiscale_rate": 0.4,
    }
    assert result.methods["autocorrelation"].diagnostics["method"] == "acf"
    assert calls == [
        ("frame_size_ks", (reference, generated, 3.0)),
        ("iat_ks", (reference, generated, 3.0, 0.95)),
        ("autocorrelation", (reference, generated, 3.0, (1,), (1.0,), 0.5, 0.5)),
        (
            "multiscale_rate",
            (reference, generated, 3.0, (0.1, 1.0), (0.5, 0.5), 0.5, 0.5, 100_000),
        ),
    ]


@pytest.mark.parametrize(
    "method_weights",
    [
        {"frame_size_ks": 1.0, "iat_ks": 0.0, "autocorrelation": 0.0, "multiscale_rate": 0.0},
        {"frame_size_ks": 0.0, "iat_ks": 1.0, "autocorrelation": 0.0, "multiscale_rate": 0.0},
        {"frame_size_ks": 0.0, "iat_ks": 0.0, "autocorrelation": 1.0, "multiscale_rate": 0.0},
        {"frame_size_ks": 0.0, "iat_ks": 0.0, "autocorrelation": 0.0, "multiscale_rate": 1.0},
        {"frame_size_ks": 0.1, "iat_ks": 0.2, "autocorrelation": 0.3, "multiscale_rate": 0.4},
        {"frame_size_ks": 0.0, "iat_ks": 0.5, "autocorrelation": 0.5, "multiscale_rate": 0.0},
    ],
)
def test_compare_traces_eagerly_retains_all_four_methods_for_every_weight_case(
    valid_config_data: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    method_weights: dict[str, float],
) -> None:
    """Aggregation weights must never choose which mandatory comparisons execute or appear in the artifact."""
    data = copy.deepcopy(valid_config_data)
    cast(dict[str, object], data["similarity"])["method_weights"] = method_weights
    scores = {"frame_size_ks": 0.2, "iat_ks": 0.4, "autocorrelation": 0.6, "multiscale_rate": 0.8}
    calls: list[str] = []

    def component(name: str) -> Callable[..., SimilarityResult]:
        def evaluate(*_args: object) -> SimilarityResult:
            calls.append(name)
            return SimilarityResult(scores[name], {"component": name, "observation_window_seconds": 3.0})

        return evaluate

    monkeypatch.setattr(comparison, "frame_size_ks", component("frame_size_ks"))
    monkeypatch.setattr(comparison, "iat_ks", component("iat_ks"))
    monkeypatch.setattr(comparison, "autocorrelation_similarity", component("autocorrelation"))
    monkeypatch.setattr(comparison, "multiscale_rate_similarity", component("multiscale_rate"))

    result = compare_traces(_trace(), _trace(), 3.0, _settings(data))

    assert calls == ["frame_size_ks", "iat_ks", "autocorrelation", "multiscale_rate"]
    assert tuple(result.methods) == ("autocorrelation", "frame_size_ks", "iat_ks", "multiscale_rate")
    assert {name: method.diagnostics["component"] for name, method in result.methods.items()} == {
        name: name for name in result.methods
    }
    expected_aggregate = math.fsum(method_weights[name] * scores[name] for name in scores)
    assert result.aggregate_score == expected_aggregate
    if 1.0 in method_weights.values():
        selected_method = next(name for name, weight in method_weights.items() if weight == 1.0)
        assert result.aggregate_score == scores[selected_method]
    artifact = result.with_input_identities(
        {
            "capture_json": ContentIdentity(size=1, sha256="a" * 64),
            "generated_pcapng": ContentIdentity(size=2, sha256="b" * 64),
            "reference_pcapng": ContentIdentity(size=3, sha256="c" * 64),
            "similarity_settings": ContentIdentity(size=4, sha256="d" * 64),
        }
    ).as_dict()
    assert tuple(artifact) == ("aggregate_score", "input_identities", "methods", "observation_window_seconds")
    methods_document = cast(dict[str, dict[str, object]], artifact["methods"])
    assert all(tuple(method) == ("diagnostics", "score", "weight") for method in methods_document.values())


@pytest.mark.parametrize(
    ("zero_weight_method", "component_name"),
    [
        ("frame_size_ks", "frame_size_ks"),
        ("iat_ks", "iat_ks"),
        ("autocorrelation", "autocorrelation_similarity"),
        ("multiscale_rate", "multiscale_rate_similarity"),
    ],
)
def test_compare_traces_propagates_each_zero_weight_component_failure(
    valid_config_data: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    zero_weight_method: str,
    component_name: str,
) -> None:
    """A failed zero-weight component is evidence failure, not a score that can be ignored."""
    data = copy.deepcopy(valid_config_data)
    weights = {name: 0.0 for name in ("frame_size_ks", "iat_ks", "autocorrelation", "multiscale_rate")}
    weights[next(name for name in weights if name != zero_weight_method)] = 1.0
    cast(dict[str, object], data["similarity"])["method_weights"] = weights
    failure = TrafficlabError(f"{zero_weight_method} failed", corrective_action="repair the component")

    def fail(*_args: object) -> SimilarityResult:
        raise failure

    monkeypatch.setattr(comparison, component_name, fail)

    with pytest.raises(TrafficlabError) as captured:
        compare_traces(_trace(), _trace(), 3.0, _settings(data))

    assert captured.value is failure


def test_compare_traces_propagates_a_component_failure(
    valid_config_data: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Converting evaluator failure into a low score would disguise broken infrastructure as poor science."""
    primary = TrafficlabError("injected evaluator failure", corrective_action="repair the evaluator")

    def fail_metric(*_args: object) -> SimilarityResult:
        raise primary

    monkeypatch.setattr(comparison, "iat_ks", fail_metric)

    with pytest.raises(TrafficlabError) as error:
        compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))

    assert error.value is primary


def test_compare_traces_translates_invalid_component_result_assembly(
    valid_config_data: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A result-invariant defect must use the comparison stage's corrective package error boundary."""

    def invalid_window_type(*_args: object) -> SimilarityResult:
        return SimilarityResult(1.0, {"observation_window_seconds": 3})

    monkeypatch.setattr(comparison, "frame_size_ks", invalid_window_type)

    with pytest.raises(TrafficlabError, match="invalid comparison result") as error:
        compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))

    assert error.value.corrective_action == "report the comparison result assembly defect"


def test_compare_traces_clamps_only_accepted_weight_sum_roundoff(
    valid_config_data: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid configuration within sum tolerance must not leak a raw aggregate range ValueError."""
    data = copy.deepcopy(valid_config_data)
    similarity_data = cast(dict[str, object], data["similarity"])
    similarity_data["method_weights"] = {
        "frame_size_ks": 0.25,
        "iat_ks": 0.25,
        "autocorrelation": 0.25,
        "multiscale_rate": 0.2500000000005,
    }

    def identical(*_args: object) -> SimilarityResult:
        return SimilarityResult(1.0, {"observation_window_seconds": 3.0})

    monkeypatch.setattr(comparison, "frame_size_ks", identical)
    monkeypatch.setattr(comparison, "iat_ks", identical)
    monkeypatch.setattr(comparison, "autocorrelation_similarity", identical)
    monkeypatch.setattr(comparison, "multiscale_rate_similarity", identical)

    result = compare_traces(_trace(), _trace(), 3.0, _settings(data))

    assert result.aggregate_score == 1.0


@pytest.mark.parametrize("score", [-0.01, 1.01, math.inf, math.nan])
def test_comparison_result_rejects_an_unbounded_or_nonfinite_aggregate(score: float) -> None:
    """An invalid aggregate must never cross the typed artifact boundary."""
    with pytest.raises(ValueError, match="aggregate_score"):
        ComparisonResult(score, 3.0, {}, None)


def test_comparison_result_is_deeply_immutable(valid_config_data: dict[str, object]) -> None:
    """Mutation after evaluation could make the reported aggregate disagree with retained diagnostics."""
    result = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))

    with pytest.raises(TypeError):
        result.methods["frame_size_ks"] = result.methods["iat_ks"]  # type: ignore[index]
    assert isinstance(result.methods, MappingProxyType)
    with pytest.raises(TypeError):
        result.methods["frame_size_ks"].diagnostics["distance"] = 99.0  # type: ignore[index]


def test_comparison_result_requires_exact_method_names(valid_config_data: dict[str, object]) -> None:
    """A missing method must not cross the typed result boundary even when remaining methods are valid."""
    result = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))
    incomplete = dict(result.methods)
    incomplete.pop("iat_ks")

    with pytest.raises(ValueError, match="methods must contain exactly"):
        ComparisonResult(result.aggregate_score, 3.0, incomplete, None)


def test_comparison_result_requires_typed_method_values(valid_config_data: dict[str, object]) -> None:
    """A mapping-shaped substitute must not bypass method score, weight, and diagnostic validation."""
    result = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))
    invalid_methods: dict[str, object] = dict(result.methods)
    invalid_methods["iat_ks"] = {"score": 1.0, "weight": 0.25, "diagnostics": {}}

    with pytest.raises(ValueError, match="every method must be a MethodComparison"):
        ComparisonResult(result.aggregate_score, 3.0, invalid_methods, None)  # type: ignore[arg-type]


@pytest.mark.parametrize("diagnostic_window", [0.0, math.nan], ids=["nonpositive", "nonfinite"])
def test_comparison_result_defensively_rejects_corrupted_method_windows(
    valid_config_data: dict[str, object], diagnostic_window: float
) -> None:
    """A corrupted in-memory method object must not bypass the finite positive diagnostic-W invariant."""
    result = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))
    original = result.methods["autocorrelation"]
    corrupted = object.__new__(MethodComparison)
    object.__setattr__(corrupted, "score", original.score)
    object.__setattr__(corrupted, "weight", original.weight)
    object.__setattr__(
        corrupted,
        "diagnostics",
        MappingProxyType({"observation_window_seconds": diagnostic_window}),
    )
    methods = dict(result.methods)
    methods["autocorrelation"] = corrupted

    with pytest.raises(ValueError, match="diagnostic.*finite positive float"):
        ComparisonResult(result.aggregate_score, 3.0, methods, None)


@pytest.mark.parametrize(
    "identities",
    [
        {
            "generated_pcapng": ContentIdentity(size=2, sha256="b" * 64),
            "reference_pcapng": ContentIdentity(size=3, sha256="c" * 64),
            "similarity_settings": ContentIdentity(size=4, sha256="d" * 64),
        },
        {
            "capture_json": 1,
            "generated_pcapng": ContentIdentity(size=2, sha256="b" * 64),
            "reference_pcapng": ContentIdentity(size=3, sha256="c" * 64),
            "similarity_settings": ContentIdentity(size=4, sha256="d" * 64),
        },
    ],
    ids=["missing-input", "non-string-input"],
)
def test_comparison_result_defensively_rejects_invalid_identity_mappings(
    valid_config_data: dict[str, object], identities: dict[str, object]
) -> None:
    """Direct typed-result construction must enforce the complete lowercase SHA-256 identity map."""
    result = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))

    with pytest.raises(ValueError, match="input_identities"):
        ComparisonResult(result.aggregate_score, 3.0, result.methods, identities)  # type: ignore[arg-type]


def test_strict_result_json_round_trip_has_the_documented_sorted_compact_shape() -> None:
    """Permissive or unstable JSON would make similarity identity and reuse ambiguous."""
    document = _valid_result_document()

    result = ComparisonResult.from_dict(document)
    rendered = render_comparison_result(result)

    assert rendered == (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert parse_comparison_result(rendered) == result
    assert result.as_dict() == document
    assert result.as_dict() is not result.as_dict()


@pytest.mark.parametrize(
    "method_name",
    ["frame_size_ks", "iat_ks", "autocorrelation", "multiscale_rate"],
)
@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "extra",
        "nonfinite",
        "bool-alias",
        "int-alias",
        "out-of-range",
        "inconsistent-count",
        "inconsistent-length",
        "score-discrepancy",
        "internal-discrepancy",
    ],
)
def test_result_parser_rejects_method_specific_diagnostic_corruption(
    method_name: str,
    corruption: str,
) -> None:
    """Each retained method shape must reject structural, scalar, range, and mathematical drift."""
    document = _valid_result_document()
    _corrupt_method_diagnostics(document, method_name, corruption)

    with pytest.raises(ValueError):
        ComparisonResult.from_dict(document)


@pytest.mark.parametrize(
    ("method_name", "_nested_name", "mutation"),
    [
        ("autocorrelation", "missing-acf-feature-key", _remove_acf_vector),
        ("autocorrelation", "extra-feature-weight-key", _add_acf_feature_weight),
        ("multiscale_rate", "missing-direction-total-key", _remove_multiscale_packet_totals),
        ("multiscale_rate", "extra-scale-feature-key", _add_multiscale_scale_feature),
    ],
)
def test_result_parser_rejects_nested_diagnostic_schema_drift(
    method_name: str,
    _nested_name: str,
    mutation: Callable[[dict[str, object]], object],
) -> None:
    """Nested diagnostic objects must preserve their documented exact key sets."""
    document = _valid_result_document()
    _method, diagnostics = _method_document(document, method_name)
    mutation(diagnostics)

    with pytest.raises(ValueError):
        ComparisonResult.from_dict(document)


@pytest.mark.parametrize(
    ("method_name", "weight_name"),
    [
        ("autocorrelation", "lag_weights"),
        ("autocorrelation", "feature_weights"),
        ("multiscale_rate", "scale_weights"),
        ("multiscale_rate", "feature_weights"),
    ],
)
def test_result_parser_rejects_unnormalized_diagnostic_weights(method_name: str, weight_name: str) -> None:
    """Every configured diagnostic weight vector must remain normalized within the shared tolerance."""
    document = _valid_result_document()
    _method, diagnostics = _method_document(document, method_name)
    weights = diagnostics[weight_name]
    if type(weights) is list:
        cast(list[object], weights)[0] = 0.75
    else:
        weight_document = cast(dict[str, object], weights)
        weight_document[next(iter(weight_document))] = 0.75

    with pytest.raises(ValueError, match="weights.*sum to one"):
        ComparisonResult.from_dict(document)


def test_result_parser_rejects_a_subnormal_multiscale_width_without_leaking_arithmetic_errors() -> None:
    """A finite positive width whose W quotient overflows must remain an ordinary artifact validation error."""
    document = _valid_result_document()
    _method, diagnostics = _method_document(document, "multiscale_rate")
    cast(list[object], diagnostics["widths"])[0] = 5e-324

    with pytest.raises(ValueError, match="W divided by a width must be finite"):
        ComparisonResult.from_dict(document)


def test_method_parser_rejects_an_unknown_dispatch_name() -> None:
    """An unrecognized name must not borrow another method's diagnostic contract."""
    document = _valid_result_document()
    method, _diagnostics = _method_document(document, "frame_size_ks")

    with pytest.raises(ValueError, match="unsupported comparison method"):
        MethodComparison.from_dict("unknown", method)


@pytest.mark.parametrize(
    "mutation",
    [
        _add_top_level_field,
        _remove_iat_method,
        _shorten_capture_hash,
        _add_method_field,
        _make_window_an_integer,
    ],
    ids=["unknown-top-level", "missing-method", "bad-hash", "unknown-method-field", "strict-window-float"],
)
def test_result_parser_rejects_schema_drift(mutation: Callable[[dict[str, object]], None]) -> None:
    """Accepting incomplete, extra, or coerced values would trust an artifact with a different meaning."""
    document = _valid_result_document()
    mutation(document)

    with pytest.raises(ValueError):
        ComparisonResult.from_dict(document)


@pytest.mark.parametrize(
    "mutation",
    [
        _change_diagnostic_window,
        _change_method_weight,
        _change_aggregate,
        _remove_input_identity,
        _make_input_identity_non_string,
        _make_input_identity_size_boolean,
        _add_input_identity_field,
    ],
    ids=[
        "different-window",
        "unnormalized-weights",
        "wrong-aggregate",
        "missing-identity",
        "non-object-identity",
        "boolean-identity-size",
        "extra-identity-field",
    ],
)
def test_result_parser_rejects_semantic_drift(mutation: Callable[[dict[str, object]], None]) -> None:
    """A structurally valid document must still preserve shared-W, weighting, and identity invariants."""
    document = _valid_result_document()
    mutation(document)

    with pytest.raises(ValueError):
        ComparisonResult.from_dict(document)


@pytest.mark.parametrize(
    ("window", "diagnostic_window"),
    [(3.0, 3), (1.0, True)],
    ids=["integer-equal-to-float", "boolean-equal-to-one"],
)
def test_result_parser_requires_diagnostic_windows_to_be_floats(window: float, diagnostic_window: int | bool) -> None:
    """Python numeric equality must not let integer or boolean diagnostic W impersonate the shared float W."""
    document = _valid_result_document()
    document["observation_window_seconds"] = window
    methods = cast(dict[str, object], document["methods"])
    for method in methods.values():
        diagnostics = cast(dict[str, object], cast(dict[str, object], method)["diagnostics"])
        diagnostics["observation_window_seconds"] = window
    first = cast(dict[str, object], methods["autocorrelation"])
    cast(dict[str, object], first["diagnostics"])["observation_window_seconds"] = diagnostic_window

    with pytest.raises(ValueError, match="diagnostic.*finite positive float"):
        ComparisonResult.from_dict(document)


def test_result_parser_rejects_duplicate_json_keys() -> None:
    """Last-value-wins duplicate keys would make validation dependent on the JSON decoder."""
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_comparison_result(b'{"aggregate_score":0.0,"aggregate_score":1.0}')


@pytest.mark.parametrize("content", [b"{", b"\xff"], ids=["malformed-json", "invalid-utf8"])
def test_result_parser_rejects_invalid_json_bytes(content: bytes) -> None:
    """Decoder failures must not escape the strict typed artifact boundary."""
    with pytest.raises(ValueError, match="invalid similarity JSON"):
        parse_comparison_result(content)


@pytest.mark.parametrize("content", [None, b"{}"], ids=["missing", "malformed"])
def test_result_loader_reports_missing_or_malformed_artifacts(content: bytes | None, tmp_path: Path) -> None:
    """File and schema failures need the package's corrective error boundary."""
    path = tmp_path / "similarity.json"
    if content is not None:
        path.write_bytes(content)

    with pytest.raises(TrafficlabError, match="similarity artifact") as error:
        comparison.load_comparison_result(path)

    assert error.value.corrective_action


def test_result_serializer_requires_file_identities(valid_config_data: dict[str, object]) -> None:
    """Publishing an aggregation-only result would omit the artifact inputs needed for reproduction."""
    result = compare_traces(_trace(), _trace(), 3.0, _settings(valid_config_data))

    with pytest.raises(ValueError, match="input content identities"):
        render_comparison_result(result)


def test_publication_reports_creation_instead_of_reuse_for_an_absent_destination(tmp_path: Path) -> None:
    """Returning no ownership state would make retry logging claim a newly created result was reused."""
    destination = tmp_path / "similarity.json"
    expected = _valid_result()

    created = comparison._publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert created is True
    assert destination.read_bytes() == render_comparison_result(expected)
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_reuses_strict_canonical_bytes_and_reads_existing_destination_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry must recognize only the exact canonical result without reopening mutable destination bytes."""
    destination = tmp_path / "similarity.json"
    expected = _valid_result()
    expected_content = render_comparison_result(expected)
    destination.write_bytes(expected_content)
    real_read_bytes = Path.read_bytes
    destination_reads = 0

    def counted_read_bytes(path: Path) -> bytes:
        nonlocal destination_reads
        if path == destination:
            destination_reads += 1
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)

    created = comparison._publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert created is False
    assert destination_reads == 1
    assert real_read_bytes(destination) == expected_content
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_rejects_renderer_bytes_for_a_different_valid_result_before_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replaced renderer must not redefine which scientific result qualifies for exact reuse."""
    destination = tmp_path / "similarity.json"
    expected = _valid_result()
    assert expected.input_identities is not None
    different_inputs = dict(expected.input_identities)
    different_inputs["capture_json"] = ContentIdentity(size=1, sha256="d" * 64)
    rendered_result = expected.with_input_identities(different_inputs)
    rendered_content = render_comparison_result(rendered_result)
    destination.write_bytes(rendered_content)

    def render_different(_result: ComparisonResult) -> bytes:
        return rendered_content

    monkeypatch.setattr(comparison, "render_comparison_result", render_different)

    with pytest.raises(TrafficlabError, match="rendered similarity artifact.*canonical evaluated result"):
        comparison._publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert destination.read_bytes() == rendered_content
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


@pytest.mark.parametrize(
    "existing_content",
    [
        b"not-json\n",
        (json.dumps(_valid_result_document(), indent=2, sort_keys=True) + "\n").encode(),
    ],
    ids=["malformed", "noncanonical"],
)
def test_publication_preserves_malformed_or_noncanonical_existing_bytes(
    tmp_path: Path, existing_content: bytes
) -> None:
    """Malformed bytes or a noncanonical encoding must never be blessed as the evaluated artifact."""
    destination = tmp_path / "similarity.json"
    destination.write_bytes(existing_content)

    with pytest.raises(TrafficlabError):
        comparison._publish_comparison_result(destination, _valid_result())  # pyright: ignore[reportPrivateUsage]

    assert destination.read_bytes() == existing_content
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_preserves_a_valid_existing_result_with_different_lineage(tmp_path: Path) -> None:
    """A valid result for different exact inputs must remain a collision rather than a successful retry."""
    destination = tmp_path / "similarity.json"
    expected = _valid_result()
    assert expected.input_identities is not None
    different_inputs = dict(expected.input_identities)
    different_inputs["capture_json"] = ContentIdentity(size=1, sha256="f" * 64)
    different = expected.with_input_identities(different_inputs)
    different_content = render_comparison_result(different)
    destination.write_bytes(different_content)

    with pytest.raises(TrafficlabError, match="already exists"):
        comparison._publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert destination.read_bytes() == different_content


def test_publication_preserves_a_valid_existing_result_with_a_different_score(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Matching lineage alone must not permit reuse of a scientifically different comparison."""
    destination = tmp_path / "similarity.json"
    expected = _valid_result()
    assert expected.input_identities is not None
    changed_trace = (
        TraceEvent(0.0, Direction.OUTBOUND, 60),
        TraceEvent(1.0, Direction.INBOUND, 180),
        TraceEvent(3.0, Direction.OUTBOUND, 100),
    )
    different = compare_traces(_trace(), changed_trace, 3.0, _settings(valid_config_data)).with_input_identities(
        expected.input_identities
    )
    different_content = render_comparison_result(different)
    assert different.aggregate_score != expected.aggregate_score
    destination.write_bytes(different_content)

    with pytest.raises(TrafficlabError, match="already exists"):
        comparison._publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert destination.read_bytes() == different_content


@pytest.mark.parametrize("collision", [False, True], ids=["existing", "link-race-winner"])
def test_publication_rejects_a_canonical_entry_replaced_immediately_after_its_validation_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collision: bool,
) -> None:
    """Reuse must remain bound to the unchanged directory entry whose exact bytes were validated."""
    destination = tmp_path / "similarity.json"
    expected = _valid_result()
    expected_content = render_comparison_result(expected)
    assert expected.input_identities is not None
    replacement_inputs = dict(expected.input_identities)
    replacement_inputs["capture_json"] = ContentIdentity(size=1, sha256="a" * 64)
    replacement_content = render_comparison_result(expected.with_input_identities(replacement_inputs))
    if not collision:
        destination.write_bytes(expected_content)

    real_read_bytes = Path.read_bytes
    real_link = os.link
    replaced = False

    def replace_after_read(path: Path) -> bytes:
        nonlocal replaced
        content = real_read_bytes(path)
        if path == destination and not replaced:
            replacement_path = tmp_path / "replacement-similarity.json"
            replacement_path.write_bytes(replacement_content)
            os.replace(replacement_path, destination)
            replaced = True
        return content

    def collide(source: str | Path, target: str | Path) -> None:
        Path(target).write_bytes(expected_content)
        real_link(source, target)

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    if collision:
        monkeypatch.setattr(comparison.os, "link", collide)

    with pytest.raises(TrafficlabError, match="changed during.*validation"):
        comparison._publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert replaced is True
    assert real_read_bytes(destination) == replacement_content
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


@pytest.mark.parametrize("winner_kind", ["identical", "different"])
def test_publication_link_race_reuses_only_an_identical_winner_and_preserves_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winner_kind: str
) -> None:
    """Losing an exclusive-link race must validate the winner exactly and never replace it."""
    destination = tmp_path / "similarity.json"
    expected = _valid_result()
    expected_content = render_comparison_result(expected)
    if winner_kind == "identical":
        winner = expected_content
    else:
        assert expected.input_identities is not None
        different_inputs = dict(expected.input_identities)
        different_inputs["capture_json"] = ContentIdentity(size=1, sha256="e" * 64)
        winner = render_comparison_result(expected.with_input_identities(different_inputs))
    real_link = os.link

    def collide(source: str | Path, destination_arg: str | Path) -> None:
        Path(destination_arg).write_bytes(winner)
        real_link(source, destination_arg)

    monkeypatch.setattr(comparison.os, "link", collide)

    if winner_kind == "identical":
        created = comparison._publish_comparison_result(  # pyright: ignore[reportPrivateUsage]
            destination, expected
        )
        assert created is False
    else:
        with pytest.raises(TrafficlabError, match="already exists"):
            comparison._publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert destination.read_bytes() == winner
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_reports_a_link_race_winner_that_disappears_before_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A vanished race winner must remain an expected publication error and release the owned temp."""
    destination = tmp_path / "similarity.json"

    def lose_to_vanished_winner(_source: str | Path, _destination: str | Path) -> None:
        raise FileExistsError("injected transient collision")

    monkeypatch.setattr(comparison.os, "link", lose_to_vanished_winner)

    with pytest.raises(TrafficlabError, match="could not publish similarity artifact"):
        comparison._publish_comparison_result(destination, _valid_result())  # pyright: ignore[reportPrivateUsage]

    assert not destination.exists()
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_fsync_failure_is_translated_and_cleans_the_owned_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed durability boundary must not leak the package API or leave a partial temporary artifact."""
    destination = tmp_path / "similarity.json"

    def fail_fsync(_file_descriptor: int) -> None:
        raise OSError("injected similarity fsync failure")

    monkeypatch.setattr(comparison.os, "fsync", fail_fsync)

    with pytest.raises(TrafficlabError, match="injected similarity fsync failure") as caught:
        comparison._publish_comparison_result(destination, _valid_result())  # pyright: ignore[reportPrivateUsage]

    assert caught.value.corrective_action == "verify the run directory is writable and has available space"
    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (
        outcome.kind,
        outcome.stage,
        outcome.detail,
        outcome.affected_evidence,
        outcome.evidence_state,
        outcome.corrective_action,
        outcome.authority,
    ) == (
        "publication_failed",
        "compare",
        "similarity.json durability check failed",
        "similarity.json",
        "not_published",
        "correct storage and rerun compare",
        "primary",
    )
    assert not destination.exists()
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_rejects_owned_temp_bytes_changed_after_strict_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A validated temp changed before linking must fail its exact canonical byte check."""
    destination = tmp_path / "similarity.json"
    real_load = comparison.load_comparison_result

    def mutate_after_load(path: Path) -> ComparisonResult:
        persisted = real_load(path)
        path.write_bytes(path.read_bytes().removesuffix(b"\n") + b" \n")
        return persisted

    monkeypatch.setattr(comparison, "load_comparison_result", mutate_after_load)

    with pytest.raises(TrafficlabError, match="temporary similarity artifact did not round-trip"):
        comparison._publish_comparison_result(destination, _valid_result())  # pyright: ignore[reportPrivateUsage]

    assert not destination.exists()
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_propagates_an_unexpected_validation_exception_after_owned_temp_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Programming defects must retain their original type while still respecting temporary-file ownership."""
    destination = tmp_path / "similarity.json"
    primary = RuntimeError("injected unexpected parser defect")

    def fail_parse(_content: bytes) -> ComparisonResult:
        raise primary

    monkeypatch.setattr(comparison, "parse_comparison_result", fail_parse)

    with pytest.raises(RuntimeError) as error:
        comparison._publish_comparison_result(destination, _valid_result())  # pyright: ignore[reportPrivateUsage]

    assert error.value is primary
    assert not destination.exists()
    assert list(tmp_path.glob(".similarity.json.*.tmp")) == []


def test_publication_preserves_an_unexpected_exception_when_owned_temp_cleanup_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cleanup failure must annotate, not translate or conceal, an unexpected programming defect."""
    destination = tmp_path / "similarity.json"
    primary = RuntimeError("injected unexpected parser defect")
    real_unlink = os.unlink

    def fail_parse(_content: bytes) -> ComparisonResult:
        raise primary

    def fail_owned_unlink(path: str | Path, *args: object, **kwargs: object) -> None:
        if Path(path).name.startswith(".similarity.json."):
            raise OSError("injected unexpected cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(comparison, "parse_comparison_result", fail_parse)
    monkeypatch.setattr(comparison.os, "unlink", fail_owned_unlink)

    with pytest.raises(RuntimeError) as error:
        comparison._publish_comparison_result(destination, _valid_result())  # pyright: ignore[reportPrivateUsage]

    assert error.value is primary
    assert error.value.__notes__ == ["owned temporary file cleanup also failed: injected unexpected cleanup failure"]
    assert not destination.exists()
    assert len(list(tmp_path.glob(".similarity.json.*.tmp"))) == 1


def test_publication_preserves_unexpected_primary_identity_when_cleanup_is_also_unexpected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected cleanup defect must annotate rather than mask an unexpected publication defect."""
    destination = tmp_path / "similarity.json"
    primary = RuntimeError("injected unexpected primary")
    cleanup = RuntimeError("injected unexpected cleanup")

    def fail_parse(_content: bytes) -> ComparisonResult:
        raise primary

    def fail_cleanup(_path: str | Path, *args: object, **kwargs: object) -> None:
        raise cleanup

    monkeypatch.setattr(comparison, "parse_comparison_result", fail_parse)
    monkeypatch.setattr(comparison.os, "unlink", fail_cleanup)

    with pytest.raises(RuntimeError) as error:
        comparison._publish_comparison_result(destination, _valid_result())  # pyright: ignore[reportPrivateUsage]

    assert error.value is primary
    assert error.value.__notes__ == [f"owned temporary file cleanup also failed: {cleanup}"]
    assert not destination.exists()
    assert len(list(tmp_path.glob(".similarity.json.*.tmp"))) == 1


def test_publication_translates_expected_primary_when_cleanup_is_unexpected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected cleanup defect must not mask the actionable expected publication failure."""
    destination = tmp_path / "similarity.json"
    primary = OSError("injected expected primary")
    cleanup = RuntimeError("injected unexpected cleanup")

    def fail_fsync(_file_descriptor: int) -> None:
        raise primary

    def fail_cleanup(_path: str | Path, *args: object, **kwargs: object) -> None:
        raise cleanup

    monkeypatch.setattr(comparison.os, "fsync", fail_fsync)
    monkeypatch.setattr(comparison.os, "unlink", fail_cleanup)

    with pytest.raises(
        TrafficlabError,
        match="injected expected primary.*cleanup incomplete.*injected unexpected cleanup",
    ) as error:
        comparison._publish_comparison_result(destination, _valid_result())  # pyright: ignore[reportPrivateUsage]

    assert error.value.__cause__ is primary
    assert not destination.exists()
    assert len(list(tmp_path.glob(".similarity.json.*.tmp"))) == 1


def test_publication_propagates_unexpected_cleanup_by_identity_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no primary failure, an unexpected cleanup defect must propagate after preserving publication."""
    destination = tmp_path / "similarity.json"
    expected = _valid_result()
    cleanup = RuntimeError("injected unexpected cleanup")

    def fail_cleanup(_path: str | Path, *args: object, **kwargs: object) -> None:
        raise cleanup

    monkeypatch.setattr(comparison.os, "unlink", fail_cleanup)

    with pytest.raises(RuntimeError) as error:
        comparison._publish_comparison_result(destination, expected)  # pyright: ignore[reportPrivateUsage]

    assert error.value is cleanup
    assert destination.read_bytes() == render_comparison_result(expected)
    assert len(list(tmp_path.glob(".similarity.json.*.tmp"))) == 1


def test_sha256_helpers_hash_exact_file_bytes_and_only_effective_similarity_settings(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Including an absolute run path in the settings identity would make an otherwise equal comparison differ."""
    file_path = tmp_path / "input.bin"
    file_path.write_bytes(b"trafficlab\x00fixture\n")
    first = ExperimentConfig.model_validate(valid_config_data)
    moved_run = first.run.model_copy(update={"directory": tmp_path / "different-absolute-run"})
    second = first.model_copy(update={"run": moved_run})

    assert comparison.sha256_file(file_path) == "6107e6e2956c92c3c474c458617dda297a2e1a7a64d0fb20fd8ba43f2b378254"
    assert comparison.similarity_settings_sha256(first.similarity) == comparison.similarity_settings_sha256(
        second.similarity
    )
    compact = json.dumps(first.similarity.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    assert comparison.similarity_settings_sha256(first.similarity) == comparison.sha256_bytes(compact)


def test_sha256_file_reports_an_unreadable_input(tmp_path: Path) -> None:
    """An absent identified input must abort rather than silently hashing an empty value."""
    with pytest.raises(TrafficlabError, match="could not hash comparison input") as error:
        comparison.sha256_file(tmp_path / "missing.pcapng")

    assert error.value.corrective_action == "verify missing.pcapng exists and is readable"
