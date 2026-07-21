# Resource Management

## Purpose

This document owns admission control for parallel fitting, generation, and
similarity work. Capture, conversion, and artifact publication retain their
stage-specific safety rules. The
[resource-management library](../libs/resource_management/README.md) implements
these shared decisions without owning application scheduling.

## Resource Budget

At launch, orchestrator records available logical CPUs and available memory.
Configuration declares a positive CPU reservation and positive memory
reservation for each job type, plus a positive maximum worker count. A job is
admitted only when all reservations fit current unreserved capacity.

For homogeneous jobs, maximum concurrent workers are:

```text
min(configured_max_workers,
    floor(available_logical_cpus / cpu_reservation),
    floor(available_memory_bytes / memory_reservation_bytes))
```

If result is zero, no job starts. Work remains queued and the run reports the
unsatisfied reservation. Mixed job types use the same per-job reservation test
rather than this homogeneous shortcut.

## Rules

- One capture workspace slot permits one capture only.
- Parent orchestration reserves resources before child launch and releases
  them only after child exit and result validation.
- A child process receives no more parallelism than its reservation permits.
- Memory pressure, unavailable storage, or failed reservation prevents new
  admission; it does not kill a running capture.
- Resource observations and decisions enter run lineage. Values are advisory
  for reproducibility, not a guarantee against operating-system contention.

## Reading

Follow [project architecture](README.md) and [genetic training](../apps/30_genetic_training/README.md)
before changing candidate scheduling.
