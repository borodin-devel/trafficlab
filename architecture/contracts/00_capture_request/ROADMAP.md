# Capture Request Contract Roadmap

Part of the [central Trafficlab roadmap](../../project/ROADMAP.md).

## [PLAN] STAGE 1 — Shared Schema and Validator

- **Task:** Implement the version 1 capture-request envelope, typed schema, exact-byte identity, and fixtures.
- **Deliverable:** One reusable producer/consumer contract library with canonical serializer and validator.
- **Applicable test types:** Unit, property, golden-file, mutation, malformed-file, path, permission, and security tests.
- **Completion criteria:** Every Stage 1 step and substep passes CRQ-AC-001 and the schema portions of CRQ-AC-003 and CRQ-AC-004.

### [PLAN] STEP 1.1 — Parse, validate, serialize, and hash requests

- **Task:** Build the deterministic functional core and bounded file-boundary shell.
- **Deliverable:** Typed request values, normalized validation result, canonical bytes, and exact-byte SHA-256 identity.
- **Applicable test types:** Unit, property, golden-file, boundary-value, malformed-TOML, and determinism tests.
- **Completion criteria:** Every Step 1.1 substep passes the shared golden and invalid-schema fixture matrix.

#### [PLAN] SUBSTEP 1.1.1 — Implement the closed version 1 schema

- **Objective:** Accept exactly the field grammar and cross-field rules in `CONFIGS.md`.
- **Task:** Implement typed parsing, bounds, closed-key validation, normalized paths, and cross-field checks as side-effect-free functions.
- **Deliverable:** Versioned request types and pure validator with stable reason identifiers.
- **Applicable test types:** Unit, table-driven boundary, property, unknown-key, type, and cross-field tests.
- **Implementation:** Add immutable request value types and pure parsing,
  normalization, field-bound, closed-key, and cross-field validators.
- **Affected files:** `src/trafficlab/contracts/capture_request/` and
  `tests/contracts/capture_request/`.
- **Dependencies:** Python 3.12 TOML parsing and the version 1 field reference.
- **Outputs:** Typed request result or a deterministic ordered list of bounded
  validation reasons.
- **Validation:** Run the shared schema fixture matrix twice and compare exact
  normalized results and canonical serialization.
- **Completion criteria:** Valid values at every boundary pass; each invalid kind and combination fails without coercion or repair.

#### [PLAN] SUBSTEP 1.1.2 — Implement the secure file envelope and identity

- **Objective:** Bind validation and hashing to one immutable bounded byte snapshot.
- **Task:** Add no-follow open, owner/mode/link/type checks, bounded read, before/after identity comparison, canonical writer, atomic publication, and SHA-256.
- **Deliverable:** File adapter plus canonical and mutation fixtures shared by every consumer.
- **Applicable test types:** Integration, symlink, hard-link, replacement-race, permission, size, atomicity, mutation, and hash tests.
- **Implementation:** Add injected filesystem operations for safe open/stat,
  bounded snapshot reads, metadata comparison, canonical writes, atomic rename,
  and streaming digest calculation.
- **Affected files:** `src/trafficlab/contracts/capture_request/`,
  `src/trafficlab/libs/artifact_io/`, and `tests/contracts/capture_request/`.
- **Dependencies:** Substep 1.1.1 and the artifact-I/O and lineage libraries.
- **Outputs:** Validated request snapshot with exact-byte digest and atomic
  producer adapter.
- **Validation:** Inject replacement at every open/read/stat/rename boundary and
  verify that no substituted or partial request is accepted.
- **Completion criteria:** CRQ-AC-001 and filesystem portions of CRQ-AC-003 pass with no privileged test dependency.

## [PLAN] STAGE 2 — Preflight and Capture Integration

- **Task:** Bind readiness production and every capture launch to one validated request.
- **Deliverable:** Explicit preflight/capture/orchestrator adapters and end-to-end request/readiness enforcement.
- **Applicable test types:** Contract, fake-clock, fake-workspace, fake-invoker, integration, failure-injection, and security tests.
- **Completion criteria:** Every Stage 2 step and substep passes CRQ-AC-002 through CRQ-AC-004 and affected application acceptance criteria.

### [PLAN] STEP 2.1 — Produce a request-bound readiness decision

- **Task:** Make preflight consume the shared request snapshot and publish its digest and workspace evidence.
- **Deliverable:** Preflight adapter and request-bound readiness fixtures.
- **Applicable test types:** Contract, golden, mutation, blocker, fake-probe, lineage, and atomic-publication tests.
- **Completion criteria:** Every Step 2.1 substep proves that readiness refers to the exact validated request and manifest.

#### [PLAN] SUBSTEP 2.1.1 — Connect request validation to preflight

- **Objective:** Prevent assessment of inferred or partially parsed capture settings.
- **Task:** Validate one request, perform fakeable workspace/capability observations, and publish matching digest and bounded diagnostics.
- **Deliverable:** Preflight request adapter and producer-consumer fixture set.
- **Applicable test types:** Fake-observation, request mutation, workspace mismatch, secret-sentinel, and contract-compatibility tests.
- **Implementation:** Connect the shared snapshot validator to preflight's pure
  assessment input and readiness lineage serializer without duplicating fields.
- **Affected files:** `src/trafficlab/apps/preflight/`, the readiness contract
  implementation, and `tests/apps/preflight/`.
- **Dependencies:** Stage 1, preflight probes, readiness schema, artifact I/O,
  and lineage libraries.
- **Outputs:** Atomically published request-bound readiness decision and shared
  golden/mutation fixtures.
- **Validation:** Feed producer bytes through preflight and both contract
  validators; compare request/workspace digests and scan diagnostics for every
  secret sentinel.
- **Completion criteria:** PRE-AC-001 through PRE-AC-003, CRQ-AC-001, and
  CRQ-AC-004 pass for request-bound preflight.

### [PLAN] STEP 2.2 — Enforce the pair at capture launch

- **Task:** Require explicit request/readiness inputs in direct and orchestrated capture and recheck workspace state after reservation.
- **Deliverable:** Capture and `trafficlab experiment capture` adapters with fail-closed lifecycle behavior.
- **Applicable test types:** Fake-clock, freshness, mutation, reservation race, wrong-workspace, argument-vector, terminal, and integration tests.
- **Completion criteria:** Every Step 2.2 substep passes CRQ-AC-002,
  CRQ-AC-003, CAP-AC-001, CAP-AC-003, and CAP-AC-004.

#### [PLAN] SUBSTEP 2.2.1 — Validate, reserve, recheck, and launch

- **Objective:** Ensure no target starts from missing, stale, changed, or mismatched evidence.
- **Task:** Add explicit CLI paths, shared validation, freshness checks, exclusive reservation, post-reservation workspace verification, and argument-vector handoff.
- **Deliverable:** Deterministic launch decision and retained bounded failure diagnostics.
- **Applicable test types:** End-to-end fake-invoker, TOCTOU, stale/future clock, mode/owner, mismatch, no-shell, and interruption tests.
- **Implementation:** Add capture and orchestrator argument adapters, validate
  the pair, reserve the slot, repeat workspace checks, and pass the unchanged
  vector to an injected invoker only after success.
- **Affected files:** `src/trafficlab/apps/capture/`,
  `src/trafficlab/apps/trafficlab/`, and their integration tests.
- **Dependencies:** Step 2.1, capture workspace state, fakeable clocks, and the
  local invoker boundary.
- **Outputs:** One request-bound capture launch decision plus terminal status
  and bounded diagnostics for every rejected path.
- **Validation:** Assert the fake invoker's exact call count/vector while
  mutating request, readiness, clock, workspace state, and reservation order.
- **Completion criteria:** All invalid pair/race fixtures observe zero target launches; the valid fixture launches the exact argument vector once.
