# Development Workflow

Trafficlab uses one reproducible Python toolchain and keeps its commands
explicit. This document owns development mechanics; [Testing](TESTING.md) owns
the evidence required from each test scope.

## Supported environment

Trafficlab's package compatibility remains CPython 3.12.x through
`pyproject.toml`'s `>=3.12,<3.13` requirement. Development and deterministic
fixture generation use the exact CPython 3.12.3 runtime pinned by
`.python-version`, because genetic checkpoints intentionally store and compare
the exact Python patch version. Install and synchronize a clean checkout with:

```bash
uv python install 3.12.3
uv sync --locked --all-groups
```

Run project commands from a checkout where `uv run --locked python --version`
reports `Python 3.12.3`; another supported 3.12 patch can install the package,
but cannot strictly resume or regenerate the checked CPython 3.12.3 genetic
checkpoint.

`uv` is the only supported interface for Python installation, dependency
resolution, environments, locking, and project commands. Commit `.python-version`,
`pyproject.toml`, and `uv.lock`; never hand-edit `uv.lock`.

## Reproducibility review and accepted evidence

Reproducibility review includes the capture Dockerfile's Debian base digest,
dated snapshot archive state, and exact direct-package inputs, as recorded in
`docker/capture/image-lock.json`. The record is checked before use; it is not
silently updated by a newly resolved image.

An accepted-study environment record includes the source commit and tree,
`uv.lock`, Python version, target and capture identities, capture-tool version,
Docker Engine, Compose, kernel, and host architecture. Compatibility uses only
the following categories and no other host field:

```text
must match for deterministic offline regeneration:
  source commit/tree, uv.lock, CPython patch, scientific schema, artifact bytes

must match for a fresh compatible capture environment:
  host architecture, target content ID, capture image-lock expected/resolved ID,
  capture-tool version, container argv/environment/workdir/mount target+mode,
  mounted-input content hashes

recorded external variation permitted after successful feature preflight:
  Docker Engine/Compose versions (including supported Compose v2/v5 plugins), kernel release, checkout/run/mount-source
  absolute paths
```

Capture reuse remains stricter: it requires the exact realized snapshot and
capture identities. Permitted fresh-environment variation is never reusable
capture equivalence.

Ordinary `runs/` and scratch study work remain ignored. The narrow checked
exception is `examples/validation_study/evidence/<study-id>/`, which holds an
accepted evidence bundle. Do not commit an accepted bundle until its offline
audit succeeds.

## Dependencies

Use uv to change dependencies:

```bash
uv add PACKAGE
uv add --dev PACKAGE
uv remove PACKAGE
uv lock --upgrade-package PACKAGE
```

The lock records exact resolved versions. Trafficlab does not maintain
`requirements.txt`, Poetry, Pipenv, tox, Hatch, a custom task runner, or npm
workflows alongside uv.

## Formatting, linting, and types

Ruff owns formatting and linting. Its maximum line length is 120 characters.
Pyright checks future `src/trafficlab` and `tests` code in strict mode.

```bash
uv run --locked ruff format .
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
```

Pyright is the only Node.js-based development exception. Its private Node.js
runtime is an implementation detail of that developer tool. Trafficlab does not
use `package.json`, npm scripts, `node_modules`, or Node.js application, runtime,
or build dependencies. Prefer Python, Rust-backed, or native packages when an
equivalent choice exists.

## Fast and detailed tests

### Task 15 normative full-verification gate

For final research-fitness verification, use the active Task 15 plan's bounded
commands. They are normative for the full non-Docker gate; the broader
focused-loop examples below are historical development bounds and are not a
substitute for this gate.

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 -m "not docker and not internet"
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 -m "not docker and not internet" \
    --cov=trafficlab --cov-branch --cov-report=term-missing --cov-fail-under=90
```

### Historical focused-loop examples

Every pytest process tree runs with a hard memory, swap, and wall-clock bound.
On Linux and WSL2 with systemd, `scripts/run_bounded.sh` creates a unique
`trafficlab-test-guard-*.scope` so the limits include pytest workers and every
descendant, even when the launching terminal or agent is interrupted.
`MemoryHigh` starts reclaim before the hard `MemoryMax` limit; `OOMPolicy=kill`
makes an OOM kill terminate the complete scope. GNU `timeout` attempts graceful
termination and then sends `SIGKILL` after the configured grace period. The
guard finally kills the exact scope, polls until it is inactive, and returns
status `125` if setup or that final containment verification fails. Otherwise it
preserves the guarded command's status, including timeout status `124`.

Run the fast unit loop with four workers, a 6 GiB throttle, an 8 GiB hard limit,
at most 1 GiB of swap, and a ten-minute deadline:

```bash
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not integration and not docker and not internet"
```

Run the deterministic unit and in-process integration suite once with branch
coverage and missing-line output. It uses the same memory limits and a
twenty-minute deadline:

```bash
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -n 4 --dist worksteal --cov=trafficlab \
  --cov-branch --cov-report=term-missing \
  -m "not docker and not internet"
```

Docker and public-Internet tests run serially because they own external
resources and need readable lifecycle failures. The scope bounds local pytest
and CLI descendants; Docker's project-scoped lifecycle and stage deadlines
continue to bound containers:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m docker
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m internet --internet-url URL
```

External tests are opt-in. Ordinary collection deselects `docker` and
`internet` tests unless their marker or test directory is explicitly selected.
An explicit Docker or Internet selection requires `-n 0`; unavailable Docker
Engine, a supported Docker Compose plugin, or image-build capability is a failing session setup with an
actionable message, never a skip. The Docker session fixture builds the capture,
controlled endpoint, direct client, and shell-free workload images from the
checked-in Dockerfiles. Each test uses unique Compose projects and verifies by
project label that containers, networks, volumes, and orphans are gone; a
failure prints every remaining resource name before bounded best-effort removal.

`--internet-url URL` is required for the Internet marker and accepts only an
explicit credential-free HTTPS URL with a hostname. The deterministic Docker
suite uses only its controlled endpoint and never reads this option. A Docker or
Internet command is valid external evidence only when the selected test actually
runs on a capable host; collection success or the expected actionable setup
failure on a host without Docker is not capture evidence.

Pinpoint one failure, or rerun the last failures, without parallel-output noise:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 tests/path/test_module.py::test_name
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 --lf
```

Broad deterministic commands use exactly four workers rather than deriving a
worker count from a large host CPU count. A selected test remains serial and
verbose. A command that is cancelled, times out, or exceeds its memory limit
must leave no pytest worker or descendant running; otherwise the verification
attempt failed and must be cleaned up before another test command starts. Run
the controlled proof itself serially through the ordinary guard:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 tests/integration/test_process_guard.py
```

The proof creates separate-session, `SIGTERM`-ignoring descendants and checks
both a 300 ms wall timeout and a nested 63 MiB/64 MiB, zero-swap memory scope.
Every controlled PID must disappear and each named scope must become inactive.

CI and supported environments without a systemd user manager must apply
equivalent limits at the job, container, or native process-group boundary and
provide their own corresponding proof that wall and hard-memory termination
leave no descendant. A shell `ulimit` or `prlimit --as` is not equivalent
because it constrains virtual address space rather than the process tree's
resident memory. Running pytest with only `timeout` is also insufficient because
it does not provide a memory ceiling. `pyproject.toml` registers `integration`,
`docker`, and `internet`; Docker and Internet tests may also carry the
`integration` marker.

## Coverage after a unit-test failure

Trafficlab has no project-wide 100% coverage target. If a unit test exposes a
defect in a function or method, the fix is complete only when focused behavioral
regression tests cover 100% of that function's executable lines and branches.
Run the selected tests with branch coverage and inspect `term-missing` for the
function's source range. Do not build a custom per-function coverage framework.

## Git workspace preference

Use an isolated `.worktrees/<branch-name>` Git worktree for substantial feature
or multi-file implementation work. Before creating it, verify that `.worktrees/`
is ignored. Tiny documentation corrections may be made directly in the main
checkout. This preference isolates active experiments without forcing a new
workspace for every edit.

The root `.gitignore` owns generated-file policy. It ignores local worktrees,
the uv environment, Python/tool caches, build output, local environment files,
logs, and generated `runs/`. It keeps the lock, configuration, Docker Compose
files, source-controlled fixtures, and architecture documents visible to Git.

## Continuous integration

The ordinary job runs, in order:

1. `uv sync --locked --all-groups`;
2. Ruff format checking and linting;
3. strict Pyright checking;
4. one four-worker, coverage-enabled deterministic test pass under the
   documented process-tree memory, swap, and wall-clock limits.

A Docker-capable job runs Docker integration tests serially. The Internet smoke
test is manual or scheduled and does not gate ordinary changes. Failures retain
verbose pytest context and missing-line information.

## References

- [uv Python versions](https://docs.astral.sh/uv/concepts/python-versions/)
- [uv project environments and locking](https://docs.astral.sh/uv/concepts/projects/sync/)
- [pytest invocation and node IDs](https://docs.pytest.org/en/stable/how-to/usage.html)
- [pytest-xdist distribution modes](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)
- [pytest-cov](https://pytest-cov.readthedocs.io/en/stable/readme.html)
- [systemd resource control](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)
- [systemd transient units](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html)
- [GNU `timeout`](https://www.gnu.org/software/coreutils/manual/html_node/timeout-invocation.html)
- [Ruff configuration](https://docs.astral.sh/ruff/configuration/)
- [Pyright installation](https://github.com/microsoft/pyright/blob/main/docs/installation.md)
