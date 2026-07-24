# Trafficlab System Roadmap

## Status Vocabulary

The controlled markers and parent/child rules are defined by
[architecture governance](../README.md#roadmap-status-rules). Each entry's
current marker and local evidence record its implementation and test status;
architecture and planning alone do not advance that status.

This central roadmap owns cross-component sequencing only. Each linked
component roadmap owns its detailed tasks, implementation notes, tests,
validation, completion criteria, and evidence.

## [DONE] STAGE 1 — Contract Foundation

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Component roadmaps:**
  - [Infrastructure](../infrastructure/ROADMAP.md) — repository toolchain, CI,
    and documentation gates.
  - [Configuration library](../libs/configuration/ROADMAP.md) — deterministic
    settings resolution and startup records.
  - [Artifact I/O](../libs/artifact_io/ROADMAP.md) — validated atomic artifact
    and status publication.
  - [Lineage](../libs/lineage/ROADMAP.md) — hashing and provenance records.
  - [Observability](../libs/observability/ROADMAP.md) — bounded structured
    diagnostics.
  - [PCAPNG I/O](../libs/pcap_io/ROADMAP.md) — validated packet interchange.
  - [Resource management](../libs/resource_management/ROADMAP.md) —
    deterministic admission and accounting.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.
- **Evidence:** Immediate child Step 1.1 is `[DONE]` based on its linked
  component-roadmap evidence.

### [DONE] STEP 1.1 — Configuration, artifacts, lineage, and PCAPNG

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.
- **Evidence:** Immediate child Substep 1.1.1 is `[DONE]` based on its linked
  component-roadmap evidence.

#### [DONE] SUBSTEP 1.1.1 — Coordinate shared foundations

- **Objective:** Deliver the shared foundation represented by this stage's
  linked component roadmaps.
- **Implementation:** Sequence the linked roadmaps without copying their
  detailed implementation work into this central plan.
- **Affected files:** Files declared by the linked component roadmaps.
- **Dependencies:** Prerequisites declared by the linked component roadmaps.
- **Outputs:** The foundation deliverables owned by the linked components.
- **Tests:** Aggregate the test evidence required by the linked roadmaps.
- **Validation:** Verify every linked roadmap against its own validation and
  completion criteria.
- **Completion criteria:** Every component roadmap linked by this stage is
  `[DONE]`, and SYS-AC-003 passes.
- **Evidence:** Infrastructure, Configuration, Artifact I/O, Lineage,
  Observability, PCAPNG I/O, and Resource management are `[DONE]`; Resource
  Management records immutable decision snapshots for lineage and diagnosis.

## [PLAN] STAGE 2 — Capture and Reference Preparation

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Component roadmaps:**
  - [Preflight](../apps/00_preflight/ROADMAP.md) — read-only capture readiness.
  - [Capture](../apps/10_capture/ROADMAP.md) — safe target and recorder
    lifecycle.
  - [Convert](../apps/20_convert/ROADMAP.md) — direction classification and
    package publication.
  - [Inspection export](../apps/25_inspection_export/ROADMAP.md) — versioned
    streaming Parquet and JSONL export.
  - [Capture request contract](../contracts/00_capture_request/ROADMAP.md) —
    request schema, identity, and consumer integration.
  - [Capture readiness contract](../contracts/00_10_capture_readiness/ROADMAP.md)
    — request-bound readiness serialization and validation.
  - [Capture directions contract](../contracts/10_20_capture_directions/ROADMAP.md)
    — directional package validation.
  - [Inspection dataset contract](../contracts/25_inspection_dataset/ROADMAP.md)
    — equivalent Parquet and JSONL package validation.
  - [Backup script](../scripts/backup_system_configuration/ROADMAP.md) — scoped
    read-only system-configuration backup.
  - [Rollback script](../scripts/rollback_capture_workspace/ROADMAP.md) —
    manifest-scoped reverse planning and execution.
  - [Setup script](../scripts/setup_capture_workspace/ROADMAP.md) —
    manifest-first workspace preparation.
  - [Verify script](../scripts/verify_capture_workspace/ROADMAP.md) — read-only
    workspace verification.
  - [Workspace orchestration script](../scripts/workspace_orchestration/ROADMAP.md)
    — manual action sequencing.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 2.1 — Deliver preflight, capture, conversion, and inspection

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 2.1.1 — Coordinate capture and reference preparation

- **Objective:** Deliver the capture and reference flow represented by this
  stage's linked component roadmaps.
- **Implementation:** Sequence the linked roadmaps after Stage 1 without
  copying their detailed implementation work into this central plan.
- **Affected files:** Files declared by the linked component roadmaps.
- **Dependencies:** Stage 1 and the prerequisites declared by the linked
  component roadmaps.
- **Outputs:** The capture and reference deliverables owned by the linked
  components.
- **Tests:** Aggregate the test evidence required by the linked roadmaps.
- **Validation:** Verify every linked roadmap against its own validation and
  completion criteria.
- **Completion criteria:** Every component roadmap linked by this stage is
  `[DONE]`, and SYS-AC-001 and SYS-AC-002 pass for the capture-to-reference
  flow.

## [PLAN] STAGE 3 — Modelling, Generation, and Similarity

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Component roadmaps:**
  - [Model creation](../apps/40_model_creation/ROADMAP.md) — registered model
    builder dispatch.
  - [Traffic generation](../apps/50_traffic_generation/ROADMAP.md) — event
    rendering and model-output publication.
  - [Similarity evaluation](../apps/60_similarity_evaluation/ROADMAP.md) —
    method execution and result publication.
  - [Similarity result contract](../contracts/60_30_similarity_result/ROADMAP.md)
    — common and method-specific result validation.
  - [FARIMA](../traffic_models/farima/ROADMAP.md) — FARIMA model research and
    implementation planning.
  - [MAP/BMAP](../traffic_models/map_bmap/ROADMAP.md) — MAP/BMAP model research
    and implementation planning.
  - [Marked point-process diffusion](../traffic_models/marked_point_process_diffusion/ROADMAP.md)
    — diffusion representation, fitting, serialization, and generation.
  - [Markov renewal](../traffic_models/markov_renewal/ROADMAP.md) — state
    preparation and seeded generation.
  - [MMPP](../traffic_models/mmpp/ROADMAP.md) — MMPP model research and
    implementation planning.
  - [Neural Hawkes](../traffic_models/neural_hawkes/ROADMAP.md) — causal
    probability, fitting, and generation.
  - [ON/OFF Pareto](../traffic_models/on_off_pareto/ROADMAP.md) — ON/OFF Pareto
    model research and implementation planning.
  - [Packet train](../traffic_models/packet_train/ROADMAP.md) — packet-train
    model research and implementation planning.
  - [Poisson empirical](../traffic_models/poisson_empirical/ROADMAP.md) —
    reference preparation and seeded generation.
  - [Poisson uniform](../traffic_models/poisson_uniform/ROADMAP.md) — validated
    deterministic baseline generation.
  - [State space](../traffic_models/state_space/ROADMAP.md) — state-space model
    research and implementation planning.
  - [Autocorrelation](../similarity_methods/autocorrelation/ROADMAP.md) — lag
    correlation comparison.
  - [Rate cross-correlation](../similarity_methods/cross_correlation_rate/ROADMAP.md)
    — shift-tolerant rate comparison.
  - [Multi-scale rate DTW](../similarity_methods/dtw_multiscale_rate/ROADMAP.md)
    — bounded multi-scale rate alignment.
  - [Frame-size KS](../similarity_methods/frame_size_ks/ROADMAP.md) — canonical
    frame-length distribution comparison.
  - [Hurst parameter](../similarity_methods/hurst_parameter/ROADMAP.md) — Hurst
    estimator and score research.
  - [IAT KS](../similarity_methods/iat_ks/ROADMAP.md) — canonical inter-arrival
    distribution comparison.
  - [Joint Sinkhorn/Wasserstein](../similarity_methods/joint_sinkhorn_wasserstein/ROADMAP.md)
    — joint transport comparison and failure integration.
  - [Weighted L2 KS](../similarity_methods/l2_ks_weighted/ROADMAP.md) — strict
    composition of validated KS results.
  - [Multi-scale rate](../similarity_methods/multiscale_rate/ROADMAP.md) —
    multi-scale count-vector comparison.
  - [Lagged mutual information](../similarity_methods/mutual_information_lag/ROADMAP.md)
    — lagged-dependence estimator research.
  - [Neighbour transition](../similarity_methods/neighbor_transition/ROADMAP.md)
    — partition and transition comparison.
  - [Sequence-kernel MMD](../similarity_methods/sequence_kernel_mmd/ROADMAP.md)
    — path construction, signature kernel, and bounded MMD evaluation.
  - [Spectral density](../similarity_methods/spectral_density/ROADMAP.md) —
    spectral comparison research.
  - [Wavelet scaling](../similarity_methods/wavelet_scaling/ROADMAP.md) —
    wavelet-scaling comparison research.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 3.1 — Deliver replaceable mathematical modules

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 3.1.1 — Coordinate modelling and evaluation

- **Objective:** Deliver the modelling, generation, and similarity flow
  represented by this stage's linked component roadmaps.
- **Implementation:** Sequence the linked roadmaps after Stage 2 without
  copying their detailed implementation work into this central plan.
- **Affected files:** Files declared by the linked component roadmaps.
- **Dependencies:** Stages 1–2 and the prerequisites declared by the linked
  component roadmaps.
- **Outputs:** The modelling and evaluation deliverables owned by the linked
  components.
- **Tests:** Aggregate the test evidence required by the linked roadmaps.
- **Validation:** Verify every linked roadmap against its own validation and
  completion criteria.
- **Completion criteria:** Every component roadmap linked by this stage is
  `[DONE]`, and SYS-AC-001 and SYS-AC-002 pass for independent and chained
  execution.

## [PLAN] STAGE 4 — Genetic Training and Orchestration

- **Task:** Complete the stage named by this heading through its ordered steps.
- **Deliverable:** A usable, testable increment comprising the outputs declared by those steps.
- **Component roadmaps:**
  - [Genetic training](../apps/30_genetic_training/ROADMAP.md) — candidate
    orchestration and strategy execution.
  - [Trafficlab orchestrator](../apps/99_trafficlab/ROADMAP.md) — child
    execution and experiment-pipeline sequencing.
  - [Basic generational strategy](../genetic_models/basic_generational/ROADMAP.md)
    — deterministic baseline genetic evolution.
  - [Island NSGA-II novelty strategy](../genetic_models/island_nsga2_novelty/ROADMAP.md)
    — multi-island ranking, novelty, and migration.
- **Applicable test types:** The test types declared by this stage's substeps.
- **Completion criteria:** Every step and substep in this stage meets its completion criteria.

### [PLAN] STEP 4.1 — Automate bounded experiments

- **Task:** Perform the implementation work named by this heading.
- **Deliverable:** All outputs declared by this step's substeps.
- **Applicable test types:** The test types declared by this step's substeps.
- **Completion criteria:** Every substep in this step meets its completion criteria.

#### [PLAN] SUBSTEP 4.1.1 — Coordinate training and orchestration

- **Objective:** Deliver bounded training and top-level orchestration through
  this stage's linked component roadmaps.
- **Implementation:** Sequence the linked roadmaps after Stage 3 without
  copying their detailed implementation work into this central plan.
- **Affected files:** Files declared by the linked component roadmaps.
- **Dependencies:** Stages 1–3 and the prerequisites declared by the linked
  component roadmaps.
- **Outputs:** The training and orchestration deliverables owned by the linked
  components.
- **Tests:** Aggregate the test evidence required by the linked roadmaps.
- **Validation:** Verify every linked roadmap against its own validation and
  completion criteria.
- **Completion criteria:** Every component roadmap linked by this stage is
  `[DONE]`, and SYS-AC-001 through SYS-AC-003 pass for the bounded experiment
  workflow.
