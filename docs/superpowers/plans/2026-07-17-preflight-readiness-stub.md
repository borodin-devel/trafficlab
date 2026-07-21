# Preflight Readiness Stub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only preflight architecture stub that gates traffic capture on the current system's readiness.

**Architecture:** Create one owner document at `apps/00_preflight/`, place it before capture in the root reading path, and make capture require its successful readiness decision. Keep all specific platform, tool, report, and remediation choices deferred; no file contract exists yet.

**Tech Stack:** Markdown, POSIX shell, Git

## Global Constraints

- Preflight assesses system requirements, installed required software, permissions, and relevant existing configuration.
- Preflight is read-only: it does not install software, alter configuration, create capture resources, or elevate privileges.
- Preflight owns readiness assessment; capture owns traffic capture.
- The exact checks, platforms, tools, report fields, format, and remediation mechanisms remain unspecified.
- All Markdown links are relative and locally resolvable.
- Do not stage the user's untracked `.gitignore`.

---

## File Structure

- Modify: `architecture/README.md` — insert the preflight app into the ordered reading path.
- Create: `architecture/apps/00_preflight/README.md` — own the read-only readiness boundary.
- Modify: `architecture/apps/10_capture/README.md` — require a successful preflight decision without duplicating preflight requirements.

### Task 1: Add the Preflight Readiness Boundary

**Files:**

- Modify: `architecture/README.md`
- Create: `architecture/apps/00_preflight/README.md`
- Modify: `architecture/apps/10_capture/README.md`

**Interfaces:**

- Consumes: the approved design at `docs/superpowers/specs/2026-07-17-preflight-readiness-stub-design.md`.
- Produces: a documented readiness decision required by the capture application.

- [ ] **Step 1: Verify the current owners and starting state**

Run:

```bash
git status --short
sed -n '1,240p' architecture/README.md
sed -n '1,260p' architecture/apps/10_capture/README.md
sed -n '1,260p' docs/superpowers/specs/2026-07-17-preflight-readiness-stub-design.md
```

Expected: only the unrelated `.gitignore` is untracked, and `00_preflight` does not yet exist.

- [ ] **Step 2: Update the ordered reading path**

In `architecture/README.md`, make the current reading order:

```markdown
1. this README;
2. the [00 preflight application](apps/00_preflight/README.md);
3. the [10 capture application](apps/10_capture/README.md); and
4. the relevant contract and model documents, once they exist.
```

- [ ] **Step 3: Create the preflight owner document**

Create `architecture/apps/00_preflight/README.md` with this content:

```markdown
# 00 Preflight Application

## Role

This application assesses whether the current system is ready to prepare for
and perform traffic capture. It publishes a readiness decision for the capture
application.

## Boundary

### Input

- A capture-preparation request.

### Output

- A readiness decision with findings and blockers for capture.

### Assessment Scope

- System requirements.
- Installed required software.
- Required permissions.
- Relevant existing configuration.

### Invariants

- The assessment is read-only.
- The readiness decision distinguishes blockers from successful readiness.
- It does not install software, change configuration, create capture resources,
  or elevate privileges.

## Deferred Decisions

This document does not select platforms, required programs, permissions,
configuration values, report fields or format, or remediation mechanisms.

## Reading

Follow the [architecture governance](../../README.md) before changing this
document.
```

- [ ] **Step 4: Require readiness before capture**

Add this item as the first entry under the capture document's `### Inputs`:

```markdown
- A successful [preflight readiness decision](../00_preflight/README.md).
```

Leave its remaining boundary and deferred decisions unchanged.

- [ ] **Step 5: Run structural, link, scope, and whitespace checks**

Run:

```bash
test -f architecture/apps/00_preflight/README.md
rg -q '\[00 preflight application\]\(apps/00_preflight/README.md\)' architecture/README.md
rg -q '\[10 capture application\]\(apps/10_capture/README.md\)' architecture/README.md
rg -q '\[architecture governance\]\(../../README.md\)' architecture/apps/00_preflight/README.md
rg -q '\[preflight readiness decision\]\(../00_preflight/README.md\)' architecture/apps/10_capture/README.md
rg -q '^\- The assessment is read-only\.$' architecture/apps/00_preflight/README.md
! rg -ni 'draft|version_1|netns|dumpcap|wsl|pcapng|cgroup' architecture
! rg -n -e '[[:blank:]]+$' architecture
git diff --check
```

Expected: every command exits with status 0 and emits no output.

- [ ] **Step 6: Review and commit only the stub**

Run:

```bash
git diff -- architecture/README.md architecture/apps/00_preflight/README.md architecture/apps/10_capture/README.md
git status --short
git add architecture/README.md architecture/apps/00_preflight/README.md architecture/apps/10_capture/README.md
git commit -m "docs(architecture): add preflight readiness stub"
```

Expected: the commit contains only the three architecture files; `.gitignore` remains untracked.

