# Roadmap Dependency Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every roadmap phase depend only on completed earlier work and assign each CLI command to the phase that can first implement it fully.

**Architecture:** Add a permanent configuration-only preflight path, build the fixture-based PCAPNG/scientific core before Docker capture, and implement individual stage commands with their owning subsystems. Reserve Phase 6 for composing completed stages into `run`, with offline analytical and complete Docker-backed test paths named separately.

**Tech Stack:** Markdown architecture documents, Trafficlab CLI contracts, pytest integration scopes, Docker Compose capture workflow

## Global Constraints

- Preserve the seven-phase roadmap and all existing MVP algorithms, artifacts, and public command names.
- Add only one CLI option: `trafficlab preflight EXPERIMENT --config-only`.
- Configuration-only preflight performs local parsing, validation, path, output, and disk checks without any Docker call or resource.
- Plain `trafficlab preflight EXPERIMENT`, `capture`, and `run` always require full Docker preflight.
- Implement `compare` only after PCAPNG/canonical trace/similarity support, and before Docker capture.
- Implement `generate` with the traffic models, `fit` with the genetic strategy, and `run` only after every stage works.
- Never call a fixture-based `fit -> generate -> compare` path a full pipeline or `run`.
- Keep the direction-aware `capture.json`, `eth0`, multiscale, testing, and roadmap rules from the previous fix.
- Do not address observation windows, genetic operators, reproducibility metadata, or other audit findings in this fix.

---

### Task 1: Define incremental preflight and command ownership

**Files:**
- Modify: `architecture/SYSTEM.md:37-91`
- Modify: `architecture/SYSTEM.md:155-172`

**Interfaces:**
- Consumes: one validated experiment TOML and local filesystem state; full mode additionally consumes Docker/Compose readiness
- Produces: permanent configuration-only preflight semantics and a clear final command contract used by the reordered roadmap

- [ ] **Step 1: Add the configuration-only command form**

In the CLI block, change only the preflight line to:

```text
trafficlab preflight EXPERIMENT [--config-only]
```

State that the bracketed option is accepted only by `preflight`; every other
command rejects it.

- [ ] **Step 2: Split local and full preflight behavior**

Replace the current preflight paragraph with two explicit scopes:

```text
Local checks, always:
- parse TOML and reject unknown fields;
- validate types, bounds, weights, argv, and timeout relationships;
- validate local paths, mount sources, output path, and minimum free space;
- produce the same effective configuration as the Python API.

Full checks, unless --config-only:
- check Docker Engine and Compose;
- inspect or pull required images;
- validate Docker-facing mount/image requirements;
- run the bounded Compose DNS/network probe.
```

State that `--config-only` succeeds without Docker when local checks pass, does
not call Docker, and does not create probe resources. Plain preflight runs both
scopes. `capture` and `run` always invoke both scopes.

- [ ] **Step 3: Clarify stage implementation ownership**

After the command list, state that all commands are the final public surface but
the roadmap implements each command with its owning subsystem. Every command
calls an in-process stage function; `run` composes the same functions and never
implements an alternate path.

In the failure policy, distinguish a local preflight validation error from a
Docker full-preflight error. Both are direct stage errors; configuration-only
mode never reports Docker as skipped success evidence for `capture` or `run`.

- [ ] **Step 4: Verify the system contract**

Run:

```bash
rg -n 'preflight EXPERIMENT \[--config-only\]|Local checks|Full checks|does not call Docker|capture.*run.*both|owning subsystem|in-process stage' architecture/SYSTEM.md
git diff --check
```

Expected: the option, exact scope split, Docker boundary, command ownership, and
shared in-process implementation are present with no whitespace errors.

- [ ] **Step 5: Commit the command contract**

```bash
git add architecture/SYSTEM.md
git commit -m "docs: split local and full preflight"
```

### Task 2: Reorder the seven roadmap phases

**Files:**
- Modify: `architecture/ROADMAP.md`

**Interfaces:**
- Consumes: the system command contract from Task 1 and existing algorithm/capture deliverables
- Produces: one dependency-ordered implementation sequence in which every phase has achievable goals, tests, and done conditions

- [ ] **Step 1: Add a command-delivery summary**

After the roadmap introduction, add this table:

```markdown
| Phase | CLI behavior completed |
|---|---|
| 1 | `preflight --config-only` |
| 2 | `compare` |
| 3 | full `preflight`, `capture` |
| 4 | `generate` |
| 5 | `fit` |
| 6 | `run` |
```

State that Validation Study validates the complete MVP rather than adding a command.

- [ ] **Step 2: Correct Phase 1**

Rename Phase 1 to “Project, configuration, and local preflight.” Preserve the
toolchain and configuration work. Add the permanent `--config-only` local checks
and CLI/API equivalence. Its done condition must require:

```text
trafficlab preflight fixture.toml --config-only
```

to succeed without contacting Docker. Remove any wording that invokes plain/full
preflight in Phase 1.

- [ ] **Step 3: Make Phase 2 the fixture-based scientific boundary**

Move the current canonical trace and similarity content into Phase 2, named
“PCAPNG, canonical trace, and similarity.” Preserve strict `capture.json`,
Ethernet direction, four methods, formulas, direction-aware multiscale behavior,
and tests.

Add `compare` as an explicit deliverable and retain its done condition. Every
Phase 2 test uses checked-in fixtures and no Docker.

- [ ] **Step 4: Make Phase 3 own Docker preflight and capture**

Move the current Docker preflight/reference capture content into Phase 3. Add
plain/full `preflight` and `capture` as explicit deliverables. State that capture
uses the Phase 2 parser to validate PCAPNG and inspect addresses, protocols,
counts, and direction.

Retain non-promiscuous `eth0`, strict `capture.json`, Internet access, timeouts,
atomic publication, and unconditional cleanup. Its done condition continues to
require controlled and real Internet captures.

- [ ] **Step 5: Assign `generate` and `fit` to their real owners**

In Phase 4, add `generate` as a deliverable and require the CLI to load a
checked-in fitted-model fixture, produce deterministic canonical events and
`generated.pcapng`, then reload them. The Phase 4 done condition names
`trafficlab generate`.

In Phase 5, add `fit` as a deliverable while preserving population, selection,
checkpoint, resume, and winner behavior. Its existing done condition already
names `trafficlab fit` and remains valid.

- [ ] **Step 6: Restrict Phase 6 to orchestration**

Rename Phase 6 to “Run orchestration and complete integration.” Replace the
deliverable that implements all commands with:

```text
Compose the completed preflight, capture, fit, generate, and compare functions
inside trafficlab run without alternate subprocess protocols.
```

Retain output validation/reuse, trial/final separation, artifact preservation,
summaries, and test commands.

Replace “full pipeline against a checked-in capture fixture without Docker” with
the explicitly named offline analytical pipeline:

```text
fit -> generate -> compare
```

Keep a separate complete `run` test using the deterministic Docker workload. The
done condition requires `run` to create all documented artifacts from a fresh
Docker-backed experiment and individual stage commands to reproduce their own
stages.

- [ ] **Step 7: Preserve Validation Study and later evidence backlog**

Keep the Validation Study's real-program evidence and the “Later, only if evidence requires it”
list unchanged except for line-number movement. Confirm there are still exactly
seven numbered phases.

- [ ] **Step 8: Verify roadmap topology**

Run:

```bash
rg -n '^## Phase [1-7]|CLI behavior completed|config-only|checked-in fixtures|full `preflight`|`compare`|`capture`|`generate`|`fit`|`run`|offline analytical pipeline' architecture/ROADMAP.md
test "$(rg -c '^## Phase [1-7]' architecture/ROADMAP.md)" = "7"
! rg -n 'full pipeline.*without Docker|Implement `preflight`, `capture`, `fit`, `generate`, `compare`, and `run`' architecture/ROADMAP.md
git diff --check
```

Expected: seven phases remain, command ownership follows dependency order, the
offline analytical path is named accurately, both stale claims are absent, and
the diff is clean.

- [ ] **Step 9: Commit the reordered roadmap**

```bash
git add architecture/ROADMAP.md
git commit -m "docs: order roadmap by dependency"
```

### Task 3: Align integration-test scopes

**Files:**
- Modify: `architecture/TESTING.md:99-170`

**Interfaces:**
- Consumes: incrementally completed commands and phases from Tasks 1–2
- Produces: test terminology that distinguishes local configuration, fixture-based analytics, Docker capture, and complete run orchestration

- [ ] **Step 1: Add configuration-only CLI/API integration**

At the start of in-process integration tests, require the same effective
configuration and errors from:

```text
trafficlab preflight fixture.toml --config-only
```

and the injected Python configuration/preflight API. Assert that the CLI path
makes no Docker subprocess call.

- [ ] **Step 2: Rename the fixture pipeline**

Replace the current “full pipeline from a checked-in reference fixture” item
with:

```text
Run the offline analytical pipeline, fit -> generate -> compare, from a
checked-in reference.pcapng and capture.json through best_model.json,
generated.pcapng, and similarity.json without Docker.
```

Do not use `run` for this test.

- [ ] **Step 3: Add complete Docker-backed run evidence**

In Docker capture integration tests, preserve all capture assertions and add one
end-to-end small-budget test that invokes `trafficlab run` against the controlled
Docker workload and requires every documented artifact. Cleanup assertions remain
mandatory on both success and failure.

State in continuous integration that fixture analytics run in the ordinary job;
the complete `run` test belongs only to the Docker-capable job.

- [ ] **Step 4: Verify test terminology**

Run:

```bash
rg -n 'config-only|no Docker subprocess|offline analytical pipeline|fit -> generate -> compare|complete.*run|Docker-capable' architecture/TESTING.md
! rg -n 'full pipeline from a checked-in reference fixture|full pipeline.*without Docker' architecture/TESTING.md
git diff --check
```

Expected: local, offline analytical, capture, and complete run scopes are
distinct; stale full-pipeline wording is absent; the diff is clean.

- [ ] **Step 5: Commit the test-scope alignment**

```bash
git add architecture/TESTING.md
git commit -m "docs: separate pipeline test scopes"
```

### Task 4: Verify dependency order across the architecture

**Files:**
- Verify: `architecture/SYSTEM.md`
- Verify: `architecture/ROADMAP.md`
- Verify: `architecture/TESTING.md`

**Interfaces:**
- Consumes: all outputs of Tasks 1–3
- Produces: evidence that command semantics, phase ownership, and integration scopes agree

- [ ] **Step 1: Verify internal links and document structure**

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

roadmap = Path("architecture/ROADMAP.md").read_text()
assert len(re.findall(r"^## Phase [1-7]", roadmap, re.MULTILINE)) == 7
PY
```

Expected: all internal links resolve and exactly seven phases remain.

- [ ] **Step 2: Verify command and test ownership end to end**

Run:

```bash
rg -n 'preflight EXPERIMENT \[--config-only\]|does not call Docker|capture.*run.*both' architecture/SYSTEM.md
rg -n 'Phase 1|Phase 2|Phase 3|Phase 4|Phase 5|Phase 6|config-only|offline analytical pipeline' architecture/ROADMAP.md
rg -n 'config-only|offline analytical pipeline|fit -> generate -> compare|Docker-capable' architecture/TESTING.md
! rg -n 'full pipeline.*without Docker|full pipeline from a checked-in reference fixture|Implement `preflight`, `capture`, `fit`, `generate`, `compare`, and `run`' architecture
git diff --check HEAD~3..HEAD
git status --short
```

Expected: the system owns semantics, the roadmap owns dependency order, testing
owns evidence scopes, stale contradictions are absent, the three implementation
commits are whitespace-clean, and the working tree is clean.

- [ ] **Step 3: Record verification corrections only when needed**

If verification exposes a mismatch, make the smallest documentation correction,
rerun Steps 1–2, and commit it:

```bash
git add architecture/
git commit -m "docs: fix roadmap consistency"
```

If no file changes, do not create an empty commit.
