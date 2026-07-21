# Complete Architecture Corpus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure and formalize the entire `architecture/` corpus required by `TASK.md` while preserving supported decisions and marking unresolved stubs.

**Architecture:** Component directories contain concise navigation in `README.md`, architecture in `SAD.md`, testable requirements in `SRS.md`, and incremental implementation/verification in `ROADMAP.md`. Detailed algorithms remain in ordered supporting documents. System-wide infrastructure and shared-library boundaries receive dedicated owners.

**Tech Stack:** Markdown, relative links, Python 3.12 target, uv, pytest, pytest-cov, pyright, ruff, PCAPNG, TOML, JSON, Apache Arrow, Apache Parquet.

## Global Constraints

- Existing supported behavior remains authoritative unless TASK.md requires restructuring.
- Explicit stubs remain unresolved and unselectable; no missing mathematics or defaults are invented.
- Requirement IDs are unique, stable, component-prefixed, and testable.
- Every roadmap prefixes `STAGE`, `STEP`, and `SUBSTEP` headings with one
  approved status marker and names implementation, tests, validation, and
  completion criteria.
- Every successful file contract validates, hashes, retains lineage, and publishes atomically.
- All links are relative and must resolve.

---

### Task 1: System Documentation, Infrastructure, and Libraries

**Files:**

- Modify: `architecture/README.md`
- Create: `architecture/project/SAD.md`, `architecture/project/SRS.md`, `architecture/project/ROADMAP.md`, `architecture/project/CONFIGS.md`
- Create: `architecture/infrastructure/README.md`, `architecture/infrastructure/SAD.md`, `architecture/infrastructure/SRS.md`, `architecture/infrastructure/ROADMAP.md`, `architecture/infrastructure/CONFIGS.md`
- Create component documents below `architecture/libs/{artifact_io,pcap_io,configuration,lineage,resource_management,observability}/`

**Interfaces:**

- Consumes: `architecture/DEVELOPMENT.md`, `architecture/CONFIGURATION.md`, existing project documents.
- Produces: complete system navigation, SAD/SRS, infrastructure roadmap, and six shared-library boundaries.

- [x] **Step 1: Write root and project document hierarchy**
- [x] **Step 2: Define infrastructure requirements and roadmap**
- [x] **Step 3: Define each derived library without inventing concrete Python APIs**
- [x] **Step 4: Verify required files, unique IDs, roadmap hierarchy, and links**

### Task 2: Applications

**Files:**

- Modify each directory below `architecture/apps/`:
  `00_preflight`, `10_capture`, `20_convert`, `25_inspection_export`,
  `30_genetic_training`, `40_model_creation`, `50_traffic_generation`,
  `60_similarity_evaluation`, `99_trafficlab`.
- Create in each directory: `SAD.md`, `SRS.md`, `CONFIGS.md`, `ROADMAP.md`.

**Interfaces:**

- Consumes: current application owner, shared configuration, contracts, models, and project qualities.
- Produces: concise application navigation plus formal architecture, requirements, configuration, and incremental delivery.

- [x] **Step 1: Preserve current decisions in application SADs**
- [x] **Step 2: Add unique functional, interface, quality, and acceptance requirements**
- [x] **Step 3: Document known configuration and explicitly unresolved settings**
- [x] **Step 4: Add implementation and verification roadmaps**
- [x] **Step 5: Verify producer/consumer names and links**

### Task 3: Contracts and Scripts

**Files:**

- Modify contract directories `10_20_capture_directions` and `60_30_similarity_result`.
- Create contract `SAD.md`, `SRS.md`, and `ROADMAP.md` files.
- Replace five standalone script owner files with component directories:
  `backup_system_configuration`, `setup_capture_workspace`,
  `verify_capture_workspace`, `rollback_capture_workspace`, and
  `workspace_orchestration`.
- Create `README.md`, `SAD.md`, `SRS.md`, and `ROADMAP.md` in each script directory.

**Interfaces:**

- Consumes: current package layouts and manual privilege boundaries.
- Produces: formal contract validation and script operational requirements.

- [x] **Step 1: Formalize both contracts**
- [x] **Step 2: Move script behavior into component directories**
- [x] **Step 3: Add script requirements and safe verification roadmaps**
- [x] **Step 4: Update all script links and verify privilege wording**

### Task 4: Genetic Strategies

**Files:**

- Modify: `architecture/genetic_models/README.md`.
- Modify component directories `basic_generational` and `island_nsga2_novelty`.
- Create component `SAD.md`, `SRS.md`, `CONFIGS.md`, `ROADMAP.md`, and ordered algorithm documents.

**Interfaces:**

- Consumes: current strategy algorithms, training application, traffic-model registry, similarity registry.
- Produces: staged genetic algorithm descriptions and verifiable mathematical requirements.

- [x] **Step 1: Split candidate representation, evaluation, selection, operators, stopping, and validation**
- [x] **Step 2: Define NSGA-II, novelty, archive, island, and migration stages**
- [x] **Step 3: Add deterministic and numerical requirements**
- [x] **Step 4: Add implementation/testing roadmaps and validate formulas**

### Task 5: Traffic Models

**Files:**

- Modify: `architecture/traffic_models/README.md`, shared Poisson and neural rules.
- Formalize directories `poisson_uniform`, `poisson_empirical`, `markov_renewal`,
  `neural_hawkes`, `marked_point_process_diffusion`, `farima`, `map_bmap`,
  `mmpp`, `on_off_pareto`, `packet_train`, and `state_space`.
- Create `SAD.md`, `SRS.md`, `CONFIGS.md`, and `ROADMAP.md` per model; create ordered design files for complex models.

**Interfaces:**

- Consumes: current equations, model schemas, fitting and generation rules.
- Produces: navigable model documentation with explicit unresolved status for six stubs.

- [x] **Step 1: Formalize Poisson models and shared rules**
- [x] **Step 2: Split Markov renewal documentation by preparation, state, generation, and validation**
- [x] **Step 3: Split neural models by representation, probability law, fitting, serialization, and generation**
- [x] **Step 4: Formalize stub boundaries without inventing algorithms**
- [x] **Step 5: Verify variables, domains, deterministic rules, and tests**

### Task 6: Similarity Methods

**Files:**

- Modify: `architecture/similarity_methods/README.md`, shared KS and temporal rules.
- Formalize directories `iat_ks`, `frame_size_ks`, `l2_ks_weighted`,
  `joint_sinkhorn_wasserstein`, `multiscale_rate`, `neighbor_transition`,
  `autocorrelation`, `sequence_kernel_mmd`, `cross_correlation_rate`,
  `dtw_multiscale_rate`, `hurst_parameter`, `mutual_information_lag`,
  `spectral_density`, and `wavelet_scaling`.
- Create `SAD.md`, `SRS.md`, `CONFIGS.md`, and `ROADMAP.md` per method; create ordered mathematical files for complex methods.

**Interfaces:**

- Consumes: canonical Layer 2 extraction, current formulas, similarity result contract.
- Produces: reproducible scoring specifications and explicit unresolved status for six stubs.

- [x] **Step 1: Formalize KS family methods**
- [x] **Step 2: Formalize temporal methods and split complex mathematics**
- [x] **Step 3: Formalize stub boundaries without selecting formulas**
- [x] **Step 4: Verify score ranges, numerical failure rules, and correctness tests**

### Task 7: Corpus Validation and Cleanup

**Files:**

- Create: `architecture/VALIDATION.md`.
- Modify: root and registry README files as needed.
- Delete obsolete `.gitkeep` and superseded standalone script files.

**Interfaces:**

- Consumes: complete rewritten corpus.
- Produces: navigable, internally consistent, clean architecture tree.

- [x] **Step 1: Check every component directory for README/SAD/SRS**
- [x] **Step 2: Check application/library roadmaps and independent component roadmaps**
- [x] **Step 3: Check requirement-ID uniqueness and roadmap hierarchy**
- [x] **Step 4: Check every local Markdown link**
- [x] **Step 5: Search for undocumented component references and unresolved contradictions**
- [x] **Step 6: Remove obsolete files, run `git diff --check`, and review final diff**

## Self-Review

- Coverage: seven tasks cover every component class and cleanup requirement in TASK.md.
- Scope: documentation only; no runtime implementation or unsupported contract detail.
- Verification: each task ends with structural, traceability, or link validation.
