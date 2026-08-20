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

`examples/schemas/scientific-artifact-v3/` contains 12 public Draft 2020-12
schemas totaling 146,738 bytes. Filenames come from the sorted
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

Three fresh post-correction full runs passed the unchanged gate. Their combined
multiscale/ACF median speedups were 19.37, 21.26, and 23.24; vector/scalar
median peak RSS ratio was 1.0 in every run. Normalization, IAT, and multiscale
agreement were exact. Selected-lag ACF maximum absolute error was
`4.336808689942018e-18`, below `1e-12`. The canonical JSON retains the later
run's complete raw values and recomputed medians.

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

- The explicit NumPy migration inventory contains 45 baseline executable lines
  nested below Python `for`/`while` loops and 5 after adoption: 88.89% reduction
  against a 25% gate.
- The disjoint Tasks 5–7 artifact-validation phases are `402→199`, `518→311`,
  and `562→479`. The aggregate is `1482→989`, a 33.27% reduction against a 30%
  gate. No owner path is counted in two phases.

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
and prepared `runs/scientific-stack`. Real Docker/Internet execution is part of
the source-bound external validation gate, not an ordinary offline test.

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

The accepted replacement Validation Study bundle is created only after the
implementation source snapshot is committed. Its `environment.json` is the
authority for the exact source commit/tree, Docker/Compose/kernel fields, image
identities, capture-tool version, CPython patch, schema, and lock identity. The
bundle retains nine real training captures, nine fixed-seed fresh simulations,
three independent held-out captures, bootstrap summaries, prerequisite command
evidence, lifecycle cleanup, lineage, and its manifest.

Acceptance requires a credential-free HTTPS prerequisite, real Docker capture,
exclusive publication under a new study ID, and this detached audit shape:

```bash
UV_OFFLINE=1 scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/<study-id>/ --repository .
```

The audit checkout must be a `git clone --no-local --no-hardlinks --no-checkout`
detached at the recorded source commit. Accepted evidence is copied into the
matching relative path as regular files. A later scientific source change must
fail binding rather than be treated as valid evidence.

The study remains finite descriptive evidence: three training captures and one
held-out capture for each of three traffic shapes against one public object. It
does not establish external generalization, causal mechanisms, universally
calibrated similarity, or superiority of one model family.
