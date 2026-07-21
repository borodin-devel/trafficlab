# Trafficlab Orchestrator Software Architecture Document

## Role

`trafficlab` is the unprefixed command that launches one application or an
experiment. The `99_` directory prefix is architecture reading order only; it
is not part of a runtime name.

## Commands

```text
trafficlab run <app> [app arguments]
trafficlab experiment <start-stage> [experiment arguments]
```

`run` launches exactly one application. `experiment` starts at one stage and
runs every later applicable stage:

| Start stage | Stages |
| --- | --- |
| `preflight` | preflight, capture, convert, genetic training |
| `capture` | capture, convert, genetic training |
| `genetic_training` | genetic training only |

An experiment beginning at `preflight` receives an explicit
`--capture-request PATH`, passes it to preflight, and passes that same path plus
the resulting readiness path to capture. An experiment beginning at `capture`
requires explicit `--capture-request PATH --readiness PATH`. It neither searches
for a recent decision nor runs preflight implicitly.

When an experiment hands the conversion package to genetic training, its
effective arguments name exactly one `--reference-member target`, `uplink`, or
`downlink`. The orchestrator passes that member with the explicit conversion
`artifact-status.toml`; it never defaults to `target` or infers a direction.

## Run Directories

The default run root is `run/` below the launch current directory.
`--run-root PATH` overrides it. Names are readable, lexically chronological
UTC names with a collision suffix:

```text
capture_2026-07-20_14-25-30_Z_a1b2c3/
experiment_2026-07-20_14-25-30_Z_a1b2c3/
```

Single-app runs use:

```text
run/<app>_<timestamp>_Z_<suffix>/
```

Experiments use:

```text
run/experiment_<timestamp>_Z_<suffix>/
  preflight/
  capture/
  convert/
  genetic_training/
```

That top-level single-run or experiment directory is the orchestrator's own
direct attempt and contains its `launch.toml` and top-level status. Stage
directories below it are managed child attempts.

Each listed stage directory is an **attempt directory**. The orchestrator
creates it as an empty, same-user, mode-`0700`, non-symlink directory before
launch, passes its absolute path as `--attempt-dir`, and does not create the
child's successful artifact destination. A child must not choose a different
managed attempt or artifact path. The common
[startup-record boundary](../../CONFIGURATION.md#startup-record) owns child
validation of the assigned directory.

Output adapters are application-specific:

| Application | Managed output argument |
|---|---|
| preflight, capture, convert, inspection export, genetic training | `--output-dir <attempt>/artifact` |
| model creation, similarity evaluation | `--output-dir <attempt>`; the absent output is `<attempt>/<model-name>.toml` or `<attempt>/similarity.toml` |
| traffic generation | `--output <attempt>/synthetic.pcapng` |

A package child atomically publishes the absent `artifact` directory. A
single-file child atomically publishes only its absent named file. Presence of
an artifact without valid [detached successful status](../../libs/artifact_io/SAD.md#successful-status-envelope)
is an orphan, not success.

## Paths, Contracts, and Current Directory

Every child retains the current directory from which `trafficlab` was launched.
The orchestrator resolves every input and output path to an absolute path.

A producing child writes contracts at its assigned artifact path. The
orchestrator first validates `artifact-status.toml`, then the declared artifact
and contract, and passes exact absolute status/artifact paths to the next child
using named arguments such as `--input-contract` or `--capture-file`. Children
never search for a latest run, infer an input, or trust destination presence.
Directional-package handoff uses the genetic-training status/member pair, not
a guessed PCAPNG path.

## Configuration and Lineage

The orchestrator accepts TOML settings only through explicit
`--config-file PATH`; command-line values override those settings under the
shared [configuration rules](../../CONFIGURATION.md). It writes a top-level
`launch.toml` containing resolved settings, child commands, stage paths,
contract paths, and status.

## Testing and Failure

Automated tests explicitly use `test_run/` below their launch directory as the
`--run-root`. Root `run/` and `test_run/` are Git-ignored; production launches
never use `test_run/`.

If a stage fails, the experiment stops immediately and launches no later
stages. The complete experiment directory remains for diagnosis. Top-level
status and lineage record the failed stage, child command, resolved paths, and
valid available contracts. The orchestrator does not delete artifacts,
staging, or orphans automatically.

## Reading

Read the [architecture map](../../README.md), shared
[configuration rules](../../CONFIGURATION.md), and each selected application
owner before changing orchestration behavior.

## Cross-Cutting Architecture

Stage graph, adapter selection, path assignment, and status transitions form the deterministic
core; clock, suffix randomness, directories, subprocesses, resources, and logs
form the shell and enter lineage. Child commands never use a shell string.
Resource reservations bound parallel work. Main risks are path escape, stale or
forged contracts, request/readiness mismatch, partial child output, and
ambiguous recovery; validation and fail-fast retention address them. Execution
itself is unprivileged.
