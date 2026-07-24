"""Bounded nonblocking admission and exact event-saturation accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from queue import Empty, Full, Queue
from threading import Lock

from .errors import InvalidEventError
from .values import ObservabilityPolicy, Severity, StructuredEvent, severity_rank


class EmitResult(StrEnum):
    """One nonblocking event-admission outcome."""

    FILTERED = "filtered"
    ADMITTED = "admitted"
    DROPPED = "dropped"
    COALESCED = "coalesced"
    OVERFLOWED = "overflowed"


@dataclass(frozen=True, slots=True)
class _CoalescedEvent:
    """Exact state retained for one saturated severe event key."""

    severity: Severity
    event_name: str
    count: int
    first_timestamp: StructuredEvent
    last_timestamp: StructuredEvent


@dataclass(frozen=True, slots=True)
class DrainBatch:
    """One deterministic non-packet batch detached from router state."""

    events: tuple[StructuredEvent, ...]


class EventRouter:
    """Route one run's events with bounded queues and exact summaries."""

    def __init__(
        self,
        policy: object,
        application: str,
        run_id: str,
    ) -> None:
        if not isinstance(policy, ObservabilityPolicy):
            raise InvalidEventError(
                "observability router requires an ObservabilityPolicy"
            )
        policy_value = policy
        probe = StructuredEvent(
            datetime(1970, 1, 1, tzinfo=UTC),
            Severity.INFO,
            application,
            run_id,
            "router_identity",
            (),
        )
        self._policy = policy_value
        self._application = probe.application
        self._run_id = probe.run_id
        self._normal: Queue[StructuredEvent] = Queue(
            maxsize=policy_value.normal_capacity
        )
        self._reserved: Queue[StructuredEvent] = Queue(
            maxsize=policy_value.reserved_capacity
        )
        self._lock = Lock()
        self._low_drops: dict[Severity, int] = {}
        self._coalesced: dict[tuple[Severity, str], _CoalescedEvent] = {}
        self._severe_overflow = 0

    def emit(self, event: object) -> EmitResult:
        """Admit one event without rendering, sink I/O, clock, or waiting."""

        if not isinstance(event, StructuredEvent):
            raise InvalidEventError("observability router requires a StructuredEvent")
        if event.application != self._application or event.run_id != self._run_id:
            raise InvalidEventError(
                "event identity does not match observability router"
            )
        if severity_rank(event.severity) < severity_rank(self._policy.minimum_severity):
            return EmitResult.FILTERED
        queue = (
            self._normal
            if event.severity in (Severity.DEBUG, Severity.INFO)
            else self._reserved
        )
        try:
            queue.put_nowait(event)
        except Full:
            return self._record_saturation(event)
        return EmitResult.ADMITTED

    def low_drop_counts(self) -> tuple[tuple[Severity, int], ...]:
        """Return exact dropped normal-event counts in severity order."""

        with self._lock:
            return tuple(
                (severity, self._low_drops[severity])
                for severity in sorted(self._low_drops, key=severity_rank)
            )

    def coalesced_counts(self) -> tuple[tuple[Severity, str, int], ...]:
        """Return exact severe-coalescing counts in deterministic key order."""

        with self._lock:
            return tuple(
                (state.severity, state.event_name, state.count)
                for _, state in sorted(
                    self._coalesced.items(),
                    key=lambda item: (severity_rank(item[0][0]), item[0][1]),
                )
            )

    def severe_overflow_count(self) -> int:
        """Return exact count of novel severe keys beyond coalescing capacity."""

        with self._lock:
            return self._severe_overflow

    def plan_drain(self, summary_timestamp: datetime) -> DrainBatch:
        """Detach one deterministic batch with this router's fixed identity."""

        timestamp = StructuredEvent(
            summary_timestamp,
            Severity.INFO,
            self._application,
            self._run_id,
            "summary_timestamp",
            (),
        ).timestamp
        reserved, normal, coalesced, low_drops, overflow = self._take_for_drain()
        return _build_drain_batch(
            timestamp,
            self._application,
            self._run_id,
            reserved,
            normal,
            coalesced,
            low_drops,
            overflow,
        )

    def _take_for_drain(
        self,
    ) -> tuple[
        tuple[StructuredEvent, ...],
        tuple[StructuredEvent, ...],
        tuple[_CoalescedEvent, ...],
        tuple[tuple[Severity, int], ...],
        int,
    ]:
        reserved = _take_queue(self._reserved)
        normal = _take_queue(self._normal)
        with self._lock:
            coalesced = tuple(
                state
                for _, state in sorted(
                    self._coalesced.items(),
                    key=lambda item: (severity_rank(item[0][0]), item[0][1]),
                )
            )
            low_drops = tuple(
                (severity, self._low_drops[severity])
                for severity in sorted(self._low_drops, key=severity_rank)
            )
            overflow = self._severe_overflow
            self._coalesced.clear()
            self._low_drops.clear()
            self._severe_overflow = 0
        return reserved, normal, coalesced, low_drops, overflow

    def _record_saturation(self, event: StructuredEvent) -> EmitResult:
        with self._lock:
            if event.severity in (Severity.DEBUG, Severity.INFO):
                self._low_drops[event.severity] = (
                    self._low_drops.get(event.severity, 0) + 1
                )
                return EmitResult.DROPPED
            key = (event.severity, event.event_name)
            existing = self._coalesced.get(key)
            if existing is not None:
                self._coalesced[key] = _CoalescedEvent(
                    existing.severity,
                    existing.event_name,
                    existing.count + 1,
                    existing.first_timestamp,
                    event,
                )
                return EmitResult.COALESCED
            if len(self._coalesced) >= self._policy.coalesce_capacity:
                self._severe_overflow += 1
                return EmitResult.OVERFLOWED
            self._coalesced[key] = _CoalescedEvent(
                event.severity, event.event_name, 1, event, event
            )
            return EmitResult.COALESCED


def plan_drain(router: object, summary_timestamp: datetime) -> DrainBatch:
    """Detach one deterministic batch and generate exact saturation summaries."""

    if not isinstance(router, EventRouter):
        raise InvalidEventError("observability drain requires an EventRouter")
    return router.plan_drain(summary_timestamp)


def _build_drain_batch(
    timestamp: datetime,
    application: str,
    run_id: str,
    reserved: tuple[StructuredEvent, ...],
    normal: tuple[StructuredEvent, ...],
    coalesced: tuple[_CoalescedEvent, ...],
    low_drops: tuple[tuple[Severity, int], ...],
    overflow: int,
) -> DrainBatch:
    summaries: list[StructuredEvent] = []
    for state in coalesced:
        summaries.append(
            StructuredEvent(
                timestamp,
                state.severity,
                application,
                run_id,
                "observability.severe_coalesced",
                (
                    ("count", state.count),
                    ("event_name", state.event_name),
                    ("first_timestamp", _timestamp_text(state.first_timestamp)),
                    ("last_timestamp", _timestamp_text(state.last_timestamp)),
                    ("severity", state.severity.value),
                ),
            )
        )
    for severity, count in low_drops:
        summaries.append(
            StructuredEvent(
                timestamp,
                Severity.WARNING,
                application,
                run_id,
                "observability.low_severity_dropped",
                (("count", count), ("severity", severity.value)),
            )
        )
    if overflow:
        summaries.append(
            StructuredEvent(
                timestamp,
                Severity.ERROR,
                application,
                run_id,
                "observability.severe_overflow",
                (("count", overflow),),
            )
        )
    return DrainBatch(reserved + normal + tuple(summaries))


def _take_queue(queue: Queue[StructuredEvent]) -> tuple[StructuredEvent, ...]:
    events: list[StructuredEvent] = []
    while True:
        try:
            events.append(queue.get_nowait())
        except Empty:
            return tuple(events)


def _timestamp_text(event: StructuredEvent) -> str:
    return (
        event.timestamp.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )
