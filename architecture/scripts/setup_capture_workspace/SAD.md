# Setup Capture Workspace Software Architecture Document

## Role

`scripts/setup_capture_workspace.sh` is the manual operator transaction that
prepares one capture workspace and starts its invoker as the designated
ordinary workspace user.

## Authority

Setup is privileged because it may prepare operating-system resources outside
the normal user's authority. Normal Trafficlab capture never invokes it.

## Inputs and Plan

Setup requires an unambiguous workspace identity, owner, and requested backend
settings. Before mutable work, it displays the resources it will create and
every public durable configuration value it would change. A protected value is
displayed only by name and a redacted change marker; its original, proposed
value, and restoration arguments are never displayed.

Temporary and reboot-cleared resources are preferred. If a durable change is
necessary, setup invokes the [backup script](../backup_system_configuration/README.md)
before changing it.

## Effects

Setup writes a durable workspace manifest before mutation. The manifest names
only resources owned by this workspace, records the ordinary invoker identity,
and records any backup needed for restoration. Setup creates the prepared
workspace, applies only the displayed plan, starts the invoker as the ordinary
user, and records either `ready` or a diagnosable failed state.

For protected backups, the manifest contains only the normalized private-file
reference contained below the named private directory, SHA-256 digest, value
names, reader identity, and outcome defined by the
[backup owner](../backup_system_configuration/SAD.md#effects).

## Failure and Rollback

Setup stops after a failed operation and preserves diagnostics. It does not
attempt broad cleanup. The [rollback script](../rollback_capture_workspace/README.md)
uses the manifest to reverse a partial setup when the operator chooses it.

## Tests

Automated tests belong in `tests/scripts/` when implemented. They use fake
commands, temporary directories, and recorded manifests; they do not create a
real workspace or require elevated access.

## Architecture Qualities and Limits

Deterministic planning is pure; privileged command execution is a confirmed
imperative shell using argument vectors. Resource count bounds work. Every
action/outcome is logged without hiding durable changes. Deployment is a manual
Bash operator tool, never an application dependency. Risks are partial setup,
manifest loss, overly broad privilege, and platform drift; manifest-first scope,
stop-on-failure, verification, and rollback address them.
