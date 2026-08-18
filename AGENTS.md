# Autonomous Implementation Instructions

## Mission

Implement every checked requirement in `architecture/ROADMAP.md`, including
source code, unit tests, in-process integration tests, Docker integration tests,
example configurations, checked-in deterministic fixtures, and the real-program
validation report. Continue until every roadmap phase satisfies its `Done when`
condition and the final verification gate passes.

The approved architecture in `architecture/` is authoritative. Keep the project
a simple one-person research prototype: one Python process, two production
containers for capture, classical models only, no security subsystem, no Node.js
application dependencies, and no speculative infrastructure.

## Output and generated-task identity

- Begin every progress update with `[<integer>%]`, calculated from weighted
  whole-task acceptance criteria rather than elapsed time or message count.
- When completion changes, follow it with `[+<integer>%]` or `[-<integer>%]`;
  every task/subtask start and every percentage change must also include
  `[T: <clock_timestamp>]`.
- In generated task documents, label every logical task, step, stage, or phase
  as `[TASK-<ordinal>-<crc32>]`, `[STEP-<ordinal>-<crc32>]`, or the equivalent
  kind. Do not emit generic unlabeled task or step headings.
- Compute `<crc32>` as eight lowercase hexadecimal digits from the UTF-8
  nanosecond creation timestamp plus document path, kind, and ordinal. Labels
  are immutable; regenerate the timestamp on collision so each label is unique
  within the repository.

## Autonomous decision policy

Classify every development problem by both impact and difficulty:

- **[PROBLEM-C1] — local:** typo, formatting, small test correction, obvious function
  implementation, or deterministic fixture update. Fix immediately.
- **[PROBLEM-C2] — contained:** isolated module bug, ordinary dependency choice,
  validation edge case, or small refactor. Compare credible options, choose the
  recommended conventional solution, implement it, and verify it.
- **[PROBLEM-C3] — cross-module:** interface mismatch, algorithm ambiguity already
  resolvable from the architecture or cited mathematics, or integration failure
  spanning several modules. Diagnose systematically, record the resolution in
  code/tests or the owning architecture document, and continue.
- **[PROBLEM-C4] — environmental or major:** unavailable tool/service, difficult
  reproducibility problem, substantial redesign within approved scope, or a
  persistent failure requiring a different implementation approach. Exhaust
  safe local diagnostics, primary documentation, deterministic substitutes, and
  recommended in-scope alternatives. Implement the best supported solution and
  continue without asking for confirmation.
- **[PROBLEM-C5] — human decision required:** an unresolved contradiction that would
  change the scientific purpose or committed scope; required credentials or
  authority that are not available; an irreversible external action; or a hard
  external blocker for which no safe implementation or valid test substitute
  can satisfy the roadmap. Only [PROBLEM-C5] permits asking the human a question.

The human has pre-approved recommended choices for [PROBLEM-C1] through [PROBLEM-C4]. Do not pause for
routine confirmations, status choices, dependency approval, expected test
failures, implementation preferences, or permission to continue. Do not broaden
scope, push to a remote, publish data, or perform destructive external actions
under this policy.

## Persistence across context compaction

At the start of every resumed or compacted context:

1. Read this file, `architecture/README.md`, `architecture/ROADMAP.md`, and the
   active full-implementation plan under `docs/superpowers/plans/`.
2. Inspect `git status`, recent commits, and the first unchecked roadmap item.
3. Run the narrow verification for the last completed task before changing it.
4. Resume the active task; do not restart completed work or ask whether to
   continue.
5. Commit coherent verified increments so Git history remains the durable work
   log.

If a command times out or a tool response is interrupted, inspect the resulting
state and continue from it. An incomplete tool call is not a reason to stop.

## Implementation discipline

- Follow test-driven development for every behavior: failing test, minimal
  implementation, passing focused test, then refactor.
- Use `apply_patch` for hand-authored edits and uv for all Python dependencies.
- Prefer standard-library code when it stays concise; add small maintained
  Python packages only when they materially reduce risk or complexity.
- Before and during every change, actively check for unnecessary abstraction,
  layers, dependencies, configuration, infrastructure, security features, and
  other overengineering. Remove or avoid them unless an approved requirement or
  demonstrated reliability need justifies the complexity. Optimize first for
  robustness, structural correctness, readability, precision, and reliable
  result output.
- Keep public interfaces typed and run strict Pyright, Ruff, and targeted pytest
  continuously.
- Use deterministic seeds and checked-in small fixtures. Never make ordinary
  tests depend on the public Internet.
- Keep Docker resources uniquely project-scoped and always run bounded cleanup.
- Request independent review after each roadmap phase and fix all Critical and
  Important findings before proceeding.
- Maintain at least 90% branch-aware coverage for the non-Docker Python package.
  Core mathematics, configuration validation, orchestration arbitration, and
  artifact validation must have direct behavioral coverage even when the total
  is already above 90%.
- If a failed unit test identifies a defective function or method, cover 100% of
  that function's executable lines and branches as required by
  `architecture/TESTING.md`.

## Completion gate

Do not stop merely because one phase passes. Stop only after:

- every Roadmap checkbox is implemented and accurately marked;
- all example configurations and deterministic example data are checked in;
- locked sync, format, lint, strict typing, unit, in-process integration,
  coverage, and available Docker integration commands pass;
- required Internet/real-program validation has reproducible evidence or a
  [PROBLEM-C5] external blocker has been demonstrated;
- an independent final review has no Critical or Important findings; and
- the working tree is clean with all implementation commits retained locally.
