"""Typed failures for artifact planning, status, and publication."""

from collections.abc import Iterable
from pathlib import Path
from typing import cast


class ArtifactIoError(Exception):
    """Base class for Artifact I/O failures."""


class InvalidPublicationPlanError(ArtifactIoError):
    """A publication plan or package-member specification is invalid."""


class InvalidArtifactStatusError(ArtifactIoError):
    """A detached artifact status is invalid or noncanonical."""


class ArtifactStatusSecurityError(InvalidArtifactStatusError):
    """A detached status filesystem envelope is unsafe."""


class MissingArtifactStatusError(InvalidArtifactStatusError):
    """Neither a detached status nor an orphan artifact exists."""


class _PublicationEvidenceError(ArtifactIoError):
    """A publication failure carrying immutable diagnostic path evidence."""

    __slots__ = ("_orphan_path", "_retained_paths")

    _retained_paths: tuple[Path, ...]
    _orphan_path: Path | None

    def __init__(
        self,
        message: str,
        *,
        retained_paths: Iterable[Path] = (),
        orphan_path: Path | None = None,
    ) -> None:
        super().__init__(message)
        materialized = tuple(retained_paths)
        if not all(
            isinstance(cast(object, retained_path), Path)
            for retained_path in materialized
        ):
            raise TypeError("retained_paths must contain only Path values")
        if not isinstance(cast(object, orphan_path), (Path, type(None))):
            raise TypeError("orphan_path must be a Path or None")
        self._retained_paths = materialized
        self._orphan_path = orphan_path

    @property
    def retained_paths(self) -> tuple[Path, ...]:
        """Return immutable paths retained for diagnosis."""

        return self._retained_paths

    @property
    def orphan_path(self) -> Path | None:
        """Return the immutable published artifact lacking valid status."""

        return self._orphan_path


class OrphanArtifactError(_PublicationEvidenceError):
    """An artifact exists without a valid detached status commit marker."""


class PublicationConflictError(_PublicationEvidenceError):
    """Publication would overwrite an existing destination or status."""


class ArtifactWriteError(_PublicationEvidenceError):
    """Artifact or status bytes could not be completely written and closed."""


class ArtifactValidationError(_PublicationEvidenceError):
    """Staged or published artifact content did not validate."""


class AtomicPublicationError(_PublicationEvidenceError):
    """An atomic no-replace publication operation failed."""


class UnsupportedAtomicPublicationError(AtomicPublicationError):
    """The filesystem cannot provide the required atomic no-replace operation."""
