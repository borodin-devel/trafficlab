# Configuration Software Architecture Document

## Context, Goals, and Boundaries

Every application needs identical explicit configuration selection and startup
lineage. The library owns shared resolution mechanics; applications own keys,
types, defaults, and domain constraints.

## Structure and Runtime Interaction

A pure resolver validates source selection, parses TOML into typed values,
rejects unknown keys, overlays matching CLI values, invokes the application
schema validator, and produces a canonical effective record. A shell writes
`launch.toml` before application work and retains resolution failures. Before
resolution, the shell either validates the shared managed `--attempt-dir`
boundary or securely creates the documented direct-attempt directory; the
flag is infrastructure input and never part of application TOML.

## Errors, Security, Performance, and Logging

Missing selected files, malformed TOML, conflicting selectors, unknown keys,
invalid values, and unsafe secret recording fail explicitly. Paths are
untrusted. Assigned attempts reject non-absolute paths, symlinks, wrong
ownership or mode, nonempty directories, and managed-run-root escape before
writing. Configuration size is bounded; no environment-variable discovery or
directory search occurs. Diagnostics never print future secret values.

## Testing, Decisions, Risks, and Limits

Table-driven tests cover precedence and all failures. Schema framework and
exact launch serialization are unresolved. Secret-bearing settings require a
new owner-defined reference or redaction rule before introduction.
