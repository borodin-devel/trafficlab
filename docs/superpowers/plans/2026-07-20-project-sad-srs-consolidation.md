# Project SAD/SRS Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish project SAD/SRS ownership and ML/LLM inspection-export architecture.

**Architecture:** Retain existing detailed owners. Add project-level cross-cutting owners and an independent inspection-export application owner.

**Tech Stack:** Markdown; Python 3.12 target; PCAPNG; Apache Arrow; Apache Parquet; JSON Lines.

## Global Constraints

- Linux and Windows Subsystem for Linux 2 only; native Windows unsupported.
- Applications exchange explicit validated, hashed, atomically published files.
- Production code uses functional cores behind imperative shells.

---

### Task 1: Add Project SAD/SRS Owners

**Files:**

- Create: `architecture/project/README.md`
- Create: `architecture/project/requirements.md`
- Create: `architecture/project/WORKFLOWS.md`
- Create: `architecture/project/IMPLEMENTATION_STRUCTURE.md`
- Create: `architecture/project/RESOURCE_MANAGEMENT.md`
- Create: `architecture/project/LOGGING.md`

**Interfaces:**

- Consumes: existing architecture owners through relative links.
- Produces: cross-cutting requirement, workflow, boundary, resource, and logging owners.

- [ ] **Step 1: Write failing assertion**

```text
test -f architecture/project/requirements.md
```

- [ ] **Step 2: Verify failure**

Run: `test -f architecture/project/requirements.md`

Expected: nonzero exit status before file creation.

- [ ] **Step 3: Write documents**

Add FR-01 through FR-09 and QR-01 through QR-07, explicit-path workflows,
source/test hierarchy, positive resource admission, and bounded logging rules.

- [ ] **Step 4: Verify documents**

Run: `git diff --check && rg -n 'TODO|TBD|implement later' architecture/project`

Expected: zero exit status and no matches.

- [ ] **Step 5: Commit**

```bash
git add architecture/project
git commit -m "docs: add project SAD and SRS owners"
```

### Task 2: Define Inspection Export Application

**Files:**

- Create: `architecture/apps/25_inspection_export/README.md`

**Interfaces:**

- Consumes: one closed validated PCAPNG and source lineage.
- Produces: `manifest.json`, `packets.parquet`, `inspection.jsonl`, `schema.json`, and `launch.toml`.

- [ ] **Step 1: Write failing assertion**

```text
test -f architecture/apps/25_inspection_export/README.md
```

- [ ] **Step 2: Verify failure**

Run: `test -f architecture/apps/25_inspection_export/README.md`

Expected: nonzero exit status before file creation.

- [ ] **Step 3: Write application owner**

Define CLI, source lineage, Parquet and JSONL schema, atomic publication,
scope boundary, and unprivileged test requirements.

- [ ] **Step 4: Verify document**

Run: `git diff --check && rg -n 'TODO|TBD|implement later' architecture/apps/25_inspection_export`

Expected: zero exit status and no matches.

- [ ] **Step 5: Commit**

```bash
git add architecture/apps/25_inspection_export
git commit -m "docs: define inspection export application"
```

### Task 3: Synchronize Delivery Roadmaps

**Files:**

- Create: `architecture/project/ROADMAP.md`
- Create: `ROADMAP.md`

**Interfaces:**

- Consumes: project requirements and implementation owners.
- Produces: delivery status, completion criteria, and test types for every stage.

- [ ] **Step 1: Write failing assertion**

```text
test -f architecture/project/ROADMAP.md
```

- [ ] **Step 2: Verify failure**

Run: `test -f architecture/project/ROADMAP.md`

Expected: nonzero exit status before file creation.

- [ ] **Step 3: Write roadmaps**

Mark all unimplemented stages `planned`; include foundation, capture,
conversion, inspection export, models/generation, similarity, GA training,
and orchestration.

- [ ] **Step 4: Verify roadmap**

Run: `git diff --check && rg -n 'TODO|TBD|implement later' ROADMAP.md architecture/project/ROADMAP.md`

Expected: zero exit status and no matches.

- [ ] **Step 5: Commit**

```bash
git add ROADMAP.md architecture/project/ROADMAP.md
git commit -m "docs: add synchronized delivery roadmap"
```

## Self-Review

- Spec coverage: Tasks 1–3 cover every identified project-level gap.
- Placeholder scan: no placeholders remain.
- Interface consistency: package filenames match Task 2 throughout.
