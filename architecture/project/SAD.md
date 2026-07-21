# Trafficlab Software Architecture Document

## Context and Goals

Trafficlab is a file-coupled research suite for capturing, modelling,
generating, and comparing Layer 2 traffic. Its goals are reproducible partial
or complete pipeline execution, replaceable mathematical components, explicit
lineage, and safe Linux capture. Native Windows execution is a non-goal.

## Components and Boundaries

Standalone applications are catalogued under [applications](../apps/README.md).
Reusable boundaries are catalogued under [libraries](../libs/README.md). File contracts
belong to [contracts](../contracts/README.md). Replaceable algorithms belong to
[traffic models](../traffic_models/README.md),
[genetic models](../genetic_models/README.md), and
[similarity methods](../similarity_methods/README.md). Manual privileged
operations belong to [scripts](../scripts/README.md).

## Runtime and Data Flow

The canonical flow and partial execution modes are defined in
[workflows](WORKFLOWS.md). PCAPNG is canonical packet interchange. Producers
validate and hash complete artifacts before atomic publication; consumers
accept explicit paths and validate contracts before processing. Application
attempt directories retain startup/diagnostic state while successful artifact
destinations remain absent until publication; detached status is the success
commit marker under [artifact I/O](../libs/artifact_io/README.md).

## Internal Structure and Extension

The functional-core and imperative-shell boundary, proposed source tree, and
replaceable-module registration are defined in
[implementation structure](IMPLEMENTATION_STRUCTURE.md). A new model, method,
strategy, application, library, script, or contract requires its own component
directory, SAD, SRS, and navigable README.

## Configuration, Errors, and Observability

Configuration follows [system configuration](CONFIGS.md). Invalid input,
configuration, lineage, or output fails the affected stage without publishing
a successful artifact. Cross-cutting structured diagnostics follow
[logging](LOGGING.md); resource admission follows
[resource management](RESOURCE_MANAGEMENT.md).

## Performance and Resources

Candidate evaluation is serial in schema version 1. Any future parallel
candidate work is bounded by explicit CPU, memory, storage, and worker
reservations before it can be enabled.
Capture retains one exclusive workspace slot. Packet-path work avoids
synchronous logging and unbounded buffering.

## Security

Capture, namespaces, privileges, target commands, paths, external processes,
and untrusted files are security boundaries. Normal applications use no
privilege elevation. Target commands use argument vectors. Manual privileged
setup and rollback remain manifest-scoped.

## Testing Architecture

Tests mirror component directories. Functional cores use deterministic unit
and property tests. File/process shells use temporary directories and fake
processes. Mathematical components add statistical, numerical, metamorphic,
and independent-reference tests. Privileged effects require manual supported-
environment verification; automated tests never mutate the host.

## Decisions, Risks, and Limits

Files, rather than an in-process plugin host, isolate applications and support
partial execution. Deterministic serialization and recorded versions improve
reproducibility but cannot eliminate operating-system contention or numerical
variation outside supported dependencies. Advanced stub components remain
unselectable until their requirements and mathematics are resolved.
