# Roll Back Capture Workspace Software Architecture Document

## Role

`scripts/rollback_capture_workspace.sh` manually tears down one prepared
workspace and restores every durable value recorded for it.

## Authority

Rollback is privileged because it removes operating-system resources prepared
by setup. Normal Trafficlab capture never invokes it.

## Inputs and Effects

Rollback requires the workspace manifest and validates its identity before
acting. It stops the invoker, reverses manifest-listed resources in reverse
creation order, and restores recorded durable configuration. For protected
values it resolves only a normalized traversal-free reference contained below
the manifest's private workspace directory, opens every path component without
following symlinks, verifies regular-file type, owner matching the recorded
reader identity, mode `0600`, stable identity, and manifest SHA-256, then reads
with equivalent operator authority. Protected contents never enter ordinary
logs or operator-facing output.

It removes only resources named by the workspace manifest. It never performs a
global firewall, network, process, or configuration cleanup.

## Invariants

Rollback is idempotent: an already absent manifest-listed resource is treated
as successfully absent. It records each attempted action and preserves enough
diagnostic information for a later manual retry.

## Tests

Automated tests use temporary manifests and fake commands to verify ordering,
scope, failure reporting, and idempotent planning. They never alter the host.

## Architecture Qualities and Limits

Manifest validation/reverse planning is pure; privileged argument-vector actions
form a manual shell. Work is bounded by manifest membership. Per-action logs and
remaining-state diagnostics support retry. Main risks are forged/stale manifest,
resource reuse, partial restoration, and broad privilege; identity/ownership
validation and exact allowlists prevent acting outside recorded scope.
Protected-file type/ownership/mode/digest validation prevents substituted or
disclosed restoration input.
