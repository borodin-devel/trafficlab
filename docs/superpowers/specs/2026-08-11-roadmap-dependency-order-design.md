# Roadmap Dependency Order Design

**Date:** 2026-08-11

## Purpose

The current roadmap requires PCAPNG parsing before its parser phase, uses stage
commands before the phase that implements them, and asks the Docker-only `run`
workflow to operate without Docker. This amendment makes every phase consume only
capabilities completed by earlier phases. It changes roadmap ownership, not the
MVP algorithm or artifact scope.

## Configuration-Only Preflight

Phase 1 needs a useful CLI configuration check without depending on Docker. The
preflight command therefore supports:

```text
trafficlab preflight EXPERIMENT --config-only
```

This mode performs only local checks:

- parse the TOML and reject unknown fields;
- validate types, values, bounds, weights, argv, and timeout relationships;
- validate configured local paths and mount sources;
- validate the output path and required free space;
- print the same effective configuration produced by the Python configuration
  API.

It does not call Docker, inspect or pull images, create probe resources, or test
DNS/Internet connectivity. It succeeds when all local checks pass even when
Docker is unavailable. It remains a permanent user feature, not a temporary
implementation stub.

Plain `trafficlab preflight EXPERIMENT` performs the local checks followed by the
Docker, Compose, image, mount, DNS, and network checks. `capture` and `run` always
require this full preflight; they cannot select configuration-only behavior. The
flag is invalid for every other subcommand.

## Command Ownership

Each public command becomes usable in the first phase that has all of its
dependencies:

| Phase | Newly completed CLI behavior | Existing dependencies |
|---|---|---|
| 1 | `preflight --config-only` | Project and experiment parser |
| 2 | `compare` | PCAPNG boundary, canonical trace, similarity methods |
| 3 | full `preflight`, `capture` | Phase 2 parser plus Docker environment |
| 4 | `generate` | PCAPNG renderer and three traffic models |
| 5 | `fit` | Models, similarities, and genetic search |
| 6 | `run` | Every completed stage command/function |

Phase 6 no longer claims to implement all commands. It composes already working
in-process stage functions, validates outputs before reuse, and adds complete
failure/resume integration evidence.

## Revised Phase Order

### Phase 1 — Project, configuration, and local preflight

Create the package, CLI, fixed development environment, experiment parser,
effective-configuration snapshot behavior, local checks, and
`preflight --config-only`. Tests compare CLI and Python API results without
Docker.

### Phase 2 — PCAPNG, canonical trace, and similarity

Implement strict `capture.json`, Ethernet PCAPNG parsing/rendering, canonical
events, direction classification, all four similarity methods, aggregation, and
`compare`. All evidence uses checked-in fixtures and runs without Docker.

### Phase 3 — Docker preflight and reference capture

Implement Compose topology, full preflight, `capture`, Internet access,
directional `eth0` capture, timeouts, artifact publication, and cleanup. It uses
the Phase 2 parser to validate real captures and inspect deterministic test
traffic.

### Phase 4 — Three traffic models and generation

Implement the common model interface, Poisson empirical, Markov Renewal,
two-state MMPP, serialization, and `generate`. Checked-in fitted-model fixtures
exercise the command before genetic fitting exists.

### Phase 5 — Heterogeneous genetic fitting

Implement the basic genetic strategy, competing model families, bounded
candidate evaluation, checkpoint/resume, winner publication, and `fit`. It uses
models and similarity methods completed in earlier phases.

### Phase 6 — Complete orchestration

Implement `run` as:

```text
full preflight -> capture -> fit -> generate -> compare
```

Add stage-output validation, reuse, artifact preservation, and end-to-end failure
evidence. Two different integration scopes are named accurately:

- the **offline analytical pipeline** runs `fit -> generate -> compare` against a
  checked-in reference fixture without Docker;
- the **complete run pipeline** calls `run` with the deterministic Docker capture
  workload.

There is no “full pipeline without Docker” claim because full `run` necessarily
includes capture.

### Validation Study — Validation on real programs

The real-workload study remains unchanged and starts only after the complete
workflow is reliable.

## Phase Completion Rule

Every phase's goal, deliverables, tests, and done condition must mention only:

- behavior implemented in that phase;
- artifacts and interfaces produced by an earlier phase;
- test infrastructure available at that point.

Later phases may extend a command, as full preflight extends configuration-only
preflight, but they must not retroactively make an earlier done condition true.
The roadmap remains one ordered seven-phase document.

## Testing Changes

The testing architecture distinguishes:

- configuration-only CLI/API integration without Docker;
- fixture-based PCAPNG and comparison integration without Docker;
- Docker capture integration using the already implemented parser;
- model generation from checked-in fitted-model fixtures;
- genetic fitting and exact checkpoint resume;
- offline analytical pipeline integration;
- complete Docker-backed `run` integration.

Tests never call `run` while expecting capture to be skipped. Tests that need to
start at a saved reference invoke the individual analytical stages.

## Architecture Documents Changed

Implementation updates:

- `architecture/SYSTEM.md` to define `preflight --config-only` and clarify that
  stage commands are implemented incrementally but share in-process functions;
- `architecture/TESTING.md` to distinguish offline analytical and complete run
  integration paths;
- `architecture/ROADMAP.md` to reorder Phases 2 and 3, assign command ownership,
  and make every completion condition dependency-correct.

No algorithm document, traffic model, similarity formula, capture topology,
artifact list, or public command name changes in this amendment.
