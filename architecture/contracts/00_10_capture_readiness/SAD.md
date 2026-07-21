# Capture Readiness Contract Software Architecture Document

## Context, Goals, and Boundaries

The interface prevents capture from inferring readiness or rerunning preflight
implicitly. It binds one [capture request](../00_capture_request/README.md),
workspace identity, observations/findings, supported capabilities, blockers,
decision, time context, validation, and lineage. It grants no capability or
privilege by itself.

## Published Package

Every successful preflight publication contains exactly:

```text
manifest.json
capture-readiness.toml
launch.toml
```

The manifest follows [artifact publication](../../libs/artifact_io/SAD.md): it
hashes both non-manifest members, excludes itself, and is bound by detached
`artifact-status.toml`. Package and status paths, type, ownership, mode, and
digests are validated before the decision is parsed.

## Version 1 Decision Schema

`capture-readiness.toml` is canonical UTF-8 TOML 1.0 with LF endings, at most
65,536 bytes, no unknown keys, and this envelope:

```toml
schema_version = 1
decision = "ready"
request_sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
workspace_id = "capture-workspace-01"
workspace_manifest_sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
created_at_utc = "2026-07-21T12:00:00.000000Z"
producer_name = "preflight"
producer_version = "0.1.0"
validation = "valid"
capabilities = ["capture.ethernet", "network.ipv4"]

[[findings]]
id = "workspace-ready"
severity = "info"
capability = "workspace"
reason = "validated"
```

Envelope validation is exact:

- `schema_version` is integer `1`; Boolean is invalid.
- `decision` is `ready` or `blocked`.
- Both SHA-256 fields are exactly 64 lowercase hexadecimal characters.
- `workspace_id` exactly equals the validated request value.
- `created_at_utc` is canonical UTC RFC 3339 with six fractional digits.
- `producer_name` is exactly `preflight`; `producer_version` is 1–64 bounded
  ASCII identifier characters.
- `validation` is exactly `valid`.
- `capabilities` contains at most 128 unique 1–64-character identifiers in
  lexical order.
- `findings` contains at most 256 entries in `(severity, id)` order. Each has a
  unique 1–64-character ID, severity `info` or `blocker`, a 1–64-character
  capability, and a 1–128-character reason code.

Identifiers and reason codes match `[A-Za-z0-9][A-Za-z0-9._-]*`. A `ready`
decision has no blocker and includes every mandatory requested capability. A
`blocked` decision has at least one blocker. Findings contain bounded reason
codes, not free-form protected observations or remediation output.

## Request Binding and Freshness

Preflight hashes the exact validated request snapshot and copies its workspace
identity and workspace-manifest digest. Capture recomputes that request digest
and requires all three values to match.

Freshness is determined at capture from `created_at_utc` and the request's
required `readiness_ttl_seconds`. A decision older than that bound or more than
five seconds in the future is invalid. Capture validates request, readiness
package, detached hashes, ownership/mode, and decision immediately before
reservation, then takes new bounded snapshots and rechecks request/readiness
identity, hashes, binding, current freshness, workspace identity, and ready
state after exclusive reservation and before recorder creation.

The request is never edited to extend freshness. A changed request requires a
new readiness decision. No command searches for a latest decision or treats
the readiness artifact as execution authority.

## Security and Authenticity

No cryptographic signature is required for the supported single-user local
deployment. Same-user mode-`0600` request/status files, detached content
digests, validated workspace identity, and the invoker endpoint's peer
credentials form the local integrity/authorization boundaries. Cross-user or
remote readiness transport is unsupported and requires a successor contract.

A mismatch, stale/unknown evidence, blocker, malformed decision, invalid
ordering, changed file identity, or broken lineage prevents capture launch.
Logs contain IDs, digests, bounded reason codes, counts, and decision only; they
do not duplicate target arguments, environment values, protected observations,
or packet data.

## Determinism, Performance, and Testing

The decision is bounded metadata. Canonical key, capability, and finding order
is deterministic. Golden producer-consumer, mutation, ordering, mismatch,
blocker, freshness/future-clock, permission, detached-hash, protected-sentinel,
and lineage tests are required. Main risks are stale readiness, request
substitution, forged workspace identity, and protected-value leakage.

## Reading

Read the [capture request](../00_capture_request/README.md),
[preflight](../../apps/00_preflight/SAD.md),
[capture](../../apps/10_capture/SAD.md), and
[artifact I/O](../../libs/artifact_io/SAD.md) before changing this contract.
