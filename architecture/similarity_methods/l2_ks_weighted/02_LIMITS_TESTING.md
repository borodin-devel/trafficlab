# 02 Limits, Testing, and Reading

## Limits

The weighted average remains a combination of two marginal distribution
scores. It does not create a joint timing-size model and cannot detect whether
particular frame lengths are associated with particular IATs. All other
component limits also apply, including the absence of packet-count, sequence,
flow, address, and protocol comparison.

## Testing

Future deterministic, unprivileged tests cover the default equal weights,
custom weights, boundary weights `0` and `1`, negative and non-finite weights,
weights whose sum is not `1`, exact-decimal sum validation, exact combined
scores, component failure including a zero-weight component, complete component
and final result details, and absence of p-values. No test requires live
capture, network access, or elevation.

## Reading

Follow the [architecture governance](../../README.md), the
[similarity-method registry](../README.md), the shared KS rules, both component
owners, and the similarity result contract before changing this method.
