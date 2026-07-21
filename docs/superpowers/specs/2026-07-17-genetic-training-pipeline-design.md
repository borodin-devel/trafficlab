# Genetic Training Pipeline Design

**Date:** 2026-07-17<br>
**Status:** approved design, pending architecture stubs

## Purpose

Define an extensible training pipeline that evolves traffic-model candidates
against one reference PCAPNG file and publishes one winning model. The design
keeps traffic models, genetic strategies, and similarity systems replaceable
without changing the orchestration role of the applications.

## Scope

Create architecture stubs for these applications:

- `30_genetic_training`;
- `40_model_creation`;
- `50_traffic_generation`; and
- `60_similarity_evaluation`.

Create architecture registries for `traffic_models/`, `genetic_models/`, and
the new `similarity_methods/`. Define only the minimum contract for a
similarity result consumed by training. This work does not implement any
application, mathematical model, genetic algorithm, PCAPNG generation,
similarity calculation, or executable command.

## Pipeline

One training run accepts one reference PCAPNG file. It evaluates each
candidate through this file-oriented sequence:

```text
30_genetic_training
  -> 40_model_creation
  -> candidate model file
  -> 50_traffic_generation
  -> generated PCAPNG file
  -> 60_similarity_evaluation(reference, generated)
  -> similarity.toml
  -> 30_genetic_training
```

`30` launches the other applications as normal child applications and retains
their per-candidate files. It uses one similarity evaluation for each
candidate: the supplied reference PCAPNG and the single generated PCAPNG. The
names and paths of generated files are configurable; names such as
`gen_all.pcapng` are examples, not contracts.

## Candidate and Genetic Rules

A candidate is one named traffic model with that model's current parameter
values. Candidates using different traffic models compete in the same run.

The configured genetic strategy controls population creation, selection,
mutation, crossover, model replacement, and stopping. Crossover is permitted
only between candidates with the same traffic-model name. Mutation may replace
one allowed traffic model with another allowed traffic model.

All genetic behavior, including fitness interpretation and selection, belongs
to `30_genetic_training`. `40_model_creation` remains simple: it creates a
selected model's file with normal starting values. `30` writes mutated and
crossover-derived parameter values into each candidate model file before
generation.

At completion, `30` publishes one winning model and a ranking report. A
candidate whose child application fails is retained with its diagnostics but
is not eligible to win; training continues with other candidates.

## Application Interfaces

The command forms below are architectural interfaces, not implemented commands:

```text
genetic_training --config-file .configs/genetic_strategy.toml --output-dir DIR
model_creation --model=NAME --output-dir=DIR
traffic_generation --model-file=PATH --output=PATH
similarity_evaluation --reference=PATH --generated=PATH --method=NAME --output-dir=DIR
```

`30_genetic_training` requires both arguments. Its configuration names the
reference PCAPNG, allowed traffic-model names, one genetic-strategy name, and
one similarity-system name. `40` creates `DIR/NAME.toml`; the file declares
its traffic-model name and current parameters. `50` creates exactly one
synthetic PCAPNG at the requested output path. `60` compares exactly the two
supplied PCAPNG files using the named similarity system and writes
`DIR/similarity.toml`.

## Component Registries

```text
architecture/traffic_models/<name>/
architecture/genetic_models/<name>/
architecture/similarity_methods/<name>/
```

A traffic-model directory owns that model's equations, parameter schema,
normal starting values, model-file validation, and synthetic-traffic behavior.
A genetic-model directory owns one named training strategy's rules and
compatibility requirements. A similarity-method directory owns one named
ranking system's input expectations, output interpretation, configuration,
and validation.

The training configuration selects components by name. A future mixed model
or model pipeline is registered as another traffic model, so the training
application does not need a separate mixed-model mechanism.

## Configuration and Diagnostics

Versioned training templates belong in:

```text
configs/30_genetic_training/
```

The active local configuration is:

```text
.configs/genetic_strategy.toml
```

This one file combines genetic rules with the reference input, allowed traffic
models, selected similarity system, and other run settings. It is selected
explicitly with `--config-file`; `30` does not rely on implicit discovery.

Each application creates its own run directory and writes `launch.toml` at
startup. It records the invocation arguments and resolved configuration. If
configuration resolution fails, the startup record retains the arguments,
selected configuration source, and resolution failure. `launch.toml` remains
available for failed or interrupted runs as well as successful ones. Child
applications use per-candidate directories, so their diagnostic files cannot
collide.

The shared application-configuration architecture must be updated to own this
startup-and-retention behavior. `30`'s nonstandard template location and
configuration filename are an application-specific exception documented by
its owner.

## Ownership and Contracts

Traffic-model directories own their model-file schemas because each model owns
its parameters and validation. `40`, `30`, and `50` reference that owner
instead of defining a shared parameter schema.

A new `60_30_similarity_result` contract owns `similarity.toml`, the result
passed from similarity evaluation to genetic training. It will define the
system identity, ranking result, input lineage, validation status, and hash;
it will not define any specific similarity formula.

## Testing and Security Boundaries

All future unit and integration tests are unprivileged. Functional-core tests
cover deterministic candidate handling, same-model crossover enforcement,
model replacement, child-result handling, and winner selection. Imperative
shell tests use temporary directories and fake child-app executables to verify
argument vectors, file lineage, failure retention, and atomic publication.
No training, model creation, generation, or similarity test needs traffic
capture, network access, or elevation.
