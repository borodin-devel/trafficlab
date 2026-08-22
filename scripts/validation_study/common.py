"""Common owner for Validation Study tooling."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal, cast
from urllib.parse import urlsplit

from trafficlab import USER_AGENT, __version__
from trafficlab.capture.lineage import CaptureResult
from trafficlab.common.compatibility import ContentIdentity, identify_bytes
from trafficlab.common.config import ExperimentConfig, FamilyName
from trafficlab.common.errors import TrafficlabError
from trafficlab.fitting.genetic.types import METHOD_ORDER
from trafficlab.pipeline.types import RunResult

type JsonScalar = str | int | float | bool

type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

type JsonObject = dict[str, JsonValue]

type FrozenJsonValue = JsonScalar | tuple[FrozenJsonValue, ...] | Mapping[str, FrozenJsonValue]

type FrozenJsonObject = Mapping[str, FrozenJsonValue]

type WorkloadName = Literal["short", "streaming", "bursty"]

type PrerequisiteCommandKind = Literal["docker_matrix", "internet_smoke"]

type TransferRange = tuple[int, int, str]

type TrainingRunner = Callable[[Path], RunResult]

type HeldOutCaptureRunner = Callable[[Path], CaptureResult]

type CollectionInputs = tuple[dict[str, object], bytes, dict[str, bytes], dict[WorkloadName, ExperimentConfig], int]

TARGET_REFERENCE = "curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b"

BOOTSTRAP_SEED = 20260819

FAMILY_ORDER: tuple[FamilyName, ...] = ("markov_renewal", "mmpp", "poisson_empirical")

PUBLISHED_METHOD_ORDER = METHOD_ORDER

ARTIFACT_NAMES = (
    "experiment.toml",
    "reference.pcapng",
    "capture.json",
    "checkpoint.json",
    "ga_history.csv",
    "best_model.json",
    "generated.pcapng",
    "similarity.json",
    "run.log",
)

PRIMARY_ORDER = (
    (1, "01-short-r1", "short", 1),
    (2, "02-streaming-r1", "streaming", 1),
    (3, "03-bursty-r1", "bursty", 1),
    (4, "04-streaming-r2", "streaming", 2),
    (5, "05-bursty-r2", "bursty", 2),
    (6, "06-short-r2", "short", 2),
    (7, "07-bursty-r3", "bursty", 3),
    (8, "08-short-r3", "short", 3),
    (9, "09-streaming-r3", "streaming", 3),
)

RUNTIME_BOUNDARY = "run_experiment_cached_images_full_lifecycle"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

REPORT_HEADINGS = (
    "## Question, scope, environment, and protocol",
    "## Natural variation",
    "## Family champions",
    "## Fresh simulation, published, and runtime",
    "## Trace diagnostics",
    "## Saved-run reproduction",
    "## Limitations and next work",
)

CURL_COMMON = (
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

LOCKED_CURL_COMMON = CURL_COMMON

ORACLE_URL = "https://validation-study.example/object"

HISTORIC_SCHEMA_ONE_RESULT_COMMIT = "976dcd6ba8bfb4df4894e79263fb8b75dc426ad0"

HISTORIC_SCHEMA_ONE_RESULT_STUDY_ID = "validation-study-20260814-ovh-r3"

HISTORIC_SCHEMA_ONE_RESULT_URL = "https://sbg.proof.ovh.net/files/10Mb.dat"

PRESERVED_PRE_USER_AGENT_R6_COMMIT = "6ea60c35922855264b574c03bee2ab64e622d183"

PRESERVED_PRE_USER_AGENT_R6_TREE = "210b52105df20da973bd507c1b2f832398035c65"

PRESERVED_PRE_USER_AGENT_R6_STUDY_ID = "2026-08-16-research-fitness-r6"

PRESERVED_PRE_USER_AGENT_R6_URL = "https://upload.wikimedia.org/wikipedia/commons/5/5b/SPACE_ELECTRIC_ROCKET_TEST%2C_SERT_II_IN_TANK_5_%28GRC-1968-C-03031%29.jpg"

PRESERVED_PRE_USER_AGENT_R6_RAW_IDENTITY = {
    "sha256": "a6cb727911ad19333c2faffa09e7f8e246750c8524b04c8cac13f3402672d275",
    "size": 5662,
}

PRESERVED_PRE_USER_AGENT_R6_MARKER_IDENTITY = {
    "sha256": "c450ec554562c364dd2dcd824fa2f4edccfa2c9d936136efc0c72739da8550e6",
    "size": 320,
}

PRESERVED_PRE_USER_AGENT_R6_EVIDENCE_IDENTITIES: tuple[tuple[str, int, str], ...] = (
    ("capability.cid", 64, "2e9d83a41fd783fcd00c394ebb3d5aef2c7ccd259b812aa4921c17be8962c3a1"),
    ("capability.headers", 2066, "c271e6e5e909db84e54bb7231936eb680d145df8c4daff4a5239056aeb1613de"),
    ("capability.stderr", 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("capability.stdout", 161, "807fe709e0c95382a0cdd878bf71e77f525dc0ec58b0142d3636f49bcedb7d97"),
    ("capture.iid", 71, "10d7ebabfa8724f6e70b02ef48d2e96b31320b9bf60306b8030d1377f4326dcd"),
    ("docker.stderr", 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("docker.stdout", 3124, "4bb15010ceebbe53ec7487d6d68b195afd08d4b7c3bda6f00020d2d7e92cf4fe"),
    ("docker.xml", 3113, "f6e61b41be3b5659d0a1e198e946a566ef2c5438c35eec9a2f016865050fa47c"),
    ("internet.stderr", 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ("internet.stdout", 900, "b15c52b90ad5d364a678e90821530a751d43bcb8ad110d4ad41ef827492316f3"),
    ("internet.xml", 403, "5a3d2d9e02961e08fe898d0c5654cc921d645b5756cdc0eb7db03a892c22a01f"),
)

PREREQUISITE_ROOT_KEYS = (
    "schema_version",
    "created_utc",
    "study_id",
    "git_commit",
    "git_tree_clean",
    "url",
    "tools",
    "images",
    "capability",
    "config_sha256",
    "commands",
)

IMAGE_KEYS = (
    "target_reference",
    "target_image_id",
    "target_repo_digests",
    "target_config_user",
    "capture_image_id",
    "capture_dockerfile_sha256",
    "capture_script_sha256",
)

CAPABILITY_KEYS = (
    "argv",
    "started_utc",
    "completed_utc",
    "exit_status",
    "status",
    "content_length",
    "object_size_bytes",
    "redirect_count",
    "body_bytes_downloaded",
    "content_range",
    "final_url",
    "mount_source",
    "canary_archive_path",
    "canary_sha256",
    "container_id",
    "stdout_sha256",
    "stderr_sha256",
    "used_image_default_user",
    "mount_directory_mode",
    "canary_file_mode",
    "canary_archive_mode",
    "container_cleanup_verified",
)

ENVIRONMENT_KEYS = (
    "git_commit",
    "python_version",
    "trafficlab_version",
    "docker_engine_version",
    "docker_compose_version",
    "platform",
    "target_image_id",
    "capture_image_id",
    "study_date_utc",
)

PROTOCOL_KEYS = (
    "study_id",
    "url",
    "capability",
    "prerequisites_sha256",
    "target_reference",
    "capture_image_id",
    "transfer_evidence_mount_source",
    "base_config_sha256",
    "primary_order",
    "seeds",
    "families",
    "methods",
    "workloads",
    "runtime_boundary",
)

TRANSFER_RESPONSE_KEYS = (
    "transfer_index",
    "requested_start",
    "requested_end",
    "status",
    "content_length",
    "content_range",
    "header_archive_path",
    "header_sha256",
    "scratch_precreate_mode",
    "archive_mode",
    "inode_preserved",
)

RAW_SEQUENCE_KEYS = (
    "seed",
    "observation_window_seconds",
    "trial_event_count",
    "final_event_count",
    "raw_events_equal",
    "fresh_simulation_score_reproduced",
    "reparsed_event_count",
    "reparsed_matches_quantized",
)

WORKLOAD_SUMMARY_KEYS = (
    "workload",
    "runtime",
    "family_champions",
    "winner_selection_fitness",
    "fresh_simulation",
    "published",
    "reference_descriptors",
    "winner_counts",
)

REPRODUCTION_COMPARISON_KEYS = (
    "winner_family_equal",
    "winner_genes_equal",
    "winner_selection_fitness_delta",
    "fresh_simulation_delta",
    "published_delta",
    "reference_similarity",
)

RESULT_ROOT_KEYS = (
    "schema_version",
    "environment",
    "protocol",
    "runs",
    "natural_variation",
    "workload_summaries",
    "reproduction",
)

STUDY_RUN_KEYS = (
    "execution_order",
    "run_id",
    "key",
    "config_path",
    "run_directory",
    "transfer_evidence_directory",
    "elapsed_seconds",
    "reuse",
    "cleanup_verified",
    "transfer_responses",
    "artifact_sha256",
    "reference",
    "generated",
    "family_champions",
    "winner",
    "fresh_simulation",
    "published",
    "raw_sequence",
)

REPRODUCTION_KEYS = (
    "source_key",
    "execution_order",
    "run_id",
    "config_path",
    "run_directory",
    "transfer_evidence_directory",
    "command",
    "guard_command",
    "guard_exit_status",
    "guard_stdout_sha256",
    "guard_stderr_sha256",
    "elapsed_seconds",
    "changed_config_fields",
    "same_locked_config",
    "seeded_artifact_count",
    "cleanup_verified",
    "reuse",
    "transfer_responses",
    "artifact_sha256",
    "reference",
    "generated",
    "family_champions",
    "winner",
    "fresh_simulation",
    "published",
    "raw_sequence",
    "comparison_to_source",
)

DESCRIPTOR_KEYS = (
    "packet_count",
    "observation_window_seconds",
    "outbound_packets",
    "inbound_packets",
    "outbound_bytes",
    "inbound_bytes",
)

STUDY_ID_PATTERN = re.compile("[a-z0-9][a-z0-9-]{0,31}")

_SHA256_PATTERN = re.compile("[0-9a-f]{64}")

_UTC_PATTERN = re.compile("\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?Z")

_DNS_LABEL_PATTERN = re.compile("[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")

SUBPROCESS_TIMEOUTS = {
    "git_or_version": 20.0,
    "image_pull_or_build": 300.0,
    "capability": 45.0,
    "container_inspect_or_remove": 20.0,
    "docker_matrix_guard": 1230.0,
    "internet_smoke_guard": 630.0,
    "reproduction_guard": 1230.0,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def require_type(condition: bool, message: str) -> None:
    if not condition:
        raise TypeError(message)


def require_frozen_mapping(value: object, *, name: str) -> FrozenJsonObject:
    require_type(type(value) is MappingProxyType, f"{name} must be a frozen JSON object")
    return cast(FrozenJsonObject, value)


def replace_existing_regular_file(
    destination: Path, content: bytes, *, validate: Callable[[bytes], None], target_name: str
) -> None:
    """Atomically replace one regular ignored support file after validating staged bytes."""
    if path_entry_exists(destination):
        try:
            mode = destination.lstat().st_mode
        except OSError as error:
            raise ValueError(f"could not inspect {target_name} target {destination}: {error}") from error
        if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            raise TrafficlabError(
                f"{target_name} target must be a regular file: {destination}",
                corrective_action="preserve the existing path and use a regular canonical target",
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor_open = False
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        persisted = temporary.read_bytes()
        require(persisted == content, f"temporary {target_name} bytes changed before publication")
        validate(persisted)
        os.replace(temporary, destination)
        directory_descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        published = destination.read_bytes()
        require(published == content, f"published {target_name} bytes changed")
        validate(published)
    finally:
        if descriptor_open:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def read_regular_prerequisite_rotation_target(destination: Path, *, name: str) -> bytes:
    """Read one existing rotation target without following a symlink."""
    try:
        mode = destination.lstat().st_mode
    except OSError as error:
        raise ValueError(f"could not inspect {name} {destination}: {error}") from error
    require(stat.S_ISREG(mode) and (not stat.S_ISLNK(mode)), f"{name} must be a regular file")
    try:
        return destination.read_bytes()
    except OSError as error:
        raise ValueError(f"could not read {name} {destination}: {error}") from error


def path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def validate_study_id(value: str) -> str:
    require(
        type(value) is str and STUDY_ID_PATTERN.fullmatch(value) is not None,
        "study ID must match [a-z0-9][a-z0-9-]{0,31}",
    )
    return value


def validate_endpoint_url(value: str) -> str:
    require(type(value) is str, "URL must be an absolute credential-free HTTPS URL with a DNS hostname")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("URL must contain a valid DNS hostname and port") from error
    hostname = parsed.hostname
    labels = () if hostname is None else tuple(hostname.rstrip(".").split("."))
    valid_hostname = (
        bool(labels)
        and all(_DNS_LABEL_PATTERN.fullmatch(label) is not None for label in labels)
        and (not all(character.isdigit() or character == "." for character in hostname or ""))
    )
    require(
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and (parsed.username is None)
        and (parsed.password is None)
        and (not parsed.query)
        and (not parsed.fragment)
        and valid_hostname
        and (port is None or 1 <= port <= 65535),
        "URL must be absolute credential-free HTTPS with a DNS hostname and no query or fragment",
    )
    return value


def exact_object(value: object, keys: Sequence[str], *, name: str) -> dict[str, object]:
    require(type(value) is dict, f"{name} must be a JSON object with exact keys")
    document = cast(dict[object, object], value)
    require(all(type(key) is str for key in document), f"{name} must have string keys")
    result = cast(dict[str, object], document)
    require(set(result) == set(keys) and len(result) == len(keys), f"{name} must contain exact keys: {', '.join(keys)}")
    return result


def strict_int(value: object, *, name: str, minimum: int | None = None) -> int:
    require(type(value) is int, f"{name} must be an exact integer")
    result = cast(int, value)
    require(minimum is None or result >= minimum, f"{name} must be an integer at least {minimum}")
    return result


def strict_float(value: object, *, name: str, lower: float | None = None, upper: float | None = None) -> float:
    require(type(value) is float, f"{name} must be an exact finite float")
    result = cast(float, value)
    require(math.isfinite(result), f"{name} must be an exact finite float")
    require(
        (lower is None or result >= lower) and (upper is None or result <= upper),
        f"{name} must be a float in [{lower}, {upper}]",
    )
    return result


def strict_bool(value: object, *, name: str) -> bool:
    require(type(value) is bool, f"{name} must be an exact boolean")
    return cast(bool, value)


def strict_string(value: object, *, name: str, nonempty: bool = True) -> str:
    qualifier = "nonempty " if nonempty else ""
    require(type(value) is str and (not nonempty or bool(value)), f"{name} must be a {qualifier}string")
    return cast(str, value)


def sha256(value: object, *, name: str) -> str:
    result = strict_string(value, name=name)
    require(_SHA256_PATTERN.fullmatch(result) is not None, f"{name} must be a 64-character lowercase SHA-256")
    return result


def utc_timestamp(value: object, *, name: str) -> str:
    result = strict_string(value, name=name)
    require(_UTC_PATTERN.fullmatch(result) is not None, f"{name} must be a UTC RFC 3339 timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(f"{result[:-1]}+00:00")
    except ValueError as error:
        raise ValueError(f"{name} must be a valid UTC RFC 3339 timestamp ending in Z") from error
    require(parsed.tzinfo == UTC, f"{name} must be a UTC RFC 3339 timestamp ending in Z")
    return result


def repository_relative_path(value: object, *, repository_root: Path, name: str) -> str:
    result = strict_string(value, name=name)
    parts = result.split("/")
    pure = PurePosixPath(result)
    require(
        "\\" not in result
        and (not pure.is_absolute())
        and all(part not in {"", ".", ".."} for part in parts)
        and (pure.as_posix() == result),
        f"{name} must be a normalized repository-relative POSIX path",
    )
    root = repository_root.resolve()
    resolved = (root / Path(*parts)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{name} must resolve as a repository-relative path beneath the repository") from error
    return result


def freeze_json(value: JsonValue) -> FrozenJsonValue:
    if type(value) is dict:
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(freeze_json(item) for item in value)
    if type(value) in {str, int, float, bool}:
        return cast(JsonScalar, value)
    raise TypeError("JSON value must contain only exact JSON scalar and collection types")


def freeze_object(value: JsonObject) -> FrozenJsonObject:
    return cast(FrozenJsonObject, freeze_json(value))


def thaw_json(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [thaw_json(item) for item in value]
    return cast(JsonScalar, value)


def canonical_json(document: JsonObject) -> bytes:
    try:
        rendered = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"could not render canonical JSON: {error}") from error
    return f"{rendered}\n".encode()


def load_json(content: bytes) -> JsonObject:

    def duplicate_free_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(token: str) -> object:
        raise ValueError(f"invalid JSON constant: {token}")

    try:
        loaded = json.loads(
            content.decode("utf-8"), object_pairs_hook=duplicate_free_object, parse_constant=invalid_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid UTF-8 JSON: {error}") from error
    if type(loaded) is not dict:
        raise ValueError("JSON root must be an object")
    return cast(JsonObject, loaded)


def strict_list(value: object, *, name: str) -> list[object]:
    require(type(value) is list, f"{name} must be a JSON array")
    return cast(list[object], value)


def string_array(value: object, *, name: str, nonempty: bool = False) -> tuple[str, ...]:
    items = strict_list(value, name=name)
    require(not nonempty or bool(items), f"{name} must be a nonempty string array")
    return tuple(strict_string(item, name=f"{name} item") for item in items)


def _timestamp_value(value: str) -> datetime:
    return datetime.fromisoformat(f"{value[:-1]}+00:00")


def require_timestamp_order(started: str, completed: str, *, name: str) -> None:
    require(
        _timestamp_value(completed) >= _timestamp_value(started),
        f"{name} completion timestamp must not precede its start",
    )


def image_id_value(value: object, *, name: str) -> str:
    result = strict_string(value, name=name)
    require(re.fullmatch("sha256:[0-9a-f]{64}", result) is not None, f"{name} must be an exact sha256 image ID")
    return result


def container_id_value(value: object, *, name: str) -> str:
    result = strict_string(value, name=name)
    require(re.fullmatch("[0-9a-f]{64}", result) is not None, f"{name} must be a full lowercase container ID")
    return result


def git_commit_value(value: object) -> str:
    result = strict_string(value, name="Git commit")
    require(re.fullmatch("[0-9a-f]{40}", result) is not None, "Git commit must be 40 lowercase hexadecimal characters")
    return result


def profile_hashes(value: object) -> JsonObject:
    document = exact_object(value, ("short", "streaming", "bursty"), name="profile hash map")
    for key in ("short", "streaming", "bursty"):
        sha256(document[key], name=f"profile hash {key}")
    return cast(JsonObject, document)


def validate_tools(value: object) -> JsonObject:
    keys = (
        "docker_engine_version",
        "docker_compose_version",
        "host_architecture",
        "kernel_release",
        "platform",
        "python_implementation",
        "python_version",
        "trafficlab_version",
        "uv_lock_sha256",
    )
    document = exact_object(value, keys, name="tools")
    for key in keys:
        strict_string(document[key], name=f"tools.{key}")
    require(document["python_version"] == "3.12.3", "tools.python_version must be exactly 3.12.3")
    require(document["python_implementation"] == "CPython", "tools.python_implementation must be CPython")
    sha256(document["uv_lock_sha256"], name="tools uv.lock SHA-256")
    require(document["trafficlab_version"] == __version__, f"tools.trafficlab_version must be exactly {__version__}")
    return cast(JsonObject, document)


def validate_images(value: object) -> JsonObject:
    document = exact_object(value, IMAGE_KEYS, name="images")
    target_reference = strict_string(document["target_reference"], name="target reference")
    require(target_reference == TARGET_REFERENCE, "target reference must be the approved digest-pinned curl image")
    repo_digests = string_array(document["target_repo_digests"], name="target repository digests", nonempty=True)
    require(
        repo_digests == tuple(sorted(repo_digests)) and TARGET_REFERENCE in repo_digests,
        "target repository digests must be sorted and include the approved target reference",
    )
    image_id_value(document["target_image_id"], name="target image ID")
    strict_string(document["target_config_user"], name="target configured user", nonempty=False)
    image_id_value(document["capture_image_id"], name="capture image ID")
    sha256(document["capture_dockerfile_sha256"], name="capture Dockerfile SHA-256")
    sha256(document["capture_script_sha256"], name="capture script SHA-256")
    return cast(JsonObject, document)


def validate_test_counts(value: object) -> JsonObject:
    keys = ("total", "passed", "failed", "errors", "skipped")
    document = exact_object(value, keys, name="test counts")
    counts = {key: strict_int(document[key], name=f"test counts.{key}", minimum=0) for key in keys}
    require(counts["total"] > 0, "test counts.total must be positive")
    for key in ("failed", "errors", "skipped"):
        require(counts[key] == 0, f"test counts.{key} must be zero")
    require(counts["passed"] == counts["total"], "test counts.passed must equal total")
    return cast(JsonObject, document)


def retained_identity(value: object, *, name: str) -> JsonObject:
    try:
        identity = ContentIdentity.from_dict(value, name=name)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a content identity: {error}") from error
    return cast(JsonObject, identity.as_dict())


def retained_output(value: object, *, name: str, expected_path: str) -> JsonObject:
    document = cast(JsonObject, value)
    require(document["path"] == expected_path, f"{name} path must be exactly {expected_path}")
    return document


def publish_support_json(
    path: Path, content: bytes, *, validate: Callable[[bytes], None], replace_existing: bool = False
) -> None:
    if path_entry_exists(path):
        if replace_existing:
            replace_existing_regular_file(
                path, content, validate=validate, target_name="official Validation Study publication"
            )
            return
        raise TrafficlabError(
            f"official Validation Study publication target already exists: {path}",
            corrective_action="preserve the existing official file and restart with a new study ID",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor_open = False
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        persisted = temporary.read_bytes()
        require(persisted == content, "temporary official Validation Study JSON bytes changed before publication")
        validate(persisted)
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise TrafficlabError(
                f"official Validation Study publication target already exists: {path}",
                corrective_action="preserve the existing official file and restart with a new study ID",
            ) from error
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        published = path.read_bytes()
        require(published == content, "published official Validation Study JSON bytes changed")
        validate(published)
    finally:
        if descriptor_open:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def study_git_status_is_permitted(content: bytes) -> bool:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return False
    permitted = {
        "examples/validation_study/prerequisites.json",
        "examples/validation_study/configs/short.toml",
        "examples/validation_study/configs/streaming.toml",
        "examples/validation_study/configs/bursty.toml",
    }
    return all(line.startswith("?? ") and line[3:] in permitted for line in lines)


def write_candidate_bytes(path: Path, content: bytes) -> None:
    require(not path_entry_exists(path), f"collection output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except OSError as error:
        raise ValueError(f"could not write collection output {path}: {error}") from error


def candidate_identity(content: bytes) -> JsonObject:
    return cast(JsonObject, identify_bytes(content).as_dict())


def phase_capture_tag(study_id: str, phase: Literal["collection", "study"]) -> str:
    """Return the exclusive capture-image tag for one irreversible public phase."""
    return f"trafficlab-validation-{validate_study_id(study_id)}:{phase}-capture"
