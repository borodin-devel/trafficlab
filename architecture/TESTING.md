# Testing Strategy

Tests protect mathematical correctness, reproducibility, Docker cleanup, and the
complete research workflow. They do not enforce documentation templates or
internal implementation layering.

## Test commands and markers

The [development workflow](DEVELOPMENT.md) owns the copyable commands and
`pyproject.toml` registers three markers:

- `integration` joins multiple Trafficlab modules without external services;
- `docker` requires Docker Engine and Docker Compose;
- `internet` uses a configurable public endpoint.

Docker and Internet tests may also be integration tests. Their external-resource
marker controls whether they run.

Every test command uses the process-tree memory, swap, worker, and wall-clock
bounds owned by the [development workflow](DEVELOPMENT.md). A pytest result is
not valid evidence when it came from an unbounded invocation. The fast unit loop
uses four workers and excludes integration and external-resource tests:

```bash
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not integration and not docker and not internet"
```

The deterministic coverage gate runs unit and in-process integration tests once:

```bash
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -n 4 --dist worksteal --cov=trafficlab \
  --cov-branch --cov-report=term-missing \
  -m "not docker and not internet"
```

Docker and public-Internet tests run serially so their lifecycle failures stay
readable:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m docker
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m internet --internet-url URL
```

When Docker tests are explicitly selected, unavailable Docker Engine or Compose
is a session-level failure with an installation/readiness message; it is not
silently skipped.

For a detailed, pinpointed failure, select one pytest node ID or the last failed
set and disable workers:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 tests/path/test_module.py::test_name
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 --lf
```

Broad deterministic suites request `-n 4 --dist worksteal` explicitly.
Resource-owning and diagnostic commands request `-n 0`, so output and teardown
remain attributable to one test. After any timeout, cancellation, or OOM kill,
the launcher must confirm that the scope stopped and no pytest descendant
survived before reporting the run or launching another test command.

`scripts/run_bounded.sh` gives each invocation a unique
`trafficlab-test-guard-*.scope`, preserves guarded nonzero statuses, and uses
status `125` for setup or final containment-verification failure. Its final
cleanup kills the exact scope and polls it to inactive. Prove that boundary with:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/integration/test_process_guard.py
```

The controlled wall probe uses a parent, separate-session child and
separate-session grandchild that ignore `SIGTERM`. The memory probe gives the
same three roles touched 4 MiB chunks inside a nested 63 MiB throttle, 64 MiB
hard limit, and zero-swap scope. Both require all PIDs gone and the named scope
inactive.

An equivalent CI job or container limit may replace the documented transient
systemd scope. It must cover the entire process tree, enforce both resident
memory and swap ceilings, kill all descendants when the hard limit is reached,
and provide its own corresponding process-tree proof. Per-process
virtual-address limits and wall-clock timeouts alone do not satisfy this
requirement.

## Unit tests

Unit tests are deterministic and do not start Docker. They cover:

- experiment parsing, unknown fields, bounds, normalized weights, argv, and
  per-family genetic-operator defaults and independent overrides, including a
  finite exact-float early-stopping tolerance in `[0, 1]`, generation zero, and
  rejection of a final seed reused as a selection trial seed;
- invalid operator probabilities, scales, and gene bounds, plus unknown-family,
  unknown-key, disabled-family operator settings, and invalid duplicate-attempt
  counts;
- strict `capture.json` parsing, normalized target MACs, unknown fields, and
  missing or invalid metadata;
- Ethernet PCAPNG parsing/rendering, malformed blocks, timestamp resolution,
  unsupported link types, frame lengths, and outbound/inbound round trips;
- source-MAC classification for outbound, inbound unicast, and inbound broadcast
  frames;
- canonical trace ordering, direction values, IAT extraction, reference
  observation window derivation, timestamp normalization, endpoint inclusion,
  generated-trace shifting/cropping, and invalid reference windows;
- hand-calculated similarity formulas and diagnostics, including one shared `W`
  in all four method diagnostics;
- every traffic-model estimator, repair rule, invariant, and fixed-seed
  generator, including full-window natural completion and incomplete-generation
  guards;
- Markov final-only active states, nonempty unsmoothed rows, uniform empty rows,
  global-IAT fallback after an empty row, transition-row serialization/loading,
  invalid missing/nonfinite/negative global IAT data, and valid zero IATs;
- population quotas, tournament ties, family compatibility, uniform crossover,
  transformed Gaussian mutation, normalized reflection, forced mutation, exact
  duplicates, duplicate-attempt bounds, fixed RNG order, and termination;
- atomic JSON/checkpoint replacement, `best_model.json`
  `observation_window_seconds` serialization/loading, and rejection of invalid
  resume state;
- CLI stage selection and errors using injected subprocess boundaries;
- table-driven capture event arbitration for every simultaneous pair in the
  fixed order: user interruption, natural target stop, unexpected capture stop,
  stage-specific timeout, and total-run timeout. A simultaneous target stop,
  capture stop, and total-run timeout chooses the natural target status.

Every equation in an algorithm document has at least one independent small
example. Important fixed expectations include:

- Observation window: reference `[10, 11, 13]` normalizes to `[0, 1, 3]` with
  `W = 3`. A generated event at `3` is retained and one after `3` is cropped. A
  naturally earlier last event creates multiscale trailing zeros; a safety guard
  reached first is incomplete.
- KS: `[1, 2]` versus `[1, 3]` has distance `1/2`.
- IAT: timestamps `[0, 1, 3]` produce `[1, 2]`; timestamp ties remain zero.
- ACF: a constant sequence has positive-lag ACF `0`; `[1, 2, 1]` has lag-one
  ACF `-2/3` under the documented estimator.
- Multiscale: identical and two all-zero vectors have discrepancy `0`; with
  `[outbound, inbound]` layout, `[1, 0]` versus `[0, 1]` has discrepancy `1`.
  Multiple widths exercise direction-separated packet/byte cells and reject
  \(2\sum_h B_h>C_{max}\).
- Poisson: three packets over observation window two estimate rate one.
- Markov Renewal: a hand-counted transition table matches additive smoothing
  and exercises both timing fallbacks.
- Markov empty row: state sequence `[A, B]` with `alpha = 0` gives rows `[0, 1]`
  and `[1/2, 1/2]`. Positive smoothing gives the same second row through the
  ordinary formula.
- MMPP: `q01=1`, `q10=3` gives stationary probabilities `3/4` and `1/4`.
- Genetic coordinates: linear, logarithmic, and integer encode/decode cover both
  bounds and integer half-rounding; initialization uses a stub RNG.
- Genetic reflection: `-0.2 -> 0.2`, `1.2 -> 0.8`, and `2.2 -> 0.2`.
- Genetic probability endpoints: crossover and mutation each cover `0` and `1`,
  including the documented fixed RNG draw order.
- Genetic reproduction: a stub RNG covers every uniform parent choice, the
  fitter clone, and the stable-ID tie when same-family crossover is disabled.
- Genetic RNG: exact `random.Random` primitive calls and lossless
  `getstate()`/`setstate()` checkpoint round trips reproduce subsequent draws.
- Mandatory integer mutation: stub Gaussian signs cover an unchanged decode,
  exact zero's positive direction, and reflection at both integer endpoints.
- Genetic repair: Poisson, Markov Renewal, and MMPP cover ordering, named bounds,
  equality rejection, and reference-threshold failure.
- Genetic duplicates: exact identity, forced mutation, invalid attempts, bounded
  exhaustion, and population-size preservation are explicit. A stub case keeps
  a source-equal repaired cross-family child whose source did not survive when
  the retry count is zero, and requires the exhaustion diagnostic.
- Genetic stage policy: enabled families, family history rows, and all
  tie-adjacent ordering are lexical; `G = 0` evaluates only generation zero;
  selection uses trial seeds and `generation.trial` limits while final validation
  uses exactly the distinct final seed and the same trial limits; checkpoint
  publication precedes derived history repair/publication; `<= tolerance`
  stagnates, `> tolerance` resets, and zero early-stopping generations disables
  stopping.
- Run orchestration: five-stage call order, immediate result invariants, no
  double preflight, strict missing/corrupt/different capture reuse matrix, and
  preservation of every earlier complete output after each post-preflight stage
  failure.
  Require `run_completed` and one success summary only on success; after
  preflight, require `run_failed`, one structured stage error, and no success
  summary. Preflight failure evidence is a separate direct-error case. Require
  stable diagnostic cleanup and exactly the nine successful artifact names.

Fixed-seed tests compare complete event sequences, not merely summary
statistics. Every generator must emit finite nondecreasing timestamps, valid
directions and frame lengths, retain any event exactly at `W`, exclude later
events, and distinguish natural full-window completion from a reliability guard.

## In-process integration tests

These tests join real modules without Docker:

1. Require `trafficlab preflight fixture.toml --config-only` and the injected
   Python configuration/preflight API to return the same effective configuration
   and errors. Assert that the CLI path makes no Docker subprocess call.
2. Render fixture events to PCAPNG using `capture.json`, parse them to a canonical
   trace, derive `W`, and confirm normalized timestamps, outbound/inbound
   directions, sizes, and feature samples.
3. Fit each model family to the same reference, generate with fixed seeds, run
   all four similarity methods, and require identical `W` in every candidate
   evaluation, final generation, and component diagnostic. The Markov fixture
   includes a final-only active state and `alpha = 0`; fit, serialize, load, and
   generate must complete without an undefined row or invalid-candidate result,
   and an equal model, seed, `W`, and limits must reproduce the same trace.
4. Use a direction-asymmetric fixture, keep timestamps and lengths fixed, reverse
   every packet direction, and require multiscale similarity below `1` while
   frame-size KS, IAT KS, and ACF remain unchanged.
5. Run a small heterogeneous population with all three families and nondefault operator
   values for every family. Require every family to remain represented and prove
   that crossover occurs only within one family, each child uses its own family
   settings, and cross-family reproduction forces mutation.
6. Interrupt after a completed generation, load `checkpoint.json`, resume, and
   require the next repaired gene tuples, child IDs, RNG state, history, and
   winner to equal an uninterrupted run. Independently alter one stored
   checkpoint operator value while preserving its experiment hash and require an
   operator-specific compatibility failure before reproduction.
7. Corrupt each reusable stage output in turn and require validation to rerun or
   reject that stage rather than trusting filename existence.
8. Run the offline analytical pipeline, `fit -> generate -> compare`, from a
   checked-in `reference.pcapng` and `capture.json` through `best_model.json`,
   `generated.pcapng`, and `similarity.json` without Docker. Require identical
   `observation_window_seconds` in the winning model, final generation input,
   every component diagnostic, and aggregate result. Do not invoke `run`.
9. Parse and validate a multi-frame PCAPNG with a fake monotonic clock. Advance
   the clock past the shared deadline after one frame and require the operation
   to abort before accepting the next frame. This proves the deadline check
   occurs before work starts and after every frame.
10. Use injected subprocess boundaries to prove that a live capture receives
    `SIGINT` and one bounded flush wait, while an already-stopped capture gets no
    signal or flush wait. A zero cleanup budget makes no Docker command and
    reports the last-known inventory as possibly remaining. A hanging cleanup
    kills its local CLI at the deadline, makes no later Docker query, and reports
    the same inventory as possibly remaining.

These tests use temporary run directories and leave them available only when a
failure-report option requests preservation.

## Docker capture integration tests

Docker tests call the real `docker` and `docker compose` CLIs. A session fixture
checks `docker info`, `docker compose version`, and required test images before
the first capture test.

Every test derives a unique Compose project name. Its production topology has
capture own the default-bridge network namespace while target joins with
`network_mode: service:capture`. A deterministic client sends known TCP and UDP
payload counts to the existing controlled endpoint service on the Compose
bridge. Assertions require:

- the rendered production service keys are exactly `{capture, target}`; the
  controlled endpoint exists only in the test fixture;
- normal orchestration invokes `docker` directly without `sudo`;
- capture readiness precedes target service-command start;
- natural successful and nonzero target statuses are propagated exactly;
- an unexpected capture exit while a long-running target is active makes target
  kill the next orchestration command and stops target within five seconds. The
  workload timeout is much longer than five seconds, and capture failure remains
  primary;
- a natural nonzero target status remains primary, while timeout, capture
  failure, and interruption remain primary over their kill-induced status;
- a background child cannot survive normal target-container exit;
- a timed-out target and its children are killed;
- a successful target publishes a nonempty, parseable `reference.pcapng`;
- a nonzero target retains valid diagnostic capture and publishes no reusable
  reference pair;
- expected endpoint addresses, protocols, and minimum packet counts appear;
- at least one outbound and one inbound frame are classified according to the
  controlled target and endpoint roles;
- a controlled inbound broadcast frame is classified as inbound;
- unrelated test-project traffic does not appear;
- capture `SIGINT` produces a readable final capture within the flush timeout;
- a capture fixture that ignores `SIGINT` is killed at the flush timeout and its
  incomplete output is rejected;
- malformed capture output publishes no reusable pair and still receives
  complete project cleanup;
- interruption performs one bounded flush before cleanup;
- success, target failure, readiness failure, timeout, capture failure, flush
  timeout, malformed output, and test interruption remove the project's
  containers, networks, volumes, and orphans.

Teardown calls Compose cleanup in `finally` and then inspects Docker for the
unique project label. A cleanup assertion failure must show remaining resource
names.

One contract fixture uses a target image with no shell or idle command. It proves
that direct service-command launch needs no wrapper, PID file, or Compose `exec`.
Docker tests remain serial, and the public Internet smoke test remains opt-in.

A focused in-process integration fixture substitutes only the cleanup command
with a controlled hanging cleanup process. It requires cleanup timeout at the
remaining total-run deadline, termination of the local Compose CLI, no later
Docker query, and a diagnostic that lists the last-known project inventory as
possibly remaining. A separate zero-budget case makes no Docker command and
reports the same inventory. These controlled cleanup-timeout cases do not claim
that resources were removed. Real Docker cleanup and complete-removal assertions
remain in every ordinary Docker capture case; the suite does not try to make the
daemon hang.

The deterministic Docker suite uses only its controlled endpoint. It verifies
the capture topology without depending on an external service.

One small-budget end-to-end test invokes complete `trafficlab run` against that
controlled workload and requires every documented artifact. The same cleanup
assertions are mandatory after success and after a failure at any stage.

The offline analytical pipeline is checked separately as `fit -> generate ->
compare` from checked-in capture fixtures; it must not call `run`. A serial
Docker test runs the complete five-stage `run` and verifies trial generation
uses trial guards/seeds while final generation uses the distinct final seed and
full limits. Each post-preflight stage failure must preserve earlier artifacts, emit one
`run_failed` record, one structured stage error, no success summary, and no
coordinator Docker cleanup. A separate preflight-failure case requires only the
direct structured error, no coordinator `run_failed`, and no run-log dependency.
A successful rerun removes only the two stable
diagnostic identities and leaves exactly the nine documented names.

## Internet smoke test

The opt-in smoke test runs a small real client, such as `curl`, in the target
container against a configurable HTTPS URL. It requires successful DNS and TLS,
a successful target exit, captured bidirectional traffic, a parseable PCAPNG,
and complete teardown.

The URL is supplied by the operator or CI environment. This test is never part
of the default or deterministic integration gate because public connectivity and
external services are outside Trafficlab's control.

## Continuous integration

All CI jobs run the four-worker, coverage-enabled unit and in-process
integration suite once under an 8 GiB process-tree memory limit, a 1 GiB swap
limit, and a twenty-minute wall-clock limit, including the fixture-based offline
analytical pipeline. Docker capture tests and the complete `run` test run
serially only on a job explicitly declared Docker-capable; that job treats
Docker readiness failure as failure. The Internet smoke test is manual or
scheduled and never gates ordinary changes.

Tests should remain proportionate: cover public behavior, mathematical edge
cases, and expensive failure boundaries. The completed non-Docker Python package
must maintain at least 90% branch-aware coverage; this threshold does not replace
any named behavioral or integration case. When a failed unit test identifies a
defect in a function or method, the fix requires behavioral regression tests
that cover 100% of that function's executable lines and branches. Verify the source range with targeted
`pytest-cov` missing-line output; do not build a custom per-function coverage
framework. A missing meaningful integration path still matters more than
uninformative aggregate coverage.

## References

- [Development workflow](DEVELOPMENT.md) owns tool configuration and commands.
- [Docker capture lifecycle](CAPTURE.md) owns the Compose topology and official
  Docker references.
- [IETF PCAPNG format, active work in progress][pcapng-draft] defines the capture
  container format.

[pcapng-draft]: https://datatracker.ietf.org/doc/draft-ietf-opsawg-pcapng/
