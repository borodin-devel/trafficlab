# Trafficlab Orchestrator Application

## Purpose and Responsibilities

`trafficlab` launches one application or an ordered experiment, creates attempt
directories without pre-creating artifact destinations, passes explicit
absolute paths through application-specific adapters, validates detached
status/contracts, and stops on stage failure.

## Inputs, Outputs, and Interface

Public commands are `trafficlab run <app>` and
`trafficlab experiment <start-stage>`. Outputs are per-stage run directories
and top-level startup/status lineage. Every experiment containing capture uses
one explicit [capture request](../../contracts/00_capture_request/README.md);
starting at capture additionally requires its matching readiness decision.

## Configuration, Dependencies, and Execution

The orchestrator runs unprivileged, uses child argument vectors, and depends on
all selected applications, resource admission, and contract validators.

The [application catalog](../README.md) owns selectable child commands,
[file contracts](../../contracts/README.md) own stage boundaries, and
[resource management](../../libs/resource_management/README.md) owns admission.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md)
