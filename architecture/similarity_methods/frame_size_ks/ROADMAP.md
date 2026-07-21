# Frame-Size KS Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Canonical KS Comparison

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Extract lengths and publish result

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Implement and verify original-length statistic

- **Objective:** Satisfy MFS-AC-001 and MFS-AC-002.
- **Implementation:** Add extraction/validation, maintained KS adapter,
  score/diagnostics, method schema, and result serialization.
- **Affected files:** `src/trafficlab/similarity_methods/frame_size_ks/`; tests.
- **Dependencies:** PCAPNG, KS family, result contract.
- **Outputs:** Deterministic method result.
- **Tests:** Known KS, ties, truncation, extended lengths, malformed metadata, byte invariance.
- **Validation:** Compare raw `D` independently and validate contract/no p-value.
- **Completion criteria:** All MFS requirements pass.
