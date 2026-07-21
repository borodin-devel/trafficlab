# Trafficlab Architecture Documentation

## System Overview

Trafficlab is a Linux research suite that captures a target process tree,
prepares PCAPNG references, fits replaceable traffic models, generates
synthetic PCAPNG, evaluates similarity, and tunes candidates with genetic
strategies. Applications exchange validated file artifacts and may run alone,
as a partial pipeline, or as an end-to-end experiment.

This directory is the self-contained source of truth for Trafficlab
architecture. Statements elsewhere must defer to the owner documents linked
from this corpus.

## Documentation Structure

- [System component](project/README.md): cross-cutting SAD, SRS, workflows,
  configuration, resource, logging, and roadmap.
- [Infrastructure](infrastructure/README.md): platform, build, test, packaging,
  continuous integration, and static analysis.
- [IDE integration](ide_integration/VSCODE.md): optional repository-owned VS
  Code workspace support.
- [Applications](apps/README.md): standalone executable process boundaries.
- [Libraries](libs/README.md): reusable in-process boundaries.
- [Contracts](contracts/README.md): inter-application file interfaces.
- [Scripts](scripts/README.md): manual operator and privilege boundaries.
- [Traffic models](traffic_models/README.md), [genetic models](genetic_models/README.md),
  and [similarity methods](similarity_methods/README.md): replaceable algorithms.

## Standard Documents and Naming

Every component directory contains `README.md`, `SAD.md`, and `SRS.md`.
Applications and libraries also contain `ROADMAP.md`; independent models,
methods, scripts, and contracts contain one as well. Components with settings
contain `CONFIGS.md`. The [central roadmap](project/ROADMAP.md) links every
component roadmap once with a brief scope description; each component roadmap
links back and remains the sole owner of its detailed work and evidence. Every
file stem contains at least one uppercase ASCII letter and uses uppercase snake
case before its lowercase extension, such as `README.md`, `CONFIGURATION.md`,
or `00_MODEL_DEFINITION.md`. Every directory name contains at least one
lowercase ASCII letter and uses lowercase snake case, such as `traffic_models`
or `00_preflight`. Numeric prefixes order multi-stage algorithms and workflows;
prefixes never become runtime names.

## Component Classification

An **application** owns a command, process lifecycle, configuration resolution,
and artifact publication. A **library** exposes reusable in-process behavior
without selecting pipeline stages. A **contract** owns file syntax, semantics,
validation, and producer-consumer compatibility. A **replaceable module** owns
one registered algorithm and schema. A **script** is a manually invoked
operator boundary, potentially privileged. Infrastructure owns development and
delivery mechanics rather than runtime research behavior.

## Roadmap Status Rules

Every roadmap hierarchy heading embeds exactly one status marker immediately
after its Markdown heading prefix and before `STAGE`, `STEP`, or `SUBSTEP`, for
example `## [PLAN] STAGE 1`, `### [ 25%] STEP 1.1`, or
`#### [DONE] SUBSTEP 1.1.1`. Separate `**Status:**` fields are invalid. The
controlled vocabulary is `[PLAN]`; a whole-number partial percentage from 1
through 99, right-aligned to three character positions (`[  1%]`, `[ 10%]`,
`[ 25%]`, `[ 50%]`, `[ 75%]`, `[ 99%]`); `[BLKD]`; `[CR_B]`; `[MK_B]`;
`[MN_B]`; `[TSTR]`; or `[DONE]`. Not-applicable markers, prose status values,
`[100%]`, zero, out-of-range, fractional, and incorrectly padded percentages
are invalid. Work outside scope is removed or stated as out of scope rather
than assigned a synthetic status.

`[PLAN]` means implementation has not started; architecture and planning alone
do not advance it. `[DONE]` requires completed implementation and all required
tests. `[TSTR]` identifies complete implementation with missing, incomplete, or
failing required tests. Blocked, bug, and test statuses state their reason or
evidence at the affected entry.

A parent reflects its children: critical, major, and minor bugs take their
respective precedence, then blocking, test-incomplete, partial progress, and
all-planned state. A parent is `[DONE]` only when every child is `[DONE]` and is
`[PLAN]` only when every child is planned. The complete heading, evidence, and
percentage rules are owned by [corpus validation](VALIDATION.md).

## Reading Order

1. This document.
2. [System SAD](project/SAD.md), [system SRS](project/SRS.md), and
   [system roadmap](project/ROADMAP.md).
3. [Infrastructure](infrastructure/README.md), shared
   [configuration](CONFIGURATION.md), and [VS Code integration](ide_integration/VSCODE.md).
4. Applications in numeric order: [00](apps/00_preflight/README.md),
   [10](apps/10_capture/README.md), [20](apps/20_convert/README.md),
   [25](apps/25_inspection_export/README.md),
   [30](apps/30_genetic_training/README.md),
   [40](apps/40_model_creation/README.md),
   [50](apps/50_traffic_generation/README.md),
   [60](apps/60_similarity_evaluation/README.md), and
   [99](apps/99_trafficlab/README.md).
5. Each selected application's contracts, libraries, models, methods, and scripts.
6. [Corpus validation](VALIDATION.md).

## Ownership and Revision

One document owns each detailed fact; other documents link to it. During this
formalization, architecture files may be revised directly. Afterward, changes
must update the owner, affected references, SRS traceability, roadmap
verification, and relative-link validation. An amendment is reserved for an
irreconcilable conflict, not missing documentation or implementation.

An amendment names the conflicting sources, decision and rationale, scope and
consequences, alternatives, compatibility or migration effects, status, owner,
and date. It belongs at `amendments/<NUMBER>_<SHORT_NAME>.md`.
