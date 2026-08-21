from __future__ import annotations

import hashlib
import json
import platform
import shutil
import stat
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from scripts import run_validation_study as study
from tests.support.validation_study import (
    CAPTURE_BYTES,
    CAPTURE_IMAGE_ID,
    HASH,
    IMAGE_ID,
    REFERENCE_BYTES,
    ROOT,
    OfflinePrimaryBaseline,
    StudyIdentityRunner,
    changed_config_paths,
    frozen,
    materialize_offline_primary_baseline,
    response_headers,
    score,
    study_result_value,
    terminal_checkpoint_and_best,
    transfer_responses,
    trial_result,
    valid_prerequisite,
    valid_result_document,
    write_checked_configs,
    write_retained_prerequisite_evidence,
    write_study_inputs,
)
from trafficlab.artifacts.io import append_run_log
from trafficlab.capture.lineage import CaptureResult
from trafficlab.capture.validation import validate_capture_pair
from trafficlab.common.compatibility import ContentIdentity, identify_bytes
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.trace import Direction, TraceEvent
from trafficlab.comparison.stage import compare_experiment
from trafficlab.fitting.genetic.types import TrialResult
from trafficlab.fitting.stage import fit_experiment
from trafficlab.generation.stage import generate_experiment
from trafficlab.pipeline.stage import run_experiment
from trafficlab.pipeline.types import RunDependencies, RunResult
from trafficlab.preflight.stage import open_or_prepare_experiment
from trafficlab.preflight.types import PreparedExperiment

STUDY_PHASE_CAPTURE_TAG = "trafficlab-validation-study-1:study-capture"

COLLECTION_PHASE_CAPTURE_TAG = "trafficlab-validation-study-1:collection-capture"


def write_collection_compatible_inputs(repository_root: Path) -> Path:
    """Write retained inputs that bind the local revalidation boundary exactly."""

    repository_root.mkdir()
    shutil.copy2(ROOT / "uv.lock", repository_root / "uv.lock")
    prerequisite, _contents = write_checked_configs(repository_root, capture_image_id=CAPTURE_IMAGE_ID)
    tools = cast(study.JsonObject, study._thaw_json(prerequisite.tools))  # pyright: ignore[reportPrivateUsage]
    tools.update(
        {
            "host_architecture": platform.machine(),
            "kernel_release": platform.release(),
            "platform": platform.platform(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "uv_lock_sha256": hashlib.sha256((repository_root / "uv.lock").read_bytes()).hexdigest(),
        }
    )
    images = cast(study.JsonObject, study._thaw_json(prerequisite.images))  # pyright: ignore[reportPrivateUsage]
    images["capture_image_id"] = CAPTURE_IMAGE_ID
    prerequisite = replace(prerequisite, tools=frozen(tools), images=frozen(images))
    prerequisite = write_retained_prerequisite_evidence(repository_root, prerequisite)
    capture_root = repository_root / "docker" / "capture"
    shutil.copy2(ROOT / "docker" / "capture" / "image-lock.json", capture_root / "image-lock.json")
    prerequisite_path = repository_root / "examples" / "validation_study" / "prerequisites.json"
    prerequisite_path.write_bytes(study.render_prerequisite_results(prerequisite))
    return prerequisite_path


def install_primary_orchestration_doubles(
    monkeypatch: pytest.MonkeyPatch,
    expected: study.StudyResults,
    events: list[str],
) -> None:
    records = iter(expected.runs)

    def prepare(
        _root: Path,
        _study_id: str,
        run_id: str,
        _workload: study.WorkloadSpec,
    ) -> dict[str, tuple[Path, int]]:
        events.append(f"scratch:{run_id}")
        return {}

    def archive(
        _root: Path,
        _study_id: str,
        run_id: str,
        workload: study.WorkloadSpec,
        _prepared: object,
        *,
        object_size_bytes: int,
    ) -> tuple[study.JsonObject, ...]:
        assert object_size_bytes == 4_194_304
        events.append(f"archive:{run_id}")
        return tuple(cast(study.JsonObject, value) for value in transfer_responses("study-1", run_id, workload.name))

    def extract(
        _root: Path,
        spec: study.StudyRunSpec,
        _workload: study.WorkloadSpec,
        _result: object,
        elapsed: float,
        _responses: tuple[study.JsonObject, ...],
    ) -> study.StudyRunRecord:
        events.append(f"extract:{spec.run_id}:{elapsed}")
        return next(records)

    def load_reference(run_directory: Path) -> tuple[TraceEvent, ...]:
        events.append(f"trace:{run_directory.name}")
        return (TraceEvent(0.0, Direction.OUTBOUND, 60), TraceEvent(1.0, Direction.INBOUND, 80))

    def variation(
        _records: Sequence[study.StudyRunRecord],
        _traces: object,
        _settings: object,
    ) -> tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject]:
        events.append("variation")
        return expected.natural_variation

    def summaries(
        _records: Sequence[study.StudyRunRecord],
    ) -> tuple[study.FrozenJsonObject, study.FrozenJsonObject, study.FrozenJsonObject]:
        events.append("summaries")
        return expected.workload_summaries

    def reproduction(*_args: object, **_kwargs: object) -> study.ReproductionRecord:
        events.append("reproduction")
        return expected.reproduction

    def publish(*_args: object, **_kwargs: object) -> None:
        events.append("publish")

    monkeypatch.setattr(study, "prepare_transfer_scratch", prepare)
    monkeypatch.setattr(study, "archive_transfer_evidence", archive)
    monkeypatch.setattr(study, "extract_primary_record", extract)
    monkeypatch.setattr(study, "_load_reference_trace", load_reference, raising=False)
    monkeypatch.setattr(study, "natural_variation", variation)
    monkeypatch.setattr(study, "workload_summaries", summaries)
    monkeypatch.setattr(study, "_run_cli_reproduction", reproduction, raising=False)
    monkeypatch.setattr(study, "_publish_results", publish)
    monkeypatch.setattr(study.platform, "python_version", lambda: "3.12.3")
    monkeypatch.setattr(study.platform, "platform", lambda: "Linux-test")


def source_record_and_config(
    repository_root: Path,
) -> tuple[study.StudyRunRecord, study.ExperimentConfig, study.WorkloadSpec]:
    document = valid_result_document(repository_root)
    source = study_result_value(document).runs[3]
    workload = {item.name: item for item in study.workload_specs(valid_prerequisite().url)}["streaming"]
    base = study.build_base_config(
        workload,
        repository_root=repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        capture_image_id=f"sha256:{'d' * 64}",
    )
    source_directory = repository_root / source.run_directory
    source_config = base.model_copy(
        update={"run": base.run.model_copy(update={"directory": source_directory.resolve()})}
    )
    source_directory.mkdir(parents=True)
    (source_directory / "experiment.toml").write_bytes(study.render_effective_config(source_config))
    return source, base, workload


def reject_direct_reproduction_mutation(mutation: str, repository_root: Path) -> bool:
    if mutation == "reused-log":
        with pytest.raises(ValueError, match="reused"):
            study._fresh_run_log_proofs(  # pyright: ignore[reportPrivateUsage]
                (
                    {"event": "capture_published", "stage": "capture", "reused": False},
                    {"event": "best_model_reused"},
                    {"event": "comparison_succeeded", "reused": False},
                    {"event": "run_completed"},
                )
            )
        return True
    if mutation == "evaluate-final-count":
        with pytest.raises(ValueError, match="exactly one"):
            study._sole_final_trial(  # pyright: ignore[reportPrivateUsage]
                (trial_result(97, 0.5), trial_result(97, 0.5))
            )
        return True
    if mutation == "unbound-published-comparison":
        _state, _best, comparison = terminal_checkpoint_and_best(repository_root)
        with pytest.raises(ValueError, match="lineage"):
            study._require_published_lineage(  # pyright: ignore[reportPrivateUsage]
                comparison,
                comparison,
                {"capture.json": b"capture", "reference.pcapng": b"reference", "generated.pcapng": b"generated"},
                ContentIdentity(size=1, sha256=HASH),
            )
        return True
    return False


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
    result = study.run_study(
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
        (order, run_id) for order, run_id, _workload, _repeat in study.PRIMARY_ORDER
    )
    for index, (_order, run_id, _workload, _repeat) in enumerate(study.PRIMARY_ORDER):
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
        ("docker", "image", "inspect", study.TARGET_REFERENCE),
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
    for order, run_id, workload, repeat in study.PRIMARY_ORDER:
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

    def invoke() -> study.StudyResults:
        return study.run_study(
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
        config = study.load_experiment(
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
        study.run_study(
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
        study.run_study(
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
    retained = study.parse_prerequisite_results(prerequisite_path.read_bytes(), repository_root=repository_root)
    tools = retained.tools
    identity = cast(
        study.JsonObject,
        {
            "git_commit": retained.git_commit,
            "python_version": tools["python_version"],
            "trafficlab_version": tools["trafficlab_version"],
            "docker_engine_version": tools["docker_engine_version"],
            "docker_compose_version": tools["docker_compose_version"],
            "platform": tools["platform"],
        },
    )

    def current_identity(*, repository_root: Path, runner: study.CommandRunner) -> study.JsonObject:
        del repository_root, runner
        return identity

    monkeypatch.setattr(study, "_study_identity", current_identity)

    if mode == "non-owned":
        prerequisites, configs, actual_identity, content = study._validated_study_inputs(  # pyright: ignore[reportPrivateUsage]
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
            study._validated_study_inputs(  # pyright: ignore[reportPrivateUsage]
                "https://downloads.example.test/object.bin",
                "study-1",
                prerequisite_path,
                repository_root=repository_root,
                runner=runner,
                owned_capture_image=study._PhaseCaptureImage("trafficlab-validation-study-1:capture"),  # pyright: ignore[reportPrivateUsage]
            )
    else:
        prerequisite_path.unlink()
        with pytest.raises(ValueError, match="could not read Validation Study prerequisites"):
            study._validated_study_inputs(  # pyright: ignore[reportPrivateUsage]
                "https://downloads.example.test/object.bin",
                "study-1",
                prerequisite_path,
                repository_root=repository_root,
                runner=runner,
            )


def test_phase_capture_image_reports_iid_cleanup_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A temporary IID-file cleanup failure remains an actionable owner-boundary error."""

    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    iidfile = tmp_path / "capture.iid"
    runner = StudyIdentityRunner(repository_root, capture_image_id=CAPTURE_IMAGE_ID)
    original_unlink = Path.unlink

    def fail_iid_unlink(self: Path, *, missing_ok: bool = False) -> None:
        if self == iidfile:
            raise OSError("simulated IID cleanup failure")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_iid_unlink)
    with pytest.raises(ValueError, match="could not remove study capture IID file"):
        study._establish_phase_capture_image(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            phase="study",
            expected_image_id=CAPTURE_IMAGE_ID,
            capture_lock_image_id=CAPTURE_IMAGE_ID,
            owned_capture_image=study._PhaseCaptureImage("trafficlab-validation-study-1:capture"),  # pyright: ignore[reportPrivateUsage]
            iidfile=iidfile,
            runner=runner,
        )
    assert iidfile.exists()


def test_phase_capture_image_preserves_build_failure_when_iid_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IID cleanup remains secondary when a cold build has already failed."""

    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    iidfile = tmp_path / "capture.iid"
    runner = StudyIdentityRunner(
        repository_root,
        capture_image_id=CAPTURE_IMAGE_ID,
        build_exit_status=1,
    )
    original_unlink = Path.unlink

    def fail_iid_unlink(self: Path, *, missing_ok: bool = False) -> None:
        if self == iidfile:
            raise OSError("simulated IID cleanup failure")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_iid_unlink)
    with pytest.raises(ValueError, match="could not cold-build study capture image") as captured:
        study._establish_phase_capture_image(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            phase="study",
            expected_image_id=CAPTURE_IMAGE_ID,
            capture_lock_image_id=CAPTURE_IMAGE_ID,
            owned_capture_image=study._PhaseCaptureImage("trafficlab-validation-study-1:capture"),  # pyright: ignore[reportPrivateUsage]
            iidfile=iidfile,
            runner=runner,
        )
    assert captured.value.__notes__ == [
        "study capture IID file cleanup failed: could not remove study capture IID file "
        f"{iidfile}: simulated IID cleanup failure"
    ]
    assert iidfile.exists()


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
        study.run_study(
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
        assert ("docker", "image", "inspect", study.TARGET_REFERENCE) in runner.calls


def test_public_study_rejects_a_conflicting_phase_capture_tag_before_any_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy study route never adopts or removes a stale phase tag."""

    repository_root = tmp_path / "repository"
    prerequisite_path, expected = write_study_inputs(repository_root)
    events: list[str] = []
    install_primary_orchestration_doubles(monkeypatch, expected, events)
    runner = StudyIdentityRunner(
        repository_root,
        capture_image_id=CAPTURE_IMAGE_ID,
        capture_image_present=False,
        owned_capture_tags={STUDY_PHASE_CAPTURE_TAG},
    )
    runs: list[Path] = []

    def must_not_run(path: Path) -> RunResult:
        runs.append(path)
        raise AssertionError("conflicting phase tag reached a study primary")

    with pytest.raises(TrafficlabError, match="study capture image tag already exists"):
        study.run_study(
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
    assert STUDY_PHASE_CAPTURE_TAG in runner.owned_capture_tags


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
        study.run_study(
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

    monkeypatch.setattr(study, "_best_effort_archive", failed_archive)

    def run(path: Path) -> RunResult:
        position = len([event for event in events if event.startswith("run:")]) + 1
        events.append(f"run:{path.stem}")
        if position == failed_position:
            raise failure
        return cast(RunResult, object())

    with pytest.raises(TrafficlabError, match=rf"position {failed_position}.*restart with a new study ID") as captured:
        study.run_study(
            "https://downloads.example.test/object.bin",
            "study-1",
            prerequisite_path,
            repository_root=repository_root,
            run=run,
            runner=StudyIdentityRunner(repository_root),
            perf_counter=iter(float(value) for value in range(30)).__next__,
            utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
        )

    _order, run_id, workload, repeat = study.PRIMARY_ORDER[failed_position - 1]
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

    def fail_archive(*_args: object, **_kwargs: object) -> tuple[study.JsonObject, ...]:
        raise failure

    def failed_best_effort(_directory: Path, _prepared: object) -> str:
        return "short.headers: disk full"

    monkeypatch.setattr(study, "archive_transfer_evidence", fail_archive)
    monkeypatch.setattr(study, "_best_effort_archive", failed_best_effort)

    with pytest.raises(TrafficlabError, match=r"short, repeat 1, position 1.*secondary evidence") as captured:
        study.run_study(
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
        original_extract = study.extract_primary_record

        def reused(
            root: Path,
            spec: study.StudyRunSpec,
            workload: study.WorkloadSpec,
            result: RunResult,
            elapsed: float,
            responses: tuple[study.JsonObject, ...],
        ) -> study.StudyRunRecord:
            record = original_extract(root, spec, workload, result, elapsed, responses)
            return replace(
                record, reuse=frozen({"capture": True, "best_model": False, "generated": False, "similarity": False})
            )

        monkeypatch.setattr(study, "extract_primary_record", reused)

    url = "https://other.example.test/object.bin" if mutation == "wrong-url" else expected.protocol["url"]
    runner = StudyIdentityRunner(
        repository_root,
        target_image_id=f"sha256:{'9' * 64}" if mutation == "wrong-live-image" else IMAGE_ID,
        capture_image_id=(f"sha256:{'8' * 64}" if mutation == "wrong-live-capture-image" else f"sha256:{'d' * 64}"),
    )
    with pytest.raises(TrafficlabError):
        study.run_study(
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

        def invalid_variation(*_args: object, **_kwargs: object) -> tuple[study.JsonObject, ...]:
            raise TrafficlabError("metric precondition failed", corrective_action="preserve evidence")

        monkeypatch.setattr(
            study,
            "natural_variation",
            invalid_variation,
        )
    else:
        invalid = [
            cast(study.JsonObject, study._thaw_json(value))  # pyright: ignore[reportPrivateUsage]
            for value in expected.workload_summaries
        ]
        cast(dict[str, object], invalid[0]["runtime"])["count"] = 2

        def invalid_summaries(_records: Sequence[study.StudyRunRecord]) -> tuple[study.JsonObject, ...]:
            return tuple(invalid)

        monkeypatch.setattr(study, "workload_summaries", invalid_summaries)

    with pytest.raises((TrafficlabError, ValueError)):
        study.run_study(
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
        spec: study.StudyRunSpec,
        selected_source: study.StudyRunRecord,
        *,
        command: tuple[str, ...],
        guard_command: tuple[str, ...],
        completed: subprocess.CompletedProcess[bytes],
        elapsed_seconds: float,
        transfer_responses: tuple[study.JsonObject, ...],
    ) -> study.ReproductionRecord:
        assert root == repository_root
        assert selected_source == source
        assert spec.run_id == "10-streaming-r2-reproduction"
        assert elapsed_seconds == 1.0
        assert completed.returncode == 0
        assert len(transfer_responses) == 1
        reconstruction_calls.append((command, guard_command))
        return expected

    monkeypatch.setattr(study, "reconstruct_reproduction", reconstruct, raising=False)
    result = study._run_cli_reproduction(  # pyright: ignore[reportPrivateUsage]
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
    source_config = study.load_experiment(repository_root / source.run_directory / "experiment.toml")
    reproduction_config = study.load_experiment(config_path)
    assert changed_config_paths(
        source_config.model_dump(mode="python"), reproduction_config.model_dump(mode="python")
    ) == {"run.directory"}
    config_record = config_path.relative_to(repository_root).as_posix()
    command = ("uv", "run", "--locked", "trafficlab", "run", config_record)
    assert reconstruction_calls == [(command, (*study._guard_prefix("20m"), *command))]  # pyright: ignore[reportPrivateUsage]
    assert calls == [(*study._guard_prefix("20m"), *command)]  # pyright: ignore[reportPrivateUsage]
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

    monkeypatch.setattr(study, "_best_effort_archive", failed_archive)

    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise failure

    with pytest.raises(TrafficlabError, match=r"streaming, repeat 2, position 10.*secondary evidence") as captured:
        study._run_cli_reproduction(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            "study-1",
            base,
            source,
            workload,
            object_size_bytes=4_194_304,
            runner=cast(study.CommandRunner, runner),
            perf_counter=lambda: 1.0,
        )

    assert "runs/validation_study/study-1/10-streaming-r2-reproduction" in str(captured.value)
    assert "streaming.headers: read failed" in str(captured.value)
    assert captured.value.__cause__ is failure


def test_best_effort_archive_returns_secondary_diagnostics(tmp_path: Path) -> None:
    scratch = tmp_path / "missing.headers"

    diagnostic = study._best_effort_archive(  # pyright: ignore[reportPrivateUsage]
        tmp_path / "missing-evidence",
        {"streaming.headers": (scratch, 1)},
    )

    assert diagnostic is not None
    assert "streaming.headers" in diagnostic

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    scratch.mkdir()
    nonregular = study._best_effort_archive(  # pyright: ignore[reportPrivateUsage]
        evidence,
        {"streaming.headers": (scratch, 1)},
    )
    assert nonregular == "streaming.headers: scratch is not a regular file"


def test_cli_reproduction_reconstructs_fresh_fresh_simulation_lineage_and_honest_source_deltas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    offline_primary_baselines: dict[str, OfflinePrimaryBaseline],
) -> None:
    evaluate_calls = 0
    real_evaluate_final = study.evaluate_final

    def count_evaluate(*args: Any, **kwargs: Any) -> tuple[TrialResult, ...]:
        nonlocal evaluate_calls
        evaluate_calls += 1
        return real_evaluate_final(*args, **kwargs)

    monkeypatch.setattr(study, "evaluate_final", count_evaluate)
    repository_root, source_result, source_spec, workload, source_responses = materialize_offline_primary_baseline(
        offline_primary_baselines["streaming"]
    )
    source = study.extract_primary_record(
        repository_root,
        source_spec,
        workload,
        source_result,
        1.5,
        source_responses,
    )
    base = study.build_base_config(
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

    reproduction = study._run_cli_reproduction(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        "study-1",
        base,
        source,
        workload,
        object_size_bytes=4_194_304,
        runner=cli_runner,
        perf_counter=iter((20.0, 22.0)).__next__,
    )

    document = cast(study.JsonObject, study._thaw_json(reproduction.document))  # pyright: ignore[reportPrivateUsage]
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
    protocol = cast(study.JsonObject, document["protocol"])
    source = cast(study.JsonObject, cast(list[object], document["runs"])[3])
    reproduction = cast(study.JsonObject, document["reproduction"])
    if mutation == "source-not-streaming-r2":
        source = cast(study.JsonObject, cast(list[object], document["runs"])[0])
    elif mutation == "extra-config-change":
        reproduction["changed_config_fields"] = ["run.directory", "target.image"]
    elif mutation == "seeded-artifact":
        reproduction["seeded_artifact_count"] = 1
    elif mutation == "wrong-cli-suffix":
        cast(list[str], reproduction["command"])[-1] = "wrong.toml"
    elif mutation == "nested-guard":
        guard = cast(list[str], reproduction["guard_command"])
        guard[guard.index("--") + 1 : guard.index("--") + 1] = list(
            study._guard_prefix("20m")  # pyright: ignore[reportPrivateUsage]
        )
    elif mutation == "nonzero-status":
        reproduction["guard_exit_status"] = 1
    elif mutation == "winner-best-model-mismatch":
        cast(dict[str, object], reproduction["winner"])["genes"] = [2.0]
    elif reject_direct_reproduction_mutation(mutation, repository_root):
        return

    with pytest.raises(ValueError):
        study._validate_reproduction(  # pyright: ignore[reportPrivateUsage]
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
    real_publish = study._publish_results  # pyright: ignore[reportPrivateUsage]
    install_primary_orchestration_doubles(monkeypatch, expected, events)
    published: list[bytes] = []

    def publish(path: Path, value: study.StudyResults, *, repository_root: Path) -> None:
        real_publish(path, value, repository_root=repository_root)
        published.append(path.read_bytes())

    monkeypatch.setattr(study, "_publish_results", publish)
    result = study.run_study(
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
    assert study.parse_study_results(published[0], repository_root=repository_root) == result
    assert study.render_study_results(result) == published[0]
    assert len(result.runs) == 9
    assert len(result.natural_variation) == len(result.workload_summaries) == 3
    assert result.reproduction == expected.reproduction
    assert not (repository_root / "examples" / "validation_study" / "REPORT.md").exists()


def test_study_cli_requires_exact_url_id_and_prerequisite_path_and_never_wraps_itself(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    calls: list[tuple[object, ...]] = []
    expected = study_result_value(valid_result_document(repository_root))

    def run_study_double(*args: object, **kwargs: object) -> study.StudyResults:
        calls.append((*args, kwargs))
        return expected

    monkeypatch.setattr(study, "run_study", run_study_double)
    prerequisite_record = "examples/validation_study/prerequisites.json"
    assert (
        study.main(
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
        assert study.main(arguments, repository_root=repository_root) == 2
        assert capsys.readouterr().err
    assert len(calls) == 1


def test_collect_cli_uses_only_frozen_prerequisite_inputs_and_the_candidate_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    inputs: study.CollectionInputs = (
        {"frozen": "environment"},
        b"frozen prerequisites\n",
        {},
        {},
        4_194_304,
    )
    calls: list[dict[str, object]] = []

    def load_inputs(*_args: object, **_kwargs: object) -> study.CollectionInputs:
        return inputs

    def collect(**kwargs: object) -> Path:
        calls.append(kwargs)
        return repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"

    monkeypatch.setattr(study, "_collection_inputs_from_prerequisites", load_inputs, raising=False)
    monkeypatch.setattr(study, "collect_validation_candidate", collect)

    runner = StudyIdentityRunner(repository_root)
    assert (
        study.main(
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
            "capture": study.capture_experiment,
            "object_size_bytes": 4_194_304,
            "owned_capture_image": study._PhaseCaptureImage(  # pyright: ignore[reportPrivateUsage]
                tag=COLLECTION_PHASE_CAPTURE_TAG
            ),
            "perf_counter": study.time.perf_counter,
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

    def should_not_load(*_args: object, **_kwargs: object) -> study.CollectionInputs:
        calls.append("inputs")
        raise AssertionError("noncanonical prerequisite path reached collection inputs")

    def should_not_collect(**_kwargs: object) -> Path:
        calls.append("collect")
        raise AssertionError("noncanonical prerequisite path reached candidate collection")

    monkeypatch.setattr(study, "_collection_inputs_from_prerequisites", should_not_load)
    monkeypatch.setattr(study, "collect_validation_candidate", should_not_collect)

    assert (
        study.main(
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
    ) -> study.PrerequisiteResults:
        calls.append((url, study_id, repository_root))
        return cast(study.PrerequisiteResults, Result())

    monkeypatch.setattr(study, "run_prerequisites", prerequisites)

    assert (
        study.main(
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

    monkeypatch.setattr(study, "publish_audited_bundle", publish)
    argument = Path("candidate") if relative else candidate

    assert (
        study.main(
            ["publish", "--candidate", str(argument), "--study-id", "study-1"],
            repository_root=repository_root,
            runner=StudyIdentityRunner(repository_root),
        )
        == 0
    )
    assert calls == [(candidate, "study-1", repository_root)]


def test_collection_inputs_revalidates_current_checked_inputs(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)

    environment, retained, files, configs, object_size_bytes = study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        prerequisite_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        runner=StudyIdentityRunner(repository_root, capture_image_id=CAPTURE_IMAGE_ID),
    )

    parsed = cast(study.JsonObject, study.parse_retained_prerequisites(retained))
    retained_environment = cast(study.JsonObject, parsed["environment"])
    assert retained_environment["capture_image_id"] == CAPTURE_IMAGE_ID
    assert environment["source_commit"] == "c" * 40
    assert environment["source_tree"] == "e" * 40
    assert set(files) == {
        "prerequisites/docker_matrix.command.json",
        "prerequisites/docker_matrix.junit.xml",
        "prerequisites/docker_matrix.status.json",
        "prerequisites/docker_matrix.stderr",
        "prerequisites/docker_matrix.stdout",
        "prerequisites/internet_smoke.command.json",
        "prerequisites/internet_smoke.junit.xml",
        "prerequisites/internet_smoke.status.json",
        "prerequisites/internet_smoke.stderr",
        "prerequisites/internet_smoke.stdout",
        "headers/prerequisites/00-prerequisites/capability.headers",
    }
    assert tuple(configs) == ("short", "streaming", "bursty")
    assert object_size_bytes == 4_194_304
    with pytest.raises(TrafficlabError, match="collection Git tree must remain exactly clean"):
        study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            prerequisite_path,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            runner=StudyIdentityRunner(repository_root, capture_image_id=CAPTURE_IMAGE_ID, dirty=True),
        )
    (repository_root / "uv.lock").unlink()
    with pytest.raises(TrafficlabError, match="Validation Study collection inputs are invalid"):
        study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            prerequisite_path,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            runner=StudyIdentityRunner(repository_root, capture_image_id=CAPTURE_IMAGE_ID),
        )


@pytest.mark.parametrize("outcome", ("success", "failure", "interrupt"))
def test_public_collection_rebuilds_and_cleans_a_no_residue_capture_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """Public collection owns a fresh lock-checked image without relying on a prerequisite cache tag."""

    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    runner = StudyIdentityRunner(
        repository_root,
        capture_image_id=CAPTURE_IMAGE_ID,
        capture_image_present=False,
    )
    calls: list[Path] = []

    def collect(**_kwargs: object) -> Path:
        calls.append(repository_root)
        if outcome == "failure":
            raise TrafficlabError("controlled collection failure", corrective_action="preserve the attempt")
        if outcome == "interrupt":
            raise KeyboardInterrupt()
        return repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"

    monkeypatch.setattr(study, "collect_validation_candidate", collect)
    argv = (
        "collect",
        "--url",
        "https://downloads.example.test/object.bin",
        "--study-id",
        "study-1",
        "--prerequisites",
        "examples/validation_study/prerequisites.json",
    )
    if outcome == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            study.main(argv, repository_root=repository_root, runner=runner)
    else:
        assert study.main(argv, repository_root=repository_root, runner=runner) == (0 if outcome == "success" else 2)

    attempt = repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1"
    tag = COLLECTION_PHASE_CAPTURE_TAG
    assert study.cold_capture_build_argv(tag, attempt / "collection-capture.iid") in runner.calls  # pyright: ignore[reportPrivateUsage]
    assert runner.capture_image_cleanup_tags == [tag]
    assert not runner.capture_image_present
    assert not (attempt / "collection-capture.iid").exists()
    assert calls == [repository_root]


def test_public_collection_rejects_a_conflicting_phase_capture_tag_before_candidate_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale phase tag is not overwritten or treated as collection-owned state."""

    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    runner = StudyIdentityRunner(
        repository_root,
        capture_image_id=CAPTURE_IMAGE_ID,
        capture_image_present=False,
        owned_capture_tags={COLLECTION_PHASE_CAPTURE_TAG},
    )
    calls: list[str] = []

    def must_not_collect(**_kwargs: object) -> Path:
        calls.append("candidate")
        raise AssertionError("conflicting phase tag reached candidate collection")

    monkeypatch.setattr(study, "collect_validation_candidate", must_not_collect)
    assert (
        study.main(
            (
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/prerequisites.json",
            ),
            repository_root=repository_root,
            runner=runner,
        )
        == 2
    )
    assert calls == []
    assert not [command for command in runner.calls if command[:2] == ("docker", "build")]
    assert runner.capture_image_cleanup_tags == []
    assert COLLECTION_PHASE_CAPTURE_TAG in runner.owned_capture_tags


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
def test_public_collection_rejects_invalid_cold_build_before_candidate_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build_exit_status: int,
    write_build_iid: bool,
    build_iid_content: str | None,
    inspected_capture_image_id: str | None,
) -> None:
    """A failed, missing, malformed, or mismatched cold build cannot begin collection."""

    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    runner = StudyIdentityRunner(
        repository_root,
        capture_image_id=CAPTURE_IMAGE_ID,
        capture_image_present=False,
        build_exit_status=build_exit_status,
        write_build_iid=write_build_iid,
        build_iid_content=build_iid_content,
        inspected_capture_image_id=inspected_capture_image_id,
    )
    calls: list[str] = []

    def must_not_collect(**_kwargs: object) -> Path:
        calls.append("candidate")
        raise AssertionError("invalid cold build reached candidate collection")

    monkeypatch.setattr(study, "collect_validation_candidate", must_not_collect)
    assert (
        study.main(
            (
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/prerequisites.json",
            ),
            repository_root=repository_root,
            runner=runner,
        )
        == 2
    )
    attempt = repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1"
    assert calls == []
    assert runner.capture_image_cleanup_tags == [COLLECTION_PHASE_CAPTURE_TAG]
    assert not (attempt / "collection-capture.iid").exists()


@pytest.mark.parametrize("mismatch", ("target", "lock"))
def test_public_collection_validates_immutable_inputs_before_cold_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    """Collection refuses a bad live target or retained lock before candidate creation."""

    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
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
    calls: list[str] = []

    def must_not_collect(**_kwargs: object) -> Path:
        calls.append("candidate")
        raise AssertionError("immutable validation reached candidate collection")

    monkeypatch.setattr(study, "collect_validation_candidate", must_not_collect)
    assert (
        study.main(
            (
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/prerequisites.json",
            ),
            repository_root=repository_root,
            runner=runner,
        )
        == 2
    )
    assert calls == []
    assert not [command for command in runner.calls if command[:2] == ("docker", "build")]
    assert runner.capture_image_cleanup_tags == []
    if mismatch == "target":
        assert ("docker", "image", "inspect", study.TARGET_REFERENCE) in runner.calls


@pytest.mark.parametrize("failure", ("config", "retained-artifact"))
def test_public_collection_defers_cold_build_until_all_retained_inputs_validate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Late immutable config/evidence failures must not create a capture-image lease."""

    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    if failure == "config":
        config_path = repository_root / "examples" / "validation_study" / "configs" / "short.toml"
        config_path.write_bytes(config_path.read_bytes() + b"\n# retained config mutation\n")
        runner = StudyIdentityRunner(
            repository_root,
            capture_image_id=CAPTURE_IMAGE_ID,
            capture_image_present=False,
        )
    else:
        capability_header = (
            repository_root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "evidence"
            / "study-1"
            / "00-prerequisites"
            / "capability.headers"
        )

        def mutate_retained_header() -> None:
            capability_header.write_bytes(b"retained evidence changed after its initial validation\n")

        runner = StudyIdentityRunner(
            repository_root,
            capture_image_id=CAPTURE_IMAGE_ID,
            capture_image_present=False,
            on_target_inspect=mutate_retained_header,
        )
    candidates: list[str] = []
    runs: list[Path] = []
    captures: list[Path] = []

    def must_not_collect(**_kwargs: object) -> Path:
        candidates.append("candidate")
        raise AssertionError("late retained validation reached candidate collection")

    def must_not_run(path: Path) -> RunResult:
        runs.append(path)
        raise AssertionError("late retained validation reached a training run")

    def must_not_capture(path: Path) -> CaptureResult:
        captures.append(path)
        raise AssertionError("late retained validation reached a held-out capture")

    monkeypatch.setattr(study, "collect_validation_candidate", must_not_collect)
    assert (
        study.main(
            (
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/prerequisites.json",
            ),
            repository_root=repository_root,
            runner=runner,
            run=must_not_run,
            capture=must_not_capture,
        )
        == 2
    )
    assert candidates == []
    assert runs == []
    assert captures == []
    assert not [command for command in runner.calls if command[:2] == ("docker", "build")]
    assert runner.capture_image_cleanup_tags == []


def test_public_collection_fails_when_owned_image_cleanup_fails_after_candidate_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful candidate collection is not reported when its phase-owned image leaks."""

    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    runner = StudyIdentityRunner(
        repository_root,
        capture_image_id=CAPTURE_IMAGE_ID,
        capture_image_present=False,
        cleanup_exit_status=1,
    )
    calls: list[str] = []

    def collect(**_kwargs: object) -> Path:
        calls.append("candidate")
        return repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"

    monkeypatch.setattr(study, "collect_validation_candidate", collect)
    assert (
        study.main(
            (
                "collect",
                "--url",
                "https://downloads.example.test/object.bin",
                "--study-id",
                "study-1",
                "--prerequisites",
                "examples/validation_study/prerequisites.json",
            ),
            repository_root=repository_root,
            runner=runner,
        )
        == 2
    )
    assert calls == ["candidate"]
    assert runner.capture_image_cleanup_tags == [COLLECTION_PHASE_CAPTURE_TAG]


@pytest.mark.parametrize("primary_kind", ("trafficlab", "base"))
def test_public_collection_preserves_primary_when_owned_image_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primary_kind: str,
) -> None:
    """Cleanup is a secondary diagnostic for both expected and arbitrary collection primaries."""

    class ControlledAbort(BaseException):
        pass

    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    runner = StudyIdentityRunner(
        repository_root,
        capture_image_id=CAPTURE_IMAGE_ID,
        capture_image_present=False,
        cleanup_exit_status=1,
    )
    primary: BaseException
    if primary_kind == "trafficlab":
        primary = TrafficlabError("controlled collection failure", corrective_action="preserve the attempt")
    else:
        primary = ControlledAbort("controlled abort")

    def abort_collection(**_kwargs: object) -> Path:
        raise primary

    monkeypatch.setattr(study, "collect_validation_candidate", abort_collection)
    argv = (
        "collect",
        "--url",
        "https://downloads.example.test/object.bin",
        "--study-id",
        "study-1",
        "--prerequisites",
        "examples/validation_study/prerequisites.json",
    )
    if primary_kind == "trafficlab":
        assert study.main(argv, repository_root=repository_root, runner=runner) == 2
    else:
        with pytest.raises(ControlledAbort) as captured:
            study.main(argv, repository_root=repository_root, runner=runner)
        assert captured.value is primary

    assert primary.__notes__ == [
        "collection capture image cleanup failed: "
        "could not remove owned collection capture image: simulated cleanup failure"
    ]
    assert runner.capture_image_cleanup_tags == [COLLECTION_PHASE_CAPTURE_TAG]


def test_phase_capture_tags_are_explicitly_disjoint() -> None:
    """Study and collection must never contend for the same temporary capture tag."""

    assert study._phase_capture_tag("study-1", "study") == "trafficlab-validation-study-1:study-capture"  # pyright: ignore[reportPrivateUsage]
    assert study._phase_capture_tag("study-1", "collection") == "trafficlab-validation-study-1:collection-capture"  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    ("route", "owned_tag", "foreign_tag"),
    (
        (
            "study",
            "trafficlab-validation-study-1:study-capture",
            "trafficlab-validation-study-1:collection-capture",
        ),
        (
            "collection",
            "trafficlab-validation-study-1:collection-capture",
            "trafficlab-validation-study-1:study-capture",
        ),
    ),
)
def test_public_phase_does_not_adopt_or_remove_another_phase_capture_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    owned_tag: str,
    foreign_tag: str,
) -> None:
    """A stale tag owned by the other public phase neither blocks nor leaks into this phase."""

    repository_root = tmp_path / "repository"
    runner = StudyIdentityRunner(
        repository_root,
        capture_image_id=CAPTURE_IMAGE_ID,
        capture_image_present=False,
        owned_capture_tags={foreign_tag},
    )
    if route == "study":
        prerequisite_path, expected = write_study_inputs(repository_root)
        events: list[str] = []
        install_primary_orchestration_doubles(monkeypatch, expected, events)

        def stop_primary(_path: Path) -> RunResult:
            raise TrafficlabError("controlled study primary", corrective_action="preserve the study")

        with pytest.raises(TrafficlabError, match="controlled study primary"):
            study.run_study(
                "https://downloads.example.test/object.bin",
                "study-1",
                prerequisite_path,
                repository_root=repository_root,
                run=stop_primary,
                runner=runner,
                perf_counter=iter(float(value) for value in range(20)).__next__,
                utc_now=lambda: datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
            )
    else:
        prerequisite_path = write_collection_compatible_inputs(repository_root)
        study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            prerequisite_content=prerequisite_path.read_bytes(),
        )

        def stop_collection(**_kwargs: object) -> Path:
            raise TrafficlabError("controlled collection primary", corrective_action="preserve the attempt")

        monkeypatch.setattr(study, "collect_validation_candidate", stop_collection)
        assert (
            study.main(
                (
                    "collect",
                    "--url",
                    "https://downloads.example.test/object.bin",
                    "--study-id",
                    "study-1",
                    "--prerequisites",
                    "examples/validation_study/prerequisites.json",
                ),
                repository_root=repository_root,
                runner=runner,
            )
            == 2
        )

    build = next(command for command in runner.calls if command[:2] == ("docker", "build"))
    assert build[build.index("--tag") + 1] == owned_tag
    assert runner.capture_image_cleanup_tags == [owned_tag]
    assert foreign_tag in runner.owned_capture_tags
    assert owned_tag not in runner.owned_capture_tags


def test_collection_inputs_rejects_legacy_image_lock_before_capture_revalidation(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)
    image_lock = repository_root / "docker" / "capture" / "image-lock.json"
    image_lock.write_text(
        json.dumps(
            {
                "base_digest": f"sha256:{'a' * 64}",
                "base_reference": "docker.io/library/debian@sha256:" + "b" * 64,
                "capture_tool_version": "4.0.17",
                "debian_snapshot": "20260816T000000Z",
                "direct_packages": ["tshark"],
                "expected_capture_image_id": CAPTURE_IMAGE_ID,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(TrafficlabError, match="image lock"):
        study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            prerequisite_path,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            runner=StudyIdentityRunner(repository_root, capture_image_id=CAPTURE_IMAGE_ID),
        )


def test_collection_inputs_rejects_changed_target_metadata_before_candidate_creation(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)

    with pytest.raises(TrafficlabError, match="target image"):
        study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            prerequisite_path,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            runner=StudyIdentityRunner(
                repository_root,
                capture_image_id=CAPTURE_IMAGE_ID,
                target_config_user="unexpected",
            ),
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "host_architecture",
        "kernel_release",
        "platform",
        "python_implementation",
        "python_version",
        "uv_lock",
        "docker_engine_version",
        "docker_compose_version",
        "target_image_id",
        "target_repo_digests",
        "target_config_user",
        "capture_image_id",
    ),
)
def test_collection_inputs_rejects_each_live_environment_mismatch_before_candidate_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    """Collection revalidates every retained host, tool, and image identity before capture."""

    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)
    runner = StudyIdentityRunner(repository_root, capture_image_id=CAPTURE_IMAGE_ID)
    if mutation == "host_architecture":
        monkeypatch.setattr(study.platform, "machine", lambda: "other-architecture")
    elif mutation == "kernel_release":
        monkeypatch.setattr(study.platform, "release", lambda: "other-kernel")
    elif mutation == "platform":
        monkeypatch.setattr(study.platform, "platform", lambda: "Other-platform")
    elif mutation == "python_implementation":
        monkeypatch.setattr(study.platform, "python_implementation", lambda: "OtherPython")
    elif mutation == "python_version":
        monkeypatch.setattr(study.platform, "python_version", lambda: "0.0.0")
    elif mutation == "uv_lock":
        (repository_root / "uv.lock").write_bytes(b"changed checked lock\n")
    elif mutation == "docker_engine_version":
        runner.docker_engine_version = "28.0.0"
    elif mutation == "docker_compose_version":
        runner.docker_compose_version = "3.0.0"
    elif mutation == "target_image_id":
        runner.target_image_id = f"sha256:{'9' * 64}"
    elif mutation == "target_repo_digests":
        runner.target_repo_digests = ("curlimages/curl@sha256:" + "f" * 64,)
    elif mutation == "target_config_user":
        runner.target_config_user = "unexpected"
    else:
        runner.capture_image_id = f"sha256:{'8' * 64}"

    with pytest.raises(TrafficlabError):
        study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
            repository_root,
            prerequisite_path,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            runner=runner,
        )

    assert not (repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1").exists()


def test_collection_binds_retained_prerequisite_before_creating_a_candidate(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    environment, retained, files, configs, object_size_bytes = study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        prerequisite_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        runner=StudyIdentityRunner(repository_root, capture_image_id=CAPTURE_IMAGE_ID),
    )
    mismatched = study.parse_retained_prerequisites(retained)
    mismatched["study_id"] = "other-study"
    for command in cast(list[dict[str, object]], mismatched["commands"]):
        kind = cast(str, command["kind"])
        argv = list(
            study.prerequisite_command_argv(
                kind, study_id="other-study", url="https://downloads.example.test/object.bin"
            )
        )
        command["argv"] = argv
        command_record = cast(dict[str, object], command["command"])
        command_record["identity"] = identify_bytes(
            json.dumps({"argv": argv}, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        ).as_dict()
    mismatched_content = study.render_retained_prerequisites(mismatched)
    attempt = study._begin_phase_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        phase="collection",
    )
    calls: list[Path] = []

    def should_not_run(path: Path) -> RunResult:
        calls.append(path)
        raise AssertionError("mismatched retained prerequisite must stop before training")

    candidate = repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"
    with pytest.raises(TrafficlabError, match="retained prerequisite"):
        study.collect_validation_candidate(
            repository_root=repository_root,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            attempt=attempt,
            environment=environment,
            retained_prerequisites=mismatched_content,
            prerequisite_files=files,
            configs=configs,
            run=should_not_run,
            object_size_bytes=object_size_bytes,
        )

    assert calls == []
    assert not candidate.exists()
    assert (
        repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1" / "collection.json"
    ).is_file()


def test_collection_persists_its_phase_marker_before_later_object_validation(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)
    study._complete_prerequisite_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    environment, retained, files, configs, _object_size_bytes = study._collection_inputs_from_prerequisites(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        prerequisite_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        runner=StudyIdentityRunner(repository_root, capture_image_id=CAPTURE_IMAGE_ID),
    )
    attempt = study._begin_phase_attempt(  # pyright: ignore[reportPrivateUsage]
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        phase="collection",
    )
    candidate = repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "study-1"
    marker = (
        repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1" / "collection.json"
    )

    with pytest.raises(TrafficlabError, match="object size"):
        study.collect_validation_candidate(
            repository_root=repository_root,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            attempt=attempt,
            environment=environment,
            retained_prerequisites=retained,
            prerequisite_files=files,
            configs=configs,
            object_size_bytes=1,
        )

    assert marker.is_file()
    assert not candidate.exists()


def test_audited_publisher_rejects_a_different_destination_id_before_audit(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    candidate = repository_root / "examples" / "validation_study" / "evidence" / ".candidates" / "candidate-study"
    candidate.mkdir(parents=True)

    with pytest.raises(TrafficlabError, match="candidate ID"):
        study.publish_audited_bundle(candidate, "destination-study", repository_root=repository_root)

    assert candidate.is_dir()
    assert not (repository_root / "examples" / "validation_study" / "evidence" / "destination-study").exists()
