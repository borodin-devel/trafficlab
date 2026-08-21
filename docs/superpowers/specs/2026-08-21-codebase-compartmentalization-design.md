# Codebase Compartmentalization Design

## Purpose

Trafficlab's source tree currently mixes shared foundations, pipeline stages,
Docker capture mechanics, and cross-stage orchestration at one package level.
The test tree correctly uses test scope as its first axis, but most scopes do
not use subsystem ownership as their second axis. The result obscures which
code belongs to preflight, capture, genetic fitting, traffic generation, or
traffic comparison.

This change reorganizes source and tests without changing scientific behavior,
artifact schemas, CLI commands, configuration, seeds, execution order, or
failure semantics.

## Chosen structure

Use stage packages plus a deliberately small shared package:

```text
src/trafficlab/
  common/
  preflight/
  capture/
  fitting/
    genetic/
  generation/
    models/
  comparison/
    similarity/
  artifacts.py
  artifact_schemas.py
  study_evidence.py
  run.py
  cli.py
```

The five stage packages match the documented pipeline. `common` contains only
concepts used by multiple stages. Cross-stage coordinators and artifact
catalogues remain at the package root because assigning them to one stage would
misstate ownership.

Two alternatives were rejected:

- Keeping the flat source tree and adding naming prefixes would leave ownership
  implicit and would not give tests a reusable subsystem taxonomy.
- Introducing controller/domain/adapter layers would add abstraction that the
  one-process research prototype does not need and would separate closely
  related code by technical role instead of scientific workflow.

## Source ownership

`trafficlab.common` owns configuration models and I/O, structured errors,
content compatibility, the canonical trace, the Scapy PCAPNG boundary,
scientific schema compatibility, and shared statistical helpers:

```text
common/config.py
common/config_io.py
common/errors.py
common/compatibility.py
common/trace.py
common/scapy_io.py
common/scientific_schema.py
common/statistics.py
```

`trafficlab.preflight` owns the read-only local and Docker prerequisite stage.
Its existing implementation moves to `preflight/stage.py`.

`trafficlab.capture` owns the capture stage and every Docker-specific runtime
mechanism used to realize it:

```text
capture/stage.py
capture/policy.py
capture/validation.py
capture/cleanup.py
capture/compose.py
capture/docker_cli.py
```

`trafficlab.fitting` owns the fit stage and the complete genetic search:

```text
fitting/stage.py
fitting/genetic/
```

`trafficlab.generation` owns the generate stage and traffic-model families.
Fitting depends on these model-family contracts because it searches and
evaluates generator parameters:

```text
generation/stage.py
generation/models/
```

`trafficlab.comparison` owns the compare stage and all similarity methods:

```text
comparison/stage.py
comparison/similarity/
```

`artifacts.py`, `artifact_schemas.py`, `study_evidence.py`, `run.py`, and
`cli.py` remain cross-stage code. They may depend on subsystem APIs; subsystem
code may use the shared artifact publication helpers already provided by
`artifacts.py`. This refactor does not introduce interfaces or dependency
injection solely to force a theoretical acyclic package diagram.

Package `__init__.py` files document ownership but do not re-export the former
flat module APIs. All repository callers migrate atomically to the owning
module. No compatibility modules, deprecated aliases, or parallel import paths
remain.

## Test ownership

Test scope remains the first directory axis. Subsystem becomes the second:

```text
tests/unit/{common,preflight,capture,fitting,generation,comparison,pipeline,validation,tooling}/
tests/integration/{preflight,capture,fitting,generation,comparison,pipeline,validation}/
tests/docker/{capture,pipeline}/
tests/internet/capture/
tests/property/{common,fitting,generation,comparison}/
tests/scientific/{fitting,generation}/
```

Existing support code and deterministic fixtures remain in `tests/support` and
`tests/fixtures`; those are reusable test infrastructure, not behavior scopes.
Tests that cover several production stages belong to `pipeline`. Tests of
validation-study collection and audit belong to `validation`. Repository
generators, benchmarks, and evidence tooling belong to `tooling`.

File names stay descriptive within their new directory. A structural unit test
binds the expected production compartments, rejects the removed flat module and
top-level `genetic`, `models`, and `similarity` paths, and requires every test
scope's Python test files to live under an approved subsystem directory.

## Migration and behavior preservation

The migration is one coherent import break:

1. Add the structural test and observe it fail on the flat layout.
2. Move production modules with Git history retained.
3. Rewrite production, test, script, and authoritative architecture imports.
4. Move tests into their subsystem directories.
5. Run collection before behavior tests so import mistakes fail early.
6. Run focused subsystem tests, strict static checks, ordinary tests, coverage,
   deterministic generators and audits, then available external validation.

No artifact is regenerated merely because a Python import path changes.
Checked-in artifacts are regenerated only if an existing deterministic checker
shows that their authoritative bytes embed a moved import path.

## Verification

Acceptance requires:

- no removed source module or old internal import remains outside historical
  design/plan documents;
- test collection reports the same behavior inventory after moves;
- Ruff format and lint, strict Pyright, the ordinary offline suite, and
  branch-aware coverage at or above 90% pass;
- deterministic fixture/schema/benchmark/probe check commands pass;
- available Docker and Internet validation pass with bounded cleanup;
- an independent review reports no Critical or Important finding; and
- the working tree is clean with coherent local commits retained.
