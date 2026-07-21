# Verify Capture Workspace Script

## Purpose and Interface

`verify_capture_workspace.sh` performs read-only inspection of one named
workspace and reports ready, busy, unhealthy, missing, or indeterminate state.

## Inputs, Outputs, Dependencies, and Context

Input is explicit workspace identity and manifest location. Output is a
diagnostic state report. It runs manually without repair or mutation.

It inspects workspaces created by [setup](../setup_capture_workspace/README.md).
[Preflight](../../apps/00_preflight/README.md) performs a separate application
readiness decision and never invokes this script.

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md)
