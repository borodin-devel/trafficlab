# Markov Registry Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register `markov_renewal` directly in the developing architecture and remove its premature amendment.

**Architecture:** The shared traffic-model document is the active registry while the architecture is being written. The Markov Renewal owner links to that registry directly. No model behavior or TOML schema changes.

**Tech Stack:** Markdown, Git, Python 3.12 standard-library `tomllib` for checking documentation examples.

## Global Constraints

- Do not change Markov Renewal behavior or its model-file schema.
- Do not create or retain an architecture amendment.
- Preserve valid relative Markdown links.
- Validate all four embedded TOML examples in the model owner.

---

### Task 1: Move Markov Renewal registration into the active registry

**Files:**

- Delete: `architecture/amendments/001_markov_renewal_registry.md`
- Modify: `architecture/traffic_models/README.md`
- Modify: `architecture/traffic_models/markov_renewal/README.md`

**Consumes:** The approved correction design in `docs/superpowers/specs/2026-07-20-markov-registry-correction-design.md`.

**Produces:** A registry table row for `markov_renewal`, a model owner that links directly to the registry, and no Amendment 001 file or reference.

- [ ] **Step 1: Remove the premature amendment**

Run:

```bash
git rm architecture/amendments/001_markov_renewal_registry.md
```

Expected: Git marks exactly the amendment document for deletion.

- [ ] **Step 2: Add the model to the common registry**

In `architecture/traffic_models/README.md`, append this table row after the existing selectable models:

```markdown
| `markov_renewal` | [Markov Renewal](markov_renewal/README.md) | Observed IAT/frame-length pairs with first-order state transitions |
```

- [ ] **Step 3: Remove the amendment dependency from the model owner**

Replace its opening registry sentence with:

```markdown
The model is selectable in [Traffic Models](../README.md). Read the
[architecture map](../../README.md) and the common traffic-model rules before
this owner.
```

Remove the Amendment 001 sentence in its `## Reading` section. Keep the common traffic-model link and all application-owner links.

- [ ] **Step 4: Verify documentation invariants**

Run:

```bash
git diff --check
! test -e architecture/amendments/001_markov_renewal_registry.md
! rg -n 'Amendment 001|001_markov_renewal_registry' architecture
python3 - <<'PY'
from pathlib import Path
import re
import tomllib

path = Path('architecture/traffic_models/markov_renewal/README.md')
text = path.read_text()
links = re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', text)
assert all((path.parent / link).resolve().is_file() for link in links)
blocks = re.findall(r'```toml\n(.*?)```', text, flags=re.S)
assert len(blocks) == 4
for block in blocks:
    tomllib.loads(block)
print('Registry links and four TOML examples validated.')
PY
```

Expected: all commands exit zero and Python prints the validation message.

- [ ] **Step 5: Commit the correction**

```bash
git add -A architecture
git commit -m "docs(architecture): register Markov model directly"
```

Expected: the commit deletes the amendment and updates only the two affected architecture documents.
