# 01 Combined Score and Validation

## Combined Score

After both component evaluations succeed, the method computes:

```text
score = iat_weight * iat_similarity
      + frame_size_weight * frame_size_similarity
```

Because both similarities are in `[0, 1]` and the validated weights form a
non-negative unit sum, the combined score is also in `[0, 1]`. Higher is more
similar.

Both components must succeed even when one configured weight is zero. Each
input therefore requires at least two scored frames. A component failure makes
the entire evaluation unsuccessful; the method never publishes a partial score
or redistributes the failed component's weight.

## Result

The final combined score is the primary ranking result. Method-specific result
details record both configured weights, both component similarities, both raw
KS distances, and the reference and generated sample counts used by each
component. No p-value is present.

The result follows the [similarity result
contract](../../contracts/60_30_similarity_result/README.md).
`l2_ks_weighted` does not interpret the primary result as genetic fitness; that
remains the responsibility of the invoking training strategy.

## Validation and Failure

Configuration validation completes before PCAPNG processing. Input extraction
and component validation then follow both component owners. Invalid weights,
either failed component, a non-finite intermediate or final calculation, or
mathematical-library failure produces no fallback similarity and no successful
partial result.

The [60 similarity evaluation
application](../../apps/60_similarity_evaluation/README.md) retains its normal
startup diagnostics after failure.
