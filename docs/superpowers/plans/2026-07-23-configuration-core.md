# Configuration Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the Configuration library's deterministic resolution and
startup-record boundary required by configuration roadmap STEP 1.1.

**Architecture:** Keep selector parsing, schema validation, precedence, and
canonical record rendering in a deterministic functional core. Put TOML reads,
attempt-directory validation/creation, and atomic launch-record publication in
an imperative shell. The public result returns immutable effective values and
the immutable launch identity that Artifact I/O consumes.

**Tech Stack:** Python 3.12 standard-library `tomllib`, `pathlib`, `os`,
`secrets`, `pytest`, Pyright, Ruff.

---

### Task 1: Define immutable configuration contracts

**Files:**
- Create: `src/trafficlab/libs/configuration/errors.py`
- Create: `src/trafficlab/libs/configuration/values.py`
- Create: `src/trafficlab/libs/configuration/__init__.py`
- Test: `tests/libs/configuration/test_values.py`

- [ ] **Step 1: Write failing value-contract tests**

  Cover immutable schema, selector, source, resolved configuration, and launch
  value construction; reject mutable/noncanonical names, duplicate or unknown
  keys, unsafe record fields, and secret-marked settings. Include exact public
  error hierarchy assertions.

- [ ] **Step 2: Run focused values tests and verify RED**

  Run: `uv run --locked pytest tests/libs/configuration/test_values.py -q`

  Expected: collection failure because `trafficlab.libs.configuration` does not
  yet exist.

- [ ] **Step 3: Implement immutable contracts and typed failures**

  Implement frozen, slotted values for application identity, selectors,
  application-owned schema hooks, immutable effective values, launch records,
  and safe error types. Restrict recordable values to finite TOML-compatible
  scalars, arrays, and mappings; reject marked secret fields until an owner
  supplies a safe recording design.

- [ ] **Step 4: Run focused values tests and verify GREEN**

  Run: `uv run --locked pytest tests/libs/configuration/test_values.py -q`

  Expected: every value-contract test passes.

### Task 2: Resolve selected TOML deterministically

**Files:**
- Create: `src/trafficlab/libs/configuration/resolution.py`
- Test: `tests/libs/configuration/test_resolution.py`

- [ ] **Step 1: Write failing precedence and rejection tests**

  Cover defaults-only, exact file selection, directory-derived selection,
  mutual exclusion, selected-file absence/type/size/symlink rejection,
  malformed TOML, non-table content, unknown keys, invalid TOML types,
  explicit CLI replacement, and application validator rejection. Assert that
  no default, file, or CLI source mutates after resolution.

- [ ] **Step 2: Run focused resolution tests and verify RED**

  Run: `uv run --locked pytest tests/libs/configuration/test_resolution.py -q`

  Expected: import failure for the missing resolver.

- [ ] **Step 3: Implement pure precedence plus bounded TOML shell input**

  Use `tomllib` only after a bounded, regular, no-follow selected-file read.
  Resolve exactly `defaults < selected TOML < explicit CLI`; require the schema
  validator to return a complete valid configuration; preserve a canonical
  selected-source identity; and surface safe typed failures without echoing
  values.

- [ ] **Step 4: Run focused resolution tests and verify GREEN**

  Run: `uv run --locked pytest tests/libs/configuration/test_resolution.py -q`

  Expected: precedence matrix and every rejection path pass.

### Task 3: Secure attempt boundary and canonical launch records

**Files:**
- Create: `src/trafficlab/libs/configuration/attempts.py`
- Create: `src/trafficlab/libs/configuration/launch.py`
- Test: `tests/libs/configuration/test_attempts.py`
- Test: `tests/libs/configuration/test_launch.py`

- [ ] **Step 1: Write failing attempt and launch tests**

  Cover managed absolute/private/empty/owned/no-symlink containment checks;
  direct collision retries and private creation under `run/`; canonical UTC
  path shape; resolved and failure records; deterministic TOML bytes; atomic
  no-overwrite publication; `0600` launch mode; and retained failure records.

- [ ] **Step 2: Run focused attempt and launch tests and verify RED**

  Run: `uv run --locked pytest tests/libs/configuration/test_attempts.py tests/libs/configuration/test_launch.py -q`

  Expected: import failure for missing attempt and launch modules.

- [ ] **Step 3: Implement secure filesystem shell and record renderer**

  Validate managed paths before any record write. Create direct directories
  using atomic private `mkdir` retries and injected clock/suffix sources.
  Render canonical UTF-8 TOML with sorted setting keys and fixed metadata
  order, then atomically publish one mode-`0600` `launch.toml` without
  overwrite. Return a Lineage external file identity for successful records.

- [ ] **Step 4: Run focused attempt and launch tests and verify GREEN**

  Run: `uv run --locked pytest tests/libs/configuration/test_attempts.py tests/libs/configuration/test_launch.py -q`

  Expected: every path, collision, rendering, and failure-record assertion
  passes.

### Task 4: Compose the application startup shell

**Files:**
- Create: `src/trafficlab/libs/configuration/service.py`
- Modify: `src/trafficlab/libs/configuration/__init__.py`
- Test: `tests/libs/configuration/test_service.py`

- [ ] **Step 1: Write failing end-to-end startup tests**

  Exercise managed and direct invocation through one public call. Assert it
  validates/creates the attempt before resolution, records successful effective
  values or a typed resolution failure, never starts application work on
  failure, and returns the immutable launch identity needed by Artifact I/O.

- [ ] **Step 2: Run focused service tests and verify RED**

  Run: `uv run --locked pytest tests/libs/configuration/test_service.py -q`

  Expected: import failure for the missing startup service.

- [ ] **Step 3: Implement minimal orchestration**

  Compose the attempt shell, resolver, canonical renderer, and atomic record
  writer. Catch only configuration-domain failures so unexpected filesystem
  faults retain their typed context; write a valid failure record whenever a
  valid attempt exists.

- [ ] **Step 4: Run all Configuration tests and verify GREEN**

  Run: `uv run --locked pytest tests/libs/configuration -q --cov=trafficlab --cov-fail-under=100`

  Expected: all Configuration tests pass at 100% total coverage.

### Task 5: Record roadmap evidence and verify the completed STEP

**Files:**
- Modify: `architecture/libs/configuration/ROADMAP.md`

- [ ] **Step 1: Update only Configuration STEP 1.1 hierarchy markers**

  Mark Stage 1, Step 1.1, and Substep 1.1.1 `[DONE]` with concise evidence
  naming CFG acceptance coverage, focused tests, full aggregate gate, and
  deterministic launch-record inspection.

- [ ] **Step 2: Run complete repository verification**

  Run: `PYTHONPATH=. UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv run --locked python tools/quality.py all`

  Expected: format, lint, Pyright, tests, 100% coverage, whitespace,
  architecture validation, and wheel build pass.

- [ ] **Step 3: Review and commit one roadmap STEP**

  Run: `git diff --check && git diff --cached --check`

  Stage only Configuration source, tests, plan, and configuration roadmap.
  Commit: `feature(configuration): resolve startup configuration`
