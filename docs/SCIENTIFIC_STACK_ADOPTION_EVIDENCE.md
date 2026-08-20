# Scientific stack adoption evidence

This record binds the permanent NumPy, SciPy, Pydantic, and Hypothesis adoption
to reproducible machine-readable evidence. The MMPP-likelihood, pymoo, and
Scapy candidates remain rejected development-only probes; none entered the
installed package.

## Locked environment

The retained local measurements used CPython 3.12.3 on x86_64 WSL2 Linux. The
checked `uv.lock` is 104,376 bytes with SHA-256
`8cac309ef5cb0b278f21c504ca0e8c750fd147ae29d3bd6fa685c2f16f55554f`.
Locked versions are NumPy 2.5.2, SciPy 1.18.0, Pydantic 2.13.4, Hypothesis
6.165.10, pymoo 0.6.2, and Scapy 2.7.0. The benchmark JSON additionally binds
the exact source-file bytes, Python/runtime fields, kernel release, host
architecture, dependency versions, and lock identity used for its samples.

Synchronize and verify the runtime with:

```bash
uv sync --locked --all-groups
uv run --locked python --version
```

## Public artifact schemas

`examples/schemas/scientific-artifact-v3/` contains 13 public Draft 2020-12
schemas totaling 147,272 bytes. Filenames come from the sorted
`PUBLIC_ARTIFACT_MODELS` registry; every schema declares its filename as `$id`.
The generator rejects changed, missing, foreign, noncanonical, or incomplete
output.

```bash
uv run --locked python scripts/generate_artifact_schemas.py --check
```

## One-million-event differential benchmark

`examples/scientific_stack/benchmark.json` retains the raw benchmark. The input
contains exactly 1,000,000 events from `Generator(PCG64(20260819))`. Each scalar
and vector implementation ran in one excluded warm-up subprocess and five
measured fresh subprocesses. Timestamp normalization, IAT, multiscale packet
and exact-byte cells, and selected-lag ACF were derived independently.

The first full run honestly failed: scalar/vector equality passed, but the
combined vector path measured only 0.535 times scalar speed and equal RSS. The
raw timings isolated repeated conversion of an already-columnar trace into one
million `TraceEvent` objects. Focused RED tests reproduced that boundary. The
correction kept `TrafficTrace` columnar through normalization and metrics,
centered each ACF feature once, and accumulated multiscale cells from integer
array indexes. All defect-exposed functions then reached 100% executable-line
and branch coverage.

Three initial post-correction runs passed at 19.37x, 21.26x, and 23.24x. The
final columnar-core record passed at `21.187956620803675x`, from scalar and
vector combined medians `2.5359822059836006s` and `0.11968979601806495s`;
vector/scalar median peak RSS ratio was `1.0`. Normalization, IAT, and
multiscale agreement were exact. Selected-lag
ACF maximum absolute error was `4.336808689942018e-18`, below `1e-12`.

`--check` independently rebuilds the one-million-event PCG64 dataset and runs
the scalar and vector kernels once without timers or subprocess sampling. It
recomputes every agreement value and result digest, then compares them with all
retained warm-up/measured identities. Fabricated zero ACF error or all-zero
digests fail. Only explicit generation runs the one-warm-up plus five measured
fresh subprocess protocol. The scalar multiscale oracle implements the literal
`round`/`math.ulp` four-ULP rule independently and does not import or call the
production snapping helper.

```bash
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 15m --kill-after 10s -- \
  uv run --locked python scripts/benchmark_scientific_stack.py
uv run --locked python scripts/benchmark_scientific_stack.py --check
```

Timing is host-specific descriptive evidence, not a universal throughput
guarantee. The equality gate and locked dataset are portable; fresh performance
acceptance must retain the same protocol on the measured host.

## Exact source reduction

`examples/scientific_stack/code_reduction.json` is rebuilt from immutable Git
objects, not current file totals or prose estimates. It retains every qualified
function and exact counted line.

- The explicit NumPy migration inventory contains 398 baseline executable
  loop-and-validation lines and 287 after adoption: 27.89% reduction against a
  25% gate. It counts unique `ast.stmt` lines in named loop bodies plus every
  statement in named straight-line custom validation functions and retains each
  function's roles and exact lines.
- All three disjoint artifact-validation phases use the same unique AST
  executable-statement-line metric over explicit qualified functions. Their
  values are `402→199`, `518→311`, and `180→174`. The aggregate is `1100→684`,
  a 37.82% reduction against a 30% gate. No owner path is counted in two phases.

Tests and generated evidence are excluded. Cross-artifact arithmetic, lineage,
canonical bytes, duplicate keys, and publication policy remain intentionally
outside the removed local structural validation.

```bash
uv run --locked python scripts/measure_scientific_stack_reduction.py --check
```

## Distributable example

`examples/scientific_stack/experiment.toml` is a small complete workflow using
master seed 20260819, final seed 97, the PCG64 production path, one trial seed,
one genetic generation, and all three production families. It uses the locked
curl target image, a credential-free HTTPS range request, equal mandatory
method weights, and bounded trial/final generation limits. Its SHA-256 is
`ec5d58400c9549d97f55c634298dbfb4771455b5de174bae4172e8afe8642033`.

The bounded configuration-only production preflight completed with status zero
and prepared `runs/scientific-stack`. A bounded real Docker/Internet
`trafficlab run` then completed the full workflow with all three families in
the checkpoint. It selected `markov_renewal`, reported selection fitness
`0.712402`, parsed 84 reference packets, generated 48 packets, and published
aggregate score `0.7303583634840887` with the exact nine documented run files.
The owned Compose project had zero labeled containers, networks, or volumes
afterward; the temporary exact capture-image tag was removed without global
Docker cleanup.

Durable evidence is in
[`example_run.json`](../examples/scientific_stack/example_run.json), with the
nine exact companion artifacts under `example_run_artifacts/`. It records the
actual bounded command, URL, resource limits, exit status, observed filesystem
completion time and provenance, source commit/tree, dirty-state limitation,
lock/config/image identities, environment, every artifact hash/size, winner,
all families, packet counts, aggregate/component scores, and empty exact-label
cleanup inventory. `scripts/check_scientific_stack_example.py --check` strictly
reparses the artifacts and recomputes every verifiable fact; it does not rebind
the run to a later clean commit.

## Optional probe decisions

The shared runner generates or checks all probes in the fixed order MMPP,
pymoo, Scapy:

```bash
uv run --locked python scripts/run_scientific_stack_probes.py --probe all --check
```

The strict retained decisions are:

- MMPP likelihood: `reject`; failed gate `held_out_likelihood`; production
  unchanged.
- pymoo: `reject`; failed gates `exact_public_state_replay` and
  `production_loc_reduction`; the basic generational strategy remains
  production.
- Scapy: technical `reject`; failed writer-equivalence, malformed-input,
  100,000-frame time, and 1,000,000-frame time gates. Production adoption is
  separately blocked because GPL compatibility was not decided by an
  authorized human. Scapy remains development-only and production is unchanged.

The final retained Scapy generation measured candidate/production median wall
ratios of 14.156 at 100,000 frames and 13.473 at 1,000,000 frames. Median peak
RSS ratios were 1.0 and 0.607 respectively. `--check` deterministically replays
the functional inventory and validates policy, source, commands, raw-sample
identities, medians, gates, and decisions. It does not claim host-dependent raw
timing bytes are reproducible or silently rerun the expensive benchmark; new
timing authenticity requires an explicit full generation, which was run twice.

Rejected gates were not relaxed after results. Synthetic probe results do not
claim real-traffic ground truth, optimizer superiority, general MMPP recovery,
or a license determination.

## Real-program validation and audit

The accepted replacement is
`examples/validation_study/evidence/2026-08-20-stack-adoption-r4/`. It binds
source commit `a71d74b7b2dc27cea0a9eb00c375e510a0d7acbf`, tree
`0e11527885623448fb03604dbf9f6bb2b4634dfd`, schema 3, CPython 3.12.3,
Docker 29.7.2, Compose 5.5.0, the exact image IDs, and the checked lock. Its
manifest SHA-256 is
`433ff85b1921051ea810f12bec034e57f0a41d71b13e56edc7f1f93bff5a296d`
and lists 231 retained paths. The bundle retains nine real training captures,
nine fixed-seed fresh simulations, three independent held-out captures,
bootstrap summaries, prerequisite command evidence, lifecycle cleanup,
lineage, and its manifest.

The cold prerequisite Docker matrix passed 18/18 tests and the explicit
credential-free HTTPS smoke passed 1/1. Every runtime and selection-fitness
training summary includes a 95% percentile-bootstrap interval with 10,000
resamples, seed 20260819, PCG64 initial state, sample size, statistic, method,
and confidence level. The auditor independently recomputes those fields.

Attempts r1 and r2 were consumed by checkout-local Hypothesis cache failures.
r3 was published under its then-current schema but was found to omit the
required bootstrap records; its complete bytes were moved to ignored
`preserved-published-nonfinal` study storage and were not committed. Producer,
schema, auditor, fixtures, and documentation were corrected before the entirely
fresh r4 attempt. No source, capture, or failed study ID was reused.

Acceptance requires a credential-free HTTPS prerequisite, real Docker capture,
exclusive publication under a new study ID, and this detached audit shape:

```bash
UV_OFFLINE=1 scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/2026-08-20-stack-adoption-r4/ \
  --repository .
```

The audit checkout must be a `git clone --no-local --no-hardlinks --no-checkout`
detached at the recorded source commit. Accepted evidence is copied into the
matching relative path as regular files. The executed detached clone had no Git
object alternates, evidence symlinks, or evidence files with link count above
one; its bytes matched the publisher and the audit accepted all 231 paths. A
later scientific source change must fail binding rather than be treated as
valid evidence.

The study remains finite descriptive evidence: three training captures and one
held-out capture for each of three traffic shapes against one public object. It
does not establish external generalization, causal mechanisms, universally
calibrated similarity, or superiority of one model family.
