# Architecture Root README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the self-contained governance charter and entry point for the new architecture corpus.

**Architecture:** Add one English Markdown document at `architecture/README.md`. It establishes corpus authority, reading and ownership rules, ordinary change discipline, and the exceptional amendment process without defining any future architecture areas or product decisions.

**Tech Stack:** Markdown, POSIX shell validation, Git

## Global Constraints

- `architecture/` is self-contained and must not discuss material outside the corpus.
- Do not define future architecture areas, product components, pipeline stages, contracts, implementations, or other architectural decisions.
- Do not prescribe any subdirectory except `architecture/amendments/`.
- Every architectural fact has one owner document; other documents use brief context and relative links.
- Add no Markdown link unless its target exists.
- Write concise, normative English without speculative examples.

---

### Task 1: Create the Architecture Governance Charter

**Files:**
- Create: `architecture/README.md`
- Reference: `docs/superpowers/specs/2026-07-16-architecture-root-readme-design.md`

**Interfaces:**
- Consumes: the approved architecture root README design specification.
- Produces: the authoritative entry point and governance rules for all later documents under `architecture/`.

- [ ] **Step 1: Verify the deliverable does not already exist**

Run:

```bash
test ! -e architecture/README.md
```

Expected: exit status 0 and no output.

- [ ] **Step 2: Create the architecture root README**

Create `architecture/README.md` with exactly this content:

```markdown
# Trafficlab Architecture

## Purpose

This directory is the self-contained source of truth for Trafficlab's
architecture. It records the decisions and constraints that govern the project.

## Authority

An architectural statement is authoritative only when it is recorded in this
directory. When architectural documents disagree, do not choose between them
silently; follow the amendment process below.

## Reading

Start with this README. As authoritative documents are added, maintain their
required reading order here. Read a fact's owner document before relying on or
changing that fact.

## Ownership and References

Every architectural fact has one owner document. Other documents may provide
brief context, but they must use relative links to the owner instead of
duplicating its definition, requirements, fields, or algorithm.

## Change Discipline

To change an architectural fact:

1. Read this README and the fact's owner document.
2. Update the owner document.
3. Update every affected reference.
4. Check the architecture corpus for consistency and validate its relative
   links.

Keep each decision with its owner. Do not use an amendment to excuse missing
implementation.

## Amendments

Use `amendments/<number>_<short_name>.md` only when authoritative sources have
an irreconcilable conflict. An amendment must:

- name the conflicting sources;
- record the decision and its rationale;
- define its scope and consequences;
- record the alternatives considered;
- describe compatibility and migration effects; and
- state its status, owner, and date.
```

- [ ] **Step 3: Verify content and prohibited scope**

Run:

```bash
test -f architecture/README.md
test "$(grep -c '^## ' architecture/README.md)" -eq 6
grep -q '^## Purpose$' architecture/README.md
grep -q '^## Authority$' architecture/README.md
grep -q '^## Reading$' architecture/README.md
grep -q '^## Ownership and References$' architecture/README.md
grep -q '^## Change Discipline$' architecture/README.md
grep -q '^## Amendments$' architecture/README.md
! grep -Eiq 'draft|system/|stages/|contracts/|implementations/|common/|project/' architecture/README.md
```

Expected: exit status 0 and no output.

- [ ] **Step 4: Validate Markdown links and whitespace**

Run:

```bash
test -z "$(grep -oE '\[[^]]+\]\([^)]+\)' architecture/README.md)"
! grep -nE '[[:blank:]]+$' architecture/README.md
```

Expected: exit status 0 and no output. The first command confirms that the
initial charter contains no links requiring unresolved targets. The second
checks the untracked file directly for trailing whitespace.

- [ ] **Step 5: Review the final diff**

Run:

```bash
git diff --no-index -- /dev/null architecture/README.md || test $? -eq 1
git status --short
```

Expected: the diff shows `architecture/README.md` as one new 49-line file with
only the approved charter content. Status lists `architecture/README.md` plus
the known implementation plan change.

- [ ] **Step 6: Commit the charter**

```bash
git add architecture/README.md
git commit -m "docs(architecture): add governance charter"
```

Expected: one commit containing only `architecture/README.md`.
