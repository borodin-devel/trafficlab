# Trafficlab Full Implementation Design

## Status and authority

The user approved full implementation of the existing `architecture/` corpus
and all seven phases in `architecture/ROADMAP.md`. This document chooses an
execution structure; it does not replace the detailed system, capture,
algorithm, or testing contracts.

`AGENTS.md` owns the durable autonomy, Class 1–5 escalation, context-compaction,
and completion rules for this implementation effort.

## Approaches considered

### 1. Dependency-ordered phase delivery — selected

Implement one small typed Python package in the Roadmap's existing order. Each
phase ends with usable CLI behavior, deterministic fixtures, its required tests,
coverage, a coherent commit, and independent review. Later phases consume only
verified interfaces from earlier phases.

This is the recommended approach because capture, stochastic generation, and
genetic fitting share artifacts and observation-window rules. Early integration
keeps those contracts concrete and failures attributable.

### 2. Parallel subsystem construction — rejected

Building capture, models, metrics, and genetics independently could shorten
calendar time, but it would require speculative interfaces and large merges.
That conflicts with a one-person prototype and increases the risk that families
do not share identical traces, windows, seeds, and diagnostics.

### 3. Big-bang end-to-end construction — rejected

Implementing every command before testing the stages would postpone evidence,
make failures difficult to localize, and encourage orchestration abstractions
before their need is known.

## Package shape

The implementation uses `src/trafficlab/` with focused modules and no internal
service framework:

- configuration and artifacts: strict TOML/JSON parsing, effective defaults,
  atomic writes, run directories, and compatibility hashes;
- trace: canonical events, PCAPNG reading/writing, normalization, and direction
  classification;
- similarity: four small mathematical implementations and weighted aggregation;
- models: one protocol plus Poisson empirical, Markov Renewal, and two-state
  MMPP implementations;
- genetics: one basic generational strategy over heterogeneous model families;
- capture: rendered two-service Compose project, preflight, event arbitration,
  deadlines, validation, and bounded cleanup;
- pipeline and CLI: direct in-process composition of `preflight`, `capture`,
  `fit`, `generate`, `compare`, and `run`.

Modules expose typed functions and plain dataclasses or immutable mappings.
There is no dependency-injection framework, plugin framework, process manager,
database, daemon, message bus, or web application.

## Data and command flow

An experiment TOML is parsed once into a validated effective configuration and
snapshotted into a unique run directory. `capture` produces the strict reference
pair. `fit` parses that pair, derives one observation window, evaluates all
enabled families using common trial seeds and all four metrics, and checkpoints
after complete generations. `generate` renders the independently evaluated
winner. `compare` writes component and aggregate diagnostics. `run` invokes the
same stage functions without alternate subprocess protocols.

Every reusable artifact is validated from content rather than filename
existence. Structured artifacts are written atomically. Completed earlier stages
remain reusable after a later failure.

## Reliability and problem handling

Expected invalid input becomes a concise CLI error plus detailed `run.log`
context. Scientific invalidity becomes an explicit candidate diagnostic rather
than an infrastructure exception. Unexpected infrastructure failures abort the
current stage, retain the earlier primary cause, and run bounded cleanup.

Development problems follow `AGENTS.md`: Classes 1–4 are resolved
autonomously with the recommended supported approach; only a genuine Class 5
decision or external authority blocker interrupts the user.

## Tests, examples, and evidence

Each phase adds tests before implementation:

- unit tests for validation, equations, RNG order, repair, arbitration, and
  artifact compatibility;
- in-process integration tests joining real modules with deterministic PCAPNG
  and model fixtures;
- serial Docker integration tests for capture ownership, real packets, failure
  precedence, deadlines, and cleanup;
- an opt-in Internet smoke test and three reproducible real-workload example
  configurations for final validation.

Checked-in examples include minimal experiment TOML files, small Ethernet
PCAPNG/reference metadata pairs, fitted-model JSON, generated PCAPNG, similarity
JSON, and a concise Phase 7 report. Generated run directories remain ignored.

The ordinary non-Docker suite must reach at least 90% branch-aware coverage.
Coverage percentage complements rather than replaces the exact behavioral cases
in `architecture/TESTING.md`.

## Delivery and completion

Work occurs in an isolated Git worktree because this is substantial multi-file
implementation. Each Roadmap phase receives a focused plan slice, TDD execution,
full phase verification, and independent review. Work proceeds automatically to
the next phase.

Completion requires every Roadmap checkbox and `Done when` condition, all
documented quality commands, available Docker tests, reproducible example data,
the Phase 7 evidence report, coverage at or above the threshold, and a clean
final review with no Critical or Important findings.
