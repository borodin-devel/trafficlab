"""Typed failures raised by deterministic lineage operations."""


class LineageError(Exception):
    """Base class for deterministic lineage failures."""


class InvalidDigestError(LineageError):
    """A SHA-256 value is not canonical."""


class UnsupportedLineageVersionError(LineageError):
    """A lineage representation version is unsupported."""


class InvalidProvenanceError(LineageError):
    """Typed provenance values are invalid or ambiguous."""


class InvalidLineagePathError(LineageError):
    """A local or external lineage path is not canonical."""


class FileSnapshotError(LineageError):
    """A stable regular-file snapshot could not be opened."""


class FileChangedError(LineageError):
    """A path or file identity changed during its snapshot."""


class HashMismatchError(LineageError):
    """Exact file bytes do not match a declared SHA-256 value."""


class InvalidHashDomainError(LineageError):
    """A digest carrier overlaps its own hash domain."""


class MissingParentError(LineageError):
    """A lineage node refers to an unavailable parent."""


class LineageCycleError(LineageError):
    """A lineage graph contains a directed cycle."""


class ManifestValidationError(LineageError):
    """A package manifest cannot safely declare members."""
