# Trafficlab

Trafficlab is a Linux research suite for reproducible Layer 2 traffic capture,
modelling, generation, and comparison. The authoritative system description
and implementation sequence are in the [architecture corpus](architecture/README.md).

## Development environment

The [development environment](architecture/DEVELOPMENT.md) owns supported
platform and toolchain constraints. The
[infrastructure architecture](architecture/infrastructure/README.md) owns the
repository command and check contract.

Install the exact locked development environment from the repository root:

```bash
uv sync --locked --all-groups
```

Run every mandatory local gate through the repository-owned interface:

```bash
uv run --locked python tools/quality.py all
```

Individual gates use the same interface:

```bash
uv run --locked python tools/quality.py format
uv run --locked python tools/quality.py lint
uv run --locked python tools/quality.py typecheck
uv run --locked python tools/quality.py test
uv run --locked python tools/quality.py coverage
uv run --locked python tools/quality.py whitespace
uv run --locked python tools/quality.py docs
uv run --locked python tools/quality.py build
```

These commands are unprivileged and do not access live capture or host
resources. The aggregate `all` command runs the gates in documented order and
stops at the first failure. The whitespace gate checks the complete Git index
and tracked working tree; untracked paths enter its scope when added to Git.
The build gate emits an installable wheel with a fixed source-date epoch
so identical source and lock inputs produce identical wheel bytes. The docs
gate enforces the [architecture corpus validation rules](architecture/VALIDATION.md).

## Continuous integration

GitHub Actions calls the same locked `all` command for pull requests and pushes
to `main`. Repository branch protection must require the workflow's `quality`
job before changes can merge.
