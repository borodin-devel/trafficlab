# Trafficlab Orchestrator Software Requirements Specification

## Scope

The orchestrator serves users running individual applications or experiment
suffixes. It does not implement child algorithms or infer latest inputs.

## Requirements

- **ORC-FR-001:** `run` shall launch exactly one selected documented application.
- **ORC-FR-002:** `experiment` shall run the documented stage suffix from preflight, capture, or genetic training.
- **ORC-FR-003:** It shall create collision-resistant lexically chronological UTC run directories.
- **ORC-FR-004:** It shall stop after first failed stage and retain all diagnostics.
- **ORC-FR-005:** Every experiment containing capture shall pass one explicit capture request; a capture-start suffix shall also require its matching readiness decision.
- **ORC-FR-006:** Every conversion-to-training handoff shall pass the explicit conversion status and exactly one requested `target`, `uplink`, or `downlink` member without a default.
- **ORC-IF-001:** It shall pass absolute explicit paths and child arguments without shell interpretation.
- **ORC-IF-002:** It shall validate each producer contract before launching a consumer.
- **ORC-IF-003:** It shall create empty same-user mode-`0700` non-symlink attempt directories, leave artifact destinations absent, pass absolute `--attempt-dir` paths, and use each application's documented output argument.
- **ORC-IF-004:** It shall require valid detached successful status before treating a child destination as an artifact.
- **ORC-CFG-001:** It shall accept only explicit `--config-file`; CLI values override under shared rules.
- **ORC-NFR-001:** Stage order, path resolution, status, and lineage shall be deterministic except recorded collision suffix and clock.
- **ORC-NFR-002:** Child scheduling shall obey resource admission.
- **ORC-ERR-001:** It shall never delete failed or interrupted run artifacts automatically.
- **ORC-ERR-002:** It shall reject missing, stale, mismatched, inferred, or invalid capture request/readiness inputs before capture launch.
- **ORC-TST-001:** Tests shall use fake child executables and `test_run/`.

## Acceptance Criteria

- **ORC-AC-001:** Every supported start stage launches exact expected child order and absolute paths.
- **ORC-AC-002:** Injected child/contract failure prevents later launches and records exact failed stage.
- **ORC-AC-003:** Path, current-directory, collision, and argument-injection fixtures remain contained and deterministic.
- **ORC-AC-004:** Every child receives the exact application-specific attempt/output vector; pre-created, orphaned, status-mismatched, and wrong-adapter outputs fail.
- **ORC-AC-005:** Capture-start fixtures launch only with an explicit fresh matching request/readiness pair and never invoke preflight implicitly.
- **ORC-AC-006:** Missing, unknown, repeated, or inferred directional member selection prevents genetic-training launch.

## Traceability

[SAD](SAD.md) · [Configuration](CONFIGS.md) · [Roadmap](ROADMAP.md) ·
[Workflows](../../project/WORKFLOWS.md) ·
[Capture request](../../contracts/00_capture_request/SRS.md)
