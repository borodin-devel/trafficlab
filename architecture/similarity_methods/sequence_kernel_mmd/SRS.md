# Sequence-Kernel MMD Software Requirements Specification

## Requirements

- **MSM-FR-001:** Method shall create `ceil(H/w)` ordered half-open windows and retain empty windows.
- **MSM-FR-002:** Each noninitial in-horizon frame shall append local-time, scaled `log1p(IAT)`, and scaled original-length point in recorded order.
- **MSM-FR-003:** Every path shall start at mathematical zero anchor; empty-window path shall be constant anchor.
- **MSM-FR-004:** Base kernel shall be documented Gaussian RBF over three-dimensional points.
- **MSM-FR-005:** Path kernel shall be normalized positive truncated signature kernel with positive degree weights including degree zero.
- **MSM-FR-006:** Raw squared MMD shall use documented biased estimator over equal window counts.
- **MSM-FR-007:** Similarity shall be `exp(-sqrt(MMD_squared)/score_mapping_scale)`.
- **MSM-IF-001:** Result shall record MMD values, window/empty/exclusion counts, all kernel/configuration fields, and dependency lineage.
- **MSM-CFG-001:** All scales/bandwidth/weights/tolerance shall be finite positive; degree positive integer.
- **MSM-NFR-001:** Fixed inputs/configuration/library shall produce deterministic similarity in `(0,1]`.
- **MSM-ERR-001:** Invalid kernel/bounds/denominator/numerics shall fail; only tolerance-small negative MMD-squared maps to zero.
- **MSM-TST-001:** Tests shall compare path kernels/MMD with authoritative examples or independent implementation.

## Acceptance Criteria

- **MSM-AC-001:** Hand windows reproduce exact points, paths, kernels, MMD, score, and counts.
- **MSM-AC-002:** Empty, ordered-contrast, post-horizon, valid-negative-kernel, and tolerance fixtures behave exactly.
- **MSM-AC-003:** Invalid config/kernel/numerical/resource cases produce no successful result.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Windows](01_WINDOWS_PATHS.md) · [Kernel](02_KERNEL_MMD.md) · [Roadmap](ROADMAP.md)
