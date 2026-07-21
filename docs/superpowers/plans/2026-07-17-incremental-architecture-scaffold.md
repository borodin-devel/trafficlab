# Incremental Architecture Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the minimal, English-language architecture scaffold for documenting Trafficlab one application at a time.

**Architecture:** Retain the root governance charter as the corpus entry point, extending it only with rules for the agreed layout and current reading path. Create one owner document for `10_capture`; represent the three shared extension roots with empty Git placeholders. Do not define future applications, inter-app contracts, models, capture mechanisms, or file formats.

**Tech Stack:** Markdown, POSIX shell, Git

## Global Constraints

- Active architecture documents are new and self-contained; they do not cite or link to `.drafts/version_1/`.
- `architecture/README.md` documents architecture governance and navigation, not the Trafficlab product.
- Applications use `apps/<number>_<name>/`; an application's `README.md` is its initial owner document.
- Contracts use `contracts/<producer>_<consumer>_<name>/` and are created only after both applications are designed.
- Traffic and genetic models each receive a self-contained directory under their respective roots when a concrete model is designed.
- `10_capture` defines only its stable boundary: purpose, inputs, output, invariants, failures, and security boundaries.
- Every Markdown link must be relative and resolve locally.
- Do not stage the user's untracked `.gitignore`.

---

## File Structure

- Modify: `architecture/README.md` — corpus rules, agreed layout conventions, and current ordered reading path.
- Create: `architecture/apps/10_capture/README.md` — owner document for the capture application's stable boundary.
- Create: `architecture/contracts/.gitkeep` — Git placeholder for future inter-app contracts.
- Create: `architecture/traffic_models/.gitkeep` — Git placeholder for future traffic models.
- Create: `architecture/genetic_models/.gitkeep` — Git placeholder for future genetic models.

### Task 1: Establish the Architecture Layout and Capture Boundary

**Files:**

- Modify: `architecture/README.md`
- Create: `architecture/apps/10_capture/README.md`
- Create: `architecture/contracts/.gitkeep`
- Create: `architecture/traffic_models/.gitkeep`
- Create: `architecture/genetic_models/.gitkeep`

**Interfaces:**

- Consumes: the approved design at `docs/superpowers/specs/2026-07-17-incremental-architecture-scaffold-design.md` and the existing root governance charter.
- Produces: the navigable architecture scaffold that later applications, contracts, and models extend.

- [ ] **Step 1: Confirm the starting state and read the owner documents**

Run:

```bash
git status --short
sed -n '1,240p' architecture/README.md
sed -n '1,260p' docs/superpowers/specs/2026-07-17-incremental-architecture-scaffold-design.md
```

Expected: `architecture/README.md` is the sole current architecture document; `.gitignore` may appear as an unrelated untracked file and must remain untouched.

- [ ] **Step 2: Update the root README with the agreed layout and reading rules**

Keep the six existing governance sections. Add a `## Layout` section that defines these paths and their ownership rules:

```text
apps/<number>_<name>/
contracts/<producer>_<consumer>_<name>/
traffic_models/<model_name>/
genetic_models/<model_name>/
```

State that contracts require a designed producer and consumer; model directories own their equations, assumptions, parameters, and validation criteria; and an application references rather than redefines a model. Replace the generic reading instruction with the current ordered path: root README, then the [10 capture application](apps/10_capture/README.md), then relevant contracts and model documents when they exist. Do not add product behavior or references to draft material.

- [ ] **Step 3: Create the capture owner document**

Create `architecture/apps/10_capture/README.md` with this structure and content:

```markdown
# 10 Capture Application

## Role

This application captures traffic produced by a requested application run. It
publishes a verified capture artifact for a later consumer and records the
execution metadata needed to establish its lineage.

## Boundary

### Inputs

- A validated application-execution request.
- Capture settings.

### Output

- A verified capture artifact and its execution metadata.

### Invariants

- Incomplete or unverified output is never published as a successful artifact.
- The application result and the capture-application result are recorded as
  separate outcomes.

### Security Boundaries

- Application commands are argument vectors; the application does not receive
  shell interpretation.
- Capture privileges are explicit, minimal, and limited to the required
  operation.
- Cleanup of resources created for capture is required on every outcome.

## Deferred Decisions

This document does not select a capture backend, operating-system mechanism,
privilege-helper design, file format, or artifact fields. The contract for a
later consumer is created only when that consumer application is designed.

## Reading

Follow the [architecture governance](../../README.md) before changing this
document.
```

- [ ] **Step 4: Create the three empty extension roots**

Create `architecture/contracts/.gitkeep`,
`architecture/traffic_models/.gitkeep`, and
`architecture/genetic_models/.gitkeep` as empty files. Do not create READMEs,
example models, contracts, or application directories beyond `10_capture`.

- [ ] **Step 5: Run structural, wording, and link checks**

Run:

```bash
test -f architecture/README.md
test -f architecture/apps/10_capture/README.md
test -f architecture/contracts/.gitkeep
test -f architecture/traffic_models/.gitkeep
test -f architecture/genetic_models/.gitkeep
rg -q '\[10 capture application\]\(apps/10_capture/README.md\)' architecture/README.md
rg -q '\[architecture governance\]\(../../README.md\)' architecture/apps/10_capture/README.md
! rg -ni 'draft|version_1|netns|dumpcap|wsl|pcapng|cgroup' architecture
! rg -n -e '[[:blank:]]+$' architecture
git diff --check
```

Expected: every command exits with status 0 and emits no output. The wording check confirms that the new active architecture neither refers to the draft nor selects a capture implementation or artifact format.

- [ ] **Step 6: Review and commit only the scaffold**

Run:

```bash
git diff -- architecture/README.md architecture/apps/10_capture/README.md architecture/contracts/.gitkeep architecture/traffic_models/.gitkeep architecture/genetic_models/.gitkeep
git status --short
git add architecture/README.md architecture/apps/10_capture/README.md architecture/contracts/.gitkeep architecture/traffic_models/.gitkeep architecture/genetic_models/.gitkeep
git commit -m "docs(architecture): add incremental scaffold"
```

Expected: the diff contains only the five scaffold files, `.gitignore` remains untracked, and the commit records only the scaffold.
