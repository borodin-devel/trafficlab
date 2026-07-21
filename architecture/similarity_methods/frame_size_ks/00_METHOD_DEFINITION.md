# 00 Frame-Size KS Method Definition

## Role

`frame_size_ks` is a selectable similarity method that compares the
distributions of original Layer 2 frame lengths in one reference Ethernet
PCAPNG and one generated Ethernet PCAPNG.

## Shared Behavior

The [KS family](../KS.md) owns the empirical-CDF distance, `1 - D` similarity,
score range and direction, p-value exclusion, Ethernet boundary, unequal-sample
support, implementation policy, and common comparison limits. This document
owns only original-length extraction and its method-specific result details and
validation.

## Frame-Length Extraction

For every scored packet, the method uses only the PCAPNG original packet length
as its Layer 2 frame-length observation. It never substitutes, scores, or
otherwise uses the retained captured byte count or packet contents as a
comparison feature.

A truncated packet remains a valid observation when its original-length
metadata is present and valid. Its captured length may be smaller, but only its
original length enters the sample.

Each input requires at least one scored frame. The two files may provide
different numbers of frame-length samples.

The method does not impose a current traffic model's supported generation
range. An otherwise valid Ethernet frame length outside that range remains in
the sample so the resulting distance exposes the traffic model's inability to
reproduce it. The method never skips, clamps, or rewrites such an observation.

## Result

The primary ranking result is the shared KS similarity for the two extracted
frame-length samples. Method-specific result details record the raw KS distance
and the reference and generated frame counts. No p-value is present.

The result follows the [similarity result
contract](../../contracts/60_30_similarity_result/README.md). `frame_size_ks`
does not interpret the primary result as genetic fitness; that remains the
responsibility of the invoking training strategy.

## Validation and Failure

Validation completes before scoring. It rejects malformed PCAPNG structure,
unsupported link types, an input without a scored frame, missing or invalid
original-length metadata, an internally inconsistent packet record, non-finite
calculations, and mathematical-library failure.

An invalid observation is never skipped or repaired. Failure produces no
fallback similarity and no successful partial result; the
[60 similarity evaluation application](../../apps/60_similarity_evaluation/README.md)
retains its normal startup diagnostics.

## Limits

The method compares only the marginal distribution of recorded original frame
lengths. It does not score total frame count, timestamps, inter-arrival times,
captured contents, frame ordering, addresses, flows, or protocol behavior.

Two captures with the same frame-length distribution can therefore receive the
same score even when their timing, ordering, or traffic behavior differs.

## Testing

Future deterministic, unprivileged fixture tests cover identical and separated
frame-length distributions, unequal sample counts, tied lengths, one-frame
inputs, truncated packets whose original and captured lengths differ, valid
lengths outside a current generator range, malformed or missing original-length
metadata, internally inconsistent records, unsupported link types, absence of
p-values, and agreement with the selected library's KS statistic. Tests prove
that changing only retained packet bytes cannot change the extracted sample or
score. No test requires live capture, network access, or elevation.

## Reading

Follow the [architecture governance](../../README.md), the
[similarity-method registry](../README.md), the shared KS rules, and the
similarity result contract before changing this method.
