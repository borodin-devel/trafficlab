# Validation Study Namespace Migration Design

## Purpose

Remove roadmap-phase numbering from the implemented validation study and its
retained examples. The study remains the same scientific protocol; only its
project-facing namespace, paths, identifiers, and documentation names change.

This migration also removes the repository-local `.superpowers/` work-log tree
completely. Those files are implementation leftovers, not project deliverables.

## Approved names

Use one spelling for each context:

- prose: **Validation Study** or **validation study**;
- Python identifiers, module names, and directory names: `validation_study`;
- dated plan/spec filenames, error prefixes, Docker names, and study-ID prefix:
  `validation-study`.

Do not add compatibility aliases for the old names. They would retain the
unwanted namespace and add behavior that the prototype does not need.

## Scope

### Active paths

Rename the legacy numbered-study paths atomically to:

- `examples/validation_study/`;
- `scripts/run_validation_study.py`;
- `tests/unit/test_validation_study.py`;
- `tests/integration/test_validation_study_pipeline.py`;
- `docs/superpowers/specs/2026-08-13-validation-study-design.md`; and
- `docs/superpowers/plans/2026-08-13-validation-study.md`.

Update all tracked references to those paths.

### Runtime and evidence namespace

Apply these semantic mappings consistently outside `architecture/`:

Use these new forms for every legacy numbered-study form:

- `Validation Study` or `validation study` in prose;
- `validation_study` in Python names and paths;
- `validation-study-...` for study IDs;
- `runs/validation_study/` for run roots;
- `validation-study:` for the error prefix;
- `trafficlab-validation-study-...` for Docker names;
- `org.trafficlab.validation-study.study` for the Docker label key; and
- `validation-study.example` for the test-only oracle host.

The migration covers constants, functions, local variables, imports, test
names, CLI examples, JSON strings, TOML paths, report prose, README links,
historical plans/specs outside `architecture/`, and the research-fitness
assessment.

The existing Git commit recorded inside retained evidence remains unchanged.
It identifies the source used for the original experiment and is not a
phase-number namespace.

### Architecture exception

Keep roadmap phase headings and conceptual prose inside `architecture/` as
historical architecture structure. Do not mechanically replace phase headings
there.

Update only the retained study identifier and example-report path in
`architecture/ROADMAP.md` when necessary to keep its evidence reference true
after the rename. This is a referential-integrity correction, not a roadmap
phase rename.

### AGENTS.md

Remove the explicit numbered-phase reference from the mission. Describe the
same deliverable generically as the real-program validation report. Do not
change the mission, autonomy policy, or completion gate otherwise.

### `.superpowers/` removal

Delete `/home/bsa/projects/trafficlab/.superpowers/` completely:

- tracked files are removed from Git;
- ignored reports, ledgers, diffs, and other local contents are removed from
  the working directory;
- the root `.gitignore` gains `/.superpowers/` so the tree is not recreated as
  visible project state.

The tracked deletions are recoverable from Git history. Ignored contents are
not project evidence and are intentionally discarded. No `.superpowers/` path
may remain after the migration.

Preserve the user's existing `TASK.md` ignore rule. Update the old study-work
ignore path to `examples/validation_study/.study-work/` and end `.gitignore`
with a newline.

## Retained evidence migration

The committed prerequisite and result documents remain historical evidence;
the migration must not change scientific observations, scores, seeds, model
parameters, timestamps, tool versions, image identities, or original source
commit.

Rename only namespace-bearing values and then restore all dependent canonical
identities:

1. rename study IDs, run/config/mount/evidence paths, Docker resource names,
   label keys, and recorded command paths;
2. render the three renamed TOML configurations and compute their exact new
   SHA-256 values;
3. update the prerequisite document's configuration-hash mapping;
4. canonically render the renamed prerequisite document and compute its new
   SHA-256 value;
5. update the result document's protocol configuration hashes and prerequisite
   hash;
6. canonically render and strictly parse both JSON documents with the renamed
   production codec;
7. update report prose and commands without changing reported scientific
   values.

No absent raw run artifact is invented or regenerated. The existing
research-fitness assessment continues to state that those artifacts are absent.

The ignored `examples/validation_study/.study-work/` evidence moves with the
example directory. Rename its namespace-bearing directory entries so retained
paths still resolve, but do not rewrite any evidence-file byte. Verify the file
count and multiset of SHA-256 content identities before and after the move.
Historical command output may therefore retain its original wording inside
hashed bytes; it is evidence content, not an active project namespace.

## Implementation sequence

1. Change and rename the owning tests first. Run a bounded focused test and
   observe failure because the new module/namespace does not yet exist.
2. Rename the production script and apply the minimal namespace mappings.
3. Rename the examples and retained identifiers, then recompute dependent
   canonical hashes.
4. Update project documentation and historical non-architecture plans/specs.
5. Make the minimal architecture evidence-reference correction.
6. Remove `.superpowers/` and finalize `.gitignore`.
7. Verify the complete repository and inspect the exact staged set.
8. Commit the verified migration and push local `main` to `origin`.

## Verification

The change is complete only when all of the following hold:

- `git grep` finds no case-insensitive legacy numbered-study token outside
  `architecture/`;
- no tracked filename outside `architecture/` contains an old token;
- `.superpowers/` does not exist and `git check-ignore .superpowers/probe`
  identifies the root ignore rule;
- old active paths are absent and all new paths exist;
- the renamed unit and in-process integration tests pass through
  `scripts/run_bounded.sh`;
- retained prerequisite/results documents strictly parse and canonically
  re-render, and their dependent hashes agree;
- all three deterministic fixture checks pass;
- Ruff formatting/lint and strict Pyright pass;
- the bounded non-Docker/non-Internet branch-aware suite passes with at least
  90% package coverage;
- Docker and Internet selections collect successfully under their renamed
  paths without executing external traffic solely for this naming migration;
- README link tests and `git diff --check` pass;
- the independent review has no Critical or Important finding;
- the staged set contains only the approved rename, documentation updates,
  `.superpowers/` deletions, and `.gitignore`;
- the commit succeeds, the worktree is clean, and `git push origin main`
  advances the configured remote to the verified commit.

## Failure handling

If strict retained-evidence parsing fails, diagnose the first mismatched
namespace or dependent hash and fix only that mapping. Do not weaken the codec,
skip validation, or regenerate scientific values.

If an old token remains, classify it before changing it:

- architecture phase prose is permitted;
- a non-architecture tracked occurrence is a migration defect;
- ignored `TASK.md` is the user's task record and is not part of the Git
  migration;
- `.git/` history and object data are outside the working-tree scope.

If the final push is rejected because the remote advanced, stop rather than
force-pushing or overwriting new remote work. The task authorizes a normal push,
not another destructive remote rewrite.

## Non-goals

- No scientific method, result, configuration value, or workflow change.
- No compatibility shim for the old module, paths, IDs, labels, or error text.
- No new dependency, abstraction layer, migration framework, or security
  subsystem.
- No rewrite of Git history or existing tags.
- No Docker or Internet experiment rerun merely to prove a mechanical rename.
