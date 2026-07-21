# Preflight Configuration

## Selection

Optional configuration filename is `00_preflight.toml`; shared resolution is
defined in [application configuration](../../CONFIGURATION.md). The existing
versioned template is empty.

## Request-Owned Policy

Workspace identity, target execution, completion, bridges, packet retention,
and readiness lifetime are fields of the explicit
[capture request](../../contracts/00_capture_request/CONFIGS.md), not preflight
configuration. Preflight must not duplicate or override them.

No application-specific preflight setting names, types, defaults, or CLI
overrides are currently defined. Future probe thresholds must be added here
before use and cannot alter request semantics.

## Validation

Until settings exist, any application-specific TOML key is unknown and fails.
