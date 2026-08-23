# Scientific artifact schema v4

These 13 Draft 2020-12 schemas describe the current public JSON artifacts.
Every root rejects unknown fields. Schema validation proves document shape;
Trafficlab additionally checks canonical bytes, cross-artifact identities,
lineage, arithmetic consistency, and scientific invariants in production code
and the offline Validation Study auditor.

## Schema catalog

| Schema | Artifact | Root fields |
| --- | --- | --- |
| `capture_metadata.schema.json` | Capture direction metadata | `interface`, `target_mac` |
| `best_model.schema.json` | Self-contained fitted model | capture/reference identities, estimator and seed policy, family, genes/bounds, fitted payload, final seed/limits, observation window, schema/version |
| `checkpoint.schema.json` | Resumable genetic-search state | input identities, family/search/similarity settings, population, best candidate, history, RNG, generation and termination state |
| `comparison_result.schema.json` | Published four-method comparison | `aggregate_score`, `input_identities`, `methods`, `observation_window_seconds` |
| `failure_outcome.schema.json` | Canonical expected failure | affected evidence, authority, correction, detail, evidence state, kind, stage, status |
| `study_environment.schema.json` | Accepted-study source/runtime/image environment | source and dependency identities, runtime versions, image IDs/references, compatibility decision |
| `study_lifecycle.schema.json` | Study cleanup proof | study ID, training and held-out lifecycle rows, phase capture-image lifecycle |
| `study_lineage.schema.json` | Typed accepted-bundle index and ownership maps | root artifact identities plus training, held-out, fresh-simulation, ownership, and lineage maps |
| `study_manifest.schema.json` | Accepted-bundle inventory | schema version and typed file entries |
| `study_prerequisite.schema.json` | Docker/Internet prerequisite evidence | study/URL, capability observation, commands, environment |
| `study_protocol.schema.json` | Frozen study protocol | IDs, seeds, workloads, repetitions, prerequisite identity, and selected model records |
| `study_report_input.schema.json` | Typed report arithmetic inputs | training, fresh, held-out, natural-variation, invalid-candidate, weight-analysis, and variance evidence |
| `study_report.schema.json` | Published report root | formula, exact report-input identity, and summary derived from the typed report-input model |

## Common records

| Field | Description |
| --- | --- |
| `sha256` | Lowercase digest of exact authoritative bytes. |
| `size` | Authoritative byte length paired with `sha256`. |
| `scientific_artifact_schema` | Shared scientific-artifact contract version, exactly `4` for these artifacts. |
| `schema_version` | Version of a particular study or manifest document format. |
| `max_packets` | Maximum events a generation attempt may emit. |
| `max_output_bytes` | Maximum encoded output size permitted for generation. |
| `max_wall_seconds` | Monotonic wall-time limit for generation. |
| `birth_generation`, `birth_index` | Stable two-part identity of a genetic candidate. |
| `workload` | Frozen study workload name: `short`, `streaming`, or `bursty`. |
| `repeat` | One-based independent training repetition. |

## Capture metadata

| Field | Description |
| --- | --- |
| `interface` | Captured container interface, currently `eth0`. |
| `target_mac` | Valid lowercase unicast MAC used for inbound/outbound classification. |

## Fitted model

| Field | Description |
| --- | --- |
| `capture_identity` | Identity of capture metadata used to parse and classify the reference. |
| `reference_identity` | Identity of the exact reference PCAPNG used for estimation. |
| `estimator_choices` | Map of named estimator policy decisions retained for interpretation. |
| `family` | Model-family discriminator selecting the fitted-payload union. |
| `genes` | Winning chromosome in registered family coordinate order. |
| `gene_bounds` | Per-coordinate `lower` and `upper` bounds, with integer bounds for integer genes. |
| `fitted` | Poisson empirical, Markov renewal, or MMPP runtime payload. |
| `final_limits` | Generation guard record used for independent final output. |
| `final_seed` | Seed used after selection for independent final generation. |
| `observation_window_seconds` | Positive reference-derived duration `W`. |
| `seed_policy` | Map of named RNG and scalar/weighted-sampling policy choices. |
| `scientific_artifact_schema` | Shared schema version. |
| `version` | Fitted-model payload version. |

`fitted.marks[]` contains `count`, `direction`, and `frame_length`. A Poisson
payload contains `base_rate`, `rate`, and `marks`; an MMPP payload contains
`q01`, `q10`, `lambda0`, `lambda1`, and `marks`. A Markov-renewal payload
contains `alpha`, `thresholds`, `states`, `transition_rows`, `conditional_iats`,
`global_iats`, `minimum_support`, `time_scale`, and `timing_diagnostics`.

## Genetic checkpoint

| Field | Description |
| --- | --- |
| `best` | Best fitness and `[birth_generation, birth_index]` identifier. |
| `capture_identity`, `experiment_identity`, `reference_identity` | Exact authoritative input identities. |
| `consecutive_stagnation` | Completed generations without the configured minimum improvement. |
| `families` | Family names, gene order, coordinate records, and per-family operators. |
| `family_priority` | Seed-derived ordering used for neutral family tie-breaking. |
| `generation` | Last fully completed generation. |
| `genetic` | Complete settings affecting search, reproduction, and termination. |
| `history` | Per-family and global completed-generation summaries. |
| `observation_window_seconds` | Reference-derived fit window. |
| `population` | Valid, invalid, or pending candidate records for exact resume. |
| `rng` | Engine, Python version, and exact serialized random state. |
| `scientific_artifact_schema` | Shared schema version. |
| `similarity` | Complete metric settings used by every trial. |
| `terminal_reason` | Null while resumable, otherwise the named stopping reason. |
| `trial_limits` | Generation limits applied to candidate simulations. |
| `trial_seeds` | Common evaluation seeds for every candidate. |

A coordinate contains `name`, `kind`, `lower`, and `upper`; operators contain
`crossover_probability`, `mutation_probability`, and `mutation_scale`.
Candidate records contain `identifier`, `family`, `genes`, `status`, `fitness`,
`invalid`, `trials`, and `duplicate_diagnostics`. A valid trial contains `seed`,
`aggregate_score`, four named `methods`, and `model_diagnostics`. History rows
contain `scope`, optional `family`, `generation`, candidate/valid counts, mean
and best fitness, and the best identifier.

## Comparison result

| Field | Description |
| --- | --- |
| `aggregate_score` | Weighted aggregate of the four method scores. |
| `input_identities` | Identities of capture metadata, reference/generated PCAPNG, and canonical similarity settings. |
| `methods` | Exactly one result each for frame-size KS, IAT KS, autocorrelation, and multiscale rate. |
| `observation_window_seconds` | Shared comparison window. |

Each method has `score`, `weight`, and `diagnostics`. Frame-size and IAT KS
diagnostics retain empirical-CDF `distance` and sample descriptions.
Autocorrelation diagnostics retain lags, weights, reference/generated ACF arrays,
absolute differences, counts, and discrepancies. Multiscale diagnostics retain
widths, bounded bin-cell counts, direction-separated packet/byte totals, scale
and feature discrepancies, weights, and the final discrepancy.

## Failure outcome

| Field | Description |
| --- | --- |
| `status` | Constant marker that the operation failed as an expected scientific or lifecycle outcome. |
| `stage` | Workflow stage that detected the failure. |
| `kind` | Stable machine-readable failure classification. |
| `detail` | Specific human-readable explanation. |
| `evidence_state` | Whether evidence is absent, partial, invalid, or otherwise unusable. |
| `affected_evidence` | Artifact or evidence class invalidated by the failure. |
| `authority` | Component or rule that owns the decision. |
| `corrective_action` | Deterministic next action; never a silent retry or fabricated result. |

## Validation Study environment and lifecycle

`study_environment.schema.json` fields:

| Field | Description |
| --- | --- |
| `source_commit`, `source_tree` | Exact clean Git commit and tree audited by the study. |
| `uv_lock_identity` | Size and digest of the dependency lock. |
| `python_implementation`, `python_version` | Recorded Python runtime identity. |
| `kernel_release`, `host_architecture` | Host kernel and architecture. |
| `docker_engine_version`, `docker_compose_version` | Docker runtime versions. |
| `capture_image_id`, `capture_image_reference`, `capture_tool_version` | Immutable capture image and tool identity. |
| `target_image_id`, `target_image_reference` | Immutable workload image identity. |
| `compatibility_decision` | `status` and `reason` for accepting or rejecting the environment. |
| `scientific_artifact_schema` | Shared schema version. |

`study_lifecycle.schema.json` fields:

| Field | Description |
| --- | --- |
| `schema_version` | Lifecycle document format version. |
| `study_id` | Unique immutable attempt identifier. |
| `training`, `held_out` | Lifecycle rows for every run; each row has `run_id`, directory, Compose `project_name`, and `cleanup_verified`. |
| `phase_capture_image` | Temporary phase tag/image ID plus post-cleanup inspect status and cleanup proof. |

## Validation Study index, lineage, and manifest

`study_lineage.schema.json` is the accepted bundle's `index.json` contract:

| Field | Description |
| --- | --- |
| `schema_version` | Index format version. |
| `environment`, `prerequisites`, `protocol`, `lifecycle`, `report_inputs`, `report` | Identities of the named root records. |
| `training` | Per-workload/repeat training lineage, directories, configs, reference identity, and capture/image lineage. |
| `fresh_simulation` | Per-workload/repeat model/reference/generated/comparison lineage and seed. |
| `held_out` | Per-workload held-out directory, training-model source, and capture lineage. |
| `ownership` | Map from every retained path to its exclusive owning evidence class. |
| `lineage` | Typed relationship record for every retained path. |

`study_manifest.schema.json` fields:

| Field | Description |
| --- | --- |
| `schema_version` | Manifest format version. |
| `files` | Canonically sorted complete retained-file inventory. |
| `files[].path` | Relative POSIX path. |
| `files[].sha256`, `files[].size` | Exact content identity. |
| `files[].owner` | Exclusive owner matching the index ownership map. |
| `files[].lineage` | Typed simple, repeated, held-out, configuration, prerequisite, or transfer relation. |

## Validation Study prerequisite and protocol

`study_prerequisite.schema.json` fields:

| Field | Description |
| --- | --- |
| `schema_version`, `study_id`, `url` | Record version, attempt identity, and exact operator-approved URL. |
| `capability` | HTTP status/range/length observation and canary digest proving endpoint suitability. |
| `commands` | Bounded Docker-matrix and Internet-smoke command records. |
| `environment` | Source, lock, capture/target image, and capture-tool identities. |

Each command records `kind`, logical `command`, exact `argv`, `exit_status`,
`status`, retained `stdout`, `stderr`, and `junit` identities/paths, plus test
counts (`total`, `passed`, `failed`, `errors`, `skipped`).

`study_protocol.schema.json` fields:

| Field | Description |
| --- | --- |
| `schema_version`, `study_id` | Protocol format and immutable attempt identity. |
| `candidate_id`, `destination_id` | Candidate and final publication identities. |
| `prerequisite_path` | Path and content identity of approved prerequisites. |
| `workloads`, `training_repetitions` | Frozen workload order/set and repetition count. |
| `selection_seeds`, `final_seed` | Common search evaluation seeds and independent final seed. |
| `model_selection` | Selection rule and one selected training-model record per workload. |

Each selected model stores `workload`, `repeat`, `training_directory`, and the
selected `best_model_identity`.

## Validation Study report inputs and report

`study_report_input.schema.json` fields:

| Field | Description |
| --- | --- |
| `formula` | Exact score and summary formula identifier/text used by the report. |
| `training` | Per-workload runtime, selection-fitness, winner counts, and descriptive/bootstrap statistics. |
| `fresh_simulation` | Per-workload fixed-seed same-reference scores. |
| `held_out` | Per-workload fixed-model independent-capture score and observation window. |
| `natural_variation` | Forward/reverse repeated-capture pairs and symmetric means. |
| `invalid_chromosome_diagnostics` | Invalid candidates, genes, identifiers, failure records, seeds, and trial limits. |
| `controlled_weight_analysis` | Baseline/alternative weights and aggregates with unchanged executed components and diagnostics. |
| `runtime_winner_variance` | Derived runtime and winner-family variability evidence. |

Shared score records contain `aggregate` and the four named method values.
Descriptive records contain `mean`, `sample_variance`, and an optional bootstrap
record. A bootstrap record fixes confidence level, method/statistic, seed,
generator and exact state, resample/sample counts, and lower/upper bounds.

`study_report.schema.json` has three roots: `formula` repeats the declared report
formula, `report_inputs_identity` binds the exact typed input bytes, and `summary`
uses the same complete `ValidationStudyReportInput` shape so an auditor can
independently recompute and compare every published value.
