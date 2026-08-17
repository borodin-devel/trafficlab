# Trafficlab Architecture

## Purpose

Trafficlab is a one-person research prototype for capturing the network traffic
of containerized programs, fitting competing classical stochastic models with a
genetic algorithm, generating synthetic PCAPNG traces, and measuring how closely
they resemble a reference capture.

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

## Research fitness closure

The [research fitness assessment](../docs/RESEARCH_FITNESS_ASSESSMENT.md) now
records the 2026-08-17 unchanged-rubric reassessment. All 17 criteria reopened by
the [Roadmap](ROADMAP.md) meet Acceptable or better, backed by the accepted
[r18 Validation Study](../examples/validation_study/REPORT.md), final local gates,
offline clean-clone reconstruction, and a final documentation re-review with no
Critical, Important, or Minor findings.

The completed work tightens scientific correctness, configurability, robustness,
and reproducibility while preserving the same one-process MVP workflow. It adds
no new model, metric, service, security subsystem, enterprise infrastructure, or
multi-user scope. The approved remediation design and earlier evidence remain
development history rather than current status.

## Principles

- One Python program owns the pipeline; stages call one another in process.
- Docker Compose owns workload isolation, Internet connectivity, and cleanup.
- Reliability means deterministic seeds, bounds, validation, atomic results,
  timeouts, and resumable genetic fitting.
- Scientific extension points are small Python interfaces, not applications or
  process boundaries.
- uv and `pyproject.toml` are the only Python project and tooling interfaces.
- Only implemented algorithms receive architecture documents.
- Mathematical documents cite authoritative sources and label local design
  choices explicitly.
- One readable roadmap owns the implementation sequence.

## Documents

- [System](SYSTEM.md) defines the workflow, CLI, data, artifacts, and failure
  behavior.
- [Development](DEVELOPMENT.md) defines the fixed Python toolchain, quality
  commands, and Git workspace policy.
- [Capture](CAPTURE.md) defines the Docker topology and reliable lifecycle.
- [Testing](TESTING.md) defines unit and integration evidence.
- [Roadmap](ROADMAP.md) gives the ordered MVP implementation path.
- [Research fitness criteria](RESEARCH_FITNESS_CRITERIA.md) defines a five-level
  rubric for scientific correctness, configurability, robustness, and
  reproducibility without grading the current implementation.
- [Research fitness assessment](../docs/RESEARCH_FITNESS_ASSESSMENT.md) records
  the original evidence grades and the 2026-08-17 closure reassessment.
- [Remediation design](../docs/superpowers/specs/2026-08-14-research-fitness-remediation-design.md)
  defines the minimum Acceptable target and targeted phase reopenings.
- [Genetic models](genetic_models/README.md) describes model-family competition
  and the enabled evolutionary strategy.
- [Traffic models](traffic_models/README.md) owns the common model interface and
  the three MVP families.
- [Similarity methods](similarity_methods/README.md) owns component scoring and
  aggregate fitness.

## Scope boundaries

The MVP includes Docker capture, PCAPNG input and output, Poisson empirical,
Markov Renewal, two-state MMPP, a basic generational genetic algorithm,
frame-size KS, IAT KS, autocorrelation, and multiscale-rate comparison.

It excludes traffic replay, payload or application-protocol modelling,
distributed execution, multi-user operation, neural and diffusion models,
optimal transport, wavelets, long-term public compatibility guarantees, and
security hardening. Docker's own access requirements remain an installation
concern rather than a Trafficlab subsystem.

## Changing the architecture

Edit the document that owns the affected behavior and rely on Git history for
past decisions. Add an algorithm document only with its implementation, tests,
and registry entry; remove the document when the algorithm is removed. Do not
create amendments, versioned design documents, SAD/SRS document sets, or empty
algorithm placeholders.
