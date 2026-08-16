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
- Genetic stage policy: enabled-family display and family history rows are
  lexical, while quota remainders, initial slots, and cross-family ties use the
  seeded `family_priority`; `G = 0` evaluates only generation zero;
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

### Bounded scientific-validation matrix

The direct scientific matrix below runs without Docker or the Internet. Its
seeds, sizes, tolerances, and failure labels are constants in the tests and must
not be changed after looking at an implementation's output. Each stochastic
assertion uses an analytical calculation or a small independent test-only
calculation as its oracle. Production generators, serializers, fixture
regeneration, production similarity functions, and round trips cannot validate
themselves. A failure starts with `scientific-validation:<case>` and reports the
seed, sample size, expected value, observed value, and tolerance.

The Poisson and MMPP mark distribution is the two-point joint distribution
`(outbound, 60): 1/4` and `(inbound, 120): 3/4`. A frequency assertion below
means absolute error at most `0.03` for both joint cells. Completion cases also
require finite nondecreasing events in `[0, W]`, emission at a scripted event
exactly at `W`, natural completion only after a scripted next event above `W`,
and an incomplete result when each reliability guard fires first.

<table>
  <thead>
    <tr><th>Case</th><th>Fixed protocol</th><th>Independent expectation and tolerance</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>Poisson empirical</td>
      <td>Rate <code>4</code>, seeds <code>1103, 2207, 3301, 4409</code>;
      four complete <code>W = 4096</code> runs; first 12,000 generated IATs and
      marks from each seed</td>
      <td>Mean IAT <code>1/4</code> within 5%; empirical CDF at <code>1/4</code>
      within <code>0.04</code> of <code>1 - exp(-1)</code>; each run's mean rate
      within 8%; Poisson/MMPP mark frequencies and completion contract</td>
    </tr>
    <tr>
      <td>Markov Renewal</td>
      <td>Kernel <code>[[0.8, 0.2], [0.3, 0.7]]</code>, seeds
      <code>5101, 5209, 5303, 5413</code>, 20,000 transitions per seed;
      A is <code>(outbound,60)</code>, B is <code>(inbound,120)</code>;
      <code>r = 2</code>, <code>c_t = 1</code>; row-major A-to-A, A-to-B, B-to-A,
      B-to-B IAT tables <code>(1, 3)</code>, <code>(2, 4)</code>,
      <code>(3, 5)</code>, <code>(4, 6)</code></td>
      <td>Each transition cell within <code>0.025</code>; state occupancy within
      <code>0.03</code> of <code>(0.6, 0.4)</code>; each conditional mean within
      5%; exact scripted transition/source/global fallback choice and counter;
      joint marks <code>(outbound,60): 0.6</code> and
      <code>(inbound,120): 0.4</code> within <code>0.03</code>; completion
      contract</td>
    </tr>
    <tr>
      <td>Two-state MMPP</td>
      <td><code>q01 = 1</code>, <code>q10 = 3</code>,
      <code>lambda0 = 1</code>, <code>lambda1 = 9</code>; seeds
      <code>7103, 7207, 7309, 7411</code>; four complete
      <code>W = 4096</code> runs; first 10,000 arrival epochs and IATs from each
      seed</td>
      <td>Arrival-epoch mix within <code>0.03</code> of
      <code>(1/4, 3/4)</code>; time occupancy within <code>0.03</code> of
      <code>(3/4, 1/4)</code>; rate within 6% of <code>3</code>; adjacent-IAT
      covariance within <code>0.015</code> of the analytical
      <code>4/147</code>; Poisson/MMPP mark frequencies and completion contract</td>
    </tr>
    <tr>
      <td>Genetic neutrality and fairness</td>
      <td>Families sorted as Markov Renewal, MMPP, Poisson empirical; master
      seeds <code>4, 0, 6</code>; population <code>6</code>, two trial seeds
      <code>17, 29</code>, <code>W = 10</code>; per candidate/trial: 50,000
      packets, 64 MiB, and 30 fake-clock seconds</td>
      <td>Every family occupies every priority position once; registry and
      configuration permutations preserve priority, quotas, children, and
      winner; all-<code>0.5</code> and symmetric-invalid cases follow priority;
      under mixed weights, controlled Markov/MMPP/Poisson component scores
      <code>(0.4,...)</code>, <code>(0.8,0.7,0.9,0.8)</code>, and
      <code>(0.6,...)</code> make MMPP the unique winner in every input order</td>
    </tr>
    <tr>
      <td>Similarity weights</td>
      <td>Method order frame-size KS, IAT KS, ACF, multiscale rate; component
      scores <code>(0.2, 0.4, 0.6, 0.8)</code>; four one-hot vectors and mixed
      weights <code>(0.1, 0.2, 0.3, 0.4)</code></td>
      <td>Each one-hot aggregate equals its selected component exactly; mixed
      aggregate equals <code>0.6</code>; every zero-weight method still executes,
      retains diagnostics, and propagates its injected failure</td>
    </tr>
  </tbody>
</table>

Markov Renewal occupancy is calculated independently from the two-state balance
equations, and conditional means come directly from the declared finite tables.
The MMPP oracle uses the two-state generator and diagonal arrival-rate matrices
to calculate time occupancy, arrival-epoch initialization, rate, and the
adjacent-IAT moment; it is not a second simulator. Exact RNG-order, threshold,
fallback, boundary, and guard tests remain separate from the statistical rows.
The matrix is a bounded test suite, not a benchmark or general statistical
testing framework.

The priority expectations are the CPython 3.12.3 results of the exact temporary
`random.Random(master_seed).sample(...)` contract; the test also calculates
them directly rather than storing implementation output as an oracle.

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
5. Run a small heterogeneous population with all three families and nondefault
   operator values for every family. Require every family to remain represented
   and prove that crossover occurs only within one family, each child uses its
   own family settings, and cross-family reproduction forces mutation.
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
11. Round-trip portable and realized configurations with method weights
    `(1, 0, 0, 0)` and `(0.1, 0.2, 0.3, 0.4)`. Require all four mandatory method
    settings and diagnostics in both cases; realization may change only the run
    directory and declared bind-mount host-source paths.
12. In a relocated fresh clean clone with its own locked dependency environment,
    realize one portable configuration after feature preflight. Require every
    scientific/workload value, source commit/tree, `uv.lock`, CPython patch,
    scientific schema, image content identity, container mount target/mode, and
    mounted-input identity to match. Only Docker Engine and Compose versions
    (including supported Compose v2/v5 plugins), kernel release, checkout path, run-directory path, and host
    mount-source absolute paths may differ.
13. Independently change host architecture, target content ID, expected capture
    image ID, resolved capture image ID, capture-tool version, mounted-input
    hash, one scientific configuration value, and the scientific artifact
    schema. Reject every case before publication and name the first mismatching
    field. Also reject an old otherwise well-formed schema before fit resume,
    generation, or any stage reuse.
14. Exercise the complete [stage-compatibility matrix](SYSTEM.md#stage-compatibility).
    Capture reuse requires exact realized snapshot bytes, capture identity, and
    both capture files. Fit, generate, compare, and offline reconstruction
    require all equality fields in that table. Image and runtime compatibility
    are checked before reuse; permitted fresh-capture variation is never treated
    as capture-reuse equivalence.

These tests use temporary run directories and leave them available only when a
failure-report option requests preservation.

Items 12 and 13 are one portable-to-realized transfer proof and its incompatible
cases, not the multi-platform matrix reserved for a stronger evidence level.
The test compares only the compatibility fields owned by
[System](SYSTEM.md#stage-compatibility),
[Capture](CAPTURE.md#reproducible-capture-environment), and
[Development](DEVELOPMENT.md#reproducibility-review-and-accepted-evidence); no
unrelated host metadata participates.

### Canonical adverse-condition diagnostics

Table-driven injected tests cover the finite failure boundary. Every row asserts
the exact `kind`, `stage`, `detail`, `affected_evidence`, `evidence_state`,
`corrective_action`, `authority`, optional exact `status`, and absence of the
named reusable output. The injected detail names the failing field or resource;
compatibility rows name the first mismatch. Slash-separated artifact variants
are separate cases, not one sampled alternative.

Each outcome cell lists, in order: exact `kind`; owning `stage`;
`affected_evidence/evidence_state`; `authority`; exact `status` or `no status`;
`corrective_action`; and the reusable output that remains unpublished.

<table>
  <thead>
    <tr><th>Injected boundary</th><th>Canonical outcome</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>Invalid configuration or path</td>
      <td><code>configuration_invalid</code>; preflight; run evidence
      <code>not_published</code>; primary; no status; correct the named field or
      path; no run artifacts</td>
    </tr>
    <tr>
      <td>Docker unavailable</td>
      <td><code>docker_preflight_failed</code>; preflight; capture evidence
      <code>not_published</code>; primary; no status; restore Docker Engine and
      Compose availability; no capture pair</td>
    </tr>
    <tr>
      <td>Docker incompatible</td>
      <td><code>docker_preflight_failed</code>; preflight; capture evidence
      <code>not_published</code>; primary; no status; provide the named required
      Docker and Compose features; no capture pair</td>
    </tr>
    <tr>
      <td>Image unavailable</td>
      <td><code>docker_preflight_failed</code>; preflight; capture evidence
      <code>not_published</code>; primary; no status; make the named image
      reference available; no capture pair</td>
    </tr>
    <tr>
      <td>Image incompatible</td>
      <td><code>docker_preflight_failed</code>; preflight; capture evidence
      <code>not_published</code>; primary; no status; restore the declared image
      content identity and architecture; no capture pair</td>
    </tr>
    <tr>
      <td>Capture tool unavailable</td>
      <td><code>docker_preflight_failed</code>; preflight; capture evidence
      <code>not_published</code>; primary; no status; install the declared
      capture tool in the capture image; no capture pair</td>
    </tr>
    <tr>
      <td>Capture tool incompatible</td>
      <td><code>docker_preflight_failed</code>; preflight; capture evidence
      <code>not_published</code>; primary; no status; restore the declared
      capture-tool version; no capture pair</td>
    </tr>
    <tr>
      <td>Mount unavailable</td>
      <td><code>docker_preflight_failed</code>; preflight; capture evidence
      <code>not_published</code>; primary; no status; make the named host source
      available to Docker; no capture pair</td>
    </tr>
    <tr>
      <td>Mount incompatible</td>
      <td><code>docker_preflight_failed</code>; preflight; capture evidence
      <code>not_published</code>; primary; no status; correct the declared
      container target and mode; no capture pair</td>
    </tr>
    <tr>
      <td>Mounted input unavailable</td>
      <td><code>docker_preflight_failed</code>; preflight; capture evidence
      <code>not_published</code>; primary; no status; restore the named mounted
      input bytes; no capture pair</td>
    </tr>
    <tr>
      <td>Mounted input incompatible</td>
      <td><code>docker_preflight_failed</code>; preflight; capture evidence
      <code>not_published</code>; primary; no status; restore the declared
      mounted-input content identity; no capture pair</td>
    </tr>
    <tr>
      <td>Prerequisite unavailable</td>
      <td><code>docker_preflight_failed</code>; preflight; capture evidence
      <code>not_published</code>; primary; no status; make the named prerequisite
      available; no capture pair</td>
    </tr>
    <tr>
      <td>Prerequisite incompatible</td>
      <td><code>docker_preflight_failed</code>; preflight; capture evidence
      <code>not_published</code>; primary; no status; satisfy the named
      prerequisite compatibility contract; no capture pair</td>
    </tr>
    <tr>
      <td>Target exits <code>23</code></td>
      <td><code>target_failed</code>; capture; capture pair
      <code>diagnostic_only</code>; primary; exact status <code>23</code>;
      inspect target status and log; no reusable capture pair</td>
    </tr>
    <tr>
      <td>Capture exits <code>42</code> while target is active</td>
      <td><code>capture_failed</code>; capture; capture pair
      <code>not_published</code>; primary; exact status <code>42</code>;
      inspect capture status and log; no <code>reference.pcapng</code></td>
    </tr>
    <tr>
      <td>Capture is already stopped after natural target success</td>
      <td><code>capture_failed</code>; capture; capture pair
      <code>not_published</code>; primary; exact capture status <code>42</code>;
      inspect capture status without SIGINT or flush wait; no
      <code>reference.pcapng</code></td>
    </tr>
    <tr>
      <td>Workload timeout</td>
      <td><code>stage_timeout</code>; capture; capture pair
      <code>diagnostic_only</code>; primary; no status; correct timeout or
      workload; no reusable capture pair</td>
    </tr>
    <tr>
      <td>Flush timeout after natural target success</td>
      <td><code>stage_timeout</code>; capture; capture pair
      <code>not_published</code>; primary; no status; correct capture flush or
      budget; no <code>reference.pcapng</code></td>
    </tr>
    <tr>
      <td>Total-run timeout while validating after natural target success</td>
      <td><code>stage_timeout</code>; capture; capture pair
      <code>not_published</code>; primary; no status; increase total budget or
      reduce validation input; no <code>reference.pcapng</code></td>
    </tr>
    <tr>
      <td>User interruption</td>
      <td><code>interrupted</code>; capture; capture pair
      <code>diagnostic_only</code>; primary; exact status <code>130</code>;
      retry when ready; no reusable capture pair</td>
    </tr>
    <tr>
      <td>Malformed capture</td>
      <td><code>capture_malformed</code>; capture; capture pair
      <code>diagnostic_only</code>; primary; no status; correct the capture
      producer; no reusable capture pair</td>
    </tr>
    <tr>
      <td>Missing fitted model</td>
      <td><code>artifact_missing</code>; generate;
      <code>best_model.json/not_published</code>; primary; no status; rerun fit;
      no <code>generated.pcapng</code></td>
    </tr>
    <tr>
      <td>Changed reference during fit resume</td>
      <td><code>artifact_changed</code>; fit;
      <code>reference.pcapng/preserved</code>; primary; no status; recreate the
      capture pair in a new matching run; no checkpoint or
      <code>best_model.json</code> publication</td>
    </tr>
    <tr>
      <td>Foreign generated trace</td>
      <td><code>artifact_foreign</code>; compare;
      <code>generated.pcapng/preserved</code>; primary; no status; regenerate
      from the current fitted model; no <code>similarity.json</code></td>
    </tr>
    <tr>
      <td>Stale valid capture pair with another identity</td>
      <td><code>artifact_stale</code>; capture;
      <code>capture pair/preserved</code>; primary; no status; select its
      matching run or a new run directory; no capture replacement</td>
    </tr>
    <tr>
      <td>Corrupt checkpoint during fit resume</td>
      <td><code>artifact_corrupt</code>; fit;
      <code>checkpoint.json/preserved</code>; primary; no status; recreate fit in
      a new run directory; no <code>best_model.json</code></td>
    </tr>
    <tr>
      <td>Old checkpoint semantics during fit resume</td>
      <td><code>scientific_semantics_incompatible</code>; fit;
      <code>checkpoint.json/preserved</code>; primary; no status; refit under the
      current schema in a new run directory; no <code>best_model.json</code></td>
    </tr>
    <tr>
      <td>Old fitted-model semantics during generation</td>
      <td><code>scientific_semantics_incompatible</code>; generate;
      <code>best_model.json/preserved</code>; primary; no status; refit under the
      current schema; no <code>generated.pcapng</code></td>
    </tr>
    <tr>
      <td>Metric, sample, or numeric infeasibility</td>
      <td><code>metric_infeasible</code>; compare; <code>similarity.json</code>
      <code>not_published</code>; primary; no status; correct samples or
      settings; no <code>similarity.json</code></td>
    </tr>
    <tr>
      <td>Generation guard or deadline</td>
      <td><code>generation_incomplete</code>; generate;
      <code>generated.pcapng</code> <code>not_published</code>; primary; no
      status; correct limit or model; no <code>generated.pcapng</code></td>
    </tr>
    <tr>
      <td>Best-model collision during fit publication</td>
      <td><code>publication_collision</code>; fit;
      <code>best_model.json/preserved</code>; primary; no status; choose a new run
      directory; no replacement <code>best_model.json</code></td>
    </tr>
    <tr>
      <td>Accepted-bundle collision after a successful offline audit</td>
      <td><code>publication_collision</code>; publication; candidate accepted
      evidence bundle <code>not_published</code>; primary; no status; choose a new
      study ID; existing different accepted bundle preserved unchanged</td>
    </tr>
    <tr>
      <td>Similarity durability failure during compare publication</td>
      <td><code>publication_failed</code>; compare;
      <code>similarity.json/not_published</code>; primary; no status; correct
      storage and rerun compare; no <code>similarity.json</code></td>
    </tr>
    <tr>
      <td>Cleanup failure after success</td>
      <td><code>cleanup_failed</code>; capture; inventory
      <code>possibly_remaining</code>; primary; no status; remove the named
      project; no successful completion record</td>
    </tr>
    <tr>
      <td>Target status <code>23</code> plus cleanup failure</td>
      <td><code>target_failed</code>; capture; capture pair
      <code>diagnostic_only</code>; primary status <code>23</code>, secondary
      <code>cleanup_failed</code> with inventory <code>possibly_remaining</code>
      and no status; inspect target then remove project; no reusable capture</td>
    </tr>
    <tr>
      <td>Workload timeout plus induced target status <code>137</code></td>
      <td><code>stage_timeout</code>; capture; capture pair
      <code>diagnostic_only</code>; primary with no status, secondary
      <code>target_failed</code> status <code>137</code>; correct workload or
      timeout; no reusable capture</td>
    </tr>
    <tr>
      <td>Simultaneous flush and total-run timeout after target success</td>
      <td><code>stage_timeout</code>; capture; capture pair
      <code>not_published</code>; primary flush timeout with no status, secondary
      total-run timeout with no status; correct flush then total budget; no
      reusable capture</td>
    </tr>
    <tr>
      <td>Simultaneous target <code>23</code>, capture <code>42</code>, and total timeout</td>
      <td><code>target_failed</code>; capture; capture pair
      <code>not_published</code>; primary status <code>23</code>, secondary
      <code>capture_failed</code> status <code>42</code> and
      <code>stage_timeout</code> with no status; inspect target first, then
      capture and budget; no reusable capture</td>
    </tr>
  </tbody>
</table>

Equivalent candidate-invalid records retain the scientific fields in checkpoint
and history diagnostics and score zero; infrastructure failures still abort.
The capture rows implement the authority and precedence rules from
[Capture reliability](CAPTURE.md#reliability-behavior).

A small checked-in immutable fixture at
`tests/fixtures/diagnostics/failure-outcomes.jsonl` contains one credential-free
canonical record for each matrix row, including each unavailable and incompatible
Docker, image, capture-tool, mount, mounted-input, and prerequisite case. Strict
offline parsing reproduces the table expectations without Docker, network
access, an observability service, or a security subsystem. Secrets, credentials,
host usernames, and absolute local paths are forbidden in the fixture.

### Full-pipeline resume and reuse equivalence

One offline integration test runs `fit -> generate -> compare -> final
publication` both uninterrupted and with interruption immediately after a whole
evaluated-generation checkpoint. The resumed path must have identical
`family_priority`, repaired genes, candidate IDs, search RNG state, history,
winner, fresh simulation seed result, generated canonical events, component
diagnostics, aggregate, and final publication inventory. It also requires
byte-identical canonical JSON, CSV, and PCAPNG scientific artifacts, their
SHA-256 identities, and every cross-artifact lineage identity.

Only wall-clock timestamps/durations, process and temporary project identities,
absolute checkout/run/mount-source paths permitted by portable realization, and
additional interruption/resume records in `run.log` may differ. No other value,
byte, identity, or lineage edge may differ. A legacy scientific artifact fails
compatibility before entering this comparison.

A companion matrix validates and reuses capture, fit, generate, and compare
outputs one stage at a time. Each reused result must validate before reuse and
produce the same authoritative source identities and downstream lineage as a
fresh execution. Filename existence, a hash without checked bytes, or permitted
fresh-environment variation does not establish reuse equivalence.

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

## Accepted Validation Study evidence

A replacement study is accepted only when its Docker and Internet prerequisite
commands both ran successfully against the same source revision, tree,
`uv.lock`, CPython patch, and scientific artifact schema as the study. Their
bounded command lines, stdout, stderr, and JUnit records are retained. A
collection-only result, skipped test, older revision, or expected unavailable-
Docker diagnostic is not prerequisite evidence.

Every training run admitted to the protocol, including each primary, repeat, and
reproduction run, retains its complete strict nine-file run tree plus the
portable configuration, realized configuration, and both identities. All
training references used for fitting, bounds, candidate selection, or family
selection remain in the bundle whether or not the report discusses them
individually. The report identifies selection seeds separately from the fresh
simulation seed; the latter evaluates the winner on its training reference and
is not held-out evidence.

The protocol predeclares and then captures one genuine independent held-out
reference per workload after training rules are frozen and before results are
interpreted. A held-out reference is never used for fitting, bounds, candidate
selection, family selection, seed choice, or protocol amendment. For each
workload, the training-only rule selects one retained fitted model; that fixed
model is loaded without refitting or reselection, simulated over the held-out
`W` with the predeclared fresh simulation seed, and compared with the held-out
reference. Held-out capture metadata and PCAPNG, fixed-model generated PCAPNG,
comparison, configuration identities, and lineage are retained.

The report states training fit/selection, repeated-capture natural variation,
fresh-simulation behavior, and held-out results as four separate claims. It does
not relabel any training reference or fresh-seed generation as held-out. For a
controlled one-factor method-weight change, it states that only aggregate
contribution and possibly ranking change while component scores, diagnostics,
and mandatory execution remain fixed. It treats an invalid chromosome only as
infeasible under its declared genes, settings, and limits, not as evidence of
poor fit or model-family inferiority.

The accepted evidence bundle is a checked tree at
`examples/validation_study/evidence/<study-id>/`. It contains the complete run
trees, portable/realized pairs, held-out inputs and outputs, prerequisite
records, protocol-used transfer headers and external observations, environment
record, report inputs, and canonical manifest. The manifest orders normalized
relative paths by UTF-8 byte order and records each retained regular file's
logical owner, byte size, SHA-256, and lineage edges. The manifest itself is the
checked inventory root and is not recursively hashed inside itself; its Git blob
identity anchors its bytes. Symlinks, unlisted files, listed missing files,
duplicate normalized paths, and paths outside the bundle are rejected.

Its environment record contains the source commit/tree, `uv.lock`, CPython
patch, target and capture references/content IDs, capture-tool version, Docker
Engine and Compose versions, kernel release, host architecture, and the declared
compatibility decision. It contains no unrelated host inventory.

Acceptance requires every report-cited byte to be checked in the bundle. It may
not depend on ignored `runs/`, scratch directories, an original absolute path,
a local cache, a remote archive, or hashes whose bytes are unavailable. Ordinary
and failed runs remain ignored; this narrow checked bundle is not an archive
service.

### Bounded offline audit

From a clean clone with the locked CPython environment already available, the
acceptance command is:

```bash
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/<study-id>/
```

The command does not invoke Docker, open a network connection, call
`trafficlab run`, or fetch a missing byte. It fails if the locked environment is
not locally available rather than accessing the network. It validates the
manifest's exact inventory, regular-file types, sizes, and hashes; then parses
every retained TOML, canonical JSON/JSONL, CSV, and PCAPNG with strict production
codecs wherever they own the public format.

The offline audit independently reconstructs normalized references and each
`W`; checks portable/realized identities and the exact offline-reconstruction
compatibility row; validates checkpoint, history, family priority, winner,
model, and fresh simulation seed consistency; regenerates every deterministic
generated trace from the retained model, seed, window, and limits; and
recomputes all four mandatory component scores, weighted aggregates, natural-
variation values, training summaries, held-out summaries, and report arithmetic.
It validates every capture, configuration, model, generated trace, comparison,
summary, report, and manifest lineage relationship instead of trusting a
precomputed report value.

Tests copy the accepted bundle into a temporary relocated clean root, prohibit
Docker subprocesses and network calls, and require successful reconstruction.
Separate copies remove one listed file, corrupt one byte, import a valid artifact
from a foreign run, and substitute a valid same-format artifact with changed
lineage; each must fail with its canonical first-mismatch diagnostic and publish
no acceptance result. A bounded collision case first passes the candidate audit,
then attempts exclusive publication at an occupied study ID and requires the
canonical `publication_collision` outcome, no candidate publication, and the
existing different accepted bundle byte-for-byte unchanged. Existing strict
per-format tests retain the exhaustive corruption matrices, so the audit adds no
duplicate codec, similarity implementation, generic experiment framework,
recovery system, security system, or publication service.

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
that cover 100% of that function's executable lines and branches. Verify the
source range with targeted `pytest-cov` missing-line output; do not build a
custom per-function coverage framework. A missing meaningful integration path
still matters more than uninformative aggregate coverage.

## References

- [Development workflow](DEVELOPMENT.md) owns tool configuration and commands.
- [Docker capture lifecycle](CAPTURE.md) owns the Compose topology and official
  Docker references.
- [IETF PCAPNG format, active work in progress][pcapng-draft] defines the capture
  container format.

[pcapng-draft]: https://datatracker.ietf.org/doc/draft-ietf-opsawg-pcapng/
