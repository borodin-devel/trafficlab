"""Codec owner for Validation Study tooling."""

from __future__ import annotations

import re
import stat
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scripts.validation_study.common import (
    CAPABILITY_KEYS,
    HISTORIC_SCHEMA_ONE_RESULT_STUDY_ID,
    HISTORIC_SCHEMA_ONE_RESULT_URL,
    PREREQUISITE_ROOT_KEYS,
    PRESERVED_PRE_USER_AGENT_R6_COMMIT,
    PRESERVED_PRE_USER_AGENT_R6_EVIDENCE_IDENTITIES,
    PRESERVED_PRE_USER_AGENT_R6_MARKER_IDENTITY,
    PRESERVED_PRE_USER_AGENT_R6_RAW_IDENTITY,
    PRESERVED_PRE_USER_AGENT_R6_STUDY_ID,
    PRESERVED_PRE_USER_AGENT_R6_TREE,
    PRESERVED_PRE_USER_AGENT_R6_URL,
    REPOSITORY_ROOT,
    SUBPROCESS_TIMEOUTS,
    TARGET_REFERENCE,
    JsonObject,
    candidate_identity,
    canonical_json,
    container_id_value,
    exact_object,
    freeze_object,
    git_commit_value,
    load_json,
    profile_hashes,
    read_regular_prerequisite_rotation_target,
    repository_relative_path,
    require,
    require_timestamp_order,
    require_type,
    retained_identity,
    retained_output,
    sha256,
    strict_bool,
    strict_int,
    strict_list,
    strict_string,
    string_array,
    thaw_json,
    utc_timestamp,
    validate_endpoint_url,
    validate_images,
    validate_study_id,
    validate_tools,
)
from scripts.validation_study.prerequisites.commands import validate_command, validate_frozen_prerequisite_command
from scripts.validation_study.records import PrerequisiteResults
from scripts.validation_study.rotation.schema import collection_attempt_root
from scripts.validation_study.workloads import WorkloadSpec
from trafficlab import USER_AGENT
from trafficlab.common.compatibility import identify_bytes
from trafficlab.common.json import render_json_line
from trafficlab.study_evidence.protocol import ValidationStudyPrerequisite, validate_study_model

if TYPE_CHECKING:
    from scripts.validation_study.records import CommandRunner

_RETAINED_PREREQUISITE_ENVIRONMENT_KEYS = (
    "capture_image_id",
    "capture_image_reference",
    "capture_tool_version",
    "source_commit",
    "source_tree",
    "target_image_id",
    "target_image_reference",
    "uv_lock_identity",
)

RETAINED_PREREQUISITE_CAPABILITY_KEYS = (
    "canary_sha256",
    "content_length",
    "content_range",
    "object_size_bytes",
    "status",
)

RETAINED_CAPABILITY_HEADER = "headers/prerequisites/00-prerequisites/capability.headers"


def _retained_prerequisite_environment(value: object) -> JsonObject:
    document = cast(JsonObject, value)
    target_reference = cast(str, document["target_image_reference"])
    require(
        target_reference == TARGET_REFERENCE,
        "retained target image reference must equal the approved digest-pinned target",
    )
    capture_id = cast(str, document["capture_image_id"])
    capture_reference = cast(str, document["capture_image_reference"])
    require(
        capture_reference == capture_id
        or (
            "@sha256:" in capture_reference
            and re.fullmatch("[0-9a-f]{64}", capture_reference.rsplit("@sha256:", 1)[-1]) is not None
        ),
        "retained capture image reference must be its immutable image ID or digest reference",
    )
    return document


def _retained_prerequisite_capability(value: object) -> JsonObject:
    document = cast(JsonObject, value)
    object_size = cast(int, document["object_size_bytes"])
    require(
        4 * 1024 * 1024 <= object_size <= 16 * 1024 * 1024,
        "retained capability object size must be from 4 MiB through 16 MiB",
    )
    status = cast(int, document["status"])
    length = cast(int, document["content_length"])
    require((status, length) == (206, 1), "retained capability must record one 206 byte-range response")
    content_range = cast(str, document["content_range"])
    require(
        content_range == f"bytes 0-0/{object_size}",
        "retained capability content range must bind the recorded object size",
    )
    return document


def _retained_prerequisite_document(value: object) -> dict[str, object]:
    require(type(value) is dict, "retained prerequisite evidence must be a JSON object")
    raw = cast(dict[str, object], value)
    require(raw.get("schema_version") == 4, "retained prerequisite schema version must be exactly 4")
    validated = validate_study_model(ValidationStudyPrerequisite, raw, name="retained prerequisite evidence")
    root = cast(dict[str, object], validated.model_dump(mode="json"))
    study_id = validate_study_id(strict_string(root["study_id"], name="retained prerequisite study ID"))
    url = validate_endpoint_url(strict_string(root["url"], name="retained prerequisite URL"))
    values = cast(list[object], root["commands"])
    commands: list[JsonObject] = []
    for value, expected_kind in zip(values, ("docker_matrix", "internet_smoke"), strict=True):
        document = cast(dict[str, object], value)
        kind = cast(str, document["kind"])
        require(kind == expected_kind, "retained prerequisite commands must use the fixed kind order")
        argv = tuple(cast(list[str], document["argv"]))
        validate_frozen_prerequisite_command(
            kind, argv, document["exit_status"], document["tests"], study_id=study_id, url=url
        )
        commands.append(
            {
                "argv": list(argv),
                "command": retained_output(
                    document["command"],
                    name=f"retained {kind} command",
                    expected_path=f"prerequisites/{kind}.command.json",
                ),
                "exit_status": 0,
                "junit": retained_output(
                    document["junit"], name=f"retained {kind} JUnit", expected_path=f"prerequisites/{kind}.junit.xml"
                ),
                "kind": kind,
                "status": retained_output(
                    document["status"],
                    name=f"retained {kind} status",
                    expected_path=f"prerequisites/{kind}.status.json",
                ),
                "stderr": retained_output(
                    document["stderr"], name=f"retained {kind} stderr", expected_path=f"prerequisites/{kind}.stderr"
                ),
                "stdout": retained_output(
                    document["stdout"], name=f"retained {kind} stdout", expected_path=f"prerequisites/{kind}.stdout"
                ),
                "tests": cast(JsonObject, document["tests"]),
            }
        )
    return {
        "capability": _retained_prerequisite_capability(root["capability"]),
        "commands": commands,
        "environment": _retained_prerequisite_environment(root["environment"]),
        "schema_version": 4,
        "study_id": study_id,
        "url": url,
    }


def render_retained_prerequisites(value: object) -> bytes:
    """Render one canonical, complete retained prerequisite evidence document."""
    return canonical_json(cast(JsonObject, _retained_prerequisite_document(value)))


def parse_retained_prerequisites(content: bytes) -> dict[str, object]:
    """Strictly parse a canonical retained prerequisite document without executing it."""
    document = _retained_prerequisite_document(load_json(content))
    if canonical_json(cast(JsonObject, document)) != content:
        raise ValueError(
            "retained prerequisite JSON must use canonical sorted readable encoding with one trailing newline"
        )
    return document


def retained_prerequisite_paths(value: object) -> tuple[str, ...]:
    """Return the three retained output paths for each validated prerequisite command."""
    document = _retained_prerequisite_document(value)
    paths = [
        cast(str, cast(JsonObject, command[field])["path"])
        for command in cast(list[JsonObject], document["commands"])
        for field in ("command", "status", "stdout", "stderr", "junit")
    ]
    return tuple(paths)


def build_expected_capability_argv(study_id: str, url: str) -> tuple[str, ...]:
    evidence = f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites"
    mount_source = f"examples/validation_study/.study-work/mount/{study_id}"
    return (
        "docker",
        "run",
        "--rm",
        "--name",
        f"trafficlab-validation-study-capability-{study_id}",
        "--label",
        f"org.trafficlab.validation-study.study={study_id}",
        "--cidfile",
        f"{evidence}/capability.cid",
        "--network",
        "bridge",
        "--mount",
        f"type=bind,src={mount_source},dst=/trafficlab-study",
        TARGET_REFERENCE,
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
        "--max-time",
        "30",
        "--range",
        "0-0",
        "--max-filesize",
        "1",
        "--dump-header",
        "/trafficlab-study/.capability.headers",
        "--output",
        "/dev/null",
        "--write-out",
        "status=%{response_code}\nsize=%{size_download}\nurl=%{url_effective}\nredirects=%{num_redirects}\n",
        "--url",
        url,
    )


def _pre_user_agent_capability_argv(study_id: str, url: str) -> tuple[str, ...]:
    """Return the immediately preceding capability projection for rotation-only compatibility."""
    current = build_expected_capability_argv(study_id, url)
    user_agent = current.index("--user-agent")
    return current[:user_agent] + current[user_agent + 2 :]


def _historic_schema_one_capability_argv() -> tuple[str, ...]:
    """Return the sole pre-User-Agent command retained in checked schema-1 evidence."""
    return _pre_user_agent_capability_argv(HISTORIC_SCHEMA_ONE_RESULT_STUDY_ID, HISTORIC_SCHEMA_ONE_RESULT_URL)


def _historic_schema_one_workload_argvs() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return the sole pre-User-Agent workload projections retained in checked schema-1 evidence."""
    common = (
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
        "--connect-timeout",
        "15",
    )
    url = HISTORIC_SCHEMA_ONE_RESULT_URL
    short = (
        *common,
        "--max-time",
        "30",
        "--limit-rate",
        "4M",
        "--range",
        "0-262143",
        "--max-filesize",
        "262144",
        "--dump-header",
        "/trafficlab-study/short.headers",
        "--output",
        "/dev/null",
        "--url",
        url,
    )
    streaming = (
        *common,
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
                *common,
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
    return (short, streaming, ("--parallel", "--parallel-max", "4", "--fail-early", *bursty_groups))


def historic_schema_one_workload_specs() -> tuple[WorkloadSpec, WorkloadSpec, WorkloadSpec]:
    """Return the complete measured profile of the sole retained schema-1 study."""
    short, streaming, bursty = _historic_schema_one_workload_argvs()
    bursty_transfers = tuple(
        (
            (start, start + 32767, f"bursty-{index}.headers")
            for index, start in enumerate((0, 524288, 1048576, 1572864, 2097152, 2621440, 3145728, 3670016))
        )
    )
    return (
        WorkloadSpec(
            name="short",
            argv=short,
            transfers=((0, 262143, "short.headers"),),
            workload_timeout_seconds=35.0,
            total_timeout_seconds=90.0,
            multiscale_widths_seconds=(0.001, 0.01),
        ),
        WorkloadSpec(
            name="streaming",
            argv=streaming,
            transfers=((0, 4194303, "streaming.headers"),),
            workload_timeout_seconds=50.0,
            total_timeout_seconds=120.0,
            multiscale_widths_seconds=(0.25, 1.0),
        ),
        WorkloadSpec(
            name="bursty",
            argv=bursty,
            transfers=bursty_transfers,
            workload_timeout_seconds=35.0,
            total_timeout_seconds=90.0,
            multiscale_widths_seconds=(0.001, 0.01),
        ),
    )


def historic_schema_one_workload_transfers(workload: str) -> tuple[tuple[int, int, str], ...]:
    """Return the exact range requests recorded by the sole schema-1 study."""
    return next(spec.transfers for spec in historic_schema_one_workload_specs() if spec.name == workload)


def validate_capability(
    value: object,
    *,
    repository_root: Path,
    study_id: str,
    url: str,
    historic_schema_one_result: bool = False,
    expected_capability_argv: tuple[str, ...] | None = None,
) -> JsonObject:
    document = exact_object(value, CAPABILITY_KEYS, name="capability")
    argv = string_array(document["argv"], name="capability argv", nonempty=True)
    expected_argv = expected_capability_argv or (
        _historic_schema_one_capability_argv()
        if historic_schema_one_result
        else build_expected_capability_argv(study_id, url)
    )
    require(argv == expected_argv, "capability argv must equal the exact repository-relative Docker/curl projection")
    started = utc_timestamp(document["started_utc"], name="capability start")
    completed = utc_timestamp(document["completed_utc"], name="capability completion")
    require_timestamp_order(started, completed, name="capability")
    exit_status = strict_int(document["exit_status"], name="capability exit status")
    status = strict_int(document["status"], name="capability status")
    content_length = strict_int(document["content_length"], name="capability content length")
    object_size = strict_int(document["object_size_bytes"], name="capability object size")
    redirect_count = strict_int(document["redirect_count"], name="capability redirect count")
    downloaded = strict_int(document["body_bytes_downloaded"], name="capability downloaded bytes")
    require(
        (exit_status, status, content_length, downloaded) == (0, 206, 1, 1),
        "capability must have exit zero, status 206, content length one, and one downloaded byte",
    )
    require(
        4 * 1024 * 1024 <= object_size <= 16 * 1024 * 1024, "capability object size must be from 4 MiB through 16 MiB"
    )
    require(0 <= redirect_count <= 3, "capability redirect count must be in 0..3")
    content_range = strict_string(document["content_range"], name="capability content range")
    require(content_range == f"bytes 0-0/{object_size}", "capability content range must exactly match its object size")
    validate_endpoint_url(strict_string(document["final_url"], name="capability final URL"))
    mount_source = repository_relative_path(
        document["mount_source"], repository_root=repository_root, name="capability mount source"
    )
    expected_mount = f"examples/validation_study/.study-work/mount/{study_id}"
    require(
        mount_source == expected_mount, "capability mount source must equal the study repository-relative mount path"
    )
    archive_path = repository_relative_path(
        document["canary_archive_path"], repository_root=repository_root, name="capability canary archive path"
    )
    expected_archive = f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites/capability.headers"
    require(archive_path == expected_archive, "capability canary archive path must equal the study evidence path")
    default_user = strict_bool(document["used_image_default_user"], name="capability default image user")
    cleanup = strict_bool(document["container_cleanup_verified"], name="capability cleanup verification")
    mount_mode = strict_int(document["mount_directory_mode"], name="capability mount directory mode")
    file_mode = strict_int(document["canary_file_mode"], name="capability canary file mode")
    archive_mode = strict_int(document["canary_archive_mode"], name="capability canary archive mode")
    require(default_user, "capability must use the image default user")
    require(cleanup, "capability container cleanup must be verified")
    require(mount_mode == 493, "capability mount directory mode must be decimal 493")
    require(file_mode == 438, "capability canary file mode must be decimal 438")
    require(archive_mode == 384, "capability canary archive mode must be decimal 384")
    sha256(document["canary_sha256"], name="capability canary SHA-256")
    container_id_value(document["container_id"], name="capability container ID")
    sha256(document["stdout_sha256"], name="capability stdout SHA-256")
    sha256(document["stderr_sha256"], name="capability stderr SHA-256")
    return cast(JsonObject, document)


def prerequisite_document(value: PrerequisiteResults) -> JsonObject:
    require_type(type(value) is PrerequisiteResults, "prerequisite value must be PrerequisiteResults")
    return {
        "schema_version": value.schema_version,
        "created_utc": value.created_utc,
        "study_id": value.study_id,
        "git_commit": value.git_commit,
        "git_tree_clean": value.git_tree_clean,
        "url": value.url,
        "tools": thaw_json(value.tools),
        "images": thaw_json(value.images),
        "capability": thaw_json(value.capability),
        "config_sha256": thaw_json(value.config_sha256),
        "commands": [thaw_json(command) for command in value.commands],
    }


def validate_prerequisite_document(
    document: JsonObject, *, repository_root: Path, expected_capability_argv: tuple[str, ...] | None = None
) -> PrerequisiteResults:
    root = exact_object(document, PREREQUISITE_ROOT_KEYS, name="prerequisite root")
    schema_version = strict_int(root["schema_version"], name="prerequisite schema version")
    require(schema_version == 1, "prerequisite schema version must be exactly 1")
    created = utc_timestamp(root["created_utc"], name="prerequisite creation time")
    study_id = validate_study_id(strict_string(root["study_id"], name="study ID"))
    git_commit = git_commit_value(root["git_commit"])
    tree_clean = strict_bool(root["git_tree_clean"], name="Git tree clean")
    require(tree_clean, "prerequisite Git tree must be clean")
    url = validate_endpoint_url(strict_string(root["url"], name="operator URL"))
    tools = validate_tools(root["tools"])
    images = validate_images(root["images"])
    capability = validate_capability(
        root["capability"],
        repository_root=repository_root,
        study_id=study_id,
        url=url,
        expected_capability_argv=expected_capability_argv,
    )
    hashes = profile_hashes(root["config_sha256"])
    commands = strict_list(root["commands"], name="prerequisite commands")
    require(len(commands) == 2, "prerequisite commands must contain docker_matrix then internet_smoke")
    validated_commands = (
        validate_command(commands[0], expected_kind="docker_matrix", study_id=study_id, url=url),
        validate_command(commands[1], expected_kind="internet_smoke", study_id=study_id, url=url),
    )
    return PrerequisiteResults(
        schema_version=schema_version,
        created_utc=created,
        study_id=study_id,
        git_commit=git_commit,
        git_tree_clean=tree_clean,
        url=url,
        tools=freeze_object(tools),
        images=freeze_object(images),
        capability=freeze_object(capability),
        config_sha256=freeze_object(hashes),
        commands=(freeze_object(validated_commands[0]), freeze_object(validated_commands[1])),
    )


def render_prerequisite_results(value: PrerequisiteResults) -> bytes:
    document = prerequisite_document(value)
    validated = validate_prerequisite_document(document, repository_root=REPOSITORY_ROOT)
    return canonical_json(prerequisite_document(validated))


def parse_prerequisite_results(content: bytes, *, repository_root: Path) -> PrerequisiteResults:
    document = load_json(content)
    result = validate_prerequisite_document(document, repository_root=repository_root)
    if canonical_json(prerequisite_document(result)) != content:
        raise ValueError("prerequisite JSON must use canonical sorted readable encoding with one trailing newline")
    return result


def parse_preserved_pre_user_agent_r6_predecessor(
    content: bytes, *, repository_root: Path, runner: CommandRunner
) -> PrerequisiteResults:
    """Validate the one retained raw r6 document without relaxing the public prerequisite codec."""
    try:
        require(
            identify_bytes(content).as_dict() == PRESERVED_PRE_USER_AGENT_R6_RAW_IDENTITY,
            "preserved pre-User-Agent r6 predecessor must equal its exact raw canonical identity",
        )
        document = load_json(content)
        result = validate_prerequisite_document(
            document,
            repository_root=repository_root,
            expected_capability_argv=_pre_user_agent_capability_argv(
                PRESERVED_PRE_USER_AGENT_R6_STUDY_ID, PRESERVED_PRE_USER_AGENT_R6_URL
            ),
        )
        require(
            render_json_line(document) == content,
            "preserved pre-User-Agent r6 predecessor must use its historical compact canonical JSON",
        )
        require(
            result.study_id == PRESERVED_PRE_USER_AGENT_R6_STUDY_ID
            and result.url == PRESERVED_PRE_USER_AGENT_R6_URL
            and (result.git_commit == PRESERVED_PRE_USER_AGENT_R6_COMMIT)
            and result.git_tree_clean,
            "preserved pre-User-Agent r6 predecessor must match its retained study identity and source commit",
        )
        tree_result = runner(
            ("git", "rev-parse", f"{PRESERVED_PRE_USER_AGENT_R6_COMMIT}^{{tree}}"),
            cwd=repository_root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        )
        require(tree_result.returncode == 0, "preserved pre-User-Agent r6 source tree could not be resolved")
        require(
            tree_result.stdout.decode("ascii", errors="strict").strip() == PRESERVED_PRE_USER_AGENT_R6_TREE,
            "preserved pre-User-Agent r6 source tree must match its retained commit tree",
        )
        marker = collection_attempt_root(repository_root, result.study_id) / "prerequisites-success.json"
        marker_content = read_regular_prerequisite_rotation_target(marker, name="successful prerequisite marker")
        require(
            identify_bytes(marker_content).as_dict() == PRESERVED_PRE_USER_AGENT_R6_MARKER_IDENTITY,
            "preserved pre-User-Agent r6 predecessor must match its exact success-marker identity",
        )
        require_successful_prerequisite_marker_content(
            marker_content, study_id=result.study_id, url=result.url, prerequisite_content=content
        )
        evidence = (
            repository_root
            / "examples"
            / "validation_study"
            / ".study-work"
            / "evidence"
            / result.study_id
            / "00-prerequisites"
        )
        try:
            mode = evidence.lstat().st_mode
            require(
                stat.S_ISDIR(mode) and (not stat.S_ISLNK(mode)),
                "preserved pre-User-Agent r6 evidence directory must be a regular directory",
            )
            names = tuple(sorted(path.name for path in evidence.iterdir()))
        except OSError as error:
            raise ValueError(f"could not inspect preserved pre-User-Agent r6 evidence {evidence}: {error}") from error
        expected_names = tuple(name for name, _size, _sha256 in PRESERVED_PRE_USER_AGENT_R6_EVIDENCE_IDENTITIES)
        require(names == expected_names, "preserved pre-User-Agent r6 evidence inventory must match exactly")
        for name, size, sha256 in PRESERVED_PRE_USER_AGENT_R6_EVIDENCE_IDENTITIES:
            retained = read_regular_prerequisite_rotation_target(
                evidence / name, name=f"preserved pre-User-Agent r6 evidence {name}"
            )
            require(
                identify_bytes(retained).as_dict() == {"sha256": sha256, "size": size},
                f"preserved pre-User-Agent r6 evidence {name} must match its retained identity",
            )
        return result
    except (OSError, TypeError, UnicodeError, ValueError) as error:
        raise ValueError("preserved pre-User-Agent r6 predecessor is not the exact retained evidence") from error


def render_successful_prerequisite_marker(*, study_id: str, url: str, prerequisite_content: bytes) -> bytes:
    """Render the sole success-visible record for one canonical prerequisite document."""
    return canonical_json(
        cast(
            JsonObject,
            {
                "phase": "prerequisites",
                "prerequisites_identity": candidate_identity(prerequisite_content),
                "study_id": study_id,
                "url": url,
            },
        )
    )


def require_successful_prerequisite_marker_content(
    content: bytes, *, study_id: str, url: str, prerequisite_content: bytes
) -> None:
    """Validate an in-memory success marker against the exact prerequisite bytes it authorizes."""
    document = exact_object(
        load_json(content),
        ("phase", "prerequisites_identity", "study_id", "url"),
        name="successful prerequisite marker",
    )
    current_canonical = canonical_json(cast(JsonObject, document)) == content
    preserved_legacy = (
        document["study_id"] == PRESERVED_PRE_USER_AGENT_R6_STUDY_ID
        and identify_bytes(content).as_dict() == PRESERVED_PRE_USER_AGENT_R6_MARKER_IDENTITY
    )
    require(
        current_canonical or preserved_legacy,
        "successful prerequisite marker must be current canonical JSON or the exact preserved legacy marker",
    )
    require(
        document["phase"] == "prerequisites" and document["study_id"] == study_id and (document["url"] == url),
        "collection requires a matching successful prerequisite marker",
    )
    require(
        retained_identity(document["prerequisites_identity"], name="successful prerequisite marker identity")
        == candidate_identity(prerequisite_content),
        "collection requires a matching successful prerequisite marker",
    )
