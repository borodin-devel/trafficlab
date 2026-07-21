# Infrastructure Software Architecture Document

## Context, Goals, and Non-Goals

Infrastructure gives all components one repeatable Python and Bash development
environment. It does not implement capture, modelling, generation, or scoring.

## Structure and Interfaces

uv manages environments and dependencies; setuptools builds packages; pytest
and pytest-cov execute and measure tests; pyright checks types; ruff lints and
formats. Continuous integration shall invoke the same repository commands used
locally. Concrete CI provider and packaging metadata are unresolved because no
implementation scaffold exists.

## Security and Execution

Automated checks run unprivileged and never mutate real system state. Test
fixtures, fake subprocesses, and temporary directories replace live capture
and privileged operations.

## Performance, Errors, and Observability

Checks fail explicitly on tool failure and retain useful logs. Parallel test
execution may be added only after deterministic isolation and resource limits
are documented. Build output must be reproducible for identical source and
locked dependencies.

## Testing Architecture, Risks, and Decisions

The pipeline orders formatting/linting, type checking, unit tests, integration
tests, coverage, documentation validation, and package build. WSL2-specific
manual capture verification remains separate. Main risks are undeclared native
dependencies, nondeterministic tests, and divergence between local and CI
commands.
