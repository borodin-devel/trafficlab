# Basic Generational Software Architecture Document

## Context, Goals, and Non-Goals

The strategy provides a deterministic baseline GA supporting heterogeneous
traffic-model names. It does not own files, child processes, model equations,
traffic generation, or similarity formulas.

## Structure and Runtime Interaction

Genetic training supplies immutable normal-builder baselines and complete
candidate evaluation results. It applies each decided candidate configuration
before model-owned preparation; the strategy never edits a builder or reuses a
prepared model as a mutable baseline. The strategy core creates populations,
restores stable order after a whole-generation barrier, selects parents/elites,
applies operators, checks stopping, and returns immutable decisions. Randomness
comes from one recorded seeded stream in canonical operation order.

## Extension, Configuration, and Errors

Traffic models extend the strategy through declared trainable parameters and
complete validators. Similarity methods provide score direction/range. The
strategy configuration is explicit. Invalid candidates retry a whole bounded
attempt; no clamping or repair occurs. Exhaustion or no valid candidate fails.

## Performance, Resources, Security, and Logging

Candidate evaluations may run in parallel, but strategy decisions wait for the
generation and ignore completion order. Population and attempt limits bound
work. The core needs no files, network, or privilege. Lineage records every
random/operator decision rather than verbose per-packet data.

## Testing, Decisions, Risks, and Limits

Pure deterministic tests cover every branch and seeded draw order; fake-child
integration belongs to genetic training. Risks are expensive populations,
invalid search domains, model imbalance, and premature stopping. This strategy
uses a single primary fitness and no diversity objective.
