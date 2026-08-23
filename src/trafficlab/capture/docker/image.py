"""Reproducible capture-image and Docker platform authority."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, cast

from trafficlab.capture.docker.types import reject_duplicate_json_keys
from trafficlab.common.json import render_json_document

_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")

_SNAPSHOT_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}Z\Z")

_CAPTURE_TOOL_VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+){2}\Z")

_DEBIAN_VERSION_PATTERN = re.compile(
    r"(?:[0-9]+:)?(?:[0-9][A-Za-z0-9.+:~]*|"
    r"[0-9][A-Za-z0-9.+:~-]*-[A-Za-z0-9.+~]+)\Z"
)

_CAPTURE_IMAGE_LOCK_FIELDS = frozenset(
    {
        "base_digest",
        "base_reference",
        "capture_tool_version",
        "debian_snapshot",
        "direct_packages",
        "expected_capture_image_id",
    }
)

_CAPTURE_DIRECT_PACKAGES = frozenset({"ca-certificates", "curl", "wireshark-common"})


class CaptureImageLockError(ValueError):
    """The checked capture-image contract is malformed or incompatible."""


CapturePlatform = Literal["linux/amd64"]

CAPTURE_PLATFORM: Final[CapturePlatform] = "linux/amd64"

_CAPTURE_HOST_ARCHITECTURES = frozenset({"amd64", "x86_64", CAPTURE_PLATFORM})


def cold_capture_build_argv(tag: object, iidfile: object) -> tuple[str, ...]:
    """Return the one reproducible, cold capture-image build invocation.

    The caller supplies a project-scoped tag and an exclusive IID destination;
    the checked Dockerfile and image lock supply all remaining inputs.
    """

    if not isinstance(tag, str) or not tag:
        raise ValueError("capture image tag must be a nonempty string")
    if not isinstance(iidfile, Path):
        raise TypeError("iidfile must be a pathlib.Path")
    return (
        "docker",
        "build",
        "--pull",
        "--no-cache",
        "--provenance=false",
        "--platform",
        CAPTURE_PLATFORM,
        "--output",
        "type=image,rewrite-timestamp=true,unpack=false",
        "--tag",
        tag,
        "--iidfile",
        str(iidfile),
        "docker/capture",
    )


def normalize_capture_platform(host_architecture: str) -> CapturePlatform:
    """Map supported host architecture names to the one capture platform."""

    if host_architecture.casefold() in _CAPTURE_HOST_ARCHITECTURES:
        return CAPTURE_PLATFORM
    raise CaptureImageLockError(
        f"unsupported capture host architecture {host_architecture!r}; required platform is {CAPTURE_PLATFORM}"
    )


def validate_capture_platform(
    operating_system: str,
    architecture: str,
    *,
    source: str,
) -> CapturePlatform:
    """Require one Docker execution or image platform to be linux/amd64."""

    if operating_system.casefold() != "linux":
        raise CaptureImageLockError(
            f"unsupported {source} platform {operating_system!r}/{architecture!r}; "
            f"required platform is {CAPTURE_PLATFORM}"
        )
    try:
        return normalize_capture_platform(architecture)
    except CaptureImageLockError as error:
        raise CaptureImageLockError(
            f"unsupported {source} platform {operating_system!r}/{architecture!r}; "
            f"required platform is {CAPTURE_PLATFORM}"
        ) from error


def parse_docker_info_platform(stdout: str) -> CapturePlatform:
    """Parse the remote Docker daemon platform from formatted Docker info."""

    try:
        parsed = cast(object, json.loads(stdout))
    except json.JSONDecodeError as error:
        raise CaptureImageLockError(f"invalid Docker info JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise CaptureImageLockError("Docker info JSON must be an object")
    payload = cast(dict[str, object], parsed)
    operating_system = payload.get("OSType")
    if not isinstance(operating_system, str) or not operating_system:
        raise CaptureImageLockError(
            f"Docker info has an invalid operating system; required platform is {CAPTURE_PLATFORM}"
        )
    architecture = payload.get("Architecture")
    if not isinstance(architecture, str) or not architecture:
        raise CaptureImageLockError(f"Docker info has an invalid architecture; required platform is {CAPTURE_PLATFORM}")
    return validate_capture_platform(operating_system, architecture, source="Docker daemon")


@dataclass(frozen=True, slots=True)
class CaptureImageLock:
    """Immutable inputs and expected output for the capture image."""

    base_reference: str
    base_digest: str
    debian_snapshot: str
    source_date_epoch: int
    direct_packages: Mapping[str, str]
    capture_tool_version: str
    expected_capture_image_id: str


@dataclass(frozen=True, slots=True)
class ImageIdentity:
    """A requested image reference and its resolved Docker content ID."""

    reference: str
    content_id: str
    operating_system: str
    architecture: str


def _canonical_lock_bytes(payload: Mapping[str, object]) -> bytes:
    return render_json_document(payload, ensure_ascii=True)


def _lock_string(payload: Mapping[str, object], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str) or not value:
        raise CaptureImageLockError(f"image-lock field {field!r} must be a string")
    return value


def load_capture_image_lock(path: Path) -> CaptureImageLock:
    """Load a strict checked lock without ever creating or refreshing it."""

    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CaptureImageLockError(f"cannot read capture image lock {path}: {error}") from error
    try:
        parsed = cast(
            object,
            json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_json_keys),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CaptureImageLockError(f"invalid capture image lock JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise CaptureImageLockError("capture image lock must be a JSON object")
    payload = cast(dict[str, object], parsed)
    fields = frozenset(payload)
    if fields != _CAPTURE_IMAGE_LOCK_FIELDS:
        missing = sorted(_CAPTURE_IMAGE_LOCK_FIELDS - fields)
        unknown = sorted(fields - _CAPTURE_IMAGE_LOCK_FIELDS)
        raise CaptureImageLockError(f"invalid image-lock fields; missing={missing!r}, unknown={unknown!r}")
    if raw != _canonical_lock_bytes(payload):
        raise CaptureImageLockError("capture image lock is not canonical JSON")

    base_reference = _lock_string(payload, "base_reference")
    if "@" in base_reference or any(character.isspace() for character in base_reference):
        raise CaptureImageLockError("base_reference must be a tag-only OCI reference")
    if ":" not in base_reference.rsplit("/", maxsplit=1)[-1]:
        raise CaptureImageLockError("base_reference must include an explicit tag")
    base_digest = _lock_string(payload, "base_digest")
    if _SHA256_PATTERN.fullmatch(base_digest) is None:
        raise CaptureImageLockError("base_digest must be a lowercase sha256 digest")
    expected_image_id = _lock_string(payload, "expected_capture_image_id")
    if _SHA256_PATTERN.fullmatch(expected_image_id) is None:
        raise CaptureImageLockError("expected_capture_image_id must be a lowercase sha256 content ID")

    snapshot = _lock_string(payload, "debian_snapshot")
    if _SNAPSHOT_PATTERN.fullmatch(snapshot) is None:
        raise CaptureImageLockError("debian_snapshot must use the YYYYMMDDTHHMMSSZ form")
    try:
        source_date_epoch = int(datetime.strptime(snapshot, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).timestamp())
    except ValueError as error:
        raise CaptureImageLockError("debian_snapshot must be a valid UTC timestamp") from error

    packages_value = payload["direct_packages"]
    if not isinstance(packages_value, dict):
        raise CaptureImageLockError("direct_packages must be a JSON object")
    packages = cast(dict[str, object], packages_value)
    if frozenset(packages) != _CAPTURE_DIRECT_PACKAGES:
        raise CaptureImageLockError("direct_packages must contain exactly ca-certificates, curl, and wireshark-common")
    package_versions: dict[str, str] = {}
    for package_name in sorted(packages):
        version = packages[package_name]
        if not isinstance(version, str) or _DEBIAN_VERSION_PATTERN.fullmatch(version) is None:
            raise CaptureImageLockError(f"direct package {package_name!r} must have one exact Debian version")
        package_versions[package_name] = version

    capture_tool_version = _lock_string(payload, "capture_tool_version")
    if _CAPTURE_TOOL_VERSION_PATTERN.fullmatch(capture_tool_version) is None:
        raise CaptureImageLockError("capture_tool_version must be a three-component numeric version")
    return CaptureImageLock(
        base_reference=base_reference,
        base_digest=base_digest,
        debian_snapshot=snapshot,
        source_date_epoch=source_date_epoch,
        direct_packages=MappingProxyType(package_versions),
        capture_tool_version=capture_tool_version,
        expected_capture_image_id=expected_image_id,
    )


def validate_capture_dockerfile(
    dockerfile: str,
    lock: CaptureImageLock,
) -> None:
    """Require the capture Dockerfile to consume only inputs named by the lock."""

    expected = f"""ARG SOURCE_DATE_EPOCH={lock.source_date_epoch}
FROM {lock.base_reference}@{lock.base_digest}

RUN printf '%s\\n' \\
    'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/{lock.debian_snapshot}/ bookworm main' \\
    'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/{lock.debian_snapshot}/ bookworm-security main' \\
    > /etc/apt/sources.list \\
 && rm -f /etc/apt/sources.list.d/debian.sources \\
 && apt-get -o Acquire::Check-Valid-Until=false update \\
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
   ca-certificates={lock.direct_packages["ca-certificates"]} \\
   curl={lock.direct_packages["curl"]} \\
   wireshark-common={lock.direct_packages["wireshark-common"]} \\
 && rm -rf /var/lib/apt/lists/* /var/cache/* /var/log/* /tmp/* /var/tmp/*

COPY --chmod=0755 capture.sh /usr/local/bin/trafficlab-capture

ENTRYPOINT ["/usr/local/bin/trafficlab-capture"]
"""
    if dockerfile != expected:
        raise CaptureImageLockError(
            "capture Dockerfile must exactly match the locked base digest, snapshot-derived "
            "SOURCE_DATE_EPOCH, snapshot APT sources, apt operations, and package versions including curl"
        )


def parse_image_inspect(reference: str, stdout: str) -> ImageIdentity:
    """Parse one Docker image-inspect record and bind it to the requested ref."""

    try:
        payload = cast(object, json.loads(stdout))
    except json.JSONDecodeError as error:
        raise CaptureImageLockError(f"invalid Docker image inspect JSON: {error}") from error
    if not isinstance(payload, list):
        raise CaptureImageLockError("Docker image inspect must return exactly one image")
    records = cast(list[object], payload)
    if len(records) != 1:
        raise CaptureImageLockError("Docker image inspect must return exactly one image")
    record = records[0]
    if not isinstance(record, dict):
        raise CaptureImageLockError("Docker image inspect record must be an object")
    typed_record = cast(dict[str, object], record)
    content_id = typed_record.get("Id")
    if not isinstance(content_id, str) or _SHA256_PATTERN.fullmatch(content_id) is None:
        raise CaptureImageLockError("Docker image inspect has an invalid content ID")
    repo_tags_value = typed_record.get("RepoTags", [])
    repo_digests_value = typed_record.get("RepoDigests", [])
    if not isinstance(repo_tags_value, list):
        raise CaptureImageLockError("Docker image inspect has invalid RepoTags")
    repo_tags = cast(list[object], repo_tags_value)
    if not all(isinstance(item, str) for item in repo_tags):
        raise CaptureImageLockError("Docker image inspect has invalid RepoTags")
    if not isinstance(repo_digests_value, list):
        raise CaptureImageLockError("Docker image inspect has invalid RepoDigests")
    repo_digests = cast(list[object], repo_digests_value)
    if not all(isinstance(item, str) for item in repo_digests):
        raise CaptureImageLockError("Docker image inspect has invalid RepoDigests")

    if reference.startswith("sha256:"):
        reference_matches = reference == content_id
    elif "@sha256:" in reference:
        reference_matches = reference in repo_digests
    else:
        reference_matches = reference in repo_tags
    if not reference_matches:
        raise CaptureImageLockError(f"Docker image inspect does not match requested reference {reference!r}")
    operating_system = typed_record.get("Os")
    if not isinstance(operating_system, str) or not operating_system:
        raise CaptureImageLockError("Docker image inspect has an invalid operating system")
    architecture = typed_record.get("Architecture")
    if not isinstance(architecture, str) or not architecture:
        raise CaptureImageLockError("Docker image inspect has an invalid architecture")
    return ImageIdentity(
        reference=reference,
        content_id=content_id,
        operating_system=operating_system,
        architecture=architecture,
    )
