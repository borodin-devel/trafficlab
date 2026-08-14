# Trafficlab MVP Roadmap

This is the only implementation roadmap. Work proceeds in order because each
phase produces a usable foundation for the next. Check a task only when its
behavior and listed tests exist in the repository.

## Research fitness reevaluation

The [2026-08-14 assessment](../docs/RESEARCH_FITNESS_ASSESSMENT.md) reopened the
owning requirements for every criterion below Acceptable. A checked historical
implementation item remains checked only when its behavior is still valid under
the amended architecture. Corrected or unverified claims are unchecked. Dated
evidence remains useful history, but it does not satisfy an amended gate.

Phase 1 is Current because it is the earliest reopened owner. Assessment grades
remain unchanged until Phase 8 has fresh evidence and reassesses them against the
unchanged [research fitness criteria](RESEARCH_FITNESS_CRITERIA.md).

Only the 17 criteria currently below Acceptable are reopened:

| Criterion | Name | Current grade | Owning remediation and final gate |
|---|---|---|---|
| 1.5 | Generated-trace correctness | Partial | Phases 4 and 8 |
| 1.7 | End-to-end result consistency | Partial | Phases 6--8 |
| 2.1 | Coverage of scientifically meaningful controls | Partial | Phases 2, 6, and 8 |
| 2.2 | Configuration semantics | Partial | Phases 1, 2, and 8 |
| 2.4 | Effective-configuration fidelity | Partial | Phases 6--8 |
| 2.6 | Portability of experiment definitions | Partial | Phases 1, 3, 7, and 8 |
| 3.3 | Stochastic-generation correctness | Partial | Phases 4 and 8 |
| 3.4 | Model-competition fairness | Partial | Phases 5 and 8 |
| 3.8 | Scientific validation strength | Partial | Phases 2, 4, 5, and 8 |
| 3.9 | Assumption and limitation transparency | Partial | Phases 2, 4, 5, 7, and 8 |
| 4.7 | Adverse-condition behavior and diagnostics | Partial | Phases 3, 6, and 8 |
| 5.2 | Environment reproducibility | Partial | Phases 1, 3, 7, and 8 |
| 5.3 | Input preservation | Poor | Phases 7 and 8 |
| 5.4 | Artifact lineage | Partial | Phases 6--8 |
| 5.6 | Fresh and resumed rerun equivalence | Partial | Phases 5, 6, and 8 |
| 5.7 | Protocol reproducibility | Partial | Phases 7 and 8 |
| 5.8 | Independent reconstruction | Dreadful | Phases 7 and 8 |

| Phase | Primary outcome |
|---|---|
| 1 | `preflight --config-only` |
| 2 | `compare` |
| 3 | full `preflight`, `capture` |
| 4 | `generate` |
| 5 | `fit` |
| 6 | `run` |
| 7 | replacement validation study |
| 8 | minimum research fitness acceptance |

Phases 7 and 8 validate the complete MVP rather than adding commands.

## Phase 1 — Project, configuration, and local preflight (Current)

**Goal:** A developer can install Trafficlab, parse one experiment file, and
obtain the same validated effective configuration from the CLI and Python API
without requiring Docker.

**Amended contracts and evidence:**
[System configuration](SYSTEM.md#portable-and-realized-configuration),
[accepted evidence policy](DEVELOPMENT.md#reproducibility-review-and-accepted-evidence), and
[in-process tests](TESTING.md#in-process-integration-tests).

**Deliverables:**

- [x] Pin CPython 3.12.x with `.python-version` and
  `requires-python = ">=3.12,<3.13"` in `pyproject.toml`.
- [x] Create the uv-managed development environment and commit `uv.lock`; keep
  dependencies and all pytest, coverage, Ruff, and Pyright settings in
  `pyproject.toml`.
- [x] Add the root `.gitignore`, including `.worktrees/`, `.venv/`, tool caches,
  build output, and generated experiment runs while keeping reproducibility
  files tracked.
- [x] Document locked uv commands, Ruff format/lint with line length 120, strict
  Pyright, four-worker deterministic tests, serial Docker/Internet tests,
  pinpointed failure runs, and process-tree memory, swap, and wall-clock limits.
- [x] Create the Python package and `trafficlab` entry point.
- [x] Define focused values for target, capture, generation, genetic, and
  similarity settings.
- [x] Define each enabled family's crossover probability, one mutation
  probability applied independently to its genes, and normalized mutation scale,
  including documented defaults.
- [x] Parse one TOML configuration shape; reject unknown fields and invalid
  bounds, weights, paths, or argv.
- [x] Implement permanent `preflight --config-only` checks for configuration,
  local paths, output capacity, and disk space without contacting Docker.
- [x] Snapshot the effective configuration into a new run directory without
  silently replacing an existing run.
- [x] Provide concise console errors and detailed file logging.
- [ ] Implement the [portable configuration and realized configuration](SYSTEM.md#portable-and-realized-configuration)
  pair, retaining every explicit scientific and workload value while resolving
  only the run directory and declared bind-mount host-source paths.
- [ ] Require all four mandatory similarity method settings in configuration;
  make a zero weight affect only aggregate contribution, never execution,
  validation, diagnostics, or the fixed result shape.
- [ ] Track an [accepted evidence bundle](SYSTEM.md#published-study-evidence) only
  under `examples/validation_study/evidence/<study-id>/`, while ordinary, failed,
  and scratch runs remain ignored and unaudited evidence cannot be committed.

**Tests:**

- [x] From a clean clone, run `uv sync --locked --all-groups` and verify
  `uv lock --check` makes no changes.
- [x] Run Ruff format checking and linting, strict Pyright, the parallel fast
  test scope, and a serial pinpointed pytest node ID through `uv run --locked`;
  run every pytest command inside the documented process-tree resource guard.
- [x] Prove with controlled child processes that the test guard terminates the
  complete process tree at both its hard memory limit and wall-clock limit and
  leaves no descendant running.
- [x] Unit-test valid and invalid TOML, operator defaults and independent
  overrides, probability/scale/bound relationships, unknown or disabled-family
  operator settings, duplicate-attempt counts, normalized weights, argv arrays,
  mounts, and deterministic configuration snapshots.
- [x] CLI integration-test identical effective configuration and errors from
  `preflight --config-only` and the injected Python API, with no Docker call.
- [ ] Round-trip portable and realized configurations through the CLI and Python
  API, proving exact non-path fidelity and relocation-only path differences.
- [ ] Round-trip one-hot and mixed method weights, including zero-weight methods,
  and require all four settings and diagnostics to remain present.
- [ ] Test the narrow accepted evidence bundle tracking exception and reject publication
  until the retained bundle passes its bounded offline audit.

**Done when:** a clean clone passes the locked sync, formatting, linting, type,
bounded parallel-test, bounded pinpointed-test, and process-tree containment
checks, and `trafficlab preflight fixture.toml --config-only` succeeds without
contacting Docker. The portable configuration and realized configuration are
faithful after relocation, all four method settings survive zero-weight round
trips, and only an audited accepted evidence bundle can enter the checked path.

## Phase 2 — PCAPNG, canonical trace, and similarity

**Goal:** Checked-in reference and generated PCAPNG fixtures can be compared
through four independently verified, interpretable component scores.

**Amended contracts and evidence:**
[Aggregate fitness](similarity_methods/README.md#aggregate-fitness) and the
[bounded scientific-validation matrix](TESTING.md#bounded-scientific-validation-matrix).

**Deliverables:**

- [x] Parse Ethernet PCAPNG plus strict `capture.json` into ordered `(timestamp,
  direction, frame_length)` events and render generated events back to valid
  PCAPNG using the target and deterministic peer MACs.
- [x] Normalize the reference, derive `W = t_n - t_1`, include both endpoints,
  shift the generated trace, and crop generated events after `W`.
- [x] Classify a frame as outbound when its source MAC equals the target MAC and
  inbound otherwise; reject missing metadata, unsupported link types, malformed
  frames, and any generated direction outside those two values.
- [x] Implement frame-size KS and IAT KS with exact merged ECDF scans.
- [x] Implement the documented ACF estimator, lag/feature discrepancy, and
  constant-series convention.
- [x] Implement multiscale packet/byte cells over derived `W`; retain direction
  separation, normalized L1 discrepancies, weights, and
  \(2\sum_h B_h\le C_{max}\) cell cap.
- [x] Aggregate configured method weights while retaining every diagnostic.
- [x] Implement `trafficlab compare` over the shared parser and four methods.
- [ ] Enforce the [mandatory similarity method contract](similarity_methods/README.md#aggregate-fitness): every
  method executes and retains diagnostics at every weight, and zero weight changes
  only its aggregate contribution.

**Tests:**

- [x] Unit-test every published hand calculation, range, precondition, and edge
  case.
- [x] Round-trip outbound and inbound fixture events through `capture.json`,
  PCAPNG, and canonical trace values.
- [x] Test reference normalization, both window endpoints, generated cropping,
  invalid reference windows, and the same `W` in all four diagnostics.
- [x] Reverse only fixture directions and require multiscale discrepancy `1` for
  a wholly outbound versus wholly inbound one-bin trace.
- [x] Integration-test all four methods against one reference/generated fixture
  pair and validate `similarity.json`.
- [x] Use only checked-in fixtures; no Phase 2 test starts or requires Docker.
- [ ] Directly test one-hot, mixed, and zero-weight aggregates, including
  execution, diagnostics, input validation, and failure propagation for each
  zero-weight mandatory similarity method.

**Done when:** `trafficlab compare` returns reproducible component and aggregate
scores for checked-in fixture PCAPNG files, and every equation has a hand-checked
test. All four mandatory methods execute with a fixed result shape for every
valid weight vector, including zero weights.

## Phase 3 — Docker preflight and reference capture

**Goal:** A configured containerized program can access the Internet and produce
a validated reference PCAPNG without Trafficlab changing host networking.

**Amended contracts and evidence:**
[Capture reproducibility](CAPTURE.md#reproducible-capture-environment),
[environment compatibility](DEVELOPMENT.md#reproducibility-review-and-accepted-evidence), and
[diagnostic evidence](TESTING.md#canonical-adverse-condition-diagnostics).

**Deliverables:**

- [x] Add the target/capture Compose template and capture image choice.
- [x] Implement plain/full `trafficlab preflight` for Docker Engine, Compose,
  images, Docker-facing mounts, DNS, and network readiness.
- [x] Implement `trafficlab capture` using the full preflight and the lifecycle
  below.
- [x] Capture only non-promiscuous target `eth0`, discover and validate its MAC,
  and atomically publish strict `capture.json` before `reference.pcapng`.
- [x] Start capture first so it owns `eth0`, wait for readiness, then start target
  with `network_mode: service:capture`.
- [x] Run target argv directly as its service command under `init: true`; use the
  target container status to close the workload window.
- [x] After readiness, use one event arbiter with fixed priority: user
  interruption, natural target stop, unexpected capture stop, stage-specific
  timeout, then total-run timeout.
- [x] Make target kill the next orchestration action after an unexpected capture
  exit instead of waiting for the workload timeout.
- [x] Preserve natural target errors as primary and timeout, capture failure, or
  interruption over their induced target status; record secondary detail in
  `run.log`.
- [x] After natural target success, make flush or validation failure primary;
  make cleanup failure primary only if the run otherwise succeeded.
- [x] On timeout, capture failure, or interruption, kill the complete target
  container. Send capture `SIGINT` and wait for bounded flush only while capture
  is still alive, then validate any diagnostic artifact.
- [x] Use the Phase 2 parser to inspect captured addresses, protocols, packet
  counts, and outbound/inbound directions.
- [x] Enforce readiness, workload, and flush timeouts inside the total-run
  deadline. Give parsing and validation the same monotonic deadline and check it
  before work and after every frame.
- [x] Make cleanup unconditional, idempotent, project-scoped, bounded, and
  visible on failure using the remaining total-run budget. A zero budget makes
  no Docker command and reports the last-known inventory as possibly remaining;
  expiry kills the local CLI and permits no later Docker query.
- [ ] Make the
  [capture environment reproducible](CAPTURE.md#reproducible-capture-environment) with an exact base
  digest, dated Debian snapshot, exact direct-package versions, and a checked
  `docker/capture/image-lock.json` expected content identity.
- [ ] Record target and capture references and resolved content identities, and
  reject incompatible architecture, image, capture-tool, mount, or mounted-input
  identities before capture or publication.
- [ ] Emit the canonical failure outcome fields for every capture boundary,
  including affected evidence, evidence state, corrective action, authority,
  optional exact process status, and possibly remaining cleanup inventory.

**Tests:**

- [x] Docker integration-test known TCP and UDP traffic through a controlled
  endpoint and assert captured addresses, protocols, packet counts, and
  directions through the Phase 2 parser.
- [x] Require controlled outbound, inbound unicast, and inbound broadcast frames
  to receive the expected source-MAC direction classifications.
- [x] Propagate exact natural nonzero target status, retain only diagnostic
  capture, and publish no reusable reference pair after target failure.
- [x] Unit-test every simultaneous pair in the five-event priority and the
  target-stop, capture-stop, and total-timeout triple with a stub event source.
- [x] Use a fake monotonic clock and multi-frame PCAPNG to prove parsing and
  validation abort before accepting the next frame after their deadline.
- [x] Exercise normal and timed-out background children, readiness failure,
  interruption, bounded flush, and malformed output; assert no project-labelled
  Docker resources remain.
- [x] Make target kill the next orchestration command after unexpected capture
  exit, and stop a long-running target within five seconds while its workload
  timeout is much longer; compare natural versus induced target status under
  every primary failure cause.
- [x] Make a capture fixture that ignores `SIGINT` reach flush timeout and reject
  its output. Prove live capture gets bounded signalling while already-stopped
  capture does not. Test zero-budget and hanging cleanup without a later Docker
  query and report the last-known inventory as possibly remaining.
- [x] Require production service keys to be exactly `{capture, target}`, keep the
  endpoint test-only, and invoke Docker directly without `sudo`.
- [x] Use a target fixture with no shell or idle command; require direct launch
  without a wrapper, PID file, or Compose `exec`.
- [x] Run the opt-in Internet smoke test with a real HTTPS client.
- [ ] Rebuild the capture image from its checked inputs and require its resolved
  content identity to match the expected identity; fail rather than update on an
  unavailable or changed snapshot, package, base, or image input.
- [ ] Test every fresh-capture environment compatibility field and the complete
  capture subset of the [canonical diagnostic matrix](TESTING.md#canonical-adverse-condition-diagnostics).

**Done when:** plain `trafficlab preflight` succeeds for the test topology and
`trafficlab capture` reliably creates parseable `reference.pcapng` for both the
controlled integration workload and one real Internet workload, with clean
Docker state afterward. The capture image rebuilds to its checked identity, and
environment compatibility plus canonical capture diagnostics pass directly.

**Docker evidence (verified 2026-08-14):** the exact guarded command
`scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M
--wall-time 20m --kill-after 10s -- uv run --locked pytest -vv -n 0 -m docker`
selected and passed 18 tests with zero skips and zero failures. Automatic
per-test tracker inspection found no remaining project-labelled resources, and
the post-run `org.trafficlab.pytest=1` inspection found zero containers,
networks, or volumes.

**Historical Internet evidence (verified 2026-08-14):** the old Phase 7
prerequisite gate selected and passed the opt-in Internet smoke test against a
credential-free HTTPS 10 MiB range object. The old ten-run study reported
parseable captures and clean labelled-resource teardown, but it predates the
amended capture-image, environment-compatibility, and retained-evidence gates
and does not satisfy them.

## Phase 4 — Three traffic models

**Goal:** Each MVP model can fit the same reference and generate a deterministic,
bounded synthetic trace through one shared interface.

**Amended contracts and evidence:**
[Model compatibility](traffic_models/README.md#serialized-model),
[MMPP mathematics](traffic_models/mmpp.md#mathematical-definition), and
[direct scientific evidence](TESTING.md#bounded-scientific-validation-matrix).

**Deliverables:**

- [x] Implement the shared model protocol, registry, validation, JSON loading,
  and serialization.
- [x] Implement Poisson rate estimation, rate-scale gene, exponential timing,
  and joint empirical marks.
- [x] Implement Markov Renewal state construction, smoothed transitions, the
  uniform zero-smoothing empty-row rule, empirical conditional timing, its
  unchanged global-IAT fallback, and generation.
- [ ] Implement two-state MMPP parameter repair,
  [arrival-epoch initialization](traffic_models/mmpp.md#mathematical-definition),
  conditioned `t=0` arrival, CTMC/arrival simulation, and joint empirical marks.
- [x] Enforce complete `[0, W]` generation, common reliability guards,
  frame-length bounds, and deterministic-seed behavior for all families.
- [x] Implement `trafficlab generate`: load a fitted model, generate canonical
  events with its stored `observation_window_seconds`, configured final seed, and
  reliability guards, then render `generated.pcapng`.
- [ ] Bump the global scientific artifact schema for corrected model semantics;
  reject well-formed older fitted models and checkpoints as incompatible before
  generation, resume, or stage reuse.

**Tests:**

- [ ] Unit-test every estimator, repair rule, invariant, fallback, event choice,
  limit, and deterministic example in the amended model documents, including the
  MMPP arrival-epoch threshold, conditioned arrival, and RNG order.
- [x] For Markov Renewal, verify the `[A, B]` empty-row calculation,
  positive-smoothing equivalence, global-IAT fallback, model JSON round-trip,
  fixed-seed generation, missing/nonfinite/negative global-IAT failures, and
  valid zero IATs.
- [ ] Run each family from fixture reference through current-schema model JSON
  and generated
  PCAPNG, then reload and reproduce the same fixed-seed output.
- [x] For every family, test the `W` endpoint, natural completion, incomplete
  generation at each reliability guard, and serialized observation window.
- [ ] Load a checked-in current-schema fitted-model fixture through
  `trafficlab generate`,
  produce deterministic canonical events and `generated.pcapng`, and reload both.
- [ ] Run the bounded [direct scientific-validation matrix](TESTING.md#bounded-scientific-validation-matrix)
  for Poisson empirical, Markov Renewal, and MMPP with predeclared seeds,
  sample sizes, tolerances, analytical or independent test-only oracles, and
  direct completion and joint-mark evidence.

**Done when:** all three families pass the same model contract suite and
`trafficlab generate` produces parseable bounded PCAPNG from a checked-in fitted
model fixture. Current-schema artifacts enforce corrected semantics, and all
three families pass their bounded direct scientific validation.

## Phase 5 — Heterogeneous genetic fitting and checkpoint resume

**Goal:** One bounded search can compare all enabled model families fairly,
resume exactly, and publish an independently validated winner.

**Amended contracts and evidence:**
[Neutral population order](genetic_models/basic_generational.md#population-contract),
[checkpoint compatibility](genetic_models/basic_generational.md#checkpoint-and-resume), and
[resume evidence](TESTING.md#full-pipeline-resume-and-reuse-equivalence).

**Deliverables:**

- [x] Implement `trafficlab fit` over the shared model, similarity, and genetic
  interfaces.
- [ ] Derive one neutral `family_priority` from a temporary
  `random.Random(master_seed).sample(sorted_family_names, len(sorted_family_names))`
  call that consumes no search RNG draw, then use it for quota remainders,
  initial family order, and exact cross-family ties.
- [ ] Implement deterministic family quotas, common trial seeds, tournament
  selection, within-family stable-ID ties, `family_priority` cross-family ties,
  global elites, and family champions without input-order or lexical preference.
- [x] Implement uniform same-family crossover, transformed Gaussian mutation in
  linear/logarithmic/integer coordinates, normalized reflection, and the fixed
  RNG draw order.
- [x] Use one dedicated search `random.Random` with the documented sampling calls
  and losslessly checkpoint its engine, Python version, and complete state.
- [x] Implement different-family clone/mutate behavior with forced mutation,
  each family's exact gene mapping and repair, exact duplicate identity, bounded
  duplicate mutation, and invalid-candidate diagnostics.
- [x] Evaluate every candidate through fit, bounded generation, four component
  metrics, and common weighted fitness.
- [x] Give every candidate and trial seed the same complete `W`; score incomplete
  generation as invalid fitness with a direct reason.
- [x] Atomically checkpoint complete population, RNG state, settings, and
  history after every evaluated generation.
- [ ] Retain and validate `family_priority` plus the current scientific artifact
  schema in checkpoint state before any resume draw, and reevaluate the winner
  with the configured fresh simulation seed on its training reference.
- [x] Write per-family and overall progress to `ga_history.csv` and winner data
  to `best_model.json`.

**Tests:**

- [ ] Unit-test priority derivation, quotas, selection, ties, probability
  endpoints, coordinate
  encode/decode, initialization, reflection examples, fixed RNG order, all three
  family repairs, exact duplicates, forced mutation, invalid duplicate attempts,
  bounded exhaustion, champions, invalid candidates, and hard termination. With
  zero retry attempts, retain a source-equal repaired cross-family child whose
  source did not survive and require its exhaustion diagnostic.
- [x] With a stub RNG, test per-gene uniform parent choices, same-family fitter
  cloning and stable-ID ties, plus mandatory integer unchanged-decode handling
  for both Gaussian signs, exact zero, and both endpoints.
- [x] Integration-test all three families competing in a small population with
  nondefault operator values; require same-family-only crossover, child-family
  settings, cross-family forced mutation, and representation of every family.
- [x] Require every competing family and seed to receive the same `W` and reject
  a guard-truncated candidate as incomplete generation.
- [ ] Permute configuration and registry family order and require identical
  priority, quotas, initial slots, children, and winner; exercise every family
  priority position, equal-fitness ties, symmetric invalids, and a controlled
  unique winner under mixed weights.
- [ ] Interrupt and resume a search; require the next population, history, and
  winner to match an uninterrupted run exactly, including repaired gene tuples,
  child IDs, `family_priority`, and RNG state. Alter each compatibility class in
  turn and reject it before reproduction.
- [ ] Prove full-pipeline resumed equivalence through final publication as owned
  by [Testing](TESTING.md#full-pipeline-resume-and-reuse-equivalence), including
  byte-identical scientific artifacts and lineage.

**Done when:** `trafficlab fit` deterministically produces a winning family and
model without lexical or input-order privilege, and compatible checkpoint resume
is behaviorally identical to uninterrupted fitting through final publication.

## Phase 6 — Run orchestration and complete integration

**Goal:** One command runs the complete experiment while individual commands
support fast stage-by-stage research iteration.

**Amended contracts and evidence:**
[Stage compatibility](SYSTEM.md#stage-compatibility),
[failure outcomes](SYSTEM.md#failure-policy), and the
[diagnostic](TESTING.md#canonical-adverse-condition-diagnostics) and
[equivalence](TESTING.md#full-pipeline-resume-and-reuse-equivalence) matrices.

**Deliverables:**

- [x] Compose the completed preflight, capture, fit, generate, and compare
  functions inside `trafficlab run` without alternate subprocess protocols.
- [ ] Validate every stage output against the exact
  [compatibility matrix](SYSTEM.md#stage-compatibility), including scientific
  artifact schema, realized configuration, authoritative input identities, and
  lineage, before reuse; preserve completed outputs after a later failure.
- [x] Separate trial generation from final full-size generation and seed.
- [x] Produce exactly the documented run directory and readable summaries.
- [x] Keep the documented fast, integration, Docker, and Internet-smoke commands
  working for the complete pipeline.
- [ ] Emit one canonical failure outcome for every configuration, external,
  artifact, scientific, numeric, generation, publication, cleanup, and combined
  failure boundary without changing the established primary-error arbitration.

**Tests:**

- [x] Run the offline analytical pipeline, `fit -> generate -> compare`, against
  a checked-in capture fixture without Docker; this is not `run`.
- [x] Run complete `trafficlab run` with the deterministic Docker capture
  workload.
- [ ] Exercise missing, corrupt, changed, foreign, stale, and incompatible-schema
  outputs across capture, fit, generate, and compare; require the first
  incompatible field before any reuse or publication.
- [x] Exercise cleanup and artifact preservation on a failure at every stage.
- [ ] Run the complete
  [canonical adverse-condition diagnostic matrix](TESTING.md#canonical-adverse-condition-diagnostics)
  and reproduce its checked credential-free fixture without Docker or network
  access.
- [ ] Compare uninterrupted and checkpoint-resumed
  `fit -> generate -> compare -> final publication` runs and
  require equivalent configuration, family priority, scientific bytes,
  identities, lineage, all four mandatory similarity method diagnostics, and
  final inventory except only the declared operational differences.

**Done when:** `trafficlab run` creates every documented artifact from a fresh
Docker-backed experiment, and each individual command can safely reproduce its
own stage. Compatible stage reuse preserves exact lineage, fresh and resumed
final artifacts are equivalent, and the canonical failure matrix passes.

**Historical verification (2026-08-13):** the exact bounded fast and
branch-coverage gates passed; the checked offline pipeline and old corruption
matrix passed; and the serial controlled-Docker suite completed the full run and
old stage-failure boundaries with exactly nine successful artifacts and no
labelled resource residue. This predates the amended compatibility, lineage,
diagnostic, and resumed-equivalence gates. The old Phase 7 run trees are absent,
so they do not supply complete study evidence.

## Phase 7 — MVP validation on real programs

**Goal:** Establish whether the prototype produces useful results on multiple
real containerized workloads under corrected semantics and retained,
independently reconstructable evidence before adding more algorithms or
infrastructure.

**Amended contracts and evidence:**
[Published study evidence](SYSTEM.md#published-study-evidence),
[accepted study evidence](TESTING.md#accepted-validation-study-evidence), and the
[bounded offline audit](TESTING.md#bounded-offline-audit).

**Deliverables:**

- [x] Select at least three reproducible workloads with different traffic
  shapes, such as a short HTTP transfer, a streaming transfer, and a bursty
  multi-request client.
- [ ] Repeat primary captures to measure natural variation in each workload
  under the corrected scientific artifact schema.
- [ ] Run all three model families and record component scores, winning family,
  runtime, and run-to-run variance.
- [ ] Inspect reference/generated traces, explain major metric disagreements,
  and state the finite-sample, model, metric, and generalization limitations
  beside the interpreted results.
- [ ] Publish a concise report with portable and realized configurations,
  reproducible commands, separated training, natural-variation, fresh simulation
  seed, and held-out reference claims, and exact artifact lineage.
- [ ] After freezing the training protocol, capture one genuine independent
  held-out reference per workload and evaluate the training-selected fixed model
  without refitting, family reselection, seed choice, or protocol amendment.
- [ ] Check the complete
  [accepted evidence bundle](SYSTEM.md#published-study-evidence), including every cited
  strict nine-file run tree, held-out input and result, configuration pair,
  prerequisite record, environment record, report input, and canonical
  path/size/SHA-256 manifest.

**Tests:**

- [ ] Run the opt-in Internet capture smoke test before the replacement study on
  the same source revision, tree, lock, Python patch, and scientific schema.
- [ ] Reproduce every report-cited run from its portable configuration,
  realized configuration, retained inputs, and seeds in a compatible clean
  environment.
- [ ] Confirm that every training winner is evaluated with the predeclared fresh
  simulation seed, distinct from selection seeds, without calling that result
  held-out evidence.
- [ ] Run the bounded [offline audit](TESTING.md#bounded-offline-audit) from a
  relocated clean clone without
  Docker, Internet, the high-level run command, missing-byte fetches, or trust in
  precomputed report values; reconstruct every score, summary, lineage edge, and
  report calculation.
- [ ] Reject representative missing, corrupt, foreign, and substituted accepted
  evidence with the canonical first-mismatch diagnostic and no acceptance
  publication.

**Done when:** a retained replacement study under corrected semantics reports
training, natural variation, fresh simulation seed behavior, and genuine held-out reference
evidence separately; every cited byte is checked; and the clean-clone offline
audit reconstructs the complete report and lineage without Docker or Internet.

**Historical evidence (2026-08-14):** study
`validation-study-20260814-ovh-r3` reported nine primary runs plus one
streaming reproduction after Docker and Internet prerequisite gates. Its report
records useful frame-size, ACF, and streaming-IAT fidelity and a
directional-volume limitation. The report-cited run trees are absent from the
repository, and the study predates corrected scientific semantics, genuine
independent held-out references, complete checked evidence, and clean-clone
offline reconstruction. It is therefore insufficient for the amended Phase 7
gate.

## Phase 8 — Minimum research fitness acceptance

**Goal:** Accept the unchanged 17-criterion research-fitness target only after
all reopened work has fresh, independently reviewed evidence. This phase adds no
model, metric, production service, or subsystem.

**Authority and evidence:** the unchanged
[research fitness criteria](RESEARCH_FITNESS_CRITERIA.md), every reopened
phase contract above, and the complete [testing strategy](TESTING.md).

**Acceptance gates:**

- [ ] Pass every reopened Done-when gate in Phases 1--7.
- [ ] Pass the bounded analytical scientific-validation matrix for every model,
  mandatory similarity method, and neutral family-competition case.
- [ ] Transfer one portable configuration to a relocated compatible clean
  environment and prove the exact permitted and forbidden realization changes.
- [ ] Rebuild and preflight the reproducible capture environment from its pinned
  base digest, Debian snapshot, exact packages, and checked image identity.
- [ ] Pass fresh and resumed full-pipeline equivalence through final artifacts,
  identities, lineage, and publication inventory.
- [ ] Pass the complete canonical adverse-condition diagnostic matrix and its
  offline checked-fixture reconstruction.
- [ ] Validate the checked accepted evidence bundle and reconstruct its complete
  study report with the bounded clean-clone offline audit.
- [ ] Rerun every available Docker and Internet prerequisite against the same
  source revision, tree, lock, Python patch, and scientific artifact schema as
  the accepted study.
- [ ] Obtain independent review with zero Critical or Important findings.
- [ ] Reassess all 17 rows in the research-fitness traceability table against the
  unchanged rubric and require every grade to be Acceptable or better.

**Done when:** every acceptance gate above has fresh retained evidence and the
unchanged-rubric reassessment grades all 17 deficient criteria Acceptable or
better.

## Later, only if evidence requires it

These are ideas, not committed scope:

- ON/OFF Pareto if measured burst durations are clearly heavy-tailed and the MVP
  families cannot reproduce them.
- Traffic replay if experiments need live synthetic emission rather than PCAPNG.
- Richer packet marks if direction and frame length cannot explain important
  protocol-independent structure.
- Parallel candidate evaluation if profiling shows genetic fitness dominates
  iteration time.
- Additional interpretable similarity methods only when current metrics miss a
  visible, repeatable behavior.

Do not create implementation placeholders or detailed design documents for
these ideas before the corresponding evidence and decision exist.
