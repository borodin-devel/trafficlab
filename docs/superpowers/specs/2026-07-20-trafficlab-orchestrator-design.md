# Trafficlab Orchestrator Design

## Goal

Add `architecture/apps/99_trafficlab/` as the owner for the unprefixed
`trafficlab` command. It launches a single app or a complete experiment with
explicit paths, predictable run directories, and recorded lineage.

## Commands

```text
trafficlab run <app> [app arguments]
trafficlab experiment <start-stage> [experiment arguments]
```

`run` launches one app instance. `experiment` starts at the selected stage and
runs each later applicable stage:

- `experiment preflight`: preflight, capture, convert, genetic training;
- `experiment capture`: capture, convert, genetic training;
- `experiment genetic_training`: genetic training only.

The architecture path prefix `99_` is not part of the command, module,
configuration, or run-directory name.

## Run Roots and Layout

By default, `trafficlab` resolves `run/` from its launch current directory.
`--run-root PATH` overrides it. Run names use readable, lexically chronological
UTC form with a collision suffix:

```text
capture_2026-07-20_14-25-30_Z_a1b2c3/
experiment_2026-07-20_14-25-30_Z_a1b2c3/
```

Single app runs create:

```text
run/capture_<timestamp>_Z_<suffix>/
run/genetic_training_<timestamp>_Z_<suffix>/
```

Experiments create:

```text
run/experiment_<timestamp>_Z_<suffix>/
  preflight/
  capture/
  convert/
  genetic_training/
```

The orchestrator creates these directories before launching a child and passes
its assigned absolute `--output-dir`. A child does not choose a different run
directory.

## Paths, Contracts, and Current Directory

Every child inherits the current directory from which `trafficlab` was
launched. The orchestrator resolves every input and output path to an absolute
path before launch.

A producer writes its contract artifact in its assigned output directory. The
orchestrator validates it and passes its exact absolute path to the next app
using named arguments. No app searches for a latest run or guesses an input.

## Configuration

The orchestrator accepts TOML settings by explicit `--config-file PATH`.
Command-line values override TOML settings according to the shared
configuration rules. The orchestrator writes resolved arguments and settings to
its own `launch.toml`, together with child commands, stage paths, contracts,
and status.

## Tests and Failure

Automated tests explicitly use `<launch directory>/test_run/` as `--run-root`.
`run/` and `test_run/` are Git-ignored. Production launches never use
`test_run/`.

If any experiment stage fails, the orchestrator stops immediately, runs no
later stages, and preserves the full experiment directory. Its top-level status
and lineage TOML records the failed stage, child command, resolved paths, and
available contracts. No automatic artifact deletion occurs.

## Scope

This design adds architecture documents only. It does not implement the
command, change existing app interfaces, add a configuration template, or add
tests yet.
