"""Schema behavior."""

from __future__ import annotations

import copy
import json
import math
import os
import platform as platform
import stat
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import pytest

import scripts.validation_study.cli as vs_cli
import scripts.validation_study.common as vs_common
import scripts.validation_study.prerequisites.codec as vs_prereq_codec
import scripts.validation_study.prerequisites.commands as vs_prereq_commands
import scripts.validation_study.prerequisites.run as vs_prereq_run
import scripts.validation_study.transfer as vs_transfer
import scripts.validation_study.workloads as vs_workloads
import trafficlab.capture.docker.image as trafficlab_capture_docker_image
from tests.support.validation_study.builders import (
    response_headers,
    valid_prerequisite,
)
from tests.support.validation_study.runners import (
    ScriptedPrerequisiteRunner,
    StudyIdentityRunner,
    write_prerequisite_repository_inputs,
)
from tests.unit.validation.study.protocol._support import (
    contains_none,
    install_prerequisite_failure,
)
from trafficlab.common.errors import TrafficlabError


def test_study_id_url_repository_path_and_utc_validators_are_exact(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    assert vs_common.validate_study_id("study-1") == "study-1"
    assert vs_common.validate_endpoint_url("https://downloads.example.test/object.bin") == (
        "https://downloads.example.test/object.bin"
    )
    assert (
        vs_common.repository_relative_path(
            "evidence/study-1/file", repository_root=repository_root, name="evidence path"
        )
        == "evidence/study-1/file"
    )
    assert vs_common.utc_timestamp("2026-08-13T12:00:00Z", name="created time") == "2026-08-13T12:00:00Z"

    for value in ("", "Study-1", "study_1", "-study", "a" * 33):
        with pytest.raises(ValueError, match="study ID"):
            vs_common.validate_study_id(value)
    for value in (
        "http://downloads.example.test/object.bin",
        "https://user@downloads.example.test/object.bin",
        "https://downloads.example.test/object.bin?token=x",
        "https://downloads.example.test/object.bin#fragment",
        "https://127.0.0.1/object.bin",
        "https:///object.bin",
    ):
        with pytest.raises(ValueError, match="URL"):
            vs_common.validate_endpoint_url(value)
    for value in (
        "/evidence/study-1/file",
        "evidence\\study-1\\file",
        "evidence//file",
        "evidence/./file",
        "evidence/../file",
        "",
    ):
        with pytest.raises(ValueError, match="repository-relative|nonempty"):
            vs_common.repository_relative_path(value, repository_root=repository_root, name="evidence path")
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository_root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="repository-relative"):
        vs_common.repository_relative_path("escape/file", repository_root=repository_root, name="evidence path")
    for value in (
        "2026-08-13T12:00:00+00:00",
        "2026-08-13T12:00:00z",
        "2026-08-13T12:00:00",
        "2026-02-30T12:00:00Z",
    ):
        with pytest.raises(ValueError, match="UTC RFC 3339"):
            vs_common.utc_timestamp(value, name="created time")

    with pytest.raises(ValueError, match="integer"):
        vs_common.strict_int(True, name="count")
    with pytest.raises(ValueError, match="float"):
        vs_common.strict_float(1, name="score")
    with pytest.raises(ValueError, match=r"\[0.0, 1.0\]"):
        vs_common.strict_float(1.1, name="score", lower=0.0, upper=1.0)
    with pytest.raises(ValueError, match="finite"):
        vs_common.strict_float(math.inf, name="score")
    with pytest.raises(ValueError, match="SHA-256"):
        vs_common.sha256("A" * 64, name="hash")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        vs_common.load_json(b'{"value":1,"value":2}\n')
    with pytest.raises(ValueError, match="invalid JSON constant"):
        vs_common.load_json(b'{"value":NaN}\n')


@pytest.mark.parametrize(
    "mutation",
    [
        "symlink",
        "replacement-inode",
        "empty-header",
        "duplicate-status",
        "duplicate-content-range",
        "wrong-total",
        "range-ignored-200",
        "wrong-content-length",
        "credential-redirect",
        "http-redirect",
        "archive-exists",
    ],
)
def test_transfer_evidence_rejects_unsafe_or_inexact_headers(mutation: str, tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    workload = vs_workloads.workload_specs("https://downloads.example.test/object.bin")[0]
    mount_directory = repository_root / "examples" / "validation_study" / ".study-work" / "mount" / "study-1"
    scratch = mount_directory / "short.headers"
    if mutation == "symlink":
        mount_directory.mkdir(parents=True)
        scratch.symlink_to(repository_root / "outside")
        with pytest.raises(ValueError, match="symlink|regular"):
            vs_transfer.prepare_transfer_scratch(repository_root, "study-1", "01-short-r1", workload)
        assert scratch.is_symlink()
        return

    prepared = vs_transfer.prepare_transfer_scratch(repository_root, "study-1", "01-short-r1", workload)
    valid = response_headers(0, 1048575)
    invalid_headers = {
        "empty-header": b"",
        "duplicate-status": b"HTTP/1.1 206 Response\r\n" + valid,
        "duplicate-content-range": valid.replace(
            b"Content-Length:", b"Content-Range: bytes 0-1048575/4194304\r\nContent-Length:"
        ),
        "wrong-total": response_headers(0, 1048575, total=4_194_305),
        "range-ignored-200": response_headers(0, 1048575, status=200),
        "wrong-content-length": response_headers(0, 1048575, length=1048575),
        "credential-redirect": response_headers(
            0,
            1048575,
            prefix=b"HTTP/1.1 302 Found\r\nLocation: https://user@example.test/object\r\n\r\n",
        ),
        "http-redirect": response_headers(
            0,
            1048575,
            prefix=b"HTTP/1.1 302 Found\r\nLocation: http://example.test/object\r\n\r\n",
        ),
    }
    if mutation == "replacement-inode":
        replacement = mount_directory / "replacement.headers"
        replacement.write_bytes(valid)
        os.chmod(replacement, 0o666)
        os.replace(replacement, scratch)
    elif mutation == "archive-exists":
        scratch.write_bytes(valid)
        archive = (
            repository_root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "evidence"
            / "study-1"
            / "01-short-r1"
            / "short.headers"
        )
        archive.write_bytes(b"existing")
    else:
        scratch.write_bytes(invalid_headers[mutation])

    original = scratch.read_bytes()
    with pytest.raises(ValueError):
        vs_transfer.archive_transfer_evidence(
            repository_root,
            "study-1",
            "01-short-r1",
            workload,
            prepared,
            object_size_bytes=4_194_304,
        )
    assert scratch.read_bytes() == original
    archive = (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / "study-1"
        / "01-short-r1"
        / "short.headers"
    )
    if mutation == "archive-exists":
        assert archive.read_bytes() == b"existing"
    else:
        assert archive.read_bytes() == original
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600


def test_prerequisite_codec_round_trips_exact_canonical_schema(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    value = valid_prerequisite()

    rendered = vs_prereq_codec.render_prerequisite_results(value)
    parsed = vs_prereq_codec.parse_prerequisite_results(rendered, repository_root=repository_root)

    assert vs_prereq_codec.render_prerequisite_results(parsed) == rendered
    assert rendered.endswith(b"\n")
    assert not rendered.endswith(b" \n")
    assert b": " not in rendered
    assert b", " not in rendered
    decoded = json.loads(rendered)
    assert not contains_none(decoded)
    assert tuple(decoded) == tuple(sorted(decoded))
    assert tuple(decoded["commands"][0]) == tuple(sorted(decoded["commands"][0]))
    with pytest.raises(TypeError):
        cast(dict[str, object], parsed.capability)["status"] = 200

    destination = repository_root / "examples" / "validation_study" / "prerequisites.json"
    destination.parent.mkdir(parents=True)
    vs_prereq_run.publish_prerequisites(destination, value, repository_root=repository_root)
    assert destination.read_bytes() == rendered


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unknown-root", "exact keys"),
        ("duplicate-key", "duplicate JSON key"),
        ("wrong-command-order", "docker_matrix"),
        ("skipped-test", "skipped"),
        ("wrong-image", "target reference"),
        ("wrong-capability-mode", "canary file mode"),
        ("wrong-container-id", "lowercase container ID"),
        ("path-escape", "repository-relative"),
        ("nan", "invalid JSON constant"),
    ],
)
def test_prerequisite_codec_rejects_each_contract_violation(
    mutation: str,
    message: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    rendered = vs_prereq_codec.render_prerequisite_results(valid_prerequisite())
    document = cast(dict[str, object], json.loads(rendered))

    if mutation == "unknown-root":
        document["unknown"] = "value"
        invalid = json.dumps(document, separators=(",", ":")).encode()
    elif mutation == "duplicate-key":
        invalid = rendered.replace(b'{"capability":', b'{"schema_version":1,"capability":', 1)
    elif mutation == "nan":
        invalid = rendered.replace(b'"schema_version":1', b'"schema_version":NaN', 1)
    else:
        mutated = copy.deepcopy(document)
        commands = cast(list[dict[str, object]], mutated["commands"])
        capability = cast(dict[str, object], mutated["capability"])
        images = cast(dict[str, object], mutated["images"])
        if mutation == "wrong-command-order":
            mutated["commands"] = list(reversed(commands))
        elif mutation == "skipped-test":
            tests = cast(dict[str, object], commands[0]["tests"])
            tests["passed"] = 1
            tests["skipped"] = 1
        elif mutation == "wrong-image":
            images["target_reference"] = "curlimages/curl:latest"
        elif mutation == "wrong-capability-mode":
            capability["canary_file_mode"] = 384
        elif mutation == "wrong-container-id":
            capability["container_id"] = "ABC123"
        elif mutation == "path-escape":
            capability["mount_source"] = "../escape"
        invalid = json.dumps(mutated, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match=message):
        vs_prereq_codec.parse_prerequisite_results(invalid, repository_root=repository_root)


def test_prerequisite_codec_rejects_changed_derived_capability_range(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    rendered = vs_prereq_codec.render_prerequisite_results(valid_prerequisite())
    document = cast(dict[str, object], json.loads(rendered))
    capability = cast(dict[str, object], document["capability"])
    capability["content_range"] = "bytes 0-0/4194305"
    invalid = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(ValueError, match="content range"):
        vs_prereq_codec.parse_prerequisite_results(invalid, repository_root=repository_root)


def test_prerequisite_codec_accepts_a_valid_credential_free_https_final_redirect(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    rendered = vs_prereq_codec.render_prerequisite_results(valid_prerequisite())
    document = cast(dict[str, object], json.loads(rendered))
    capability = cast(dict[str, object], document["capability"])
    capability["final_url"] = "https://cdn.example.test/object.bin"
    capability["redirect_count"] = 1
    redirected = json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"

    parsed = vs_prereq_codec.parse_prerequisite_results(redirected, repository_root=repository_root)

    assert parsed.capability["final_url"] == "https://cdn.example.test/object.bin"
    assert parsed.capability["redirect_count"] == 1


def test_prerequisites_remove_the_shared_capture_tag_after_a_guarded_test_failure(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, "docker-matrix-failed")

    with pytest.raises(TrafficlabError, match="docker_matrix guarded pytest failed"):
        vs_prereq_run.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    commands = [command for command, _timeout in runner.calls]
    shared_tag = f"trafficlab-validation-{runner.study_id}:capture"
    assert (
        commands.count(
            trafficlab_capture_docker_image.cold_capture_build_argv(shared_tag, runner.evidence / "capture.iid")
        )
        == 1
    )
    assert commands[-1] == ("docker", "image", "rm", "--force", shared_tag)


def test_prerequisite_cleanup_does_not_replace_its_guarded_test_failure(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, "docker-matrix-failed-cleanup-failed")

    with pytest.raises(TrafficlabError, match="docker_matrix guarded pytest failed") as captured:
        vs_prereq_run.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    assert captured.value.__notes__ == [
        "prerequisite capture image cleanup failed: could not remove owned prerequisite capture image: cleanup failed"
    ]
    assert [command for command, _timeout in runner.calls][-1] == (
        "docker",
        "image",
        "rm",
        "--force",
        f"trafficlab-validation-{runner.study_id}:capture",
    )


def test_prerequisites_preserve_an_arbitrary_primary_when_shared_capture_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interruption keeps ownership cleanup as an ordered secondary diagnostic."""

    class ControlledAbort(BaseException):
        pass

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, "capture-image-cleanup-failed")

    def abort(*_args: object, **_kwargs: object) -> vs_common.JsonObject:
        raise ControlledAbort("controlled abort")

    monkeypatch.setattr(vs_prereq_run, "run_prerequisite_test", abort)
    with pytest.raises(ControlledAbort) as captured:
        vs_prereq_run.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    assert captured.value.__notes__ == [
        "prerequisite capture image cleanup failed: could not remove owned prerequisite capture image: cleanup failed"
    ]
    assert [command for command, _timeout in runner.calls][-1] == (
        "docker",
        "image",
        "rm",
        "--force",
        f"trafficlab-validation-{runner.study_id}:capture",
    )


@pytest.mark.parametrize("entry_kind", ("regular", "symlink", "fifo"))
@pytest.mark.parametrize(
    ("protocol", "expected"),
    (
        ("valid", "ignored prerequisite worktree entry is not permitted"),
        ("truncated", "ignored prerequisite paths must be terminal NUL-delimited"),
        ("nonempty-no-match", "ignored prerequisite paths must be empty for no-match status"),
        ("empty-match", "ignored prerequisite paths must be nonempty for match status"),
        ("nonzero", "could not resolve ignored prerequisite paths"),
    ),
)
def test_prerequisites_reject_local_exclude_ignored_worktree_entries_before_docker(
    tmp_path: Path,
    entry_kind: str,
    protocol: str,
    expected: str,
) -> None:
    """Ignored source entries use the same strict Git-NUL boundary as accepted evidence."""

    if entry_kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("nonregular FIFO entries require POSIX")
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    relative = f"locally-excluded-{entry_kind}"
    entry = repository_root / relative
    exclude = repository_root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(f"{relative}\n", encoding="utf-8")
    if entry_kind == "regular":
        entry.write_text("ignored foreign source\n", encoding="utf-8")
    elif entry_kind == "symlink":
        entry.symlink_to("source.py")
    else:
        os.mkfifo(entry)
    runner = ScriptedPrerequisiteRunner(repository_root)
    runner.ignored_worktree_paths = frozenset({relative})
    runner.ignored_worktree_protocol = protocol

    with pytest.raises(TrafficlabError, match=expected):
        vs_prereq_run.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    commands = [command for command, _timeout in runner.calls]
    assert any(command == ("git", "check-ignore", "-z", "--stdin") for command in commands)
    assert not any(command[:2] == ("docker", "version") for command in commands)
    assert (
        repository_root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "attempts"
        / runner.study_id
        / "prerequisites.json"
    ).is_file()


def test_prerequisites_do_not_publish_success_when_shared_capture_cleanup_fails(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, "capture-image-cleanup-failed")

    with pytest.raises(TrafficlabError, match="remove owned prerequisite capture image"):
        vs_prereq_run.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
        )

    assert not (repository_root / "examples" / "validation_study" / "prerequisites.json").exists()
    assert [command for command, _timeout in runner.calls][-1] == (
        "docker",
        "image",
        "rm",
        "--force",
        f"trafficlab-validation-{runner.study_id}:capture",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "dirty-tree",
        "wrong-python",
        "target-digest-absent",
        "capture-iid-tag",
        "capture-iid-missing",
        "preexisting-name",
        "preexisting-cid",
        "capability-daemon-error",
        "capability-lingering-unowned",
        "capability-timeout-owned",
        "capability-timeout-unowned",
        "canary-not-written",
        "canary-replaced",
        "wrong-write-out",
        "range-ignored",
        "oversize-object",
        "docker-matrix-failed",
        "internet-skipped",
        "config-publication-failed",
    ],
)
def test_prerequisites_stop_at_first_failure_preserve_primary_and_publish_no_valid_json(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, mutation)
    install_prerequisite_failure(mutation, monkeypatch)

    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    with pytest.raises(TrafficlabError, match="prerequisite validation failed") as captured:
        vs_prereq_run.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: now,
        )

    assert "restart with a new study ID" in captured.value.corrective_action
    prerequisite_path = repository_root / "examples" / "validation_study" / "prerequisites.json"
    assert not prerequisite_path.exists()
    config_directory = repository_root / "examples" / "validation_study" / "configs"
    assert not config_directory.exists() or not tuple(config_directory.glob("*.toml"))
    commands = [command for command, _timeout in runner.calls]
    docker_guard = vs_prereq_commands.command_live_argv(
        "docker_matrix",
        vs_prereq_commands.docker_matrix_argv(runner.study_id),
        repository_root=repository_root,
    )
    internet_guard = vs_prereq_commands.command_live_argv(
        "internet_smoke",
        vs_prereq_commands.internet_smoke_argv(runner.study_id, runner.url),
        repository_root=repository_root,
    )
    forbidden_prefixes: list[tuple[str, ...]] = list(
        {
            "dirty-tree": (("docker", "version"),),
            "wrong-python": (("docker", "version"),),
            "target-digest-absent": (("docker", "build"),),
            "capture-iid-tag": (("docker", "container", "inspect"),),
            "capture-iid-missing": (("docker", "container", "inspect"),),
            "preexisting-name": (("docker", "run", "--rm"),),
            "preexisting-cid": (("docker", "run", "--rm"),),
        }.get(mutation, ())
    )
    if mutation not in {"docker-matrix-failed", "internet-skipped", "config-publication-failed"}:
        forbidden_prefixes.append(docker_guard)
    if mutation not in {"internet-skipped", "config-publication-failed"}:
        forbidden_prefixes.append(internet_guard)
    for prefix in forbidden_prefixes:
        assert not any(command[: len(prefix)] == prefix for command in commands)
    if mutation == "capability-timeout-owned":
        assert ("docker", "container", "rm", "--force", runner.container_id) in commands
        assert runner.container_running is False
    if mutation in {"capability-timeout-unowned", "capability-lingering-unowned"}:
        assert not any(command[:3] == ("docker", "container", "rm") for command in commands)
        assert runner.container_running is True
        assert runner.container_id in str(captured.value)
    if mutation == "capability-daemon-error":
        assert "daemon" in str(captured.value).lower()
    if mutation.startswith("capability-timeout"):
        assert "timed out" in str(captured.value).lower()
    evidence = runner.evidence
    if mutation in {
        "canary-not-written",
        "canary-replaced",
        "wrong-write-out",
        "range-ignored",
        "oversize-object",
        "docker-matrix-failed",
        "internet-skipped",
        "config-publication-failed",
    }:
        assert evidence.is_dir()
    if mutation in {
        "capability-timeout-owned",
        "capability-timeout-unowned",
        "canary-not-written",
        "canary-replaced",
        "wrong-write-out",
        "range-ignored",
        "oversize-object",
    }:
        canary = runner.mount / ".capability.headers"
        archive = evidence / "capability.headers"
        assert archive.read_bytes() == canary.read_bytes()
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600


def test_prerequisites_wrap_invalid_study_id_without_attempt_preservation(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()

    with pytest.raises(TrafficlabError, match="prerequisite validation failed"):
        vs_prereq_run.run_prerequisites(
            "https://downloads.example.test/object.bin",
            "INVALID_ID",
            repository_root=repository_root,
            runner=StudyIdentityRunner(repository_root),
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    assert not (repository_root / "examples" / "validation_study" / ".study-work").exists()


def test_capability_normal_exit_proves_exact_full_id_and_anchored_name_absent(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root)

    result = vs_prereq_run.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )

    commands = [command for command, _timeout in runner.calls]
    id_listing = (
        "docker",
        "container",
        "ls",
        "-a",
        "--filter",
        f"id={runner.container_id}",
        "--format",
        "{{.ID}}",
    )
    name_listing = (
        "docker",
        "container",
        "ls",
        "-a",
        "--filter",
        f"name=^/{runner.capability_name}$",
        "--format",
        "{{.ID}}",
    )
    assert result.capability["container_cleanup_verified"] is True
    assert commands.count(id_listing) == 1
    assert commands.count(name_listing) == 2


def test_capability_removes_only_a_lingering_exact_owned_id_after_success(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, "capability-lingering-owned")

    result = vs_prereq_run.run_prerequisites(
        runner.url,
        runner.study_id,
        repository_root=repository_root,
        runner=runner,
        utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )

    commands = [command for command, _timeout in runner.calls]
    assert result.capability["container_cleanup_verified"] is True
    assert ("docker", "container", "rm", "--force", runner.container_id) in commands
    assert (
        commands.count(
            (
                "docker",
                "container",
                "ls",
                "-a",
                "--filter",
                f"id={runner.container_id}",
                "--format",
                "{{.ID}}",
            )
        )
        == 2
    )
    assert (
        commands.count(
            (
                "docker",
                "container",
                "ls",
                "-a",
                "--filter",
                f"name=^/{runner.capability_name}$",
                "--format",
                "{{.ID}}",
            )
        )
        == 2
    )
    assert runner.container_running is False


@pytest.mark.parametrize(
    "mutation",
    [
        "capability-post-id-daemon-error",
        "capability-post-name-daemon-error",
        "capability-name-reclaimed",
        "capability-lingering-owned-name-reclaimed",
    ],
)
def test_capability_cleanup_fails_closed_for_each_listing_and_an_unrelated_name_reclaimer(
    mutation: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, mutation)

    with pytest.raises(TrafficlabError, match="prerequisite validation failed") as captured:
        vs_prereq_run.run_prerequisites(
            runner.url,
            runner.study_id,
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )

    commands = [command for command, _timeout in runner.calls]
    unrelated_id = "f" * 64
    assert not any(command == ("docker", "container", "rm", "--force", unrelated_id) for command in commands)
    if mutation == "capability-lingering-owned-name-reclaimed":
        assert ("docker", "container", "rm", "--force", runner.container_id) in commands
    else:
        assert not any(command[:3] == ("docker", "container", "rm") for command in commands)
    if "daemon-error" in mutation:
        assert "daemon unavailable" in str(captured.value)
    else:
        assert "still exists" in str(captured.value)
    assert (runner.evidence / "capability.stdout").is_file()
    assert (runner.evidence / "capability.stderr").is_file()
    assert (runner.evidence / "capability.headers").is_file()
    assert not (repository_root / "examples" / "validation_study" / "prerequisites.json").exists()


def test_capability_absence_helpers_reject_invalid_daemon_evidence_and_report_absence(tmp_path: Path) -> None:
    container_id = "e" * 64

    def invalid_utf8(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, check, capture_output, shell, timeout
        return subprocess.CompletedProcess(tuple(argv), 0, stdout=b"\xff", stderr=b"")

    with pytest.raises(ValueError, match="UTF-8"):
        vs_prereq_commands.container_listing(
            tmp_path,
            f"id={container_id}",
            runner=invalid_utf8,
        )

    def invalid_inspect(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, check, capture_output, shell, timeout
        command = tuple(argv)
        stdout = f"{container_id}\n".encode() if command[:4] == ("docker", "container", "ls", "-a") else b"not JSON"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    with pytest.raises(ValueError, match="must return JSON"):
        vs_prereq_commands.remove_owned_capability_if_present(
            repository_root=tmp_path,
            study_id="study-1",
            capability_name="trafficlab-validation-study-capability-study-1",
            container_id=container_id,
            runner=invalid_inspect,
        )

    cid = tmp_path / "capability.cid"
    cid.write_text(f"{container_id}\n", encoding="ascii")
    cid.chmod(0o600)

    def absent(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, check, capture_output, shell, timeout
        return subprocess.CompletedProcess(tuple(argv), 0, stdout=b"", stderr=b"")

    diagnostic = vs_prereq_run._cleanup_failed_capability(  # pyright: ignore[reportPrivateUsage]
        repository_root=tmp_path,
        study_id="study-1",
        capability_name="trafficlab-validation-study-capability-study-1",
        capability_cid=cid,
        runner=absent,
    )
    assert diagnostic == f"capability container {container_id} is absent"

    cid.unlink()
    unreadable = vs_prereq_run._cleanup_failed_capability(  # pyright: ignore[reportPrivateUsage]
        repository_root=tmp_path,
        study_id="study-1",
        capability_name="trafficlab-validation-study-capability-study-1",
        capability_cid=cid,
        runner=absent,
    )
    assert "could not read the exclusive CID" in unreadable


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("capability-start-error", "could not start"),
        ("capability-nonzero", "failed with status 7"),
        ("capability-missing-cid", "could not read capability CID"),
    ],
)
def test_capability_failure_boundaries_retain_exact_context(
    mutation: str,
    message: str,
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    runner = ScriptedPrerequisiteRunner(repository_root, mutation)
    runner.evidence.mkdir(parents=True)
    runner.mount.mkdir(parents=True)

    with pytest.raises(ValueError, match=message):
        vs_prereq_run._prepare_capability(  # pyright: ignore[reportPrivateUsage]
            repository_root=repository_root,
            study_id=runner.study_id,
            url=runner.url,
            evidence_directory=runner.evidence,
            mount_directory=runner.mount,
            runner=runner,
            utc_now=lambda: datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )


def test_prerequisite_cli_requires_exact_subcommand_arguments_and_reports_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reject_calls: list[tuple[str, ...]] = []

    def reject_runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: Literal[False],
        capture_output: Literal[True],
        shell: Literal[False],
        timeout: float,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        del cwd, check, capture_output, shell, timeout
        reject_calls.append(tuple(argv))
        raise AssertionError("invalid CLI input must not run a command")

    assert vs_cli.main([], repository_root=tmp_path, runner=reject_runner) == 2
    assert "usage:" in capsys.readouterr().err
    invalid_arguments = (
        ["prerequisites"],
        ["prerequisites", "--url", "https://downloads.example.test/object.bin"],
        ["prerequisites", "--study-id", "study-1"],
        ["prerequisites", "--url", "http://example.test/object", "--study-id", "study-1"],
        ["prerequisites", "--url", "https://downloads.example.test/object.bin", "--study-id", "INVALID"],
        [
            "prerequisites",
            "--url",
            "https://downloads.example.test/object.bin",
            "--study-id",
            "study-1",
            "extra",
        ],
    )
    for arguments in invalid_arguments:
        assert vs_cli.main(arguments, repository_root=tmp_path, runner=reject_runner) == 2
        assert capsys.readouterr().err
    assert reject_calls == []

    repository_root = tmp_path / "repository"
    write_prerequisite_repository_inputs(repository_root)
    runner = ScriptedPrerequisiteRunner(repository_root, "dirty-tree")
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    assert (
        vs_cli.main(
            ["prerequisites", "--url", runner.url, "--study-id", runner.study_id],
            repository_root=repository_root,
            runner=runner,
            utc_now=lambda: now,
        )
        == 2
    )
    error = capsys.readouterr().err.strip()
    assert error.startswith("validation-study: Validation Study prerequisite validation failed:")
    assert "; preserve the ignored evidence" in error
