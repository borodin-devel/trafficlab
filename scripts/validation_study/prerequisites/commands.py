"""Commands owner for Validation Study tooling."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast
from urllib.parse import urljoin

from scripts.validation_study.common import (
    ORACLE_URL,
    SUBPROCESS_TIMEOUTS,
    TARGET_REFERENCE,
    JsonObject,
    exact_object,
    image_id_value,
    path_entry_exists,
    require,
    require_timestamp_order,
    require_type,
    sha256,
    strict_int,
    strict_string,
    string_array,
    utc_timestamp,
    validate_endpoint_url,
    validate_study_id,
    validate_test_counts,
)
from scripts.validation_study.transfer import header_blocks, singleton_header

if TYPE_CHECKING:
    from scripts.validation_study.common import PrerequisiteCommandKind
    from scripts.validation_study.records import CommandRunner


def guard_prefix(wall_time: str) -> tuple[str, ...]:
    return (
        "scripts/run_bounded.sh",
        "--memory-high",
        "2G",
        "--memory-max",
        "3G",
        "--swap-max",
        "512M",
        "--wall-time",
        wall_time,
        "--kill-after",
        "10s",
        "--",
    )


def docker_matrix_argv(study_id: str) -> tuple[str, ...]:
    return _expected_prerequisite_command("docker_matrix", study_id=validate_study_id(study_id), url=ORACLE_URL)


def internet_smoke_argv(study_id: str, url: str) -> tuple[str, ...]:
    return _expected_prerequisite_command(
        "internet_smoke", study_id=validate_study_id(study_id), url=validate_endpoint_url(url)
    )


def _command_study_id(argv: Sequence[str]) -> str:
    require(bool(argv), "prerequisite command argv must be nonempty")
    junit_path = PurePosixPath(argv[-1])
    parts = junit_path.parts
    require(
        len(parts) == 7
        and parts[:4] == ("examples", "validation_study", ".study-work", "evidence")
        and (parts[5] == "00-prerequisites"),
        "prerequisite command must use its exact repository-relative JUnit path",
    )
    return validate_study_id(parts[4])


def command_live_argv(kind: PrerequisiteCommandKind, argv: Sequence[str], *, repository_root: Path) -> tuple[str, ...]:
    checked = tuple(argv)
    study_id = _command_study_id(checked)
    if kind == "docker_matrix":
        expected = docker_matrix_argv(study_id)
    else:
        if len(checked) < 4 or checked[-4] != "--internet-url":
            raise ValueError("internet argv must contain its exact URL")
        expected = internet_smoke_argv(study_id, checked[-3])
    require(checked == expected, f"{kind} argv must equal the exact guarded study command")
    if not checked:
        raise ValueError("prerequisite command argv must be nonempty")
    return (*checked[:-1], str(repository_root.resolve() / Path(*PurePosixPath(checked[-1]).parts)))


def _project_command_argv(
    kind: PrerequisiteCommandKind, argv: Sequence[str], *, repository_root: Path
) -> tuple[str, ...]:
    live = tuple(argv)
    require(bool(live), "prerequisite command argv must be nonempty")
    root = repository_root.resolve()
    junit_path = Path(live[-1])
    require(junit_path.is_absolute(), "live prerequisite JUnit path must be absolute")
    try:
        relative = junit_path.resolve().relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError("live prerequisite JUnit path must resolve beneath the repository") from error
    projected = (*live[:-1], relative)
    require(
        command_live_argv(kind, projected, repository_root=root) == live,
        f"{kind} live argv may resolve only its exact JUnit operand",
    )
    return projected


def prerequisite_junit_counts(content: bytes) -> JsonObject:
    """Parse one retained pytest JUnit document into its strict all-passed counts."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError(f"JUnit evidence must be valid XML: {error}") from error
    if root.tag == "testsuite":
        suites = (root,)
    elif root.tag == "testsuites":
        suites = tuple(child for child in root if child.tag == "testsuite")
        require(bool(suites), "JUnit evidence must contain at least one pytest test suite")
    else:
        raise ValueError("JUnit evidence root must be testsuite or testsuites")

    def count(suite: ET.Element[str], name: str) -> int:
        raw = suite.get(name)
        if raw is None or re.fullmatch("[0-9]+", raw) is None:
            raise ValueError(f"JUnit {name} must be an integer")
        return int(raw)

    total = sum(count(suite, "tests") for suite in suites)
    failed = sum(count(suite, "failures") for suite in suites)
    errors = sum(count(suite, "errors") for suite in suites)
    skipped = sum(count(suite, "skipped") for suite in suites)
    counts: JsonObject = {
        "total": total,
        "passed": total - failed - errors - skipped,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
    }
    return validate_test_counts(counts)


def parse_junit_counts(content: bytes) -> JsonObject:
    """Backward-compatible private spelling used by the live prerequisite runner."""
    return prerequisite_junit_counts(content)


def timestamp_now(utc_now: Callable[[], datetime]) -> str:
    value = utc_now()
    require(value.tzinfo is not None, "prerequisite clock must return a timezone-aware UTC datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def completed_output(completed: subprocess.CompletedProcess[bytes], *, operation: str) -> tuple[bytes, bytes]:
    require_type(type(completed.stdout) is bytes, f"{operation} stdout must be bytes")
    require_type(type(completed.stderr) is bytes, f"{operation} stderr must be bytes")
    return (completed.stdout, completed.stderr)


def command_detail(completed: subprocess.CompletedProcess[bytes], *, operation: str) -> str:
    stdout, stderr = completed_output(completed, operation=operation)
    detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
    return detail or "no command output"


def private_bytes(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(content)
        path.chmod(384)
        require(path.read_bytes() == content, f"retained evidence {path.name} must preserve exact bytes")
        require(stat.S_IMODE(path.lstat().st_mode) == 384, f"retained evidence {path.name} must use mode 0600")
    except OSError as error:
        raise ValueError(f"could not retain prerequisite evidence {path}: {error}") from error


def best_effort_preserve_capability_canary(evidence_directory: Path, canary: Path) -> None:
    archive = evidence_directory / "capability.headers"
    if path_entry_exists(archive) or not evidence_directory.is_dir():
        return
    try:
        metadata = canary.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return
        with archive.open("xb") as stream:
            stream.write(canary.read_bytes())
        archive.chmod(384)
    except OSError:
        return


def stdout_text(completed: subprocess.CompletedProcess[bytes], *, operation: str) -> str:
    stdout, _stderr = completed_output(completed, operation=operation)
    try:
        return stdout.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise ValueError(f"{operation} stdout must be UTF-8") from error


def target_image_record(content: bytes) -> JsonObject:
    try:
        loaded = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"target image inspect must return UTF-8 JSON: {error}") from error
    require(type(loaded) is list and len(loaded) == 1, "target image inspect must return exactly one image")
    image = cast(dict[str, object], loaded[0])
    require(type(image) is dict, "target image inspect entry must be an object")
    target_image_id = image_id_value(image.get("Id"), name="target image ID")
    repo_digests = string_array(image.get("RepoDigests"), name="target repository digests", nonempty=True)
    require(TARGET_REFERENCE in repo_digests, "target repository digests must include the approved target reference")
    config = image.get("Config")
    require(type(config) is dict, "target image inspect Config must be an object")
    configured_user = strict_string(
        cast(dict[str, object], config).get("User"), name="target configured user", nonempty=False
    )
    return {
        "target_reference": TARGET_REFERENCE,
        "target_image_id": target_image_id,
        "target_repo_digests": list(sorted(repo_digests)),
        "target_config_user": configured_user,
    }


def inspected_image_id(content: bytes, *, name: str) -> str:
    try:
        loaded = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} inspect must return UTF-8 JSON: {error}") from error
    require(type(loaded) is list and len(loaded) == 1, f"{name} inspect must return exactly one image")
    image = loaded[0]
    require(type(image) is dict, f"{name} inspect entry must be an object")
    return image_id_value(cast(dict[str, object], image).get("Id"), name=f"{name} image ID")


def capability_header_values(content: bytes, *, initial_url: str) -> tuple[int, int, str, str]:
    blocks = header_blocks(content)
    require(len(blocks) <= 4, "capability header must contain at most three redirects")
    current_url = initial_url
    for status_code, headers in blocks[:-1]:
        require(300 <= status_code <= 399, "every capability response before the final block must be a redirect")
        current_url = validate_endpoint_url(urljoin(current_url, singleton_header(headers, "Location")))
    status_code, final_headers = blocks[-1]
    require(status_code == 206, "capability final response status must be exactly 206")
    content_range = singleton_header(final_headers, "Content-Range")
    match = re.fullmatch("bytes 0-0/([0-9]+)", content_range)
    if match is None:
        raise ValueError("capability Content-Range must be bytes 0-0/TOTAL")
    object_size = int(match.group(1))
    require(
        4 * 1024 * 1024 <= object_size <= 16 * 1024 * 1024, "capability object size must be from 4 MiB through 16 MiB"
    )
    length = singleton_header(final_headers, "Content-Length")
    require(length == "1", "capability Content-Length must be exactly one")
    return (len(blocks) - 1, object_size, content_range, current_url)


def capability_write_out(content: bytes) -> tuple[int, int, str, int]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("capability write-out must be UTF-8") from error
    match = re.fullmatch("status=([0-9]+)\\nsize=([0-9]+)\\nurl=([^\\n]+)\\nredirects=([0-9]+)\\n", text)
    if match is None:
        raise ValueError("capability write-out must contain the exact status, size, URL, and redirects lines")
    status = int(match.group(1))
    downloaded = int(match.group(2))
    final_url = validate_endpoint_url(match.group(3))
    redirects = int(match.group(4))
    require(status == 206 and downloaded == 1, "capability write-out must report status 206 and size one")
    require(0 <= redirects <= 3, "capability write-out redirects must be in 0..3")
    return (status, downloaded, final_url, redirects)


def run_prerequisite_test(
    kind: PrerequisiteCommandKind,
    checked_argv: tuple[str, ...],
    *,
    repository_root: Path,
    evidence_directory: Path,
    runner: CommandRunner,
    utc_now: Callable[[], datetime],
) -> JsonObject:
    live_argv = command_live_argv(kind, checked_argv, repository_root=repository_root)
    prefix = "docker" if kind == "docker_matrix" else "internet"
    timeout = SUBPROCESS_TIMEOUTS["docker_matrix_guard" if kind == "docker_matrix" else "internet_smoke_guard"]
    started = timestamp_now(utc_now)
    try:
        completed = runner(
            live_argv, cwd=repository_root, check=False, capture_output=True, shell=False, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"{kind} guarded pytest command failed: {error}") from error
    completed_time = timestamp_now(utc_now)
    stdout, stderr = completed_output(completed, operation=f"{kind} guarded pytest")
    private_bytes(evidence_directory / f"{prefix}.stdout", stdout)
    private_bytes(evidence_directory / f"{prefix}.stderr", stderr)
    if completed.returncode != 0:
        raise ValueError(
            f"{kind} guarded pytest failed with status {completed.returncode}: {command_detail(completed, operation=kind)}"
        )
    junit_path = Path(live_argv[-1])
    try:
        junit = junit_path.read_bytes()
    except OSError as error:
        raise ValueError(f"could not read {kind} JUnit evidence: {error}") from error
    junit_path.chmod(384)
    tests = parse_junit_counts(junit)
    return {
        "kind": kind,
        "argv": list(_project_command_argv(kind, live_argv, repository_root=repository_root)),
        "started_utc": started,
        "completed_utc": completed_time,
        "exit_status": completed.returncode,
        "tests": tests,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "junit_sha256": hashlib.sha256(junit).hexdigest(),
    }


def remove_owned_prerequisite_capture_image(capture_tag: str, *, repository_root: Path, runner: CommandRunner) -> None:
    """Remove the runner-owned shared image without granting fixture ownership of it."""
    completed = runner(
        ("docker", "image", "rm", "--force", capture_tag),
        cwd=repository_root,
        check=False,
        capture_output=True,
        shell=False,
        timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
    )
    require(
        completed.returncode == 0,
        f"could not remove owned prerequisite capture image: {command_detail(completed, operation='capture image cleanup')}",
    )


_PREREQUISITE_IGNORED_TOOL_ROOTS = frozenset(
    {".superpowers", ".venv", ".worktrees", ".pytest_cache", ".pyright", ".ruff_cache", "build", "dist", "htmlcov"}
)

_PREREQUISITE_IGNORED_TOOL_FILES = frozenset({".coverage", "TASK.md"})

_PREREQUISITE_OWNED_IGNORED_PATHS = frozenset(
    {
        "examples/validation_study/prerequisites.json",
        "examples/validation_study/results.json",
        "examples/validation_study/configs/short.toml",
        "examples/validation_study/configs/streaming.toml",
        "examples/validation_study/configs/bursty.toml",
    }
)

_PREREQUISITE_OWNED_IGNORED_PREFIXES = (
    "examples/validation_study/.study-work/",
    "examples/validation_study/.candidates/",
    "examples/validation_study/evidence/.candidates/",
)


def _prerequisite_publisher_temporary_path(path: str) -> bool:
    parts = path.split("/")
    return (
        len(parts) >= 4
        and parts[:3] == ["examples", "validation_study", "evidence"]
        and parts[3].startswith(".")
        and parts[3].endswith(".tmp")
    )


def _permitted_ignored_prerequisite_worktree_path(path: str) -> bool:
    parts = path.split("/")
    first = parts[0]
    if (
        path in _PREREQUISITE_IGNORED_TOOL_FILES
        or first in _PREREQUISITE_IGNORED_TOOL_ROOTS
        or first == ".env"
        or first.startswith(".env.")
        or first.startswith(".coverage.")
        or ("__pycache__" in parts)
        or any(part.endswith(".egg-info") for part in parts)
        or path.endswith((".pyc", ".pyo", ".pyd"))
        or path.endswith(".log")
        or (first == "runs")
    ):
        return True
    return (
        path in _PREREQUISITE_OWNED_IGNORED_PATHS
        or path.startswith(_PREREQUISITE_OWNED_IGNORED_PREFIXES)
        or _prerequisite_publisher_temporary_path(path)
    )


def _prerequisite_worktree_entries(repository_root: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    directories = [repository_root]
    entries: list[str] = []
    nonregular_entries: list[str] = []
    while directories:
        directory = directories.pop()
        try:
            children = tuple(sorted(directory.iterdir(), key=lambda child: child.name))
        except OSError as error:
            raise ValueError(f"could not inspect prerequisite worktree directory: {error}") from error
        for child in children:
            relative = child.relative_to(repository_root).as_posix()
            if relative == ".git" or _permitted_ignored_prerequisite_worktree_path(relative):
                continue
            try:
                mode = child.lstat().st_mode
            except OSError as error:
                raise ValueError(f"could not inspect prerequisite worktree entry: {error}") from error
            if stat.S_ISDIR(mode):
                directories.append(child)
            else:
                entries.append(relative)
                if not stat.S_ISREG(mode):
                    nonregular_entries.append(relative)
    return (tuple(entries), tuple(nonregular_entries))


def _ignored_prerequisite_worktree_paths(
    repository_root: Path, paths: Sequence[str], *, runner: CommandRunner
) -> frozenset[str]:
    if not paths:
        return frozenset()
    completed = runner(
        ("git", "check-ignore", "-z", "--stdin"),
        cwd=repository_root,
        check=False,
        capture_output=True,
        shell=False,
        timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        input=b"".join(os.fsencode(path) + b"\x00" for path in paths),
    )
    output, _stderr = completed_output(completed, operation="ignored prerequisite paths")
    if completed.returncode not in (0, 1):
        raise ValueError(
            f"could not resolve ignored prerequisite paths: {command_detail(completed, operation='ignored prerequisite paths')}"
        )
    if completed.returncode == 0 and (not output):
        raise ValueError("ignored prerequisite paths must be nonempty for match status")
    if completed.returncode == 1 and output:
        raise ValueError("ignored prerequisite paths must be empty for no-match status")
    if output and (not output.endswith(b"\x00")):
        raise ValueError("ignored prerequisite paths must be terminal NUL-delimited")
    records = output[:-1].split(b"\x00") if output else ()
    try:
        ignored_paths = tuple(record.decode("utf-8") for record in records)
    except UnicodeDecodeError as error:
        raise ValueError(f"ignored prerequisite path is not UTF-8: {error}") from error
    if len(set(ignored_paths)) != len(ignored_paths):
        raise ValueError("ignored prerequisite paths must be unique")
    if any(path not in paths for path in ignored_paths):
        raise ValueError("ignored prerequisite paths do not match the inspected worktree")
    return frozenset(ignored_paths)


def require_clean_prerequisite_worktree(repository_root: Path, *, runner: CommandRunner) -> None:
    status_result = runner(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=repository_root,
        check=False,
        capture_output=True,
        shell=False,
        timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
    )
    status_stdout, _status_stderr = completed_output(status_result, operation="Git tree inspection")
    require(status_result.returncode == 0, "could not inspect prerequisite Git tree")
    require(status_stdout == b"", "prerequisites require an exactly clean tracked and untracked Git tree")
    entries, nonregular_entries = _prerequisite_worktree_entries(repository_root)
    ignored_entries = _ignored_prerequisite_worktree_paths(repository_root, entries, runner=runner)
    for path in entries:
        if path in ignored_entries and (not _permitted_ignored_prerequisite_worktree_path(path)):
            raise ValueError(f"ignored prerequisite worktree entry is not permitted: {path}")
    for path in nonregular_entries:
        if path not in ignored_entries:
            raise ValueError(f"prerequisite worktree contains non-regular entry: {path}")


def timeout_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="replace")


def retain_failed_capability_output(evidence_directory: Path, *, stdout: bytes, stderr: bytes) -> str:
    failures: list[str] = []
    for name, content in (("capability.stdout", stdout), ("capability.stderr", stderr)):
        try:
            private_bytes(evidence_directory / name, content)
        except (TypeError, ValueError) as error:
            failures.append(str(error))
    if failures:
        return f"evidence retention incomplete: {'; '.join(failures)}"
    return "capability stdout and stderr were retained"


def container_listing(repository_root: Path, filter_value: str, *, runner: CommandRunner) -> tuple[str, ...]:
    command = ("docker", "container", "ls", "-a", "--filter", filter_value, "--format", "{{.ID}}")
    completed = runner(
        command,
        cwd=repository_root,
        check=False,
        capture_output=True,
        shell=False,
        timeout=SUBPROCESS_TIMEOUTS["container_inspect_or_remove"],
    )
    stdout, _stderr = completed_output(completed, operation="capability container listing")
    require(
        completed.returncode == 0,
        f"could not prove capability container absence: {command_detail(completed, operation='capability container listing')}",
    )
    try:
        lines = tuple(line for line in stdout.decode("utf-8").splitlines() if line)
    except UnicodeDecodeError as error:
        raise ValueError("capability container listing must be UTF-8") from error
    return lines


def remove_owned_capability_if_present(
    *, repository_root: Path, study_id: str, capability_name: str, container_id: str, runner: CommandRunner
) -> bool:
    removed_owned = False
    if container_listing(repository_root, f"id={container_id}", runner=runner):
        inspected = runner(
            ("docker", "container", "inspect", container_id),
            cwd=repository_root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["container_inspect_or_remove"],
        )
        stdout, _stderr = completed_output(inspected, operation="capability ownership inspection")
        require(inspected.returncode == 0, f"capability container {container_id} could not be inspected")
        try:
            loaded = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"capability ownership inspection must return JSON: {error}") from error
        require(type(loaded) is list and len(loaded) == 1, "capability container inspect must return one object")
        document = cast(dict[str, object], loaded[0])
        require(type(document) is dict, "capability container inspect entry must be an object")
        config = document.get("Config")
        require(type(config) is dict, "capability container inspect Config must be an object")
        labels = cast(dict[str, object], config).get("Labels")
        require(type(labels) is dict, "capability container labels must be an object")
        require(
            document.get("Id") == container_id
            and document.get("Name") == f"/{capability_name}"
            and (cast(dict[str, object], labels).get("org.trafficlab.validation-study.study") == study_id),
            f"ownership could not be proved; container {container_id} may remain",
        )
        removed = runner(
            ("docker", "container", "rm", "--force", container_id),
            cwd=repository_root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["container_inspect_or_remove"],
        )
        completed_output(removed, operation="capability removal")
        require(removed.returncode == 0, f"owned capability container {container_id} could not be removed")
        require(
            not container_listing(repository_root, f"id={container_id}", runner=runner),
            f"owned capability container still exists: {container_id}",
        )
        removed_owned = True
    require(
        not container_listing(repository_root, f"name=^/{capability_name}$", runner=runner),
        f"capability container name still exists: {capability_name}",
    )
    return removed_owned


def _expected_prerequisite_command(kind: PrerequisiteCommandKind, *, study_id: str, url: str) -> tuple[str, ...]:
    evidence = f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites"
    capture_tag = f"trafficlab-validation-{study_id}:capture"
    pytest_prefix = ("uv", "run", "--locked", "pytest", "-vv", "-n", "0", "-m")
    if kind == "docker_matrix":
        return (
            *guard_prefix("20m"),
            *pytest_prefix,
            "docker",
            "--capture-image",
            capture_tag,
            "--junitxml",
            f"{evidence}/docker.xml",
        )
    return (
        *guard_prefix("10m"),
        *pytest_prefix,
        "internet",
        "--capture-image",
        capture_tag,
        "--internet-url",
        url,
        "--junitxml",
        f"{evidence}/internet.xml",
    )


def prerequisite_command_argv(kind: str, *, study_id: str, url: str) -> tuple[str, ...]:
    """Return the one frozen, repository-relative prerequisite command for a retained study."""
    if kind not in ("docker_matrix", "internet_smoke"):
        raise ValueError("prerequisite kind must be docker_matrix or internet_smoke")
    checked_kind = kind
    return _expected_prerequisite_command(
        checked_kind, study_id=validate_study_id(study_id), url=validate_endpoint_url(url)
    )


def validate_frozen_prerequisite_command(
    kind: str, argv: Sequence[str], exit_status: object, tests: object, *, study_id: str, url: str
) -> tuple[str, ...]:
    """Validate the command/count core shared by live and retained prerequisite evidence."""
    if kind not in ("docker_matrix", "internet_smoke"):
        raise ValueError("prerequisite kind must be docker_matrix or internet_smoke")
    checked_kind = kind
    checked_argv = tuple(strict_string(item, name=f"{kind} argv item") for item in argv)
    require(bool(checked_argv), f"{kind} argv must be nonempty")
    require(
        checked_argv == prerequisite_command_argv(checked_kind, study_id=study_id, url=url),
        f"{kind} argv must equal the exact guarded study command",
    )
    require(strict_int(exit_status, name=f"{kind} exit status") == 0, f"{kind} exit status must be zero")
    validate_test_counts(tests)
    return checked_argv


def validate_command(value: object, *, expected_kind: PrerequisiteCommandKind, study_id: str, url: str) -> JsonObject:
    keys = (
        "kind",
        "argv",
        "started_utc",
        "completed_utc",
        "exit_status",
        "tests",
        "stdout_sha256",
        "stderr_sha256",
        "junit_sha256",
    )
    document = exact_object(value, keys, name="prerequisite command")
    kind = strict_string(document["kind"], name="prerequisite command kind")
    require(
        kind == expected_kind, f"prerequisite commands must be ordered docker_matrix then internet_smoke; got {kind}"
    )
    argv = string_array(document["argv"], name=f"{kind} argv", nonempty=True)
    started = utc_timestamp(document["started_utc"], name=f"{kind} start")
    completed = utc_timestamp(document["completed_utc"], name=f"{kind} completion")
    require_timestamp_order(started, completed, name=kind)
    validate_frozen_prerequisite_command(
        kind, argv, document["exit_status"], document["tests"], study_id=study_id, url=url
    )
    sha256(document["stdout_sha256"], name=f"{kind} stdout SHA-256")
    sha256(document["stderr_sha256"], name=f"{kind} stderr SHA-256")
    sha256(document["junit_sha256"], name=f"{kind} JUnit SHA-256")
    return cast(JsonObject, document)
