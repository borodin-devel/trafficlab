# Markov Renewal Software Requirements Specification

## Requirements

- **TMR-FR-001:** Preparation shall derive `(IAT_i, original_length_i)` from each frame after the first in recorded order.
- **TMR-FR-002:** It shall reject negative/non-finite IAT and invalid/non-Ethernet original lengths; zero IAT shall remain valid.
- **TMR-FR-003:** Every observation shall belong to exactly one deterministic state.
- **TMR-FR-004:** Automatic quantile shall not split ties and shall produce requested nonempty category counts or fail.
- **TMR-FR-005:** Automatic exact shall create one state per distinct observed pair.
- **TMR-FR-006:** Automatic cluster shall use normalized `log1p(IAT)`/length vectors and canonical cluster ordering.
- **TMR-FR-007:** Manual ranges shall be finite, gap-free, nonoverlapping, and cover every observation exactly once.
- **TMR-FR-008:** State emissions shall be observed pairs with positive counts; transitions shall represent observed adjacent states.
- **TMR-FR-009:** A state without outgoing transition shall restart from all-observation start weights.
- **TMR-FR-010:** Generation shall enforce exactly one count or duration stop mode.
- **TMR-FR-011:** Preparation shall apply and validate candidate complexity controls before state construction without mutating the builder.
- **TMR-IF-001:** Prepared model shall identify generation-ready state, be self-contained, and record builder/candidate lineage and exact reference SHA-256.
- **TMR-IF-002:** Generation shall enforce explicit positive packet, complete-output-byte, and proposal limits in addition to the model stop.
- **TMR-CFG-001:** Only documented automatic complexity counts shall be trainable.
- **TMR-NFR-001:** Fixed source/configuration/seed/library versions shall reproduce state assignment, serialization, and events.
- **TMR-ERR-001:** Invalid observations, state construction, learned data, or samples shall fail without repair.
- **TMR-TST-001:** Tests shall cover every mode, dead-end restart, hashes, stops, and deterministic ordering.

## Acceptance Criteria

- **TMR-AC-001:** Hand-calculated fixtures reproduce exact observations, states, counts, transitions, and canonical TOML.
- **TMR-AC-002:** Golden seed reproduces exact event sequence without source-file access.
- **TMR-AC-003:** Gap, overlap, impossible buckets/clusters, invalid metadata, and corrupt learned state fail.
- **TMR-AC-004:** Candidate complexity changes affect preparation while builder bytes remain unchanged, and zero-IAT duration proposals terminate at the explicit proposal limit without success.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [General information](00_GENERAL_INFO.md) · [Roadmap](ROADMAP.md)
