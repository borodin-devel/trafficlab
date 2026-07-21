# Basic Similarity Methods Design

**Date:** 2026-07-20

**Status:** Approved design

## Goal

Add a small interchangeable set of similarity methods for comparing one
reference PCAPNG file with one generated PCAPNG file. The first methods compare
Layer 2 frame timing and frame length without attempting to model flows,
protocols, or application behavior.

## Scope

This design adds three selectable methods:

- `iat_ks`, comparing inter-arrival-time distributions;
- `frame_size_ks`, comparing original Layer 2 frame-length distributions; and
- `l2_ks_weighted`, combining the two component similarities with configurable
  weights.

It also adds one non-selectable KS-family owner for behavior shared by those
methods and extends the similarity-method registry with common score and
implementation guidance.

This design does not implement similarity evaluation, change genetic fitness
selection, compare packet counts, or define an advanced sequence-, burst-,
flow-, protocol-, or raw IEEE 802.11-aware method.

## Document Structure

```text
architecture/similarity_methods/
├── README.md
├── KS.md
├── iat_ks/
│   └── README.md
├── frame_size_ks/
│   └── README.md
└── l2_ks_weighted/
    └── README.md
```

`KS.md` is a shared family owner, not a selectable method. Each selectable
method directory owns only its feature extraction, settings, validation, score
details, limitations, and method-specific tests.

## Common Similarity Guidance

The similarity-method registry will state that methods should prefer a
normalized score in `[0, 1]`, with higher values meaning more similarity, only
when that transformation is mathematically meaningful. Genetic algorithms do
not inherently require this range. A method must not apply an arbitrary
normalization that would make its score misleading; every method declares its
actual range, direction, and interpretation.

Similarity implementations should prefer an established, maintained, and
well-tested Python library when it provides the required behavior. A
handwritten mathematical implementation is justified only when no suitable
library exists or available libraries cannot satisfy the method's documented
behavior. The method must record that reason and require direct correctness
tests against authoritative examples or an independent implementation.

## Shared KS Behavior

For reference empirical CDF `F_reference` and generated empirical CDF
`F_generated`, the two-sided KS distance is:

```text
D = max_x(abs(F_reference(x) - F_generated(x)))
```

The family defines similarity as:

```text
similarity = 1 - D
```

`D` and `similarity` are naturally in `[0, 1]`; higher similarity is better.
No histogram bins or artificial scale are introduced.

Only the KS distance is used. A KS p-value is neither a ranking input nor a
published diagnostic because it measures evidence against a null hypothesis,
not distribution distance. It varies with sample count for the same `D`, can
bias fitness when generated captures have different lengths, and its standard
continuous-distribution interpretation is unsuitable for discrete frame sizes
and tied timestamps.

The implementation should use the statistic from a suitable maintained Python
library, such as SciPy, when compatible with these rules. Library-generated
p-values are ignored. The implementation and relevant library version are
included in normal result lineage so later runs remain explainable.

Unequal reference and generated sample counts are valid. These methods compare
the shapes of empirical distributions; they do not score total frame count or
traffic volume.

## PCAPNG Boundary

The first KS methods support Ethernet-form Layer 2 traffic. This includes
application traffic exposed by Linux as Ethernet while it is transported over
an ordinary Wi-Fi connection. Raw IEEE 802.11 and Radiotap monitor-mode
captures are outside this basic family and may be supported by future advanced
methods.

Every scored packet must reference valid Ethernet interface metadata. Invalid
PCAPNG structure, unsupported link types, metadata required by the selected
method that is missing or invalid, or an unreadable selected observation fails
validation. A method does not skip, clamp, repair, or silently reinterpret an
invalid observation.

## `iat_ks`

`iat_ks` reads frames in their recorded PCAPNG order. For each consecutive
pair, it computes:

```text
IAT_i = timestamp_i - timestamp_(i-1)
```

Timestamp resolutions are converted to one common exact time meaning before
comparison. Zero IAT is valid because capture timestamp resolution can assign
the same timestamp to multiple frames. A negative IAT is invalid; the method
does not sort frames and thereby hide backwards time.

Each input requires at least two frames, producing at least one IAT. The method
publishes its primary similarity, raw KS distance, and reference and generated
IAT sample counts.

## `frame_size_ks`

`frame_size_ks` uses only each packet's original recorded Layer 2 frame length.
It never substitutes or scores the captured byte count. Consequently, a
truncated packet remains usable when its original length metadata is present
and valid.

Each input requires at least one frame. The method publishes its primary
similarity, raw KS distance, and reference and generated frame counts.

The evaluator does not reject an otherwise valid Ethernet observation merely
because its length is outside a current traffic model's generation range. A
similarity method measures the supplied captures and must not hide a traffic
model's inability to reproduce an observed length.

## `l2_ks_weighted`

`l2_ks_weighted` applies the exact `iat_ks` and `frame_size_ks` component rules
to the same two inputs, then computes:

```text
score = iat_weight * iat_similarity
      + frame_size_weight * frame_size_similarity
```

The finite weights are non-negative and must total `1`. Defaults are:

```toml
iat_weight = 0.5
frame_size_weight = 0.5
```

Invalid weights fail configuration validation; they are not silently
normalized. Each input requires at least two frames because both components
must be valid. The result records the two configured weights, both component
similarities, both raw KS distances, their sample counts, and the final score.

## Result and Failure Behavior

Each successful method produces one primary ranking score through the existing
`similarity.toml` contract. Method-specific details make the calculation
auditable without changing the genetic-training application's ownership of
fitness interpretation.

Too few frames, backwards timestamps, unsupported link types, invalid original
length metadata, invalid weights, non-finite calculations, or library failure
make the evaluation unsuccessful. No fallback score is invented and no
partial successful result is published. Existing startup diagnostics remain
available.

## Limitations

The basic methods compare marginal empirical distributions only. They do not
distinguish different packet orders that yield the same distributions, model
burst or idle-period structure beyond the overall IAT distribution, measure
joint dependence between timing and size, inspect packet bytes, compare
addresses or flows, or interpret IP, TCP, UDP, or higher protocols.

These limitations are explicit so a high KS similarity is not interpreted as
proof that two complete traffic processes are equivalent.

## Alternatives Considered

### Jensen-Shannon distance

Jensen-Shannon distance can compare probability vectors and can have a useful
bounded interpretation. It was not selected for the first IAT method because
continuous IAT observations would require histogram bins or another density
representation. Those choices materially affect the result.

### Wasserstein distance

Wasserstein distance has an intuitive interpretation in the feature's native
units. It was not selected for the first combined method because IAT and frame
size distances would use incompatible units, and converting either to `[0, 1]`
would require a scale that is not yet justified.

### KS p-value

The KS p-value was rejected as a similarity score because it answers a
hypothesis-testing question and depends on sample size. It does not measure the
magnitude of distribution difference needed for genetic ranking.

## Testing Expectations

Future deterministic, unprivileged tests cover:

- identical and maximally separated empirical distributions;
- unequal sample counts and tied observations;
- zero and backwards IATs;
- minimum-frame validation for each method;
- original frame length versus truncated captured length;
- unsupported link types and malformed PCAPNG metadata;
- default, custom, and invalid combined weights;
- component and final score reporting;
- the absence of p-values from ranking and output; and
- agreement with authoritative library examples or an independent oracle.

No test requires live capture, network access, or elevated privileges.
