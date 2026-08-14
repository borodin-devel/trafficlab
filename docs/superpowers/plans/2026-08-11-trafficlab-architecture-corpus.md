# Trafficlab Architecture Corpus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a concise, mathematically sourced `architecture/` corpus for a fast-moving one-person Trafficlab research prototype.

**Architecture:** Document one in-process Python pipeline with a Docker Compose capture boundary, three competing classical traffic models, one basic heterogeneous genetic algorithm, four interpretable similarity methods, explicit integration testing, and one readable roadmap. Organize scientific extension points in separate folders without per-component governance documents or placeholder implementations.

**Tech Stack:** Markdown, TOML configuration, Python CLI, Docker Engine, Docker Compose, PCAPNG, pytest, classical stochastic-process models.

## Global Constraints

- Create only the 16 architecture files approved by the design spec.
- Optimize for one-person research iteration; do not copy the old SAD/SRS/CONFIGS/component-roadmap structure.
- Use one Python CLI and in-process stage calls; do not design child Trafficlab applications or service layers.
- Docker owns capture isolation, Internet access, DNS, NAT, and teardown; Trafficlab never configures host networking.
- Reliability includes bounds, validation, deterministic seeds, cleanup, atomic output writes, and genetic checkpoints.
- Do not introduce permission policing, inode pinning, symlink defenses, custom syscalls, authorization, secret handling, or protected manifests.
- Keep one current experiment/configuration format without a version family or migration design.
- Include Poisson empirical, Markov Renewal, and two-state MMPP; do not document unimplemented model families outside the final backlog.
- Include frame-size KS, IAT KS, autocorrelation, and multiscale rate; do not include neural, diffusion, optimal-transport, or wavelet methods.
- Every mathematical file must distinguish established results from Trafficlab-specific definitions and cite primary or authoritative sources.
- Explicitly define unit, in-process integration, Docker capture integration, and opt-in Internet smoke tests.
- Use one roadmap with ordinary checkboxes and the fields Goal, Deliverables, Tests, and Done when.
- Do not add an architecture validator or a documentation-specific test framework.
- Do not modify the approved design spec.

## File Map

- `architecture/README.md`: corpus entry point, scope, principles, navigation, and change rule.
- `architecture/SYSTEM.md`: end-to-end workflow, CLI behavior, experiment configuration, data flow, run outputs, model interface, and failure policy.
- `architecture/CAPTURE.md`: exact Docker Compose topology, lifecycle, preflight, cleanup, and capture reliability.
- `architecture/TESTING.md`: fast unit suite, in-process integrations, Docker capture fixture, Internet smoke test, and CI expectations.
- `architecture/ROADMAP.md`: seven ordered MVP phases and evidence-driven later backlog.
- `architecture/genetic_models/README.md`: heterogeneous-family genetic extension point and enabled strategy.
- `architecture/genetic_models/basic_generational.md`: chromosome compatibility, population construction, tournament selection, family champions, crossover, mutation, fitness, checkpoint, and termination.
- `architecture/traffic_models/README.md`: shared trace/model interface, fair competition rules, and model comparison table.
- `architecture/traffic_models/poisson_empirical.md`: homogeneous Poisson timing with joint empirical direction/size marks.
- `architecture/traffic_models/markov_renewal.md`: observable states, transition matrix, conditional holding times, sparse fallback, chromosome, and generation.
- `architecture/traffic_models/mmpp.md`: two-state CTMC generator, state-dependent Poisson arrivals, chromosome constraints, simulation, and empirical marks.
- `architecture/similarity_methods/README.md`: common score range, aggregation, enabled methods, and interpretation rule.
- `architecture/similarity_methods/frame_size_ks.md`: two-sample empirical-CDF distance and bounded similarity.
- `architecture/similarity_methods/iat_ks.md`: nonnegative-IAT extraction, two-sample empirical-CDF distance, and edge cases.
- `architecture/similarity_methods/autocorrelation.md`: sample ACF, lag discrepancy, feature weighting, and constant-series behavior.
- `architecture/similarity_methods/multiscale_rate.md`: aligned packet/byte count vectors, normalized L1 discrepancy, and feature/scale weighting.

---

### Task 1: Core System and Docker Capture Design

**Files:**
- Create: `architecture/README.md`
- Create: `architecture/SYSTEM.md`
- Create: `architecture/CAPTURE.md`

**Interfaces:**
- Consumes: approved design at `docs/superpowers/specs/2026-08-11-trafficlab-prototype-architecture-design.md`.
- Produces: canonical terms `Experiment`, `Run directory`, `Canonical trace`, `Model family`, `Candidate`, `Fitness`, and the six CLI commands used by every later document.

- [ ] **Step 1: Create the corpus entry point**

Create `architecture/README.md` with these concrete sections:

```markdown
# Trafficlab Architecture

## Purpose
State that Trafficlab captures containerized real programs, fits competing
classical traffic models with a GA, generates PCAPNG, and compares traces.

## MVP workflow
Show: preflight -> capture -> fit -> generate -> compare.

## Principles
List one process, Docker capture boundary, reliability not security,
implemented algorithms only, concise sourced mathematics, one roadmap.

## Documents
Link SYSTEM.md, CAPTURE.md, TESTING.md, ROADMAP.md and the three algorithm
folder READMEs with one-sentence ownership descriptions.

## Scope boundaries
Name the approved inclusions and exclusions explicitly.

## Changing the architecture
Say to edit the owning document directly, add an algorithm file only with its
implementation, and use Git history instead of amendments or versioned docs.
```

- [ ] **Step 2: Write the system data flow and command contract**

Create `architecture/SYSTEM.md`. Define:

```text
Experiment TOML
  -> preflight
  -> Docker reference capture
  -> canonical sequence x_i=(t_i,d_i,l_i)
  -> heterogeneous GA over enabled model families
  -> best_model.json
  -> final generated trace and generated.pcapng
  -> component metrics and weighted similarity.json
```

Give exact behavior for:

```text
trafficlab preflight EXPERIMENT  # read-only readiness
trafficlab capture EXPERIMENT    # create reference.pcapng
trafficlab fit EXPERIMENT        # GA, checkpoint, winner
trafficlab generate EXPERIMENT   # final full-size generated.pcapng
trafficlab compare EXPERIMENT    # component and aggregate result
trafficlab run EXPERIMENT        # ordered full workflow
```

Specify one TOML configuration with target image/argv, optional environment,
workdir and mounts, timeouts, output directory, random seeds, enabled families,
family bounds, GA settings, trial/final limits, similarity settings, and weights.
Define the eight approved run files and state that completed stages are reused
only after output validation. Define atomic structured writes, direct input
hashes, non-overwrite by default, concise console error plus full `run.log`, and
checkpoint-only resume. State explicitly that all stages are in-process Python
calls except Docker/Compose and the captured target command.

- [ ] **Step 3: Write the Docker capture topology and lifecycle**

Create `architecture/CAPTURE.md` with this topology:

```text
Compose default bridge (Docker-provided Internet/DNS/NAT)
  target service: idle container, later receives configured argv via exec
  capture service: network_mode: service:target, writes reference.pcapng
```

Define lifecycle in exact order: validate configuration; choose unique Compose
project; start idle target; start capture sidecar; wait for capture readiness;
execute target argv; enforce timeout; preserve target exit code; send `SIGINT`
to capture; wait for flush; validate PCAPNG; run
`docker compose down --volumes --remove-orphans` in unconditional cleanup.

Define preflight checks: `docker info`, `docker compose version`, image
availability/pull, target POSIX-shell/idle-command contract, mounts, writable
run directory, disk space, capture image, DNS, and a bounded network probe.

Define reliability cases: readiness timeout, target failure, workload timeout,
capture failure, malformed/empty output, interruption, stale resources, and
cleanup failure. Cleanup is idempotent; cleanup failure is reported without
hiding the primary failure. State that Trafficlab never edits host network
configuration and never requires a project-owned sudo script.

Reference official sources:

- Docker Compose service/network namespace reference:
  `https://docs.docker.com/reference/compose-file/services/`
- Docker Compose networking and default bridge behavior:
  `https://docs.docker.com/compose/how-tos/networking/`
- Current PCAPNG work-in-progress format:
  `https://datatracker.ietf.org/doc/draft-ietf-opsawg-pcapng/`

- [ ] **Step 4: Validate navigation, terminology, and scope**

Run:

```bash
test "$(find architecture -maxdepth 1 -type f | wc -l)" -eq 3
rg -n 'preflight|capture|fit|generate|compare|run' architecture/SYSTEM.md
rg -n 'network_mode: service:target|SIGINT|down --volumes --remove-orphans' architecture/CAPTURE.md
rg -n 'SAD|SRS|security hardening|host network' architecture
git diff --check
```

Expected: three files exist; all six commands and capture lifecycle terms are
present; forbidden old document types appear only in an explicit exclusion;
no whitespace errors.

- [ ] **Step 5: Commit the core architecture**

```bash
git add architecture/README.md architecture/SYSTEM.md architecture/CAPTURE.md
git commit -m "docs: define prototype system architecture"
```

---

### Task 2: Genetic and Traffic Model Mathematics

**Files:**
- Create: `architecture/genetic_models/README.md`
- Create: `architecture/genetic_models/basic_generational.md`
- Create: `architecture/traffic_models/README.md`
- Create: `architecture/traffic_models/poisson_empirical.md`
- Create: `architecture/traffic_models/markov_renewal.md`
- Create: `architecture/traffic_models/mmpp.md`

**Interfaces:**
- Consumes: canonical trace \(x_i=(t_i,d_i,l_i)\), model interface, run limits, and result terminology from `architecture/SYSTEM.md`.
- Produces: exact family chromosomes and `fit(reference, genes)`, `generate(model, seed, limits)`, and JSON serialization contracts used by genetic fitting and testing.

- [ ] **Step 1: Write the model catalog and common interface**

Create `architecture/traffic_models/README.md`. Include a table comparing the
three models by dependence captured, chromosome, strengths, limitations, and
expected fitting/generation cost. Define:

```text
fit(reference, genes) -> fitted model
generate(fitted model, seed, limits) -> canonical trace
serialize(fitted model) -> JSON-compatible value
```

Require model name, fitted parameters, gene values, reference SHA-256, seed
policy, estimator details, and bounds in serialized output. Require common trial
seeds and limits for fair competition. Link exactly the three implemented model
documents.

- [ ] **Step 2: Document the Poisson empirical baseline**

Create `architecture/traffic_models/poisson_empirical.md` with:

```math
\hat\lambda = N/T
f_\Delta(t)=\lambda e^{-\lambda t},\quad t\ge0
\lambda_g=c_\lambda\hat\lambda
```

Define \(N\) as interval count over positive reference duration \(T\), and
bound the sole gene \(c_\lambda\). Sample direction/frame-length pairs from the
joint empirical distribution with replacement. Define exponential interarrival
generation until packet-count or duration limit, minimum sample requirements,
zero-duration rejection, deterministic RNG use, \(O(N)\) fit, and \(O(M)\)
generation. Clearly label empirical joint marks and rate scaling as Trafficlab
choices.

Use MIT OpenCourseWare's Poisson-process notes as the authoritative reference:
`https://ocw.mit.edu/courses/6-262-discrete-stochastic-processes-spring-2011/68c1d4b947f61d7374154a8902ed2c10_MIT6_262S11_lec04.pdf`.

- [ ] **Step 3: Document the Markov Renewal model**

Create `architecture/traffic_models/markov_renewal.md`. Define states
\(J_i=(d_i,b(l_i))\), transition counts \(N_{jk}\), additive smoothing

```math
p_{jk}=\frac{N_{jk}+\alpha}{\sum_m N_{jm}+\alpha K},
```

and empirical conditional holding CDF

```math
\hat F_{jk}(u)=\frac{1}{N_{jk}}\sum_i
1[J_i=j,J_{i+1}=k,\Delta_i\le u].
```

Define the Markov renewal kernel
\(Q_{jk}(u)=p_{jk}F_{jk}(u)\). Specify two ordered reference quantile genes,
\(\alpha\), minimum conditional support, and timing-scale gene. When conditional
support is insufficient, fall back first to source-state empirical IAT and then
global empirical IAT. Generation samples next state, holding time, then a frame
length from the empirical observations in that destination state. Define
invalid boundaries, unseen/empty states, termination limits, reproducibility,
\(O(N+K^2)\) fit storage, and \(O(MK)\) simple cumulative-probability generation.

Use Ronald Pyke's foundational paper, DOI
`https://doi.org/10.1214/aoms/1177704863`, and label binning, smoothing, fallback,
and score-oriented genes as Trafficlab choices.

- [ ] **Step 4: Document the two-state MMPP**

Create `architecture/traffic_models/mmpp.md`. Define:

```math
Q=\begin{bmatrix}-q_{01}&q_{01}\\q_{10}&-q_{10}\end{bmatrix},
\quad q_{01},q_{10}>0,
\quad 0<\lambda_0<\lambda_1,
```

with stationary probabilities

```math
\pi_0=\frac{q_{10}}{q_{01}+q_{10}},\qquad
\pi_1=\frac{q_{01}}{q_{01}+q_{10}}.
```

The four positive rates are the chromosome, with ordering repair for arrival
rates. The GA fits them directly by simulation fitness; do not specify an unused
likelihood optimizer. Generate exponential CTMC state sojourns and Poisson
arrivals at the active state's rate. Sample joint direction/frame-length marks
from the global reference empirical distribution, independent of latent state.
Define stationary initial-state sampling, simultaneous-event handling, bounds,
degenerate-rate rejection, deterministic RNG, and \(O(N)\) mark fitting plus
\(O(M+R)\) generation for \(R\) regime changes.

Use Fischer and Meier-Hellstern, “The Markov-modulated Poisson process (MMPP)
cookbook,” DOI `https://doi.org/10.1016/0166-5316(93)90035-S`. Label direct GA
fitting and regime-independent empirical marks as Trafficlab choices.

- [ ] **Step 5: Write the heterogeneous basic generational algorithm**

Create `architecture/genetic_models/README.md` with the purpose of the extension
point, the single enabled `basic_generational` strategy, and a rule forbidding
placeholder strategies.

Create `architecture/genetic_models/basic_generational.md` with exact pseudocode:

```text
initialize family quotas with deterministic seed
evaluate every candidate on the same trial seeds
repeat until generation budget:
    retain global elites and the best candidate from each family
    fill remaining slots by tournament selection
    same family: family-specific crossover then mutation
    different families: clone fitter parent then mutate
    repair bounded/order constraints or reject with worst fitness
    evaluate on common trial seeds
    atomically checkpoint population, RNG state, generation, and history
reevaluate global winner on fresh final seeds
```

Define weighted fitness \(S=\sum_m w_ms_m\), tournament size, fixed population
size, per-family initial quota, duplicate handling, tie-breaking by stable
candidate ID, family champions, crossover/mutation compatibility, invalid model
versus infrastructure failure, checkpoint contents, resume validation, and
termination. State that family champions preserve competition but consume slots;
therefore population size must be at least the enabled family count plus the
global elite count. Give time cost as
\(O(GPSE)\), with generations \(G\), population \(P\), trial seeds \(S\), and
candidate evaluation cost \(E\).

Reference Miller and Goldberg, “Genetic Algorithms, Tournament Selection, and
the Effects of Noise”:
`https://wpmedia.wolfram.com/sites/13/2018/02/09-3-2.pdf`. Label cross-family
parent handling, family champions, common seeds, and checkpoint format as
Trafficlab choices.

- [ ] **Step 6: Validate mathematical completeness and cross-links**

Run:

```bash
test "$(find architecture/traffic_models architecture/genetic_models -type f | wc -l)" -eq 6
for file in architecture/traffic_models/{poisson_empirical,markov_renewal,mmpp}.md architecture/genetic_models/basic_generational.md; do rg '## References' "$file"; done
for file in architecture/traffic_models/{poisson_empirical,markov_renewal,mmpp}.md architecture/genetic_models/basic_generational.md; do rg 'Trafficlab-specific' "$file"; done
rg -n 'fit\(reference, genes\)|generate\(fitted model' architecture/traffic_models/README.md
rg -n 'Poisson|Markov Renewal|MMPP' architecture/genetic_models/basic_generational.md
rg -n 'T[B]D|T[O]DO|placeholder implementation|neural|diffusion' architecture/traffic_models architecture/genetic_models
git diff --check
```

Expected: six files exist; every algorithm file has references and an explicit
Trafficlab-specific section; common interfaces and all three families are
present; no placeholder or excluded-model content appears except explicit scope
statements; no whitespace errors.

- [ ] **Step 7: Commit the model architecture**

```bash
git add architecture/traffic_models architecture/genetic_models
git commit -m "docs: define competing traffic models"
```

---

### Task 3: Similarity Method Mathematics

**Files:**
- Create: `architecture/similarity_methods/README.md`
- Create: `architecture/similarity_methods/frame_size_ks.md`
- Create: `architecture/similarity_methods/iat_ks.md`
- Create: `architecture/similarity_methods/autocorrelation.md`
- Create: `architecture/similarity_methods/multiscale_rate.md`

**Interfaces:**
- Consumes: canonical trace from `architecture/SYSTEM.md` and common genetic fitness from `architecture/genetic_models/basic_generational.md`.
- Produces: component scores \(s_m\in[0,1]\), diagnostics, and aggregate \(S=\sum_mw_ms_m\) used by candidate fitness and final comparison.

- [ ] **Step 1: Write the common similarity contract**

Create `architecture/similarity_methods/README.md`. Require every method to
return a bounded score in `[0,1]`, where `1` means identical under that method,
plus named diagnostics. Define nonnegative weights summing to one and

```math
S(R,G)=\sum_m w_m s_m(R,G).
```

State that aggregate fitness never hides component results; failed preconditions
are errors, not zero similarity; and methods compare canonical traces without
reading files themselves. Include a table mapping each method to the behavior it
measures, configuration, minimum sample, cost, and limitation. Link exactly the
four implemented method files.

- [ ] **Step 2: Document frame-size KS**

Create `architecture/similarity_methods/frame_size_ks.md`. Define empirical CDF
\(\hat F_n(x)=n^{-1}\sum_i1[X_i\le x]\), two-sample distance
\(D=\sup_x|\hat F_R(x)-\hat F_G(x)|\), and score \(s=1-D\). Specify full captured
frame lengths, at least one observation per trace, ties/discrete lengths, exact
merged sorted evaluation, diagnostics `distance`, sample counts, and
minimum/maximum lengths, plus \(O((n+m)\log(n+m))\) cost. Clarify that Trafficlab
uses the statistic as a descriptive distance and does not report a hypothesis
test p-value.

Reference NIST's two-sample definition:
`https://itl.nist.gov/div898/software/dataplot/refman1/auxillar/ks2samp.htm`.

- [ ] **Step 3: Document IAT KS**

Create `architecture/similarity_methods/iat_ks.md` with the same ECDF, distance,
score, and exact evaluation, but on \(\Delta_i=t_{i+1}-t_i\). Require at least
two packets per trace and nonnegative monotonic differences; retain zero IATs
because capture timestamps can tie, and reject negative values. Report distance,
IAT sample counts, zero counts, median, and upper quantile. State the same
descriptive-statistic limitation and \(O((n+m)\log(n+m))\) cost. Reference the
same NIST two-sample source.

- [ ] **Step 4: Document autocorrelation similarity**

Create `architecture/similarity_methods/autocorrelation.md`. For a sequence
\(y_1,\ldots,y_N\), define the NIST sample ACF:

```math
\rho_y(k)=\frac{\sum_{i=1}^{N-k}(y_i-\bar y)(y_{i+k}-\bar y)}
{\sum_{i=1}^{N}(y_i-\bar y)^2}.
```

Compute it separately for IAT and frame-length sequences at configured positive
lags smaller than both series. Define constant-series ACF as zero. With lag
weights \(a_k\) summing to one, define
\(D_f=\sum_ka_k|\rho_{R,f}(k)-\rho_{G,f}(k)|/2\). Combine IAT and length
discrepancies with nonnegative feature weights summing to one and return
\(s=1-D\). Report every ACF value and discrepancy. Give direct cost
\(O(|L|(n+m))\). Label the discrepancy and constant-series convention as
Trafficlab-specific.

Reference the NIST autocorrelation definition:
`https://www.itl.nist.gov/div898/handbook/eda/section3/eda35c.htm`.

- [ ] **Step 5: Document multiscale-rate similarity**

Create `architecture/similarity_methods/multiscale_rate.md`. For each positive
width \(h\), align both traces to relative time zero and a common configured
horizon \(H\), producing packet-count and original-byte-count vectors. Define:

```math
D(r,g)=\frac{\sum_i|r_i-g_i|}{\sum_i r_i+\sum_i g_i},
```

with `D=0` when both sums are zero. Combine discrepancies using nonnegative
packet/byte feature weights and per-width scale weights, each normalized to sum
to one; return `s=1-D_total`. Require strictly increasing unique widths no larger
than the horizon and a configured maximum total bin count. Report per-feature,
per-scale discrepancies and totals. Give \(O(n+m+B)\) cost for total bins \(B\).
Label the entire bounded score as a Trafficlab-specific definition; cite no
external method name.

- [ ] **Step 6: Validate formulas, ranges, and references**

Run:

```bash
test "$(find architecture/similarity_methods -type f | wc -l)" -eq 5
for file in architecture/similarity_methods/{frame_size_ks,iat_ks,autocorrelation,multiscale_rate}.md; do rg '## References' "$file"; done
for file in architecture/similarity_methods/{frame_size_ks,iat_ks,autocorrelation,multiscale_rate}.md; do rg 'Computational cost' "$file"; done
rg -n '0,1|\[0, 1\]|\[0,1\]' architecture/similarity_methods
rg -n 'T[B]D|T[O]DO|p-value.*fitness|neural|optimal transport|wavelet' architecture/similarity_methods
git diff --check
```

Expected: five files exist; each file has references and cost; bounded-score
language is present; no placeholders or excluded methods are designed; no
whitespace errors.

- [ ] **Step 7: Commit the similarity architecture**

```bash
git add architecture/similarity_methods
git commit -m "docs: define similarity mathematics"
```

---

### Task 4: Testing Strategy, Roadmap, and Corpus Verification

**Files:**
- Create: `architecture/TESTING.md`
- Create: `architecture/ROADMAP.md`
- Modify if verification finds inconsistency: any `architecture/*.md` or algorithm document created in Tasks 1–3.

**Interfaces:**
- Consumes: all system, capture, model, genetic, and similarity contracts from Tasks 1–3.
- Produces: an executable test strategy, the only implementation roadmap, and a coherent self-contained architecture corpus.

- [ ] **Step 1: Write the testing strategy**

Create `architecture/TESTING.md` with four explicit suites:

```text
unit: formulas, hand-computed examples, estimators, model invariants,
      deterministic RNG, bounds, config, PCAPNG, malformed input
integration: PCAPNG -> trace -> model -> generation -> similarity;
             heterogeneous GA; interruption/checkpoint/resume; stage reuse
docker integration: real Docker/Compose CLI, unique project, controlled client
                    and endpoint, expected packets, unconditional teardown
internet smoke: opt-in external request proving production topology
```

State that fast unit tests run by default. Explicit integration selection must
fail clearly when Docker/Compose is unavailable rather than silently skip.
Deterministic Docker tests use a controlled endpoint and never require public
Internet. The Internet smoke target is configurable and not part of normal CI.
CI runs Docker tests only on a runner declared Docker-capable.

Define concrete acceptance examples:

- KS hand case with one known maximum ECDF difference;
- ACF constant series and a short hand-computed lag-one series;
- multiscale identical/empty/disjoint count vectors;
- fixed-seed repeatability for every model;
- model output timestamps monotonic and within limits;
- all three families appear and receive scores in a bounded GA run;
- interrupted GA resumes to the same result as uninterrupted execution;
- Docker client traffic appears in parseable `reference.pcapng`;
- target timeout and `SIGINT` still remove Compose resources.

Reference the PCAPNG draft and Docker sources already used by `CAPTURE.md` rather
than duplicating their explanations.

- [ ] **Step 2: Write the understandable roadmap**

Create `architecture/ROADMAP.md` with seven phases in this exact order:

1. Project skeleton and experiment configuration.
2. Docker preflight and reference capture.
3. Canonical trace and four similarity methods.
4. Three traffic models.
5. Heterogeneous genetic fitting and checkpoint resume.
6. Complete CLI workflow and integration suite.
7. MVP validation on representative real containerized programs.

For every phase include:

```markdown
## Phase N — Name

**Goal:** One observable outcome.

**Deliverables:**
- [ ] Concrete files or behavior.

**Tests:**
- [ ] Exact unit or integration proof.

**Done when:** One sentence a developer can verify without interpreting a
percentage.
```

Mark Phase 1 as `Current`; leave all task checkboxes unchecked because no
production implementation exists. End with `Later, only if evidence requires
it`: ON/OFF Pareto, traffic replay, richer packet marks, parallel evaluation,
and additional interpretable similarity methods. State that these are ideas,
not committed scope.

- [ ] **Step 3: Verify exact corpus shape and links**

Run:

```bash
test "$(find architecture -type f -name '*.md' | wc -l)" -eq 16
find architecture -type f -name '*.md' | sort
rg -o '\[[^]]+\]\([^)]+\.md\)' architecture \
  | sed -E 's/.*\]\(([^)#]+).*/\1/'
rg -n 'SAD\.md|SRS\.md|CONFIGS\.md|schema_version|\[[[:space:]]*[0-9]+%\]|CR_B|MK_B|MN_B' architecture
rg -n 'integration|Docker|checkpoint|Poisson|Markov Renewal|MMPP' architecture/TESTING.md architecture/ROADMAP.md
rg -n 'T[B]D|T[O]DO|F[I]XME|implement[ ]later|fill[ ]in[ ]details|appropriate[ ]error[ ]handling' architecture
git diff --check
```

Expected: 16 files exist—the approved design lists five core files, two genetic
files, four traffic-model files, and five similarity files; every printed link
target resolves relative to its source; old governance/schema/status terms occur
only in explicit exclusions, not requirements; testing and roadmap cover the
approved pipeline; no placeholders or whitespace errors exist.

- [ ] **Step 4: Perform acceptance-criteria review**

Read the full corpus in navigation order and confirm:

```text
[ ] No old architecture knowledge is required.
[ ] One data flow connects Docker capture to final similarity.
[ ] Docker is the only capture environment manager.
[ ] Three model families share one interface and compete under common fitness.
[ ] Every algorithm gives equations, bounds, edge cases, cost, tests, sources,
    and a Trafficlab-specific distinction.
[ ] Unit, integration, Docker integration, and Internet smoke tests are defined.
[ ] ROADMAP uses phases, checkboxes, and observable Done when statements only.
[ ] No security, compatibility, governance, or placeholder subsystem leaked in.
```

Correct any failed item directly in its owning document, then rerun Step 3.

- [ ] **Step 5: Commit testing, roadmap, and consistency fixes**

```bash
git add architecture
git commit -m "docs: complete prototype architecture"
```

- [ ] **Step 6: Verify repository state**

Run:

```bash
git status --short
git log --oneline --decorate -5
find architecture -type f -name '*.md' | sort
```

Expected: clean working tree; separate commits for core architecture, models,
similarity mathematics, and final testing/roadmap; exactly the 16 approved
architecture Markdown files.
