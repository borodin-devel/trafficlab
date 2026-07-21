# PCAPNG I/O Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Supported PCAPNG Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Select backend and implement immutable records

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Parse, validate, filter, and write fixtures

- **Objective:** Deliver deterministic PCAPNG behavior for pipeline contracts.
- **Implementation:** Select maintained backend; define records; implement
  streaming parse, policy validation, preservation filtering, and writing.
- **Affected files:** `src/trafficlab/libs/pcap_io/`; `tests/libs/pcap_io/`.
- **Dependencies:** infrastructure and artifact I/O.
- **Outputs:** Versioned API and supported/malformed fixture corpus.
- **Tests:** Round-trip, malformed, property, multi-interface, metadata tests.
- **Validation:** Compare retained records and hashes against fixture expectations.
- **Completion criteria:** PCP-AC-001 and PCP-AC-002 pass.
