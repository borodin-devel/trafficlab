# Directional Capture Conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `20_convert` and its `10_20_capture_directions` contract for deterministic target/uplink/downlink PCAPNG artifacts.

**Architecture:** `20_convert` owns boundary-based direction filtering and conversion publication. The contract owns the fixed five-file package and pipeline handoff. Capture remains the canonical producer and links to its consumer. External PCAPNG is accepted with explicit source provenance and an app-side reference profile.

**Tech Stack:** Markdown, POSIX shell, Git

## Global Constraints

- Direction is observed at the app-side boundary: leaving is uplink; arriving is downlink.
- Do not infer direction from client/server roles, ports, or arbitrary address guesses.
- Directional files contain only confidently classified IP packets; excluded packets are reported.
- Directional files preserve selected original packet bytes and metadata; `target.pcapng` is unchanged.
- The fixed output layout is manifest, target, uplink, downlink, and direction report.
- Uplink/downlink are Wi-Fi-like analytical labels, not physical Wi-Fi observations.
- `20_convert` creates no datasets, tables, exports, or model features.
- All documentation links are relative and resolve locally. Do not stage `.gitignore`.

---

### Task 1: Add the Conversion Application and Navigation

**Files:**

- Modify: `architecture/README.md`
- Modify: `architecture/apps/10_capture/README.md`
- Create: `architecture/apps/20_convert/README.md`

**Interfaces:**

- Consumes: `docs/superpowers/specs/2026-07-17-directional-capture-conversion-design.md` and the canonical capture artifact.
- Produces: the app owner for the directional conversion boundary.

- [ ] **Step 1: Insert conversion into the root reading order**

Place [20 convert](apps/20_convert/README.md) immediately after capture and
before workspace scripts/contracts in `architecture/README.md`.

- [ ] **Step 2: Link capture to its consumer**

In the capture output section, state that `20_convert` consumes the canonical
`raw/target.pcapng` under the
[capture directions contract](../../contracts/10_20_capture_directions/README.md).

- [ ] **Step 3: Create the conversion owner**

Write sections `Role`, `Inputs`, `Direction Model`, `Output`, `Publication`,
`Wi-Fi-like Terminology`, `Scope Boundary`, `Testing`, and `Reading`.

Document pipeline and external input modes; boundary/interface/local-address
reference profile; confidence-only IP selection; exclusion/reporting rules;
the fixed package; exact packet preservation; hashes/atomic publication;
unprivileged deterministic tests; and the absence of datasets and model data.
Link to capture, contract, development environment, and root governance.

- [ ] **Step 4: Verify application navigation**

Run: `test -f architecture/apps/20_convert/README.md && rg -q '\[20 convert application\]\(apps/20_convert/README.md\)' architecture/README.md && rg -q '\[20 convert application\](../20_convert/README.md)' architecture/apps/10_capture/README.md`

Expected: exit 0.

### Task 2: Create the Capture Directions Contract

**Files:**

- Delete: `architecture/contracts/.gitkeep`
- Create: `architecture/contracts/10_20_capture_directions/README.md`

**Interfaces:**

- Consumes: the canonical capture package or external PCAPNG plus reference profile.
- Produces: the fixed target/uplink/downlink directional package.

- [ ] **Step 1: Define contract ownership and input modes**

State that the contract owns the file package between capture and conversion.
For pipeline input, require canonical `raw/target.pcapng` and capture metadata.
For external input, require source description, source hash, and reference
profile. Do not specify a configuration serialization.

- [ ] **Step 2: Define the fixed artifact layout**

Use exactly:

```text
manifest.json
target.pcapng
uplink.pcapng
downlink.pcapng
direction-report.json
```

Define target as unchanged input; directional files as byte/timestamp/length/
interface-preserving filtered subsets; manifest hashes/provenance; report
profile, counts, exclusions, and reasons.

- [ ] **Step 3: Define classification and publication invariants**

State boundary-only direction, confidently classified IP-only directional
files, no guesswork, excluded ambiguity/non-IP reporting, deterministic output,
and validate/hash/atomic-publication requirements. State that labels are not
physical Wi-Fi observations.

- [ ] **Step 4: Verify contract content**

Run: `test -f architecture/contracts/10_20_capture_directions/README.md && ! test -e architecture/contracts/.gitkeep && rg -q 'target.pcapng' architecture/contracts/10_20_capture_directions/README.md && rg -q 'uplink.pcapng' architecture/contracts/10_20_capture_directions/README.md && rg -q 'downlink.pcapng' architecture/contracts/10_20_capture_directions/README.md && rg -q 'direction-report.json' architecture/contracts/10_20_capture_directions/README.md`

Expected: exit 0.

### Task 3: Validate and Commit Documentation

**Files:**

- Modify/Create/Delete: all files from Tasks 1–2.

**Interfaces:**

- Consumes: completed application and contract documents.
- Produces: a consistent committed directional-capture architecture.

- [ ] **Step 1: Run corpus checks**

Run: `! rg -ni 'draft|version_1' architecture && rg -q 'Wi-Fi-like analytical labels' architecture/apps/20_convert/README.md && rg -q 'Wi-Fi-like analytical labels' architecture/contracts/10_20_capture_directions/README.md && ! rg -n -e '[[:blank:]]+$' architecture && git diff --check`

Expected: exit 0.

- [ ] **Step 2: Review scope**

Run: `git diff -- architecture && git status --short`

Expected: only planned architecture changes and the implementation plan are present; `.gitignore` remains untracked.

- [ ] **Step 3: Commit in two scopes**

Run: `git add docs/superpowers/plans/2026-07-17-directional-capture-conversion.md && git commit -m "docs: plan directional capture conversion"`

Expected: a plan-only commit.

Run: `git add architecture/README.md architecture/apps/10_capture/README.md architecture/apps/20_convert/README.md architecture/contracts/.gitkeep architecture/contracts/10_20_capture_directions/README.md && git commit -m "docs(architecture): add directional conversion"`

Expected: an architecture-documentation commit with `.gitignore` unstaged.
