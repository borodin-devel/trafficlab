#!/usr/bin/env python3
"""Audit a retained Validation Study bundle without Docker, network, or mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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
from statistics import fmean, variance
from typing import NoReturn, cast

from pydantic import BaseModel, ValidationError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_validation_study import (
    ARTIFACT_NAMES,
    PRIMARY_ORDER,
    PUBLISHED_METHOD_ORDER,
    TARGET_REFERENCE,
    HeldOutEvaluation,
    _parse_transfer_header,  # pyright: ignore[reportPrivateUsage]
    parse_retained_prerequisites,
    prerequisite_junit_counts,
    retained_prerequisite_paths,
)
from trafficlab import USER_AGENT
from trafficlab.artifacts import quantize_generated_events
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
from trafficlab.models.registry import BestModel, get_family, load_best_model, render_best_model, runtime_fitted_model
from trafficlab.pcapng import encode_pcapng, parse_pcapng_bytes
from trafficlab.study_evidence import (
    ValidationStudyEnvironment,
    ValidationStudyLifecycle,
    ValidationStudyLineage,
    ValidationStudyManifest,
    ValidationStudyProtocol,
    ValidationStudyReport,
    ValidationStudyReportInput,
)
from trafficlab.trace import TraceEvent, align_generated, normalize_reference, parse_capture_metadata

_MANIFEST = "manifest.json"
_INDEX = "index.json"
_SCHEMA = 2
_INDEX_SCHEMA = 3
_WORKLOADS = ("short", "streaming", "bursty")
_REPEATS = (1, 2, 3)
_HEX40 = re.compile(r"[0-9a-f]{40}", flags=re.ASCII)
_HEX64 = re.compile(r"[0-9a-f]{64}", flags=re.ASCII)
_TEMP_SUFFIXES = (".tmp", ".partial", ".swp")
_TRANSFER_PROFILE_URL = "https://validation-study.example/object"
_MODEL_FAMILIES = ("poisson_empirical", "markov_renewal", "mmpp")
_FIXTURE_STUDY_ID = "fixture-study"
_FIXTURE_URL = "https://downloads.example.test/object.bin"


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
class _Transfer:
    scope: str
    run_id: str
    workload: str
    transfer_index: int
    requested_start: int
    requested_end: int
    filename: str


@dataclass(frozen=True, slots=True)
class _FrozenWorkload:
    """Auditor-owned immutable workload policy, independent of collection code."""

    argv: tuple[str, ...]
    transfers: tuple[tuple[int, int, str], ...]
    workload_timeout_seconds: float
    total_timeout_seconds: float
    multiscale_widths_seconds: tuple[float, float]


_FROZEN_CURL_COMMON = (
    "--fail",
    "--silent",
    "--show-error",
    "--location",
    "--max-redirs",
    "3",
    "--proto",
    "=https",
    "--proto-redir",
    "=https",
    "--http1.1",
    "--user-agent",
    USER_AGENT,
    "--connect-timeout",
    "15",
)


def _frozen_workload_profiles(url: str) -> dict[str, _FrozenWorkload]:
    """Reconstruct the validation profile without reusing the collection oracle."""

    # The apparent duplication with the collector is deliberate.  An auditor
    # that imported the producer's profile would accept the same accidental or
    # malicious mutation on both sides and cease to be an independent oracle.

    short = _FrozenWorkload(
        argv=(
            *_FROZEN_CURL_COMMON,
            "--max-time",
            "30",
            "--limit-rate",
            "4M",
            "--range",
            "0-1048575",
            "--max-filesize",
            "1048576",
            "--dump-header",
            "/trafficlab-study/short.headers",
            "--output",
            "/dev/null",
            "--url",
            url,
        ),
        transfers=((0, 1_048_575, "short.headers"),),
        workload_timeout_seconds=35.0,
        total_timeout_seconds=90.0,
        multiscale_widths_seconds=(0.001, 0.01),
    )
    streaming = _FrozenWorkload(
        argv=(
            *_FROZEN_CURL_COMMON,
            "--max-time",
            "40",
            "--limit-rate",
            "256K",
            "--range",
            "0-4194303",
            "--max-filesize",
            "4194304",
            "--dump-header",
            "/trafficlab-study/streaming.headers",
            "--output",
            "/dev/null",
            "--url",
            url,
        ),
        transfers=((0, 4_194_303, "streaming.headers"),),
        workload_timeout_seconds=50.0,
        total_timeout_seconds=120.0,
        multiscale_widths_seconds=(0.25, 1.0),
    )
    bursty_transfers = tuple(
        (start, start + 32_767, f"bursty-{index}.headers")
        for index, start in enumerate((0, 524_288, 1_048_576, 1_572_864, 2_097_152, 2_621_440, 3_145_728, 3_670_016))
    )
    bursty_groups: list[str] = []
    for index, (start, end, filename) in enumerate(bursty_transfers):
        if index:
            bursty_groups.append("--next")
        bursty_groups.extend(
            (
                *_FROZEN_CURL_COMMON,
                "--max-time",
                "30",
                "--range",
                f"{start}-{end}",
                "--max-filesize",
                "32768",
                "--dump-header",
                f"/trafficlab-study/{filename}",
                "--output",
                "/dev/null",
                "--url",
                url,
            )
        )
    return {
        "short": short,
        "streaming": streaming,
        "bursty": _FrozenWorkload(
            argv=("--parallel", "--parallel-max", "4", "--fail-early", *bursty_groups),
            transfers=bursty_transfers,
            workload_timeout_seconds=35.0,
            total_timeout_seconds=90.0,
            multiscale_widths_seconds=(0.001, 0.01),
        ),
    }


@dataclass(frozen=True, slots=True)
class _Training:
    workload: str
    repeat: int
    directory: Path
    contents: Mapping[str, bytes]
    config: ExperimentConfig
    reference: tuple[TraceEvent, ...]
    window: float
    runtime_seconds: float
    checkpoint: CheckpointState
    best_model: BestModel
    comparison: ComparisonResult


@dataclass(frozen=True, slots=True)
class _Issue(Exception):
    kind: str
    affected: str
    detail: str
    action: str


_TRANSFER_RUNS = (
    *(("training", run_id, workload) for _order, run_id, workload, _repeat in PRIMARY_ORDER),
    *(("held_out", f"held-out-{workload}", workload) for workload in _WORKLOADS),
)
_TRANSFER_SPECS = {
    name: profile.transfers for name, profile in _frozen_workload_profiles(_TRANSFER_PROFILE_URL).items()
}
_TRANSFER_BINDINGS = (
    _Transfer("prerequisites", "00-prerequisites", "prerequisites", 0, 0, 0, "capability.headers"),
) + tuple(
    _Transfer(scope, run_id, workload, index, start, end, filename)
    for scope, run_id, workload in _TRANSFER_RUNS
    for index, (start, end, filename) in enumerate(_TRANSFER_SPECS[workload])
)


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


def _validated_study_root(
    document: dict[str, object],
    model: type[BaseModel],
    *,
    name: str,
    affected: str | None = None,
) -> dict[str, object]:
    """Validate one public study shape after duplicate-free canonical JSON decoding."""

    try:
        validated = model.model_validate(document)
    except ValidationError as error:
        first = error.errors(include_url=False, include_input=False)[0]
        location = ".".join(str(part) for part in first["loc"]) or "root"
        _fail(
            "artifact_corrupt",
            affected or name,
            f"{name} has invalid {location}: {first['msg']} [{first['type']}]",
            "restore canonical evidence",
        )
    return cast(dict[str, object], validated.model_dump(mode="json"))


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
    # Inventory the filesystem before trusting manifest paths.  This detects
    # foreign files, symlinks, FIFOs, and unowned residue that a manifest could
    # otherwise omit from its self-description.
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


def _transfer_path(relative: str) -> tuple[str, _Transfer] | None:
    parts = PurePosixPath(relative).parts
    if len(parts) != 4 or parts[0] not in ("headers", "observations"):
        return None
    kind, scope, run_id, name = parts
    if kind == "observations":
        if not name.endswith(".json"):
            return None
        filename = name.removesuffix(".json")
    else:
        filename = name
    for binding in _TRANSFER_BINDINGS:
        if (binding.scope, binding.run_id, binding.filename) == (scope, run_id, filename):
            return kind, binding
    return None


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
    if relative == "lifecycle.json":
        return "study-lifecycle"
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
    transfer_path = _transfer_path(relative)
    if transfer_path is not None:
        kind, binding = transfer_path
        owner = "transfer-header" if kind == "headers" else "external-observation"
        return f"{owner}:{binding.scope}:{binding.run_id}:{binding.transfer_index}"
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
    if relative in {
        "protocol.json",
        "environment.json",
        "prerequisites.json",
        "report_inputs.json",
        "report.json",
        "lifecycle.json",
    }:
        return {"relation": relative.removesuffix(".json")}
    if len(parts) == 2 and parts[0] == "prerequisites":
        return {"relation": "prerequisite", "record": parts[1]}
    transfer_path = _transfer_path(relative)
    if transfer_path is not None:
        kind, binding = transfer_path
        return {
            "filename": binding.filename,
            "relation": "transfer-header" if kind == "headers" else "external-observation",
            "requested_end": binding.requested_end,
            "requested_start": binding.requested_start,
            "run_id": binding.run_id,
            "scope": binding.scope,
            "transfer_index": binding.transfer_index,
            "workload": binding.workload,
        }
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


_RELOCATED_DOCUMENTATION_PATHS = frozenset(
    {
        "examples/validation_study/REPORT.md",
        "examples/validation_study/README.md",
    }
)
_RELOCATED_TEST_PREFIX = "tests/"
_RELOCATED_EVIDENCE_PREFIX = "examples/validation_study/evidence/"
_RELOCATED_IGNORED_TOOL_ROOTS = frozenset(
    {
        ".superpowers",
        ".venv",
        ".worktrees",
        ".pytest_cache",
        ".pyright",
        ".ruff_cache",
        "build",
        "dist",
        "htmlcov",
    }
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
        or "__pycache__" in parts
        or any(part.endswith(".egg-info") for part in parts)
        or path.endswith((".pyc", ".pyo", ".pyd"))
        or path.endswith(".log")
        or first == "runs"
    ):
        return True
    return (
        path in _RELOCATED_IGNORED_VALIDATION_PATHS
        or path.startswith(_RELOCATED_IGNORED_VALIDATION_PREFIXES)
        or _publisher_temporary_worktree_path(path)
    )


def _relocated_worktree_paths(repository: Path) -> tuple[str, ...]:
    status = _git_bytes(
        repository,
        ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"),
        name="relocated Git working tree",
    )
    if not status:
        return ()
    if not status.endswith(b"\0"):
        _fail(
            "artifact_corrupt",
            "environment",
            "relocated Git working-tree status is not NUL-terminated",
            "repair the relocated checkout",
        )
    paths: list[str] = []
    for record in status[:-1].split(b"\0"):
        if len(record) < 4 or record[2:3] != b" ":
            _fail(
                "artifact_corrupt",
                "environment",
                "relocated Git working-tree status is malformed",
                "repair the relocated checkout",
            )
        try:
            state = record[:2].decode("ascii")
        except UnicodeDecodeError as error:
            _fail(
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
            _fail(
                "artifact_corrupt",
                "environment",
                "relocated Git working-tree status is malformed",
                "repair the relocated checkout",
            )
        try:
            path = record[3:].decode("utf-8")
        except UnicodeDecodeError as error:
            _fail(
                "artifact_corrupt",
                "environment",
                f"relocated Git working-tree path is not UTF-8: {error}",
                "repair the relocated checkout",
            )
        relative = PurePosixPath(path)
        if not path or relative.is_absolute() or any(part == ".." for part in relative.parts):
            _fail(
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
    repository: Path,
    *,
    candidate_paths: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    directories = [repository]
    paths: list[str] = []
    nonregular_paths: list[str] = []
    while directories:
        directory = directories.pop()
        try:
            children = tuple(sorted(directory.iterdir(), key=lambda child: child.name))
        except OSError as error:
            _fail(
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
                _fail(
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
    return tuple(paths), tuple(nonregular_paths)


def _nonregular_relocated_worktree_paths(  # pyright: ignore[reportUnusedFunction]
    repository: Path,
    *,
    candidate_paths: Sequence[str],
) -> tuple[str, ...]:
    return _relocated_worktree_entry_paths(repository, candidate_paths=candidate_paths)[1]


def _ignored_relocated_worktree_paths(repository: Path, paths: Sequence[str]) -> frozenset[str]:
    if not paths:
        return frozenset()
    try:
        input_paths = b"".join(path.encode("utf-8") + b"\0" for path in paths)
    except UnicodeEncodeError as error:
        _fail(
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
        _fail(
            "artifact_corrupt",
            "environment",
            f"could not inspect relocated Git ignored paths: {error}",
            "repair the relocated checkout",
        )
    if completed.returncode not in (0, 1):
        _fail(
            "artifact_foreign",
            "environment",
            "could not resolve ignored paths from the relocated Git checkout",
            "audit from the recorded clean source checkout",
        )
    output = completed.stdout
    if completed.returncode == 0 and not output:
        _fail(
            "artifact_corrupt",
            "environment",
            "relocated Git ignored paths must be nonempty for match status",
            "repair the relocated checkout",
        )
    if completed.returncode == 1 and output:
        _fail(
            "artifact_corrupt",
            "environment",
            "relocated Git ignored paths must be empty for no-match status",
            "repair the relocated checkout",
        )
    if output and not output.endswith(b"\0"):
        _fail(
            "artifact_corrupt",
            "environment",
            "relocated Git ignored paths must be terminal NUL-delimited",
            "repair the relocated checkout",
        )
    records = output[:-1].split(b"\0") if output else ()
    try:
        ignored_paths = tuple(record.decode("utf-8") for record in records)
    except UnicodeDecodeError as error:
        _fail(
            "artifact_corrupt",
            "environment",
            f"relocated Git ignored path is not UTF-8: {error}",
            "repair the relocated checkout",
        )
    if len(set(ignored_paths)) != len(ignored_paths):
        _fail(
            "artifact_corrupt",
            "environment",
            "relocated Git ignored paths must be unique",
            "repair the relocated checkout",
        )
    if any(path not in paths for path in ignored_paths):
        _fail(
            "artifact_corrupt",
            "environment",
            "relocated Git ignored paths do not match the inspected worktree",
            "repair the relocated checkout",
        )
    return frozenset(ignored_paths)


def _require_permitted_relocated_worktree(
    repository: Path,
    *,
    candidate: Path,
    source_candidate: Path | None = None,
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
        _fail(
            "artifact_foreign",
            "environment",
            f"relocated checkout contains non-evidence working-tree change: {path}",
            "audit a clean descendant containing only accepted evidence and report changes",
        )
    worktree_paths, nonregular_paths = _relocated_worktree_entry_paths(repository, candidate_paths=candidate_paths)
    ignored_paths = _ignored_relocated_worktree_paths(repository, worktree_paths)
    for path in worktree_paths:
        if path in ignored_paths and not _permitted_ignored_relocated_worktree_path(path):
            _fail(
                "artifact_foreign",
                "environment",
                f"relocated checkout contains non-evidence working-tree change: {path}",
                "audit a clean descendant containing only accepted evidence and report changes",
            )
    for path in nonregular_paths:
        if path not in ignored_paths:
            _fail(
                "artifact_foreign",
                "environment",
                f"relocated checkout contains non-regular working-tree entry: {path}",
                "remove the non-regular entry or audit a clean relocated checkout",
            )


def _environment(content: bytes, *, repository: Path) -> dict[str, object]:
    document = _json(content, name="environment.json")
    if document.get("scientific_artifact_schema") != 2:
        _fail(
            "scientific_semantics_incompatible",
            "environment",
            "environment must record scientific schema 2",
            "recreate evidence under schema 2",
        )
    if ("python_implementation" in document and document["python_implementation"] != "CPython") or (
        "python_version" in document and document["python_version"] != platform.python_version()
    ):
        _fail(
            "scientific_semantics_incompatible",
            "environment",
            "environment Python runtime does not match the locked auditor",
            "audit with the retained CPython patch",
        )
    expected_decision = {
        "reason": "source, lock, and image-lock identities are compatible",
        "status": "compatible",
    }
    raw_decision = document.get("compatibility_decision")
    if type(raw_decision) is dict and set(cast(dict[str, object], raw_decision)) == {"reason", "status"}:
        if raw_decision != expected_decision:
            _fail(
                "scientific_semantics_incompatible",
                "environment",
                "environment compatibility decision does not match recomputed locked compatibility",
                "restore the recomputed compatible environment decision",
            )
    document = _validated_study_root(
        document, ValidationStudyEnvironment, name="environment.json", affected="environment"
    )
    for field in ("source_commit", "source_tree"):
        value = cast(str, document[field])
        if set(value) == {"0"}:
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
    decision = cast(dict[str, object], document["compatibility_decision"])
    source_commit = _string(document["source_commit"], name="environment source_commit")
    source_tree = _string(document["source_tree"], name="environment source_tree")
    current_head = _git_identity(repository, ("rev-parse", "HEAD"), name="relocated Git HEAD")
    recorded_tree = _git_identity(
        repository,
        ("rev-parse", f"{source_commit}^{{tree}}"),
        name="recorded source tree",
    )
    if recorded_tree != source_tree:
        _fail(
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
        _fail(
            "artifact_corrupt",
            "environment",
            f"could not inspect source ancestry: {error}",
            "repair the relocated checkout",
        )
    if ancestor.returncode != 0:
        _fail(
            "artifact_foreign",
            "environment",
            "environment source commit is not an ancestor of the relocated Git checkout",
            "audit from a descendant of the recorded source revision",
        )
    changed = _git_bytes(
        repository,
        ("diff", "--name-only", "-z", "--no-renames", f"{source_commit}..{current_head}"),
        name="post-source changed paths",
    )
    try:
        changed_paths = tuple(path.decode("utf-8") for path in changed.split(b"\0") if path)
    except UnicodeDecodeError as error:
        _fail(
            "artifact_corrupt",
            "environment",
            f"post-source path is not UTF-8: {error}",
            "repair the relocated checkout",
        )
    if any(not _permitted_relocated_change(path) for path in changed_paths):
        _fail(
            "artifact_foreign",
            "environment",
            "relocated checkout contains non-evidence changes after the recorded source revision",
            "audit a descendant containing only accepted evidence and report changes",
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
    image_lock_content = _read_regular(
        repository / "docker" / "capture" / "image-lock.json", affected="docker/capture/image-lock.json"
    )
    committed_image_lock = _git_bytes(
        repository,
        ("show", f"{source_commit}:docker/capture/image-lock.json"),
        name="recorded capture image lock",
    )
    if image_lock_content != committed_image_lock:
        _fail(
            "artifact_foreign",
            "environment",
            "relocated capture image-lock bytes do not match the recorded source commit",
            "restore the exact checked image lock",
        )
    image_lock = _exact(
        _json(image_lock_content, name="docker/capture/image-lock.json"),
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
    if decision != expected_decision:
        _fail(
            "scientific_semantics_incompatible",
            "environment",
            "environment compatibility decision does not match recomputed locked compatibility",
            "restore the recomputed compatible environment decision",
        )
    return document


def _protocol(content: bytes) -> dict[str, object]:
    document = _json(content, name="protocol.json")
    if document.get("schema_version") != 3 or document.get("final_seed") != 97:
        _fail(
            "scientific_semantics_incompatible",
            "protocol",
            "protocol must freeze schema 3 and final seed 97",
            "restore frozen protocol",
        )
    raw_selection = document.get("model_selection")
    if type(raw_selection) is dict and cast(dict[str, object], raw_selection).get("rule") not in (
        None,
        "highest_best_fitness_then_lowest_repeat",
    ):
        _fail(
            "scientific_semantics_incompatible",
            "protocol",
            "protocol model selection rule must retain the frozen training-only rule",
            "restore frozen protocol",
        )
    document = _validated_study_root(document, ValidationStudyProtocol, name="protocol.json", affected="protocol")
    if cast(int, document["training_repetitions"]) != 3:
        _fail(
            "artifact_corrupt",
            "protocol",
            "protocol must retain exactly three training repetitions",
            "restore full protocol",
        )
    workloads = document["workloads"]
    if tuple(cast(list[object], workloads)) != _WORKLOADS:
        _fail(
            "artifact_corrupt",
            "protocol",
            "protocol workloads must be short, streaming, bursty in order",
            "restore frozen protocol",
        )
    study_id = _string(document["study_id"], name="protocol study ID")
    if document["candidate_id"] != study_id or document["destination_id"] != study_id:
        _fail(
            "artifact_foreign",
            "protocol.json",
            "protocol study, candidate, and destination IDs must be identical",
            "restore one exact frozen study identity",
        )
    if document["prerequisite_path"] != "examples/validation_study/prerequisites.json":
        _fail(
            "artifact_foreign",
            "protocol.json",
            "protocol prerequisite path must be the canonical checked prerequisite path",
            "restore the canonical prerequisite path",
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


def _training_runtime(content: bytes, *, name: str, workload: str, repeat: int) -> float:
    """Extract the one producer-recorded training runtime from canonical JSONL."""

    matches: list[dict[str, object]] = []
    for line_number, raw in enumerate(content.splitlines(keepends=True), start=1):
        record = _json(raw, name=f"{name}:{line_number}", canonical=False)
        if record.get("event") == "validation_study_training_completed":
            matches.append(record)
    if len(matches) != 1:
        _fail(
            "artifact_corrupt",
            name,
            "training run log must contain exactly one validation study runtime record",
            "restore canonical training runtime evidence",
        )
    record = _exact(
        matches[0],
        ("event", "repeat", "runtime_seconds", "stage", "workload"),
        name=f"{name} training runtime",
    )
    value = record["runtime_seconds"]
    if (
        record["event"] != "validation_study_training_completed"
        or record["stage"] != "study"
        or record["workload"] != workload
        or record["repeat"] != repeat
        or type(value) is not float
        or not math.isfinite(value)
        or value < 0.0
    ):
        _fail(
            "artifact_foreign",
            name,
            "training runtime record does not match the retained run identity",
            "restore matching training runtime evidence",
        )
    return value


def _run_log_records(content: bytes, *, name: str) -> tuple[dict[str, object], ...]:
    """Parse canonical JSONL into the retained producer records."""

    _canonical_jsonl(content, name=name)
    records: list[dict[str, object]] = []
    for line_number, raw in enumerate(content.splitlines(keepends=True), start=1):
        value = _json(raw, name=f"{name}:{line_number}", canonical=False)
        records.append(value)
    return tuple(records)


def _required_log_record(records: Sequence[Mapping[str, object]], *, event: str, name: str) -> Mapping[str, object]:
    matches = tuple(record for record in records if record.get("event") == event)
    if len(matches) != 1:
        _fail(
            "artifact_foreign",
            name,
            f"run log must contain exactly one {event} record",
            "restore complete matching run-log lineage",
        )
    return matches[0]


def _require_log_fields(record: Mapping[str, object], expected: Mapping[str, object], *, name: str, event: str) -> None:
    if any(record.get(field) != value for field, value in expected.items()):
        _fail(
            "artifact_foreign",
            name,
            f"{event} run-log record does not match retained identities and status",
            "restore complete matching run-log lineage",
        )


def _capture_log_environment(environment: Mapping[str, object]) -> dict[str, object]:
    return {
        "capture_content_id": environment["capture_image_id"],
        "capture_reference": environment["capture_image_reference"],
        "capture_tool_version": environment["capture_tool_version"],
        # The capture service records the frozen container platform, while the
        # top-level environment records the host-machine architecture.
        "host_architecture": "linux/amd64",
        "target_content_id": environment["target_image_id"],
        "target_reference": environment["target_image_reference"],
    }


def _require_successful_log_status(records: Sequence[Mapping[str, object]], *, name: str) -> None:
    for record in records:
        event = record.get("event")
        if (type(event) is str and (event.endswith("_reused") or event.endswith("_failed"))) or (
            "reused" in record and record["reused"] is not False
        ):
            _fail(
                "artifact_foreign",
                name,
                "run log cannot retain reused or failed stage status",
                "restore complete matching run-log lineage",
            )


def _require_terminal_log_events(
    records: Sequence[Mapping[str, object]], *, events: tuple[str, ...], name: str
) -> None:
    if tuple(record.get("event") for record in records[-len(events) :]) != events:
        _fail(
            "artifact_foreign",
            name,
            f"run log must end with the successful publication sequence {events!r}",
            "restore complete matching run-log lineage",
        )


def _require_ordered_log_events(records: Sequence[Mapping[str, object]], *, events: tuple[str, ...], name: str) -> None:
    positions: list[int] = []
    for event in events:
        position = next((index for index, record in enumerate(records) if record.get("event") == event), None)
        if position is None:
            _fail(
                "artifact_foreign",
                name,
                f"run log lacks required {event} event for successful stage order",
                "restore complete matching run-log lineage",
            )
        positions.append(position)
    if positions != sorted(positions):
        _fail(
            "artifact_foreign",
            name,
            "run log does not preserve successful stage order",
            "restore complete matching run-log lineage",
        )


def _require_capture_log_lineage(
    records: Sequence[Mapping[str, object]],
    *,
    name: str,
    environment: Mapping[str, object],
    capture: bytes,
    reference: bytes,
    experiment: bytes,
    packet_count: int | None,
) -> None:
    _require_successful_log_status(records, name=name)
    environment_fields = _capture_log_environment(environment)
    _require_log_fields(
        _required_log_record(records, event="capture_environment_identity", name=name),
        {"event": "capture_environment_identity", "stage": "preflight", **environment_fields},
        name=name,
        event="capture_environment_identity",
    )
    capture_record = _required_log_record(records, event="capture_published", name=name)
    expected: dict[str, object] = {
        "event": "capture_published",
        "stage": "capture",
        "capture_identity": _identity(capture),
        "reference_identity": _identity(reference),
        "experiment_identity": _identity(experiment),
        "reused": False,
    }
    if packet_count is not None:
        expected["packet_count"] = packet_count
    _require_log_fields(capture_record, expected, name=name, event="capture_published")
    nested = capture_record.get("capture_environment_identity")
    if not isinstance(nested, Mapping):
        _fail(
            "artifact_foreign",
            name,
            "capture_published run-log record lacks its capture environment identity",
            "restore complete matching run-log lineage",
        )
    _require_log_fields(cast(Mapping[str, object], nested), environment_fields, name=name, event="capture_published")


def _require_training_log_lineage(
    records: Sequence[Mapping[str, object]],
    *,
    name: str,
    environment: Mapping[str, object],
    contents: Mapping[str, bytes],
    reference_count: int,
    generated_count: int,
    checkpoint: CheckpointState,
    best: BestModel,
    comparison: ComparisonResult,
    window: float,
) -> None:
    _require_capture_log_lineage(
        records,
        name=name,
        environment=environment,
        capture=contents["capture.json"],
        reference=contents["reference.pcapng"],
        experiment=contents["experiment.toml"],
        packet_count=reference_count,
    )
    _require_log_fields(
        _required_log_record(records, event="best_model_published", name=name),
        {
            "event": "best_model_published",
            "stage": "fit",
            "family": best.family,
            "observation_window_seconds": window,
            "reference_sha256": _identity(contents["reference.pcapng"])["sha256"],
        },
        name=name,
        event="best_model_published",
    )
    _require_log_fields(
        _required_log_record(records, event="generated_pcapng_published", name=name),
        {
            "event": "generated_pcapng_published",
            "stage": "generate",
            "seed": best.final_seed,
            "observation_window_seconds": window,
            "packet_count": generated_count,
        },
        name=name,
        event="generated_pcapng_published",
    )
    _require_log_fields(
        _required_log_record(records, event="comparison_succeeded", name=name),
        {
            "event": "comparison_succeeded",
            "stage": "compare",
            "observation_window_seconds": window,
            "aggregate_score": comparison.aggregate_score,
            "reused": False,
        },
        name=name,
        event="comparison_succeeded",
    )
    _require_log_fields(
        _required_log_record(records, event="run_completed", name=name),
        {
            "event": "run_completed",
            "stage": "run",
            "family": best.family,
            "fitness": checkpoint.best_fitness,
            "reference_packet_count": reference_count,
            "generated_packet_count": generated_count,
            "aggregate_score": comparison.aggregate_score,
        },
        name=name,
        event="run_completed",
    )
    _require_terminal_log_events(
        records,
        events=("run_completed", "validation_study_training_completed"),
        name=name,
    )
    _require_ordered_log_events(
        records,
        events=(
            "capture_environment_identity",
            "capture_published",
            "best_model_published",
            "generated_pcapng_published",
            "comparison_succeeded",
            "run_completed",
            "validation_study_training_completed",
        ),
        name=name,
    )


def _require_held_out_log_lineage(
    records: Sequence[Mapping[str, object]],
    *,
    name: str,
    workload: str,
    environment: Mapping[str, object],
    capture: bytes,
    reference: bytes,
    experiment: bytes,
) -> None:
    _require_capture_log_lineage(
        records,
        name=name,
        environment=environment,
        capture=capture,
        reference=reference,
        experiment=experiment,
        packet_count=None,
    )
    _require_log_fields(
        _required_log_record(records, event="held_out_evaluated", name=name),
        {"event": "held_out_evaluated", "stage": "compare", "workload": workload},
        name=name,
        event="held_out_evaluated",
    )
    _require_terminal_log_events(records, events=("held_out_evaluated",), name=name)
    _require_ordered_log_events(
        records,
        events=("capture_environment_identity", "capture_published", "held_out_evaluated"),
        name=name,
    )


def _config_semantics(config: ExperimentConfig) -> dict[str, object]:
    document = cast(dict[str, object], config.model_dump(mode="json", exclude_none=True))
    run = cast(dict[str, object], document["run"])
    run["directory"] = "<operational>"
    target = cast(dict[str, object], document["target"])
    mounts = cast(list[dict[str, object]], target["mounts"])
    for mount in mounts:
        mount["source"] = "<operational>"
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


def _fixture_profile(
    repository: Path,
    *,
    source_commit: str,
    workload: str,
    url: str,
    environment: Mapping[str, object],
) -> ExperimentConfig:
    """Derive the deterministic fixture profile from its recorded source tree."""

    relative = "examples/data/fit/experiment.toml"
    path = repository / relative
    content = _read_regular(path, affected=relative)
    committed = _git_bytes(repository, ("show", f"{source_commit}:{relative}"), name="recorded fixture profile")
    if content != committed:
        _fail(
            "artifact_foreign",
            relative,
            "fixture profile bytes do not match the recorded source revision",
            "audit the exact recorded fixture source",
        )
    try:
        pair = load_configuration_pair(path)
    except TrafficlabError as error:
        _fail(
            "artifact_corrupt",
            relative,
            f"recorded fixture profile is invalid: {error}",
            "restore the checked fixture profile",
        )
    if render_effective_config(pair.portable) != content:
        _fail(
            "artifact_foreign",
            relative,
            "recorded fixture profile is not canonical",
            "restore the canonical fixture profile",
        )
    spec = _frozen_workload_profiles(url)[workload]
    target = pair.portable.target.model_copy(update={"argv": spec.argv, "image": environment["target_image_reference"]})
    capture = pair.portable.capture.model_copy(update={"image": environment["capture_image_reference"]})
    return pair.portable.model_copy(update={"target": target, "capture": capture})


def _validation_profile(*, workload: str, url: str, environment: Mapping[str, object]) -> ExperimentConfig:
    """Independently reconstruct one non-operational frozen Validation Study profile."""

    spec = _frozen_workload_profiles(url)[workload]
    return ExperimentConfig.model_validate(
        {
            "run": {
                "directory": Path("."),
                "minimum_free_bytes": 1_048_576,
                "master_seed": 73,
                "final_seed": 97,
            },
            "target": {
                "image": environment["target_image_reference"],
                "argv": spec.argv,
                "environment": {},
                "working_directory": "/",
                "mounts": ({"source": Path("."), "target": "/trafficlab-study", "read_only": False},),
            },
            "capture": {
                "image": environment["capture_image_reference"],
                "network_probe_url": url,
                "readiness_timeout_seconds": 10.0,
                "workload_timeout_seconds": spec.workload_timeout_seconds,
                "flush_timeout_seconds": 5.0,
                "total_timeout_seconds": spec.total_timeout_seconds,
            },
            "generation": {
                "trial": {"max_packets": 25_000, "max_output_bytes": 40_000_000, "max_wall_seconds": 5.0},
                "final": {"max_packets": 50_000, "max_output_bytes": 80_000_000, "max_wall_seconds": 10.0},
            },
            "genetic": {
                "population_size": 6,
                "generation_count": 2,
                "tournament_size": 2,
                "elite_count": 1,
                "trial_seeds": (17, 29),
                "duplicate_mutation_attempts": 3,
                "early_stopping_generations": 0,
                "early_stopping_tolerance": 0.0,
                "resume": True,
            },
            "models": {
                "enabled": _MODEL_FAMILIES,
                "poisson_empirical": {
                    "crossover_probability": 0.9,
                    "mutation_probability": 1.0,
                    "mutation_scale": 0.1,
                    "c_lambda": {"lower": 0.25, "upper": 4.0},
                },
                "markov_renewal": {
                    "crossover_probability": 0.9,
                    "mutation_probability": 0.2,
                    "mutation_scale": 0.1,
                    "q1": {"lower": 0.1, "upper": 0.4},
                    "q2": {"lower": 0.6, "upper": 0.9},
                    "alpha": {"lower": 0.0, "upper": 2.0},
                    "r": {"lower": 1, "upper": 8},
                    "c_t": {"lower": 0.25, "upper": 4.0},
                },
                "mmpp": {
                    "crossover_probability": 0.9,
                    "mutation_probability": 0.25,
                    "mutation_scale": 0.1,
                    "q01": {"lower": 0.01, "upper": 10.0},
                    "q10": {"lower": 0.01, "upper": 10.0},
                    "lambda0": {"lower": 10.0, "upper": 100.0},
                    "lambda1": {"lower": 0.1, "upper": 1000.0},
                },
            },
            "similarity": {
                "iat_diagnostic_quantile": 0.95,
                "acf_lags": (1,),
                "acf_lag_weights": (1.0,),
                "acf_iat_weight": 0.5,
                "acf_size_weight": 0.5,
                "multiscale_widths_seconds": spec.multiscale_widths_seconds,
                "multiscale_scale_weights": (0.5, 0.5),
                "multiscale_packet_weight": 0.5,
                "multiscale_byte_weight": 0.5,
                "max_direction_bin_cells": 100_000,
                "method_weights": {
                    "frame_size_ks": 0.25,
                    "iat_ks": 0.25,
                    "autocorrelation": 0.25,
                    "multiscale_rate": 0.25,
                },
            },
        }
    )


def _frozen_profiles(
    repository: Path,
    *,
    environment: Mapping[str, object],
    protocol: Mapping[str, object],
    url: str,
) -> dict[str, ExperimentConfig]:
    """Reconstruct the source-owned profile for every retained workload."""

    study_id = _string(protocol["study_id"], name="protocol study ID")
    source_commit = _string(environment["source_commit"], name="environment source_commit")
    if study_id == _FIXTURE_STUDY_ID:
        if url != _FIXTURE_URL:
            _fail(
                "artifact_foreign",
                "protocol.json",
                "fixture-study must use its exact frozen URL",
                "restore the deterministic fixture protocol",
            )
        profiles = {
            workload: _fixture_profile(
                repository,
                source_commit=source_commit,
                workload=workload,
                url=url,
                environment=environment,
            )
            for workload in _WORKLOADS
        }
    else:
        profiles = {
            workload: _validation_profile(workload=workload, url=url, environment=environment)
            for workload in _WORKLOADS
        }
    for workload, profile in profiles.items():
        if tuple(profile.models.enabled) != _MODEL_FAMILIES:
            _fail(
                "scientific_semantics_incompatible",
                f"frozen-profile/{workload}",
                "frozen profile must enable exactly poisson_empirical, markov_renewal, and mmpp",
                "restore the complete frozen model-family profile",
            )
    return profiles


def _require_frozen_profile(config: ExperimentConfig, frozen: ExperimentConfig, *, affected: str) -> None:
    """Require one retained configuration to equal its independently reconstructed profile."""

    if tuple(config.models.enabled) != _MODEL_FAMILIES or _config_semantics(config) != _config_semantics(frozen):
        _fail(
            "artifact_foreign",
            affected,
            "configuration does not match the frozen source-owned study profile",
            "restore the exact frozen workload configuration",
        )


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


def _require_config_workload_argv(config: ExperimentConfig, *, workload: str, url: str, affected: str) -> None:
    expected = _frozen_workload_profiles(url)[workload].argv
    if config.target.argv != expected:
        _fail(
            "artifact_foreign",
            affected,
            "configuration target argv does not match the frozen workload profile",
            "restore the exact frozen curl workload configuration",
        )


def _training(
    bundle: Path,
    value: object,
    *,
    protocol: dict[str, object],
    environment: Mapping[str, object],
    frozen_profiles: Mapping[str, ExperimentConfig],
    url: str,
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
    _require_config_workload_argv(
        config,
        workload=workload,
        url=url,
        affected=directory_relative,
    )
    _require_frozen_profile(config, frozen_profiles[workload], affected=directory_relative)
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
    if render_effective_config(pair.portable) != contents["experiment.toml"] or _config_semantics(
        pair.realized
    ) != _config_semantics(config):
        _fail(
            "artifact_foreign",
            f"{directory_relative}/experiment.toml",
            "run configuration does not match retained configuration pair",
            "restore matching run configuration",
        )
    _canonical_jsonl(contents["run.log"], name=f"{directory_relative}/run.log")
    run_log_records = _run_log_records(contents["run.log"], name=f"{directory_relative}/run.log")
    runtime_seconds = _training_runtime(
        contents["run.log"], name=f"{directory_relative}/run.log", workload=workload, repeat=repeat
    )
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
    _require_training_log_lineage(
        run_log_records,
        name=f"{directory_relative}/run.log",
        environment=environment,
        contents=contents,
        reference_count=len(reference),
        generated_count=len(generated),
        checkpoint=checkpoint,
        best=best,
        comparison=persisted_comparison,
        window=window,
    )
    return _Training(
        workload,
        repeat,
        directory,
        contents,
        config,
        reference,
        window,
        runtime_seconds,
        checkpoint,
        best,
        persisted_comparison,
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


def _rebuild_held_out(
    training: _Training,
    *,
    config: ExperimentConfig,
    capture_content: bytes,
    capture_source: Path,
    reference_content: bytes,
    reference_source: Path,
) -> HeldOutEvaluation:
    """Independently reproduce a fixed training model at the held-out horizon."""

    metadata = parse_capture_metadata(capture_content, source=capture_source)
    reference, W = normalize_reference(parse_pcapng_bytes(reference_content, metadata, source=reference_source))
    model = training.best_model
    raw_generated = (
        get_family(model.family)
        .generate(
            runtime_fitted_model(model),
            model.final_seed,
            W,
            model.final_limits,
        )
        .require_complete()
    )
    generated = quantize_generated_events(raw_generated, W)
    generated_pcapng = encode_pcapng(generated, metadata)
    settings_identity = similarity_settings_identity(config.similarity)
    comparison = compare_traces(reference, align_generated(generated, W), W, config.similarity).with_input_identities(
        {
            "capture_json": identify_bytes(capture_content),
            "generated_pcapng": identify_bytes(generated_pcapng),
            "reference_pcapng": identify_bytes(reference_content),
            "similarity_settings": settings_identity,
        }
    )
    comparison_json = render_comparison_result(comparison)
    return HeldOutEvaluation(
        training_model=model,
        training_model_identity=identify_bytes(training.contents["best_model.json"]),
        capture_identity=identify_bytes(capture_content),
        reference_identity=identify_bytes(reference_content),
        generated_identity=identify_bytes(generated_pcapng),
        similarity_settings_identity=settings_identity,
        generated_pcapng=generated_pcapng,
        comparison=comparison,
        comparison_json=comparison_json,
        seed=model.final_seed,
        observation_window_seconds=W,
    )


def _held_out(
    bundle: Path,
    value: object,
    training: _Training,
    *,
    final_seed: int,
    training_references: set[str],
    environment: Mapping[str, object],
    frozen_profiles: Mapping[str, ExperimentConfig],
) -> tuple[str, set[str], HeldOutEvaluation]:
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
    _require_frozen_profile(config, frozen_profiles[workload], affected=directory_relative)
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
    run_log_records = _run_log_records(contents["run.log"], name=f"{directory_relative}/run.log")
    reference_identity = identify_bytes(contents["reference.pcapng"])
    if reference_identity.sha256 in training_references:
        _fail(
            "artifact_foreign",
            f"{directory_relative}/reference.pcapng",
            "held-out reference is not independent from training captures",
            "capture a new held-out reference",
        )
    try:
        evaluation = _rebuild_held_out(
            training,
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
            "observation_window_seconds",
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
        "observation_window_seconds": evaluation.observation_window_seconds,
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
    _require_held_out_log_lineage(
        run_log_records,
        name=f"{directory_relative}/run.log",
        workload=workload,
        environment=environment,
        capture=contents["capture.json"],
        reference=contents["reference.pcapng"],
        experiment=_read_regular(bundle / realized, affected=realized),
    )
    return directory_relative, config_paths | {f"{directory_relative}/{name}" for name in names}, evaluation


def _mean(scores: Sequence[dict[str, object]]) -> dict[str, object]:
    if not scores:
        _fail("artifact_corrupt", "report_inputs.json", "report arithmetic requires scores", "restore report inputs")
    methods = [cast(dict[str, object], score["methods"]) for score in scores]
    return {
        "aggregate": fmean(cast(float, score["aggregate"]) for score in scores),
        "methods": {name: fmean(cast(float, item[name]) for item in methods) for name in PUBLISHED_METHOD_ORDER},
    }


def _sample_summary(values: Sequence[float], *, name: str) -> dict[str, object]:
    if len(values) < 2 or any(not math.isfinite(value) or value < 0.0 for value in values):
        _fail("artifact_corrupt", "report_inputs.json", f"{name} requires finite observations", "restore report inputs")
    return {"mean": fmean(values), "sample_variance": variance(values)}


def _winner_family(training: _Training) -> str:
    candidate = rank_candidates(training.checkpoint.population, family_priority=training.checkpoint.family_priority)[0]
    return candidate.family


def _controlled_weight_analysis(training: Sequence[_Training]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    alternate_weights = {
        "autocorrelation": 0.2,
        "frame_size_ks": 0.4,
        "iat_ks": 0.2,
        "multiscale_rate": 0.2,
    }
    for workload in _WORKLOADS:
        group = [item for item in training if item.workload == workload]
        selected = min(group, key=lambda item: (-item.checkpoint.best_fitness, item.repeat))
        baseline_weights = cast(dict[str, object], selected.config.similarity.method_weights.model_dump(mode="json"))
        if baseline_weights != {method: 0.25 for method in PUBLISHED_METHOD_ORDER}:
            _fail(
                "scientific_semantics_incompatible",
                "report_inputs.json",
                "controlled weight analysis requires the frozen equal-weight baseline",
                "restore frozen similarity controls",
            )
        score = _score(selected.comparison)
        components = cast(dict[str, object], score["methods"])
        rendered = _json(render_comparison_result(selected.comparison), name="controlled comparison")
        methods = cast(dict[str, object], rendered["methods"])
        rows.append(
            {
                "alternative_aggregate": math.fsum(
                    alternate_weights[method] * cast(float, components[method]) for method in PUBLISHED_METHOD_ORDER
                ),
                "alternative_weights": alternate_weights,
                "baseline_aggregate": score["aggregate"],
                "baseline_weights": baseline_weights,
                "components": components,
                "diagnostics": {
                    method: cast(dict[str, object], methods[method])["diagnostics"] for method in PUBLISHED_METHOD_ORDER
                },
                "executed_methods": list(PUBLISHED_METHOD_ORDER),
                "training_directory": f"training/{selected.workload}/r{selected.repeat}",
                "workload": workload,
            }
        )
    return rows


def _invalid_chromosome_diagnostics(training: Sequence[_Training]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in training:
        invalid: list[object] = []
        for candidate in item.checkpoint.population:
            if candidate.status != "invalid":
                continue
            failure = candidate.invalid
            if failure is None:
                _fail(
                    "artifact_corrupt",
                    "report_inputs.json",
                    "invalid candidate must retain a classified failure",
                    "restore invalid-chromosome diagnostics",
                )
            invalid.append(
                {
                    "affected_evidence": failure.affected_evidence,
                    "authority": failure.authority,
                    "corrective_action": failure.corrective_action,
                    "detail": failure.detail,
                    "evidence_state": failure.evidence_state,
                    "family": candidate.family,
                    "genes": list(candidate.genes) if candidate.genes is not None else None,
                    "identifier": {
                        "birth_generation": candidate.identifier.birth_generation,
                        "birth_index": candidate.identifier.birth_index,
                    },
                    "kind": failure.kind,
                    "seed": failure.seed,
                    "stage": failure.stage,
                }
            )
        rows.append(
            {
                "invalid_candidates": invalid,
                "trial_limits": item.config.generation.trial.model_dump(mode="json"),
                "training_directory": f"training/{item.workload}/r{item.repeat}",
                "workload": item.workload,
                "repeat": item.repeat,
            }
        )
    return rows


def _report_inputs(
    training: Sequence[_Training],
    held: Mapping[str, HeldOutEvaluation],
) -> dict[str, object]:
    fresh_rows: list[dict[str, object]] = []
    training_rows: list[dict[str, object]] = []
    variation_rows: list[dict[str, object]] = []
    held_rows: list[dict[str, object]] = []
    for workload in _WORKLOADS:
        group = tuple(item for item in training if item.workload == workload)
        fresh_rows.append({"score": _mean([_score(item.comparison) for item in group]), "workload": workload})
        training_rows.append(
            {
                "runtime_seconds": _sample_summary(
                    [item.runtime_seconds for item in group], name="training runtime variance"
                ),
                "selection_fitness": _sample_summary(
                    [item.checkpoint.best_fitness for item in group], name="training selection variance"
                ),
                "winner_family_count_variance": variance(
                    [
                        sum(_winner_family(item) == family for item in group)
                        for family in ("markov_renewal", "mmpp", "poisson_empirical")
                    ]
                ),
                "winner_family_counts": {
                    family: sum(_winner_family(item) == family for item in group)
                    for family in ("markov_renewal", "mmpp", "poisson_empirical")
                },
                "workload": workload,
            }
        )
        pairs: list[dict[str, object]] = []
        for left, right in combinations(group, 2):
            if similarity_settings_identity(left.config.similarity) != similarity_settings_identity(
                right.config.similarity
            ):
                _fail(
                    "scientific_semantics_incompatible",
                    "report_inputs.json",
                    "natural variation requires common similarity settings",
                    "restore common protocol controls before comparing natural variation",
                )
            left_reference, forward_window = normalize_reference(left.reference)
            right_reference, reverse_window = normalize_reference(right.reference)
            forward = _score(
                compare_traces(
                    left_reference,
                    align_generated(right.reference, forward_window),
                    forward_window,
                    left.config.similarity,
                )
            )
            reverse = _score(
                compare_traces(
                    right_reference,
                    align_generated(left.reference, reverse_window),
                    reverse_window,
                    right.config.similarity,
                )
            )
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
        held_rows.append(
            {
                "observation_window_seconds": held[workload].observation_window_seconds,
                "score": _score(held[workload].comparison),
                "workload": workload,
            }
        )
    return {
        "controlled_weight_analysis": _controlled_weight_analysis(training),
        "formula": "arithmetic_mean",
        "fresh_simulation": fresh_rows,
        "held_out": held_rows,
        "invalid_chromosome_diagnostics": _invalid_chromosome_diagnostics(training),
        "natural_variation": variation_rows,
        "runtime_winner_variance": training_rows,
        "training": training_rows,
    }


def _lifecycle_project_name(content: bytes, *, name: str) -> str:
    """Read the one capture project identity already bound by the retained run log."""

    records = _run_log_records(content, name=name)
    creation = _required_log_record(records, event="capture_project_created", name=name)
    publication = _required_log_record(records, event="capture_published", name=name)
    created_project_name = _string(creation.get("project_name"), name=f"created capture project name for {name}")
    project_name = _string(publication.get("project_name"), name=f"capture project name for {name}")
    if (
        creation.get("stage") != "capture"
        or publication.get("stage") != "capture"
        or not created_project_name.startswith("trafficlab-capture-")
        or created_project_name != project_name
        or records.index(creation) >= records.index(publication)
    ):
        _fail(
            "artifact_foreign",
            name,
            "capture project creation and publication do not retain one owned project identity",
            "restore matching capture lineage",
        )
    return project_name


def _lifecycle_rows(value: object, *, expected: Sequence[dict[str, object]], name: str) -> None:
    """Require a closed ordered lifecycle row list before trusting cleanup assertions."""

    if type(value) is not list:
        _fail("artifact_corrupt", "lifecycle.json", f"{name} lifecycle rows must be a list", "restore lifecycle proof")
    rows = cast(list[dict[str, object]], value)
    if rows != expected:
        _fail(
            "artifact_foreign",
            "lifecycle.json",
            f"{name} lifecycle rows do not bind the completed capture runs",
            "restore matching collection cleanup evidence",
        )


def _lifecycle(
    bundle: Path,
    value: object,
    *,
    protocol: Mapping[str, object],
    environment: Mapping[str, object],
    training: Sequence[_Training],
) -> None:
    """Independently validate candidate-owned capture and phase-image cleanup proof."""

    if (
        type(value) is not dict
        or type(cast(dict[str, object], value).get("schema_version")) is not int
        or cast(dict[str, object], value).get("schema_version") != 1
    ):
        _fail(
            "artifact_corrupt",
            "lifecycle.json",
            "collection lifecycle must use schema version 1",
            "restore canonical collection cleanup evidence",
        )
    document = _validated_study_root(cast(dict[str, object], value), ValidationStudyLifecycle, name="lifecycle.json")
    study_id = _string(document["study_id"], name="lifecycle study ID")
    if study_id != protocol["study_id"] or study_id != bundle.name:
        _fail(
            "artifact_foreign",
            "lifecycle.json",
            "collection lifecycle study ID does not match frozen protocol identity",
            "restore matching collection cleanup evidence",
        )
    phase = cast(dict[str, object], document["phase_capture_image"])
    expected_phase = {
        "capture_image_id": environment["capture_image_id"],
        "cleanup_verified": True,
        "post_cleanup_inspect_exit_status": 1,
        "tag": f"trafficlab-validation-{study_id}:collection-capture",
    }
    if phase != expected_phase:
        _fail(
            "artifact_foreign",
            "lifecycle.json",
            "collection phase capture image cleanup does not match frozen identity",
            "restore matching collection cleanup evidence",
        )
    training_by_key = {(item.workload, item.repeat): item for item in training}
    expected_training: list[dict[str, object]] = []
    for _order, run_id, workload, repeat in PRIMARY_ORDER:
        item = training_by_key[(workload, repeat)]
        relative = f"training/{workload}/r{repeat}"
        expected_training.append(
            {
                "cleanup_verified": True,
                "directory": relative,
                "project_name": _lifecycle_project_name(item.contents["run.log"], name=f"{relative}/run.log"),
                "run_id": run_id,
            }
        )
    _lifecycle_rows(document["training"], expected=expected_training, name="training")
    expected_held_out: list[dict[str, object]] = []
    for workload in _WORKLOADS:
        relative = f"held_out/{workload}"
        content = _read_regular(bundle / relative / "run.log", affected=f"{relative}/run.log")
        expected_held_out.append(
            {
                "cleanup_verified": True,
                "directory": relative,
                "project_name": _lifecycle_project_name(content, name=f"{relative}/run.log"),
                "run_id": f"held-out-{workload}",
            }
        )
    _lifecycle_rows(document["held_out"], expected=expected_held_out, name="held-out")
    project_names = [cast(str, row["project_name"]) for row in (*expected_training, *expected_held_out)]
    if len(project_names) != 12 or len(set(project_names)) != len(project_names):
        _fail(
            "artifact_foreign",
            "lifecycle.json",
            "collection lifecycle must bind twelve distinct capture projects",
            "restore the exact retained capture cleanup proof",
        )


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
        _relative(index["lifecycle"], name="index lifecycle"),
        _relative(index["report_inputs"], name="index report inputs"),
        _relative(index["report"], name="index report"),
        *prerequisite_paths,
        *fresh_paths,
        *held_paths,
    }
    for binding in _TRANSFER_BINDINGS:
        paths.add(f"headers/{binding.scope}/{binding.run_id}/{binding.filename}")
        paths.add(f"observations/{binding.scope}/{binding.run_id}/{binding.filename}.json")
    for item in training:
        relative = f"training/{item.workload}/r{item.repeat}"
        paths.update(f"{relative}/{name}" for name in ARTIFACT_NAMES)
        paths.add(f"configs/training-{item.workload}-r{item.repeat}.portable.toml")
        paths.add(f"configs/training-{item.workload}-r{item.repeat}.realized.toml")
    return paths


def _headers_and_observations(bundle: Path, *, prerequisites: Mapping[str, object]) -> set[str]:
    capability_value = prerequisites.get("capability")
    if not isinstance(capability_value, Mapping):
        _fail(
            "artifact_corrupt",
            "prerequisites.json",
            "prerequisites must retain a capability record",
            "restore canonical prerequisite evidence",
        )
    capability = cast(Mapping[str, object], capability_value)
    initial_url = _string(prerequisites.get("url"), name="prerequisite URL")
    object_size = _integer(capability.get("object_size_bytes"), name="prerequisite object size", minimum=1)
    paths: set[str] = set()
    for binding in _TRANSFER_BINDINGS:
        header = f"headers/{binding.scope}/{binding.run_id}/{binding.filename}"
        observation = f"observations/{binding.scope}/{binding.run_id}/{binding.filename}.json"
        content = _read_regular(bundle / header, affected=header)
        try:
            status, content_length, content_range = _parse_transfer_header(
                content,
                initial_url=initial_url,
                start=binding.requested_start,
                end=binding.requested_end,
                object_size_bytes=object_size,
            )
        except ValueError as error:
            _fail(
                "artifact_corrupt",
                header,
                f"protocol header is not the retained transfer response: {error}",
                "restore protocol-used headers",
            )
        if binding.scope == "prerequisites" and (
            capability.get("canary_sha256") != hashlib.sha256(content).hexdigest()
            or capability.get("content_length") != content_length
            or capability.get("content_range") != content_range
            or capability.get("status") != status
        ):
            _fail(
                "artifact_foreign",
                header,
                "capability header does not match the retained prerequisite facts",
                "restore the exact retained capability header",
            )
        document = _exact(
            _json(_read_regular(bundle / observation, affected=observation), name=observation),
            (
                "content_length",
                "content_range",
                "header_identity",
                "requested_end",
                "requested_start",
                "run_id",
                "scope",
                "status",
                "transfer_index",
                "workload",
            ),
            name=observation,
        )
        expected = {
            "content_length": content_length,
            "content_range": content_range,
            "header_identity": _identity(content),
            "requested_end": binding.requested_end,
            "requested_start": binding.requested_start,
            "run_id": binding.run_id,
            "scope": binding.scope,
            "status": status,
            "transfer_index": binding.transfer_index,
            "workload": binding.workload,
        }
        if document != expected:
            _fail(
                "artifact_foreign",
                observation,
                "external observation does not match retained protocol header",
                "restore matching observation",
            )
        paths.update((header, observation))
    return paths


def _audit(
    bundle: Path,
    repository: Path,
    entries: tuple[_Entry, ...],
    *,
    source_candidate: Path | None = None,
) -> AuditResult:
    _require_permitted_relocated_worktree(repository, candidate=bundle, source_candidate=source_candidate)
    index = _json(_read_regular(bundle / _INDEX, affected=_INDEX), name=_INDEX)
    index_version = index.get("schema_version")
    if type(index_version) is not int:
        _fail(
            "artifact_corrupt",
            _INDEX,
            "evidence index schema version must be an integer",
            "restore canonical evidence index",
        )
    if index_version != _INDEX_SCHEMA:
        _fail(
            "scientific_semantics_incompatible",
            _INDEX,
            "evidence index must use schema version 3",
            "rebuild retained evidence under schema 3",
        )
    if "ownership" not in index or "lineage" not in index:
        _validated_study_root(index, ValidationStudyLineage, name=_INDEX)
    _metadata(index, entries)
    _validated_study_root(
        _json(_read_regular(bundle / _MANIFEST, affected=_MANIFEST), name=_MANIFEST),
        ValidationStudyManifest,
        name=_MANIFEST,
    )
    index = _validated_study_root(index, ValidationStudyLineage, name=_INDEX)
    environment_path = _relative(index["environment"], name="index environment")
    protocol_path = _relative(index["protocol"], name="index protocol")
    prerequisites_path = _relative(index["prerequisites"], name="index prerequisites")
    if (
        environment_path,
        protocol_path,
        prerequisites_path,
        _relative(index["lifecycle"], name="index lifecycle"),
        _relative(index["report_inputs"], name="index report inputs"),
        _relative(index["report"], name="index report"),
    ) != (
        "environment.json",
        "protocol.json",
        "prerequisites.json",
        "lifecycle.json",
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
    prerequisites, prerequisite_paths = _prerequisites(bundle, prerequisites_path, environment=environment)
    if protocol["study_id"] != bundle.name:
        _fail(
            "artifact_foreign",
            "protocol.json",
            "protocol destination ID must equal the candidate directory name",
            "restore the candidate under its frozen study ID",
        )
    if prerequisites["study_id"] != protocol["study_id"]:
        _fail(
            "artifact_foreign",
            "prerequisites.json",
            "retained prerequisites must bind the frozen study identity",
            "restore matching prerequisite evidence",
        )
    _headers_and_observations(bundle, prerequisites=prerequisites)
    frozen_profiles = _frozen_profiles(
        repository,
        environment=environment,
        protocol=protocol,
        url=cast(str, prerequisites["url"]),
    )
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
    training = tuple(
        _training(
            bundle,
            value,
            protocol=protocol,
            environment=environment,
            frozen_profiles=frozen_profiles,
            url=cast(str, prerequisites["url"]),
        )
        for value in training_items
    )
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
        _directory_relative, paths, evaluation = _held_out(
            bundle,
            value,
            selected[workload],
            final_seed=cast(int, protocol["final_seed"]),
            training_references=training_references,
            environment=environment,
            frozen_profiles=frozen_profiles,
        )
        held_evaluations[workload] = evaluation
        held_paths.update(paths)
    lifecycle_path = _relative(index["lifecycle"], name="index lifecycle")
    _lifecycle(
        bundle,
        _json(_read_regular(bundle / lifecycle_path, affected=lifecycle_path), name=lifecycle_path),
        protocol=protocol,
        environment=environment,
        training=ordered_training,
    )
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
    report_inputs = _validated_study_root(report_inputs, ValidationStudyReportInput, name=inputs_path)
    report_path = _relative(index["report"], name="index report")
    report = _json(_read_regular(bundle / report_path, affected=report_path), name=report_path)
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
    _validated_study_root(report, ValidationStudyReport, name=report_path)
    return AuditResult(
        bundle,
        ordered_training[0].directory,
        hashlib.sha256(_read_regular(bundle / _MANIFEST, affected=_MANIFEST)).hexdigest(),
        len(entries),
    )


def _audit_bundle(bundle: Path, *, repository: Path, source_candidate: Path | None = None) -> AuditResult:
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
        return _audit(root, repository_root, entries, source_candidate=source_candidate)
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


def audit_bundle(bundle: Path, *, repository: Path) -> AuditResult:
    """Strictly audit one complete candidate before exclusive accepted publication."""

    return _audit_bundle(bundle, repository=repository)


def _audit_staged_bundle(  # pyright: ignore[reportUnusedFunction]
    bundle: Path,
    *,
    repository: Path,
    source_candidate: Path,
) -> AuditResult:
    """Audit a copied candidate while excluding its known source from worktree-state evidence."""

    return _audit_bundle(bundle, repository=repository, source_candidate=source_candidate)


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
