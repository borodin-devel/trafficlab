"""Reproduction behavior."""

from __future__ import annotations

import stat
import subprocess
import time as time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import pytest

import scripts.validation_study.cli as vs_cli
import scripts.validation_study.common as vs_common
import scripts.validation_study.prerequisites.commands as vs_prereq_commands
import scripts.validation_study.records as vs_records
import scripts.validation_study.results.codec as vs_results_codec
import scripts.validation_study.results.reproduction as vs_results_reproduction
import trafficlab.common.config_io as trafficlab_common_config_io
from tests.support.validation_study.builders import (
    changed_config_paths,
    response_headers,
    study_result_value,
    valid_result_document,
)
from tests.support.validation_study.repository import write_study_inputs
from tests.support.validation_study.runners import StudyIdentityRunner
from tests.unit.validation.study.orchestration._support import (
    install_primary_orchestration_doubles,
    reject_direct_reproduction_mutation,
    source_record_and_config,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.pipeline.types import RunResult


@pytest.mark.parametrize("invalid_derived", ["variation", "summary"])
def test_study_validates_variation_and_summaries_before_any_reproduction_runner_call(
    invalid_derived: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, expected = write_study_inputs(repository_root)
    events: list[str] = []
    install_primary_orchestration_doubles(monkeypatch, expected, events)
    if invalid_derived == "variation":

        def invalid_variation(*_args: object, **_kwargs: object) -> tuple[vs_common.JsonObject, ...]:
            raise TrafficlabError("metric precondition failed", corrective_action="preserve evidence")

        monkeypatch.setattr(
            vs_cli,
            "natural_variation",
            invalid_variation,
        )
    else:
        invalid = [cast(vs_common.JsonObject, vs_common.thaw_json(value)) for value in expected.workload_summaries]
        cast(dict[str, object], invalid[0]["runtime"])["count"] = 2

        def invalid_summaries(_records: Sequence[vs_records.StudyRunRecord]) -> tuple[vs_common.JsonObject, ...]:
            return tuple(invalid)

        monkeypatch.setattr(vs_cli, "workload_summaries", invalid_summaries)

    with pytest.raises((TrafficlabError, ValueError)):
        vs_cli.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=lambda _path: cast(RunResult, object()),
            runner=StudyIdentityRunner(repository_root),
            perf_counter=iter(float(value) for value in range(30)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )
    assert "reproduction" not in events
    assert not (
        repository_root / "runs" / "validation_study" / "study-1" / "realized-configs" / "reproduction.toml"
    ).exists()
    assert not (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / "study-1"
        / "10-streaming-r2-reproduction"
    ).exists()
    assert not (repository_root / "examples" / "validation_study" / "results.json").exists()


def test_reproduction_changes_only_run_directory_seeds_nothing_and_invokes_exact_nonnested_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    source, base, workload = source_record_and_config(repository_root)
    expected = study_result_value(valid_result_document(repository_root)).reproduction
    calls: list[tuple[str, ...]] = []
    reconstruction_calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def runner(
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
        calls.append(command)
        assert cwd == repository_root
        assert check is False and capture_output is True and shell is False
        assert timeout == 1230.0
        assert command.count("scripts/run_bounded.sh") == 1
        assert not (repository_root / "runs" / "validation_study" / "study-1" / "10-streaming-r2-reproduction").exists()
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
        return subprocess.CompletedProcess(command, 0, stdout=b"installed cli output\n", stderr=b"")

    def reconstruct(
        root: Path,
        spec: vs_records.StudyRunSpec,
        selected_source: vs_records.StudyRunRecord,
        *,
        command: tuple[str, ...],
        guard_command: tuple[str, ...],
        completed: subprocess.CompletedProcess[bytes],
        elapsed_seconds: float,
        transfer_responses: tuple[vs_common.JsonObject, ...],
    ) -> vs_records.ReproductionRecord:
        assert root == repository_root
        assert selected_source == source
        assert spec.run_id == "10-streaming-r2-reproduction"
        assert elapsed_seconds == 1.0
        assert completed.returncode == 0
        assert len(transfer_responses) == 1
        reconstruction_calls.append((command, guard_command))
        return expected

    monkeypatch.setattr(vs_results_reproduction, "reconstruct_reproduction", reconstruct, raising=False)
    result = vs_results_reproduction.run_cli_reproduction(
        repository_root,
        "study-1",
        base,
        source,
        workload,
        object_size_bytes=4_194_304,
        runner=runner,
        perf_counter=iter((10.0, 11.0)).__next__,
    )

    assert result == expected
    config_path = repository_root / "runs" / "validation_study" / "study-1" / "realized-configs" / "reproduction.toml"
    source_config = trafficlab_common_config_io.load_experiment(
        repository_root / source.run_directory / "experiment.toml"
    )
    reproduction_config = trafficlab_common_config_io.load_experiment(config_path)
    assert changed_config_paths(
        source_config.model_dump(mode="python"), reproduction_config.model_dump(mode="python")
    ) == {"run.directory"}
    config_record = config_path.relative_to(repository_root).as_posix()
    command = ("uv", "run", "--locked", "trafficlab", "run", config_record)
    assert reconstruction_calls == [(command, (*vs_prereq_commands.guard_prefix("20m"), *command))]
    assert calls == [(*vs_prereq_commands.guard_prefix("20m"), *command)]
    evidence = (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / "study-1"
        / "10-streaming-r2-reproduction"
    )
    assert (evidence / "guard.stdout").read_bytes() == b"installed cli output\n"
    assert stat.S_IMODE((evidence / "guard.stdout").stat().st_mode) == 0o600
    assert stat.S_IMODE((evidence / "guard.stderr").stat().st_mode) == 0o600


def test_reproduction_failure_preserves_primary_cause_and_appends_archive_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    source, base, workload = source_record_and_config(repository_root)
    failure = TrafficlabError("installed CLI failed", corrective_action="inspect CLI output")

    def failed_archive(_directory: Path, _prepared: object) -> str:
        return "streaming.headers: read failed"

    monkeypatch.setattr(vs_results_reproduction, "best_effort_archive", failed_archive)

    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise failure

    with pytest.raises(TrafficlabError, match=r"streaming, repeat 2, position 10.*secondary evidence") as captured:
        vs_results_reproduction.run_cli_reproduction(
            repository_root,
            "study-1",
            base,
            source,
            workload,
            object_size_bytes=4_194_304,
            runner=cast(vs_records.CommandRunner, runner),
            perf_counter=lambda: 1.0,
        )

    assert "runs/validation_study/study-1/10-streaming-r2-reproduction" in str(captured.value)
    assert "streaming.headers: read failed" in str(captured.value)
    assert captured.value.__cause__ is failure


@pytest.mark.parametrize(
    "mutation",
    [
        "source-not-streaming-r2",
        "extra-config-change",
        "seeded-artifact",
        "wrong-cli-suffix",
        "nested-guard",
        "nonzero-status",
        "reused-log",
        "winner-best-model-mismatch",
        "evaluate-final-count",
        "unbound-published-comparison",
    ],
)
def test_reproduction_rejects_nonfresh_or_inconsistent_evidence(
    mutation: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    document = valid_result_document(repository_root)
    protocol = cast(vs_common.JsonObject, document["protocol"])
    source = cast(vs_common.JsonObject, cast(list[object], document["runs"])[3])
    reproduction = cast(vs_common.JsonObject, document["reproduction"])
    if mutation == "source-not-streaming-r2":
        source = cast(vs_common.JsonObject, cast(list[object], document["runs"])[0])
    elif mutation == "extra-config-change":
        reproduction["changed_config_fields"] = ["run.directory", "target.image"]
    elif mutation == "seeded-artifact":
        reproduction["seeded_artifact_count"] = 1
    elif mutation == "wrong-cli-suffix":
        cast(list[str], reproduction["command"])[-1] = "wrong.toml"
    elif mutation == "nested-guard":
        guard = cast(list[str], reproduction["guard_command"])
        guard[guard.index("--") + 1 : guard.index("--") + 1] = list(vs_prereq_commands.guard_prefix("20m"))
    elif mutation == "nonzero-status":
        reproduction["guard_exit_status"] = 1
    elif mutation == "winner-best-model-mismatch":
        cast(dict[str, object], reproduction["winner"])["genes"] = [2.0]
    elif reject_direct_reproduction_mutation(mutation, repository_root):
        return

    with pytest.raises(ValueError):
        vs_results_codec._validate_reproduction(  # pyright: ignore[reportPrivateUsage]
            reproduction,
            repository_root=repository_root,
            protocol=protocol,
            source=source,
        )


def test_study_builds_variation_summaries_reproduction_and_publishes_one_canonical_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, expected = write_study_inputs(repository_root)
    events: list[str] = []
    real_publish = vs_results_codec.publish_results
    install_primary_orchestration_doubles(monkeypatch, expected, events)
    published: list[bytes] = []

    def publish(path: Path, value: vs_records.StudyResults, *, repository_root: Path) -> None:
        real_publish(path, value, repository_root=repository_root)
        published.append(path.read_bytes())

    monkeypatch.setattr(vs_cli, "publish_results", publish)
    result = vs_cli.run_study(
        "https://downloads.example.test/object.bin",
        "study-1",
        prerequisite_path,
        repository_root=repository_root,
        run=lambda _path: cast(RunResult, object()),
        runner=StudyIdentityRunner(repository_root),
        perf_counter=iter(float(value) for value in range(30)).__next__,
        utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
    )

    result_path = repository_root / "examples" / "validation_study" / "results.json"
    assert len(published) == 1
    assert result_path.read_bytes() == published[0]
    assert vs_results_codec.parse_study_results(published[0], repository_root=repository_root) == result
    assert vs_results_codec.render_study_results(result) == published[0]
    assert len(result.runs) == 9
    assert len(result.natural_variation) == len(result.workload_summaries) == 3
    assert result.reproduction == expected.reproduction
    assert not (repository_root / "examples" / "validation_study" / "REPORT.md").exists()
