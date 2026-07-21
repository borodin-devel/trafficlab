# Project SAD/SRS Consolidation Design

## Context

Existing architecture owners define capture, conversion, model, generation,
similarity, genetic training, orchestration, configuration, and workspace
boundaries. Project-level SRS traceability, resource admission, logging,
implementation layout, roadmap, and required ML/LLM inspection export were
not owned in the architecture tree.

## Alternatives

1. One monolithic SAD/SRS document. Rejected: duplicates owner facts and makes
   contracts difficult to evolve.
2. Add project-level owners that link to detailed owners. Selected: one fact
   has one owner while requirements remain traceable.
3. Implement an unplanned source tree. Rejected: current architecture marks
   applications unimplemented; code would select contracts and algorithms
   before their delivery increments.

## Architecture

`architecture/project/` owns system requirements, workflow composition,
source/test boundaries, resource admission, logging, and delivery state. It
links to existing application and contract owners for detailed behavior.

`25_inspection_export` is an independently runnable auxiliary application.
It reads validated PCAPNG and publishes a hash-verified Apache Parquet plus
JSON Lines inspection package. It does not feed training and therefore is not
an orchestrator experiment stage.

## Data and Safety

PCAPNG remains packet source of truth. Parquet contains typed ML observations;
JSON Lines contains approved bounded inspection fields. Both retain source
hashes and schema version. Default export excludes payload and addresses.

Parallel execution uses positive per-job CPU and memory reservations. Capture
remains single-slot. Structured logs use bounded nonblocking packet-path
events and retain severe diagnostics.

## Verification

Documentation validation checks patch whitespace, owner-link targets, absence
of placeholders, and roadmap synchronization. Future implementation increments
must use the project roadmap's declared unit, integration, property,
statistical, metamorphic, resource, schema, and privileged-manual test types.

## Scope

This change formalizes architecture and requirements. It creates no runtime
implementation, configuration template, dataset, fixture, package manifest, or
traffic-capture setup.
