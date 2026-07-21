# Advanced Layer 2 Similarity Methods Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document five selectable advanced Layer 2 similarity methods and their shared temporal rules.

**Architecture:** `TEMPORAL_L2.md` owns common Ethernet extraction, time-zero alignment, validation, diagnostics, determinism, and offline-test boundaries. Each method owner defines only its mathematics, configuration, score, limits, and method-specific testing. The existing registry gains five selectable rows.

**Tech Stack:** Markdown, Git, Python 3.12 standard-library `tomllib` for validating existing TOML examples where relevant.

## Global Constraints

- Create architecture documents only: no Python implementation, templates, or fixtures.
- Inputs are supported Ethernet pcapng files; use timestamps and original Ethernet frame lengths only.
- Shift each capture's first frame to time zero; accept zero IAT and reject invalid time or length data.
- Each method returns one reproducible primary similarity in `[0, 1]` and raw diagnostics.
- Every parameter is explicit; future tests are offline and require no network access, live capture, or elevated privilege.

---

### Task 1: Add shared temporal rules and register all methods

**Files:**

- Create: `architecture/similarity_methods/TEMPORAL_L2.md`
- Modify: `architecture/similarity_methods/README.md`

**Consumes:** `architecture/similarity_methods/README.md`, the existing KS
family rules in `architecture/similarity_methods/KS.md`, and the approved
design at `docs/superpowers/specs/2026-07-20-advanced-l2-similarity-methods-design.md`.

**Produces:** One shared owner for the new temporal methods and five discoverable selectable names.

- [ ] **Step 1: Create `TEMPORAL_L2.md`**

Document: supported Ethernet pcapng input; timestamps and original Ethernet
frame lengths only; no captured bytes or higher-protocol fields; first-frame
time-zero alignment; finite, nondecreasing timestamp validation; valid zero
IAT; 60–1514-byte length validation; deterministic ordering; raw diagnostics;
explicit configuration; atomic output delegated to the application contract;
and offline, non-privileged future tests.

- [ ] **Step 2: Register the five names**

Add these exact rows to `architecture/similarity_methods/README.md`:

```markdown
| `joint_sinkhorn_wasserstein` | [joint Sinkhorn/Wasserstein](joint_sinkhorn_wasserstein/README.md) |
| `multiscale_rate` | [multi-scale rate](multiscale_rate/README.md) |
| `neighbor_transition` | [neighbour transition](neighbor_transition/README.md) |
| `autocorrelation` | [autocorrelation](autocorrelation/README.md) |
| `sequence_kernel_mmd` | [sequence-kernel MMD](sequence_kernel_mmd/README.md) |
```

Link the new temporal owner from the registry's reading section without
changing the existing KS-family ownership.

- [ ] **Step 3: Verify shared documentation links**

Run:

```bash
git diff --check
```

Expected: exit 0.

- [ ] **Step 4: Commit the shared layer and registry**

```bash
git add architecture/similarity_methods/TEMPORAL_L2.md architecture/similarity_methods/README.md
git commit -m "docs(architecture): register advanced similarity methods"
```

### Task 2: Add joint-distribution and burst-structure owners

**Files:**

- Create: `architecture/similarity_methods/joint_sinkhorn_wasserstein/README.md`
- Create: `architecture/similarity_methods/multiscale_rate/README.md`

**Consumes:** `architecture/similarity_methods/TEMPORAL_L2.md`.

**Produces:** Selectable methods for timing-size association and multi-scale burst structure.

- [ ] **Step 1: Create the joint Sinkhorn/Wasserstein owner**

Define observations as `(log1p(IAT seconds), original frame length)`, explicit
positive feature scales, entropic regularization, empirical weights, Sinkhorn
divergence, solver convergence requirements, raw distance, and a configured
positive monotonic mapping into `[0, 1]`. State that it ignores order and fails
on fewer than two frames or solver non-convergence.

- [ ] **Step 2: Create the multi-scale rate owner**

Define configured positive window widths and a positive horizon; time-zero
bins including zero-count bins; packet-count and original-byte-count vectors;
normalized-L1 component distances; positive feature and scale weights summing
to one; excluded post-horizon frames reported as diagnostics; and the method's
burst-focused limit.

- [ ] **Step 3: Verify each owner delegates shared rules**

Run:

```bash
rg -n 'temporal_l2|Sinkhorn|horizon|\[0, 1\]|elevated privilege' architecture/similarity_methods/joint_sinkhorn_wasserstein/README.md architecture/similarity_methods/multiscale_rate/README.md
```

Expected: each owner links to `TEMPORAL_L2.md`, defines its score and limits,
and preserves offline-test requirements.

- [ ] **Step 4: Commit the two owners**

```bash
git add architecture/similarity_methods/joint_sinkhorn_wasserstein/README.md architecture/similarity_methods/multiscale_rate/README.md
git commit -m "docs(architecture): add distribution and burst similarity"
```

### Task 3: Add local and linear-sequence-dependency owners

**Files:**

- Create: `architecture/similarity_methods/neighbor_transition/README.md`
- Create: `architecture/similarity_methods/autocorrelation/README.md`

**Consumes:** `architecture/similarity_methods/TEMPORAL_L2.md`.

**Produces:** Selectable methods for one-step transitions and configured linear repetition.

- [ ] **Step 1: Create the neighbour-transition owner**

Define explicit nonoverlapping joint categories that cover every valid
`(log1p(IAT), frame length)` observation exactly once; canonical state order;
adjacent-transition count distributions; base-2 Jensen-Shannon distance; and
its direct `[0, 1]` similarity. Specify failures for gaps, overlaps,
uncovered observations, and inadequate input.

- [ ] **Step 2: Create the autocorrelation owner**

Define configured positive integer lags; sample autocorrelation for both
`log1p(IAT)` and frame length; per-lag difference divided by two; configured
feature and lag weights; `1 - distance` primary similarity; and failure for
insufficient observations or a constant series.

- [ ] **Step 3: Verify mathematical-bound documentation**

Run:

```bash
rg -n 'Jensen-Shannon|base-2|\[-1, 1\]|divided by two|constant series|exactly once' architecture/similarity_methods/neighbor_transition/README.md architecture/similarity_methods/autocorrelation/README.md
```

Expected: the owners state the bounded-distance reasoning and input failures.

- [ ] **Step 4: Commit the two owners**

```bash
git add architecture/similarity_methods/neighbor_transition/README.md architecture/similarity_methods/autocorrelation/README.md
git commit -m "docs(architecture): add dependency similarity methods"
```

### Task 4: Add the advanced sequence-aware owner and audit the architecture

**Files:**

- Create: `architecture/similarity_methods/sequence_kernel_mmd/README.md`
- Verify: `architecture/similarity_methods/README.md`
- Verify: `architecture/similarity_methods/TEMPORAL_L2.md`
- Verify: all five new owner documents

**Consumes:** `architecture/similarity_methods/TEMPORAL_L2.md`.

**Produces:** The sequence-aware comparison owner and complete, linked advanced-method documentation.

- [ ] **Step 1: Create the sequence-kernel-MMD owner**

Define fixed-width non-overlapping windows over an explicit positive horizon,
including empty windows; ordered sequences of `(log1p(IAT), frame length)`;
configured signature-kernel parameters and bounded characteristic base kernel;
MMD estimator; raw MMD and window-count diagnostics; a configured positive
mapping scale; numerical validation; and its advanced whole-sequence scope.

- [ ] **Step 2: Run final documentation validation**

Run:

```bash
git diff --check
python3 - <<'PY'
from pathlib import Path
import re

for path in Path('architecture/similarity_methods').rglob('*.md'):
    for target in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', path.read_text()):
        assert (path.parent / target).resolve().is_file(), (path, target)
print('All similarity-method relative links resolve.')
PY
rg -n 'joint_sinkhorn_wasserstein|multiscale_rate|neighbor_transition|autocorrelation|sequence_kernel_mmd' architecture/similarity_methods/README.md
git status --short
```

Expected: clean whitespace, all links resolve, each name is registered once,
and only the planned architecture documents are modified before commit.

- [ ] **Step 3: Commit the sequence owner**

```bash
git add architecture/similarity_methods/sequence_kernel_mmd/README.md
git commit -m "docs(architecture): add sequence kernel MMD"
```

- [ ] **Step 4: Confirm clean final state**

Run:

```bash
git status --short
```

Expected: no output.
