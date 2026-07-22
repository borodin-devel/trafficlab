"""Manifest-first validation for contract-owned artifact packages."""

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import cast

from .domains import HashDomain, HashRegion, validate_hash_domain
from .errors import ManifestValidationError
from .hashing import (
    DEFAULT_CHUNK_SIZE,
    _read_verified_local_bytes,  # pyright: ignore[reportPrivateUsage]
    validate_local_file,
)
from .values import FileIdentity, PathKind


def validate_package_members(
    root: Path,
    manifest: FileIdentity,
    parse_members: Callable[[bytes], Iterable[FileIdentity]],
    *,
    max_manifest_bytes: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[FileIdentity, ...]:
    """Verify a detached manifest before trusting and hashing its members."""

    if manifest.kind is not PathKind.LOCAL:
        raise ManifestValidationError("package manifest must use a local path")
    verified = _read_verified_local_bytes(
        root,
        manifest,
        max_bytes=max_manifest_bytes,
        chunk_size=chunk_size,
    )
    try:
        parsed_members = cast(object, tuple(parse_members(verified)))
    except Exception as exc:
        raise ManifestValidationError(
            "manifest parser rejected verified bytes"
        ) from exc

    members = cast(tuple[object, ...], parsed_members)
    if not members:
        raise ManifestValidationError("manifest must declare at least one member")
    if not all(isinstance(member, FileIdentity) for member in members):
        raise ManifestValidationError("manifest members must be FileIdentity values")

    typed_members = cast(tuple[FileIdentity, ...], members)
    if any(member.kind is not PathKind.LOCAL for member in typed_members):
        raise ManifestValidationError("manifest members must use local paths")

    ordered = tuple(sorted(typed_members, key=lambda item: item.path))
    seen_paths: set[str] = set()
    for member in ordered:
        if member.path == manifest.path:
            raise ManifestValidationError(
                "manifest must not declare itself as a member"
            )
        if member.path in seen_paths:
            raise ManifestValidationError("manifest contains duplicate member path")
        seen_paths.add(member.path)

    for member in ordered:
        validate_hash_domain(
            HashDomain(
                carrier=HashRegion(
                    manifest.path,
                    f"member:{member.path}:sha256",
                ),
                covered=(HashRegion(member.path),),
            )
        )
        validate_local_file(root, member, chunk_size=chunk_size)
    return ordered
