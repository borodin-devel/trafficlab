# Frame-Size KS Software Requirements Specification

## Requirements

- **MFS-FR-001:** The method shall extract only original packet length from each scored Ethernet frame.
- **MFS-FR-002:** It shall retain valid lengths outside current generator support and ignore captured-byte contents.
- **MFS-FR-003:** It shall compute two-sided empirical KS distance and similarity `1-D`.
- **MFS-FR-004:** It shall neither use nor publish a p-value.
- **MFS-IF-001:** Each input shall contain at least one valid scored frame.
- **MFS-NFR-001:** Score shall be deterministic in `[0,1]`, higher better.
- **MFS-ERR-001:** Missing/inconsistent original length or library failure shall fail without fallback.
- **MFS-TST-001:** Tests shall prove byte/captured-length changes cannot alter sample when original length remains.

## Acceptance Criteria

- **MFS-AC-001:** Identical, disjoint, tied, unequal-count, truncated, and extended-length fixtures yield expected results.
- **MFS-AC-002:** Invalid/unsupported fixtures fail and no p-value is present.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Definition](00_METHOD_DEFINITION.md) · [Roadmap](ROADMAP.md)
