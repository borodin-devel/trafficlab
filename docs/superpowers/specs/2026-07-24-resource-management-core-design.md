# Resource Management Core Design

**Date:** 2026-07-24
**Status:** Implemented
**Owner:** Trafficlab maintainers

## Purpose

Implement Resource Management roadmap STEP 1.1 as a deterministic,
four-dimension admission ledger. The pure core validates CPU, memory, storage,
and worker budgets/reservations; atomically accepts or rejects one stable job
identity; and releases exactly the held reservation. A narrow, unprivileged
Linux shell captures available CPUs, memory, and storage for an explicit path.

The design applies [Amendment 0002](../../../architecture/amendments/0002_RESOURCE_STORAGE_RESERVATION.md).
It does not schedule jobs, launch children, choose a storage path, kill
running work, reserve OS quotas, or make probe observations a guarantee against
independent OS contention.

## Public Values

All public values are frozen, slotted dataclasses. Quantities are exact Python
integers, reject booleans, and are bounded by signed 64-bit maximum to keep
records finite.

`ResourceBudget` has positive `cpu_units`, `memory_bytes`, `storage_bytes`, and
`worker_slots`. `ResourceCapacity` has the same nonnegative fields and records
remaining capacity, including an exhausted zero dimension.
`ResourceObservation` has positive available quantities plus an absolute
normalized `storage_path`; a failed observation is represented by
`ProbeFailure(reason)` instead of fabricated zero capacity.

`JobReservation` has non-empty safe `job_id`, non-empty `job_type`, and
positive CPU, memory, storage, and worker requests. `LedgerState` contains one
budget and a tuple of active reservations canonicalized by `job_id`.

`AdmissionDecision` records `job_id`, `accepted`, `reason`, the observation or
probe failure supplied to the operation, and remaining capacities before and
after. Reasons are exactly `accepted`, `duplicate_job`,
`probe_failed`, `observation_insufficient`, `cpu_exhausted`,
`memory_exhausted`, `storage_exhausted`, `worker_exhausted`, or
`unknown_job`.

Every decision also retains immutable `LedgerState` snapshots before and after
the operation. Each snapshot contains the configured budget and canonical
active reservations, so a retained decision independently records the
configuration and current reservations required for diagnosis and lineage.
Decision validation requires both snapshots to use the same budget and requires
each recorded capacity to equal the capacity derived from its corresponding
snapshot.

## Core Transitions

`admit(state, reservation, observation) -> (LedgerState, AdmissionDecision)`
is pure. It first rejects duplicate job identity. A `ProbeFailure` rejects with
`probe_failed`; an observation below any budget rejects with
`observation_insufficient`. Otherwise it subtracts active reservations from the
configured budget, checks dimensions in CPU/memory/storage/worker order, and
either returns unchanged state plus the first exact exhaustion reason or adds
the complete reservation. No partial dimension is ever retained.

`release(state, job_id, observation) -> (LedgerState, AdmissionDecision)` is
pure. It removes precisely one canonical reservation; unknown identity returns
unchanged state and `unknown_job`. Release records the current observation for
lineage but does not require it to succeed, because failed new admission must
not prevent release of already-finished work.

`remaining(state) -> ResourceCapacity` subtracts active reservations from budget
and raises `InconsistentLedgerError` if a supplied state exceeds any bound.
Builders and every transition verify this invariant, so a valid state always
has nonnegative capacity and no duplicate job identity.

## Linux Probe Shell

`probe_resources(storage_path: Path) -> ResourceObservation | ProbeFailure`
performs read-only, unprivileged observation. It requires one absolute
normalized path, uses `os.cpu_count()`, reads `MemAvailable` from a bounded
`/proc/meminfo` snapshot, and calculates available storage as
`statvfs.f_frsize * statvfs.f_bavail` with overflow checking. A missing CPU
count, absent/malformed `MemAvailable`, invalid storage path, failing `statvfs`,
zero available quantity, or integer overflow returns a single-line
`ProbeFailure`; it does not raise raw OS errors or attempt retry/logging.

The shell accepts injected CPU, proc-read, and `statvfs` callables for
deterministic tests. It opens no files for writing, executes no process, and
uses no elevation.

## Package Structure

```text
src/trafficlab/libs/resource_management/
  __init__.py     public facade
  errors.py       typed validation and accounting failures
  values.py       immutable budget, capacity, observation, reservation, decision values
  ledger.py       pure remaining/admit/release transitions
  probe.py        injected read-only Linux observation shell
tests/libs/resource_management/
  __init__.py
  test_values.py
  test_ledger.py
  test_probe.py
  test_defensive_paths.py
```

## Testing

Tests use a deterministic matrix of generated reservation/release traces
without a new test dependency. After each transition they assert active
reservations are unique, every dimension is nonnegative, and each total never
exceeds budget. Fixtures cover each exhaustion reason, atomic multi-dimension
rejection, duplicate identity, exact release, unknown release, zero capacity,
probe failures, overflow, malformed meminfo, deterministic ordering, decision
snapshots, and injected Linux probe values. Full repository formatting, lint,
Pyright, 100% coverage, corpus, whitespace, docs, and wheel checks must pass
before completion.

## Compatibility and Limits

This is the first public Resource Management API. Callers must now provide
positive storage budgets/reservations and an explicit storage path as defined
by Amendment 0002. The library supports Linux/Python 3.12 and records
observations for reproducibility; it does not enforce cgroups, quotas,
filesystem allocation, CPU affinity, dynamic re-admission, queues, or worker
process lifecycle.
