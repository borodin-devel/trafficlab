# Preflight Application

## Purpose and Responsibilities

`preflight` performs a read-only readiness assessment for one requested
capture workspace and publishes blockers, findings, and supported capabilities.

## Inputs, Outputs, and Interface

Input is one explicit [capture request](../../contracts/00_capture_request/README.md).
Output is one request-bound readiness package under the
[readiness contract](../../contracts/00_10_capture_readiness/README.md)
consumed by [capture](../10_capture/README.md). It never repairs or
mutates the workspace.

```text
preflight --capture-request PATH --output-dir DIR
```

## Configuration, Dependencies, and Execution

The application runs unprivileged on Linux and depends on read-only workspace
observations. No application-specific settings are defined yet.

Related boundaries are shared [configuration](../../libs/configuration/README.md)
and the manual [workspace verification script](../../scripts/verify_capture_workspace/README.md);
preflight invokes neither operator scripts nor privileged setup.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
