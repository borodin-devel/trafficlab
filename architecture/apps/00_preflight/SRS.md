# Preflight Software Requirements Specification

## Scope and Assumptions

Preflight serves capture operators and orchestration. It assumes an explicit
request and prepared-workspace identity; every observation is read-only.

## Requirements

- **PRE-FR-001:** Preflight shall assess workspace identity, ownership, manifest consistency, invoker health, exclusivity, and state.
- **PRE-FR-002:** It shall assess recorder access, required software, resources, network/DNS support, requested bridges, packet-length policy, and storage.
- **PRE-FR-003:** It shall classify every finding as readiness evidence or a blocker.
- **PRE-IF-001:** CLI shall require explicit `--capture-request` and `--output-dir` paths; input shall be one validated version 1 request identifying the workspace/manifest and complete requested capture policy.
- **PRE-IF-002:** Output shall be one detached-status readiness package bound to the exact request SHA-256 and consumable by capture.
- **PRE-CFG-001:** Configuration shall follow shared explicit selection and startup-record rules.
- **PRE-NFR-001:** Assessment shall be deterministic for identical observations and request.
- **PRE-SEC-001:** It shall not mutate, repair, install, elevate, or invoke operator scripts.
- **PRE-ERR-001:** Unknown or indeterminate mandatory capability shall block readiness with a reason.
- **PRE-ERR-002:** Changed request identity, workspace-manifest mismatch, invalid permissions, or failed detached publication shall produce no ready decision.
- **PRE-TST-001:** Automated tests shall use fake observations without reading real protected resources.

## Acceptance Criteria

- **PRE-AC-001:** Ready, busy, unhealthy, missing-capability, bridge, storage, and invalid-request fixtures yield expected decisions and findings.
- **PRE-AC-002:** Side-effect spies observe no file mutation, process launch, network mutation, or elevation request.
- **PRE-AC-003:** Request mutation, identity/mode/path substitution, and publication-failure fixtures cannot yield a matching ready package.

## Traceability

[SAD](SAD.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md) ·
[Capture request](../../contracts/00_capture_request/SRS.md) ·
[Readiness contract](../../contracts/00_10_capture_readiness/README.md) ·
[Capture consumer](../10_capture/SRS.md)
