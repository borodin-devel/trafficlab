# Basic Similarity Methods Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add authoritative architecture documents for three basic Layer 2 KS similarity methods.

**Architecture:** A non-selectable KS-family owner defines the shared empirical-CDF distance, normalized similarity, PCAPNG boundary, p-value exclusion, and implementation policy once. Three selectable method owners reference it for IAT comparison, original frame-length comparison, and a configurable weighted combination; the result contract provides an extensible envelope for their auditable details.

**Tech Stack:** Markdown, TOML examples, Python 3.12 library guidance, Git.

## Global Constraints

- Add exactly three selectable methods: `iat_ks`, `frame_size_ks`, and `l2_ks_weighted`.
- Define `KS.md` as a shared family owner, not a selectable method.
- Use the two-sided empirical-CDF distance `D` and the natural similarity `1 - D`, both in `[0, 1]`.
- Do not use or publish KS p-values for similarity ranking.
- Allow unequal reference and generated sample counts; do not score total frame count.
- Support Ethernet-form Layer 2 traffic, including normal application traffic transported over Wi-Fi; exclude raw IEEE 802.11 and Radiotap monitor-mode captures.
- Use original recorded frame length, never captured or truncated byte count, for frame-size comparison.
- Prefer maintained, tested Python libraries when compatible; document and directly test any necessary handwritten mathematics.
- Do not force `[0, 1]` normalization on future methods when that transformation would be mathematically misleading.
- No similarity implementation, dependency change, PCAPNG parser, generated artifact, or privileged operation is added.

---

### Task 1: Define the registry, shared KS family, and result boundary

**Files:**
- Modify: `architecture/similarity_methods/README.md`
- Create: `architecture/similarity_methods/KS.md`
- Modify: `architecture/contracts/60_30_similarity_result/README.md`

**Interfaces:**
- Consumes: the approved design in `docs/superpowers/specs/2026-07-20-basic-similarity-methods-design.md` and the existing `60_similarity_evaluation` boundary.
- Produces: one shared KS owner, registry links for all three selectable methods, and a result envelope that permits method-owned diagnostic fields without owning their formulas.

- [ ] **Step 1: Add common score and implementation rules to the registry**

  In `architecture/similarity_methods/README.md`, add a score-convention
  section requiring every method to declare its range, direction, and meaning.
  Prefer `[0, 1]` with higher values meaning more similarity only when that is
  mathematically valid; state that genetic algorithms do not themselves
  require this range and forbid misleading normalization.

  Add an implementation-policy section that prefers established, maintained,
  tested Python libraries. Permit handwritten mathematics only when no library
  satisfies the documented behavior; require the owner to record why and to
  require direct correctness tests against authoritative examples or an
  independent implementation.

- [ ] **Step 2: Register the KS family and three selectable methods**

  Add a compact registry table linking these names to their owner documents:

  ```text
  iat_ks
  frame_size_ks
  l2_ks_weighted
  ```

  Link `KS.md` separately as shared behavior and explicitly state that `ks` is
  not a selectable method name. Do not duplicate formulas, feature extraction,
  weight rules, or validation details in the registry.

- [ ] **Step 3: Create the non-selectable KS-family owner**

  In `architecture/similarity_methods/KS.md`, define:

  ```text
  D = max_x(abs(F_reference(x) - F_generated(x)))
  similarity = 1 - D
  ```

  Define the two-sided statistic, natural `[0, 1]` range, higher-is-better
  direction, unequal-sample support, and absence of histogram bins. Exclude
  p-values from both ranking and published diagnostics and explain their
  sample-count and hypothesis-testing mismatch.

  Define the common Ethernet PCAPNG boundary, including ordinary Wi-Fi traffic
  exposed by Linux as Ethernet and excluding raw IEEE 802.11/Radiotap. Require
  strict validation of metadata needed by the selected method without silently
  skipping, sorting, clamping, repairing, or reinterpreting observations.

  Apply the registry's library policy to the KS statistic. Require result
  lineage to identify the method implementation and relevant library version.
  State the distribution-only limitations: no total-count, ordering, burst,
  joint timing-size, flow, address, or protocol comparison.

- [ ] **Step 4: Make method-specific result details explicit in the contract**

  In `architecture/contracts/60_30_similarity_result/README.md`, retain the
  contract's ownership of the `similarity.toml` envelope and its refusal to
  define one universal scale or formula. Clarify that every success contains
  one primary method-defined ranking result and may contain method-defined
  component distances, component scores, weights, and sample counts. Require
  the envelope to identify the selected method implementation and relevant
  mathematical-library version; all details remain covered by validation and
  the content hash.

- [ ] **Step 5: Validate and commit Task 1**

  Run:

  ```bash
  git diff --check
  rg -n -- '\[0, 1\]|misleading|Python librar|not a selectable|p-value|raw IEEE 802\.11|implementation.*version|primary.*ranking' architecture/similarity_methods/README.md architecture/similarity_methods/KS.md architecture/contracts/60_30_similarity_result/README.md
  ```

  Expected: common policy appears in the registry, all KS mechanics appear in
  `KS.md`, the three method links resolve to their planned paths, and the
  contract remains formula- and scale-neutral while allowing auditable method
  details.

  Commit:

  ```bash
  git add architecture/similarity_methods/README.md architecture/similarity_methods/KS.md architecture/contracts/60_30_similarity_result/README.md
  git commit -m "docs(architecture): define KS similarity family"
  ```

### Task 2: Add the `iat_ks` method owner

**Files:**
- Create: `architecture/similarity_methods/iat_ks/README.md`

**Interfaces:**
- Consumes: shared KS behavior from `architecture/similarity_methods/KS.md` and the existing `similarity.toml` contract.
- Produces: the exact extraction, validation, score details, and limitations selected by method name `iat_ks`.

- [ ] **Step 1: Define IAT extraction and minimum input**

  State that frames remain in recorded PCAPNG order and define:

  ```text
  IAT_i = timestamp_i - timestamp_(i-1)
  ```

  Convert each PCAPNG timestamp resolution to one common time meaning before
  comparison. Allow zero IAT, reject negative IAT, and never sort frames to
  conceal backwards time. Require at least two frames and therefore at least
  one IAT in each file.

- [ ] **Step 2: Define result details, validation, and limits**

  Reference `KS.md` for `D`, `1 - D`, p-value exclusion, Ethernet validation,
  unequal sample counts, and library policy. Require the result details to
  record raw KS distance, primary similarity, and both IAT sample counts.

  State explicitly that the method ignores absolute start time, packet count,
  frame length, order beyond consecutive IAT extraction, bursts with the same
  marginal distribution, flows, and protocols. Invalid timestamps, too few
  frames, unsupported link types, malformed PCAPNG, non-finite calculation, or
  library failure makes evaluation unsuccessful without a fallback score.

- [ ] **Step 3: Define future deterministic tests**

  Require unprivileged fixture tests for identical and separated distributions,
  unequal counts, tied timestamps and zero IAT, backwards timestamps, minimum
  frame count, timestamp-resolution conversion, no p-value output, and stable
  agreement with the selected library statistic. No test uses live capture or
  network access.

- [ ] **Step 4: Validate and commit Task 2**

  Run:

  ```bash
  git diff --check
  rg -n -- 'IAT_i|recorded PCAPNG order|zero IAT|negative IAT|at least two|sample counts|ks\.md|p-value' architecture/similarity_methods/iat_ks/README.md
  ```

  Expected: `iat_ks` owns only IAT-specific behavior and links shared KS rules
  rather than redefining the family formula.

  Commit:

  ```bash
  git add architecture/similarity_methods/iat_ks/README.md
  git commit -m "docs(architecture): add IAT KS method"
  ```

### Task 3: Add the `frame_size_ks` method owner

**Files:**
- Create: `architecture/similarity_methods/frame_size_ks/README.md`

**Interfaces:**
- Consumes: shared KS behavior from `architecture/similarity_methods/KS.md` and the existing `similarity.toml` contract.
- Produces: the exact extraction, validation, score details, and limitations selected by method name `frame_size_ks`.

- [ ] **Step 1: Define original frame-length extraction**

  Require each packet's original recorded Layer 2 frame length. State that the
  method never substitutes, scores, or otherwise uses the retained captured
  byte count. Accept a truncated packet when its original-length metadata is
  present and valid. Require at least one frame in each file.

  Do not reject an otherwise valid Ethernet observation only because its length
  is outside a current traffic model's generation range. Do not skip, clamp, or
  rewrite such an observation; its mismatch must remain visible in similarity.

- [ ] **Step 2: Define result details, validation, and limits**

  Reference `KS.md` for the statistic, primary similarity, p-value exclusion,
  Ethernet boundary, unequal counts, and library policy. Require raw KS
  distance, primary similarity, and both frame counts in result details.

  State that the method ignores timestamps, packet-count similarity, captured
  contents, ordering, addresses, flows, and protocols. Missing or invalid
  original lengths, too few frames, unsupported link types, malformed PCAPNG,
  non-finite calculation, or library failure is unsuccessful without a
  fallback score.

- [ ] **Step 3: Define future deterministic tests**

  Require unprivileged fixture tests for identical and separated distributions,
  unequal counts, tied lengths, one-frame inputs, truncated packets whose
  original length differs from captured length, valid lengths outside a current
  generator range, unsupported link types, missing metadata, no p-value output,
  and agreement with the selected library statistic.

- [ ] **Step 4: Validate and commit Task 3**

  Run:

  ```bash
  git diff --check
  rg -n -- 'original recorded|captured byte count|truncated|at least one|generation range|frame counts|ks\.md|p-value' architecture/similarity_methods/frame_size_ks/README.md
  ```

  Expected: `frame_size_ks` uses only original length and leaves every shared
  KS rule with the family owner.

  Commit:

  ```bash
  git add architecture/similarity_methods/frame_size_ks/README.md
  git commit -m "docs(architecture): add frame-size KS method"
  ```

### Task 4: Add the `l2_ks_weighted` combined method owner

**Files:**
- Create: `architecture/similarity_methods/l2_ks_weighted/README.md`

**Interfaces:**
- Consumes: the exact component behavior owned by `iat_ks` and `frame_size_ks`, plus shared KS behavior from `KS.md`.
- Produces: one configurable combined score selected by method name `l2_ks_weighted`.

- [ ] **Step 1: Define the combined score and configuration**

  Define:

  ```text
  score = iat_weight * iat_similarity
        + frame_size_weight * frame_size_similarity
  ```

  Include the normal settings:

  ```toml
  iat_weight = 0.5
  frame_size_weight = 0.5
  ```

  Require finite non-negative weights whose sum is `1`. Reject invalid weights
  instead of silently normalizing them. State that these settings follow the
  selected method's configuration through `60_similarity_evaluation`.

- [ ] **Step 2: Reuse both component owners without redefining them**

  Link `iat_ks` and `frame_size_ks` as the owners of extraction, minimum-input,
  validation, raw distance, and component-similarity rules. Require at least
  two frames per input because both components must succeed. Any failed
  component makes the combined evaluation unsuccessful; there is no partial
  score or weight redistribution.

- [ ] **Step 3: Define auditable results, limits, and future tests**

  Require result details to record both configured weights, both component
  similarities, both KS distances, their reference/generated sample counts,
  and the final score. State that a weighted combination remains in `[0, 1]`
  under the validated weights and that higher is better.

  Preserve all component limitations and state explicitly that the weighted
  average does not create a joint timing-size model. Require unprivileged tests
  for defaults, custom boundary weights `0` and `1`, invalid signs/sums/nonfinite
  values, exact weighted results, component failure, full detail output, and no
  p-value output.

- [ ] **Step 4: Validate and commit Task 4**

  Run:

  ```bash
  git diff --check
  rg -n -- 'iat_weight|frame_size_weight|0\.5|sum.*1|silently normal|component|final score|iat_ks|frame_size_ks' architecture/similarity_methods/l2_ks_weighted/README.md
  ```

  Expected: the combined method owns only weighting and reporting, while both
  component definitions remain linked to their owners.

  Commit:

  ```bash
  git add architecture/similarity_methods/l2_ks_weighted/README.md
  git commit -m "docs(architecture): add weighted L2 KS method"
  ```

### Task 5: Verify the complete similarity-method architecture

**Files:**
- Verify: `architecture/similarity_methods/README.md`
- Verify: `architecture/similarity_methods/KS.md`
- Verify: `architecture/similarity_methods/iat_ks/README.md`
- Verify: `architecture/similarity_methods/frame_size_ks/README.md`
- Verify: `architecture/similarity_methods/l2_ks_weighted/README.md`
- Verify: `architecture/contracts/60_30_similarity_result/README.md`

**Interfaces:**
- Consumes: the registry, family owner, three method owners, result contract, and existing application boundaries.
- Produces: evidence that the methods are selectable, interchangeable, auditable, internally consistent, and free of broken relative references.

- [ ] **Step 1: Run static content and scope checks**

  Run:

  ```bash
  git diff --check HEAD~4..HEAD
  rg -n -- 'iat_ks|frame_size_ks|l2_ks_weighted' architecture/similarity_methods
  rg -n -- 'D = max_x|similarity = 1 - D' architecture/similarity_methods
  rg -n -- 'p-value|captured byte count|raw IEEE 802\.11|Python librar' architecture/similarity_methods
  rg -n -- 'packet count|flow|protocol|joint' architecture/similarity_methods
  git status --short
  ```

  Expected: a clean worktree; all three selectable names occur in the registry
  and their owners; shared equations have one owner in `KS.md`; p-values are
  excluded; original-length and protocol boundaries are explicit.

- [ ] **Step 2: Validate every new relative link target**

  Run:

  ```bash
  test -f architecture/README.md
  test -f architecture/apps/60_similarity_evaluation/README.md
  test -f architecture/contracts/60_30_similarity_result/README.md
  test -f architecture/similarity_methods/README.md
  test -f architecture/similarity_methods/KS.md
  test -f architecture/similarity_methods/iat_ks/README.md
  test -f architecture/similarity_methods/frame_size_ks/README.md
  test -f architecture/similarity_methods/l2_ks_weighted/README.md
  ```

  Expected: every command exits `0`.

- [ ] **Step 3: Report completion**

  Report the shared family owner, all three selectable method owners, the
  result-contract clarification, verification output, and commit IDs. State
  explicitly that no Python implementation, dependency, PCAPNG artifact,
  runtime test, network operation, or elevated operation was added.
