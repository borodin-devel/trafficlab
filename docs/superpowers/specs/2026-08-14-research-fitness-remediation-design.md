# Minimum Research Fitness Remediation Design

**Date:** 2026-08-14

## Status and authority

This design defines how the MVP architecture and Roadmap will respond to the
evidence in `docs/RESEARCH_FITNESS_ASSESSMENT.md`. The assessment rubric in
`architecture/RESEARCH_FITNESS_CRITERIA.md` remains unchanged. Grades in the
assessment remain unchanged until implementation and fresh evidence justify a
new assessment.

The architecture under `architecture/` remains authoritative after the
approved changes are applied. This document is the implementation-design
record for those changes.

The target is **Acceptable** for every currently lower-graded criterion. The
work must not add Excellent-only requirements merely to improve appearance.

## Decision

Use targeted reopening of the owning MVP phases, followed by one final
cross-cutting acceptance phase:

- retain completed requirements whose assessment evidence is Acceptable;
- reopen requirements whose scientific semantics are wrong or whose claimed
  evidence cannot be reconstructed;
- add only the missing requirements and tests needed for Acceptable evidence;
- retain dated historical evidence as history, but do not present it as proof
  of corrected behavior;
- add a final phase that reassesses every deficient criterion after all owning
  phases pass.

This preserves the useful implementation history without leaving contradicted
checkboxes marked complete or rewriting the Roadmap from scratch.

## Approaches considered

### Add one remediation phase without reopening prior phases -- rejected

This is the smallest textual change, but it would leave completed claims for
MMPP semantics, genetic fairness, artifact retention, reproduction, and the
Validation Study even though the assessment contradicts those claims. A new
phase cannot make an inaccurate earlier checkbox truthful.

### Rewrite the complete MVP Roadmap -- rejected

Most MVP behavior already has Acceptable evidence. Rewriting every phase would
discard useful traceability, reopen unrelated behavior, and encourage broad
refactors with no scientific benefit.

### Reopen owning requirements and add a final acceptance phase -- selected

This approach changes only the affected scientific and evidentiary boundaries.
It makes current status truthful while preserving all unrelated completed work.

## Scope

The remediation covers the 17 criteria below Acceptable:

| Criterion | Current grade | Owning remediation |
|---|---|---|
| 1.5 Generated-trace correctness | Partial | Phase 4 |
| 1.7 End-to-end result consistency | Partial | Phases 6--7 |
| 2.1 Scientific controls | Partial | Phase 2 and Phase 6 integration |
| 2.2 Configuration semantics | Partial | Phase 1 and Phase 2 |
| 2.4 Effective-configuration fidelity | Partial | Phases 6--7 |
| 2.6 Portability | Partial | Phases 1, 3, and 7 |
| 3.3 Stochastic-generation correctness | Partial | Phase 4 |
| 3.4 Model-competition fairness | Partial | Phase 5 |
| 3.8 Scientific validation strength | Partial | Phases 2, 4, and 5 |
| 3.9 Assumption transparency | Partial | Phases 2, 4, 5, and 7 |
| 4.7 Adverse-condition diagnostics | Partial | Phases 3 and 6 |
| 5.2 Environment reproducibility | Partial | Phases 1, 3, and 7 |
| 5.3 Input preservation | Poor | Phase 7 |
| 5.4 Artifact lineage | Partial | Phases 6--7 |
| 5.6 Fresh/resumed equivalence | Partial | Phases 5--6 |
| 5.7 Protocol reproducibility | Partial | Phase 7 |
| 5.8 Independent reconstruction | Dreadful | Phase 7 |

The remediation does not lower or reinterpret any rubric anchor.

## Non-goals and simplicity boundary

The work will not add:

- another traffic model or similarity method;
- a configurable metric plug-in or variable result schema;
- a database, object-storage service, workflow engine, web service, or
  distributed runner;
- a security, authorization, signing, attestation, or multi-user subsystem;
- a general experiment framework or statistical package;
- mutation-testing infrastructure as an acceptance requirement;
- broad refactors of already Acceptable stages;
- claims of causal inference, likelihood identification, or generalization
  beyond the retained experiments.

The preferred implementation uses typed Python records, canonical JSON,
ordinary checked files, the existing bounded runner, and direct test-only
analytical oracles.

## Scientific semantics

### MMPP initialization at an arrival epoch

The normalized reference begins at an observed packet arrival at time zero.
The generated MMPP must therefore initialize its latent regime at an arrival
epoch rather than at an arbitrary time.

For stationary time probabilities

\[
\pi_0=\frac{q_{10}}{q_{01}+q_{10}},\qquad
\pi_1=\frac{q_{01}}{q_{01}+q_{10}},
\]

and mean arrival rate

\[
\bar\lambda=\pi_0\lambda_0+\pi_1\lambda_1,
\]

the time-zero arrival-state probabilities are

\[
a_z=\frac{\pi_z\lambda_z}{\bar\lambda}.
\]

Generation will:

1. draw the initial regime from `a`;
2. emit the conditioned packet arrival at time zero;
3. continue with the existing competing exponential regime-transition and
   packet-arrival clocks;
4. retain the existing complete-window and resource-limit contracts.

The exact analytical hand case is:

```text
q01 = 1
q10 = 3
lambda0 = 1
lambda1 = 9
pi = (3/4, 1/4)
arrival_epoch = (1/4, 3/4)
```

Tests will cover this partition, threshold boundaries, exact RNG draw order,
large finite values, and deterministic bounded empirical behavior.

### Scientific-semantics compatibility

Changing MMPP initialization changes the meaning of a fitted model and any
checkpoint containing MMPP candidates. Silent reuse is forbidden.

The simplest compatibility boundary is a single artifact-schema version bump
for fitted-model and genetic-checkpoint documents. The bumped version denotes
the corrected model semantics for all families. Older well-formed artifacts
fail with an actionable incompatible-scientific-semantics result before
generation, resume, or stage reuse.

No per-family migration framework will be added. Poisson and Markov Renewal
models are refitted under the new artifact version when needed.

### Similarity-weight semantics

All four MVP similarity methods are mandatory scientific diagnostics:

- frame-size KS;
- inter-arrival-time KS;
- autocorrelation discrepancy;
- multiscale count/byte discrepancy.

`method_weights` affect only the aggregate arithmetic. Each value is finite in
`[0, 1]`, all four values sum exactly according to the existing configuration
contract, and a zero value means zero aggregate contribution. It does not
disable execution, validation, diagnostic retention, or failure behavior.

All four method settings and result records remain mandatory. A mandatory
method failure remains fatal for comparison or makes a candidate invalid even
when that method has weight zero. This preserves the existing fixed schemas and
avoids a metric-enable subsystem.

Tests will include `1/0/0/0`, each one-hot vector, mixed weights, a zero-weight
method failure, exact effective-snapshot round trips, checkpoint round trips,
and examples where only the aggregate changes.

### Neutral heterogeneous competition

Lexical family ordering must not determine scientific preference.

At search creation, derive one deterministic family-priority permutation from
the master seed and the lexically sorted enabled family names. Use a temporary
`random.Random(master_seed)` only for one
`sample(sorted_family_names, len(sorted_family_names))` call, so deriving the
priority does not consume or otherwise change the search RNG stream. Retain
that exact permutation in the checkpoint compatibility/state document.

Use the retained priority for:

- population-quota remainders;
- initial candidate family ordering;
- exact cross-family fitness ties.

Candidate IDs remain stable, but they do not decide a cross-family tie before
family priority. Within one family, the existing stable candidate-ID rule may
remain. Report tables may remain lexical for readability.

Tests will prove:

- input registry/config ordering does not change results;
- controlled master seeds give each family each priority position;
- equal-fitness families, symmetric invalid candidates, and equal budgets do
  not systematically prefer a lexical family;
- a controlled scientifically better family still wins;
- interruption/resume retains the exact priority and continuation.

No adaptive allocation or statistical racing algorithm will be introduced.

## Direct scientific validation

The package will add a bounded deterministic scientific-validation matrix.
Tests must use analytical calculations or small independent test-only
implementations, not production similarity functions to validate themselves.

### Generator evidence

Predeclare seeds, sample sizes, tolerances, and failure messages before running
the test outputs. Cover:

- Poisson exponential inter-arrival behavior, mean arrival rate, window
  completion, and joint mark frequencies;
- Markov Renewal transition probabilities, state occupancy, conditional
  holding-time samples, fallback behavior, and joint mark frequencies;
- MMPP arrival-epoch state mixture, long-run mean arrival rate, time occupancy,
  serial dependence, window completion, and joint mark frequencies.

The matrix is a test suite, not a benchmark framework. Sample sizes must remain
small enough for ordinary bounded non-Docker execution and large enough for the
predeclared tolerances.

### Similarity evidence

The four similarity methods and aggregate already have Acceptable direct
method evidence. Retain their existing hand calculations and add only the
missing weight-semantics evidence: one-hot and mixed-weight aggregate
arithmetic, mandatory execution at zero weight, diagnostic retention, and
controlled one-factor examples. Do not add another implementation of each
similarity method or reopen their otherwise completed scientific validation.

### Competition evidence

Add the neutral-family matrices above, plus known-winner cases whose expected
winner follows from controlled component scores. All families receive equal
trial seeds, the same observation window, and explicit budgets.

## Assumptions and interpretation

Owning method documents will state the practical consequences of:

- normalizing the first packet to time zero;
- estimating Poisson rate as `(n - 1) / W`, which conditions on active span and
  excludes silence before the first and after the last observed packet;
- Markov Renewal empty-row and global-IAT fallbacks, including when a fallback
  was used;
- MMPP arrival-epoch conditioning and the difference from arbitrary-time
  stationary occupancy;
- finite genetic population, generation count, bounds, seeds, and operator
  probabilities;
- similarity weights and one-factor sensitivity;
- invalid-candidate outcomes and the information they do and do not provide.

Scores and winners are descriptive for the observed captures and declared
validation protocol. They do not establish likelihood optimality, causal
network mechanisms, universal model superiority, or generalization to unseen
programs.

## Configuration fidelity and portability

Keep one TOML schema and define two retained representations:

- **portable source configuration:** config-relative host paths and explicit
  scientific settings suitable for relocation;
- **realized effective configuration:** defaults applied and permitted host
  paths resolved to absolute paths for one run.

Only these values may change implicitly during realization:

- `run.directory`;
- declared host bind-mount source paths.

Images, argument vectors, environment variables, URL, seeds, model bounds,
limits, operators, and similarity settings must not be substituted. Missing or
incompatible tools, images, and mount sources fail preflight before publication.

Every accepted study run retains its source/realized pair. One bounded transfer
test starts from a fresh compatible clean clone with its own dependency
environment and relocated host paths. It realizes the portable configuration
there and proves that every effective scientific value is identical and that
differences are exactly the enumerated absolute paths. A companion case changes
one declared compatibility input and proves rejection before publication. This
is one successful compatible-environment transfer, not a cross-platform matrix.

## Environment reproducibility

The capture image will use:

- an exact base-image digest rather than a mutable tag;
- one dated Debian snapshot repository;
- exact package versions for every installed package named by the Dockerfile;
- a checked expected resolved capture-image content ID.

Docker recommends digest references when reproducible image identity matters,
and Debian Snapshot exposes dated archive states. No supply-chain policy engine
or signature subsystem is required.

Each published study records the scientifically relevant environment:

- source commit and tree state;
- `uv.lock` identity and exact CPython runtime;
- target and capture image references plus resolved content IDs;
- Docker Engine, Compose, kernel, architecture, and packet-capture tool
  versions;
- the compatibility decision used before reuse or reconstruction.

Compatibility checks compare only declared scientifically relevant fields.
Unrelated host metadata must not make an otherwise reproducible run unusable.

## Failure and diagnostic evidence

Use one small immutable failure-outcome record across expected stage failures.
It contains:

- stable `kind`;
- stage;
- cause/detail;
- affected artifact or evidence name;
- evidence state: `not_published`, `diagnostic_only`, `preserved`, or
  `possibly_remaining`;
- corrective action;
- primary or secondary authority;
- relevant process/status value when present.

This record extends existing error and run-log behavior; it does not replace
exceptions with a generic workflow engine. Candidate-invalid outcomes retain
the equivalent scientific fields in checkpoint/history diagnostics.

The declared matrix is finite:

- invalid configuration or path realization;
- unavailable/incompatible Docker, image, mount, or prerequisite;
- external nonzero exit, timeout, interruption, or malformed output;
- missing, changed, foreign, stale, or corrupt artifact;
- incompatible checkpoint/model semantics;
- metric/sample/numeric infeasibility;
- generation limit or deadline;
- publication collision or durability failure;
- cleanup failure;
- combined primary and secondary failures.

Direct tests inject every boundary and verify exact evidence state and
nonpublication. A small checked, credential-free JSONL fixture proves offline
diagnostic reconstruction.

## Fresh and resumed equivalence

The existing search-continuation test is necessary but insufficient. Add one
offline full-pipeline equivalence test over the same reference and effective
configuration:

1. run fit, generate, compare, and final publication uninterrupted;
2. interrupt at a declared checkpoint boundary;
3. resume, then generate, compare, and publish;
4. compare every deterministic scientific value, canonical artifact byte, and
   lineage hash;
5. permit differences only in a short, explicit list of external runtime/log
   observations.

Validated stage reuse must produce the same authoritative lineage. Legacy
scientific-semantics artifacts must fail rather than enter this equivalence.

## Validation data and held-out evidence

### Training evidence

The corrected Validation Study repeats the predeclared workloads and retains
all source captures used for fitting and selection. All model-family
competition and selection decisions use training references only.

### Fresh simulation seed

The existing final seed is a fresh simulation seed applied to a model fitted on
the same training reference. It checks stochastic stability but is not
held-out data. Architecture, results, and report wording must use this exact
interpretation.

### Independent held-out references

Capture one predeclared independent held-out reference per workload after the
training protocol is frozen and before interpretation. The held-out reference
is never used for fitting, bounds, candidate selection, family selection, or
protocol amendment.

For each workload:

1. select the training result by the predeclared training-only rule;
2. load its fitted model without refitting;
3. generate from those fixed fitted parameters over the held-out observation
   window with the predeclared final simulation seed;
4. compare the generated trace to the held-out reference;
5. report the held-out result separately from training and natural-variation
   results.

The validation harness may call existing family generation/comparison APIs
directly for the held-out window. It must not add a production pipeline mode or
mutate the fitted parameters.

## Retained accepted-study evidence

Ordinary runs, failed attempts, and scratch work remain ignored. Accepted study
evidence is a narrow checked exception under:

```text
examples/validation_study/evidence/<study-id>/
```

Every report-cited primary or reproduction run retains the complete strict
nine-file run tree:

```text
experiment.toml
capture.json
reference.pcapng
checkpoint.json
ga_history.csv
best_model.json
generated.pcapng
similarity.json
run.log
```

The evidence bundle also retains:

- portable source configurations;
- held-out capture metadata and PCAPNG;
- held-out generated traces and comparisons;
- transfer headers and external observations used by the protocol;
- prerequisite command stdout, stderr, and JUnit records;
- the environment record;
- a canonical manifest.

The manifest records each relative path, regular-file type, byte size, and
SHA-256. It also records logical ownership and lineage relationships. Hashes
without retrievable bytes are not evidence.

Publication fails unless the complete candidate bundle passes the offline
audit. Publication is exclusive and preserves an existing different accepted
bundle. No general archive service, signing system, or recursive recovery
framework will be added.

## Independent offline reconstruction

Provide one bounded study-audit command that runs from a clean repository clone
without Docker or Internet. It must:

1. validate the exact manifest inventory and every file identity;
2. parse every TOML, canonical JSON, CSV, and PCAPNG with strict production
   codecs where those codecs define the public format;
3. independently recompute reference normalization and observation windows;
4. validate checkpoint, history, winner, and model compatibility;
5. regenerate every deterministic generated trace from retained model, seed,
   window, and limits;
6. recompute all four similarity components, weighted aggregates, natural
   variation, training summaries, held-out summaries, and report arithmetic;
7. validate every cross-artifact and report lineage hash;
8. fail with the canonical diagnostic record for a missing, changed, foreign,
   or substituted item.

The audit may reuse strict parsers and public scientific functions. It must not
call the high-level run command, trust precomputed report values, require the
original absolute workspace path, or silently fetch missing data.

Tests copy the retained bundle to a temporary clean path, prohibit Docker and
network calls, prove successful reconstruction, and cover representative
missing, corrupt, foreign, and substituted evidence. Existing strict artifact
tests remain responsible for exhaustive per-format corruption matrices.

## Roadmap restructuring

### Phase 1

Keep existing dependency, configuration, and local-preflight work complete.
Add or reopen only:

- exact zero-weight semantics in configuration documentation and validation;
- portable-versus-realized configuration identity;
- tracked accepted-study evidence as a narrow exception to ignored ordinary
  run data.

Do not reopen unrelated configuration or tooling requirements.

### Phase 2

Keep all four existing method implementations complete. Add or reopen only:

- mandatory-all-four method semantics;
- zero-weight aggregate behavior;
- independent bounded component and aggregate validation.

### Phase 3

Keep lifecycle, capture, cleanup, controlled Docker, and existing Internet
smoke behavior complete. Reopen:

- capture-image reproducibility;
- environment compatibility evidence;
- affected diagnostic boundaries;
- the claim that the old study supplies current full-study evidence.

Historical Docker results remain recorded but do not satisfy the amended gate.

### Phase 4

Reopen:

- MMPP stationary initialization and describe it as arrival-epoch stationary;
- MMPP serialization/generation tests;
- all-family direct stochastic-validation evidence;
- artifact/version compatibility for corrected semantics;
- the Phase 4 Done-when condition.

### Phase 5

Reopen:

- deterministic quotas/ties where lexical order creates family preference;
- checkpoint compatibility/state for retained family priority and model
  semantics;
- family-fairness tests;
- full resumed-equivalence evidence;
- the Phase 5 Done-when condition.

### Phase 6

Reopen:

- validation of legacy scientific-semantics artifacts before reuse;
- cross-stage configuration/lineage validation;
- the corruption/staleness matrix for the new compatibility boundary;
- complete fresh/resumed final-artifact equivalence;
- canonical diagnostic outcomes;
- the Phase 6 claim that the old Phase 7 run trees provide complete evidence;
- the Phase 6 Done-when condition.

### Phase 7

Keep the approved workload definitions and historical smoke evidence as dated
history. Reopen:

- repeated primary captures;
- all-family runs and summaries;
- trace inspection and interpretation;
- report/configuration publication;
- saved-configuration reproduction;
- final-seed wording and evidence;
- prerequisite Internet evidence for the replacement same-commit study;
- the Phase 7 Done-when and Verified claims.

Add unchecked requirements for:

- independent held-out captures and fixed-model evaluation;
- complete checked evidence retention;
- environment and artifact manifest;
- clean-clone offline reconstruction;
- successful correction of every retained limitation and report claim.

### Phase 8 -- Minimum research fitness acceptance

Add a final cross-cutting phase with no new scientific algorithm. It will:

- run every reopened phase gate;
- run the bounded analytical scientific-validation matrix;
- run fresh/resumed equivalence and diagnostic matrices;
- verify capture-image reproducibility and portable configuration relocation;
- reconstruct the accepted study from a clean clone without Docker/network;
- rerun available Docker and Internet prerequisites for the final revision;
- obtain independent review with zero Critical or Important findings;
- reassess the 17 deficient criteria using the unchanged rubric;
- require each of those criteria to have fresh evidence at Acceptable or
  better before marking Phase 8 complete.

The earliest reopened phase becomes Current. No phase with an unchecked owning
requirement may retain a checked Done-when claim.

## Architecture file ownership

The implementation plan will update only relevant sections of:

- `architecture/README.md` for navigation and current phase;
- `architecture/ROADMAP.md` for truthful reopened/new requirements;
- `architecture/SYSTEM.md` for scientific semantics, configuration identity,
  failure records, lineage, and published evidence;
- `architecture/CAPTURE.md` for pinned capture environment and diagnostic
  behavior;
- `architecture/TESTING.md` for direct science, equivalence, diagnostics,
  retained evidence, and offline reconstruction;
- `architecture/DEVELOPMENT.md` for pinned environment and the narrow accepted
  evidence exception;
- `architecture/traffic_models/README.md` and the three family documents for
  assumptions and direct validation;
- `architecture/similarity_methods/README.md` and method documents only where
  weight semantics or limitations are owned;
- `architecture/genetic_models/basic_generational.md` for neutral family
  priority, checkpoint compatibility, and interpretation.

The assessment document is evidence, not an architecture owner. It will not be
edited during architecture remediation. A later assessment is a distinct
Phase 8 evidence task.

## Documentation quality checks

The architecture edit must:

- use one term for each concept: arrival-epoch initialization, mandatory
  method, fresh simulation seed, held-out reference, portable configuration,
  realized configuration, accepted evidence bundle, and offline audit;
- remove or correct conflicting old claims rather than adding contradictory
  notes elsewhere;
- link every reopened Roadmap item to its owning architecture contract and
  required test evidence;
- state assumptions and limitations next to the relevant method;
- keep historical evidence dates and explain why the corrected gate is open;
- avoid speculative implementation APIs beyond what the requirements need.

## Acceptance of the architecture remediation task

This documentation task is complete when:

- all 17 deficient criteria have an explicit minimal Acceptable requirement;
- each requirement has one owning architecture section and Roadmap task;
- contradicted checkboxes and Done-when claims are reopened;
- unaffected Acceptable work remains checked;
- the new Phase 8 gate uses the unchanged assessment rubric;
- scientific terminology and evidence claims are internally consistent;
- no production code, tests, fixtures, or assessment grade is changed;
- documentation links, formatting, and diff checks pass;
- independent review finds no Critical or Important architecture issue.

## Primary-source basis

- Docker documents digest pinning as the way to retain immutable base-image
  identity for reproducible builds:
  <https://docs.docker.com/build/building/best-practices/>.
- Debian Snapshot documents dated archive states suitable for retrieving fixed
  package versions:
  <https://snapshot.debian.org/>.
- Asanjarani and Nazarathy distinguish time-stationary and event-stationary
  Markovian arrival processes and give the two-state MMPP event-stationary
  vector in rate-weighted form:
  <https://arxiv.org/abs/1905.01736>.
- Existing model documents retain their other cited stochastic-process
  references.
