"""Immutable bounded structured-observability values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from math import isfinite
from typing import cast

from .errors import InvalidEventError, InvalidPolicyError, InvalidSeverityError

type Scalar = None | bool | int | float | str
_MAX_IDENTITY_LENGTH = 128
_MAX_FIELD_COUNT = 32
_MAX_FIELD_VALUE_BYTES = 1024
_MAX_INTEGER = (1 << 63) - 1
_SECRET_PARTS = (
    "secret",
    "token",
    "password",
    "credential",
    "authorization",
    "cookie",
)


class Severity(StrEnum):
    """Supported structured-event severities."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


_SEVERITY_RANKS = {
    Severity.DEBUG: 10,
    Severity.INFO: 20,
    Severity.WARNING: 30,
    Severity.ERROR: 40,
    Severity.CRITICAL: 50,
}


def severity_rank(severity: object) -> int:
    """Return the stable filtering and ordering rank for one severity."""

    if not isinstance(severity, Severity):
        raise InvalidSeverityError("severity must be a supported Severity")
    return _SEVERITY_RANKS[severity]


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_IDENTITY_LENGTH:
        raise InvalidEventError(f"{field} must be a bounded non-empty identifier")
    if not value.isascii() or any(
        character.isspace() or ord(character) < 0x21 or ord(character) == 0x7F
        for character in value
    ):
        raise InvalidEventError(f"{field} must be a single-line ASCII identifier")
    return value


def _field_name(value: object) -> str:
    name = _identity(value, "event field name")
    if any(part in name.lower() for part in _SECRET_PARTS):
        raise InvalidEventError("event field name is prohibited for secret safety")
    return name


def _scalar(value: object) -> Scalar:
    if value is None or type(value) is bool:
        return cast(Scalar, value)
    if type(value) is int:
        integer = value
        if abs(integer) > _MAX_INTEGER:
            raise InvalidEventError("event integer field value is out of bounds")
        return integer
    if type(value) is float:
        number = value
        if not isfinite(number):
            raise InvalidEventError("event float field value must be finite")
        return number
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_FIELD_VALUE_BYTES:
            raise InvalidEventError("event string field value exceeds byte bound")
        if "\x00" in value or "\r" in value or "\n" in value:
            raise InvalidEventError("event string field value contains controls")
        return value
    raise InvalidEventError("event field value must be a bounded scalar")


@dataclass(frozen=True, slots=True)
class StructuredEvent:
    """One bounded application event with canonical field ordering."""

    timestamp: datetime
    severity: Severity
    application: str
    run_id: str
    event_name: str
    fields: tuple[tuple[str, Scalar], ...]

    def __post_init__(self) -> None:
        timestamp = cast(object, self.timestamp)
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise InvalidEventError("event timestamp must be timezone-aware UTC")
        if timestamp.utcoffset() != timedelta(0):
            raise InvalidEventError("event timestamp must be UTC")
        object.__setattr__(self, "timestamp", timestamp.astimezone(UTC))
        severity_rank(self.severity)
        _identity(cast(object, self.application), "application")
        _identity(cast(object, self.run_id), "run identifier")
        _identity(cast(object, self.event_name), "event name")
        raw_fields = cast(object, self.fields)
        if type(raw_fields) is not tuple:
            raise InvalidEventError("event fields must be a bounded tuple")
        field_items = cast(tuple[object, ...], raw_fields)
        if len(field_items) > _MAX_FIELD_COUNT:
            raise InvalidEventError("event fields must be a bounded tuple")
        canonical: list[tuple[str, Scalar]] = []
        for item in field_items:
            if type(item) is not tuple:
                raise InvalidEventError("event field must be a name/value pair")
            field_item = cast(tuple[object, ...], item)
            if len(field_item) != 2:
                raise InvalidEventError("event field must be a name/value pair")
            name, value = field_item
            canonical.append((_field_name(name), _scalar(value)))
        canonical.sort(key=lambda item: item[0])
        if any(left[0] == right[0] for left, right in pairwise(canonical)):
            raise InvalidEventError("event field names must be unique")
        object.__setattr__(self, "fields", tuple(canonical))


@dataclass(frozen=True, slots=True)
class ObservabilityPolicy:
    """Explicit bounded routing configuration for one run logger."""

    normal_capacity: int
    reserved_capacity: int
    coalesce_capacity: int
    minimum_severity: Severity = Severity.INFO

    def __post_init__(self) -> None:
        for value, field in (
            (self.normal_capacity, "normal capacity"),
            (self.reserved_capacity, "reserved capacity"),
            (self.coalesce_capacity, "coalescing capacity"),
        ):
            if type(cast(object, value)) is not int or value <= 0:
                raise InvalidPolicyError(f"{field} must be a positive integer")
        try:
            severity_rank(self.minimum_severity)
        except InvalidSeverityError as error:
            raise InvalidPolicyError(
                "minimum severity must be a supported Severity"
            ) from error
