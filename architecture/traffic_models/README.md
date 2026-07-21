# Traffic Models

## Role

This directory contains the named traffic models that genetic training may
select, create, mutate, and use to generate synthetic traffic.

## Protocol Scope

Traffic models primarily simulate Layer 2 behavior, especially Ethernet frame
timing and frame size. Basic traffic models remain entirely at Layer 2.

Only explicitly advanced traffic models may additionally simulate IPv4, IPv6,
TCP, or UDP behavior. Application-layer and other higher-level protocol
simulation is outside Trafficlab's traffic-model scope.

## Layout

Each model belongs in `traffic_models/<name>/`. Its `README.md` owns that
model's equations, assumptions, parameter schema, normal starting values,
self-describing `NAME.toml` model-file validation, synthetic-traffic behavior,
and validation criteria.

Applications reference a traffic-model owner instead of redefining its
parameters or mathematics. A mixed model or model pipeline is registered as
another traffic model with its own name and owner document.

## Model Lifecycle

Published model files are immutable values. Every mature model distinguishes
the states in this lifecycle through its versioned validator:

```text
normal builder -> in-memory candidate configuration -> generation-ready model
```

Every mature model file carries the common top-level string `model_state`. It
is exactly `"builder"` in model-creation output and exactly
`"generation_ready"` after model-owned preparation/finalization. The candidate
configuration is an in-memory value, not a third published file state. Missing,
unknown, or inconsistent state is invalid.

[Model creation](../apps/40_model_creation/README.md) publishes only a complete
validated normal builder. Genetic training reads but never edits or republishes
that file. It copies the builder values, applies and validates fixed run
context plus all candidate parameters in memory, then invokes the model-owned
preparation protocol. Preparation
atomically publishes a new generation-ready file containing the builder digest,
candidate identity, effective parameters, reference assignment and hashes,
seeds, implementation versions, and all fitted or finalized model-owned data.

Preparation derives learned data only after candidate values are applied. A
model with no reference-dependent fit still performs a deterministic finalize
transition and publishes a distinct generation-ready file. Traffic generation
accepts only the generation-ready state; a builder or transient candidate
configuration is invalid generation input. Each model owner defines the exact
state representation and additional builder/prepared schema fields without
weakening these common transitions.

## Models

| Selectable name | Owner | Packet-size rule |
| --- | --- | --- |
| `poisson_uniform` | [Poisson uniform](poisson_uniform/README.md) | Inclusive discrete uniform distribution |
| `poisson_empirical` | [Poisson empirical](poisson_empirical/README.md) | Observed discrete frequency table |
| `markov_renewal` | [Markov Renewal](markov_renewal/README.md) | Observed IAT/frame-length pairs with first-order state transitions |
| `neural_hawkes` | [Neural Hawkes](neural_hawkes/README.md) | Causal self-attentive marked temporal point process |
| `marked_point_process_diffusion` | [Marked point-process diffusion](marked_point_process_diffusion/README.md) | Conditional whole-window marked point-process diffusion |

## Unresolved, Unselectable Models

These directories preserve research scope but define no implementation. Model
creation, training, and generation must reject their names until each roadmap's
specification gate passes:

| Planned name | Owner | Missing design class |
| --- | --- | --- |
| `farima` | [FARIMA](farima/README.md) | Fractional time-series mathematics |
| `map_bmap` | [MAP/BMAP](map_bmap/README.md) | Matrix/batch process semantics |
| `mmpp` | [MMPP](mmpp/README.md) | Hidden-state Poisson process |
| `on_off_pareto` | [ON/OFF Pareto](on_off_pareto/README.md) | Heavy-tail period process |
| `packet_train` | [Packet train](packet_train/README.md) | Burst-train process |
| `state_space` | [State space](state_space/README.md) | Latent-state family and inference |

The [Poisson family](POISSON.md) owns timing, stopping, randomness, and Layer 2
length rules shared by both models. It is not a selectable model.

## Reading

Follow the [architecture governance](../README.md). Read the relevant model
owner before changing [model creation](../apps/40_model_creation/README.md),
[traffic generation](../apps/50_traffic_generation/README.md), or
[genetic training](../apps/30_genetic_training/README.md). Read the
[neural marked-point-process common rules](NEURAL_MARKED_POINT_PROCESS.md)
before changing either registered neural model.

Every model directory contains a concise README, SAD, testable SRS,
configuration status, and implementation/testing roadmap. Complex models split
representation, mathematics, fitting, generation, and validation into ordered
supporting documents.
