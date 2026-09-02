# Required Traffic Models and Similarity Methods Design

**Status:** Approved in conversation on 2026-09-02

## Purpose

Implement every traffic model and similarity method classified as `Required`
in `architecture/CANDIDATES.md`, while preserving Trafficlab's one-process,
classical-model architecture and shortening the feedback loop during
development.

The change adds four traffic-model families:

- packet-level Hidden Markov Model;
- Markov packet-train model;
- Autoregressive Conditional Duration model;
- non-homogeneous Poisson process.

It also adds four weighted similarity methods:

- two-sample Cramér--von Mises;
- Anderson--Darling;
- Jensen--Shannon divergence;
- approximate joint Maximum Mean Discrepancy.

Three additional required methods are final-comparison diagnostics rather than
genetic-fitness components:

- Fano/Allan dispersion curves;
- transition-matrix fidelity;
- classical classifier two-sample test.

Implementation occurs on branch
`feature/required-models-similarity-v5` in the isolated workspace
`.worktrees/required-models-similarity-v5`. The main checkout remains available
for stable work and release tags.

## Goals

- Expand model-family competition from three to seven closed built-in
  families.
- Expand weighted genetic fitness from four to eight methods.
- Publish three additional post-fit diagnostics without invoking them during
  genetic evaluation.
- Retain deterministic PCG64 generation, complete-window semantics, strict
  reliability guards, typed artifacts, and reproducible fitting.
- Make every scientifically consequential bin, state, kernel, optimization,
  and fallback rule explicit and serializable.
- Update the owning architecture documentation with each implemented
  algorithm rather than deferring documentation until the end.
- Use small, medium, and big verification tiers so incomplete milestones do
  not trigger unnecessarily expensive suites or experiments.
- Validate development behavior against a bounded derivative of
  `dumps/moutai-stock-price-response-success`, then perform broader experiments
  only after coherent milestones pass.

## Non-goals

- Dynamic model or metric plugins
- Neural models, diffusion models, wavelets, or optimal transport
- Packet, flow, or application replay
- Payload, IP, transport, flow, or application-protocol generation
- Automatic migration of old scientific artifacts
- Evolving arbitrary HMM, MAP, kernel, or optimizer matrices directly in the
  genetic algorithm
- Claiming that a short development experiment identifies the true traffic
  mechanism or yields a best production model
- Running the full release, Docker, or big-experiment matrix after every small
  implementation increment

## Scientific artifact compatibility

The global scientific artifact schema becomes version 5. The reason is not
only the additional fitted-model payload variants: similarity settings,
genetic trial records, checkpoint results, final comparison artifacts, and
dashboard-consumed diagnostics change their scientific meaning and exact
shape.

Schema-4 checkpoints and best models are rejected before resume, generation,
or stage reuse with an explicit refit instruction. There is no migration path
or compatibility adapter. Historical evidence remains immutable and continues
to describe the implementation that created it.

Schema 5 retains one top-level final aggregate and separates weighted fitness
from post-fit evaluation:

```text
similarity.json
├── aggregate_score
├── methods
│   ├── frame_size_ks
│   ├── iat_ks
│   ├── autocorrelation
│   ├── multiscale_rate
│   ├── cramer_von_mises
│   ├── anderson_darling
│   ├── jensen_shannon
│   └── approximate_mmd
└── postfit_diagnostics
    ├── fano_allan
    ├── transition_matrix
    └── classical_c2st
```

Every weighted method retains a score, configured weight, and typed
diagnostics. Its weights are finite, nonnegative, and sum to one across all
eight methods. A zero-weight method still executes and validates, preserving
the established semantics.

Post-fit diagnostics have a score and typed diagnostic payload but no genetic
weight. Genetic trial and checkpoint records contain only the eight fitness
methods. Only the final comparison stage evaluates and publishes the three
post-fit diagnostics.

## Shared model-family contract

All four families implement the existing `ModelFamily` boundary:

```text
repair(chromosome, bounds) -> canonical genes
fit(reference, genes) -> fitted model
generate(fitted, seed, W, guards) -> complete canonical trace
dump_fitted(fitted) -> strict JSON-compatible payload
load_fitted(payload) -> validated fitted model
```

The closed registry expands to seven explicit singleton families. There is no
dynamic discovery or plugin abstraction. Each new family uses one small
integer structural coordinate:

| Family | Structural gene | Initial supported range |
|---|---|---|
| `packet_hmm` | hidden state count | 2 through 4 |
| `markov_packet_train` | train-length state cap | 3 through 8 |
| `acd` | equal AR and duration order | 1 through 3 |
| `nhpp` | piecewise-constant intensity bin count | 2 through 16 |

The family interface declares coordinate kinds alongside gene names. This
replaces family-identity checks for Markov Renewal's integer coordinate and
allows generic coordinate encoding, decoding, repair, serialization, and
mutation without adding a generic latent-model framework.

Scientific parameters are estimated deterministically inside `fit` using
reference data, not encoded as large chromosomes. Each estimator has fixed
iteration limits, convergence rules, initialization, and failure diagnostics.
NumPy and the already locked SciPy dependency are sufficient; no HMM,
time-series, or machine-learning dependency is added.

Every generator:

- emits the conditioned first packet at `t=0` under a documented convention;
- owns one local `numpy.random.Generator(PCG64(seed))`;
- documents and tests exact scalar draw order;
- emits only finite nondecreasing timestamps inside `[0, W]`;
- checks packet, byte, and wall-time guards before and after stochastic work;
- distinguishes natural complete-window termination from incomplete guard
  exhaustion;
- uses joint direction/frame-length marks, conditional on the strongest
  supported state without inventing unobserved payload semantics.

## Packet-level HMM

The observation sequence excludes the conditioned first packet. Each later
packet becomes one categorical observation formed from:

- the preceding IAT bin, including an explicit zero-IAT category;
- canonical outbound/inbound direction;
- a reference-derived frame-length bin.

IAT and frame-size thresholds use declared Hyndman--Fan Type-7 reference
quantiles. The categorical vocabulary contains only combinations present in
the reference, bounded by configuration.

For gene-selected `K`, fit a categorical HMM with deterministic Baum--Welch:

- fixed reference-derived initialization;
- additive smoothing;
- scaled or log-space forward/backward recursions;
- finite iteration and convergence limits;
- canonical state ordering by expected IAT category, then emission and
  transition vectors, to remove label ambiguity.

The fitted artifact stores the initial, transition, and emission
probabilities, categorical vocabulary, quantile thresholds, category-specific
raw individual-packet reservoirs, estimator settings, convergence outcome, and
fallback counters. It does not store or replay whole packet subsequences.

Generation samples a hidden state, observation category, and one raw
`(IAT, direction, frame_length)` member from that category. The hidden state
therefore controls joint timing/mark categories, while raw values preserve
valid observed support without template replay.

## Markov packet-train model

Define packet trains by a frozen reference-only gap threshold, initially the
Type-7 0.90 IAT quantile. An IAT at or below the threshold remains inside the
train; a larger IAT separates trains. The threshold and endpoint convention are
serialized.

The structural gene caps the train-length state. Fit:

- empirical initial train-state probabilities;
- a smoothed Markov transition matrix between capped length states;
- actual train-length reservoirs for each capped state;
- state- and packet-position-conditioned joint mark reservoirs, using
  `first`, `interior`, and `last` positions;
- within-train IAT reservoirs by state and position;
- transition-conditioned, source-state, and global inter-train gap reservoirs
  with a declared fallback order.

Generation samples train state and actual train length, then generates each
packet from individual IAT and mark reservoirs. It never samples a stored whole
train template. Guards and the observation-window boundary are checked between
every packet, not only between trains.

## Autoregressive Conditional Duration model

Use exponential ACD(`p`,`p`) for gene-selected order `p`:

\[
\psi_i=\omega+\sum_{j=1}^{p}\alpha_j\Delta_{i-j}
       +\sum_{j=1}^{p}\beta_j\psi_{i-j},
\qquad
\Delta_i=\psi_i\epsilon_i,
\qquad
\epsilon_i\sim\operatorname{Exponential}(1).
\]

Require `omega > 0`, nonnegative coefficients, and
`sum(alpha) + sum(beta) < 1`. Deterministic constrained maximum likelihood uses
SciPy with a declared parameter transform, initialization, tolerance, maximum
iterations, and convergence failure. The fitted payload stores coefficients,
initial conditional duration, likelihood diagnostics, the reference joint mark
distribution, and estimator choices.

Generation initializes the recursion exactly as documented, samples scalar
unit-exponential innovations, and emits empirical joint marks. Zero observed
IATs remain valid fitting observations; no jitter is introduced.

## Non-homogeneous Poisson process

Use gene-selected equal-width bins over `[0, W]` and fit one nonnegative
piecewise-constant rate per bin. The conditioned packet at zero is excluded
from rate counts. Zero-rate bins are valid. The fitted payload stores exact bin
edges, rates, integrated intensity, bin-conditional joint mark distributions,
and a global mark fallback for empty bins.

Generation uses exact piecewise-constant integrated-intensity inversion or an
equivalent exponential clock that advances across exhausted and zero-rate bins
without consuming a packet mark. It emits a conditioned mark at zero, samples
later marks from the active time bin, and uses the global fallback only for a
bin with no observed marks.

## Weighted similarity methods

The genetic evaluator and final comparator share the same implementations and
configuration for the following methods. They are deterministic descriptive
distances; no IID packet-level p-value is published.

### Cramér--von Mises

Compute pooled-support integrated squared ECDF differences for frame length and
IAT, globally and in direction strata when configured. Empty one-sided strata
receive maximum discrepancy rather than an invented sample. Normalize each
component to `[0, 1]`, combine it with declared feature/stratum weights, and
return `score = 1 - discrepancy`.

### Anderson--Darling

Compute a tie-aware, tail-weighted two-sample ECDF discrepancy for frame length
and IAT. Endpoint and zero-denominator behavior is explicit. The score uses a
bounded monotone mapping with retained raw statistic and component diagnostics.
Trafficlab does not consume or publish the classical IID p-value.

### Jensen--Shannon

Use base-2 JSD, naturally bounded in `[0, 1]`, for:

- the exact joint `(direction, frame_length)` PMF;
- `(following-packet direction, log1p(IAT) bin)` PMF over shared bins derived
  only from the reference window and configured bin count.

No pseudocount is required for JSD. Components and feature weights are
retained; `score = 1 - weighted_jsd`.

### Approximate joint MMD

Represent each noninitial packet with continuous
`(log1p(IAT), log(frame_length))` values and unordered direction. Continuous
coordinates use reference-only mean/scale with a configured floor. Direction
uses separate feature blocks, equivalent to a delta kernel with no ordinal
meaning.

Use deterministic random Fourier frequencies from a dedicated configured seed.
Each cosine/sine feature map has unit norm, is accumulated as a streaming mean,
and needs no whole-trace pairwise matrix. The squared distance between mean
feature embeddings is at most four; define

\[
D_{MMD}=\frac{\lVert\bar z_R-\bar z_G\rVert_2}{2},
\qquad s_{MMD}=1-D_{MMD}.
\]

Configuration freezes feature dimension, seed, continuous scaling, and minimum
sample requirements.

## Post-fit diagnostics

These diagnostics run only for the final comparison artifact. They are not
called by candidate/trial fitness, and adding a zero genetic weight is not used
as a substitute for that separation.

### Fano/Allan curves

For configured strictly increasing widths, bin total and direction-separated
packet counts. Retain reference and generated Fano factors and Allan factors,
window counts, zero-mean conventions, and scale-wise differences. Produce a
bounded curve discrepancy from predeclared normalized `log1p` differences and
scale/feature weights.

### Transition-matrix fidelity

Build a small reference-defined state vocabulary from direction, log-size bin,
and log-IAT bin. Retain occupancy, smoothed transition rows, run lengths, sparse
fallbacks, and active-state counts. Combine bounded occupancy, transition-row,
and run-length JSD components under declared weights. Generated values outside
reference thresholds enter explicit edge categories rather than changing the
vocabulary.

### Classical C2ST

Create nonoverlapping fixed-duration window features containing direction-wise
packet/byte counts, size/IAT summaries, zero-IAT count, and activity/burst
summaries. Standardize continuous features from reference training blocks only.

Fit a deterministic L2-regularized logistic regression with SciPy. Time-blocked
folds contain guard blocks between train and evaluation data. Configuration
freezes window width, fold layout, guard size, feature version, regularization,
iteration limit, and tolerance. Retain balanced accuracy, AUC, coefficients,
fold sizes, and convergence diagnostics. Define similarity as
`1 - 2 * abs(AUC - 0.5)`, clamped only for floating-point roundoff.

## Configuration

The strict TOML schema gains four model tables, four new fitness weights, and
all settings needed by the new fitness and post-fit methods. Release defaults
enable all seven model families and all eight weighted methods.

The default fitness policy starts with equal method weights of `0.125`. This is
a neutral operational default, not evidence that the methods are independent
or equally informative. Sensitivity studies may later revise it explicitly.

All new integer bounds, quantiles, bins, feature dimensions, seeds, smoothing,
iteration limits, tolerances, scale widths, component weights, state caps,
window widths, and allocation caps receive strict validation. Unknown or
partial family/method tables remain errors.

## Comparison and fitting data flow

The comparison boundary separates fitness from final diagnostics without a
mode boolean:

```text
evaluate_fitness(reference, generated, W, settings)
  -> eight weighted methods + aggregate

evaluate_postfit(reference, generated, W, settings)
  -> Fano/Allan + transition fidelity + C2ST

genetic trial
  -> evaluate_fitness only

final compare stage
  -> evaluate_fitness + evaluate_postfit
  -> one schema-5 similarity artifact
```

Checkpoints retain eight-method trial tuples in one canonical order. Resume
compatibility binds the complete schema-5 similarity configuration. The final
publisher validates every component's arithmetic and the absence of post-fit
diagnostics from genetic trial records.

## Dashboard behavior

The existing similarity-score aspect expands to the eight fitness methods plus
aggregate score. Three new read-only run-level aspects display only stored
artifact diagnostics:

- Fano/Allan curves by scale and direction;
- reference/generated transition occupancy and row discrepancy summaries;
- C2ST balanced accuracy/AUC and standardized coefficient magnitudes.

The dashboard does not recompute these methods, refit the classifier, or open a
second scientific path. A schema-4 run remains explicitly incompatible with
schema-5-only diagnostic aspects rather than being silently upgraded.

## Architecture documentation

Normative documentation changes occur in the same coherent commit as the code
and tests they define:

- add `architecture/traffic_models/packet_hmm.md`;
- add `architecture/traffic_models/markov_packet_train.md`;
- add `architecture/traffic_models/acd.md`;
- add `architecture/traffic_models/nhpp.md`;
- add one algorithm document for each of the seven required similarity and
  diagnostic methods;
- update `architecture/traffic_models/README.md` and
  `architecture/similarity_methods/README.md`;
- update `architecture/SYSTEM.md`, `TESTING.md`, `VISUALIZATION.md`, and the
  genetic-model contract where artifact or evaluation behavior changes;
- remove implemented entries from `architecture/CANDIDATES.md` rather than
  leaving them described as future candidates.

Architecture files contain stable definitions and verification standards, not
milestone progress or experimental outcomes. Implementation plans, progress,
and generated evidence remain outside `architecture/`.

## Three-tier test strategy

Verification is gated by completeness so immediate work does not repeatedly
pay the final release cost.

### Small tier: immediate feedback

Run after each red/green behavior increment and before each small commit:

- the directly affected unit test file;
- one independent hand/scientific oracle for changed mathematics;
- targeted Ruff and strict Pyright for touched production modules;
- deterministic seed, boundary, guard, and invalid-input cases relevant to the
  increment.

Small-tier work does not run the complete test suite, Docker, dashboard suite,
full coverage, or a multi-generation experiment. Failed small tests block the
increment immediately.

### Medium tier: coherent subsystem milestone

Run only after a coherent subsystem is complete: shared schema/config,
weighted metrics, post-fit diagnostics/dashboard, each pair of model families,
or complete seven-family registry.

The medium tier includes:

- all unit and scientific tests for the changed package;
- in-process integration for affected fit/generate/compare/artifact paths;
- strict Pyright and Ruff for the complete changed package;
- branch coverage for changed non-Docker modules, with 100% executable
  line/branch coverage for any function previously exposed by a failing test;
- schema generation and canonical round-trip checks affected by the milestone;
- one medium development experiment only when the end-to-end path for all
  algorithms included in that milestone exists.

It omits unrelated packages, Docker, Internet validation, immutable historical
evidence audits, and the big experiment.

### Big tier: integrated acceptance

Run only after all four models, four weighted methods, three post-fit
diagnostics, schema-5 artifacts, dashboard loading, examples, and documentation
are complete and every medium gate passes.

The big tier is the project completion gate:

- locked sync and schema regeneration;
- format, repository-wide Ruff, and strict Pyright;
- complete non-Docker pytest and branch-aware coverage at or above 90%;
- in-process, CLI, fixture, dashboard, scientific, property, resume, and
  artifact-publication integration;
- available bounded Docker and explicit Internet/real-program validation;
- deterministic regeneration of checked fixtures and examples;
- the full-capture big experiment and retained evidence;
- independent final review with no Critical or Important findings.

Big-tier failure returns work to the smallest owning tier; it does not cause
unrelated full reruns after every local correction.

## Three-tier development experiments

The source capture is
`dumps/moutai-stock-price-response-success/trafficlab-ready-moutai-stock-price-response-success.pcapng`,
with its existing `capture.json`. Its recorded identity is retained in every
derived-experiment manifest. Derived captures are development inputs, not new
claims about workload provenance.

### Small experiment

Purpose: immediate end-to-end sanity after one model or metric becomes usable.

- derive packets 1 through 256 with `editcap`, then normalize ordering with
  `reordercap`;
- expected reference: 256 packets, approximately 19.67 seconds, both
  directions, 43 distinct frame lengths;
- enable every family and metric implemented at that increment;
- population 8 once all seven families exist, otherwise the minimum satisfying
  elites plus enabled families;
- one generation, one trial seed `[17]`, no resume;
- trial/final wall guards 5/10 seconds;
- target wall-clock expectation: under one minute after warm startup.

Run this profile freely at small milestones, but never interpret its champion
as scientific model selection.

### Medium experiment

Purpose: expose interaction and seed variability after a coherent subsystem is
complete.

- derive packets 1 through 512 with `editcap` and `reordercap`;
- expected reference: 512 packets, approximately 47.19 seconds, both
  directions;
- enable all algorithms completed by the milestone;
- population 12, three generations, two trial seeds `[17, 29]`;
- early stopping after two stagnant generations with tolerance `0.0001`;
- trial/final wall guards 10/30 seconds;
- run only after weighted metrics, after post-fit diagnostics, and after all
  seven model families become end-to-end complete;
- target wall-clock expectation: bounded to roughly ten minutes on the
  development host.

### Big experiment

Purpose: integrated schema-5 validation with a longer and more variable search,
not release-grade best-model assurance.

- use the complete 3,649-packet, approximately 112.37-second reference;
- enable all seven model families, eight weighted methods, and three post-fit
  diagnostics;
- population 21, ten generations, three trial seeds `[17, 29, 43]`;
- early stopping after three stagnant generations with tolerance `0.0001`;
- trial/final wall guards 30/120 seconds;
- retain configuration, checkpoint, history, best model, generated PCAPNG,
  comparison artifact, logs, identities, elapsed time, and peak RSS;
- run only after the complete big test tier is otherwise ready;
- use a bounded outer wall-time appropriate to a final integration run and do
  not silently deepen the search after inspecting results.

The big experiment may motivate a later independent release study with more
generations and seeds. It does not replace such a study and is deliberately not
run during incomplete milestones.

## Fixtures and examples

Unit and scientific tests use small checked-in synthetic fixtures with exact
hand-derived behavior. Ordinary tests never depend on a large user capture or
the public Internet.

The Moutai-derived small and medium captures are created reproducibly in a
temporary or ignored development directory from the existing local source.
They are not duplicated into the repository unless a later evidence protocol
explicitly requires it. The derivation manifest records source SHA-256, packet
range, tool versions, resulting packet count, observation window, and output
identity.

Schema-5 examples and deterministic fixture generators are checked in only
after the owning algorithm milestone is stable. Historical validation-study
evidence is never rewritten to schema 5; fresh evidence uses a new study
identity.

## Error handling and boundedness

- Estimator nonconvergence is an explicit invalid candidate or configuration
  error with actionable diagnostics; it is not replaced by arbitrary
  parameters.
- Numerical underflow/overflow, nonfinite probabilities, malformed rows,
  invalid PMFs, excessive state/bin/feature allocation, and unsafe iteration
  counts fail before generation or comparison publication.
- A post-fit diagnostic with insufficient configured windows fails final
  comparison with an exact minimum-sample message; it never fabricates a
  neutral score.
- Genetic evaluation never invokes a post-fit diagnostic and therefore cannot
  fail because C2ST or final-only window preconditions are unavailable.
- Every temporary artifact is same-directory and atomically published under
  the existing ownership rules.
- Experiment commands use bounded wrappers and leave no Docker or temporary
  resource behind after failure.

## Acceptance

The feature is accepted when:

- all seven model families fit, serialize, load, generate, and compete under
  the common interface;
- all eight weighted methods execute for every candidate and their configured
  weights aggregate exactly;
- the three final-only diagnostics are absent from genetic trials and present
  in final schema-5 comparisons;
- deterministic independent scientific oracles cover every new model and
  metric definition;
- schema-4 reuse fails explicitly and schema-5 round trips are canonical;
- release/default examples enable the new algorithms with valid bounded
  settings;
- dashboard aspects load only stored schema-5 diagnostics;
- architecture documentation defines every implemented algorithm and no longer
  lists it as a candidate;
- the small and medium gates pass at their owning milestones;
- the big test and experiment gates pass only after integrated completeness;
- independent final review reports no Critical or Important findings;
- the implementation branch is clean, pushed, and ready for integration.
