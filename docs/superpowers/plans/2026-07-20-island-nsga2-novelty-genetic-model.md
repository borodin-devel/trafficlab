# Island NSGA-II Novelty Genetic Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a selectable island-model NSGA-II genetic strategy with behavior novelty.

**Architecture:** One owner document defines islands, two-objective selection,
behavior descriptors, ring migration, deterministic lineage, failures, and
future offline tests. The genetic-model registry gains the selectable name.

**Tech Stack:** Markdown and Git.

## Global Constraints

- Architecture documentation only; no implementation, configuration template, or test fixture.
- Islands may mix traffic-model types; crossover remains same-type only.
- Similarity and novelty are maximized NSGA-II objectives.
- Probe captures and all future tests are offline and need no elevated privilege.

---

### Task 1: Add the island NSGA-II novelty owner and registry entry

**Files:**

- Create: `architecture/genetic_models/island_nsga2_novelty/README.md`
- Modify: `architecture/genetic_models/README.md`

- [ ] **Step 1: Create the owner**

Define configurable islands, stable IDs, mixed model types, NSGA-II front and
crowding ordering, deterministic probe descriptor and novelty archive,
same-type crossover, mutation/elitism, ring migration, deterministic seeds
and lineage, failures, stopping, limits, and offline test expectations.

- [ ] **Step 2: Register the exact name**

Add `island_nsga2_novelty` to the genetic-model registry with a relative owner link.

- [ ] **Step 3: Verify and commit**

```bash
git diff --check
python3 - <<'PY'
from pathlib import Path
import re
for path in Path('architecture/genetic_models').rglob('*.md'):
    for target in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', path.read_text()):
        assert (path.parent / target).resolve().is_file(), (path, target)
print('All genetic-model relative links resolve.')
PY
git add architecture/genetic_models/island_nsga2_novelty/README.md architecture/genetic_models/README.md
git commit -m "docs(architecture): add island NSGA-II novelty model"
```
