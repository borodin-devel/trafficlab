# 03 Model File, Lineage, and Generation

[Model Creation](../../apps/40_model_creation/README.md) creates an immutable
normal builder. [Genetic Training](../../apps/30_genetic_training/README.md)
copies it, applies and validates all candidate hyperparameters before window
preparation or fitting, performs the candidate-local fit, and atomically
publishes a distinct generation-ready self-describing model only after all
validation succeeds. It never edits the builder.

### Normal builder schema

A normal untrained builder is a complete candidate-fitting configuration. It
uses the following defaults; fields shown in the file are required, even where
their value equals the default. TOML booleans do not satisfy integer fields;
numeric strings, non-finite floats, and unknown fields are invalid.

| Path | Default | Validation |
|---|---:|---|
| `model` | `"neural_hawkes"` | Exactly this string. |
| `schema_version` | `1` | Exactly integer `1`. |
| `model_state` | `"builder"` | Exactly `"builder"` before fitting; the trained descendant requires `"generation_ready"`. |
| `seed` | `0` | Any TOML integer; it is the recorded root seed for deterministic candidate streams. |
| `architecture.hidden_size` | `64` | Positive integer. |
| `architecture.attention_layers` | `2` | Positive integer. |
| `architecture.attention_heads` | `4` | Positive integer that divides `hidden_size` exactly. |
| `architecture.history_length` | `32` | Positive integer finite event count. |
| `architecture.position_embedding_max_events` | `32` | Positive integer exactly equal to `history_length`. |
| `architecture.attention_type` | `"causal_self_attention"` | Exactly this value. |
| `architecture.attention_mask` | `"strict_causal"` | Exactly this value; a bidirectional, omitted, or otherwise weaker mask is invalid. |
| `architecture.time_mixture_components` | `4` | Positive integer number of log-normal components. |
| `architecture.mark_mixture_components` | `4` | Positive integer number of truncated-normal components. |
| `iat_law.family` | `"zero_inflated_log_normal_mixture"` | Exactly this normalized law. |
| `iat_law.zero_iat_observation_policy` | `"atom_at_zero"` | Exactly this policy: a learned sigmoid atom models observed zero IATs and the positive mixture models only IATs greater than zero. Clamping, dropping, jittering, or reordering a zero IAT is invalid. |
| `mark_law.family` | `"truncated_normal_mixture"` | Exactly this normalized continuous law. |
| `mark_law.minimum_bytes` | `60` | Exactly integer `60`. |
| `mark_law.maximum_bytes` | `1514` | Exactly integer `1514`; it must be greater than `minimum_bytes`. |
| `mark_law.integer_mapping` | `"round_half_to_even"` | Exactly this deterministic conversion; a non-finite or out-of-range result fails rather than clamps. |
| `optimizer.family` | `"adamw"` | Exactly `"adamw"` in schema version 1. |
| `optimizer.beta1` | `0.9` | Finite float in `[0, 1)`. |
| `optimizer.beta2` | `0.999` | Finite float in `[0, 1)`. |
| `optimizer.epsilon` | `1e-8` | Finite float greater than `0`. |
| `optimizer.weight_decay` | `0.0` | Finite float greater than or equal to `0`. |
| `optimizer.gradient_norm_limit` | `1.0` | Finite float greater than `0`. |
| `fitting.learning_rate` | `0.001` | Finite float greater than `0`. |
| `fitting.batch_size` | `32` | Positive integer. |
| `fitting.window_width` | `64` | Positive integer and at least `history_length + 1`. |
| `fitting.max_epochs` | `100` | Positive integer. |
| `fitting.min_epochs` | `1` | Positive integer no greater than `max_epochs`. |
| `fitting.validation_interval_epochs` | `1` | Positive integer no greater than `max_epochs`. |
| `fitting.early_stopping_patience` | `10` | Integer greater than or equal to `0`. |
| `validation.validation_fraction` | `0.2` | Finite float strictly between `0` and `1`. |
| `validation.split_policy` | `"relative_path_tail_then_chronological_windows"` | Exactly this shared-rule policy. It reserves deterministic relative-path-tail files when possible; a single-file source uses the anchored chronological suffix selected before independent training and validation windows are built. |
| `runtime.execution_backend` | `"cpu"` | Exactly `"cpu"` in schema version 1. |
| `runtime.deterministic_algorithms` | `true` | Exactly `true`; unsupported nondeterministic operations are a failure. |
| `runtime.thread_count` | `1` | Positive integer exactly `1` in schema version 1. |
| `runtime.random_stream_derivation` | `"candidate_seed_v1"` | Exactly this versioned seed-derivation rule. |
| `stop.mode` | `"packet_count"` | Exactly one of `"packet_count"` or `"duration"`. |
| `stop.packet_count` | `1000` | Required only when `mode` is `"packet_count"`; positive integer. It is forbidden when `mode` is `"duration"`. |
| `stop.duration_seconds` | absent | Required only when `mode` is `"duration"`; finite float greater than or equal to `0`. It is forbidden when `mode` is `"packet_count"`. |

The `validation` values must equal the effective neural validation controls in
the genetic-training configuration; that application's configuration owner
defines resolution and assignment. Genetic training copies the builder and
replaces these two values in memory with the resolved run-wide controls before
candidate-specific hyperparameters and window preparation; every candidate
receives the same values. They are not eligible search-space paths. This
duplication makes a builder and its trained descendant self-describing; it does
not give the model permission to choose a different assignment.

The required default builder file is:

```toml
model = "neural_hawkes"
schema_version = 1
model_state = "builder"
seed = 0

[architecture]
hidden_size = 64
attention_layers = 2
attention_heads = 4
history_length = 32
position_embedding_max_events = 32
attention_type = "causal_self_attention"
attention_mask = "strict_causal"
time_mixture_components = 4
mark_mixture_components = 4

[iat_law]
family = "zero_inflated_log_normal_mixture"
zero_iat_observation_policy = "atom_at_zero"

[mark_law]
family = "truncated_normal_mixture"
minimum_bytes = 60
maximum_bytes = 1514
integer_mapping = "round_half_to_even"

[optimizer]
family = "adamw"
beta1 = 0.9
beta2 = 0.999
epsilon = 1e-8
weight_decay = 0.0
gradient_norm_limit = 1.0

[fitting]
learning_rate = 0.001
batch_size = 32
window_width = 64
max_epochs = 100
min_epochs = 1
validation_interval_epochs = 1
early_stopping_patience = 10

[validation]
validation_fraction = 0.2
split_policy = "relative_path_tail_then_chronological_windows"

[runtime]
execution_backend = "cpu"
deterministic_algorithms = true
thread_count = 1
random_stream_derivation = "candidate_seed_v1"

[stop]
mode = "packet_count"
packet_count = 1000
```

This is the normal untrained builder file: it is valid for candidate fitting,
but it lacks the learned-weight payload required for generation. A file first
must validate as this complete builder schema before fitting may add any
trained-file fields. A trained file retains every validated candidate field
except that `model_state` transitions from `"builder"` to
`"generation_ready"`; it adds builder digest, candidate identity, canonical
learned weights, selected-checkpoint results, and required lineage. Validation
rejects an attempt to generate from an untrained builder file.

```text
model = "neural_hawkes"
schema_version
model_state = "generation_ready"
the complete, already validated effective candidate schema
canonical learned-weight payload or immutable weight-file reference
SHA-256 hash of the exact learned-weight bytes
selected epoch and training/validation metrics
the common owner's required source, assignment, configuration, artifact, and
library-identity/version lineage
```

An external weight-file reference is valid only when it is an immutable,
validated file inside the published model artifact directory; its canonical
relative path and hash are recorded. Resolution must reject traversal,
absolute paths, missing files, hash mismatches, unknown fields, duplicate
weights, unsupported schema versions, incompatible architecture dimensions,
or non-finite parameters. Traffic Generation validates the entire file and
weight payload before sampling and never rereads reference captures.

The common owner records the learned-weight hash in candidate lineage. This
owner additionally requires the model file to retain that hash and every
architecture and fitting choice needed to interpret the weights. Learned
weights are not a substitute for lineage; source paths or deterministic
directory membership, artifact hashes, split assignment, seeds, and library
versions remain owned by the common rules.

## Generation and Stopping

[Traffic Generation](../../apps/50_traffic_generation/README.md) supplies a
validated model file and writes the PCAPNG artifact. This model starts from
the empty history, samples one IAT, then one continuous mark conditional on
that IAT, maps the mark to an integer Ethernet length, emits one event, and
appends that emitted pair to its bounded history. It repeats causally; it does
not generate a future window in parallel or revise an emitted event.

Generation has exactly one configured stop mode:

- `packet_count` emits exactly the configured positive count; or
- `duration` emits events while cumulative generated time is at most the
  configured finite, non-negative duration and stops before an event would
  exceed it.

Traffic generation additionally enforces required positive packet,
complete-output-byte, and proposal limits. Proposal accounting includes
accepted, rejected, resampled, and first-beyond-duration events. Reaching any
limit before the configured stop completes fails without publication; the
proposal bound makes repeated zero-IAT duration sampling finite.

Timestamps advance by each sampled IAT and are nondecreasing. The model does
not infer interface metadata or packet bytes; the generation application owns
valid PCAPNG construction. It fails before publication for invalid model
files, invalid stop values, invalid RNG state, non-finite distribution
parameters or samples, failed integer-length mapping, or a library/runtime
failure. Partial generated output is not a successful artifact.
