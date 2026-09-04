from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType

import pytest

import trafficlab_dashboard.run_loader as run_loader
from tests.trafficlab_dashboard.support.dashboard_fixtures import (
    copy_checked_dashboard_run,
    write_complete_dashboard_run,
)
from trafficlab.common.compatibility import ContentIdentity, identify_bytes
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scapy_io import read_pcapng_bytes
from trafficlab.common.trace import align_generated, normalize_reference, parse_capture_metadata
from trafficlab.comparison.codec import parse_comparison_result
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.generation.models import BestModel
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
    assert loaded.similarity is None
    assert loaded.best_model is None
    assert loaded.history is None
    assert loaded.experiment is not None
    assert len(loaded.identities.reference_sha256) == 64
    assert len(loaded.identities.generated_sha256) == 64
    assert len(loaded.identities.capture_sha256) == 64
    assert loaded.identities.similarity_sha256 is None
    assert loaded.identities.best_model_sha256 is None
    assert loaded.identities.history_sha256 is None
    assert loaded.identities.experiment_sha256 is not None
    assert "foreign" in loaded.unavailable["similarity_scores"]
    assert "foreign" in loaded.unavailable["best_model"]
    assert "population_size" in loaded.unavailable["ga_fitness_history"]


def test_checked_run_accepts_only_lineage_bound_optional_artifacts(tmp_path: Path) -> None:
    loaded = load_dashboard_run(copy_checked_dashboard_run(tmp_path))

    assert loaded.similarity is not None
    assert loaded.best_model is not None
    assert loaded.history is not None
    assert loaded.experiment is not None
    assert loaded.identities.similarity_sha256 is not None
    assert loaded.identities.best_model_sha256 is not None
    assert loaded.identities.history_sha256 is not None
    assert loaded.identities.experiment_sha256 is not None
    assert dict(loaded.unavailable) == {}
    assert loaded.similarity is not None
    postfit = loaded.similarity.postfit_diagnostics
    assert postfit is not None
    assert loaded.fano_allan_diagnostic == postfit.fano_allan.diagnostics
    assert loaded.transition_fidelity_diagnostic == postfit.transition_matrix.diagnostics
    assert loaded.c2st_diagnostic == postfit.classical_c2st.diagnostics


def test_same_format_foreign_optional_artifacts_degrade_with_actionable_lineage_reasons(tmp_path: Path) -> None:
    run_directory = write_complete_dashboard_run(tmp_path)

    loaded = load_dashboard_run(run_directory)

    assert loaded.similarity is None
    assert loaded.best_model is None
    similarity_reason = loaded.unavailable["similarity_scores"]
    best_model_reason = loaded.unavailable["best_model"]
    assert similarity_reason == loaded.unavailable["multiscale_discrepancy"]
    assert "foreign" in similarity_reason
    assert "capture.json" in similarity_reason
    assert "rerun compare" in similarity_reason
    assert "foreign" in best_model_reason
    assert "capture.json" in best_model_reason
    assert "rerun fit" in best_model_reason


@pytest.mark.parametrize(
    ("mismatch", "expected_detail"),
    (
        ("capture_json", "capture.json"),
        ("reference_pcapng", "reference.pcapng"),
        ("generated_pcapng", "generated.pcapng"),
        ("window", "observation_window_seconds"),
        ("missing_identities", "no canonical input identities"),
    ),
)
def test_similarity_lineage_checks_every_required_identity_and_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
    expected_detail: str,
) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    original = parse_comparison_result((run_directory / "similarity.json").read_bytes())
    if mismatch == "window":
        changed = original.model_copy(update={"observation_window_seconds": original.observation_window_seconds + 1.0})
    elif mismatch == "missing_identities":
        changed = original.model_copy(update={"input_identities": None})
    else:
        identities = original.input_identities
        assert identities is not None
        stored = identities[mismatch]
        foreign = stored.model_copy(update={"sha256": "0" * 64})
        changed = original.model_copy(update={"input_identities": identities.model_copy(update={mismatch: foreign})})

    def return_changed_similarity(_content: bytes) -> ComparisonResult:
        return changed

    monkeypatch.setattr(run_loader, "parse_comparison_result", return_changed_similarity)

    loaded = load_dashboard_run(run_directory)

    assert loaded.similarity is None
    assert loaded.identities.similarity_sha256 is None
    assert expected_detail in loaded.unavailable["similarity_scores"]
    assert "rerun compare" in loaded.unavailable["similarity_scores"]


@pytest.mark.parametrize(
    ("mismatch", "expected_detail"),
    (("capture", "capture.json"), ("reference", "reference.pcapng"), ("window", "observation_window_seconds")),
)
def test_best_model_lineage_checks_every_required_identity_and_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
    expected_detail: str,
) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    original = run_loader.load_best_model(
        (run_directory / "best_model.json").read_bytes(),
        source=run_directory / "best_model.json",
    )
    if mismatch == "capture":
        changed = original.model_copy(
            update={
                "capture_identity": ContentIdentity(
                    size=original.capture_identity.size + 1,
                    sha256=original.capture_identity.sha256,
                )
            }
        )
    elif mismatch == "reference":
        changed = original.model_copy(
            update={
                "reference_identity": ContentIdentity(
                    size=original.reference_identity.size + 1,
                    sha256=original.reference_identity.sha256,
                )
            }
        )
    else:
        changed = original.model_copy(update={"observation_window_seconds": original.observation_window_seconds + 1.0})

    def return_changed_best_model(_content: bytes, *, source: Path) -> BestModel:
        del source
        return changed

    monkeypatch.setattr(run_loader, "load_best_model", return_changed_best_model)

    loaded = load_dashboard_run(run_directory)

    assert loaded.best_model is None
    assert loaded.identities.best_model_sha256 is None
    assert expected_detail in loaded.unavailable["best_model"]
    assert "rerun fit" in loaded.unavailable["best_model"]


def test_missing_similarity_artifact_disables_only_its_dependent_aspects(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    (run_directory / "similarity.json").unlink()

    loaded = load_dashboard_run(run_directory)

    assert loaded.similarity is None
    assert loaded.best_model is not None
    assert loaded.history is not None
    assert loaded.unavailable["similarity_scores"] == "similarity.json is missing"
    assert loaded.unavailable["multiscale_discrepancy"] == "similarity.json is missing"
    assert loaded.unavailable["fano_allan"] == "similarity.json is missing"
    assert loaded.unavailable["transition_fidelity"] == "similarity.json is missing"
    assert loaded.unavailable["c2st"] == "similarity.json is missing"
    assert "ga_fitness_history" not in loaded.unavailable


def test_schema_four_similarity_artifact_disables_every_schema_five_dependent_aspect(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    schema_four = (
        Path(__file__).parents[3] / "examples" / "scientific_stack" / "example_run_artifacts" / "similarity.json"
    )
    (run_directory / "similarity.json").write_bytes(schema_four.read_bytes())

    loaded = load_dashboard_run(run_directory)

    assert loaded.similarity is None
    assert loaded.fano_allan_diagnostic is None
    assert loaded.transition_fidelity_diagnostic is None
    assert loaded.c2st_diagnostic is None
    for aspect_id in ("similarity_scores", "multiscale_discrepancy", "fano_allan", "transition_fidelity", "c2st"):
        assert "similarity.json is unavailable" in loaded.unavailable[aspect_id]


@pytest.mark.parametrize("artifact_as_directory", (False, True))
def test_invalid_or_unreadable_similarity_degrades_only_similarity_views(
    tmp_path: Path,
    artifact_as_directory: bool,
) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    path = run_directory / "similarity.json"
    if artifact_as_directory:
        path.unlink()
        path.mkdir()
    else:
        path.write_bytes(b"{}\n")

    loaded = load_dashboard_run(run_directory)

    assert loaded.similarity is None
    assert "similarity.json is unavailable" in loaded.unavailable["similarity_scores"]
    assert loaded.best_model is not None


def test_missing_or_invalid_best_model_degrades_without_affecting_views(tmp_path: Path) -> None:
    missing_directory = copy_checked_dashboard_run(tmp_path / "missing")
    (missing_directory / "best_model.json").unlink()
    invalid_directory = copy_checked_dashboard_run(tmp_path / "invalid")
    (invalid_directory / "best_model.json").write_bytes(b"{}\n")

    missing = load_dashboard_run(missing_directory)
    invalid = load_dashboard_run(invalid_directory)

    assert missing.best_model is None
    assert "best_model" not in missing.unavailable
    assert invalid.best_model is None
    assert "best_model.json is unavailable" in invalid.unavailable["best_model"]
    assert invalid.similarity is not None


def test_ga_history_requires_a_valid_experiment_configuration(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    (run_directory / "experiment.toml").write_text("not = [valid\n", encoding="utf-8")

    loaded = load_dashboard_run(run_directory)

    assert loaded.experiment is None
    assert loaded.history is None
    assert "experiment.toml" in loaded.unavailable["ga_fitness_history"]


def test_missing_ga_history_disables_only_the_history_aspect_when_experiment_is_valid(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    (run_directory / "ga_history.csv").unlink()

    loaded = load_dashboard_run(run_directory)

    assert loaded.experiment is not None
    assert loaded.similarity is not None
    assert loaded.history is None
    assert loaded.unavailable["ga_fitness_history"] == "ga_history.csv is missing"
    assert "similarity_scores" not in loaded.unavailable
    assert "multiscale_discrepancy" not in loaded.unavailable


def test_loaded_dashboard_run_exposes_immutable_arrays_and_mapping(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)

    loaded = load_dashboard_run(run_directory)

    assert loaded.reference.timestamps.flags.writeable is False
    assert loaded.reference.directions.flags.writeable is False
    assert loaded.reference.frame_lengths.flags.writeable is False
    assert loaded.generated.timestamps.flags.writeable is False
    assert loaded.generated.directions.flags.writeable is False
    assert loaded.generated.frame_lengths.flags.writeable is False
    assert type(loaded.unavailable) is MappingProxyType


def test_failed_second_load_does_not_mutate_a_previous_dashboard_run(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    first = load_dashboard_run(run_directory)

    (run_directory / "capture.json").write_text("{}", encoding="utf-8")

    with pytest.raises(TrafficlabError, match="capture.json"):
        load_dashboard_run(run_directory)

    assert first.reference.timestamps[0] == 0.0
    assert first.generated.timestamps[0] == 0.0
    assert dict(first.unavailable) == {}


def test_similarity_identity_matches_the_exact_bytes_used_for_parsing_when_rewritten_mid_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
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

    metadata = parse_capture_metadata(
        (run_directory / "capture.json").read_bytes(), source=run_directory / "capture.json"
    )
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


def test_invalid_history_block_order_disables_ga_history_at_load_time(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    history_path = run_directory / "ga_history.csv"
    lines = history_path.read_text(encoding="utf-8").splitlines()
    history_path.write_text("\n".join((lines[0], lines[2], lines[1], *lines[3:])) + "\n", encoding="utf-8")

    loaded = load_dashboard_run(run_directory)

    assert loaded.history is None
    assert loaded.identities.history_sha256 is None
    assert "ascending lexical family rows followed by overall" in loaded.unavailable["ga_fitness_history"]


def test_history_counts_must_match_experiment_population_at_load_time(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    history_path = run_directory / "ga_history.csv"
    content = history_path.read_text(encoding="utf-8")
    changed = content.replace("0,family,markov_renewal,1,1,", "0,family,markov_renewal,2,1,", 1)
    history_path.write_text(changed.replace("0,overall,,8,6,", "0,overall,,9,6,", 1), encoding="utf-8")

    loaded = load_dashboard_run(run_directory)

    assert loaded.history is None
    assert "population_size" in loaded.unavailable["ga_fitness_history"]


def test_impossible_history_mean_disables_ga_history_at_load_time(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    history_path = run_directory / "ga_history.csv"
    content = history_path.read_text(encoding="utf-8")
    family_original = "0,family,mmpp,1,1,0.7220251606878283,0.7220251606878283,0,2"
    family_replacement = "0,family,mmpp,1,1,0.7220251606878283,1.0,0,2"
    overall_original = "0,overall,,8,6,0.8636028231040744,0.5735595501451406,0,3"
    overall_replacement = "0,overall,,8,6,0.8636028231040744,0.608306405059162,0,3"
    assert family_original in content
    assert overall_original in content
    history_path.write_text(
        content.replace(family_original, family_replacement, 1).replace(
            overall_original, overall_replacement, 1
        ),
        encoding="utf-8",
    )

    loaded = load_dashboard_run(run_directory)

    assert loaded.history is None
    assert loaded.identities.history_sha256 is None
    assert "history mean_fitness is not feasible for valid_count" in loaded.unavailable["ga_fitness_history"]


def test_unreadable_history_disables_ga_view_without_rejecting_run(tmp_path: Path) -> None:
    run_directory = copy_checked_dashboard_run(tmp_path)
    history_path = run_directory / "ga_history.csv"
    history_path.unlink()
    history_path.mkdir()

    loaded = load_dashboard_run(run_directory)

    assert loaded.history is None
    assert "could not read ga_history.csv" in loaded.unavailable["ga_fitness_history"]
