# Markov Renewal Empty Transition Row Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define and test a valid Markov Renewal transition row when an active
state has no observed outgoing transition and additive smoothing is zero.

**Architecture:** Keep exact zero smoothing and the existing additive estimator.
Define the single zero-denominator case by its continuous uniform limit, store it
as an ordinary transition row, and reuse the existing global-IAT timing fallback.

**Tech Stack:** Markdown architecture documents, Markov Renewal transition
counts, additive smoothing, deterministic pytest evidence

## Global Constraints

- Keep `alpha = 0` valid.
- For `N_j = 0`, `alpha = 0`, and `K >= 1`, define `p_jk = 1 / K` for every
  active destination state `k`.
- Retain the existing additive formula whenever `N_j + alpha*K > 0`.
- Store and load the uniform row exactly like every other transition row.
- Reuse the existing global IAT fallback; do not add a timing rule.
- Treat the empty row as valid, but keep `K = 0`, missing global IAT data, and
  malformed stored rows invalid.
- Add no setting, artifact field, model, abstraction, RNG policy, genetic change,
  or similarity change.
- Keep exactly three traffic models, four similarity methods, one genetic
  strategy, and seven roadmap phases.
- Keep edited lines at no more than 120 characters.

---

### Task 1: Define the complete transition estimator

**Files:**
- Modify: `architecture/traffic_models/markov_renewal.md:20-44`
- Modify: `architecture/traffic_models/markov_renewal.md:81-143`

**Interfaces:**
- Consumes: transition counts `N_jk`, outgoing total `N_j`, active-state count
  `K`, smoothing `alpha`, and the existing source/global IAT samples
- Produces: one finite probability row for every active source state, consumed
  by generation, serialization, loading, and model tests

- [ ] **Step 1: Publish the outgoing-count definition**

In `Estimator`, define the row total before the transition formula:

```text
Let N_j = sum_m N_jm be the total number of observed transitions leaving active
state j. K is the positive number of active states.
```

Use the mathematical definition:

```math
N_j=\sum_m N_{jm}.
```

- [ ] **Step 2: Replace the partial estimator with the complete piecewise rule**

Replace the current single fraction with:

```math
p_{jk}=
\begin{cases}
\dfrac{N_{jk}+\alpha}{N_j+\alpha K},
  & N_j+\alpha K>0,\\
\dfrac{1}{K},
  & N_j=0\ \text{and}\ \alpha=0.
\end{cases}
```

Immediately state:

```text
The denominator can be zero only in the second case. When N_j = 0 and
alpha > 0, the first case already equals alpha/(alpha*K) = 1/K, so the second
case is its continuous extension to alpha = 0. Every row is finite,
nonnegative, and sums to one.
```

Label the extension as a Trafficlab boundary choice for an unobserved row, not
evidence that the reference exhibited uniform transitions.

- [ ] **Step 3: Define fit, storage, and failure behavior**

After the estimator, add the exact operational contract:

```text
Fitting applies the uniform rule without an RNG draw and stores it as an ordinary
transition row. Loading performs the same finite, nonnegative, and row-sum
validation as for every other row. K = 0 and malformed stored rows remain model
errors. The defined empty row is not an invalid candidate merely because
alpha = 0.
```

Do not add a serialized discriminator, fallback name, or experiment field.

- [ ] **Step 4: Connect the row to the existing timing fallback**

At the end of `Sparse timing fallback`, add:

```text
An empty transition row has no IATs leaving its source state, so after sampling
its uniform destination the same fallback reaches the global IAT sample. No
separate empty-row timing rule exists.
```

Keep missing, nonfinite, or negative global IAT data invalid and zero IATs valid.

- [ ] **Step 5: Add hand-calculated deterministic cases**

Extend `Deterministic test examples` with these exact expectations:

```text
- For state sequence [A, B], alpha = 0 gives A's row [0, 1] and final-only B's
  row [1/2, 1/2].
- A nonempty alpha = 0 row equals its empirical transition frequencies.
- A positive-alpha empty row is uniform through the ordinary formula.
- A stub RNG enters the final-only state, samples its uniform row, and reaches
  the global IAT fallback.
- Serialization and loading preserve and validate the uniform row.
- Missing global IAT data remains invalid.
```

- [ ] **Step 6: Verify the model contract**

Run:

```bash
rg -n \
  -e 'N_j=.*sum|N_j.*alpha K.*>0|N_j=0.*alpha=0|1.*K' \
  -e 'continuous extension|no RNG|ordinary transition row|not an invalid candidate' \
  -e 'empty transition row|global IAT|\[A, B\]|\[1/2, 1/2\]' \
  architecture/traffic_models/markov_renewal.md
git diff --check
awk 'length($0) > 120 { print FNR ":" length($0); bad=1 } END { exit bad }' \
  architecture/traffic_models/markov_renewal.md
```

Expected: the estimator covers both denominator branches, the existing timing
fallback owns the empty source, every focused example is present, and formatting
is clean.

- [ ] **Step 7: Commit the estimator contract**

```bash
git add architecture/traffic_models/markov_renewal.md
git commit -m "docs: define empty Markov row"
```

---

### Task 2: Assign test and roadmap ownership

**Files:**
- Modify: `architecture/TESTING.md:75-128`
- Modify: `architecture/TESTING.md:132-166`
- Modify: `architecture/ROADMAP.md:151-180`

**Interfaces:**
- Consumes: the complete estimator, ordinary serialization, and global-IAT
  fallback from Task 1
- Produces: focused unit/integration evidence and Phase 4 implementation
  ownership

- [ ] **Step 1: Add the mathematical unit expectation**

After the existing Markov Renewal hand-calculation bullet in `TESTING.md`, add:

```text
- Markov empty row: state sequence [A, B] with alpha = 0 gives rows [0, 1] and
  [1/2, 1/2]. Positive smoothing gives the same second row through the ordinary
  formula.
```

Extend the traffic-model unit-test scope to require:

```text
final-only active states, nonempty unsmoothed rows, uniform empty rows, global-IAT
fallback after an empty row, serialization/loading, and invalid missing global
IAT data
```

- [ ] **Step 2: Add one in-process model integration case**

Extend the existing all-family model integration item rather than creating a new
test subsystem. Require its Markov fixture to include a final-only active state
and `alpha = 0`, then assert:

```text
fit -> serialize -> load -> generate completes without an undefined row or
invalid-candidate result, and an equal model, seed, W, and limits reproduce the
same trace
```

This test remains in-process and does not start Docker.

- [ ] **Step 3: Assign implementation to Roadmap Phase 4**

In Phase 4 deliverables, extend the Markov Renewal item to name:

```text
the uniform zero-smoothing empty-row rule and its unchanged global-IAT fallback
```

In Phase 4 tests, require the `[A, B]` hand calculation, positive-smoothing
equivalence, model JSON round-trip, fixed-seed generation, and missing-global-IAT
failure. Do not add or reorder a phase.

- [ ] **Step 4: Verify evidence ownership**

Run:

```bash
rg -n \
  -e 'Markov empty row|\[A, B\]|\[1/2, 1/2\]|positive smoothing' \
  -e 'final-only active state|global-IAT|serialize.*load.*generate|fixed seed' \
  architecture/TESTING.md
rg -n \
  -e 'Phase 4|uniform zero-smoothing empty-row|global-IAT' \
  -e '\[A, B\]|positive-smoothing|JSON round-trip|fixed-seed' \
  architecture/ROADMAP.md
test "$(rg -c '^## Phase [1-7]' architecture/ROADMAP.md)" = "7"
git diff --check
```

Expected: unit and in-process evidence exercise the exact undefined case, Phase
4 owns implementation, seven phases remain, and formatting is clean.

- [ ] **Step 5: Commit evidence ownership**

```bash
git add architecture/TESTING.md architecture/ROADMAP.md
git commit -m "docs: test empty Markov row"
```

---

### Task 3: Verify scope and cross-document consistency

**Files:**
- Verify: `architecture/traffic_models/markov_renewal.md`
- Verify: `architecture/TESTING.md`
- Verify: `architecture/ROADMAP.md`
- Verify: `architecture/SYSTEM.md`
- Verify: `architecture/traffic_models/README.md`
- Verify: `architecture/similarity_methods/README.md`
- Verify: `architecture/genetic_models/README.md`

**Interfaces:**
- Consumes: Tasks 1-2
- Produces: evidence that the mathematical gap is closed without expanding MVP
  configuration, artifacts, algorithms, or phases

- [ ] **Step 1: Verify links and stable counts**

Run:

```bash
uv run --locked python - <<'PY'
from pathlib import Path
import re

missing = []
for document in Path("architecture").rglob("*.md"):
    for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", document.read_text()):
        if "://" in target or target.startswith("#"):
            continue
        path = (document.parent / target.split("#", 1)[0]).resolve()
        if not path.exists():
            missing.append(f"{document}: {target}")
assert not missing, "\n".join(missing)

assert len(re.findall(r"^\| \[", Path("architecture/traffic_models/README.md").read_text(), re.MULTILINE)) == 3
assert len(re.findall(r"^\| \[", Path("architecture/similarity_methods/README.md").read_text(), re.MULTILINE)) == 4
assert len(re.findall(r"^- \[", Path("architecture/genetic_models/README.md").read_text(), re.MULTILINE)) == 1
assert len(re.findall(r"^## Phase [1-7]", Path("architecture/ROADMAP.md").read_text(), re.MULTILINE)) == 7
PY
```

Expected: every local link resolves and counts remain three models, four
similarity methods, one genetic strategy, and seven phases.

- [ ] **Step 2: Verify the complete rule chain**

Run:

```bash
rg -n \
  -e 'N_j=0.*alpha=0|1.*K|continuous extension' \
  -e 'ordinary transition row|global IAT|not an invalid candidate' \
  architecture/traffic_models/markov_renewal.md
rg -n 'Markov empty row|final-only active state|\[1/2, 1/2\]' \
  architecture/TESTING.md architecture/ROADMAP.md
```

Expected: the estimator, timing behavior, failure result, tests, and phase owner
form one consistent chain.

- [ ] **Step 3: Verify that scope did not expand**

Run:

```bash
test "$(git diff --name-only HEAD~2..HEAD | sort)" = "$(printf '%s\n' \
  architecture/ROADMAP.md \
  architecture/TESTING.md \
  architecture/traffic_models/markov_renewal.md | sort)"
test -z "$(git diff --name-only HEAD~2..HEAD -- architecture/SYSTEM.md)"
test -z "$(git diff --name-only HEAD~2..HEAD -- architecture/traffic_models/README.md)"
test -z "$(git diff --name-only HEAD~2..HEAD -- architecture/similarity_methods/README.md)"
test -z "$(git diff --name-only HEAD~2..HEAD -- architecture/genetic_models/README.md)"
git diff --check HEAD~2..HEAD
git status --short
```

Expected: only the three owning documents changed, no registry/configuration or
artifact surface changed, the two commits are whitespace-clean, and the worktree
is clean.

- [ ] **Step 4: Record only necessary corrections**

If the verification finds a mismatch, make the smallest correction in the
owning document, rerun Tasks 1-3 checks, and commit it:

```bash
git add architecture/
git commit -m "docs: fix empty Markov row consistency"
```

If every check passes without a correction, do not create an empty commit.
