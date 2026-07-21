# Model Creation Software Requirements Specification

## Scope

Model creation serves standalone users and genetic training. It creates normal
starting model files but does not fit, mutate, generate, or score.

## Requirements

- **MCR-FR-001:** The application shall resolve one registered traffic-model name.
- **MCR-FR-002:** It shall create that model's complete normal builder values.
- **MCR-FR-003:** It shall validate and atomically publish the absent `DIR/NAME.toml` destination as an immutable builder.
- **MCR-IF-001:** CLI shall require explicit model name and output directory.
- **MCR-IF-002:** Output shall identify builder state, model, and schema version and satisfy the selected model's builder validator.
- **MCR-IF-003:** Detached successful status shall bind the builder bytes and immutable attempt launch record; the builder shall contain no self-digest.
- **MCR-CFG-001:** No implicit model or output location shall be selected.
- **MCR-NFR-001:** Identical model/version shall produce deterministic canonical serialization.
- **MCR-ERR-001:** Unknown model, invalid defaults, existing conflict, or publication failure shall produce no successful model.
- **MCR-TST-001:** Tests shall invoke every registered model builder in temporary directories.

## Acceptance Criteria

- **MCR-AC-001:** Every mature registered model's normal file passes its own validator and expected canonical fixture.
- **MCR-AC-002:** Stub/unknown models and injected publication failures expose no successful file or status.
- **MCR-AC-003:** A published builder remains byte-identical through candidate preparation and is rejected as traffic-generation input.

## Traceability

[SAD](SAD.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md) ·
[Traffic-model lifecycle](../../traffic_models/README.md#model-lifecycle)
