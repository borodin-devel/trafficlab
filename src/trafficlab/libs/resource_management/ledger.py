"""Pure deterministic four-dimension resource ledger."""

from __future__ import annotations

from .errors import InconsistentLedgerError
from .values import (
    AdmissionDecision,
    JobReservation,
    LedgerState,
    ProbeFailure,
    ResourceCapacity,
    ResourceObservation,
)


def remaining(state: LedgerState) -> ResourceCapacity:
    values = [
        state.budget.cpu_units,
        state.budget.memory_bytes,
        state.budget.storage_bytes,
        state.budget.worker_slots,
    ]
    for reservation in state.active:
        for index, amount in enumerate(
            (
                reservation.cpu_units,
                reservation.memory_bytes,
                reservation.storage_bytes,
                reservation.worker_slots,
            )
        ):
            values[index] -= amount
    if any(value < 0 for value in values):
        raise InconsistentLedgerError("ledger reservations exceed configured budget")
    return ResourceCapacity(*values)


def admit(
    state: LedgerState,
    reservation: JobReservation,
    observation: ResourceObservation | ProbeFailure,
) -> tuple[LedgerState, AdmissionDecision]:
    before = remaining(state)
    if any(value.job_id == reservation.job_id for value in state.active):
        return state, _decision(
            reservation.job_id, False, "duplicate_job", observation, state, state
        )
    if isinstance(observation, ProbeFailure):
        return state, _decision(
            reservation.job_id, False, "probe_failed", observation, state, state
        )
    if (
        observation.cpu_units < state.budget.cpu_units
        or observation.memory_bytes < state.budget.memory_bytes
        or observation.storage_bytes < state.budget.storage_bytes
    ):
        return state, _decision(
            reservation.job_id,
            False,
            "observation_insufficient",
            observation,
            state,
            state,
        )
    requested = (
        reservation.cpu_units,
        reservation.memory_bytes,
        reservation.storage_bytes,
        reservation.worker_slots,
    )
    available = (
        before.cpu_units,
        before.memory_bytes,
        before.storage_bytes,
        before.worker_slots,
    )
    reasons = (
        "cpu_exhausted",
        "memory_exhausted",
        "storage_exhausted",
        "worker_exhausted",
    )
    for amount, capacity, reason in zip(requested, available, reasons, strict=True):
        if amount > capacity:
            return state, _decision(
                reservation.job_id, False, reason, observation, state, state
            )
    next_state = LedgerState(state.budget, (*state.active, reservation))
    return next_state, _decision(
        reservation.job_id, True, "accepted", observation, state, next_state
    )


def release(
    state: LedgerState, job_id: str, observation: ResourceObservation | ProbeFailure
) -> tuple[LedgerState, AdmissionDecision]:
    _ = remaining(state)
    retained = tuple(value for value in state.active if value.job_id != job_id)
    if len(retained) == len(state.active):
        return state, _decision(job_id, False, "unknown_job", observation, state, state)
    next_state = LedgerState(state.budget, retained)
    return next_state, _decision(
        job_id, True, "accepted", observation, state, next_state
    )


def _decision(
    job_id: str,
    accepted: bool,
    reason: str,
    observation: ResourceObservation | ProbeFailure,
    state_before: LedgerState,
    state_after: LedgerState,
) -> AdmissionDecision:
    return AdmissionDecision(
        job_id,
        accepted,
        reason,
        observation,
        remaining(state_before),
        remaining(state_after),
        state_before,
        state_after,
    )
