# Advanced Neural Traffic Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document two selectable advanced neural Layer 2 traffic models that train from one capture or a capture directory.

**Architecture:** A shared neural marked-point-process owner defines source modes, file boundaries, deterministic window splitting, lineage, and candidate-fitting rules. Two independent owners define causal self-attentive Hawkes generation and whole-window marked point-process diffusion generation. The common traffic-model registry gains both names.

**Tech Stack:** Markdown, Git, Python 3.12 standard library for documentation-link validation.

## Global Constraints

- Create architecture documents only: no implementation, templates, datasets, or fixtures.
- Model only Layer 2 IAT and original Ethernet frame length; no bytes or higher protocols.
- Accept exactly one source mode: one capture file or one capture directory.
- Never derive an IAT, transition, or sequence across files.
- Fitting learns neural weights deterministically; genetic training searches only explicit high-level hyperparameters.
- Record source hashes, assignment, configuration, seed, library versions, and learned-weight hash in lineage.
- Future tests are offline and require no network access, live capture, or elevated privilege.

---

### Task 1: Create shared neural-model rules and register both models

**Files:**

- Create: `architecture/traffic_models/NEURAL_MARKED_POINT_PROCESS.md`
- Modify: `architecture/traffic_models/README.md`

**Consumes:** `architecture/traffic_models/README.md` and the approved design at `docs/superpowers/specs/2026-07-20-advanced-neural-traffic-models-design.md`.

**Produces:** Shared source/window/lineage ownership and two selectable names.

- [ ] **Step 1: Write the shared owner**

Document the Layer 2 event domain; one-file versus one-directory source modes;
deterministic relative-path enumeration; per-file independent validation;
strict file-boundary separation; deterministic non-overlapping windows;
whole-file validation assignment where possible and chronological-window
fallback; single-capture overfitting warning; required diagnostics and lineage;
candidate-local deterministic fitting; and genetic search only over explicit
hyperparameters.

- [ ] **Step 2: Register exact selectable names**

Add these rows to the traffic-model table:

```markdown
| `neural_hawkes` | [Neural Hawkes](neural_hawkes/README.md) | Causal self-attentive marked temporal point process |
| `marked_point_process_diffusion` | [Marked point-process diffusion](marked_point_process_diffusion/README.md) | Conditional whole-window marked point-process diffusion |
```

Link the shared neural owner from the registry Reading section while retaining
the existing Poisson-family ownership statement.

- [ ] **Step 3: Verify and commit**

Run:

```bash
git diff --check
```

Expected: exit 0.

Commit:

```bash
git add architecture/traffic_models/NEURAL_MARKED_POINT_PROCESS.md architecture/traffic_models/README.md
git commit -m "docs(architecture): register advanced neural models"
```

### Task 2: Create the causal Neural Hawkes owner

**Files:**

- Create: `architecture/traffic_models/neural_hawkes/README.md`

**Consumes:** `architecture/traffic_models/NEURAL_MARKED_POINT_PROCESS.md`.

**Produces:** A selectable causal, one-frame-at-a-time neural traffic model.

- [ ] **Step 1: Write model behavior and fitting ownership**

Define a self-attention history encoder over earlier `(IAT, frame length)`
events; a conditional non-negative IAT density or intensity; a continuous
frame-length mark density; deterministic mapping to integer 60–1514-byte
lengths; finite history/context controls; likelihood/loss; fitting and
validation; model-file learned-weight lineage; stopping; failure behavior;
and limits.

- [ ] **Step 2: Document explicit high-level trainables**

List architecture size, attention heads/layers, history length, learning rate,
optimizer controls, epoch/batch controls, window width, and validation policy
as explicit candidate hyperparameters. State learned weights are not genetic
parameters.

- [ ] **Step 3: Verify and commit**

Run:

```bash
rg -n 'self-attention|non-negative|continuous|60 through 1514|directory|file boundary|learned weights|elevated privilege' architecture/traffic_models/neural_hawkes/README.md
git diff --check
```

Expected: required behavior is documented and whitespace check exits 0.

Commit:

```bash
git add architecture/traffic_models/neural_hawkes/README.md
git commit -m "docs(architecture): add Neural Hawkes model"
```

### Task 3: Create the marked point-process diffusion owner and audit

**Files:**

- Create: `architecture/traffic_models/marked_point_process_diffusion/README.md`
- Verify: `architecture/traffic_models/README.md`
- Verify: `architecture/traffic_models/NEURAL_MARKED_POINT_PROCESS.md`
- Verify: both neural model owners

**Consumes:** `architecture/traffic_models/NEURAL_MARKED_POINT_PROCESS.md`.

**Produces:** A selectable conditional whole-window diffusion model and a complete linked architecture set.

- [ ] **Step 1: Write model behavior and fitting ownership**

Define fixed-window generation from noise into ordered normalized
`(log1p(IAT), frame length)` sequences; conditioning only on generated
history and seed; explicit variable-event-count representation; diffusion
schedule and denoising network controls; loss, sampling, output conversion to
non-negative IAT and deterministically rounded 60–1514-byte integer lengths;
model-file learned-weight lineage; stopping; failures; and limits.

- [ ] **Step 2: Document explicit high-level trainables**

List window width, maximum event representation, network size, diffusion-step
and noise-schedule controls, learning rate, optimizer controls, epoch/batch
controls, validation policy, and generation controls. State external labels,
addresses, payloads, and protocols are not conditions.

- [ ] **Step 3: Run final documentation audit**

Run:

```bash
git diff --check
python3 - <<'PY'
from pathlib import Path
import re

for path in Path('architecture/traffic_models').rglob('*.md'):
    for target in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', path.read_text()):
        assert (path.parent / target).resolve().is_file(), (path, target)
print('All traffic-model relative links resolve.')
PY
rg -n 'neural_hawkes|marked_point_process_diffusion' architecture/traffic_models/README.md
git status --short
```

Expected: clean whitespace, resolving links, each name registered once, and
only the planned architecture files modified before commit.

- [ ] **Step 4: Commit and verify clean state**

```bash
git add architecture/traffic_models/marked_point_process_diffusion/README.md
git commit -m "docs(architecture): add marked point-process diffusion"
git status --short
```

Expected: the commit succeeds and final status has no output.
