# Capture Software Requirements Specification

## Scope and Assumptions

Capture serves researchers and orchestration. It assumes a successful matching
preflight decision and one ready exclusive prepared workspace.

## Requirements

- **CAP-FR-001:** Capture shall launch exactly one requested target argument vector and its descendants in the reserved workspace.
- **CAP-FR-002:** It shall support fixed-time and target-tree-termination completion policies.
- **CAP-FR-003:** It shall start and confirm the recorder before launching the target.
- **CAP-FR-004:** It shall support a pseudoterminal for an interactive target only when a terminal is attached.
- **CAP-FR-005:** It shall stop the recorder cleanly after target-tree completion and publish canonical `raw/target.pcapng`.
- **CAP-IF-001:** CLI shall require explicit `--capture-request`, `--readiness`, and `--output-dir` paths to one immutable request, one successful matching fresh readiness package, and one absent artifact destination.
- **CAP-IF-002:** Output shall preserve execution, network, packet-length, request/readiness binding, verified cleanup outcome, validation, hash, and source lineage.
- **CAP-IF-003:** Capture shall revalidate request/readiness bytes, binding, freshness, workspace identity, and ready state before target launch.
- **CAP-CFG-001:** Completion, interactive, bridge, and packet-length values shall be explicit and validated before launch.
- **CAP-NFR-001:** Capture shall preserve one exclusive workspace slot through cleanup.
- **CAP-SEC-001:** Normal capture shall not elevate, alter global configuration, or interpret a shell string.
- **CAP-ERR-001:** Recorder, publication, or cleanup failure shall retain diagnostics; failed cleanup shall mark the workspace unhealthy.
- **CAP-ERR-002:** Cleanup failure shall leave the successful artifact destination absent and shall not emit successful detached status.
- **CAP-TST-001:** Automated tests shall use fake invoker/recorder boundaries and no real privilege.

## Acceptance Criteria

- **CAP-AC-001:** Fixed-time, process-exit, failed-target, interactive, timeout, recorder-failure, and interrupt scenarios produce specified separate target/capture outcomes.
- **CAP-AC-002:** Only closed, validated, hashed PCAPNG publishes successfully after recorder readiness and verified cleanup.
- **CAP-AC-003:** Command and path injection fixtures never receive shell interpretation or workspace escape.
- **CAP-AC-004:** Changed, stale, mismatched, blocked, or substituted request/readiness fixtures prevent reservation or target launch, and cleanup failure leaves no published-success signal.

## Traceability

[SAD](SAD.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md) ·
[Preflight](../00_preflight/SRS.md) ·
[Capture request](../../contracts/00_capture_request/SRS.md) ·
[Readiness contract](../../contracts/00_10_capture_readiness/README.md) ·
[Directions contract](../../contracts/10_20_capture_directions/README.md)
