# Minimum Research Fitness Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the authoritative architecture and Roadmap specify the minimal
work needed to raise all 17 sub-Acceptable research-fitness criteria to at least
Acceptable without changing implementation or assessment grades.

**Architecture:** Update each owning architecture document first, then
restructure the existing MVP phases around those contracts and add a final
minimum-fitness acceptance phase. Preserve completed Acceptable work, reopen
only contradicted requirements, and retain historical evidence as dated history
rather than current proof.

**Tech Stack:** Markdown architecture documents, CommonMark links, Git, `rg`,
`awk`, Ruff's Markdown-aware format check, and existing documentation tests.

## Global Constraints

- The approved design is
  `docs/superpowers/specs/2026-08-14-research-fitness-remediation-design.md`
  at commit `c45f71a`.
- Modify architecture documentation only. Do not modify `src/`, `tests/`,
  Dockerfiles, fixtures, configurations, example evidence, or dependency files.
- Do not edit `docs/RESEARCH_FITNESS_ASSESSMENT.md` or change any grade during
  this work.
- Do not weaken or reinterpret
  `architecture/RESEARCH_FITNESS_CRITERIA.md`.
- Target Acceptable evidence. Do not add Excellent-only requirements unless the
  unchanged Acceptable anchor itself makes the behavior necessary.
- Keep the prototype one-process, classical-model, Docker-capture architecture.
  Add no service, database, object store, workflow framework, security system,
  plug-in system, or multi-user feature.
- Use these exact terms consistently: `arrival-epoch initialization`,
  `mandatory similarity method`, `fresh simulation seed`, `held-out reference`,
  `portable configuration`, `realized configuration`, `accepted evidence
  bundle`, and `offline audit`.
- All four similarity methods remain mandatory. A zero method weight means zero
  aggregate contribution, not disabled execution.
- The MMPP initial regime uses
  `a_z = pi_z * lambda_z / sum_j(pi_j * lambda_j)`.
- Family priority comes from one temporary
  `random.Random(master_seed).sample(sorted_family_names,
  len(sorted_family_names))` call and does not consume the search RNG stream.
- Accepted evidence is checked under
  `examples/validation_study/evidence/<study-id>/`; ordinary, failed, and
  scratch runs remain ignored.
- One independent held-out reference per workload is required because the
  unchanged criterion 5.7 Acceptable anchor explicitly requires held-out
  validation.
- Keep lines at or below 120 characters and use `apply_patch` for every
  hand-authored edit.
- Run no Docker or Internet workload during this documentation-only plan.
- Commit each task separately with only its declared files staged.

## File map

| File | Responsibility in this plan |
|---|---|
| `architecture/README.md` | Architecture navigation, current phase, and fitness-remediation scope |
| `architecture/SYSTEM.md` | Shared scientific, configuration, lineage, evidence, and failure contracts |
| `architecture/CAPTURE.md` | Reproducible capture image/environment and capture diagnostics |
| `architecture/DEVELOPMENT.md` | Pinned build inputs and checked accepted-evidence exception |
| `architecture/traffic_models/README.md` | Shared model semantics version and validation interpretation |
| `architecture/traffic_models/poisson_empirical.md` | Active-span conditioning and direct validation |
| `architecture/traffic_models/markov_renewal.md` | Fallback interpretation/diagnostics and direct validation |
| `architecture/traffic_models/mmpp.md` | Arrival-epoch mathematics, generation, and validation |
| `architecture/similarity_methods/README.md` | Mandatory-method and zero-weight semantics |
| `architecture/genetic_models/README.md` | Search interpretation and limits |
| `architecture/genetic_models/basic_generational.md` | Neutral priority, compatibility, and resume |
| `architecture/TESTING.md` | Required direct science, portability, diagnostics, evidence, and audit gates |
| `architecture/ROADMAP.md` | Truthful phase reopenings, Phase 8, and criterion traceability |

## Specification coverage map

| Approved design section | Implementing tasks |
|---|---|
| MMPP initialization and scientific compatibility | Tasks 1, 3, 5, 6 |
| Similarity-weight semantics | Tasks 1, 4, 5, 6 |
| Neutral heterogeneous competition | Tasks 1, 4, 5, 6 |
| Direct scientific validation | Tasks 3, 4, 5, 6 |
| Assumptions and interpretation | Tasks 1, 3, 4 |
| Configuration fidelity and portability | Tasks 1, 2, 5, 6 |
| Environment reproducibility | Tasks 2, 5, 6 |
| Failure and diagnostic evidence | Tasks 1, 2, 4, 5, 6 |
| Fresh and resumed equivalence | Tasks 1, 4, 5, 6 |
| Training, fresh simulation, and held-out evidence | Tasks 1, 5, 6 |
| Retained accepted-study evidence | Tasks 1, 2, 5, 6 |
| Independent offline reconstruction | Tasks 1, 5, 6 |
| Roadmap restructuring and Phase 8 | Task 6 |
| Documentation quality and independent review | Task 7 |

---

### Task 1: Define shared system and configuration contracts

**Files:**

- Modify: `architecture/SYSTEM.md:1-360`

**Interfaces:**

- Consumes: approved terms, formulas, and evidence boundaries from the design.
- Produces: authoritative contracts used verbatim by model, genetic, testing,
  capture, and Roadmap tasks.

- [ ] **Step 1: Record the pre-edit contradictions**

Run:

```bash
rg -n -e 'lexical order of enabled family names' \
  -e 'every enabled similarity method' \
  -e 'there is no separate lineage graph' -e 'fresh final seeds' \
  architecture/SYSTEM.md
```

Expected before editing: matches in `fit`, `compare`, `Run directory`, or the
genetic configuration text. These phrases conflict with neutral family
priority, mandatory-method terminology, retained evidence, or exact seed
interpretation.

- [ ] **Step 2: Replace shared stage semantics**

Use `apply_patch` to make these exact normative changes:

```text
fit:
- Family display order remains lexical.
- Search priority is one master-seed permutation of sorted enabled families.
- The priority controls quota remainders, initial family order, and exact
  cross-family ties, and is retained in checkpoint state.
- Final validation uses a fresh simulation seed on the same training reference;
  it is not held-out data.

generate:
- Fitted models and checkpoints carry the current global scientific artifact
  schema version.
- Legacy MMPP semantics fail before reuse, resume, or generation.

compare:
- All four similarity methods are mandatory.
- Every method runs, validates its inputs, and retains diagnostics.
- A zero weight changes only aggregate contribution.
```

Keep the existing stage order, public CLI, and fixed four-method result shape.

- [ ] **Step 3: Define portable and realized configurations**

Add a subsection under `Experiment configuration` containing this contract:

```text
Portable configuration:
- retains config-relative run and bind-mount source paths;
- contains every explicit scientific and workload value;
- is suitable for transfer to another compatible clean environment.

Realized configuration:
- applies defaults;
- resolves only run.directory and declared bind-mount host sources to absolute
  paths;
- retains every image, argv, environment, URL, seed, bound, limit, operator,
  and similarity value without substitution.

Preflight rejects an unavailable or incompatible realization before scientific
artifact publication. Every accepted study run retains the portable/realized
pair and their identities.
```

State that all four method settings remain required and weights are finite in
`[0, 1]`, normalized under the existing numeric rule, and never enable/disable
execution.

- [ ] **Step 4: Define scientific artifact compatibility**

Under `Run directory` and `Research interfaces`, add:

```text
- best_model.json and checkpoint.json use the bumped global artifact schema
  version for corrected model semantics;
- a well-formed older version is incompatible, not corrupt;
- compatibility is checked before generation, resume, or stage reuse;
- accepted reports require a retained evidence bundle in addition to the nine
  ordinary run artifacts;
- ordinary run directories still have exactly the existing nine names.
```

Do not add a production manifest to every ordinary run. The accepted study
bundle is a publication/evidence rule owned by Phase 7.

Add one stage-compatibility table:

```text
portable transfer:
  equal: every non-path scientific/workload value, image content identity,
  container mount target/mode, and mounted-input content identity
  may differ: checkout, run-directory, and host mount-source absolute paths

capture reuse:
  exact realized snapshot bytes plus capture identity and both capture files

fit resume:
  exact reference/capture identities, scientific schema, CPython patch,
  families, genes/bounds/operators, seeds, trial limits, similarity settings

generate reuse:
  exact best-model/schema identity, final seed/limits, capture identity

compare reuse:
  exact capture/reference/generated/settings identities

offline reconstruction:
  exact retained source tree, uv.lock, CPython, scientific schema, and artifact
  identities; Docker/Compose/kernel are recorded but not invoked
```

Each mismatch names the first incompatible field and fails before reuse.

- [ ] **Step 5: Define one canonical failure outcome**

Extend `Failure policy` without replacing the existing event arbitration or
exception behavior. Add the exact fields:

```text
kind
stage
detail
affected_evidence
evidence_state = not_published | diagnostic_only | preserved | possibly_remaining
corrective_action
authority = primary | secondary
status (optional exact external/process status)
```

Enumerate only these boundary classes: configuration/path, Docker/preflight,
external exit/timeout/interruption/malformed output, missing/changed/foreign or
corrupt artifact, incompatible scientific semantics, metric/sample/numeric
infeasibility, generation guard/deadline, publication, cleanup, and combined
failures. Candidate-invalid diagnostics expose equivalent scientific fields.

- [ ] **Step 6: Define published evidence and interpretation**

Add a `Published study evidence` subsection after `Run directory` that requires:

```text
- checked accepted bundle at examples/validation_study/evidence/<study-id>/;
- every report-cited primary/reproduction strict nine-file run tree;
- portable configs, held-out inputs/results, prerequisite evidence, environment
  record, and canonical path/size/SHA-256 inventory;
- publication only after bounded offline audit;
- hashes without retained bytes do not qualify;
- scores/winners are descriptive and do not establish likelihood, causal
  mechanism, universal superiority, or unseen-program generalization.
```

- [ ] **Step 7: Verify the shared contract and commit**

Run:

```bash
rg -n -e 'arrival-epoch' -e 'mandatory similarity' \
  -e 'fresh simulation seed' -e 'portable configuration' \
  -e 'realized configuration' -e 'accepted evidence bundle' \
  -e 'evidence_state' -e 'scientific artifact schema' \
  architecture/SYSTEM.md
! rg -n 'lexical order of enabled family names|every enabled similarity method|held-out seed' \
  architecture/SYSTEM.md
awk 'length($0) > 120 { print FNR ":" length($0) }' architecture/SYSTEM.md
git diff --check -- architecture/SYSTEM.md
git diff -- architecture/SYSTEM.md
```

Expected: required terms present, contradiction scan empty, no overlong or
whitespace-error output, and the diff changes only the specified contracts.

Commit:

```bash
git add architecture/SYSTEM.md
git commit -m "docs: define research fitness system contracts"
```

### Task 2: Define reproducible capture and development evidence

**Files:**

- Modify: `architecture/CAPTURE.md:1-245`
- Modify: `architecture/DEVELOPMENT.md:1-220`

**Interfaces:**

- Consumes: portable/realized configuration, canonical failure outcome, and
  accepted evidence bundle from Task 1.
- Produces: exact capture-image reproducibility and environment-compatibility
  requirements used by Testing and Roadmap.

- [ ] **Step 1: Establish the mutable-image RED audit**

Run:

```bash
rg -n 'digest|snapshot.debian.org|exact package|environment record' \
  architecture/CAPTURE.md architecture/DEVELOPMENT.md
```

Expected before editing: the complete capture-image reproducibility contract is
absent.

- [ ] **Step 2: Add the capture-image identity contract**

In `CAPTURE.md`, add `Reproducible capture environment` after `Preflight`:

```text
- FROM uses the approved Debian tag plus exact sha256 digest;
- apt reads one dated snapshot.debian.org archive state;
- every directly installed Dockerfile package has an exact Debian version;
- docker/capture/image-lock.json is a small checked canonical record containing
  the base digest, snapshot timestamp, direct package versions, capture-tool
  version, and expected resolved capture-image content ID;
- a successful build must equal the checked expected content ID; merely
  recording a newly resolved ID is insufficient;
- preflight records target/capture references and resolved content IDs;
- reuse rejects any mismatch in the exact capture-reuse fields defined by the
  shared stage-compatibility table;
- unavailable snapshot/package/image inputs fail rather than silently updating.
```

Cite Docker's official digest-pinning guidance and Debian Snapshot. Do not add
signature verification, a build-policy engine, or host security checks.

- [ ] **Step 3: Bind capture failures to the shared diagnostic contract**

In `Reliability behavior`, preserve the current priority/cleanup rules and
state that each listed failure records the Task 1 fields. Map affected evidence
explicitly:

```text
readiness/target/capture/flush/timeout -> capture pair state
metadata/malformed output -> diagnostic pair or not_published
cleanup -> possibly_remaining project inventory
successful validated capture -> preserved reusable pair
```

No new event, timeout, or lifecycle branch is introduced.

- [ ] **Step 4: Define development environment and evidence retention**

In `DEVELOPMENT.md`:

- retain the current CPython 3.12.3 fixture/checkpoint pin and uv lock rules;
- add capture Dockerfile digest/snapshot/package inputs to reproducibility
  review;
- require the accepted-study environment record to include source commit/tree,
  `uv.lock`, Python, target/capture identities, capture tool, Docker Engine,
  Compose, kernel, and architecture;
- state that compatibility uses only the exact category matrix below and no
  other host field;
- keep ordinary `runs/` and scratch study work ignored;
- define the narrow checked exception
  `examples/validation_study/evidence/<study-id>/`;
- forbid committing an accepted bundle until its offline audit succeeds.

Declare the compatibility categories exactly:

```text
must match for deterministic offline regeneration:
  source commit/tree, uv.lock, CPython patch, scientific schema, artifact bytes

must match for a fresh compatible capture environment:
  host architecture, target content ID, capture image-lock expected/resolved ID,
  capture-tool version, container argv/environment/workdir/mount target+mode,
  mounted-input content hashes

recorded external variation permitted after successful feature preflight:
  Docker Engine/Compose patch versions, kernel release, checkout/run/mount-source
  absolute paths
```

Existing capture reuse remains stricter: it requires the exact realized
snapshot and capture identities and never treats permitted fresh-environment
variation as reusable capture equivalence.

- [ ] **Step 5: Verify and commit**

Run:

```bash
rg -n 'Reproducible capture environment|sha256|snapshot.debian.org|evidence_state' \
  architecture/CAPTURE.md
rg -n 'accepted-study|accepted evidence|compatibility categories|uv.lock' \
  architecture/DEVELOPMENT.md
awk 'length($0) > 120 { print FILENAME ":" FNR ":" length($0) }' \
  architecture/CAPTURE.md architecture/DEVELOPMENT.md
git diff --check -- architecture/CAPTURE.md architecture/DEVELOPMENT.md
git diff -- architecture/CAPTURE.md architecture/DEVELOPMENT.md
```

Commit:

```bash
git add architecture/CAPTURE.md architecture/DEVELOPMENT.md
git commit -m "docs: require reproducible capture evidence"
```

### Task 3: Correct and qualify traffic-model science

**Files:**

- Modify: `architecture/traffic_models/README.md:1-60`
- Modify: `architecture/traffic_models/poisson_empirical.md`
- Modify: `architecture/traffic_models/markov_renewal.md:1-205`
- Modify: `architecture/traffic_models/mmpp.md:1-125`

**Interfaces:**

- Consumes: global scientific artifact version and interpretation boundary from
  Task 1.
- Produces: exact model mathematics, assumptions, diagnostics, and direct
  validation requirements used by Testing and Roadmap.

- [ ] **Step 1: Establish the MMPP semantics RED audit**

Run:

```bash
rg -n 'stationary distribution|time zero|first event|initial regime' \
  architecture/traffic_models/mmpp.md
! rg -n 'arrival-epoch|event-stationary|pi_z.*lambda_z' \
  architecture/traffic_models/mmpp.md
```

Expected: existing arbitrary-time stationary initialization is present and the
arrival-epoch contract is absent.

- [ ] **Step 2: Correct MMPP mathematics and generation**

In `mmpp.md`, distinguish arbitrary-time probabilities from event-stationary
probabilities:

```text
pi0 = q10 / (q01 + q10)
pi1 = q01 / (q01 + q10)
lambda_bar = pi0 * lambda0 + pi1 * lambda1
a0 = pi0 * lambda0 / lambda_bar
a1 = pi1 * lambda1 / lambda_bar
```

Replace the generation start with:

```text
1. Draw initial regime from (a0, a1).
2. Emit the conditioned arrival at t=0 with a joint empirical mark.
3. Draw the next arrival and regime-transition clocks in the existing order.
4. Continue the existing exact competing-clock and reliability behavior.
```

Add the exact hand case `q01=1`, `q10=3`, `lambda0=1`, `lambda1=9`,
`pi=(3/4,1/4)`, `a=(1/4,3/4)`. Cite Asanjarani and Nazarathy,
<https://arxiv.org/abs/1905.01736>, for event-stationary MMPP context.

- [ ] **Step 3: Define shared model compatibility and validation**

In `traffic_models/README.md`, state:

- fitted-model and checkpoint schema version identifies scientific semantics;
- the corrected version rejects older MMPP semantics before reuse;
- direct scientific tests use predeclared seeds, sample sizes, tolerances, and
  analytical/test-only oracles;
- serialization round trips and same-implementation generation are necessary
  but not sufficient scientific evidence.

Do not add a migration or plug-in mechanism.

- [ ] **Step 4: Add per-family assumptions and direct evidence**

In `poisson_empirical.md`, explicitly document:

```text
- lambda_hat=(n-1)/W is conditioned on active span;
- silence before the first and after the last packet is excluded;
- t=0 is a normalized observed arrival;
- expected direct tests cover exponential IAT behavior, mean rate, window
  completion, and joint empirical marks.
```

In `markov_renewal.md`, explicitly document:

```text
- when empty-row and global-IAT fallbacks apply;
- fallback use is retained in model/evaluation diagnostics;
- the fallback weakens state-conditional interpretation but remains a declared
  finite-sample rule;
- direct tests cover transitions, occupancy, conditional holding, fallback,
  completion, and joint marks.
```

In `mmpp.md`, add direct tests for arrival-state mixture, long-run mean rate,
time occupancy, serial dependence, completion, marks, exact threshold/RNG order,
and large finite normalization. State that finite GA fitting is not a
likelihood estimator or proof of the latent mechanism.

- [ ] **Step 5: Verify and commit**

Run:

```bash
rg -n 'arrival-epoch|event-stationary|lambda_bar|1/4, 3/4|Asanjarani' \
  architecture/traffic_models/mmpp.md
rg -n 'active span|silence|exponential|joint empirical' \
  architecture/traffic_models/poisson_empirical.md
rg -n 'fallback|diagnostic|conditional holding|occupancy' \
  architecture/traffic_models/markov_renewal.md
rg -n 'scientific semantics|schema version|predeclared' \
  architecture/traffic_models/README.md
awk 'length($0) > 120 { print FILENAME ":" FNR ":" length($0) }' \
  architecture/traffic_models/*.md
git diff --check -- architecture/traffic_models
git diff -- architecture/traffic_models
```

Commit:

```bash
git add architecture/traffic_models
git commit -m "docs: correct traffic model validation semantics"
```

### Task 4: Define similarity weights and neutral genetic competition

**Files:**

- Modify: `architecture/similarity_methods/README.md:1-55`
- Modify: `architecture/genetic_models/README.md`
- Modify: `architecture/genetic_models/basic_generational.md:1-325`

**Interfaces:**

- Consumes: mandatory method semantics, temporary family-priority RNG, and
  compatibility version from Tasks 1 and 3.
- Produces: exact aggregate, competition, checkpoint, and interpretation rules
  used by Testing and Roadmap.

- [ ] **Step 1: Establish semantic contradictions**

Run:

```bash
rg -n 'enabled method|lexical|smallest stable candidate ID|remainder' \
  architecture/similarity_methods/README.md \
  architecture/genetic_models/basic_generational.md
```

Expected: undefined enabled-method wording and/or lexical quota/tie behavior is
present.

- [ ] **Step 2: Make method-weight semantics exact**

In `similarity_methods/README.md`, replace `enabled methods` with `mandatory
methods` and specify:

```text
- all four methods always execute and retain diagnostics;
- all method-specific preconditions and settings always apply;
- zero weight contributes exactly zero to the aggregate and does nothing else;
- a zero-weight method failure still fails comparison or invalidates a genetic
  candidate;
- the fixed four-method similarity/checkpoint shapes remain unchanged.
```

State that the existing hand calculations plus one-hot/mixed/zero-weight tests
are sufficient for this semantics change; no duplicate metric implementation
is required.

- [ ] **Step 3: Define neutral family priority**

In `basic_generational.md`, define exactly:

```python
priority_rng = random.Random(master_seed)
family_priority = tuple(
    priority_rng.sample(sorted_family_names, len(sorted_family_names))
)
```

Clarify that this temporary RNG is discarded and the search RNG is initialized
separately, so the existing search draw stream is not consumed. Apply
`family_priority` to quota remainders, initial slots, and exact cross-family
ties. Keep stable candidate ID for ties within one family and lexical report
ordering only.

- [ ] **Step 4: Define checkpoint/resume and interpretation**

Add `family_priority` and the bumped scientific artifact schema version to
checkpoint compatibility/state. Resume requires exact equality before another
random draw or child. Add deterministic examples for:

- config/registry input-order invariance;
- seeds that give each family each priority position;
- equal-fitness and symmetric-invalid candidates;
- equal budgets and controlled known winner;
- resumed versus uninterrupted priority, children, history, winner, and RNG.

In both genetic documents, state that finite population, generations, bounds,
operators, and seeds affect the result. Scores do not establish likelihood,
causal mechanism, universal family superiority, or unseen-program
generalization.

- [ ] **Step 5: Verify and commit**

Run:

```bash
rg -n 'mandatory|zero.*aggregate|still.*fail|four-method' \
  architecture/similarity_methods/README.md
rg -n 'family_priority|random.Random\(master_seed\)|sample\(|cross-family|within one family|scientific.*version' \
  architecture/genetic_models/basic_generational.md
! rg -n 'lexical family order.*tie|smallest stable candidate ID.*cross-family' \
  architecture/genetic_models/basic_generational.md
awk 'length($0) > 120 { print FILENAME ":" FNR ":" length($0) }' \
  architecture/similarity_methods/*.md architecture/genetic_models/*.md
git diff --check -- architecture/similarity_methods architecture/genetic_models
git diff -- architecture/similarity_methods architecture/genetic_models
```

Commit:

```bash
git add architecture/similarity_methods architecture/genetic_models
git commit -m "docs: define neutral model competition"
```

### Task 5: Define the evidence and verification strategy

**Files:**

- Modify: `architecture/TESTING.md:1-380`

**Interfaces:**

- Consumes: all contracts from Tasks 1--4.
- Produces: precise evidence gates and bounded commands referenced by every
  reopened Roadmap phase.

- [ ] **Step 1: Establish missing evidence categories**

Run:

```bash
for term in 'arrival-epoch' 'family priority' 'portable configuration' \
  'accepted evidence bundle' 'offline audit' 'held-out reference'; do
  rg -q "$term" architecture/TESTING.md || printf 'missing: %s\n' "$term"
done
```

Expected before editing: multiple missing categories.

- [ ] **Step 2: Add bounded scientific-validation requirements**

Under `Unit tests`, add a matrix with predeclared fixed seeds, sample sizes, and
tolerances. Require test-only analytical calculations for:

```text
Poisson: exponential IAT, mean rate, completion, joint marks
Markov Renewal: transitions, occupancy, conditional holding, fallback, completion, joint marks
MMPP: arrival-epoch mix, long-run rate, time occupancy, serial dependence, completion, joint marks
GA: input-order invariance, priority balance, equal-fitness and invalid symmetry, known winner
Weights: one-hot/mixed arithmetic, zero-weight mandatory execution and failure
```

State that deterministic fixture regeneration and production round trips do not
alone satisfy this matrix. Do not require another full similarity
implementation or a generic statistical framework.

- [ ] **Step 3: Add configuration and environment evidence**

Under `In-process integration tests`, require:

- source/realized round trip for `1/0/0/0` and mixed weights;
- one fresh compatible clean-clone transfer with identical scientific values
  and only enumerated absolute-path differences;
- one declared incompatibility rejected before publication;
- image/runtime compatibility checked before stage reuse;
- old scientific artifact schema rejected before resume/generate/reuse.

Use the exact compatibility matrix from `SYSTEM.md`, `CAPTURE.md`, and
`DEVELOPMENT.md`. Positive cases may vary only Docker/Compose patch versions,
kernel release, and permitted absolute paths after feature preflight. Negative
cases independently change host architecture, target ID, expected/resolved
capture ID, capture-tool version, mounted-input hash, scientific configuration,
and scientific schema. Require the first mismatch in the diagnostic.

This is one transfer proof, not the multi-platform matrix reserved for
Excellent.

- [ ] **Step 4: Add diagnostics and full-pipeline equivalence**

Require a table-driven injected matrix for every declared Task 1 failure class.
Assert exact kind, stage, affected evidence/state, authority, action, status,
and nonpublication. Require a small checked credential-free JSONL diagnostic
fixture and offline parsing.

Add one uninterrupted-versus-resumed offline pipeline test through fit,
generate, compare, and final publication. Compare all deterministic scientific
values, canonical bytes, hashes, and lineage. List wall-clock/log/external
observations as the only permitted differences. Require validated stage reuse
to yield the same authoritative lineage.

- [ ] **Step 5: Add accepted-study and held-out evidence gates**

Add an `Accepted Validation Study evidence` subsection requiring:

- same-revision Docker and Internet prerequisites before replacement study;
- complete training run trees and portable/realized configs;
- one predeclared independent held-out reference per workload;
- training-only fit/selection, then fixed fitted model over held-out `W` with no
  refit/reselection;
- separate training, natural-variation, fresh-simulation, and held-out report
  claims;
- canonical manifest of every retained path, size, and SHA-256;
- checked accepted bundle and no dependency on ignored/local-only bytes.

- [ ] **Step 6: Add the bounded offline-audit contract**

Require one no-Docker/no-Internet command to:

- validate exact inventory and hashes;
- parse every retained format;
- recompute normalized references, checkpoint/history/winner consistency,
  generated traces, all component/aggregate scores, variation, held-out
  summaries, and report arithmetic;
- validate every lineage relationship;
- reject representative missing, corrupt, foreign, and substituted evidence.

Existing per-format tests retain responsibility for exhaustive corruption
matrices. The offline command must not call `trafficlab run` or fetch missing
bytes.

- [ ] **Step 7: Verify and commit**

Run:

```bash
for term in 'arrival-epoch' 'family priority' 'portable configuration' \
  'accepted evidence bundle' 'offline audit' 'held-out reference' \
  'fresh simulation seed'; do
  rg -q "$term" architecture/TESTING.md || exit 1
done
awk 'length($0) > 120 { print FNR ":" length($0) }' architecture/TESTING.md
git diff --check -- architecture/TESTING.md
git diff -- architecture/TESTING.md
```

Commit:

```bash
git add architecture/TESTING.md
git commit -m "docs: define minimum research evidence gates"
```

### Task 6: Reevaluate and restructure the MVP Roadmap

**Files:**

- Modify: `architecture/ROADMAP.md:1-390`
- Modify: `architecture/README.md:1-85`

**Interfaces:**

- Consumes: all owning contracts and evidence gates from Tasks 1--5.
- Produces: truthful current status, exact implementation sequence, and final
  criterion reassessment gate.

- [ ] **Step 1: Preserve a pre-edit Roadmap inventory**

Run:

```bash
rg -n '^## Phase|^- \[[ x]\]|^\*\*Done when|^\*\*Verified|^\*\*Internet evidence' \
  architecture/ROADMAP.md
```

Expected: Phases 1--7 appear complete, including claims contradicted by the
assessment.

- [ ] **Step 2: Add an explicit reevaluation policy and traceability table**

Near the top of `ROADMAP.md`, state:

```text
- the 2026-08-14 assessment reopened owning requirements;
- a checked historical implementation item remains checked only when its
  behavior is still valid;
- corrected or unverified claims are unchecked;
- dated evidence remains history and does not satisfy amended gates;
- the earliest reopened phase is Current;
- assessment grades do not change until Phase 8 evidence and reassessment.
```

Add a 17-row table mapping the exact criteria and current grades from the design
to Phases 1--8. Do not list any already Acceptable criterion as reopened.

- [ ] **Step 3: Reopen Phases 1--3 narrowly**

Phase 1:

- keep all existing implementation boxes checked;
- add unchecked portable/realized configuration and accepted-evidence tracking
  requirements/tests;
- add unchecked exact zero-weight configuration semantics/round-trip evidence;
- mark Phase 1 `Current` and extend Done-when with those tasks.

Phase 2:

- keep four method implementations and existing hand tests checked;
- add unchecked mandatory-all-four/zero-weight semantics and direct tests;
- extend Done-when without requiring independent reimplementations.

Phase 3:

- keep lifecycle and historical Docker/Internet behavior checked;
- add unchecked digest/snapshot/package/environment compatibility and diagnostic
  evidence;
- rewrite the dated Internet note as historical only and remove its claim that
  the old ten-run study satisfies amended evidence;
- extend Done-when with reproducible capture-image identity.

- [ ] **Step 4: Reopen Phases 4--6 at contradicted requirements**

Phase 4:

- uncheck and rewrite MMPP stationary initialization as arrival-epoch;
- uncheck affected model-generation and all-family scientific validation tests;
- add unchecked scientific artifact schema/version rejection;
- extend Done-when with direct bounded validation for all families.

Phase 5:

- uncheck quota/tie fairness and checkpoint compatibility items;
- uncheck affected fairness and resume tests;
- add exact family-priority requirements and full-pipeline resumed-equivalence
  ownership;
- extend Done-when with neutral-order evidence.

Phase 6:

- uncheck output reuse/corruption claims affected by semantics version;
- add unchecked canonical diagnostic matrix and final-artifact equivalence;
- rewrite the Verified note so it records historical behavior but withdraws the
  claim that old Phase 7 files supply complete study evidence;
- extend Done-when with compatible reuse, lineage, and failure evidence.

- [ ] **Step 5: Reopen Phase 7 and add Phase 8**

In Phase 7:

- keep only approved workload definitions checked;
- uncheck repeated captures, all-family results, trace interpretation, report
  publication, prerequisite Internet run, saved-config reproduction, and final
  seed confirmation for the replacement study;
- add unchecked independent held-out references and fixed-model evaluation;
- add unchecked complete checked evidence bundle/environment manifest;
- add unchecked clean-clone offline audit and report reconstruction;
- replace `held-out seed` language with `fresh simulation seed` and a separate
  genuine held-out-reference requirement;
- label the 2026-08-14 study record `Historical evidence` and state that its
  missing run trees make it insufficient for the amended gate;
- replace Done-when with retained corrected-semantics study, held-out evidence,
  and successful offline reconstruction.

Add `Phase 8 -- Minimum research fitness acceptance` with unchecked tasks for:

```text
all reopened phase gates
bounded analytical scientific-validation matrix
portable compatible-environment transfer
capture environment reproducibility
fresh/resumed full-pipeline equivalence
canonical adverse-condition diagnostics
checked accepted evidence and offline reconstruction
same-revision available Docker and Internet prerequisites
independent review with zero Critical/Important
unchanged-rubric reassessment of all 17 criteria to Acceptable or better
```

Phase 8 adds no model, metric, or production service.

- [ ] **Step 6: Update architecture navigation**

In `architecture/README.md`:

- state Phase 1 is Current because it is the earliest reopened owner;
- link the assessment, criteria, approved design, and Roadmap;
- summarize that the remediation targets scientific correctness,
  configurability, robustness, and reproducibility without enterprise scope;
- preserve the existing MVP workflow and scope boundaries.

- [ ] **Step 7: Verify exact Roadmap truthfulness and commit**

Run:

```bash
rg -n '^## Phase [1-8]|Current|Historical evidence|Research fitness|arrival-epoch|held-out reference|offline audit' \
  architecture/ROADMAP.md architecture/README.md
for c in 1.5 1.7 2.1 2.2 2.4 2.6 3.3 3.4 3.8 3.9 4.7 5.2 5.3 5.4 5.6 5.7 5.8; do
  rg -Fq "| $c |" architecture/ROADMAP.md || exit 1
done
! rg -n 'ignored.*retain.*raw audits|fresh held-out seed' architecture/ROADMAP.md
awk 'length($0) > 120 { print FILENAME ":" FNR ":" length($0) }' \
  architecture/ROADMAP.md architecture/README.md
git diff --check -- architecture/ROADMAP.md architecture/README.md
git diff -- architecture/ROADMAP.md architecture/README.md
```

Manually verify:

- unaffected Acceptable work remains checked;
- every corrected or unverified requirement is unchecked;
- no phase with open work claims current completion;
- every one of the 17 criteria has an owning phase and final Phase 8 gate;
- the assessment itself is unchanged.

Commit:

```bash
git add architecture/ROADMAP.md architecture/README.md
git commit -m "docs: reopen minimum research fitness roadmap"
```

### Task 7: Run the architecture consistency and review gate

**Files:**

- Verify only: all files listed in the File map
- Must remain unchanged: `docs/RESEARCH_FITNESS_ASSESSMENT.md`
- Must remain unchanged: `architecture/RESEARCH_FITNESS_CRITERIA.md`
- Must remain unchanged: production, tests, fixtures, configs, and dependency files

**Interfaces:**

- Consumes: committed Tasks 1--6.
- Produces: reviewed, internally consistent architecture ready for a separate
  implementation plan for reopened Roadmap work.

- [ ] **Step 1: Verify scope and immutable evidence documents**

Run:

```bash
plan_path=docs/superpowers/plans/2026-08-14-research-fitness-remediation.md
implementation_base=$(git log -1 --format=%H -- "$plan_path")
test -n "$implementation_base"
git diff "$implementation_base"..HEAD --name-only
git diff --exit-code "$implementation_base"..HEAD -- \
  docs/RESEARCH_FITNESS_ASSESSMENT.md \
  architecture/RESEARCH_FITNESS_CRITERIA.md \
  src tests docker examples pyproject.toml uv.lock
```

Expected: the name list contains only declared architecture files; the second
command exits zero.

- [ ] **Step 2: Run terminology and contradiction scans**

Run:

```bash
rg -n -e 'arrival-epoch initialization' -e 'mandatory similarity method' \
  -e 'fresh simulation seed' -e 'held-out reference' \
  -e 'portable configuration' -e 'realized configuration' \
  -e 'accepted evidence bundle' -e 'offline audit' \
  architecture
! rg -n -e 'fresh held-out seed' -e 'ignored.*retain.*raw audits' \
  -e 'every enabled similarity method' \
  -e 'lexical order of enabled family names' \
  architecture
```

Expected: every required term has an owning definition and no obsolete claim
matches.

- [ ] **Step 3: Run documentation verification**

Run:

```bash
uv sync --locked --all-groups
uv lock --check
uv run --locked ruff format --check architecture
uv run --locked ruff check .
uv run --locked pyright
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_readme.py
git diff --check
```

Expected: every command passes. Do not run Docker or Internet tests for this
documentation-only change.

- [ ] **Step 4: Request independent architecture review**

Give the reviewer:

```text
Base: the exact commit printed by:
git log -1 --format=%H -- docs/superpowers/plans/2026-08-14-research-fitness-remediation.md
Head: current Task 6 commit
Authority: TASK.md, AGENTS.md, assessment, criteria, approved design
Review axes:
- all 17 deficient criteria have minimal Acceptable remedies;
- Roadmap reopenings are truthful and unaffected Acceptable work is preserved;
- scientific semantics are correct;
- held-out and reconstruction requirements satisfy, but do not exceed, their
  Acceptable anchors unnecessarily;
- no implementation interface, service, security feature, or enterprise scope
  was invented;
- no contradictory terminology or evidence claim remains.
```

Require zero Critical or Important findings. Apply any correction with
`superpowers:receiving-code-review`, rerun the affected checks, and make one
narrow corrective documentation commit.

- [ ] **Step 5: Record final clean state**

Run:

```bash
plan_path=docs/superpowers/plans/2026-08-14-research-fitness-remediation.md
implementation_base=$(git log -1 --format=%H -- "$plan_path")
git log --oneline "$implementation_base"..HEAD
git status --short --branch
```

Expected: coherent documentation commits and a clean tree. Do not mark any
reopened Roadmap implementation box complete; this plan defines requirements
and implementation order only.
