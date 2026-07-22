import os
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path
from typing import cast

import pytest

from trafficlab.libs.lineage import (
    DEFAULT_CHUNK_SIZE,
    FileIdentity,
    FileSnapshotError,
    HashMismatchError,
    ManifestValidationError,
    PathKind,
    Sha256Digest,
    sha256_bytes,
    snapshot_external_file,
    snapshot_local_file,
    validate_package_members,
)

package_module = import_module("trafficlab.libs.lineage.package")
hashing_module = import_module("trafficlab.libs.lineage.hashing")

ZERO = Sha256Digest("0" * 64)
ONE = Sha256Digest("1" * 64)

PackageFixture = tuple[bytes, FileIdentity, tuple[FileIdentity, ...]]


@pytest.fixture
def two_member_package(tmp_path: Path) -> PackageFixture:
    manifest_bytes = b"member declarations for a-member.bin and z-member.bin"
    (tmp_path / "manifest.json").write_bytes(manifest_bytes)
    (tmp_path / "a-member.bin").write_bytes(b"alpha member")
    (tmp_path / "z-member.bin").write_bytes(b"zulu member")
    manifest = snapshot_local_file(tmp_path, "manifest.json")
    members = (
        snapshot_local_file(tmp_path, "z-member.bin"),
        snapshot_local_file(tmp_path, "a-member.bin"),
    )
    return manifest_bytes, manifest, members


@pytest.mark.unit
def test_lin_ac_001_golden_two_member_package_uses_exact_manifest_bytes(
    tmp_path: Path,
    two_member_package: PackageFixture,
) -> None:
    """Trace LIN-AC-001 with a golden exact-byte multi-file package."""
    manifest_bytes, manifest, members = two_member_package
    parsed: list[bytes] = []

    def parser(verified: bytes) -> tuple[FileIdentity, ...]:
        parsed.append(verified)
        return members

    assert validate_package_members(
        tmp_path,
        manifest,
        parser,
        max_manifest_bytes=len(manifest_bytes),
        chunk_size=3,
    ) == tuple(sorted(members, key=lambda item: item.path))
    assert parsed == [manifest_bytes]


@pytest.mark.unit
def test_lin_ac_003_bad_manifest_digest_prevents_parser_call(
    tmp_path: Path,
) -> None:
    """Trace LIN-AC-003: detached manifest verification precedes parsing."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b"member declarations")
    called = False

    def parser(_: bytes) -> tuple[FileIdentity, ...]:
        nonlocal called
        called = True
        return ()

    bad = FileIdentity(PathKind.LOCAL, "manifest.json", ZERO)
    with pytest.raises(HashMismatchError):
        validate_package_members(
            tmp_path,
            bad,
            parser,
            max_manifest_bytes=1024,
        )
    assert called is False


@pytest.mark.unit
def test_manifest_at_exact_size_bound_is_accepted(
    tmp_path: Path,
    two_member_package: PackageFixture,
) -> None:
    manifest_bytes, manifest, members = two_member_package
    called = False

    def parser(verified: bytes) -> tuple[FileIdentity, ...]:
        nonlocal called
        called = True
        assert verified == manifest_bytes
        return members

    assert validate_package_members(
        tmp_path,
        manifest,
        parser,
        max_manifest_bytes=len(manifest_bytes),
    )
    assert called is True


@pytest.mark.unit
def test_manifest_over_size_bound_prevents_parser_call(
    tmp_path: Path,
    two_member_package: PackageFixture,
) -> None:
    manifest_bytes, manifest, _ = two_member_package
    called = False

    def parser(_: bytes) -> tuple[FileIdentity, ...]:
        nonlocal called
        called = True
        return ()

    with pytest.raises(ManifestValidationError):
        validate_package_members(
            tmp_path,
            manifest,
            parser,
            max_manifest_bytes=len(manifest_bytes) - 1,
        )
    assert called is False


@pytest.mark.unit
def test_oversized_manifest_stops_after_first_overflow_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_bytes = b"0123456789"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest = snapshot_local_file(tmp_path, manifest_path.name)
    requested_sizes: list[int] = []
    observed_sizes: list[int] = []
    parser_called = False

    def recording_read(fd: int, size: int) -> bytes:
        requested_sizes.append(size)
        chunk = os.read(fd, size)
        observed_sizes.append(len(chunk))
        return chunk

    def parser(_: bytes) -> tuple[FileIdentity, ...]:
        nonlocal parser_called
        parser_called = True
        return ()

    monkeypatch.setattr(hashing_module, "_read_chunk", recording_read)

    with pytest.raises(
        ManifestValidationError,
        match=r"^manifest exceeds maximum byte size: manifest\.json$",
    ):
        validate_package_members(
            tmp_path,
            manifest,
            parser,
            max_manifest_bytes=5,
            chunk_size=4,
        )

    assert requested_sizes == [4, 2]
    assert observed_sizes == [4, 2]
    assert sum(observed_sizes) == 6
    assert parser_called is False


@pytest.mark.unit
@pytest.mark.parametrize("max_manifest_bytes", [0, -1])
def test_manifest_requires_positive_size_bound_before_parser_call(
    tmp_path: Path,
    max_manifest_bytes: int,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b"manifest")
    manifest = snapshot_local_file(tmp_path, manifest_path.name)
    called = False

    def parser(_: bytes) -> tuple[FileIdentity, ...]:
        nonlocal called
        called = True
        return ()

    with pytest.raises(ManifestValidationError):
        validate_package_members(
            tmp_path,
            manifest,
            parser,
            max_manifest_bytes=max_manifest_bytes,
        )
    assert called is False


@pytest.mark.unit
def test_external_manifest_is_rejected_before_parser_call(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_bytes = b"manifest"
    manifest_path.write_bytes(manifest_bytes)
    manifest = FileIdentity(
        PathKind.EXTERNAL,
        str(manifest_path),
        sha256_bytes(manifest_bytes),
    )
    called = False

    def parser(_: bytes) -> tuple[FileIdentity, ...]:
        nonlocal called
        called = True
        return ()

    with pytest.raises(
        ManifestValidationError,
        match=r"^package manifest must use a local path$",
    ):
        validate_package_members(
            tmp_path,
            manifest,
            parser,
            max_manifest_bytes=1024,
        )
    assert called is False


@pytest.mark.unit
def test_parser_exception_is_wrapped_without_leaking_text_or_bytes(
    tmp_path: Path,
) -> None:
    manifest_bytes = b"private manifest payload"
    (tmp_path / "manifest.json").write_bytes(manifest_bytes)
    manifest = snapshot_local_file(tmp_path, "manifest.json")
    cause = ValueError(f"private parser detail: {manifest_bytes.decode()}")

    def parser(verified: bytes) -> tuple[FileIdentity, ...]:
        assert verified == manifest_bytes
        raise cause

    with pytest.raises(ManifestValidationError) as caught:
        validate_package_members(
            tmp_path,
            manifest,
            parser,
            max_manifest_bytes=len(manifest_bytes),
        )

    assert str(caught.value) == "manifest parser rejected verified bytes"
    assert caught.value.__cause__ is cause
    assert str(cause) not in str(caught.value)
    assert manifest_bytes.decode() not in str(caught.value)


@pytest.mark.unit
def test_generator_exception_is_wrapped_before_any_yielded_member_is_hashed(
    tmp_path: Path,
) -> None:
    manifest_bytes = b"private generator manifest"
    (tmp_path / "manifest.json").write_bytes(manifest_bytes)
    manifest = snapshot_local_file(tmp_path, "manifest.json")
    missing = FileIdentity(PathKind.LOCAL, "missing.bin", ZERO)
    cause = RuntimeError(f"private generator detail: {manifest_bytes.decode()}")

    def parser(verified: bytes) -> Iterator[FileIdentity]:
        assert verified == manifest_bytes
        yield missing
        raise cause

    with pytest.raises(ManifestValidationError) as caught:
        validate_package_members(
            tmp_path,
            manifest,
            parser,
            max_manifest_bytes=len(manifest_bytes),
        )

    assert str(caught.value) == "manifest parser rejected verified bytes"
    assert caught.value.__cause__ is cause
    assert str(cause) not in str(caught.value)
    assert manifest_bytes.decode() not in str(caught.value)


@pytest.mark.unit
def test_parser_base_exception_is_not_wrapped(tmp_path: Path) -> None:
    manifest_bytes = b"manifest"
    (tmp_path / "manifest.json").write_bytes(manifest_bytes)
    manifest = snapshot_local_file(tmp_path, "manifest.json")

    def parser(_: bytes) -> tuple[FileIdentity, ...]:
        raise KeyboardInterrupt("stop parsing")

    with pytest.raises(KeyboardInterrupt, match="stop parsing"):
        validate_package_members(
            tmp_path,
            manifest,
            parser,
            max_manifest_bytes=len(manifest_bytes),
        )


@pytest.mark.unit
def test_empty_member_declaration_is_rejected(tmp_path: Path) -> None:
    manifest_bytes = b"manifest"
    (tmp_path / "manifest.json").write_bytes(manifest_bytes)
    manifest = snapshot_local_file(tmp_path, "manifest.json")

    with pytest.raises(
        ManifestValidationError,
        match=r"^manifest must declare at least one member$",
    ):
        validate_package_members(
            tmp_path,
            manifest,
            lambda _: (),
            max_manifest_bytes=len(manifest_bytes),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("first_digest", "second_digest"),
    [(ZERO, ZERO), (ZERO, ONE)],
    ids=["equal-digests", "different-digests"],
)
def test_duplicate_member_paths_are_rejected_before_hashing_regardless_of_digest(
    tmp_path: Path,
    first_digest: Sha256Digest,
    second_digest: Sha256Digest,
) -> None:
    manifest_bytes = b"manifest"
    (tmp_path / "manifest.json").write_bytes(manifest_bytes)
    (tmp_path / "member.bin").write_bytes(b"actual member bytes")
    manifest = snapshot_local_file(tmp_path, "manifest.json")
    duplicates = (
        FileIdentity(PathKind.LOCAL, "member.bin", first_digest),
        FileIdentity(PathKind.LOCAL, "member.bin", second_digest),
    )

    with pytest.raises(
        ManifestValidationError,
        match=r"^manifest contains duplicate member path$",
    ):
        validate_package_members(
            tmp_path,
            manifest,
            lambda _: duplicates,
            max_manifest_bytes=len(manifest_bytes),
        )


@pytest.mark.unit
def test_all_declarations_are_checked_before_external_member_hashing(
    tmp_path: Path,
) -> None:
    manifest_bytes = b"manifest"
    (tmp_path / "manifest.json").write_bytes(manifest_bytes)
    changed_path = tmp_path / "!changed.bin"
    changed_path.write_bytes(b"before")
    changed = snapshot_local_file(tmp_path, changed_path.name)
    changed_path.write_bytes(b"beFore")
    external_path = tmp_path / "external.bin"
    external_path.write_bytes(b"external")
    external = snapshot_external_file(external_path)
    manifest = snapshot_local_file(tmp_path, "manifest.json")

    with pytest.raises(
        ManifestValidationError,
        match=r"^manifest members must use local paths$",
    ):
        validate_package_members(
            tmp_path,
            manifest,
            lambda _: (changed, external),
            max_manifest_bytes=len(manifest_bytes),
        )


@pytest.mark.unit
def test_non_identity_member_declaration_is_rejected(tmp_path: Path) -> None:
    manifest_bytes = b"manifest"
    (tmp_path / "manifest.json").write_bytes(manifest_bytes)
    manifest = snapshot_local_file(tmp_path, "manifest.json")
    invalid = cast(FileIdentity, object())

    with pytest.raises(
        ManifestValidationError,
        match=r"^manifest members must be FileIdentity values$",
    ):
        validate_package_members(
            tmp_path,
            manifest,
            lambda _: (invalid,),
            max_manifest_bytes=len(manifest_bytes),
        )


@pytest.mark.unit
def test_manifest_cannot_list_its_own_path_regardless_of_digest(
    tmp_path: Path,
) -> None:
    manifest_bytes = b"manifest"
    (tmp_path / "manifest.json").write_bytes(manifest_bytes)
    manifest = snapshot_local_file(tmp_path, "manifest.json")
    self_listed = FileIdentity(PathKind.LOCAL, manifest.path, ZERO)

    with pytest.raises(
        ManifestValidationError,
        match=r"^manifest must not declare itself as a member$",
    ):
        validate_package_members(
            tmp_path,
            manifest,
            lambda _: (self_listed,),
            max_manifest_bytes=len(manifest_bytes),
        )


@pytest.mark.unit
def test_missing_member_is_rejected_by_real_local_validation(tmp_path: Path) -> None:
    manifest_bytes = b"manifest"
    (tmp_path / "manifest.json").write_bytes(manifest_bytes)
    manifest = snapshot_local_file(tmp_path, "manifest.json")
    missing = FileIdentity(PathKind.LOCAL, "missing.bin", ZERO)

    with pytest.raises(FileSnapshotError):
        validate_package_members(
            tmp_path,
            manifest,
            lambda _: (missing,),
            max_manifest_bytes=len(manifest_bytes),
        )


@pytest.mark.unit
def test_lin_ac_002_changed_member_is_rejected_by_real_local_validation(
    tmp_path: Path,
) -> None:
    """Trace LIN-AC-002 with a deterministic one-byte member mutation."""
    manifest_bytes = b"manifest"
    (tmp_path / "manifest.json").write_bytes(manifest_bytes)
    member_path = tmp_path / "member.bin"
    member_path.write_bytes(b"before")
    member = snapshot_local_file(tmp_path, member_path.name)
    member_path.write_bytes(b"beFore")
    manifest = snapshot_local_file(tmp_path, "manifest.json")

    with pytest.raises(HashMismatchError):
        validate_package_members(
            tmp_path,
            manifest,
            lambda _: (member,),
            max_manifest_bytes=len(manifest_bytes),
        )


@pytest.mark.unit
def test_members_are_validated_in_canonical_path_order(
    tmp_path: Path,
    two_member_package: PackageFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_bytes, manifest, members = two_member_package
    validated: list[tuple[Path, str, int]] = []

    def recording_validate_local_file(
        root: Path,
        expected: FileIdentity,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> FileIdentity:
        validated.append((root, expected.path, chunk_size))
        return expected

    monkeypatch.setattr(
        package_module,
        "validate_local_file",
        recording_validate_local_file,
    )

    result = validate_package_members(
        tmp_path,
        manifest,
        lambda _: members,
        max_manifest_bytes=len(manifest_bytes),
        chunk_size=7,
    )

    expected = tuple(sorted(members, key=lambda item: item.path))
    assert result == expected
    assert validated == [(tmp_path, item.path, 7) for item in expected]
