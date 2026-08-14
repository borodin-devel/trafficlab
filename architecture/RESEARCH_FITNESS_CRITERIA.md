# Research Prototype Fitness Criteria

## Purpose

This document defines criteria for judging whether a project is scientifically
fit for the following research purpose:

1. capture the network traffic of a containerized program;
2. fit competing classical stochastic models to that reference traffic;
3. generate a synthetic PCAPNG trace from a selected fitted model; and
4. measure how closely the synthetic trace resembles the reference capture.

It is a rubric, not an assessment of Trafficlab. It assigns no current grade,
contains no measurements, and draws no conclusion about the implementation.

The rubric evaluates only:

- scientific precision and correctness of each stage's results;
- configurability;
- scientific precision and correctness of the methods;
- robustness; and
- reproducibility.

It does not reward unnecessary program complexity. It also does not require or
grade enterprise hardening, multi-user operation, distributed execution,
authentication, hosted services, or a general security subsystem. Such matters
are relevant only when they directly affect scientific correctness, safe local
execution, preservation of evidence, or reproducibility.

## How to use the rubric

Grade every criterion independently. Do not calculate an overall grade, assign
weights, convert labels to numbers, or let strength in one criterion compensate
for weakness in another.

Use the highest grade whose complete anchor is supported by relevant evidence:

- **Dreadful:** the capability is absent or fundamentally invalid, so its
  outputs cannot support credible research.
- **Poor:** the capability exists, but material errors, uncontrolled
  assumptions, or missing evidence make its results generally unreliable.
- **Partial:** important paths are correct, but significant limitations or
  inconsistencies restrict scientific use and require explicit qualification.
- **Acceptable:** behavior is correct and sufficiently evidenced throughout the
  project's declared scope, assumptions, input domain, and precision.
- **Excellent:** correctness has unusually strong, independent, transparent,
  and representative evidence within that same scope.

Feature count is not evidence of fitness. An excellent grade never requires a
broader product scope than an acceptable grade. It requires stronger scientific
quality and evidence for the scope that was actually declared.

When behavior varies across the declared domain, ordinary material failures
cannot be averaged away by stronger cases. Missing evidence limits the grade
even when the implementation appears plausible. Applicable-evidence notes below
name suitable kinds of evidence; they do not assert that such evidence exists.

## 1. Scientific precision and correctness of stage results

Stage-result criteria judge the observable scientific evidence produced by a
stage. They do not substitute for judging whether the underlying methods are
scientifically valid.

### 1.1 Preflight decision accuracy

**Evaluation question:** Does preflight correctly decide whether the exact
configured experiment can run and produce interpretable evidence?

- **Dreadful:** There is no meaningful preflight, or it routinely approves an
  environment in which the declared experiment cannot run or cannot produce
  scientifically interpretable results.
- **Poor:** Checks are superficial or disconnected from the effective
  experiment. Material false approvals and false rejections are common, and
  the decision cannot be traced to exact prerequisites.
- **Partial:** Common prerequisites are checked correctly, but important
  scientific requirements or interactions are omitted, inconsistently checked,
  or checked differently from the real run.
- **Acceptable:** Every prerequisite necessary within the declared scope is
  checked against the effective configuration using the same semantics as the
  real run. Failures identify the unmet condition without claiming readiness.
- **Excellent:** Acceptable behavior is additionally supported by controlled
  positive and negative cases for each prerequisite, including boundary and
  changed-environment cases that demonstrate low false-approval and
  false-rejection risk.

**Applicable evidence:** controlled prerequisite mutations, exact command and
configuration records, known-ready and known-unready environments, and
comparison between preflight conclusions and subsequent stage behavior.

### 1.2 Capture fidelity

**Evaluation question:** Does the reference capture faithfully represent the
declared traffic of the target container during the declared observation
boundary?

- **Dreadful:** The capture cannot be attributed to the target, observes the
  wrong interface or traffic domain, or omits or contaminates traffic so
  severely that the reference is scientifically meaningless.
- **Poor:** Target attribution is weak, capture boundaries are unclear, or
  material packet loss, unrelated traffic, direction errors, or timestamp
  errors can occur without detection.
- **Partial:** Typical captures represent the target reasonably, but known
  traffic classes, directions, lifecycle boundaries, or loss conditions remain
  ambiguous and materially restrict interpretation.
- **Acceptable:** The target, interface, traffic boundary, start and stop
  conditions, direction convention, timestamp basis, and loss/error behavior
  are explicit and correct for the declared scope.
- **Excellent:** Acceptable fidelity is independently demonstrated with
  controlled traffic of known timing, size, direction, address, and protocol
  under representative lifecycle and traffic-rate variation.

**Applicable evidence:** controlled TCP and UDP exchanges, outbound and inbound
unicast and broadcast traffic, independent packet inspection, packet-count
oracles, capture-loss indicators, and known lifecycle timing.

### 1.3 Capture artifact correctness

**Evaluation question:** Do the capture metadata and PCAPNG bytes describe the
same complete, ordered reference trace?

- **Dreadful:** Required artifacts are missing, malformed, mutually unrelated,
  or accepted despite an unsupported or unknowable packet interpretation.
- **Poor:** Metadata and packet bytes are only loosely connected. Window,
  address, direction, link-type, timestamp, or packet-count disagreement can be
  silently accepted.
- **Partial:** Core fields agree in ordinary cases, but strict schema,
  cross-artifact identity, endpoint, ordering, or malformed-input validation is
  incomplete.
- **Acceptable:** Metadata and PCAPNG are strictly parsed, mutually validated,
  ordered, bounded by one explicit observation window, and connected by exact
  identity or lineage evidence.
- **Excellent:** Acceptable behavior is supported by independent parsing,
  round-trip checks, precise timestamp-resolution cases, mutation tests for
  every cross-artifact invariant, and representative real captures.

**Applicable evidence:** strict codec tests, packet-by-packet comparisons,
independent PCAPNG readers, timestamp-resolution fixtures, hash lineage, and
single-field or single-byte corruption cases.

### 1.4 Fit result correctness

**Evaluation question:** Does the fit stage report the candidate, parameters,
fitness, diagnostics, and winner that were actually evaluated?

- **Dreadful:** The reported model or parameters are not demonstrably derived
  from the reference, or the reported winner can differ from the evaluated
  winner without detection.
- **Poor:** Some fitting occurs, but candidate inputs, scores, seeds, bounds, or
  selection decisions are missing, stale, inconsistent, or irreproducible.
- **Partial:** The main fitting path is credible, but some candidate states,
  invalid-candidate handling, tie decisions, final validation, or artifact
  lineage is incomplete.
- **Acceptable:** Every reported candidate and winner is linked to exact inputs,
  parameters, bounds, seeds, method settings, component results, aggregate
  fitness, and deterministic selection rules. The published model matches the
  final validated winner.
- **Excellent:** Acceptable correctness is additionally established with
  analytically tractable fits, controlled family competitions, mutation of
  retained state, independent winner reconstruction, and fresh versus resumed
  equivalence.

**Applicable evidence:** known-parameter simulations, hand-computed candidate
fitness, complete checkpoint histories, stable tie cases, invalid-candidate
cases, independent winner selection, and fitted-model round trips.

### 1.5 Generated-trace correctness

**Evaluation question:** Does the generated event sequence and PCAPNG faithfully
represent the selected fitted model within the configured observation window?

- **Dreadful:** Generated packets are malformed, unrelated to the fitted model,
  outside the intended observation window, or not interpretable as the declared
  traffic representation.
- **Poor:** Generation uses some fitted information, but timing, marks,
  directions, ordering, bounds, seed behavior, or PCAPNG encoding is materially
  wrong or unverifiable.
- **Partial:** Ordinary generation is plausible and parseable, but important
  model semantics, endpoints, resource guards, exact-byte publication, or
  model-to-artifact lineage remains incomplete.
- **Acceptable:** Events follow the selected fitted model and configured seed,
  remain ordered and within the exact window, satisfy all declared limits, and
  round-trip through a valid PCAPNG with exact model and input lineage.
- **Excellent:** Acceptable correctness is independently checked against direct
  model simulation, deterministic event oracles, timestamp-resolution
  boundaries, distributional expectations, and repeated codec round trips.

**Applicable evidence:** scripted RNG tests, known event sequences, statistical
simulation checks, packet-byte round trips, boundary-resolution PCAPNG files,
hash lineage, and generation-limit fault cases.

### 1.6 Comparison-result correctness

**Evaluation question:** Does the comparison artifact report exactly the values
obtained from its actual reference trace, generated trace, and settings?

- **Dreadful:** The comparison does not use the claimed inputs, or its reported
  values cannot be reconstructed from retained evidence.
- **Poor:** Results are produced, but input identity, trace alignment, component
  calculations, weights, diagnostics, or aggregate calculations are materially
  wrong or undocumented.
- **Partial:** Main calculations are correct in ordinary cases, but some input
  lineage, endpoint handling, component diagnostics, aggregation, or malformed
  input behavior is incomplete.
- **Acceptable:** Strictly parsed and aligned traces feed every configured
  method; all component values, diagnostics, weights, and aggregate results are
  correct and bound to exact input and settings identities.
- **Excellent:** Acceptable correctness is additionally demonstrated by hand
  calculations, independent recomputation, controlled equal and maximally
  different traces, direction-sensitive cases, and boundary mutations.

**Applicable evidence:** published hand examples, independent comparison code,
input-hash checks, component-to-aggregate reconstruction, controlled trace
pairs, and strict artifact mutation tests.

### 1.7 End-to-end result consistency

**Evaluation question:** Does a complete experiment publish one internally
consistent scientific result rather than a mixture of stale, partial, or
unrelated stage outputs?

- **Dreadful:** A completed run can combine unrelated inputs or artifacts, omit
  essential evidence, or report success despite a failed or incomplete stage.
- **Poor:** Stage outputs usually exist but lack authoritative ownership,
  cross-stage lineage, final validation, or an exact definition of completion.
- **Partial:** The normal path is internally consistent, but reuse, retries,
  concurrent replacement, final-tree validation, or secondary failures can
  create ambiguous results.
- **Acceptable:** Completion requires a strict final artifact set whose contents,
  identities, ordering, lineage, and stage outcomes agree. Stale, foreign,
  partial, or changed artifacts prevent a success claim.
- **Excellent:** Acceptable consistency is independently reconstructed from the
  retained tree and demonstrated across fresh runs, valid reuse, interruption,
  retry, corruption, and concurrent-replacement scenarios.

**Applicable evidence:** final-tree validators, full lineage graphs, exact
artifact-name checks, race injection, interrupted-stage matrices, independent
offline reconstruction, and fresh complete experiments.

## 2. Configurability

Configurability means controlled scientific variation with exact semantics. It
does not mean exposing every internal value as an option.

### 2.1 Coverage of scientifically meaningful controls

**Evaluation question:** Can a researcher control the variables needed to
define, bound, and repeat the declared experiment without manipulating source
code or irrelevant internals?

- **Dreadful:** Essential scientific variables are fixed, hidden, or changed
  only through source edits, so the intended experiment cannot be defined.
- **Poor:** A few settings exist, but major workload, capture, model, generation,
  comparison, seed, or limit choices remain implicit or coupled incorrectly.
- **Partial:** Most common studies are configurable, but some material variables
  cannot be controlled independently or require undocumented workarounds.
- **Acceptable:** Every scientifically meaningful variable within the declared
  scope is explicit and independently controllable where independence is
  meaningful; implementation-only details remain hidden.
- **Excellent:** Acceptable coverage is supported by representative experiment
  families showing that substantial scientific variation requires configuration
  changes only, without a proliferation of redundant or ambiguous settings.

**Applicable evidence:** a setting-to-research-question map, diverse checked
configurations, controlled one-factor variations, and absence of source changes
across representative experiments.

### 2.2 Configuration semantics

**Evaluation question:** Does every setting have one precise scientific meaning,
unit, domain, default, and interaction with other settings?

- **Dreadful:** Settings are undocumented or ambiguous enough that two users can
  reasonably run scientifically different experiments from the same file.
- **Poor:** Names and defaults exist, but units, bounds, precedence, stage
  ownership, or interactions are materially unclear.
- **Partial:** Common settings are precise, but some edge cases, derived values,
  overrides, or cross-setting relationships remain ambiguous.
- **Acceptable:** Every setting has an exact type, unit, domain, default,
  precedence, owning stage, and interaction rule consistent across documentation
  and implementation.
- **Excellent:** Acceptable semantics are reinforced by executable examples,
  exact effective-configuration rendering, boundary cases, and independent
  interpretation yielding the same experiment definition.

**Applicable evidence:** configuration reference tables, schema tests, unit and
boundary examples, effective snapshots, and independent configuration reviews.

### 2.3 Configuration validation

**Evaluation question:** Are invalid, contradictory, ambiguous, or
scientifically meaningless configurations rejected before they affect results?

- **Dreadful:** Arbitrary or malformed values are accepted and can silently
  produce invalid results or undefined behavior.
- **Poor:** Basic syntax is checked, but material type, range, relationship,
  unknown-field, or feasibility errors reach later stages.
- **Partial:** Most invalid values are rejected, but cross-field invariants,
  numerical edge cases, derived feasibility, or exact error ownership is
  incomplete.
- **Acceptable:** Validation is strict, typed, finite, range-aware,
  relationship-aware, rejects unknown input, and proves feasibility required by
  every consuming stage before scientific work begins.
- **Excellent:** Acceptable validation is mutation-tested across all fields and
  important interactions, including exact boundaries, extreme numeric values,
  and configurations that are locally valid but globally impossible.

**Applicable evidence:** exhaustive field mutation, cross-field matrices,
boundary and overflow tests, unknown-field cases, and proof that invalid input
causes no scientific artifact publication.

### 2.4 Effective-configuration fidelity

**Evaluation question:** Is the configuration actually used by every stage
identical to the retained effective configuration?

- **Dreadful:** The used configuration is unknown, or stages silently use values
  that differ from the declared or retained configuration.
- **Poor:** Some values are recorded, but defaults, normalization, paths,
  derived values, or stage-specific interpretations can diverge.
- **Partial:** Normal executions retain a mostly accurate snapshot, but reuse,
  resume, independent stages, or environment-derived values can escape it.
- **Acceptable:** One strict effective configuration includes every normalized,
  defaulted, resolved, and derived value that affects science, and all stages
  prove they consume that exact snapshot.
- **Excellent:** Acceptable fidelity is independently reconstructed and verified
  across full runs, separate stages, resume, reuse, and configuration-mutation
  attempts.

**Applicable evidence:** canonical snapshots, snapshot hashes in artifacts,
stage-boundary assertions, independent reloads, resume compatibility checks,
and deliberate post-snapshot mutation.

### 2.5 Stage-level reuse and controlled variation

**Evaluation question:** Can researchers repeat or vary one stage without
unintentionally changing the authoritative inputs of other stages?

- **Dreadful:** Stages cannot be separated, or repeating one stage silently
  changes unrelated evidence and destroys experimental comparability.
- **Poor:** Some manual reuse is possible, but ownership, compatibility, or
  invalidation rules are unclear and stale evidence is easily accepted.
- **Partial:** Common reuse paths work, but important parameter changes,
  checkpoint resume, artifact replacement, or downstream invalidation is
  incomplete.
- **Acceptable:** Stage boundaries, authoritative inputs, compatibility rules,
  reusable outputs, and invalidation effects are explicit and enforced.
  Controlled changes affect only the scientifically dependent stages.
- **Excellent:** Acceptable behavior is demonstrated with a systematic variation
  matrix covering independent reruns, compatible reuse, incompatible changes,
  resume, and exact downstream reconstruction.

**Applicable evidence:** stage dependency tables, one-factor experiment
variations, reuse and incompatibility tests, unchanged-input hashes, and
reconstructed downstream outputs.

### 2.6 Portability of experiment definitions

**Evaluation question:** Can an experiment definition be transferred to another
compatible environment without silently changing its scientific meaning?

- **Dreadful:** Configurations depend on undocumented machine state or embedded
  local assumptions and cannot be interpreted elsewhere.
- **Poor:** Transfer is possible only through extensive manual editing, with no
  distinction between environmental realization and scientific configuration.
- **Partial:** Typical paths and resources can be adapted, but some environment
  substitution, resolved value, image, or tool assumption changes scientific
  behavior invisibly.
- **Acceptable:** Portable definitions separate scientific values from explicit
  environment realization, retain both forms where necessary, and reject
  incompatible realization rather than silently changing meaning.
- **Excellent:** Acceptable portability is demonstrated on multiple compatible
  clean environments with identical effective scientific values and explained,
  bounded differences only where the environment necessarily contributes.

**Applicable evidence:** portable and realized configuration pairs, clean-clone
experiments, environment compatibility records, canonical snapshots, and
cross-machine value comparisons.

## 3. Scientific precision and correctness of methods

Method criteria judge whether the algorithms themselves are scientifically
valid for their declared assumptions. Correct artifact plumbing cannot make an
incorrect method fit.

### 3.1 Specification-to-implementation fidelity

**Evaluation question:** Do implemented equations, estimators, conventions, and
algorithms exactly match their declared scientific definitions?

- **Dreadful:** Methods lack a precise definition or materially contradict the
  definition they claim to implement.
- **Poor:** Broad intent matches, but important equations, normalizations,
  estimators, sampling rules, or conventions are wrong or omitted.
- **Partial:** Core formulas are correct, but boundary conventions, estimator
  choices, data preparation, or algorithm steps remain inconsistent or vague.
- **Acceptable:** Every implemented method has a complete mathematical or
  algorithmic specification, and implementation behavior matches it over the
  declared domain, including explicit local design choices.
- **Excellent:** Acceptable fidelity is supported by independent derivation,
  reference calculations or implementations, mutation-sensitive tests, and
  traceable authoritative citations where applicable.

**Applicable evidence:** equation-to-code maps, published hand calculations,
independent implementations, mutation tests, authoritative citations, and
explicit local conventions.

### 3.2 Model-fitting correctness

**Evaluation question:** Are model parameters and empirical components estimated
correctly from the reference under each model's declared assumptions?

- **Dreadful:** Estimates are unrelated to the data, mathematically invalid, or
  outside the model's valid parameter space.
- **Poor:** Some estimators are plausible, but material bias, invalid states,
  incorrect conditioning, or unhandled degeneracy makes fits unreliable.
- **Partial:** Standard cases fit correctly, but sparse, empty, constant,
  boundary, state-assignment, smoothing, or repair behavior has significant
  gaps.
- **Acceptable:** Estimation, conditioning, smoothing, empirical distributions,
  parameter repair, and degenerate cases are correct and explicit for each
  declared model family.
- **Excellent:** Acceptable correctness is confirmed with known-parameter data,
  analytical estimators, independent implementations, and systematic sparse,
  boundary, and misspecification studies.

**Applicable evidence:** synthetic data with known parameters, analytical fits,
state and transition counts, empirical-distribution reconstruction, degenerate
fixtures, and independent estimator comparison.

### 3.3 Stochastic-generation correctness

**Evaluation question:** Does each model generate samples from the stochastic
process it claims to represent?

- **Dreadful:** The generator does not implement the claimed process or produces
  invalid event sequences.
- **Poor:** The process shape is recognizable, but transition, holding-time,
  arrival, mark, initial-state, or termination sampling is materially wrong.
- **Partial:** Typical draws follow the model, but RNG order, conditional marks,
  stationary initialization, boundary termination, or degenerate behavior is
  incomplete.
- **Acceptable:** Every draw, state transition, mark, initialization, and stop
  decision follows the declared process and exact RNG contract within all
  configured bounds.
- **Excellent:** Acceptable correctness is supported by scripted draw-order
  oracles and large controlled simulations whose empirical properties agree
  with independently derived expectations within predeclared tolerances.

**Applicable evidence:** scripted RNGs, exact short sequences, transition and
arrival frequency studies, stationary-distribution checks, mark-distribution
checks, and independent simulators.

### 3.4 Model-competition fairness

**Evaluation question:** Are competing model families evaluated and selected
under scientifically comparable conditions?

- **Dreadful:** Models use incomparable inputs, metrics, windows, or undisclosed
  advantages, so the declared winner has no valid meaning.
- **Poor:** Some conditions are shared, but budgets, seeds, bounds, invalid-case
  handling, selection, or final validation materially favors particular models.
- **Partial:** Normal competition is mostly comparable, but family allocation,
  reproduction, ties, repair, stopping, or held-out validation has meaningful
  asymmetry or ambiguity.
- **Acceptable:** All families receive the same authoritative data, window,
  metrics, trial policy, reliability limits, and declared resource policy;
  family-specific chromosomes are handled by explicit neutral rules.
- **Excellent:** Acceptable fairness is supported by order-invariance,
  equal-fitness, family-permutation, budget, invalid-candidate, and controlled
  known-winner experiments.

**Applicable evidence:** family-order permutations, equal-fitness populations,
quota and seed records, symmetric failure cases, known-winner simulations, and
independent selection reconstruction.

### 3.5 Similarity-method correctness

**Evaluation question:** Does each similarity method correctly calculate and
measure its stated traffic property?

- **Dreadful:** Scores do not implement their claimed statistic or cannot be
  interpreted in relation to reference and generated traffic.
- **Poor:** Basic calculations exist, but normalization, alignment, direction,
  lag, bin, sample, or range handling is materially wrong.
- **Partial:** Typical scores are correct, but constants, empty samples,
  endpoints, small windows, merged supports, or method-specific boundaries have
  important gaps.
- **Acceptable:** Every method exactly implements its stated statistic,
  preconditions, range, normalization, diagnostics, edge conventions, and
  interpretation for the declared domain.
- **Excellent:** Acceptable correctness is independently demonstrated with hand
  calculations, analytical equal and extreme cases, direction reversals,
  implementation comparisons, and sensitivity examples.

**Applicable evidence:** hand-computed traces, equal and disjoint samples,
direction-reversal cases, constant and minimal samples, reference libraries,
and component diagnostic reconstruction.

### 3.6 Aggregate-fitness correctness

**Evaluation question:** Are component scores combined and used for candidate
selection exactly as declared?

- **Dreadful:** Aggregate fitness is unrelated to component results or can rank
  candidates contrary to the declared objective.
- **Poor:** Weighting exists, but normalization, invalid candidates, missing
  methods, ties, sign, range, or selection use is materially inconsistent.
- **Partial:** Standard weighted results are correct, but boundary weights,
  diagnostics, exact ties, failure precedence, or final winner use has gaps.
- **Acceptable:** Configured components and normalized weights produce the exact
  declared aggregate; invalid cases, ties, ranges, diagnostics, and winner
  selection are deterministic and consistent.
- **Excellent:** Acceptable aggregation is supported by exhaustive component
  combinations, independent reconstruction, monotonicity checks, exact tie
  cases, and controlled ranking examples.

**Applicable evidence:** hand-weighted examples, all-zero and single-component
weights where valid, exact ties, invalid-candidate matrices, monotonic changes,
and independent ranking.

### 3.7 Numerical and boundary correctness

**Evaluation question:** Do finite precision, rounding, timestamp resolution,
sample boundaries, and resource limits preserve the declared science?

- **Dreadful:** Nonfinite values, overflow, rounding, or boundary errors can
  silently invalidate ordinary results.
- **Poor:** Central values work, but endpoints, very small or large finite
  values, exact zero, precision conversion, or limit interactions are unsafe.
- **Partial:** Most boundaries are handled, but some method or stage can cross
  its observation window, change ordering, leak arithmetic errors, or accept an
  infeasible result.
- **Acceptable:** Numeric domains are finite and explicit; rounding and
  quantization preserve ordering and bounds; inclusive and exclusive endpoints,
  overflow, and exact limits follow one documented convention.
- **Excellent:** Acceptable behavior is supported by exact rational or
  high-precision oracles, multiple timestamp resolutions, adversarial finite
  boundaries, and mutation-sensitive tests for every critical conversion.

**Applicable evidence:** rational arithmetic checks, next-representable-float
cases, binary and decimal timestamp resolutions, extreme finite inputs,
overflow cases, and exact limit boundaries.

### 3.8 Scientific validation strength

**Evaluation question:** Is method correctness established by evidence capable
of revealing scientific errors rather than only software execution errors?

- **Dreadful:** Methods have no meaningful validation beyond producing output.
- **Poor:** Tests check examples or snapshots without an independent expected
  value, scientific invariant, or mutation sensitivity.
- **Partial:** Some methods have hand or analytical evidence, but important
  equations, stochastic properties, family behavior, or interactions rely only
  on self-consistency.
- **Acceptable:** Every core method has direct behavioral evidence from hand
  calculations, analytical invariants, known simulations, or an independent
  implementation, including important edge cases.
- **Excellent:** Acceptable evidence is triangulated across multiple independent
  forms and representative real data, with predeclared tolerances and deliberate
  mutations proving that the evidence detects plausible scientific defects.

**Applicable evidence:** analytical derivations, reference implementations,
known-parameter simulation, real-data controls, mutation testing, and
predeclared tolerance studies.

### 3.9 Assumption and limitation transparency

**Evaluation question:** Are the valid domain, assumptions, estimator choices,
and interpretive limitations of every method explicit enough to prevent
overclaiming?

- **Dreadful:** Outputs are presented without the assumptions necessary to
  interpret them, or known invalid uses are presented as valid conclusions.
- **Poor:** Some limitations are mentioned, but major model, sample, capture,
  metric, independence, or observation-window assumptions remain hidden.
- **Partial:** Core assumptions are documented, but their consequences,
  interactions, failure signals, or bounds of interpretation are incomplete.
- **Acceptable:** Every method states its assumptions, valid input domain,
  estimator and convention choices, limitations, and what conclusions its
  outputs do and do not support.
- **Excellent:** Acceptable transparency is connected directly to diagnostics,
  controlled counterexamples, sensitivity evidence, and report language that
  consistently avoids conclusions beyond the method's support.

**Applicable evidence:** method documentation, cited definitions, diagnostic
fields, counterexamples, sensitivity studies, and audit of claims against
declared limitations.

## 4. Robustness

Robustness here means reliable scientific behavior under expected failures and
edge conditions. It does not mean building a general hardening or security
subsystem.

### 4.1 Input and artifact validation

**Evaluation question:** Are malformed, incomplete, inconsistent, stale, or
changed inputs rejected before they can influence a claimed result?

- **Dreadful:** Invalid or unrelated inputs are routinely accepted as valid
  scientific evidence.
- **Poor:** Syntax errors are caught, but material schema, type, cross-file,
  identity, freshness, or concurrent-change errors pass silently.
- **Partial:** Normal invalid inputs are rejected, but some nested invariants,
  race boundaries, symlinks or nonregular files, or full-set relationships are
  unchecked.
- **Acceptable:** Inputs and artifacts are strictly parsed, completely
  cross-validated, identity-checked before and after critical reads, and rejected
  on stale, partial, foreign, or changed state.
- **Excellent:** Acceptable validation is supported by systematic field, byte,
  identity, timing, replacement, and incomplete-set fault injection across every
  artifact boundary.

**Applicable evidence:** schema mutation, byte corruption, exact-tree checks,
identity replacement during reads, partial artifact sets, stale lineage, and
foreign-run cases.

### 4.2 Bounded execution

**Evaluation question:** Does every potentially long stage respect explicit
time, work, output, and local resource bounds without accepting incomplete
results?

- **Dreadful:** Ordinary failures can hang indefinitely, exhaust resources, or
  publish truncated work as a valid result.
- **Poor:** Some timeouts exist, but nested work, cleanup, parsing, subprocesses,
  candidate evaluation, or output growth can escape them.
- **Partial:** Main paths are bounded, but deadline propagation, post-timeout
  actions, process trees, or exact work/output limits have material gaps.
- **Acceptable:** One explicit budget model covers every stage and descendant;
  remaining budgets are recalculated at boundaries, expiry stops later work, and
  incomplete output is never accepted.
- **Excellent:** Acceptable containment is proven with real descendant trees,
  deliberately hanging tools, exact boundary clocks, output-growth cases, and
  repeated confirmation that no work survives the bound.

**Applicable evidence:** fake monotonic clocks, real process trees, hanging
subprocesses and containers, exact limit tests, output-budget cases, and
post-timeout process/resource inspection.

### 4.3 Failure semantics

**Evaluation question:** Are failures classified, ordered, and retained without
losing the scientifically authoritative cause?

- **Dreadful:** Failures are silently ignored, mislabeled as success, or replaced
  by unrelated cleanup or secondary errors.
- **Poor:** Errors are reported, but primary cause, stage, status, or important
  secondary evidence is frequently lost or misclassified.
- **Partial:** Common failures retain a useful primary result, but simultaneous
  events, induced statuses, later failures, or boundary errors remain ambiguous.
- **Acceptable:** A documented arbitration and precedence model retains the exact
  primary scientific failure, ordered secondary failures, stage context, and
  natural versus induced outcomes.
- **Excellent:** Acceptable semantics are exhaustively demonstrated across
  simultaneous-event combinations, failure injection at every boundary, real
  process statuses, and independent log/result reconstruction.

**Applicable evidence:** event-pair and event-triple matrices, injected command
failures, real exit and signal statuses, ordered diagnostic records, and primary
plus cleanup failure cases.

### 4.4 Atomic artifact publication

**Evaluation question:** Can any incomplete, stale, or concurrently replaced
artifact set be mistaken for a completed scientific result?

- **Dreadful:** Results are written directly in place and partial files are
  routinely visible as complete.
- **Poor:** Temporary files or rename are used inconsistently, and multi-file
  ordering, durability, reuse, or race behavior can expose mixed results.
- **Partial:** Normal publication is atomic enough, but link races, cleanup
  warnings, identity changes, rollback, or full-set ownership has important
  gaps.
- **Acceptable:** Publication validates durable private content before an
  exclusive, documented multi-file order; exact ownership and identity govern
  reuse and rollback; incomplete sets are never reusable.
- **Excellent:** Acceptable behavior is proven at every publish and rollback
  boundary with interruption, collision, replacement, durability, and cleanup
  injection that preserves unrelated winners.

**Applicable evidence:** publish-order tests, filesystem fault injection,
exclusive-link races, crash-boundary cases, identity-safe rollback, fsync
evidence, and concurrent valid-winner preservation.

### 4.5 Recovery and resumability

**Evaluation question:** Do retries and resumed work preserve scientific
authority and deterministic state rather than merely continue execution?

- **Dreadful:** Resume accepts arbitrary or incompatible state, or retries
  silently overwrite valid evidence.
- **Poor:** Some checkpoints or reuse exist, but compatibility, RNG state,
  history, ownership, or terminal-state behavior is incomplete.
- **Partial:** Standard resume works, but corruption, configuration change,
  lineage change, terminal re-entry, or partial publication can alter results.
- **Acceptable:** Resume strictly validates complete compatibility, restores all
  scientific and RNG state, repairs only derived presentation artifacts, and
  produces the same authoritative outcome as uninterrupted work.
- **Excellent:** Acceptable recovery is demonstrated at every meaningful
  interruption point, with byte or value equivalence, corruption matrices,
  incompatible-state rejection, and exact terminal re-entry.

**Applicable evidence:** checkpoint mutation, interruption at generation
boundaries, RNG-state comparisons, fresh-versus-resumed artifacts, terminal
checkpoint reuse, and valid-result preservation.

### 4.6 Lifecycle cleanup

**Evaluation question:** Are owned processes, containers, networks, volumes,
and temporary files cleaned reliably without damaging unrelated work?

- **Dreadful:** Failed or successful runs commonly leak resources or remove
  unrelated resources.
- **Poor:** Best-effort cleanup exists, but ownership is weak, descendants are
  missed, deadlines are unbounded, or cleanup failures are invisible.
- **Partial:** Ordinary cleanup is reliable, but zero budget, hung commands,
  partial startup, name reuse, concurrent ownership, or exact inventory has gaps.
- **Acceptable:** Cleanup is unconditional, bounded, idempotent, exact-owner
  scoped, descendant-aware, preserves unrelated resources, and reports what may
  remain when proof of absence is impossible.
- **Excellent:** Acceptable cleanup is proven under success and every staged
  failure with real nested processes and resources, concurrent name or identity
  reuse, deadline exhaustion, and independent post-run inventory.

**Applicable evidence:** labelled resource inventories, three-level process
trees, partial-start cases, name-reclaimer races, zero-budget cleanup, hanging
tools, and independent absence checks.

### 4.7 Adverse-condition behavior and diagnostics

**Evaluation question:** Do expected environmental, data, process, and numerical
problems produce reliable outcomes and useful corrective information?

- **Dreadful:** Expected adverse conditions cause crashes, hangs, silent data
  loss, false success, or uninterpretable output.
- **Poor:** Some errors are translated, but raw exceptions, vague messages,
  missing context, or unsafe continuation is common.
- **Partial:** Common failures are actionable, but unusual finite values,
  malformed external output, disappearing resources, partial state, or combined
  failures remain weakly handled.
- **Acceptable:** Every expected failure boundary produces a typed outcome with
  stage, cause, affected evidence, retained authoritative status, and a concrete
  corrective action, without publishing invalid work.
- **Excellent:** Acceptable diagnostics are mutation-tested across all boundary
  failures and shown to remain precise for combined failures, real external-tool
  behavior, and retained offline reconstruction.

**Applicable evidence:** external-output mutation, disappearing files and
resources, extreme finite inputs, combined-failure cases, structured logs,
operator correction tests, and offline diagnosis from retained evidence.

## 5. Reproducibility

Reproducibility means another compatible execution can identify the same inputs,
repeat the declared procedure, and recover the expected scientific values. It
does not require byte identity where an explicitly recorded environmental value
is inherently variable.

### 5.1 Randomness control

**Evaluation question:** Is every stochastic decision controlled by explicit,
stable, and retained randomness rules?

- **Dreadful:** Important random behavior is unseeded, uses hidden shared state,
  or changes unpredictably between equivalent runs.
- **Poor:** Seeds exist for some stages, but RNG ownership, derivation, draw
  order, trial assignment, or resume state is incomplete.
- **Partial:** Ordinary fresh runs are deterministic, but cross-family behavior,
  invalid paths, resume, final validation, or implementation refactors can alter
  stochastic decisions silently.
- **Acceptable:** Every stochastic component has an explicit seed policy,
  private RNG ownership, stable draw-order contract, retained seed lineage, and
  exact resume restoration.
- **Excellent:** Acceptable control is supported by scripted RNG oracles, full
  draw-order mutation tests, independent reruns, resume at multiple boundaries,
  and deliberate invalid-path cases.

**Applicable evidence:** seed records, scripted random sources, exact call
sequences, family-order changes, failed-candidate paths, checkpoint RNG state,
and fresh-versus-resumed comparisons.

### 5.2 Environment reproducibility

**Evaluation question:** Are the interpreter, dependencies, tools, images, and
runtime assumptions fixed or recorded well enough to repeat the experiment?

- **Dreadful:** The required environment is unknown and results depend on
  uncontrolled versions or tools.
- **Poor:** Major dependencies are named, but exact resolution, interpreter,
  container image, external program behavior, or platform assumptions are not
  retained.
- **Partial:** A typical development environment can be recreated, but fixture,
  checkpoint, container, or real-study reproduction depends on unrecorded
  versions or mutable identifiers.
- **Acceptable:** All result-affecting interpreter, package, tool, image, and
  runtime requirements are locked or exactly identified, with compatibility
  checks before reuse.
- **Excellent:** Acceptable environment control is proven from clean compatible
  environments and includes byte or value stability checks plus explicit records
  for unavoidable external variation.

**Applicable evidence:** lockfiles, interpreter pins, immutable image digests,
tool-version records, clean-clone runs, compatibility checks, and environment
manifests tied to results.

### 5.3 Input preservation

**Evaluation question:** Are the exact scientific inputs retained or
unambiguously identified for later reconstruction?

- **Dreadful:** Reference data, configuration, model bounds, or workload inputs
  are lost or cannot be identified after the run.
- **Poor:** Some inputs are copied or named, but mutable paths, incomplete
  snapshots, missing external evidence, or undocumented transformations remain.
- **Partial:** Core inputs are preserved, but one or more result-affecting
  settings, derived values, workload details, or environmental observations are
  not exact.
- **Acceptable:** Every result-affecting input is stored exactly or identified by
  immutable content identity, including effective configuration, reference,
  bounds, seeds, workload, and retained external evidence.
- **Excellent:** Acceptable preservation is independently audited for
  completeness and exactness, with reconstruction from a clean environment and
  mutation detection for every retained input class.

**Applicable evidence:** content hashes, canonical snapshots, immutable external
identifiers, checked workloads, exact input inventories, clean reconstruction,
and tamper matrices.

### 5.4 Artifact lineage

**Evaluation question:** Can every published scientific value be traced through
all actual inputs, intermediate artifacts, methods, and settings that produced
it?

- **Dreadful:** Results have no reliable connection to their source data or
  intermediate stages.
- **Poor:** Filenames or run IDs provide loose association, but stale or replaced
  artifacts can satisfy the claimed lineage.
- **Partial:** Major artifacts have hashes or identifiers, but some nested model,
  configuration, checkpoint, generated, comparison, or final relationships are
  incomplete.
- **Acceptable:** Exact content identities and strict schemas form a complete,
  validated chain from reference and effective configuration through fitting,
  generation, comparison, and final publication.
- **Excellent:** Acceptable lineage is independently reconstructed from retained
  bytes and remains correct under replacement, reuse, resume, publication race,
  and deliberate lineage-mutation scenarios.

**Applicable evidence:** content-address maps, strict lineage fields,
independent hash recomputation, complete-tree audits, stale-artifact cases, and
replacement during validation.

### 5.5 Canonical serialization

**Evaluation question:** Do equivalent scientific values have stable, strict,
and independently parseable representations?

- **Dreadful:** Artifacts are ambiguous, unstable, or not reliably parseable.
- **Poor:** A format exists, but unknown fields, duplicate keys, nonfinite
  values, ordering, encoding, or formatting can silently change meaning.
- **Partial:** Normal output is deterministic, but nested strictness, exact
  types, canonical rendering, round trips, or version validation is incomplete.
- **Acceptable:** Scientific artifacts use versioned, strict schemas with exact
  types, finite values, duplicate and unknown-field rejection, canonical byte
  rendering, and value-preserving round trips.
- **Excellent:** Acceptable serialization is validated by independent parsers,
  byte-identical fixture regeneration, extensive nested mutation, cross-platform
  checks where applicable, and compatibility policy tests.

**Applicable evidence:** parse-render-parse tests, duplicate and unknown-field
mutation, canonical fixture checks, independent readers, exact-byte comparison,
and version incompatibility cases.

### 5.6 Fresh and resumed rerun equivalence

**Evaluation question:** Do equivalent fresh, reused, and resumed executions
recover the expected authoritative scientific result?

- **Dreadful:** Repeating the same declared experiment produces unrelated or
  unexplained results.
- **Poor:** Some deterministic pieces repeat, but complete results depend on
  stale state, execution order, hidden randomness, or manual intervention.
- **Partial:** Fresh reruns are generally stable, but resume, reuse, final
  validation, publication, or diagnostic values differ without a documented
  scientific reason.
- **Acceptable:** Equivalent fresh and resumed executions reproduce all
  deterministic scientific values and correctly identify any explicitly
  variable external observations without changing authoritative lineage.
- **Excellent:** Acceptable equivalence is shown across multiple interruption
  points, clean environments, retained-input reconstruction, and repeated
  executions, with exact byte equality wherever the format promises it.

**Applicable evidence:** fresh-versus-resumed checkpoint histories, fixture
regeneration, clean-clone reruns, repeated complete pipelines, exact hashes, and
documented external-variation records.

### 5.7 Protocol reproducibility

**Evaluation question:** Is the complete experimental procedure explicit enough
for another researcher to repeat without inventing material decisions?

- **Dreadful:** Workload, ordering, repetitions, seeds, selection, validation,
  or reporting decisions are missing or chosen after observing results.
- **Poor:** A general procedure is described, but material choices remain
  discretionary, implicit, or inconsistent with retained evidence.
- **Partial:** Most steps are explicit, but failure handling, reruns, held-out
  validation, natural variation, amendments, or publication rules leave room for
  outcome-dependent choice.
- **Acceptable:** Workloads, ordering, repetitions, seeds, model competition,
  failure policy, amendments, selection, held-out validation, evidence retention,
  and reporting are fixed before interpretation and recorded exactly.
- **Excellent:** Acceptable protocol is independently executable, includes
  successful fresh reproduction, exposes all deviations and failed attempts,
  and separates exploratory, selection, and held-out evidence clearly.

**Applicable evidence:** checked protocol definitions, exact command records,
predeclared seed and order lists, failed-attempt retention, amendment history,
held-out reconstruction, and independent execution.

### 5.8 Independent reconstruction

**Evaluation question:** Can another researcher reconstruct and verify the
scientific result from retained files and documented commands?

- **Dreadful:** Reconstruction is impossible because essential data, commands,
  formats, or decisions are absent.
- **Poor:** The general workflow can be guessed, but substantial undocumented
  expertise, unavailable mutable resources, or manual repair is required.
- **Partial:** Most results can be reproduced, but one or more important stage,
  lineage, environment, raw-event, or comparison claims relies on the original
  process's internal state.
- **Acceptable:** A compatible researcher can use retained inputs, strict
  artifacts, pinned tools, and bounded documented commands to reconstruct and
  independently verify every published scientific value.
- **Excellent:** Acceptable reconstruction has been completed independently from
  a clean state, including direct re-parsing and recomputation rather than only
  rerunning the same high-level command.

**Applicable evidence:** clean-room instructions, retained input inventories,
strict offline loaders, direct model and comparison recomputation, hash audits,
bounded command transcripts, and an independent reproduction record.

## Excluded grading dimensions

Do not add or infer criteria for:

- authentication, authorization, user management, or tenancy;
- network-service exposure or hosted deployment;
- distributed scheduling, queues, databases, or horizontal scaling;
- enterprise observability, service-level objectives, or long-term support;
- speculative algorithm families or extension infrastructure;
- compatibility promises outside the declared research lifetime; or
- hardening against hostile users beyond what is necessary to preserve the
  validity and reproducibility of the local experiment.

If one of these concerns directly causes incorrect scientific results, destroys
evidence, prevents bounded local execution, or makes reproduction impossible,
grade the affected scientific, robustness, or reproducibility criterion. Do not
create a separate enterprise or security grade.
