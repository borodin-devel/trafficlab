# Joint Sinkhorn/Wasserstein Software Requirements Specification

## Requirements

- **MJS-FR-001:** Each noninitial frame shall form `(log1p(IAT_seconds), original_length)` observation.
- **MJS-FR-002:** Positive feature scales shall define documented Euclidean ground cost.
- **MJS-FR-003:** Empirical clouds shall use uniform weights and may have unequal counts.
- **MJS-FR-004:** Raw distance shall be debiased entropic Sinkhorn divergence with two self terms.
- **MJS-FR-005:** Similarity shall be `exp(-D/score_mapping_scale)`.
- **MJS-IF-001:** Result shall record clouds, scales, epsilon, solver/convergence, deterministic settings, and dependency lineage.
- **MJS-CFG-001:** Feature/mapping/regularization/tolerance values shall be finite positive and iteration limit positive integer.
- **MJS-NFR-001:** Fixed inputs/configuration/backend/version shall yield deterministic result in `(0,1]`.
- **MJS-ERR-001:** Non-convergence, invalid cost/divergence/score, or negative divergence outside numerical guarantees shall fail.
- **MJS-TST-001:** Tests shall compare transport terms/divergence with authoritative examples or independent implementation.

## Acceptance Criteria

- **MJS-AC-001:** Identical, translated, scaled, zero-IAT, and unequal-cloud examples yield expected divergence/score.
- **MJS-AC-002:** Convergence/tolerance/numerical failures produce no successful result and complete diagnostics.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Mathematics](01_TRANSPORT_MATHEMATICS.md) · [Roadmap](ROADMAP.md)
