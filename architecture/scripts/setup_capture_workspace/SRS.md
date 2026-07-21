# Setup Script Software Requirements Specification

## Requirements

- **SST-FR-001:** Setup shall validate explicit workspace identity, owner, and backend settings.
- **SST-FR-002:** It shall display all resources and public durable changes before mutation while rendering protected changes by name and redacted marker only.
- **SST-FR-003:** It shall write a durable manifest before creating resources.
- **SST-FR-004:** It shall start the invoker as the designated ordinary user.
- **SST-IF-001:** Durable change shall consume a successful scoped backup record.
- **SST-IF-002:** A protected backup reference shall carry a normalized traversal-free path contained below the named private directory, digest, value names, reader identity, and outcome without protected plaintext.
- **SST-NFR-001:** Resource creation order and manifest recording shall be deterministic.
- **SST-SEC-001:** Setup shall create only workspace-owned displayed resources with explicit privilege.
- **SST-ERR-001:** Failure shall stop further actions and retain diagnosable partial state.
- **SST-TST-001:** Automated tests shall use fake commands and no elevation.

## Acceptance Criteria

- **SST-AC-001:** Successful fixture records every created resource and yields ready state.
- **SST-AC-002:** Failure at each action yields exact partial manifest and no later action.
- **SST-AC-003:** Undisplayed/global-resource attempts are rejected.

## Traceability

[SAD](SAD.md) · [Roadmap](ROADMAP.md) · [Backup](../backup_system_configuration/README.md)
