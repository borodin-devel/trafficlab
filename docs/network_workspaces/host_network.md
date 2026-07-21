# Host Network

## Status

Inactive alternative. It is not selectable by the active capture application.
A separate future architecture decision is required before implementation.

## Purpose

Host-network capture leaves the target application tree on the ordinary host
network. It preserves host `localhost`, addresses, VPN behavior, and existing
host services more closely than the dedicated workspace.

## Attribution Requirement

Host interfaces carry traffic from unrelated processes. Therefore this variant
needs pre-installed process-tree attribution, such as a delegated process group
and a kernel packet or socket collector, before normal capture begins. The
invoker must start the target tree in the attributed group so descendants are
included.

The collector must capture both physical and loopback paths and prove that only
flows belonging to the target tree enter the result. It must reject uncertainty
rather than silently include unrelated host traffic.

## Privacy and Correctness Boundary

Unlike a dedicated interface, filtering mistakes can expose traffic from other
host applications. The design therefore needs independent checks for flow
attribution, loopback coverage, recorder loss, and exclusion of unrelated
packets before it can publish a canonical capture.

## Administration and Testing

Any kernel collector or process-group delegation would be prepared by manual
operator setup and removed by rollback. Normal capture and automated tests
would still remain unprivileged, but this variant needs its own later test and
security design before it can be activated.
