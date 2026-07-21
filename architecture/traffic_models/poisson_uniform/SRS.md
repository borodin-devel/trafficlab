# Poisson Uniform Software Requirements Specification

## Requirements

- **TPU-FR-001:** IATs shall be independent exponential samples with finite positive `arrival_rate_pps`.
- **TPU-FR-002:** Lengths shall be independent discrete-uniform integers over inclusive configured bounds.
- **TPU-FR-003:** Length bounds shall satisfy `60 <= minimum <= maximum <= 1514`.
- **TPU-FR-004:** Count and duration stop behavior shall follow Poisson family rules.
- **TPU-IF-001:** Builder and generation-ready files shall identify `poisson_uniform`, schema version, lifecycle state, parameters, seed, and exactly one stop mode; the ready file shall retain builder/candidate lineage.
- **TPU-CFG-001:** Only arrival rate and length bounds shall be trainable.
- **TPU-NFR-001:** Fixed model and generator version shall reproduce events exactly.
- **TPU-ERR-001:** Invalid schema/value/sample shall fail without clamping or partial success.
- **TPU-TST-001:** Tests shall cover support endpoints, one-value range, first arrival, and both stops.

## Acceptance Criteria

- **TPU-AC-001:** Golden seeds reproduce exact IAT/length sequence and canonical model serialization.
- **TPU-AC-002:** Boundary and invalid files pass/fail exactly under schema rules.
- **TPU-AC-003:** Finalization leaves builder bytes unchanged, publishes a distinct generation-ready model, and generation rejects the builder.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Definition](00_MODEL_DEFINITION.md) · [Roadmap](ROADMAP.md)
