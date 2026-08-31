from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

import pytest

import trafficlab_dashboard.run_loader as run_loader
from tests.trafficlab_dashboard.support.dashboard_fixtures import write_complete_dashboard_run
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import align_generated, normalize_reference, parse_capture_metadata
from trafficlab.comparison.codec import parse_comparison_result
from trafficlab_dashboard.run_loader import load_dashboard_run


def test_load_dashboard_run_normalizes_and_aligns_required_traces(tmp_path: Path) -> None:
    run_directory = write_complete_dashboard_run(
        tmp_path,
        reference_times=(10.0, 11.0, 13.0),
        generated_times=(20.0, 21.0, 24.0),
    )

    loaded = load_dashboard_run(run_directory)

    assert loaded.window == 3.0
    assert loaded.reference.timestamps.tolist() == [0.0, 1.0, 3.0]
    assert loaded.generated.timestamps.tolist() == [0.0, 1.0]
    assert loaded.reference_packet_count == 3
    assert loaded.generated_packet_count == 2
    assert loaded.similarity is not None
    assert loaded.best_model is not None
    assert loaded.history is not None
    assert loaded.experiment is not None
    assert len(loaded.identities.reference_sha256) == 64
    assert len(loaded.identities.generated_sha256) == 64
    assert len(loaded.identities.capture_sha256) == 64
    assert loaded.identities.similarity_sha256 is not None
    assert loaded.identities.best_model_sha256 is not None
    assert loaded.identities.history_sha256 is not None
    assert dict(loaded.unavailable) == {}


def test_missing_similarity_artifact_disables_only_its_dependent_aspects(tmp_path: Path) -> None:
    run_directory = write_complete_dashboard_run(tmp_path)
    (run_directory / "similarity.json").unlink()

    loaded = load_dashboard_run(run_directory)

    assert loaded.similarity is None
    assert loaded.best_model is not None
    assert loaded.history is not None
    assert loaded.unavailable["similarity_scores"] == "similarity.json is missing"
    assert loaded.unavailable["multiscale_discrepancy"] == "similarity.json is missing"
    assert "ga_fitness_history" not in loaded.unavailable


def test_ga_history_requires_a_valid_experiment_configuration(tmp_path: Path) -> None:
    run_directory = write_complete_dashboard_run(tmp_path)
    (run_directory / "experiment.toml").write_text("not = [valid\n", encoding="utf-8")

    loaded = load_dashboard_run(run_directory)

    assert loaded.experiment is None
    assert loaded.history is None
    assert "experiment.toml" in loaded.unavailable["ga_fitness_history"]


def test_missing_ga_history_disables_only_the_history_aspect_when_experiment_is_valid(tmp_path: Path) -> None:
    run_directory = write_complete_dashboard_run(tmp_path)
    (run_directory / "ga_history.csv").unlink()

    loaded = load_dashboard_run(run_directory)

    assert loaded.experiment is not None
    assert loaded.similarity is not None
    assert loaded.history is None
    assert loaded.unavailable["ga_fitness_history"] == "ga_history.csv is missing"
    assert "similarity_scores" not in loaded.unavailable
    assert "multiscale_discrepancy" not in loaded.unavailable


def test_loaded_dashboard_run_exposes_immutable_arrays_and_mapping(tmp_path: Path) -> None:
    run_directory = write_complete_dashboard_run(tmp_path)

    loaded = load_dashboard_run(run_directory)

    assert loaded.reference.timestamps.flags.writeable is False
    assert loaded.reference.directions.flags.writeable is False
    assert loaded.reference.frame_lengths.flags.writeable is False
    assert loaded.generated.timestamps.flags.writeable is False
    assert loaded.generated.directions.flags.writeable is False
    assert loaded.generated.frame_lengths.flags.writeable is False
    assert type(loaded.unavailable) is MappingProxyType


def test_failed_second_load_does_not_mutate_a_previous_dashboard_run(tmp_path: Path) -> None:
    run_directory = write_complete_dashboard_run(tmp_path)
    first = load_dashboard_run(run_directory)

    (run_directory / "capture.json").write_text("{}", encoding="utf-8")

    with pytest.raises(TrafficlabError, match="capture.json"):
        load_dashboard_run(run_directory)

    assert first.reference.timestamps.tolist() == [0.0, 1.0, 3.0]
    assert first.generated.timestamps.tolist() == [0.0, 1.0]
    assert dict(first.unavailable) == {}


def test_similarity_identity_matches_the_exact_bytes_used_for_parsing_when_rewritten_mid_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_directory = write_complete_dashboard_run(tmp_path)
    similarity_path = run_directory / "similarity.json"
    original_similarity = similarity_path.read_bytes()
    rewritten_similarity = (
        json.dumps(json.loads(original_similarity), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")
    expected_similarity = parse_comparison_result(original_similarity)
    expected_similarity_sha256 = identify_bytes(original_similarity).sha256

    def rewrite_path_and_parse(path: Path) -> object:
        assert path == similarity_path
        content = path.read_bytes()
        assert content == original_similarity
        path.write_bytes(rewritten_similarity)
        return parse_comparison_result(content)

    def rewrite_bytes_and_parse(content: bytes) -> object:
        assert content == original_similarity
        similarity_path.write_bytes(rewritten_similarity)
        return parse_comparison_result(content)

    monkeypatch.setattr(run_loader, "load_comparison_result", rewrite_path_and_parse, raising=False)
    monkeypatch.setattr(run_loader, "parse_comparison_result", rewrite_bytes_and_parse, raising=False)

    loaded = load_dashboard_run(run_directory)

    assert loaded.similarity == expected_similarity
    assert loaded.identities.similarity_sha256 == expected_similarity_sha256


def test_reference_and_generated_identities_match_the_exact_loaded_trace_bytes(tmp_path: Path) -> None:
    run_directory = write_complete_dashboard_run(
        tmp_path,
        reference_times=(10.0, 11.0, 13.0),
        generated_times=(20.0, 21.0, 24.0),
    )

    metadata = parse_capture_metadata((run_directory / "capture.json").read_bytes(), source=run_directory / "capture.json")
    reference_bytes = (run_directory / "reference.pcapng").read_bytes()
    generated_bytes = (run_directory / "generated.pcapng").read_bytes()
    expected_reference, _ = normalize_reference(
        read_pcapng_bytes(reference_bytes, metadata, source=run_directory / "reference.pcapng")
    )
    expected_generated_trace = align_generated(
        read_pcapng_bytes(
            generated_bytes,
            metadata,
            source=run_directory / "generated.pcapng",
        ),
        3.0,
    )

    loaded = load_dashboard_run(run_directory)

    assert loaded.reference == expected_reference
    assert loaded.generated == expected_generated_trace
    assert loaded.identities.reference_sha256 == identify_bytes(reference_bytes).sha256
    assert loaded.identities.generated_sha256 == identify_bytes(generated_bytes).sha256
