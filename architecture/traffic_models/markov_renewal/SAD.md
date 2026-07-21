# Markov Renewal Software Architecture Document

## Context, Goals, and Boundaries

This model captures first-order dependence in joint Layer 2 IAT/frame-length
states and emits only observed pairs. It models no protocols, addresses,
payloads, directions, flows, sessions, or cross-capture transitions.

## Internal Structure and Data Flow

Preparation consumes a fully applied candidate configuration, derives ordered
observations, constructs deterministic automatic or manual states, counts
start membership, emissions, and transitions, then publishes a distinct
immutable generation-ready model. Generation samples start state, observed
emission, and next-state transitions; dead ends restart from start weights.

## Extension, Configuration, Errors, and Resources

Automatic modes are quantile, exact, or normalized k-means cluster; manual mode
uses complete nonoverlapping ranges. Trainable complexity exists only in
automatic quantile/cluster counts. Preparation is linear except library
clustering; exact state/transition storage can grow quadratically in distinct
observations and requires explicit resource admission. Packet, output-byte,
and proposal limits additionally bound every generation invocation.

## Security, Logging, Testing, Risks, and Limits

Reference PCAPNG is untrusted and processed offline without privilege. Logs
record counts/hashes, not packets. Unit/fixture tests cover every state mode,
dead ends, determinism, and hashes. Limits include first-order memory,
reference-only emissions, state explosion, cluster sensitivity, and no
protocol/direction behavior.
