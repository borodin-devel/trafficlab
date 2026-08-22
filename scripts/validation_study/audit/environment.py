"""Environment owner for Validation Study tooling."""

from __future__ import annotations

import platform
import re
import stat
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import cast

from scripts.validation_study.audit.common import (
    HEX40,
    HEX64,
    WORKLOADS,
    artifact_identity,
    exact,
    fail,
    parse_json_object,
    read_regular,
    string,
    validated_study_root,
)
from scripts.validation_study.common import TARGET_REFERENCE
from scripts.validation_study.prerequisites.codec import parse_retained_prerequisites, retained_prerequisite_paths
from scripts.validation_study.prerequisites.commands import prerequisite_junit_counts
from trafficlab.common.config import ExperimentConfig
from trafficlab.study_evidence.protocol import (
    ValidationStudyEnvironment,
    ValidationStudyProtocol,
)

_HEX40 = re.compile("[0-9a-f]{40}", flags=re.ASCII)


def validate_source_identities(source_commit: str, source_tree: str) -> None:
    """Require the nonzero lowercase Git commit and tree identities retained by a fixture."""
    if (
        _HEX40.fullmatch(source_commit) is None
        or _HEX40.fullmatch(source_tree) is None
        or set(source_commit) == {"0"}
        or (set(source_tree) == {"0"})
    ):
        raise ValueError("source identities must be nonzero commit/tree hexadecimal values")


def git_bytes(repository: Path, argv: tuple[str, ...], *, name: str) -> bytes:
    try:
        completed = subprocess.run(("git", *argv), cwd=repository, check=False, capture_output=True)
    except OSError as error:
        fail("artifact_corrupt", "environment", f"could not inspect {name}: {error}", "repair the relocated checkout")
    if completed.returncode != 0:
        fail(
            "artifact_foreign",
            "environment",
            f"could not resolve {name} from the relocated Git checkout",
            "audit from the recorded clean source checkout",
        )
    return completed.stdout


def _git_identity(repository: Path, argv: tuple[str, ...], *, name: str) -> str:
    try:
        value = git_bytes(repository, argv, name=name).decode("ascii").strip()
    except UnicodeDecodeError as error:
        fail("artifact_corrupt", "environment", f"{name} is not ASCII: {error}", "repair the relocated checkout")
    if HEX40.fullmatch(value) is None:
        fail("artifact_corrupt", "environment", f"{name} is not a Git identity", "repair the relocated checkout")
    return value


_RELOCATED_DOCUMENTATION_PATHS = frozenset(
    {"examples/validation_study/REPORT.md", "examples/validation_study/README.md"}
)

_RELOCATED_TEST_PREFIX = "tests/"

_RELOCATED_EVIDENCE_PREFIX = "examples/validation_study/evidence/"

_RELOCATED_IGNORED_TOOL_ROOTS = frozenset(
    {".superpowers", ".venv", ".worktrees", ".pytest_cache", ".pyright", ".ruff_cache", "build", "dist", "htmlcov"}
)

_RELOCATED_IGNORED_TOOL_FILES = frozenset({".coverage", "TASK.md"})

_RELOCATED_IGNORED_VALIDATION_PATHS = frozenset(
    {
        "examples/validation_study/prerequisites.json",
        "examples/validation_study/results.json",
        "examples/validation_study/configs/short.toml",
        "examples/validation_study/configs/streaming.toml",
        "examples/validation_study/configs/bursty.toml",
    }
)

_RELOCATED_IGNORED_VALIDATION_PREFIXES = (
    "examples/validation_study/.study-work/",
    "examples/validation_study/.candidates/",
    "examples/validation_study/evidence/.candidates/",
)


def _permitted_relocated_change(path: str) -> bool:
    return (
        path in _RELOCATED_DOCUMENTATION_PATHS
        or path.startswith(_RELOCATED_TEST_PREFIX)
        or path.startswith(_RELOCATED_EVIDENCE_PREFIX)
    )


def _publisher_temporary_worktree_path(path: str) -> bool:
    parts = path.split("/")
    return (
        len(parts) >= 4
        and parts[:3] == ["examples", "validation_study", "evidence"]
        and parts[3].startswith(".")
        and parts[3].endswith(".tmp")
    )


def _permitted_ignored_relocated_worktree_path(path: str) -> bool:
    parts = path.split("/")
    first = parts[0]
    if (
        path in _RELOCATED_IGNORED_TOOL_FILES
        or first in _RELOCATED_IGNORED_TOOL_ROOTS
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
        path in _RELOCATED_IGNORED_VALIDATION_PATHS
        or path.startswith(_RELOCATED_IGNORED_VALIDATION_PREFIXES)
        or _publisher_temporary_worktree_path(path)
    )


def _relocated_worktree_paths(repository: Path) -> tuple[str, ...]:
    status = git_bytes(
        repository,
        ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"),
        name="relocated Git working tree",
    )
    if not status:
        return ()
    if not status.endswith(b"\x00"):
        fail(
            "artifact_corrupt",
            "environment",
            "relocated Git working-tree status is not NUL-terminated",
            "repair the relocated checkout",
        )
    paths: list[str] = []
    for record in status[:-1].split(b"\x00"):
        if len(record) < 4 or record[2:3] != b" ":
            fail(
                "artifact_corrupt",
                "environment",
                "relocated Git working-tree status is malformed",
                "repair the relocated checkout",
            )
        try:
            state = record[:2].decode("ascii")
        except UnicodeDecodeError as error:
            fail(
                "artifact_corrupt",
                "environment",
                f"relocated Git working-tree status is not ASCII: {error}",
                "repair the relocated checkout",
            )
        if (
            state == "  "
            or any(character not in " MADRCUT?" for character in state)
            or ("?" in state and state != "??")
        ):
            fail(
                "artifact_corrupt",
                "environment",
                "relocated Git working-tree status is malformed",
                "repair the relocated checkout",
            )
        try:
            path = record[3:].decode("utf-8")
        except UnicodeDecodeError as error:
            fail(
                "artifact_corrupt",
                "environment",
                f"relocated Git working-tree path is not UTF-8: {error}",
                "repair the relocated checkout",
            )
        relative = PurePosixPath(path)
        if not path or relative.is_absolute() or any(part == ".." for part in relative.parts):
            fail(
                "artifact_corrupt",
                "environment",
                "relocated Git working-tree path is not repository-relative",
                "repair the relocated checkout",
            )
        paths.append(relative.as_posix())
    return tuple(paths)


def _is_candidate_worktree_path(path: str, candidate_paths: Sequence[str]) -> bool:
    return any(path == candidate or path.startswith(f"{candidate}/") for candidate in candidate_paths)


def _relocated_worktree_entry_paths(
    repository: Path, *, candidate_paths: Sequence[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    directories = [repository]
    paths: list[str] = []
    nonregular_paths: list[str] = []
    while directories:
        directory = directories.pop()
        try:
            children = tuple(sorted(directory.iterdir(), key=lambda child: child.name))
        except OSError as error:
            fail(
                "artifact_corrupt",
                "environment",
                f"could not inspect relocated working-tree directory: {error}",
                "repair the relocated checkout",
            )
        for child in children:
            relative = child.relative_to(repository).as_posix()
            if (
                relative == ".git"
                or _is_candidate_worktree_path(relative, candidate_paths)
                or _permitted_ignored_relocated_worktree_path(relative)
            ):
                continue
            try:
                mode = child.lstat().st_mode
            except OSError as error:
                fail(
                    "artifact_corrupt",
                    "environment",
                    f"could not inspect relocated working-tree entry: {error}",
                    "repair the relocated checkout",
                )
            if stat.S_ISDIR(mode):
                directories.append(child)
            else:
                paths.append(relative)
                if not stat.S_ISREG(mode):
                    nonregular_paths.append(relative)
    return (tuple(paths), tuple(nonregular_paths))


def _ignored_relocated_worktree_paths(repository: Path, paths: Sequence[str]) -> frozenset[str]:
    if not paths:
        return frozenset()
    try:
        input_paths = b"".join(path.encode("utf-8") + b"\x00" for path in paths)
    except UnicodeEncodeError as error:
        fail(
            "artifact_corrupt",
            "environment",
            f"relocated Git working-tree path is not UTF-8: {error}",
            "repair the relocated checkout",
        )
    try:
        completed = subprocess.run(
            ("git", "check-ignore", "-z", "--stdin"),
            cwd=repository,
            check=False,
            capture_output=True,
            input=input_paths,
        )
    except OSError as error:
        fail(
            "artifact_corrupt",
            "environment",
            f"could not inspect relocated Git ignored paths: {error}",
            "repair the relocated checkout",
        )
    if completed.returncode not in (0, 1):
        fail(
            "artifact_foreign",
            "environment",
            "could not resolve ignored paths from the relocated Git checkout",
            "audit from the recorded clean source checkout",
        )
    output = completed.stdout
    if completed.returncode == 0 and (not output):
        fail(
            "artifact_corrupt",
            "environment",
            "relocated Git ignored paths must be nonempty for match status",
            "repair the relocated checkout",
        )
    if completed.returncode == 1 and output:
        fail(
            "artifact_corrupt",
            "environment",
            "relocated Git ignored paths must be empty for no-match status",
            "repair the relocated checkout",
        )
    if output and (not output.endswith(b"\x00")):
        fail(
            "artifact_corrupt",
            "environment",
            "relocated Git ignored paths must be terminal NUL-delimited",
            "repair the relocated checkout",
        )
    records = output[:-1].split(b"\x00") if output else ()
    try:
        ignored_paths = tuple(record.decode("utf-8") for record in records)
    except UnicodeDecodeError as error:
        fail(
            "artifact_corrupt",
            "environment",
            f"relocated Git ignored path is not UTF-8: {error}",
            "repair the relocated checkout",
        )
    if len(set(ignored_paths)) != len(ignored_paths):
        fail(
            "artifact_corrupt",
            "environment",
            "relocated Git ignored paths must be unique",
            "repair the relocated checkout",
        )
    if any(path not in paths for path in ignored_paths):
        fail(
            "artifact_corrupt",
            "environment",
            "relocated Git ignored paths do not match the inspected worktree",
            "repair the relocated checkout",
        )
    return frozenset(ignored_paths)


def require_permitted_relocated_worktree(
    repository: Path, *, candidate: Path, source_candidate: Path | None = None
) -> None:
    candidate_paths: list[str] = []
    for root in (candidate, source_candidate):
        if root is None:
            continue
        try:
            relative = root.relative_to(repository).as_posix()
        except ValueError:
            continue
        if relative != ".":
            candidate_paths.append(relative)
    for path in _relocated_worktree_paths(repository):
        if _permitted_relocated_change(path):
            continue
        if _is_candidate_worktree_path(path, candidate_paths):
            continue
        fail(
            "artifact_foreign",
            "environment",
            f"relocated checkout contains non-evidence working-tree change: {path}",
            "audit a clean descendant containing only accepted evidence and report changes",
        )
    worktree_paths, nonregular_paths = _relocated_worktree_entry_paths(repository, candidate_paths=candidate_paths)
    ignored_paths = _ignored_relocated_worktree_paths(repository, worktree_paths)
    for path in worktree_paths:
        if path in ignored_paths and (not _permitted_ignored_relocated_worktree_path(path)):
            fail(
                "artifact_foreign",
                "environment",
                f"relocated checkout contains non-evidence working-tree change: {path}",
                "audit a clean descendant containing only accepted evidence and report changes",
            )
    for path in nonregular_paths:
        if path not in ignored_paths:
            fail(
                "artifact_foreign",
                "environment",
                f"relocated checkout contains non-regular working-tree entry: {path}",
                "remove the non-regular entry or audit a clean relocated checkout",
            )


def load_environment(content: bytes, *, repository: Path) -> dict[str, object]:
    document = parse_json_object(content, name="environment.json")
    if document.get("scientific_artifact_schema") != 4:
        fail(
            "scientific_semantics_incompatible",
            "environment",
            "environment must record scientific schema 4",
            "recreate evidence under schema 4",
        )
    if (
        "python_implementation" in document
        and document["python_implementation"] != "CPython"
        or ("python_version" in document and document["python_version"] != platform.python_version())
    ):
        fail(
            "scientific_semantics_incompatible",
            "environment",
            "environment Python runtime does not match the locked auditor",
            "audit with the retained CPython patch",
        )
    expected_decision = {"reason": "source, lock, and image-lock identities are compatible", "status": "compatible"}
    raw_decision = document.get("compatibility_decision")
    if type(raw_decision) is dict and set(cast(dict[str, object], raw_decision)) == {"reason", "status"}:
        if raw_decision != expected_decision:
            fail(
                "scientific_semantics_incompatible",
                "environment",
                "environment compatibility decision does not match recomputed locked compatibility",
                "restore the recomputed compatible environment decision",
            )
    document = validated_study_root(
        document, ValidationStudyEnvironment, name="environment.json", affected="environment"
    )
    for field in ("source_commit", "source_tree"):
        value = cast(str, document[field])
        if set(value) == {"0"}:
            fail(
                "artifact_corrupt",
                "environment",
                f"environment {field} must be a nonzero lowercase identity",
                "restore frozen source identity",
            )
    if document["uv_lock_identity"] != artifact_identity(read_regular(repository / "uv.lock", affected="uv.lock")):
        fail(
            "artifact_foreign",
            "environment",
            "environment uv.lock identity does not match the relocated repository",
            "use the exact locked repository",
        )
    target_reference = string(document["target_image_reference"], name="environment target_image_reference")
    if "@sha256:" not in target_reference or HEX64.fullmatch(target_reference.rsplit("@sha256:", 1)[-1]) is None:
        fail(
            "artifact_corrupt",
            "environment",
            "environment target_image_reference must be an immutable digest reference",
            "restore image lock evidence",
        )
    capture_reference = string(document["capture_image_reference"], name="environment capture_image_reference")
    if capture_reference != document["capture_image_id"] and (
        "@sha256:" not in capture_reference or HEX64.fullmatch(capture_reference.rsplit("@sha256:", 1)[-1]) is None
    ):
        fail(
            "artifact_corrupt",
            "environment",
            "environment capture_image_reference must be its immutable image ID or digest reference",
            "restore image lock evidence",
        )
    decision = cast(dict[str, object], document["compatibility_decision"])
    source_commit = string(document["source_commit"], name="environment source_commit")
    source_tree = string(document["source_tree"], name="environment source_tree")
    current_head = _git_identity(repository, ("rev-parse", "HEAD"), name="relocated Git HEAD")
    recorded_tree = _git_identity(repository, ("rev-parse", f"{source_commit}^{{tree}}"), name="recorded source tree")
    if recorded_tree != source_tree:
        fail(
            "artifact_foreign",
            "environment",
            "environment source commit does not resolve to its retained source tree",
            "audit from the recorded source revision",
        )
    try:
        ancestor = subprocess.run(
            ("git", "merge-base", "--is-ancestor", source_commit, current_head),
            cwd=repository,
            check=False,
            capture_output=True,
        )
    except OSError as error:
        fail(
            "artifact_corrupt",
            "environment",
            f"could not inspect source ancestry: {error}",
            "repair the relocated checkout",
        )
    if ancestor.returncode != 0:
        fail(
            "artifact_foreign",
            "environment",
            "environment source commit is not an ancestor of the relocated Git checkout",
            "audit from a descendant of the recorded source revision",
        )
    changed = git_bytes(
        repository,
        ("diff", "--name-only", "-z", "--no-renames", f"{source_commit}..{current_head}"),
        name="post-source changed paths",
    )
    try:
        changed_paths = tuple(path.decode("utf-8") for path in changed.split(b"\x00") if path)
    except UnicodeDecodeError as error:
        fail(
            "artifact_corrupt",
            "environment",
            f"post-source path is not UTF-8: {error}",
            "repair the relocated checkout",
        )
    if any(not _permitted_relocated_change(path) for path in changed_paths):
        fail(
            "artifact_foreign",
            "environment",
            "relocated checkout contains non-evidence changes after the recorded source revision",
            "audit a descendant containing only accepted evidence and report changes",
        )
    committed_lock = git_bytes(repository, ("show", f"{source_commit}:uv.lock"), name="recorded uv.lock")
    current_lock = read_regular(repository / "uv.lock", affected="uv.lock")
    if current_lock != committed_lock:
        fail(
            "artifact_foreign",
            "environment",
            "relocated uv.lock bytes do not match the recorded source commit",
            "restore the exact locked source checkout",
        )
    image_lock_content = read_regular(
        repository / "docker" / "capture" / "image-lock.json", affected="docker/capture/image-lock.json"
    )
    committed_image_lock = git_bytes(
        repository, ("show", f"{source_commit}:docker/capture/image-lock.json"), name="recorded capture image lock"
    )
    if image_lock_content != committed_image_lock:
        fail(
            "artifact_foreign",
            "environment",
            "relocated capture image-lock bytes do not match the recorded source commit",
            "restore the exact checked image lock",
        )
    image_lock = exact(
        parse_json_object(image_lock_content, name="docker/capture/image-lock.json"),
        (
            "base_digest",
            "base_reference",
            "capture_tool_version",
            "debian_snapshot",
            "direct_packages",
            "expected_capture_image_id",
        ),
        name="docker/capture/image-lock.json",
    )
    if (
        document["target_image_reference"] != TARGET_REFERENCE
        or document["capture_image_id"] != image_lock["expected_capture_image_id"]
        or document["capture_tool_version"] != image_lock["capture_tool_version"]
    ):
        fail(
            "artifact_foreign",
            "environment",
            "environment image identities do not match the checked image locks",
            "restore image-lock-bound environment evidence",
        )
    if decision != expected_decision:
        fail(
            "scientific_semantics_incompatible",
            "environment",
            "environment compatibility decision does not match recomputed locked compatibility",
            "restore the recomputed compatible environment decision",
        )
    return document


def load_protocol(content: bytes) -> dict[str, object]:
    document = parse_json_object(content, name="protocol.json")
    if document.get("schema_version") != 4 or document.get("final_seed") != 97:
        fail(
            "scientific_semantics_incompatible",
            "protocol",
            "protocol must freeze schema 4 and final seed 97",
            "restore frozen protocol",
        )
    raw_selection = document.get("model_selection")
    if type(raw_selection) is dict and cast(dict[str, object], raw_selection).get("rule") not in (
        None,
        "highest_best_fitness_then_lowest_repeat",
    ):
        fail(
            "scientific_semantics_incompatible",
            "protocol",
            "protocol model selection rule must retain the frozen training-only rule",
            "restore frozen protocol",
        )
    document = validated_study_root(document, ValidationStudyProtocol, name="protocol.json", affected="protocol")
    if cast(int, document["training_repetitions"]) != 3:
        fail(
            "artifact_corrupt",
            "protocol",
            "protocol must retain exactly three training repetitions",
            "restore full protocol",
        )
    workloads = document["workloads"]
    if tuple(cast(list[object], workloads)) != WORKLOADS:
        fail(
            "artifact_corrupt",
            "protocol",
            "protocol workloads must be short, streaming, bursty in order",
            "restore frozen protocol",
        )
    study_id = string(document["study_id"], name="protocol study ID")
    if document["candidate_id"] != study_id or document["destination_id"] != study_id:
        fail(
            "artifact_foreign",
            "protocol.json",
            "protocol study, candidate, and destination IDs must be identical",
            "restore one exact frozen study identity",
        )
    if document["prerequisite_path"] != "examples/validation_study/prerequisites.json":
        fail(
            "artifact_foreign",
            "protocol.json",
            "protocol prerequisite path must be the canonical checked prerequisite path",
            "restore the canonical prerequisite path",
        )
    return document


def load_prerequisites(
    bundle: Path, relative: str, *, environment: Mapping[str, object]
) -> tuple[Mapping[str, object], set[str]]:
    try:
        document = parse_retained_prerequisites(read_regular(bundle / relative, affected=relative))
    except (TypeError, ValueError) as error:
        fail(
            "artifact_corrupt",
            relative,
            f"retained prerequisite evidence is invalid: {error}",
            "restore canonical prerequisite evidence",
        )
    expected_environment = {
        field: environment[field]
        for field in (
            "capture_image_id",
            "capture_image_reference",
            "capture_tool_version",
            "source_commit",
            "source_tree",
            "target_image_id",
            "target_image_reference",
            "uv_lock_identity",
        )
    }
    if document["environment"] != expected_environment:
        fail(
            "artifact_foreign",
            relative,
            "prerequisite environment does not bind the frozen source and image identities",
            "restore matching prerequisite environment evidence",
        )
    required = {relative, *retained_prerequisite_paths(document)}
    for command in cast(list[object], document["commands"]):
        record = cast(dict[str, object], command)
        for field in ("command", "status", "stdout", "stderr", "junit"):
            output = cast(dict[str, object], record[field])
            path = cast(str, output["path"])
            content = read_regular(bundle / path, affected=path)
            if artifact_identity(content) != output["identity"]:
                fail(
                    "artifact_foreign",
                    path,
                    "prerequisite output does not match its retained content identity",
                    "restore exact prerequisite output bytes",
                )
            if field == "command":
                if parse_json_object(content, name=path) != {"argv": record["argv"]}:
                    fail(
                        "artifact_foreign",
                        path,
                        "prerequisite command copy does not match the frozen argv",
                        "restore matching prerequisite command evidence",
                    )
            elif field == "status":
                if parse_json_object(content, name=path) != {
                    "exit_status": record["exit_status"],
                    "tests": record["tests"],
                }:
                    fail(
                        "artifact_foreign",
                        path,
                        "prerequisite status copy does not match the frozen command result",
                        "restore matching prerequisite status evidence",
                    )
            elif field in ("stdout", "stderr"):
                try:
                    content.decode("utf-8")
                except UnicodeDecodeError as error:
                    fail(
                        "artifact_corrupt",
                        path,
                        f"prerequisite output is not UTF-8: {error}",
                        "restore retained prerequisite output",
                    )
            else:
                try:
                    counts = prerequisite_junit_counts(content)
                except ValueError as error:
                    fail(
                        "artifact_corrupt",
                        path,
                        f"prerequisite JUnit is invalid: {error}",
                        "restore retained JUnit evidence",
                    )
                if counts != record["tests"]:
                    fail(
                        "artifact_foreign",
                        path,
                        "prerequisite JUnit counts do not match the frozen command result",
                        "restart after passing prerequisites",
                    )
    return (document, required)


def config_semantics(config: ExperimentConfig) -> dict[str, object]:
    document = cast(dict[str, object], config.model_dump(mode="json", exclude_none=True))
    run = cast(dict[str, object], document["run"])
    run["directory"] = "<operational>"
    target = cast(dict[str, object], document["target"])
    mounts = cast(list[dict[str, object]], target["mounts"])
    for mount in mounts:
        mount["source"] = "<operational>"
    return document
