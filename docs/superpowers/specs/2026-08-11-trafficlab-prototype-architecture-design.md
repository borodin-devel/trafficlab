# Trafficlab Research Prototype Architecture Design

**Date:** 2026-08-11

## Purpose

Trafficlab is a one-person Linux research prototype for capturing the network
traffic of containerized programs, fitting competing stochastic traffic models,
generating synthetic traffic, and measuring how closely it resembles a reference
capture.

The architecture must optimize for fast scientific iteration. It keeps
reproducibility and operational reliability, but does not build security
hardening, multi-team governance, independent application services, or
compatibility machinery before they are needed.

## Scope

The MVP supports this workflow:

```text
containerized workload
  -> reference PCAPNG
  -> competing model fitting by a genetic algorithm
  -> winning fitted model
  -> generated PCAPNG
  -> component and aggregate similarity scores
```

Three classical traffic-model families compete in the genetic population:

- Poisson empirical, as an independent-arrival baseline;
- Markov Renewal, for observable packet-state transition and timing dependence;
- two-state MMPP, for latent burst and idle regimes.

Fitness uses four interpretable similarity methods:

- frame-size Kolmogorov-Smirnov similarity;
- inter-arrival-time Kolmogorov-Smirnov similarity;
- autocorrelation similarity at configured lags;
- multiscale packet/byte-rate similarity.

The MVP excludes neural models, diffusion models, optimal transport, wavelet
methods, traffic replay, distributed execution, multi-user operation, and a
public long-term file compatibility promise.

## Design Principles

1. Use one program and in-process function calls for the research pipeline.
2. Use Docker Compose for capture isolation, Internet access, and teardown.
3. Keep algorithms replaceable through small Python interfaces, not separate
   processes.
4. Store only useful research outputs; do not add detached status, launch,
   manifest, or lineage subsystems.
5. Add algorithm documentation only when the algorithm is implemented.
6. Separate established mathematics from Trafficlab-specific engineering
   choices.
7. Prefer deterministic seeds, bounds, validation, cleanup, and checkpointing
   over security hardening.
8. Keep the roadmap as one ordered, observable implementation sequence.

## Architecture Documentation

The implementation creates this corpus:

```text
architecture/
|-- README.md
|-- SYSTEM.md
|-- CAPTURE.md
|-- TESTING.md
|-- ROADMAP.md
|-- genetic_models/
|   |-- README.md
|   `-- basic_generational.md
|-- traffic_models/
|   |-- README.md
|   |-- poisson_empirical.md
|   |-- markov_renewal.md
|   `-- mmpp.md
`-- similarity_methods/
    |-- README.md
    |-- frame_size_ks.md
    |-- iat_ks.md
    |-- autocorrelation.md
    `-- multiscale_rate.md
```

There are no per-component SAD, SRS, CONFIGS, or ROADMAP files. There is no
architecture-validation program and no prescribed filename grammar beyond
clear, stable names. Each algorithm document is concise and contains:

- its purpose and applicability;
- exact mathematical definition;
- assumptions and estimator;
- fitted or configured parameters and bounds;
- fitting and generation or scoring procedure;
- edge cases and numerical behavior;
- expected computational cost;
- deterministic test examples;
- primary or authoritative references;
- explicit labels for Trafficlab-specific choices.

Unimplemented algorithms are mentioned only in the final roadmap backlog. They
do not receive placeholder directories, schemas, registries, or design files.

## Runtime Structure

Trafficlab is one Python package and one `trafficlab` CLI. The public commands
are:

```text
trafficlab preflight EXPERIMENT
trafficlab capture EXPERIMENT
trafficlab fit EXPERIMENT
trafficlab generate EXPERIMENT
trafficlab compare EXPERIMENT
trafficlab run EXPERIMENT
```

`EXPERIMENT` is a path to one TOML configuration file. The prototype supports
one current configuration shape without a schema-version family.

The configuration declares:

- target Docker image and argument vector;
- optional target environment, work directory, and mounts;
- workload and capture timeouts;
- output directory and random seed;
- enabled model families and family-specific parameter bounds;
- genetic population, generation, selection, crossover, mutation, elitism,
  and checkpoint settings;
- trial and final generation limits;
- similarity parameters and nonnegative aggregate weights summing to one.

The individual commands expose pipeline stages for development, diagnosis, and
resume. They call the same Python functions used by `run`; they are not separate
applications or subprocess protocols.

### Preflight

`preflight` performs read-only configuration and environment checks. It checks
the Docker daemon, Docker Compose support, target and capture images, output
space, parameter bounds, configured mounts, and a short container DNS/network
probe. It reports actionable failures and does not create a persistent capture
environment.

### Capture

`capture` starts an idle target container on a normal Docker bridge network,
then starts a capture sidecar with `network_mode: service:target`. After the
capture process reports readiness, Trafficlab executes the configured target
argv inside the target container. The default image contract requires a POSIX
shell and an idle command suitable for `docker compose exec`.

The capture sidecar receives only the Docker capabilities required by its
packet-capture tool. This is an operational Docker requirement, not a Trafficlab
security boundary. It writes directly to the run directory through a bind mount.

Trafficlab stops capture with `SIGINT`, waits for buffered packets to flush,
checks the target and capture exit states, and validates that the resulting
PCAPNG is nonempty and parseable. Cleanup always runs:

```text
docker compose down --volumes --remove-orphans
```

Each run uses a unique Compose project name. Docker owns namespace creation,
Internet NAT, forwarding, DNS integration, and resource removal. Trafficlab
does not edit host routes, firewall rules, sysctls, users, groups, or sudo
configuration.

### Fit

`fit` extracts a canonical trace, builds a heterogeneous initial genetic
population across every enabled model family, evaluates candidates using common
trial seeds and limits, and checkpoints after every generation. It can resume
from the latest valid checkpoint. It writes the winning family and fitted model
to `best_model.json` and per-family progress to `ga_history.csv`.

### Generate

`generate` loads the winning fitted model and uses a distinct configured final
seed to produce the full synthetic trace and `generated.pcapng`. Trial
generations inside genetic fitting are smaller and do not serve as the final
output.

### Compare

`compare` evaluates the reference and final generated traces with every enabled
similarity method. It writes all component diagnostics and the configured
weighted aggregate to `similarity.json`. The aggregate never replaces the
component scores.

### Run

`run` executes:

```text
preflight -> capture -> fit -> generate -> compare
```

It stops at the first failure, always cleans Docker resources, preserves valid
completed outputs, and resumes genetic fitting from its checkpoint when
requested. It may reuse a completed stage only after validating the expected
output for that stage.

## Run Outputs

A run directory contains:

```text
experiment.toml
reference.pcapng
checkpoint.json
ga_history.csv
best_model.json
generated.pcapng
similarity.json
run.log
```

`experiment.toml` is the exact effective configuration snapshot. Input hashes
are stored directly in model and similarity results where they help reproduce
an experiment. They do not require separate provenance objects or graph APIs.

Result and checkpoint files are written to a temporary sibling and renamed only
after successful serialization and validation. Existing result files are not
silently overwritten unless the user explicitly selects resume or replacement
behavior.

## Canonical Trace

The research core represents a capture as the ordered sequence

\[
x_i=(t_i,d_i,l_i),
\]

where \(t_i\) is the packet timestamp, \(d_i\) is packet direction, and \(l_i\)
is captured frame length. The MVP models timing, direction, and size. It does not
model payload contents or reconstruct application protocols.

Trace extraction rejects unsupported link types, malformed frames,
non-monotonic timestamps, and samples too small for the requested method.
Generated events are bounded by both packet count and duration. PCAPNG rendering
uses deterministic dummy frames whose lengths and directions match generated
events.

## Model Interface and Competition

Every model family implements the same conceptual interface:

```text
fit(reference, genes) -> fitted model
generate(fitted model, seed, limits) -> synthetic trace
serialize(fitted model) -> JSON-compatible value
```

An individual contains `model_family` and that family's chromosome. All
families use the same reference, trial seeds, generation limits, similarity
methods, and fitness weights.

The initial population reserves candidates for every enabled family. At least
one family champion survives each generation so a family is not eliminated by
one unlucky stochastic trial. Normal crossover occurs only between compatible
chromosomes from the same family. When selected parents belong to different
families, the fitter parent is cloned and mutated. A cross-family operation
never combines unrelated parameters.

The GA compares behavioral fidelity, not chromosome shape. The history records
the best candidate and score for each family in each generation. The final
result states which family won.

### Poisson Empirical

The baseline estimates a homogeneous arrival rate from the reference duration
and packet count. Inter-arrival times are exponentially distributed. Direction
and frame-length pairs are sampled from their joint empirical distribution so
their observed association is preserved. Its sole gene is a bounded multiplier
on the maximum-likelihood arrival-rate estimate.

### Markov Renewal

Each packet maps to a state

\[
J_i=(d_i,b(l_i)),
\]

where \(b\) maps frame length into size bins. The model estimates transition
probabilities

\[
p_{jk}=P(J_{i+1}=k\mid J_i=j)
\]

and transition-conditioned holding-time distributions

\[
F_{jk}(u)=P(t_{i+1}-t_i\le u\mid J_i=j,J_{i+1}=k).
\]

Generation samples a next state and then a holding time. Sparse transitions use
a documented source-state or global empirical fallback. The MVP chromosome
contains two ordered size-quantile boundaries, transition smoothing strength,
minimum conditional timing support, and a bounded global timing-scale
correction.

### Two-State MMPP

The MMPP has a two-state continuous-time Markov chain with generator \(Q\) and
state-dependent Poisson rates \(\lambda_0\) and \(\lambda_1\):

\[
Q=\begin{bmatrix}-q_{01}&q_{01}\\q_{10}&-q_{10}\end{bmatrix},
\qquad 0<\lambda_0<\lambda_1.
\]

The chromosome directly contains positive \(q_{01},q_{10},\lambda_0,\lambda_1\)
within configured bounds; the GA optimizes them by simulation fitness rather
than a separate likelihood fit. Generation alternates exponentially distributed
CTMC sojourns and homogeneous Poisson arrivals at the active state's rate.
Direction and frame-length pairs are sampled from the same joint empirical mark
distribution used by the Poisson baseline, independent of the latent state.

## Genetic Model

The basic generational algorithm uses:

- deterministic seeded initialization around family-specific defaults;
- tournament selection;
- fixed elitism plus one champion per enabled family;
- family-specific bounded crossover and mutation;
- repair or rejection of invalid constrained chromosomes;
- common random trial seeds for fair candidate comparison;
- hard population, generation, event, and wall-time limits;
- an atomic checkpoint after every generation;
- final winner evaluation across several fresh seeds.

Fitness is a configured weighted mean of similarity scores. Invalid candidates
receive a documented worst score and diagnostic reason; infrastructure failures
abort the run instead of being misclassified as poor models.

## Similarity Methods

For reference trace \(R\), generated trace \(G\), component scores \(s_m\), and
weights \(w_m\), the aggregate is

\[
S(R,G)=\sum_m w_m s_m(R,G),\qquad w_m\ge 0,\qquad \sum_m w_m=1.
\]

Frame-size and IAT KS similarities use

\[
s_{KS}=1-\sup_x|F_R(x)-F_G(x)|.
\]

Autocorrelation is computed separately for the IAT sequence and frame-length
sequence using the ordinary mean-centered sample autocorrelation at configured
positive lags. For feature \(f\), lag set \(L\), and lag weights \(a_l\), its
bounded discrepancy is

\[
D_f=\sum_{l\in L}a_l\frac{|\rho_{R,f}(l)-\rho_{G,f}(l)|}{2}.
\]

A constant series has autocorrelation zero at every positive lag. The final
autocorrelation similarity is one minus the configured weighted mean of IAT and
frame-length discrepancies.

For multiscale rate, each configured width produces aligned nonnegative packet
count and byte count vectors \(r\) and \(g\) over a common horizon. Each vector
pair uses normalized L1 discrepancy

\[
D(r,g)=\frac{\sum_i|r_i-g_i|}{\sum_i r_i+\sum_i g_i},
\]

with \(D=0\) when both sums are zero. The multiscale similarity is one minus the
configured weighted mean of these discrepancies across features and widths.
These score constructions are Trafficlab engineering definitions rather than
standard named statistics.

## Reliability and Failure Handling

Reliability mechanisms are limited to failures expected in research use:

- deterministic random seeds;
- explicit time, packet, event, file-size, population, and generation bounds;
- Docker readiness checks and unique project names;
- signal-aware, idempotent Compose cleanup;
- atomic checkpoints and final structured outputs;
- parse and semantic validation after capture, model loading, and generation;
- preservation of completed results after later-stage failure;
- concise console errors plus full diagnostic logging;
- checkpoint resume for genetic fitting.

The prototype does not implement permission policing, inode pinning, symlink
defenses, custom filesystem syscalls, protected manifests, immutable publication
transactions, secret detection, multi-user authorization, or rollback of host
configuration that Trafficlab does not modify.

## Testing Strategy

### Unit Tests

Unit tests cover configuration parsing, hand-calculated mathematical examples,
estimators, model invariants, deterministic generation, chromosome bounds,
mutation and crossover, aggregation, PCAPNG parsing/rendering, and malformed
inputs. Every equation published in an algorithm document has at least one
small deterministic test whose expected value can be checked independently.

### Integration Tests

In-process integration tests exercise:

- PCAPNG to canonical trace to features;
- reference to fitted model to generated trace to similarity;
- all three model families in one genetic population;
- interruption, checkpoint persistence, and resume;
- stage reuse after output validation;
- full bounded `trafficlab run` against fixture inputs.

### Docker Capture Integration Tests

Docker tests invoke the real Docker and Compose CLIs. A session fixture checks
daemon and Compose readiness. When the integration suite is explicitly
selected, missing Docker is an actionable failure, not a silent skip.

Each capture test uses a unique Compose project. It runs a deterministic
containerized client against a controlled endpoint, verifies expected traffic
in the PCAPNG, and removes all containers, networks, and volumes in teardown.
The default integration suite does not depend on the public Internet. A separate
opt-in Internet smoke test executes a real external request to verify the
production capture topology without making deterministic CI depend on an
external service.

The normal developer command runs the fast unit suite. A documented integration
command runs unit and integration tests. CI runs Docker capture tests only on a
runner explicitly configured with Docker.

## Roadmap Format

There is one `architecture/ROADMAP.md`, ordered by usable increments:

1. project skeleton and experiment configuration;
2. Docker preflight and capture;
3. canonical trace and similarity methods;
4. three fitted traffic models;
5. heterogeneous genetic fitting and checkpoint resume;
6. complete CLI workflow and integration suite;
7. validation on representative real containerized programs.

Each phase contains exactly the information needed to execute it:

- **Goal**
- **Deliverables**
- **Tests**
- **Done when**

Tasks use ordinary Markdown checkboxes. One incomplete phase may be labelled
`Current`. There are no percentages, controlled status vocabulary, nested
stage/step/substep arithmetic, component roadmaps, reciprocal links, evidence
boilerplate, or roadmap validators.

The roadmap ends with `Later, only if evidence requires it`, which may mention
ON/OFF Pareto, replay, richer packet marks, parallel evaluation, or additional
similarity methods. Backlog entries do not imply committed scope.

## Acceptance Criteria for the Architecture Corpus

The new `architecture/` folder is complete when:

1. It contains only the documents listed in this design.
2. A reader can trace capture through final comparison without consulting the
   old architecture.
3. Capture design uses Docker and never requires Trafficlab-managed host network
   configuration.
4. All three model families can compete under one common fitness definition.
5. Every algorithm document gives concise, sourced mathematics and clearly
   labels Trafficlab-specific choices.
6. Unit, in-process integration, Docker capture integration, and opt-in Internet
   smoke tests are defined.
7. The roadmap is an ordered checklist with observable completion conditions.
8. No document introduces security hardening, speculative implementation
   components, compatibility versions, or governance machinery outside the
   approved scope.
