# Capture Configuration

## Selection

Optional filename is `10_capture.toml`; shared resolution is defined in
[application configuration](../../CONFIGURATION.md). Current template is empty.

## Request-Owned Policy

Completion policy, interactive mode, requested host-service bridges, target
environment, and packet retention are required fields of the explicit
[capture request](../../contracts/00_capture_request/CONFIGS.md). They have no
`10_capture.toml` fallback or override. In particular, capture supplies no
implicit packet-retention default.

## Application-Specific Settings

Exact recorder-specific TOML keys remain unresolved. They may control only
implementation choices that do not change request semantics or widen a
preflight-approved capability.

## Validation

Until concrete fields exist, any application-specific TOML key fails as
unknown. Future validation must reject keys that duplicate or override request
policy, widen an approved network capability, or weaken validation, cleanup,
publication, or security behavior.
