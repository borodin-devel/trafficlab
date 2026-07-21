# Capture Request Version 1 Field Reference

This document owns the exact version 1 `capture-request.toml` field names,
types, bounds, and cross-field validation. The file is an inter-application
contract, not an application configuration layer: built-in configuration,
`--set`, environment overrides, and implicit defaults do not apply.

## Required Envelope Fields

| Path | TOML type | Constraint |
|---|---|---|
| `schema_version` | integer | Required; exactly `1`. Boolean is invalid. |
| `request_id` | string | Required; 1–64 ASCII characters matching `[a-z0-9][a-z0-9._-]*`. |
| `workspace_id` | string | Required; 1–128 ASCII characters matching `[A-Za-z0-9][A-Za-z0-9._-]*`. |
| `workspace_manifest` | string | Required absolute normalized host POSIX path, at most 4,096 UTF-8 bytes, without NUL, `.` or `..` segments. |
| `workspace_manifest_sha256` | string | Required; exactly 64 lowercase hexadecimal characters. |
| `readiness_ttl_seconds` | integer | Required; from `1` through `300` inclusive. Boolean is invalid. |

## Target Fields

| Path | TOML type | Constraint |
|---|---|---|
| `target.argv` | array of strings | Required; 1–256 elements. Each is at most 4,096 UTF-8 bytes and contains no NUL/control character; aggregate encoded content is at most 32,768 bytes. Element zero is an absolute normalized workspace POSIX path. Order is significant. |
| `target.working_directory` | string | Required absolute normalized workspace POSIX path, at most 4,096 UTF-8 bytes, without `.` or `..` segments. |
| `target.interactive` | Boolean | Required. `true` additionally requires an attached terminal at capture launch. |
| `target.environment` | table of string to string | Required, including when empty; at most 128 entries. Keys match `[A-Za-z_][A-Za-z0-9_]*` and are lexically sorted by canonical producers. Each value is at most 4,096 UTF-8 bytes and contains no NUL/control character. Secret-bearing values are forbidden. |

No argument or environment value is expanded or interpreted. Duplicate
environment keys after exact case-sensitive comparison are invalid.

## Completion Fields

| Path | TOML type | Constraint |
|---|---|---|
| `completion.mode` | string | Required; exactly `"fixed_duration"` or `"process_tree"`. |
| `completion.maximum_runtime_seconds` | float | Required TOML float; finite, strictly greater than `0`, and at most `86400.0`. An integer token is invalid. |

For `fixed_duration`, the value is the requested capture duration. For
`process_tree`, it is the hard timeout if the complete target tree has not
already terminated.

## Network Fields

| Path | TOML type | Constraint |
|---|---|---|
| `network.bridge_ids` | array of strings | Required; 0–32 unique identifiers in lexical order. Each is 1–64 ASCII characters matching `[A-Za-z0-9][A-Za-z0-9._-]*`. |

An identifier must resolve in the exact selected workspace manifest. Consumers
reject duplicate, unknown, unsupported, or differently ordered identifiers.

## Packet-Retention Fields

| Path | TOML type | Constraint |
|---|---|---|
| `packet_retention.mode` | string | Required; exactly `"prefix"` or `"full"`. |
| `packet_retention.bytes` | integer | Required only for `prefix`; from `1` through `65535` inclusive. Forbidden for `full`. Boolean is invalid. |

## Closed Schema and Cross-Field Rules

All listed fields and tables are required except the conditionally forbidden
`packet_retention.bytes`; no other key or table is allowed. TOML datetime,
array-of-table, inline-table substitution, and dotted aliases for these tables
are invalid producer forms.

Validation additionally requires:

- the request file envelope, workspace manifest identity, path containment,
  executable access, terminal condition, and bridge capabilities defined by
  the [SAD](SAD.md);
- one complete target invocation and one completion policy;
- no value classified as a secret by its source or producer; and
- a matching successful readiness decision before capture, never merely valid
  request syntax.

Any violation rejects the whole request. Consumers do not drop fields, repair
ordering, coerce types, clamp values, or substitute defaults.
