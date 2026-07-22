# Lineage Library Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [DONE] STAGE 1 — Hash and Provenance Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.
- **Evidence:** Step 1.1 and Substep 1.1.1 are `[DONE]`; the public facade and
  focused, aggregate, static, documentation, coverage, and reproducible-build
  evidence below satisfy the stage criteria.

### [DONE] STEP 1.1 — Implement canonical lineage

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.
- **Evidence:** Substep 1.1.1 implements the versioned typed API, stable file
  snapshot shell, detached hash-domain and graph validators, and manifest-first
  package validation required by LIN-AC-001 through LIN-AC-003.

#### [DONE] SUBSTEP 1.1.1 — Hash, serialize, and validate provenance

- **Objective:** Satisfy LIN-AC-001 through LIN-AC-003.
- **Implementation:** Add streaming hash, canonical builders, detached hash
  domains, local/external path snapshot validation, graph validation, and typed errors.
- **Affected files:** `src/trafficlab/libs/lineage/`; `tests/libs/lineage/`.
- **Dependencies:** artifact I/O contract needs.
- **Outputs:** Versioned lineage representation and validators.
- **Tests:** Known-vector, ordering, path, mutation, self-hash, detached-status,
  graph, and property tests.
- **Validation:** Compare exact fixtures and verify self-reference, mutation,
  changed external source, and broken-parent rejection.
- **Completion criteria:** All lineage requirements and LIN-AC-001 through
  LIN-AC-003 pass.
- **Evidence:** `src/trafficlab/libs/lineage/` exposes typed canonical values,
  exact-byte bounded snapshots, detached domains, deterministic graph checks,
  and manifest-first package validation. The 174 focused tests cover published
  SHA-256 vectors, permutation-invariant ordering, local/external path and
  no-follow boundaries, deterministic mutation and replacement detection,
  self-hash/domain rejection, detached manifest sequencing, package members,
  and missing-parent/cycle graphs. The locked aggregate gate passed 417 tests,
  Ruff formatting and linting, strict Pyright, whitespace and all 319
  architecture Markdown files, 100% statement and branch coverage (511
  statements and 158 branches), and wheel construction. The aggregate build
  and an independent repeated build produced identical SHA-256
  `362da122eb23385d6047e52e2d96247ea1e4a5971fd71fc700046cdb5b3e859e`.
