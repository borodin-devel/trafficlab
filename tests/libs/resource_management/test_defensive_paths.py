# pyright: reportArgumentType=false
"""Tests for resource-management defensive boundaries."""

from pathlib import Path

import pytest

from trafficlab.libs.resource_management import (
    InconsistentLedgerError,
    InvalidResourceValueError,
    JobReservation,
    LedgerState,
    ProbeFailure,
    ResourceBudget,
    ResourceObservation,
    admit,
    probe_resources,
    remaining,
)


@pytest.mark.unit
def test_ledger_rejects_inconsistent_and_insufficient_observation() -> None:
    budget = ResourceBudget(2, 2, 2, 2)
    state = LedgerState(budget, (JobReservation("a", "fit", 2, 2, 2, 2),))
    with pytest.raises(InconsistentLedgerError):
        remaining(
            LedgerState(budget, (*state.active, JobReservation("b", "fit", 1, 1, 1, 1)))
        )
    _, decision = admit(
        LedgerState(budget),
        JobReservation("a", "fit", 1, 1, 1, 1),
        ResourceObservation(2, 2, 2, Path("/storage")),
    )
    assert decision.accepted
    _, insufficient = admit(
        LedgerState(budget),
        JobReservation("b", "fit", 1, 1, 1, 1),
        ResourceObservation(1, 1, 1, Path("/storage")),
    )
    assert insufficient.reason == "observation_insufficient"


@pytest.mark.unit
def test_probe_and_values_reject_remaining_invalid_paths() -> None:
    assert isinstance(probe_resources(Path("relative")), ProbeFailure)
    assert isinstance(
        probe_resources(
            Path("/storage"),
            cpu_count=lambda: 1,
            read_meminfo=lambda: "MemFree: 1 kB\n",
            statvfs=lambda _: (_ for _ in ()).throw(OSError()),
        ),
        ProbeFailure,
    )
    assert isinstance(
        probe_resources(
            Path("/storage"),
            cpu_count=lambda: 0,
            read_meminfo=lambda: "MemAvailable: 1 kB\n",
            statvfs=lambda _: type("Stat", (), {"f_frsize": 1, "f_bavail": 1})(),
        ),
        ProbeFailure,
    )
    assert isinstance(
        probe_resources(
            Path("/storage"),
            cpu_count=lambda: 1,
            read_meminfo=lambda: "MemAvailable: invalid\n",
            statvfs=lambda _: type("Stat", (), {"f_frsize": 1, "f_bavail": 1})(),
        ),
        ProbeFailure,
    )
    with pytest.raises(InvalidResourceValueError):
        ProbeFailure("")
    with pytest.raises(InvalidResourceValueError):
        JobReservation("job", "тype", 1, 1, 1, 1)
    with pytest.raises(InvalidResourceValueError):
        LedgerState(
            ResourceBudget(1, 1, 1, 1),
            (JobReservation("job", "fit", 1, 1, 1, 1),) * 2,
        )
