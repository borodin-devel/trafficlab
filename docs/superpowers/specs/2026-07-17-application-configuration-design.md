# Application Configuration Design

**Date:** 2026-07-17  
**Status:** approved design, pending architecture documentation

## Purpose

Define one simple, deterministic way for Trafficlab applications to obtain
optional configuration without coupling applications to one another or making
configuration files conflict with explicit command-line choices.

## Scope

This design covers the existing applications:

- `00_preflight`;
- `10_capture`; and
- `20_convert`.

It defines configuration discovery, precedence, validation, repository
locations, documentation locations, and the per-run configuration snapshot.
It does not define individual configuration fields, command-line syntax beyond
the shared selection options, report schemas, or application behavior.

## Options Considered

1. **A single project-wide `config.toml`.** Rejected because it would couple
   independent applications and make unrelated settings accumulate in one
   file.
2. **Implicitly load `.configs/` whenever it exists.** Rejected because an
   unnoticed local file would change a command's behavior and make runs harder
   to reproduce.
3. **One optional file per application, selected explicitly.** Chosen because
   the file owner is clear, the command remains runnable with defaults, and an
   explicit command shows whether configuration influenced it.

## Repository Layout

Versioned example templates are kept in `configs/`:

```text
configs/00_preflight.toml
configs/10_capture.toml
configs/20_convert.toml
```

User working copies are kept in `.configs/`, which is intentionally excluded
from Git:

```text
.configs/00_preflight.toml
.configs/10_capture.toml
.configs/20_convert.toml
```

Separate human-readable descriptions are kept in `docs/configs/` using the
same application names:

```text
docs/configs/00_preflight.md
docs/configs/10_capture.md
docs/configs/20_convert.md
```

The descriptions will define each template's settings, types, valid values,
defaults, and matching command-line override. Template field definitions are
added only as the owning application is designed; the initial templates remain
empty rather than guessing settings.

## Application Independence

Each application has only its own possible configuration file. `00_preflight`
does not read `10_capture` configuration, and no application reads another
application's configuration. Any information passed between applications
continues to use their explicit contracts and published artifacts.

## Selection and Resolution

Every application accepts these optional, mutually exclusive selection
arguments:

```text
--config-file PATH
--config-dir DIRECTORY
```

`--config-file` selects exactly `PATH`. `--config-dir` selects the invoking
application's fixed numbered filename within `DIRECTORY`; for example,
`10_capture` selects `DIRECTORY/10_capture.toml`.

When neither option is given, the application searches neither `configs/` nor
`.configs/` and runs with built-in defaults only.

For every documented setting, the final value is resolved in this order:

```text
built-in default -> selected config file -> explicit command-line argument
```

An explicit command-line argument changes only its matching setting. Values
not supplied on the command line remain from the selected file or built-in
default. No environment-variable configuration layer exists.

## Validation and Failure

Before doing work, an application validates the chosen path, TOML syntax,
known setting names, types, and setting-specific constraints. A missing chosen
file, invalid directory selection, malformed TOML, unknown setting, or invalid
value is a command failure. The application does not silently ignore or
partially apply configuration.

## Run Snapshot

Each successful application result includes `launch.toml` in its artifact
directory. It records the complete effective configuration after defaults,
the selected file, and command-line overrides are resolved. It is a snapshot
of final setting values, not a copy of the source config file.

The current configuration scope contains no secret-bearing settings. If a
future setting could contain a secret, its owner must define a safe reference
or redaction rule before it can be included in a config file or `launch.toml`.

## Ownership and Documentation Changes

A new common architecture document will own the shared mechanism above and
will be added to the architecture reading order. Each existing application
document will reference it and retain ownership of its own settings. The new
`docs/configs/` files describe application-specific template fields but do not
override architecture.

## Error Handling and Testing

Configuration loading and resolution belong to deterministic, unprivileged
functional-core logic. Tests will cover defaults-only runs, each selection
mode, mutual exclusion, per-setting CLI precedence, independent application
selection, `launch.toml` construction, and all validation failures. No test
needs a prepared capture workspace or elevated privilege.
