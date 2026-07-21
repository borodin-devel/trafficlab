# Neural Model Contract Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make neural traffic models executable through genetic training and mathematically self-describing.

**Architecture:** Genetic training owns neural directory orchestration; the two model owners gain complete configuration and mathematics.

**Tech Stack:** Markdown and Git.

## Global Constraints

- Architecture documents only; no implementation, templates, datasets, or fixtures.
- Existing non-neural models retain their one-capture workflow.
- Neural directory captures are separate targets with equal-weight validation mean.
- Every fitting and generation parameter is explicit.

---

### Task 1: Extend genetic training for neural directories

**Files:**

- Modify: `architecture/apps/30_genetic_training/README.md`
- Modify: `docs/configs/30_genetic_training.md`

- [ ] Document neural-only file/directory source modes, deterministic splits, candidate-local fitting, per-validation-capture generation/evaluation, equal-weight aggregation, failure behavior, and aggregate lineage.
- [ ] Document mutually exclusive neural source selectors and split controls; capture weights remain unsupported.
- [ ] Verify with `git diff --check` and required-term search; commit `docs(architecture): support neural capture directories`.

### Task 2: Complete Neural Hawkes builder schema

**Files:**

- Modify: `architecture/traffic_models/neural_hawkes/README.md`

- [ ] Add explicit defaults and validation for architecture, attention compatibility, optimizer/fitting/validation/split controls, zero-IAT policy, IAT/mark laws, seed/runtime determinism, and packet-count versus duration stopping.
- [ ] State trained files add weights and lineage only after builder validation.
- [ ] Verify with `git diff --check`; commit `docs(architecture): complete Neural Hawkes schema`.

### Task 3: Complete diffusion contract

**Files:**

- Modify: `architecture/traffic_models/marked_point_process_diffusion/README.md`

- [ ] Keep normalized `(log1p(IAT), frame length)` targets; first IAT is from window start and cumulative IAT must stay strictly within duration.
- [ ] Define beta schedule, forward noising, epsilon loss, deterministic `x0_hat`, reverse update, and separate categorical count head trained by cross-entropy and sampled before value sampling.
- [ ] Define complete-sample failure without repair for invalid support.
- [ ] Verify with `git diff --check`; commit `docs(architecture): complete diffusion model contract`.

### Task 4: Final audit

- [ ] Run `git diff --check`, relative-link validation, and `git status --short`.
