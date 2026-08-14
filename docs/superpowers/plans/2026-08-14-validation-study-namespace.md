# Validation Study Namespace Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the completed validation study's roadmap-numbered namespace
with the generic Validation Study namespace, remove `.superpowers/` completely,
and push the verified result to `origin/main`.

**Architecture:** Perform one behavior-preserving namespace migration across
the owning script, tests, retained examples, and documentation. Recompute only
hashes whose bytes change, preserve scientific observations and historical
source identity, and add no compatibility aliases. Remove the local work-log
tree in a separate deletion commit, then run one complete verification and
independent review before a normal fast-forward push.

**Tech Stack:** Python 3.12.3, uv, pytest, pytest-xdist, pytest-cov, Ruff,
Pyright, Git, Docker test collection, Bash, canonical JSON/TOML codecs.

## Global Constraints

- Follow `AGENTS.md`, `architecture/TESTING.md`, and the approved design at
  `docs/superpowers/specs/2026-08-14-validation-study-namespace-design.md`.
- Use **Validation Study** in prose, `validation_study` in Python/filesystem
  names, and `validation-study` in external identifiers and dated filenames.
- Preserve all scientific values, timestamps, seeds, tool/image identities,
  results, and the original experiment source commit.
- Do not add aliases, redirects, fallback imports, migrations, dependencies,
  abstractions, or security features.
- Keep architecture phase headings and conceptual prose. Change only the two
  machine references needed to keep the Roadmap evidence binding true.
- Preserve the user's `TASK.md` ignore entry.
- Delete `.superpowers/` from Git and disk and ignore the root path thereafter.
- Run every pytest command through `scripts/run_bounded.sh` with all five
  resource flags and never overlap test processes.
- Use exact paths and bounded commands. Do not use a recursive destructive
  target broader than `/home/bsa/projects/trafficlab/.superpowers`.
- Use a normal fast-forward push. Never force-push or rewrite remote history.

## File Map

### Renamed active files

- `scripts/run_validation_study.py`: protocol, codecs, orchestration, audit,
  and CLI.
- `tests/unit/test_validation_study.py`: exact namespace, codec, command,
  retained-evidence, and orchestration contracts.
- `tests/integration/test_validation_study_pipeline.py`: real in-process study
  extraction contract.
- `examples/validation_study/`: retained configs, prerequisite/result JSON,
  instructions, and report.

### Renamed historical implementation documents

- `docs/superpowers/specs/2026-08-13-validation-study-design.md`
- `docs/superpowers/plans/2026-08-13-validation-study.md`

### Content-only updates

- `.gitignore`
- `AGENTS.md`
- `README.md`
- `architecture/ROADMAP.md`
- `docs/RESEARCH_FITNESS_ASSESSMENT.md`
- all tracked plans/specs outside `architecture/` that name the legacy study
  namespace or paths

### Removed files

- every tracked and ignored path beneath repository-root `.superpowers/`

---

### Task 1: Rename study code, tests, examples, and documentation

**Files:**

- Rename: the six active/historical paths listed in the File Map
- Modify: all tracked non-architecture files containing the legacy namespace
- Modify: `architecture/ROADMAP.md`
- Modify: `.gitignore`

**Interfaces:**

- Consumes: the existing fixed study-script API and retained JSON schema
  version 1.
- Produces: importable `scripts.run_validation_study`, generic paths and IDs,
  canonical prerequisite/results bytes, and zero legacy tokens outside
  `architecture/`.

- [ ] **Step 1: Confirm the exact starting state**

Run:

```bash
set -euo pipefail
test "$(git rev-parse --show-toplevel)" = "/home/bsa/projects/trafficlab"
test "$(git diff --cached --name-only | wc -l)" -eq 0
test "$(git status --porcelain=v1 --untracked-files=all)" = " M .gitignore"
git diff -- .gitignore
git log --oneline -3
```

Expected: only the user's unstaged `.gitignore` change is present; it adds
`TASK.md`. Do not discard or overwrite it.

- [ ] **Step 2: Define the legacy token without adding it literally to new docs**

Run in the same shell used for the mechanical migration:

```bash
legacy_number=7
legacy_compact="phase${legacy_number}"
legacy_hyphen="phase-${legacy_number}"
legacy_prose="Phase ${legacy_number}"
legacy_prose_hyphen="Phase-${legacy_number}"
export legacy_number legacy_compact legacy_hyphen legacy_prose legacy_prose_hyphen
```

Expected: the variables resolve the existing namespace while this plan itself
remains free of deprecated literal spellings.

- [ ] **Step 3: Rename the owning tests first and update their expectations**

Run:

```bash
git mv "tests/unit/test_${legacy_compact}_study.py" \
  tests/unit/test_validation_study.py
git mv "tests/integration/test_${legacy_compact}_study_pipeline.py" \
  tests/integration/test_validation_study_pipeline.py

LEGACY_COMPACT="$legacy_compact" \
LEGACY_HYPHEN="$legacy_hyphen" \
LEGACY_PROSE="$legacy_prose" \
LEGACY_PROSE_HYPHEN="$legacy_prose_hyphen" \
perl -pi -e '
  BEGIN {
    $compact = $ENV{"LEGACY_COMPACT"};
    $hyphen = $ENV{"LEGACY_HYPHEN"};
    $prose = $ENV{"LEGACY_PROSE"};
    $prose_hyphen = $ENV{"LEGACY_PROSE_HYPHEN"};
  }
  s/run_\Q$compact\E_study/run_validation_study/g;
  s/test_\Q$compact\E_study/test_validation_study/g;
  s/\Q$prose\E validation study/Validation Study/g;
  s/\Q$prose_hyphen\E/validation-study/g;
  s/\Q$prose\E/Validation Study/g;
  s/\Q$hyphen\E-validation/validation-study/g;
  s/\Q$hyphen\E/validation-study/g;
  s/\Q$compact\E-/validation-study-/g;
  s/\Q$compact\E:/validation-study:/g;
  s/\Q$compact\E\.example/validation-study.example/g;
  s/org\.trafficlab\.\Q$compact\E\.study/org.trafficlab.validation-study.study/g;
  s/\Q$compact\E/validation_study/g;
' -- tests/unit/test_validation_study.py \
  tests/integration/test_validation_study_pipeline.py
```

Expected: tests import the new module and assert the new paths, identifiers,
labels, error prefix, helper names, and test names.

- [ ] **Step 4: Run the genuine RED boundary**

Run:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -x -n 0 \
  tests/unit/test_validation_study.py \
  tests/integration/test_validation_study_pipeline.py
```

Expected: collection fails because `scripts.run_validation_study` does not yet
exist. Confirm the bounded scope is inactive and no pytest process remains.

- [ ] **Step 5: Rename all owning paths**

Record the ignored study-work evidence identities before moving the example
directory:

```bash
study_work_count_before=$(find "examples/${legacy_compact}/.study-work" -type f | wc -l)
study_work_digest_before=$(
  find "examples/${legacy_compact}/.study-work" -type f -exec sha256sum {} + |
    awk '{print $1}' | sort | sha256sum | awk '{print $1}'
)
export study_work_count_before study_work_digest_before
```

Rename the tracked owners; the directory rename also carries ignored evidence:

```bash
git mv "scripts/run_${legacy_compact}_study.py" scripts/run_validation_study.py
git mv "examples/${legacy_compact}" examples/validation_study
git mv "docs/superpowers/specs/2026-08-13-${legacy_hyphen}-validation-design.md" \
  docs/superpowers/specs/2026-08-13-validation-study-design.md
git mv "docs/superpowers/plans/2026-08-13-${legacy_hyphen}-validation.md" \
  docs/superpowers/plans/2026-08-13-validation-study.md
```

Rename only namespace-bearing ignored directory entries, deepest first:

```bash
while IFS= read -r -d '' path; do
  parent=$(dirname "$path")
  name=$(basename "$path")
  replacement=${name//$legacy_compact/validation-study}
  test "$replacement" != "$name"
  mv -- "$path" "$parent/$replacement"
done < <(
  find examples/validation_study/.study-work -depth \
    -name "*${legacy_compact}*" -print0
)
```

Prove the evidence bytes did not change:

```bash
study_work_count_after=$(find examples/validation_study/.study-work -type f | wc -l)
study_work_digest_after=$(
  find examples/validation_study/.study-work -type f -exec sha256sum {} + |
    awk '{print $1}' | sort | sha256sum | awk '{print $1}'
)
test "$study_work_count_after" = "$study_work_count_before"
test "$study_work_digest_after" = "$study_work_digest_before"
test -z "$(
  find examples/validation_study/.study-work -type d \
    -name "*${legacy_compact}*" -print -quit
)"
```

Expected: old active paths are absent, all new paths exist, ignored evidence
directory names are generic, and every ignored evidence-file byte is unchanged.

- [ ] **Step 6: Apply the mapping to tracked non-architecture files**

Collect the affected text files after the path moves:

```bash
mapfile -t namespace_files < <(
  git grep -Il -i -E "phase[[:space:]_-]*${legacy_number}" -- \
    ':(exclude)architecture/**' ':(exclude).superpowers/**'
)
printf '%s\n' "${namespace_files[@]}"
test "${#namespace_files[@]}" -gt 0
```

Apply the same ordered mapping used for the tests:

```bash
LEGACY_COMPACT="$legacy_compact" \
LEGACY_HYPHEN="$legacy_hyphen" \
LEGACY_PROSE="$legacy_prose" \
LEGACY_PROSE_HYPHEN="$legacy_prose_hyphen" \
perl -pi -e '
  BEGIN {
    $compact = $ENV{"LEGACY_COMPACT"};
    $hyphen = $ENV{"LEGACY_HYPHEN"};
    $prose = $ENV{"LEGACY_PROSE"};
    $prose_hyphen = $ENV{"LEGACY_PROSE_HYPHEN"};
  }
  s/run_\Q$compact\E_study/run_validation_study/g;
  s/test_\Q$compact\E_study/test_validation_study/g;
  s/\Q$prose\E validation study/Validation Study/g;
  s/\Q$prose_hyphen\E/validation-study/g;
  s/\Q$prose\E/Validation Study/g;
  s/\Q$hyphen\E-validation/validation-study/g;
  s/\Q$hyphen\E/validation-study/g;
  s/\Q$compact\E-/validation-study-/g;
  s/\Q$compact\E:/validation-study:/g;
  s/\Q$compact\E\.example/validation-study.example/g;
  s/org\.trafficlab\.\Q$compact\E\.study/org.trafficlab.validation-study.study/g;
  s/\Q$compact\E/validation_study/g;
' -- "${namespace_files[@]}"
```

Inspect awkward duplicate wording:

```bash
rg -n -i 'validation study.*validation study|validation-study validation' \
  --glob '!architecture/**' . || true
```

Use `apply_patch` for any sentence-level correction found by this inspection.
Also use `apply_patch` to make the `AGENTS.md` mission phrase exactly
`example configurations, checked-in deterministic fixtures, and the
real-program validation report.` Leave all other mission and policy text
unchanged.

- [ ] **Step 7: Update only the Roadmap's machine references**

Run this narrow mechanical replacement without changing phase prose:

```bash
LEGACY_COMPACT="$legacy_compact" perl -pi -e '
  BEGIN { $compact = $ENV{"LEGACY_COMPACT"}; }
  s/\Q$compact\E-20260814-ovh-r3/validation-study-20260814-ovh-r3/g;
  s#examples/\Q$compact\E/REPORT\.md#examples/validation_study/REPORT.md#g;
' -- architecture/ROADMAP.md
```

Expected: the Roadmap heading and conceptual phase prose are unchanged; only
the accepted study ID and report path use the new names.

- [ ] **Step 8: Update `.gitignore` without losing the user change**

Use `apply_patch` to make the relevant tail exactly:

```gitignore
runs/
examples/validation_study/.study-work/
/.superpowers/

TASK.md
```

Ensure the file has one trailing newline.

- [ ] **Step 9: Recompute only namespace-dependent retained hashes**

Run:

```bash
uv run --locked python - <<'PY'
from __future__ import annotations

import hashlib
import json
from pathlib import Path

root = Path.cwd()
study = root / "examples" / "validation_study"
profiles = ("short", "streaming", "bursty")
config_hashes = {
    profile: hashlib.sha256((study / "configs" / f"{profile}.toml").read_bytes()).hexdigest()
    for profile in profiles
}

def canonical(document: object) -> bytes:
    rendered = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return f"{rendered}\n".encode()

prerequisite_path = study / "prerequisites.json"
prerequisite = json.loads(prerequisite_path.read_bytes())
prerequisite["config_sha256"] = config_hashes
prerequisite_content = canonical(prerequisite)
prerequisite_path.write_bytes(prerequisite_content)

result_path = study / "results.json"
result = json.loads(result_path.read_bytes())
protocol = result["protocol"]
protocol["base_config_sha256"] = config_hashes
protocol["prerequisites_sha256"] = hashlib.sha256(prerequisite_content).hexdigest()
result_path.write_bytes(canonical(result))
PY
```

Expected: no scientific observation, score, timestamp, seed, image/tool
identity, artifact digest, or source commit changes.

- [ ] **Step 10: Strictly validate retained evidence with the renamed codec**

Run:

```bash
uv run --locked python - <<'PY'
from pathlib import Path

from scripts import run_validation_study as study

root = Path.cwd()
published = root / "examples" / "validation_study"

prerequisite_content = (published / "prerequisites.json").read_bytes()
prerequisite = study.parse_prerequisite_results(prerequisite_content, repository_root=root)
assert study.render_prerequisite_results(prerequisite) == prerequisite_content

result_content = (published / "results.json").read_bytes()
result = study.parse_study_results(result_content, repository_root=root)
assert study.render_study_results(result) == result_content
PY
```

Expected: both strict parsers and canonical renderers accept exact bytes.

- [ ] **Step 11: Run the focused GREEN boundary**

Run:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 \
  tests/unit/test_validation_study.py \
  tests/integration/test_validation_study_pipeline.py \
  tests/unit/test_readme.py
```

Expected: all selected tests pass. Confirm no pytest or bounded scope remains.

- [ ] **Step 12: Prove the old namespace is absent outside architecture**

Run:

```bash
test -z "$(git ls-files | rg -i "phase([ _-]?${legacy_number})" || true)"
test -z "$(
  git grep -n -I -i -E "phase[[:space:]_-]*${legacy_number}" -- \
    ':(exclude)architecture/**' ':(exclude).superpowers/**' || true
)"
git grep -n -I -i -E "phase[[:space:]_-]*${legacy_number}" -- architecture
```

Expected: only intentional architecture phase headings/prose remain; its
machine study ID and report path are generic.

- [ ] **Step 13: Run scoped static checks**

Run:

```bash
uv run --locked ruff format --check \
  scripts/run_validation_study.py \
  tests/unit/test_validation_study.py \
  tests/integration/test_validation_study_pipeline.py
uv run --locked ruff check \
  scripts/run_validation_study.py \
  tests/unit/test_validation_study.py \
  tests/integration/test_validation_study_pipeline.py
uv run --locked pyright \
  scripts/run_validation_study.py \
  tests/unit/test_validation_study.py \
  tests/integration/test_validation_study_pipeline.py
git diff --check
```

Expected: all commands exit zero.

- [ ] **Step 14: Commit the namespace migration**

Inspect and stage all namespace changes while excluding `.superpowers/`:

```bash
git status --short
git diff --stat
git diff --name-status
git add -- . ':(exclude).superpowers/**'
test -z "$(git diff --cached --name-only | rg '^\.superpowers/' || true)"
git diff --cached --check
```

Commit with the migration context required by the commit-message policy:

```bash
commit_body="Replace roadmap-numbered paths and identifiers without compatibility aliases.
Retained scientific values stay unchanged; only namespace-dependent hashes are recomputed."
git commit -m "refactor: rename validation study namespace" -m "$commit_body"
```

Expected: the commit succeeds; `.superpowers/` remains for Task 2 and there are
no other unstaged changes.

---

### Task 2: Remove `.superpowers/` from Git and disk

**Files:**

- Delete: every path under repository-root `.superpowers/`
- Verify: root `.gitignore` contains `/.superpowers/`

**Interfaces:**

- Consumes: the exact repository root and root ignore rule from Task 1.
- Produces: no tracked, ignored, untracked, or on-disk `.superpowers/` entry.

- [ ] **Step 1: Resolve and validate the destructive target**

Run:

```bash
set -euo pipefail
repository_root=$(git rev-parse --show-toplevel)
target="$repository_root/.superpowers"
test "$repository_root" = "/home/bsa/projects/trafficlab"
test "$target" = "/home/bsa/projects/trafficlab/.superpowers"
test -d "$target"
test ! -L "$target"
tracked_count=$(git ls-files -- .superpowers | wc -l)
total_count=$(find "$target" -type f | wc -l)
printf 'tracked=%s total=%s\n' "$tracked_count" "$total_count"
```

Expected at the assessed boundary: 8 tracked files and 178 total files. If the
counts differ, print the exact inventory and confirm all entries are within the
validated target before continuing.

- [ ] **Step 2: Stage tracked deletions and remove ignored remnants**

Run only after Step 1 passes:

```bash
git rm -r -- .superpowers
rm -rf -- "/home/bsa/projects/trafficlab/.superpowers"
```

This destructive action is explicitly approved by the user. Tracked bytes are
recoverable from Git history; ignored work logs are intentionally discarded.

- [ ] **Step 3: Prove complete absence and durable ignoring**

Run:

```bash
test ! -e /home/bsa/projects/trafficlab/.superpowers
test -z "$(git ls-files -- .superpowers)"
test -z "$(git ls-files --others --exclude-standard -- .superpowers)"
test "$(git diff --cached --diff-filter=D --name-only -- .superpowers | wc -l)" -eq 8
git check-ignore -v --no-index .superpowers/probe
```

Expected: the path is absent, all eight tracked deletions are staged, and the
root `/.superpowers/` rule is reported.

- [ ] **Step 4: Commit the deletion**

Run:

```bash
test -z "$(git diff --cached --name-only | rg -v '^\.superpowers/' || true)"
git diff --cached --check
git commit -m "chore: remove superpowers work logs"
git show --check --stat --oneline HEAD
```

Expected: the commit deletes only the eight formerly tracked work-log files;
the ignored remainder is absent from disk.

---

### Task 3: Run final gates and independent review

**Files:**

- Verify: entire tracked repository
- Modify only if required: the exact owner of a verified Critical or Important
  finding

**Interfaces:**

- Consumes: the two migration commits from Tasks 1 and 2.
- Produces: fresh static, deterministic-fixture, behavioral, coverage,
  collection, and review evidence for the final commit range.

- [ ] **Step 1: Confirm no runner or memory pressure exists**

Run:

```bash
set -euo pipefail
test -z "$(pgrep -x pytest || true)"
test -z "$(pgrep -x py.test || true)"
systemctl --user list-units --all --plain --no-legend 'trafficlab-test-*.scope' || true
free -h
```

Expected: no pytest process or active test scope, at least 2 GiB available
memory, and zero swap use before each broad command.

- [ ] **Step 2: Run locked dependency and repository static gates**

Run:

```bash
uv sync --locked --all-groups
uv lock --check
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
git diff --check
```

Expected: all exit zero and `uv.lock` remains unchanged.

- [ ] **Step 3: Run deterministic fixture checks**

Run:

```bash
uv run --locked python scripts/generate_phase2_fixtures.py --check
uv run --locked python scripts/generate_model_fixtures.py --check
uv run --locked python scripts/generate_fit_fixtures.py --check
```

Expected: all checked-in bytes are unchanged.

- [ ] **Step 4: Run the non-external branch-aware suite once**

Run:

```bash
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -n 4 --dist worksteal \
  --cov=trafficlab --cov-branch --cov-report=term-missing \
  -m "not docker and not internet"
```

Expected: zero failures and at least 90% branch-aware package coverage. Confirm
the scope is inactive and no pytest process remains before Step 5.

- [ ] **Step 5: Collect external selections without running them**

Run sequentially:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 -m docker --collect-only

scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 -m internet --collect-only
```

Expected: both selections collect successfully. Collection launches no Docker
container and makes no public Internet request.

- [ ] **Step 6: Run repository invariants and final status checks**

Run:

```bash
legacy_number=7
test -z "$(git ls-files | rg -i "phase([ _-]?${legacy_number})" || true)"
test -z "$(
  git grep -n -I -i -E "phase[[:space:]_-]*${legacy_number}" -- \
    ':(exclude)architecture/**' || true
)"
test ! -e .superpowers
test -z "$(git ls-files -- .superpowers)"
git check-ignore -v --no-index .superpowers/probe
test -f examples/validation_study/REPORT.md
test -f scripts/run_validation_study.py
test -f tests/unit/test_validation_study.py
test -f tests/integration/test_validation_study_pipeline.py
git status --short --branch
```

Expected: all invariants pass and the working tree is clean.

- [ ] **Step 7: Request independent review**

Give a fresh reviewer:

- the approved design and this plan;
- the base commit immediately before Task 1 and current `HEAD`;
- the full diff and gate outputs;
- old-token and `.superpowers/` absence evidence; and
- retained JSON/TOML hash-validation evidence.

Ask the reviewer to inspect for incomplete namespace changes, broken imports or
links, accidental scientific-value changes, stale dependent hashes, excessive
architecture edits, incomplete work-log deletion, compatibility aliases,
unnecessary complexity, and unsafe push behavior.

Expected: zero Critical and zero Important findings.

- [ ] **Step 8: Fix each valid Critical or Important finding with TDD**

For each finding:

1. reproduce it with the narrowest bounded failing test or invariant command;
2. make the smallest owning correction with `apply_patch`;
3. cover 100% of any defective function's executable lines and branches;
4. rerun the focused test, affected suite, static gates, and any superseded
   broad gate;
5. commit the correction with a terse conventional message; and
6. obtain a clean rereview.

Do not implement unverified suggestions or unrelated cleanup.

---

### Task 4: Push the verified main branch

**Files:**

- Modify: remote `refs/heads/main` only through a normal push
- Preserve: remote tag `MVP_1`

**Interfaces:**

- Consumes: clean local `main`, clean independent review, configured `origin`.
- Produces: remote `main` exactly at the verified local `HEAD`.

- [ ] **Step 1: Verify remote identity and fast-forward safety**

Run:

```bash
set -euo pipefail
test "$(git branch --show-current)" = main
test -z "$(git status --porcelain=v1 --untracked-files=all)"
test "$(git remote get-url origin)" = "git@github.com:borodin-devel/trafficlab.git"
git fetch origin main --tags
git merge-base --is-ancestor origin/main HEAD
local_head=$(git rev-parse HEAD)
remote_before=$(git rev-parse origin/main)
printf 'local=%s remote-before=%s\n' "$local_head" "$remote_before"
```

Expected: local `main` is a strict fast-forward of `origin/main`. If not, stop;
do not merge, rebase, force-push, or overwrite new remote work automatically.

- [ ] **Step 2: Push normally**

Run:

```bash
git push origin main
```

Expected: the normal push succeeds without `--force`.

- [ ] **Step 3: Verify the remote commit and preserved tag**

Run:

```bash
local_head=$(git rev-parse HEAD)
remote_head=$(git ls-remote origin refs/heads/main | awk '{print $1}')
tag_target=$(git ls-remote origin 'refs/tags/MVP_1^{}' | awk '{print $1}')
test "$remote_head" = "$local_head"
test "$tag_target" = "f60ad3407f7713b2cfb51158e737793fbbbb8789"
git status --short --branch
```

Expected: remote `main` equals verified local `HEAD`, `MVP_1` still resolves to
the single-root commit, and the working tree is clean.
