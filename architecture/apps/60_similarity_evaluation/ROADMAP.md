# Similarity Evaluation Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Method Protocol and Result Contract

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Execute one mature comparison

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Validate inputs, score, and publish

- **Objective:** Produce contract-valid deterministic similarity results.
- **Implementation:** Add CLI/configuration, method registry/protocol,
  input validation, typed numerical failures, diagnostics, lineage, atomic
  non-self-hashing result, and detached status.
- **Affected files:** `src/trafficlab/apps/similarity_evaluation/`; tests.
- **Dependencies:** PCAPNG, artifact, lineage, one mature method, result contract.
- **Outputs:** Versioned method adapter and `similarity.toml`.
- **Tests:** Per-method fixtures, unsupported/stub, malformed input, deterministic
  serialization, self-digest/status/orphan, numerical and publication failure injection.
- **Validation:** Validate every output with result-contract validator and hashes.
- **Completion criteria:** SIM-AC-001 through SIM-AC-003 pass.
