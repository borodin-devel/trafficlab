# Inspection Export Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Versioned Inspection Schema

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Define approved logical observations

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Specify schema and privacy boundary

- **Objective:** Resolve every output field without exposing payload or addresses.
- **Implementation:** Define Arrow/JSON types, units, nulls, schema version,
  row ordering, compression, batching, and manifest fields.
- **Affected files:** schema documentation, `src/trafficlab/apps/inspection_export/`, tests.
- **Dependencies:** PCAPNG, artifact I/O, lineage, PyArrow selection.
- **Outputs:** Machine-validated schema and privacy field allowlist.
- **Tests:** Schema fixtures, forbidden-field scan, null/unit validation.
- **Validation:** Validate representative rows against both encodings.
- **Completion criteria:** Schema resolves CONFIGS.md decisions and IEX-AC-002 passes.

## [PLAN] STAGE 2 — Streaming Dual Export

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Implement atomic package generation

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Stream, validate, and publish

- **Objective:** Produce matching Parquet and JSONL views with bounded memory.
- **Implementation:** Add source validation, streaming batches, dual
  serialization, row counts, non-self-referential hashes, manifest validation,
  atomic publication, and detached status.
- **Affected files:** inspection export shell and integration tests.
- **Dependencies:** Stage 1 and shared libraries.
- **Outputs:** Complete inspection package.
- **Tests:** Exact fixture, direct source lineage, directional status/member
  ambiguity, large-stream, manifest self-entry, status/orphan, malformed input,
  failure injection, and PyArrow readback.
- **Validation:** Cross-compare every logical row and validate all hashes.
- **Completion criteria:** IEX-AC-001 through IEX-AC-003 pass.
