# Capture Request Contract Software Architecture Document

## Context, Goals, and Boundaries

Preflight and capture need one stable description of the operation being
assessed. `capture-request.toml` is a bounded, immutable, user-owned file. It
contains execution and capture policy only; it contains no readiness result,
password, token, packet bytes, privilege grant, shell program, or repair action.

The producer is an operator-facing client or the
[Trafficlab orchestrator](../../apps/99_trafficlab/README.md). Consumers are
[preflight](../../apps/00_preflight/README.md) and
[capture](../../apps/10_capture/README.md). Both use the same parser,
normalizer, semantic validator, and exact-byte hashing implementation.

## File Envelope

The filename is exactly `capture-request.toml`. Schema version 1 has these
envelope rules:

- UTF-8 without BOM, LF line endings, valid TOML 1.0, and at most 65,536 bytes;
- one regular file, never a symlink, FIFO, device, socket, or hard-linked file;
- owner equal to the invoking ordinary user, link count one, and mode exactly
  `0600`;
- atomically published from a private same-filesystem sibling temporary file;
- no duplicate keys, unknown keys, non-finite numbers, control characters, or
  NUL bytes; and
- canonical producers emit the tables and keys in the order shown below, with
  arrays retaining their semantic order except `network.bridge_ids`, which is
  sorted lexically.

Consumers may accept a noncanonical but otherwise valid TOML representation.
They never rewrite it. They read one bounded byte snapshot through a no-follow
open, compare file identity and metadata before and after the read, parse that
same snapshot, and compute lowercase hexadecimal SHA-256 over exactly those
bytes. A changed identity, size, metadata, or digest fails validation.

## Version 1 Schema

```toml
schema_version = 1
request_id = "experiment-20260721-a1b2c3"
workspace_id = "capture-workspace-01"
workspace_manifest = "/absolute/host/path/workspace-manifest.toml"
workspace_manifest_sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
readiness_ttl_seconds = 60

[target]
argv = ["/usr/bin/example", "--flag"]
working_directory = "/work"
interactive = false

[target.environment]
LANG = "C.UTF-8"

[completion]
mode = "process_tree"
maximum_runtime_seconds = 300.0

[network]
bridge_ids = []

[packet_retention]
mode = "prefix"
bytes = 256
```

The exact fields and constraints are owned by [CONFIGS.md](CONFIGS.md). All
fields are explicit; schema version 1 supplies no behavioral default.

## Identity and Path Semantics

`request_id` correlates diagnostics and prevents accidental reuse; it is not a
credential. `workspace_id`, the absolute host-side workspace-manifest path,
and the manifest digest jointly identify the prepared workspace. Preflight
opens and hashes the manifest without following a final symlink, validates its
owned workspace identity, and records that evidence. Capture repeats this
validation after reserving the slot.

`target.argv[0]` is an absolute, lexically normalized POSIX path interpreted
inside the selected workspace. `target.working_directory` is likewise an
absolute workspace-internal path. Neither may contain `.` or `..` segments.
Preflight establishes existence, type, executable access, and containment for
the selected workspace mapping; capture passes the validated argument vector
without shell interpretation. No field undergoes shell, tilde, glob, command,
or environment-variable expansion.

Environment keys and literal values are an allowlist for the target. Values
known or marked by their source as secret-bearing are forbidden in version 1;
a future secret-reference mechanism requires a new compatible schema rule.
Consumers never inherit unspecified environment variables except the minimal
fixed launcher environment owned by the capture backend.

## Completion, Network, and Packet Policy

`fixed_duration` keeps capture active until
`completion.maximum_runtime_seconds`. `process_tree` stops normally when the
target tree terminates and uses the same value as a hard timeout. Timeout and
target exit are separate recorded target outcomes; neither bypasses recorder
close, cleanup, or artifact validation.

Bridge identifiers are opaque references to bridges already declared by the
prepared workspace. Preflight must validate every requested identifier and
capability. An address, port, interface, or command is not a bridge identifier.
Capture cannot add a bridge that was absent from the request and readiness
decision.

`prefix` captures exactly the first requested number of bytes available from
each packet, subject to recorder/link-layer limits. `full` is an explicit
request for full captured packets and forbids the `bytes` field. Metadata
records requested and actual retention.

## Request and Readiness Binding

The producer publishes the request before invoking preflight. Preflight records
the exact-byte request SHA-256 in the readiness decision. Capture receives
explicit paths to both artifacts and accepts them only when:

1. both file envelopes and schemas validate;
2. capture's recomputed request digest equals the readiness request digest;
3. request and readiness name the same workspace identity and manifest digest;
4. readiness is successful, contains no mandatory blocker, and is no older
   than `readiness_ttl_seconds` at capture's validated UTC time;
5. readiness creation is not in the future beyond the readiness contract's
   allowed clock tolerance; and
6. new post-reservation snapshots prove that request/readiness identity, hashes,
   binding, and freshness plus workspace identity and ready state still match.

The request is never edited to extend freshness. A retry that changes any byte
creates a new request/readiness pair. `trafficlab experiment capture` must
receive both paths and never searches for a recent file.

## Security, Errors, and Logging

File modes and same-user ownership reduce local substitution; the invoker's
user-owned endpoint and peer credentials remain the execution authorization
boundary. No cryptographic signature is required for the single-user local
deployment. Cross-user or remote transport is unsupported by schema version 1
and requires an authenticated successor contract.

Malformed or oversized input, changed file identity, bad ownership/mode,
unknown fields, request/readiness mismatch, stale evidence, wrong workspace,
secret-bearing environment data, unsafe paths, and unsupported requested
capabilities fail before target launch. Logs contain request/workspace IDs,
digests, counts, and bounded reasons; they do not repeat argument values,
environment values, protected workspace data, or packet content.

## Determinism, Performance, and Testing

Parsing, normalized validation results, bridge ordering, and canonical
producer serialization are deterministic. Validation uses bounded memory and
one snapshot of each small metadata file. Golden, mutation, size, mode,
symlink/hard-link, ownership, path, request/readiness mismatch, freshness,
secret-sentinel, and time-of-check/time-of-use fixtures are required. Tests use
fake clocks, filesystem metadata, workspace probes, and invokers; no test
launches a privileged operation or live capture.

## Reading

Read the [readiness contract](../00_10_capture_readiness/README.md),
[preflight](../../apps/00_preflight/SAD.md),
[capture](../../apps/10_capture/SAD.md), and
[artifact publication](../../libs/artifact_io/SAD.md) before changing this
contract.
