# Traffic Generation Software Requirements Specification

## Scope

Traffic generation renders model events into one valid synthetic PCAPNG. The
selected traffic model owns event mathematics; the application owns files and
packet construction.

## Requirements

- **TGN-FR-001:** The application shall validate one complete generation-ready model file before sampling and shall reject builders or transient candidates.
- **TGN-FR-002:** It shall resolve only a mature registered model named by that file.
- **TGN-FR-003:** It shall render generated timestamps and original frame lengths into valid PCAPNG with a fixed documented Ethernet template.
- **TGN-FR-004:** It shall support deterministic development-only randomized source traffic through an explicitly named model or mode once specified.
- **TGN-IF-001:** CLI shall require explicit model-file and output paths plus positive `--max-packets`, `--max-output-bytes`, and `--max-proposals` values with no defaults.
- **TGN-IF-002:** Successful output shall be exactly one closed validated PCAPNG with model, seed, implementation, and input lineage; detached status shall carry its content and launch digests.
- **TGN-CFG-001:** Seed and stopping controls shall come from validated model schema unless an owner defines explicit CLI override.
- **TGN-NFR-001:** Fixed model, seed, versions, and settings shall produce deterministic packet order and bytes.
- **TGN-NFR-002:** Every invocation shall remain bounded by packet, complete-output-byte, and accepted/rejected/resampled/boundary-crossing proposal limits.
- **TGN-ERR-001:** Invalid event, model, stopping state, renderer, or file failure shall publish no successful partial PCAPNG.
- **TGN-ERR-002:** Reaching any resource limit before the model stop completes shall fail without a published file or successful status and shall not truncate output.
- **TGN-TST-001:** Tests shall use offline event fixtures and no live network.

## Acceptance Criteria

- **TGN-AC-001:** Every mature model fixture produces PCAPNG whose decoded timestamps and lengths equal expected events.
- **TGN-AC-002:** Fixed-seed repeated generation produces identical content hashes.
- **TGN-AC-003:** Invalid/stub model and injected write failures expose no successful output.
- **TGN-AC-004:** Boundary fixtures stop before exceeding each limit, and duration generation terminates under an infinite zero-IAT proposal stream at `max_proposals` without success.

## Traceability

[SAD](SAD.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md) ·
[Traffic-model lifecycle](../../traffic_models/README.md#model-lifecycle)
