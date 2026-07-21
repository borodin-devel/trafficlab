# Traffic Generation Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Event Renderer

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 1.1 — Define and render canonical Ethernet records

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 1.1.1 — Resolve template and PCAPNG metadata

- **Objective:** Convert validated event sequences into reproducible PCAPNG.
- **Implementation:** Specify fixed Ethernet bytes/interface metadata,
  timestamp representation, event validation, and streaming renderer.
- **Affected files:** `src/trafficlab/apps/traffic_generation/`; tests.
- **Dependencies:** PCAPNG and artifact libraries.
- **Outputs:** Renderer protocol and canonical fixture PCAPNG.
- **Tests:** Boundary lengths, zero IAT, output-byte accounting, timestamp
  precision, malformed events, and round trip.
- **Validation:** Decode outputs and compare exact events and bytes.
- **Completion criteria:** Rendering portion of TGN-AC-001 passes.

## [PLAN] STAGE 2 — Model Dispatch and Publication

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Generate from mature models

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Validate, sample, and publish

- **Objective:** Produce one complete deterministic synthetic artifact.
- **Implementation:** Add CLI, registry, model validation, seeded sampling,
  generation-ready enforcement, three resource counters, stop enforcement,
  lineage, close/validate/detached hash, and atomic output.
- **Affected files:** generation shell, model adapters, integration tests.
- **Dependencies:** Stage 1 and mature traffic models.
- **Outputs:** Synthetic PCAPNG and retained startup diagnostics.
- **Tests:** Per-model fixtures, builder rejection, determinism, packet/byte/
  proposal boundaries, infinite zero-IAT proposals, invalid/stub model, orphan
  file, and failure injection.
- **Validation:** Re-read output, compare events, verify detached file/launch
  digests, and repeat content hash.
- **Completion criteria:** TGN-AC-001 through TGN-AC-004 pass.
