# Setup Capture Workspace Script

## Purpose and Interface

`setup_capture_workspace.sh` is the manual privileged transaction that creates
one manifest-scoped capture workspace and starts its ordinary-user invoker.

## Inputs, Outputs, Dependencies, and Context

Input is explicit identity, owner, and backend settings. Output is a durable
manifest and ready or diagnosable failed workspace. It may depend on the
[backup script](../backup_system_configuration/README.md); normal capture never invokes it.

It prepares the boundary used by [capture](../../apps/10_capture/README.md).
Operators confirm state with [verification](../verify_capture_workspace/README.md)
and reverse it only through [rollback](../rollback_capture_workspace/README.md).

## Documents

[SAD](SAD.md) · [SRS](SRS.md) · [Roadmap](ROADMAP.md)
