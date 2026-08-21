# Functional Module Decomposition Design

## Purpose

Trafficlab's subsystem directories now make broad ownership visible, but many
individual modules still combine several independent responsibilities. The
largest production files contain artifact schemas, validation, codecs,
filesystem publication, orchestration, and failure translation in the same
module. Large test files repeat the same problem by combining unrelated
behavioral fixtures and scenarios.

This refactor makes the next level of ownership explicit without changing CLI
commands, scientific definitions, artifacts, configuration, deterministic draw
order, failure authority, timeouts, cleanup, or the one-process architecture.

## Inventory and decision method

An AST inventory identified every top-level production symbol before any move:

- 962 symbols in 50 Python modules;
- 541 functions, 236 classes, 91 type aliases, and 94 module constants;
- 10 production modules over 600 lines containing 549 symbols;
- 18 test, support, or scientific-probe modules over 1,000 lines; and
- three validation-study scripts over 800 lines, including the 9,236-line
  collection runner.

For every oversized production module, same-module references were mapped
between symbols. The clusters below follow those dependencies and the behavior
named by each symbol. Files already at or below 600 lines remain intact when
their symbols form one cohesive unit; size alone does not justify extraction.

Three approaches were considered:

- Mechanical chunks by line range minimize edits but conceal behavior behind
  names such as `part1` and create arbitrary dependencies.
- Technical layers such as `models`, `services`, and `utils` separate code by
  implementation mechanism rather than Trafficlab's scientific workflow.
- Functional ownership groups symbols that validate, encode, publish, or
  orchestrate one concept and gives each group a direct test owner.

Functional ownership is selected. Physical line limits are regression
backstops after the functional split, not the decomposition algorithm.

## Repository-level boundaries

Production modules under `src/trafficlab` must contain at most 600 physical
lines. Test, support, probe, and script modules should normally contain 500–800
lines and must contain at most 1,000. Package initializers contain ownership
documentation and only genuinely package-level public names; they do not
re-export every moved private symbol as a compatibility layer.

All repository callers migrate atomically to the owning module. The CLI remains
the only stable user-facing Python surface. A module may import lower-level
data contracts and operations, but low-level modules do not import stage
coordinators.

## Production symbol routing

### Artifact persistence

`src/trafficlab/artifacts.py` becomes `trafficlab.artifacts`:

| Current symbols | New owner | Purpose |
| --- | --- | --- |
| Lines 25–183, from `_artifact_error` through `append_run_log` | `artifacts/io.py` | Atomic temporary writes, directory fsync, durable replacement, and run-log append |
| Lines 187–436, from `BestModelPublication` through `publish_best_model` | `artifacts/best_model.py` | Exclusive best-model validation, reuse, publication, and failure evidence |
| Lines 440–693, from `GeneratedPublication` through `publish_generated_pcapng` | `artifacts/generated.py` | Generated-trace/PCAPNG validation, reuse, and publication |
| Lines 696–810, from `_validate_persisted_snapshot` through `create_run_directory` | `artifacts/run_directory.py` | Initial realized configuration and deterministic run-log publication |
| Lines 813–1166, from `_copy_capture_temporary` through `rollback_capture_publication` | `artifacts/capture.py` | Capture-pair identity, recovery, quarantine, publication, diagnostics, and rollback |

The common filesystem primitives in `io.py` are imported by the four artifact
owners. Artifact owners do not import one another.

### Docker capture adapter

`capture/docker_cli.py` becomes `capture/docker/`, and the pure Compose renderer
`capture/compose.py` is renamed `capture/topology.py`:

| Current symbols | New owner | Purpose |
| --- | --- | --- |
| `CommandResult`, `ServiceState`, `ProcessHandle`, `CommandBoundary` | `capture/docker/types.py` | Typed command and service boundaries |
| Capture platform constants; `CaptureImageLockError`; `cold_capture_build_argv`; platform, lock, Dockerfile, and image-inspect functions and records | `capture/docker/image.py` | Reproducible capture-image and daemon-platform authority |
| `_process_error`, `_decode_output`, `_SubprocessHandle`, `SubprocessBoundary`, and finite-time validation | `capture/docker/process.py` | Standard-library subprocess implementation |
| Budget, scope, service, inventory, and config validators plus `DockerCompose` | `capture/docker/compose.py` | Bounded project-scoped Compose operations |

The small Docker types module prevents image, process, and Compose owners from
importing one another merely to share protocols.

### Capture stage

`capture/stage.py` keeps `CaptureDocker`, `CaptureResult`,
`capture_prepared_experiment`, and `capture_experiment`. Its other symbols move
as follows:

| Current symbols | New owner | Purpose |
| --- | --- | --- |
| Snapshot/mounted-input errors and lines 147–405 from environment rendering through input-failure recording | `capture/lineage.py` | Mounted-input identity, capture lineage, snapshot consistency, and reuse compatibility |
| Lines 409–746 from temporary-directory creation through `_flush_capture` | `capture/lifecycle.py` | Readiness, workload observation, deadlines, target termination, and capture flushing |
| Lines 749–977 from `_outcome_error` through `_capture_failure_logs` | `capture/failures.py` | Canonical primary/secondary capture outcomes and diagnostic logs |
| `_interrupt_lifecycle` | `capture/lifecycle.py` | Interruption handling is part of lifecycle control |
| `_validate_prepared_capture` and `_try_reuse_prepared_capture` | `capture/lineage.py` | Reusable prepared-capture validation |

The stage coordinator calls these operations but none imports the stage.

### Preflight

`preflight/stage.py` is split into:

| Current symbols | New owner | Purpose |
| --- | --- | --- |
| Boundary protocols and records through `PreparedExperiment` | `preflight/types.py` | Immutable local/Docker preflight contracts |
| `default_writable`, path helpers, mount/run-directory/free-space checks, and `check_local` | `preflight/local.py` | Host-only configuration realization checks |
| Probe document, readiness/target waits, probe finish, and `_run_probe` | `preflight/probe.py` | Disposable Compose network/capture feature probe |
| Deadline/version/failure/image helpers, `_preflight_failure_outcome`, and `check_docker` | `preflight/docker.py` | Sequential Docker prerequisite decisions |
| Preparation, reopen validation, `open_or_prepare_experiment`, and `run_preflight` | `preflight/stage.py` | Public preflight stage coordination |

### Genetic checkpointing

`fitting/genetic/checkpoint.py` becomes `fitting/genetic/checkpoint/`:

| Current symbols | New owner | Purpose |
| --- | --- | --- |
| Lines 81–408: strict scalar aliases and every Pydantic wire record | `checkpoint/schema.py` | Persisted checkpoint schema |
| Corruption helpers, strict scalar/JSON parsing, family/genetic compatibility validation, and RNG codec through line 732 | `checkpoint/compatibility.py` | Compatibility and PCG64 state authority |
| Gene parsing, weighting, candidate/history/state validation, history progress, and generation summaries through line 1034 | `checkpoint/state.py` | Semantic checkpoint invariants |
| Document conversion, artifact conversion, `render_checkpoint`, `parse_checkpoint`, checkpoint publish/load | `checkpoint/codec.py` | Canonical checkpoint JSON codec |
| Decimal/repr parsing, history CSV codec, generation publication/load | `checkpoint/history.py` | Derived history and two-file generation persistence |

Each module has direct tests. `checkpoint/__init__.py` exposes only the public
records/functions used as the genetic checkpoint boundary.

### Traffic comparison

`comparison/stage.py` becomes:

| Current symbols | New owner | Purpose |
| --- | --- | --- |
| Strict scalar aliases, diagnostic base/types, discriminators, and diagnostic arithmetic through line 388 | `comparison/diagnostics.py` | Four-method diagnostic schema and invariants |
| Method/result identity models, published models, `ComparisonResult`, and operational/published conversion | `comparison/schema.py` | In-memory and persisted comparison result contracts |
| Duplicate/cross-key rejection, parse/render/load, SHA helpers, and similarity-settings identity | `comparison/codec.py` | Canonical JSON and input identity codec |
| `compare_traces` | `comparison/metrics.py` | Execute all four mandatory methods and aggregate them |
| Publication errors, entry identity, reuse, and `_publish_comparison_result` | `comparison/publication.py` | Exclusive similarity artifact publication |
| Failure append and `compare_experiment` | `comparison/stage.py` | Public compare-stage orchestration |

### Markov Renewal family

`generation/models/markov_renewal.py` becomes a package:

| Current symbols | New owner | Purpose |
| --- | --- | --- |
| Quantiles, size bins, bounds, canonical genes, and repair | `markov_renewal/parameters.py` | Model parameter validation and reference-based repair |
| IAT fallback selection, RNG protocol, draw validators, transition/empirical sampling | `markov_renewal/sampling.py` | Exact stochastic sampling primitives |
| `MarkovState`, timing diagnostics, `MarkovRenewalModel`, state encoding/counts, and fitting | `markov_renewal/model.py` | Fitted observable-state model and estimator |
| Model/window validation and `_generate_with_rng` | `markov_renewal/generation.py` | Reliability-bounded trace simulation |
| Loading/document helpers and `MarkovRenewalFamily` | `markov_renewal/family.py` | ModelFamily adapter and fitted payload codec |

### Registry and fitted-model artifact

`generation/models/registry.py` retains only family instances, `REGISTRY`,
`get_family`, family-name validation, coordinate/bounds construction, and
configuration reconstruction. Wire payload types move to
`generation/models/fitted_schema.py`. `BestModel`, runtime payload conversion,
best-model validation/construction, and JSON load/render move to
`generation/models/fitted_model.py`.

The registry knows concrete families. The fitted-model codec consumes the
registry; concrete families do not import the codec.

### Accepted-study evidence

`study_evidence.py` becomes `study_evidence/`:

| Current symbols | New owner | Purpose |
| --- | --- | --- |
| Strict aliases, `validate_study_model`, and environment/prerequisite/lineage/manifest/lifecycle/protocol records through `ValidationStudyProtocol` | `study_evidence/protocol.py` | Accepted-study identity and protocol schemas |
| Score, diagnostic, natural-variation, RNG/bootstrap, summary, report-input, and report records | `study_evidence/report.py` | Accepted scientific report schemas |
| Publication error and filesystem functions through `publish_accepted_bundle` | `study_evidence/publication.py` | Audited exclusive directory publication |

### Full pipeline

`run.py` becomes `pipeline/`. `pipeline/types.py` owns `RunResult` and
`RunDependencies`; `pipeline/validation.py` owns every immediate and final
artifact validator plus `_FinalArtifactError`; `pipeline/stage.py` owns failure
append and `run_experiment`.

## Validation-study tooling

The executable files `scripts/run_validation_study.py`,
`scripts/audit_validation_study.py`, and
`scripts/generate_validation_study_fixture.py` remain thin command wrappers.
Their implementations move under `scripts/validation_study/`:

```text
common.py                    strict JSON/scalar/path primitives
workloads.py                 workload declarations and base configurations
transfer.py                  HTTP transfer parsing and retained archives
records.py                   run/prerequisite/result immutable records
evidence.py                  persisted run loading, trace summaries, extraction
prerequisites/commands.py     command, JUnit, capability, and cleanup mechanics
prerequisites/codec.py        current and historical prerequisite codecs
prerequisites/run.py          prerequisite execution and publication
results/codec.py              run/result validation and canonical result codec
results/reporting.py          natural variation, score summaries, report inputs
results/reproduction.py       reproduction reconstruction and audit
rotation/schema.py            rotation journal/target schema and recovery
rotation/run.py               durable prerequisite rotation transaction
candidate/artifacts.py        candidate files, configs, and training records
candidate/reporting.py        selection, natural variation, controlled weights
candidate/held_out.py         held-out capture/evaluation collection
collection.py                 phase image lifecycle and candidate collection
audit/common.py               audit primitives and trusted input loading
audit/environment.py          source, lock, image, and prerequisite binding
audit/artifacts.py            manifest, lineage, and artifact reconstruction
audit/science.py              model, score, report, and reproduction checks
audit/lifecycle.py            project/image cleanup proof
fixture.py                    deterministic validation-study fixture builder
cli.py                        parser and command dispatch
```

No tooling module may exceed 800 lines. The wrappers may import only a `main`
function; tests import the actual functional owner.

## Test decomposition

Test kind and subsystem remain the first two axes. The following oversized
files split by behavior; shared setup moves to typed support modules rather than
being copied.

| Current file | New behavioral owners |
| --- | --- |
| `unit/pipeline/test_artifacts.py` | `pipeline/artifacts/test_io.py`, `test_best_model.py`, `test_generated.py`, `test_run_directory.py`, `test_capture.py` |
| `unit/capture/test_capture.py` | `test_lineage.py`, `test_lifecycle.py`, `test_failures.py`, `test_stage.py` |
| `unit/fitting/genetic/test_checkpoint.py` | `genetic/checkpoint/test_schema.py`, `test_compatibility.py`, `test_state.py`, `test_codec.py`, `test_history.py` |
| `unit/comparison/test_comparison.py` | `test_diagnostics.py`, `test_schema.py`, `test_codec.py`, `test_metrics.py`, `test_publication.py`, `test_stage.py` |
| `unit/generation/models/test_markov_renewal.py` | `markov_renewal/test_parameters.py`, `test_sampling.py`, `test_model.py`, `test_generation.py`, `test_family.py` |
| `unit/pipeline/test_run.py` | `pipeline/test_stage_results.py`, `test_final_validation.py`, `test_stage.py` |
| `unit/fitting/test_fitting.py` | `fitting/test_input.py`, `test_reuse.py`, `test_publication.py`, `test_stage.py` |
| `integration/generation/test_generate_cli.py` | `test_generate_cli.py`, `test_generate_publication.py`, `test_generate_failures.py`, `test_generate_reproduction.py` |
| `unit/fitting/genetic/test_operators.py` | `test_selection.py`, `test_crossover.py`, `test_mutation.py`, `test_reproduction.py` |
| `unit/pipeline/test_failure_outcome_public_matrix.py` | matrix cases/doubles/runners under `tests/support/failure_matrix/`; concise boundary and oracle tests remain under `unit/pipeline/failure_matrix/` |
| `support/validation_study.py` | `support/validation_study/{constants,builders,repository,runners,artifacts}.py` |
| `scientific/fitting/probes/mmpp_likelihood.py` | `probes/mmpp_likelihood/{schema,likelihood,fit,evidence}.py` |
| `scientific/fitting/probes/pymoo_optimizer.py` | `probes/pymoo_optimizer/{schema,policy,adapter,evidence}.py` |
| `unit/validation/study/test_audit.py` | `audit/test_manifest.py`, `test_environment.py`, `test_artifacts.py`, `test_science.py`, `test_publication.py` |
| `unit/validation/study/test_audit_boundaries.py` | `audit/test_environment_boundaries.py`, `test_lineage_boundaries.py`, `test_worktree_boundaries.py` |
| `unit/validation/study/test_protocol.py` | `protocol/test_schema.py`, `test_run_codec.py`, `test_reporting.py`, `test_reproduction.py` |
| `unit/validation/study/test_orchestration.py` | `orchestration/test_study.py`, `test_reproduction.py`, `test_collection.py`, `test_cli.py` |
| `unit/validation/study/test_prerequisites.py` | `prerequisites/test_attempt.py`, `test_rotation.py`, `test_recovery.py`, `test_cli.py` |

Existing files below the hard limit may be split when a production move gives
them a clearer direct owner, but no test is split merely to distribute lines
evenly.

## Test preservation and direct coverage

Before each move, collect current node IDs, test function names, parametrized
case counts, and markers. After the move, compare normalized test names and
case suffixes using an explicit old-to-new path map. The final inventory must
retain all 3,833 tests present at the start of this refactor plus new structural
tests; no assertion, parameter, marker, or fixture scope is weakened.

The existing source/test layout tests gain line-limit checks. Every extracted
production module receives direct tests for its public behavior and for private
branches already covered by the source file's original tests. Moving a helper
does not justify a compatibility proxy or a duplicate test utility.

## Error handling and compatibility

The work is an internal import break only. Persisted JSON/TOML/CSV/PCAPNG
schemas and CLI behavior do not change. Pydantic-generated schema definition
names and path-bound benchmark/probe evidence are regenerated by their owning
programs when module qualification changes.

Circular imports are prevented by moving immutable contracts below operations
and operations below stage coordinators. Imports inside functions remain only
where they currently enforce lazy Docker/CLI boundaries; they are not used to
hide a new cycle.

## Verification

Acceptance requires:

- every oversized production/test/tooling file is replaced by the functional
  owners above;
- every current top-level production symbol has exactly one owner;
- production modules are at most 600 lines and test/support/probe modules at
  most 1,000 lines;
- validation-study tooling implementation modules are at most 800 lines and
  executable wrappers contain only bootstrap/import and `main` dispatch;
- all 3,833 original tests and all new structural tests collect and pass;
- locked sync, Ruff formatting/lint, strict Pyright, ordinary tests, and at
  least 90% branch-aware coverage pass;
- every deterministic fixture/schema/benchmark/probe checker passes;
- the detached accepted-study audit and available Docker/Internet gates pass;
- each implementation phase and the final result receive independent review
  with no unresolved Critical or Important finding; and
- all coherent commits remain local with a clean working tree.
