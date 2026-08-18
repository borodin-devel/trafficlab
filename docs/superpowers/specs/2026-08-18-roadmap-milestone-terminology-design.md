# Roadmap Milestone Terminology Rename Design

## Purpose

Replace opaque numbered Roadmap milestone references outside `architecture/`
and `docs/` with names that state the capability they describe. Preserve the
architecture's legitimate generic lifecycle terminology and preserve ordinal
`r<number>` labels when they actually identify a repetition, attempt, or
release.

This is a terminology-only migration. It must not change runtime behavior,
scientific calculations, accepted evidence, public data meaning, or the
authoritative architecture documents.

## Scope

The migration covers tracked filenames, Python identifiers, Docker fixture
tags, human-facing prose, generated deterministic fixtures, and test data
outside `architecture/` and `docs/`.

The following are intentionally out of scope:

- all existing content under `architecture/` and `docs/`;
- generic uses of `phase` that describe a real but non-numbered lifecycle
  division, such as prerequisite, collection, capture, fitting, or cleanup;
- schema keys and APIs such as `phase_capture_image`, `_begin_phase_attempt`,
  and capture failure phase, because they identify generic lifecycle phases
  rather than concealing a named Roadmap milestone;
- `r<number>` tokens that context proves are repetition, whole-study attempt,
  or release ordinals;
- Git history and immutable accepted evidence whose ordinal study identifiers
  already have precise attempt semantics.

## Required contextual names

Numbered Roadmap aliases outside the exempt directories map to these capability
names:

| Numbered alias | Required terminology |
| --- | --- |
| Project milestone 1 | project configuration and local preflight, or prepared experiment run when referring to artifact publication |
| Project milestone 2 | canonical trace and offline similarity |
| Project milestone 3 | Docker preflight and reference capture |
| Project milestone 4 | traffic-model generation |
| Project milestone 5 | genetic fitting and checkpoint resume |
| Project milestone 7 | real-program validation study |

The replacement may be shortened where the surrounding noun already supplies
the missing context, but it must remain unambiguous. For example, a generator
dedicated to similarity fixtures may say `similarity fixture`, while a Docker
image tag may say `docker-capture-test`.

## Concrete migration

The Docker integration image tags change from the milestone-coded
`phase3-test` suffix to `docker-capture-test`. The checked Compose fixture and
every owning test must use the same exact tags.

Similarity fixture generator prose, temporary-directory prefixes, tests, and
errors use `similarity` or `canonical trace and similarity` instead of the
second milestone number.

Traffic-model fixture generator prose, tests, and errors use
`traffic-model generation` or `model fixture` instead of the fourth milestone
number.

Genetic-fitting fixture prose and errors use `genetic fitting` or
`genetic-fitting fixture` instead of the fifth milestone number.

Validation-study prose, reports, tests, and retained implementation reports use
`real-program validation study` instead of the seventh milestone number.

Prepared-run artifact prose uses `prepared experiment run` instead of the first
milestone number.

Synthetic test paths that exist only to exercise the generic phase-name layout
checker remain generic and lose misleading numeric suffixes. The checker itself
remains because generic lifecycle phase terminology is valid and its filename
policy is not a Roadmap milestone alias.

## Ordinal labels

Every retained `r<number>` occurrence must belong to one of these categories:

- a numbered repeated capture or fresh simulation for the same workload;
- a numbered whole-study attempt, including failed and accepted attempts;
- a numbered release where release context is explicit.

Training paths such as `r1`, `r2`, and `r3`, study IDs such as `r6`, `r20`, and
`r21`, and test-only successive study IDs therefore remain unchanged. A local
identifier may be expanded when its ordinal role is otherwise unclear, but no
data migration is required solely to spell out a proven ordinal.

## Testing and verification

Use test-driven development for changed behavior-bearing names and fixtures:

1. Add or update focused assertions for the descriptive Docker tags, generator
   messages, fixture names, and test identifiers.
2. Confirm the focused tests fail against the numbered aliases.
3. Apply the smallest contextual source and fixture changes.
4. Confirm focused tests pass and deterministic fixture checks remain exact.
5. Run an exhaustive case-insensitive tracked-content and tracked-path scan
   outside `architecture/` and `docs/` for numbered Roadmap aliases. Review
   every remaining match individually; only generic lifecycle terminology and
   proven ordinal `r<number>` labels may remain.
6. Run global Ruff formatting and lint, strict Pyright, the complete bounded
   non-Docker test suite, branch-aware coverage, and available Docker tests.
7. Request independent review and resolve every Critical or Important finding.

The accepted validation-study bundle must continue to pass its offline audit.
No Internet collection or accepted-evidence replacement is required because
the generic lifecycle field and ordinal study ID remain valid.
