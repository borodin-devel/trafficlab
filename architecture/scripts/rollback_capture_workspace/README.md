# Roll Back Capture Workspace Script

## Purpose and Interface

`rollback_capture_workspace.sh` manually removes only resources named by one
validated workspace manifest and restores its recorded durable values.

## Inputs, Outputs, Dependencies, and Context

Input is the explicit manifest and operator authority. Output is per-action
diagnostics and a restored/remaining-resource state. It is privileged,
idempotent, and never invoked by normal capture.

It reverses manifests created by [setup](../setup_capture_workspace/README.md),
uses recorded [backup](../backup_system_configuration/README.md) values, and may
be followed by read-only [verification](../verify_capture_workspace/README.md).

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md)
