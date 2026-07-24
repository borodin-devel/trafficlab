"""Typed failures for bounded structured observability."""


class ObservabilityError(Exception):
    """Base class for structured-observability failures."""


class InvalidSeverityError(ObservabilityError):
    """A severity is outside the supported ordered set."""


class InvalidEventError(ObservabilityError):
    """An event violates bounded, safe structured-event requirements."""


class InvalidPolicyError(ObservabilityError):
    """A queue or coalescing bound is invalid."""


class InvalidSinkError(ObservabilityError):
    """An injected drain sink is not callable."""


class SinkWriteError(ObservabilityError):
    """A non-packet sink, renderer, or flush operation failed."""
