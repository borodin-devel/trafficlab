import copy
import json
import os
from pathlib import Path
from typing import cast

import pytest

import trafficlab.artifacts as artifacts
import trafficlab.comparison as comparison
from tests.fixtures.paths import PIPELINE_FIXTURE_ROOT
from trafficlab.artifacts import append_run_log, create_run_directory
from trafficlab.comparison import compare_experiment
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import render_effective_config
from trafficlab.errors import TrafficlabError
from trafficlab.pcapng import parse_pcapng_bytes
from trafficlab.trace import (
    CaptureMetadata,
    TraceEvent,
    parse_capture_metadata,
)

_REPOSITORY = Path(__file__).parents[2]
_EXAMPLE_DATA = PIPELINE_FIXTURE_ROOT
_EXPECTED_AGGREGATE_SCORE = 0.5956427487361957


def _prepare_run(valid_config_data: dict[str, object], tmp_path: Path) -> tuple[Path, Path, ExperimentConfig]:
    data = copy.deepcopy(valid_config_data)
    run_directory = tmp_path / "run"
    cast(dict[str, object], data["run"])["directory"] = str(run_directory)
    config = ExperimentConfig.model_validate(data)
    caller_path = tmp_path / "caller.toml"
    caller_path.write_bytes(render_effective_config(config))
    create_run_directory(config)
    for artifact_name in (
        "capture.json",
        "reference.pcapng",
        "best_model.json",
        "generated.pcapng",
    ):
        (run_directory / artifact_name).write_bytes((_EXAMPLE_DATA / artifact_name).read_bytes())
    return caller_path, run_directory, config


def _log_records(run_directory: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (run_directory / "run.log").read_text(encoding="utf-8").splitlines()]


def test_append_run_log_writes_one_sorted_fsynced_json_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A buffered or nondeterministic detail record could disappear or vary after a reported stage result."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    (run_directory / "run.log").write_bytes(b'{"prior":true}\n')
    real_fsync = os.fsync
    fsync_calls: list[int] = []

    def observed_fsync(file_descriptor: int) -> None:
        fsync_calls.append(file_descriptor)
        real_fsync(file_descriptor)

    monkeypatch.setattr(artifacts.os, "fsync", observed_fsync)

    append_run_log(run_directory, {"z": 2, "event": "comparison_succeeded", "a": 1})

    assert (run_directory / "run.log").read_bytes() == (
        b'{"prior":true}\n{"a":1,"event":"comparison_succeeded","z":2}\n'
    )
    assert len(fsync_calls) == 1


def test_append_run_log_rejects_non_json_detail_before_opening_the_log(tmp_path: Path) -> None:
    """Invalid detail must not create or partially append run.log."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()

    with pytest.raises(TrafficlabError, match="could not encode run log record"):
        append_run_log(run_directory, {"invalid": object()})

    assert not (run_directory / "run.log").exists()


def test_append_run_log_reports_a_durability_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed record fsync must be reported instead of allowing the stage to claim durable logging."""
    run_directory = tmp_path / "run"
    run_directory.mkdir()

    def fail_fsync(_file_descriptor: int) -> None:
        raise OSError("injected append fsync failure")

    monkeypatch.setattr(artifacts.os, "fsync", fail_fsync)

    with pytest.raises(TrafficlabError, match="could not append run log.*append fsync failure"):
        append_run_log(run_directory, {"event": "comparison_failed"})


def test_compare_experiment_rejects_a_caller_snapshot_mismatch_and_logs_it(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Using caller similarity settings after a run starts would make stored results contradict experiment.toml."""
    caller_path, run_directory, config = _prepare_run(valid_config_data, tmp_path)
    changed = config.model_copy(update={"run": config.run.model_copy(update={"master_seed": 777})})
    caller_path.write_bytes(render_effective_config(changed))

    with pytest.raises(TrafficlabError, match="does not match the authoritative run snapshot"):
        compare_experiment(caller_path)

    assert not (run_directory / "similarity.json").exists()
    assert _log_records(run_directory)[-1] == {
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
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)
    evaluated_sha256: dict[str, str] = {}

    def mutate_after_metadata_read(content: bytes, *, source: Path) -> CaptureMetadata:
        evaluated_sha256["capture_json"] = comparison.sha256_bytes(content)
        source.write_bytes(b"changed metadata after read")
        return parse_capture_metadata(content, source=source)

    def mutate_after_pcapng_read(
        content: bytes,
        metadata: CaptureMetadata,
        *,
        source: Path,
    ) -> tuple[TraceEvent, ...]:
        input_name = "reference_pcapng" if source.name == "reference.pcapng" else "generated_pcapng"
        evaluated_sha256[input_name] = comparison.sha256_bytes(content)
        source.write_bytes(f"changed {source.name} after read".encode())
        return parse_pcapng_bytes(content, metadata, source=source)

    monkeypatch.setattr(comparison, "parse_capture_metadata", mutate_after_metadata_read)
    monkeypatch.setattr(comparison, "parse_pcapng_bytes", mutate_after_pcapng_read)

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
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)
    existing = run_directory / "similarity.json"
    existing.write_bytes(b"caller-owned-result\n")

    with pytest.raises(TrafficlabError, match="already exists"):
        compare_experiment(caller_path)

    assert existing.read_bytes() == b"caller-owned-result\n"
    assert _log_records(run_directory)[-1]["failure_kind"] == "publication"
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_existing_identical_similarity_is_reused_and_success_is_logged(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """A coordinator retry must reuse the exact completed comparison and record that decision explicitly."""
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)

    first = compare_experiment(caller_path)
    first_content = (run_directory / "similarity.json").read_bytes()
    second = compare_experiment(caller_path)

    assert second == first
    assert (run_directory / "similarity.json").read_bytes() == first_content
    assert _log_records(run_directory)[-2]["reused"] is False
    assert _log_records(run_directory)[-1] == {
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
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)
    real_link = os.link
    winner = b"racing-winner\n"

    def collide(source: str | Path, destination: str | Path) -> None:
        destination_path = Path(destination)
        destination_path.write_bytes(winner)
        real_link(source, destination)

    monkeypatch.setattr(comparison.os, "link", collide)

    with pytest.raises(TrafficlabError, match="already exists"):
        compare_experiment(caller_path)

    assert (run_directory / "similarity.json").read_bytes() == winner
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_publication_failure_reports_temp_cleanup_failure_without_removing_unowned_files(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup must stay bounded to the owned temporary name and retain the original publication error."""
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)
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

    monkeypatch.setattr(comparison.os, "link", fail_link)
    monkeypatch.setattr(comparison.os, "unlink", fail_temp_unlink)

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
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)
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

    monkeypatch.setattr(comparison.os, "unlink", replace_after_failed_unlink)

    with pytest.raises(
        TrafficlabError,
        match="similarity artifact was published.*owned temporary file cleanup failed.*post-link cleanup failure",
    ):
        compare_experiment(caller_path)

    assert len(unlink_attempts) == 1
    assert unlink_attempts[0].read_bytes() == replacement
    assert (
        comparison.load_comparison_result(run_directory / "similarity.json").aggregate_score
        == _EXPECTED_AGGREGATE_SCORE
    )
    assert _log_records(run_directory)[-1]["failure_kind"] == "publication"


def test_valid_but_changed_rendered_result_is_rejected_before_temporary_publication(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema validation alone must not publish valid JSON that differs from the evaluated typed result."""
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)
    real_render = comparison.render_comparison_result

    def render_different_result(result: comparison.ComparisonResult) -> bytes:
        changed_document = result.as_dict()
        methods = cast(dict[str, object], changed_document["methods"])
        cast(dict[str, object], methods["autocorrelation"])["weight"] = 0.1
        cast(dict[str, object], methods["frame_size_ks"])["weight"] = 0.4
        changed_document["aggregate_score"] = sum(
            cast(float, method["score"]) * cast(float, method["weight"])
            for method in cast(dict[str, dict[str, object]], methods).values()
        )
        return real_render(comparison.ComparisonResult.from_dict(changed_document))

    monkeypatch.setattr(comparison, "render_comparison_result", render_different_result)

    with pytest.raises(TrafficlabError, match="rendered similarity artifact.*canonical evaluated result"):
        compare_experiment(caller_path)

    assert not (run_directory / "similarity.json").exists()
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_numeric_type_tampering_is_rejected_by_canonical_temporary_validation(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typed equality treats diagnostic integer 3 and float 3.0 alike, but their artifact bytes are not equivalent."""
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)
    real_load = comparison.load_comparison_result

    def tamper_count_type(path: Path) -> comparison.ComparisonResult:
        content = path.read_bytes()
        changed = content.replace(b'"reference_count":5', b'"reference_count":5.0', 1)
        assert changed != content
        path.write_bytes(changed)
        return real_load(path)

    monkeypatch.setattr(comparison, "load_comparison_result", tamper_count_type)

    with pytest.raises(TrafficlabError, match="reference_count must be an integer"):
        compare_experiment(caller_path)

    assert not (run_directory / "similarity.json").exists()
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_serialization_failure_before_temp_creation_is_reported_without_cleanup_side_effects(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-write publication failure has no owned file to clean and must preserve adjacent files."""
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)
    unowned = run_directory / "unowned.txt"
    unowned.write_text("keep", encoding="utf-8")

    def fail_render(_result: comparison.ComparisonResult) -> bytes:
        raise ValueError("injected serialization failure")

    monkeypatch.setattr(comparison, "render_comparison_result", fail_render)

    with pytest.raises(TrafficlabError, match="injected serialization failure"):
        compare_experiment(caller_path)

    assert unowned.read_text(encoding="utf-8") == "keep"
    assert not (run_directory / "similarity.json").exists()
    assert list(run_directory.glob(".similarity.json.*.tmp")) == []


def test_input_failure_remains_primary_when_failure_logging_also_fails(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secondary run-log error must not conceal which required comparison input was missing."""
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)
    (run_directory / "capture.json").unlink()

    def fail_log(_run_directory: Path, _record: object) -> None:
        raise TrafficlabError("injected logging failure", corrective_action="repair logging")

    monkeypatch.setattr(comparison, "append_run_log", fail_log)

    with pytest.raises(TrafficlabError, match="capture metadata.*additionally.*injected logging failure") as error:
        compare_experiment(caller_path)

    assert error.value.corrective_action == "verify capture.json exists and is readable"


def test_comparison_result_assembly_failure_is_translated_and_logged(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid experiment must not leak a raw ValueError when retained component diagnostics break result invariants."""
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)

    def invalid_window_type(*_args: object) -> comparison.SimilarityResult:
        return comparison.SimilarityResult(1.0, {"observation_window_seconds": 3})

    monkeypatch.setattr(comparison, "frame_size_ks", invalid_window_type)

    with pytest.raises(TrafficlabError, match="invalid comparison result") as error:
        compare_experiment(caller_path)

    assert error.value.corrective_action == "report the comparison result assembly defect"
    assert _log_records(run_directory)[-1] == {
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
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)

    def fail_log(_run_directory: Path, _record: object) -> None:
        raise TrafficlabError("injected logging failure", corrective_action="repair logging")

    monkeypatch.setattr(comparison, "append_run_log", fail_log)

    with pytest.raises(TrafficlabError, match="comparison result was published.*injected logging failure"):
        compare_experiment(caller_path)

    assert (
        comparison.load_comparison_result(run_directory / "similarity.json").aggregate_score
        == _EXPECTED_AGGREGATE_SCORE
    )


def test_missing_authoritative_snapshot_is_logged_as_an_input_failure(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Filename existence must never substitute for loading the authoritative effective configuration."""
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)
    (run_directory / "experiment.toml").unlink()

    with pytest.raises(TrafficlabError, match="experiment configuration"):
        compare_experiment(caller_path)

    assert _log_records(run_directory)[-1]["failure_kind"] == "evaluation_or_input"


def test_invalid_capture_metadata_aborts_before_parsing_or_publication(
    valid_config_data: dict[str, object], tmp_path: Path
) -> None:
    """Direction classification must use the run's strict capture metadata rather than a permissive default."""
    caller_path, run_directory, _config = _prepare_run(valid_config_data, tmp_path)
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
