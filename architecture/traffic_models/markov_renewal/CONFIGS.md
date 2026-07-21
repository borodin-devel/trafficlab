# Markov Renewal Configuration

## Version 1 Modes

Exact normal and alternative TOML forms are in
[generation and model file](03_GENERATION_VALIDATION.md#model-file). Common
fields are model, schema version, common `model_state`, seed, state mode, and
exactly one stop mode.

Automatic mode chooses `quantile`, `exact`, or `cluster`. Quantile uses positive
IAT/frame bucket counts; cluster uses positive cluster count no greater than
distinct normalized vectors; exact has no complexity count. Manual mode uses
explicit half-open IAT ranges and inclusive-domain frame ranges represented by
half-open bounds through maximum 1515.

## Trainable Fields

Only `automatic.quantile.iat_bucket_count`,
`automatic.quantile.frame_size_bucket_count`, and
`automatic.cluster.cluster_count` are trainable. Mode, submode, manual ranges,
seed policy, and stop remain fixed per training run.

These values first form an immutable builder. Genetic training applies and
validates candidate complexity values before constructing states, then
publishes a distinct file with `model_state = "generation_ready"` and complete
builder/candidate/reference lineage.
