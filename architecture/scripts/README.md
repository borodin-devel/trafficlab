# Workspace Script Architecture

## Role

This directory owns the manual operator scripts that prepare, inspect, back up,
or remove a capture workspace. Their source files belong in `scripts/`.

Normal Trafficlab commands never invoke these scripts. They are not part of
normal capture execution or automated tests.

Shared platform and toolchain constraints belong to the
[development environment](../DEVELOPMENT.md).

## Reading

Read the [capture application](../apps/10_capture/README.md) first. Then read
the owner document for the operator action being considered.

| Script | Owner document | Privilege boundary |
| --- | --- | --- |
| `setup_capture_workspace.sh` | [Setup](setup_capture_workspace/README.md) | Manual privileged setup |
| `rollback_capture_workspace.sh` | [Rollback](rollback_capture_workspace/README.md) | Manual privileged teardown |
| `verify_capture_workspace.sh` | [Verify](verify_capture_workspace/README.md) | Read-only inspection |
| `backup_system_configuration.sh` | [Backup](backup_system_configuration/README.md) | Conditional read-only backup |
| `workspace_orchestration.sh` | [Orchestration](workspace_orchestration/README.md) | Manual operator sequencing |

## Common Rules

Each script owner defines its inputs, authority, effects, manifest behavior,
failure handling, rollback behavior, and test constraints. Scripts validate
workspace identity and paths, use argument vectors, retain diagnostics on
failure, and never perform broad global cleanup.
