# Marked Point-Process Diffusion Software Requirements Specification

## Requirements

- **TPD-FR-001:** Preparation shall create nonoverlapping per-file windows with positive finite width and explicit trailing policy.
- **TPD-FR-002:** Window representation shall use a prefix mask/count and padded active `(log1p(IAT),length)` values; overflow shall fail, not truncate.
- **TPD-FR-003:** First window IAT shall be measured from window start and no sequence shall cross files.
- **TPD-FR-004:** Categorical head shall sample count `0..max_events_per_window` before continuous reverse diffusion.
- **TPD-FR-005:** Active values shall follow configured Gaussian forward process and all-step epsilon-predicting DDPM reverse equations.
- **TPD-FR-006:** Inactive rows shall receive no noise and emit no event.
- **TPD-FR-007:** A complete decoded window shall satisfy finite nonnegative IATs and strict `sum(IAT) < window_width` or fail without repair.
- **TPD-FR-008:** Length shall map through sigmoid scaling and round-half-to-even into `[60,1514]`.
- **TPD-FR-009:** Fitting loss shall combine masked active noise MSE, categorical count cross-entropy, and configured regularization.
- **TPD-FR-010:** Checkpoint and early stopping shall use deterministic finite validation total loss and documented controls.
- **TPD-FR-011:** Generation shall emit only complete accepted windows under window-count or duration stop.
- **TPD-IF-001:** Generation-ready trained model shall retain builder digest, candidate identity/configuration, weights/hash, count head, metrics, schedule, source/split/seed/library lineage.
- **TPD-CFG-001:** Learned weights shall never be genetic parameters.
- **TPD-NFR-001:** Fixed supported inputs/configuration/seeds/versions shall reproduce windows, fitting, weights, and generation.
- **TPD-ERR-001:** Invalid mask/count/schedule/numerics/support/schema/path shall fail without clipping, trimming, resampling, or partial emission.
- **TPD-TST-001:** Tests shall verify equations, masks, count law, loss, schedules, support, conversion, and determinism independently.

## Acceptance Criteria

- **TPD-AC-001:** Hand examples reproduce forward/reverse updates, mask behavior, count loss, and complete-window support decisions.
- **TPD-AC-002:** Tiny fixed fixture reproduces checkpoint, weight hash, sampled counts/windows, and generated events.
- **TPD-AC-003:** Overflow, non-prefix mask, unsupported schedule/sampler, invalid support, weight/path/hash, and non-finite cases fail.
- **TPD-AC-004:** Candidate hyperparameters are applied before split/window preparation and fitting while builder bytes remain unchanged; generation limits terminate pathological proposal streams without success.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [General information](00_GENERAL_INFO.md) · [Common rules](../NEURAL_MARKED_POINT_PROCESS.md) · [Roadmap](ROADMAP.md)
