# IAT KS Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Canonical KS Comparison

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Extract IAT and publish result

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Implement and verify two-sided statistic

- **Objective:** Satisfy MIK-AC-001 and MIK-AC-002.
- **Implementation:** Add canonical extraction, validation, maintained KS
  adapter, score mapping, diagnostics, method schema, and result serialization.
- **Affected files:** `src/trafficlab/similarity_methods/iat_ks/`; tests.
- **Dependencies:** PCAPNG, temporal metadata, KS family, result contract.
- **Outputs:** Deterministic method result.
- **Tests:** Known statistic, ties/zero, unequal sizes, timestamp metadata, invalids, no p-value.
- **Validation:** Compare raw `D` with independent/library output and validate contract.
- **Completion criteria:** All MIK requirements pass.
