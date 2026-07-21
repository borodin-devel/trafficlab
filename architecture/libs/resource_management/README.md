# Resource Management Library

## Purpose and Responsibilities

`resource_management` admits and releases parallel jobs against explicit CPU,
memory, storage, and worker reservations.

## Inputs, Outputs, and Public Interface

Inputs are resource observations, configured budgets, and job reservations.
Outputs are deterministic admission decisions and retained decision records.
Exact Python signatures and operating-system probes are unresolved.

## Dependencies, Configuration, and Execution

It reads unprivileged system observations and applies
[system resource rules](../../project/RESOURCE_MANAGEMENT.md). It starts no
child process itself.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md)
