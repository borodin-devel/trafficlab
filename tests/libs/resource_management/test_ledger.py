"""Tests for deterministic atomic resource admission and release."""

from pathlib import Path
from random import Random

import pytest

from trafficlab.libs.resource_management import (
    JobReservation,
    LedgerState,
    ProbeFailure,
    ResourceBudget,
    ResourceCapacity,
    ResourceObservation,
    admit,
    release,
    remaining,
)


def _state() -> LedgerState:
    return LedgerState(ResourceBudget(4, 8, 16, 2))


def _observation() -> ResourceObservation:
    return ResourceObservation(4, 8, 16, Path("/storage"))


def _job(job_id: str = "job-1", *, storage: int = 4) -> JobReservation:
    return JobReservation(job_id, "fit", 1, 2, storage, 1)


@pytest.mark.unit
def test_admit_and_release_preserve_all_four_dimensions_atomically() -> None:
    state, decision = admit(_state(), _job(), _observation())

    assert decision.accepted
    assert remaining(state) == ResourceCapacity(3, 6, 12, 1)
    released, release_decision = release(state, "job-1", _observation())
    assert release_decision.accepted
    assert remaining(released) == ResourceCapacity(4, 8, 16, 2)


@pytest.mark.unit
def test_admission_records_deterministic_rejections_without_partial_reservation() -> (
    None
):
    state, accepted = admit(_state(), _job(), _observation())
    duplicate_state, duplicate = admit(state, _job(), _observation())
    rejected_state, rejected = admit(state, _job("job-2", storage=13), _observation())
    failed_state, failed = admit(state, _job("job-3"), ProbeFailure("statvfs failed"))

    assert accepted.reason == "accepted"
    assert duplicate.reason == "duplicate_job" and duplicate_state == state
    assert rejected.reason == "storage_exhausted" and rejected_state == state
    assert failed.reason == "probe_failed" and failed_state == state


@pytest.mark.unit
def test_unknown_release_keeps_ledger_unchanged() -> None:
    state, decision = release(_state(), "missing", _observation())

    assert state == _state()
    assert not decision.accepted
    assert decision.reason == "unknown_job"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("existing", "reason"),
    (
        pytest.param((2, 1, 1, 1), "cpu_exhausted", id="cpu"),
        pytest.param((1, 2, 1, 1), "memory_exhausted", id="memory"),
        pytest.param((1, 1, 2, 1), "storage_exhausted", id="storage"),
        pytest.param((1, 1, 1, 2), "worker_exhausted", id="workers"),
    ),
)
def test_admission_reports_first_exhausted_dimension(
    existing: tuple[int, int, int, int], reason: str
) -> None:
    state = LedgerState(
        ResourceBudget(2, 2, 2, 2),
        (JobReservation("existing", "fit", *existing),),
    )

    next_state, decision = admit(
        state,
        JobReservation("next", "fit", 1, 1, 1, 1),
        ResourceObservation(2, 2, 2, Path("/storage")),
    )

    assert next_state == state
    assert decision.reason == reason


@pytest.mark.unit
def test_seeded_admit_release_trace_preserves_resource_bounds() -> None:
    budget = ResourceBudget(6, 60, 600, 3)
    state = LedgerState(budget)
    random = Random(20260724)  # noqa: S311 - fixed deterministic test trace
    observation = ResourceObservation(6, 60, 600, Path("/storage"))

    for index in range(200):
        if state.active and random.random() < 0.4:
            job_id = state.active[random.randrange(len(state.active))].job_id
            state, _ = release(state, job_id, observation)
        else:
            reservation = JobReservation(
                f"job-{index}",
                "fit",
                random.randint(1, 3),
                random.randint(1, 30),
                random.randint(1, 300),
                1,
            )
            state, _ = admit(state, reservation, observation)

        available = remaining(state)
        assert tuple(job.job_id for job in state.active) == tuple(
            sorted(job.job_id for job in state.active)
        )
        assert all(
            0 <= value <= limit
            for value, limit in zip(
                (
                    available.cpu_units,
                    available.memory_bytes,
                    available.storage_bytes,
                    available.worker_slots,
                ),
                (
                    budget.cpu_units,
                    budget.memory_bytes,
                    budget.storage_bytes,
                    budget.worker_slots,
                ),
                strict=True,
            )
        )
