# Traffic-model generation fixture

This focused pair proves that a checked fitted model can be loaded and used to
produce deterministic PCAPNG bytes without fitting again. Both files are derived
from the parent `capture.json`, `reference.pcapng`, and `minimal.toml` inputs.

| File | Purpose |
| --- | --- |
| `best_model.json` | Self-contained Poisson empirical model and its reference, capture, seed, bounds, and generation-limit lineage. |
| `generated.pcapng` | Final-seed trace generated from that model and round-tripped through the production PCAPNG codec. |

Regenerate or check the pair from the repository root:

```bash
uv run --locked python scripts/generate_model_fixtures.py
uv run --locked python scripts/generate_model_fixtures.py --check
```

## `best_model.json` fields

| Field | Description |
| --- | --- |
| `scientific_artifact_schema` | Shared public artifact schema version. |
| `version` | Fitted-model payload version. |
| `family` | Registered traffic-model family used by the runtime generator. |
| `genes` | Ordered fitted chromosome values. |
| `gene_bounds` | Coordinate-name map of fitting lower and upper bounds. |
| `fitted` | Complete family-specific runtime parameters; this fixture stores `base_rate`, fitted `rate`, and empirical `marks`. |
| `estimator_choices` | Names of the first-event, mark, and rate estimation policies. |
| `seed_policy` | Names of the RNG and sampling algorithms. |
| `final_seed` | Independent deterministic seed used for this final trace. |
| `final_limits` | Packet, output-byte, and wall-time generation guards. |
| `observation_window_seconds` | Reference-derived duration into which events are generated. |
| `reference_identity` | SHA-256 and byte size of the source reference PCAPNG. |
| `capture_identity` | SHA-256 and byte size of the source capture metadata. |

Each `fitted.marks[]` row contains the observed `direction`, `frame_length`, and
`count`. See the complete family-specific dictionary in
[`../README.md`](../README.md#best_modeljson-fields) and the machine-readable
contract in
[`../../schemas/scientific-artifact-v4/best_model.schema.json`](../../schemas/scientific-artifact-v4/best_model.schema.json).
