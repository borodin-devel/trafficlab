# Convert Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Direction Classification Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Define profile and packet decisions

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Classify without rewriting

- **Objective:** Deterministically assign only confidently identified IP packets.
- **Implementation:** Resolve profile schema; implement pure classification,
  exclusion reasons, counters, and report ordering.
- **Affected files:** `src/trafficlab/apps/convert/`; `tests/apps/convert/`.
- **Dependencies:** PCAPNG library and capture-directions contract.
- **Outputs:** Typed profile, classifier, and report values.
- **Tests:** IPv4/IPv6, MAC evidence, ambiguous, non-IP, multi-interface tables.
- **Validation:** Compare every fixture packet to expected decision and reason.
- **Completion criteria:** Classification portion of CNV-AC-001 passes.

## [PLAN] STAGE 2 — Package Publication

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Filter and publish directional PCAPNG

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Build validated contract package

- **Objective:** Publish exact complete, uplink, and downlink artifacts.
- **Implementation:** Stream input, preserve selected records, build report,
  hash every non-manifest member, build a non-self-referential manifest,
  validate/publish the package atomically, and publish detached status.
- **Affected files:** convert shell and integration tests.
- **Dependencies:** Stage 1, artifact I/O, lineage, PCAPNG I/O.
- **Outputs:** Complete capture-directions package.
- **Tests:** Byte/metadata preservation, hash/status mutation, manifest
  self-entry, orphan, malformed, failure injection, and atomicity.
- **Validation:** Re-read output and compare retained packet records and hashes.
- **Completion criteria:** CNV-AC-001 and CNV-AC-002 pass.
