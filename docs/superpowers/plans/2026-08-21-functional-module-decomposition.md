# Functional Module Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split Trafficlab's oversized production, test, support, probe, and
validation-study tooling modules into small functional owners without changing
observable behavior.

**Architecture:** Follow the precomputed symbol routes in the design. Immutable
contracts sit below operations and operations below stage coordinators; tests
mirror those owners. Production modules use a 600-line backstop, validation
tooling 800 lines, and tests/support/probes 1,000 lines.

**Tech Stack:** CPython 3.12.3, uv, pytest, Ruff, strict Pyright, Pydantic 2,
NumPy, SciPy, Git, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-21-functional-module-decomposition-design.md`

## Global Constraints

- Preserve CLI commands, scientific equations, artifact bytes and schemas,
  configuration, deterministic seeds/draw order, failure authority, timeouts,
  cleanup, and the one-process architecture.
- Do not add dependencies, services, compatibility modules, broad re-export
  facades, generic `utils.py`, or technical-layer directories.
- Move each current symbol exactly once to the functional owner named by the
  spec; do not copy implementations between old and new paths.
- Preserve the 3,833 starting tests, every parameter case and marker, and add
  direct structural tests. A path move may change only the file-qualified part
  of a pytest node ID.
- Use `apply_patch` for hand-authored changes and `git mv` or a reviewed
  deterministic split for bulk mechanical moves.
- Keep production modules at most 600 lines, validation-study implementation
  modules at most 800 lines, and tests/support/probes at most 1,000 lines.
- Run focused RED/GREEN tests, Ruff, strict Pyright, and an independent review
  before each task commit.
- Run every canonical release gate, deterministic checker, detached accepted
  study audit, and available external gate before completion.

---

### [TASK-1-13cce9a6] Split artifact persistence by artifact owner

**Files:**

- Remove: `src/trafficlab/artifacts.py`
- Create: `src/trafficlab/artifacts/{__init__,io,best_model,generated,run_directory,capture}.py`
- Split: `tests/unit/pipeline/test_artifacts.py` into
  `tests/unit/pipeline/artifacts/test_{io,best_model,generated,run_directory,capture}.py`
- Modify: production, scripts, tests, and schema registry imports of artifact
  functions and records
- Modify: `tests/unit/pipeline/test_source_layout.py`

**Interfaces:**

- Produces `atomic_replace`, `fsync_published_artifact`, and `append_run_log`
  from `artifacts.io`; `BestModelPublication`, `validate_existing_best_model`,
  and `publish_best_model` from `artifacts.best_model`; `GeneratedPublication`
  and `publish_generated_pcapng` from `artifacts.generated`;
  `create_run_directory` from `artifacts.run_directory`; and the capture-pair
  identity/publication API from `artifacts.capture`.

- [x] **[STEP-1-789ca4ae] Snapshot artifact tests and write the failing ownership contract**

  Save normalized node suffixes from the current artifact test, then extend the
  source-layout test to require the six new modules and reject
  `src/trafficlab/artifacts.py`:

  ```bash
  uv run --locked pytest --collect-only -q tests/unit/pipeline/test_artifacts.py \
    | sed -n '/^tests\//p' | sed 's#^[^:]*::##' | sort \
    > /tmp/trafficlab-task1-artifact-nodes.txt
  ```

- [x] **[STEP-2-5c993e9c] Verify the artifact ownership contract is RED**

  Run the source-layout node under the canonical bounded Focused command.
  Expected: failure naming the absent `trafficlab/artifacts/` owner modules and
  still-present `artifacts.py`.

- [x] **[STEP-3-8cb87586] Extract artifact implementations by the spec's symbol ranges**

  Create the package, move each complete symbol body to its exact owner, import
  shared filesystem primitives only from `artifacts.io`, update every caller to
  the direct owner, and delete the old module. `__init__.py` contains only:

  ```python
  """Durable experiment artifact persistence by artifact kind."""
  ```

- [x] **[STEP-4-a3285153] Split artifact tests without changing their cases**

  Move fixtures/helpers beside the one artifact kind that consumes them; put
  genuinely shared builders in `tests/support/artifacts.py`. Collect the new
  directory, normalize node suffixes, and require an empty diff against
  `/tmp/trafficlab-task1-artifact-nodes.txt`.

- [x] **[STEP-5-cf3fb23f] Verify artifact behavior and static boundaries**

  Run the new artifact test directory, capture integration tests, fitting,
  generation, comparison, and pipeline integration tests; then Ruff and strict
  Pyright. Run `generate_artifact_schemas.py --check` and every fixture checker
  that imports artifact publishers.

- [x] **[STEP-6-d08bceaf] Review and commit artifact ownership**

  Request independent review, resolve every Critical/Important finding, repeat
  Step 5, and commit:

  ```bash
  git commit -m "refactor: split artifact persistence"
  ```

### [TASK-2-a392ec57] Split comparison and full-pipeline responsibilities

**Files:**

- Replace: `src/trafficlab/comparison/stage.py` with
  `comparison/{diagnostics,schema,codec,metrics,publication,stage}.py`
- Replace: `src/trafficlab/run.py` with
  `pipeline/{__init__,types,validation,stage}.py`
- Split: `tests/unit/comparison/test_comparison.py` into direct comparison
  owners; split `tests/unit/pipeline/test_run.py` into stage-result, final
  validation, and coordinator tests
- Modify: CLI, scripts, artifact schema registry, study tooling, and all imports

**Interfaces:**

- Produces comparison diagnostic/result contracts, canonical codec, metric
  runner, publisher, and stage from their named modules.
- Produces `RunResult`/`RunDependencies` from `pipeline.types`, artifact
  validators from `pipeline.validation`, and `run_experiment` from
  `pipeline.stage`.

- [x] **[STEP-7-735d4010] Snapshot comparison/pipeline nodes and write RED path tests**

  Record normalized node suffixes for both current test files. Extend the
  source-layout contract with every new comparison/pipeline file and absence of
  `run.py`; run it and require RED on the old layout.

- [x] **[STEP-8-66b12c71] Extract comparison contracts, codec, metrics, publication, and stage**

  Move the exact symbol groups from the spec, order dependencies as
  `diagnostics -> schema -> codec/metrics -> publication -> stage`, update all
  callers, and leave no moved body in `comparison/stage.py`.

- [x] **[STEP-9-a5aa6137] Extract pipeline types, validation, and coordinator**

  Move `RunResult`/`RunDependencies`, every immediate/final validator, and the
  coordinator into the three direct owners. Update CLI lazy-import guards to
  classify `trafficlab.pipeline.stage`, not the deleted `trafficlab.run`.

- [x] **[STEP-10-d3c39fd2] Split tests and prove inventory equivalence**

  Split tests by the paths in the spec, migrate monkeypatch strings to direct
  owners, and require normalized comparison and pipeline node inventories to
  equal their snapshots exactly.

- [x] **[STEP-11-9a38fe74] Verify comparison and pipeline behavior**

  Run new comparison/pipeline unit directories, comparison and pipeline
  integration directories, CLI tests, schema generation check, Ruff, and strict
  Pyright. Require no old comparison-stage private import outside historical
  docs.

- [x] **[STEP-12-fd755bc7] Review and commit comparison/pipeline ownership**

  Resolve independent review findings, repeat Step 11, and commit:

  ```bash
  git commit -m "refactor: split comparison pipeline"
  ```

### [TASK-3-7f40eec8] Split Docker image, process, and Compose operations

**Files:**

- Remove: `src/trafficlab/capture/docker_cli.py`
- Rename: `src/trafficlab/capture/compose.py` to `capture/topology.py`
- Create: `src/trafficlab/capture/docker/{__init__,types,image,process,compose}.py`
- Split: `tests/unit/capture/test_docker_cli.py` into
  `tests/unit/capture/docker/test_{image,process,compose}.py`
- Rename/update: `tests/unit/capture/test_compose.py` to `test_topology.py`

**Interfaces:**

- Produces typed command/service records, image authority, subprocess boundary,
  and `DockerCompose` from separate direct owners.

- [x] **[STEP-13-5978fe13] Snapshot Docker tests and write the RED owner contract**

  Capture normalized nodes for both Docker adapter and topology tests; require
  the new package/files and reject `docker_cli.py`/`capture/compose.py`.

- [x] **[STEP-14-039d4482] Extract Docker types and image authority**

  Move command/service protocols to `docker.types`; move platform, lock,
  Dockerfile, build argv, and image-inspect symbols to `docker.image`. Keep
  duplicate-key parsing local to the image codec when no Compose caller uses it.

- [x] **[STEP-15-4f02bc71] Extract subprocess and Compose operations**

  Move the subprocess implementation to `docker.process`, Compose validation
  and `DockerCompose` to `docker.compose`, rename the pure renderer to
  `topology.py`, and update injected-boundary types without a cycle.

- [x] **[STEP-16-155728f1] Split Docker tests and update lazy import guards**

  Split by image/process/Compose behavior, rename topology tests, update dynamic
  import guards and monkeypatch strings, and prove normalized node equivalence.

- [x] **[STEP-17-c75cb637] Verify Docker adapter boundaries**

  Run Docker adapter/topology/cleanup/preflight/capture unit tests, all
  config-only and compare lazy-import integration cases, capture Docker tests
  when available, Ruff, and strict Pyright.

- [x] **[STEP-18-cde0c548] Review and commit Docker ownership**

  Resolve independent review findings and commit:

  ```bash
  git commit -m "refactor: split Docker operations"
  ```

### [TASK-4-75ec88b6] Split capture lineage, lifecycle, failures, and stage

**Files:**

- Create: `src/trafficlab/capture/{lineage,lifecycle,failures}.py`
- Reduce: `src/trafficlab/capture/stage.py` to public records and coordination
- Split: `tests/unit/capture/test_capture.py` into
  `test_{lineage,lifecycle,failures,stage}.py`

**Interfaces:**

- Produces lineage/reuse validation, lifecycle operations, failure translation,
  and the unchanged public capture stage from direct owners.

- [x] **[STEP-19-cf82b2a6] Snapshot capture nodes and write RED owner tests**

  Record normalized test nodes and require the three new source files before
  any symbol move. Expected RED names all absent owners.

- [x] **[STEP-20-864e3db6] Extract lineage and lifecycle symbols**

  Move mounted-input/snapshot/lineage/reuse symbols to `lineage.py` and
  readiness/workload/flush/interruption symbols to `lifecycle.py`. Pass
  required callbacks/records explicitly; neither module imports the stage.

- [x] **[STEP-21-3f3568ec] Extract failure translation and reduce stage**

  Move canonical outcome/log translation to `failures.py`; keep only
  `CaptureDocker`, `CaptureResult`, `capture_prepared_experiment`, and
  `capture_experiment` plus their direct orchestration helpers in `stage.py`.

- [x] **[STEP-22-5d31fcbc] Split capture tests by functional owner**

  Move exact existing tests and owner-specific fakes, extract shared capture
  builders to `tests/support/capture.py`, and prove normalized node equivalence.

- [x] **[STEP-23-e8945a87] Verify capture behavior and coverage**

  Run all capture unit/integration tests with branch coverage for the four new
  modules, capture failure/public matrix tests, Ruff, and strict Pyright.

- [x] **[STEP-24-1d4fef66] Review and commit capture ownership**

  Resolve independent review findings and commit:

  ```bash
  git commit -m "refactor: split capture lifecycle"
  ```

### [TASK-5-a9ae9e86] Split local, Docker, probe, and stage preflight

**Files:**

- Create: `src/trafficlab/preflight/{types,local,docker,probe}.py`
- Reduce: `src/trafficlab/preflight/stage.py`
- Modify: preflight/capture/CLI imports and preflight tests

**Interfaces:**

- Produces immutable preflight contracts, host-only checks, Docker checks,
  disposable probe operations, and the unchanged public stage API.

- [x] **[STEP-25-e1ce1a47] Snapshot preflight nodes and write RED owner tests**

  Record normalized preflight unit/integration nodes and require the four new
  modules in the source-layout contract.

- [x] **[STEP-26-cf8eed13] Extract types and local checks**

  Move all protocols/records to `types.py`; move writable, mount,
  run-directory, disk-space checks, and `check_local` to `local.py`.

- [x] **[STEP-27-fe58f623] Extract Docker probe and check orchestration**

  Move disposable probe mechanics to `probe.py`, Docker prerequisite decisions
  to `docker.py`, and retain prepare/open/run coordination in `stage.py`.

- [x] **[STEP-28-13bb0d45] Update preflight tests and prove inventory equivalence**

  Rehome direct tests to `test_types.py`, `test_local.py`, `test_docker.py`,
  `test_probe.py`, and `test_stage.py` when ownership is clearer; preserve every
  test/parameter/marker and update lazy import targets.

- [x] **[STEP-29-8e26520a] Verify preflight behavior**

  Run preflight unit/integration, config-only CLI, capture reuse, Docker probe
  tests, Ruff, strict Pyright, and direct branch coverage of each new module.

- [x] **[STEP-30-62ab582f] Review and commit preflight ownership**

  Resolve independent review findings and commit:

  ```bash
  git commit -m "refactor: split preflight checks"
  ```

### [TASK-6-65f4d70c] Split checkpoint schema, state, codecs, and tests

**Files:**

- Replace: `src/trafficlab/fitting/genetic/checkpoint.py` with
  `checkpoint/{__init__,schema,compatibility,state,codec,history}.py`
- Split: `tests/unit/fitting/genetic/test_checkpoint.py` into matching owner
  tests under `tests/unit/fitting/genetic/checkpoint/`
- Split: `tests/unit/fitting/genetic/test_operators.py` into selection,
  crossover, mutation, and reproduction tests

**Interfaces:**

- Produces the same checkpoint wire records and public checkpoint/history API,
  each from the direct functional module named in the spec.

- [x] **[STEP-31-aac487cf] Snapshot checkpoint/operator nodes and write RED path tests**

  Record both normalized inventories and require the checkpoint package modules
  while rejecting the old single file.

- [x] **[STEP-32-0f8b590d] Extract checkpoint schema and compatibility/RNG**

  Move strict wire models to `schema.py`; move corruption/scalar/JSON,
  family/genetic compatibility, and RNG state codec to `compatibility.py`.

- [x] **[STEP-33-197e4a93] Extract state semantics and JSON/CSV codecs**

  Move candidate/history/state invariants to `state.py`, canonical checkpoint
  JSON to `codec.py`, and history CSV plus generation pair publication to
  `history.py`. Keep `__init__.py` limited to the documented public boundary.

- [x] **[STEP-34-562a5065] Split checkpoint and operator tests**

  Move existing tests by direct owner, move shared immutable records to
  `tests/support/checkpoint.py`, and prove both normalized inventories are
  unchanged.

- [x] **[STEP-35-a77c6cd4] Verify checkpoint behavior and generated schemas**

  Run checkpoint/operator/fitting integration tests with branch coverage,
  regenerate/check public schemas only through the schema generator, run fit
  fixture checks, Ruff, and strict Pyright.

- [x] **[STEP-36-a4ea95af] Review and commit checkpoint ownership**

  Resolve independent review findings and commit:

  ```bash
  git commit -m "refactor: split checkpoint codecs"
  ```

### [TASK-7-3d671c66] Split Markov Renewal and fitted-model registry

**Files:**

- Replace: `generation/models/markov_renewal.py` with
  `markov_renewal/{__init__,parameters,sampling,model,generation,family}.py`
- Create: `generation/models/{fitted_schema,fitted_model}.py`
- Reduce: `generation/models/registry.py`
- Split: Markov Renewal unit tests into matching owners

**Interfaces:**

- Produces unchanged model equations, fit/generation draw order, ModelFamily
  adapter, registry, and best-model artifact API.

- [x] **[STEP-37-752187b4] Snapshot model nodes and write RED owner tests**

  Record Markov/registry test inventories; require every new source module and
  reject the old Markov module path.

- [x] **[STEP-38-74562d18] Extract Markov parameters, sampling, model, and generation**

  Move exact spec symbol groups, keep stochastic draw validators with sampling,
  and ensure generation depends on the fitted model without the family adapter.

- [x] **[STEP-39-275896ea] Extract family adapter and fitted-model schema/codec**

  Move loading/document helpers and `MarkovRenewalFamily` to `family.py`; move
  wire payloads to `fitted_schema.py`; move `BestModel`, validation,
  construction, runtime conversion, load/render to `fitted_model.py`; reduce
  registry to closed family/bounds ownership.

- [x] **[STEP-40-ddc1b58e] Split Markov tests and update registry callers**

  Rehome tests by parameters/sampling/model/generation/family, update all best
  model imports and monkeypatch paths, and prove normalized test inventories.

- [x] **[STEP-41-69bd2ed3] Verify scientific behavior and evidence**

  Run model/registry/property/scientific/fitting/generation tests, deterministic
  model/fit fixtures, reduction and benchmark checks, Ruff, strict Pyright, and
  direct branch coverage.

- [x] **[STEP-42-5cae9cdf] Review and commit model ownership**

  Resolve independent review findings and commit:

  ```bash
  git commit -m "refactor: split fitted traffic models"
  ```

### [TASK-8-635a560e] Split accepted-study schemas and publication

**Files:**

- Replace: `src/trafficlab/study_evidence.py` with
  `study_evidence/{__init__,protocol,report,publication}.py`
- Modify: artifact schema registry, validation tooling, tests, and docs imports

**Interfaces:**

- Produces protocol/identity schemas, report schemas, and audited publication
  from separate direct owners without changing wire bytes.

- [x] **[STEP-43-da9b25a0] Snapshot study-evidence nodes and write RED owner tests**

  Record normalized study-evidence tests and require the three implementation
  modules while rejecting `study_evidence.py`.

- [x] **[STEP-44-041f0c6a] Extract protocol and report schema symbols**

  Move strict aliases and environment/prerequisite/lineage/manifest/lifecycle/
  protocol records to `protocol.py`; move score/statistics/report records to
  `report.py`; preserve exact Pydantic validation order.

- [x] **[STEP-45-78e5089b] Extract accepted-bundle publication**

  Move publication errors and filesystem operations to `publication.py`, import
  only the audit callable contract, and update all callers to direct owners.

- [x] **[STEP-46-f10223a8] Rehome direct tests and verify inventory**

  Split schema/report/publication tests when it improves ownership, reuse the
  existing accepted-bundle builders, and prove normalized node equivalence.

- [x] **[STEP-47-a8a5d431] Verify study schemas and immutable evidence**

  Run study evidence/audit/schema tests, artifact schema generator/check,
  historical byte-identity tests, Ruff, strict Pyright, and direct coverage.

- [x] **[STEP-48-a75d7b8e] Review and commit study-evidence ownership**

  Resolve independent review findings and commit:

  ```bash
  git commit -m "refactor: split study evidence"
  ```

### [TASK-9-0391c6e7] Decompose validation-study tooling and its large suites

**Files:**

- Create: `scripts/validation_study/` tree exactly as specified
- Reduce: `scripts/{run_validation_study,audit_validation_study,generate_validation_study_fixture}.py`
  to thin wrappers
- Split: `tests/support/validation_study.py` into a support package
- Split: oversized audit/protocol/orchestration/prerequisite tests into the
  owner directories from the spec
- Split: scientific `mmpp_likelihood.py` and `pymoo_optimizer.py` into packages

**Interfaces:**

- Produces thin executable wrappers importing only `main`; typed functional
  tooling modules consumed directly by tests; unchanged retained evidence
  codecs, collection, audit, probe, and fixture behavior.

- [x] **[STEP-49-b9719f8b] Snapshot validation/probe nodes and write RED tooling tests**

  Save normalized inventories for all validation-study and scientific-probe
  tests. Extend repository layout tests to require the tooling package tree and
  wrappers of at most 40 lines; run RED against the monoliths.

- [x] **[STEP-50-a8e7a97a] Extract common, workload, transfer, record, and evidence modules**

  Move strict JSON primitives, configs, HTTP transfer archive logic, immutable
  records, persisted-run loading, trace summaries, and primary extraction to
  the exact spec owners. Update callers before deleting each original body.

- [x] **[STEP-51-f9d0c99c] Extract prerequisites, results, rotation, candidate, and collection**

  Build the named subpackages, moving each complete symbol group once. Enforce
  dependency direction from codecs/records to operations to collection; retain
  historic schema-one codecs with prerequisite codec ownership.

- [x] **[STEP-52-6b0904f7] Extract audit, fixture, CLI, support, and probe packages**

  Split audit by environment/artifacts/science/lifecycle, fixture generation to
  `fixture.py`, CLI dispatch to `cli.py`, validation test support to its five
  owners, and MMPP/pymoo probes to their schema/math/adapter/evidence owners.

- [x] **[STEP-53-9b8ff136] Split validation tests and prove exact inventory**

  Rehome every existing test by audit/protocol/orchestration/prerequisite owner,
  update imports to direct tooling/support modules, and require normalized node
  inventories, markers, and the 3,833-test baseline to remain complete.

- [x] **[STEP-54-3a52ceec] Verify tooling, probes, fixture, and audit behavior**

  Run all validation unit/integration/scientific tests, standalone wrapper
  tests, every validation/probe/fixture checker, the detached accepted-study
  audit, Ruff, strict Pyright, and module-size checks.

- [x] **[STEP-55-6569817f] Review and commit validation tooling ownership**

  Resolve independent review findings and commit:

  ```bash
  git commit -m "refactor: decompose validation tooling"
  ```

### [TASK-10-00e380c8] Split remaining oversized tests and enforce cohesion backstops

**Files:**

- Split: `tests/unit/fitting/test_fitting.py`
- Split: `tests/integration/generation/test_generate_cli.py`
- Split: `tests/unit/pipeline/test_failure_outcome_public_matrix.py` and create
  `tests/support/failure_matrix/`
- Modify: source/test/repository layout tests with final line backstops
- Modify: `architecture/DEVELOPMENT.md` and `architecture/TESTING.md`

**Interfaces:**

- Produces cohesive remaining tests, normalized node inventory parity, and
  documented/enforced module-size regression backstops.

- [x] **[STEP-56-f10c116c] Snapshot remaining nodes and write final RED size tests**

  Record normalized inventories. Add literal tests that enumerate every Python
  file above 600/800/1,000 lines for production/tooling/tests respectively and
  require empty offender sets. Expected RED lists only the remaining files in
  this task or missed earlier work.

- [x] **[STEP-57-90f4a613] Split fitting and generation integration tests**

  Rehome fit-input/reuse/publication/stage tests and generate CLI/publication/
  failure/reproduction tests; extract shared builders only to typed support
  modules; prove normalized inventory equality.

- [x] **[STEP-58-ece907fc] Extract failure-matrix support and concise boundary tests**

  Move case records, doubles, runners, and oracle helpers to
  `tests/support/failure_matrix/{cases,doubles,runners,oracle}.py`; keep only
  direct matrix/oracle behavior tests under `unit/pipeline/failure_matrix/` and
  preserve every parametrized case.

- [x] **[STEP-59-6e741f84] Document and satisfy final cohesion backstops**

  Add stable development/testing wording that ownership precedes size, update
  layout expectations for every new package, and make all production/tooling/
  test offender sets empty without exclusions.

- [x] **[STEP-60-a17b3590] Verify complete collection and ordinary gates**

  Require at least the 3,833 original tests plus structural additions, then run
  Ruff format/lint, strict Pyright, Fast, Ordinary, branch-aware coverage, and
  all deterministic checkers.

- [x] **[STEP-61-1d94bb9d] Review and commit final test decomposition**

  Resolve independent review findings and commit:

  ```bash
  git commit -m "test: split oversized suites"
  ```

### [TASK-11-d8e8fbd1] Complete release validation and final review

**Files:**

- Modify only gate-proven defects, their direct regression tests, generated
  path-bound evidence, and this plan's checkboxes

**Interfaces:**

- Consumes the complete decomposed repository.
- Produces a clean local branch with reproducible completion evidence.

- [x] **[STEP-62-9d2814e7] Run locked static and complete offline gates**

  Run `uv sync --locked --all-groups`, Python 3.12.3 check, Ruff format/lint,
  strict Pyright, Fast, Ordinary, and four-worker branch-aware Coverage. Require
  all tests pass and total coverage remain at least 90%.

- [x] **[STEP-63-6fbd099e] Run every deterministic and real-program checker**

  Run all fixture, schema, reduction, benchmark, example, probe, module-size,
  and symbol-owner checks. Regenerate only checker-owned path/hash records using
  their real programs, then rerun the complete parent gate.

- [x] **[STEP-64-1a9008c5] Run detached audit and external validation**

  Audit the current accepted study from its recorded detached source checkout,
  then run the combined serial Docker/Internet gate with the accepted
  credential-free endpoint and verify no Trafficlab Docker resource remains.

- [x] **[STEP-65-5a9e6ffa] Obtain independent final review and resolve findings**

  Review the full diff from `720944a` through HEAD for missing/misowned
  symbols, arbitrary fragments, cycles, stale paths, compatibility shims, test
  loss, evidence drift, and verification gaps. Resolve all Critical/Important
  findings and rerun affected parent gates.

- [x] **[STEP-66-5ce5cccf] Commit completion state and hand off cleanly**

  Mark only evidenced checkboxes, commit the completed plan, verify no unchecked
  item or Git/Docker residue, list retained commits, and report exact static,
  test, coverage, deterministic, audit, external, and review evidence.
