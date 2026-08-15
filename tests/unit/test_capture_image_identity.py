from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from trafficlab.docker_cli import (
    CaptureImageLockError,
    ImageIdentity,
    load_capture_image_lock,
    parse_image_inspect,
    validate_capture_dockerfile,
)
from trafficlab.preflight import capture_environment_identity

_BASE_DIGEST = "sha256:" + ("a" * 64)
_IMAGE_ID = "sha256:" + ("b" * 64)


def _lock_payload() -> dict[str, Any]:
    return {
        "base_digest": _BASE_DIGEST,
        "base_reference": "debian:bookworm-20260803-slim",
        "capture_tool_version": "4.0.17",
        "debian_snapshot": "20260803T203533Z",
        "direct_packages": {
            "ca-certificates": "20230311+deb12u1",
            "curl": "7.88.1-10+deb12u15",
            "wireshark-common": "4.0.17-0+deb12u3",
        },
        "expected_capture_image_id": _IMAGE_ID,
    }


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _write_lock(path: Path, payload: dict[str, Any] | None = None) -> None:
    path.write_bytes(_canonical_json(payload or _lock_payload()))


_INVALID_LOCK_MUTATIONS: list[Callable[[dict[str, Any]], object]] = [
    lambda payload: payload.pop("base_digest"),
    lambda payload: payload.__setitem__("unexpected", True),
    lambda payload: payload.__setitem__("base_digest", "sha256:not-a-digest"),
    lambda payload: payload.__setitem__("expected_capture_image_id", "sha256:not-an-id"),
    lambda payload: payload.__setitem__("base_reference", "debian@sha256:bad"),
    lambda payload: payload.__setitem__("debian_snapshot", "2026-08-03"),
    lambda payload: payload.__setitem__("direct_packages", {"curl": ""}),
    lambda payload: payload.__setitem__("capture_tool_version", ""),
]

_INVALID_DOCKERFILE_MUTATIONS: list[tuple[Callable[[str], str], str]] = [
    (lambda text: text.replace(f"@{_BASE_DIGEST}", "", 1), "digest"),
    (
        lambda text: text.replace("snapshot.debian.org", "deb.debian.org", 1),
        "snapshot",
    ),
    (lambda text: text.replace("curl=7.88.1-10+deb12u15", "curl", 1), "curl"),
    (
        lambda text: text.replace("20260803T203533Z", "20260802T000000Z", 1),
        "snapshot",
    ),
    (
        lambda text: (
            text
            + "\nRUN echo 'deb http://snapshot.debian.org/archive/debian/"
            + "20260802T000000Z/ bookworm main' >> /etc/apt/sources.list\n"
        ),
        "exactly match",
    ),
    (
        lambda text: text + "\nRUN apt-get install -y --no-install-recommends " + "curl=7.88.1-10+deb12u15\n",
        "exactly match",
    ),
]


def _dockerfile() -> str:
    payload = _lock_payload()
    packages = payload["direct_packages"]
    return f"""FROM {payload["base_reference"]}@{payload["base_digest"]}

RUN printf '%s\\n' \\
    'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/{payload["debian_snapshot"]}/ bookworm main' \\
    'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/{payload["debian_snapshot"]}/ bookworm-security main' \\
    > /etc/apt/sources.list \\
 && rm -f /etc/apt/sources.list.d/debian.sources \\
 && apt-get -o Acquire::Check-Valid-Until=false update \\
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
   ca-certificates={packages["ca-certificates"]} \\
   curl={packages["curl"]} \\
   wireshark-common={packages["wireshark-common"]} \\
 && rm -rf /var/lib/apt/lists/*

COPY --chmod=0755 capture.sh /usr/local/bin/trafficlab-capture

ENTRYPOINT ["/usr/local/bin/trafficlab-capture"]
"""


def test_capture_image_lock_requires_canonical_valid_content(tmp_path: Path) -> None:
    lock_path = tmp_path / "image-lock.json"
    _write_lock(lock_path)

    lock = load_capture_image_lock(lock_path)

    assert lock.base_reference == "debian:bookworm-20260803-slim"
    assert lock.base_digest == _BASE_DIGEST
    assert lock.debian_snapshot == "20260803T203533Z"
    assert dict(lock.direct_packages) == _lock_payload()["direct_packages"]
    assert lock.capture_tool_version == "4.0.17"
    assert lock.expected_capture_image_id == _IMAGE_ID


@pytest.mark.parametrize(
    "mutate",
    _INVALID_LOCK_MUTATIONS,
)
def test_capture_image_lock_rejects_invalid_fields(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], object],
) -> None:
    payload = _lock_payload()
    mutate(payload)
    lock_path = tmp_path / "image-lock.json"
    _write_lock(lock_path, payload)

    with pytest.raises(CaptureImageLockError):
        load_capture_image_lock(lock_path)


def test_capture_image_lock_rejects_noncanonical_json(tmp_path: Path) -> None:
    lock_path = tmp_path / "image-lock.json"
    lock_path.write_text(json.dumps(_lock_payload(), indent=2) + "\n")

    with pytest.raises(CaptureImageLockError, match="canonical"):
        load_capture_image_lock(lock_path)


def test_capture_image_lock_reports_read_failure(tmp_path: Path) -> None:
    lock_path = tmp_path / "missing-image-lock.json"

    with pytest.raises(CaptureImageLockError, match="cannot read"):
        load_capture_image_lock(lock_path)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"{",
        b'{"base_digest":"first","base_digest":"second"}\n',
    ],
)
def test_capture_image_lock_rejects_invalid_json(tmp_path: Path, raw: bytes) -> None:
    lock_path = tmp_path / "image-lock.json"
    lock_path.write_bytes(raw)

    with pytest.raises(CaptureImageLockError, match="invalid capture image lock JSON"):
        load_capture_image_lock(lock_path)


def test_capture_image_lock_rejects_non_object_document(tmp_path: Path) -> None:
    lock_path = tmp_path / "image-lock.json"
    lock_path.write_bytes(b"[]\n")

    with pytest.raises(CaptureImageLockError, match="JSON object"):
        load_capture_image_lock(lock_path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("base_reference", "debian", "explicit tag"),
        ("direct_packages", [], "JSON object"),
        ("capture_tool_version", "4.0", "three-component"),
    ],
)
def test_capture_image_lock_rejects_invalid_structured_values(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _lock_payload()
    payload[field] = value
    lock_path = tmp_path / "image-lock.json"
    _write_lock(lock_path, payload)

    with pytest.raises(CaptureImageLockError, match=message):
        load_capture_image_lock(lock_path)


@pytest.mark.parametrize("version", ["*", "1.2.*", "1.2?", "1.[0-9]"])
def test_capture_image_lock_rejects_apt_version_patterns(
    tmp_path: Path,
    version: str,
) -> None:
    payload = _lock_payload()
    packages = cast(dict[str, object], payload["direct_packages"])
    packages["curl"] = version
    lock_path = tmp_path / "image-lock.json"
    _write_lock(lock_path, payload)

    with pytest.raises(CaptureImageLockError, match="exact Debian version"):
        load_capture_image_lock(lock_path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    _INVALID_DOCKERFILE_MUTATIONS,
)
def test_capture_dockerfile_rejects_unlocked_inputs(
    tmp_path: Path,
    mutate: Callable[[str], str],
    message: str,
) -> None:
    lock_path = tmp_path / "image-lock.json"
    _write_lock(lock_path)
    lock = load_capture_image_lock(lock_path)

    with pytest.raises(CaptureImageLockError, match=message):
        validate_capture_dockerfile(mutate(_dockerfile()), lock)


def test_capture_dockerfile_accepts_locked_inputs(tmp_path: Path) -> None:
    lock_path = tmp_path / "image-lock.json"
    _write_lock(lock_path)

    validate_capture_dockerfile(_dockerfile(), load_capture_image_lock(lock_path))


def test_parse_image_inspect_returns_requested_reference_and_content_id() -> None:
    raw = json.dumps(
        [
            {
                "Id": _IMAGE_ID,
                "RepoDigests": [],
                "RepoTags": ["trafficlab-capture:task9"],
            }
        ]
    )

    identity = parse_image_inspect("trafficlab-capture:task9", raw)

    assert identity.reference == "trafficlab-capture:task9"
    assert identity.content_id == _IMAGE_ID


@pytest.mark.parametrize(
    ("reference", "raw", "message"),
    [
        ("target:test", "[]", "one image"),
        (
            "target:test",
            json.dumps([{"Id": "sha256:bad", "RepoTags": ["target:test"]}]),
            "content ID",
        ),
        (
            "target:test",
            json.dumps([{"Id": _IMAGE_ID, "RepoTags": ["other:test"]}]),
            "reference",
        ),
    ],
)
def test_parse_image_inspect_rejects_malformed_or_mismatched_identity(
    reference: str,
    raw: str,
    message: str,
) -> None:
    with pytest.raises(CaptureImageLockError, match=message):
        parse_image_inspect(reference, raw)


def test_capture_environment_identity_records_both_resolved_images(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "image-lock.json"
    _write_lock(lock_path)
    lock = load_capture_image_lock(lock_path)
    target_id = "sha256:" + ("c" * 64)

    identity = capture_environment_identity(
        target=ImageIdentity("trafficlab-target:test", target_id),
        capture=ImageIdentity("trafficlab-capture:test", _IMAGE_ID),
        lock=lock,
        host_architecture="amd64",
    )

    assert identity.host_architecture == "linux/amd64"
    assert identity.target_reference == "trafficlab-target:test"
    assert identity.target_content_id == target_id
    assert identity.capture_reference == "trafficlab-capture:test"
    assert identity.capture_content_id == _IMAGE_ID
    assert identity.capture_tool_version == "4.0.17"


def test_capture_environment_identity_rejects_mismatch_without_refreshing_lock(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "image-lock.json"
    _write_lock(lock_path)
    before = lock_path.read_bytes()
    lock = load_capture_image_lock(lock_path)

    with pytest.raises(CaptureImageLockError, match="expected capture image"):
        capture_environment_identity(
            target=ImageIdentity("trafficlab-target:test", "sha256:" + ("c" * 64)),
            capture=ImageIdentity("trafficlab-capture:test", "sha256:" + ("d" * 64)),
            lock=lock,
            host_architecture="amd64",
        )

    assert lock_path.read_bytes() == before


@pytest.mark.parametrize("host_architecture", ["aarch64", "arm64", "", "unknown"])
def test_capture_environment_identity_rejects_unsupported_platform(
    tmp_path: Path,
    host_architecture: str,
) -> None:
    lock_path = tmp_path / "image-lock.json"
    _write_lock(lock_path)
    lock = load_capture_image_lock(lock_path)

    with pytest.raises(CaptureImageLockError, match="linux/amd64"):
        capture_environment_identity(
            target=ImageIdentity("trafficlab-target:test", "sha256:" + ("c" * 64)),
            capture=ImageIdentity("trafficlab-capture:test", _IMAGE_ID),
            lock=lock,
            host_architecture=host_architecture,
        )
