# Capture Request Contract Software Requirements Specification

## Requirements

- **CRQ-FR-001:** The contract shall identify one request, one prepared workspace and manifest digest, one target argument vector, and one complete capture policy.
- **CRQ-FR-002:** It shall define completion, interactive, bridge, environment, and packet-retention values without implicit behavioral defaults.
- **CRQ-FR-003:** Preflight and capture shall validate and hash the same immutable request bytes with one shared implementation.
- **CRQ-IF-001:** The artifact shall be one bounded versioned `capture-request.toml` conforming to the closed schema in `CONFIGS.md`.
- **CRQ-IF-002:** Readiness shall bind the lowercase SHA-256 of the exact request bytes and matching workspace-manifest identity and digest.
- **CRQ-IF-003:** Capture shall receive explicit request and readiness paths and revalidate both before and after workspace reservation as applicable.
- **CRQ-NFR-001:** Canonical production, validation results, and bridge ordering shall be deterministic and bounded.
- **CRQ-NFR-002:** Validation shall use bounded reads and shall not execute, expand, or shell-interpret any request value.
- **CRQ-SEC-001:** The request shall be a same-user mode-`0600` regular single-link file and shall grant no privilege or execution authority by itself.
- **CRQ-SEC-002:** Schema version 1 shall reject secret-bearing environment values and shall keep argument/environment values out of routine logs.
- **CRQ-ERR-001:** Malformed, changed, substituted, oversized, stale, mismatched, unknown, unsafe, or unsupported input shall prevent target launch.
- **CRQ-TST-001:** Producer and both consumers shall share golden, mutation, boundary, mismatch, filesystem-substitution, freshness, and secret-sentinel fixtures.

## Acceptance Criteria

- **CRQ-AC-001:** Producer, preflight, and capture accept the same canonical golden request and compute the same exact-byte digest.
- **CRQ-AC-002:** Changing any request byte after readiness, selecting another workspace/manifest, or exceeding freshness prevents reservation or target launch.
- **CRQ-AC-003:** Unknown fields, wrong TOML kinds, limit boundaries, symlink/hard-link/mode/owner failures, path escapes, and interactive-without-terminal fixtures fail closed.
- **CRQ-AC-004:** No argument, environment value, protected value, or secret sentinel appears in routine logs or readiness diagnostics.

## Traceability

[SAD](SAD.md) · [Field reference](CONFIGS.md) · [Roadmap](ROADMAP.md) ·
[Preflight SRS](../../apps/00_preflight/SRS.md) ·
[Capture SRS](../../apps/10_capture/SRS.md) ·
[Readiness contract](../00_10_capture_readiness/SRS.md) ·
[Artifact I/O](../../libs/artifact_io/SRS.md)
