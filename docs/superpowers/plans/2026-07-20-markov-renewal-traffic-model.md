# Markov Renewal Traffic Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the selectable Layer 2 `markov_renewal` model architecture and its required immutable-registry amendment.

**Architecture:** One new traffic-model owner defines all Markov Renewal behavior: joint IAT/frame-size state building, empirical emissions, first-order transitions, deterministic model files, and validation. A separate amendment makes the model selectable without editing the original traffic-model registry.

**Tech Stack:** Markdown, TOML examples, Python 3.12 `tomllib` for static document-example validation, Git.

## Global Constraints

- Create exactly `architecture/traffic_models/markov_renewal/README.md` and `architecture/amendments/001_markov_renewal_registry.md` as authoritative architecture additions.
- Do not modify any original architecture document, configuration template, configuration reference, source code, dependency, test file, roadmap, or draft reference.
- The selectable model name is exactly `markov_renewal`.
- The model is Layer 2 only: timestamps and original Ethernet frame lengths; no addresses, payload, VLAN, raw 802.11/Radiotap, IP, TCP, UDP, higher protocol, flow, direction, or session behavior.
- Derive observations only from frames `1..N-1` as `(timestamp_i - timestamp_(i-1), original_frame_length_i)`; require at least two frames.
- Preserve zero IAT; reject negative, missing, non-finite, or nonrepresentable IAT and invalid original Ethernet length.
- Provide non-trainable `automatic` and `manual` state modes; automatic submodes are `quantile`, `exact`, and `cluster`.
- Quantile bucket counts and cluster count are the only trainable parameters; state modes, manual ranges, seed, stop controls, and learned data are non-trainable.
- Emit stored observed IAT/frame-size pairs by frequency, start from state-occurrence frequencies, use observed first-order transition weights, and restart from start-state weights at a terminal state.
- Use deterministic canonical state IDs, row ordering, fitting, serialization, hashes, implementation lineage, and atomic publication.
- Use `packet_count` or `duration` stopping, never both; first output frame occurs after its sampled IAT from time zero.
- Validate strictly and never skip, clamp, repair, invent, or partially apply invalid input.
- Future tests are deterministic, local, offline, unprivileged, and may use fake child applications only.

---

### Task 1: Add the immutable-registry amendment

**Files:**
- Create: `architecture/amendments/001_markov_renewal_registry.md`

**Interfaces:**
- Consumes: `AGENTS.md`, `architecture/README.md`, and `architecture/traffic_models/README.md`.
- Produces: an authoritative addition declaring `markov_renewal` selectable while leaving the original model table unchanged; Task 2 supplies its owner document.

- [ ] **Step 1: Create the amendment header and conflicting-source record**

  Create the amendment with this identity block:

  ```markdown
  # Amendment 001: Markov Renewal Traffic-Model Registry

  **Status:** Accepted

  **Owner:** Trafficlab architecture

  **Date:** 2026-07-20
  ```

  Add a `## Conflicting Sources` section linking:

  - `../../AGENTS.md`, which makes original architecture documents immutable;
  - `../README.md`, whose change discipline otherwise requires affected
    references to be updated; and
  - `../traffic_models/README.md`, whose selectable-model table is an original
    registry that cannot receive a new row.

  State the conflict plainly: a selectable model needs registry visibility, but
  modifying that original table violates the immutability rule.

- [ ] **Step 2: Record the decision, rationale, scope, and consequences**

  Add a `## Decision` section that declares `markov_renewal` selectable and
  links `../traffic_models/markov_renewal/README.md` as its owner. State that
  this amendment is an authoritative addition to the original registry table;
  it does not rewrite, remove, or reinterpret any existing model.

  Add `## Rationale` explaining that this preserves immutable source documents
  while making the new model discoverable through the architecture corpus.

  Add `## Scope and Consequences` stating that readers and applications treat
  `markov_renewal` exactly like a listed selectable traffic model, read its
  owner before use, and leave every previous selectable model unchanged. The
  original table remains historical and non-exhaustive until its governing
  immutability rule changes through a future amendment.

- [ ] **Step 3: Record alternatives and compatibility**

  Add `## Alternatives` with both rejected alternatives:

  - editing `traffic_models/README.md`, rejected because it violates
    immutability; and
  - adding an unregistered directory, rejected because it hides a selectable
    public model from the registry.

  Add `## Compatibility and Migration` stating that this is additive, changes
  no existing name, model-file schema, configuration, generated artifact, or
  migration path, and therefore requires no migration.

  End with `## Reading`, linking architecture governance, the traffic-model
  registry, and the `markov_renewal` owner.

- [ ] **Step 4: Validate and commit Task 1**

  Run:

  ```bash
  git diff --check
  rg -n -- 'Status|Owner|Date|Conflicting Sources|Decision|Rationale|Scope and Consequences|Alternatives|Compatibility and Migration|markov_renewal|immutable|unregistered' architecture/amendments/001_markov_renewal_registry.md
  python3 -c 'import pathlib,re; p=pathlib.Path("architecture/amendments/001_markov_renewal_registry.md"); future="../traffic_models/markov_renewal/README.md"; links=[x.split("#",1)[0] for x in re.findall(r"\]\(([^)]+)\)",p.read_text()) if not x.startswith(("http:","https:"))]; assert future in links,links; missing=[x for x in links if x != future and not (p.parent/x).resolve().exists()]; assert not missing,missing; print(f"existing_relative_links={len(links)-1} valid future_owner_link=recorded")'
  ```

  Expected: every amendment requirement is present, every existing link
  resolves, the recorded future owner link has the exact expected path, and no
  original architecture file changes. Task 3 verifies that this link resolves
  after Task 2 creates the owner.

  Commit:

  ```bash
  git add architecture/amendments/001_markov_renewal_registry.md
  git commit -m "docs(architecture): register Markov Renewal model"
  ```

### Task 2: Add the Markov Renewal model owner

**Files:**
- Create: `architecture/traffic_models/markov_renewal/README.md`

**Interfaces:**
- Consumes: the registry amendment from Task 1, 30/40/50 application boundaries, existing Layer 2 frame-length rules, and the approved design in `docs/superpowers/specs/2026-07-20-markov-renewal-traffic-model-design.md`.
- Produces: the complete schema, state preparation, generation, validation, determinism, and test contract selected by model name `markov_renewal`.

- [ ] **Step 1: Define role, boundaries, and reference observations**

  Create the owner with title `Markov Renewal Traffic Model`. State that
  `markov_renewal` is selectable under Amendment 001 and models only a
  first-order sequence of joint IAT/original-Ethernet-length observations.

  Link, using paths relative to the new file:

  ```text
  ../../README.md
  ../README.md
  ../../amendments/001_markov_renewal_registry.md
  ../../apps/30_genetic_training/README.md
  ../../apps/40_model_creation/README.md
  ../../apps/50_traffic_generation/README.md
  ```

  Give 30 ownership of one-reference preparation and subprocess orchestration,
  40 ownership of the normal file, and 50 ownership of PCAPNG creation. Define
  the L2-only exclusions exactly as in the global constraints.

  Define reference observations exactly:

  ```text
  IAT_i = timestamp_i - timestamp_(i - 1)
  observation_i = (IAT_i, original_frame_length_i)
  ```

  Use only frames `1..N-1`. Require two frames, finite IAT seconds representable
  in deterministic TOML serialization, zero-IAT acceptance, negative-IAT
  rejection, Ethernet-form links, original lengths 60–1514 inclusive, and no
  skipped, clamped, or rewritten observation.

- [ ] **Step 2: Define all state modes and their trainable fields**

  Define non-trainable `state.mode = "automatic" | "manual"`, and, for
  automatic mode only, non-trainable
  `state.automatic_submode = "quantile" | "exact" | "cluster"`. Require
  same-model crossover to use candidates with identical non-trainable mode
  settings.

  For `quantile`, define positive integer trainable
  `automatic.quantile.iat_bucket_count` and
  `automatic.quantile.frame_size_bucket_count`. Each must be no greater than
  the corresponding distinct observed-value count, produce exactly that many
  nonempty ordered categories, and never split tied values. Only observed joint
  category pairs are states; an impossible count fails preparation.

  For `exact`, define one state per exact canonical
  `(IAT_seconds, frame_size_bytes)` pair and no trainable state-builder size.

  For `cluster`, define positive integer trainable
  `automatic.cluster.cluster_count`, bounded by distinct observed feature
  vectors. Define ordinary k-means features:

  ```text
  (normalized log1p(IAT_seconds), normalized frame_size_bytes)
  ```

  A zero-variance feature becomes zero after normalization. Use model-seed
  initialization and canonical cluster ordering by ascending centroid with
  smallest-member-observation tie-break. Prefer a maintained k-means library,
  record its version, and require direct comparison tests if handwritten.

  For `manual`, define named IAT and frame-size ranges with `minimum <= value <
  maximum`. IAT limits are finite nonnegative seconds. Frame-size minima are
  60–1514 and exclusive maxima 61–1515. Require positive width, unique IDs,
  and exact one-range coverage for every reference value; gaps, overlaps, and
  unassigned observations fail. Manual ranges are non-trainable.

- [ ] **Step 3: Define learned tables, generation, and stopping**

  Require 30 to append learned data after successful reference preparation.
  Include this exact TOML shape:

  ```toml
  [learned]
  reference_sha256 = "..."
  state_count = 2

  [[learned.states]]
  id = 0
  start_weight = 12

  [[learned.states.emissions]]
  iat_seconds = 0.002
  frame_size_bytes = 128
  weight = 3

  [[learned.states.transitions]]
  to_state = 1
  weight = 7
  ```

  Define IDs as contiguous integers `0..state_count-1` in canonical state
  order. Each state has positive `start_weight`, at least one frequency-weighted
  emission, and zero or more positive-weight transitions to known state IDs.
  Emission IAT is finite and nonnegative; emission length is 60–1514. Reference
  hash and all learned rows are content-hashed and canonical.

  Define generation exactly:

  1. Choose a state by `start_weight`.
  2. Choose an emission by that state’s emission weight.
  3. Emit the observed pair and advance timestamp by its IAT.
  4. Select the next state by the current state’s transition weights and repeat.
  5. If the current state has no outgoing transition, restart at step 1.

  State that this never smooths or invents a transition, zero IAT yields
  nondecreasing timestamps, and only the current state controls the next state.

  Define exactly one stop mode: positive integer `packet_count`, or finite
  positive `duration_seconds`. The first frame occurs after its sampled IAT;
  duration emits only while cumulative timestamp is at most the bound.

- [ ] **Step 4: Define normal files, validation, lineage, and future tests**

  Include the normal automatic quantile file:

  ```toml
  model = "markov_renewal"
  schema_version = 1
  seed = 0

  [state]
  mode = "automatic"
  automatic_submode = "quantile"

  [automatic.quantile]
  iat_bucket_count = 4
  frame_size_bucket_count = 4

  [stop]
  mode = "packet_count"
  packet_count = 1000
  ```

  Include this cluster fragment:

  ```toml
  [automatic.cluster]
  cluster_count = 4
  ```

  Include this manual fragment:

  ```toml
  [state]
  mode = "manual"

  [[manual.iat_ranges]]
  id = "short"
  minimum = 0.0
  maximum = 0.01

  [[manual.frame_size_ranges]]
  id = "small"
  minimum = 60
  maximum = 512
  ```

  State that exact mode has neither automatic subtable and manual mode has no
  automatic table.

  Reject unknown keys, unsupported version, invalid type, incompatible mode
  tables, invalid seed/stop controls, invalid counts/ranges, ambiguous or
  unassigned observations, empty states/emissions, invalid IDs/weights,
  noncanonical ordering, bad transition endpoints, missing learned data after
  preparation, malformed reference lineage, and hash mismatch. Validate before
  50 publishes; never partially apply input.

  Require deterministic model-file serialization, validation and hash before
  atomic publication, and lineage containing reference/model hashes, mode and
  settings, learned-state count, generator identity, and cluster-library
  identity when applicable. State that mapping iteration, parser completion,
  and child completion cannot affect results.

  List deterministic unprivileged future tests for every extraction boundary,
  all four state builders, emission/transition/start weights, dead-end restart,
  zero-IAT timestamps, both stop modes, normal and learned schema, hashes,
  atomic output, and fake 40/50 orchestration. Forbid real network, live
  capture, elevation, and privileged scripts.

- [ ] **Step 5: Validate and commit Task 2**

  Run:

  ```bash
  git diff --check
  python3 -c 'import pathlib,re,tomllib; p=pathlib.Path("architecture/traffic_models/markov_renewal/README.md"); blocks=re.findall(r"^```toml\n(.*?)^```$",p.read_text(),re.M|re.S); assert len(blocks)==4,len(blocks); [tomllib.loads(b) for b in blocks]; print("toml_blocks=4 valid")'
  python3 -c 'import pathlib,re; p=pathlib.Path("architecture/traffic_models/markov_renewal/README.md"); links=[x.split("#",1)[0] for x in re.findall(r"\]\(([^)]+)\)",p.read_text()) if not x.startswith(("http:","https:"))]; missing=[x for x in links if not (p.parent/x).resolve().exists()]; assert not missing,missing; print(f"relative_links={len(links)} valid")'
  rg -n -- 'markov_renewal|observation_i|zero IAT|quantile|exact|cluster|manual|start_weight|transitions|packet_count|duration|hash|elevated' architecture/traffic_models/markov_renewal/README.md
  ```

  Expected: every required model mode, schema boundary, generation rule,
  failure rule, lineage rule, and test boundary is present; all four TOML blocks
  parse and every relative owner link resolves.

  Commit:

  ```bash
  git add architecture/traffic_models/markov_renewal/README.md
  git commit -m "docs(architecture): add Markov Renewal model"
  ```

### Task 3: Verify the complete Markov Renewal architecture

**Files:**
- Verify: `architecture/amendments/001_markov_renewal_registry.md`
- Verify: `architecture/traffic_models/markov_renewal/README.md`
- Verify unchanged: `architecture/README.md`
- Verify unchanged: `architecture/traffic_models/README.md`

**Interfaces:**
- Consumes: both new authoritative documents.
- Produces: evidence that the model is discoverable under immutable-registry
  governance, self-contained, deterministic, Layer 2 only, and unprivileged.

- [ ] **Step 1: Run final static and ownership checks**

  Run after the two task commits:

  ```bash
  git diff --check HEAD~2..HEAD
  git status --short
  git diff --name-only HEAD~2..HEAD -- architecture
  rg -n -- 'markov_renewal' architecture/amendments architecture/traffic_models/markov_renewal
  rg -n -- 'original Ethernet|captured byte|raw IEEE 802\.11|Radiotap|IP|TCP|UDP|payload|flow|direction|session' architecture/traffic_models/markov_renewal/README.md
  rg -n -- 'sudo|root privilege|elevated privilege|live capture|network access' architecture/traffic_models/markov_renewal/README.md
  ```

  Expected: whitespace checks succeed; the worktree is clean; the architecture
  diff names only the new amendment and new model owner; the amendment and
  owner use the exact selectable name; protocol scope remains Layer 2 only; and
  tests and model behavior require no live network, capture, or elevation.

- [ ] **Step 2: Verify registry immutability and document examples**

  Run:

  ```bash
  git diff --exit-code HEAD~2..HEAD -- architecture/README.md architecture/traffic_models/README.md
  python3 -c 'import pathlib,re,tomllib; docs=[pathlib.Path("architecture/amendments/001_markov_renewal_registry.md"), pathlib.Path("architecture/traffic_models/markov_renewal/README.md")]; missing=[]; [(missing.extend((str(p),x) for x in re.findall(r"\]\(([^)]+)\)",p.read_text()) if not x.startswith(("http:","https:")) and not (p.parent/x.split("#",1)[0]).resolve().exists())) for p in docs]; assert not missing,missing; blocks=re.findall(r"^```toml\n(.*?)^```$",docs[1].read_text(),re.M|re.S); assert len(blocks)==4,len(blocks); [tomllib.loads(b) for b in blocks]; print("original_registry=unchanged relative_links=valid toml_blocks=4_valid")'
  ```

  Expected: original registry files have no diff; every relative link resolves;
  and all model-file TOML examples parse.

- [ ] **Step 3: Report completion**

  Report both implementation commit IDs and final verification evidence. State
  explicitly that the amendment extends the immutable registry, the new owner
  is Layer 2 only, and no code, dependency, configuration template, generated
  traffic, network action, capture action, or elevated action was added.
