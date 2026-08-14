# Trafficlab Development Tooling Design

**Date:** 2026-08-11

## Purpose

This amendment defines a small, reproducible Python development environment for
the Trafficlab research prototype. It favors one configuration file, one package
manager, fast feedback, and focused diagnostics. It does not introduce a task
runner, a second dependency format, or a JavaScript application toolchain.

## Repository Files

The implementation adds one architecture document and four repository-level
files:

```text
.gitignore
.python-version
pyproject.toml
uv.lock
architecture/DEVELOPMENT.md
```

`architecture/README.md` links to the new development document.
`architecture/TESTING.md` adopts its commands and regression-coverage rule.
Phase 1 of `architecture/ROADMAP.md` makes the toolchain part of the project
skeleton.

One root `.gitignore` owns repository ignore policy. Nested ignore files are not
needed for this small repository.

## Python and Dependency Policy

Trafficlab supports CPython 3.12 only, while allowing the latest available patch
release:

```text
.python-version: 3.12
pyproject.toml: requires-python = ">=3.12,<3.13"
```

`uv` is the only supported interface for installing Python, creating the local
environment, resolving dependencies, locking, and running project commands.
The standard bootstrap is:

```bash
uv python install 3.12
uv sync --locked --all-groups
```

Runtime dependencies and the development dependency group live in
`pyproject.toml`. Developers change them with `uv add`, `uv add --dev`, and
`uv remove`, not by directly editing lock data. `uv.lock` is committed and must
not be ignored. Routine commands use `uv run --locked` so an out-of-date lock is
reported rather than silently repaired.

Trafficlab does not add `requirements.txt`, Poetry, Pipenv, tox, Hatch, Make, or
a custom command wrapper unless measured pain later justifies one.

## Development Tools

The initial development group contains only:

- `pytest` for tests;
- `pytest-cov` for coverage and missing-line reports;
- `pytest-xdist` for parallel local and CI execution;
- `ruff` for formatting and linting;
- `pyright` for static type checking.

All tool configuration belongs in `pyproject.toml`. Ruff uses a maximum line
length of 120 characters. Pyright uses strict mode for the `trafficlab` package
and tests, with narrow per-line exceptions only when a library boundary cannot
be expressed accurately.

Pyright is the sole permitted Node.js-based development exception. It remains a
development dependency and does not make Node.js part of the application,
runtime image, capture image, experiment format, or command workflow. Trafficlab
does not add `package.json`, npm scripts, `node_modules`, or runtime/build
packages that require Node.js. Prefer Python, Rust-backed, or native packages
when equivalent choices exist.

## Commands

Formatting and static checks are explicit and copyable:

```bash
uv run --locked ruff format .
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
```

The normal fast test loop uses all available workers and work stealing:

```bash
uv run --locked pytest -q -n auto --dist worksteal \
  -m "not integration and not docker and not internet"
```

The coverage gate runs the deterministic unit and in-process integration suite
once, rather than first running tests and then repeating them for coverage:

```bash
uv run --locked pytest -n auto --dist worksteal --cov=trafficlab \
  --cov-branch --cov-report=term-missing \
  -m "not docker and not internet"
```

Docker capture tests and public-Internet smoke tests are serial because they
manage external resources and need readable lifecycle failures:

```bash
uv run --locked pytest -vv -n 0 -m docker
uv run --locked pytest -vv -n 0 -m internet --internet-url URL
```

A developer pinpoints one failure without parallel-output noise by selecting a
pytest node ID:

```bash
uv run --locked pytest -vv -x -n 0 tests/path/test_module.py::test_name
uv run --locked pytest -vv -x -n 0 --lf
```

Parallel execution is requested by the broad-suite commands instead of being
hidden in global pytest defaults. A selected test therefore stays serial and
detailed unless the developer explicitly requests workers.

Markers are registered in `pyproject.toml`:

- `integration`: joins multiple Trafficlab modules without external services;
- `docker`: requires Docker Engine and Compose;
- `internet`: uses a configurable public endpoint.

Docker and Internet tests may also be integration tests, but their external
resource marker controls whether they run.

## Focused Regression Coverage

There is no project-wide 100% coverage target. Aggregate percentage alone is a
poor proxy for useful research-prototype tests.

When a unit test exposes a defect in a particular function or method, that fix
is complete only after focused regression tests exercise 100% of the affected
function's executable lines and branches. The developer runs the selected tests
with branch coverage and uses the missing-line report to verify the function's
source range. Tests must assert behavior and the original failure, not merely
execute lines.

This is a review and fix-completion rule, not a custom coverage plugin. Building
per-function coverage infrastructure would cost more than it helps this
one-person prototype.

## CI Shape

The ordinary CI job performs, in order:

1. `uv sync --locked --all-groups`;
2. Ruff format check and lint;
3. Pyright strict checking;
4. one parallel, coverage-enabled deterministic test pass.

A Docker-capable job runs Docker integration tests serially. The public-Internet
smoke test stays manual or scheduled and does not gate ordinary changes. Failures
retain verbose pytest context and missing-line information.

## Git Workspace Policy

Substantial feature work and multi-file implementation should use an isolated
Git worktree under:

```text
.worktrees/<branch-name>
```

Before creating it, verify that `.worktrees/` is ignored. Tiny documentation
corrections may be made directly in the main checkout. This is a preference for
keeping experiments and implementation changes isolated, not a requirement to
create a worktree for every edit.

The root `.gitignore` includes:

- `.worktrees/` and `.venv/`;
- Python bytecode and caches;
- pytest, coverage, Ruff, and build output;
- local `.env` files and logs;
- generated local experiment run directories.

It does not ignore `uv.lock`, `.python-version`, `pyproject.toml`, Docker Compose
files, source-controlled fixtures, or architecture documents.

## Roadmap Effect

Phase 1 gains explicit deliverables for the fixed Python version, locked uv
environment, centralized tool configuration, ignore policy, and documented
commands. Its tests verify that a clean clone can sync with the committed lock
and run formatting, linting, type checking, parallel tests, and pinpointed tests.
Later phases continue to own Docker and Internet integration behavior.

This amendment changes development mechanics only. It does not add runtime
services, abstractions, security features, or algorithm scope.

## Authoritative References

- [uv Python versions](https://docs.astral.sh/uv/concepts/python-versions/)
- [uv project environments and locking](https://docs.astral.sh/uv/concepts/projects/sync/)
- [pytest invocation and node IDs](https://docs.pytest.org/en/stable/how-to/usage.html)
- [pytest verbosity](https://docs.pytest.org/en/stable/how-to/output.html)
- [pytest-xdist distribution modes](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)
- [pytest-cov](https://pytest-cov.readthedocs.io/en/stable/readme.html)
- [Ruff configuration](https://docs.astral.sh/ruff/configuration/)
- [Pyright installation](https://github.com/microsoft/pyright/blob/main/docs/installation.md)
