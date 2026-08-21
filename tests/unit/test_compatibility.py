"""Deterministic content identity and ordered compatibility checks."""

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import trafficlab.common.compatibility as compatibility
from trafficlab.common.compatibility import ContentIdentity, identify_bytes, identify_file, require_compatible
from trafficlab.common.errors import TrafficlabError


def test_identify_file_returns_size_and_sha256_of_exact_bytes(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    content = b"trafficlab\x00portable\n"
    artifact.write_bytes(content)

    assert identify_file(artifact) == ContentIdentity(
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def test_identify_directory_returns_canonical_tree_identity(tmp_path: Path) -> None:
    root = tmp_path / "input"
    root.mkdir()
    (root / "empty").mkdir()
    (root / "payload.bin").write_bytes(b"abc")
    canonical_inventory = (
        b'[{"kind":"directory","path":"empty"},'
        b'{"kind":"file","path":"payload.bin",'
        b'"sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",'
        b'"size":3}]'
    )

    assert compatibility.identify_directory(root) == ContentIdentity(
        size=3,
        sha256=hashlib.sha256(canonical_inventory).hexdigest(),
    )


def test_identify_directory_changes_with_file_bytes_paths_and_empty_directories(tmp_path: Path) -> None:
    root = tmp_path / "input"
    root.mkdir()
    payload = root / "request.txt"
    payload.write_bytes(b"v1")
    original = compatibility.identify_directory(root)

    payload.write_bytes(b"v2")
    changed_bytes = compatibility.identify_directory(root)
    payload.rename(root / "renamed.txt")
    changed_path = compatibility.identify_directory(root)
    (root / "empty").mkdir()
    changed_inventory = compatibility.identify_directory(root)

    assert len({original, changed_bytes, changed_path, changed_inventory}) == 4


@pytest.mark.parametrize("entry_kind", ("symlink", "fifo"))
def test_identify_directory_rejects_nonregular_entries(tmp_path: Path, entry_kind: str) -> None:
    root = tmp_path / "input"
    root.mkdir()
    entry = root / "foreign"
    if entry_kind == "symlink":
        entry.symlink_to(root / "missing")
    else:
        os.mkfifo(entry)

    with pytest.raises(TrafficlabError, match="directory identity.*regular files and directories"):
        compatibility.identify_directory(root)


def test_identify_directory_rejects_an_inventory_change_during_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "input"
    root.mkdir()
    payload = root / "request.txt"
    payload.write_bytes(b"v1")
    original_identify_file = compatibility.identify_file

    def mutate_during_hash(path: Path) -> ContentIdentity:
        identity = original_identify_file(path)
        (root / "added.txt").write_bytes(b"late")
        return identity

    monkeypatch.setattr(compatibility, "identify_file", mutate_during_hash)

    with pytest.raises(TrafficlabError, match="directory changed while.*identity"):
        compatibility.identify_directory(root)


def test_content_identity_has_one_strict_canonical_codec() -> None:
    identity = identify_bytes(b"identity bytes")

    assert identity.as_dict() == {
        "size": 14,
        "sha256": hashlib.sha256(b"identity bytes").hexdigest(),
    }
    assert ContentIdentity.from_dict(identity.as_dict(), name="source artifact") == identity


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], "source artifact identity must be an object"),
        ({"size": 1}, "source artifact identity must contain exactly"),
        ({"size": True, "sha256": "a" * 64}, "source artifact identity size"),
        ({"size": -1, "sha256": "a" * 64}, "source artifact identity size"),
        ({"size": 1, "sha256": 1}, "source artifact identity sha256"),
        ({"size": 1, "sha256": "A" * 64}, "source artifact identity sha256"),
    ],
)
def test_content_identity_rejects_malformed_documents(value: object, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        ContentIdentity.from_dict(value, name="source artifact")


def test_content_identity_and_identify_bytes_reject_invalid_direct_values() -> None:
    with pytest.raises(TypeError, match="size must be a nonnegative integer"):
        ContentIdentity(size=True, sha256="a" * 64)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="size must be a nonnegative integer"):
        ContentIdentity(size=-1, sha256="a" * 64)
    with pytest.raises(TypeError, match="sha256 must be a lowercase"):
        ContentIdentity(size=1, sha256=cast(Any, 1))
    with pytest.raises(ValueError, match="sha256 must be a lowercase"):
        ContentIdentity(size=1, sha256="short")
    with pytest.raises(TypeError, match="content must be bytes"):
        identify_bytes(cast(Any, bytearray(b"bytes")))


def test_identify_file_rejects_a_file_that_changes_during_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"stable bytes")
    actual = artifact.stat()
    changed = SimpleNamespace(
        st_ctime_ns=actual.st_ctime_ns + 1,
        st_dev=actual.st_dev,
        st_ino=actual.st_ino,
        st_mode=actual.st_mode,
        st_mtime_ns=actual.st_mtime_ns,
        st_size=actual.st_size,
    )
    calls = 0

    def changing_fstat(file_descriptor: int) -> object:
        nonlocal calls
        del file_descriptor
        calls += 1
        return actual if calls == 1 else changed

    monkeypatch.setattr(compatibility.os, "fstat", changing_fstat)

    with pytest.raises(TrafficlabError, match="changed while.*content identity") as error:
        identify_file(artifact)

    assert "concurrent writes" in error.value.corrective_action


def test_identify_file_translates_unreadable_and_non_path_inputs(tmp_path: Path) -> None:
    missing = tmp_path / "missing.bin"

    with pytest.raises(TrafficlabError, match=r"could not identify file.*missing\.bin") as error:
        identify_file(missing)
    assert "readable regular file" in error.value.corrective_action

    with pytest.raises(TypeError, match="path must be a Path"):
        identify_file(cast(Any, str(missing)))

    with pytest.raises(TrafficlabError, match="non-regular file") as non_regular:
        identify_file(Path(os.devnull))
    assert "regular file" in non_regular.value.corrective_action


def test_require_compatible_accepts_exact_type_sensitive_records() -> None:
    expected: dict[str, object] = {
        "schema_version": 2,
        "source": ContentIdentity(size=12, sha256="a" * 64),
        "operators": ("tournament", "uniform"),
    }

    require_compatible(expected, dict(expected))


def test_require_compatible_names_the_first_declared_mismatch() -> None:
    expected: dict[str, object] = {
        "host_architecture": "x86_64",
        "capture_image_id": "sha256:" + "a" * 64,
    }
    actual: dict[str, object] = {
        "host_architecture": "aarch64",
        "capture_image_id": "sha256:" + "b" * 64,
    }

    with pytest.raises(TrafficlabError, match="host_architecture") as error:
        require_compatible(expected, actual)

    assert "capture_image_id" not in str(error.value)
    assert "start a fresh stage" in error.value.corrective_action


@pytest.mark.parametrize(
    ("expected", "actual", "message"),
    [
        ({"schema_version": 2}, {}, "missing field 'schema_version'"),
        ({"seed": 1}, {"seed": True}, "field 'seed'"),
        ({"schema_version": 2}, {"schema_version": 2, "extra": 1}, "unexpected field 'extra'"),
    ],
)
def test_require_compatible_rejects_missing_type_changed_and_unexpected_fields(
    expected: dict[str, object], actual: dict[str, object], message: str
) -> None:
    with pytest.raises(TrafficlabError, match=message):
        require_compatible(expected, actual)


@pytest.mark.parametrize("value", [None, [], "record"])
def test_require_compatible_requires_mappings(value: object) -> None:
    with pytest.raises(TypeError, match="expected must be a mapping"):
        require_compatible(cast(Any, value), {})
    with pytest.raises(TypeError, match="actual must be a mapping"):
        require_compatible({}, cast(Any, value))


def test_require_compatible_requires_nonempty_string_field_names() -> None:
    with pytest.raises(TypeError, match="field names must be strings"):
        require_compatible(cast(Any, {1: "value"}), cast(Any, {1: "value"}))
    with pytest.raises(ValueError, match="field names must be nonempty"):
        require_compatible({"": "value"}, {"": "value"})
    with pytest.raises(TypeError, match="field names must be strings"):
        require_compatible({}, cast(Any, {1: "value"}))


_A = ContentIdentity(size=10, sha256="a" * 64)
_B = ContentIdentity(size=20, sha256="b" * 64)
_C = ContentIdentity(size=30, sha256="c" * 64)

_COMPATIBILITY_CONTEXTS: tuple[tuple[str, tuple[str, ...], tuple[object, ...]], ...] = (
    (
        "portable transfer",
        (
            "scientific and workload values",
            "target image content identity",
            "capture image content identity",
            "container mount target",
            "container mount mode",
            "mounted input identity",
        ),
        (("families", "seeds", "limits"), "sha256:target", "sha256:capture", "/work/data", "ro", _A),
    ),
    (
        "capture reuse",
        (
            "realized experiment identity",
            "host architecture",
            "target image reference",
            "target image content identity",
            "capture image reference",
            "capture image content identity",
            "capture tool version",
            "capture metadata identity",
            "reference capture identity",
        ),
        (
            _A,
            "linux/amd64",
            "target:locked",
            "sha256:target",
            "capture:locked",
            "sha256:capture",
            "dumpcap-4.0.17",
            _B,
            _C,
        ),
    ),
    (
        "fit resume",
        (
            "experiment identity",
            "reference identity",
            "capture identity",
            "scientific artifact schema",
            "CPython patch",
            "enabled families",
            "family priority",
            "gene order and bounds",
            "operator settings",
            "master seed",
            "trial seeds",
            "final seed",
            "trial generation limits",
            "similarity settings",
        ),
        (
            _A,
            _B,
            _C,
            2,
            "3.12.3",
            ("mmpp", "poisson_empirical"),
            ("mmpp", "poisson_empirical"),
            ("q01", 0.1, 10.0),
            (0.7, 0.1, 0.2),
            73,
            (7, 11),
            97,
            (1, 2, 3.0),
            (0.25, 0.25, 0.25, 0.25),
        ),
    ),
    (
        "generate reuse",
        (
            "best-model identity",
            "scientific artifact schema",
            "final seed",
            "final generation limits",
            "capture identity",
        ),
        (_A, 2, 97, (20_000, 40_000_000, 30.0), _B),
    ),
    (
        "compare reuse",
        ("capture_json", "generated_pcapng", "reference_pcapng", "similarity_settings"),
        (_A, _B, _C, ContentIdentity(size=40, sha256="d" * 64)),
    ),
    (
        "offline reconstruction",
        (
            "source tree identity",
            "uv.lock identity",
            "CPython patch",
            "scientific artifact schema",
            "artifact identities",
        ),
        (_A, _B, "3.12.3", 2, (_A, _B, _C)),
    ),
)


@pytest.mark.parametrize(("_context", "fields", "values"), _COMPATIBILITY_CONTEXTS)
def test_every_architecture_compatibility_context_accepts_only_its_exact_ordered_record(
    _context: str,
    fields: tuple[str, ...],
    values: tuple[object, ...],
) -> None:
    expected = dict(zip(fields, values, strict=True))

    require_compatible(expected, dict(expected))


def _different_value(value: object) -> object:
    if type(value) is ContentIdentity:
        return ContentIdentity(size=value.size + 1, sha256=value.sha256)
    if type(value) is int:
        return value + 1
    if type(value) is str:
        return value + "-different"
    if type(value) is tuple:
        return (*cast(tuple[object, ...], value), "different")
    raise AssertionError(f"unsupported compatibility fixture value {value!r}")


@pytest.mark.parametrize(("_context", "fields", "values"), _COMPATIBILITY_CONTEXTS)
def test_every_architecture_compatibility_field_reports_the_first_independent_mismatch(
    _context: str,
    fields: tuple[str, ...],
    values: tuple[object, ...],
) -> None:
    expected = dict(zip(fields, values, strict=True))
    for index, field in enumerate(fields):
        actual = dict(expected)
        actual[field] = _different_value(actual[field])
        if index + 1 < len(fields):
            actual[fields[index + 1]] = _different_value(actual[fields[index + 1]])

        with pytest.raises(TrafficlabError, match=field) as caught:
            require_compatible(expected, actual)

        if index + 1 < len(fields):
            assert fields[index + 1] not in str(caught.value)


def test_portable_transfer_omits_only_declared_operational_location_and_patch_observations() -> None:
    fields = _COMPATIBILITY_CONTEXTS[0][1]
    first = {
        **dict(zip(fields, _COMPATIBILITY_CONTEXTS[0][2], strict=True)),
        "checkout path": "/checkout/a",
        "run directory": "/runs/a",
        "host mount source": "/data/a",
        "Docker patch": "27.1.1",
        "Compose patch": "2.29.1",
        "kernel": "6.8.0",
    }
    second = {
        **first,
        "checkout path": "/relocated/b",
        "run directory": "/other-runs/b",
        "host mount source": "/other-data/b",
        "Docker patch": "27.1.2",
        "Compose patch": "2.29.2",
        "kernel": "6.8.1",
    }

    require_compatible(
        {field: first[field] for field in fields},
        {field: second[field] for field in fields},
    )
