# Continuous Integration and Corpus Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Infrastructure Roadmap Substep 2.1.1 with deterministic architecture-corpus validation and a locked, least-privilege GitHub Actions quality gate.

**Architecture:** `tools/validate_architecture.py` keeps parsing and validation in a deterministic functional core over an immutable corpus snapshot; its CLI alone traverses files and prints issues. `tools/quality.py` remains the one fail-fast local/CI interface, while `.github/workflows/ci.yml` is a minimal GitHub adapter that bootstraps the exact environment and invokes `all`.

**Tech Stack:** Python 3.12 standard library, uv 0.11.25, pytest, ruff, pyright, setuptools, GitHub Actions

## Global Constraints

- The owner documents are `architecture/infrastructure/{SAD,SRS,ROADMAP}.md` and `architecture/VALIDATION.md`; original architecture prose is immutable.
- Every local and CI gate runs unprivileged, uses fixed argument vectors, performs no capture or host mutation, and fails on the first mandatory error.
- Validation is deterministic: paths, issues, identifiers, headings, links, registries, and output are sorted independently of filesystem iteration order.
- The validator uses only the Python 3.12 standard library, follows no symlink, accesses no network, and rejects paths that escape the repository.
- GitHub Actions gets only `contents: read`; third-party actions use full immutable commit SHAs and checkout credentials are not persisted.
- CI synchronizes `uv.lock` with `uv sync --locked --all-groups` and invokes `uv run --locked python tools/quality.py all` without duplicating check semantics.
- TDD red/green evidence is required for behavioral changes; each completed substage is committed separately with an allowed TASK.md category.

---

### Task 1: Restore the Central Roadmap's Unique Component Links

**Files:**

- Modify: `architecture/project/ROADMAP.md`

**Interfaces:**

- Consumes: Required Check 6 in `architecture/VALIDATION.md`.
- Produces: exactly one Markdown link from the central roadmap to every component roadmap.

- [ ] **Step 1: Demonstrate the existing duplicate**

Run:

```bash
rg -o '\(\.\./infrastructure/ROADMAP\.md\)' architecture/project/ROADMAP.md | wc -l
```

Expected: `2`, because the component list and evidence paragraph both link the
same owner.

- [ ] **Step 2: Remove only the redundant evidence link**

Change the Substep 1.1.1 evidence opening from:

```markdown
- **Evidence:** [Infrastructure](../infrastructure/ROADMAP.md) is 50% complete
```

to:

```markdown
- **Evidence:** The infrastructure roadmap is 50% complete
```

Keep the authoritative component-list link unchanged.

- [ ] **Step 3: Verify and commit the correction**

Run:

```bash
rg -o '\(\.\./infrastructure/ROADMAP\.md\)' architecture/project/ROADMAP.md | wc -l
git diff --check
```

Expected: the count is `1` and `git diff --check` is silent.

Commit:

```bash
git add architecture/project/ROADMAP.md
git commit -m "docs(roadmap): keep infrastructure link unique"
```

---

### Task 2: Reject Broken Architecture Links and Duplicate SRS Identifiers

**Files:**

- Create: `tools/validate_architecture.py`
- Create: `tests/infrastructure/test_architecture_validation.py`

**Interfaces:**

- Consumes: a repository root and architecture-root-relative Markdown files.
- Produces: `Corpus`, `SourceFile`, `ValidationIssue`, `corpus_from_mapping()`, `load_corpus()`, `validate_links()`, `validate_identifiers()`, `validate()`, and `main()`.
- Stable acceptance codes: `LNK001` missing target, `LNK002` missing fragment, `LNK003` unsafe path, and `SRS001` duplicate identifier.

- [ ] **Step 1: Write the failing acceptance tests**

Create `tests/infrastructure/test_architecture_validation.py` with these first
fixtures and assertions:

```python
"""Tests for deterministic architecture-corpus validation."""

from pathlib import Path, PurePosixPath

import pytest

from tools.validate_architecture import (
    ValidationIssue,
    corpus_from_mapping,
    validate,
    validate_identifiers,
    validate_links,
)


@pytest.mark.unit
def test_broken_relative_link_is_rejected() -> None:
    corpus = corpus_from_mapping(
        {"architecture/README.md": "# Architecture\n\n[Missing](MISSING.md)\n"}
    )

    assert validate_links(corpus) == (
        ValidationIssue(
            PurePosixPath("architecture/README.md"),
            3,
            "LNK001",
            "local target does not exist: architecture/MISSING.md",
        ),
    )


@pytest.mark.unit
def test_missing_heading_fragment_is_rejected() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/README.md": "# Architecture\n\n[Rule](RULES.md#missing)\n",
            "architecture/RULES.md": "# Rules\n\n## Present\n",
        }
    )

    assert validate_links(corpus)[0].code == "LNK002"


@pytest.mark.unit
def test_duplicate_requirement_identifier_is_rejected() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/one/SRS.md": "- **ONE-FR-001:** First.\n",
            "architecture/two/SRS.md": "- **ONE-FR-001:** Duplicate.\n",
        }
    )

    issues = validate_identifiers(corpus)

    assert len(issues) == 2
    assert {issue.code for issue in issues} == {"SRS001"}


@pytest.mark.unit
def test_issue_order_is_path_line_code_order() -> None:
    corpus = corpus_from_mapping(
        {
            "architecture/Z.md": "[Missing](NO.md)\n",
            "architecture/A.md": "[Missing](NO.md)\n",
        }
    )

    assert tuple(issue.path for issue in validate(corpus)) == (
        PurePosixPath("architecture/A.md"),
        PurePosixPath("architecture/Z.md"),
    )
```

- [ ] **Step 2: Run the tests and confirm the missing-module failure**

Run:

```bash
uv run pytest tests/infrastructure/test_architecture_validation.py -q
```

Expected: collection fails with `ModuleNotFoundError` for
`tools.validate_architecture`.

- [ ] **Step 3: Implement the immutable model, loader, Markdown parser, and acceptance rules**

In `tools/validate_architecture.py`, define these exact public records and
entry points:

```python
@dataclass(frozen=True, order=True, slots=True)
class ValidationIssue:
    path: PurePosixPath
    line: int
    code: str
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: [{self.code}] {self.message}"


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: PurePosixPath
    text: str


@dataclass(frozen=True, slots=True)
class Corpus:
    architecture_root: PurePosixPath
    markdown_files: tuple[SourceFile, ...]
    existing_paths: frozenset[PurePosixPath]
    directories: frozenset[PurePosixPath]


```

Add the call signatures `corpus_from_mapping(files: Mapping[str, str]) ->
Corpus`, `load_corpus(repository_root: Path, architecture_root: Path) ->
Corpus`, `validate_links(corpus: Corpus) -> tuple[ValidationIssue, ...]`,
`validate_identifiers(corpus: Corpus) -> tuple[ValidationIssue, ...]`,
`validate(corpus: Corpus) -> tuple[ValidationIssue, ...]`, and
`main(argv: Sequence[str] | None = None) -> int`, with the following complete
behavior:

- `corpus_from_mapping` normalizes POSIX paths, infers every parent directory,
  rejects absolute/escaping paths, sorts `SourceFile` values, and sets
  `architecture_root` to `architecture`.
- `load_corpus` resolves both roots, requires the architecture root to remain
  beneath the repository, walks with `os.walk(..., followlinks=False)`, prunes
  `.git`, `.venv`, `.worktrees`, caches, `build`, and `dist`, records regular
  repository paths, and reads only architecture `*.md` files as UTF-8.
- Markdown extraction ignores fenced code, reads inline and reference-style
  link destinations, and records one-based source lines.
- Local URI paths are percent-decoded, normalized relative to the source,
  rejected if absolute or escaping, and checked against `existing_paths`.
- Heading anchors remove formatting punctuation, case-fold letters, replace
  each whitespace character with `-`, retain Unicode letters/numbers and ASCII
  `_`/`-`, and suffix duplicate anchors with `-1`, `-2`, in source order.
- Identifier declarations match bold uppercase tokens with at least three
  hyphen-separated fields, for example `INF-FR-001`; every duplicate produces
  an `SRS001` issue at every declaration.
- `validate` concatenates rule results and returns `tuple(sorted(issues))`.
- `main` defaults to `architecture`, catches boundary decoding/path errors with
  exit code 2, prints every rendered issue, and returns 1 for validation issues
  or 0 for success.

Do not use `shell=True`, network calls, mutable global state, or filesystem
access from rule functions.

- [ ] **Step 4: Run focused tests and static checks**

Run:

```bash
uv run pytest tests/infrastructure/test_architecture_validation.py -q
uv run ruff format --check tools/validate_architecture.py tests/infrastructure/test_architecture_validation.py
uv run ruff check tools/validate_architecture.py tests/infrastructure/test_architecture_validation.py
uv run pyright tools/validate_architecture.py tests/infrastructure/test_architecture_validation.py
```

Expected: all four commands pass.

- [ ] **Step 5: Commit the acceptance core**

```bash
git add tools/validate_architecture.py tests/infrastructure/test_architecture_validation.py
git diff --cached --check
git commit -m "feature(validation): reject broken links and duplicate IDs"
```

---

### Task 3: Enforce the Complete Mechanical Architecture Contract

**Files:**

- Modify: `tools/validate_architecture.py`
- Modify: `tests/infrastructure/test_architecture_validation.py`

**Interfaces:**

- Consumes: Task 2's immutable `Corpus` and issue model.
- Produces: `validate_naming()`, `validate_component_documents()`, `validate_srs_structure()`, `validate_roadmaps()`, `validate_roadmap_links()`, `validate_registries()`, and `validate_hygiene()`; `validate()` invokes them in that order after link/ID checks.
- Stable rule families: `NAM`, `DOC`, `SRS`, `RDM`, `REG`, and `HYG`.

- [ ] **Step 1: Add failing unit fixtures for every structural rule family**

Add parametrized tests that assert these exact defects and codes:

```python
@pytest.mark.unit
@pytest.mark.parametrize(
    ("files", "expected_code"),
    [
        ({"architecture/bad-name.md": "# Bad\n"}, "NAM001"),
        ({"architecture/apps/demo/README.md": "# Demo\n"}, "DOC001"),
        (
            {
                "architecture/demo/SRS.md": (
                    "# SRS\n\n## Requirements\n\n- **DEM-FR-001:** Rule.\n"
                )
            },
            "SRS002",
        ),
        (
            {
                "architecture/demo/ROADMAP.md": (
                    "# Roadmap\n\n## [100%] STAGE 1 — Invalid\n"
                )
            },
            "RDM001",
        ),
        ({"architecture/demo/NOTE.md": "# Note \n"}, "HYG001"),
    ],
)
def test_structural_defect_is_rejected(
    files: dict[str, str], expected_code: str
) -> None:
    assert expected_code in {issue.code for issue in validate(corpus_from_mapping(files))}
```

Add focused fixtures for a central roadmap that omits or duplicates a component
roadmap (`RDM005`), a registry missing an immediate component (`REG001`), a
non-plan roadmap entry without `Evidence` (`RDM003`), and a parent status that
does not reflect its immediate children (`RDM004`).

- [ ] **Step 2: Run the new tests and confirm each rule family is absent**

Run:

```bash
uv run pytest tests/infrastructure/test_architecture_validation.py -q
```

Expected: failures show the corresponding expected code missing from the issue
set, not fixture or import errors.

- [ ] **Step 3: Implement the structural rules**

Use these exact conventions:

- Markdown filenames match `(?=.*[A-Z])[A-Z0-9_]+\.md`; architecture directory
  segments match `(?=.*[a-z])[a-z0-9_]+`.
- Immediate component directories under `apps`, `libs`, `contracts`, `scripts`,
  `traffic_models`, `genetic_models`, and `similarity_methods` require
  `README.md`, `SAD.md`, `SRS.md`, and `ROADMAP.md`. Include `project` and
  `infrastructure` in the same owner-file check.
- Every SRS contains `## Acceptance Criteria`, at least one declared `-AC-`
  identifier, `## Traceability`, and at least one local link after that heading.
- Roadmap headings match the documented level, label, number, em dash, and only
  `[PLAN]`, `[BLKD]`, `[CR_B]`, `[MK_B]`, `[MN_B]`, `[TSTR]`, `[DONE]`,
  `[  1%]` through `[  9%]`, or `[ 10%]` through `[ 99%]`.
- Stage and step bodies require `Task`, `Deliverable`, `Applicable test types`,
  and `Completion criteria`. Substeps require `Objective`, `Implementation`,
  `Affected files`, `Dependencies`, `Outputs`, `Tests`, `Validation`, and
  `Completion criteria`. Non-plan entries also require `Evidence`.
- Parent states use immediate children: all plan is plan; all done is done;
  complete children with any TSTR is TSTR; bug/block states use
  CR_B/MK_B/MN_B/BLKD precedence; otherwise use the rounded, clamped arithmetic
  mean with PLAN=0 and DONE/TSTR=100.
- `architecture/project/ROADMAP.md` links every other architecture roadmap once;
  each other roadmap links back once.
- Registry tables cover every immediate model/method/strategy directory exactly
  once, use an owner link to `<name>/README.md`, and place unresolved entries in
  an explicitly unselectable section or status cell.
- Every architecture Markdown line is free of trailing spaces/tabs. A
  `.gitkeep` is invalid when its directory contains any other path.

Keep every checker side-effect-free and return sorted immutable issue tuples.

- [ ] **Step 4: Add and run the committed-corpus integration test**

Add:

```python
@pytest.mark.integration
def test_repository_architecture_corpus_is_valid() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    corpus = load_corpus(repository_root, repository_root / "architecture")

    assert validate(corpus) == ()
```

Run:

```bash
uv run pytest tests/infrastructure/test_architecture_validation.py -q
uv run python -m tools.validate_architecture architecture
```

Expected: all unit fixtures pass and the CLI is silent with exit code 0. If the
integration test reveals a genuine pre-existing corpus defect, diagnose it
against its owner rule, make the smallest permitted roadmap-only correction,
record the finding in the plan's decision log, and rerun the focused fixture.

- [ ] **Step 5: Run static checks and commit**

```bash
uv run ruff format --check tools/validate_architecture.py tests/infrastructure/test_architecture_validation.py
uv run ruff check tools/validate_architecture.py tests/infrastructure/test_architecture_validation.py
uv run pyright tools/validate_architecture.py tests/infrastructure/test_architecture_validation.py
git diff --check
git add tools/validate_architecture.py tests/infrastructure/test_architecture_validation.py
git commit -m "feature(validation): enforce architecture corpus rules"
```

---

### Task 4: Add Documentation and Whitespace to the Quality Interface

**Files:**

- Modify: `tools/quality.py`
- Modify: `tests/infrastructure/test_quality.py`
- Modify: `README.md`

**Interfaces:**

- Consumes: Task 3's `python -m tools.validate_architecture architecture` CLI.
- Produces: named `whitespace` and `docs` checks and an aggregate order ending `coverage`, `whitespace`, `docs`, `build`.

- [ ] **Step 1: Write failing command-vector, order, and injected-failure tests**

Update the order assertion to:

```python
assert tuple(check.name for check in select_checks("all")) == (
    "format",
    "lint",
    "typecheck",
    "test",
    "coverage",
    "whitespace",
    "docs",
    "build",
)
```

Add:

```python
@pytest.mark.unit
def test_documentation_checks_use_fixed_repository_commands() -> None:
    (whitespace,) = select_checks("whitespace")
    (docs,) = select_checks("docs")

    assert whitespace.argv == ("git", "diff", "--check")
    assert docs.argv == (
        sys.executable,
        "-m",
        "tools.validate_architecture",
        "architecture",
    )


@pytest.mark.unit
@pytest.mark.parametrize("failed_name", ["test", "build"])
def test_mandatory_failure_stops_the_aggregate_gate(failed_name: str) -> None:
    calls: list[tuple[str, ...]] = []
    failed = next(check for check in CHECKS if check.name == failed_name)

    def fake_runner(argv: tuple[str, ...]) -> int:
        calls.append(argv)
        return 23 if argv == failed.argv else 0

    assert run_checks(CHECKS, runner=fake_runner) == 23
    assert calls[-1] == failed.argv
```

- [ ] **Step 2: Run the focused tests and confirm the order/vector failures**

```bash
uv run pytest tests/infrastructure/test_quality.py -q
```

Expected: the order omits `whitespace`/`docs`, and selecting either name raises
`ValueError`.

- [ ] **Step 3: Add the two fixed checks immediately before build**

In `CHECKS`, add:

```python
Check("whitespace", ("git", "diff", "--check")),
Check(
    "docs",
    (
        sys.executable,
        "-m",
        "tools.validate_architecture",
        "architecture",
    ),
),
```

Do not alter `run_checks`, argument parsing, or existing vectors.

Document these commands in `README.md`:

```bash
uv run python tools/quality.py whitespace
uv run python tools/quality.py docs
```

Link the docs command to `architecture/VALIDATION.md` instead of copying its
rule list.

- [ ] **Step 4: Verify and commit the quality interface**

```bash
uv run pytest tests/infrastructure/test_quality.py tests/infrastructure/test_architecture_validation.py -q
uv run python tools/quality.py whitespace
uv run python tools/quality.py docs
uv run ruff check tools tests
uv run pyright tools tests
git add tools/quality.py tests/infrastructure/test_quality.py README.md
git diff --cached --check
git commit -m "infra(quality): enforce docs and whitespace gates"
```

---

### Task 5: Add the Locked Least-Privilege GitHub Workflow

**Files:**

- Create: `.github/workflows/ci.yml`
- Create: `tests/infrastructure/test_ci_workflow.py`
- Modify: `README.md`

**Interfaces:**

- Consumes: `uv.lock` and Task 4's `tools/quality.py all`.
- Produces: a `quality` GitHub Actions check for pull requests and pushes to `main`.

- [ ] **Step 1: Write the failing workflow contract tests**

Create `tests/infrastructure/test_ci_workflow.py`:

```python
"""Contract tests for the hosted quality adapter."""

import re
from pathlib import Path

import pytest


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"


@pytest.mark.unit
def test_ci_uses_immutable_least_privilege_actions() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    action_refs = re.findall(r"uses: [^@\s]+@([^\s]+)", workflow)

    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
    assert "permissions:\n  contents: read\n" in workflow
    assert "persist-credentials: false" in workflow


@pytest.mark.unit
def test_ci_delegates_to_the_locked_repository_gate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "uv sync --locked --all-groups" in workflow
    assert "uv run --locked python tools/quality.py all" in workflow
    assert workflow.count("tools/quality.py all") == 1
    assert 'version: "0.11.25"' in workflow
    assert 'python-version: "3.12"' in workflow
    assert "enable-cache: true" in workflow
    assert 'cache-dependency-glob: "uv.lock"' in workflow
```

- [ ] **Step 2: Run the tests and confirm the missing-workflow failure**

```bash
uv run pytest tests/infrastructure/test_ci_workflow.py -q
```

Expected: both tests fail with `FileNotFoundError` for `.github/workflows/ci.yml`.

- [ ] **Step 3: Create the exact workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  pull_request:
  push:
    branches:
      - main

permissions:
  contents: read

jobs:
  quality:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    steps:
      - name: Check out source
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - name: Install uv and Python
        uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2
        with:
          version: "0.11.25"
          python-version: "3.12"
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - name: Synchronize locked environment
        run: uv sync --locked --all-groups
      - name: Run mandatory gates
        run: uv run --locked python tools/quality.py all
```

Append a short `Continuous integration` paragraph to `README.md` stating that
GitHub calls the same `all` command and that repository branch protection must
require the `quality` job; do not claim branch protection is already enabled.

- [ ] **Step 4: Verify workflow policy and commit**

```bash
uv run pytest tests/infrastructure/test_ci_workflow.py -q
uv run ruff format --check tests/infrastructure/test_ci_workflow.py
uv run ruff check tests/infrastructure/test_ci_workflow.py
uv run pyright tests/infrastructure/test_ci_workflow.py
git diff --check
git add .github/workflows/ci.yml tests/infrastructure/test_ci_workflow.py README.md
git commit -m "infra(ci): run locked quality gate on GitHub"
```

---

### Task 6: Verify, Record Evidence, and Complete Infrastructure

**Files:**

- Modify: `architecture/infrastructure/ROADMAP.md`
- Modify: `architecture/project/ROADMAP.md`
- Modify: this plan only if consequential deviations occurred
- Review: `ROADMAP.md`

**Interfaces:**

- Consumes: Tasks 1-5 and INF-AC-002.
- Produces: `[DONE]` Infrastructure Stage 2 evidence and a 14% central foundation status derived from one complete component of seven.

- [ ] **Step 1: Run focused defect and failure-path evidence**

```bash
uv run pytest \
  tests/infrastructure/test_architecture_validation.py \
  tests/infrastructure/test_quality.py \
  tests/infrastructure/test_ci_workflow.py -q
uv run python tools/quality.py docs
```

Expected: broken-link, missing-fragment, duplicate-ID, roadmap, registry,
hygiene, failing-test, failing-build, and workflow-policy fixtures pass; the
committed corpus produces no issue output.

- [ ] **Step 2: Run the complete gate twice and compare wheels**

```bash
uv sync --locked --all-groups
uv run --locked python tools/quality.py all
sha256sum dist/trafficlab-0.1.0-py3-none-any.whl
uv run --locked python tools/quality.py build
sha256sum dist/trafficlab-0.1.0-py3-none-any.whl
git diff --check
```

Expected: both aggregate/build runs pass, both hashes are identical, and the
diff check is silent.

- [ ] **Step 3: Verify a clean local clone of the committed branch**

After Tasks 1-5 are committed, clone the local repository and current branch
into an exact `mktemp -d` path, then run:

```bash
uv sync --locked --all-groups
uv run --locked python tools/quality.py all
```

Expected: locked synchronization and every gate pass without access to the
development worktree environment. Remove only the exact temporary clone after
recording test count, corpus count, and wheel hash.

- [ ] **Step 4: Update roadmap status and evidence**

In `architecture/infrastructure/ROADMAP.md`, change Stage 2, Step 2.1, and
Substep 2.1.1 to `[DONE]`. Add local evidence naming:

- the standard-library validator and stable defect fixtures;
- the number of architecture Markdown files validated;
- immutable action pins, least privilege, locked uv sync, and local/CI command parity;
- focused test count, full suite count, coverage, static checks, `git diff --check`, and reproducible wheel hash;
- the clean-clone verification.

In `architecture/project/ROADMAP.md`, change the first Stage/Step/Substep status
from `[  7%]` to `[ 14%]`. State that Infrastructure is `[DONE]`, six sibling
component roadmaps remain `[PLAN]`, and the equal-weight arithmetic is
`100 / 7 = 14%` after rounding. Keep exactly one central link to the
infrastructure roadmap.

Do not modify original SAD, SRS, CONFIGS, or VALIDATION prose. `ROADMAP.md`
remains the one-line pointer and needs no status duplication.

- [ ] **Step 5: Re-run the full gate on the evidence diff and commit**

```bash
uv run --locked python tools/quality.py all
git diff --check
git status --short
git add architecture/infrastructure/ROADMAP.md architecture/project/ROADMAP.md docs/superpowers/plans/2026-07-21-continuous-integration-and-corpus-validation.md
git diff --cached --check
git commit -m "docs(roadmap): complete infrastructure delivery"
```

- [ ] **Step 6: Request independent review and integrate**

Use `superpowers:requesting-code-review` against the Stage 2 base. Resolve every
Critical and Important finding through `superpowers:receiving-code-review`,
rerun `tools/quality.py all`, then use
`superpowers:finishing-a-development-branch`. Under TASK.md's autonomous
contract, take the safe local fast-forward option, verify merged `main`, remove
only the owned `.worktrees/` checkout, and continue to the next central-roadmap
deliverable.

## Decision Log

- **Minor — provider scheduling wording:** Infrastructure CONFIGS says the
  initial configuration set resolves the provider, while Stage 2 owns CI
  implementation. GitHub is selected now from the configured remote; no
  immutable architecture prose is changed.
- **Minor — duplicate central link:** Stage 1 evidence introduced a second link
  to the infrastructure roadmap. Task 1 restores the exact-once corpus rule
  without changing status meaning.
- **Minor — capture-request substep test labels:** Task 3's committed-corpus
  integration fixture found four capture-request roadmap substeps using
  `Applicable test types` instead of the substep-owned `Tests` field required
  by `architecture/VALIDATION.md`. Rename only those four roadmap field labels;
  their test definitions and status meaning remain unchanged.
- **Supply-chain choice:** Use checkout v7.0.1 SHA
  `3d3c42e5aac5ba805825da76410c181273ba90b1` and setup-uv v8.3.2 SHA
  `11f9893b081a58869d3b5fccaea48c9e9e46f990`, verified from their official
  GitHub release commits on 2026-07-21. Pin uv to the locally verified 0.11.25.

## Plan Self-Review

- Spec coverage: Tasks 2-3 implement every mechanical corpus rule; Task 4
  provides identical local commands and explicit failure propagation; Task 5
  provides secure hosted CI; Task 6 supplies clean-environment evidence and
  roadmap status arithmetic.
- Type consistency: all tasks use `Corpus`, `SourceFile`, `ValidationIssue`,
  immutable issue tuples, `Path` at the shell boundary, and `PurePosixPath` in
  the core.
- Scope: no runtime application, capture, deployment, or architecture-prose
  change is included.
