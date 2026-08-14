# Reference Observation Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give reference normalization, all model families, every genetic trial,
and all similarity methods one explicit reference-derived observation window.

**Architecture:** Derive `W = t_n - t_1` once while preparing the reference,
pass that scalar through generation and evaluation, and persist it in existing
result artifacts. Remove independent generation-duration and multiscale-horizon
configuration while retaining packet-count, output-size, and wall-time
reliability guards.

**Tech Stack:** Markdown architecture contracts, canonical PCAPNG event traces,
three interpretable traffic models, four similarity methods, basic generational
genetic search, pytest integration scopes

## Global Constraints

- Define `W = t_n - t_1` from at least two finite, nondecreasing reference timestamps and require finite `W > 0`.
- Normalize reference timestamps with `t'_i = t_i - t_1`; include events at both `0` and `W`.
- Shift a nonempty generated trace to its first packet and discard generated events after `W` before comparison.
- Use the same complete `W` for every model family, every genetic trial seed,
  final generation, and all four similarity methods.
- Store `observation_window_seconds` in `best_model.json` and `similarity.json`; add no artifact or trace wrapper.
- Remove configured trial/final generation durations and the configured multiscale horizon.
- Keep packet-count, output-size, and wall-time limits only as reliability guards, never as shorter scientific windows.
- Treat a guard reached before full-window completion as invalid-candidate
  fitness during trials and as an unpublished stage failure during final
  generation.
- Keep strict `capture.json` at exactly `interface` and `target_mac`; do not change capture lifecycle or metadata.
- Preserve all existing model formulas, similarity formulas, direction rules,
  genetic seed separation, and command names.
- Do not address genetic operators, reproducibility metadata, zero-smoothing rows, or other audit findings in this fix.

---

### Task 1: Define the system-wide observation-window contract

**Files:**
- Modify: `architecture/SYSTEM.md:3-35`
- Modify: `architecture/SYSTEM.md:85-123`
- Modify: `architecture/SYSTEM.md:129-189`

**Interfaces:**
- Consumes: ordered reference and generated canonical event sequences
- Produces: `W`, normalized/cropped events, explicit generation/evaluation
  signatures, and artifact/configuration ownership used by all later tasks

- [ ] **Step 1: Add reference normalization to the canonical trace boundary**

After the canonical trace definition and direction rule, add the exact scientific
contract:

```text
For a reference with n >= 2 finite, nondecreasing timestamps, define
W = t_n - t_1 and require finite W > 0. Normalize every reference timestamp as
t'_i = t_i - t_1, so the reference occupies the closed interval [0, W].
Packets at both endpoints are included.

Before comparison, require a nonempty generated trace, shift it to its first
packet, and retain only events in [0, W]. The same W is passed explicitly to
fitting, generation, genetic evaluation, and every similarity method. Silence
outside the reference's first and last packets is outside MVP scope.
```

Keep the existing Ethernet direction and generated-MAC rules immediately after
this contract. Do not add a canonical-trace wrapper type.

- [ ] **Step 2: Align stage descriptions with the derived window**

Make these stage behaviors explicit:

```text
fit: normalize the reference once, derive W, and give every candidate the same W.
generate: load observation_window_seconds from best_model.json and simulate the
          complete [0, W] interval with the distinct final seed.
compare: normalize/crop both traces at the shared boundary, pass W to all four
         methods, and store W with component diagnostics.
```

Retain checkpointing, final-seed separation, summaries, and command composition.
Replace “short trial traces” with “trial generations”; trials may use different
seeds and reliability budgets but never a shorter observation window.

- [ ] **Step 3: Remove conflicting configuration durations**

Change the configuration bullets so they name:

```text
- trial and final packet-count and output-size guards plus candidate wall-time limits;
- similarity lags, time-bin widths, feature weights, method weights, and maximum
  total direction-bin cell count.
```

State that multiscale width-versus-window validation and the derived cell count
occur after reference parsing because `W` is not a configuration value. Do not
add `observation_window` or `horizon` to TOML.

- [ ] **Step 4: Define persistence and research signatures**

In the run-directory explanation, require:

```text
best_model.json and similarity.json contain the same finite positive
observation_window_seconds derived from the identified reference input.
```

Replace the two interfaces with:

```text
fit(reference, genes) -> fitted model
generate(fitted model, seed, observation_window, limits) -> canonical trace
serialize(fitted model) -> JSON-compatible value

evaluate(reference, generated, observation_window, settings) -> score, diagnostics
```

State that every method returns `observation_window_seconds` in diagnostics and
that fitting stores it in the winning fitted model.

- [ ] **Step 5: Verify the system contract**

Run:

```bash
rg -n \
  -e 'W = t_n - t_1|closed interval|nonempty generated|same W' \
  -e 'observation_window_seconds|wall-time limits|after reference parsing' \
  -e 'generate\(fitted model, seed, observation_window, limits\)' \
  -e 'evaluate\(reference, generated, observation_window, settings\)' \
  architecture/SYSTEM.md
! rg -n 'trial and final packet-count, duration|configured.*horizon|Short trial traces' architecture/SYSTEM.md
git diff --check
```

Expected: one derived window owns normalization, interfaces, configuration, and
artifacts; conflicting duration language is absent; whitespace is clean.

- [ ] **Step 6: Commit the system contract**

```bash
git add architecture/SYSTEM.md
git commit -m "docs: define shared observation window"
```

### Task 2: Make similarity evaluation consume the shared window

**Files:**
- Modify: `architecture/similarity_methods/README.md:1-42`
- Modify: `architecture/similarity_methods/multiscale_rate.md:8-104`

**Interfaces:**
- Consumes: normalized reference/generated events and finite `observation_window = W > 0` from Task 1
- Produces: a common similarity boundary and multiscale bins derived only from `W`

- [ ] **Step 1: Put normalization and cropping at the similarity boundary**

In the catalog, replace the method signature with:

```text
evaluate(reference, generated, observation_window, settings) -> score, diagnostics
```

State that the shared boundary, not individual methods, validates already
normalized/cropped events in `[0, W]`. Every method receives the same finite
positive `W` and returns it as `observation_window_seconds` in diagnostics.
Frame-size KS, IAT KS, and ACF remain sample-based; they do not invent samples
for boundary silence.

- [ ] **Step 2: Remove horizon configuration from the catalog**

Change only the multiscale table configuration cell from:

```text
Horizon, widths, weights, cell cap
```

to:

```text
Widths, weights, cell cap
```

Keep exactly four methods and every existing method limitation.

- [ ] **Step 3: Derive multiscale bins from `W`**

Replace the start of “Time series construction” with this definition:

```text
Receive normalized traces and their shared observation window W > 0. For each
unique, strictly increasing width 0 < h <= W, create B = ceil(W / h) bins. Bins
are left-closed and right-open, except the last bin includes timestamp W. Input
events outside [0, W] are a shared-boundary error rather than silently ignored.
```

Retain direction-separated vectors, normalized L1 discrepancy, feature/scale
weights, and `2 sum_h B_h <= C_max` unchanged.

- [ ] **Step 4: Align diagnostics and edge cases**

Replace configured-horizon wording with the derived observation window:

```text
Return observation_window_seconds, widths, direction-bin cell counts, weights,
direction totals, per-scale/per-feature discrepancies, and final discrepancy.
Require finite W > 0 and widths no larger than W. A trace whose last packet is
before W has trailing zero bins. An event exactly at W enters the last bin.
```

State that alignment and cropping are shared Trafficlab boundary choices, while
the multiscale method owns the endpoint bin rule and trailing-zero representation.

- [ ] **Step 5: Verify similarity consistency**

Run:

```bash
rg -n \
  -e 'evaluate\(reference, generated, observation_window, settings\)' \
  -e 'observation_window_seconds|already normalized|sample-based' \
  -e 'Widths, weights, cell cap' \
  architecture/similarity_methods/README.md
rg -n \
  -e 'shared observation window|B=.*ceil|outside.*shared-boundary error' \
  -e 'trailing zero bins|event exactly at.*W|2\\sum_h B_h' \
  architecture/similarity_methods/multiscale_rate.md
! rg -n \
  'configured horizon|Return horizon|positive finite horizon|Events after H are ignored' \
  architecture/similarity_methods
test "$(rg -c '^\| \[' architecture/similarity_methods/README.md)" = "4"
git diff --check
```

Expected: all methods share `W`, multiscale derives its cells from `W`, the
catalog still contains four methods, and no independent horizon remains.

- [ ] **Step 6: Commit the similarity contract**

```bash
git add architecture/similarity_methods/
git commit -m "docs: derive similarity horizon"
```

### Task 3: Make every model and genetic trial complete `[0, W]`

**Files:**
- Modify: `architecture/traffic_models/README.md:1-42`
- Modify: `architecture/traffic_models/poisson_empirical.md:10-77`
- Modify: `architecture/traffic_models/markov_renewal.md:76-112`
- Modify: `architecture/traffic_models/mmpp.md:46-84`
- Modify: `architecture/genetic_models/basic_generational.md:9-21`
- Modify: `architecture/genetic_models/basic_generational.md:80-108`

**Interfaces:**
- Consumes: normalized reference, explicit `W`, and reliability `limits` from Task 1
- Produces: three full-window generators and genetic fitness that cannot silently compare truncated trials

- [ ] **Step 1: Define the shared model completion rule**

In the traffic-model catalog, use:

```text
generate(fitted model, seed, observation_window, limits) -> canonical trace
```

State exactly:

```text
Every generator starts at zero and simulates the complete closed interval
[0, W]. It emits events at or before W and finishes normally only after the next
simulated event would be after W. Packet-count, output-size, and wall-time limits
are guards. Reaching one before full-window completion returns an explicit
incomplete-generation error, not a shortened trace.
```

Add `observation_window_seconds` to the existing `best_model.json` contents.
In “Fair competition,” replace packet/duration limits with the same `W` and the
same reliability guards for every candidate.

- [ ] **Step 2: Update Poisson generation**

Keep `W = t_n - t_1` and `lambda_hat = (n - 1) / W` mathematically unchanged,
but name `W` as the shared observation window. In generation:

```text
Place the first packet at zero. Repeatedly sample the next exponential delay.
Emit when next_time <= W. Finish normally when next_time > W. If a reliability
guard stops this process before that comparison establishes completion, return
incomplete generation.
```

Add deterministic cases for retaining a stubbed event at `W`, excluding one
after `W`, natural early completion, and guard interruption.

- [ ] **Step 3: Update Markov Renewal generation**

Replace “duration limit” with `W` in the algorithm:

```text
sample destination and holding time;
finish normally when the next timestamp is greater than W;
emit the destination event when the next timestamp is at most W;
return incomplete generation if a reliability guard is reached first.
```

Retain state transitions, timing fallback, marks, and RNG rules. Add the same
endpoint, natural-completion, and guard-interruption deterministic cases.

- [ ] **Step 4: Update MMPP generation**

State that arrival and regime-change simulation continues over `[0, W]`:

```text
Sample the stationary initial regime and emit one empirical mark at time zero.
Process the earlier sampled event when its time is <= W. Emit only arrivals.
Finish normally when the next arrival/regime-change event would be after W.
Retain the existing exact-tie regime-change-first rule. A reliability guard
reached before normal completion returns incomplete generation.
```

Name the forced event at zero as a Trafficlab trace-alignment choice, not a
general MMPP property. Add scripted initial-event, endpoint/later-event, and
guard-interruption cases without changing the CTMC or stationary formulas.

- [ ] **Step 5: Make the GA reject truncated trials**

In “Fitness,” require every candidate and seed to use the same complete `W` as
well as common reliability guards. In “Invalid candidates and failures,” add an
incomplete-generation guard result to family-level invalid candidates with
fitness `0` and a reason.

In “Termination and final evaluation,” replace duration limits with packet-count,
output-size, and wall-time guards. State that fresh final seeds use the same `W`
as selection and that final generation failure is a stage error rather than a
candidate score.

- [ ] **Step 6: Verify model and GA semantics**

Run:

```bash
rg -n \
  -e 'generate\(fitted model, seed, observation_window, limits\)' \
  -e 'complete closed interval|incomplete-generation' \
  -e 'observation_window_seconds|same `W`' \
  architecture/traffic_models/README.md
rg -n \
  'next.*<= W|next.*> W|event at `W`|incomplete generation|reliability guard' \
  architecture/traffic_models/*.md
rg -n \
  -e 'same complete `W`|incomplete-generation|fitness `0`' \
  -e 'same `W` as selection|packet-count, output-size, and wall-time' \
  architecture/genetic_models/basic_generational.md
! rg -n \
  'duration limit|packet/duration limits|packet, duration, output-size' \
  architecture/traffic_models architecture/genetic_models
test "$(rg -c '^\| \[' architecture/traffic_models/README.md)" = "3"
git diff --check
```

Expected: all three families simulate the same window, guard truncation is never
treated as a valid short trace, the catalog still contains three models, and no
independent generation duration remains.

- [ ] **Step 7: Commit model and genetic semantics**

```bash
git add architecture/traffic_models/ architecture/genetic_models/basic_generational.md
git commit -m "docs: require full-window generation"
```

### Task 4: Add observation-window evidence to testing and roadmap

**Files:**
- Modify: `architecture/TESTING.md:59-125`
- Modify: `architecture/ROADMAP.md:64-204`

**Interfaces:**
- Consumes: the system, similarity, model, and genetic contracts from Tasks 1-3
- Produces: focused test expectations and phase deliverables that make the shared-window contract implementable

- [ ] **Step 1: Add focused unit evidence**

Extend unit-test coverage with:

```text
- reference observation-window derivation, timestamp normalization, endpoint
  inclusion, generated-trace shifting/cropping, and invalid reference windows;
- one shared W in all four similarity diagnostics;
- full-window natural completion and incomplete-generation guards for all three
  model families;
- best_model.json observation_window_seconds serialization and loading.
```

Add fixed expectations:

```text
Reference [10, 11, 13] normalizes to [0, 1, 3] with W = 3. A generated event at
3 is retained and an event after 3 is cropped. A naturally earlier last event
creates multiscale trailing zeros; a safety guard reached first is incomplete.
```

Replace the generic packet/duration-limit generator assertion with the explicit
`[0, W]` endpoint and reliability-guard contract.

- [ ] **Step 2: Trace `W` through in-process integration**

In the PCAPNG/canonical integration item, require derivation of `W`. In the
model/similarity and offline analytical pipeline items, require the identical
value in every candidate evaluation, `best_model.json`, final generation, every
component diagnostic, and `similarity.json`.

Do not add Docker assertions: capture metadata and lifecycle are unchanged.

- [ ] **Step 3: Assign the behavior to existing roadmap phases**

In Phase 2, add reference normalization, `W` derivation, generated cropping, and
the replacement of configured multiscale horizon with `W`. Require endpoint and
invalid-window tests using checked-in fixtures.

In Phase 4, replace common duration-limit wording with complete `[0, W]`
generation and reliability guards for all three models. Require endpoint,
natural-completion, incomplete-generation, and serialized-window tests.

In Phase 5, require the same complete `W` for every candidate/trial seed and
invalid fitness for incomplete generation. Keep final fresh seeds unchanged.

- [ ] **Step 4: Verify evidence ownership**

Run:

```bash
rg -n \
  -e 'observation window|W = 3|event at.*3|trailing zeros' \
  -e 'incomplete-generation|observation_window_seconds|identical.*candidate' \
  architecture/TESTING.md
rg -n \
  -e 'derive.*W|generated.*crop|multiscale.*W|complete.*\[0, W\]' \
  -e 'reliability guards|same complete.*W|incomplete generation' \
  architecture/ROADMAP.md
! rg -n \
  'packet/duration limit|common packet, duration|configured.*horizon' \
  architecture/TESTING.md architecture/ROADMAP.md
test "$(rg -c '^## Phase [1-7]' architecture/ROADMAP.md)" = "7"
git diff --check
```

Expected: unit/integration evidence covers the approved semantics, the behavior
belongs to Phases 2, 4, and 5, exactly seven phases remain, and capture scope is
unchanged.

- [ ] **Step 5: Commit testing and roadmap alignment**

```bash
git add architecture/TESTING.md architecture/ROADMAP.md
git commit -m "docs: test shared observation window"
```

### Task 5: Verify observation-window consistency across the architecture

**Files:**
- Verify: `architecture/SYSTEM.md`
- Verify: `architecture/similarity_methods/`
- Verify: `architecture/traffic_models/`
- Verify: `architecture/genetic_models/basic_generational.md`
- Verify: `architecture/TESTING.md`
- Verify: `architecture/ROADMAP.md`

**Interfaces:**
- Consumes: all documentation contracts from Tasks 1-4
- Produces: evidence that the architecture has one window, no conflicting duration, and unchanged MVP/capture scope

- [ ] **Step 1: Verify internal links and stable catalog sizes**

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
assert len(re.findall(r"^## Phase [1-7]", Path("architecture/ROADMAP.md").read_text(), re.MULTILINE)) == 7
PY
```

Expected: all links resolve; three models, four methods, and seven phases remain.

- [ ] **Step 2: Verify one end-to-end window contract**

Run:

```bash
rg -n \
  -e 'W = t_n - t_1|observation_window_seconds' \
  -e 'generate\(fitted model, seed, observation_window, limits\)' \
  -e 'evaluate\(reference, generated, observation_window, settings\)' \
  architecture/SYSTEM.md
rg -n 'observation_window_seconds|shared observation window|trailing zero bins' architecture/similarity_methods
rg -n 'complete closed interval|incomplete-generation|same `W`' architecture/traffic_models architecture/genetic_models
rg -n 'W = 3|observation_window_seconds|incomplete-generation' architecture/TESTING.md
```

Expected: derivation flows through interfaces, algorithms, persistence, and tests.

- [ ] **Step 3: Verify stale configuration is absent and capture is unchanged**

Run:

```bash
! rg -n \
  -e 'configured horizon|trial and final packet-count, duration' \
  -e 'duration limit|packet/duration limits|common packet, duration' \
  architecture/SYSTEM.md architecture/similarity_methods \
  architecture/traffic_models architecture/genetic_models \
  architecture/TESTING.md architecture/ROADMAP.md
test "$(rg -c '"interface"' architecture/SYSTEM.md)" = "1"
test "$(rg -c '"target_mac"' architecture/SYSTEM.md)" = "1"
rg -n \
  'containing only `interface: "eth0"` and the|strict two-field|exactly:' \
  architecture/CAPTURE.md architecture/SYSTEM.md \
  docs/superpowers/specs/2026-08-11-reference-observation-window-design.md
git diff --check HEAD~4..HEAD
git status --short
```

Expected: no conflicting scientific duration remains, strict capture metadata is
still two fields, the four implementation commits are whitespace-clean, and the
working tree is clean.

- [ ] **Step 4: Record only necessary consistency corrections**

If any verification in Steps 1-3 fails, make the smallest correction in the
owning architecture document, rerun all three steps, and commit it:

```bash
git add architecture/
git commit -m "docs: fix observation-window consistency"
```

If all checks pass without changes, do not create an empty commit.
