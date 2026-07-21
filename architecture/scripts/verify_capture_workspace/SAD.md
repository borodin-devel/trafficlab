# Verify Capture Workspace Software Architecture Document

## Role

`scripts/verify_capture_workspace.sh` reports whether a named workspace is
ready for normal capture.

## Authority

Verification is read-only. It does not repair, start, stop, install, configure,
or remove anything.

## Checks and Result

Verification compares workspace identity, manifest consistency, ownership,
invoker health, known state, and detectable resource drift. It reports whether
the workspace is ready, busy, unhealthy, missing, or indeterminate, with
findings that let the operator choose setup or rollback.

## Tests

Automated tests use recorded manifests and fake observations to cover ready,
busy, unhealthy, missing, and drift results without inspecting real system
resources.

## Architecture Qualities and Limits

Comparison/classification is a pure deterministic core; read-only Linux probes
form the shell. Work is bounded by one manifest's resources. Logs contain
findings and probe failures. Deployment is manual Bash and requires only read
authority. Risks are incomplete observability and time-of-check/time-of-use;
indeterminate evidence never becomes ready, and capture performs its own preflight.
