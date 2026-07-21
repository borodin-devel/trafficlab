# KS Similarity-Method Family

## Role

This document owns behavior shared by Trafficlab's Kolmogorov-Smirnov-based
similarity methods. It is not a selectable similarity-method name; selectable
methods reference these rules and own their feature extraction and any combined
scoring behavior.

## Distance and Similarity

For the empirical cumulative distribution functions of one reference sample
and one generated sample, the two-sided KS distance is:

```text
D = max_x(abs(F_reference(x) - F_generated(x)))
```

The family converts that distance directly to similarity:

```text
similarity = 1 - D
```

Both `D` and `similarity` are naturally in `[0, 1]`. A higher similarity is
better. This comparison accepts unequal reference and generated sample counts
and introduces no histogram bins or normalization scale.

## P-Value Exclusion

The KS p-value is not a similarity value. It answers a hypothesis-testing
question and changes with sample count even when `D` is unchanged. Using it for
ranking could therefore favor a candidate because of its sample count rather
than because its empirical distribution is closer to the reference.

These methods neither use nor publish a p-value. If a selected mathematical
library also calculates one, that value is ignored and does not enter result
serialization, diagnostics, or fitness ranking. This also avoids applying the
standard continuous-distribution p-value interpretation to discrete frame
lengths or tied timestamps.

## Implementation

Implementations follow the registry's [library policy](README.md#implementation-policy).
They should obtain the two-sided empirical-CDF statistic from a suitable
maintained Python library, such as SciPy, when that library satisfies these
rules. The [similarity result](../contracts/60_30_similarity_result/README.md)
identifies the method implementation version and every relevant mathematical
library version that can affect the result.

## Computational Complexity

Let `n` and `m` be the reference and generated sample counts. A conventional
empirical-CDF implementation sorts both samples, requiring
`O(n log n + m log m)` time and `O(n + m)` sample storage; a maintained library
may use an equivalent bounded-domain optimization. Extraction is linear in the
input frame counts. Implementations record and test resource limits instead of
silently downsampling either empirical distribution.

## PCAPNG Boundary

The basic KS methods compare Ethernet-form Layer 2 traffic. This includes
application traffic presented by Linux as Ethernet while an ordinary Wi-Fi
connection transports it. Raw IEEE 802.11 and Radiotap monitor-mode captures
are outside this family.

Every scored packet references valid Ethernet interface metadata. Each method
validates the PCAPNG structure and the timestamp or original-length metadata
that its own extraction requires. It does not silently skip, sort, clamp,
repair, or reinterpret an invalid observation. A validation or mathematical
library failure makes the evaluation unsuccessful; no fallback similarity is
invented.

## Comparison Boundary

The methods compare marginal empirical distributions. They do not score total
packet count or traffic volume. They also do not establish equivalence of
packet ordering, burst structure, joint timing-size behavior, addresses,
flows, protocol behavior, or an entire traffic-generating process.

## Reading

Follow the [architecture governance](../README.md), the
[similarity-method registry](README.md), the selected method owner, and the
[similarity result contract](../contracts/60_30_similarity_result/README.md)
before changing this family.
