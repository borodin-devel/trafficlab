# Amendment 0002: Resource Storage Reservation

- **Status:** Approved
- **Owner:** Resource-management architecture owner
- **Decision date:** 2026-07-24

## Conflicting Sources

The [Resource Management SAD](../libs/resource_management/SAD.md) requires a
probe shell for logical CPUs, memory, and storage, and a pure ledger that
reserves every requested dimension atomically. Its SRS requires admission only
when every requested dimension fits unreserved capacity.

The system [Resource Management](../project/RESOURCE_MANAGEMENT.md) defines
positive CPU and memory reservations plus maximum workers, but leaves exact
storage reservation policy unresolved. It nevertheless requires unavailable
storage to prevent new admission. Without a storage budget and a per-job
reservation, the library cannot decide whether available storage is sufficient
or fulfill atomic multi-resource admission.

## Decision

Resource Management version 1 has four positive reservation dimensions:

- logical CPU units;
- memory bytes;
- storage bytes; and
- worker slots.

An orchestrator supplies an explicit `ResourceBudget` containing total logical
CPU units, memory bytes, storage bytes, and maximum worker slots. Every
`JobReservation` contains positive values for the same four dimensions. The
ledger admits a stable job identity only when all four requested values fit the
budget remaining after every active reservation. Admission reserves all four
dimensions atomically; release returns exactly that job's recorded reservation.

The supported Linux probe obtains CPU availability from `os.cpu_count()`,
memory availability from `/proc/meminfo` `MemAvailable`, and storage
availability from `os.statvfs` available bytes for an explicit absolute
normalized storage path. Any missing, malformed, zero, overflowed, or failed
observation produces a failed observation record and prevents new admission.
It never terminates work already admitted.

## Rationale

Storage is a consumable capacity for model artifacts, generated PCAPNG, and
temporary processing data. A boolean storage-ready check cannot prove that a
specific job fits alongside current reservations. Explicit byte quantities make
the ledger deterministic, preserve conservation invariants, and satisfy both
the library's atomic-dimension boundary and the system rule that unavailable
storage blocks new work.

## Scope and Consequences

This amendment applies only to the Resource Management library's first
admission interface and its callers. Applications continue to own scheduling,
child processes, queues, workspace policy, and storage-location selection.
They must record the observed storage path and available bytes in their
decision/lineage inputs and must supply a matching positive storage reservation
for each job type.

The ledger remains advisory: an accepted reservation does not guarantee the
operating system will not consume capacity independently. Probe results are a
launch-time observation and do not cause the library to reclaim or kill a
running job.

## Alternatives Considered

1. **CPU, memory, and worker dimensions only.** Rejected because it contradicts
   the library's storage dimension and leaves the system's storage-failure rule
   unenforceable as a capacity decision.
2. **A boolean storage-ready probe.** Rejected because it cannot atomically
   reserve bytes or decide whether mixed job reservations fit together.
3. **Implicit storage reservation derived from memory.** Rejected because disk
   use is workload-specific and an implicit ratio is neither reproducible nor
   configurable.
4. **Defer all storage handling.** Rejected because it would leave the first
   roadmap step unable to satisfy its stated four-dimension admission scope.

## Compatibility and Migration

No runtime Resource Management API exists before this amendment. New callers
must add positive storage-byte values to their resource budgets and job-type
reservations, and must provide an explicit storage path for Linux observation.
No persisted artifact or contract schema changes. A future storage quota,
filesystem-reservation, or dynamic re-probe policy requires a separate
amendment and explicit migration path.
