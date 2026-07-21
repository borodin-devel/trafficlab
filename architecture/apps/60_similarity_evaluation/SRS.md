# Similarity Evaluation Software Requirements Specification

## Scope

Similarity evaluation executes one registered comparison method over one
reference and one generated PCAPNG. The method owns mathematics; the
application owns validation, invocation, lineage, and publication.

## Requirements

- **SIM-FR-001:** The application shall validate both explicit PCAPNG inputs before scoring.
- **SIM-FR-002:** It shall resolve and execute exactly one mature registered method.
- **SIM-FR-003:** It shall preserve the method's declared score direction, range, primary value, and diagnostics.
- **SIM-IF-001:** CLI shall require reference, generated, method, and output-directory arguments.
- **SIM-IF-002:** Output shall be one non-self-hashing `similarity.toml` conforming to the result contract, with detached successful status binding file and launch digests.
- **SIM-CFG-001:** Method configuration shall be explicit, validated, and recorded in `launch.toml`.
- **SIM-NFR-001:** Fixed inputs, configuration, implementation, and supported dependencies shall yield deterministic result serialization.
- **SIM-ERR-001:** Invalid input, unsupported/stub method, numerical failure, or publication failure shall produce no successful result.
- **SIM-TST-001:** Tests shall compare method outputs with authoritative fixtures and result contract validation.

## Acceptance Criteria

- **SIM-AC-001:** Every mature method's identical and contrasting fixtures yield documented score/diagnostic results.
- **SIM-AC-002:** Repeated fixed evaluation produces identical `similarity.toml` content hash.
- **SIM-AC-003:** All validation/numerical/publication failures, self-digest fields, and orphan outputs retain diagnostics and no successful result.

## Traceability

[SAD](SAD.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md) ·
[Methods](../../similarity_methods/README.md) · [Result contract](../../contracts/60_30_similarity_result/README.md)
