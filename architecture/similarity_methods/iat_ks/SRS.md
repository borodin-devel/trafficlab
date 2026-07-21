# IAT KS Software Requirements Specification

## Requirements

- **MIK-FR-001:** The method shall derive each IAT from consecutive recorded frames after timestamp resolution/offset interpretation.
- **MIK-FR-002:** It shall preserve zero IAT and reject negative/non-finite IAT without sorting.
- **MIK-FR-003:** It shall compute two-sided empirical KS distance and similarity `1-D`.
- **MIK-FR-004:** It shall neither use nor publish a p-value.
- **MIK-IF-001:** Each input shall contain at least two valid Ethernet frames.
- **MIK-NFR-001:** Score shall be deterministic in `[0,1]`, higher better.
- **MIK-ERR-001:** Invalid metadata/library calculation shall fail without fallback.
- **MIK-TST-001:** Tests shall compare against authoritative KS examples/library.

## Acceptance Criteria

- **MIK-AC-001:** Identical, disjoint, tied, zero-IAT, and unequal-count fixtures yield expected distances/scores/counts.
- **MIK-AC-002:** Backward/malformed/unsupported fixtures fail and no result contains a p-value.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Definition](00_METHOD_DEFINITION.md) · [Roadmap](ROADMAP.md)
