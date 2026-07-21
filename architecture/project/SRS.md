# Trafficlab Software Requirements Specification

## Purpose, Scope, and Stakeholders

This specification covers the complete research pipeline and its shared
quality constraints. Stakeholders are researchers, developers, operators, and
maintainers of models and comparison methods.

## Terminology and Assumptions

- **Reference traffic:** validated captured PCAPNG used for fitting or scoring.
- **Synthetic traffic:** PCAPNG generated from a validated traffic-model file.
- **Artifact:** immutable published file or package with validation and lineage.
- Linux execution and a prepared capture workspace are assumed where capture
  is requested.

## Functional Requirements

- **SYS-FR-001:** The system shall assess capture readiness before target launch.
- **SYS-FR-002:** The system shall capture one requested target process tree for
  a configured duration or until the target tree terminates.
- **SYS-FR-003:** The system shall preserve canonical PCAPNG and derive explicit
  directional artifacts without rewriting selected packets.
- **SYS-FR-004:** The system shall export bounded ML and LLM inspection formats
  while retaining source-PCAPNG lineage.
- **SYS-FR-005:** The system shall create, fit, validate, and serialize named
  replaceable traffic models.
- **SYS-FR-006:** The system shall generate validated synthetic PCAPNG from one
  selected model file.
- **SYS-FR-007:** The system shall compare one reference and one generated
  PCAPNG with one named similarity method.
- **SYS-FR-008:** The system shall evolve and rank candidates with one named
  genetic strategy while excluding failed candidates from winning.
- **SYS-FR-009:** The system shall support end-to-end, partial-pipeline, and
  individual-application execution using explicit paths.

## Interface and Data Requirements

- **SYS-IF-001:** Applications shall exchange data only through documented file
  contracts, never another application's configuration.
- **SYS-IF-002:** Every successful artifact shall provide a schema or format
  identity, non-self-referential SHA-256 hashes, source lineage, and detached
  validation status.
- **SYS-IF-003:** Packet interchange shall use PCAPNG; typed ML tables shall use
  Apache Parquet; bounded LLM inspection records shall use JSON Lines.

## Non-Functional Requirements

- **SYS-NFR-001:** Production Python shall target Python 3.12.
- **SYS-NFR-002:** Identical validated inputs, seeds, configuration, and
  supported dependency versions shall produce deterministic decisions,
  ordering, and serialization.
- **SYS-NFR-003:** Successful file publication shall be atomic.
- **SYS-NFR-004:** Parallel work shall be admitted only within explicit CPU,
  memory, storage, and worker limits.
- **SYS-NFR-005:** Normal application execution shall not request privilege
  elevation or broaden manual workspace authority.
- **SYS-NFR-006:** Logging shall be structured, level-controlled, bounded, and
  nonblocking on packet paths.
- **SYS-NFR-007:** Tests shall mirror application, library, model, method,
  script, and contract boundaries.

## Error and Testability Requirements

- **SYS-ERR-001:** A failed stage shall retain diagnostics and shall not expose
  incomplete output as successful.
- **SYS-ERR-002:** A pipeline shall not launch a dependent stage after an
  upstream contract fails validation.
- **SYS-TST-001:** Automated tests shall require no elevation and shall not
  mutate real system resources.
- **SYS-TST-002:** Mathematical implementations shall have direct correctness
  tests against authoritative examples, invariants, or independent methods.

## Acceptance Criteria

- **SYS-AC-001:** Every invoked successful stage publishes its documented
  artifact and all hashes and lineage resolve.
- **SYS-AC-002:** End-to-end and partial workflows pass contract validation at
  every producer-consumer boundary.
- **SYS-AC-003:** Required unit, integration, static-analysis, formatting, link,
  and documentation checks pass for a release.

## Traceability

[Configuration](CONFIGS.md)

| System requirements | Refining owners | Implementation path |
| --- | --- | --- |
| SYS-FR-001 | [Capture request](../contracts/00_capture_request/SRS.md), [preflight](../apps/00_preflight/SRS.md), [capture](../apps/10_capture/SRS.md), [readiness contract](../contracts/00_10_capture_readiness/SRS.md) | [Roadmap stage 2](ROADMAP.md#plan-stage-2--capture-and-reference-preparation) |
| SYS-FR-002 | [Capture](../apps/10_capture/SRS.md) and [workspace scripts](../scripts/README.md) | [Roadmap stage 2](ROADMAP.md#plan-stage-2--capture-and-reference-preparation) |
| SYS-FR-003 | [Capture](../apps/10_capture/SRS.md), [convert](../apps/20_convert/SRS.md), [directions contract](../contracts/10_20_capture_directions/SRS.md) | [Roadmap stage 2](ROADMAP.md#plan-stage-2--capture-and-reference-preparation) |
| SYS-FR-004 | [Inspection export](../apps/25_inspection_export/SRS.md) and [dataset contract](../contracts/25_inspection_dataset/SRS.md) | [Roadmap stage 2](ROADMAP.md#plan-stage-2--capture-and-reference-preparation) |
| SYS-FR-005 | [Model creation](../apps/40_model_creation/SRS.md) and [traffic-model owners](../traffic_models/README.md) | [Roadmap stage 3](ROADMAP.md#plan-stage-3--modelling-generation-and-similarity) |
| SYS-FR-006 | [Traffic generation](../apps/50_traffic_generation/SRS.md) and [traffic-model owners](../traffic_models/README.md) | [Roadmap stage 3](ROADMAP.md#plan-stage-3--modelling-generation-and-similarity) |
| SYS-FR-007 | [Similarity evaluation](../apps/60_similarity_evaluation/SRS.md), [method owners](../similarity_methods/README.md), [result contract](../contracts/60_30_similarity_result/SRS.md) | [Roadmap stage 3](ROADMAP.md#plan-stage-3--modelling-generation-and-similarity) |
| SYS-FR-008 | [Genetic training](../apps/30_genetic_training/SRS.md) and [strategy owners](../genetic_models/README.md) | [Roadmap stage 4](ROADMAP.md#plan-stage-4--genetic-training-and-orchestration) |
| SYS-FR-009 | [Trafficlab orchestrator](../apps/99_trafficlab/SRS.md) | [Roadmap stage 4](ROADMAP.md#plan-stage-4--genetic-training-and-orchestration) |
| SYS-IF-001–003, SYS-NFR-002–006, SYS-ERR-001–002 | [File contracts](../contracts/README.md), [shared libraries](../libs/README.md), and [system SAD](SAD.md) | [Roadmap stages 1–4](ROADMAP.md) |
| SYS-NFR-001, SYS-NFR-007, SYS-TST-001–002, SYS-AC-001–003 | [Infrastructure](../infrastructure/SRS.md) and every component SRS/roadmap | [Infrastructure roadmap](../infrastructure/ROADMAP.md) and [system roadmap](ROADMAP.md) |

Component acceptance identifiers referenced by a component roadmap are the
verification bridge between each refining SRS and its implementation work.
