# 04 Model File, Generation, and Verification

[Model Creation](../../apps/40_model_creation/README.md) creates an immutable
normal builder. [Genetic Training](../../apps/30_genetic_training/README.md)
copies it, applies and validates all candidate hyperparameters before window
preparation and fitting, fits each candidate locally, and atomically publishes
a distinct generation-ready trained file only after validation succeeds. It
never edits the builder. A normal untrained builder file is valid
for fitting but cannot generate:

```toml
model = "marked_point_process_diffusion"
schema_version = 1
model_state = "builder"
seed = 0

[representation]
window_width_seconds = 1.0
max_events_per_window = 64
trailing_window_policy = "discard"
generated_history_events = 128
iat_feature_scale = 1.0

[architecture]
hidden_size = 64
attention_layers = 2
attention_heads = 4
feed_forward_size = 128
dropout_probability = 0.0
context_embedding_size = 32
seed_embedding_size = 32

[diffusion]
diffusion_steps = 100
noise_schedule = "linear"
beta_start = 0.0001
beta_end = 0.02
predictor = "epsilon"
sampler = "ddpm"
sampling_stochasticity = 1.0
sampler_step_subset = "all"

[fitting]
learning_rate = 0.001
optimizer = "adamw"
optimizer_beta1 = 0.9
optimizer_beta2 = 0.999
optimizer_epsilon = 0.00000001
weight_decay = 0.0
gradient_norm_limit = 1.0
batch_size = 32
max_epochs = 100
min_epochs = 1
validation_interval_epochs = 1
early_stopping_patience = 10
lambda_continuous = 1.0
lambda_count = 1.0
l2_regularization = 0.0
validation_policy = "deterministic_loss"
validation_sampling_window_count = 0

[stop]
mode = "window_count"
window_count = 10
duration_seconds = 10.0
```

The builder requires common `model_state = "builder"` and validates every
field before fitting. In particular,
`window_width_seconds` is finite and positive;
`max_events_per_window` is a positive integer;
`generated_history_events` is a non-negative integer; `iat_feature_scale` is
finite and positive; and `diffusion_steps` is a positive integer.  The only
accepted `noise_schedule` family is
`"linear"`; its finite `beta_start` and `beta_end` endpoints derive the
recorded linearly interpolated `beta_1 ... beta_T`, and every derived beta
must be strictly in `(0, 1)`.  `hidden_size` and `attention_heads` are
positive integers, and `hidden_size` is divisible by `attention_heads`.
Sizes, counts, epochs, intervals, and `window_count` are otherwise positive
integers; `min_epochs <= max_epochs`; patience and
`validation_sampling_window_count` are non-negative integers.  Finite
non-negative values are required for dropout, weight decay, regularization,
and loss weights; dropout is at most one, the loss weights are not both zero,
and the gradient-norm limit is finite and positive.  `learning_rate` and
optimizer epsilon are finite and positive;
AdamW beta values are finite in `[0, 1)`.  The normal optimizer is `adamw`;
other optimizer families are valid only when the schema names and validates
their corresponding momentum or beta controls.  `predictor` is `"epsilon"`,
`sampler` is `"ddpm"`, and `sampler_step_subset` is `"all"`; no count
transition or terminal count distribution is configured or recorded.
`sampling_stochasticity` must be exactly `1.0`; no tempered DDPM variant is
supported.
`validation_policy` is `"deterministic_loss"` and permits zero fixed-seed
sampling diagnostic windows; other policies require an explicitly supported
shared-rule value.  Stop mode selects exactly its matching validated control:
`window_count` for `window_count`, or finite non-negative
`duration_seconds` for `duration`; the unselected value neither changes the
run nor supplies fallback output.  Unknown fields and values that violate
these rules fail validation.  The TOML defaults above provide all of these
fields and satisfy these core invariants.

A trained file changes `model_state` to `"generation_ready"` and additionally
contains builder digest, candidate identity and effective configuration, all
optimizer and validation controls, loss weights, canonical learned-weight payload or an immutable
weight-file reference, SHA-256 hash of the exact learned-weight bytes, selected
epoch and metrics, count-head parameters, and the common owner's complete source,
assignment, configuration, seed, library-version, and reference-artifact
lineage.  An external weight file is valid only inside the published model
artifact directory and only with a canonical relative path and matching hash.
Validation rejects traversal, absolute paths, missing files, hash mismatches,
unknown fields, duplicate weights, unsupported schema versions, incompatible
dimensions or schedule, non-finite values, and an untrained file submitted for
generation.  Traffic Generation validates the entire model before sampling and
never rereads reference captures.

## Generation, Conversion, and Stopping

[Traffic Generation](../../apps/50_traffic_generation/README.md) supplies a
validated trained file and owns PCAPNG construction.  For each window this
model samples its categorical count first, obtains terminal Gaussian noise for
that active prefix from its generation seed, and denoises those rows in
reverse-step order.  It decodes every active row, validates the complete
window's joint timing support, and only then emits all active rows in slot
order.  The time coordinate becomes:

```text
require every active x0_hat_iat is finite
x_iat[j] = max(0, x0_hat_iat[j])
require every x_iat is finite and >= 0
iat_seconds[j] = exp(x_iat[j] * iat_feature_scale) - 1
require every iat_seconds is finite and >= 0
require sum(iat_seconds[0] ... iat_seconds[n-1]) < window_width_seconds
```

The normalized length coordinate becomes a deterministic original Ethernet
frame length:

```text
continuous_length = 60 + 1454 * normalized_clean_length
integer_length = round_half_to_even(continuous_length)
require 60 <= integer_length <= 1514
```

No random rounding, resampling, clamping, or repair is permitted.  Converted
events retain slot order; their IATs advance timestamps monotonically, and the
strict joint-support check ensures no emitted window's final event reaches or
crosses its half-open end.  The configured stop mode is
exactly one of `window_count`, which concatenates the stated positive number
of complete bounded windows, or `duration`, which stops before a whole
complete bounded window would make cumulative generated IAT exceed the stated
finite non-negative duration.  It never emits a partial denoised window or
fallback output to satisfy duration.

Traffic generation additionally enforces required positive packet,
complete-output-byte, and proposal limits. Proposals include every accepted,
rejected, resampled, and first-beyond-duration event/window attempt. Reaching a
limit before the configured stop completes fails without publication; the
proposal bound makes repeated zero-IAT duration work finite.

Generation fails before publication for an invalid model, invalid seed or
stop value, non-finite schedule/weights/noise/sample, invalid count or mask,
failed reverse update, failed conversion, output exceeding the configured
event/window limit, or library/runtime failure.  Partial output is not a
successful artifact.

## Determinism, Limits, and Future Verification

With identical validated inputs, model file, reference artifacts,
configuration, seeds, and supported library versions, preparation, noise
draws, training batches, fitting, checkpoint selection, canonical weight
serialization, and lineage are deterministic.  Given the same valid trained
model and generation seed, reverse sampling, count selection, output order,
and conversion are deterministic.  Unsupported nondeterministic operations
are forbidden unless a future schema records and controls them.

The model is bounded by its fixed duration, maximum event representation,
generated-history limit, finite network, finite diffusion steps, finite
training epochs, and explicit stop policy.  It has no implicit source
discovery, unbounded output, silent data repair, payload or protocol model,
address or flow model, or live-network capability.  A single capture can
overfit as diagnosed by the common owner.

Future unit tests cover canonical variable-count masks, prefix validation,
window overflow and trailing policy, normalized `log1p(IAT)` representation,
zero-IAT conversion, complete-sample strict joint-support rejection, schedule
and categorical count-head/count-distribution validation, masked loss, seed
reproducibility, full-window reverse sampling, history-only conditioning,
deterministic 60-through-1514
length conversion, checkpoint tie selection, early stopping, and strict
model/weight-file validation and lineage.
Future integration tests
use small offline Ethernet pcapng fixtures for file-boundary separation,
source-mode validation, fitting, atomic publication, and generation; they
require no live capture, network access, sudo, root, or elevated privilege.

## Computational Complexity

Let `M` be `max_events_per_window`, `H` denoiser hidden size, `F` feed-forward
size, `L` attention-layer count, `T` diffusion steps, and `W` generated window
count. One dense denoiser pass requires
`O(L * (M^2 * H + M * H * F))` time and
`O(L * M^2 + M * (H + F))` activation memory, excluding weights. Full DDPM
generation performs `T` passes per window, so its time is
`O(W * T * L * (M^2 * H + M * H * F))`. Training samples one diffusion step
per example rather than all `T`; its total cost scales with finite batches and
epochs. Resource admission uses these configured bounds before work.

## Reading

Read [Traffic Models](../README.md), then the [neural marked-point-process
common rules](../NEURAL_MARKED_POINT_PROCESS.md), before changing this owner.
The surrounding workflow is owned by [Genetic Training](../../apps/30_genetic_training/README.md),
[Model Creation](../../apps/40_model_creation/README.md), and
[Traffic Generation](../../apps/50_traffic_generation/README.md).
