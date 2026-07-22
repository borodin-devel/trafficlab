"""Immutable, canonical values for deterministic artifact provenance."""

import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import cast

from .errors import (
    InvalidDigestError,
    InvalidLineagePathError,
    InvalidProvenanceError,
    UnsupportedLineageVersionError,
)

CURRENT_LINEAGE_VERSION = 1

_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


def _validate_text(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or _CONTROL_CHARACTER.search(value) is not None
    ):
        raise InvalidProvenanceError(
            f"{field} must be a non-empty string without control characters"
        )


def _validate_path_text(path: object, description: str) -> None:
    if (
        not isinstance(path, str)
        or not path
        or _CONTROL_CHARACTER.search(path) is not None
    ):
        raise InvalidLineagePathError(
            f"{description} lineage path must be a non-empty string without "
            "control characters"
        )


def _validate_local_path(path: str) -> None:
    _validate_path_text(path, "local")
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != path
        or any(component in {".", ".."} for component in path.split("/"))
        or path.endswith("/")
    ):
        raise InvalidLineagePathError(
            "local lineage path must be a canonical relative POSIX path"
        )


def _validate_external_path(path: str) -> None:
    _validate_path_text(path, "external")
    if (
        not path.startswith("/")
        or path.startswith("//")
        or posixpath.normpath(path) != path
    ):
        raise InvalidLineagePathError(
            "external lineage path must be a canonical absolute POSIX path"
        )


@dataclass(frozen=True, slots=True, order=True)
class Sha256Digest:
    """A canonical lowercase SHA-256 digest."""

    value: str

    def __post_init__(self) -> None:
        value = cast(object, self.value)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise InvalidDigestError(
                "sha256 must be 64 lowercase hexadecimal characters"
            )

    def __str__(self) -> str:
        return self.value


class PathKind(StrEnum):
    """The resolution domain for a lineage file path."""

    LOCAL = "local"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """The exact-byte digest of a canonical local or external path."""

    kind: PathKind
    path: str
    sha256: Sha256Digest

    def __post_init__(self) -> None:
        kind = cast(object, self.kind)
        digest = cast(object, self.sha256)
        if not isinstance(kind, PathKind):
            raise InvalidLineagePathError(
                "lineage path kind must be PathKind.LOCAL or PathKind.EXTERNAL"
            )
        if not isinstance(digest, Sha256Digest):
            raise InvalidProvenanceError("file sha256 must be a Sha256Digest")
        if self.kind is PathKind.LOCAL:
            _validate_local_path(self.path)
        elif self.kind is PathKind.EXTERNAL:
            _validate_external_path(self.path)
        else:  # pragma: no cover - defensive against future enum members
            raise InvalidLineagePathError("unsupported lineage path kind")


@dataclass(frozen=True, slots=True, order=True)
class NamedIdentity:
    """A named implementation or dependency and its version."""

    name: str
    version: str

    def __post_init__(self) -> None:
        _validate_text(self.name, "identity name")
        _validate_text(self.version, "identity version")


@dataclass(frozen=True, slots=True, order=True)
class ConfigurationIdentity:
    """A component-owned configuration identity."""

    name: str
    identity: str

    def __post_init__(self) -> None:
        _validate_text(self.name, "configuration name")
        _validate_text(self.identity, "configuration identity")


@dataclass(frozen=True, slots=True, order=True)
class SeedIdentity:
    """An explicit integer seed used by a named deterministic operation."""

    name: str
    value: int

    def __post_init__(self) -> None:
        _validate_text(self.name, "seed name")
        value = cast(object, self.value)
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidProvenanceError("seed value must be an integer")


def _validate_schema_version(schema_version: int) -> None:
    if type(schema_version) is not int or schema_version != CURRENT_LINEAGE_VERSION:
        raise UnsupportedLineageVersionError(
            "only lineage schema version 1 is supported"
        )


def _require_tuple_of(
    items: object, expected_type: type[object], category: str
) -> None:
    if not isinstance(items, tuple):
        raise InvalidProvenanceError(f"{category} must be an immutable tuple")
    typed_items = cast(tuple[object, ...], items)
    if not all(isinstance(item, expected_type) for item in typed_items):
        raise InvalidProvenanceError(
            f"{category} must contain only {expected_type.__name__} values"
        )


def _reject_duplicate_keys(keys: Iterable[object], description: str) -> None:
    seen: set[object] = set()
    for key in keys:
        if key in seen:
            raise InvalidProvenanceError(f"duplicate {description}")
        seen.add(key)


def _validate_categories(
    *,
    paths: tuple[FileIdentity, ...],
    implementations: tuple[NamedIdentity, ...],
    dependencies: tuple[NamedIdentity, ...],
    seeds: tuple[SeedIdentity, ...],
    configurations: tuple[ConfigurationIdentity, ...],
    parent_hashes: tuple[Sha256Digest, ...],
) -> None:
    _require_tuple_of(paths, FileIdentity, "paths")
    _require_tuple_of(implementations, NamedIdentity, "implementations")
    _require_tuple_of(dependencies, NamedIdentity, "dependencies")
    _require_tuple_of(seeds, SeedIdentity, "seeds")
    _require_tuple_of(configurations, ConfigurationIdentity, "configurations")
    _require_tuple_of(parent_hashes, Sha256Digest, "parent_hashes")

    _reject_duplicate_keys(
        ((item.kind.value, item.path) for item in paths), "lineage path"
    )
    _reject_duplicate_keys(
        (item.name for item in implementations), "implementation name"
    )
    _reject_duplicate_keys((item.name for item in dependencies), "dependency name")
    _reject_duplicate_keys((item.name for item in seeds), "seed name")
    _reject_duplicate_keys((item.name for item in configurations), "configuration name")
    _reject_duplicate_keys(parent_hashes, "parent hash")


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    """Canonical, versioned provenance ready for contract-owned serialization."""

    schema_version: int
    paths: tuple[FileIdentity, ...]
    implementations: tuple[NamedIdentity, ...]
    dependencies: tuple[NamedIdentity, ...]
    seeds: tuple[SeedIdentity, ...]
    configurations: tuple[ConfigurationIdentity, ...]
    parent_hashes: tuple[Sha256Digest, ...]

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_categories(
            paths=self.paths,
            implementations=self.implementations,
            dependencies=self.dependencies,
            seeds=self.seeds,
            configurations=self.configurations,
            parent_hashes=self.parent_hashes,
        )
        canonical = (
            self.paths == tuple(sorted(self.paths, key=_path_key))
            and self.implementations
            == tuple(sorted(self.implementations, key=_named_key))
            and self.dependencies == tuple(sorted(self.dependencies, key=_named_key))
            and self.seeds == tuple(sorted(self.seeds, key=_seed_key))
            and self.configurations
            == tuple(sorted(self.configurations, key=_configuration_key))
            and self.parent_hashes == tuple(sorted(self.parent_hashes, key=_digest_key))
        )
        if not canonical:
            raise InvalidProvenanceError(
                "provenance categories must be in canonical order"
            )


def _path_key(item: FileIdentity) -> tuple[str, str]:
    return item.kind.value, item.path


def _named_key(item: NamedIdentity) -> tuple[str, str]:
    return item.name, item.version


def _seed_key(item: SeedIdentity) -> tuple[str, int]:
    return item.name, item.value


def _configuration_key(item: ConfigurationIdentity) -> tuple[str, str]:
    return item.name, item.identity


def _digest_key(item: Sha256Digest) -> str:
    return item.value


def build_provenance(
    *,
    schema_version: int = CURRENT_LINEAGE_VERSION,
    paths: Iterable[FileIdentity] = (),
    implementations: Iterable[NamedIdentity] = (),
    dependencies: Iterable[NamedIdentity] = (),
    seeds: Iterable[SeedIdentity] = (),
    configurations: Iterable[ConfigurationIdentity] = (),
    parent_hashes: Iterable[Sha256Digest] = (),
) -> ProvenanceRecord:
    """Materialize, validate, and canonically order explicit provenance values."""

    _validate_schema_version(schema_version)
    materialized_paths = tuple(paths)
    materialized_implementations = tuple(implementations)
    materialized_dependencies = tuple(dependencies)
    materialized_seeds = tuple(seeds)
    materialized_configurations = tuple(configurations)
    materialized_parent_hashes = tuple(parent_hashes)
    _validate_categories(
        paths=materialized_paths,
        implementations=materialized_implementations,
        dependencies=materialized_dependencies,
        seeds=materialized_seeds,
        configurations=materialized_configurations,
        parent_hashes=materialized_parent_hashes,
    )
    return ProvenanceRecord(
        schema_version=schema_version,
        paths=tuple(sorted(materialized_paths, key=_path_key)),
        implementations=tuple(sorted(materialized_implementations, key=_named_key)),
        dependencies=tuple(sorted(materialized_dependencies, key=_named_key)),
        seeds=tuple(sorted(materialized_seeds, key=_seed_key)),
        configurations=tuple(
            sorted(materialized_configurations, key=_configuration_key)
        ),
        parent_hashes=tuple(sorted(materialized_parent_hashes, key=_digest_key)),
    )


def provenance_items(record: ProvenanceRecord) -> tuple[tuple[str, object], ...]:
    """Return the fixed serialization-neutral field representation of a record."""

    return (
        ("schema_version", record.schema_version),
        (
            "paths",
            tuple(
                (
                    ("kind", item.kind.value),
                    ("path", item.path),
                    ("sha256", str(item.sha256)),
                )
                for item in record.paths
            ),
        ),
        (
            "implementations",
            tuple(
                (("name", item.name), ("version", item.version))
                for item in record.implementations
            ),
        ),
        (
            "dependencies",
            tuple(
                (("name", item.name), ("version", item.version))
                for item in record.dependencies
            ),
        ),
        (
            "seeds",
            tuple(
                (("name", item.name), ("value", item.value)) for item in record.seeds
            ),
        ),
        (
            "configurations",
            tuple(
                (("name", item.name), ("identity", item.identity))
                for item in record.configurations
            ),
        ),
        ("parent_hashes", tuple(str(item) for item in record.parent_hashes)),
    )
