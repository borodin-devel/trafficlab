# Development Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fixed, uv-managed Python development stack and document fast, precise testing and Git workspace practices for the Trafficlab prototype.

**Architecture:** Keep project metadata and every Python-tool setting in one root `pyproject.toml`, with `.python-version` selecting CPython 3.12 and `uv.lock` fixing resolved packages. Put human workflow in one `architecture/DEVELOPMENT.md`, then make only focused cross-reference, testing, and roadmap edits. Use a single root `.gitignore`; do not add task runners or JavaScript project files.

**Tech Stack:** CPython 3.12.x, uv, pytest, pytest-cov, pytest-xdist, Pyright, Ruff, Git worktrees

## Global Constraints

- Support CPython `>=3.12,<3.13`; `.python-version` contains `3.12` so the latest compatible patch may be used.
- Use uv exclusively for Python installation, dependency management, locking, environments, and project commands.
- Commit `uv.lock`; do not hand-edit it or add a second dependency format.
- Keep pytest, coverage, Ruff, and Pyright configuration in `pyproject.toml`.
- Set Ruff maximum line length to exactly 120 characters.
- Use strict Pyright checking for future `src/trafficlab` and `tests` Python code.
- Pyright is the only Node.js-based development exception; add no `package.json`, npm scripts, `node_modules`, or Node.js application/build dependency.
- A function or method implicated by a failed unit test requires focused regression tests with 100% executable-line and branch coverage before its fix is complete.
- Do not impose a project-wide 100% coverage threshold.
- Prefer `.worktrees/<branch-name>` for substantial future implementation; this approved documentation/tooling amendment remains in the current checkout under the user's earlier direct-workspace choice.

---

### Task 1: Add the locked project toolchain and ignore policy

**Files:**
- Create: `.python-version`
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create through uv: `uv.lock`

**Interfaces:**
- Consumes: the fixed-version, dependency, Node.js-exception, testing, and workspace policies in `docs/superpowers/specs/2026-08-11-development-tooling-design.md`
- Produces: a locked uv development environment and centralized configuration used by every later development command and document

- [ ] **Step 1: Write the Python selector and root ignore policy**

Create `.python-version` with exactly:

```text
3.12
```

Create `.gitignore` with exactly these repository-local generated paths:

```gitignore
.worktrees/
.venv/

__pycache__/
*.py[cod]

.pytest_cache/
.coverage
.coverage.*
htmlcov/
.ruff_cache/
.pyright/

build/
dist/
*.egg-info/

.env
.env.*
!.env.example
*.log
runs/
```

- [ ] **Step 2: Verify ignore boundaries before adding tool configuration**

Run:

```bash
test "$(cat .python-version)" = "3.12"
git check-ignore -q .worktrees/probe
git check-ignore -q .venv/probe
git check-ignore -q runs/probe
! git check-ignore -q uv.lock
! git check-ignore -q .python-version
```

Expected: every command exits zero; generated workspaces/environments/runs are ignored, while reproducibility files remain trackable.

- [ ] **Step 3: Create centralized project and tool configuration**

Create `pyproject.toml` with:

```toml
[project]
name = "trafficlab"
version = "0.1.0"
description = "Research prototype for fitting and comparing network traffic models"
readme = "architecture/README.md"
requires-python = ">=3.12,<3.13"
dependencies = []

[dependency-groups]
dev = [
    "pyright",
    "pytest",
    "pytest-cov",
    "pytest-xdist",
    "ruff",
]

[tool.pytest.ini_options]
addopts = "--strict-config --strict-markers"
testpaths = ["tests"]
pythonpath = ["src"]
markers = [
    "integration: joins multiple Trafficlab modules without external services",
    "docker: requires Docker Engine and Docker Compose",
    "internet: uses a configurable public Internet endpoint",
]

[tool.coverage.run]
branch = true
source = ["trafficlab"]

[tool.coverage.report]
show_missing = true

[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F", "I", "B", "UP"]

[tool.pyright]
include = ["src/trafficlab", "tests"]
pythonVersion = "3.12"
typeCheckingMode = "strict"
```

Do not add a build backend yet: Phase 1 still owns creation of the Python package and entry point.

- [ ] **Step 4: Resolve and verify the committed lock**

Run:

```bash
uv lock
uv sync --locked --all-groups
uv lock --check
uv run --locked pytest --version
uv run --locked ruff --version
uv run --locked pyright --version
```

Expected: uv creates `uv.lock`, the locked sync succeeds under Python 3.12, `uv lock --check` reports no lock change is needed, and all five development packages are present in the lock. Pyright may obtain its private Node.js runtime as its one approved development-only exception; no JavaScript project files appear.

- [ ] **Step 5: Validate the static configuration values**

Run:

```bash
uv run --locked python - <<'PY'
from pathlib import Path
import tomllib

data = tomllib.loads(Path("pyproject.toml").read_text())
assert data["project"]["requires-python"] == ">=3.12,<3.13"
assert set(data["dependency-groups"]["dev"]) == {
    "pyright", "pytest", "pytest-cov", "pytest-xdist", "ruff"
}
assert data["tool"]["ruff"]["line-length"] == 120
assert data["tool"]["pyright"]["typeCheckingMode"] == "strict"
assert {item.split(":", 1)[0] for item in data["tool"]["pytest"]["ini_options"]["markers"]} == {
    "integration", "docker", "internet"
}
PY
```

Expected: the script exits zero.

- [ ] **Step 6: Commit the toolchain**

```bash
git add .gitignore .python-version pyproject.toml uv.lock
git commit -m "build: add locked Python toolchain"
```

### Task 2: Publish the development workflow

**Files:**
- Create: `architecture/DEVELOPMENT.md`
- Modify: `architecture/README.md`

**Interfaces:**
- Consumes: commands and policies backed by Task 1's `pyproject.toml`, `.python-version`, `.gitignore`, and `uv.lock`
- Produces: the single public development reference linked from the architecture index

- [ ] **Step 1: Write the development document**

Create `architecture/DEVELOPMENT.md` with these sections and policies:

1. **Supported environment** — CPython 3.12.x, `uv python install 3.12`, `uv sync --locked --all-groups`, committed `.python-version` and `uv.lock`.
2. **Dependency changes** — use `uv add`, `uv add --dev`, `uv remove`, and `uv lock --upgrade-package PACKAGE`; never hand-edit `uv.lock` or add pip/requirements/Poetry/Pipenv/tox/Hatch/npm workflows.
3. **Quality commands** — include exactly:

   ```bash
   uv run --locked ruff format .
   uv run --locked ruff format --check .
   uv run --locked ruff check .
   uv run --locked pyright
   ```

4. **Test commands** — document exactly:

   ```bash
   uv run --locked pytest -q -n auto --dist worksteal \
     -m "not integration and not docker and not internet"

   uv run --locked pytest -n auto --dist worksteal --cov=trafficlab \
     --cov-branch --cov-report=term-missing \
     -m "not docker and not internet"

   uv run --locked pytest -vv -n 0 -m docker
   uv run --locked pytest -vv -n 0 -m internet --internet-url URL
   uv run --locked pytest -vv -x -n 0 tests/path/test_module.py::test_name
   uv run --locked pytest -vv -x -n 0 --lf
   ```

5. **Coverage after a failure** — no global 100% target; after a unit-test failure identifies a defective function/method, add behavioral regression tests and use branch/missing-line output until that function alone has 100% executable-line and branch coverage.
6. **Node.js boundary** — Pyright alone may use Node.js as a development implementation detail; prohibit `package.json`, npm scripts, `node_modules`, and Node.js runtime/build dependencies.
7. **Git workspace preference** — use `.worktrees/<branch-name>` for substantial feature/multi-file work after confirming it is ignored; allow tiny documentation corrections in the main checkout.
8. **CI shape** — locked sync, Ruff format check, Ruff lint, Pyright, one parallel coverage-enabled deterministic test pass; run Docker tests serially in a Docker-capable job and Internet tests only manually or on a schedule.

Keep the document concise and link to `TESTING.md` instead of repeating capture-test behavior.

- [ ] **Step 2: Link the document from the architecture index**

In `architecture/README.md`, add this item after System and before Capture:

```markdown
- [Development](DEVELOPMENT.md) defines the fixed Python toolchain, quality
  commands, and Git workspace policy.
```

Add a principle stating that uv and `pyproject.toml` are the only Python project/tooling interfaces; do not duplicate the detailed commands in the index.

- [ ] **Step 3: Verify content and links**

Run:

```bash
test -f architecture/DEVELOPMENT.md
rg -n '>=3\.12,<3\.13|line length.*120|100%.*branch|Pyright.*only|\.worktrees/' architecture/DEVELOPMENT.md
rg -n '\[Development\]\(DEVELOPMENT\.md\)' architecture/README.md
git diff --check
```

Expected: each required policy is found, the index link resolves, and no whitespace errors are reported.

- [ ] **Step 4: Commit the public development documentation**

```bash
git add architecture/DEVELOPMENT.md architecture/README.md
git commit -m "docs: add development workflow"
```

### Task 3: Align testing and the roadmap

**Files:**
- Modify: `architecture/TESTING.md`
- Modify: `architecture/ROADMAP.md`

**Interfaces:**
- Consumes: `architecture/DEVELOPMENT.md` as owner of development commands and `pyproject.toml` as owner of marker/tool configuration
- Produces: consistent test scopes, regression expectations, and Phase 1 implementation criteria without duplicating the entire development guide

- [ ] **Step 1: Replace the test command and marker section**

Update `architecture/TESTING.md` so it:

- links to `DEVELOPMENT.md` as the command owner;
- uses the fast parallel, coverage-enabled deterministic, serial Docker, serial Internet, selected-node-ID, and `--lf` commands from Task 2;
- distinguishes the registered `integration`, `docker`, and `internet` markers;
- explains that broad deterministic suites use `-n auto --dist worksteal`, while resource-owning and diagnostic tests explicitly use `-n 0`;
- keeps unavailable Docker readiness as a failure when Docker tests are explicitly selected.

Remove the statement that the command wrapper may change during Phase 1.

- [ ] **Step 2: Add the focused regression coverage rule**

Replace the final statement that no coverage percentage is an architectural requirement with the following policy:

```markdown
There is no project-wide percentage target. When a failed unit test identifies a
defect in a function or method, the fix requires behavioral regression tests
that cover 100% of that function's executable lines and branches. Verify the
source range with targeted `pytest-cov` missing-line output; do not build a
custom per-function coverage framework.
```

Preserve the principle that meaningful integration paths matter more than
uninformative aggregate coverage.

- [ ] **Step 3: Make Phase 1 own the toolchain foundation**

In Phase 1 of `architecture/ROADMAP.md`:

- add deliverables for `.python-version`, `pyproject.toml`, `uv.lock`, the root `.gitignore`, centralized Ruff/Pyright/pytest/coverage settings, and the documented commands;
- add tests/checks for `uv sync --locked --all-groups`, Ruff format/lint, strict Pyright, parallel deterministic tests, and serial pinpointed tests;
- change the done condition to require those checks from a clean clone as well as the existing `trafficlab preflight fixture.toml` behavior.

In Phase 6, keep integration-suite ownership but refer to the established commands rather than implying a new wrapper.

- [ ] **Step 4: Run cross-document consistency checks**

Run:

```bash
rg -n 'pytest .*-(n auto|n 0)|pytest-cov|100%|DEVELOPMENT\.md' architecture/TESTING.md
rg -n '\.python-version|pyproject\.toml|uv\.lock|ruff|pyright|parallel|pinpoint' architecture/ROADMAP.md
! rg -n 'command wrapper may change|No coverage percentage' architecture/TESTING.md
git diff --check
```

Expected: testing contains the command ownership and focused coverage rule, the roadmap contains every Phase 1 tooling deliverable, stale wording is absent, and the diff is clean.

- [ ] **Step 5: Commit the aligned architecture**

```bash
git add architecture/TESTING.md architecture/ROADMAP.md
git commit -m "docs: align testing and roadmap"
```

### Task 4: Verify the complete amendment

**Files:**
- Verify: `.gitignore`
- Verify: `.python-version`
- Verify: `pyproject.toml`
- Verify: `uv.lock`
- Verify: `architecture/README.md`
- Verify: `architecture/DEVELOPMENT.md`
- Verify: `architecture/TESTING.md`
- Verify: `architecture/ROADMAP.md`

**Interfaces:**
- Consumes: all outputs of Tasks 1–3
- Produces: evidence that the architecture, executable tool configuration, lock, and ignore behavior agree

- [ ] **Step 1: Verify the locked environment and tool configuration**

Run:

```bash
uv sync --locked --all-groups
uv lock --check
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pytest --version
uv run --locked pyright --version
```

Expected: every command exits zero. Do not run the documented full pytest scopes yet because Phase 1 has not created `src/trafficlab` or `tests`; pytest would correctly report that it collected no tests.

- [ ] **Step 2: Verify internal Markdown links and required policy text**

Run:

```bash
uv run --locked python - <<'PY'
from pathlib import Path
import re

root = Path("architecture")
missing = []
for document in root.rglob("*.md"):
    text = document.read_text()
    for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
        if "://" in target or target.startswith("#"):
            continue
        path = (document.parent / target.split("#", 1)[0]).resolve()
        if not path.exists():
            missing.append(f"{document}: {target}")
assert not missing, "\n".join(missing)
PY

rg -n '3\.12|uv run --locked|120|100%|Pyright|Node\.js|\.worktrees/' \
  architecture/DEVELOPMENT.md architecture/TESTING.md architecture/ROADMAP.md
git diff --check HEAD~3..HEAD
git status --short
```

Expected: all relative links resolve, every required policy appears in its owning document, the three implementation commits have no whitespace errors, and the working tree is clean.

- [ ] **Step 3: Record fixes only if verification changed files**

If verification exposed a documentation or configuration mismatch, make the smallest correction, rerun Steps 1–2, then commit only those corrections:

```bash
git add .gitignore .python-version pyproject.toml uv.lock architecture/
git commit -m "docs: fix tooling consistency"
```

If no file changed, do not create an empty commit.
