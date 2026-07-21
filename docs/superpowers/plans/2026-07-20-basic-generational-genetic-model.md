# Basic Generational Genetic Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the authoritative `basic_generational` genetic-strategy document and its exact genetic-training configuration template and reference.

**Architecture:** One new strategy owner defines the deterministic functional-core algorithm while the existing 30 genetic-training application remains the imperative orchestrator for files and child applications. The versioned TOML template exposes safe defaults without silently choosing a capture or search domain, and the configuration reference owns every field name, type, default, override, and validation rule.

**Tech Stack:** Markdown, TOML, Python 3.12 `tomllib` for static template validation, Git.

## Global Constraints

- Create `architecture/genetic_models/basic_generational/README.md`; do not modify any original architecture document or create an amendment.
- Use selectable strategy name `basic_generational` and full generational replacement with a constant configured population size.
- One candidate is one allowed traffic-model name plus its current trainable parameters; different model names compete globally.
- Use tournament parent selection, at least one elite, same-model-only uniform crossover, mutually exclusive local parameter mutation and traffic-model replacement, and bounded whole-offspring retries.
- Crossover may mix only model-declared trainable fields and must preserve compatible non-trainable settings from the first parent.
- Only configured search parameters may be initialized or locally mutated; omitted trainable parameters retain their prepared run-baseline values.
- Prepare every candidate through the existing 30 → 40 orchestration and any model-owned run preparation before applying genetic parameter values.
- Use one seeded deterministic random stream with canonical generation, slot, attempt, operator, candidate, model, choice, and dotted-parameter ordering.
- Evaluate every population member through 30 → 50 → 60, including copied elites; do not introduce score caching.
- Treat an unsuccessful candidate evaluation as ineligible, fail when no valid candidate remains, and never shrink a population or silently repair an invalid model.
- Support maximum-generation, target-score, and no-improvement stopping; stop after a complete population when any enabled condition is satisfied.
- Keep the combined active configuration at `.configs/genetic_strategy.toml`, its versioned template at `configs/30_genetic_training/genetic_strategy.toml`, and its exact reference at `docs/configs/30_genetic_training.md`.
- Resolve effective values as built-in defaults → the required `--config-file` → repeatable `--set` leaf overrides; parse override values as TOML and never as shell or Python code.
- Keep one reference PCAPNG and one generated PCAPNG comparison per candidate; do not introduce directional or multi-reference evaluation.
- Preserve model-file hashes, parent and operation lineage, stable candidate ordering, deterministic serialization, and atomic successful publication through the existing application and contract boundaries.
- Add no Python implementation, genetic-algorithm dependency, PCAPNG processing, network access, capture action, elevated operation, or roadmap file.

---

### Task 1: Add the `basic_generational` strategy owner

**Files:**
- Create: `architecture/genetic_models/basic_generational/README.md`

**Interfaces:**
- Consumes: `architecture/genetic_models/README.md`, `architecture/apps/30_genetic_training/README.md`, the selected traffic-model owner, the selected similarity-method owner, `architecture/contracts/60_30_similarity_result/README.md`, and the approved design in `docs/superpowers/specs/2026-07-20-basic-generational-genetic-model-design.md`.
- Produces: the complete algorithm and validation rules selected by genetic-strategy name `basic_generational`; Task 2 documents the exact configuration fields that control these rules.

- [ ] **Step 1: Create the role, ownership, and component-boundary sections**

  Create `architecture/genetic_models/basic_generational/README.md` with the
  title `Basic Generational Genetic Strategy`. State that
  `basic_generational` is selectable by 30 genetic training and forms a whole
  next population only after the current population has been fully evaluated.

  Link, using paths relative to the new file:

  ```text
  ../README.md
  ../../apps/30_genetic_training/README.md
  ../../apps/40_model_creation/README.md
  ../../apps/50_traffic_generation/README.md
  ../../apps/60_similarity_evaluation/README.md
  ../../contracts/60_30_similarity_result/README.md
  ../../traffic_models/README.md
  ../../traffic_models/poisson_empirical/README.md
  ../../similarity_methods/README.md
  ../../../docs/configs/30_genetic_training.md
  ```

  Keep ownership explicit: this document owns the genetic algorithm,
  ordering, stopping, failure, and strategy validation; 30 owns file and
  subprocess orchestration; traffic models own trainable fields, schemas, and
  complete model validation; similarity methods own score meaning, range, and
  direction; the configuration reference owns TOML fields and defaults.

- [ ] **Step 2: Define candidates, run baselines, fitness, and stable identity**

  Define one candidate as one allowed traffic-model name with that model's
  current parameter values. For every candidate, require 30 to obtain the
  normal model file through 40 and apply model-owned run preparation, including
  the reference-derived table required by `poisson_empirical`, before applying
  genetic values. Call those prepared values the model's run baseline.

  State that crossover and local mutation never alter non-trainable settings;
  model replacement adopts the replacement model's complete prepared baseline
  and translates no setting from the previous model. A candidate becomes
  eligible only after 50 generation and 60 evaluation succeed and the primary
  score validates under the result contract.

  Compare fitness only through the selected similarity method's declared
  direction. Do not normalize or assume higher-is-better. Define strict
  improvement as a genuinely better primary score. Define deterministic ID
  ordering as the numeric tuple `(generation, slot)` and use the lower ID for
  elite ordering, reporting, and final-winner score ties; an ID tie-break is
  not a stagnation improvement.

- [ ] **Step 3: Define deterministic randomness and the initial population**

  Require one pseudo-random stream initialized from the configured strategy
  seed. Draw in generation, slot, whole-attempt, and operator order. Before
  drawing, order candidates by stable ID, retain configured allowed-model and
  choice-value order, and order parameter paths lexicographically by dotted
  path. Never depend on mapping iteration or subprocess completion order.
  Record the strategy implementation and random-generator name and version.

  Define population `0` exactly:

  1. Require population size to be at least `2` and at least the allowed-model
     count.
  2. Place one unchanged prepared run-baseline candidate for every allowed
     model in configured model-list order.
  3. Fill remaining slots by cycling through that model list.
  4. For each remaining candidate, sample every configured floating parameter
     uniformly within inclusive bounds, every integer parameter uniformly from
     its inclusive discrete bounds, and every choice parameter uniformly from
     configured values; retain baseline values for omitted fields.
  5. Validate each complete candidate. On failure, resample the whole candidate
     up to the configured attempt limit, then fail the run if exhausted.

  Permit duplicate candidates and forbid retries whose only purpose is
  uniqueness.

- [ ] **Step 4: Define generation evaluation, tournament selection, and elitism**

  Require every completed population member, including a copied elite, to run
  through 50 and 60. Wait for the entire population and restore stable-ID order
  before stopping or reproduction. Retain failed candidates as diagnostics but
  exclude them from parenting, elitism, and winning. Fail if the generation has
  no valid candidate.

  Define a tournament as distinct uniform sampling without replacement from
  its eligible pool. Its effective size is
  `min(configured tournament size, eligible pool size)` and is recorded. The
  best fitness wins; a tournament score tie is resolved through the seeded
  random stream. A candidate may enter different tournaments and parent
  multiple children.

  Select the first parent from every eligible candidate so model types compete
  globally. Copy the configured number of best valid elites into leading next-
  generation slots without genetic operations. Require at least one configured
  elite and fewer elites than population slots. If failures leave fewer valid
  candidates than requested elites, copy all valid candidates and record the
  reduced count. Use the lower stable ID for elite score ties.

- [ ] **Step 5: Define same-model uniform crossover**

  For every non-elite slot, select the first parent globally, then make the
  independent configured crossover draw. When crossover is not selected,
  begin with a complete clone of the first parent.

  When selected, form the second-parent eligible pool from candidates with the
  first parent's exact model name and exclude the first parent. If that pool is
  empty, clone the first parent and record skipped crossover. Otherwise select
  the second parent by the same effective-size tournament rule. Require
  identical non-trainable settings between parents; incompatibility invalidates
  and restarts the whole offspring attempt.

  For compatible parents, visit every model-declared trainable parameter in
  canonical dotted-path order and independently copy its complete value from
  either parent with probability `0.5`. Keep the first parent's non-trainable
  values. Validate the complete mix. If only the combination violates a cross-
  parameter constraint, use the fitter complete parent, or the lower-ID parent
  on a score tie, and record the rejected mix and fallback. Never cross model
  names, repair individual fields, or clamp values.

- [ ] **Step 6: Define mutation, replacement, and next-population formation**

  After clone or crossover, make one categorical mutation decision: model
  replacement, local parameter mutation, or no mutation. Require both
  configured mutation probabilities in `[0, 1]` and their sum at most `1`.
  The no-mutation probability is `1` minus that sum. Elites never reach this
  draw.

  For local mutation, choose exactly one configured search parameter of the
  current model uniformly. Define the proposal by parameter kind:

  ```text
  float:   current + Uniform(-mutation_step, +mutation_step)
  integer: current + UniformInteger({-mutation_step, ..., -1, 1, ..., +mutation_step})
  choice:  one configured value other than the current value, uniformly
  ```

  Require a changed, in-range value and a valid complete model. An invalid
  proposal invalidates the whole attempt; do not clamp, reflect, repair, or
  random-reset it.

  For replacement, require a different model selected uniformly from the
  configured allowed-model order. Require at least two models whenever its
  probability is nonzero. Have 30 create and prepare the replacement model's
  baseline, sample its configured search parameters by the initial-population
  rules, retain omitted baseline parameters, and validate the complete model.
  Run no additional parameter mutation and translate no values across schemas.

  Define each next population in this order:

  1. copy effective elites to leading slots;
  2. for each remaining slot select a first parent;
  3. apply the independent crossover rule;
  4. apply exactly one mutation-category decision;
  5. validate the complete child; and
  6. on success assign its `(generation, slot)` ID, or on incompatible parents
     or invalid mutation/replacement discard the whole attempt and restart
     from parent selection.

  Keep an invalid crossover mix's fitter-parent fallback within the current
  attempt. Bound complete attempts per slot, fail on exhaustion, and keep the
  configured population size constant. Never shrink, clamp, repair, or insert
  an unrecorded clone.

- [ ] **Step 7: Define stopping, validation, lineage, and future tests**

  Check stopping only after every candidate in a population has completed.
  Count population `0` as the first maximum-generation evaluation. Support:

  - an always-effective positive maximum-generation limit;
  - an optional finite target score, reached with `>=` for higher-is-better or
    `<=` for lower-is-better; and
  - an optional positive no-improvement generation limit.

  Set the no-improvement counter to zero after population `0`; increment after
  a later population has no strictly better candidate and reset only on strict
  score improvement. Stop when any enabled condition is true, record all that
  become true together, and choose the best valid candidate seen across the
  whole run with the deterministic ID tie-break.

  Require complete pre-run validation of component names, model list,
  population and tournament sizes, elitism, finite probabilities, mutation-
  probability sum, replacement compatibility, attempt and stopping values,
  target compatibility, parameter paths/types/trainability, baseline inclusion,
  and nondegenerate search spaces. Validate every complete model before 50.
  Treat no valid candidate, attempt exhaustion, corrupted lineage, or invalid
  stopping state as a failed run without a successful winner.

  Require retained diagnostics for stable IDs and slots, model hashes, parent
  and elite sources, effective tournaments and tie decisions, crossover state,
  mutation/replacement state, every discarded attempt and reason, generation
  and similarity lineage, eligibility, generation ordering, best-so-far state,
  stopping counters, and simultaneous stop reasons.

  Require candidate model serialization to be canonical and deterministic,
  validation and hashing before traffic generation, and atomic publication of
  successful final artifacts through 30's existing boundary.

  Define future deterministic, unprivileged functional-core tests for every
  initialization distribution, ordering rule, tournament reduction and tie,
  elite case, crossover branch, mutation kind, replacement branch, retry and
  failure path, score direction, and stopping combination. Define imperative-
  shell tests with temporary directories and fake 40/50/60 applications for
  argument vectors, barriers, retained failure diagnostics, hashes, elite
  reevaluation, and atomic final publication. Forbid real capture, network, or
  elevation in every test.

- [ ] **Step 8: Validate and commit Task 1**

  Run:

  ```bash
  git diff --check
  rg -n -- 'basic_generational|run baseline|strictly better|generation, slot|subprocess completion|population `0`|without replacement|same.*model|0\.5|model replacement|whole.*attempt|maximum-generation|target score|no-improvement|elevat' architecture/genetic_models/basic_generational/README.md
  rg -n -- '\.\./README\.md|30_genetic_training|40_model_creation|50_traffic_generation|60_similarity_evaluation|60_30_similarity_result|traffic_models|similarity_methods|docs/configs/30_genetic_training' architecture/genetic_models/basic_generational/README.md
  ```

  Expected: the strategy owner contains every algorithm phase and failure
  boundary, all external facts are relative links to their owners, and no
  numeric configuration default is duplicated from Task 2's configuration
  reference.

  Commit:

  ```bash
  git add architecture/genetic_models/basic_generational/README.md
  git commit -m "docs(architecture): add basic genetic strategy"
  ```

### Task 2: Define the genetic-training template and configuration reference

**Files:**
- Modify: `configs/30_genetic_training/genetic_strategy.toml`
- Modify: `docs/configs/30_genetic_training.md`

**Interfaces:**
- Consumes: the algorithm owned by `architecture/genetic_models/basic_generational/README.md`, shared precedence from `architecture/CONFIGURATION.md`, and the explicit 30 application interface.
- Produces: one safely incomplete versioned template and the exact public TOML/CLI contract for selecting and configuring `basic_generational`.

- [ ] **Step 1: Replace the empty template with exact defaults and commented required choices**

  Replace `configs/30_genetic_training/genetic_strategy.toml` with exactly this
  syntactically valid TOML template:

  ```toml
  # Copy this file to .configs/genetic_strategy.toml before use.
  # The required capture, component choices, and search ranges stay commented
  # so this template cannot silently select an unintended training domain.

  schema_version = 1

  # Required: replace the example values and uncomment all three settings.
  # reference = "/path/to/reference.pcapng"
  # allowed_traffic_models = ["poisson_uniform", "poisson_empirical"]
  # similarity_method = "l2_ks_weighted"

  [strategy]
  name = "basic_generational"
  strategy_seed = 0
  population_size = 10
  elite_count = 1
  tournament_size = 3
  crossover_probability = 0.8
  parameter_mutation_probability = 0.2
  model_replacement_probability = 0.05
  offspring_attempt_limit = 100

  [strategy.stopping]
  maximum_generations = 50
  # Optional: stop when the selected method reaches this score.
  # target_score = 0.95
  # Optional: stop after this many complete generations without improvement.
  # no_improvement_generations = 10

  # Required when parameter_mutation_probability is positive: define at least
  # one mutable search parameter for every allowed model. Values below are only
  # examples and must be chosen for the run's real search domain.
  #
  # [strategy.search.poisson_uniform.arrival_rate_pps]
  # minimum = 1.0
  # maximum = 100.0
  # mutation_step = 5.0
  #
  # [strategy.search.poisson_empirical.arrival_rate_pps]
  # minimum = 1.0
  # maximum = 100.0
  # mutation_step = 5.0
  ```

  Do not add a real reference path, silently active search range, output path,
  generated filename, directional inputs, or settings belonging to another
  application.

- [ ] **Step 2: Document invocation, resolution, and safe `--set` parsing**

  In `docs/configs/30_genetic_training.md`, retain links to the versioned
  template, 30 application owner, and shared configuration owner. Replace the
  statement that no settings exist with this conceptual invocation:

  ```text
  genetic_training --config-file .configs/genetic_strategy.toml \
      --output-dir DIR \
      [--set DOTTED.PATH=TOML_VALUE ...]
  ```

  State that `--config-file` and `--output-dir` are required and that 30 does
  not use `--config-dir` or automatic search. The output directory is not a
  TOML setting. Resolve each effective setting as built-in default, then file,
  then repeatable `--set`.

  Require each override path to identify one documented leaf. Parse the value
  as TOML data without `eval`, shell interpretation, or string coercion. Permit
  arrays as atomic leaf values and replace rather than merge them; reject table
  replacement, unknown paths or keys, duplicate paths, malformed TOML, type
  mismatches, and an invalid final configuration before population creation.
  Include these examples:

  ```text
  --set strategy.population_size=20
  --set 'allowed_traffic_models=["poisson_uniform"]'
  --set strategy.model_replacement_probability=0.0
  ```

- [ ] **Step 3: Document every top-level and scalar strategy setting**

  Define these exact types, defaults, accepted values, and effects:

  | Path | TOML type | Built-in default | Accepted value and effect |
  |---|---|---:|---|
  | `schema_version` | integer | none; required | exactly `1` |
  | `reference` | string | none; required | one readable regular reference PCAPNG path; resolve relative paths from the invocation working directory, with no shell or environment expansion |
  | `allowed_traffic_models` | array of strings | none; required | nonempty, unique, registered model names in initialization/replacement order |
  | `similarity_method` | string | none; required | one registered method whose primary result supplies fitness |
  | `strategy.name` | string | `"basic_generational"` | exactly `"basic_generational"` for this strategy |
  | `strategy.strategy_seed` | integer | `0` | any TOML integer |
  | `strategy.population_size` | integer | `10` | at least `2` and at least the allowed-model count |
  | `strategy.elite_count` | integer | `1` | at least `1` and less than population size |
  | `strategy.tournament_size` | integer | `3` | at least `2` and no greater than population size; runtime effective size may be smaller |
  | `strategy.crossover_probability` | float | `0.8` | finite and in `[0, 1]` |
  | `strategy.parameter_mutation_probability` | float | `0.2` | finite and in `[0, 1]`; together with replacement probability, at most `1` |
  | `strategy.model_replacement_probability` | float | `0.05` | finite and in `[0, 1]`; nonzero requires at least two allowed models |
  | `strategy.offspring_attempt_limit` | integer | `100` | at least `1`; also bounds randomized population-0 candidate sampling |

  Resolve the reference to an absolute path for lineage, require an existing
  readable regular file, and record its content identity through the existing
  application and similarity-result boundaries. Do not trust the filename
  extension, execute file contents, or bypass the selected model's and 60's
  PCAPNG validation.

  State explicitly that one-model training is supported only when
  `strategy.model_replacement_probability = 0.0`. Reject booleans as integers,
  non-finite floats, numeric strings, unknown settings, and values outside
  these constraints.

- [ ] **Step 4: Document stopping settings and their exact counting rules**

  Define:

  | Path | TOML type | Built-in default | Accepted value and effect |
  |---|---|---:|---|
  | `strategy.stopping.maximum_generations` | integer | `50` | at least `1`; counts population `0`, so `50` evaluates populations `0` through `49` at most |
  | `strategy.stopping.target_score` | float | none; disabled | finite and inside the selected method's declared range; use the method's declared direction |
  | `strategy.stopping.no_improvement_generations` | integer | none; disabled | at least `1`; counts complete post-population-0 generations without strict score improvement |

  State that every enabled condition is checked only after a full population
  evaluation, any one condition stops the run, and all simultaneously true
  conditions are recorded. Clarify that deterministic candidate-ID tie-breaks
  do not reset no-improvement counting.

- [ ] **Step 5: Document numeric and choice search-space entries**

  Define dynamic paths as:

  ```text
  strategy.search.<allowed-model-name>.<trainable-model-parameter-path>
  ```

  The final portion is the parameter's dotted path in its model file, expressed
  as nested TOML tables. Only trainable parameters declared by an allowed
  traffic model are valid. Omitted trainable parameters retain the prepared
  run-baseline value.

  For integer and floating parameters, require `minimum`, `maximum`, and
  `mutation_step` with the exact numeric kind declared by the model schema.
  Bounds are inclusive, finite where applicable, satisfy `minimum < maximum`,
  contain the prepared baseline value, and `mutation_step` is positive. Integer
  initialization is discrete uniform over both bounds; floating initialization
  is uniform over both bounds. Mutation uses the strategy owner's local-delta
  rule and rejects rather than repairs an invalid complete candidate.

  For a future choice parameter, require only an ordered `values` array with at
  least two distinct values of the schema-declared type, including the prepared
  baseline value. Initialization selects uniformly; mutation selects uniformly
  among values other than the current value. Reject `minimum`, `maximum`, or
  `mutation_step` for a choice entry and reject `values` for a numeric entry.

  When parameter-mutation probability is positive, require at least one valid
  search entry for every allowed model. Reject search entries for disallowed
  models, unknown paths, non-trainable parameters, degenerate domains, and
  individually schema-invalid bounds. State that every sampled complete model
  still undergoes cross-parameter validation and bounded resampling.

- [ ] **Step 6: Add one complete valid example and startup-record behavior**

  Include this complete two-model example:

  ```toml
  schema_version = 1
  reference = "/path/to/reference.pcapng"
  allowed_traffic_models = ["poisson_uniform", "poisson_empirical"]
  similarity_method = "l2_ks_weighted"

  [strategy]
  name = "basic_generational"
  strategy_seed = 0
  population_size = 10
  elite_count = 1
  tournament_size = 3
  crossover_probability = 0.8
  parameter_mutation_probability = 0.2
  model_replacement_probability = 0.05
  offspring_attempt_limit = 100

  [strategy.stopping]
  maximum_generations = 50
  target_score = 0.95
  no_improvement_generations = 10

  [strategy.search.poisson_uniform.arrival_rate_pps]
  minimum = 1.0
  maximum = 100.0
  mutation_step = 5.0

  [strategy.search.poisson_empirical.arrival_rate_pps]
  minimum = 1.0
  maximum = 100.0
  mutation_step = 5.0
  ```

  Label the path, target, stagnation limit, and numeric ranges as examples;
  label target and stagnation as optional.

  Explain that the user copies the versioned template to
  `.configs/genetic_strategy.toml`, supplies the required capture/component/
  search choices, and passes that exact file explicitly. Link `launch.toml` to
  the shared startup-record owner and state that it records the invocation,
  selected source, fully resolved configuration including `--set`, and any
  resolution failure. Do not redefine startup publication or redaction rules.

- [ ] **Step 7: Validate and commit Task 2**

  Run:

  ```bash
  git diff --check
  python3 -c 'import pathlib, tomllib; data = tomllib.loads(pathlib.Path("configs/30_genetic_training/genetic_strategy.toml").read_text()); assert data["schema_version"] == 1; assert data["strategy"]["name"] == "basic_generational"; assert data["strategy"]["stopping"]["maximum_generations"] == 50'
  rg -n -- 'reference|allowed_traffic_models|similarity_method|strategy_seed|population_size|elite_count|tournament_size|crossover_probability|parameter_mutation_probability|model_replacement_probability|offspring_attempt_limit|maximum_generations|target_score|no_improvement_generations|strategy\.search|--set' configs/30_genetic_training/genetic_strategy.toml docs/configs/30_genetic_training.md
  rg -n -- 'array.*atomic|table.*reject|one-model|TOML integer|strict improvement|prepared run-baseline|cross-parameter|launch\.toml' docs/configs/30_genetic_training.md
  ```

  Expected: `git diff --check` and `tomllib` return status `0`; the template
  parses with the documented defaults but keeps project-specific required
  values and search ranges commented; the reference defines every field,
  override, default, constraint, and stopping rule exactly once.

  Commit:

  ```bash
  git add configs/30_genetic_training/genetic_strategy.toml docs/configs/30_genetic_training.md
  git commit -m "docs(config): define genetic training strategy"
  ```

### Task 3: Verify the complete genetic-strategy architecture

**Files:**
- Verify: `architecture/genetic_models/basic_generational/README.md`
- Verify: `configs/30_genetic_training/genetic_strategy.toml`
- Verify: `docs/configs/30_genetic_training.md`
- Verify unchanged: `architecture/README.md`
- Verify unchanged: `architecture/genetic_models/README.md`
- Verify unchanged: `architecture/apps/30_genetic_training/README.md`

**Interfaces:**
- Consumes: the new strategy owner and its coupled configuration artifacts.
- Produces: repository evidence that the strategy is complete, linked,
  deterministic, unprivileged, and added without rewriting original
  architecture.

- [ ] **Step 1: Run static and ownership checks**

  Run after the two task commits:

  ```bash
  git diff --check HEAD~2..HEAD
  git status --short
  git diff --name-only HEAD~2..HEAD -- architecture
  rg -n -- 'basic_generational' architecture/genetic_models configs/30_genetic_training docs/configs/30_genetic_training.md
  rg -n -- 'population|tournament|elite|crossover|mutation|replacement|stopping|lineage|determin' architecture/genetic_models/basic_generational/README.md
  rg -n -- 'sudo|root privilege|elevated privilege|live capture|network access' architecture/genetic_models/basic_generational/README.md
  ```

  Expected:

  - `git diff --check` returns status `0`;
  - `git status --short` prints nothing;
  - the architecture name-only diff contains exactly
    `architecture/genetic_models/basic_generational/README.md`;
  - all three artifacts use the exact strategy name and expose all five genetic
    phases plus deterministic lineage and stopping; and
  - the strategy owner explicitly states that tests need no live network,
    capture, or elevated privilege.

- [ ] **Step 2: Verify relative owner links and configuration separation**

  Run:

  ```bash
  test -f architecture/genetic_models/README.md
  test -f architecture/apps/30_genetic_training/README.md
  test -f architecture/apps/40_model_creation/README.md
  test -f architecture/apps/50_traffic_generation/README.md
  test -f architecture/apps/60_similarity_evaluation/README.md
  test -f architecture/contracts/60_30_similarity_result/README.md
  test -f architecture/traffic_models/README.md
  test -f architecture/similarity_methods/README.md
  test -f docs/configs/30_genetic_training.md
  rg -n -- 'crossover_probability.*0\.8|parameter_mutation_probability.*0\.2|model_replacement_probability.*0\.05|offspring_attempt_limit.*100|maximum_generations.*50' architecture/genetic_models/basic_generational/README.md
  ```

  Expected: every linked owner exists. The final `rg` prints no lines because
  numeric configuration defaults belong only to the configuration reference
  and template, not the genetic algorithm owner.

- [ ] **Step 3: Report completion**

  Report both implementation commit IDs and the validation evidence. State
  explicitly that the work added one new authoritative strategy owner, updated
  only non-architecture configuration artifacts, left all original architecture
  documents unchanged, and added no runtime implementation, dependency,
  generated traffic, network operation, or elevated action.
