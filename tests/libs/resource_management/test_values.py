"""Tests for immutable resource-admission values."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from trafficlab.libs.resource_management import (
    AdmissionDecision,
    InvalidResourceValueError,
    JobReservation,
    LedgerState,
    ProbeFailure,
    ResourceBudget,
    ResourceCapacity,
    ResourceObservation,
)


@pytest.mark.unit
def test_resource_values_are_frozen_and_accept_positive_dimensions() -> None:
    budget = ResourceBudget(4, 8_192, 16_384, 2)
    observation = ResourceObservation(4, 8_192, 16_384, Path("/storage"))
    reservation = JobReservation("job-1", "fit", 1, 2_048, 4_096, 1)

    assert observation.storage_path == Path("/storage")
    assert reservation.job_id == "job-1"
    assert ProbeFailure("statvfs failed").reason == "statvfs failed"
    with pytest.raises(FrozenInstanceError):
        budget.cpu_units = 3  # type: ignore[misc]


@pytest.mark.unit
@pytest.mark.parametrize(
    "factory",
    (
        pytest.param(lambda: ResourceBudget(0, 1, 1, 1), id="zero-budget"),
        pytest.param(
            lambda: ResourceObservation(1, 1, 1, Path("relative")),
            id="relative-path",
        ),
        pytest.param(lambda: JobReservation("", "fit", 1, 1, 1, 1), id="empty-job"),
        pytest.param(
            lambda: JobReservation("job", "fit", True, 1, 1, 1),
            id="boolean-quantity",
        ),
        pytest.param(
            lambda: ResourceBudget(1 << 63, 1, 1, 1),
            id="overflow-budget",
        ),
        pytest.param(
            lambda: ResourceCapacity(-1, 0, 0, 0),
            id="negative-capacity",
        ),
    ),
)
def test_resource_values_reject_invalid_boundaries(factory: object) -> None:
    with pytest.raises(InvalidResourceValueError):
        factory()  # type: ignore[operator]


@pytest.mark.unit
def test_resource_observation_rejects_unnormalized_storage_path() -> None:
    with pytest.raises(InvalidResourceValueError):
        ResourceObservation(1, 1, 1, Path("/storage/../other"))


@pytest.mark.unit
def test_ledger_state_and_decision_reject_invalid_public_values() -> None:
    observation = ResourceObservation(1, 1, 1, Path("/storage"))
    capacity = ResourceCapacity(1, 1, 1, 1)
    with pytest.raises(InvalidResourceValueError):
        LedgerState(object())  # type: ignore[arg-type]
    with pytest.raises(InvalidResourceValueError):
        AdmissionDecision("", True, "accepted", observation, capacity, capacity)
    with pytest.raises(InvalidResourceValueError):
        AdmissionDecision("job", True, "unrecognized", observation, capacity, capacity)
    with pytest.raises(InvalidResourceValueError):
        LedgerState(
            ResourceBudget(1, 1, 1, 1),
            (object(),),  # type: ignore[arg-type]
        )
    with pytest.raises(InvalidResourceValueError):
        AdmissionDecision("job", "yes", "accepted", observation, capacity, capacity)  # type: ignore[arg-type]
    with pytest.raises(InvalidResourceValueError):
        AdmissionDecision("job", True, "accepted", object(), capacity, capacity)  # type: ignore[arg-type]
    with pytest.raises(InvalidResourceValueError):
        AdmissionDecision(
            "job",
            True,
            "accepted",
            observation,
            ResourceBudget(1, 1, 1, 1),  # type: ignore[arg-type]
            capacity,
        )
