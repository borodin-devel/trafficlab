# 00 IAT KS Method Definition

## Role

`iat_ks` is a selectable similarity method that compares the distributions of
frame inter-arrival times in one reference Ethernet PCAPNG and one generated
Ethernet PCAPNG.

## Shared Behavior

The [KS family](../KS.md) owns the empirical-CDF distance, `1 - D` similarity,
score range and direction, p-value exclusion, Ethernet boundary, unequal-sample
support, implementation policy, and common comparison limits. This document
owns only IAT extraction and its method-specific result details and validation.

## IAT Extraction

Frames remain in their recorded PCAPNG order. For every consecutive pair:

```text
IAT_i = timestamp_i - timestamp_(i-1)
```

Each timestamp is interpreted using its interface's recorded resolution and
offset, then converted to one common time meaning before subtraction and
comparison. The deterministic representation must not collapse timestamps that
the input resolution distinguishes.

Zero IAT is valid because capture resolution can give multiple frames the same
timestamp. A negative IAT is invalid. The method never sorts frames to hide
backwards time.

Each input requires at least two scored frames and therefore provides at least
one IAT sample. The two files may provide different numbers of IAT samples.

## Result

The primary ranking result is the shared KS similarity for the two extracted
IAT samples. Method-specific result details record the raw KS distance and the
reference and generated IAT sample counts. No p-value is present.

The result follows the [similarity result
contract](../../contracts/60_30_similarity_result/README.md). `iat_ks` does not
interpret the primary result as genetic fitness; that remains the responsibility
of the invoking training strategy.

## Validation and Failure

Validation completes before scoring. It rejects malformed PCAPNG structure,
unsupported link types, fewer than two scored frames, a packet without a usable
timestamp, invalid timestamp resolution or offset metadata, backwards time,
non-finite calculations, and mathematical-library failure.

An invalid observation is never skipped or repaired. Failure produces no
fallback similarity and no successful partial result; the
[60 similarity evaluation application](../../apps/60_similarity_evaluation/README.md)
retains its normal startup diagnostics.

## Limits

Absolute capture start time is irrelevant because only consecutive differences
are compared. The method uses recorded frame order to extract IATs, but it then
compares only their marginal distributions; it does not compare the order of
the IAT values themselves. Traffic with different burst or idle sequences can
therefore receive the same score when its IAT distributions are equal.

The method does not score total frame count, frame length, captured contents,
addresses, flows, or protocol behavior.

## Testing

Future deterministic, unprivileged fixture tests cover identical and separated
IAT distributions, unequal sample counts, tied timestamps and zero IAT,
backwards timestamps, the two-frame minimum, timestamp-resolution and offset
conversion, malformed metadata, unsupported link types, absence of p-values,
and agreement with the selected library's KS statistic. No test requires live
capture, network access, or elevation.

## Reading

Follow the [architecture governance](../../README.md), the
[similarity-method registry](../README.md), the shared KS rules, and the
similarity result contract before changing this method.
