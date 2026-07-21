# Trafficlab Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document the `trafficlab` orchestrator app and ignore its run roots.

**Architecture:** `apps/99_trafficlab/README.md` owns unprefixed command behavior, launch roots, stage layouts, explicit paths/contracts, configuration, and failure lineage. `.gitignore` excludes `run/` and `test_run/` only.

**Tech Stack:** Markdown and Git.

## Global Constraints

- Architecture documentation only; no command implementation, template, or test.
- `99_` is only an architecture ordering prefix; runtime command is `trafficlab`.
- Child apps receive explicit absolute paths and retain the launch current directory.
- Experiment failure preserves artifacts and stops later stages.

---

### Task 1: Add orchestrator owner and run-root ignores

**Files:**

- Create: `architecture/apps/99_trafficlab/README.md`
- Modify: `architecture/README.md`
- Modify: `.gitignore`

- [ ] **Step 1: Create the orchestrator owner**

Document `trafficlab run <app>` and `trafficlab experiment <start-stage>`;
stage expansion; readable UTC sortable run names; run-root override; single-run
and experiment layouts; orchestrator-owned output directories; explicit
absolute contract paths; inherited launch current directory; explicit TOML
configuration; `launch.toml`; test root; and failure status/lineage.

- [ ] **Step 2: Add the architecture reading link**

Add `99 trafficlab` after `60 similarity evaluation` in the root architecture
reading order. Keep the numeric-prefix rule unchanged.

- [ ] **Step 3: Ignore runtime roots**

Add root-relative `run/` and `test_run/` entries to `.gitignore` without
ignoring unrelated directories with those names below other paths.

- [ ] **Step 4: Verify and commit**

```bash
git diff --check
python3 - <<'PY'
from pathlib import Path
import re
for path in Path('architecture').rglob('*.md'):
    for target in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', path.read_text()):
        assert (path.parent / target).resolve().is_file(), (path, target)
print('All architecture relative links resolve.')
PY
git check-ignore -v run/example test_run/example
git add architecture/apps/99_trafficlab/README.md architecture/README.md .gitignore
git commit -m "docs(architecture): add trafficlab orchestrator"
```
