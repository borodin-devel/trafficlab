"""Common owner for Validation Study tooling."""

from __future__ import annotations

import json
import math
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from statistics import fmean
from typing import NoReturn, cast

from pydantic import BaseModel, ValidationError

from scripts.validation_study.common import PRIMARY_ORDER, PUBLISHED_METHOD_ORDER
from trafficlab import USER_AGENT
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import FailureKind
from trafficlab.common.json import render_json_document
from trafficlab.common.trace import TrafficTrace
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.checkpoint import CheckpointState
from trafficlab.generation.models.fitted_model import (
    BestModel,
)
from trafficlab.study_evidence.protocol import (
    ValidationStudyManifest,
)

MANIFEST = "manifest.json"

INDEX = "index.json"

SCHEMA = 2

INDEX_SCHEMA = 4

WORKLOADS = ("short", "streaming", "bursty")

REPEATS = (1, 2, 3)

HEX40 = re.compile("[0-9a-f]{40}", flags=re.ASCII)

HEX64 = re.compile("[0-9a-f]{64}", flags=re.ASCII)

_TEMP_SUFFIXES = (".tmp", ".partial", ".swp")

_TRANSFER_PROFILE_URL = "https://validation-study.example/object"

MODEL_FAMILIES = ("poisson_empirical", "markov_renewal", "mmpp")

FIXTURE_STUDY_ID = "fixture-study"

FIXTURE_URL = "https://downloads.example.test/object.bin"


@dataclass(frozen=True, slots=True)
class AuditResult:
    bundle: Path
    run_directory: Path
    manifest_sha256: str
    file_count: int


@dataclass(frozen=True, slots=True)
class Entry:
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


def frozen_workload_profiles(url: str) -> dict[str, _FrozenWorkload]:
    """Reconstruct the validation profile without reusing the collection oracle."""
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
        transfers=((0, 1048575, "short.headers"),),
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
        transfers=((0, 4194303, "streaming.headers"),),
        workload_timeout_seconds=50.0,
        total_timeout_seconds=120.0,
        multiscale_widths_seconds=(0.25, 1.0),
    )
    bursty_transfers = tuple(
        (
            (start, start + 32767, f"bursty-{index}.headers")
            for index, start in enumerate((0, 524288, 1048576, 1572864, 2097152, 2621440, 3145728, 3670016))
        )
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
class Training:
    workload: str
    repeat: int
    directory: Path
    contents: Mapping[str, bytes]
    config: ExperimentConfig
    reference: TrafficTrace
    window: float
    runtime_seconds: float
    checkpoint: CheckpointState
    best_model: BestModel
    comparison: ComparisonResult


@dataclass(frozen=True, slots=True)
class Issue(Exception):
    kind: FailureKind
    affected: str
    detail: str
    action: str


_TRANSFER_RUNS = (
    *(("training", run_id, workload) for _order, run_id, workload, _repeat_number in PRIMARY_ORDER),
    *(("held_out", f"held-out-{workload}", workload) for workload in WORKLOADS),
)

_TRANSFER_SPECS = {name: profile.transfers for name, profile in frozen_workload_profiles(_TRANSFER_PROFILE_URL).items()}

TRANSFER_BINDINGS = (
    _Transfer("prerequisites", "00-prerequisites", "prerequisites", 0, 0, 0, "capability.headers"),
) + tuple(
    (
        _Transfer(scope, run_id, workload, index, start, end, filename)
        for scope, run_id, workload in _TRANSFER_RUNS
        for index, (start, end, filename) in enumerate(_TRANSFER_SPECS[workload])
    )
)


def fail(kind: FailureKind, affected: str, detail: str, action: str) -> NoReturn:
    raise Issue(kind, affected, detail, action)


def canonical_json_bytes(value: object) -> bytes:
    return render_json_document(value, ensure_ascii=False)


def canonical_json_line_bytes(value: object) -> bytes:
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


def path_key(value: str) -> bytes:
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        fail("artifact_foreign", value, "bundle path is not strict UTF-8", "restore a strict UTF-8 retained path")


def read_regular(path: Path, *, affected: str) -> bytes:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        fail("artifact_missing", affected, f"{affected} is missing", "restore the exact retained artifact")
    except OSError as error:
        fail("artifact_corrupt", affected, f"could not inspect {affected}: {error}", "repair the retained artifact")
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        fail(
            "artifact_foreign", affected, f"{affected} must be a regular non-symlink file", "replace the foreign entry"
        )
    try:
        return path.read_bytes()
    except OSError as error:
        fail("artifact_corrupt", affected, f"could not read {affected}: {error}", "repair the retained artifact")


def parse_json_object(content: bytes, *, name: str, canonical: bool = True) -> dict[str, object]:
    try:
        parsed = json.loads(content.decode("utf-8"), object_pairs_hook=_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        fail("artifact_corrupt", name, f"{name} is not strict UTF-8 JSON: {error}", "restore canonical retained JSON")
    if type(parsed) is not dict:
        fail("artifact_corrupt", name, f"{name} must contain one JSON object", "restore canonical retained JSON")
    document = cast(dict[str, object], parsed)
    if canonical and canonical_json_bytes(document) != content:
        fail("artifact_corrupt", name, f"{name} is not canonical JSON", "restore canonical retained JSON")
    return document


def validated_study_root(
    document: dict[str, object], model: type[BaseModel], *, name: str, affected: str | None = None
) -> dict[str, object]:
    """Validate one public study shape after duplicate-free canonical JSON decoding."""
    try:
        validated = model.model_validate(document)
    except ValidationError as error:
        first = error.errors(include_url=False, include_input=False)[0]
        location = ".".join(str(part) for part in first["loc"]) or "root"
        fail(
            "artifact_corrupt",
            affected or name,
            f"{name} has invalid {location}: {first['msg']} [{first['type']}]",
            "restore canonical evidence",
        )
    return cast(dict[str, object], validated.model_dump(mode="json"))


def exact(value: object, keys: tuple[str, ...], *, name: str) -> dict[str, object]:
    if type(value) is not dict or set(cast(dict[str, object], value)) != set(keys):
        fail("artifact_corrupt", name, f"{name} must contain exactly {', '.join(keys)}", "restore canonical evidence")
    return cast(dict[str, object], value)


def string(value: object, *, name: str) -> str:
    if type(value) is not str or not value.strip():
        fail("artifact_corrupt", name, f"{name} must be a nonempty string", "restore canonical evidence")
    return value


def integer(value: object, *, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        fail("artifact_corrupt", name, f"{name} must be an integer at least {minimum}", "restore canonical evidence")
    return value


def relative_path(value: object, *, name: str) -> str:
    text = string(value, name=name)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or "\\" in text
        or (not path.parts)
        or any(part in ("", ".", "..") for part in path.parts)
        or (path.as_posix() != text)
    ):
        fail(
            "artifact_foreign",
            name,
            f"{name} must be a normalized bundle-relative POSIX path",
            "restore canonical evidence",
        )
    path_key(text)
    return text


def require_directory(value: object, *, name: str) -> Path:
    if not isinstance(value, Path):
        raise TypeError(f"{name} must be a pathlib.Path")
    try:
        mode = value.lstat().st_mode
    except FileNotFoundError:
        fail("artifact_missing", name, f"{name} is missing", "restore the retained candidate directory")
    except OSError as error:
        fail("artifact_corrupt", name, f"could not inspect {name}: {error}", "repair the local filesystem entry")
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        fail(
            "artifact_foreign", name, f"{name} must be a regular non-symlink directory", "replace the foreign directory"
        )
    return value.resolve()


def files_for_candidate(root: Path, *, include_manifest: bool) -> dict[str, Path]:
    discovered: dict[str, Path] = {}
    try:
        paths = tuple(root.rglob("*"))
    except OSError as error:
        fail(
            "artifact_corrupt", "bundle", f"could not enumerate retained bundle: {error}", "repair the retained bundle"
        )
    for path in sorted(paths, key=lambda item: path_key(item.relative_to(root).as_posix())):
        relative = path.relative_to(root).as_posix()
        if any(part.startswith(".") or part.endswith(_TEMP_SUFFIXES) for part in PurePosixPath(relative).parts):
            fail(
                "artifact_foreign",
                relative,
                f"{relative} is a temporary or hidden retained entry",
                "remove temporary residue",
            )
        try:
            mode = path.lstat().st_mode
        except OSError as error:
            fail("artifact_corrupt", relative, f"could not inspect {relative}: {error}", "repair the retained bundle")
        if stat.S_ISLNK(mode):
            fail(
                "artifact_foreign",
                relative,
                f"{relative} must not be a symlink",
                "replace the symlink with retained bytes",
            )
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            fail(
                "artifact_foreign",
                relative,
                f"{relative} must be a regular file",
                "remove the foreign filesystem entry",
            )
        if relative == MANIFEST and (not include_manifest):
            continue
        discovered[relative] = path
    return discovered


def manifest_entries(content: bytes) -> tuple[Entry, ...]:
    document = parse_json_object(content, name=MANIFEST)
    if document.get("schema_version") != SCHEMA:
        fail(
            "artifact_corrupt",
            MANIFEST,
            "manifest must use schema version 2 and a file list",
            "restore canonical manifest",
        )
    document = validated_study_root(document, ValidationStudyManifest, name=MANIFEST)
    parsed: list[Entry] = []
    for item in cast(list[object], document["files"]):
        entry = exact(item, ("lineage", "owner", "path", "sha256", "size"), name="manifest file entry")
        relative = relative_path(entry["path"], name="manifest path")
        digest = string(entry["sha256"], name=f"manifest SHA-256 for {relative}")
        parsed.append(
            Entry(
                relative,
                integer(entry["size"], name=f"manifest size for {relative}"),
                digest,
                string(entry["owner"], name=f"manifest owner for {relative}"),
                entry["lineage"],
            )
        )
    return tuple(parsed)


def workload_name(value: object, *, name: str) -> str:
    result = string(value, name=name)
    if result not in WORKLOADS:
        fail(
            "artifact_corrupt", name, "workload must be short, streaming, or bursty", "restore frozen protocol evidence"
        )
    return result


def repeat_number(value: object, *, name: str) -> int:
    result = integer(value, name=name, minimum=1)
    if result not in REPEATS:
        fail("artifact_corrupt", name, "repeat must be one, two, or three", "restore frozen protocol evidence")
    return result


def scoped_transfer_path(relative: str) -> tuple[str, _Transfer] | None:
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
    for binding in TRANSFER_BINDINGS:
        if (binding.scope, binding.run_id, binding.filename) == (scope, run_id, filename):
            return (kind, binding)
    return None


def artifact_identity(content: bytes) -> dict[str, object]:
    result: dict[str, object] = dict(identify_bytes(content).as_dict())
    return result


def canonical_jsonl(content: bytes, *, name: str) -> None:
    if not content or not content.endswith(b"\n"):
        fail(
            "artifact_corrupt",
            name,
            "run log must be nonempty canonical JSONL with a terminal newline",
            "restore canonical run log",
        )
    for line_number, raw in enumerate(content.splitlines(keepends=True), start=1):
        if not raw.endswith(b"\n"):
            fail("artifact_corrupt", name, "run log must use LF-terminated records", "restore canonical run log")
        record = parse_json_object(raw, name=f"{name}:{line_number}", canonical=False)
        if canonical_json_line_bytes(record) != raw:
            fail("artifact_corrupt", name, "run log record is not canonical JSONL", "restore canonical run log")


def training_runtime(content: bytes, *, name: str, workload: str, repeat: int) -> float:
    """Extract the one producer-recorded training runtime from canonical JSONL."""
    matches: list[dict[str, object]] = []
    for line_number, raw in enumerate(content.splitlines(keepends=True), start=1):
        record = parse_json_object(raw, name=f"{name}:{line_number}", canonical=False)
        if record.get("event") == "validation_study_training_completed":
            matches.append(record)
    if len(matches) != 1:
        fail(
            "artifact_corrupt",
            name,
            "training run log must contain exactly one validation study runtime record",
            "restore canonical training runtime evidence",
        )
    record = exact(
        matches[0], ("event", "repeat", "runtime_seconds", "stage", "workload"), name=f"{name} training runtime"
    )
    value = record["runtime_seconds"]
    if (
        record["event"] != "validation_study_training_completed"
        or record["stage"] != "study"
        or record["workload"] != workload
        or (record["repeat"] != repeat)
        or (type(value) is not float)
        or (not math.isfinite(value))
        or (value < 0.0)
    ):
        fail(
            "artifact_foreign",
            name,
            "training runtime record does not match the retained run identity",
            "restore matching training runtime evidence",
        )
    return value


def parse_run_log_records(content: bytes, *, name: str) -> tuple[dict[str, object], ...]:
    """Parse canonical JSONL into the retained producer records."""
    canonical_jsonl(content, name=name)
    records: list[dict[str, object]] = []
    for line_number, raw in enumerate(content.splitlines(keepends=True), start=1):
        value = parse_json_object(raw, name=f"{name}:{line_number}", canonical=False)
        records.append(value)
    return tuple(records)


def required_log_record(records: Sequence[Mapping[str, object]], *, event: str, name: str) -> Mapping[str, object]:
    matches = tuple(record for record in records if record.get("event") == event)
    if len(matches) != 1:
        fail(
            "artifact_foreign",
            name,
            f"run log must contain exactly one {event} record",
            "restore complete matching run-log lineage",
        )
    return matches[0]


def require_log_fields(record: Mapping[str, object], expected: Mapping[str, object], *, name: str, event: str) -> None:
    if any((record.get(field) != value for field, value in expected.items())):
        fail(
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
        "host_architecture": "linux/amd64",
        "target_content_id": environment["target_image_id"],
        "target_reference": environment["target_image_reference"],
    }


def _require_successful_log_status(records: Sequence[Mapping[str, object]], *, name: str) -> None:
    for record in records:
        event = record.get("event")
        if (
            type(event) is str
            and (event.endswith("_reused") or event.endswith("_failed"))
            or ("reused" in record and record["reused"] is not False)
        ):
            fail(
                "artifact_foreign",
                name,
                "run log cannot retain reused or failed stage status",
                "restore complete matching run-log lineage",
            )


def require_terminal_log_events(records: Sequence[Mapping[str, object]], *, events: tuple[str, ...], name: str) -> None:
    if tuple(record.get("event") for record in records[-len(events) :]) != events:
        fail(
            "artifact_foreign",
            name,
            f"run log must end with the successful publication sequence {events!r}",
            "restore complete matching run-log lineage",
        )


def require_ordered_log_events(records: Sequence[Mapping[str, object]], *, events: tuple[str, ...], name: str) -> None:
    positions: list[int] = []
    for event in events:
        position = next((index for index, record in enumerate(records) if record.get("event") == event), None)
        if position is None:
            fail(
                "artifact_foreign",
                name,
                f"run log lacks required {event} event for successful stage order",
                "restore complete matching run-log lineage",
            )
        positions.append(position)
    if positions != sorted(positions):
        fail(
            "artifact_foreign",
            name,
            "run log does not preserve successful stage order",
            "restore complete matching run-log lineage",
        )


def require_capture_log_lineage(
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
    require_log_fields(
        required_log_record(records, event="capture_environment_identity", name=name),
        {"event": "capture_environment_identity", "stage": "preflight", **environment_fields},
        name=name,
        event="capture_environment_identity",
    )
    capture_record = required_log_record(records, event="capture_published", name=name)
    expected: dict[str, object] = {
        "event": "capture_published",
        "stage": "capture",
        "capture_identity": artifact_identity(capture),
        "reference_identity": artifact_identity(reference),
        "experiment_identity": artifact_identity(experiment),
        "reused": False,
    }
    if packet_count is not None:
        expected["packet_count"] = packet_count
    require_log_fields(capture_record, expected, name=name, event="capture_published")
    nested = capture_record.get("capture_environment_identity")
    if not isinstance(nested, Mapping):
        fail(
            "artifact_foreign",
            name,
            "capture_published run-log record lacks its capture environment identity",
            "restore complete matching run-log lineage",
        )
    require_log_fields(cast(Mapping[str, object], nested), environment_fields, name=name, event="capture_published")


def mean(scores: Sequence[dict[str, object]]) -> dict[str, object]:
    if not scores:
        fail("artifact_corrupt", "report_inputs.json", "report arithmetic requires scores", "restore report inputs")
    methods = [cast(dict[str, object], score["methods"]) for score in scores]
    return {
        "aggregate": fmean(cast(float, score["aggregate"]) for score in scores),
        "methods": {name: fmean(cast(float, item[name]) for item in methods) for name in PUBLISHED_METHOD_ORDER},
    }
