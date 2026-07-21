# 02 Implementation, Result, Validation, and Testing

A future implementation must use a maintained, correct Python
optimal-transport/Sinkhorn library only if the selected implementation is
deterministic for identical inputs and configuration. It must select and fix a
specific solver, backend, and algorithm, and configure every available
determinism control (including deterministic execution mode, seed, thread or
device settings, and stable ordering where applicable). Result lineage and
method-defined diagnostics must record the package identity and version, the
selected solver, backend, and algorithm, the effective deterministic settings,
and the convergence settings.

If no maintained implementation can meet these deterministic requirements, a
handwritten fallback is permitted only with a documented rationale and
independent correctness and determinism tests. The fallback has the same
lineage and diagnostic recording requirements.

## Score and Result

`D` is the raw non-negative joint-distribution distance. The primary
similarity is the configured positive-scale monotonic mapping:

```text
similarity = exp(-D / score_mapping_scale)
```

It is in `(0, 1]`, and hence `[0, 1]`; `1` means identical under this method
and higher values are more similar. The positive mapping scale is required
because the ground cost has no natural upper bound.

Method-defined details in `similarity.toml` record `D`, the reference and
generated observation counts, both feature scales, entropic regularization,
score-mapping scale, solver settings, achieved convergence measures, and the
selected mathematical-library identity and version, solver/backend/algorithm,
and effective deterministic settings. Result lineage records the implementation
identity and version, solver/backend/algorithm, effective deterministic
settings, and convergence settings. The [similarity result
contract](../../contracts/60_30_similarity_result/README.md) owns result
validation, hashing, and atomic publication.

## Validation and Failure

Configuration validation completes before PCAPNG processing. Both inputs must
pass the shared rules and contain at least two frames. The solver must meet
both configured convergence tolerances within the configured iteration limit
for all three transport terms in the divergence. A non-finite cost, transport
value, divergence, convergence measure, or score is invalid. A divergent or
negative divergence inconsistent with the configured numerical guarantees is
also a failure.

Invalid configuration, insufficient frames, a solver failure or
non-convergence, or any invalid numerical result makes the evaluation
unsuccessful. The method never substitutes an approximate partial result,
clamps a value, or publishes a fallback similarity. The similarity evaluation
application retains its normal startup diagnostics after failure.

## Limits

The empirical cloud deliberately ignores packet order after observations are
formed. It can distinguish timing--size association that separate marginal
comparisons cannot, but it cannot distinguish captures with the same joint
cloud and different burst order, transition structure, packet count, flows,
addresses, contents, or protocol behavior.

## Testing

Future deterministic unit tests cover the ground cost, uniform empirical
weights, Sinkhorn-divergence calculation against authoritative examples or an
independent implementation, mapping bounds, zero IAT, unequal cloud sizes,
feature and mapping scales, convergence and non-convergence, invalid numeric
settings, raw diagnostics, and identical-input/configuration determinism.
Future integration tests use small offline Ethernet PCAPNG fixtures and verify
`similarity.toml` details and lineage. A handwritten fallback additionally has
independent correctness and determinism tests. No test requires network access,
live capture, sudo, root, or other elevated privilege.

## Reading

Follow the [architecture governance](../../README.md), the
[similarity-method registry](../README.md), the shared [temporal Layer 2
rules](../TEMPORAL_L2.md), the [60 similarity evaluation
application](../../apps/60_similarity_evaluation/README.md), and the
[similarity result contract](../../contracts/60_30_similarity_result/README.md)
before changing this method.
