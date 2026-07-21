# Poisson Empirical Software Requirements Specification

## Requirements

- **TPE-FR-001:** Preparation shall count each valid reference packet's original Ethernet length exactly.
- **TPE-FR-002:** Table entries shall have unique ascending sizes and positive integer weights.
- **TPE-FR-003:** Length probability shall equal entry weight divided by total weight.
- **TPE-FR-004:** IAT and stopping shall follow Poisson family rules.
- **TPE-IF-001:** Prepared model shall identify generation-ready state and retain builder/candidate lineage, exact reference SHA-256, and complete frequency table.
- **TPE-CFG-001:** Only `arrival_rate_pps` shall be trainable; table, seed, and stop shall not.
- **TPE-NFR-001:** Fixed model and generator version shall reproduce events exactly.
- **TPE-ERR-001:** Unsupported link/length, empty/invalid table, or invalid model shall fail without skipped observations.
- **TPE-TST-001:** Fixtures shall distinguish captured from original length and verify exact counts.

## Acceptance Criteria

- **TPE-AC-001:** Reference fixture produces exact canonical table and source hash.
- **TPE-AC-002:** Golden seed produces exact categorical/timing sequence; invalid fixtures fail.
- **TPE-AC-003:** Preparation leaves builder bytes unchanged, applies candidate values before extraction, and publishes a distinct generation-ready model.

## Traceability

[Configuration](CONFIGS.md)

[SAD](SAD.md) · [Definition](00_MODEL_DEFINITION.md) · [Roadmap](ROADMAP.md)
