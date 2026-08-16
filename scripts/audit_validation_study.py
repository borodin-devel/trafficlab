#!/usr/bin/env python3
"""Audit a retained Validation Study bundle without Docker, network, or mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import stat
import subprocess
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path, PurePosixPath
from statistics import fmean
from typing import NoReturn, cast

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_validation_study import (
    ARTIFACT_NAMES,
    PUBLISHED_METHOD_ORDER,
    TARGET_REFERENCE,
    HeldOutEvaluation,
    evaluate_study_held_out,
    parse_retained_prerequisites,
    prerequisite_junit_counts,
    retained_prerequisite_paths,
)
from trafficlab.capture_validation import validate_capture_pair
from trafficlab.comparison import (
    ComparisonResult,
    compare_traces,
    parse_comparison_result,
    render_comparison_result,
    similarity_settings_identity,
)
from trafficlab.compatibility import identify_bytes
from trafficlab.config import ExperimentConfig
from trafficlab.config_io import load_configuration_pair, render_effective_config
from trafficlab.errors import FailureOutcome, TrafficlabError
from trafficlab.generation import reproduce_generated_pcapng
from trafficlab.genetic.checkpoint import CheckpointState, parse_checkpoint, render_history_csv
from trafficlab.genetic.population import rank_candidates
from trafficlab.genetic.strategy import make_strategy_context
from trafficlab.models.registry import BestModel, load_best_model, render_best_model
from trafficlab.pcapng import parse_pcapng_bytes
from trafficlab.trace import TraceEvent, align_generated, normalize_reference, parse_capture_metadata

_MANIFEST = "manifest.json"
_INDEX = "index.json"
_SCHEMA = 2
_WORKLOADS = ("short", "streaming", "bursty")
_REPEATS = (1, 2, 3)
_HEX40 = re.compile(r"[0-9a-f]{40}", flags=re.ASCII)
_HEX64 = re.compile(r"[0-9a-f]{64}", flags=re.ASCII)
_TEMP_SUFFIXES = (".tmp", ".partial", ".swp")


@dataclass(frozen=True, slots=True)
class AuditResult:
    bundle: Path
    run_directory: Path
    manifest_sha256: str
    file_count: int


@dataclass(frozen=True, slots=True)
class _Entry:
    path: str
    size: int
    sha256: str
    owner: str
    lineage: object


@dataclass(frozen=True, slots=True)
class _Training:
    workload: str
    repeat: int
    directory: Path
    contents: Mapping[str, bytes]
    config: ExperimentConfig
    reference: tuple[TraceEvent, ...]
    window: float
    checkpoint: CheckpointState
    best_model: BestModel
    comparison: ComparisonResult


@dataclass(frozen=True, slots=True)
class _Issue(Exception):
    kind: str
    affected: str
    detail: str
    action: str


def _fail(kind: str, affected: str, detail: str, action: str) -> NoReturn:
    raise _Issue(kind, affected, detail, action)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        + b"\n"
    )


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _path_key(value: str) -> bytes:
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _fail("artifact_foreign", value, "bundle path is not strict UTF-8", "restore a strict UTF-8 retained path")


def _read_regular(path: Path, *, affected: str) -> bytes:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        _fail("artifact_missing", affected, f"{affected} is missing", "restore the exact retained artifact")
    except OSError as error:
        _fail("artifact_corrupt", affected, f"could not inspect {affected}: {error}", "repair the retained artifact")
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        _fail(
            "artifact_foreign", affected, f"{affected} must be a regular non-symlink file", "replace the foreign entry"
        )
    try:
        return path.read_bytes()
    except OSError as error:
        _fail("artifact_corrupt", affected, f"could not read {affected}: {error}", "repair the retained artifact")


def _json(content: bytes, *, name: str, canonical: bool = True) -> dict[str, object]:
    try:
        parsed = json.loads(content.decode("utf-8"), object_pairs_hook=_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _fail("artifact_corrupt", name, f"{name} is not strict UTF-8 JSON: {error}", "restore canonical retained JSON")
    if type(parsed) is not dict:
        _fail("artifact_corrupt", name, f"{name} must contain one JSON object", "restore canonical retained JSON")
    document = cast(dict[str, object], parsed)
    if canonical and _canonical(document) != content:
        _fail("artifact_corrupt", name, f"{name} is not canonical JSON", "restore canonical retained JSON")
    return document


def _exact(value: object, keys: tuple[str, ...], *, name: str) -> dict[str, object]:
    if type(value) is not dict or set(cast(dict[str, object], value)) != set(keys):
        _fail("artifact_corrupt", name, f"{name} must contain exactly {', '.join(keys)}", "restore canonical evidence")
    return cast(dict[str, object], value)


def _string(value: object, *, name: str) -> str:
    if type(value) is not str or not value.strip():
        _fail("artifact_corrupt", name, f"{name} must be a nonempty string", "restore canonical evidence")
    return value


def _integer(value: object, *, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail("artifact_corrupt", name, f"{name} must be an integer at least {minimum}", "restore canonical evidence")
    return value


def _relative(value: object, *, name: str) -> str:
    text = _string(value, name=name)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or "\\" in text
        or not path.parts
        or any(part in ("", ".", "..") for part in path.parts)
        or path.as_posix() != text
    ):
        _fail(
            "artifact_foreign",
            name,
            f"{name} must be a normalized bundle-relative POSIX path",
            "restore canonical evidence",
        )
    _path_key(text)
    return text


def _directory(value: object, *, name: str) -> Path:
    if not isinstance(value, Path):
        raise TypeError(f"{name} must be a pathlib.Path")
    try:
        mode = value.lstat().st_mode
    except FileNotFoundError:
        _fail("artifact_missing", name, f"{name} is missing", "restore the retained candidate directory")
    except OSError as error:
        _fail("artifact_corrupt", name, f"could not inspect {name}: {error}", "repair the local filesystem entry")
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        _fail(
            "artifact_foreign", name, f"{name} must be a regular non-symlink directory", "replace the foreign directory"
        )
    return value.resolve()


def files_for_candidate(root: Path, *, include_manifest: bool) -> dict[str, Path]:
    discovered: dict[str, Path] = {}
    try:
        paths = tuple(root.rglob("*"))
    except OSError as error:
        _fail(
            "artifact_corrupt", "bundle", f"could not enumerate retained bundle: {error}", "repair the retained bundle"
        )
    for path in sorted(paths, key=lambda item: _path_key(item.relative_to(root).as_posix())):
        relative = path.relative_to(root).as_posix()
        if any(part.startswith(".") or part.endswith(_TEMP_SUFFIXES) for part in PurePosixPath(relative).parts):
            _fail(
                "artifact_foreign",
                relative,
                f"{relative} is a temporary or hidden retained entry",
                "remove temporary residue",
            )
        try:
            mode = path.lstat().st_mode
        except OSError as error:
            _fail("artifact_corrupt", relative, f"could not inspect {relative}: {error}", "repair the retained bundle")
        if stat.S_ISLNK(mode):
            _fail(
                "artifact_foreign",
                relative,
                f"{relative} must not be a symlink",
                "replace the symlink with retained bytes",
            )
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            _fail(
                "artifact_foreign",
                relative,
                f"{relative} must be a regular file",
                "remove the foreign filesystem entry",
            )
        if relative == _MANIFEST and not include_manifest:
            continue
        discovered[relative] = path
    return discovered


def _entries(content: bytes) -> tuple[_Entry, ...]:
    document = _exact(_json(content, name=_MANIFEST), ("files", "schema_version"), name=_MANIFEST)
    if document["schema_version"] != _SCHEMA or type(document["files"]) is not list:
        _fail(
            "artifact_corrupt",
            _MANIFEST,
            "manifest must use schema version 2 and a file list",
            "restore canonical manifest",
        )
    parsed: list[_Entry] = []
    seen: set[str] = set()
    for item in cast(list[object], document["files"]):
        entry = _exact(item, ("lineage", "owner", "path", "sha256", "size"), name="manifest file entry")
        relative = _relative(entry["path"], name="manifest path")
        if relative == _MANIFEST or relative in seen:
            _fail(
                "artifact_foreign",
                _MANIFEST,
                "manifest paths must be unique and exclude manifest.json",
                "restore canonical manifest",
            )
        seen.add(relative)
        digest = _string(entry["sha256"], name=f"manifest SHA-256 for {relative}")
        if _HEX64.fullmatch(digest) is None:
            _fail(
                "artifact_corrupt",
                _MANIFEST,
                f"manifest SHA-256 for {relative} is invalid",
                "restore canonical manifest",
            )
        parsed.append(
            _Entry(
                relative,
                _integer(entry["size"], name=f"manifest size for {relative}"),
                digest,
                _string(entry["owner"], name=f"manifest owner for {relative}"),
                entry["lineage"],
            )
        )
    if tuple(entry.path for entry in parsed) != tuple(sorted(seen, key=_path_key)):
        _fail(
            "artifact_corrupt",
            _MANIFEST,
            "manifest file entries must be UTF-8-byte sorted",
            "restore canonical manifest",
        )
    return tuple(parsed)


def write_manifest(candidate: Path, ownership: Mapping[str, str], lineage: Mapping[str, object]) -> Path:
    """Write the canonical schema-2 manifest for a completed local candidate tree."""
    root = _directory(candidate, name="candidate")
    files = files_for_candidate(root, include_manifest=False)
    if set(ownership) != set(files) or set(lineage) != set(files):
        raise ValueError("ownership and lineage keys must equal the regular-file inventory")
    entries: list[dict[str, object]] = []
    for relative in sorted(files, key=_path_key):
        owner = ownership[relative]
        if type(owner) is not str or not owner.strip():
            raise ValueError(f"manifest owner for {relative} must be a nonempty string")
        content = _read_regular(files[relative], affected=relative)
        entries.append(
            {
                "lineage": lineage[relative],
                "owner": owner,
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    manifest = root / _MANIFEST
    manifest.write_bytes(_canonical({"files": entries, "schema_version": _SCHEMA}))
    return manifest


def _verify_inventory(root: Path, manifest: bytes) -> tuple[_Entry, ...]:
    entries = _entries(manifest)
    actual = files_for_candidate(root, include_manifest=False)
    expected = {entry.path: entry for entry in entries}
    for relative in sorted(expected, key=_path_key):
        if relative not in actual:
            _fail(
                "artifact_missing",
                relative,
                f"{relative} is missing from the retained bundle",
                "restore the exact retained artifact",
            )
    for relative in sorted(set(actual) - set(expected), key=_path_key):
        _fail(
            "artifact_foreign",
            relative,
            f"{relative} is not listed by the manifest",
            "remove the unlisted artifact and rebuild the manifest",
        )
    for relative in sorted(expected, key=_path_key):
        content = _read_regular(actual[relative], affected=relative)
        entry = expected[relative]
        if len(content) != entry.size or hashlib.sha256(content).hexdigest() != entry.sha256:
            _fail(
                "artifact_corrupt",
                relative,
                f"{relative} does not match its manifest identity",
                "restore the exact retained artifact",
            )
    return entries


def _workload(value: object, *, name: str) -> str:
    result = _string(value, name=name)
    if result not in _WORKLOADS:
        _fail(
            "artifact_corrupt", name, "workload must be short, streaming, or bursty", "restore frozen protocol evidence"
        )
    return result


def _repeat(value: object, *, name: str) -> int:
    result = _integer(value, name=name, minimum=1)
    if result not in _REPEATS:
        _fail("artifact_corrupt", name, "repeat must be one, two, or three", "restore frozen protocol evidence")
    return result


def owner_for_path(relative: str) -> str:
    parts = PurePosixPath(relative).parts
    if relative == _INDEX:
        return "study-index"
    if relative == "protocol.json":
        return "study-protocol"
    if relative == "environment.json":
        return "study-environment"
    if relative == "prerequisites.json":
        return "study-prerequisites"
    if relative == "report_inputs.json":
        return "study-report-inputs"
    if relative == "report.json":
        return "study-report"
    if len(parts) == 2 and parts[0] == "prerequisites":
        kind, _, suffix = parts[1].partition(".")
        if kind in ("docker_matrix", "internet_smoke") and suffix in (
            "command.json",
            "stdout",
            "stderr",
            "status.json",
            "junit.xml",
        ):
            return f"prerequisite:{kind}:{suffix}"
    if len(parts) == 2 and parts[0] == "headers" and parts[1] == f"{parts[1].split('.')[0]}.headers":
        workload = parts[1].removesuffix(".headers")
        if workload in _WORKLOADS:
            return f"transfer-header:{workload}"
    if len(parts) == 2 and parts[0] == "observations" and parts[1].endswith(".json"):
        workload = parts[1].removesuffix(".json")
        if workload in _WORKLOADS:
            return f"external-observation:{workload}"
    if len(parts) == 2 and parts[0] == "configs" and parts[1].endswith(".toml"):
        return f"configuration:{parts[1].removesuffix('.toml')}"
    if (
        len(parts) == 4
        and parts[0] == "training"
        and parts[1] in _WORKLOADS
        and parts[2] in ("r1", "r2", "r3")
        and parts[3] in ARTIFACT_NAMES
    ):
        return f"training:{parts[1]}:{parts[2]}"
    if (
        len(parts) == 3
        and parts[0] == "fresh_simulation"
        and parts[1] in _WORKLOADS
        and parts[2] in ("r1.json", "r2.json", "r3.json")
    ):
        return f"fresh-simulation:{parts[1]}:{parts[2].removesuffix('.json')}"
    if (
        len(parts) == 3
        and parts[0] == "held_out"
        and parts[1] in _WORKLOADS
        and parts[2]
        in {
            "capture.json",
            "reference.pcapng",
            "portable.toml",
            "realized.toml",
            "generated.pcapng",
            "similarity.json",
            "record.json",
            "run.log",
        }
    ):
        return f"held-out:{parts[1]}"
    _fail("artifact_foreign", relative, f"{relative} has no documented owner", "rebuild the candidate inventory")


def lineage_for_path(relative: str) -> dict[str, object]:
    parts = PurePosixPath(relative).parts
    if relative == _INDEX:
        return {"relation": "study-index"}
    if relative in {"protocol.json", "environment.json", "prerequisites.json", "report_inputs.json", "report.json"}:
        return {"relation": relative.removesuffix(".json")}
    if len(parts) == 2 and parts[0] == "prerequisites":
        return {"relation": "prerequisite", "record": parts[1]}
    if len(parts) == 2 and parts[0] == "headers":
        return {"relation": "transfer-header", "workload": parts[1].removesuffix(".headers")}
    if len(parts) == 2 and parts[0] == "observations":
        return {"relation": "external-observation", "workload": parts[1].removesuffix(".json")}
    if len(parts) == 2 and parts[0] == "configs":
        return {"relation": "configuration", "name": parts[1].removesuffix(".toml")}
    if len(parts) == 4 and parts[0] == "training":
        return {"relation": parts[3], "repeat": int(parts[2][1:]), "workload": parts[1]}
    if len(parts) == 3 and parts[0] == "fresh_simulation":
        return {"relation": "fresh_simulation", "repeat": int(parts[2][1]), "workload": parts[1]}
    if len(parts) == 3 and parts[0] == "held_out":
        return {"relation": parts[2], "workload": parts[1]}
    _fail("artifact_foreign", relative, f"{relative} has no documented lineage", "rebuild the candidate inventory")


def _metadata(index: dict[str, object], entries: tuple[_Entry, ...]) -> None:
    ownership = index["ownership"]
    lineage = index["lineage"]
    if type(ownership) is not dict or type(lineage) is not dict:
        _fail(
            "artifact_corrupt", _INDEX, "ownership and lineage must be JSON objects", "restore canonical evidence index"
        )
    expected_owners = {entry.path: entry.owner for entry in entries}
    expected_lineage = {entry.path: entry.lineage for entry in entries}
    if ownership != expected_owners or lineage != expected_lineage:
        _fail(
            "artifact_foreign",
            _INDEX,
            "index ownership or lineage does not match the manifest",
            "restore matching manifest and index",
        )
    for relative in sorted(expected_owners, key=_path_key):
        if expected_owners[relative] != owner_for_path(relative) or expected_lineage[relative] != lineage_for_path(
            relative
        ):
            _fail(
                "artifact_foreign",
                relative,
                f"{relative} has invalid owner or lineage",
                "restore documented ownership and lineage",
            )


def _identity(content: bytes) -> dict[str, object]:
    result: dict[str, object] = dict(identify_bytes(content).as_dict())
    return result


def _git_bytes(repository: Path, argv: tuple[str, ...], *, name: str) -> bytes:
    try:
        completed = subprocess.run(("git", *argv), cwd=repository, check=False, capture_output=True)
    except OSError as error:
        _fail("artifact_corrupt", "environment", f"could not inspect {name}: {error}", "repair the relocated checkout")
    if completed.returncode != 0:
        _fail(
            "artifact_foreign",
            "environment",
            f"could not resolve {name} from the relocated Git checkout",
            "audit from the recorded clean source checkout",
        )
    return completed.stdout


def _git_identity(repository: Path, argv: tuple[str, ...], *, name: str) -> str:
    try:
        value = _git_bytes(repository, argv, name=name).decode("ascii").strip()
    except UnicodeDecodeError as error:
        _fail("artifact_corrupt", "environment", f"{name} is not ASCII: {error}", "repair the relocated checkout")
    if _HEX40.fullmatch(value) is None:
        _fail("artifact_corrupt", "environment", f"{name} is not a Git identity", "repair the relocated checkout")
    return value


def _environment(content: bytes, *, repository: Path) -> dict[str, object]:
    document = _exact(
        _json(content, name="environment.json"),
        (
            "capture_image_id",
            "capture_image_reference",
            "capture_tool_version",
            "compatibility_decision",
            "docker_compose_version",
            "docker_engine_version",
            "host_architecture",
            "kernel_release",
            "python_implementation",
            "python_version",
            "scientific_artifact_schema",
            "source_commit",
            "source_tree",
            "target_image_id",
            "target_image_reference",
            "uv_lock_identity",
        ),
        name="environment.json",
    )
    if document["scientific_artifact_schema"] != 2:
        _fail(
            "scientific_semantics_incompatible",
            "environment",
            "environment must record scientific schema 2",
            "recreate evidence under schema 2",
        )
    if document["python_implementation"] != "CPython" or document["python_version"] != platform.python_version():
        _fail(
            "scientific_semantics_incompatible",
            "environment",
            "environment Python runtime does not match the locked auditor",
            "audit with the retained CPython patch",
        )
    for field, width in (("source_commit", 40), ("source_tree", 40)):
        value = _string(document[field], name=f"environment {field}")
        if (width == 40 and _HEX40.fullmatch(value) is None) or set(value) == {"0"}:
            _fail(
                "artifact_corrupt",
                "environment",
                f"environment {field} must be a nonzero lowercase identity",
                "restore frozen source identity",
            )
    if document["uv_lock_identity"] != _identity(_read_regular(repository / "uv.lock", affected="uv.lock")):
        _fail(
            "artifact_foreign",
            "environment",
            "environment uv.lock identity does not match the relocated repository",
            "use the exact locked repository",
        )
    target_reference = _string(document["target_image_reference"], name="environment target_image_reference")
    if "@sha256:" not in target_reference or _HEX64.fullmatch(target_reference.rsplit("@sha256:", 1)[-1]) is None:
        _fail(
            "artifact_corrupt",
            "environment",
            "environment target_image_reference must be an immutable digest reference",
            "restore image lock evidence",
        )
    for field in ("target_image_id", "capture_image_id"):
        value = _string(document[field], name=f"environment {field}")
        if not value.startswith("sha256:") or _HEX64.fullmatch(value.removeprefix("sha256:")) is None:
            _fail(
                "artifact_corrupt",
                "environment",
                f"environment {field} must be an immutable image ID",
                "restore image identity evidence",
            )
    capture_reference = _string(document["capture_image_reference"], name="environment capture_image_reference")
    if capture_reference != document["capture_image_id"] and (
        "@sha256:" not in capture_reference or _HEX64.fullmatch(capture_reference.rsplit("@sha256:", 1)[-1]) is None
    ):
        _fail(
            "artifact_corrupt",
            "environment",
            "environment capture_image_reference must be its immutable image ID or digest reference",
            "restore image lock evidence",
        )
    decision = _exact(
        document["compatibility_decision"], ("reason", "status"), name="environment compatibility decision"
    )
    for field in (
        "capture_tool_version",
        "docker_compose_version",
        "docker_engine_version",
        "host_architecture",
        "kernel_release",
    ):
        _string(document[field], name=f"environment {field}")
    source_commit = _git_identity(repository, ("rev-parse", "HEAD"), name="relocated Git HEAD")
    source_tree = _git_identity(repository, ("rev-parse", "HEAD^{tree}"), name="relocated Git tree")
    if (source_commit, source_tree) != (document["source_commit"], document["source_tree"]):
        _fail(
            "artifact_foreign",
            "environment",
            "environment source commit or tree does not match the relocated Git checkout",
            "audit from the recorded source revision",
        )
    committed_lock = _git_bytes(repository, ("show", f"{source_commit}:uv.lock"), name="recorded uv.lock")
    current_lock = _read_regular(repository / "uv.lock", affected="uv.lock")
    if current_lock != committed_lock:
        _fail(
            "artifact_foreign",
            "environment",
            "relocated uv.lock bytes do not match the recorded source commit",
            "restore the exact locked source checkout",
        )
    image_lock = _exact(
        _json(
            _read_regular(
                repository / "docker" / "capture" / "image-lock.json", affected="docker/capture/image-lock.json"
            ),
            name="docker/capture/image-lock.json",
        ),
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
        _fail(
            "artifact_foreign",
            "environment",
            "environment image identities do not match the checked image locks",
            "restore image-lock-bound environment evidence",
        )
    expected_decision = {
        "reason": "source, lock, and image-lock identities are compatible",
        "status": "compatible",
    }
    if decision != expected_decision:
        _fail(
            "scientific_semantics_incompatible",
            "environment",
            "environment compatibility decision does not match recomputed locked compatibility",
            "restore the recomputed compatible environment decision",
        )
    return document


def _protocol(content: bytes) -> dict[str, object]:
    document = _exact(
        _json(content, name="protocol.json"),
        ("final_seed", "model_selection", "schema_version", "selection_seeds", "training_repetitions", "workloads"),
        name="protocol.json",
    )
    if document["schema_version"] != 2 or _integer(document["final_seed"], name="protocol final seed") != 97:
        _fail(
            "scientific_semantics_incompatible",
            "protocol",
            "protocol must freeze schema 2 and final seed 97",
            "restore frozen protocol",
        )
    if _integer(document["training_repetitions"], name="protocol training repetitions") != 3:
        _fail(
            "artifact_corrupt",
            "protocol",
            "protocol must retain exactly three training repetitions",
            "restore full protocol",
        )
    seeds = document["selection_seeds"]
    if (
        type(seeds) is not list
        or not seeds
        or any(type(seed) is not int or seed < 0 for seed in cast(list[object], seeds))
    ):
        _fail(
            "artifact_corrupt",
            "protocol",
            "protocol selection seeds must be nonempty nonnegative integers",
            "restore frozen protocol",
        )
    workloads = document["workloads"]
    if type(workloads) is not list or tuple(cast(list[object], workloads)) != _WORKLOADS:
        _fail(
            "artifact_corrupt",
            "protocol",
            "protocol workloads must be short, streaming, bursty in order",
            "restore frozen protocol",
        )
    return document


def _prerequisites(
    bundle: Path, relative: str, *, environment: Mapping[str, object]
) -> tuple[Mapping[str, object], set[str]]:
    try:
        document = parse_retained_prerequisites(_read_regular(bundle / relative, affected=relative))
    except (TypeError, ValueError) as error:
        _fail(
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
        _fail(
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
            content = _read_regular(bundle / path, affected=path)
            if _identity(content) != output["identity"]:
                _fail(
                    "artifact_foreign",
                    path,
                    "prerequisite output does not match its retained content identity",
                    "restore exact prerequisite output bytes",
                )
            if field == "command":
                if _json(content, name=path) != {"argv": record["argv"]}:
                    _fail(
                        "artifact_foreign",
                        path,
                        "prerequisite command copy does not match the frozen argv",
                        "restore matching prerequisite command evidence",
                    )
            elif field == "status":
                if _json(content, name=path) != {"exit_status": record["exit_status"], "tests": record["tests"]}:
                    _fail(
                        "artifact_foreign",
                        path,
                        "prerequisite status copy does not match the frozen command result",
                        "restore matching prerequisite status evidence",
                    )
            elif field in ("stdout", "stderr"):
                try:
                    content.decode("utf-8")
                except UnicodeDecodeError as error:
                    _fail(
                        "artifact_corrupt",
                        path,
                        f"prerequisite output is not UTF-8: {error}",
                        "restore retained prerequisite output",
                    )
            else:
                try:
                    counts = prerequisite_junit_counts(content)
                except ValueError as error:
                    _fail(
                        "artifact_corrupt",
                        path,
                        f"prerequisite JUnit is invalid: {error}",
                        "restore retained JUnit evidence",
                    )
                if counts != record["tests"]:
                    _fail(
                        "artifact_foreign",
                        path,
                        "prerequisite JUnit counts do not match the frozen command result",
                        "restart after passing prerequisites",
                    )
    return document, required


def _canonical_jsonl(content: bytes, *, name: str) -> None:
    if not content or not content.endswith(b"\n"):
        _fail(
            "artifact_corrupt",
            name,
            "run log must be nonempty canonical JSONL with a terminal newline",
            "restore canonical run log",
        )
    for line_number, raw in enumerate(content.splitlines(keepends=True), start=1):
        if not raw.endswith(b"\n"):
            _fail("artifact_corrupt", name, "run log must use LF-terminated records", "restore canonical run log")
        record = _json(raw, name=f"{name}:{line_number}", canonical=False)
        if _canonical(record) != raw:
            _fail("artifact_corrupt", name, "run log record is not canonical JSONL", "restore canonical run log")


def _config_semantics(config: ExperimentConfig) -> dict[str, object]:
    document = cast(dict[str, object], config.model_dump(mode="json", exclude_none=True))
    run = cast(dict[str, object], document["run"])
    run["directory"] = "<operational>"
    return document


def _config_pair(
    bundle: Path, portable: str, realized: str, *, directory: Path, name: str
) -> tuple[ExperimentConfig, set[str]]:
    portable_path = bundle / portable
    portable_content = _read_regular(portable_path, affected=portable)
    try:
        pair = load_configuration_pair(portable_path)
    except TrafficlabError as error:
        _fail(
            "artifact_corrupt",
            portable,
            f"portable configuration is invalid: {error}",
            "restore canonical portable configuration",
        )
    if render_effective_config(pair.portable) != portable_content or pair.realized.run.directory != directory:
        _fail(
            "artifact_foreign",
            portable,
            "portable configuration does not realize to its retained directory",
            "restore matching configuration pair",
        )
    realized_content = _read_regular(bundle / realized, affected=realized)
    try:
        realized_document = tomllib.loads(realized_content.decode("utf-8"))
        realized_config = ExperimentConfig.model_validate(realized_document)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as error:
        _fail(
            "artifact_corrupt",
            realized,
            f"realized configuration is invalid: {error}",
            "restore canonical realized configuration",
        )
    if render_effective_config(realized_config) != realized_content or _config_semantics(
        realized_config
    ) != _config_semantics(pair.realized):
        _fail(
            "artifact_foreign",
            realized,
            "realized configuration does not match its portable configuration",
            "restore matching configuration pair",
        )
    return pair.realized, {portable, realized}


def _score(result: ComparisonResult) -> dict[str, object]:
    return {
        "aggregate": result.aggregate_score,
        "methods": {name: result.methods[name].score for name in PUBLISHED_METHOD_ORDER},
    }


def _capture_lineage(content: bytes, environment: Mapping[str, object]) -> dict[str, object]:
    return {
        "capture_identity": _identity(content),
        "capture_image_id": environment["capture_image_id"],
        "capture_image_reference": environment["capture_image_reference"],
        "capture_tool_version": environment["capture_tool_version"],
        "target_image_id": environment["target_image_id"],
        "target_image_reference": environment["target_image_reference"],
    }


def _require_config_images(config: ExperimentConfig, environment: Mapping[str, object], *, affected: str) -> None:
    if (
        config.target.image != environment["target_image_reference"]
        or config.capture.image != environment["capture_image_reference"]
    ):
        _fail(
            "artifact_foreign",
            affected,
            "configuration image references do not match the frozen prerequisite environment",
            "restore image-lock-bound configuration evidence",
        )


def _training(
    bundle: Path, value: object, *, protocol: dict[str, object], environment: Mapping[str, object]
) -> _Training:
    document = _exact(
        value,
        (
            "directory",
            "capture_lineage",
            "portable_config",
            "portable_config_identity",
            "realized_config",
            "realized_config_identity",
            "reference_identity",
            "repeat",
            "run_config_identity",
            "workload",
        ),
        name="training record",
    )
    workload = _workload(document["workload"], name="training workload")
    repeat = _repeat(document["repeat"], name="training repeat")
    directory_relative = _relative(document["directory"], name="training directory")
    expected_directory = f"training/{workload}/r{repeat}"
    if directory_relative != expected_directory:
        _fail(
            "artifact_foreign",
            directory_relative,
            "training directory does not match its workload and repeat",
            "restore canonical index",
        )
    directory = _directory(bundle / directory_relative, name=directory_relative)
    portable = _relative(document["portable_config"], name="training portable configuration")
    realized = _relative(document["realized_config"], name="training realized configuration")
    expected_portable = f"configs/training-{workload}-r{repeat}.portable.toml"
    expected_realized = f"configs/training-{workload}-r{repeat}.realized.toml"
    if (portable, realized) != (expected_portable, expected_realized):
        _fail(
            "artifact_foreign",
            directory_relative,
            "training configuration paths are not canonical",
            "restore matching configuration paths",
        )
    config, _ = _config_pair(bundle, portable, realized, directory=directory, name=directory_relative)
    _require_config_images(config, environment, affected=directory_relative)
    if config.run.final_seed != protocol["final_seed"] or tuple(config.genetic.trial_seeds) != tuple(
        cast(list[int], protocol["selection_seeds"])
    ):
        _fail(
            "scientific_semantics_incompatible",
            directory_relative,
            "training configuration does not match frozen seeds",
            "restore frozen configuration",
        )
    contents = {
        artifact: _read_regular(directory / artifact, affected=f"{directory_relative}/{artifact}")
        for artifact in ARTIFACT_NAMES
    }
    try:
        pair = load_configuration_pair(directory / "experiment.toml")
    except TrafficlabError as error:
        _fail(
            "artifact_corrupt",
            f"{directory_relative}/experiment.toml",
            f"run configuration is invalid: {error}",
            "restore canonical run configuration",
        )
    if render_effective_config(pair.portable) != contents["experiment.toml"] or pair.realized != config:
        _fail(
            "artifact_foreign",
            f"{directory_relative}/experiment.toml",
            "run configuration does not match retained configuration pair",
            "restore matching run configuration",
        )
    _canonical_jsonl(contents["run.log"], name=f"{directory_relative}/run.log")
    try:
        inspection = validate_capture_pair(directory / "capture.json", directory / "reference.pcapng", deadline=None)
        metadata = parse_capture_metadata(contents["capture.json"], source=directory / "capture.json")
        reference, window = normalize_reference(
            parse_pcapng_bytes(contents["reference.pcapng"], metadata, source=directory / "reference.pcapng")
        )
        context = make_strategy_context(
            config,
            reference,
            window,
            directory,
            experiment_identity=identify_bytes(contents["experiment.toml"]),
            reference_identity=identify_bytes(contents["reference.pcapng"]),
            capture_identity=identify_bytes(contents["capture.json"]),
        )
        checkpoint = parse_checkpoint(contents["checkpoint.json"], context.compatibility)
        best = load_best_model(contents["best_model.json"], source=directory / "best_model.json")
        _, generated, generated_bytes = reproduce_generated_pcapng(best, metadata)
        parsed_generated = parse_pcapng_bytes(
            contents["generated.pcapng"], metadata, source=directory / "generated.pcapng"
        )
    except TrafficlabError as error:
        _fail(
            "artifact_corrupt",
            directory_relative,
            f"training artifact reconstruction failed: {error}",
            "restore matching retained training artifacts",
        )
    if document["capture_lineage"] != _capture_lineage(contents["capture.json"], environment):
        _fail(
            "artifact_foreign",
            directory_relative,
            "training capture lineage does not match retained capture bytes and environment",
            "restore matching training capture lineage",
        )
    if (
        inspection.packet_count != len(reference)
        or render_history_csv(checkpoint) != contents["ga_history.csv"]
        or render_best_model(best) != contents["best_model.json"]
    ):
        _fail(
            "artifact_foreign",
            directory_relative,
            "training artifacts are not their canonical projections",
            "restore canonical training artifacts",
        )
    candidate = rank_candidates(checkpoint.population, family_priority=checkpoint.family_priority)[0]
    if (
        (candidate.family, candidate.genes) != (best.family, best.genes)
        or best.reference_identity != identify_bytes(contents["reference.pcapng"])
        or best.capture_identity != identify_bytes(contents["capture.json"])
    ):
        _fail(
            "artifact_foreign",
            directory_relative,
            "checkpoint winner and retained best model disagree",
            "restore matching checkpoint and best model",
        )
    if (
        best.final_seed != config.run.final_seed
        or best.final_limits != config.generation.final
        or best.observation_window_seconds != window
    ):
        _fail(
            "scientific_semantics_incompatible",
            directory_relative,
            "best model final controls do not match normalized training reference",
            "restore frozen training evidence",
        )
    if generated_bytes != contents["generated.pcapng"] or parsed_generated != generated:
        _fail(
            "artifact_foreign",
            f"{directory_relative}/generated.pcapng",
            "generated trace does not reproduce from the retained model",
            "restore matching generated trace",
        )
    settings_identity = similarity_settings_identity(config.similarity)
    expected_comparison = compare_traces(
        reference, align_generated(generated, window), window, config.similarity
    ).with_input_identities(
        {
            "capture_json": identify_bytes(contents["capture.json"]),
            "generated_pcapng": identify_bytes(contents["generated.pcapng"]),
            "reference_pcapng": identify_bytes(contents["reference.pcapng"]),
            "similarity_settings": settings_identity,
        }
    )
    try:
        persisted_comparison = parse_comparison_result(contents["similarity.json"])
    except (TrafficlabError, ValueError) as error:
        _fail(
            "artifact_corrupt",
            f"{directory_relative}/similarity.json",
            f"comparison is invalid: {error}",
            "restore canonical comparison",
        )
    if (
        render_comparison_result(persisted_comparison) != contents["similarity.json"]
        or persisted_comparison != expected_comparison
    ):
        _fail(
            "artifact_foreign",
            f"{directory_relative}/similarity.json",
            "comparison does not match reconstructed inputs",
            "restore matching comparison evidence",
        )
    identities = {
        "portable_config_identity": _identity(_read_regular(bundle / portable, affected=portable)),
        "realized_config_identity": _identity(_read_regular(bundle / realized, affected=realized)),
        "reference_identity": _identity(contents["reference.pcapng"]),
        "run_config_identity": _identity(contents["experiment.toml"]),
    }
    if any(document[name] != identity for name, identity in identities.items()):
        _fail(
            "artifact_foreign",
            directory_relative,
            "training index identities do not match retained bytes",
            "restore matching index identities",
        )
    return _Training(
        workload, repeat, directory, contents, config, reference, window, checkpoint, best, persisted_comparison
    )


def _fresh(bundle: Path, value: object, training: _Training, *, final_seed: int) -> str:
    document = _exact(
        value,
        (
            "comparison_identity",
            "generated_identity",
            "path",
            "reference_identity",
            "seed",
            "training_directory",
            "training_model_identity",
            "workload",
            "repeat",
        ),
        name="fresh simulation record",
    )
    workload = _workload(document["workload"], name="fresh simulation workload")
    repeat = _repeat(document["repeat"], name="fresh simulation repeat")
    expected_path = f"fresh_simulation/{workload}/r{repeat}.json"
    path = _relative(document["path"], name="fresh simulation path")
    if (workload, repeat, path, document["training_directory"], document["seed"]) != (
        training.workload,
        training.repeat,
        expected_path,
        f"training/{training.workload}/r{training.repeat}",
        final_seed,
    ):
        _fail(
            "artifact_foreign",
            path,
            "fresh simulation record does not bind its training run",
            "restore matching fresh simulation evidence",
        )
    stored = _json(_read_regular(bundle / path, affected=path), name=path)
    if stored != document:
        _fail(
            "artifact_foreign",
            path,
            "fresh simulation record differs from index",
            "restore matching fresh simulation record",
        )
    expected = {
        "comparison_identity": _identity(training.contents["similarity.json"]),
        "generated_identity": _identity(training.contents["generated.pcapng"]),
        "reference_identity": _identity(training.contents["reference.pcapng"]),
        "training_model_identity": _identity(training.contents["best_model.json"]),
    }
    if any(document[name] != item for name, item in expected.items()):
        _fail(
            "artifact_foreign",
            path,
            "fresh simulation identities do not match the training run",
            "restore matching fresh simulation evidence",
        )
    return path


def _selected_training(protocol: Mapping[str, object], training: Sequence[_Training]) -> dict[str, _Training]:
    selection = _exact(protocol["model_selection"], ("rule", "selected"), name="protocol model selection")
    if selection["rule"] != "highest_best_fitness_then_lowest_repeat":
        _fail(
            "scientific_semantics_incompatible",
            "protocol",
            "protocol must freeze the training-only highest-best-fitness selection rule",
            "restore the frozen training-only model-selection protocol",
        )
    values = selection["selected"]
    if type(values) is not list or len(cast(list[object], values)) != len(_WORKLOADS):
        _fail(
            "artifact_corrupt",
            "protocol",
            "protocol must retain one selected training model for each workload",
            "restore complete model-selection evidence",
        )
    selected: dict[str, _Training] = {}
    for value in cast(list[object], values):
        record = _exact(
            value,
            ("best_model_identity", "repeat", "training_directory", "workload"),
            name="protocol selected training model",
        )
        workload = _workload(record["workload"], name="protocol selected workload")
        if workload in selected:
            _fail(
                "artifact_foreign",
                "protocol",
                "protocol selected training models must be unique by workload",
                "restore complete model-selection evidence",
            )
        group = tuple(item for item in training if item.workload == workload)
        winner = min(group, key=lambda item: (-item.checkpoint.best_fitness, item.repeat))
        expected = {
            "best_model_identity": _identity(winner.contents["best_model.json"]),
            "repeat": winner.repeat,
            "training_directory": f"training/{winner.workload}/r{winner.repeat}",
            "workload": winner.workload,
        }
        if record != expected:
            _fail(
                "artifact_foreign",
                "protocol",
                "protocol selected model does not match the reconstructed training-only rule",
                "restore matching model-selection evidence",
            )
        selected[workload] = winner
    if tuple(selected) != _WORKLOADS:
        _fail(
            "artifact_foreign",
            "protocol",
            "protocol selected model records must use workload order",
            "restore ordered model-selection evidence",
        )
    return selected


def _held_out(
    bundle: Path,
    value: object,
    training: _Training,
    *,
    final_seed: int,
    training_references: set[str],
    environment: Mapping[str, object],
) -> tuple[str, set[str]]:
    document = _exact(
        value, ("capture_lineage", "directory", "training_directory", "workload"), name="held-out index record"
    )
    workload = _workload(document["workload"], name="held-out workload")
    directory_relative = _relative(document["directory"], name="held-out directory")
    expected_directory = f"held_out/{workload}"
    if (workload, directory_relative, document["training_directory"]) != (
        training.workload,
        expected_directory,
        f"training/{training.workload}/r{training.repeat}",
    ):
        _fail(
            "artifact_foreign",
            directory_relative,
            "held-out record does not bind its frozen training model",
            "restore matching held-out evidence",
        )
    directory = _directory(bundle / directory_relative, name=directory_relative)
    portable = f"{directory_relative}/portable.toml"
    realized = f"{directory_relative}/realized.toml"
    config, config_paths = _config_pair(bundle, portable, realized, directory=directory, name=directory_relative)
    _require_config_images(config, environment, affected=directory_relative)
    if _config_semantics(config) != _config_semantics(training.config) or config.run.final_seed != final_seed:
        _fail(
            "scientific_semantics_incompatible",
            directory_relative,
            "held-out configuration does not match frozen training controls",
            "restore matching held-out configuration",
        )
    names = ("capture.json", "reference.pcapng", "generated.pcapng", "similarity.json", "record.json", "run.log")
    contents = {name: _read_regular(directory / name, affected=f"{directory_relative}/{name}") for name in names}
    _canonical_jsonl(contents["run.log"], name=f"{directory_relative}/run.log")
    reference_identity = identify_bytes(contents["reference.pcapng"])
    if reference_identity.sha256 in training_references:
        _fail(
            "artifact_foreign",
            f"{directory_relative}/reference.pcapng",
            "held-out reference is not independent from training captures",
            "capture a new held-out reference",
        )
    try:
        evaluation: HeldOutEvaluation = evaluate_study_held_out(
            model_content=training.contents["best_model.json"],
            model_source=training.directory / "best_model.json",
            config=config,
            capture_content=contents["capture.json"],
            capture_source=directory / "capture.json",
            reference_content=contents["reference.pcapng"],
            reference_source=directory / "reference.pcapng",
        )
        persisted = parse_comparison_result(contents["similarity.json"])
    except TrafficlabError as error:
        _fail(
            "artifact_corrupt",
            directory_relative,
            f"held-out reconstruction failed: {error}",
            "restore matching held-out evidence",
        )
    if document["capture_lineage"] != _capture_lineage(contents["capture.json"], environment):
        _fail(
            "artifact_foreign",
            directory_relative,
            "held-out capture lineage does not match retained capture bytes and environment",
            "restore matching held-out capture lineage",
        )
    if (
        evaluation.generated_pcapng != contents["generated.pcapng"]
        or evaluation.comparison_json != contents["similarity.json"]
        or persisted != evaluation.comparison
    ):
        _fail(
            "artifact_foreign",
            directory_relative,
            "held-out outputs do not reproduce from the frozen training model",
            "restore matching held-out outputs",
        )
    record = _exact(
        _json(contents["record.json"], name=f"{directory_relative}/record.json"),
        (
            "capture_identity",
            "capture_lineage",
            "comparison_identity",
            "generated_identity",
            "reference_identity",
            "seed",
            "training_directory",
            "training_model_identity",
            "workload",
        ),
        name=f"{directory_relative}/record.json",
    )
    expected = {
        "capture_identity": evaluation.capture_identity.as_dict(),
        "capture_lineage": _capture_lineage(contents["capture.json"], environment),
        "comparison_identity": _identity(contents["similarity.json"]),
        "generated_identity": evaluation.generated_identity.as_dict(),
        "reference_identity": evaluation.reference_identity.as_dict(),
        "seed": final_seed,
        "training_directory": f"training/{training.workload}/r{training.repeat}",
        "training_model_identity": evaluation.training_model_identity.as_dict(),
        "workload": workload,
    }
    if record != expected:
        _fail(
            "artifact_foreign",
            f"{directory_relative}/record.json",
            "held-out record does not match reconstructed evidence",
            "restore matching held-out record",
        )
    return directory_relative, config_paths | {f"{directory_relative}/{name}" for name in names}


def _mean(scores: Sequence[dict[str, object]]) -> dict[str, object]:
    if not scores:
        _fail("artifact_corrupt", "report_inputs.json", "report arithmetic requires scores", "restore report inputs")
    methods = [cast(dict[str, object], score["methods"]) for score in scores]
    return {
        "aggregate": fmean(cast(float, score["aggregate"]) for score in scores),
        "methods": {name: fmean(cast(float, item[name]) for item in methods) for name in PUBLISHED_METHOD_ORDER},
    }


def _report_inputs(training: Sequence[_Training], held: Mapping[str, HeldOutEvaluation]) -> dict[str, object]:
    fresh_rows: list[dict[str, object]] = []
    training_rows: list[dict[str, object]] = []
    variation_rows: list[dict[str, object]] = []
    held_rows: list[dict[str, object]] = []
    for workload in _WORKLOADS:
        group = tuple(item for item in training if item.workload == workload)
        fresh_rows.append({"score": _mean([_score(item.comparison) for item in group]), "workload": workload})
        training_rows.append(
            {"selection_fitness": fmean(item.checkpoint.best_fitness for item in group), "workload": workload}
        )
        pairs: list[dict[str, object]] = []
        for left, right in combinations(group, 2):
            if left.window != right.window or similarity_settings_identity(
                left.config.similarity
            ) != similarity_settings_identity(right.config.similarity):
                _fail(
                    "scientific_semantics_incompatible",
                    "report_inputs.json",
                    "natural variation requires a common normalized window and similarity settings",
                    "restore common protocol controls before comparing natural variation",
                )
            forward = _score(compare_traces(left.reference, right.reference, left.window, left.config.similarity))
            reverse = _score(compare_traces(right.reference, left.reference, right.window, right.config.similarity))
            pairs.append(
                {
                    "forward": forward,
                    "left_repeat": left.repeat,
                    "reverse": reverse,
                    "right_repeat": right.repeat,
                    "symmetric_mean": _mean((forward, reverse)),
                }
            )
        variation_rows.append(
            {
                "pairs": pairs,
                "symmetric_mean": _mean([cast(dict[str, object], pair["symmetric_mean"]) for pair in pairs]),
                "workload": workload,
            }
        )
        held_rows.append({"score": _score(held[workload].comparison), "workload": workload})
    return {
        "formula": "arithmetic_mean",
        "fresh_simulation": fresh_rows,
        "held_out": held_rows,
        "natural_variation": variation_rows,
        "training": training_rows,
    }


def _expected_paths(
    index: dict[str, object],
    protocol: dict[str, object],
    prerequisite_paths: set[str],
    training: Sequence[_Training],
    fresh_paths: set[str],
    held_paths: set[str],
) -> set[str]:
    paths = {
        _INDEX,
        _relative(index["environment"], name="index environment"),
        _relative(index["protocol"], name="index protocol"),
        _relative(index["prerequisites"], name="index prerequisites"),
        _relative(index["report_inputs"], name="index report inputs"),
        _relative(index["report"], name="index report"),
        *prerequisite_paths,
        *fresh_paths,
        *held_paths,
    }
    for workload in _WORKLOADS:
        paths.add(f"headers/{workload}.headers")
        paths.add(f"observations/{workload}.json")
    for item in training:
        relative = f"training/{item.workload}/r{item.repeat}"
        paths.update(f"{relative}/{name}" for name in ARTIFACT_NAMES)
        paths.add(f"configs/training-{item.workload}-r{item.repeat}.portable.toml")
        paths.add(f"configs/training-{item.workload}-r{item.repeat}.realized.toml")
    return paths


def _headers_and_observations(bundle: Path) -> set[str]:
    paths: set[str] = set()
    for workload in _WORKLOADS:
        header = f"headers/{workload}.headers"
        observation = f"observations/{workload}.json"
        content = _read_regular(bundle / header, affected=header)
        if not content.startswith(b"HTTP/") or b"\r\n\r\n" not in content:
            _fail(
                "artifact_corrupt",
                header,
                "protocol header is not a retained HTTP header block",
                "restore protocol-used headers",
            )
        document = _exact(
            _json(_read_regular(bundle / observation, affected=observation), name=observation),
            ("header_identity", "status", "workload"),
            name=observation,
        )
        if (
            document["workload"] != workload
            or document["status"] != 206
            or document["header_identity"] != _identity(content)
        ):
            _fail(
                "artifact_foreign",
                observation,
                "external observation does not match retained protocol header",
                "restore matching observation",
            )
        paths.update((header, observation))
    return paths


def _audit(bundle: Path, repository: Path, entries: tuple[_Entry, ...]) -> AuditResult:
    index = _exact(
        _json(_read_regular(bundle / _INDEX, affected=_INDEX), name=_INDEX),
        (
            "environment",
            "fresh_simulation",
            "held_out",
            "lineage",
            "ownership",
            "prerequisites",
            "protocol",
            "report",
            "report_inputs",
            "schema_version",
            "training",
        ),
        name=_INDEX,
    )
    if index["schema_version"] != _SCHEMA:
        _fail(
            "scientific_semantics_incompatible",
            _INDEX,
            "evidence index must use schema version 2",
            "rebuild retained evidence under schema 2",
        )
    _metadata(index, entries)
    environment_path = _relative(index["environment"], name="index environment")
    protocol_path = _relative(index["protocol"], name="index protocol")
    prerequisites_path = _relative(index["prerequisites"], name="index prerequisites")
    if (
        environment_path,
        protocol_path,
        prerequisites_path,
        _relative(index["report_inputs"], name="index report inputs"),
        _relative(index["report"], name="index report"),
    ) != (
        "environment.json",
        "protocol.json",
        "prerequisites.json",
        "report_inputs.json",
        "report.json",
    ):
        _fail(
            "artifact_foreign",
            _INDEX,
            "index root evidence paths are not canonical",
            "restore canonical evidence index",
        )
    environment = _environment(
        _read_regular(bundle / environment_path, affected=environment_path), repository=repository
    )
    protocol = _protocol(_read_regular(bundle / protocol_path, affected=protocol_path))
    _, prerequisite_paths = _prerequisites(bundle, prerequisites_path, environment=environment)
    _headers_and_observations(bundle)
    training_values = index["training"]
    if type(training_values) is not list:
        _fail(
            "artifact_corrupt", _INDEX, "index must retain nine training run records", "restore all training evidence"
        )
    training_items = cast(list[object], training_values)
    if len(training_items) != 9:
        _fail(
            "artifact_corrupt", _INDEX, "index must retain nine training run records", "restore all training evidence"
        )
    training = tuple(_training(bundle, value, protocol=protocol, environment=environment) for value in training_items)
    expected_keys = {(workload, repeat) for workload in _WORKLOADS for repeat in _REPEATS}
    if {(item.workload, item.repeat) for item in training} != expected_keys:
        _fail(
            "artifact_foreign",
            _INDEX,
            "training records must contain each workload and repeat exactly once",
            "restore complete training evidence",
        )
    ordered_training = tuple(sorted(training, key=lambda item: (_WORKLOADS.index(item.workload), item.repeat)))
    fresh_values = index["fresh_simulation"]
    if type(fresh_values) is not list:
        _fail(
            "artifact_corrupt",
            _INDEX,
            "index must retain nine fresh_simulation records",
            "restore fresh simulation evidence",
        )
    fresh_items = cast(list[object], fresh_values)
    if len(fresh_items) != 9:
        _fail(
            "artifact_corrupt",
            _INDEX,
            "index must retain nine fresh_simulation records",
            "restore fresh simulation evidence",
        )
    fresh_paths = {
        _fresh(bundle, value, item, final_seed=cast(int, protocol["final_seed"]))
        for value, item in zip(fresh_items, ordered_training, strict=True)
    }
    if len(fresh_paths) != 9:
        _fail(
            "artifact_foreign",
            _INDEX,
            "fresh_simulation records must be unique",
            "restore complete fresh simulation evidence",
        )
    held_values = index["held_out"]
    if type(held_values) is not list:
        _fail(
            "artifact_corrupt",
            _INDEX,
            "index must retain three independent held-out records",
            "restore held-out evidence",
        )
    held_items = cast(list[object], held_values)
    if len(held_items) != 3:
        _fail(
            "artifact_corrupt",
            _INDEX,
            "index must retain three independent held-out records",
            "restore held-out evidence",
        )
    selected = _selected_training(protocol, ordered_training)
    training_references = {identify_bytes(item.contents["reference.pcapng"]).sha256 for item in ordered_training}
    held_evaluations: dict[str, HeldOutEvaluation] = {}
    held_paths: set[str] = set()
    for value in held_items:
        record = _exact(
            value,
            ("capture_lineage", "directory", "training_directory", "workload"),
            name="held-out index record",
        )
        workload = _workload(record["workload"], name="held-out workload")
        if workload in held_evaluations or workload not in selected:
            _fail(
                "artifact_foreign",
                _INDEX,
                "held-out records must bind each workload once",
                "restore complete held-out evidence",
            )
        directory_relative, paths = _held_out(
            bundle,
            value,
            selected[workload],
            final_seed=cast(int, protocol["final_seed"]),
            training_references=training_references,
            environment=environment,
        )
        directory = bundle / directory_relative
        evaluation = evaluate_study_held_out(
            model_content=selected[workload].contents["best_model.json"],
            model_source=selected[workload].directory / "best_model.json",
            config=load_configuration_pair(directory / "portable.toml").realized,
            capture_content=_read_regular(directory / "capture.json", affected=f"{directory_relative}/capture.json"),
            capture_source=directory / "capture.json",
            reference_content=_read_regular(
                directory / "reference.pcapng", affected=f"{directory_relative}/reference.pcapng"
            ),
            reference_source=directory / "reference.pcapng",
        )
        held_evaluations[workload] = evaluation
        held_paths.update(paths)
    expected_paths = _expected_paths(index, protocol, prerequisite_paths, ordered_training, fresh_paths, held_paths)
    actual_paths = {entry.path for entry in entries}
    for relative in sorted(expected_paths - actual_paths, key=_path_key):
        _fail(
            "artifact_missing",
            relative,
            f"{relative} is required by the complete evidence schema",
            "restore complete retained evidence",
        )
    for relative in sorted(actual_paths - expected_paths, key=_path_key):
        _fail(
            "artifact_foreign",
            relative,
            f"{relative} is not part of the complete evidence schema",
            "remove foreign retained evidence",
        )
    inputs_path = _relative(index["report_inputs"], name="index report inputs")
    report_inputs = _json(_read_regular(bundle / inputs_path, affected=inputs_path), name=inputs_path)
    expected_inputs = _report_inputs(ordered_training, held_evaluations)
    if report_inputs != expected_inputs:
        _fail(
            "artifact_foreign",
            inputs_path,
            "report inputs do not match reconstructed evidence arithmetic",
            "restore matching report inputs",
        )
    report_path = _relative(index["report"], name="index report")
    report = _exact(
        _json(_read_regular(bundle / report_path, affected=report_path), name=report_path),
        ("formula", "report_inputs_identity", "summary"),
        name=report_path,
    )
    if (
        report["formula"] != "arithmetic_mean"
        or report["report_inputs_identity"] != _identity(_read_regular(bundle / inputs_path, affected=inputs_path))
        or report["summary"] != expected_inputs
    ):
        _fail(
            "artifact_foreign",
            report_path,
            "report does not match retained report inputs and arithmetic",
            "restore matching report",
        )
    return AuditResult(
        bundle,
        ordered_training[0].directory,
        hashlib.sha256(_read_regular(bundle / _MANIFEST, affected=_MANIFEST)).hexdigest(),
        len(entries),
    )


def audit_bundle(bundle: Path, *, repository: Path) -> AuditResult:
    """Strictly audit one complete candidate before exclusive accepted publication."""
    try:
        root = _directory(bundle, name="bundle")
        repository_root = _directory(repository, name="repository")
        try:
            root.relative_to(repository_root)
        except ValueError:
            _fail(
                "artifact_foreign",
                "bundle",
                "bundle must remain beneath the relocated repository",
                "use a retained candidate beneath the repository",
            )
        manifest = _read_regular(root / _MANIFEST, affected=_MANIFEST)
        entries = _verify_inventory(root, manifest)
        return _audit(root, repository_root, entries)
    except _Issue as issue:
        outcome = FailureOutcome(
            kind=issue.kind,
            stage="publication",
            detail=issue.detail,
            affected_evidence=issue.affected,
            evidence_state="not_published",
            corrective_action=issue.action,
            authority="primary",
        )
        error = TrafficlabError(issue.detail, corrective_action=issue.action)
        error.failure_outcomes = (outcome,)
        error.failure_outcome = outcome
        raise error from issue
    except TrafficlabError as error:
        if error.failure_outcome is not None:
            raise
        outcome = FailureOutcome(
            kind="artifact_corrupt",
            stage="publication",
            detail=str(error),
            affected_evidence="candidate evidence",
            evidence_state="not_published",
            corrective_action=error.corrective_action,
            authority="primary",
        )
        error.failure_outcomes = (outcome,)
        error.failure_outcome = outcome
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="audit_validation_study.py", description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parsed = build_parser().parse_args(argv)
    try:
        result = audit_bundle(parsed.bundle, repository=parsed.repository)
    except TrafficlabError as error:
        print(f"validation-study-audit: {error}; {error.corrective_action}", file=sys.stderr)
        return error.exit_code
    print(f"validation-study-audit: accepted {result.file_count} retained files at {result.bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
