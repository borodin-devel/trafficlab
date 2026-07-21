# Genetic Training Software Requirements Specification

## Scope and Assumptions

Genetic training serves researchers selecting one genetic strategy, one
similarity method, allowed traffic models, and reference source. Child
applications are independently executable and contract-validated.

## Requirements

- **GTR-FR-001:** The application shall create, immutably configure, prepare, generate, and evaluate every candidate through documented child stages.
- **GTR-FR-002:** It shall execute the selected registered genetic strategy without redefining strategy mathematics.
- **GTR-FR-003:** It shall permit crossover only between same-name compatible traffic models.
- **GTR-FR-004:** It shall make every failed child or invalid result candidate ineligible to parent, elite, or win as required by strategy.
- **GTR-FR-005:** Neural candidates shall fit locally and evaluate every validation capture separately, using equal-weight arithmetic mean fitness.
- **GTR-FR-006:** It shall publish one winning self-describing model and ranking report only after successful stopping.
- **GTR-FR-007:** It shall never mutate a published builder and shall apply and validate all candidate parameters before model-owned preparation publishes a distinct generation-ready model.
- **GTR-IF-001:** CLI shall require explicit `--config-file` and `--output-dir` paths and support documented `--set` overrides.
- **GTR-IF-002:** Every artifact-producing child or preparation operation shall use a distinct private leaf attempt and be invoked with explicit candidate/unit paths by argument vector where a process boundary exists.
- **GTR-IF-003:** Exactly one reference selector shall be effective; contract selection shall require an explicit validated `target`, `uplink`, or `downlink` member with no default.
- **GTR-CFG-001:** Configuration shall validate reference mode, component names, strategy values, stopping, and model search spaces before population work.
- **GTR-CFG-002:** Configuration shall require positive packet, output-byte, and proposal limits and pass all three to every generation child.
- **GTR-NFR-001:** Genetic decisions and serialized lineage shall be deterministic for fixed inputs, results, seeds, and versions.
- **GTR-NFR-002:** Schema version 1 shall evaluate candidates serially; any future parallel schema shall require explicit resource reservations and restore canonical order before strategy decisions.
- **GTR-ERR-001:** No valid candidate, attempt exhaustion, invalid aggregate, resource-limit exhaustion, or corrupted lineage shall fail without a successful winner.
- **GTR-TST-001:** Shell tests shall use fake model creation, generation, and evaluation executables.

## Acceptance Criteria

- **GTR-AC-001:** Fixed fake-child results reproduce candidate decisions, winner, ranking, and lineage across repeated runs.
- **GTR-AC-002:** Child failures, invalid results, and one failed neural validation capture cannot produce a winning or partial aggregate candidate.
- **GTR-AC-003:** Version 1 never runs more than one candidate concurrently; future parallel fixtures never exceed their explicit worker, CPU, or memory reservations.
- **GTR-AC-004:** Builder mutation fixtures leave original bytes unchanged, every generated candidate descends from a fully configured generation-ready model with complete builder/reference lineage, and distinct operation attempts prevent detached-status collision.
- **GTR-AC-005:** Directional-package ambiguity, missing member selection, neural boundary leakage, or any generation limit reached before stop completion makes the affected candidate ineligible.

## Traceability

[SAD](SAD.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md) ·
[Authoritative schema](CONFIGURATION_SCHEMA.md) ·
[Genetic strategies](../../genetic_models/README.md) ·
[Traffic-model lifecycle](../../traffic_models/README.md#model-lifecycle)
