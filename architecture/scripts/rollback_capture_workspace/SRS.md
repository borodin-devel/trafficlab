# Rollback Script Software Requirements Specification

## Requirements

- **SRB-FR-001:** Rollback shall validate one workspace manifest before acting.
- **SRB-FR-002:** It shall stop invoker, remove manifest-listed resources in reverse creation order, and restore recorded values.
- **SRB-FR-003:** Already absent listed resources shall count as successfully absent.
- **SRB-IF-001:** Input shall be an explicit manifest and operator-approved action.
- **SRB-IF-002:** Protected restoration shall resolve only a traversal-free relative private-file reference contained below the named private directory after no-follow component, stable type, reader-owner, mode, and digest validation.
- **SRB-NFR-001:** Action order and retained diagnostics shall be deterministic.
- **SRB-SEC-001:** It shall never perform global firewall, network, process, or configuration cleanup.
- **SRB-SEC-002:** It shall not emit protected originals or restoration arguments to ordinary output or logs.
- **SRB-ERR-001:** Failed action shall be recorded with enough state for manual retry.
- **SRB-TST-001:** Tests shall use fake commands and temporary manifests without elevation.

## Acceptance Criteria

- **SRB-AC-001:** Complete and partial manifests reverse only listed resources in exact order.
- **SRB-AC-002:** Repeated rollback is idempotent and global-resource attempts are rejected.
- **SRB-AC-003:** Protected restoration succeeds only with equivalent authority and matching mode-`0600` file identity/digest; substitution and sentinel disclosure fixtures fail.

## Traceability

[SAD](SAD.md) · [Roadmap](ROADMAP.md) · [Setup](../setup_capture_workspace/README.md)
