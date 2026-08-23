# Retained scientific-stack run artifacts

These are the exact artifacts from the real run summarized by
[`../example_run.json`](../example_run.json). That evidence document records the
SHA-256 digest and size of every authoritative file; this README is explanatory
and is not part of that artifact identity map.

| File | Purpose |
| --- | --- |
| `experiment.toml` | Realized configuration snapshot used by the run. |
| `capture.json` | Captured interface and target MAC. |
| `reference.pcapng` | Ordered real target traffic captured from the approved workload. |
| `checkpoint.json` | Terminal genetic-search state. |
| `ga_history.csv` | Exact checkpoint-history projection. |
| `best_model.json` | Selected and independently final-validated fitted model. |
| `generated.pcapng` | Final-seed synthetic trace from the selected model. |
| `similarity.json` | Published four-method comparison result. |
| `run.log` | Canonical structured run-event log. |

## JSON field dictionaries

| File | Fields |
| --- | --- |
| `capture.json` | `interface` names the captured interface; `target_mac` is the lowercase MAC used for direction classification. |
| `best_model.json` | Schema/version, family, genes/bounds, fitted payload, estimator/seed policies, final seed/limits, observation window, and reference/capture identities. |
| `checkpoint.json` | Input identities, family/search/similarity settings, trial seeds/limits, population, best candidate, history, RNG state, current generation, stagnation count, and terminal reason. |
| `similarity.json` | Aggregate score, observation window, input identities, and weighted score/diagnostics for frame-size KS, IAT KS, autocorrelation, and multiscale rate. |

The detailed descriptions are shared with the deterministic fixtures:

- [`../../data/README.md`](../../data/README.md#best_modeljson-fields) describes
  fitted-model and comparison fields.
- [`../../data/fit/README.md`](../../data/fit/README.md#checkpointjson-root-fields)
  describes checkpoint fields and nested candidate records.
- [`../../schemas/scientific-artifact-v4/README.md`](../../schemas/scientific-artifact-v4/README.md)
  catalogs the exact public schemas.

Verify the artifact identities, scientific derivations, configuration snapshot,
source binding, and empty cleanup inventory with:

```bash
uv run --locked python scripts/check_scientific_stack_example.py --check
```
