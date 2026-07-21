# Application Configuration

## Purpose

This document owns the shared configuration mechanism for Trafficlab
applications. Each application owns its own setting names, values, and
application-specific behavior.

## Locations

Versioned empty templates belong in `configs/`. User working copies belong in
`.configs/` and are excluded from Git. Separate explanations of individual
settings belong in `docs/configs/`.

By default, an application in `apps/<number>_<name>/` has the configuration
filename `<number>_<name>.toml`. For example, the capture application uses
`10_capture.toml`. An application owner may define a required alternative
filename and location. A template or a working copy is never discovered
automatically.

The current alternative is [30 genetic training](apps/30_genetic_training/README.md).
It requires an explicitly selected `.configs/genetic_strategy.toml`; its
versioned templates belong in `configs/30_genetic_training/`.

## Selection

By default, every application accepts these optional, mutually exclusive
arguments:

```text
--config-file PATH
--config-dir DIRECTORY
```

`--config-file` selects exactly `PATH`. `--config-dir` selects the invoking
application's numbered configuration filename within `DIRECTORY`. For
example, `10_capture --config-dir .configs` selects
`.configs/10_capture.toml`.

When neither argument is supplied, the application uses its built-in defaults
only. It does not search `configs/`, `.configs/`, or any other location. An
application owner may instead require an explicit configuration file; 30
genetic training is the current exception.

## Resolution

For every documented setting, the effective value is resolved in this order:

```text
built-in default -> selected config file -> explicit command-line argument
```

An explicit command-line argument replaces only its matching setting. A value
not passed on the command line remains from the selected file or the built-in
default. Environment variables are not a configuration layer.

## Validation and Failure

Before it performs work, an application validates the selected path, TOML
syntax, known setting names, types, and setting-specific constraints. A
missing selected file, invalid directory selection, malformed TOML, unknown
setting, or invalid value fails the command. Configuration is never silently
ignored or partially applied.

## Application Independence

Each application reads only its own possible configuration file. Applications
exchange information only through their explicit contracts and published
artifacts, never through another application's configuration.

## Startup Record

Each application owns an attempt directory and writes immutable `launch.toml`
at startup, before application work. Every application accepts the shared
infrastructure argument `--attempt-dir PATH`. A managed invocation must supply
it; a direct invocation omits it and creates a collision-resistant attempt at
`run/<application>_<UTC timestamp>_Z_<suffix>/` below the launch current
directory unless its owner defines a more specific run shape. The
`trafficlab` orchestrator uses its documented single-run or experiment
directory as its own attempt. The argument is not a configuration setting and
cannot appear in a TOML file.

An assigned attempt path is an absolute normalized path to an already-created,
empty, mode-`0700` directory owned by the invoking user. Neither the path nor
any ancestor selected below the managed run root may be a symlink. A direct
invocation creates the same private shape with an atomic collision check.
Invalid ownership, mode, type, aliasing, or pre-existing contents fail before
the application writes or launches anything. The application then writes
`launch.toml`, which records invocation arguments and effective configuration
after defaults, selected-file values, and command-line overrides are resolved.
It is not a copy of the selected source file.

If configuration resolution fails, `launch.toml` records the invocation
arguments, selected configuration source, and resolution failure. It remains
available after failure or interruption. A package copies the immutable record
into staging and hashes that member before publication. A single-file artifact
keeps the record in its attempt directory and binds its digest through the
[successful artifact status](libs/artifact_io/SAD.md#successful-status-envelope).
The live attempt record is never moved into an artifact.

No secret-bearing configuration setting is currently defined. Before a future
setting could contain a secret, its owner must define a safe reference or
redaction rule for both configuration files and `launch.toml`.

## Reading

Follow the [architecture governance](README.md). Read the relevant
application owner document before adding or changing its settings.
