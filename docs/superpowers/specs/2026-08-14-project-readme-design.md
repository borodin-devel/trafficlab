# Trafficlab Project README Design

**Date:** 2026-08-14

## Goal

Add a detailed root `README.md` that lets a new researcher understand Trafficlab,
install its prerequisites, run the complete workflow, invoke individual stages,
find generated artifacts, and navigate to the authoritative technical documents.

## Documentation authority

The README is an onboarding and navigation document. It summarizes behavior but
does not replace or duplicate the specifications under `architecture/`.
Technical details, invariants, algorithms, failure semantics, and test policy
remain authoritative in the architecture corpus.

No parallel `docs/stages`, `docs/traffic_models`, `docs/similarity_methods`, or
`docs/genetic_models` hierarchy will be created. All detailed links point
directly to `architecture/` files or exact headings within them.

## README structure

The root README will contain:

1. Project purpose and research scope.
2. Main capabilities and the end-to-end data flow.
3. Runtime, Python, uv, Docker, Compose, Linux/WSL2, and resource requirements.
4. Installation and environment verification.
5. Configuration guidance using `examples/configs/minimal.toml`.
6. A quick-start path for config-only preflight and the complete `run` command.
7. A stages section for `preflight`, `capture`, `fit`, `generate`, `compare`,
   and `run`, each linked to its exact `architecture/SYSTEM.md` heading.
8. Output artifact and resumability guidance.
9. Direct links to every implemented traffic model, similarity method, and
   genetic model document.
10. Testing, deterministic fixtures, Validation Study evidence, limitations, and the
    remaining architecture index.

## Command policy

Examples use `uv sync --locked --all-groups`, `uv run --locked trafficlab`, and
the checked configuration files. Test commands defer to
`scripts/run_bounded.sh` and the exact limits documented in
`architecture/DEVELOPMENT.md`; the README will not introduce an alternate test
runner or unbounded pytest invocation.

Docker commands do not use `sudo`. The README explains that the current user
must have access to Docker Engine and Compose v2. Internet tests remain opt-in.

## Link policy

Every relative README link must resolve to a tracked file. Links to headings use
GitHub-compatible anchors derived from the current architecture headings.
The documentation verification will parse README links, reject missing relative
targets, and check architecture anchors used by stage links.

## Scope discipline

This change adds documentation and its focused validation only. It does not
alter production code, dependencies, configuration schemas, architecture,
fixtures, study results, or Roadmap completion claims.

## Acceptance

- A new reader can install and run Trafficlab from the root README.
- Every CLI stage, model, method, and major architecture topic is directly
  discoverable.
- Commands match the implemented CLI and locked development workflow.
- All README relative links and stage anchors validate.
- Ruff, strict Pyright, focused documentation tests, and the working-tree checks
  remain clean.
