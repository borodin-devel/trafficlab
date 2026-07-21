# Genetic Training Authoritative Configuration Schema

This document owns every genetic-training configuration path, TOML type,
default, override, and cross-field rule. The user guide and templates are
derived material and must link here rather than redefine behavior.

Read the shared [application configuration](../../CONFIGURATION.md), the
[genetic training SAD](SAD.md), and [configuration overview](CONFIGS.md) first.
The selected genetic strategy and traffic models continue to own their
mathematics and trainable parameter definitions.

## Invocation and Source Selection

The direct command is:

```text
genetic_training --config-file PATH --output-dir DIR \
  [--set DOTTED.PATH=TOML_VALUE ...] \
  [--reference PATH | --reference-directory PATH | \
   --reference-contract STATUS_PATH --reference-member MEMBER]
```

`--config-file` and `--output-dir` are required. `--config-dir` and automatic
file discovery are unsupported. Managed invocation additionally supplies
`--attempt-dir PATH` under the shared
[attempt topology](../../libs/artifact_io/SAD.md#structure-and-data-flow).

One optional command-line source group replaces the complete source-selector
group from the file. `--reference` and `--reference-directory` each stand
alone. `--reference-contract` and `--reference-member` must occur together;
`MEMBER` is exactly `target`, `uplink`, or `downlink`. Mixing source groups,
repeating a selector, or supplying only half the contract pair fails.

## Resolution and Overrides

Each ordinary leaf resolves in this order:

```text
built-in default -> required TOML file -> repeatable --set override
```

The dedicated command-line source group then atomically replaces the file's
source-selector group. Source selector paths cannot be targeted by `--set`.

Each `--set` path identifies one documented ordinary leaf or valid dynamic
search-space leaf. Its value is parsed as TOML data, never shell or Python.
Arrays replace atomically and do not merge. Unknown paths, repeated overrides,
table replacement, malformed TOML, type coercion, and an invalid final
configuration fail before candidate creation. Several distinct dynamic search
leaf overrides may construct one entry without command-line-order dependence.

## Complete Shape

```toml
schema_version = 1
reference = "/path/to/reference.pcapng"
allowed_traffic_models = ["poisson_uniform", "poisson_empirical"]
similarity_method = "l2_ks_weighted"

[resources.generation]
max_packets = 1000000
max_output_bytes = 1073741824
max_proposals = 10000000

[strategy]
name = "basic_generational"
strategy_seed = 0
population_size = 10
elite_count = 1
tournament_size = 3
crossover_probability = 0.8
parameter_mutation_probability = 0.2
model_replacement_probability = 0.05
offspring_attempt_limit = 100

[strategy.stopping]
maximum_generations = 50
target_score = 0.95
no_improvement_generations = 10

[strategy.search.poisson_uniform.arrival_rate_pps]
minimum = 1.0
maximum = 100.0
mutation_step = 5.0

[strategy.search.poisson_empirical.arrival_rate_pps]
minimum = 1.0
maximum = 100.0
mutation_step = 5.0
```

Values in this example are illustrative, not additional defaults. In
particular, every run must choose and record its own three generation limits.

## Top-Level Fields

| Path | TOML type | Built-in default | Accepted value and effect |
|---|---|---|---|
| `schema_version` | integer | none; required | Exactly `1`; Boolean is invalid. |
| `reference` | string | none | One readable regular reference PCAPNG. Mutually exclusive with every other selector. |
| `reference_directory` | string | none | One readable reference directory, permitted only when every allowed model is `neural_hawkes` or `marked_point_process_diffusion`. Mutually exclusive with every other selector. |
| `reference_contract` | string | none | Path to the producer attempt's `artifact-status.toml` for one capture-directions package. Requires `reference_member`; mutually exclusive with direct file/directory selectors. |
| `reference_member` | string | none | Required only with `reference_contract`; exactly `"target"`, `"uplink"`, or `"downlink"`. |
| `allowed_traffic_models` | array of strings | none; required | Nonempty ordered list of unique mature registered traffic-model names. Order controls baseline placement, round-robin filling, and deterministic replacement choices. |
| `similarity_method` | string | none; required | One mature registered method whose declared primary ranking result supplies fitness. |

Exactly one effective selector is required. Relative paths resolve from the
invocation current directory and are recorded as absolute normalized paths.
No path receives shell, tilde, glob, command, or environment expansion.

For `reference`, the application validates the PCAPNG and records its SHA-256.
For `reference_directory`, it applies the deterministic membership and
per-file rules in [neural common rules](../../traffic_models/NEURAL_MARKED_POINT_PROCESS.md#reference-sources).
For `reference_contract`, it verifies the detached manifest digest from the
explicit status record, exact package membership, the selected member digest,
and the selected PCAPNG. It records the status, package, manifest, member path,
and member digest. It never infers `target` or searches for a recent contract.

## Generation Resource Envelope

These fields are required below `[resources.generation]` and have no built-in
defaults:

| Path | TOML type | Accepted value and effect |
|---|---|---|
| `resources.generation.max_packets` | integer | Positive, at most `9223372036854775807`; maximum accepted packets in any one generation job. |
| `resources.generation.max_output_bytes` | integer | Positive, at most `9223372036854775807`; maximum complete PCAPNG bytes in any one job. |
| `resources.generation.max_proposals` | integer | Positive, at most `9223372036854775807`; maximum accepted, rejected, resampled, and boundary-crossing event proposals in any one job. |

Booleans are invalid. A packet-count stop must not exceed `max_packets`; a
window-count stop must have
`window_count * max_events_per_window <= max_packets`, using overflow-safe
integer arithmetic. Generation checks all limits before the operation that
would exceed one. Reaching a limit before the model stop completes fails that
child without a successful PCAPNG; it never silently truncates or repairs output.
These limits apply to every per-validation neural generation too.
Genetic training passes them verbatim as `--max-packets`,
`--max-output-bytes`, and `--max-proposals` to every traffic-generation child.

These are job-output guards, not CPU/memory worker reservations. Candidate
parallelism has no schema in version 1 and therefore remains serial. A future
parallel implementation must add positive CPU, memory, storage, and worker
paths here before it can schedule concurrent candidates.

## Neural Validation Fields

The `[neural_validation]` table is allowed only when every allowed model is
`neural_hawkes` or `marked_point_process_diffusion`:

| Path | TOML type | Built-in default | Accepted value and effect |
|---|---|---|---|
| `neural_validation.validation_fraction` | float | `0.2` | Finite, strictly greater than `0`, and less than `1`. |
| `neural_validation.split_policy` | string | `"relative_path_tail_then_chronological_windows"` | Exactly that value. |

The split reserves whole captures from deterministic relative-path order when
that leaves nonempty train and validation sets. For `C >= 2` files and fraction
`f`, the final `min(C - 1, ceil(f * C))` files are validation. For one file with
`N` derived events, `V = ceil(f * N)` events form the validation suffix,
`E_(N-V)` is an unused guard, and only events through `E_(N-V-1)` may train.
The derived reference contains anchor frame `F_(N-V)` and the frames ending
validation events `E_(N-V+1)` through `E_N`. Inadequate exact partitions fail
rather than clamp or move the boundary; `N - V` must be at least `2` before
any boundary index is interpreted. This follows the [neural
validation-unit rule](../../traffic_models/NEURAL_MARKED_POINT_PROCESS.md#windows-and-validation-split).
The complete mixed source file is not an evaluation reference.

Each validation unit receives one matching generation and similarity result.
Fitness is their equal-weight arithmetic mean. Custom capture weights,
`capture_weights`, `validation_capture_weights`, and equivalent settings are
unknown and invalid.

## Basic Generational Strategy Fields

The selected [basic generational strategy](../../genetic_models/basic_generational/README.md)
owns operation meaning. These fields belong below `[strategy]`:

| Path | TOML type | Built-in default | Accepted value and effect |
|---|---|---|---|
| `strategy.name` | string | `"basic_generational"` | Exactly `"basic_generational"`. |
| `strategy.strategy_seed` | integer | `0` | Any TOML integer; initializes the recorded deterministic strategy stream. |
| `strategy.population_size` | integer | `10` | At least `2` and at least the number of allowed traffic models. |
| `strategy.elite_count` | integer | `1` | At least `1` and less than `population_size`. |
| `strategy.tournament_size` | integer | `3` | At least `2` and no greater than `population_size`; runtime may use the smaller eligible-pool size. |
| `strategy.crossover_probability` | float | `0.8` | Finite and in `[0, 1]`. |
| `strategy.parameter_mutation_probability` | float | `0.2` | Finite and in `[0, 1]`; its sum with replacement probability is at most `1`. |
| `strategy.model_replacement_probability` | float | `0.05` | Finite and in `[0, 1]`; nonzero requires at least two allowed models. |
| `strategy.offspring_attempt_limit` | integer | `100` | At least `1`; bounds randomized population-0 sampling and every later offspring slot. |

A one-model run must explicitly resolve
`strategy.model_replacement_probability` to `0.0`. Integer tokens do not
satisfy float fields, Booleans do not satisfy integer fields, and numeric
strings/non-finite values are invalid.

## Strategy Stopping Fields

These fields belong below `[strategy.stopping]`:

| Path | TOML type | Built-in default | Accepted value and effect |
|---|---|---|---|
| `strategy.stopping.maximum_generations` | integer | `50` | At least `1`; population zero counts, so `50` evaluates populations `0` through `49` at most. |
| `strategy.stopping.target_score` | float | none; disabled | Optional finite value inside the selected method's declared range; comparison follows declared score direction. |
| `strategy.stopping.no_improvement_generations` | integer | none; disabled | Optional and at least `1`; counts complete post-population-zero generations without strict improvement. |

Enabled conditions are checked only after a whole population finishes. Any
one satisfied condition stops training; all simultaneously satisfied
conditions are recorded. The no-improvement counter begins at zero after
population zero, resets only on a strictly better score, and is unaffected by
a candidate-ID tie-break.

## Dynamic Search-Space Entries

Search entries use:

```text
strategy.search.<allowed-model-name>.<trainable-model-parameter-path>
```

The suffix is the parameter's dotted path in that model's TOML schema,
represented as nested TOML tables. Only a selected model's explicitly declared
trainable parameter is allowed. An omitted trainable parameter retains its
validated normal builder value for that run.

### Numeric Entry

An integer or floating parameter has exactly:

```toml
minimum = 1.0
maximum = 100.0
mutation_step = 5.0
```

All three values use the model schema's exact numeric kind. Floats are finite;
bounds are inclusive, satisfy `minimum < maximum`, contain the normal builder
baseline, and represent at least two possible values. `mutation_step` is
positive. Integer initialization is discrete-uniform over both endpoints;
floating initialization is uniform over both bounds. Out-of-domain or
complete-model-invalid proposals discard and restart the bounded whole
offspring attempt; values are not clamped, repaired, or individually resampled.

### Choice Entry

A choice parameter has exactly one leaf:

```toml
values = ["first", "second"]
```

The ordered array contains at least two distinct exact-schema-kind values and
the builder baseline. Initialization selects uniformly from all values;
mutation selects uniformly from values other than current. Numeric leaves are
invalid for a choice entry and `values` is invalid for a numeric entry.

When parameter mutation probability is positive, every allowed model has at
least one valid search entry. When zero, entries are optional but any present
entry still controls randomized initialization and replacement. Validation
rejects disallowed model names, unknown/non-trainable paths, mismatched kinds,
invalid bounds, builder baselines outside the domain, degenerate domains, unknown
leaves, and any complete model that violates its owner schema.

## Immutable Preparation and Lineage

Candidate values are applied to an in-memory copy of the model-creation
builder, then the selected model prepares or fits a new immutable
generation-ready model. Search never edits a published builder. Exact builder,
candidate, prepared/trained model, reference, validation-unit, generated-file,
and similarity-result digests enter candidate lineage under the
[candidate model lifecycle](SAD.md#candidate-model-lifecycle).

`launch.toml` records the selected source mode, resolved absolute paths,
contract/member identity where applicable, complete effective configuration,
all `--set` and dedicated selector overrides, generation limits, component
versions, and any resolution failure under the shared startup-record rules.

## Validation and Failure

Before population work, genetic training rejects malformed TOML, unknown or
repeated settings, unsupported/stub component names, wrong types, unreadable or
invalid sources, bad detached hashes, incompatible source/model selection,
missing generation limits, invalid strategy combinations, and invalid search
domains. It never ignores, partially applies, repairs, or silently defaults a
required value.

No setting grants privilege, invokes live capture, permits network access, or
contains a shell command.
