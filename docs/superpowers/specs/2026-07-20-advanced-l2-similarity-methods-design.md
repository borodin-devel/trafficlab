# Advanced Layer 2 Similarity Methods Design

## Goal

Add architecture owners for five independently selectable Layer 2 traffic
similarity methods. They extend the existing frame-size KS, IAT KS, and
weighted-L2-KS methods without changing them.

## Scope

Create these owner documents:

- `architecture/similarity_methods/joint_sinkhorn_wasserstein/README.md`
- `architecture/similarity_methods/multiscale_rate/README.md`
- `architecture/similarity_methods/neighbor_transition/README.md`
- `architecture/similarity_methods/autocorrelation/README.md`
- `architecture/similarity_methods/sequence_kernel_mmd/README.md`
- `architecture/similarity_methods/TEMPORAL_L2.md`

Register the five method names in `architecture/similarity_methods/README.md`.
No Python implementation, configuration template, or test fixture is in scope.

## Shared Temporal Layer 2 Rules

All five methods consume two supported Ethernet pcapng files. They use only
ordered timestamps and original Ethernet frame lengths. They do not inspect
captured bytes, addresses, payloads, IP, TCP, UDP, flows, direction, or higher
protocols.

Each capture's first frame is shifted to time zero before comparison. IATs are
differences between successive ordered timestamps. Timestamps must be finite
and nondecreasing; zero IAT is valid. Original frame lengths must be integers
from 60 through 1514 bytes. Invalid inputs fail rather than being repaired.

All canonical extraction, ordering, input validation, output reporting, and
offline-test rules belong in `TEMPORAL_L2.md`. It also requires deterministic
implementation, explicit configuration, raw diagnostics in `similarity.toml`,
and no network access, live capture, or elevated privilege.

## Primary Score Convention

Every method produces one primary similarity in `[0, 1]`, where 1 is identical
under that method. The result also records raw measures and all configuration
needed to reproduce the score. A method may use a monotonic configured mapping
from an unbounded non-negative distance to `[0, 1]`; its owner defines that
mapping and its positive scale. Methods with a mathematically bounded raw
distance use their natural normalization.

The five methods are independent. No method silently incorporates another
method's result. A future combined method may use configured weights.

## Selectable Methods

### `joint_sinkhorn_wasserstein`

Builds the empirical cloud of observations
`(log1p(IAT seconds), original frame length)` for each capture. It compares the
two clouds using entropically regularized optimal transport (Sinkhorn) with
configured positive feature scales, regularization, and score-mapping scale.
It detects joint timing-size behavior but intentionally ignores packet order.

The owner documents the exact ground cost, empirical weights, Sinkhorn
divergence, convergence settings, and deterministic-library policy. It fails
when a capture lacks the two frames required to derive an IAT or the numerical
solver cannot meet configured convergence criteria.

### `multiscale_rate`

For configured positive window widths and a configured positive comparison
horizon, it starts bins at time zero. For each scale it builds packet-count and
original-byte-count vectors, including zero-count bins, over the same horizon.
It compares each pair of vectors with normalized L1 distance and combines the
packet and byte components with configured weights. It combines scale scores
with configured positive weights that sum to one.

Frames after the comparison horizon are excluded and their counts are reported.
The owner specifies normal scales, horizon, weights, score formula, and failures
for invalid or insufficient configuration. It detects burst and idle structure
but not packet-size/timing association inside a bin.

### `neighbor_transition`

Each noninitial frame becomes `(log1p(IAT seconds), original frame length)`.
Configured, nonoverlapping categories map every valid observation to exactly one
joint state. The method forms counts of adjacent state transitions and compares
their normalized distributions with Jensen-Shannon distance, whose natural
range is normalized to `[0, 1]` using base-2 logarithms.

Its owner defines category coverage, state ordering, transition serialization,
minimum input, and empty or uncovered-state failure. It detects one-step local
ordering but not longer dependencies.

### `autocorrelation`

For configured positive integer lags, it independently calculates sample
autocorrelation of `log1p(IAT seconds)` and of original frame length. Each
per-lag absolute difference is divided by two because correlation lies in
`[-1, 1]`. Configured weights combine features and lags into one distance;
similarity is `1 - distance`.

The method requires enough variation and observations for every requested lag.
It fails rather than inventing a correlation for a constant series. It detects
linear repetition at selected lags but not arbitrary nonlinear sequence
structure.

### `sequence_kernel_mmd`

Each capture is shifted to time zero and divided into configured fixed-width,
non-overlapping time windows over a configured positive horizon. Every window,
including an empty window, is one ordered sequence of
`(log1p(IAT seconds), original frame length)` observations. The method compares
the two sets of window sequences using a configured signature kernel and
maximum mean discrepancy (MMD).

The owner defines sequence representation, signature-kernel parameters,
bounded characteristic base-kernel requirements, MMD estimator, numerical
requirements, and a positive score-mapping scale. It records MMD and window
counts. It detects joint sequence structure, ordering, and bursts more broadly
than the preceding methods. It is an advanced method, not the first one to
implement.

## Configuration and Testing

Every method's owner documents its normal configuration, but implementation
templates remain future work. All method parameters—including category bounds,
window widths, horizon, weights, lags, solver tolerances, kernel parameters,
and mapping scales—are explicit and recorded by the similarity application in
`launch.toml`.

Future unit tests cover mathematics, score bounds, invalid data, determinism,
and known contrasting fixtures. Future integration tests use small offline
Ethernet pcapng fixtures and verify `similarity.toml` diagnostics. No test
requires network access, live capture, sudo, root, or other elevated privilege.
