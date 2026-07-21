# Capture Request Contract

This contract owns the immutable `capture-request.toml` supplied to
[preflight](../../apps/00_preflight/README.md) and later matched by
[capture](../../apps/10_capture/README.md). It identifies one workspace, one
target argument vector, and the complete runtime/capture policy assessed by
preflight.

An operator-facing client or the `trafficlab` orchestrator produces the
request. Preflight and capture share one validator and hash the same exact file
bytes. The resulting [readiness decision](../00_10_capture_readiness/README.md)
binds the request digest; it does not duplicate or override request values.

The contract is unprivileged metadata and grants no authority. Files,
workspaces, target arguments, environment values, and paths remain untrusted
at every consumer boundary.

## Documents

1. [Software Architecture Document](SAD.md)
2. [Software Requirements Specification](SRS.md)
3. [Contract configuration and field reference](CONFIGS.md)
4. [Implementation and testing roadmap](ROADMAP.md)

Request creation reuses the atomic file-writing boundary from
[artifact I/O](../../libs/artifact_io/README.md), but the request is user input,
not a successful application artifact: its exact-byte digest is bound by the
readiness package and it has no detached status of its own. The
[readiness contract](../00_10_capture_readiness/README.md) defines request
binding/freshness, and the [capture owner](../../apps/10_capture/SAD.md#inputs)
requires the same pair for every capture start.
