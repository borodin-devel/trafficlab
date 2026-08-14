# Trafficlab Project README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a detailed root README that onboards researchers and links every stage, traffic model, similarity
method, and genetic model directly to its authoritative `architecture/` documentation.

**Architecture:** Keep `README.md` as a concise onboarding and navigation layer. Add one focused unit test that
checks required sections, exact stage/model/method links, and existence of every relative Markdown link; do not
create a parallel documentation hierarchy or change production behavior.

**Tech Stack:** Markdown, Python 3.12, pytest, pathlib, regular expressions, uv, Ruff, strict Pyright.

## Global Constraints

- Implement inline on `main`; do not create another worktree.
- Use `apply_patch` for the README and test.
- Link directly to `architecture/`; do not create `docs/stages`, `docs/traffic_models`, `docs/similarity_methods`,
  or `docs/genetic_models`.
- Use only implemented CLI commands and locked `uv run --locked` examples.
- Route every pytest command through `scripts/run_bounded.sh` with all five resource flags.
- Do not change production code, dependencies, configuration schemas, fixtures, study evidence, or Roadmap state.

---

### Task 1: Root onboarding README and link contract

**Files:**
- Create: `README.md`
- Create: `tests/unit/test_readme.py`

**Interfaces:**
- Consumes: CLI stages from `src/trafficlab/cli.py`, example configuration at `examples/configs/minimal.toml`, and
  the authoritative architecture corpus.
- Produces: root project onboarding plus a permanent relative-link and required-section contract.

- [ ] **Step 1: Write the failing README contract test**

Create `tests/unit/test_readme.py` with these exact behaviors:

```python
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
README_PATH = REPOSITORY_ROOT / "README.md"

REQUIRED_HEADINGS = {
    "## Purpose",
    "## Requirements",
    "## Installation",
    "## Quick start",
    "## Workflow stages",
    "## Traffic models",
    "## Similarity methods",
    "## Genetic model",
    "## Testing",
    "## Project documentation",
}

REQUIRED_LINKS = {
    "architecture/SYSTEM.md#preflight",
    "architecture/SYSTEM.md#capture",
    "architecture/SYSTEM.md#fit",
    "architecture/SYSTEM.md#generate",
    "architecture/SYSTEM.md#compare",
    "architecture/SYSTEM.md#run",
    "architecture/traffic_models/poisson_empirical.md",
    "architecture/traffic_models/markov_renewal.md",
    "architecture/traffic_models/mmpp.md",
    "architecture/similarity_methods/frame_size_ks.md",
    "architecture/similarity_methods/iat_ks.md",
    "architecture/similarity_methods/autocorrelation.md",
    "architecture/similarity_methods/multiscale_rate.md",
    "architecture/genetic_models/basic_generational.md",
}


def _links(document: str) -> set[str]:
    return set(re.findall(r"(?<!!)\[[^]]+\]\(([^)]+)\)", document))


def test_readme_has_required_sections_and_architecture_links() -> None:
    document = README_PATH.read_text(encoding="utf-8")
    headings = {line for line in document.splitlines() if line.startswith("## ")}

    assert REQUIRED_HEADINGS <= headings
    assert REQUIRED_LINKS <= _links(document)
    assert "docs/stages" not in document
    assert "docs/traffic_models" not in document
    assert "docs/similarity_methods" not in document
    assert "docs/genetic_models" not in document


def test_every_relative_readme_link_resolves() -> None:
    document = README_PATH.read_text(encoding="utf-8")

    for link in sorted(_links(document)):
        if "://" in link or link.startswith("#"):
            continue
        relative_target = unquote(link.split("#", maxsplit=1)[0])
        assert (REPOSITORY_ROOT / relative_target).is_file(), link
```

- [ ] **Step 2: Run the focused test and verify RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_readme.py
```

Expected: failure because root `README.md` does not exist.

- [ ] **Step 3: Write the detailed root README**

Create `README.md` with:

- a one-paragraph purpose and MVP scope;
- the workflow `experiment.toml -> preflight -> capture -> fit -> generate -> compare` and `run` orchestration;
- Linux/WSL2, CPython 3.12.3, uv, Docker Engine, Compose v2, systemd user manager, disk, memory, and Internet
  requirements with links to `architecture/DEVELOPMENT.md` and `architecture/CAPTURE.md`;
- locked installation commands and Docker access checks;
- configuration-copy guidance that never presents `example.invalid` as runnable Internet input;
- config-only preflight, full preflight, and complete `run` quick-start commands;
- short explanations and exact `architecture/SYSTEM.md` links for all six CLI stages;
- run-directory artifact descriptions and resume/reuse behavior;
- direct links to every required model/method document;
- bounded focused, fast, coverage, Docker, and opt-in Internet test commands, while directing exact policy to
  `architecture/TESTING.md`;
- links to Validation Study `REPORT.md`, architecture overview, system, capture, development, testing, and Roadmap;
- explicit research-prototype limitations: no replay, payload/application modelling, distributed execution,
  multi-user service, neural/diffusion model, or security subsystem.

- [ ] **Step 4: Run focused README tests and static checks**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/unit/test_readme.py
uv run --locked ruff format --check tests/unit/test_readme.py
uv run --locked ruff check tests/unit/test_readme.py
uv run --locked pyright tests/unit/test_readme.py
git diff --check
```

Expected: two focused tests pass; Ruff and Pyright report no findings.

- [ ] **Step 5: Run the complete fast regression gate**

```bash
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not integration and not docker and not internet"
```

Expected: the complete fast suite passes with the new README contract included.

- [ ] **Step 6: Review and commit**

Verify every example command against `trafficlab --help`, scan README prose for placeholders and lines over 120
characters, require a clean staged diff containing only `README.md` and `tests/unit/test_readme.py`, then commit:

```bash
git add README.md tests/unit/test_readme.py
git diff --cached --check
git commit -m "docs: add project README"
```
