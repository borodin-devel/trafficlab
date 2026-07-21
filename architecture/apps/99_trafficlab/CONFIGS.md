# Trafficlab Orchestrator Configuration

## Selection

The orchestrator accepts TOML only through explicit `--config-file PATH`.
CLI values override matching settings under shared rules. `--run-root PATH`
overrides the default `run/` below launch current directory.

## Known and Unresolved Settings

Run root and start stage are public CLI behavior; exact TOML paths for them,
child executable resolution, resource budgets, collision suffix generation,
and child timeout policy are unresolved. No environment-variable discovery or
latest-run lookup may be added.

## Capture Inputs and Output Adapters

Capture request/readiness paths, `--attempt-dir`, and application output paths
are explicit CLI/orchestration interface values, not TOML settings. Their
required combinations and adapter table are owned by the [SAD](SAD.md). No
configuration value may infer a latest readiness decision, pre-create a child
artifact destination, or replace an application-specific output flag.
