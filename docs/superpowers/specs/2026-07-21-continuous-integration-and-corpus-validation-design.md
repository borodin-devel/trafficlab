# Continuous Integration and Corpus Validation Design

**Date:** 2026-07-21

**Status:** Approved by the repository architecture and autonomous delivery contract

## Scope

Implement Infrastructure Stage 2, Step 2.1, Substep 2.1.1 from
[`architecture/infrastructure/ROADMAP.md`](../../../architecture/infrastructure/ROADMAP.md).
The increment must make the architecture corpus mechanically verifiable and
run every mandatory local gate for proposed GitHub changes. Runtime capture,
application behavior, publication, and deployment remain out of scope.

The authoritative requirements are INF-IF-001, INF-NFR-001, INF-NFR-002,
INF-ERR-001, INF-TST-001, and INF-AC-002 in the
[Infrastructure SRS](../../../architecture/infrastructure/SRS.md), together
with the rules in
[`architecture/VALIDATION.md`](../../../architecture/VALIDATION.md).

## Resolved Design Decisions

### CI provider

Use GitHub Actions. The repository's only configured remote is GitHub, while a
provider-neutral command interface already exists in `tools/quality.py`.
GitHub Actions therefore adds the smallest provider adapter without moving any
gate semantics into hosted configuration.

Alternatives rejected:

- A provider-neutral script alone would preserve portability but would not
  configure CI or satisfy INF-AC-002 for proposed changes.
- GitLab CI or another hosted provider would introduce an unsupported delivery
  system with no repository evidence.
- A containerized CI image would add image publication and patch-management
  responsibilities without improving the Python 3.12 acceptance criteria.

The infrastructure configuration document scheduled provider selection with
the first configuration set, while the component roadmap schedules actual CI
work in Stage 2. This is a minor sequencing mismatch, not an architectural
conflict: the repository command interface was established in Stage 1 and the
provider adapter is resolved here when it becomes implementable. Original
architecture documents remain unchanged; the durable choice is recorded in
this design and the Stage 2 roadmap evidence.

### Validation implementation

Use a repository-owned, standard-library Python validator. It will expose
small deterministic checks over an immutable snapshot of file paths and text,
with filesystem traversal and CLI output kept at the imperative boundary.

Alternatives rejected:

- Chaining general Markdown, link, naming, and custom roadmap tools would add
  several independently configured dependencies and still require custom
  validation for Trafficlab identifiers, status evidence, and registry rules.
- A link-and-ID-only regex script would meet the narrow acceptance examples but
  would leave the remaining required corpus rules unenforced.

## Architecture Validator

Create `tools/validate_architecture.py`. Its public functional core accepts a
corpus snapshot and returns sorted `ValidationIssue` values. An issue contains
a stable rule code, repository-relative path, optional line number, and a
concise message. No check mutates the corpus, accesses the network, follows
symlinks, or invokes privileged commands.

The imperative CLI will:

1. resolve the requested architecture root beneath the repository root;
2. reject a missing, non-directory, or escaping root;
3. read regular Markdown files as UTF-8 without following symlinks;
4. run the checks in a fixed order;
5. print deterministically sorted diagnostics and return nonzero on any issue.

The checks cover every mechanically enforceable item in
`architecture/VALIDATION.md`:

- uppercase-snake-case Markdown filenames and lowercase-snake-case directory
  names;
- required `README.md`, `SAD.md`, `SRS.md`, and `ROADMAP.md` files in component
  directories;
- unique requirement and acceptance-criterion identifiers across SRS files,
  plus acceptance-criteria and traceability sections;
- exact roadmap hierarchy/status syntax, required fields, non-plan evidence,
  and mechanically decidable parent/child status consistency;
- exactly one central-roadmap link to each component roadmap and exactly one
  back-link from each component roadmap;
- resolvable local Markdown paths and heading fragments;
- complete selectable/unselectable tables for traffic models, similarity
  methods, and genetic strategies, with one documented owner per component;
- no trailing whitespace in architecture Markdown and no obsolete `.gitkeep`
  in a nonempty architecture directory.

Human judgment remains necessary only for semantic traceability quality,
whether a reported bug affects a parent, and whether prose accurately
describes a selectable implementation. The validator still enforces the
observable structure that makes those reviews possible.

Markdown fragments use the repository's GitHub-style heading convention:
formatting punctuation is removed, letters are case-folded, spaces become
hyphens, Unicode letters are retained, and duplicate headings receive numeric
suffixes. External URI schemes are ignored. Relative paths are percent-decoded
and must remain inside the repository before existence and fragment checks.

## Quality Command Integration

Add fixed `whitespace` and `docs` checks to `tools/quality.py` immediately
before `build`:

```text
git diff --check
<active Python> -m tools.validate_architecture architecture
```

`all` remains fail-fast and continues to be the sole complete local/CI gate.
The README will document both checks alongside the existing named checks. The
architecture validator scans the complete committed corpus for trailing
whitespace, while `git diff --check` also enforces the architecture's exact
working-diff requirement. The build gate remains last, so a documentation or
whitespace defect cannot publish a misleading successful build result.

Tests will inject runner failures at the `test` and `build` checks and assert
that `all` returns the exact failure code without invoking later gates. This
provides deterministic failure-path evidence without deliberately corrupting
the working tree or executing untrusted commands.

## GitHub Actions Workflow

Create `.github/workflows/ci.yml` for pull requests and pushes to `main`. It
contains one bounded `quality` job on Ubuntu 24.04 with a timeout and only
`contents: read` permission.

The job will:

1. check out source with credentials disabled;
2. install the verified uv version and Python 3.12 with the official setup-uv
   action;
3. restore only setup-uv's dependency cache keyed from `uv.lock`;
4. run `uv sync --locked --all-groups`;
5. run `uv run --locked python tools/quality.py all`.

Third-party actions are pinned to full release commit SHAs with release-version
comments. No workflow input is interpolated into a shell command, no secrets
or write permissions are requested, and no host or external system is mutated.
The workflow follows GitHub's secure-use guidance and Astral's official uv
integration guidance:

- <https://docs.github.com/en/actions/reference/security/secure-use>
- <https://docs.astral.sh/uv/guides/integration/github/>

## Test Strategy

Unit tests use temporary corpus snapshots and cover each rule family. Required
acceptance fixtures include:

- an existing document that links to a missing local file;
- an existing file with a missing heading fragment;
- two SRS files declaring the same requirement identifier;
- malformed roadmap status and missing required fields;
- missing component owner files and registry membership errors;
- injected `test` and `build` command failures.

Integration tests validate the committed architecture corpus and inspect the
workflow contract: immutable action pins, least privilege, locked sync, and the
single repository-owned aggregate command. The normal full gate must pass from
a clean locked environment. Each injected defect must produce its intended
stable rule code or quality-gate failure.

## Completion and Evidence

After all checks pass, update only roadmap status and evidence records. Stage 2,
Step 2.1, and Substep 2.1.1 become `[DONE]`; the infrastructure component then
has both stages complete. The central roadmap percentage is recomputed from
its seven equal-weight foundation component roadmaps and records the arithmetic
basis. Evidence names the clean-environment gate, fixture counts, corpus file
count, workflow contract checks, and successful package build.
