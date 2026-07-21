# Genetic Training Software Architecture Document

## Role

This application trains traffic-model candidates against their configured
reference source. Non-neural models use exactly one reference PCAPNG file.
The registered neural models may instead use exactly one reference PCAPNG file
or one directory of reference captures. It orchestrates model creation,
traffic generation, and similarity evaluation, then publishes one winning
model and a ranking report.

## Interface

The conceptual command is:

```text
genetic_training --config-file PATH --output-dir DIR \
  [--reference PATH | --reference-directory PATH | \
   --reference-contract STATUS_PATH --reference-member MEMBER]
```

The configuration and output arguments are required. The
[authoritative configuration schema](CONFIGURATION_SCHEMA.md) owns every
setting, default, override, reference-selector rule, and generation limit. The
external guide and template are derived from it.

## Reference Source Resolution

Exactly one effective source selector is required:

- `reference` selects one explicit PCAPNG file;
- `reference_directory` selects one directory and is valid only when all
  allowed models are neural; or
- `reference_contract` plus `reference_member` selects one explicit
  `artifact-status.toml` for a validated capture-directions package and exactly
  one member named `target`, `uplink`, or `downlink`.

Contract mode validates detached manifest status, exact package membership,
the selected member digest, and the selected PCAPNG before candidate work. It
records the resolved member path and digest. The caller must name the intended
member; `target` is not an implicit default, and no recent package is searched.

## Candidate Rules

A candidate is one named traffic model with that model's current parameter
values. Candidates using different traffic-model names compete in the same
run. Crossover is allowed only between candidates with the same traffic-model
name. Mutation may replace an allowed traffic model with another allowed
traffic model.

## Orchestration

For every candidate, this application creates a deterministic diagnostic
namespace below its own attempt directory. That namespace is not itself a
status-bearing application attempt. Each artifact-producing child application
or in-process preparation operation receives a distinct empty private leaf
attempt so its fixed `artifact-status.toml` path cannot collide with another
artifact:

```text
<training-attempt>/candidates/g000000_s000000/
  model_creation/
  model_preparation/
  unit_000000/
    traffic_generation/
    similarity_evaluation/
```

Each leaf contains its own immutable `launch.toml`, diagnostics, absent
artifact destination, and eventual detached status. Non-neural candidates have
one validation unit; neural candidates have one numbered unit subtree per
assigned validation capture. When a one-file neural split requires a derived
suffix, run-wide reference preparation publishes it through its own leaf under
`<training-attempt>/reference_units/`; unchanged whole-file units retain their
explicit source digests. All directory identifiers derive canonically from
stable candidate/unit order, never source filenames or child completion order.
The successful final `DIR` artifact destination remains absent until winner
publication.

The per-operation adapters are exact:

| Operation | Assigned output |
|---|---|
| model creation | `--output-dir <model_creation-leaf>`; absent `<leaf>/<model-name>.toml` |
| model preparation | in-process absent `<model_preparation-leaf>/<model-name>.toml` |
| derived validation reference | in-process absent `<reference-unit-leaf>/reference.pcapng` |
| traffic generation | `--output <traffic_generation-leaf>/synthetic.pcapng` |
| similarity evaluation | `--output-dir <similarity_evaluation-leaf>`; absent `<leaf>/similarity.toml` |

Every child process additionally receives its exact leaf as `--attempt-dir`.
Every in-process publisher supplies that leaf and its immutable operation
`launch.toml` to the shared artifact protocol. No adapter pre-creates a listed
artifact file.

1. [40 model creation](../40_model_creation/README.md) creates the selected
   model's immutable normal builder in the candidate's `model_creation` leaf.
2. This application copies builder values into an in-memory candidate
   configuration, applies fixed run context and every initialized, mutated,
   crossover-derived, or replacement value, and validates the complete
   candidate. Fixed neural split controls are identical across candidates.
3. The selected model's preparation protocol consumes that configuration and
   reference assignment and atomically publishes a distinct immutable
   generation-ready model in the `model_preparation` leaf.
4. [50 traffic generation](../50_traffic_generation/README.md) creates one
   generated PCAPNG file in the matching unit's generation leaf.
5. [60 similarity evaluation](../60_similarity_evaluation/README.md) compares
   that generated file with the one reference PCAPNG and publishes one
   `similarity.toml` in the matching similarity leaf under the
   [similarity result contract](../../contracts/60_30_similarity_result/README.md).

The application retains every candidate namespace, operation attempt, and
child diagnostic outside the successful artifact destination. It validates
each leaf's detached status before handing its artifact to another operation.
A candidate with a failed child application or preparation operation is
ineligible to win; training continues with other candidates.

### Candidate Model Lifecycle

Model values move through three immutable states:

```text
builder -> candidate configuration -> generation-ready model
```

Genetic training never edits, replaces, or republishes the builder path.
Preparation occurs only after all candidate parameters validate, so Markov
state counts and clustering controls affect state construction and neural
hyperparameters affect window preparation and fitting. A model needing no
reference fit still performs a deterministic finalize step and publishes a
distinct generation-ready file.

The prepared model records its explicit generation-ready state, builder
digest, candidate identity, reference assignment and hashes, effective
parameters, seeds, implementation versions, and model-owned fitted data or
finalization result. Traffic generation receives that file, never the builder
or mutable candidate configuration.

### Neural Source and Candidate Evaluation

For `neural_hawkes` and `marked_point_process_diffusion`, the application
accepts exactly one effective source: one direct PCAPNG, one explicitly
selected directional-contract member, or one reference directory. A directory
is available only when every allowed model is neural. The neural source,
deterministic directory membership, independent
capture validation, file-boundary separation, deterministic window preparation,
and deterministic training/validation assignment are owned by the [neural
marked-point-process common rules](../../traffic_models/NEURAL_MARKED_POINT_PROCESS.md).
The application applies the configuration's selected split policy and retains
the resulting assignment; it never merges captures or lets examples cross a
capture boundary. For `C >= 2` source files and validation fraction `f`, the
last `min(C - 1, ceil(f * C))` files in lexical relative-path order are
validation units and preceding files train.

For one source with `N` derived events, `V = ceil(f * N)`: training may use
only `E_1` through `E_(N-V-1)`, `E_(N-V)` is an unused guard, and validation
uses `E_(N-V+1)` through `E_N`. The derived validation PCAPNG contains anchor
frame `F_(N-V)` through `F_N`; that anchor supplies the first validation IAT
and participates in frame comparison but never in training. The split requires
`N - V >= 2` plus both models' minimum complete units and otherwise fails
without clamping or moving the boundary. The common neural owner defines
[the exact split and artifact semantics](../../traffic_models/NEURAL_MARKED_POINT_PROCESS.md#windows-and-validation-split).

Each neural candidate fits its learned weights locally from that candidate's
assigned training windows using the selected model's deterministic fitting
procedure. Genetic operations may vary only the selected model's declared
high-level hyperparameters; they never directly mutate, crossover, or search
learned weights. The candidate then creates and evaluates one generated PCAPNG
artifact for each unchanged whole-file or derived-suffix validation unit,
comparing it only with that unit rather than a complete mixed source capture.
Each immutable per-unit generation input records the trained-model parent
digest and uses the unit's exact packet count, complete-window count, or
timestamp horizon as its model-owned stop value. Fitness is the equal-weight
arithmetic mean of those per-unit similarity results. Custom weights, capture
merging, and a single substitute aggregate capture are unsupported.

Every required capture-level preparation, fit, generation, or evaluation must
succeed. Failure for any one capture fails the complete neural candidate and
makes it ineligible to win; it must not publish a partial aggregate fitness.

## Generation Resource Envelope

Every traffic-generation child receives the configured positive
`resources.generation.max_packets`, `max_output_bytes`, and `max_proposals`
values as `--max-packets`, `--max-output-bytes`, and `--max-proposals`.
They have no defaults. A packet-count stop above `max_packets`, or a
window-count stop whose validated `window_count * max_events_per_window` upper
bound exceeds `max_packets`, fails before child launch. Reaching any limit
before the model stop completes fails the candidate without a successful
generated artifact; it never truncates or repairs output. These limits apply
separately to every neural validation unit.

## Output and Diagnostics

The run artifact contains one generation-ready winning model file and a
ranking report. It is a package whose manifest hashes all non-manifest members
and whose detached successful status binds the manifest. Each attempt follows the shared
[startup record](../../CONFIGURATION.md#startup-record) rule. `launch.toml`
is retained for successful, failed, and interrupted training attempts.

For a neural attempt, `launch.toml`, the candidate directory, trained model,
and ranking report retain complete lineage and diagnostics: source mode; exact
source path or deterministic directory membership; every capture-artifact hash;
window count; train/validation assignment; effective split and model
configuration; seeds; library identities and versions; learned-weight hash;
each generated/evaluated validation artifact and similarity result; and the
equal-weight aggregate. Diagnostics state the capture count and, for a
single-capture source, that validation may overfit that capture.

## Boundaries

This application owns fitness interpretation, population handling, selection,
mutation, crossover, model replacement, stopping, and winner selection. It
does not own traffic-model equations, model-file schemas, synthetic-traffic
behavior, or similarity formulas.

Traffic-model facts belong to [traffic models](../../traffic_models/README.md),
strategy facts belong to [genetic models](../../genetic_models/README.md), and
scoring facts belong to [similarity methods](../../similarity_methods/README.md).

## Implementation and Testing

No implementation exists yet. Future tests are unprivileged. Functional-core
tests cover deterministic candidate handling, immutable builders, same-model
crossover enforcement, model replacement, failed-candidate handling, winner
selection, directional source selection, deterministic split assignment,
candidate-local fitting and preparation,
per-validation-capture evaluation, equal-weight aggregation, and failure of a
candidate with any failed capture. Imperative shell tests use temporary
directories and fake child applications to verify argument vectors, all three
generation limits, aggregate lineage, detached publication status, and
retained diagnostics.

## Reading

Follow the [architecture governance](../../README.md), [application
configuration](../../CONFIGURATION.md), and the selected component owners
before changing this application.

## Cross-Cutting Architecture

Candidate identity, genetic decisions, aggregation, stopping, and ranking form
the deterministic core. Files, subprocesses, resources, clocks, and publication
form the shell. Required generation limits and bounded reservations control
work; completion order never controls random draws. Child arguments and
artifacts are untrusted boundaries. Risks include search explosion, neural
overfit, numerical failure, and retained candidate storage; configuration and
lineage expose these limits.
