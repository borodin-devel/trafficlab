# Basic Generational Software Requirements Specification

## Requirements

- **GBG-FR-001:** Population zero shall contain every allowed model's normal-builder-value baseline before deterministic round-robin filling.
- **GBG-FR-002:** Every complete generation shall have configured population size and be fully evaluated before stopping/reproduction.
- **GBG-FR-003:** Failed candidates shall be ineligible to parent, elite, or win.
- **GBG-FR-004:** Tournaments shall sample distinct eligible candidates without replacement and use documented reduced effective size.
- **GBG-FR-005:** Elitism shall copy best valid candidates into leading next-generation slots.
- **GBG-FR-006:** Crossover shall use compatible same-name models only and choose each trainable parameter from one complete parent.
- **GBG-FR-007:** Each non-elite shall receive exactly one model-replacement, local-mutation, or no-mutation decision.
- **GBG-FR-008:** Whole offspring retries shall not exceed `offspring_attempt_limit`.
- **GBG-FR-009:** Stopping shall occur only after complete generation evaluation and use enabled generation, target, and no-improvement conditions.
- **GBG-IF-001:** Inputs shall contain ordered allowed models, immutable normal-builder baselines, search domains, method score metadata, and validated candidate results; preparation shall occur only after candidate values are applied.
- **GBG-CFG-001:** All probabilities, counts, search fields, stopping values, and seed shall validate before initialization.
- **GBG-NFR-001:** Fixed inputs and recorded RNG version shall produce identical decisions and lineage.
- **GBG-ERR-001:** No valid candidate or attempt exhaustion shall fail without winner publication.
- **GBG-TST-001:** Tests shall cover every draw branch, retry, tie, score direction, and stopping condition.

## Acceptance Criteria

- **GBG-AC-001:** Golden fixed-seed runs reproduce every candidate, parent, operator, stop reason, and winner.
- **GBG-AC-002:** Invalid/failing-candidate matrices never select an ineligible candidate and fail exactly when no valid path remains.
- **GBG-AC-003:** Property tests preserve population size, parameter domains, same-model crossover, and attempt limits.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Algorithm stages](00_REPRESENTATION.md) · [Roadmap](ROADMAP.md)
