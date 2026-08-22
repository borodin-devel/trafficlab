"""Collection behavior."""

from __future__ import annotations

import json
import platform
import time as time
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

import scripts.validation_study.cli as vs_cli
import scripts.validation_study.collection as vs_collection
import scripts.validation_study.common as vs_common
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.prerequisites.commands as vs_prereq_commands
import scripts.validation_study.rotation.run as vs_rotation_run
import trafficlab.capture.docker.image as trafficlab_capture_docker_image
from tests.support.validation_study.constants import CAPTURE_IMAGE_ID, IMAGE_ID
from tests.support.validation_study.repository import write_study_inputs
from tests.support.validation_study.runners import StudyIdentityRunner
from tests.unit.validation.study.orchestration._support import (
    COLLECTION_PHASE_CAPTURE_TAG,
    STUDY_PHASE_CAPTURE_TAG,
    install_primary_orchestration_doubles,
    write_collection_compatible_inputs,
)
from trafficlab.capture.lineage import CaptureResult
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.errors import TrafficlabError
from trafficlab.pipeline.types import RunResult


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
        vs_collection.establish_phase_capture_image(
            repository_root,
            phase="study",
            expected_image_id=CAPTURE_IMAGE_ID,
            capture_lock_image_id=CAPTURE_IMAGE_ID,
            owned_capture_image=vs_collection.PhaseCaptureImage("trafficlab-validation-study-1:capture"),
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
        vs_collection.establish_phase_capture_image(
            repository_root,
            phase="study",
            expected_image_id=CAPTURE_IMAGE_ID,
            capture_lock_image_id=CAPTURE_IMAGE_ID,
            owned_capture_image=vs_collection.PhaseCaptureImage("trafficlab-validation-study-1:capture"),
            iidfile=iidfile,
            runner=runner,
        )
    assert captured.value.__notes__ == [
        "study capture IID file cleanup failed: could not remove study capture IID file "
        f"{iidfile}: simulated IID cleanup failure"
    ]
    assert iidfile.exists()


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
    assert STUDY_PHASE_CAPTURE_TAG in runner.owned_capture_tags


def test_collection_inputs_revalidates_current_checked_inputs(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)

    environment, retained, files, configs, object_size_bytes = vs_collection.collection_inputs_from_prerequisites(
        repository_root,
        prerequisite_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        runner=StudyIdentityRunner(repository_root, capture_image_id=CAPTURE_IMAGE_ID),
    )

    parsed = cast(vs_common.JsonObject, vs_prereq_codec.parse_retained_prerequisites(retained))
    retained_environment = cast(vs_common.JsonObject, parsed["environment"])
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
        vs_collection.collection_inputs_from_prerequisites(
            repository_root,
            prerequisite_path,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            runner=StudyIdentityRunner(repository_root, capture_image_id=CAPTURE_IMAGE_ID, dirty=True),
        )
    (repository_root / "uv.lock").unlink()
    with pytest.raises(TrafficlabError, match="Validation Study collection inputs are invalid"):
        vs_collection.collection_inputs_from_prerequisites(
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
    vs_rotation_run.complete_prerequisite_attempt(
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

    monkeypatch.setattr(vs_cli, "collect_validation_candidate", collect)
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
            vs_cli.main(argv, repository_root=repository_root, runner=runner)
    else:
        assert vs_cli.main(argv, repository_root=repository_root, runner=runner) == (0 if outcome == "success" else 2)

    attempt = repository_root / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1"
    tag = COLLECTION_PHASE_CAPTURE_TAG
    assert (
        trafficlab_capture_docker_image.cold_capture_build_argv(tag, attempt / "collection-capture.iid") in runner.calls
    )
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
    vs_rotation_run.complete_prerequisite_attempt(
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

    monkeypatch.setattr(vs_cli, "collect_validation_candidate", must_not_collect)
    assert (
        vs_cli.main(
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
    vs_rotation_run.complete_prerequisite_attempt(
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

    monkeypatch.setattr(vs_cli, "collect_validation_candidate", must_not_collect)
    assert (
        vs_cli.main(
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
    vs_rotation_run.complete_prerequisite_attempt(
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

    monkeypatch.setattr(vs_cli, "collect_validation_candidate", must_not_collect)
    assert (
        vs_cli.main(
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
        assert ("docker", "image", "inspect", vs_common.TARGET_REFERENCE) in runner.calls


@pytest.mark.parametrize("failure", ("config", "retained-artifact"))
def test_public_collection_defers_cold_build_until_all_retained_inputs_validate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Late immutable config/evidence failures must not create a capture-image lease."""

    repository_root = tmp_path / "repository"
    prerequisite_path = write_collection_compatible_inputs(repository_root)
    vs_rotation_run.complete_prerequisite_attempt(
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

    monkeypatch.setattr(vs_cli, "collect_validation_candidate", must_not_collect)
    assert (
        vs_cli.main(
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
    vs_rotation_run.complete_prerequisite_attempt(
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

    monkeypatch.setattr(vs_cli, "collect_validation_candidate", collect)
    assert (
        vs_cli.main(
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
    vs_rotation_run.complete_prerequisite_attempt(
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

    monkeypatch.setattr(vs_cli, "collect_validation_candidate", abort_collection)
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
        assert vs_cli.main(argv, repository_root=repository_root, runner=runner) == 2
    else:
        with pytest.raises(ControlledAbort) as captured:
            vs_cli.main(argv, repository_root=repository_root, runner=runner)
        assert captured.value is primary

    assert primary.__notes__ == [
        "collection capture image cleanup failed: "
        "could not remove owned collection capture image: simulated cleanup failure"
    ]
    assert runner.capture_image_cleanup_tags == [COLLECTION_PHASE_CAPTURE_TAG]


def test_phase_capture_tags_are_explicitly_disjoint() -> None:
    """Study and collection must never contend for the same temporary capture tag."""

    assert vs_common.phase_capture_tag("study-1", "study") == "trafficlab-validation-study-1:study-capture"
    assert vs_common.phase_capture_tag("study-1", "collection") == "trafficlab-validation-study-1:collection-capture"


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
            vs_cli.run_study(
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
        vs_rotation_run.complete_prerequisite_attempt(
            repository_root,
            study_id="study-1",
            url="https://downloads.example.test/object.bin",
            prerequisite_content=prerequisite_path.read_bytes(),
        )

        def stop_collection(**_kwargs: object) -> Path:
            raise TrafficlabError("controlled collection primary", corrective_action="preserve the attempt")

        monkeypatch.setattr(vs_cli, "collect_validation_candidate", stop_collection)
        assert (
            vs_cli.main(
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
        vs_collection.collection_inputs_from_prerequisites(
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
        vs_collection.collection_inputs_from_prerequisites(
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
        monkeypatch.setattr(platform, "machine", lambda: "other-architecture")
    elif mutation == "kernel_release":
        monkeypatch.setattr(platform, "release", lambda: "other-kernel")
    elif mutation == "platform":
        monkeypatch.setattr(platform, "platform", lambda: "Other-platform")
    elif mutation == "python_implementation":
        monkeypatch.setattr(platform, "python_implementation", lambda: "OtherPython")
    elif mutation == "python_version":
        monkeypatch.setattr(platform, "python_version", lambda: "0.0.0")
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
        vs_collection.collection_inputs_from_prerequisites(
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
    vs_rotation_run.complete_prerequisite_attempt(
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    environment, retained, files, configs, object_size_bytes = vs_collection.collection_inputs_from_prerequisites(
        repository_root,
        prerequisite_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        runner=StudyIdentityRunner(repository_root, capture_image_id=CAPTURE_IMAGE_ID),
    )
    mismatched = vs_prereq_codec.parse_retained_prerequisites(retained)
    mismatched["study_id"] = "other-study"
    for command in cast(list[dict[str, object]], mismatched["commands"]):
        kind = cast(str, command["kind"])
        argv = list(
            vs_prereq_commands.prerequisite_command_argv(
                kind, study_id="other-study", url="https://downloads.example.test/object.bin"
            )
        )
        command["argv"] = argv
        command_record = cast(dict[str, object], command["command"])
        command_record["identity"] = identify_bytes(
            json.dumps({"argv": argv}, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        ).as_dict()
    mismatched_content = vs_prereq_codec.render_retained_prerequisites(mismatched)
    attempt = vs_rotation_run.begin_phase_attempt(
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
        vs_collection.collect_validation_candidate(
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
    vs_rotation_run.complete_prerequisite_attempt(
        repository_root,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        prerequisite_content=prerequisite_path.read_bytes(),
    )
    environment, retained, files, configs, _object_size_bytes = vs_collection.collection_inputs_from_prerequisites(
        repository_root,
        prerequisite_path,
        study_id="study-1",
        url="https://downloads.example.test/object.bin",
        runner=StudyIdentityRunner(repository_root, capture_image_id=CAPTURE_IMAGE_ID),
    )
    attempt = vs_rotation_run.begin_phase_attempt(
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
        vs_collection.collect_validation_candidate(
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
