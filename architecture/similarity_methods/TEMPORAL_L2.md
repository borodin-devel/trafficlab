# Temporal Layer 2 Similarity Rules

## Role

This document owns the canonical temporal Layer 2 extraction, input
validation, reproducibility, result-detail, and future-test rules shared by
the advanced temporal similarity methods. It does not define the behavior of
the [KS family](KS.md), whose existing owner remains unchanged.

## PCAPNG Boundary and Canonical Observations

Each evaluation consumes one reference and one generated supported Ethernet
PCAPNG file. Every scored packet must reference valid Ethernet interface
metadata. Raw IEEE 802.11, Radiotap monitor-mode, and other link types are
unsupported.

For each frame, extraction uses only these two Layer 2 metadata values:

- its recorded timestamp; and
- its original Ethernet frame length.

Extraction never reads or compares captured bytes, retained captured length,
addresses, payloads, IP, TCP, UDP, flows, direction, or other
higher-protocol fields. A truncated frame is therefore usable only when its
original frame length is present and valid.

Frames retain their recorded PCAPNG order. For each capture, the first frame's
timestamp is subtracted from every frame timestamp, so its canonical time is
zero. The interval between every noninitial frame and its predecessor is:

```text
IAT_i = timestamp_i - timestamp_(i-1)
```

Timestamp metadata is interpreted using its interface's recorded resolution
and offset before this alignment and subtraction. The deterministic timestamp
representation must preserve distinctions expressible by that metadata.

## Validation and Failure

Validation completes before scoring. Timestamps must be present, finite, and
nondecreasing in recorded order. Their absolute values may be negative: the
first-frame alignment makes comparison times relative. A zero IAT is valid; a
negative IAT is invalid because it would require decreasing timestamps.
Original Ethernet frame lengths must be integer values from 60 through 1514
bytes, inclusive.

Malformed PCAPNG structure, unsupported or invalid interface metadata, missing
or invalid timestamp or original-length metadata, an invalid timestamp
resolution or offset, non-finite calculations, or a method-specific minimum
input failure makes the evaluation unsuccessful. An invalid observation is
never skipped, sorted, clamped, repaired, or reinterpreted, and no fallback
similarity or successful partial result is published.

## Determinism and Configuration

Implementations process canonical observations in recorded order and make all
ordering and serialization deterministic. Every parameter needed to reproduce
a method's score is explicit method configuration, including any categories,
window widths, horizons, weights, lags, solver tolerances, kernel parameters,
or score-mapping scales. The [60 similarity evaluation
application](../apps/60_similarity_evaluation/README.md) records the selected
configuration in `launch.toml` under the shared startup-record rules.

Each advanced temporal method produces one reproducible primary similarity in
`[0, 1]`, where `1` means identical under that method, and records its raw
measures and configuration needed to reproduce the score as method-defined
diagnostics in `similarity.toml`. The [similarity result
contract](../contracts/60_30_similarity_result/README.md) owns result
validation, hashing, and atomic publication; this document does not redefine
those application responsibilities.

## Future Testing

Future unit tests cover each method's mathematics, score bounds, invalid input,
determinism, and known contrasting fixtures. Future integration tests use
small offline Ethernet PCAPNG fixtures and verify `similarity.toml` raw
diagnostics. No test requires network access, live capture, sudo, root, or
other elevated privilege.

## Reading

Follow the [architecture governance](../README.md), the
[similarity-method registry](README.md), the selected method owner, the
[60 similarity evaluation application](../apps/60_similarity_evaluation/README.md),
and the [similarity result contract](../contracts/60_30_similarity_result/README.md)
before changing these rules or a method that references them.
