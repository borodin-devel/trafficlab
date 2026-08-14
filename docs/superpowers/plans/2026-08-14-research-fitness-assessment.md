# Research Fitness Assessment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce an evidence-based five-level grade for every criterion in
`architecture/RESEARCH_FITNESS_CRITERIA.md` without changing or repairing the
assessed product.

**Architecture:** Freeze product revision
`63f8c1b6da8a293bc65740890ca0ec7d0f479e1a`, collect four distinct evidence
classes, and synthesize them into one report. Source/mathematical, fresh local,
retained real-study, and primary-literature evidence remain separately labeled.
The report assigns independent grades only; it has no aggregate score.

**Tech Stack:** Git, uv, CPython 3.12.3, pytest, pytest-cov, Ruff, Pyright,
Docker Engine/Compose v2, the existing Validation Study audit API, Markdown, and primary
scientific sources.

## Global Constraints

- Assess exactly commit `63f8c1b6da8a293bc65740890ca0ec7d0f479e1a`.
- Use all 37 criteria and their exact five anchors from
  `architecture/RESEARCH_FITNESS_CRITERIA.md`.
- Grade each criterion independently; derive no overall grade or numeric score.
- Distinguish fresh verification from audited retained observations.
- Compare method implementations with both architecture and primary literature.
- Run every pytest command through `scripts/run_bounded.sh` with all five
  resource-control flags.
- Do not modify production source, tests, fixtures, architecture, configuration,
  or scientific artifacts when a defect is found; record and grade it.
- Create only `docs/RESEARCH_FITNESS_ASSESSMENT.md` as assessment output.
- Do not reward enterprise, multi-user, distributed, hosted, generic security,
  or speculative infrastructure features.
- Use applicable evidence, not test count, code size, or documentation volume,
  to choose a grade.

---

## File map

- Read: `architecture/RESEARCH_FITNESS_CRITERIA.md` — exact grading anchors.
- Read: `architecture/{README,SYSTEM,CAPTURE,TESTING,DEVELOPMENT,ROADMAP}.md` —
  declared product and evidence contracts.
- Read: `architecture/traffic_models/*.md` — traffic-model mathematics and
  citations.
- Read: `architecture/similarity_methods/*.md` — metric mathematics and
  citations.
- Read: `architecture/genetic_models/*.md` — competition mathematics and
  citations.
- Read: `src/trafficlab/**/*.py` and `scripts/*.py` — assessed implementation.
- Read: `tests/**/*.py` — direct behavioral evidence.
- Read: `examples/validation_study/**` and retained ignored Validation Study evidence — historical
  real-study evidence.
- Create: `docs/RESEARCH_FITNESS_ASSESSMENT.md` — sole assessment result.

---

### Task 1: Freeze revision and collect fresh local evidence

**Files:**
- Read: `pyproject.toml`
- Read: `uv.lock`
- Read: `architecture/TESTING.md`
- Read: `scripts/run_bounded.sh`
- Modify: none

**Interfaces:**
- Consumes: repository at assessed revision and documented verification commands.
- Produces: exact fresh command results for configurability, robustness, and
  reproducibility grading.

- [ ] **Step 1: Prove the assessed source revision and clean starting state**

Run:

```bash
ASSESS_COMMIT=63f8c1b6da8a293bc65740890ca0ec7d0f479e1a
test "$(git rev-parse "$ASSESS_COMMIT")" = "$ASSESS_COMMIT"
test "$(git diff --name-only "$ASSESS_COMMIT"..HEAD)" = \
  docs/superpowers/plans/2026-08-14-research-fitness-assessment.md
test -z "$(git status --porcelain=v1 --untracked-files=all)"
git show --no-patch --format=fuller "$ASSESS_COMMIT"
sha256sum uv.lock
```

Expected: exact assessed commit, clean tree, and one recorded lockfile hash.

- [ ] **Step 2: Verify locked environment and static quality**

Run sequentially:

```bash
LOCK_BEFORE="$(sha256sum uv.lock)"
uv sync --locked --all-groups
uv lock --check
test "$(sha256sum uv.lock)" = "$LOCK_BEFORE"
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
git diff --exit-code
```

Expected: each command exits zero and the lockfile/tree remain unchanged. Record
any failure rather than repairing it.

- [ ] **Step 3: Verify deterministic fixture regeneration**

Run:

```bash
uv run --locked python scripts/generate_phase2_fixtures.py --check
uv run --locked python scripts/generate_model_fixtures.py --check
uv run --locked python scripts/generate_fit_fixtures.py --check
git diff --exit-code
```

Expected: all three fixture trees compare byte-identically without edits.

- [ ] **Step 4: Run the full bounded non-external coverage gate**

Run exactly:

```bash
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -n 4 --dist worksteal --cov=trafficlab \
  --cov-branch --cov-report=term-missing \
  -m "not docker and not internet"
```

Expected: zero failures and at least 90% branch-aware package coverage. Preserve
the exact test count, coverage percentage, missed branches, and duration.

- [ ] **Step 5: Run the direct process-tree containment proof**

Run exactly:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/integration/test_process_guard.py
```

Expected: every wall, signal, collision, and hard-memory containment case passes;
the guard leaves no descendant or named test scope.

- [ ] **Step 6: Record post-gate integrity**

Run:

```bash
test -z "$(git status --porcelain=v1 --untracked-files=all)"
ASSESS_COMMIT=63f8c1b6da8a293bc65740890ca0ec7d0f479e1a
test "$(git diff --name-only "$ASSESS_COMMIT"..HEAD)" = \
  docs/superpowers/plans/2026-08-14-research-fitness-assessment.md
pgrep -af 'pytest|run_bounded.sh' || true
systemctl --user list-units 'trafficlab-test-guard-*.scope' --all --no-legend
```

Expected: source revision and tracked/untracked-visible tree are unchanged, with
no surviving test runner or guard scope.

### Task 2: Audit scientific methods against implementation and literature

**Files:**
- Read: `architecture/traffic_models/*.md`
- Read: `architecture/similarity_methods/*.md`
- Read: `architecture/genetic_models/*.md`
- Read: `src/trafficlab/models/*.py`
- Read: `src/trafficlab/similarity/*.py`
- Read: `src/trafficlab/genetic/*.py`
- Read: `tests/unit/{models,similarity,genetic}/*.py`
- Modify: none

**Interfaces:**
- Consumes: method specifications, implementations, direct behavioral tests, and
  primary literature.
- Produces: evidence for criteria 3.1 through 3.9 and related generation, fit,
  configurability, numerical, and reproducibility criteria.

- [ ] **Step 1: Build a specification-to-code map for every method**

Inspect and record exact owning functions for:

```text
Poisson empirical fit/generate
Markov Renewal Type-7 state construction, transition/holding fit, generate
two-state MMPP repair, fit, stationary initialization, CTMC/arrival generate
frame-size and IAT two-sample KS
autocorrelation estimator and weighted lag discrepancy
direction-aware multiscale packet/byte discrepancy
weighted aggregate fitness
population allocation, tournaments, elitism, crossover, mutation, duplicates,
checkpoint resume, early stopping, and final held-out validation
```

Expected: every declared equation and convention maps to an implementation and
direct test, or the gap is retained as assessment evidence.

- [ ] **Step 2: Audit primary scientific sources**

Use the direct citations in the architecture and primary authoritative sources
for Poisson processes, Markov renewal processes, MMPP, two-sample KS,
autocorrelation, and tournament selection. Record source title/URL, supported
claim, local conventions, and any material mismatch or limitation.

Expected: literature establishes scientific defensibility independently of the
repository's own prose. Do not use secondary summaries when a primary or
authoritative source is available.

- [ ] **Step 3: Run direct mathematical and model tests**

Run exactly:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 \
  tests/unit/models tests/unit/similarity tests/unit/genetic
```

Expected: direct hand calculations, scripted RNG behavior, fitting invariants,
metric boundaries, competition rules, and checkpoint state tests pass. Record
the exact count and failures without modifying code.

- [ ] **Step 4: Classify scientific validation strength**

For every model and metric, classify evidence as hand calculation, analytical
invariant, scripted RNG sequence, known-parameter simulation, independent
implementation/reference, real-data validation, or mutation-sensitive
regression.

Expected: grades distinguish self-consistency from independent scientific
validation and identify what prevents the next anchor.

### Task 3: Audit retained evidence and run available external verification

**Files:**
- Read: `examples/validation_study/{prerequisites.json,results.json,REPORT.md}`
- Read: `examples/validation_study/configs/*.toml`
- Read: `examples/validation_study/.study-work/evidence/**`
- Read: `runs/validation_study/**`
- Read: `scripts/run_validation_study.py`
- Read: `tests/{docker,internet}/*.py`
- Modify: none

**Interfaces:**
- Consumes: retained ten-run evidence, the production audit function, Docker,
  and the credential-free HTTPS range endpoint.
- Produces: fresh external and audited historical evidence for capture,
  end-to-end, robustness, and reproducibility criteria.

- [ ] **Step 1: Run the production retained-study audit read-only**

Run bounded:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked python -c \
  'from pathlib import Path; '\
'from scripts.run_validation_study import audit_published_study; '\
'root=Path.cwd(); '\
'audit_published_study(repository_root=root, '\
'prerequisite_path=root/"examples/validation_study/prerequisites.json", '\
'result_path=root/"examples/validation_study/results.json", '\
'report_path=root/"examples/validation_study/REPORT.md"); '\
  'print("validation_study audit passed")'
```

Expected: schemas, retained evidence, configs, report identifiers, lineage,
natural variation, summaries, all nine primary runs, and reproduction validate
without changing the tree.

- [ ] **Step 2: Independently inspect retained counts, hashes, and arithmetic**

Use independent standard-library JSON/hash code rather than the production audit
alone. Confirm three workloads by three repeats, one reproduction, configured
seeds, family champions, winner equality, retained hashes, report arithmetic,
natural-variation ordering, and stated limitations.

Expected: independent values agree exactly, or discrepancies become assessment
evidence.

- [ ] **Step 3: Run the exact serial Docker matrix if available**

Run:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m docker
```

Then inspect `org.trafficlab.pytest=1` labels for remaining containers,
networks, and volumes. Expected: zero failures/skips and zero owned resources.
If Docker is unavailable, retain the diagnostic as an evidence limitation.

- [ ] **Step 4: Run the exact serial Internet smoke if available**

Run:

```bash
TRAFFICLAB_INTERNET_URL='https://cachefly.cachefly.net/10mb.test'
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m internet \
  --internet-url "$TRAFFICLAB_INTERNET_URL"
```

Expected: the HTTPS range workload produces a parseable real capture and clean
resource state. Endpoint unavailability is reported separately from product
behavior.

- [ ] **Step 5: Verify no external residue or tracked mutation**

Run:

```bash
docker ps -aq --filter label=org.trafficlab.pytest=1
docker network ls -q --filter label=org.trafficlab.pytest=1
docker volume ls -q --filter label=org.trafficlab.pytest=1
test -z "$(git status --porcelain=v1 --untracked-files=all)"
```

Expected: all Docker queries are empty and the assessed tree is unchanged.

### Task 4: Grade all criteria and publish the assessment

**Files:**
- Create: `docs/RESEARCH_FITNESS_ASSESSMENT.md`
- Read: all evidence from Tasks 1 through 3
- Test: `tests/unit/test_readme.py`

**Interfaces:**
- Consumes: exact rubric anchors and the four classified evidence sets.
- Produces: the sole assessment result with 37 independent grades.

- [ ] **Step 1: Draft evidence inventory and methodology**

Use `apply_patch` to create the report with assessed commit/date, scope,
exclusions, grading rule, fresh command table, retained evidence inventory,
primary literature inventory, and known evidence limitations.

Expected: every claim identifies whether evidence is fresh, retained, source,
or literature evidence.

- [ ] **Step 2: Grade all 37 criteria in rubric order**

For each exact rubric heading, add:

```markdown
### N.N Criterion name

**Grade: acceptable**

**Evidence**

- Fresh: cite the exact directly relevant command result and test names.

**Rationale:** Explain why the cited evidence satisfies every part of the
selected anchor.

**Limitation:** Identify the missing independent or representative evidence
that prevents the next anchor.
```

Expected: one approved label, evidence, rationale, and next-grade limitation per
criterion, with no overall grade or numeric conversion.

- [ ] **Step 3: Add non-aggregating navigation summaries**

Add one grade-count table per top-level category and a concise cross-cutting
limitations section. Do not combine the counts into a project score or rank.

- [ ] **Step 4: Independently review evidence-to-grade fidelity**

Request read-only review of all low grades, all excellent grades, at least two
acceptable grades from every category, every literature-supported claim, and
every fresh-versus-retained characterization.

Expected: correct all Critical and Important evidence or grading findings before
commit; do not fix product defects during this task.

- [ ] **Step 5: Validate report structure mechanically**

Run:

```bash
test "$(rg -c '^### [1-5]\.[0-9]+ ' docs/RESEARCH_FITNESS_ASSESSMENT.md)" -eq 37
GRADE_COUNT="$(rg -c '^\*\*Grade: (dreadful|poor|partial|acceptable|excellent)\*\*$' \
  docs/RESEARCH_FITNESS_ASSESSMENT.md)"
test "$GRADE_COUNT" -eq 37
test "$(rg -c '^\*\*Evidence\*\*$' docs/RESEARCH_FITNESS_ASSESSMENT.md)" -eq 37
test "$(rg -c '^\*\*Rationale:\*\*' docs/RESEARCH_FITNESS_ASSESSMENT.md)" -eq 37
test "$(rg -c '^\*\*Limitation:\*\*' docs/RESEARCH_FITNESS_ASSESSMENT.md)" -eq 37
! rg -n -i 'overall (grade|score)|weighted score|[0-9]+/[0-9]+' docs/RESEARCH_FITNESS_ASSESSMENT.md
test "$(awk 'length($0) > 120 {count++} END {print count+0}' docs/RESEARCH_FITNESS_ASSESSMENT.md)" -eq 0
git diff --check
```

Expected: all records are complete, no aggregate score exists, and formatting is
clean.

- [ ] **Step 6: Run documentation regression and final tree check**

Run:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_readme.py
git diff --check
test "$(git diff --name-only)" = docs/RESEARCH_FITNESS_ASSESSMENT.md
```

Expected: documentation tests pass and the assessment is the only uncommitted
tracked change.

- [ ] **Step 7: Commit the assessment**

Run:

```bash
git add docs/RESEARCH_FITNESS_ASSESSMENT.md
git diff --cached --check
test "$(git diff --cached --name-only)" = docs/RESEARCH_FITNESS_ASSESSMENT.md
git commit -m "docs: assess research prototype fitness"
git show --check --stat --oneline HEAD
git status --short --branch
```

Expected: one assessment-only commit and a clean working tree.
