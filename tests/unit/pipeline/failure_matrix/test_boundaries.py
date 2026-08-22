import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import trafficlab.fitting.stage as fitting
import trafficlab.generation.stage as generation
import trafficlab.preflight.types as preflight_types
from tests.support.failure_matrix.cases import (
    CAPTURE_FAILURE_SCENARIOS,
    FIXTURE_PATH,
    PUBLIC_BOUNDARY_CASES,
    SCENARIOS,
    BoundaryCase,
)
from tests.support.failure_matrix.doubles import runtime_model_double
from tests.support.failure_matrix.runners import (
    fit_config,
    fit_inputs,
    fit_success_outcome,
    prepared_experiment,
    render_snapshot,
    run_capture_boundary_case,
    run_capture_mounted_input_boundary_case,
    run_capture_stale_boundary_case,
    run_comparison_boundary_case,
    run_fit_boundary_case,
    run_generation_boundary_case,
    run_preflight_case,
    run_study_publication_case,
)
from trafficlab.common.compatibility import ContentIdentity, identify_bytes
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import EncodedPcapng
from trafficlab.common.trace import TrafficTrace
from trafficlab.fitting.stage import FitDependencies


def test_fit_changed_reference_without_resume_uses_the_generic_recovery_action(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """The resume-specific canonical action does not leak into a fresh non-resume fit."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    experiment_path = tmp_path / "experiment.toml"
    base = fit_config(valid_config_data, run_directory)
    config = base.model_copy(update={"genetic": base.genetic.model_copy(update={"resume": False})})
    inputs = fit_inputs(config)
    reference_path = run_directory / "reference.pcapng"
    original_reference = inputs[reference_path]
    reference_path.write_bytes(original_reference)
    reads = 0

    def read_bytes(path: Path) -> bytes:
        nonlocal reads
        if path == reference_path:
            reads += 1
            return original_reference if reads == 1 else original_reference + b"changed after fitting\n"
        return inputs[path]

    prepared = preflight_types.PreparedExperiment(
        experiment_path,
        config,
        preflight_types.PreflightReport(config, ()),
        run_directory,
    )
    dependencies = FitDependencies(
        lambda _path: prepared,
        read_bytes,
        lambda _context: fit_success_outcome(config),
    )

    with pytest.raises(TrafficlabError) as caught:
        fitting.fit_experiment(experiment_path, dependencies=dependencies)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "kind": "artifact_changed",
        "stage": "fit",
        "detail": "reference.pcapng changed during fit",
        "corrective_action": "restore the exact fitted inputs and rerun fit",
        "affected_evidence": "reference.pcapng",
        "evidence_state": "preserved",
        "authority": "primary",
    }
    assert reference_path.read_bytes() == original_reference


def test_generation_maps_missing_capture_after_a_validated_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The public generator retains the missing-capture primary outcome before generation."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    prepared = prepared_experiment(run_directory)
    config = cast(Any, prepared.config)
    config.run.final_seed = 1
    config.models = SimpleNamespace(enabled=("poisson_empirical",), poisson_empirical=SimpleNamespace())
    final_limits = SimpleNamespace()
    config.generation = SimpleNamespace(final=final_limits)
    records: list[dict[str, object]] = []
    best = SimpleNamespace(
        family="poisson_empirical",
        gene_bounds={},
        capture_identity=ContentIdentity(size=0, sha256="0" * 64),
        final_seed=1,
        final_limits=final_limits,
        observation_window_seconds=1.0,
        fitted=object(),
    )

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    def read(path: Path, **_kwargs: object) -> bytes:
        if path.name == "best_model.json":
            return b"best model"
        raise TrafficlabError(
            "capture.json is missing",
            corrective_action="restore capture.json before generation",
        )

    class _Family:
        gene_names: tuple[str, ...] = ()

    def open_prepared(_path: Path) -> preflight_types.PreparedExperiment:
        return prepared

    def load_best(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return best

    def get_family(_name: str) -> _Family:
        return _Family()

    monkeypatch.setattr(generation, "open_or_prepare_experiment", open_prepared)
    monkeypatch.setattr(generation, "append_run_log", append)
    monkeypatch.setattr(generation, "_read_required_bytes", read)
    monkeypatch.setattr(generation, "load_best_model", load_best)
    monkeypatch.setattr(generation, "get_family", get_family)
    monkeypatch.setattr(generation, "runtime_fitted_model", runtime_model_double)

    with pytest.raises(TrafficlabError) as caught:
        generation.generate_experiment(prepared.source)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "kind": "artifact_missing",
        "stage": "generate",
        "detail": "capture.json is missing",
        "corrective_action": "restore capture.json before generation",
        "affected_evidence": "capture.json",
        "evidence_state": "not_published",
        "authority": "primary",
    }
    assert records[-1]["failure_outcome"] == outcome.as_dict()


def test_generation_preserves_published_bytes_when_post_publication_parse_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Post-publication verification failure records preserved generated evidence."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    prepared = prepared_experiment(run_directory)
    config = cast(Any, prepared.config)
    config.run.final_seed = 1
    config.models = SimpleNamespace(enabled=("poisson_empirical",), poisson_empirical=SimpleNamespace())
    final_limits = SimpleNamespace()
    config.generation = SimpleNamespace(final=final_limits)
    records: list[dict[str, object]] = []
    captured = b"capture metadata"
    best = SimpleNamespace(
        family="poisson_empirical",
        gene_bounds={},
        capture_identity=identify_bytes(captured),
        final_seed=1,
        final_limits=final_limits,
        observation_window_seconds=1.0,
        fitted=object(),
    )
    generated_path = run_directory / "generated.pcapng"

    def append(_directory: Path, record: dict[str, object]) -> None:
        records.append(record)

    def read(path: Path, **_kwargs: object) -> bytes:
        return b"best model" if path.name == "best_model.json" else captured

    def publish(*_args: object, **_kwargs: object) -> object:
        generated_path.write_bytes(b"generated bytes")
        return SimpleNamespace(content=b"generated bytes", path=generated_path)

    def parse_failure(*_args: object, **_kwargs: object) -> TrafficTrace:
        raise TrafficlabError(
            "generated bytes cannot be parsed",
            corrective_action="repair generated PCAPNG serialization",
        )

    class _Generated:
        @staticmethod
        def require_complete() -> TrafficTrace:
            return TrafficTrace.from_events(())

    class _Family:
        gene_names: tuple[str, ...] = ()

        @staticmethod
        def generate(*_args: object, **_kwargs: object) -> _Generated:
            return _Generated()

    def open_prepared(_path: Path) -> preflight_types.PreparedExperiment:
        return prepared

    def load_best(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return best

    def get_family(_name: str) -> _Family:
        return _Family()

    def parse_metadata(*_args: object, **_kwargs: object) -> object:
        return object()

    def reproduce(*_args: object, **_kwargs: object) -> tuple[TrafficTrace, EncodedPcapng]:
        trace = TrafficTrace.from_events(())
        return trace, EncodedPcapng(content=b"generated bytes", trace=trace)

    monkeypatch.setattr(generation, "open_or_prepare_experiment", open_prepared)
    monkeypatch.setattr(generation, "append_run_log", append)
    monkeypatch.setattr(generation, "_read_required_bytes", read)
    monkeypatch.setattr(generation, "load_best_model", load_best)
    monkeypatch.setattr(generation, "get_family", get_family)
    monkeypatch.setattr(generation, "runtime_fitted_model", runtime_model_double)
    monkeypatch.setattr(generation, "parse_capture_metadata", parse_metadata)
    monkeypatch.setattr(generation, "reproduce_generated_pcapng", reproduce)
    monkeypatch.setattr(generation, "publish_generated_pcapng", publish)
    monkeypatch.setattr(generation, "read_pcapng_bytes", parse_failure)
    monkeypatch.setattr(generation, "render_effective_config", render_snapshot)

    def identify_generation_input(path: Path) -> ContentIdentity:
        contents = {
            "experiment.toml": b"canonical snapshot",
            "best_model.json": b"best model",
            "capture.json": captured,
        }
        return identify_bytes(contents[path.name])

    monkeypatch.setattr(generation, "identify_file", identify_generation_input)

    with pytest.raises(TrafficlabError) as caught:
        generation.generate_experiment(prepared.source)

    outcome = caught.value.failure_outcome
    assert outcome is not None
    assert outcome.as_dict() == {
        "kind": "artifact_corrupt",
        "stage": "generate",
        "detail": "generated bytes cannot be parsed",
        "corrective_action": "repair generated PCAPNG serialization",
        "affected_evidence": "generated.pcapng",
        "evidence_state": "preserved",
        "authority": "primary",
    }
    assert generated_path.read_bytes() == b"generated bytes"
    assert records[-1]["failure_outcome"] == outcome.as_dict()


def test_public_boundary_case_registry_covers_each_authoritative_fixture_row_once() -> None:
    """Every checked fixture row belongs to one public-boundary primary/secondary case."""
    fixture_rows = tuple(json.loads(line) for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines() if line)
    registry_rows = tuple(outcome.as_dict() for case in PUBLIC_BOUNDARY_CASES for outcome in case.outcomes)

    assert len(fixture_rows) == 43
    assert len(PUBLIC_BOUNDARY_CASES) == 38
    assert len(set(SCENARIOS)) == 38
    assert registry_rows == fixture_rows
    assert all(case.identifier.startswith("primitive-boundary-") for case in PUBLIC_BOUNDARY_CASES)


@pytest.mark.parametrize("case", PUBLIC_BOUNDARY_CASES, ids=lambda case: case.identifier)
def test_public_boundaries_serialize_the_authoritative_failure_matrix(
    case: BoundaryCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    valid_config_data: dict[str, object],
) -> None:
    """A wrong owner, action, authority, state, or publication side effect breaks this matrix."""
    if case.scenario in {"mounted_input_unavailable", "mounted_input_incompatible"}:
        run_capture_mounted_input_boundary_case(case, monkeypatch, tmp_path, valid_config_data)
    elif case.scenario in {
        "config_invalid",
        "docker_unavailable",
        "compose_incompatible",
        "target_image_unavailable",
        "capture_image_incompatible",
        "dumpcap_unavailable",
        "dumpcap_incompatible",
        "mount_source_unavailable",
        "mount_target_incompatible",
        "prerequisite_unavailable",
        "prerequisite_incompatible",
    }:
        run_preflight_case(case, tmp_path, valid_config_data)
    elif case.scenario in CAPTURE_FAILURE_SCENARIOS | {"stale_capture_pair"}:
        if case.scenario == "stale_capture_pair":
            run_capture_stale_boundary_case(case, tmp_path, valid_config_data)
        else:
            run_capture_boundary_case(case, monkeypatch, tmp_path, valid_config_data)
    elif case.scenario in {"checkpoint_corrupt", "checkpoint_schema", "best_model_collision", "reference_changed"}:
        run_fit_boundary_case(case, monkeypatch, tmp_path, valid_config_data)
    elif case.scenario in {"best_model_missing", "best_model_schema", "packet_limit"}:
        run_generation_boundary_case(case, tmp_path, valid_config_data)
    elif case.scenario in {"foreign_generated", "metric_infeasible", "similarity_durability"}:
        run_comparison_boundary_case(case, monkeypatch, tmp_path, valid_config_data)
    elif case.scenario == "accepted_collision":
        run_study_publication_case(case, tmp_path)
    else:
        raise AssertionError(f"unsupported public boundary scenario {case.scenario!r}")
