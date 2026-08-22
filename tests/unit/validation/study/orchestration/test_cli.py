"""Cli behavior."""

from __future__ import annotations

import subprocess
import time as time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import pytest

import scripts.validation_study.cli as vs_cli
import scripts.validation_study.collection as vs_collection
import scripts.validation_study.common as vs_common
import scripts.validation_study.evidence as vs_evidence
import scripts.validation_study.records as vs_records
import scripts.validation_study.results.reproduction as vs_results_reproduction
import scripts.validation_study.workloads as vs_workloads
import trafficlab.capture.stage as trafficlab_capture_stage
from tests.support.validation_study.artifacts import OfflinePrimaryBaseline, materialize_offline_primary_baseline
from tests.support.validation_study.builders import response_headers, score, study_result_value, valid_result_document
from tests.support.validation_study.constants import CAPTURE_BYTES, REFERENCE_BYTES
from tests.support.validation_study.runners import StudyIdentityRunner
from tests.unit.validation.study.orchestration._support import (
    COLLECTION_PHASE_CAPTURE_TAG,
)
from trafficlab.artifacts.io import append_run_log
from trafficlab.capture.stage import CaptureResult
from trafficlab.capture.validation import validate_capture_pair
from trafficlab.comparison.stage import compare_experiment
from trafficlab.fitting.genetic.types import TrialResult
from trafficlab.fitting.stage import fit_experiment
from trafficlab.generation.stage import generate_experiment
from trafficlab.pipeline.stage import run_experiment
from trafficlab.pipeline.types import RunDependencies, RunResult
from trafficlab.preflight.stage import open_or_prepare_experiment
from trafficlab.preflight.types import PreparedExperiment


def test_cli_reproduction_reconstructs_fresh_fresh_simulation_lineage_and_honest_source_deltas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    offline_primary_baselines: dict[str, OfflinePrimaryBaseline],
) -> None:
    evaluate_calls = 0
    real_evaluate_final = vs_results_reproduction.evaluate_final

    def count_evaluate(*args: Any, **kwargs: Any) -> tuple[TrialResult, ...]:
        nonlocal evaluate_calls
        evaluate_calls += 1
        return real_evaluate_final(*args, **kwargs)

    monkeypatch.setattr(vs_results_reproduction, "evaluate_final", count_evaluate)
    repository_root, source_result, source_spec, workload, source_responses = materialize_offline_primary_baseline(
        offline_primary_baselines["streaming"]
    )
    source = vs_evidence.extract_primary_record(
        repository_root,
        source_spec,
        workload,
        source_result,
        1.5,
        source_responses,
    )
    base = vs_workloads.build_base_config(
        workload,
        repository_root=repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        capture_image_id=f"sha256:{'d' * 64}",
    )

    def cli_runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        command = tuple(argv)
        assert cwd == repository_root
        assert check is False and capture_output is True and shell is False
        assert timeout == 1230.0
        config_path = repository_root / command[-1]

        def capture(_path: Path, prepared: PreparedExperiment) -> CaptureResult:
            capture_path = prepared.run_directory / "capture.json"
            reference_path = prepared.run_directory / "reference.pcapng"
            capture_path.write_bytes(CAPTURE_BYTES)
            reference_path.write_bytes(REFERENCE_BYTES)
            inspection = validate_capture_pair(capture_path, reference_path, deadline=None)
            append_run_log(
                prepared.run_directory,
                {
                    "event": "capture_published",
                    "packet_count": inspection.packet_count,
                    "path": str(reference_path),
                    "project_name": "trafficlab-validation-study-reproduction",
                    "reused": False,
                    "stage": "capture",
                },
            )
            return CaptureResult(prepared.run_directory, reference_path, inspection.packet_count, 0, reused=False)

        run_experiment(
            config_path,
            dependencies=RunDependencies(
                open_or_prepare_experiment,
                capture,
                fit_experiment,
                generate_experiment,
                compare_experiment,
            ),
        )
        scratch = (
            repository_root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "mount"
            / "study-1"
            / "streaming.headers"
        )
        scratch.write_bytes(response_headers(0, 4_194_303))
        return subprocess.CompletedProcess(command, 0, stdout=b"reproduced\n", stderr=b"")

    reproduction = vs_results_reproduction.run_cli_reproduction(
        repository_root,
        "study-1",
        base,
        source,
        workload,
        object_size_bytes=4_194_304,
        runner=cli_runner,
        perf_counter=iter((20.0, 22.0)).__next__,
    )

    document = cast(vs_common.JsonObject, vs_common.thaw_json(reproduction.document))
    assert evaluate_calls == 1
    assert document["fresh_simulation"]["source"] == "post_cli_evaluate_final"  # type: ignore[index]
    assert document["seeded_artifact_count"] == 0
    assert document["reuse"] == {"capture": False, "best_model": False, "generated": False, "similarity": False}
    assert document["raw_sequence"] == {
        "seed": 97,
        "observation_window_seconds": 10.0,
        "trial_event_count": cast(dict[str, object], document["generated"])["packet_count"],
        "final_event_count": cast(dict[str, object], document["generated"])["packet_count"],
        "raw_events_equal": True,
        "fresh_simulation_score_reproduced": True,
        "reparsed_event_count": cast(dict[str, object], document["generated"])["packet_count"],
        "reparsed_matches_quantized": True,
    }
    comparison = cast(dict[str, object], document["comparison_to_source"])
    assert comparison["winner_family_equal"] is True
    assert comparison["winner_genes_equal"] is True
    assert comparison["winner_selection_fitness_delta"] == 0.0
    assert comparison["reference_similarity"] == score(1.0)


def test_study_cli_requires_exact_url_id_and_prerequisite_path_and_never_wraps_itself(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    calls: list[tuple[object, ...]] = []
    expected = study_result_value(valid_result_document(repository_root))

    def run_study_double(*args: object, **kwargs: object) -> vs_records.StudyResults:
        calls.append((*args, kwargs))
        return expected

    monkeypatch.setattr(vs_cli, "run_study", run_study_double)
    prerequisite_record = "examples/validation_study/prerequisites.json"
    assert (
        vs_cli.main(
            [
                "study",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                prerequisite_record,
            ],
            repository_root=repository_root,
            run=lambda _path: cast(RunResult, object()),
            runner=StudyIdentityRunner(repository_root),
            perf_counter=lambda: 1.0,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )
        == 0
    )
    assert len(calls) == 1
    positional = calls[0][:-1]
    keywords = cast(dict[str, object], calls[0][-1])
    assert positional == (
        "https://downloads.example.test/object.bin",
        "study-1",
        repository_root / prerequisite_record,
    )
    assert keywords["repository_root"] == repository_root
    assert "run_bounded.sh" not in str(calls)
    assert "study completed" in capsys.readouterr().out

    invalid = (
        ["study"],
        [
            "study",
            "--url",
            "http://example.test/object",
            "--study-id",
            "study-1",
            "--prerequisites",
            prerequisite_record,
        ],
        [
            "study",
            "--url",
            "https://downloads.example.test/object.bin",
            "--study-id",
            "INVALID",
            "--prerequisites",
            prerequisite_record,
        ],
        [
            "study",
            "--url",
            "https://downloads.example.test/object.bin",
            "--study-id",
            "study-1",
            "--prerequisites",
            "../outside.json",
        ],
    )
    for arguments in invalid:
        assert vs_cli.main(arguments, repository_root=repository_root) == 2
        assert capsys.readouterr().err
    assert len(calls) == 1


def test_collect_cli_uses_only_frozen_prerequisite_inputs_and_the_candidate_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    inputs: vs_common.CollectionInputs = (
        {"frozen": "environment"},
        b"frozen prerequisites\n",
        {},
        {},
        4_194_304,
    )
    calls: list[dict[str, object]] = []

    def load_inputs(*_args: object, **_kwargs: object) -> vs_common.CollectionInputs:
        return inputs

    def collect(**kwargs: object) -> Path:
        calls.append(kwargs)
        return repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"

    monkeypatch.setattr(vs_cli, "collection_inputs_from_prerequisites", load_inputs, raising=False)
    monkeypatch.setattr(vs_cli, "collect_validation_candidate", collect)

    runner = StudyIdentityRunner(repository_root)
    assert (
        vs_cli.main(
            [
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/prerequisites.json",
            ],
            repository_root=repository_root,
            runner=runner,
        )
        == 0
    )
    assert calls == [
        {
            "repository_root": repository_root,
            "study_id": "study-1",
            "url": "https://downloads.example.test/object.bin",
            "attempt": repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1",
            "environment": inputs[0],
            "retained_prerequisites": inputs[1],
            "prerequisite_files": inputs[2],
            "configs": inputs[3],
            "run": run_experiment,
            "capture": trafficlab_capture_stage.capture_experiment,
            "object_size_bytes": 4_194_304,
            "owned_capture_image": vs_collection.PhaseCaptureImage(tag=COLLECTION_PHASE_CAPTURE_TAG),
            "perf_counter": time.perf_counter,
            "runner": runner,
        }
    ]
    assert "candidate collected" in capsys.readouterr().out


def test_collect_cli_rejects_an_in_repository_noncanonical_prerequisite_path_before_loading_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the frozen canonical prerequisite path can begin collection."""

    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    calls: list[str] = []

    def should_not_load(*_args: object, **_kwargs: object) -> vs_common.CollectionInputs:
        calls.append("inputs")
        raise AssertionError("noncanonical prerequisite path reached collection inputs")

    def should_not_collect(**_kwargs: object) -> Path:
        calls.append("collect")
        raise AssertionError("noncanonical prerequisite path reached candidate collection")

    monkeypatch.setattr(vs_cli, "collection_inputs_from_prerequisites", should_not_load)
    monkeypatch.setattr(vs_cli, "collect_validation_candidate", should_not_collect)

    assert (
        vs_cli.main(
            [
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/other.json",
            ],
            repository_root=repository_root,
            runner=StudyIdentityRunner(repository_root),
        )
        == 2
    )
    assert calls == []
    assert not (repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1").exists()
    assert not (repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1").exists()


def test_prerequisites_cli_publishes_the_canonical_prerequisite_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The public prerequisite command reports its canonical retained path after success."""

    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    calls: list[tuple[str, str, Path]] = []

    class Result:
        study_id = "study-1"

    def prerequisites(
        url: str,
        study_id: str,
        *,
        repository_root: Path,
        **_kwargs: object,
    ) -> vs_records.PrerequisiteResults:
        calls.append((url, study_id, repository_root))
        return cast(vs_records.PrerequisiteResults, Result())

    monkeypatch.setattr(vs_cli, "run_prerequisites", prerequisites)

    assert (
        vs_cli.main(
            ["prerequisites", "--url", "https://downloads.example.test/object.bin", "--study-id", "study-1"],
            repository_root=repository_root,
            runner=StudyIdentityRunner(repository_root),
        )
        == 0
    )
    assert calls == [("https://downloads.example.test/object.bin", "study-1", repository_root)]
    assert str(repository_root / "examples" / "validation_study" / "prerequisites.json") in capsys.readouterr().out


@pytest.mark.parametrize("relative", (True, False))
def test_publish_cli_resolves_relative_and_absolute_candidate_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: bool,
) -> None:
    """The publish command resolves only relative candidates against the supplied repository root."""

    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    candidate = repository_root / "candidate"
    calls: list[tuple[Path, str, Path]] = []

    def publish(candidate_path: Path, study_id: str, *, repository_root: Path) -> Path:
        calls.append((candidate_path, study_id, repository_root))
        return repository_root / "examples" / "validation_study" / "evidence" / study_id

    monkeypatch.setattr(vs_cli, "publish_audited_bundle", publish)
    argument = Path("candidate") if relative else candidate

    assert (
        vs_cli.main(
            ["publish", "--candidate", str(argument), "--study-id", "study-1"],
            repository_root=repository_root,
            runner=StudyIdentityRunner(repository_root),
        )
        == 0
    )
    assert calls == [(candidate, "study-1", repository_root)]
