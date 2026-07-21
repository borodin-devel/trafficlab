# Infrastructure Software Requirements Specification

## Scope and Constraints

Stakeholders are developers, maintainers, and release operators. Python 3.12,
Linux, uv, setuptools, pytest, pytest-cov, pyright, and ruff are constraints.

## Requirements

- **INF-FR-001:** Infrastructure shall create a locked Python 3.12 environment with uv.
- **INF-FR-002:** Infrastructure shall build installable artifacts with setuptools.
- **INF-FR-003:** Infrastructure shall run unit and integration tests with pytest.
- **INF-FR-004:** Infrastructure shall measure coverage with pytest-cov.
- **INF-FR-005:** Infrastructure shall run pyright and ruff through documented commands.
- **INF-IF-001:** Local and CI checks shall use the same repository-owned command interface.
- **INF-NFR-001:** Automated checks shall run without elevation or host mutation.
- **INF-NFR-002:** Locked dependencies and build inputs shall be reproducible.
- **INF-ERR-001:** Any failed mandatory check shall fail the delivery pipeline.
- **INF-TST-001:** Infrastructure shall validate the documentation corpus, including links and requirement identifiers.

## Acceptance Criteria

- **INF-AC-001:** A clean checkout can install, lint, type-check, test, measure coverage, and build using documented commands.
- **INF-AC-002:** CI rejects broken local architecture links and duplicate requirement identifiers.

## Traceability

[Configuration](CONFIGS.md)

Architecture: [SAD.md](SAD.md). Delivery sequence: [ROADMAP.md](ROADMAP.md).
Shared constraints: [development environment](../DEVELOPMENT.md).
