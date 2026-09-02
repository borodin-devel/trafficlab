# Trafficlab Architecture

## Purpose

Trafficlab is a one-person research prototype for capturing the network traffic
of containerized programs, fitting competing classical stochastic models with a
genetic algorithm, generating synthetic PCAPNG traces, and measuring how closely
they resemble a reference capture. An optional desktop visualization companion
opens one canonical run directory read-only without changing the one-process
pipeline.

The design favors fast scientific iteration. It keeps reproducibility,
validation, bounded execution, cleanup, and checkpointing, while avoiding
infrastructure that a single-process prototype does not need.

## MVP workflow

```text
experiment.toml
  -> preflight
  -> capture
  -> fit
  -> generate
  -> compare
```

The complete `trafficlab run` command performs this sequence. Each stage is also
available separately so a researcher can iterate on a model or metric without
repeating a real capture.

## Principles

- One Python program owns the pipeline; stages call one another in process.
- Docker Compose owns workload isolation, Internet connectivity, and cleanup.
- Reliability means deterministic seeds, bounds, validation, atomic results,
  timeouts, and resumable genetic fitting.
- Scientific extension points are small Python interfaces, not applications or
  process boundaries.
- uv and `pyproject.toml` are the only Python project and tooling interfaces.
- Only implemented algorithms receive normative algorithm documents. The
  non-normative candidate catalog is the sole exception and does not declare
  candidate behavior part of the architecture.
- Mathematical documents cite authoritative sources and label local design
  choices explicitly.
- Architecture documents describe intended behavior and stable boundaries; they
  never track implementation sequence, task state, or completion progress.

## Documents

- [System](SYSTEM.md) defines the workflow, CLI, data, artifacts, and failure
  behavior.
- [Development](DEVELOPMENT.md) defines the fixed Python toolchain, quality
  commands, and Git workspace policy.
- [Capture](CAPTURE.md) defines the Docker topology and reliable lifecycle.
- [Visualization](VISUALIZATION.md) defines the optional desktop companion,
  aspect catalog, interaction, and display-reduction contract.
- [Testing](TESTING.md) defines unit and integration evidence.
- [Research fitness criteria](RESEARCH_FITNESS_CRITERIA.md) defines a five-level
  rubric for scientific correctness, configurability, robustness, and
  reproducibility without grading the current implementation.
- [Research candidates](CANDIDATES.md) catalogs literature-backed traffic-model
  and similarity-method alternatives without declaring them implemented.
- [Genetic models](genetic_models/README.md) describes model-family competition
  and the enabled evolutionary strategy.
- [Traffic models](traffic_models/README.md) owns the common model interface and
  the five MVP families.
- [Similarity methods](similarity_methods/README.md) owns component scoring and
  aggregate fitness.

## Scope boundaries

The MVP includes Docker capture, PCAPNG input and output, Poisson empirical,
Markov Renewal, two-state MMPP, piecewise-constant NHPP, exponential ACD, a
basic generational genetic algorithm, frame-size KS, IAT KS, autocorrelation,
multiscale-rate comparison, and one optional read-only desktop visualization
companion for checked run artifacts.

It excludes traffic replay, payload or application-protocol modelling,
distributed execution, multi-user operation, neural and diffusion models,
optimal transport, wavelets, long-term public compatibility guarantees, and
security hardening. The desktop companion does not become a pipeline stage,
service, container subsystem, or alternate parser. Docker's own access
requirements remain an installation concern rather than a Trafficlab subsystem.

## Changing the architecture

Edit the document that owns the affected behavior and rely on Git history for
past decisions. Add an algorithm document only with its implementation, tests,
and registry entry; remove the document when the algorithm is removed. Do not
create amendments, versioned design documents, SAD/SRS document sets, or empty
algorithm placeholders. Keep implementation plans, task checklists, completion
evidence, and progress state outside `architecture/`. `CANDIDATES.md` may
catalog and prioritize literature-backed model and similarity alternatives,
but it remains
non-normative and must not contain implementation plans, task state, or dated
experiment results.
