# System Configuration Architecture

## Scope

Shared selection, resolution, startup recording, and validation are defined by
[application configuration](../CONFIGURATION.md). Each component owns its
setting names in its `CONFIGS.md` or explicitly states that no settings exist.

## Invariants

- Configuration sources are explicit; no application searches for a file.
- Built-in defaults precede a selected TOML file, which precedes explicit CLI
  overrides.
- Environment variables are not a configuration layer.
- Unknown fields, malformed values, and inconsistent settings fail before work.
- `launch.toml` records effective settings and resolution failure information.

## Unresolved Decisions

Concrete settings absent from current component owners remain unresolved and
must not be invented by implementations. Their component roadmap must resolve
schema, default, validation, and documentation together.
