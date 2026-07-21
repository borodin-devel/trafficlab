# Preflight Readiness Stub Design

## Goal

Add the first application before capture: a small, read-only architectural
boundary for determining whether the current system is ready to prepare and
perform traffic capture.

## Scope

The change creates `architecture/apps/00_preflight/README.md` and updates the
ordered reading path in `architecture/README.md` and the capture boundary in
`architecture/apps/10_capture/README.md`.

No contract directory is created. The preflight report's fields and file format
remain undecided until the producer and consumer have concrete interface needs.

## Preflight Boundary

The preflight application assesses the current system's readiness for capture
preparation. Its scope covers system requirements, installed required software,
permissions, and relevant existing configuration.

It accepts a capture-preparation request and publishes a readiness decision
with findings and blockers for capture. It is strictly read-only: it does not
install software, change system configuration, create capture resources, or
elevate privileges.

The document does not select a platform, required programs, permissions,
configuration values, report format, or remediation mechanism. Those are later
implementation decisions.

## Integration

The architecture reading order becomes root governance, `00_preflight`, then
`10_capture`. The capture boundary names a successful preflight readiness
decision as a required input and references the preflight owner document.

The application documents retain their own ownership: preflight owns readiness
assessment; capture owns traffic capture. Neither duplicates the other's
requirements or report details.

## Validation

The change is complete when the three Markdown documents use valid relative
links, the root reading order lists both applications, and the preflight stub
contains no platform, tool, report-format, or remediation commitment. The
existing architecture scope exclusions and whitespace checks continue to pass.

