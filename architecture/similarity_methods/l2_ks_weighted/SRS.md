# Weighted L2 KS Software Requirements Specification

## Requirements

- **MLK-FR-001:** The method shall execute complete IAT and frame-size KS components.
- **MLK-FR-002:** It shall compute `iat_weight*iat_similarity + frame_weight*frame_similarity`.
- **MLK-FR-003:** Both components shall succeed even when one weight is zero.
- **MLK-IF-001:** Result shall include weights, component scores/distances, and sample counts.
- **MLK-CFG-001:** Weights shall be finite nonnegative exact-decimal values summing exactly to one.
- **MLK-NFR-001:** Primary score shall be deterministic in `[0,1]`, higher better.
- **MLK-ERR-001:** Invalid weight/component/numerical state shall fail without normalization, redistribution, or partial score.
- **MLK-TST-001:** Tests shall cover default, custom, boundary, invalid sum, and zero-weight failure.

## Acceptance Criteria

- **MLK-AC-001:** Hand component inputs produce exact weighted scores and full diagnostics.
- **MLK-AC-002:** Invalid weights or either failed component produce no successful result.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Combined score](01_COMBINED_SCORE_VALIDATION.md) ·
[Limits and testing](02_LIMITS_TESTING.md) · [Roadmap](ROADMAP.md)
