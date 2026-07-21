# Basic Poisson Traffic Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add authoritative architecture documents for the `poisson_uniform` and `poisson_empirical` Layer 2 traffic models.

**Architecture:** A non-selectable Poisson-family owner defines shared arrival timing, stopping, randomness, and L2 length semantics once. Two selectable model owners reference it and define independent packet-size distributions, TOML schemas, defaults, validation, and training behavior.

**Tech Stack:** Markdown, TOML examples, Git.

## Global Constraints

- Both models produce only timestamp and original L2 frame-length sequences.
- Inter-arrival times are independent exponential samples from one finite positive `arrival_rate_pps`.
- The first frame occurs after the first sampled IAT, not automatically at time zero.
- Exactly one stopping mode is valid: positive `packet_count` or positive `duration_seconds`.
- Original Ethernet frame length includes the Ethernet header, excludes FCS, and is restricted to 60–1514 bytes.
- Reference extraction uses PCAPNG original packet length, never captured byte count.
- Seed and stop settings are generation controls, not trainable parameters.
- No implementation, random sampler, PCAPNG parser, or Ethernet-frame generator is created.

---

### Task 1: Define shared Poisson-family behavior and registry entries

**Files:**
- Create: `architecture/traffic_models/POISSON.md`
- Modify: `architecture/traffic_models/README.md`

**Interfaces:**
- Consumes: the common behavior approved in `docs/superpowers/specs/2026-07-20-basic-poisson-traffic-models-design.md`.
- Produces: one authoritative owner referenced by both selectable models.

- [ ] **Step 1: Create the non-selectable Poisson-family owner**

  In `architecture/traffic_models/POISSON.md`, state explicitly that this is a
  shared family document, not a selectable model name. Define:

  ```text
  IAT_i ~ Exponential(arrival_rate_pps)
  timestamp_i = timestamp_(i-1) + IAT_i
  ```

  Require finite positive rate, independent IAT and size samples, first arrival
  after a sampled IAT, and monotonically increasing timestamps. Define exactly
  one stopping mode and the count/duration boundary behavior. Define seed and
  generator implementation version lineage for repeatability.

- [ ] **Step 2: Define the common Layer 2 size boundary**

  In the same document, define original untagged Ethernet length including the
  header and excluding FCS, with inclusive bounds 60–1514. State that reference
  extraction uses PCAPNG original length even when captured bytes are truncated.
  Exclude VLAN overhead, jumbo frames, non-Ethernet links, invalid original
  lengths, protocols, addresses, payload meaning, directions, flows, and
  sessions. Link to `50_traffic_generation` as the owner of frame construction.

- [ ] **Step 3: Add both selectable models to the registry**

  Add a table to `architecture/traffic_models/README.md` linking
  `poisson_uniform` and `poisson_empirical` to their owner documents. Link the
  shared Poisson-family rules separately and state that the family document is
  not selectable. Do not duplicate equations or validation rules in the index.

- [ ] **Step 4: Validate and commit Task 1**

  Run:

  ```bash
  git diff --check
  rg -n -- 'Exponential|packet_count|duration_seconds|original recorded length|60.*1514|not a selectable' architecture/traffic_models/POISSON.md architecture/traffic_models/README.md
  ```

  Expected: all shared rules occur in `POISSON.md`; the registry links both
  models and labels the family owner as non-selectable.

  Commit:

  ```bash
  git add architecture/traffic_models/POISSON.md architecture/traffic_models/README.md
  git commit -m "docs(architecture): define Poisson traffic family"
  ```

### Task 2: Add the `poisson_uniform` model owner

**Files:**
- Create: `architecture/traffic_models/poisson_uniform/README.md`

**Interfaces:**
- Consumes: shared behavior from `architecture/traffic_models/POISSON.md`.
- Produces: the exact schema and behavior selected by model name `poisson_uniform`.

- [ ] **Step 1: Define the size distribution and trainable parameters**

  State:

  ```text
  frame_size_bytes ~ DiscreteUniform(minimum_bytes, maximum_bytes)
  ```

  Require independent inclusive integer sampling. Define
  `arrival_rate_pps`, `minimum_bytes`, and `maximum_bytes` as trainable. Require
  `60 <= minimum_bytes <= maximum_bytes <= 1514`. Reference the shared family
  owner for timing, stopping, seed, length meaning, and output boundaries.

- [ ] **Step 2: Include the exact normal model file**

  Include the approved `poisson_uniform.toml` example with:

  ```toml
  model = "poisson_uniform"
  schema_version = 1
  arrival_rate_pps = 10.0
  seed = 0

  [packet_size]
  minimum_bytes = 60
  maximum_bytes = 1514

  [stop]
  mode = "packet_count"
  packet_count = 1000
  ```

  State that `40_model_creation` creates this normal file and
  `30_genetic_training` may change only the trainable values.

- [ ] **Step 3: Define validation and future tests**

  Reject unknown keys, unsupported schema versions, invalid types, non-finite
  rate, invalid bounds, and invalid shared stopping controls before generation.
  Require deterministic unprivileged tests for fixed-seed repeatability,
  inclusive bounds, the one-value range, both stop modes, first-arrival timing,
  monotonically increasing timestamps, and invalid files.

- [ ] **Step 4: Validate and commit Task 2**

  Run:

  ```bash
  git diff --check
  rg -n -- 'DiscreteUniform|arrival_rate_pps|minimum_bytes|maximum_bytes|packet_count = 1000|poisson\.md' architecture/traffic_models/poisson_uniform/README.md
  ```

  Expected: the exact model identity, schema, defaults, trainable fields, and
  common-owner link are present without redefining shared equations.

  Commit:

  ```bash
  git add architecture/traffic_models/poisson_uniform/README.md
  git commit -m "docs(architecture): add Poisson uniform model"
  ```

### Task 3: Add the `poisson_empirical` model owner

**Files:**
- Create: `architecture/traffic_models/poisson_empirical/README.md`

**Interfaces:**
- Consumes: shared behavior from `architecture/traffic_models/POISSON.md`.
- Produces: the exact schema and behavior selected by model name `poisson_empirical`.

- [ ] **Step 1: Define the empirical size distribution**

  Define a discrete categorical table of unique exact frame sizes and positive
  integer weights, stored in canonical ascending size order. Probabilities are
  normalized weights. Define `arrival_rate_pps` as trainable; define the
  extracted size-frequency table, seed, and stop controls as non-trainable.

- [ ] **Step 2: Define reference extraction and strict failure**

  State that `30_genetic_training` replaces the starting table with exact
  observed counts from its one reference Ethernet PCAPNG before generation.
  Use original packet lengths. Reject non-Ethernet inputs, missing or invalid
  original lengths, duplicate/noncanonical entries, nonpositive weights, and
  any frame outside 60–1514. Never exclude, clamp, or rewrite an observation.

- [ ] **Step 3: Include the exact normal model file**

  Include the approved `poisson_empirical.toml` example with:

  ```toml
  model = "poisson_empirical"
  schema_version = 1
  arrival_rate_pps = 10.0
  seed = 0

  [[packet_size.entries]]
  size_bytes = 60
  weight = 1

  [stop]
  mode = "packet_count"
  packet_count = 1000
  ```

  Explain that the one-entry table is a valid standalone starting table, not a
  claim about real traffic, and training replaces it before generation.

- [ ] **Step 4: Define validation and future tests**

  Require the shared schema checks plus exact extraction, canonical ordering,
  weighted sampling, fixed-seed repeatability, both stopping modes, original
  versus captured length, and strict unsupported-frame rejection. Tests are
  deterministic, unprivileged, and use fixture PCAPNG files only.

- [ ] **Step 5: Validate and commit Task 3**

  Run:

  ```bash
  git diff --check
  rg -n -- 'categorical|original packet length|size_bytes = 60|weight = 1|not genetically|poisson\.md' architecture/traffic_models/poisson_empirical/README.md
  ```

  Expected: the model owns its table schema and extraction behavior, references
  common Poisson rules, and never duplicates the arrival equation.

  Commit:

  ```bash
  git add architecture/traffic_models/poisson_empirical/README.md
  git commit -m "docs(architecture): add Poisson empirical model"
  ```

### Task 4: Verify the complete model architecture

**Files:**
- Verify: `architecture/traffic_models/README.md`
- Verify: `architecture/traffic_models/POISSON.md`
- Verify: `architecture/traffic_models/poisson_uniform/README.md`
- Verify: `architecture/traffic_models/poisson_empirical/README.md`

**Interfaces:**
- Consumes: all three owner documents and the registry.
- Produces: evidence that the two models are selectable, separate, complete,
  and consistent with the existing app boundaries.

- [ ] **Step 1: Run static checks**

  Run:

  ```bash
  git diff --check HEAD~3..HEAD
  git status --short
  rg -n -- 'poisson_uniform|poisson_empirical' architecture/traffic_models
  rg -n -- 'IAT_i.*Exponential' architecture/traffic_models
  rg -n -- 'captured byte count|original packet length|original recorded length' architecture/traffic_models
  ```

  Expected: a clean worktree; both model names appear in the registry and their
  owners; the arrival equation has one owner; every size reference uses original
  L2 length rather than truncated captured length.

- [ ] **Step 2: Report completion**

  Report the shared family owner, both selectable model owners, validation
  output, and commit IDs. State explicitly that no code, generated PCAPNG,
  mathematical implementation, or elevated operation was added.
