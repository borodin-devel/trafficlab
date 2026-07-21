# Dedicated Workspace

## Status

Active default. The authoritative boundary is the
[capture architecture](../../architecture/apps/10_capture/README.md).

## Purpose

A manually prepared workspace gives one target application tree a dedicated
external capture point. This makes packet ownership clear: traffic crossing
that point belongs to the target workspace rather than unrelated host
processes.

## Prepared Slot

Manual setup prepares one slot and starts its invoker as the ordinary workspace
user. The invoker accepts one capture at a time, starts the target and all
child processes, and reports `ready`, `busy`, or `unhealthy` state. Normal
capture never creates or tears down the slot.

## Network Paths

External traffic passes the dedicated capture point and is recorded in both
directions. Workspace loopback is a separate capture point, so `localhost`
means the workspace itself and its local traffic is observable.

Outbound network access is normal within confirmed workspace capabilities.
Host-local services are unavailable by default. A requested host service is
made available only through an explicit configured bridge that preflight can
verify.

## Interactive Targets

For interactive targets, the invoker creates a pseudoterminal in the workspace
and bridges it to the caller. The bridge carries terminal input/output, window
resizes, and interrupts, while the target owns the pseudoterminal foreground
process group.

## Capture and Reuse

The recorder becomes ready before target launch. After the target tree ends,
the recorder closes, Trafficlab validates and publishes the canonical capture,
and unprivileged per-capture state is cleaned. The slot remains available for a
later capture only when it can be shown ready; otherwise it is unhealthy until
manual verification, setup, or rollback.

## Administration

Setup and rollback are manifest-scoped manual operations. They prefer temporary
or reboot-cleared resources. A durable change is exceptional: setup displays
the old and proposed values, records a backup, and rollback restores it.
