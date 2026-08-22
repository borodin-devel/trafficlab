from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import trafficlab.generation.stage as generation_module
from tests.support.generation import (
    MODEL_BYTES,
    log_records,
    prepare_stage_run,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import (
    CaptureMetadata,
    TraceEvent,
    TrafficTrace,
)
from trafficlab.generation.models.common import GenerationResult, ModelFamily
from trafficlab.generation.models.fitted_model import (
    load_best_model,
)
from trafficlab.generation.models.registry import (
    get_family,
)
from trafficlab.generation.stage import generate_experiment
from trafficlab.preflight.types import PreparedExperiment

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("change", ["final-seed", "final-limits"])
def test_stage_rejects_generation_policy_drift_before_family_or_rng_use(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """Mutable configuration cannot silently replace the seed or guards retained by best_model.json."""
    data = copy.deepcopy(valid_config_data)
    if change == "final-seed":
        run = cast(dict[str, object], data["run"])
        run["final_seed"] = cast(int, run["final_seed"]) + 100_000
    else:
        generation = cast(dict[str, object], data["generation"])
        final = cast(dict[str, object], generation["final"])
        final["max_packets"] = cast(int, final["max_packets"]) + 1
    experiment_path, run_directory, _config = prepare_stage_run(data, tmp_path)

    def forbidden_family(_name: str) -> ModelFamily:
        raise AssertionError("incompatible generation policy reached the model family or RNG")

    monkeypatch.setattr(generation_module, "get_family", forbidden_family)

    with pytest.raises(TrafficlabError, match="generation policy") as caught:
        generate_experiment(experiment_path, clock=lambda: 0.0)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert (outcome.kind, outcome.stage, outcome.affected_evidence, outcome.evidence_state) == (
        "scientific_semantics_incompatible",
        "generate",
        "best_model.json",
        "preserved",
    )
    assert not (run_directory / "generated.pcapng").exists()


def test_stage_rejects_best_model_mutation_before_generated_publication(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generation cannot publish bytes after its authoritative fitted model changes."""
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    model_path = run_directory / "best_model.json"
    real_reproduce = generation_module.reproduce_generated_pcapng

    def reproduce_and_mutate(*args: Any, **kwargs: Any) -> object:
        result = real_reproduce(*args, **kwargs)
        model_path.write_bytes(model_path.read_bytes() + b"changed after generation")
        return result

    monkeypatch.setattr(generation_module, "reproduce_generated_pcapng", reproduce_and_mutate)

    with pytest.raises(TrafficlabError, match="best_model.json changed during generate") as caught:
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert caught.value.failure_outcome is not None
    assert caught.value.failure_outcome.kind == "artifact_changed"
    assert not (run_directory / "generated.pcapng").exists()


@pytest.mark.parametrize(
    "defect",
    ["disabled", "missing-bounds", "bounds"],
    ids=["stored-family-disabled", "enabled-family-bounds-absent", "stored-bounds-mismatch"],
)
def test_stage_rejects_model_outside_authoritative_family_configuration(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
) -> None:
    """A stored model is valid only under the exact enabled family bounds that produced it."""
    data = copy.deepcopy(valid_config_data)
    models = cast(dict[str, object], data["models"])
    if defect == "disabled":
        models["enabled"] = ["markov_renewal", "mmpp"]
        models.pop("poisson_empirical")
    elif defect == "bounds":
        poisson = cast(dict[str, object], models["poisson_empirical"])
        cast(dict[str, object], poisson["c_lambda"])["upper"] = 5.0
    experiment_path, run_directory, config = prepare_stage_run(data, tmp_path)
    if defect == "missing-bounds":
        invalid_models = config.models.model_copy(update={"poisson_empirical": None})
        invalid_config = config.model_copy(update={"models": invalid_models})
        prepared = cast(
            PreparedExperiment,
            SimpleNamespace(run_directory=run_directory, config=invalid_config),
        )

        def prepare_invalid(_path: Path) -> PreparedExperiment:
            return prepared

        monkeypatch.setattr(generation_module, "open_or_prepare_experiment", prepare_invalid)

    with pytest.raises(TrafficlabError, match="enabled" if defect == "disabled" else "bounds"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert not (run_directory / "generated.pcapng").exists()
    assert log_records(run_directory)[-1]["event"] == "stage_failed"


@pytest.mark.parametrize("defect", ["missing", "invalid"], ids=["missing-model", "invalid-model"])
def test_stage_reports_missing_or_invalid_model_as_direct_generation_error(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    defect: str,
) -> None:
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    model_path = run_directory / "best_model.json"
    if defect == "missing":
        model_path.unlink()
    else:
        model_path.write_bytes(b"not JSON")

    expected_detail = "best_model.json is missing" if defect == "missing" else "best model"
    with pytest.raises(TrafficlabError, match=expected_detail) as error:
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert log_records(run_directory)[-1] == {
        "corrective_action": error.value.corrective_action,
        "detail": str(error.value),
        "event": "stage_failed",
        "failure_outcome": {
            "affected_evidence": "best_model.json",
            "authority": "primary",
            "corrective_action": error.value.corrective_action,
            "detail": str(error.value),
            "evidence_state": "not_published" if defect == "missing" else "preserved",
            "kind": "artifact_missing" if defect == "missing" else "artifact_corrupt",
            "stage": "generate",
        },
        "stage": "generate",
    }


def test_stage_reports_a_missing_capture_metadata_file_through_the_real_reader(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    (run_directory / "capture.json").unlink()

    with pytest.raises(TrafficlabError, match="could not read capture metadata") as error:
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert error.value.failure_outcome is not None
    assert error.value.failure_outcome.affected_evidence == "capture.json"
    assert error.value.failure_outcome.evidence_state == "not_published"
    assert not (run_directory / "generated.pcapng").exists()


def test_stage_rejects_incompatible_model_schema_before_generation(
    valid_config_data: dict[str, object],
    tmp_path: Path,
) -> None:
    """An old fitted-model schema is preserved scientific evidence, not corrupt input."""
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    model_path = run_directory / "best_model.json"
    document = cast(dict[str, object], json.loads(model_path.read_bytes()))
    document["scientific_artifact_schema"] = 1
    incompatible = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    model_path.write_bytes(incompatible)

    with pytest.raises(TrafficlabError, match="best model schema is incompatible") as raised:
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert model_path.read_bytes() == incompatible
    assert not (run_directory / "generated.pcapng").exists()
    assert raised.value.failure_outcome is not None
    assert raised.value.failure_outcome.as_dict() == {
        "affected_evidence": "best_model.json",
        "authority": "primary",
        "corrective_action": "refit under the current schema",
        "detail": "best model schema is incompatible",
        "evidence_state": "preserved",
        "kind": "scientific_semantics_incompatible",
        "stage": "generate",
    }
    assert log_records(run_directory)[-1]["failure_outcome"] == raised.value.failure_outcome.as_dict()


@pytest.mark.parametrize("defect", ["capture-hash", "metadata"], ids=["capture-hash-mismatch", "malformed-metadata"])
def test_stage_rejects_invalid_capture_lineage_before_generation(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    defect: str,
) -> None:
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    capture_path = run_directory / "capture.json"
    capture_path.write_bytes(
        b'{"interface":"eth0","target_mac":"02:42:ac:11:00:04"}' if defect == "capture-hash" else b"{"
    )

    with pytest.raises(TrafficlabError, match="capture" if defect == "capture-hash" else "JSON"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert not (run_directory / "generated.pcapng").exists()


def test_stage_rejects_incomplete_generation_without_publication(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    best = load_best_model(MODEL_BYTES, source=Path("best_model.json"))
    family = get_family(best.family)

    def incomplete_generate(*_args: Any, **_kwargs: Any) -> GenerationResult:
        return GenerationResult(False, TrafficTrace.from_events(()), "max_packets")

    incomplete = cast(
        ModelFamily,
        SimpleNamespace(
            name=family.name,
            gene_names=family.gene_names,
            generate=incomplete_generate,
        ),
    )

    def get_incomplete_family(_name: str) -> ModelFamily:
        return incomplete

    monkeypatch.setattr(generation_module, "get_family", get_incomplete_family)

    with pytest.raises(TrafficlabError, match="generation exceeded the configured packet limit"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert not (run_directory / "generated.pcapng").exists()


def test_stage_rejects_a_post_publication_round_trip_mismatch(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stage result must expose events parsed from the exact published bytes, not pre-render values."""
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    real_parse = read_pcapng_bytes

    def change_stage_parse(
        content: bytes,
        metadata: CaptureMetadata,
        *,
        source: Path,
    ) -> TrafficTrace:
        parsed = real_parse(content, metadata, source=source)
        return parsed[:-1]

    monkeypatch.setattr(generation_module, "read_pcapng_bytes", change_stage_parse)

    with pytest.raises(TrafficlabError, match="round-trip"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert (run_directory / "generated.pcapng").exists()
    assert log_records(run_directory)[-1]["event"] == "stage_failed"


def test_stage_rejects_a_post_publication_timestamp_above_stored_window(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stage must independently enforce parsed timestamps inside stored W."""
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    real_parse = read_pcapng_bytes

    def move_stage_parse_outside_window(
        content: bytes,
        metadata: CaptureMetadata,
        *,
        source: Path,
    ) -> TrafficTrace:
        parsed = real_parse(content, metadata, source=source)
        return TrafficTrace.from_events(
            (*parsed[:-1].to_events(), TraceEvent(10.000000001, parsed[-1].direction, parsed[-1].frame_length))
        )

    monkeypatch.setattr(generation_module, "read_pcapng_bytes", move_stage_parse_outside_window)

    with pytest.raises(TrafficlabError, match="outside.*observation window"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert (run_directory / "generated.pcapng").exists()
    assert log_records(run_directory)[-1]["event"] == "stage_failed"


def test_stage_rejects_and_preserves_a_different_existing_output(
    valid_config_data: dict[str, object],
    tmp_path: Path,
) -> None:
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    destination = run_directory / "generated.pcapng"
    destination.write_bytes(b"preserve")

    with pytest.raises(TrafficlabError, match="already exists"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert destination.read_bytes() == b"preserve"


def test_stage_failure_log_wrapper_preserves_primary_error_contract(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    (run_directory / "best_model.json").unlink()
    primary = TrafficlabError("primary", corrective_action="primary action", exit_code=7)
    real_read = generation_module._read_required_bytes  # pyright: ignore[reportPrivateUsage]

    def fail_model(path: Path, *, kind: str, corrective_action: str) -> bytes:
        if path.name == "best_model.json":
            raise primary
        return real_read(path, kind=kind, corrective_action=corrective_action)

    def fail_log(_run_directory: Path, _record: object) -> None:
        raise TrafficlabError("secondary log failure", corrective_action="repair log")

    monkeypatch.setattr(generation_module, "_read_required_bytes", fail_model)
    monkeypatch.setattr(generation_module, "append_run_log", fail_log)

    with pytest.raises(TrafficlabError, match="primary.*secondary log failure") as raised:
        generate_experiment(experiment_path, clock=lambda: 0.0)

    assert raised.value.corrective_action == "primary action"
    assert raised.value.exit_code == 7


def test_stage_success_log_failure_leaves_output_reusable(
    valid_config_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment_path, run_directory, _config = prepare_stage_run(valid_config_data, tmp_path)
    real_append = generation_module.append_run_log

    def fail_success(_run_directory: Path, record: object) -> None:
        assert cast(dict[str, object], record)["event"] == "generated_pcapng_published"
        raise TrafficlabError("success log failure", corrective_action="repair log")

    monkeypatch.setattr(generation_module, "append_run_log", fail_success)
    with pytest.raises(TrafficlabError, match="published.*success logging failed"):
        generate_experiment(experiment_path, clock=lambda: 0.0)

    published = (run_directory / "generated.pcapng").read_bytes()
    monkeypatch.setattr(generation_module, "append_run_log", real_append)
    retried = generate_experiment(experiment_path, clock=lambda: 0.0)

    assert retried.reused is True
    assert retried.generated_path.read_bytes() == published
