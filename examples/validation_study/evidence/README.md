# Accepted Validation Study evidence

Each visible child directory is a complete accepted, offline-auditable study
bundle. The current bundle is identified in [`../README.md`](../README.md);
older accepted bundles remain immutable predecessors. Hidden `.candidates/` and
temporary paths are publication workspaces, not accepted evidence.

Do not add, remove, rename, or edit files inside an accepted bundle. Its
`manifest.json` binds every retained path and `index.json` binds ownership and
lineage. This README lives above those bundles and is intentionally outside
their manifests.

## Bundle map

| Path | Purpose |
| --- | --- |
| `environment.json` | Clean source, lock, host/runtime, and immutable image identity. |
| `prerequisites.json` | Successful bounded Docker/Internet prerequisite record. |
| `protocol.json` | Frozen workloads, repetitions, seeds, and training-model selection. |
| `lifecycle.json` | Per-run and temporary-image cleanup proof. |
| `report_inputs.json` | Typed arithmetic inputs independently recomputed by the auditor. |
| `report.json` | Published summary bound to the exact report-input bytes. |
| `index.json` | Root artifact identities plus complete typed ownership and lineage maps. |
| `manifest.json` | Canonical path/size/digest/owner/lineage inventory. |
| `configs/` | Portable and environment-realized training configurations. |
| `training/` | Nine strict fit artifact trees: three workloads by three repetitions. |
| `fresh_simulation/` | Fixed-final-seed same-reference model-generation records. |
| `held_out/` | Three independent captures evaluated with already selected models. |
| `prerequisites/` | Exact command argv, outputs, JUnit records, and test status summaries. |
| `observations/` | Parsed retained HTTP transfer-header observations. |

The machine-readable public contracts are under
[`../../schemas/scientific-artifact-v4/`](../../schemas/scientific-artifact-v4/),
with a complete root-field catalog in that directory's
[`README.md`](../../schemas/scientific-artifact-v4/README.md).

## Root JSON fields

### `environment.json`

| Field | Description |
| --- | --- |
| `scientific_artifact_schema` | Shared public artifact schema version. |
| `source_commit`, `source_tree` | Exact clean Git source revision and tree. |
| `uv_lock_identity` | SHA-256 and byte size of the dependency lock. |
| `python_implementation`, `python_version` | Python runtime identity. |
| `kernel_release`, `host_architecture` | Host kernel and architecture. |
| `docker_engine_version`, `docker_compose_version` | Docker runtime versions. |
| `capture_image_id`, `capture_image_reference`, `capture_tool_version` | Capture image and tool identity. |
| `target_image_id`, `target_image_reference` | Workload image identity. |
| `compatibility_decision` | Accepted/rejected status and its reason. |

### `prerequisites.json`

| Field | Description |
| --- | --- |
| `schema_version`, `study_id`, `url` | Record version, immutable attempt ID, and exact approved endpoint. |
| `capability` | Canary digest plus HTTP status, content length/range, and total object size. |
| `commands` | Docker-matrix and Internet-smoke command records. |
| `environment` | Source/lock plus capture/target image and capture-tool identities. |

Every `commands[]` row contains `kind`, logical `command`, exact `argv`,
`exit_status`, `status`, retained `stdout`, `stderr`, and `junit` path/identity
records, and `tests` counts (`total`, `passed`, `failed`, `errors`, `skipped`).

### `protocol.json`

| Field | Description |
| --- | --- |
| `schema_version`, `study_id` | Protocol format and attempt identity. |
| `candidate_id`, `destination_id` | Candidate and accepted publication identifiers. |
| `prerequisite_path` | Relative prerequisite path and exact identity. |
| `workloads`, `training_repetitions` | Frozen workload set/order and independent repetition count. |
| `selection_seeds`, `final_seed` | Common fitting seeds and independent final-generation seed. |
| `model_selection` | Training-only selection rule and selected model per workload. |

Each selected model records `workload`, `repeat`, `training_directory`, and
`best_model_identity`.

### `lifecycle.json`

| Field | Description |
| --- | --- |
| `schema_version`, `study_id` | Lifecycle format and attempt identity. |
| `training`, `held_out` | Per-run `run_id`, directory, Compose project name, and cleanup verification. |
| `phase_capture_image` | Temporary phase tag/image ID, cleanup flag, and post-cleanup inspect exit status. |

### `index.json`

| Field | Description |
| --- | --- |
| `schema_version` | Index format version. |
| `environment`, `prerequisites`, `protocol`, `lifecycle`, `report_inputs`, `report` | Exact identities of the root records. |
| `training` | Per-workload/repeat directories, configs, reference identity, and capture/image lineage. |
| `fresh_simulation` | Per-workload/repeat model, reference, generated, comparison, seed, and path lineage. |
| `held_out` | Per-workload directory, selected training directory, and capture/image lineage. |
| `ownership` | Exclusive evidence owner for every retained relative path. |
| `lineage` | Typed relationship for every retained path. |

### `manifest.json`

| Field | Description |
| --- | --- |
| `schema_version` | Manifest format version. |
| `files` | Canonically path-sorted complete retained-file inventory. |
| `files[].path` | Bundle-relative POSIX path. |
| `files[].sha256`, `files[].size` | Exact file identity. |
| `files[].owner` | Owner that must match `index.json`. |
| `files[].lineage` | Typed simple, repeated, held-out, configuration, prerequisite, or transfer relationship. |

### `report_inputs.json` and `report.json`

| Field | Description |
| --- | --- |
| `formula` | Exact formula identifier/text used for report arithmetic. |
| `training` | Per-workload runtime, selection-fitness, winner-count, descriptive, and bootstrap evidence. |
| `fresh_simulation` | Same-reference fixed-seed scores for selected models. |
| `held_out` | Independent-capture fixed-model scores and observation windows. |
| `natural_variation` | Forward/reverse repeated-capture comparisons and symmetric means. |
| `invalid_chromosome_diagnostics` | Invalid candidates, limits, genes, seeds, and canonical failure outcomes. |
| `controlled_weight_analysis` | Baseline/alternative aggregation with unchanged components and diagnostics. |
| `runtime_winner_variance` | Derived variability evidence for runtimes and winning families. |

`report_inputs.json` consists of those eight fields. `report.json` contains
`formula`, `report_inputs_identity`, and `summary`; `summary` repeats the full
typed input shape. Score objects contain `aggregate` and all four method values.
Descriptive objects contain `mean`, `sample_variance`, and optional bootstrap
metadata/state/bounds.

## JSON files below the root

| File pattern | Fields and meaning |
| --- | --- |
| `training/*/*/capture.json`, `held_out/*/capture.json` | `interface` and direction-classifying `target_mac`. |
| `training/*/*/best_model.json` | Fitted-model schema fields documented in [`../../data/README.md`](../../data/README.md#best_modeljson-fields). |
| `training/*/*/checkpoint.json` | `scientific_artifact_schema`, `capture_identity`, `experiment_identity`, `reference_identity`, `observation_window_seconds`, `generation`, `terminal_reason`, `consecutive_stagnation`, `families`, `family_priority`, `genetic`, `trial_limits`, `trial_seeds`, `similarity`, `population`, `best`, `history`, and `rng`; details are in [`../../data/fit/README.md`](../../data/fit/README.md#checkpointjson-root-fields). |
| `training/*/*/similarity.json`, `held_out/*/similarity.json` | Aggregate, input identities, four method scores/weights, and diagnostics documented in [`../../data/README.md`](../../data/README.md#similarityjson-fields). |
| `fresh_simulation/*/rN.json` | `workload`, `repeat`, `seed`, `training_directory`, `training_model_identity`, `reference_identity`, `generated_identity`, `comparison_identity`, and retained artifact `path`. |
| `held_out/*/record.json` | `workload`, `seed`, `observation_window_seconds`, `training_directory`, `training_model_identity`, `reference_identity`, `capture_identity`, `generated_identity`, `comparison_identity`, and `capture_lineage`. |
| `observations/**/*.headers.json` | `workload`, `run_id`, `scope`, `transfer_index`, `requested_start`, `requested_end`, HTTP `status`, `content_range`, `content_length`, and retained `header_identity`. |
| `prerequisites/*.command.json` | Exact command `argv`. |
| `prerequisites/*.status.json` | Command `exit_status` and `tests` count object. |

All repeated content identities use `sha256` and `size`. A capture-lineage object
binds capture and target image IDs/references, capture-tool version, and capture
metadata identity. No record authorizes selective reruns or reuse of a failed
study ID.

## Offline audit

Audit in the source revision recorded by `environment.json`, using regular-file
copies as described in the parent instructions:

```bash
UV_OFFLINE=1 uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/STUDY_ID --repository .
```

The audit is read-only: it must not contact the endpoint, start Docker,
regenerate artifacts, or mutate the bundle.
