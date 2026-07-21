# Infrastructure Configuration

## Known Tooling

Python version is 3.12. Dependency management is uv; build backend is
setuptools; tests use pytest and pytest-cov; static checks use pyright and ruff.

## Unresolved Decisions

Exact `pyproject.toml` metadata, dependency versions, coverage threshold,
pyright strictness, ruff rule selection, build targets, and CI provider are not
defined by current architecture. Stage 1 of the roadmap must resolve them as
one reviewed configuration set before application implementation.
