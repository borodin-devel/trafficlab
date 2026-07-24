"""Typed failures for configuration resolution and startup records."""


class ConfigurationError(Exception):
    """Base class for configuration boundary failures."""


class InvalidConfigurationSchemaError(ConfigurationError):
    """Application-owned configuration schema is unsafe or inconsistent."""


class InvalidConfigurationSelectorError(ConfigurationError):
    """Configuration-file selection is invalid."""


class ConfigurationSourceError(ConfigurationError):
    """An explicitly selected configuration source cannot be read safely."""


class ConfigurationValidationError(ConfigurationError):
    """Configuration data fails schema or domain validation."""


class UnsafeConfigurationRecordError(ConfigurationError):
    """A future secret-bearing field lacks an approved record rule."""


class AttemptDirectoryError(ConfigurationError):
    """Managed or direct attempt-directory boundary is unsafe."""


class LaunchRecordError(ConfigurationError):
    """A startup record cannot be rendered or safely published."""


class LaunchRecordConflictError(LaunchRecordError):
    """A launch record already exists and must never be overwritten."""
