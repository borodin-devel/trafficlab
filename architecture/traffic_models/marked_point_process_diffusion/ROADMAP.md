# Marked Point-Process Diffusion Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Representation and Diffusion Core

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Implement bounded windows and mathematical kernels

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Verify masks, schedules, forward/reverse equations

- **Objective:** Satisfy TPD-AC-001.
- **Implementation:** Add window representation/context, count head,
  schedule, forward noise, denoiser interface, DDPM reverse, decode/support checks.
- **Affected files:** `src/trafficlab/traffic_models/marked_point_process_diffusion/`; tests.
- **Dependencies:** common neural rules and deterministic neural runtime.
- **Outputs:** Pure bounded diffusion-window interface.
- **Tests:** Hand equations, masks/counts, empty/overflow, support, conversion, invalid schedules.
- **Validation:** Compare independent numerical examples at every step.
- **Completion criteria:** TPD-AC-001 and core TPD-AC-003 cases pass.

## [PLAN] STAGE 2 — Deterministic Fitting and Serialization

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Train count and value model

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Reproduce tiny fixture checkpoint

- **Objective:** Create one deterministic trained artifact.
- **Implementation:** Add preparation adapter, seeded noise/batches,
  immutable candidate-before-preparation transition, loss/optimizer, finite
  checks, validation, checkpointing, canonical weights/lineage.
- **Affected files:** fitting core and tests.
- **Dependencies:** Stage 1, artifact/lineage/configuration.
- **Outputs:** Valid trained model.
- **Tests:** Tiny fixture, builder immutability, split-before-windows,
  empty-window loss, checkpoint tie, patience, path/hash, non-finite failures.
- **Validation:** Repeat and compare epoch, metrics, weights, hash.
- **Completion criteria:** Fitting portions of TPD-AC-002 through TPD-AC-004
  pass.

## [PLAN] STAGE 3 — Complete-Window Generation

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 3.1 — Sample and publish bounded event windows

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 3.1.1 — Integrate both stop modes

- **Objective:** Complete TPD-AC-002 and TPD-AC-003.
- **Implementation:** Add seeded count/reverse loop, complete-window
  validation/emission, history, stop and packet/byte/proposal limit behavior,
  and generation application adapter.
- **Affected files:** generation adapter and integration tests.
- **Dependencies:** Stage 2 and traffic generation.
- **Outputs:** Deterministic events/PCAPNG with full lineage.
- **Tests:** Golden sampled windows, empty windows, support rejection, stops,
  zero-IAT proposal exhaustion, and failures.
- **Validation:** Repeat fixed run and compare counts/events/artifact hashes.
- **Completion criteria:** All TPD requirements and TPD-AC-001 through
  TPD-AC-004 pass.
