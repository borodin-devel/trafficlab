# Resource Management Software Architecture Document

## Context, Goals, and Boundaries

Model fitting and evaluation may run concurrently on limited hardware. The
library owns accounting and admission; orchestrators own queues and processes.

## Structure and Runtime Interaction

A probe shell captures available logical CPUs, memory, and storage. A pure
ledger validates positive budgets, reserves all requested dimensions
atomically, rejects overcommit, and releases by stable job identity. Decisions
record observations, configuration, current reservations, and reason.

## Errors, Performance, Security, and Logging

Invalid/overflowing quantities, duplicate reservations, unknown releases, and
probe failures are explicit. Accounting uses fixed-size job records and cannot
block capture to reclaim resources. Probes are read-only and unprivileged.

## Testing, Decisions, Risks, and Limits

State-machine and property tests cover conservation and deterministic queues.
Memory observations are advisory; the OS may contend independently. Exact
storage reservation policy and platform probe implementation remain unresolved.
