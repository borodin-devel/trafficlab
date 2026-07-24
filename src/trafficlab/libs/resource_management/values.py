"""Immutable values for four-dimension resource admission."""

from __future__ import annotations

import os
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import cast

from .errors import InvalidResourceValueError

_MAX_QUANTITY = (1 << 63) - 1
_DECISION_REASONS = frozenset(
    {
        "accepted",
        "cpu_exhausted",
        "duplicate_job",
        "memory_exhausted",
        "observation_insufficient",
        "probe_failed",
        "storage_exhausted",
        "unknown_job",
        "worker_exhausted",
    }
)


def _positive(value: object, field: str) -> int:
    if type(value) is not int or value <= 0 or value > _MAX_QUANTITY:
        raise InvalidResourceValueError(f"{field} must be a bounded positive integer")
    return value


def _nonnegative(value: object, field: str) -> int:
    if type(value) is not int or value < 0 or value > _MAX_QUANTITY:
        raise InvalidResourceValueError(
            f"{field} must be a bounded nonnegative integer"
        )
    return value


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise InvalidResourceValueError(f"{field} must be a bounded identifier")
    if not value.isascii() or any(character.isspace() for character in value):
        raise InvalidResourceValueError(
            f"{field} must be a single-line ASCII identifier"
        )
    return value


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    cpu_units: int
    memory_bytes: int
    storage_bytes: int
    worker_slots: int

    def __post_init__(self) -> None:
        for value, field in (
            (self.cpu_units, "CPU budget"),
            (self.memory_bytes, "memory budget"),
            (self.storage_bytes, "storage budget"),
            (self.worker_slots, "worker budget"),
        ):
            _positive(cast(object, value), field)


@dataclass(frozen=True, slots=True)
class ResourceCapacity:
    cpu_units: int
    memory_bytes: int
    storage_bytes: int
    worker_slots: int

    def __post_init__(self) -> None:
        for value, field in (
            (self.cpu_units, "CPU capacity"),
            (self.memory_bytes, "memory capacity"),
            (self.storage_bytes, "storage capacity"),
            (self.worker_slots, "worker capacity"),
        ):
            _nonnegative(cast(object, value), field)


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    cpu_units: int
    memory_bytes: int
    storage_bytes: int
    storage_path: Path

    def __post_init__(self) -> None:
        for value, field in (
            (self.cpu_units, "observed CPUs"),
            (self.memory_bytes, "observed memory"),
            (self.storage_bytes, "observed storage"),
        ):
            _positive(cast(object, value), field)
        path = cast(object, self.storage_path)
        if (
            not isinstance(path, Path)
            or not path.is_absolute()
            or str(path) != os.path.normpath(str(path))
        ):
            raise InvalidResourceValueError(
                "storage path must be normalized absolute Path"
            )


@dataclass(frozen=True, slots=True)
class ProbeFailure:
    reason: str

    def __post_init__(self) -> None:
        reason = cast(object, self.reason)
        if not isinstance(reason, str) or not reason or "\n" in reason:
            raise InvalidResourceValueError("probe failure reason must be one line")


@dataclass(frozen=True, slots=True)
class JobReservation:
    job_id: str
    job_type: str
    cpu_units: int
    memory_bytes: int
    storage_bytes: int
    worker_slots: int

    def __post_init__(self) -> None:
        _identity(cast(object, self.job_id), "job identifier")
        _identity(cast(object, self.job_type), "job type")
        for value, field in (
            (self.cpu_units, "CPU reservation"),
            (self.memory_bytes, "memory reservation"),
            (self.storage_bytes, "storage reservation"),
            (self.worker_slots, "worker reservation"),
        ):
            _positive(cast(object, value), field)


@dataclass(frozen=True, slots=True)
class LedgerState:
    budget: ResourceBudget
    active: tuple[JobReservation, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.budget), ResourceBudget):
            raise InvalidResourceValueError("ledger budget must be a resource budget")
        if not isinstance(cast(object, self.active), tuple) or any(
            not isinstance(cast(object, value), JobReservation) for value in self.active
        ):
            raise InvalidResourceValueError("ledger reservations must be a tuple")
        ordered = tuple(sorted(self.active, key=lambda value: value.job_id))
        if any(left.job_id == right.job_id for left, right in pairwise(ordered)):
            raise InvalidResourceValueError("ledger job identities must be unique")
        object.__setattr__(self, "active", ordered)


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    job_id: str
    accepted: bool
    reason: str
    observation: ResourceObservation | ProbeFailure
    before: ResourceCapacity
    after: ResourceCapacity

    def __post_init__(self) -> None:
        _identity(cast(object, self.job_id), "decision job identifier")
        if type(self.accepted) is not bool:
            raise InvalidResourceValueError("decision acceptance must be boolean")
        reason = cast(object, self.reason)
        if not isinstance(reason, str) or reason not in _DECISION_REASONS:
            raise InvalidResourceValueError("decision reason is invalid")
        if not isinstance(
            cast(object, self.observation), (ProbeFailure, ResourceObservation)
        ):
            raise InvalidResourceValueError("decision observation is invalid")
        if not isinstance(
            cast(object, self.before), ResourceCapacity
        ) or not isinstance(cast(object, self.after), ResourceCapacity):
            raise InvalidResourceValueError(
                "decision capacities must be resource capacities"
            )
