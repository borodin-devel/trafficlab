# Shared Libraries

## Role

Libraries provide reusable in-process behavior to applications without owning
pipeline selection or standalone commands.

## Components

| Library | Responsibility |
| --- | --- |
| [artifact_io](artifact_io/README.md) | Validation and atomic publication |
| [pcap_io](pcap_io/README.md) | Deterministic PCAPNG reading and writing |
| [configuration](configuration/README.md) | Shared configuration resolution |
| [lineage](lineage/README.md) | Hash and provenance records |
| [resource_management](resource_management/README.md) | Parallel-work admission |
| [observability](observability/README.md) | Structured logging and events |

## Rules

Libraries expose deterministic functional cores and inject file, clock,
process, and operating-system effects through imperative shells. Exact Python
APIs remain unresolved until each roadmap's first stage.
