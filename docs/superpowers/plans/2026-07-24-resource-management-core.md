# Resource Management Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Resource Management STEP 1.1 with deterministic four-dimension admission, release, decision records, and Linux observations.

**Architecture:** Immutable values and ledger transitions stay pure. A read-only injected Linux shell observes CPU, memory, and explicit-path storage. Storage-byte budgets/reservations follow Amendment 0002; no application scheduling or process lifecycle enters the library.

**Tech Stack:** Python 3.12 standard library (`os`, `pathlib`); pytest, Pyright, Ruff.

---

### Task 1: Define values and typed failures

**Files:**
- Create: `src/trafficlab/libs/resource_management/errors.py`
- Create: `src/trafficlab/libs/resource_management/values.py`
- Create: `src/trafficlab/libs/resource_management/__init__.py`
- Test: `tests/libs/resource_management/test_values.py`

- [x] **Step 1: Write RED value tests**

  Test frozen budgets, observations, probe failures, reservations, decisions,
  positive 64-bit quantities, canonical job ordering, and invalid identity/
  path/boolean/overflow rejection.

- [x] **Step 2: Run RED tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/resource_management/test_values.py -q`

  Expected: missing package import.

- [x] **Step 3: Implement values/errors**

  Define `ResourceBudget(cpu_units, memory_bytes, storage_bytes, worker_slots)`,
  `ResourceCapacity`, `ResourceObservation`, `ProbeFailure`, `JobReservation`, and
  `AdmissionDecision` as frozen/slotted values. Reject nonpositive or overflow
  quantities and unsafe identifiers at constructors.

- [x] **Step 4: Run GREEN tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/resource_management/test_values.py -q`

### Task 2: Implement pure deterministic ledger transitions

**Files:**
- Create: `src/trafficlab/libs/resource_management/ledger.py`
- Modify: `src/trafficlab/libs/resource_management/__init__.py`
- Test: `tests/libs/resource_management/test_ledger.py`

- [x] **Step 1: Write RED ledger tests**

  Test atomic admit/reject across CPU, memory, storage, workers; duplicate job;
  failed/insufficient observation; exact release; unknown release; deterministic
  ordering; and fixed-seed mixed reservation/release traces preserving bounds.

- [x] **Step 2: Run RED tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/resource_management/test_ledger.py -q`

- [x] **Step 3: Implement transitions**

  `remaining` returns nonnegative `ResourceCapacity` after summing canonical
  active reservations and rejects inconsistent state.
  `admit` returns unchanged state on every rejection and adds all four fields
  only after all fit. `release` removes exactly one matching reservation.

- [x] **Step 4: Run GREEN tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/resource_management/test_ledger.py -q`

### Task 3: Add injected Linux probe and defensive coverage

**Files:**
- Create: `src/trafficlab/libs/resource_management/probe.py`
- Modify: `src/trafficlab/libs/resource_management/__init__.py`
- Test: `tests/libs/resource_management/test_probe.py`
- Test: `tests/libs/resource_management/test_defensive_paths.py`

- [x] **Step 1: Write RED probe tests**

  Test injected CPU/proc/statvfs success, malformed/missing MemAvailable,
  zero/overflow capacity, invalid storage path, and OS probe failures returning
  `ProbeFailure` without raw exception escape.

- [x] **Step 2: Run RED tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/resource_management/test_probe.py tests/libs/resource_management/test_defensive_paths.py -q`

- [x] **Step 3: Implement probe shell**

  Require absolute normalized storage path, parse one bounded MemAvailable line,
  multiply `f_frsize * f_bavail` with signed-64-bit bound, and convert every
  observation boundary failure to a deterministic `ProbeFailure`.

- [x] **Step 4: Run GREEN tests**

  Run: `PYTHONPATH=. uv run --locked pytest tests/libs/resource_management/test_probe.py tests/libs/resource_management/test_defensive_paths.py -q`

### Task 4: Verify and record evidence

**Files:**
- Modify: `architecture/libs/resource_management/ROADMAP.md`
- Modify: `architecture/project/ROADMAP.md`
- Create: `architecture/amendments/0002_RESOURCE_STORAGE_RESERVATION.md`
- Create: `docs/superpowers/specs/2026-07-24-resource-management-core-design.md`
- Create: `docs/superpowers/plans/2026-07-24-resource-management-core.md`

- [x] **Step 1: Mark Resource Management Stage 1, Step 1.1, and Substep 1.1.1 done**

  Add RES-AC-001/002 evidence. Mark central Stage 1/Step 1.1/Substep 1.1.1
  `[DONE]`: all seven linked foundations complete.

- [x] **Step 2: Run full verification**

  Run: `PYTHONPATH=. UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv run --locked python tools/quality.py all`

  Expected: format, lint, Pyright, tests, 100% coverage, corpus/docs,
  whitespace, and wheel build pass.

- [x] **Step 3: Commit one roadmap STEP**

  Stage only Resource Management source/tests, amendment, approved spec/plan,
  and its two roadmap updates. Commit:

  ```text
  feature(resource-management): add admission ledger
  ```
