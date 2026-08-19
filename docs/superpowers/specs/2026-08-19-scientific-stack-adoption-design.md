# [DESIGN-1-61e58ed7] Scientific Stack Adoption Design

## [SECTION-1-f5d5c185] Source, goal, and authority

This design turns the repository-local `TASK.md` scientific-library assessment
into an implementable system design. The source brief is intentionally ignored
by Git; the reviewed input used for this design has SHA-256
`091abcfd538548116adc812189e94f5c82916ffac7d0b4318ab67dd18402dfed`.

The goal is to replace generic hand-written array, statistical, and structural
validation work with NumPy, SciPy, and Pydantic while retaining Trafficlab's
scientific definitions, canonical artifact bytes, lineage rules, resource
bounds, and one-process prototype architecture. Hypothesis supplies a
deterministic development safety net. MMPP likelihood, pymoo, and Scapy remain
bounded adoption probes until their explicit gates pass.

The approved documents in `architecture/` remain authoritative whenever the
library assessment is broader than an existing scientific definition. In
particular, the implementation must preserve:

- the nearest-rank IAT diagnostic quantile, even though NumPy Type 7 quantiles
  are used for Markov-renewal frame-size bin boundaries;
- the NIST whole-series-mean autocorrelation formula and constant-series zero
  convention;
- exact multiscale boundary snapping and exact normalized-L1 accumulation;
- arrival-epoch MMPP initialization and explicit terminal censoring;
- all four similarity methods at every weight, including zero weight; and
- one Python process, Docker Compose CLI orchestration, two production capture
  containers, and classical models only.

## [SECTION-2-dbee84f6] Chosen migration approach

Three approaches were considered:

1. A big-bang rewrite would convert traces, RNGs, models, metrics, artifacts,
   and optimization together. It offers a short-lived single representation but
   makes differential diagnosis and artifact regeneration too risky.
2. A layered migration introduces a columnar trace with compatibility adapters,
   proves each numerical replacement against independent scalar oracles, then
   consolidates schemas and evaluates optional libraries behind narrow probes.
3. A proof-of-concept-first migration would evaluate every library before
   changing production. It minimizes early production churn but postpones the
   NumPy/SciPy/Pydantic benefits that are already sufficiently justified.

The design chooses the layered migration. The permanent stack is adopted in the
order Hypothesis safety net, NumPy trace, NumPy/SciPy kernels, and Pydantic
artifacts. The MMPP, pymoo, and Scapy probes then run against stable typed
boundaries. Docker cleanup is simplified last so failures during the scientific
migration retain the current mature cleanup evidence.

Each layer lands as a coherent local commit after RED/GREEN tests and an
independent review. Compatibility adapters are deleted once the final production
consumer has migrated; they do not become a second public trace or artifact API.

## [SECTION-3-6a54d673] Dependency and reproducibility policy

The runtime dependency group gains NumPy and SciPy. Pydantic remains the sole
schema framework. The development group gains Hypothesis. pymoo 0.6.2 and Scapy
2.7.0 are development-only probe dependencies and must not be imported by the
installed `trafficlab` package unless a later reviewed adoption decision moves
them into runtime scope.

`uv` remains the only dependency interface. `uv.lock` records exact versions,
and accepted evidence records the lock identity and the resolved versions of
NumPy, SciPy, Pydantic, Hypothesis, pymoo, and Scapy. No pandas, Polars, PyArrow,
Docker SDK, psutil, alternate JSON codec, Node.js application dependency, or
second orchestration path is introduced.

All new stochastic production code owns an explicit named NumPy bit generator:
`numpy.random.PCG64`. It constructs `numpy.random.Generator(PCG64(seed))`,
records the bit-generator name and JSON-compatible state wherever resumability
requires it, and never calls module-global `numpy.random` functions or
`default_rng`. Draw order, dtype, shape, and endpoint semantics are part of the
locked reproducibility contract. A migration that changes production draws
increments the global scientific artifact schema and regenerates deterministic
fixtures rather than pretending to preserve legacy bytes.

Hypothesis uses one registered locked profile with `derandomize=True`,
`database=None`, `deadline=None`, and `max_examples=100`. Acceptance runs do not
read or write a mutable example database. A minimized scientific boundary case
found by Hypothesis is promoted to a literal `@example` or ordinary regression
test before the originating defect is closed.

## [SECTION-4-c12e8951] Canonical columnar trace

The scientific core receives one `TrafficTrace` value. It owns three
one-dimensional, equal-length NumPy arrays:

| Column | dtype | Meaning |
| --- | --- | --- |
| `timestamps` | `numpy.float64` | finite, nonnegative, nondecreasing seconds |
| `directions` | `numpy.uint8` | `0` outbound, `1` inbound, no other value |
| `frame_lengths` | `numpy.uint32` | strictly positive captured frame lengths |

Construction always copies input data into owned C-contiguous arrays, validates
shape, dtype, finiteness, ordering, direction domain, equal lengths, and caller
minimum length, then sets every array's writable flag to false. Public access
never returns a writable alias. Inputs whose integer length cannot be represented
as `uint32` fail before conversion; values are never silently wrapped.

`TrafficTrace.from_events(iterable)` and `TrafficTrace.to_events()` are the only
general compatibility boundary for the existing immutable `TraceEvent` record.
PCAPNG parsing, packet inspection, and canonical artifact encoding may produce
or consume event records at the boundary, but normalization, fitting,
generation, comparison, and validation-study analysis pass `TrafficTrace` in
memory. The trace exposes typed length, slicing that returns another owned
read-only trace, IATs via `numpy.diff`, and direction masks without duplicating
validation in every consumer.

Reference normalization subtracts the first timestamp vectorially and returns
the positive closed observation window. Generated alignment subtracts its first
timestamp and retains the closed mask `timestamp <= W`. Timestamp agreement
with parsed captures is evaluated against the interface's declared PCAPNG
resolution; no additional rounding is introduced.

## [SECTION-5-42cdfd8c] NumPy numerical migration

NumPy replaces generic scalar storage and counting work while Trafficlab keeps
all domain formulas and validation:

- `numpy.diff` derives IATs and retains zeros;
- `numpy.quantile(..., method="linear")` implements Hyndman-Fan Type 7 only
  where the Markov-renewal estimator requires Type 7 boundaries;
- `numpy.searchsorted` or `numpy.digitize` encodes frame-size states with the
  architecture's exact boundary convention;
- `numpy.bincount` counts directions, marks, transitions, and multiscale cells;
- vector masks split direction-specific values without Python event loops; and
- centered dot products compute selected autocorrelation numerators and the
  shared denominator under the documented NIST formula.

The multiscale implementation computes each bin count and every snapped bin
index with the existing four-ULP scalar boundary rule before vectorized
accumulation. It retains exact integer packet/byte cell values and the existing
exact normalized-L1 accumulator, rather than replacing that accumulator with a
floating NumPy reduction. This prevents a performance migration from changing
the metric.

Model estimators retain Trafficlab-specific empty-state fallbacks, smoothing,
mark distributions, diagnostics, and reliability guards. Vectorization may
replace loops only after fixed hand calculations and property-generated traces
agree with an independent scalar oracle within `1e-12`, or exactly for integer
counts and encoded states.

## [SECTION-6-c3534296] SciPy statistics and uncertainty

Two-sample KS uses `scipy.stats.ks_2samp(left, right).statistic` after the
Trafficlab boundary validates nonempty finite samples. The result is checked
against the retained independent merged-ECDF oracle on fixed tied samples and
Hypothesis-generated samples. Production diagnostics contain the statistic,
counts, extrema, medians, zero counts, and configured quantile only. They never
store, display, or interpret SciPy's p-value.

Autocorrelation remains the architecture-defined selected-lag estimator. NumPy
centered dot products are preferred over a full correlation sequence because
only configured lags are needed; SciPy does not redefine the estimator.

Research reports add reproducible 95% percentile bootstrap confidence intervals
for declared scalar summaries. The fixed protocol is
`scipy.stats.bootstrap(..., confidence_level=0.95, n_resamples=10_000,
method="percentile", rng=Generator(PCG64(seed)))`. Each report records the
seed, bit-generator name, method, confidence level, resample count, statistic,
sample size, and lower/upper bounds. Bootstrap results support uncertainty
description only; they do not turn similarity scores into calibrated
hypothesis tests or ground truth.

## [SECTION-7-58a8fdab] Pydantic artifact consolidation

Every persisted JSON artifact receives a strict frozen Pydantic model or a
discriminated union of such models. `ConfigDict(extra="forbid", frozen=True,
strict=True, allow_inf_nan=False)` is the shared policy. Constrained fields and
model validators enforce local shape and arithmetic; discriminators select
model family, diagnostic method, failure kind, and study record variant.

The public schema set covers:

- capture metadata and canonical failure outcomes;
- best models and every family-specific fitted payload;
- checkpoint population, candidates, trials, history, and RNG state;
- similarity methods, diagnostics, aggregate result, and input identities;
- environment, prerequisite, manifest, lineage, lifecycle, protocol, and
  validation-study report records.

One registry exports `model_json_schema()` for every public root artifact and a
deterministic command writes their JSON Schemas under
`examples/schemas/scientific-artifact-v3/`. Family- and method-specific schemas
are reachable through discriminated root schemas rather than a parallel codec
framework.

Pydantic does not own duplicate-key detection, canonical JSON key order,
canonical byte rendering, hashes, atomic publication, cross-artifact lineage,
or recomputation of trusted-looking totals. JSON bytes first pass the existing
duplicate-key hook, then Pydantic structural validation, then Trafficlab
cross-artifact validation. Rendering constructs the established canonical
document from validated models and verifies round-trip equality before
publication.

The scientific artifact version increments from 2 to 3 when the new NumPy RNG
and representation are adopted. Older best models and checkpoints fail with the
existing explicit refit instruction. Artifact implementation changes that do
not alter scientific semantics do not create additional unrelated version
numbers.

## [SECTION-8-98118868] MMPP likelihood probe

The SciPy MMPP probe is test-only and cannot silently replace the production
fitter. For rates `(q01, q10, lambda0, lambda1)`, it constructs

```text
Q  = [[-q01, q01], [q10, -q10]]
L  = diag(lambda0, lambda1)
D0 = Q - L
D1 = L
```

The initial row vector is the rate-weighted arrival-epoch distribution already
defined by `architecture/traffic_models/mmpp.md`. For each observed IAT `u`,
the forward recursion multiplies by `scipy.linalg.expm(D0 * u) @ D1`, rescales
by its positive finite row sum, and accumulates the logarithm of that scale.
After the last arrival it multiplies by `expm(D0 * terminal_silence) @ 1` so
right censoring to `W` remains explicit.

Optimization uses transformed unconstrained coordinates that decode to finite
positive `q01`, `q10`, `lambda0`, and gap-ordered `lambda1 > lambda0`, with
declared finite bounds. A bounded SciPy optimizer receives deterministic starts
and evaluation limits. The probe reports likelihood, decoded parameters,
termination, evaluation count, and seed; it is never accepted solely because an
optimizer reports success.

Acceptance requires agreement with literal two-state hand calculations, finite
likelihood for predeclared extreme valid rates, synthetic recovery across fixed
seeds, and held-out likelihood or existing similarity no worse than the current
simulation-distance fitter at the same evaluation count. Failure of any gate is
a documented rejection result, not a reason to weaken the gate or change the
production MMPP semantics.

## [SECTION-9-dfae305e] pymoo optimizer probe

The pymoo 0.6.2 probe runs one independent optimizer per enabled traffic-model
family. It does not create one mixed categorical family search. Every family
receives the same initial evaluation budget, observation window, trial seeds,
generation limits, and similarity weights. Execution is sequential and
round-robin only when exercising quick-threshold arbitration; family champions
are compared on predeclared fresh seeds after all minimum budgets are consumed.

The adapter maps Trafficlab's continuous, integer, and bounded genes to public
pymoo variable and algorithm interfaces. Trafficlab retains candidate
classification, evaluation caching, fitness construction, family fairness,
diagnostics, and artifact publication. Known continuous and mixed-integer
functions prove basic optimizer behavior before traffic models are involved.

Opaque dill checkpointing is prohibited. A passing adapter must extract through
documented public state all population genes, objective values, evaluation
count, generation/termination state, configuration, library version, and named
RNG state into strict Trafficlab records, then resume to a trial history
identical to an uninterrupted locked run. If exact public-state replay cannot be
demonstrated, the probe is rejected and pymoo remains development-only.

Production adoption is allowed only when the replay, fairness, cache, known
optimum, and repeatability gates pass and the resulting project-owned optimizer
implementation is at least 40% smaller than the current genetic subsystem
without removing required diagnostics. Otherwise the existing basic
generational strategy remains authoritative.

## [SECTION-10-bf9e944e] Scapy PCAPNG probe

The Scapy 2.7.0 probe is a typed development-only adapter that reads and writes
checked PCAPNG fixtures and converts them to the same `TrafficTrace` boundary as
the production codec. It exercises Ethernet, IPv4, IPv6, ARP, timestamp
resolution, malformed input, exactly one supported interface, source-MAC
direction classification, frame lengths, closed observation windows, and
per-packet deadline checks.

The probe does not alter production imports or artifact bytes. It compares
Scapy output with Trafficlab's canonical trace, not with incidental packet
object structure. Local type protocols isolate Scapy's dynamic surface so
strict Pyright covers the adapter without broad `Any` or blanket ignores.

GPL-2.0 is an explicit adoption gate. This design resolves only the probe: Scapy
is development-only, no Scapy code is copied, and no production module imports
it. Moving Scapy to runtime scope requires a separate recorded compatibility
decision. Even with that decision, adoption also requires the 100,000- and
1,000,000-frame time and peak-memory comparisons to show no material regression
and every malformed/timestamp/deadline test to pass. A failed gate retains the
current production codec.

## [SECTION-11-d0139420] Docker cleanup simplification

Capture continues to use Docker Compose CLI with a unique project name per
run/test. The production `finally` path issues one bounded
`compose down --volumes --remove-orphans` for the owned project and preserves
the existing primary-error arbitration: cleanup failure is primary only when no
earlier stage failure exists, otherwise it is attached as secondary context.

Production cleanup no longer implements a multi-step fake resource-state
machine. Command construction, absolute deadline handling, local process
termination/reaping, and error arbitration retain unit tests. Label-based
container/network/volume inventory and leak assertions move to the dedicated
Docker lifecycle fixture and one session teardown sweep, where Docker state is
real and resource ownership can be observed.

No global Docker cleanup is allowed. Every cleanup target is an explicit
validated project name, and bounded best-effort recovery may touch only
resources carrying that exact Compose project label.

## [SECTION-12-9d0a3af1] End-to-end data flow

The resulting production flow is:

```text
PCAPNG + capture metadata
  -> strict boundary parse
  -> owned read-only TrafficTrace
  -> NumPy model features and SciPy statistics
  -> independent per-family fit/generate/evaluate
  -> strict Pydantic artifact models
  -> canonical bytes + identities + atomic publication
```

The optional probe flow branches after the stable typed boundary:

```text
TrafficTrace -> MMPP likelihood evidence
TrafficTrace -> per-family pymoo evidence
PCAPNG <-> Scapy adapter -> differential trace evidence
```

Probe output is checked evidence, never a hidden runtime selection. Production
behavior changes only through an explicit reviewed adoption commit whose
fixtures, schema version, and architecture text change together.

## [SECTION-13-3f6ecc86] Failure behavior

Boundary failures remain `TrafficlabError` values with actionable corrective
text. NumPy dtype/shape/domain failures are translated at `TrafficTrace`
construction. Pydantic `ValidationError` details are normalized deterministically
and never expose unstable repr text. SciPy nonfinite results, failed matrix
scaling, optimizer exhaustion, or invalid confidence intervals become explicit
scientific-validation or invalid-candidate outcomes according to the owning
stage; they are not clamped into plausible values.

Optional dependency probes report pass, reject, or environmental blocker with
the exact command, dependency lock, inputs, and observed result. A probe cannot
make ordinary tests depend on the public Internet. Docker and Internet
validation remain explicit bounded external gates.

## [SECTION-14-d9a1e2e0] Test strategy

Every production behavior follows test-driven development: a focused test names
the break it catches, is observed failing for that reason, and only then receives
the minimal implementation. Property tests exercise real parsers, schemas,
traces, and numerical functions; mocks are limited to external clocks,
processes, or network/Docker boundaries.

The test matrix contains:

- unit tests for trace ownership, immutability, dtype/domain/order validation,
  normalization, IATs, state encoding, counts, quantiles, KS, ACF, multiscale
  cells, bootstrap metadata, every schema root, duplicate keys, arithmetic, and
  RNG state;
- Hypothesis properties for finite ordered traces, PCAPNG block lengths,
  padding/endian/options, schema documents, gene coordinate round trips,
  checkpoint render/parse, numerical oracle equivalence, and artifact
  publication/recovery state transitions;
- in-process integration tests spanning PCAPNG to `TrafficTrace`, model fit and
  generation, comparison publication, checkpoint resume, schema generation,
  validation-study reporting, and each optional probe;
- Docker integration tests for real unique-project cleanup, volumes, orphans,
  failure arbitration, and the complete capture/run path; and
- Internet validation using an explicit credential-free HTTPS endpoint.

The non-Docker package retains at least 90% branch-aware coverage. A unit test
that exposes a defective function must cover 100% of that function's executable
lines and branches before the fix closes. Existing hand calculations and the
scientific validation matrix remain; generated tests supplement rather than
replace them.

## [SECTION-15-ba277b6a] Deterministic performance and reduction gates

The checked benchmark protocol generates 1,000,000 events from `PCG64(20260819)`
with timestamps as a cumulative exponential sample, alternating Bernoulli
directions, and bounded integer frame lengths. It runs each isolated benchmark
case in a fresh subprocess, records wall time and process peak RSS, and compares
the scalar baseline with the production NumPy implementation for normalization,
IAT, multiscale, and selected-lag ACF work.

The NumPy migration passes when all results agree within `1e-12` and the
combined multiscale/ACF case is at least 3 times faster or uses at most half the
peak memory. The checked evidence records machine, OS, Python, dependency lock,
commands, repetitions, raw samples, median, and decision. Timing acceptance uses
the median of five post-warmup subprocess runs.

The deterministic Hypothesis selection is executed twice from empty temporary
state and must report the same collected cases and outcomes. Its addition may
increase the median Fast-gate wall time by no more than 20% against the recorded
pre-adoption baseline on the same host and checkout configuration.

A source-measurement script records executable lines in the named migrated
functions before and after adoption. The NumPy trace/numerical custom
loop-and-validation total must fall by at least 25%. Manual parse/type/key
validation in the migrated artifact modules must fall by at least 30%. Generated
schemas, tests, comments, blank lines, and probe code do not count as removed
production validation.

## [SECTION-16-da38b0f9] Fixtures, examples, and evidence

Checked-in deterministic assets include:

- small canonical trace arrays and PCAPNG differential fixtures;
- tied KS, Type 7 quantile, transition-count, ACF, and multiscale oracles;
- valid and corrupt scientific artifact v3 fixtures plus generated JSON Schema;
- MMPP hand-likelihood, extreme-rate, recovery, and equal-budget comparison
  cases;
- pymoo known-optimum, replay, family-budget, and trial-history records;
- Scapy trace-equivalence and benchmark records; and
- a distributable example configuration whose locked seeds exercise the full
  production workflow.

`docs/SCIENTIFIC_STACK_ADOPTION_EVIDENCE.md` records reproducible commands and
links to machine-readable JSON evidence. Accepted real-program evidence is a new
manifest-bound validation-study bundle produced from the final source commit,
including Docker and Internet prerequisites, real captures, held-out results,
bootstrap intervals, dependency versions, and offline audit output. Evidence is
not accepted from a dirty tree or a later checkout whose source identity differs.

Fixture generators retain `--check` modes and deterministic ordering. The
fixture manifest is regenerated only through its project generator; hashes are
never hand-edited.

## [SECTION-17-2ef3ddc0] Documentation, rollout, and completion gates

Architecture documents are updated with stable behavior as each production
layer lands: `SYSTEM.md` owns the columnar trace flow, traffic-model documents
own NumPy RNG and estimator semantics, similarity documents own SciPy/NumPy
kernels without changing formulas, `DEVELOPMENT.md` owns locked commands and
dependencies, and `TESTING.md` owns the new evidence obligations. Probe plans,
progress, benchmark status, and adoption ledgers remain outside `architecture/`.

Final acceptance requires all of the following on the same clean committed
tree:

- locked sync, Ruff formatting/check, strict Pyright, bounded Ordinary, and
  branch-aware Coverage gates pass;
- every deterministic generator `--check` and the offline validation-study
  audit pass;
- the combined serial Docker/Internet gate runs successfully on a capable host;
- every permanent-stack differential, performance, code-reduction, schema,
  reproducibility, and uncertainty requirement above passes;
- each optional probe either passes all adoption gates or records a reproducible
  rejection without entering production;
- an independent phase review and final whole-branch review have no Critical or
  Important findings; and
- all source, tests, integration tests, Docker tests, example configuration,
  fixtures, schemas, and evidence are retained in coherent local commits with a
  clean worktree.

Remote push, publication, merge, and destructive external cleanup remain outside
this design's authorization.
