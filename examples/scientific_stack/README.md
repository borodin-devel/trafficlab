# Scientific-stack evidence

This directory retains deterministic proof-of-concept decisions, performance
measurements, source-reduction evidence, and one real Trafficlab run used to
validate the adopted scientific stack. The files are evidence snapshots, not
runtime configuration defaults.

## Contents and checks

| File | Purpose | Check command |
| --- | --- | --- |
| `benchmark.json` | Scalar-versus-NumPy correctness, time, and peak-RSS comparison. | `uv run --locked python scripts/benchmark_scientific_stack.py --check` |
| `code_reduction.json` | Git-derived executable-line inventories before and after stack adoption. | `uv run --locked python scripts/measure_scientific_stack_reduction.py --check` |
| `mmpp_cases.json` | Hand likelihood, extreme-value, recovery, and held-out MMPP probe results. | `uv run --locked python scripts/run_scientific_stack_probes.py --probe mmpp --check` |
| `pymoo_cases.json` | Immutable schema-3 optimizer evidence from the four-method scientific stack. | `uv run --locked python scripts/run_scientific_stack_probes.py --probe pymoo --check` |
| `pymoo_schema5_cases.json` | Current schema-4 probe document for optimizer correctness under schema-5 eight-method settings. | `uv run --locked python scripts/run_scientific_stack_probes.py --probe pymoo-v5 --check` |
| `scapy_production_benchmark.json` | Non-gating production codec time and memory measurements. | `uv run --locked python scripts/benchmark_scapy_production.py --check` |
| `experiment.toml` | Portable configuration for the retained real run. | `uv run --locked python scripts/check_scientific_stack_example.py --check` |
| `example_run.json` | Source, environment, image, execution, artifact, result, and cleanup evidence for that run. | Same retained-run check |
| [`example_run_artifacts/`](example_run_artifacts/) | Exact run artifacts bound by `example_run.json`. | Same retained-run check |

The benchmark files contain measurements from their recorded environments;
checking recomputes the canonical evidence expected by each owner. Do not infer
portable performance guarantees from one host.

## Shared evidence fields

| Field | Description |
| --- | --- |
| `schema_version` | Version of the individual evidence format. |
| `passed` | Whether the named gate or comparison met its recorded criterion. |
| `decision` | Final adoption or acceptance result, including failed/passing gates where applicable. |
| `environment` | Runtime, dependency, platform, source, and lock identity needed to interpret measurements. |
| `sha256` / `size` | SHA-256 digest and byte length of an exact input or artifact. |

## `benchmark.json` fields

| Field | Description |
| --- | --- |
| `schema_version` | Benchmark document format version. |
| `dataset` | Deterministic `seed`, RNG `bit_generator`, `event_count`, and per-array content identities. |
| `protocol` | Acceptance expression, tolerance and thresholds, selected lags/widths, command templates, and warmup/measured subprocess counts. |
| `implementations` | `scalar` and `vector` records containing warmups, measured samples, and medians. |
| `agreement` | Per-kernel maximum absolute error and pass flag for normalization, IAT, selected-lag ACF, and multiscale output. |
| `comparison` | Combined multiscale/ACF speedup and vector-to-scalar peak-RSS ratio. |
| `decision` | Overall pass flag and the threshold clauses that passed. |
| `environment` | Dependency/runtime/platform versions, source-file identities, and `uv.lock` identity. |

Each sample stores its `ordinal`, `event_count`, `fresh_subprocess` flag,
`input_identity`, output `result_identities`, per-kernel `wall_seconds`, and
`peak_rss_kib`. `medians` stores the median of the same timing and memory values.

## `code_reduction.json` fields

| Field | Description |
| --- | --- |
| `schema_version` | Reduction-evidence format version. |
| `excluded_prefixes` | Repository paths deliberately excluded from production inventory. |
| `categories` | Measured migration categories. |
| `decision.passed` | Whether every category met its declared reduction gate. |

Each category contains `name`, `before_lines`, `after_lines`,
`reduction_percent`, `threshold_percent`, `passed`, and `phases`. A phase binds
the before/after source revisions and inventories. Function rows retain `path`,
`function`, `roles`, exact `executable_lines`, and `line_count`; the surrounding
revision records retain commit/tree and source identities so the calculation is
reproducible rather than an estimate from the current checkout.

## `mmpp_cases.json` fields

| Field | Description |
| --- | --- |
| `schema_version`, `probe` | Evidence format and probe name. |
| `policy` | Fixed rates, seeds, windows, bounds, evaluation budgets, optimizers, RNG, and recovery tolerances. |
| `hand_cases` | Small cases with IATs/rates, terminal silence, expected and observed log likelihood, absolute error, and pass flag. |
| `extreme_cases` | Numerically extreme IAT/rate cases with finite observed likelihood and pass flag. |
| `trials` | Per-seed training data, fitted rates, log-rate errors, held-out results, seed-limit plan, and gate outcomes. |
| `gates` | Overall booleans for hand likelihood, finite extremes, synthetic recovery, held-out likelihood, and equal budget. |
| `decision` | Final outcome, failed gates, and whether production was changed. |

`policy.rate_bounds` contains one lower/upper pair for `q01`, `q10`, `lambda0`,
and `lambda1`. Optimizer objects record the method and every setting that can
change evaluations. Each trial distinguishes likelihood fitting from the
independent simulation-distance baseline and retains the true rates and window.

## `pymoo_cases.json` and `pymoo_schema5_cases.json` fields

`pymoo_cases.json` is byte-immutable schema-3 history. Its check first requires
the exact accepted byte identity, then runs the snapshot through the checker in
an offline no-local/no-hardlink detached clone of its source revision. It is
never passed through the current schema-5 configuration model or overwritten by
the probe runner.
`pymoo_schema5_cases.json` has evidence schema version 4 and probe identity
`pymoo_optimizer_schema5`; it is the generator-owned current snapshot. The
structural fields below apply to each snapshot under its owning version.

| Field | Description |
| --- | --- |
| `schema_version`, `probe` | Evidence format and optimizer probe name. |
| `policy` | Pymoo version, family isolation, seeds, budgets, bounds/limits, similarity settings, checkpoint policy, cache key, tolerances, and line-reduction gate. |
| `known_cases` | Known-optimum continuous and mixed-variable cases, settings, per-seed runs, optimum, and tolerance. |
| `families` | Independent optimizer runs for each Trafficlab family. |
| `fairness` | Common seeds/settings/budgets, measured family set, champions, fresh comparison, and winner. |
| `invalid_classification` | Bounded invalid generation, objective/history/cache records, and evaluation accounting. |
| `checkpoint` | Snapshot encoding, included/missing state, replay comparison, and installed public-API proof. |
| `production_loc` | Current production inventory, measured SLOC, method, threshold, estimate, status, and pass flag. |
| `gates` | Booleans for known optima, repeats, family fairness, cache/diagnostics, public-state replay, and line reduction. |
| `decision` | Outcome, failed gates, production-change flag, and selected production strategy. |

A champion record contains `family`, candidate values, completed search attempts,
trial results, common fresh seed, and fresh fitness. Generation-limit records use
`max_packets`, `max_output_bytes`, and `max_wall_seconds`. Cache-history rows bind
evaluation/generation indices, candidate, objective, cache-hit status, and the
complete key payload.

## `scapy_production_benchmark.json` fields

| Field | Description |
| --- | --- |
| `schema_version` | Benchmark format version. |
| `production` | Confirms the measured implementation is the production codec path. |
| `codec` | Scapy package and version. |
| `command` | Exact bounded argv used to record evidence. |
| `environment` | Source commit/tree, implementation and lock digests, runtime, platform, machine, and Scapy version. |
| `cases` | One record per frame count with warmup/measured counts, samples, and median encode/read/RSS results. |

Each sample contains `frame_count`, `encode_wall_seconds`, `read_wall_seconds`,
`peak_rss_kib`, input/trace identities, output digest, and output byte size.

## `example_run.json` fields

| Field | Description |
| --- | --- |
| `schema_version` | Retained-run evidence format version. |
| `source` | Clean commit/tree, state note, and exact config/lock identities. |
| `environment` | Python and dependency versions plus host kernel, architecture, and Docker versions. |
| `images` | Capture and target image references/IDs and capture-tool version. |
| `execution` | Exact command/target argv/URL, exit status, timestamps and source, and process-tree resource bounds. |
| `artifacts_directory` | Repository-relative directory containing the retained run files. |
| `artifacts` | Filename-to-`sha256`/`size` identity map for every authoritative run artifact. |
| `result` | Winner, enabled families, fitness, aggregate/method scores, observation window, and packet counts. |
| `cleanup` | Compose project name, empty remaining resource inventories, and verification flag. |

`execution.resource_bounds` contains `memory_high`, `memory_max`, `swap_max`,
`wall_time`, and `kill_after`. `result.method_scores` contains all four mandatory
similarity scores. `cleanup.containers`, `networks`, and `volumes` must be empty
when `verified` is true.
