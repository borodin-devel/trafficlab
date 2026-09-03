"""Attempt behavior."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile as tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import pytest

import scripts.validation_study.cli as vs_cli
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.prerequisites.run as vs_prereq_run
import scripts.validation_study.records as vs_records
import scripts.validation_study.rotation.run as vs_rotation_run
import scripts.validation_study.workloads as vs_workloads
import trafficlab.capture.docker.image as trafficlab_capture_docker_image
from tests.support.validation_study.builders import valid_prerequisite
from tests.support.validation_study.runners import ScriptedPrerequisiteRunner, write_prerequisite_repository_inputs
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.errors import TrafficlabError
from trafficlab.pipeline.types import RunResult


def test_prerequisite_attempt_marker_is_written_before_any_later_failure(tmp_path: Path) -> None:
    """A syntactically valid prerequisite attempt is permanently visible even if Git fails first."""

    repository = tmp_path / "repository"
    repository.mkdir()

    def fail_git(
        argv: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 1, b"", b"missing Git state")

    with pytest.raises(TrafficlabError, match="prerequisite validation failed"):
        vs_prereq_run.run_prerequisites(
            "https://downloads.example.test/object.bin",
            "study-1",
            repository_root=repository,
            runner=cast(vs_records.CommandRunner, fail_git),
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    marker = (
        repository / "examples" / "validation_study" / ".study-work" / "attempts" / "study-1" / "prerequisites.json"
    )
    assert json.loads(marker.read_text()) == {
        "phase": "prerequisites",
        "study_id": "study-1",
        "url": "https://downloads.example.test/object.bin",
    }
    assert not marker.with_name("prerequisites-success.json").exists()


def test_successful_prerequisite_marker_binds_the_published_prerequisite_bytes(tmp_path: Path) -> None:
    """Collection can only follow the exact successful prerequisite publication."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    runner = ScriptedPrerequisiteRunner(repository)
    vs_prereq_run.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    prerequisite = repository / "examples" / "validation_study" / "prerequisites.json"
    marker = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / runner.study_id
        / "prerequisites-success.json"
    )
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "phase": "prerequisites",
        "prerequisites_identity": identify_bytes(prerequisite.read_bytes()).as_dict(),
        "study_id": runner.study_id,
        "url": runner.url,
    }


@pytest.mark.parametrize("entry_kind", ("symlink", "fifo"))
def test_successful_prerequisite_marker_rejects_nonregular_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_kind: str,
) -> None:
    """A matching marker must be a canonical regular file, never an indirection or device."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    runner = ScriptedPrerequisiteRunner(repository, study_id="study-r4")
    vs_prereq_run.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    prerequisite = repository / "examples" / "validation_study" / "prerequisites.json"
    marker = (
        repository
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / runner.study_id
        / "prerequisites-success.json"
    )
    marker_bytes = marker.read_bytes()
    marker.unlink()
    if entry_kind == "symlink":
        outside = repository / "outside-marker.json"
        outside.write_bytes(marker_bytes)
        marker.symlink_to(outside)
    else:
        os.mkfifo(marker)
        original_read_bytes = Path.read_bytes

        def forbid_fifo_read(path: Path) -> bytes:
            if path == marker:
                raise AssertionError("marker reader must reject a FIFO before opening it")
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", forbid_fifo_read)

    with pytest.raises(TrafficlabError, match="matching successful prerequisite marker"):
        vs_rotation_run.require_successful_prerequisite_attempt(
            repository,
            study_id=runner.study_id,
            url=runner.url,
            prerequisite_content=prerequisite.read_bytes(),
        )


def test_checked_config_create_race_is_reported_without_a_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legacy exclusive-create path retains its race handling for first publication."""

    destination = tmp_path / "short.toml"
    original_open = Path.open

    def fail_config_create(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> object:
        if path == destination and mode == "xb":
            raise FileExistsError("simulated config create race")
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", fail_config_create)
    with pytest.raises(ValueError, match="config target already exists"):
        vs_workloads._write_new_config(destination, b"[run]\n")  # pyright: ignore[reportPrivateUsage]

    assert not destination.exists()


def test_prerequisite_publication_rejects_noncanonical_validated_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prerequisite publication codec rejects a parser/render disagreement before link or replace."""

    repository = tmp_path / "repository"
    repository.mkdir()
    destination = repository / "prerequisites.json"
    original_render = vs_prereq_codec.render_prerequisite_results
    calls = 0

    def render_once_then_mismatch(value: vs_records.PrerequisiteResults) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_render(value)
        return b"{}\n"

    monkeypatch.setattr(vs_prereq_run, "render_prerequisite_results", render_once_then_mismatch)
    with pytest.raises(ValueError, match="persisted prerequisite JSON is not canonical"):
        vs_prereq_run.publish_prerequisites(
            destination,
            valid_prerequisite(),
            repository_root=repository,
        )

    assert not destination.exists()
    assert not tuple(destination.parent.glob(".prerequisites.json.*"))


def test_public_prerequisites_then_collect_binds_the_raw_published_marker_before_transformation(tmp_path: Path) -> None:
    """The public phase transition checks schema-1 publication bytes before schema-5 retention."""

    repository = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository)
    scripted = ScriptedPrerequisiteRunner(repository)
    now = datetime(2026, 8, 16, tzinfo=UTC)
    assert (
        vs_cli.main(
            ("prerequisites", "--url", scripted.url, "--study-id", scripted.study_id),
            repository_root=repository,
            runner=scripted,
            utc_now=lambda: now,
        )
        == 0
    )

    collection_builds: list[tuple[str, ...]] = []
    collection_cleanups: list[tuple[str, ...]] = []

    def collection_runner(
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
        if command == ("git", "rev-parse", "HEAD^{tree}"):
            return subprocess.CompletedProcess(command, 0, stdout=b"d" * 40 + b"\n", stderr=b"")
        if command == (
            "docker",
            "image",
            "inspect",
            f"trafficlab-validation-{scripted.study_id}:collection-capture",
            "--format",
            "{{.Id}}",
        ):
            return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"not present\n")
        if command[:2] == ("docker", "build"):
            collection_builds.append(command)
            iidfile = Path(command[command.index("--iidfile") + 1])
            iidfile.write_text(f"{scripted.capture_id}\n", encoding="ascii")
            return subprocess.CompletedProcess(command, 0, stdout=b"rebuilt\n", stderr=b"")
        if command == ("docker", "image", "inspect", scripted.capture_id, "--format", "{{.Id}}"):
            return subprocess.CompletedProcess(command, 0, stdout=f"{scripted.capture_id}\n".encode(), stderr=b"")
        if command == (
            "docker",
            "image",
            "rm",
            "--force",
            f"trafficlab-validation-{scripted.study_id}:collection-capture",
        ):
            collection_cleanups.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=b"removed\n", stderr=b"")
        return scripted(argv, cwd=cwd, check=check, capture_output=capture_output, shell=shell, timeout=timeout)

    training_calls: list[Path] = []

    def stop_at_training(path: Path) -> RunResult:
        training_calls.append(path)
        raise ValueError("training callback reached")

    argv = (
        "collect",
        "--url",
        scripted.url,
        "--study-id",
        scripted.study_id,
        "--prerequisites",
        "examples/validation_study/prerequisites.json",
    )
    with pytest.raises(ValueError, match="training callback reached"):
        vs_cli.main(
            argv,
            repository_root=repository,
            runner=collection_runner,
            run=stop_at_training,
            capture=lambda _path: pytest.fail("held-out capture must not begin"),
        )

    prerequisite = repository / "examples" / "validation_study" / "prerequisites.json"
    attempt = repository / "examples" / "validation_study" / ".study-work" / "attempts" / scripted.study_id
    candidate = repository / "examples" / "validation_study" / "evidence" / ".candidates" / scripted.study_id
    success = cast(dict[str, object], json.loads((attempt / "prerequisites-success.json").read_text(encoding="utf-8")))
    assert success["prerequisites_identity"] == identify_bytes(prerequisite.read_bytes()).as_dict()
    assert (candidate / "prerequisites.json").read_bytes() != prerequisite.read_bytes()
    assert (attempt / "collection.json").is_file()
    assert (attempt / "frozen-protocol.json").is_file()
    assert len(training_calls) == 1
    assert collection_builds == [
        trafficlab_capture_docker_image.cold_capture_build_argv(
            f"trafficlab-validation-{scripted.study_id}:collection-capture",
            attempt / "collection-capture.iid",
        )
    ]
    assert collection_cleanups == [
        ("docker", "image", "rm", "--force", f"trafficlab-validation-{scripted.study_id}:collection-capture")
    ]
    assert not (attempt / "collection-capture.iid").exists()
    assert vs_cli.main(argv, repository_root=repository, runner=collection_runner) == 2
    assert len(training_calls) == 1
