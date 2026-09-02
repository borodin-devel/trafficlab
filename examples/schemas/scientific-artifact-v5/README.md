# Scientific artifact schema v5

These 13 Draft 2020-12 schemas describe the current public JSON artifacts.
Every root rejects unknown fields. Runtime validation additionally enforces
canonical bytes, cross-artifact identities, lineage, arithmetic consistency,
and scientific invariants.

Schema 5 publishes the current seven fitted-model families, eight-method
fitness/checkpoint records, and the strict comparison result. Its fitness
registry is `autocorrelation`, `frame_size_ks`, `iat_ks`, `multiscale_rate`,
`cramer_von_mises`, `anderson_darling`, `jensen_shannon`, and
`approximate_mmd`.

The catalog contains schemas for best models, capture metadata, checkpoints,
comparison results, failure outcomes, and the eight validation-study evidence
roots. `scientific_artifact_schema` is exactly `5` in current best models and
checkpoints. Retained schema-4 validation-study evidence remains historical;
it is not migrated or rewritten by this directory.

Regenerate or verify the complete deterministic set with:

```bash
uv run --locked python scripts/generate_artifact_schemas.py
uv run --locked python scripts/generate_artifact_schemas.py --check
```
