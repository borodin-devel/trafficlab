# Trafficlab Research Fitness Assessment

Assessed product commit: `63f8c1b6da8a293bc65740890ca0ec7d0f479e1a`

Assessment date: 2026-08-14 (Europe/Moscow)

## Scope and method

This assessment applies the 37 independent anchors in the
[research-fitness rubric](../architecture/RESEARCH_FITNESS_CRITERIA.md) to the declared one-process research
prototype. It covers stage results, configurability, scientific methods, robustness, and reproducibility.
Enterprise hardening, multi-user operation, distributed execution, hosted deployment, and feature breadth are
excluded unless they directly affect scientific evidence.

Each criterion receives the highest label whose complete anchor is supported. A strong result in one criterion does
not compensate for a weak result in another. Missing or non-retrievable evidence limits a grade. Test volume and code
volume are not grading evidence by themselves.

Evidence provenance is explicit throughout:

- **Fresh** means a command was executed against the assessed source during this assessment.
- **Source** means architecture, implementation, direct tests, or deterministic fixtures at the assessed commit.
- **Retained** means a committed historical record was audited during this assessment.
- **Literature** means an external primary paper or authoritative mathematical definition.

The current `HEAD` also contains the later assessment plan commit. No product-source change after the assessed commit
is credited. The assessment reports defects and evidence gaps without changing the product.

## Evidence inventory

### Fresh command results

| Evidence | Result | Interpretation |
|---|---|---|
| Locked sync, lock check, Ruff, Pyright | Passed; no lock change or type errors | Environment and static quality |
| Three deterministic fixture checks | Checked-in bytes matched regeneration | Canonical fixture evidence |
| Bounded non-external branch gate | 2,262 passed; 97.21% branch-aware coverage | Broad local behavioral evidence |
| Bounded process-tree suite | 10 passed | Real descendant, wall-time, signal, collision, and memory containment |
| Focused model, similarity, and genetic suite | 846 passed | Direct mathematical and algorithmic evidence |
| Fresh Docker matrix | Indeterminate after selecting 18 tests | Neither passing nor failing evidence |
| Fresh Internet smoke | One passed; 2,280 deselected | Real HTTPS DNS, TLS, bidirectional capture, and teardown |
| Retained-study production audit | Failed on absent realized config | Run tree is not reproducibly auditable |

The fresh Docker process ceased and left no labelled container, network, or volume, but its terminal pytest summary
and exit status were not retained. An empty pytest failure cache is not a substitute. The Internet smoke passed in
13.50 seconds against the configured credential-free HTTPS object.

### Retained evidence

The historical prerequisite, result, and base-configuration records were internally
consistent, but they were retired from the active protocol because their cited run
trees are absent and their semantics predate the corrected schema. The historical
bytes remain available in Git history; they are not an accepted evidence bundle.
The replacement protocol publishes only an audited candidate tree with all cited
bytes, configurations, lineage, and four evidence classes retained together.

That audit cannot validate the scientific values from raw evidence. `runs/validation_study/` is absent. The nine primary records
and one reproduction record name absent realized configurations and absent run directories. All 90 named artifact
targets are absent. Thirty-two locally present ignored HTTP header records match the committed hashes, but they are
not committed, are not retrievable from the assessed revision, and cannot reconstruct captures or run artifacts.

The retained report's three workloads, three repeats, one endpoint and host, small genetic settings, one fresh final
seed, and public-network variation remain explicit limitations. Its arithmetic is historical consistency evidence,
not a fresh rerun or an independent reconstruction from packet bytes.

### Source and mathematical evidence

The [system architecture](../architecture/SYSTEM.md), [capture architecture](../architecture/CAPTURE.md),
[testing contract](../architecture/TESTING.md), model and metric specifications, implementation, and direct tests
provide traceable behavior. The focused audit mapped each declared model, metric, aggregate, and genetic-search rule
to owning code and tests. It found no material code-to-specification contradiction, but found scientific and evidence
limitations that are graded below.

### Primary literature

- Gallager's Poisson-process chapter supports IID exponential interarrivals and `lambda` as the process rate. The
  estimator used here follows from the assessment's independent likelihood derivation.
  <https://ocw.mit.edu/courses/6-262-discrete-stochastic-processes-spring-2011/resources/mit6_262s11_chap02/>
- Pyke's [Markov renewal paper](https://doi.org/10.1214/aoms/1177704863) supports the state-and-time kernel class.
- Hyndman and Fan's [sample-quantile paper](https://robjhyndman.com/papers/sample_quantiles.pdf) supports the named
  Type-7 quantile convention.
- Fischer and Meier-Hellstern's [MMPP paper](https://doi.org/10.1016%2F0166-5316%2893%2990035-S) supports the
  Markov-modulated Poisson process definition.
- NIST's [two-sample KS definition](https://itl.nist.gov/div898/software/dataplot/refman1/auxillar/ks2samp.htm)
  supports the empirical-distribution distance.
- NIST's [autocorrelation definition](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35c.htm) supports the
  whole-series-mean lag estimator.
- Miller and Goldberg's
  [tournament-selection paper](https://content.wolfram.com/sites/%31%33/%32%30%31%38/%30%32/%30%39-3-2.pdf)
  supports tournament selection, but not Trafficlab's heterogeneous-family fairness rules.

These sources support named method definitions. They do not validate Trafficlab's local smoothing, fallback,
alignment, weighting, heterogeneous reproduction, or final-seed conventions.

## Known evidence limitations

- The fresh Docker matrix has no retained terminal result and is indeterminate.
- The fresh external evidence is one Internet smoke execution, not a representative rate or platform matrix.
- The retained Validation Study has no committed realized configurations, captures, fitted models, checkpoints,
  histories, generated traces, logs, or comparison artifacts.
- Model evidence lacks large known-parameter recovery studies, independent simulators, and distributional tests.
- Metric evidence lacks independent numerical implementations and broad sensitivity or calibration studies.
- Genetic evidence lacks family-permutation, equal-known-optimum, compute-budget, and controlled known-winner studies.
- No executed scientific mutation-testing campaign demonstrates sensitivity to plausible mathematical defects.

## Section grade distributions

#### 1. Scientific precision and correctness of stage results

| Dreadful | Poor | Partial | Acceptable | Excellent |
|---:|---:|---:|---:|---:|
| 0 | 0 | 2 | 5 | 0 |

#### 2. Configurability

| Dreadful | Poor | Partial | Acceptable | Excellent |
|---:|---:|---:|---:|---:|
| 0 | 0 | 4 | 2 | 0 |

#### 3. Scientific precision and correctness of methods

| Dreadful | Poor | Partial | Acceptable | Excellent |
|---:|---:|---:|---:|---:|
| 0 | 0 | 4 | 5 | 0 |

#### 4. Robustness

| Dreadful | Poor | Partial | Acceptable | Excellent |
|---:|---:|---:|---:|---:|
| 0 | 0 | 1 | 6 | 0 |

#### 5. Reproducibility

| Dreadful | Poor | Partial | Acceptable | Excellent |
|---:|---:|---:|---:|---:|
| 1 | 1 | 4 | 2 | 0 |

## 1. Scientific precision and correctness of stage results

### 1.1 Preflight decision accuracy

**Grade: acceptable**

**Evidence**

- Fresh: the 2,262-test non-external gate passed, and the real Internet capture passed its exact preflight path.
- Source: [local preflight tests](../tests/unit/test_preflight.py) cover ready, missing, unwritable, and low-space
  cases.
- Source: [Docker preflight tests](../tests/unit/test_docker_preflight.py) cover images, topology, network, deadlines,
  primary failures, and cleanup detail using the production boundary semantics.

**Rationale:** Effective configuration drives local and Docker checks, and direct failures name the unmet condition.
Config-only and full paths are separated without silently claiming Docker readiness.

**Limitation:** The excellent anchor is not supported because the fresh Docker matrix is indeterminate and no
independent changed-environment matrix measures false approvals and false rejections for every prerequisite.

### 1.2 Capture fidelity

**Grade: acceptable**

**Evidence**

- Fresh: the Internet smoke proved real DNS, TLS, bidirectional traffic capture, parsing, and teardown.
- Source: [Docker capture tests](../tests/docker/test_capture_docker.py) define controlled TCP, UDP, unicast,
  broadcast, address, protocol, packet-count, and direction oracles.
- Source: [capture policy](../architecture/CAPTURE.md) fixes target `eth0`, non-promiscuous capture, target-MAC
  direction, readiness, workload stop, flush, validation, and cleanup boundaries.

**Rationale:** Target ownership, interface, observation lifecycle, direction, and error behavior are explicit and
directly exercised within the declared container topology. The fresh real capture confirms current external use.

**Limitation:** Independent packet inspection across known timing and rate variation is absent, and the fresh
controlled Docker matrix has no conclusive result, preventing excellent evidence strength.

### 1.3 Capture artifact correctness

**Grade: acceptable**

**Evidence**

- Fresh: deterministic Phase 2 bytes regenerated exactly, and the Internet smoke produced a parseable real capture.
- Source: [capture metadata tests](../tests/unit/test_capture_metadata.py) enforce an exact schema and target identity.
- Source: [PCAPNG tests](../tests/unit/test_pcapng.py) and
  [capture validation tests](../tests/unit/test_capture_validation.py) cover ordering, link type, timestamps,
  directions, malformed frames, and shared deadlines.

**Rationale:** Strict metadata and PCAPNG parsing produce one ordered canonical trace under an explicit observation
window. Validation rejects unsupported or malformed interpretations instead of publishing them as reusable evidence.

**Limitation:** No independent PCAPNG reader, comprehensive byte-corruption matrix, or multi-resolution real-capture
study supports the excellent anchor.

### 1.4 Fit result correctness

**Grade: acceptable**

**Evidence**

- Fresh: all 846 focused model, similarity, and genetic tests passed.
- Source: [checkpoint tests](../tests/unit/genetic/test_checkpoint.py) recompute candidate fitness, history, winner,
  compatibility, and RNG state from strict retained values.
- Source: [evaluation tests](../tests/unit/genetic/test_evaluation.py) prove common inputs, seeds, invalid reasons,
  and a distinct fresh final seed.

**Rationale:** Candidate parameters, bounds, seeds, component scores, aggregate fitness, invalid states, stable ties,
and terminal winner publication are explicitly linked and revalidated. The final seed is fresh-seed validation, not
held-out data.

**Limitation:** No analytically known family competition, independent winner-selection implementation, or auditable
retained real checkpoint supports excellent reconstruction across fresh and resumed executions.

### 1.5 Generated-trace correctness

**Grade: partial**

**Evidence**

- Fresh: the focused suite passed scripted event, draw-order, endpoint, bound, seed, and round-trip tests.
- Source: [Poisson tests](../tests/unit/models/test_poisson.py),
  [Markov Renewal tests](../tests/unit/models/test_markov_renewal.py), and
  [MMPP tests](../tests/unit/models/test_mmpp.py) cover each declared generator.
- Literature: the MMPP definition implies arrival-epoch state weights proportional to `pi_z * lambda_z`.

**Rationale:** Ordinary outputs are ordered, bounded, deterministic, parseable, and linked to fitted models. However,
MMPP forces the time-zero packet after sampling a time-stationary state. A real normalized trace begins at an arrival,
so its first scored IAT has the wrong latent-state distribution whenever the arrival rates differ.

**Limitation:** Acceptable requires an arrival-aligned MMPP initialization or a scientifically justified scoring
convention, plus direct distributional evidence that generated events follow each claimed process.

### 1.6 Comparison-result correctness

**Grade: acceptable**

**Evidence**

- Fresh: focused similarity tests and deterministic Phase 2 fixture regeneration passed.
- Source: [comparison tests](../tests/unit/test_comparison.py) reconstruct configured components, diagnostics, weights,
  aggregate values, bounds, and strict artifact inputs.
- Source: hand cases in [KS tests](../tests/unit/similarity/test_ks.py),
  [ACF tests](../tests/unit/similarity/test_autocorrelation.py), and
  [multiscale tests](../tests/unit/similarity/test_multiscale.py) cover equality, extremes, and direction reversal.

**Rationale:** Strictly parsed, aligned traces feed all four methods under one window, and exact component diagnostics
and normalized weights reconstruct the reported aggregate. Malformed and changed artifacts are rejected.

**Limitation:** Independent comparison code, retained raw Validation Study traces, and a broader boundary-mutation matrix are
absent, so excellent input-to-value reconstruction is not demonstrated.

### 1.7 End-to-end result consistency

**Grade: partial**

**Evidence**

- Fresh: the non-external gate passed strict run-pipeline tests; the Internet capture passed, but the Docker matrix is
  indeterminate.
- Source: [run tests](../tests/unit/test_run.py) reload the exact final tree and reject replacement, missing, foreign,
  corrupt, or lineage-inconsistent stage results.
- Retained: the production Validation Study audit failed because all ten run directories, realized configs, and 90 named
  artifacts are absent.

**Rationale:** The implemented normal path has strong final-tree and stage-lineage controls. The committed study,
however, cannot demonstrate that its published values remain one complete result because its authoritative run trees
were not retained. That material evidence gap prevents an unqualified scientific use of the historical result.

**Limitation:** Acceptable requires committed or immutable access to the complete strict final artifact set and a
successful independent audit or fresh complete experiment, neither of which is available.

## 2. Configurability

### 2.1 Coverage of scientifically meaningful controls

**Grade: partial**

**Evidence**

- Source: [configuration models](../src/trafficlab/config.py) expose workload, capture, model bounds, generation,
  genetic operators, seeds, metrics, weights, and reliability limits.
- Source: [configuration validation tests](../tests/unit/test_config_validation.py) exercise independent family
  overrides, bounds, seeds, lags, widths, and normalized weights.
- Source: a method with zero aggregate weight is still evaluated and can invalidate a comparison or candidate.

**Rationale:** Most declared studies can be configured without source edits, and implementation-only details remain
hidden. The absence of a metric-enable control means a nominally zero-weight method still imposes its preconditions,
which prevents some scientifically meaningful single-component studies.

**Limitation:** Acceptable requires explicit independent control of metric participation, or an unambiguous declared
rule that all diagnostics are mandatory regardless of weight, with representative controlled variations.

### 2.2 Configuration semantics

**Grade: partial**

**Evidence**

- Fresh: deterministic effective-configuration rendering and all broad configuration tests passed.
- Source: [configuration I/O tests](../tests/unit/test_config_io.py) prove strict types, defaults, path resolution, and
  value-preserving snapshots.
- Source: documents describe weights as controlling enabled methods, while production evaluates zero-weight methods.

**Rationale:** Types, units, domains, defaults, stage ownership, and most interactions are precise. The zero-weight
participation interaction remains materially ambiguous because it can change feasibility without changing fitness.

**Limitation:** Acceptable requires one documented and tested meaning for zero weight and method enablement across
configuration, comparison, genetic evaluation, and reporting.

### 2.3 Configuration validation

**Grade: acceptable**

**Evidence**

- Fresh: the 2,262-test gate passed with strict configuration cases included.
- Source: [schema tests](../tests/unit/test_config_schema.py) reject unknown fields, coercion, nonfinite values, invalid
  paths, and empty argv.
- Source: [validation tests](../tests/unit/test_config_validation.py) cover bounds, probabilities, cross-family tables,
  population feasibility, seed separation, metric vectors, and normalized weights.

**Rationale:** Validation is strict, typed, finite, range-aware, relationship-aware, and rejects unknown input before
scientific work. Cross-section feasibility is checked at the owning configuration boundary.

**Limitation:** No independently enumerated all-field mutation matrix or systematic extreme-value campaign proves the
excellent anchor for every field and important interaction.

### 2.4 Effective-configuration fidelity

**Grade: partial**

**Evidence**

- Source: canonical snapshot and stage-lineage tests reject changed effective configurations and incompatible resume.
- Source: [run-pipeline tests](../tests/integration/test_run_pipeline.py) exercise fresh, reused, repaired presentation,
  and incompatible stage artifacts under one authoritative snapshot.
- Retained: all ten Validation Study realized configurations named by the result manifest are absent.

**Rationale:** Normal implementation paths retain and enforce a mostly complete normalized snapshot. The published
real study cannot prove that every stage consumed its claimed realization because those exact realized files were not
retained for the production audit.

**Limitation:** Acceptable requires retrievable realized snapshots and successful cross-stage identity reconstruction
for full runs, separate stages, resume, and reuse.

### 2.5 Stage-level reuse and controlled variation

**Grade: acceptable**

**Evidence**

- Source: separate `capture`, `fit`, `generate`, `compare`, and `run` commands share strict loaders and ownership rules.
- Source: [run-pipeline tests](../tests/integration/test_run_pipeline.py) cover compatible reuse, incompatible changes,
  missing artifacts, checkpoint resume, derived-history repair, and exact downstream invalidation.
- Fresh: deterministic model and fit fixtures regenerated byte-identically.

**Rationale:** Authoritative inputs and compatibility rules are explicit at stage boundaries. Controlled replacement
or changes invalidate only scientifically dependent outputs, while valid stable inputs can be reused.

**Limitation:** A systematic external variation matrix with retained unchanged-input hashes and independently
reconstructed downstream values is absent, preventing excellent evidence.

### 2.6 Portability of experiment definitions

**Grade: partial**

**Evidence**

- Source: paths resolve relative to the config, image references and mounts are explicit, and canonical snapshots
  retain resolved values.
- Retained: Validation Study base configs use immutable image identities and the prerequisite manifest records platform and
  tools.
- Retained: the environment-specific realized configuration for every published run is absent.

**Rationale:** Typical definitions separate many scientific values from environment realization and reject invalid
paths or images. Missing realized records prevent proof that transfer did not silently change the actual study, and no
cross-machine comparison is retained.

**Limitation:** Acceptable requires retaining both portable and realized definitions and rejecting or explaining every
environment contribution in a successful compatible-environment transfer.

## 3. Scientific precision and correctness of methods

### 3.1 Specification-to-implementation fidelity

**Grade: acceptable**

**Evidence**

- Fresh: 846 focused tests passed.
- Source: every requested estimator, draw, metric, aggregate, and genetic rule mapped to an owning function and direct
  behavioral test; no material implementation contradiction was found.
- Literature: the Poisson, Markov renewal, MMPP, KS, ACF, quantile, and tournament sources above support the named
  external definitions; local conventions are labeled as local.

**Rationale:** Implemented behavior matches complete repository specifications across the declared domain, including
explicit local choices and important boundaries.

**Limitation:** Independent implementations, executed mutation testing, a complete architecture citation for Type 7,
and an unambiguous zero-weight rule are missing, preventing excellent traceability and error sensitivity.

### 3.2 Model-fitting correctness

**Grade: acceptable**

**Evidence**

- Source: [Poisson tests](../tests/unit/models/test_poisson.py) distinguish the complete-IAT rate estimator from an
  event-count-over-window error.
- Source: [Markov Renewal tests](../tests/unit/models/test_markov_renewal.py) reconstruct states, smoothing, empty rows,
  conditional holding samples, fallbacks, and strict round trips.
- Source: [MMPP tests](../tests/unit/models/test_mmpp.py) prove rate repair and empirical marks; rates are direct GA
  genes rather than likelihood estimates, matching the declared method.

**Rationale:** Estimation, empirical conditioning, smoothing, repair, mark preservation, and declared degeneracies are
correct and explicit for all three families. Heuristic choices are not misrepresented as literature-derived estimates.

**Limitation:** Known-parameter recovery, independent estimators, misspecification studies, and uncertainty analysis
are absent, so excellent scientific confirmation is not available.

### 3.3 Stochastic-generation correctness

**Grade: partial**

**Evidence**

- Fresh: exact scripted sequences, seed repeats, endpoints, and guard cases passed for all three generators.
- Source: Poisson and Markov Renewal draw orders match their declarations; MMPP correctly races exponential arrival
  and transition clocks after initialization.
- Literature: stationary MMPP arrival epochs weight states by `pi_z * lambda_z`, not time-stationary `pi_z` alone.

**Rationale:** Typical draws follow the implemented process and exact reliability contract, but MMPP's forced
time-zero alignment initializes from the wrong latent distribution for an arrival-aligned trace. The first scored IAT
is systematically mismatched whenever the two arrival rates differ.

**Limitation:** Acceptable requires corrected or justified arrival-epoch initialization and direct long-run frequency,
occupancy, correlation, and conditional-mark studies against independent expectations.

### 3.4 Model-competition fairness

**Grade: partial**

**Evidence**

- Source: [evaluation tests](../tests/unit/genetic/test_evaluation.py) prove common reference, window, metrics, trial
  seeds, and reliability limits.
- Source: [population tests](../tests/unit/genetic/test_population.py) codify lexical quota remainders, contiguous
  lexical-family IDs, and smallest-ID equal-fitness ties.
- Literature: tournament selection supports maximum-fitness sampling, not heterogeneous-family neutrality.

**Rationale:** Common scientific inputs and family champions provide meaningful comparability. Candidate handling is
not neutral: lexical remainders favor earlier families, and their smaller initial IDs also win equal-fitness ranking,
elite, and tournament ties.

**Limitation:** Acceptable requires neutral family handling or evidence that the asymmetries are scientifically
immaterial through family permutations, equal-known-optimum budgets, failure symmetry, and controlled winners.

### 3.5 Similarity-method correctness

**Grade: acceptable**

**Evidence**

- Literature: NIST definitions support the merged-ECDF KS statistic and whole-series-mean autocorrelation estimator.
- Source: hand cases cover equal, disjoint, tied, constant, minimal, direction-reversed, endpoint, and cell-cap traces.
- Fresh: all focused similarity tests passed.

**Rationale:** Frame-size KS, IAT KS, event-index ACF, and direction-aware multiscale rate implement their documented
statistics, domains, ranges, diagnostics, normalizations, and edge conventions. KS outputs are descriptive distances,
not invalid distribution-free p-values.

**Limitation:** No SciPy, R, or other independent implementation comparison exists, and the local multiscale method
lacks broad sensitivity or calibration evidence, preventing excellent validation.

### 3.6 Aggregate-fitness correctness

**Grade: acceptable**

**Evidence**

- Source: [comparison tests](../tests/unit/test_comparison.py) reconstruct normalized weighted components with full
  diagnostics.
- Source: checkpoint loading recomputes aggregates, and genetic evaluation uses a stable arithmetic mean over trials.
- Fresh: focused hand-weighted, invalid-candidate, exact-tie, and winner-use tests passed.

**Rationale:** Configured components and normalized weights produce the declared bounded aggregate. Invalid cases,
ties, diagnostics, checkpoint values, trial means, and winner selection are deterministic and consistent.

**Limitation:** Independent ranking reconstruction, exhaustive monotonic component combinations, and clarified
zero-weight participation are missing, preventing excellent evidence.

### 3.7 Numerical and boundary correctness

**Grade: acceptable**

**Evidence**

- Source: multiscale tests use exact integer and binary-rational accumulation, huge integers, subnormals, and
  near-boundary binning.
- Source: model tests cover nonfinite clocks, overflow, exact endpoints, zero IATs, ordering, and generation limits.
- Fresh: fixture bytes and all focused numerical cases passed without changes.

**Rationale:** Finite domains, quantization, ordering, closed-window endpoints, overflow rejection, and exact limits
follow documented conventions and prevent infeasible scientific output.

**Limitation:** Multiple timestamp resolutions, independent high-precision oracles, and mutation-sensitive coverage
for every critical conversion are absent, so excellent boundary evidence is not established.

### 3.8 Scientific validation strength

**Grade: partial**

**Evidence**

- Fresh: 846 focused tests passed hand calculations, analytical invariants, and scripted RNG sequences.
- Source: deterministic fixture regeneration and same-implementation round trips provide strong self-consistency.
- Source: no large known-parameter model recovery, independent simulator, family-permutation study, controlled
  known-winner study, or scientific mutation campaign is present.

**Rationale:** Important equations and edge cases have independent expected values, but major stochastic properties,
parameter recovery, heterogeneous-family behavior, and interactions still rely mainly on self-consistency.

**Limitation:** Acceptable requires direct behavioral validation for every core stochastic and competition method,
including known simulations or independent implementations and important edge cases.

### 3.9 Assumption and limitation transparency

**Grade: partial**

**Evidence**

- Source: every method document states purpose, domain, local choices, and limitations; KS and ACF caveats are careful.
- Literature: named methods can be distinguished from Trafficlab-defined smoothing, weighting, and search policies.
- Source: documents do not fully expose the MMPP arrival mismatch, lexical GA preference, fallback-use frequency,
  active-span rate effect, weight sensitivity, or fresh-seed versus held-out-data meaning.

**Rationale:** Core assumptions are documented, but several consequences and interactions that materially affect
interpretation lack diagnostics, controlled counterexamples, sensitivity evidence, or sufficiently precise language.

**Limitation:** Acceptable requires every method to state what its result does and does not support, including these
known interactions, failure signals, and bounds of interpretation.

## 4. Robustness

### 4.1 Input and artifact validation

**Grade: acceptable**

**Evidence**

- Fresh: broad strict-schema, corruption, replacement, and final-tree tests passed.
- Source: [artifact tests](../tests/unit/test_artifacts.py) cover malformed pairs, identities, replacement races,
  partial sets, stale state, publication collisions, and exact-owner recovery.
- Source: [run tests](../tests/unit/test_run.py) recheck identities before and after critical reads and reject changed,
  foreign, or incomplete trees.

**Rationale:** Strict parsing, cross-file validation, identity rechecks, and exact-tree ownership prevent malformed,
stale, partial, foreign, or changed inputs from supporting a success claim.

**Limitation:** A systematic fault matrix across every field, byte, timing, and artifact boundary has not been
independently demonstrated, preventing excellent evidence.

### 4.2 Bounded execution

**Grade: acceptable**

**Evidence**

- Fresh: 10 process-guard tests passed real wall-time, signal, ownership collision, and three-role hard-memory cases.
- Fresh: bounded broad and focused suites completed, and exact-name checks found no surviving pytest process or guard.
- Source: fake-clock and hanging-command tests cover capture, parsing, cleanup, model generation, candidate evaluation,
  and output limits.

**Rationale:** Explicit stage and descendant budgets are recalculated at boundaries; expiry blocks later work, and
partial output is not accepted. Real process trees demonstrate local containment under wall and memory limits.

**Limitation:** The fresh Docker matrix is indeterminate, and no independent repeated container/output-growth campaign
proves containment at every production boundary, preventing excellent evidence.

### 4.3 Failure semantics

**Grade: acceptable**

**Evidence**

- Source: [capture tests](../tests/unit/test_capture.py) exercise all event-priority pairs, the critical triple,
  natural and induced statuses, flush, validation, interruption, and cleanup precedence.
- Source: [run tests](../tests/unit/test_run.py) retain stage primary failures when logging or later work also fails.
- Fresh: these non-external failure matrices passed in the broad gate.

**Rationale:** A documented arbitration model preserves the exact primary cause, ordered secondary detail, stage
context, and natural versus induced outcomes instead of replacing science-relevant failures with cleanup noise.

**Limitation:** Real external-process statuses at every boundary and independent reconstruction from retained logs are
not available, so excellent evidence is not supported.

### 4.4 Atomic artifact publication

**Grade: acceptable**

**Evidence**

- Source: [artifact tests](../tests/unit/test_artifacts.py) cover fsynced private bytes, directory durability, ordered
  multi-file links, collisions, rollback, replacement, and preservation of unrelated winners.
- Source: checkpoint, model, history, generated, comparison, result, and report codecs publish validated canonical
  content through exclusive ownership rules.
- Fresh: broad publication and race tests passed.

**Rationale:** Private content is validated before documented publication order; identity-safe reuse and rollback
prevent incomplete or concurrently replaced sets from being treated as completed evidence.

**Limitation:** Crash injection and independent durability observation at every publish and rollback instruction are
absent, preventing the excellent anchor.

### 4.5 Recovery and resumability

**Grade: acceptable**

**Evidence**

- Source: [strategy tests](../tests/unit/genetic/test_strategy.py) compare resumed and uninterrupted population,
  history, winner, child IDs, repaired genes, and RNG state exactly.
- Source: checkpoint compatibility rejects changed Python, families, bounds, operators, metrics, seeds, population,
  and lineage before reproduction.
- Fresh: model and fit fixture regeneration was byte-identical.

**Rationale:** Resume restores scientific and RNG state under strict compatibility and produces the same authoritative
outcome at the tested boundary. Derived presentation artifacts are repaired without rewriting authoritative state.

**Limitation:** Multiple interruption points, a full corruption matrix, clean-environment resume, and retained real-run
equivalence are missing, preventing excellent recovery evidence.

### 4.6 Lifecycle cleanup

**Grade: acceptable**

**Evidence**

- Fresh: the Internet smoke passed teardown, and post-gate Docker inventory contained no labelled resource.
- Source: [cleanup tests](../tests/unit/test_cleanup.py) cover idempotence, zero budget, hung commands, inventory,
  remaining resources, signal failures, and exact-owner scoping.
- Source: [cleanup-boundary tests](../tests/integration/test_cleanup_boundary.py) use real subprocesses to prove bounded
  kill and no later Docker query after expiry.

**Rationale:** Cleanup is unconditional, bounded, idempotent, owner-scoped, descendant-aware, and honest about
possibly remaining resources when absence cannot be proven.

**Limitation:** The fresh Docker matrix has no conclusive result, and no independent real-resource matrix spans every
staged failure and concurrent identity reuse, preventing excellent evidence.

### 4.7 Adverse-condition behavior and diagnostics

**Grade: partial**

**Evidence**

- Fresh: broad malformed-input, numerical, timeout, process, and combined-failure tests passed.
- Source: CLI and stage errors preserve context and ordered detail; the retained-study audit identified the exact first
  missing realized configuration rather than claiming success.
- Retained: absent run artifacts prevent testing offline diagnosis from the evidence that a researcher would receive.

**Rationale:** Common adverse conditions yield actionable, non-success outcomes with useful stage and cause context.
Evidence does not establish that every expected external, numerical, disappearing-resource, and combined boundary
also identifies the affected scientific evidence and a concrete corrective action.

**Limitation:** Acceptable requires a complete boundary-to-diagnostic audit and retained offline reconstruction that
shows precise typed outcomes, authority, affected evidence, and correction for every expected failure class.

## 5. Reproducibility

### 5.1 Randomness control

**Grade: acceptable**

**Evidence**

- Fresh: scripted RNG and resume tests passed in the focused suite.
- Source: private seeded RNGs have fixed primitive order; checkpoint state retains the complete MT19937 state, exact
  CPython version, family order, trial seeds, and final seed.
- Source: invalid-candidate, duplicate-retry, cross-family, endpoint, and terminal-resume paths have direct draw-order
  tests.

**Rationale:** Every stochastic component has explicit seed ownership, retained lineage, stable draw contracts, and
lossless resume restoration within the pinned runtime.

**Limitation:** Independent reruns across multiple resume boundaries, deliberate draw-order mutations, and broader
invalid-path sequences are absent, preventing excellent evidence.

### 5.2 Environment reproducibility

**Grade: partial**

**Evidence**

- Fresh: locked sync and lock verification passed unchanged; CPython is pinned and strict typing passed.
- Retained: the prerequisite manifest records Python, platform, Docker Engine, Compose, an immutable target digest,
  a local capture image ID, source hashes, and exact guarded commands.
- Source: checkpoint reuse checks the exact Python runtime, but it does not enforce Docker, Compose, or platform
  compatibility. The capture image build starts from a tag and installs unpinned system packages.

**Rationale:** The Python toolchain and study environment are substantially recorded, but capture-image reconstruction
and non-Python runtime compatibility are not fixed or enforced, so a typical recreation can silently differ.

**Limitation:** Acceptable requires an immutable retrievable capture image or fully locked rebuild inputs, plus
compatibility checks for every recorded result-affecting Docker and runtime assumption before reuse.

### 5.3 Input preservation

**Grade: poor**

**Evidence**

- Retained: base configurations, prerequisites, result summaries, seeds, workload argv, and declared hashes remain.
- Retained: all ten realized configurations, run directories, captures, models, checkpoints, histories, generated
  traces, comparison results, and logs are absent; the ignored headers are not retrievable from the revision.
- Source: normal run artifacts carry strict identities, but generated run trees are intentionally ignored by Git.

**Rationale:** Some inputs are exactly named or hashed, but the reference captures and realized inputs needed to verify
the published study are lost from the retrievable evidence. Missing external and run evidence makes those historical
scientific inputs generally unreliable for later use.

**Limitation:** Partial requires preservation of the core exact inputs. Committing or immutably archiving each realized
configuration, reference capture, workload response evidence, and authoritative run tree would meet that threshold.

### 5.4 Artifact lineage

**Grade: partial**

**Evidence**

- Source: strict schemas and content hashes connect reference, effective config, checkpoint, best model, generated
  trace, comparison result, and final publication in normal run tests.
- Retained: the result manifest names nine hashes for each of ten runs and has internally consistent winner and config
  relationships.
- Retained: none of the 90 named artifact bytes is present, so the hashes and nested scientific values cannot be
  independently recomputed.

**Rationale:** Major artifact relationships have strong cryptographic identities and strict schemas, which is more
than filename association. Their complete retained chain cannot be validated because the content those identities
name is absent.

**Limitation:** Acceptable requires retrievable bytes and a successful complete-chain audit from reference and
effective configuration through fitting, generation, comparison, and publication.

### 5.5 Canonical serialization

**Grade: acceptable**

**Evidence**

- Fresh: all three deterministic fixture generators matched checked-in bytes exactly.
- Source: configuration, capture metadata, fitted model, checkpoint, similarity, prerequisite, and result tests reject
  unknown fields, duplicate keys, wrong exact types, nonfinite values, and incompatible schema values.
- Source: parse-render-parse and corruption tests preserve scientific values and canonical ordering.

**Rationale:** Scientific artifacts use strict, finite, value-preserving representations with deterministic rendering
and version checks across the declared format set.

**Limitation:** Independent parsers, broader nested mutation outside the owning implementation, and cross-platform
canonical-byte checks are absent, preventing excellent serialization evidence.

### 5.6 Fresh and resumed rerun equivalence

**Grade: partial**

**Evidence**

- Fresh: model, fit, and trace fixtures regenerated byte-identically.
- Source: the checkpoint strategy test proves resumed and uninterrupted population, history, winner, model state,
  child IDs, and RNG state are identical at the tested interruption boundary.
- Retained: the Validation Study reproduction summary is arithmetically consistent, but its source and reproduced artifacts are
  absent and are not credited as independent rerun proof.

**Rationale:** Fresh deterministic outputs and one fit-checkpoint resume boundary are exact, but complete fresh,
reused, and resumed result equivalence is not established.

**Limitation:** Acceptable requires a run-level equivalence test or audit covering resumed fit through generated trace,
comparison, and final publication, with every deterministic scientific value compared.

### 5.7 Protocol reproducibility

**Grade: partial**

**Evidence**

- Retained: committed protocol records fix workloads, order, repeats, seeds, model competition, final-seed validation,
  natural variation, failure policy, amendments, and report arithmetic.
- Retained: the audit confirmed balanced primaries and reproduction metadata, but all run evidence and realized configs
  are absent.
- Source: the reported final result is fresh-seed validation against the same reference, not held-out-data validation.

**Rationale:** Most procedure choices are explicit and predeclared, including rejected pilots and bound changes.
Evidence retention and reconstruction cannot be executed as written, and the single same-reference final seed limits
the strength and interpretation of the validation protocol.

**Limitation:** Acceptable requires a protocol whose exact retention and reconstruction steps succeed without
inventing missing decisions or evidence, with final-seed claims described consistently.

### 5.8 Independent reconstruction

**Grade: dreadful**

**Evidence**

- Fresh: the production audit failed immediately on the absent realized config for the first primary run.
- Retained: all 90 artifact targets and all ten run directories are absent from the assessed revision.
- Retained: manifest and report arithmetic can be recomputed, but raw captures, models, checkpoints, traces, and
  similarity inputs cannot be re-parsed or independently recalculated.

**Rationale:** Essential data are absent, so another researcher cannot reconstruct or verify the published scientific
result from retained files and documented commands. Rechecking the same summary JSON is not reconstruction.

**Limitation:** Poor would require a reconstructable general workflow with essential evidence still available, even if
substantial manual work were needed. The absent raw and intermediate artifacts must first be restored immutably.

## Cross-cutting limitations

The main scientific limitation is not software execution: it is the gap between exact scripted behavior and
independent validation of stochastic meaning. MMPP arrival alignment is materially wrong for its first scored IAT,
and heterogeneous GA ordering creates family-name preferences. Model recovery, distributional generation, metric
sensitivity, and controlled winner evidence remain too limited for stronger scientific claims.

The main empirical limitation is evidence retention. The committed Validation Study summaries are internally consistent but
cannot be audited back to packet bytes or reconstructed because every realized run artifact is missing. The passing
fresh Internet smoke establishes current basic real use only. It neither recreates the study nor turns the
indeterminate fresh Docker matrix into passing evidence.

The main interpretive limitation is that similarity values are descriptive. They are not likelihoods, calibrated
probabilities, proof of causal mechanism, or evidence that a selected model generalizes to an unseen capture. The
final seed reduces simulation-stream selection effects while reusing the same reference and fitted components.

## Reproduction commands for fresh evidence

Run from the repository root at the assessed source revision.

```bash
uv sync --locked --all-groups
uv lock --check
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
```

```bash
uv run --locked python scripts/generate_phase2_fixtures.py --check
uv run --locked python scripts/generate_model_fixtures.py --check
uv run --locked python scripts/generate_fit_fixtures.py --check
```

```bash
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -n 4 --dist worksteal \
  --cov=trafficlab --cov-branch --cov-report=term-missing \
  -m "not docker and not internet"
```

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 \
  tests/integration/test_process_guard.py
```

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 \
  tests/unit/models tests/unit/similarity tests/unit/genetic
```

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m docker
```

```bash
TRAFFICLAB_INTERNET_URL='https://cachefly.cachefly.net/10mb.test'
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m internet \
  --internet-url "$TRAFFICLAB_INTERNET_URL"
```
