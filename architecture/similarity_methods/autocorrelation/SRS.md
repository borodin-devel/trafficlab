# Autocorrelation Software Requirements Specification

## Requirements

- **MAC-FR-001:** Method shall form aligned `log1p(IAT)` and original-length sequences.
- **MAC-FR-002:** It shall compute documented sample autocorrelation for every configured positive distinct lag.
- **MAC-FR-003:** Per-feature lag distance shall be absolute reference/generated correlation difference divided by two.
- **MAC-FR-004:** Exact unit-sum feature and lag weights shall combine distance; similarity shall be `1-D`.
- **MAC-IF-001:** Result shall record every lag/correlation/component, weights, raw distance, counts, and configuration.
- **MAC-CFG-001:** Lags shall be ordered, nonempty, distinct, positive integers; all weights positive exact unit sums.
- **MAC-NFR-001:** Score shall be deterministic in `[0,1]`, higher better.
- **MAC-ERR-001:** Constant series, insufficient observations, invalid config, or non-finite calculation shall fail.
- **MAC-TST-001:** Tests shall independently calculate positive, negative, zero, and mixed correlations.

## Acceptance Criteria

- **MAC-AC-001:** Hand sequences reproduce exact per-lag correlations, distances, and weighted score.
- **MAC-AC-002:** Constant, short, invalid-lag/weight, and malformed fixtures fail without partial lags.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Definition](00_METHOD_DEFINITION.md) · [Roadmap](ROADMAP.md)
