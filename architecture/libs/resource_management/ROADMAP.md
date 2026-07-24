# Resource Management Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [DONE] STAGE 1 — Deterministic Admission Ledger

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.
- **Evidence:** Step 1.1 is `[DONE]` with four-dimension deterministic admission.

### [DONE] STEP 1.1 — Implement resource accounting

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.
- **Evidence:** Substep 1.1.1 covers RES-AC-001 and RES-AC-002.

#### [DONE] SUBSTEP 1.1.1 — Validate, reserve, release, and report

- **Objective:** Enforce every configured resource bound.
- **Implementation:** Define quantities, probes, immutable ledger
  transitions, atomic multi-resource admission, releases, and diagnostics.
- **Affected files:** `src/trafficlab/libs/resource_management/`; `tests/libs/resource_management/`.
- **Dependencies:** observability and supported Linux probes.
- **Outputs:** Versioned admission interface and decision records.
- **Tests:** State-machine, property, overflow, probe-failure, deterministic-order tests.
- **Validation:** Run randomized traces and assert all invariants after every transition.
- **Completion criteria:** RES-AC-001 and RES-AC-002 pass.
- **Evidence:** Tests cover four-dimension atomic admission/release, deterministic
  rejection, probe failure, malformed observations, and capacity invariants.
