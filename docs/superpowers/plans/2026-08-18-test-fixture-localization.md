# [PLAN-2-4d3e720f] Test Fixture Localization Implementation Plan

**Goal:** Localize test fixtures below `tests/`, keep runnable example assets
below `examples/`, and remove production dependencies on test infrastructure.

**Spec:** `docs/superpowers/specs/2026-08-18-test-fixture-localization-design.md`

### [TASK-1-fc5a0082] Define the ownership contract

- [ ] **[STEP-1-72fe7d04]** Add RED layout tests for split roots, manifests,
  misplaced data, the path catalog, and production dependency direction.
- [ ] **[STEP-2-c947b17f]** Update the layout checker without adding a package or
  runtime dependency.

### [TASK-2-1c0e3db7] Move data without changing bytes

- [ ] **[STEP-3-4af43c42]** Move example pipeline assets to `examples/data/`.
- [ ] **[STEP-4-d73b30ea]** Move static test assets to domain directories below
  `tests/fixtures/data/` and move the shared path catalog beside them.
- [ ] **[STEP-5-5852642e]** Generate independent canonical manifests and prove
  byte/mode parity through Git rename detection and focused tests.

### [TASK-3-a37e9a41] Migrate consumers and provenance

- [ ] **[STEP-6-b872ba1d]** Update example configuration, test imports, Docker
  contexts, fixture generators, and current architecture references.
- [ ] **[STEP-7-257be38b]** Remove fixture-specific filesystem paths from the
  public auditor and permit only general non-production `tests/` descendants.
- [ ] **[STEP-8-ec72f779]** Commit the source/example migration before regenerating
  the source-bound validation fixture.

### [TASK-4-9269c6d0] Regenerate and verify deterministic data

- [ ] **[STEP-9-9e0fe8f7]** Regenerate the validation-study fixture against the
  source commit and update the test-data manifest.
- [ ] **[STEP-10-f844d531]** Run generator checks, copied-bundle audit, focused
  fixture owners, Ruff, and strict Pyright.

### [TASK-5-e60d36fc] Complete gates and review

- [ ] **[STEP-11-6bdfe6d7]** Run bounded parallel non-external tests and serial
  branch coverage at or above 90%.
- [ ] **[STEP-12-59dcfb0c]** Run the available bounded Docker fixture matrix with
  exact owned-resource cleanup.
- [ ] **[STEP-13-d6d5c55a]** Obtain independent review, fix every Critical or
  Important finding, commit coherent increments, and finish with a clean tree.
