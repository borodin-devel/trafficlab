# Poisson Uniform Software Architecture Document

## Context, Goals, and Boundaries

This baseline model separates homogeneous Poisson timing from an independent
bounded discrete-uniform size law. It models no protocol, direction, flow,
payload, or correlation.

## Structure and Data Flow

Validation produces immutable parameters. One seeded RNG stream samples an
exponential IAT and inclusive integer frame length per event; stop policy bounds
the sequence. Traffic generation renders events into PCAPNG.

## Configuration, Errors, Performance, and Security

Schema and defaults are in CONFIGS.md and exact rules in model definition.
Invalid/non-finite values fail before sampling; samples are never clamped.
Runtime is linear in emitted packets with constant model memory. It runs offline
without file/network/privilege in the functional core.

## Testing, Decisions, Risks, and Limits

Tests cover support endpoints, one-value range, exponential timing, stops,
seeds, and validation. Main limit is complete independence between timing and
sizes, which cannot reproduce bursts, ordering, or joint behavior.
