# Capture Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Lifecycle Functional Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Model target and recorder state transitions

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Implement deterministic lifecycle decisions

- **Objective:** Establish recorder-before-target ordering and separate outcomes.
- **Implementation:** Define request validation, state machine, completion
  policy interpretation, request/readiness binding and freshness checks,
  cleanup decisions, and post-cleanup artifact/publication plan.
- **Affected files:** `src/trafficlab/apps/capture/`; `tests/apps/capture/`.
- **Dependencies:** preflight contract, configuration, artifact and PCAPNG libraries.
- **Outputs:** Pure lifecycle API and typed failure/outcome records.
- **Tests:** State transition, timeout, interrupt, failed-target, cleanup,
  request mutation, binding, reservation-wait expiry, post-reservation
  substitution, and freshness property tests.
- **Validation:** Assert every transition sequence, terminal workspace state,
  and absence of successful publication before verified cleanup.
- **Completion criteria:** Lifecycle portions of CAP-AC-001, CAP-AC-002, and
  CAP-AC-004 pass.

## [PLAN] STAGE 2 — Invoker, Recorder, and Terminal Shell

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Integrate prepared workspace

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Capture target tree safely

- **Objective:** Publish canonical PCAPNG from supported workspace execution.
- **Implementation:** Implement local endpoint client, argument-vector
  launch, recorder readiness, pseudoterminal bridge, signal forwarding,
  post-reservation revalidation, cleanup, and detached publication status.
- **Affected files:** capture shell; fake and supported-environment tests.
- **Dependencies:** Stage 1 and manually prepared workspace.
- **Outputs:** Closed PCAPNG artifact and execution/network metadata.
- **Tests:** Fake invoker/recorder integration, terminal tests, injection tests,
  request/readiness substitution, stale decision, cleanup-before-publication,
  orphan artifact, manual WSL2 capture, and failure recovery.
- **Validation:** Validate hashes, packet closure, ordering, slot state, and lineage.
- **Completion criteria:** CAP-AC-001 through CAP-AC-004 pass.
