# Trafficlab MVP Roadmap

This is the only implementation roadmap. Work proceeds in order because each
phase produces a usable foundation for the next. Check a task only when its
behavior and listed tests exist in the repository.

| Phase | CLI behavior completed |
|---|---|
| 1 | `preflight --config-only` |
| 2 | `compare` |
| 3 | full `preflight`, `capture` |
| 4 | `generate` |
| 5 | `fit` |
| 6 | `run` |

Phase 7 validates the complete MVP rather than adding another command.

## Phase 1 — Project, configuration, and local preflight

**Goal:** A developer can install Trafficlab, parse one experiment file, and
obtain the same validated effective configuration from the CLI and Python API
without requiring Docker.

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

**Done when:** a clean clone passes the locked sync, formatting, linting, type,
bounded parallel-test, bounded pinpointed-test, and process-tree containment
checks, and `trafficlab preflight fixture.toml --config-only` succeeds without
contacting Docker.

## Phase 2 — PCAPNG, canonical trace, and similarity

**Goal:** Checked-in reference and generated PCAPNG fixtures can be compared
through four independently verified, interpretable component scores.

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

**Done when:** `trafficlab compare` returns reproducible component and aggregate
scores for checked-in fixture PCAPNG files, and every equation has a hand-checked
test.

## Phase 3 — Docker preflight and reference capture

**Goal:** A configured containerized program can access the Internet and produce
a validated reference PCAPNG without Trafficlab changing host networking.

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

**Done when:** plain `trafficlab preflight` succeeds for the test topology and
`trafficlab capture` reliably creates parseable `reference.pcapng` for both the
controlled integration workload and one real Internet workload, with clean
Docker state afterward.

**Docker evidence (verified 2026-08-14):** the exact guarded command
`scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M
--wall-time 20m --kill-after 10s -- uv run --locked pytest -vv -n 0 -m docker`
selected and passed 18 tests with zero skips and zero failures. Automatic
per-test tracker inspection found no remaining project-labelled resources, and
the post-run `org.trafficlab.pytest=1` inspection found zero containers,
networks, or volumes.

**Internet evidence (verified 2026-08-14):** the Phase 7 prerequisite gate
selected and passed the opt-in Internet smoke test against a credential-free
HTTPS 10 MiB range object. The accepted ten-run study produced parseable real
captures for short, streaming, and bursty curl workloads and left no labelled
container, network, or volume, satisfying the Phase 3 Done-when condition.

## Phase 4 — Three traffic models

**Goal:** Each MVP model can fit the same reference and generate a deterministic,
bounded synthetic trace through one shared interface.

**Deliverables:**

- [x] Implement the shared model protocol, registry, validation, JSON loading,
  and serialization.
- [x] Implement Poisson rate estimation, rate-scale gene, exponential timing,
  and joint empirical marks.
- [x] Implement Markov Renewal state construction, smoothed transitions, the
  uniform zero-smoothing empty-row rule, empirical conditional timing, its
  unchanged global-IAT fallback, and generation.
- [x] Implement two-state MMPP parameter repair, stationary initialization,
  CTMC/arrival simulation, and joint empirical marks.
- [x] Enforce complete `[0, W]` generation, common reliability guards,
  frame-length bounds, and deterministic-seed behavior for all families.
- [x] Implement `trafficlab generate`: load a fitted model, generate canonical
  events with its stored `observation_window_seconds`, configured final seed, and
  reliability guards, then render `generated.pcapng`.

**Tests:**

- [x] Unit-test every estimator, repair rule, invariant, fallback, event choice,
  limit, and deterministic example in the model documents.
- [x] For Markov Renewal, verify the `[A, B]` empty-row calculation,
  positive-smoothing equivalence, global-IAT fallback, model JSON round-trip,
  fixed-seed generation, missing/nonfinite/negative global-IAT failures, and
  valid zero IATs.
- [x] Run each family from fixture reference through model JSON and generated
  PCAPNG, then reload and reproduce the same fixed-seed output.
- [x] For every family, test the `W` endpoint, natural completion, incomplete
  generation at each reliability guard, and serialized observation window.
- [x] Load a checked-in fitted-model fixture through `trafficlab generate`,
  produce deterministic canonical events and `generated.pcapng`, and reload both.

**Done when:** all three families pass the same model contract suite and
`trafficlab generate` produces parseable bounded PCAPNG from a checked-in fitted
model fixture.

## Phase 5 — Heterogeneous genetic fitting and checkpoint resume

**Goal:** One bounded search can compare all enabled model families fairly,
resume exactly, and publish an independently validated winner.

**Deliverables:**

- [x] Implement `trafficlab fit` over the shared model, similarity, and genetic
  interfaces.
- [x] Implement deterministic family quotas, common trial seeds, tournament
  selection, stable ties, global elites, and family champions.
- [x] Implement uniform same-family crossover, transformed Gaussian mutation in
  linear/logarithmic/integer coordinates, normalized reflection, and the fixed
  RNG draw order.
- [x] Use one dedicated `random.Random` with the documented sampling calls and
  losslessly checkpoint its engine, Python version, and complete state.
- [x] Implement different-family clone/mutate behavior with forced mutation,
  each family's exact gene mapping and repair, exact duplicate identity, bounded
  duplicate mutation, and invalid-candidate diagnostics.
- [x] Evaluate every candidate through fit, bounded generation, four component
  metrics, and common weighted fitness.
- [x] Give every candidate and trial seed the same complete `W`; score incomplete
  generation as invalid fitness with a direct reason.
- [x] Atomically checkpoint complete population, RNG state, settings, and
  history after every evaluated generation.
- [x] Resume only a compatible checkpoint and reevaluate the winner on fresh
  final seeds.
- [x] Write per-family and overall progress to `ga_history.csv` and winner data
  to `best_model.json`.

**Tests:**

- [x] Unit-test quotas, selection, ties, probability endpoints, coordinate
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
- [x] Interrupt and resume a search; require the next population, history, and
  winner to match an uninterrupted run exactly, including repaired gene tuples,
  child IDs, and RNG state. Alter one checkpoint operator value without changing
  its experiment hash and require an operator-specific compatibility failure
  before reproduction.

**Done when:** `trafficlab fit` deterministically produces a winning family and
model, and checkpoint resume is behaviorally identical to uninterrupted fitting.

## Phase 6 — Run orchestration and complete integration

**Goal:** One command runs the complete experiment while individual commands
support fast stage-by-stage research iteration.

**Deliverables:**

- [x] Compose the completed preflight, capture, fit, generate, and compare
  functions inside `trafficlab run` without alternate subprocess protocols.
- [x] Validate stage outputs before reuse and preserve completed outputs after a
  later failure.
- [x] Separate trial generation from final full-size generation and seed.
- [x] Produce exactly the documented run directory and readable summaries.
- [x] Keep the documented fast, integration, Docker, and Internet-smoke commands
  working for the complete pipeline.

**Tests:**

- [x] Run the offline analytical pipeline, `fit -> generate -> compare`, against
  a checked-in capture fixture without Docker; this is not `run`.
- [x] Run complete `trafficlab run` with the deterministic Docker capture
  workload.
- [x] Corrupt or remove each stage output and verify correct rerun/failure
  behavior.
- [x] Exercise cleanup and artifact preservation on a failure at every stage.

**Done when:** `trafficlab run` creates every documented artifact from a fresh
Docker-backed experiment, and each individual command can safely reproduce its
own stage.

**Verified (2026-08-13):** the exact bounded fast and branch-coverage gates pass;
the checked offline pipeline and corruption matrix pass; and the serial
controlled-Docker suite completes the full run plus every stage-failure boundary
with exactly nine successful artifacts and no labelled resource residue. Phase
7 subsequently supplied the opt-in Internet and complete-study evidence.

## Phase 7 — MVP validation on real programs

**Goal:** Establish whether the prototype produces useful results on multiple
real containerized workloads before adding more algorithms or infrastructure.

**Deliverables:**

- [x] Select at least three reproducible workloads with different traffic
  shapes, such as a short HTTP transfer, a streaming transfer, and a bursty
  multi-request client.
- [x] Repeat captures to measure natural variation in each workload.
- [x] Run all three model families and record component scores, winning family,
  runtime, and run-to-run variance.
- [x] Inspect reference/generated traces and explain major metric disagreements.
- [x] Publish a concise experiment report with configurations and reproducible
  commands.

**Tests:**

- [x] Run the opt-in Internet capture smoke test before the study.
- [x] Reproduce at least one complete experiment from its saved configuration
  and seeds.
- [x] Confirm that the final winner is evaluated on seeds not used during
  selection.

**Done when:** the experiment report demonstrates useful fidelity or identifies
specific model/metric gaps with evidence strong enough to choose the next work.

**Verified (2026-08-14):** study `phase7-20260814-ovh-r3` completed nine fresh
balanced primary runs plus `10-streaming-r2-reproduction` after Docker 18/18
and Internet 1/1 prerequisite gates. Canonical configs, prerequisites, results,
and `examples/phase7/REPORT.md` bind the evidence to commit `976dcd6`; ignored
run and `.study-work/evidence` trees retain the ten raw audits. All families
were evaluated on selection seeds 17/29 and winners on fresh seed 97. The report
finds useful frame-size, ACF, and streaming-IAT fidelity and identifies local
directional-volume dynamics as the specific next model work.

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
