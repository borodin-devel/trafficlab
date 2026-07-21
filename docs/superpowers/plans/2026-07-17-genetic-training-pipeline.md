# Genetic Training Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add architecture stubs for interchangeable genetic traffic-model training and its child stages.

**Architecture:** `30_genetic_training` orchestrates `40_model_creation`, `50_traffic_generation`, and `60_similarity_evaluation` through files in per-candidate directories. Named traffic models, genetic strategies, and similarity systems have separate registries; only a model's own owner defines its parameter schema.

**Tech Stack:** Markdown, TOML, Git.

## Global Constraints

- One candidate is one named traffic model with current parameter values; different model types compete in one run.
- Crossover is allowed only between candidates with the same traffic-model name; mutation may replace an allowed model with another allowed model.
- One candidate uses one reference PCAPNG file, creates one generated PCAPNG, and receives exactly one similarity result.
- `30` requires `--config-file .configs/genetic_strategy.toml` and `--output-dir DIR`.
- `40` uses `model_creation --model=NAME --output-dir=DIR` and creates `DIR/NAME.toml` with normal starting values.
- `50` uses `traffic_generation --model-file=PATH --output=PATH` and creates one PCAPNG at the requested path.
- `60` uses `similarity_evaluation --reference=PATH --generated=PATH --method=NAME --output-dir=DIR` and writes `DIR/similarity.toml`.
- Every app writes `launch.toml` at startup in its own run directory; it remains available for success, failure, and interruption.
- No application implementation, mathematics, genetic algorithm, PCAPNG generation, or similarity calculation is in scope.

---

### Task 1: Update shared architecture, configuration, and diagnostics rules

**Files:**
- Modify: `architecture/README.md`
- Modify: `architecture/CONFIGURATION.md`
- Modify: `architecture/apps/00_preflight/README.md`
- Modify: `architecture/apps/10_capture/README.md`
- Modify: `architecture/apps/20_convert/README.md`
- Modify: `architecture/contracts/10_20_capture_directions/README.md`
- Create: `configs/30_genetic_training/genetic_strategy.toml`
- Create: `docs/configs/30_genetic_training.md`
- Modify: `docs/configs/README.md`

**Interfaces:**
- Consumes: the configuration and run-snapshot rules already owned by `architecture/CONFIGURATION.md`.
- Produces: a shared startup diagnostic rule, a documented required-config exception for `30`, and an empty versioned training template.

- [ ] **Step 1: Extend the root architecture reading order and layout**

  Add `30_genetic_training`, `40_model_creation`, `50_traffic_generation`,
  and `60_similarity_evaluation` after `20_convert` in the reading order.
  Add `similarity_methods/<method_name>/` beside the existing traffic and
  genetic model registries. State that a traffic model owns its parameter
  schema and model-file validation; a genetic model owns strategy rules; and a
  similarity method owns scoring behavior.

- [ ] **Step 2: Replace the successful-only run snapshot rule**

  In `architecture/CONFIGURATION.md`, state that each app creates its own run
  directory and writes `launch.toml` at startup, before application work. It
  records invocation arguments and resolved configuration. If configuration
  resolution fails, it records the arguments, selected source, and resolution
  failure. It remains available after failure or interruption and is included
  in a successful published artifact when that application publishes one.

  Change the universal configuration filename text to a default convention,
  allowing an application owner to define a required custom filename and
  location. Record `30` as the current exception: it requires an explicitly
  selected `.configs/genetic_strategy.toml`; its versioned templates are in
  `configs/30_genetic_training/`.

- [ ] **Step 3: Align existing application and conversion documents**

  In `00_preflight`, `10_capture`, and `20_convert`, replace
  successful-only `launch.toml` wording with a relative link to the updated
  shared startup diagnostic rule. In the 20-convert output snippet, add
  `launch.toml` so it matches the already authoritative conversion contract.
  In the capture-directions contract, retain `launch.toml` in the successful
  package and point its startup/retention behavior to the shared owner.

- [ ] **Step 4: Create the required training configuration scaffold**

  Create `configs/30_genetic_training/genetic_strategy.toml` containing only:

  ```toml
  # No settings are defined for 30_genetic_training yet.
  ```

  Create `docs/configs/30_genetic_training.md`. It must link to the template,
  identify `.configs/genetic_strategy.toml` as the active local file, state
  that the file combines reference input, allowed traffic-model names, one
  genetic strategy, one similarity system, and later run settings, and state
  that no concrete fields exist yet. Add the entry to `docs/configs/README.md`.

- [ ] **Step 5: Validate Task 1**

  Run:

  ```bash
  python3.12 -c "import pathlib, tomllib; tomllib.loads(pathlib.Path('configs/30_genetic_training/genetic_strategy.toml').read_text()); print('template: valid')"
  git diff --check
  rg -n -- '30_genetic_training|similarity_methods|launch\.toml|genetic_strategy\.toml' architecture configs docs/configs
  ```

  Expected: the template parses; the new application and registry are in the
  root map; startup retention is owned only by `CONFIGURATION.md`; and there
  are no whitespace errors.

- [ ] **Step 6: Commit Task 1**

  ```bash
  git add architecture/README.md architecture/CONFIGURATION.md architecture/apps/00_preflight/README.md architecture/apps/10_capture/README.md architecture/apps/20_convert/README.md architecture/contracts/10_20_capture_directions/README.md configs/30_genetic_training/genetic_strategy.toml docs/configs/30_genetic_training.md docs/configs/README.md
  git commit -m "docs(architecture): define training diagnostics"
  ```

### Task 2: Define the interchangeable registries and similarity-result contract

**Files:**
- Create: `architecture/traffic_models/README.md`
- Create: `architecture/genetic_models/README.md`
- Create: `architecture/similarity_methods/README.md`
- Create: `architecture/contracts/60_30_similarity_result/README.md`

**Interfaces:**
- Consumes: registry ownership declared in Task 1.
- Produces: named-component rules and a single file contract consumed by `30`.

- [ ] **Step 1: Create the traffic-model registry owner**

  State that each `traffic_models/<name>/README.md` owns one traffic model's
  equations, parameter schema, normal starting values, self-describing
  `NAME.toml` model-file validation, synthetic-traffic behavior, assumptions,
  and validation. State that a mixed model or model pipeline is registered as
  another traffic model.

- [ ] **Step 2: Create the genetic-model registry owner**

  State that each `genetic_models/<name>/README.md` owns one named strategy's
  population creation, selection, mutation, same-model crossover,
  model-replacement rule, stopping conditions, compatibility requirements, and
  validation. State that `30` implements the strategy and selects one name per
  run.

- [ ] **Step 3: Create the similarity-method registry owner**

  State that each `similarity_methods/<name>/README.md` owns one named system's
  accepted PCAPNG inputs, configuration, ranking interpretation, validation,
  and any combined scoring behavior. State that `60` selects one name and does
  not implement an application-specific fitness policy.

- [ ] **Step 4: Create `60_30_similarity_result`**

  Define the successful file as `similarity.toml`. It records the selected
  similarity-method identity, ranking result, reference and generated input
  lineage, validation status, and content hash. It defines no formula or
  scoring scale. Require validation, hashing, and atomic publication; a
  missing, partial, or invalid result is not a successful result. Link to the
  future 30 and 60 application owners and the shared configuration owner.

- [ ] **Step 5: Validate Task 2**

  Run:

  ```bash
  git diff --check
  rg -n -- 'parameter schema|same-model crossover|ranking interpretation|similarity\.toml' architecture/traffic_models architecture/genetic_models architecture/similarity_methods architecture/contracts/60_30_similarity_result
  ```

  Expected: every registry has one owner boundary; only the contract defines
  the similarity-result file; no formula is introduced.

- [ ] **Step 6: Commit Task 2**

  ```bash
  git add architecture/traffic_models/README.md architecture/genetic_models/README.md architecture/similarity_methods/README.md architecture/contracts/60_30_similarity_result/README.md
  git commit -m "docs(architecture): add training component registries"
  ```

### Task 3: Add the four application architecture stubs

**Files:**
- Create: `architecture/apps/30_genetic_training/README.md`
- Create: `architecture/apps/40_model_creation/README.md`
- Create: `architecture/apps/50_traffic_generation/README.md`
- Create: `architecture/apps/60_similarity_evaluation/README.md`

**Interfaces:**
- Consumes: the component registries from Task 2 and
  `60_30_similarity_result`.
- Produces: four non-overlapping stage boundaries and their conceptual command
  interfaces.

- [ ] **Step 1: Write `30_genetic_training`**

  Define it as the parent orchestrator for one reference PCAPNG. Require
  `--config-file .configs/genetic_strategy.toml` and `--output-dir DIR`.
  State that it launches `40`, `50`, and `60` as child apps in per-candidate
  folders; retains all child diagnostics; evaluates one generated PCAPNG
  against the one reference; keeps failed candidates ineligible; and publishes
  one winning model and ranking report. State the candidate and crossover/model
  replacement rules exactly from the global constraints. Link to all three
  registries, the child apps, the result contract, and the configuration owner.

- [ ] **Step 2: Write `40_model_creation`**

  Define the conceptual command
  `model_creation --model=NAME --output-dir=DIR`. State that it creates
  `DIR/NAME.toml` using the selected traffic model's normal starting values
  and writes the model identity and parameter values into that file. It does
  not mutate, cross over, select, evaluate fitness, generate traffic, or score
  similarity. Link to the traffic-model registry and `30`.

- [ ] **Step 3: Write `50_traffic_generation`**

  Define the conceptual command
  `traffic_generation --model-file=PATH --output=PATH`. State that it reads
  the model identity and parameters from one self-describing model file and
  creates exactly one synthetic PCAPNG at `--output`. The output name is not
  fixed. It does not choose a model, alter parameters, or compare traffic.
  Link to the traffic-model registry, `40`, `60`, and `30`.

- [ ] **Step 4: Write `60_similarity_evaluation`**

  Define the conceptual command
  `similarity_evaluation --reference=PATH --generated=PATH --method=NAME --output-dir=DIR`.
  State that it compares exactly those two PCAPNG files with one named
  similarity system and writes `DIR/similarity.toml` under the contract. It
  does not combine results, assign genetic fitness policy, select candidates,
  or mutate models. Link to the similarity-method registry, the result
  contract, and `30`.

- [ ] **Step 5: State common non-implementation and testing boundaries**

  In each app stub, state that no current implementation exists and future
  tests are unprivileged. In `30`, identify deterministic candidate handling
  and fake child-app tests. In `40`, `50`, and `60`, identify deterministic
  file validation/publication tests. Do not name a real recorder, math model,
  algorithm, or scoring formula.

- [ ] **Step 6: Validate Task 3**

  Run:

  ```bash
  git diff --check
  rg -n -- 'model_creation --model=NAME|traffic_generation --model-file=PATH|similarity_evaluation --reference=PATH|genetic_training --config-file' architecture/apps
  rg -n -- 'crossover|model replacement|ineligible|exactly one' architecture/apps/30_genetic_training architecture/apps/50_traffic_generation architecture/apps/60_similarity_evaluation
  ```

  Expected: all four conceptual interfaces are present; `30` owns genetics;
  `50` creates one arbitrarily named PCAPNG; and `60` performs one comparison.

- [ ] **Step 7: Commit Task 3**

  ```bash
  git add architecture/apps/30_genetic_training/README.md architecture/apps/40_model_creation/README.md architecture/apps/50_traffic_generation/README.md architecture/apps/60_similarity_evaluation/README.md
  git commit -m "docs(architecture): add training pipeline stubs"
  ```

### Task 4: Verify the completed architecture change

**Files:**
- Verify: all files changed in Tasks 1–3

**Interfaces:**
- Consumes: the shared rules, registries, result contract, and application
  stubs.
- Produces: evidence that component boundaries, configuration, and diagnostic
  behavior are consistent.

- [ ] **Step 1: Run final static verification**

  Run:

  ```bash
  git diff --check HEAD~3..HEAD
  git status --short
  rg -n -- 'successful.*launch\.toml|launch\.toml.*successful' architecture
  rg -n -- 'genetic_strategy\.toml|--output-dir DIR|similarity\.toml' architecture configs docs/configs
  ```

  Expected: no whitespace errors; `launch.toml` is described as a startup
  diagnostic, not a successful-only artifact; every special 30 configuration
  location and child interface is consistent.

- [ ] **Step 2: Report scope and verification**

  Report the new stage stubs, registries, similarity contract, training
  template, startup diagnostic behavior, validation output, and commit IDs.
  Explicitly state that no executable application, mathematical model, or
  elevated operation was added.
