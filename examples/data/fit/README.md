# Deterministic offline fitting fixture

This Docker-free fixture exercises production codecs and the real heterogeneous fitting path, including
checkpoint-compatible artifacts. Regenerate it with `uv run --locked python scripts/generate_fit_fixtures.py`;
verify every expected path and byte with `uv run --locked python scripts/generate_fit_fixtures.py --check`.

The reference contains 21 Ethernet events from timestamp 20.0 through 30.0, so the one normalized observation
window is exactly `W = 10.0` seconds. Registry metadata remains lexical for display, while master seed 73 derives
the neutral family priority `mmpp`, `markov_renewal`, `poisson_empirical` before any search draw. Population size is
6, with quota 2 per family, elite count 1, generation count 1 (evaluated generations 0 and 1), tournament size 2,
duplicate mutation attempts 1, selection seeds `[17]`, and the distinct final-validation seed 97. Resume is enabled
and early stopping is disabled.

Every family deliberately uses nondefault operators:

- `markov_renewal`: crossover 1.0, mutation 0.0, normalized scale 0.06.
- `mmpp`: crossover 0.45, mutation 0.0, normalized scale 0.08.
- `poisson_empirical`: crossover 0.35, mutation 0.0, normalized scale 0.07.

Zero ordinary mutation makes the different-family forced-mutation boundary directly observable in the integration
trace. Trial guards are 500 packets, 1,000,000 bytes, and 5.0 seconds; final guards are 1,000 packets, 2,000,000
bytes, and 10.0 seconds. The checked checkpoint is terminal generation 1, `ga_history.csv` is its exact derived
projection, and `best_model.json` is the independently final-validated winner.

## Files

| File | Purpose |
| --- | --- |
| `experiment.toml` | Canonical effective configuration used for the offline fit. |
| `capture.json` | Interface and target MAC used to classify the reference frames. |
| `reference.pcapng` | Hand-defined 21-event reference trace. |
| `checkpoint.json` | Complete resumable genetic-search state at terminal generation 1. |
| `ga_history.csv` | Exact tabular projection of the checkpoint history records. |
| `best_model.json` | Independently final-validated winning model. |

## JSON fields

`capture.json` contains `interface`, the captured interface name, and `target_mac`, the lowercase target MAC used
for inbound/outbound classification. `best_model.json` uses the shared fitted-model fields documented in
[`../README.md`](../README.md#best_modeljson-fields) and validated by
[`../../schemas/scientific-artifact-v4/best_model.schema.json`](../../schemas/scientific-artifact-v4/best_model.schema.json).

### `checkpoint.json` root fields

| Field | Description |
| --- | --- |
| `scientific_artifact_schema` | Shared public artifact schema version. |
| `experiment_identity`, `reference_identity`, `capture_identity` | SHA-256 and byte size of each authoritative fit input. |
| `observation_window_seconds` | Reference-derived fitting and generation duration `W`. |
| `generation` | Last fully evaluated generation stored in the checkpoint. |
| `terminal_reason` | Why fitting stopped, such as the configured hard generation limit or early stopping. |
| `consecutive_stagnation` | Count of successive completed generations without the required improvement. |
| `family_priority` | Deterministically seeded family ordering used to break otherwise equal choices. |
| `families` | Registered coordinate, bound, gene-order, and genetic-operator metadata for each enabled family. |
| `genetic` | Search settings that affect initialization, selection, reproduction, termination, and resume. |
| `trial_limits` | Packet, output-byte, and wall-time guards applied to each candidate trial. |
| `trial_seeds` | Common deterministic evaluation seeds used for every candidate. |
| `similarity` | Complete metric settings and method weights used to calculate candidate fitness. |
| `population` | Current candidate records, including valid, invalid, and pending states. |
| `best` | Best candidate's `fitness` and two-part `identifier`. |
| `history` | Per-generation family and global aggregate records from which `ga_history.csv` is derived. |
| `rng` | Named Python RNG engine, Python version, and exact serialized state needed for resume. |

Each `families[]` row contains `name`, `gene_order`, `operators`, and `coordinates`; every coordinate has `name`,
`kind` (`linear`, `log`, or `integer`), `lower`, and `upper`. Each `population[]` row contains `identifier`
(`[birth_generation, birth_index]`), `family`, `genes`, `status`, `fitness`, `invalid`, `trials`, and bounded
`duplicate_diagnostics`. Each trial stores its `seed`, `aggregate_score`, four named method results, and model
diagnostics. Each history row stores `scope`, optional `family`, `generation`, candidate/valid counts, mean and best
fitness, and the best identifier. The exact union and expected-failure records are defined by
[`checkpoint.schema.json`](../../schemas/scientific-artifact-v4/checkpoint.schema.json).
