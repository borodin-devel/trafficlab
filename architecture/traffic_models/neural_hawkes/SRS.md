# Neural Hawkes Software Requirements Specification

## Requirements

- **TNH-FR-001:** The model shall condition each next event only on a bounded strictly earlier same-file history.
- **TNH-FR-002:** Strict causal attention shall prevent target or future event influence.
- **TNH-FR-003:** IAT law shall be a normalized zero-inflated log-normal mixture over `[0,infinity)` under schema 1.
- **TNH-FR-004:** Frame-length law shall be a normalized conditional truncated-normal mixture on `[60,1514]`.
- **TNH-FR-005:** Sampled continuous lengths shall map by round-half-to-even then exact range validation.
- **TNH-FR-006:** Fitting shall minimize deterministic mean time-plus-mark negative log likelihood plus configured regularization.
- **TNH-FR-007:** Checkpoint shall minimize finite validation total loss; exact tie selects earlier epoch.
- **TNH-FR-008:** Early stopping shall follow maximum/minimum epoch, validation interval, and patience controls.
- **TNH-FR-009:** Generation shall sample causally from empty history and append each accepted event before next sampling.
- **TNH-IF-001:** Generation-ready trained model shall retain builder digest, candidate identity/configuration, canonical weights/hash, checkpoint metrics, source assignment, seeds, and library lineage.
- **TNH-CFG-001:** Only declared high-level hyperparameters shall be genetically variable; learned weights shall not.
- **TNH-NFR-001:** Fixed supported inputs/configuration/seeds/versions shall reproduce preparation, fitting, checkpoint, weights, and events.
- **TNH-ERR-001:** Invalid data, likelihood, gradient, weight, schema, path, sample, or runtime shall fail without repair.
- **TNH-TST-001:** Tests shall verify causal masking, probability normalization, zero IAT, rounding, loss, checkpointing, and determinism.

## Acceptance Criteria

- **TNH-AC-001:** Synthetic hand examples match independent time/mark likelihood and causal-mask calculations.
- **TNH-AC-002:** Fixed tiny fixture training reproduces selected epoch, canonical weight hash, metrics, and generated events.
- **TNH-AC-003:** Leakage, invalid weight path/hash, non-finite values, unsupported runtime, and untrained generation fail.
- **TNH-AC-004:** Candidate hyperparameters are applied before split/window preparation and fitting while builder bytes remain unchanged; generation limits terminate pathological proposal streams without success.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [General information](00_GENERAL_INFO.md) · [Common rules](../NEURAL_MARKED_POINT_PROCESS.md) · [Roadmap](ROADMAP.md)
