"""Study behavior."""

from __future__ import annotations

import json
import time as time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

import scripts.validation_study.cli as vs_cli
import scripts.validation_study.collection as vs_collection
import scripts.validation_study.common as vs_common
import scripts.validation_study.evidence as vs_evidence
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.records as vs_records
import scripts.validation_study.results.reproduction as vs_results_reproduction
import scripts.validation_study.transfer as vs_transfer
import scripts.validation_study.workloads as vs_workloads
import trafficlab.common.config_io as trafficlab_common_config_io
from tests.support.validation_study.builders import frozen
from tests.support.validation_study.constants import CAPTURE_IMAGE_ID, IMAGE_ID
from tests.support.validation_study.repository import write_study_inputs
from tests.support.validation_study.runners import StudyIdentityRunner
from tests.unit.validation.study.orchestration._support import (
    STUDY_PHASE_CAPTURE_TAG,
    install_primary_orchestration_doubles,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.pipeline.types import RunResult


def test_study_runs_nine_absent_primaries_serially_in_balanced_order_and_times_only_run_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, expected = write_study_inputs(repository_root)
    events: list[str] = []
    install_primary_orchestration_doubles(monkeypatch, expected, events)
    timer_values = iter(float(value) for value in range(20))

    def timer() -> float:
        value = next(timer_values)
        events.append(f"time:{value}")
        return value

    def run(path: Path) -> RunResult:
        events.append(f"run:{path.stem}")
        return cast(RunResult, object())

    runner = StudyIdentityRunner(repository_root)
    result = vs_cli.run_study(
        "https://downloads.example.test/object.bin",
        "study-1",
        prerequisite_path,
        repository_root=repository_root,
        run=run,
        runner=runner,
        perf_counter=timer,
        utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
    )

    assert tuple((record.execution_order, record.run_id) for record in result.runs) == tuple(
        (order, run_id) for order, run_id, _workload, _repeat in vs_common.PRIMARY_ORDER
    )
    for index, (_order, run_id, _workload, _repeat) in enumerate(vs_common.PRIMARY_ORDER):
        segment = events[index * 7 : index * 7 + 7]
        assert segment == [
            f"scratch:{run_id}",
            f"time:{float(index * 2)}",
            f"run:{run_id}",
            f"time:{float(index * 2 + 1)}",
            f"archive:{run_id}",
            f"extract:{run_id}:1.0",
            f"trace:{run_id}",
        ]
    assert events[63:] == ["variation", "summaries", "reproduction", "publish"]
    assert runner.calls[:6] == [
        ("git", "rev-parse", "HEAD"),
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        ("docker", "version", "--format", "{{.Server.Version}}"),
        ("docker", "compose", "version", "--short"),
        ("docker", "image", "inspect", vs_common.TARGET_REFERENCE),
        ("docker", "image", "inspect", STUDY_PHASE_CAPTURE_TAG, "--format", "{{.Id}}"),
    ]
    build = runner.calls[6]
    assert build[:2] == ("docker", "build")
    assert build[build.index("--tag") + 1] == STUDY_PHASE_CAPTURE_TAG
    assert Path(build[build.index("--iidfile") + 1]).name == "capture.iid"
    assert build[-1] == "docker/capture"
    assert runner.calls[7:] == [
        ("docker", "image", "inspect", CAPTURE_IMAGE_ID, "--format", "{{.Id}}"),
        ("docker", "image", "inspect", CAPTURE_IMAGE_ID),
        ("docker", "image", "rm", "--force", STUDY_PHASE_CAPTURE_TAG),
    ]
    for order, run_id, workload, repeat in vs_common.PRIMARY_ORDER:
        record = result.runs[order - 1]
        assert record.key == {"workload": workload, "repeat": repeat}
        assert record.config_path == f"runs/validation_study/study-1/realized-configs/{run_id}.toml"
        assert record.run_directory == f"runs/validation_study/study-1/{run_id}"
        assert record.transfer_evidence_directory.endswith(f"/study-1/{run_id}")


@pytest.mark.parametrize("outcome", ("success", "failure", "interrupt"))
def test_public_study_rebuilds_and_cleans_a_no_residue_capture_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """The legacy study owner leases one cold lock-checked image through all primary runs."""

    repository_root = tmp_path / "repository"
    prerequisite_path, expected = write_study_inputs(repository_root)
    events: list[str] = []
    install_primary_orchestration_doubles(monkeypatch, expected, events)
    runner = StudyIdentityRunner(
        repository_root,
        capture_image_id=CAPTURE_IMAGE_ID,
        capture_image_present=False,
    )
    run_calls: list[Path] = []

    def run(path: Path) -> RunResult:
        assert runner.capture_image_present
        run_calls.append(path)
        if outcome == "failure":
            raise TrafficlabError("controlled study failure", corrective_action="preserve the run")
        if outcome == "interrupt":
            raise KeyboardInterrupt()
        return cast(RunResult, object())

    def invoke() -> vs_records.StudyResults:
        return vs_cli.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=run,
            runner=runner,
            perf_counter=iter(float(value) for value in range(20)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    if outcome == "success":
        result = invoke()
        assert len(result.runs) == 9
        assert result.environment["capture_image_id"] == CAPTURE_IMAGE_ID
    elif outcome == "failure":
        with pytest.raises(TrafficlabError, match="controlled study failure"):
            invoke()
    else:
        with pytest.raises(KeyboardInterrupt):
            invoke()

    tag = STUDY_PHASE_CAPTURE_TAG
    build = next(command for command in runner.calls if command[:2] == ("docker", "build"))
    assert build[build.index("--tag") + 1] == tag
    iidfile = Path(build[build.index("--iidfile") + 1])
    assert not iidfile.exists()
    assert runner.capture_image_cleanup_tags == [tag]
    assert not runner.capture_image_present
    assert len(run_calls) == (9 if outcome == "success" else 1)
    for workload in ("short", "streaming", "bursty"):
        config = trafficlab_common_config_io.load_experiment(
            repository_root / "examples" / "validation_study" / "configs" / f"{workload}.toml"
        )
        assert config.capture.image == CAPTURE_IMAGE_ID


def test_public_study_fails_when_owned_image_cleanup_fails_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed legacy study remains failed if its phase-owned image cannot be removed."""

    repository_root = tmp_path / "repository"
    prerequisite_path, expected = write_study_inputs(repository_root)
    events: list[str] = []
    install_primary_orchestration_doubles(monkeypatch, expected, events)
    runner = StudyIdentityRunner(
        repository_root,
        capture_image_id=CAPTURE_IMAGE_ID,
        capture_image_present=False,
        cleanup_exit_status=1,
    )

    with pytest.raises(TrafficlabError, match="study capture image cleanup failed"):
        vs_cli.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=lambda _path: cast(RunResult, object()),
            runner=runner,
            perf_counter=iter(float(value) for value in range(20)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    assert runner.capture_image_cleanup_tags == [STUDY_PHASE_CAPTURE_TAG]


def test_public_study_preserves_a_primary_base_exception_when_owned_image_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup becomes an ordered secondary diagnostic without replacing an unexpected primary."""

    class ControlledAbort(BaseException):
        pass

    repository_root = tmp_path / "repository"
    prerequisite_path, expected = write_study_inputs(repository_root)
    events: list[str] = []
    install_primary_orchestration_doubles(monkeypatch, expected, events)
    runner = StudyIdentityRunner(
        repository_root,
        capture_image_id=CAPTURE_IMAGE_ID,
        capture_image_present=False,
        cleanup_exit_status=1,
    )

    def abort(_path: Path) -> RunResult:
        raise ControlledAbort("controlled abort")

    with pytest.raises(ControlledAbort) as captured:
        vs_cli.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=abort,
            runner=runner,
            perf_counter=iter(float(value) for value in range(20)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    assert captured.value.__notes__ == [
        "study capture image cleanup failed: could not remove owned study capture image: simulated cleanup failure"
    ]
    assert runner.capture_image_cleanup_tags == [STUDY_PHASE_CAPTURE_TAG]


@pytest.mark.parametrize("mode", ("non-owned", "missing-iid", "missing-prerequisites"))
def test_validated_study_inputs_covers_owned_image_preconditions(
    tmp_path: Path,
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The study input boundary preserves its non-owned and owned-IID validation paths."""

    repository_root = tmp_path / "repository"
    prerequisite_path, _expected = write_study_inputs(repository_root)
    runner = StudyIdentityRunner(repository_root, capture_image_id=CAPTURE_IMAGE_ID)
    retained = vs_prereq_codec.parse_prerequisite_results(
        prerequisite_path.read_bytes(), repository_root=repository_root
    )
    tools = retained.tools
    identity = cast(
        vs_common.JsonObject,
        {
            "git_commit": retained.git_commit,
            "python_version": tools["python_version"],
            "trafficlab_version": tools["trafficlab_version"],
            "docker_engine_version": tools["docker_engine_version"],
            "docker_compose_version": tools["docker_compose_version"],
            "platform": tools["platform"],
        },
    )

    def current_identity(*, repository_root: Path, runner: vs_records.CommandRunner) -> vs_common.JsonObject:
        del repository_root, runner
        return identity

    monkeypatch.setattr(vs_results_reproduction, "_study_identity", current_identity)

    if mode == "non-owned":
        prerequisites, configs, actual_identity, content = vs_results_reproduction.validated_study_inputs(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            runner=runner,
        )
        assert prerequisites.study_id == "study-1"
        assert tuple(configs) == ("short", "streaming", "bursty")
        assert actual_identity["git_commit"] == retained.git_commit
        assert content == prerequisite_path.read_bytes()
    elif mode == "missing-iid":
        with pytest.raises(ValueError, match="study capture IID file is required"):
            vs_results_reproduction.validated_study_inputs(
                "https://downloads.example.test/object.bin",
                "study-1",
                prerequisite_path,
                repository_root=repository_root,
                runner=runner,
                owned_capture_image=vs_collection.PhaseCaptureImage("trafficlab-validation-study-1:capture"),
            )
    else:
        prerequisite_path.unlink()
        with pytest.raises(ValueError, match="could not read Validation Study prerequisites"):
            vs_results_reproduction.validated_study_inputs(
                "https://downloads.example.test/object.bin",
                "study-1",
                prerequisite_path,
                repository_root=repository_root,
                runner=runner,
            )


@pytest.mark.parametrize("mismatch", ("target", "lock"))
def test_public_study_validates_immutable_inputs_before_cold_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    """A bad retained lock or live target cannot create a study-owned capture tag."""

    repository_root = tmp_path / "repository"
    prerequisite_path, expected = write_study_inputs(repository_root)
    events: list[str] = []
    install_primary_orchestration_doubles(monkeypatch, expected, events)
    if mismatch == "lock":
        lock_path = repository_root / "docker" / "capture" / "image-lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["expected_capture_image_id"] = f"sha256:{'8' * 64}"
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
    runner = StudyIdentityRunner(
        repository_root,
        capture_image_id=CAPTURE_IMAGE_ID,
        target_image_id=f"sha256:{'8' * 64}" if mismatch == "target" else IMAGE_ID,
        capture_image_present=False,
    )
    runs: list[Path] = []

    def must_not_run(path: Path) -> RunResult:
        runs.append(path)
        raise AssertionError("immutable validation reached a study primary")

    with pytest.raises(TrafficlabError, match="Validation Study failed validation"):
        vs_cli.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=must_not_run,
            runner=runner,
            perf_counter=iter(float(value) for value in range(20)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    assert runs == []
    assert not [command for command in runner.calls if command[:2] == ("docker", "build")]
    assert runner.capture_image_cleanup_tags == []
    if mismatch == "target":
        assert ("docker", "image", "inspect", vs_common.TARGET_REFERENCE) in runner.calls


@pytest.mark.parametrize(
    ("build_exit_status", "write_build_iid", "build_iid_content", "inspected_capture_image_id"),
    (
        (1, True, None, None),
        (0, False, None, None),
        (0, True, "not-an-image-id", None),
        (0, True, f"sha256:{'8' * 64}", None),
        (0, True, None, f"sha256:{'8' * 64}"),
    ),
)
def test_public_study_rejects_invalid_cold_build_before_any_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_exit_status: int,
    write_build_iid: bool,
    build_iid_content: str | None,
    inspected_capture_image_id: str | None,
) -> None:
    """Every cold-build/IID identity failure stops the legacy study before a primary run."""

    repository_root = tmp_path / "repository"
    prerequisite_path, expected = write_study_inputs(repository_root)
    events: list[str] = []
    install_primary_orchestration_doubles(monkeypatch, expected, events)
    runner = StudyIdentityRunner(
        repository_root,
        capture_image_id=CAPTURE_IMAGE_ID,
        capture_image_present=False,
        build_exit_status=build_exit_status,
        write_build_iid=write_build_iid,
        build_iid_content=build_iid_content,
        inspected_capture_image_id=inspected_capture_image_id,
    )
    runs: list[Path] = []

    def must_not_run(path: Path) -> RunResult:
        runs.append(path)
        raise AssertionError("invalid cold build reached a study primary")

    with pytest.raises(TrafficlabError, match="Validation Study failed validation"):
        vs_cli.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=must_not_run,
            runner=runner,
            perf_counter=iter(float(value) for value in range(20)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    assert runs == []
    assert runner.capture_image_cleanup_tags == [STUDY_PHASE_CAPTURE_TAG]


@pytest.mark.parametrize("failed_position", [1, 5, 9])
def test_primary_failure_stops_preserves_evidence_and_publishes_no_results(
    failed_position: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, expected = write_study_inputs(repository_root)
    events: list[str] = []
    install_primary_orchestration_doubles(monkeypatch, expected, events)
    failure = TrafficlabError("simulated primary failure", corrective_action="inspect the failed run")

    def failed_archive(_directory: Path, _prepared: object) -> str:
        return "short.headers: disk full"

    monkeypatch.setattr(vs_cli, "best_effort_archive", failed_archive)

    def run(path: Path) -> RunResult:
        position = len([event for event in events if event.startswith("run:")]) + 1
        events.append(f"run:{path.stem}")
        if position == failed_position:
            raise failure
        return cast(RunResult, object())

    with pytest.raises(TrafficlabError, match=rf"position {failed_position}.*restart with a new study ID") as captured:
        vs_cli.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=run,
            runner=StudyIdentityRunner(repository_root),
            perf_counter=iter(float(value) for value in range(30)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    _order, run_id, workload, repeat = vs_common.PRIMARY_ORDER[failed_position - 1]
    assert workload in str(captured.value)
    assert f"repeat {repeat}" in str(captured.value)
    assert f"runs/validation_study/study-1/{run_id}" in str(captured.value)
    assert "secondary evidence archive failure: short.headers: disk full" in str(captured.value)
    assert captured.value.__cause__ is failure
    assert not (repository_root / "examples" / "validation_study" / "results.json").exists()
    assert "reproduction" not in events
    assert len([event for event in events if event.startswith("run:")]) == failed_position


def test_primary_archive_failure_preserves_the_archive_cause_and_secondary_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, expected = write_study_inputs(repository_root)
    events: list[str] = []
    install_primary_orchestration_doubles(monkeypatch, expected, events)
    failure = OSError("simulated archive failure")

    def fail_archive(*_args: object, **_kwargs: object) -> tuple[vs_common.JsonObject, ...]:
        raise failure

    def failed_best_effort(_directory: Path, _prepared: object) -> str:
        return "short.headers: disk full"

    monkeypatch.setattr(vs_cli, "archive_transfer_evidence", fail_archive)
    monkeypatch.setattr(vs_cli, "best_effort_archive", failed_best_effort)

    with pytest.raises(TrafficlabError, match=r"short, repeat 1, position 1.*secondary evidence") as captured:
        vs_cli.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=lambda _path: cast(RunResult, object()),
            runner=StudyIdentityRunner(repository_root),
            perf_counter=iter((1.0, 2.0)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    assert "runs/validation_study/study-1/01-short-r1" in str(captured.value)
    assert captured.value.__cause__ is failure
    assert not (repository_root / "examples" / "validation_study" / "results.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-url",
        "wrong-live-image",
        "wrong-live-capture-image",
        "existing-run",
        "existing-evidence",
        "reused-record",
    ],
)
def test_study_rejects_incompatible_prerequisites_existing_targets_and_any_reuse(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path, expected = write_study_inputs(repository_root)
    events: list[str] = []
    install_primary_orchestration_doubles(monkeypatch, expected, events)
    if mutation == "existing-run":
        (repository_root / "runs" / "validation_study" / "study-1" / "01-short-r1").mkdir(parents=True)
    elif mutation == "existing-evidence":
        (
            repository_root / "examples" / "validation_study" / ".study-work" / "evidence" / "study-1" / "01-short-r1"
        ).mkdir(parents=True)
    elif mutation == "reused-record":
        original_extract = vs_evidence.extract_primary_record

        def reused(
            root: Path,
            spec: vs_records.StudyRunSpec,
            workload: vs_workloads.WorkloadSpec,
            result: RunResult,
            elapsed: float,
            responses: tuple[vs_common.JsonObject, ...],
        ) -> vs_records.StudyRunRecord:
            record = original_extract(root, spec, workload, result, elapsed, responses)
            return replace(
                record, reuse=frozen({"capture": True, "best_model": False, "generated": False, "similarity": False})
            )

        monkeypatch.setattr(vs_cli, "extract_primary_record", reused)

    url = "https://other.example.test/object.bin" if mutation == "wrong-url" else expected.protocol["url"]
    runner = StudyIdentityRunner(
        repository_root,
        target_image_id=f"sha256:{'9' * 64}" if mutation == "wrong-live-image" else IMAGE_ID,
        capture_image_id=(f"sha256:{'8' * 64}" if mutation == "wrong-live-capture-image" else f"sha256:{'d' * 64}"),
    )
    with pytest.raises(TrafficlabError):
        vs_cli.run_study(
            cast(str, url),
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=lambda _path: cast(RunResult, object()),
            runner=runner,
            perf_counter=iter(float(value) for value in range(30)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )
    assert "reproduction" not in events
    assert not (repository_root / "examples" / "validation_study" / "results.json").exists()


def test_best_effort_archive_returns_secondary_diagnostics(tmp_path: Path) -> None:
    scratch = tmp_path / "missing.headers"

    diagnostic = vs_transfer.best_effort_archive(
        tmp_path / "missing-evidence",
        {"streaming.headers": (scratch, 1)},
    )

    assert diagnostic is not None
    assert "streaming.headers" in diagnostic

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    scratch.mkdir()
    nonregular = vs_transfer.best_effort_archive(
        evidence,
        {"streaming.headers": (scratch, 1)},
    )
    assert nonregular == "streaming.headers: scratch is not a regular file"


def test_audited_publisher_rejects_a_different_destination_id_before_audit(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    candidate = repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "candidate-study"
    candidate.mkdir(parents=True)

    with pytest.raises(TrafficlabError, match="candidate ID"):
        vs_results_reproduction.publish_audited_bundle(candidate, "destination-study", repository_root=repository_root)

    assert candidate.is_dir()
    assert not (repository_root / "examples" / "validation_study" / "evidence" / "destination-study").exists()
