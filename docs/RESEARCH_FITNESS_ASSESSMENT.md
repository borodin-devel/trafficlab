# Trafficlab Research Fitness Assessment

Original assessed product commit: `63f8c1b6da8a293bc65740890ca0ec7d0f479e1a`

Closure scientific source commit: `2f1537b0f0339bbf761a6c04a33035ee8fd26e8b`

Accepted-evidence commit: `c310900b139b806f45f13785bb1d204554515eef`

Original assessment date: 2026-08-14 (Europe/Moscow)

Closure reassessment date: 2026-08-17 (Europe/Moscow)

## Scope and method

This document applies the 37 independent anchors in the
[research-fitness rubric](../architecture/RESEARCH_FITNESS_CRITERIA.md) to the declared one-process research
prototype. The 2026-08-17 closure reassesses exactly the 17 criteria reopened by the Roadmap against the unchanged
rubric. The other 20 criteria retain their 2026-08-14 Acceptable grades. Enterprise hardening, multi-user operation,
distributed execution, hosted deployment, and feature breadth remain excluded unless they directly affect scientific
evidence.

Each criterion receives the highest label whose complete anchor is supported. A strong result in one criterion does
not compensate for a weak result in another. Missing or non-retrievable evidence limits a grade. Test volume and code
volume are not grading evidence by themselves.

Evidence provenance is explicit throughout:

- **Fresh reassessment** means a 2026-08-17 command or independent review against the closure source and accepted r18
  evidence.
- **Fresh** without a date remains evidence executed for the original 2026-08-14 assessment and is credited only to
  rows that were not reopened.
- **Source** means architecture, implementation, direct tests, or deterministic fixtures at the closure source.
- **Retained r18** means a checked artifact in the accepted
  [`2026-08-17-research-fitness-r18`](../examples/validation_study/evidence/2026-08-17-research-fitness-r18/)
  bundle.
- **Historical** means evidence preserved for context but not used to satisfy an amended closure gate.
- **Literature** means an external primary paper or authoritative mathematical definition.

The accepted bundle is an allowed evidence-only descendant of its recorded scientific source. The reassessment does
not treat later documentation as scientific source behavior.

## Evidence inventory

### Fresh closure command results

| Evidence | Result | Interpretation |
|---|---|---|
| Locked sync and lock check | Passed offline with no lock change | Exact Python dependency environment |
| Global Ruff and strict Pyright | Passed; 0 type errors, warnings, or informations | Static and documentation-adjacent quality |
| Three deterministic fixture checks | Checked-in paths and bytes matched | Canonical fixture evidence |
| Bounded parallel non-external gate | 3,402 passed in 49.94 seconds | Broad behavioral evidence |
| Bounded serial branch gate | 3,402 passed, 20 deselected in 425.37 seconds; 97.82% | Branch coverage above 90% |
| Retained r18 Docker matrix | 19 passed; 0 failed, skipped, or errored | Same-source controlled Docker prerequisite |
| Retained r18 Internet smoke | 1 passed; 0 failed, skipped, or errored | Same-source real HTTPS prerequisite |
| Current-tree r18 audit | Accepted 230 retained files | Complete current accepted bundle |
| Guarded no-hardlink clone audit | Accepted the same 230 retained files | Offline relocated reconstruction |
| Whole-branch independent review | No Critical or Important findings | Independent implementation/evidence review |
| Task 16 documentation re-review | No Critical, Important, or Minor findings | Independent closure review |

The external commands, stdout, stderr, JUnit, status, exact image identities, URL observation, source commit, source
tree, and lock identity are retained under r18. The external suites were not rerun after publication because the
accepted bundle binds them to the exact scientific source used for collection.

### Retained r18 evidence

The accepted r18 index and manifest independently enumerate 230 owned lineage entries. They retain nine strict
training trees, nine same-reference fresh simulations, three independent held-out bundles, nine portable/realized
configuration pairs, exact prerequisite records, protocol, environment, report inputs, and report. Candidate,
accepted-tree, current-tree, and guarded clone audits all accepted the same content. Missing, malformed, foreign,
lineage-substituted, and occupied-publication cases were rejected without mutating accepted evidence.

Earlier studies and failed attempt IDs remain historical or forensic evidence only. They are not used to satisfy a
closure grade and are not described as accepted results.

### Source and mathematical evidence

The [system architecture](../architecture/SYSTEM.md), [capture architecture](../architecture/CAPTURE.md),
[testing contract](../architecture/TESTING.md), model and metric specifications, implementation, and direct tests
provide traceable behavior. The bounded scientific matrix uses standard-library test-only oracles, predeclared seeds,
sample sizes, and tolerances. Pipeline-equivalence, canonical adverse-outcome, relocation, publication, and audit
matrices exercise the public production boundaries rather than producer-private reconstruction shortcuts.

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

- The accepted real study uses one approved HTTPS object, one linux/amd64 host, three workload shapes, three training
  captures per workload, and one held-out capture per workload.
- The direct scientific matrix establishes declared-process behavior but is not an exhaustive recovery, calibration,
  or mutation campaign over all feasible parameter combinations.
- Similarity components are descriptive trace diagnostics, not probabilities, likelihoods, causal evidence, or proof
  that the selected family generalizes beyond the retained experiment.
- The clean-clone reconstruction uses the checked Trafficlab auditor and strict public codecs; no second independent
  implementation of the complete pipeline was written.
- These limitations prevent Excellent grades where the rubric demands broader triangulation, repeated environments,
  exhaustive mutation, or independent reimplementation. They do not leave a reopened criterion below Acceptable.

## Section grade distributions

#### 1. Scientific precision and correctness of stage results

| Dreadful | Poor | Partial | Acceptable | Excellent |
|---:|---:|---:|---:|---:|
| 0 | 0 | 0 | 7 | 0 |

#### 2. Configurability

| Dreadful | Poor | Partial | Acceptable | Excellent |
|---:|---:|---:|---:|---:|
| 0 | 0 | 0 | 6 | 0 |

#### 3. Scientific precision and correctness of methods

| Dreadful | Poor | Partial | Acceptable | Excellent |
|---:|---:|---:|---:|---:|
| 0 | 0 | 0 | 9 | 0 |

#### 4. Robustness

| Dreadful | Poor | Partial | Acceptable | Excellent |
|---:|---:|---:|---:|---:|
| 0 | 0 | 0 | 7 | 0 |

#### 5. Reproducibility

| Dreadful | Poor | Partial | Acceptable | Excellent |
|---:|---:|---:|---:|---:|
| 0 | 0 | 0 | 8 | 0 |

## 1. Scientific precision and correctness of stage results

### 1.1 Preflight decision accuracy

**Grade: acceptable**

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Grade: acceptable**

**Evidence**

- Fresh reassessment: the bounded 3,402-test gates include exact RNG-order, endpoint, guard, schema, codec, and
  [`tests/scientific/test_model_validation.py`](../tests/scientific/test_model_validation.py) oracle cases.
- Source: schema 2 implements arrival-epoch MMPP initialization and conditioned time-zero arrival; all three families
  retain the exact fitted model, seed, observation window, limits, diagnostics, and input lineage.
- Retained r18: nine training and nine fresh-simulation traces plus three held-out traces were strictly reparsed and
  identity-checked by the current-tree and guarded clone audits.

**Rationale:** Events follow each declared fitted process and seed, remain ordered within the exact window and limits,
and round-trip through strict PCAPNG with model, configuration, reference, and environment lineage. The independent
statistical oracles directly cover the corrected MMPP alignment and every family contract.

**Limitation:** The real evidence covers three finite workloads and the scientific matrix is bounded rather than an
exhaustive parameter-space or cross-platform simulation campaign, preventing Excellent.

### 1.6 Comparison-result correctness

**Grade: acceptable**

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Grade: acceptable**

**Evidence**

- Fresh reassessment: current-tree and guarded no-hardlink clone audits each accepted all 230 r18 manifest entries;
  representative missing, malformed, foreign, substituted-lineage, and collision mutations were rejected.
- Source: [`tests/integration/test_pipeline_equivalence.py`](../tests/integration/test_pipeline_equivalence.py) and
  [`tests/integration/test_run_pipeline.py`](../tests/integration/test_run_pipeline.py) reconstruct final identities
  and reject stale, partial, changed, or incompatible stage trees.
- Retained r18: the index binds nine strict training trees, nine fresh simulations, and three held-out bundles to exact
  owners, content identities, protocol, environment, configurations, and report inputs.

**Rationale:** Success requires one strict final set whose bytes, identities, order, lineage, and outcomes agree. The
accepted study is a complete reconstructable result rather than a mixture of stale summaries or absent run trees.

**Limitation:** One retained study and the tested interruption/replacement matrix do not constitute repeated
independent operation across every compatible platform, preventing Excellent.

## 2. Configurability

### 2.1 Coverage of scientifically meaningful controls

**Grade: acceptable**

**Evidence**

- Fresh reassessment: configuration, comparison, candidate-evaluation, and report tests in the 3,402-test gate cover
  workload, model, search, seed, metric, weight, and reliability-limit variation without source edits.
- Source: the [mandatory aggregate contract](../architecture/similarity_methods/README.md#aggregate-fitness) makes all
  four methods explicit and mandatory; zero weight changes only aggregate contribution, never execution or diagnostics.
- Retained r18: portable configurations express three workload shapes and the controlled one-factor analysis changes
  only weights while retaining identical components, diagnostics, traces, and executed methods.

**Rationale:** Every result-affecting variable within the declared MVP is explicit and independently controllable where
independence is meaningful. Mandatory diagnostics are a declared scientific contract, not a hidden enablement rule.

**Limitation:** The representative study covers three experiment families rather than a broader controlled-variation
catalog, so the stronger Excellent evidence is not established.

### 2.2 Configuration semantics

**Grade: acceptable**

**Evidence**

- Fresh reassessment: strict configuration and zero-weight owner tests passed in the full bounded gates, including
  exact types, units, domains, defaults, precedence, feasibility, unknown fields, and cross-setting relationships.
- Source: the [portable/realized contract](../architecture/SYSTEM.md#portable-and-realized-configuration) and mandatory
  aggregate contract define stage ownership, path realization, method participation, and zero-weight behavior once.
- Retained r18: nine portable/realized pairs and the one-factor weight record preserve all four settings and diagnostics
  under both baseline and alternative aggregates.

**Rationale:** Documentation, validation, effective rendering, comparison, fitting, and reporting now give every
setting one consistent scientific meaning and interaction rule.

**Limitation:** No independent multi-user interpretation study demonstrates the Excellent anchor beyond executable
examples, strict snapshots, and reviewed documentation.

### 2.3 Configuration validation

**Grade: acceptable**

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Grade: acceptable**

**Evidence**

- Fresh reassessment: relocation, stage-reuse, resume, pipeline-equivalence, and mutation tests passed in the bounded
  gates; the guarded clone audit accepted the retained configurations at the recorded source.
- Source: strict snapshot identities are checked before and after stage reads and before reuse, resume, final
  publication, or report acceptance.
- Retained r18: every training index entry names byte-identified portable, realized, and run configurations; the audit
  proves the run configuration equals the realized configuration and that all downstream lineage consumes it.

**Rationale:** One normalized effective configuration contains every defaulted, resolved, derived, and scientific value
used by each stage, and the retained study makes that exact snapshot independently reloadable.

**Limitation:** Verification covers the declared stage and study paths but not every possible future stage or multiple
operating systems, preventing Excellent.

### 2.5 Stage-level reuse and controlled variation

**Grade: acceptable**

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Grade: acceptable**

**Evidence**

- Fresh reassessment: the relocation matrix passed 58 focused cases, and the final guarded no-hardlink clone recreated
  the locked environment and accepted all 230 r18 retained files without Docker or network access.
- Source: only `run.directory` and declared bind-mount host sources may realize differently; all scientific and workload
  values remain strict, and incompatible environment identities are rejected before reuse.
- Retained r18: nine portable/realized pairs, exact environment identities, source/tree binding, and immutable image
  references preserve both the transferable definition and its concrete realization.

**Rationale:** Transfer changes only declared environmental paths, retains both forms, and rejects incompatible
realization rather than silently changing scientific meaning.

**Limitation:** The clean relocation was on one compatible linux/amd64 host rather than multiple independent host
platforms, so Excellent is not supported.

## 3. Scientific precision and correctness of methods

### 3.1 Specification-to-implementation fidelity

**Grade: acceptable**

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Grade: acceptable**

**Evidence**

- Fresh reassessment: the bounded direct scientific matrix uses standard-library test-only oracles, predeclared seeds,
  samples, and tolerances for Poisson, Markov Renewal, and MMPP arrival, transition, occupancy, correlation, and marks.
- Source: exact scripted draw-order tests cover arrival-epoch initialization, conditioned time-zero arrival, state and
  mark choices, transitions, endpoints, degenerate cases, and every reliability stop.
- Retained r18: strict audits reparse finite generated traces from all three competing families' evaluations and bind
  published winners to current schema 2 semantics.

**Rationale:** Every draw, state transition, initialization, mark, and stop decision follows the declared process and
RNG contract, with direct empirical agreement against independent expectations.

**Limitation:** The bounded oracle matrix is not triangulated with a separate external simulator or exhaustive
parameter recovery study, preventing Excellent.

### 3.4 Model-competition fairness

**Grade: acceptable**

**Evidence**

- Fresh reassessment: priority, quota, permutation, equal-fitness, known-winner, invalid-candidate, checkpoint, and
  resumed-equivalence tests passed in the bounded gates and received phase and whole-branch independent review.
- Source: one seed-derived neutral `family_priority` owns quota remainders, initial order, cross-family ties, champions,
  elites, and tournament ties; all families share references, windows, metrics, trial seeds, and limits.
- Retained r18: every training run evaluates all three enabled families under the same recorded settings and selection
  seeds, then freezes the training-only winner before held-out evaluation.

**Rationale:** Family-specific chromosomes use explicit neutral rules without lexical or configuration-order privilege,
and all competitors receive scientifically comparable data, metrics, seed policy, and bounds.

**Limitation:** Three workloads, two selection seeds, and small research-prototype budgets do not establish broad
resource-fairness behavior across all feasible model difficulties, preventing Excellent.

### 3.5 Similarity-method correctness

**Grade: acceptable**

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Grade: acceptable**

**Evidence**

- Fresh reassessment: 3,402 bounded tests include hand-calculated similarity cases, analytical invariants, independent
  standard-library stochastic oracles, known simulations, family-order controls, and strict edge cases.
- Source: [`tests/scientific/oracles.py`](../tests/scientific/oracles.py) does not call production fitting or generation
  helpers when deriving expected statistics, draw races, occupancy, or tolerance decisions.
- Retained r18: real captures, generated traces, natural-variation pairs, fixed-seed simulations, held-out comparisons,
  and a one-factor weight analysis supplement the controlled evidence without being treated as universal validation.

**Rationale:** Every core model, metric, aggregate, and heterogeneous-competition method has direct behavioral evidence
capable of detecting scientific errors, including important finite, boundary, and invalid cases.

**Limitation:** No exhaustive scientific mutation campaign or second independent implementation triangulates every
method across a large real-data corpus, preventing Excellent.

### 3.9 Assumption and limitation transparency

**Grade: acceptable**

**Evidence**

- Fresh reassessment: final independent documentation re-review found no Critical, Important, or Minor finding after
  its initial closure-document findings were resolved and every affected gate was rerun.
- Source: model, genetic, similarity, capture, and testing documents state domains, estimator choices, local policies,
  failure signals, bounds, mandatory diagnostics, neutral family rules, and current schema semantics.
- Retained r18: the report separates training selection, repeated-capture variation, same-reference fresh simulation,
  and genuine held-out evidence, exposes weak multiscale scores and weight sensitivity, and limits generalization claims.

**Rationale:** Assumptions, conventions, diagnostics, failure meaning, and supported conclusions are explicit enough to
prevent the prior MMPP, lexical-priority, zero-weight, and fresh-seed interpretation errors.

**Limitation:** The report contains one bounded sensitivity analysis rather than controlled counterexamples and
sensitivity studies for every documented limitation, preventing Excellent.

## 4. Robustness

### 4.1 Input and artifact validation

**Grade: acceptable**

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Grade: acceptable**

**Evidence**

- Fresh reassessment: the canonical adverse-condition fixture passed 50 cases in a guarded clone, and the full bounded
  gates passed malformed-input, extreme-finite, timeout, process, replacement, combined-failure, and cleanup cases.
- Source: public failure-outcome matrices require typed stage, cause, affected evidence, evidence state, authority,
  correction, exact status where known, secondary detail, and possibly remaining resource inventory.
- Retained r18: offline mutation and publication-collision cases reject invalid work with canonical diagnostics while
  preserving candidate and accepted inventories; external prerequisites retain exact command and status evidence.

**Rationale:** Every declared expected failure boundary produces an actionable typed non-success result without
publishing invalid scientific evidence or replacing the primary cause with cleanup noise.

**Limitation:** Real external-tool behavior is retained for one Docker/Internet environment rather than every supported
failure combination and host, preventing Excellent.

## 5. Reproducibility

### 5.1 Randomness control

**Grade: acceptable**

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Grade: acceptable**

**Evidence**

- Fresh reassessment: locked offline sync, lock check, deterministic fixtures, strict typing, current-tree audit, and
  guarded no-hardlink clone audit passed against the final accepted-evidence head.
- Source: CPython 3.12 is pinned; the capture image uses an exact base digest, dated snapshot, direct package versions,
  reproducible build inputs, expected content identity, platform checks, and capture-tool compatibility.
- Retained r18: environment and prerequisites bind CPython 3.12.3, lock SHA-256, source commit/tree, Docker Engine and
  Compose versions, linux/amd64, immutable target/capture image IDs, dumpcap 4.0.17, and schema 2.

**Rationale:** Every result-affecting interpreter, package, tool, image, source, and runtime assumption is locked or
exactly identified and checked before reuse. The same-source Docker and Internet prerequisites passed before collection.

**Limitation:** Reproduction was demonstrated on one compatible linux/amd64 environment rather than multiple clean host
implementations, preventing Excellent.

### 5.3 Input preservation

**Grade: acceptable**

**Evidence**

- Fresh reassessment: current-tree and guarded clone audits accepted the same 230 manifest-retained bytes and rejected
  representative missing, malformed, foreign, and substituted inputs.
- Retained r18: exact portable/realized configurations, references, workload argv, bounds, seeds, checkpoints, models,
  generated traces, comparisons, logs, headers, observations, prerequisite results, and environment are retained.
- Retained r18: the canonical manifest stores path, size, SHA-256, owner, and lineage for every file; the index contains
  the same 230 ownership and lineage entries.

**Rationale:** Every result-affecting scientific and external input is stored exactly or identified immutably, including
raw references, effective settings, workload behavior, model bounds, seed policy, and environment observations.

**Limitation:** Mutation audits cover representative input classes rather than every byte and field in every retained
file across multiple storage implementations, preventing Excellent.

### 5.4 Artifact lineage

**Grade: acceptable**

**Evidence**

- Fresh reassessment: the production auditor reconstructed all 230 r18 owner/lineage entries in the current tree and a
  guarded no-hardlink clone; lineage substitution and foreign/replaced evidence were rejected.
- Source: strict schemas and content identities connect reference, effective configuration, checkpoint, history,
  best model, generated trace, similarity result, final result, fresh simulation, and held-out publication.
- Retained r18: index, manifest, training, fresh-simulation, and held-out records expose each exact input/output identity
  and bind the report and report inputs to the same complete evidence graph.

**Rationale:** Exact identities and strict schemas form a complete validated chain from reference and configuration
through fitting, generation, comparison, selection, held-out evaluation, and publication.

**Limitation:** The evidence does not repeat the complete replacement, resume, race, and lineage-mutation matrix on
multiple filesystems and independent implementations, preventing Excellent.

### 5.5 Canonical serialization

**Grade: acceptable**

**Evidence status:** The evidence, rationale, and limitation below are the original 2026-08-14 snapshot. They are
historical context, not current closure inventory; this already-Acceptable row was not reopened or regraded.

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

**Grade: acceptable**

**Evidence**

- Fresh reassessment: [`tests/integration/test_pipeline_equivalence.py`](../tests/integration/test_pipeline_equivalence.py)
  passed within the full gate and independently reconstructs exact checkpoint, model, generated, comparison, result,
  failure-log, and lineage identities for uninterrupted, resumed, and reused paths.
- Source: checkpoint tests preserve population, history, family priority, winner, IDs, complete MT19937 state, schema,
  settings, and compatibility before any resumed draw; deterministic fixtures regenerate byte-identically.
- Retained r18: nine fresh-simulation records distinguish expected seeded simulation variation from authoritative input
  lineage, and the clean-clone audit reconstructs their exact reference/model/generated/comparison identities.

**Rationale:** Equivalent fresh, reused, and resumed executions reproduce every deterministic scientific value through
final publication while explicitly identifying external observations that are not promised byte-identical.

**Limitation:** Equivalence is tested at the declared interruption/reuse boundaries rather than many independent hosts
and every possible interruption instruction, preventing Excellent.

### 5.7 Protocol reproducibility

**Grade: acceptable**

**Evidence**

- Fresh reassessment: the current-tree and guarded clone audits reconstructed the r18 protocol and report, and the
  independent whole-branch review found no Critical or Important protocol/evidence mismatch.
- Retained r18: protocol freezes workloads, balanced order, three repeats, selection seeds 17 and 29, final seed 97,
  failure/no-retry rules, training-only selection, one independent held-out capture per workload, and exclusive publish.
- Retained r18: exact prerequisite and collection commands, outcomes, failed-attempt policy, natural variation, fresh
  simulation, held-out results, environment, configurations, report inputs, and limitations are retained separately.

**Rationale:** Another researcher need not invent any material workload, order, seed, competition, failure, selection,
validation, retention, or reporting decision, and exploratory/training/fresh/held-out claims remain distinct.

**Limitation:** One successful retained execution does not establish repeated independent protocol execution across
multiple endpoints, hosts, or operators, preventing Excellent.

### 5.8 Independent reconstruction

**Grade: acceptable**

**Evidence**

- Fresh reassessment: the current-tree audit and a socket-, Docker-, shell-, and subprocess-guarded no-hardlink clone
  audit each accepted all 230 r18 files from the recorded source and locked environment without network or Docker.
- Source: the offline auditor directly reloads strict public codecs, reparses PCAPNG, recomputes hashes, aggregates,
  natural variation, selection, held-out bindings, report inputs, ownership, lineage, and manifest consistency.
- Fresh reassessment: representative missing, malformed, foreign, substituted-lineage, and publication-collision clone
  cases failed canonically; Task 16 review found no reconstruction-specific Critical or Important issue.

**Rationale:** A compatible researcher can use retained inputs, strict artifacts, pinned tools, and the documented
bounded command to reconstruct and verify every published scientific value without original process state or external
services.

**Limitation:** The reconstruction uses the checked Trafficlab auditor on one compatible host rather than a separately
implemented complete analysis run by an independent operator, preventing Excellent.

## Cross-cutting limitations

All 17 reopened criteria now meet the unchanged Acceptable anchor within the declared one-process MVP scope. The
remaining scientific limitation is evidence breadth rather than a known method contradiction: bounded direct oracles
cover every core method, but no exhaustive recovery, calibration, or deliberate scientific-mutation campaign spans all
parameters and model interactions.

The accepted r18 study is complete and independently auditable from retained bytes, but it remains a finite experiment
on one host, one approved HTTPS object, three workload shapes, three training captures per workload, and one held-out
capture per workload. It must not be generalized to unseen programs, endpoints, networks, platforms, or model classes.

Similarity values remain descriptive. They are not likelihoods, calibrated probabilities, proof of causal mechanism,
or evidence that the selected family generalizes beyond the retained observations. The report preserves weak
components and weight sensitivity rather than hiding them behind the aggregate.

## Reproduction commands for fresh closure evidence

Run from the repository root at the accepted-evidence revision. The external Docker and Internet commands and their
passing results are retained in r18 `prerequisites.json`; the closure audit itself is offline.

```bash
UV_OFFLINE=1 uv sync --locked --all-groups
UV_OFFLINE=1 uv lock --check
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
  -m "not docker and not internet"
```

```bash
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 \
  -m "not docker and not internet" \
  --cov=trafficlab --cov-branch --cov-fail-under=90 \
  --cov-report=term-missing
```

```bash
UV_OFFLINE=1 scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/2026-08-17-research-fitness-r18 \
  --repository .
```
