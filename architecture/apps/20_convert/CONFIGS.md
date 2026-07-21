# Convert Configuration

## Selection

Optional filename is `20_convert.toml`; current template is empty. Shared
selection and precedence are defined in [configuration](../../CONFIGURATION.md).

## Unresolved Reference Profile

The profile must identify one capture interface, one or more app-side local IP
addresses, and local MAC addresses when available. Exact TOML paths, types,
defaults, address-family representation, and CLI overrides are unresolved.

## Validation

Until serialization is approved, application-specific keys fail as unknown.
Future validation must reject duplicate/invalid addresses, unknown interfaces,
inconsistent MAC/IP evidence, and profiles unable to define one observation boundary.
