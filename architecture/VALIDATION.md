# Architecture Corpus Validation

## Required Checks

1. Every file stem contains an uppercase ASCII letter and uses uppercase snake
   case with a lowercase extension. Every directory name contains a lowercase
   ASCII letter and uses lowercase snake case.
2. Every application, library, model, method, script, and contract directory
   contains `README.md`, `SAD.md`, and `SRS.md`.
3. Every application and library, plus each independently implementable model,
   method, script, and contract, contains `ROADMAP.md`.
4. Every SRS has stable, unique requirement and acceptance-criterion identifiers,
   acceptance criteria, and traceability links.
5. Every roadmap places one allowed marker in headings shaped as
   `## [PLAN] STAGE`, `### [PLAN] STEP`, and `#### [PLAN] SUBSTEP`; another
   allowed marker may replace `[PLAN]`. Every level records a task or objective,
   deliverable, applicable tests, and completion criteria; substeps also record
   implementation, files, dependencies, outputs, and validation.
6. `project/ROADMAP.md` links every other architecture roadmap exactly once
   with a brief scope description, and every other roadmap links back to it.
   Detailed component tasks and evidence remain in their component roadmaps.
7. Every relative Markdown link resolves to an existing target and every
   fragment resolves to a heading anchor.
8. Registries name only documented selectable components; unresolved stubs are
   marked unselectable.
9. `git diff --check` reports no whitespace errors, and no obsolete `.gitkeep`
   remains in a nonempty directory.

## Roadmap Status Validation

Every `STAGE`, `STEP`, and `SUBSTEP` heading has exactly one status marker
immediately after its Markdown heading prefix and before the hierarchy keyword.
A separate `**Status:**` field is invalid. A status is one of `[PLAN]`,
`[BLKD]`, `[CR_B]`, `[MK_B]`, `[MN_B]`, `[TSTR]`, `[DONE]`, or a right-aligned
whole percentage from `[  1%]` through `[ 99%]`. `[100%]`, zero, fractions,
prose status values, not-applicable markers, and incorrect padding fail
validation.

Status evidence is local to the affected entry:

- `[PLAN]` requires defined work and completion criteria but no production
  implementation.
- A percentage requires implementation evidence and the basis for the estimate.
- `[BLKD]` states the concrete blocking reason immediately after the entry
  description.
- `[CR_B]`, `[MK_B]`, and `[MN_B]` state or link the corresponding bug.
- `[TSTR]` names the missing, incomplete, or failing required tests.
- `[DONE]` cites implementation and passing evidence for every applicable test
  type and completion criterion.

A stage or step reflects its immediate children. A critical, major, or minor
bug that affects the parent takes that precedence; a child blocker that blocks
parent progress yields `[BLKD]`. All children `[PLAN]` yields `[PLAN]`; all
children `[DONE]` yields `[DONE]`. When every child implementation is complete
but at least one has incomplete tests, the parent is `[TSTR]`. Otherwise the
parent uses an implementation percentage derived from its child evidence. Use
the equal-weight arithmetic mean of immediate child estimates (`PLAN = 0`,
`DONE` or implementation-complete `TSTR = 100`, percentage as written) unless
the entry documents a more accurate non-arbitrary weighting; round to the
nearest whole percentage and clamp only the displayed in-progress result to
1–99. A parent never hides an applicable higher-precedence child state behind
a percentage.

## Traceability Review

Reviewers compare each SAD responsibility to its component SRS, then map every
acceptance criterion to a roadmap test and validation step. Each component
roadmap cites its own acceptance criteria. Producer and consumer documents must
name the same artifact and contract owner. When a consolidated document is
split, every still-valid source section must remain in an ordered owner file.
