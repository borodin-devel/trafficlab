# Roadmap Milestone Terminology Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace numbered Roadmap milestone aliases outside `architecture/`
and `docs/` with precise capability names while preserving legitimate generic
lifecycle phases and ordinal repetition, attempt, and release labels.

**Architecture:** Apply contextual terminology at the owning boundary rather
than globally replacing words. Tests first pin descriptive Docker tags,
generator diagnostics, and the repository-wide absence of numbered milestone
aliases; source prose and deterministic fixtures then move together without
changing schemas, accepted evidence, or runtime behavior.

**Tech Stack:** Python 3.12, pytest, Ruff, Pyright, uv, Git, Docker Compose.

**Spec:**
`docs/superpowers/specs/2026-08-18-roadmap-milestone-terminology-design.md`

## Global Constraints

- Do not modify existing content under `architecture/` or `docs/` except this
  implementation plan.
- Preserve generic lifecycle names such as `_begin_phase_attempt`,
  `phase_capture_image`, and capture failure phase.
- Preserve every `r<number>` proven to be a repetition, study attempt, or
  release ordinal.
- Expand numbered milestone references fully in comments, docstrings, help,
  errors, READMEs, and reports; constrained identifiers and tags may use a
  concise capability name.
- Do not rewrite accepted validation evidence, alter scientific calculations,
  add dependencies, or run a new validation study.
- Follow RED, minimal GREEN, focused verification, and coherent local commit for
  every behavior-bearing increment.

---

### [TASK-1-c7f15eb4] Docker capture integration tag terminology

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/fixtures/data/docker/compose.endpoint.json`
- Modify: `tests/unit/test_docker_fixture_support.py`
- Modify: `tests/integration/test_full_preflight.py`

**Interfaces:**
- Consumes: existing Docker fixture constants `CAPTURE_IMAGE`,
  `ENDPOINT_IMAGE`, `CLIENT_IMAGE`, and `NO_SHELL_IMAGE`.
- Produces: exact `*:docker-capture-test` image tags shared by fixture setup,
  retained Compose data, and integration assertions.

- [ ] **[STEP-1-0856dc7f] Write failing Docker tag assertions**

Change the direct expected references in
`tests/unit/test_docker_fixture_support.py` and
`tests/integration/test_full_preflight.py` before changing fixture constants:

```python
reference = "trafficlab-capture:docker-capture-test"
assert environment.capture_image.startswith("trafficlab-capture:docker-capture-test-")
```

Require the retained Compose fixture to use
`trafficlab-endpoint:docker-capture-test` for all three services.

- [ ] **[STEP-2-3593ff7f] Run Docker tag RED tests**

Run:

```bash
UV_OFFLINE=1 uv run --locked pytest -q -n 0 \
  tests/unit/test_docker_fixture_support.py \
  tests/integration/test_full_preflight.py
```

Expected: FAIL because `tests/conftest.py` and the checked fixture still expose
the milestone-coded tag.

- [ ] **[STEP-3-423a1796] Rename Docker integration tags and fixture data**

Set the four constants in `tests/conftest.py` to:

```python
CAPTURE_IMAGE = "trafficlab-capture:docker-capture-test"
ENDPOINT_IMAGE = "trafficlab-endpoint:docker-capture-test"
CLIENT_IMAGE = "trafficlab-client:docker-capture-test"
NO_SHELL_IMAGE = "trafficlab-no-shell:docker-capture-test"
```

Replace each retained endpoint image in
`tests/fixtures/data/docker/compose.endpoint.json` with the matching descriptive
tag. Do not alter service names or Compose structure.

- [ ] **[STEP-4-ff75a80d] Run Docker tag GREEN tests and static checks**

Run the Step 2 pytest command, then:

```bash
UV_OFFLINE=1 uv run --locked ruff format --check \
  tests/conftest.py tests/unit/test_docker_fixture_support.py \
  tests/integration/test_full_preflight.py
UV_OFFLINE=1 uv run --locked ruff check \
  tests/conftest.py tests/unit/test_docker_fixture_support.py \
  tests/integration/test_full_preflight.py
UV_OFFLINE=1 uv run --locked pyright \
  tests/conftest.py tests/unit/test_docker_fixture_support.py \
  tests/integration/test_full_preflight.py
```

Expected: all commands exit zero.

- [ ] **[STEP-5-ec2146e2] Commit Docker tag terminology**

```bash
git add tests/conftest.py tests/fixtures/data/docker/compose.endpoint.json \
  tests/unit/test_docker_fixture_support.py \
  tests/integration/test_full_preflight.py
git commit -m "test: name Docker capture fixture tags"
```

### [TASK-2-6d6a0866] Deterministic fixture generator terminology

**Files:**
- Modify: `scripts/generate_similarity_fixtures.py`
- Modify: `scripts/generate_model_fixtures.py`
- Modify: `scripts/generate_fit_fixtures.py`
- Modify: `examples/data/fit/README.md`
- Modify: `tests/unit/models/test_fixture_generator.py`
- Modify: `tests/integration/test_generate_cli.py`

**Interfaces:**
- Consumes: existing fixture generator CLI and deterministic byte contracts.
- Produces: unchanged fixture bytes except for the generated fitting README;
  errors and help identify canonical-trace similarity, traffic-model generation,
  and genetic fitting by name.

- [ ] **[STEP-6-d1053f4e] Write failing generator terminology assertions**

Update the parent-read error expectation first:

```python
with pytest.raises(
    TrafficlabError,
    match="parent canonical-trace and offline-similarity fixture.*parent read sentinel",
):
    fixture_generator._build_fixture()  # pyright: ignore[reportPrivateUsage]
```

Rename test identifiers to describe behavior:

```python
def test_similarity_builder_publishes_the_model_that_owns_generated_bytes(...):
def test_normal_mode_writes_both_traffic_model_artifacts(...):
def test_check_mode_accepts_both_exact_traffic_model_artifacts(...):
def test_cli_generated_capture_matches_checked_traffic_model_fixture_and_final_settings(...):
```

- [ ] **[STEP-7-f29af5ff] Run generator terminology RED tests**

Run:

```bash
UV_OFFLINE=1 uv run --locked pytest -q -n 0 \
  tests/unit/models/test_fixture_generator.py \
  tests/integration/test_generate_cli.py
```

Expected: the parent-read assertion fails because the generator still reports
the numbered similarity milestone.

- [ ] **[STEP-8-c38ae110] Expand similarity and model generator terminology**

In `scripts/generate_similarity_fixtures.py`, describe the fixture as the
`canonical-trace and offline-similarity fixture`; explain in every error that
metadata, reference events, model, and generated capture belong to that
fixture. Change the temporary prefix to `trafficlab-similarity-fixtures-`.

In `scripts/generate_model_fixtures.py`, describe the output as the
`traffic-model generation fixture`; explain that its parent input is the
`canonical-trace and offline-similarity fixture`. CLI help must say
`byte-compare both checked-in traffic-model generation artifacts`, and terminal
errors must start with `traffic-model generation fixture:`.

- [ ] **[STEP-9-e27d8ce8] Expand genetic fitting fixture terminology**

In `scripts/generate_fit_fixtures.py`, replace every numbered fitting milestone
reference with `genetic-fitting and checkpoint-resume fixture`. The generated
README must explain that the Docker-free fixture exercises production codecs
and the real heterogeneous fitting path, including checkpoint-compatible
artifacts. Regenerate only `examples/data/fit/README.md` if its deterministic
content changes.

- [ ] **[STEP-10-00856e7d] Run fixture GREEN checks and static analysis**

Run:

```bash
UV_OFFLINE=1 uv run --locked pytest -q -n 0 \
  tests/unit/models/test_fixture_generator.py \
  tests/integration/test_generate_cli.py
UV_OFFLINE=1 uv run --locked python scripts/generate_similarity_fixtures.py --check
UV_OFFLINE=1 uv run --locked python scripts/generate_model_fixtures.py --check
UV_OFFLINE=1 uv run --locked python scripts/generate_fit_fixtures.py --check
UV_OFFLINE=1 uv run --locked ruff format --check \
  scripts/generate_similarity_fixtures.py scripts/generate_model_fixtures.py \
  scripts/generate_fit_fixtures.py tests/unit/models/test_fixture_generator.py \
  tests/integration/test_generate_cli.py
UV_OFFLINE=1 uv run --locked ruff check \
  scripts/generate_similarity_fixtures.py scripts/generate_model_fixtures.py \
  scripts/generate_fit_fixtures.py tests/unit/models/test_fixture_generator.py \
  tests/integration/test_generate_cli.py
UV_OFFLINE=1 uv run --locked pyright \
  scripts/generate_similarity_fixtures.py scripts/generate_model_fixtures.py \
  scripts/generate_fit_fixtures.py tests/unit/models/test_fixture_generator.py \
  tests/integration/test_generate_cli.py
```

Expected: all tests, checks, and deterministic byte comparisons pass.

- [ ] **[STEP-11-a2e122b6] Commit deterministic fixture terminology**

```bash
git add scripts/generate_similarity_fixtures.py \
  scripts/generate_model_fixtures.py scripts/generate_fit_fixtures.py \
  examples/data/fit/README.md tests/unit/models/test_fixture_generator.py \
  tests/integration/test_generate_cli.py
git commit -m "refactor: name deterministic fixture roles"
```

### [TASK-3-6f0f38e3] Validation and artifact commentary terminology

**Files:**
- Modify: `tests/unit/test_repository_layout.py`
- Modify: `tests/unit/test_validation_study.py`
- Modify: `src/trafficlab/artifacts.py`
- Modify: `scripts/run_validation_study.py`
- Modify: `README.md`
- Modify: `examples/validation_study/README.md`
- Modify: `examples/validation_study/REPORT.md`
- Modify: `.superpowers/sdd/2026-08-15-research-fitness-implementation/task-13-report.md`

**Interfaces:**
- Consumes: Git tracked-file inventory and existing accepted validation bundle.
- Produces: a repository regression that confines numbered Roadmap aliases to
  `architecture/` and `docs/`, plus fully expanded explanatory prose elsewhere.

- [ ] **[STEP-12-78abf1e8] Write the repository terminology regression**

Add `import os` beside the existing standard-library imports in
`tests/unit/test_repository_layout.py`, then add this behavior:

```python
def test_numbered_roadmap_aliases_are_confined_to_authoritative_documents() -> None:
    legacy_word = "pha" + "se"
    pattern = re.compile(
        rf"{legacy_word}[\s_-]*[0-9]+",
        flags=re.IGNORECASE,
    )
    completed = subprocess.run(
        ("git", "ls-files", "-z"),
        cwd=REPOSITORY,
        check=True,
        stdout=subprocess.PIPE,
    )
    matches: list[Path] = []
    for encoded in completed.stdout.split(b"\0"):
        if not encoded:
            continue
        relative = Path(os.fsdecode(encoded))
        if relative.parts[0] in {"architecture", "docs"}:
            continue
        path = REPOSITORY / relative
        if not path.is_file():
            continue
        try:
            document = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if pattern.search(document):
            matches.append(relative)
    assert matches == []
```

The split literal prevents the guard from matching its own implementation.

- [ ] **[STEP-13-9f2278f4] Run the repository terminology RED test**

Run:

```bash
UV_OFFLINE=1 uv run --locked pytest -q -n 0 \
  tests/unit/test_repository_layout.py::test_numbered_roadmap_aliases_are_confined_to_authoritative_documents
```

Expected: FAIL with the remaining source, test, prose, fixture-tag, and retained
implementation-report paths.

- [ ] **[STEP-14-ee1092ba] Expand validation study and artifact commentary**

Use these complete explanations:

```python
# src/trafficlab/artifacts.py
"""Atomically publish deterministic records for a prepared experiment run,
the artifact boundary established by project configuration and local preflight.
"""

# scripts/run_validation_study.py
"""Collect one immutable, audit-ready real-program validation candidate
through the existing capture, fitting, generation, and comparison owners.
"""
```

In root and validation-study prose, replace the numbered validation milestone
with `real-program validation study` and explain whether the material is an old
non-accepted attempt, the replacement protocol, or complete retained evidence.
In the retained Task 13 report, explain that the complete fixture represents the
nine-run real-program validation workload rather than merely naming a milestone.
Rename the validation-study test to
`test_offline_bundle_fixture_carries_complete_real_program_validation_evidence_and_reconstructs_it`.

- [ ] **[STEP-15-b2502aad] Remove misleading numeric synthetic path aliases**

In `tests/unit/test_repository_layout.py`, change the synthetic
`docs/legacy-phase-1.md` path to `docs/legacy-phase-name.md` in both setup and
expected results. Retain generic phase-name checker coverage and the other
generic lifecycle examples unchanged.

- [ ] **[STEP-16-8de0329c] Run terminology GREEN tests and evidence audit**

Run:

```bash
UV_OFFLINE=1 uv run --locked pytest -q -n 0 \
  tests/unit/test_repository_layout.py \
  tests/unit/test_validation_study.py::test_offline_bundle_fixture_carries_complete_real_program_validation_evidence_and_reconstructs_it \
  tests/unit/test_readme.py
UV_OFFLINE=1 uv run --locked python scripts/audit_validation_study.py \
  --repository . \
  examples/validation_study/evidence/2026-08-18-research-fitness-r21
```

Expected: all tests pass and the auditor accepts 231 retained files without
changing evidence bytes.

- [ ] **[STEP-17-b0df4463] Commit commentary terminology**

```bash
git add tests/unit/test_repository_layout.py \
  tests/unit/test_validation_study.py src/trafficlab/artifacts.py \
  scripts/run_validation_study.py README.md \
  examples/validation_study/README.md examples/validation_study/REPORT.md \
  .superpowers/sdd/2026-08-15-research-fitness-implementation/task-13-report.md
git commit -m "docs: expand roadmap milestone terminology"
```

### [TASK-4-719405f0] Ordinal label audit

**Files:**
- Inspect: every tracked file outside `architecture/` and `docs/` containing a
  case-insensitive standalone `r<number>` token.
- Modify: only a source or test file where contextual inspection proves the
  token is not a repetition, whole-study attempt, or release ordinal.

**Interfaces:**
- Consumes: training repeat layout, validation study IDs, and test attempt
  identities.
- Produces: an exhaustive classification with no hidden named stage represented
  by `r<number>`.

- [ ] **[STEP-18-bdf64fe4] Inventory every ordinal r label**

Run and retain the complete output:

```bash
git grep -inE '(^|[^[:alnum:]_])r[0-9]+([^[:alnum:]_]|$)' -- . \
  ':(exclude)architecture/**' ':(exclude)docs/**'
git ls-files | rg -i '(^|[/_.-])r[0-9]+([/_.-]|$)' \
  | rg -v '^(architecture|docs)/'
```

- [ ] **[STEP-19-617ea7bc] Classify repetition attempt and release labels**

Classify every match into one exact category:

```text
training/<workload>/r1..r3             repeated captures
fresh_simulation/<workload>/r1..r3     matching fresh simulations
*-r<number> study IDs                  whole-study attempts
study-r<number> test IDs               synthetic whole-study attempts
release context + r<number>            release ordinal
```

Inspect any match outside those shapes individually. Do not infer semantics
from spelling alone.

- [ ] **[STEP-20-b7b0c2e6] Rename only opaque nonordinal identifiers**

If a source-local identifier is not demonstrably one of the categories above,
replace it with the capability or lifecycle name proven by its owning code and
add a focused failing test first. Do not rewrite retained IDs or paths whose
ordinal meaning is established.

- [ ] **[STEP-21-e759de00] Run focused ordinal and validation tests**

If Step 20 changes code, run its exact owner test plus:

```bash
UV_OFFLINE=1 uv run --locked pytest -q -n 0 \
  tests/unit/test_validation_study.py \
  tests/integration/test_validation_study_collection.py
```

Expected: all tests pass. If Step 20 makes no changes, record the complete
classification and proceed without manufacturing a commit.

- [ ] **[STEP-22-4b52faee] Commit any proven ordinal clarity corrections**

Stage only files changed by a RED-proven correction and commit with:

```bash
git commit -m "refactor: clarify ordinal validation labels"
```

Skip this commit when the audit confirms every token is already a valid ordinal.

### [TASK-5-ce875125] Complete verification and review

**Files:**
- Verify: complete tracked tree and accepted validation-study bundle.
- Modify: only files required by a failing gate, using a new RED/GREEN cycle.

**Interfaces:**
- Consumes: all prior terminology commits.
- Produces: clean tree, complete passing test evidence, exact terminology scan,
  and independent approval with no Critical or Important findings.

- [ ] **[STEP-23-5f4fd587] Run deterministic fixture checks**

```bash
UV_OFFLINE=1 uv sync --locked --all-groups
UV_OFFLINE=1 uv lock --check
UV_OFFLINE=1 uv run --locked python scripts/generate_similarity_fixtures.py --check
UV_OFFLINE=1 uv run --locked python scripts/generate_model_fixtures.py --check
UV_OFFLINE=1 uv run --locked python scripts/generate_fit_fixtures.py --check
UV_OFFLINE=1 uv run --locked python scripts/generate_validation_study_fixture.py --check
```

Expected: every command exits zero without changing tracked files.

- [ ] **[STEP-24-f51a74b6] Run global formatting linting and typing**

```bash
UV_OFFLINE=1 uv run --locked ruff format --check .
UV_OFFLINE=1 uv run --locked ruff check .
UV_OFFLINE=1 uv run --locked pyright
git diff --check
```

Expected: every command exits zero.

- [ ] **[STEP-25-f463a050] Run bounded nonDocker tests and coverage**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 30s -- \
  uv run --locked pytest -q -n auto -m 'not docker and not internet'
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 30s -- \
  uv run --locked pytest -q -n 0 -m 'not docker and not internet' \
  --cov=trafficlab --cov-branch --cov-report=term-missing \
  --cov-fail-under=90
```

Expected: both suites pass and branch-aware package coverage is at least 90%.

- [ ] **[STEP-26-182e2b3f] Run bounded Docker and Internet tests**

The session-scoped `docker_test_environment` fixture builds uniquely suffixed
capture images from the descriptive base tag and removes them in teardown. Run:

```bash
scripts/run_bounded.sh --memory-high 8G --memory-max 10G --swap-max 2G \
  --wall-time 20m --kill-after 30s -- \
  uv run --locked pytest -q -n 0 -m docker
scripts/run_bounded.sh --memory-high 8G --memory-max 10G --swap-max 2G \
  --wall-time 5m --kill-after 30s -- \
  uv run --locked pytest -q -n 0 -m internet \
  --internet-url \
  'https://upload.wikimedia.org/wikipedia/commons/5/5b/SPACE_ELECTRIC_ROCKET_TEST%2C_SERT_II_IN_TANK_5_%28GRC-1968-C-03031%29.jpg'
```

Expected: all available external tests pass. Remove only the exact owned test
tags and confirm no owned containers remain.

- [ ] **[STEP-27-bf514fbd] Run final terminology and evidence audits**

```bash
git grep -inE 'phase[[:space:]_-]*[0-9]+' -- . \
  ':(exclude)architecture/**' ':(exclude)docs/**'
git ls-files | rg -i 'phase[_. -]*[0-9]+' \
  | rg -v '^(architecture|docs)/'
UV_OFFLINE=1 uv run --locked python scripts/audit_validation_study.py \
  --repository . \
  examples/validation_study/evidence/2026-08-18-research-fitness-r21
git status --short --branch
```

Expected: both terminology scans return no matches, the evidence audit accepts
231 retained files, and the tracked worktree is clean.

- [ ] **[STEP-28-612eef97] Request independent review and commit fixes**

Request a read-only review of the full terminology range. Require the reviewer
to inspect every changed context, the two explicit exception classes, fixture
determinism, Docker tag consistency, accepted-evidence immutability, and test
evidence. Fix each Critical or Important finding through a focused RED/GREEN
cycle and coherent local commit. Do not push.
