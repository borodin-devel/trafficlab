# Development and Platform Architecture Design

## Goal

Record the shared development toolchain and Linux runtime boundary without
duplicating those facts across application and script documents.

## Development Owner

`architecture/DEVELOPMENT.md` is the single owner document for the shared
development environment:

- development targets Linux-based operating systems, with Windows Subsystem
  for Linux 2 as the primary environment;
- no native Windows application or capture support is planned;
- application development uses Python 3.12, uv, pytest, pytest-cov, and ruff;
- script development uses Bash; and
- script tests belong in `tests/scripts/`, use no elevated access, and do not
  mutate the real system.

The root architecture README adds this document immediately after governance in
its reading order.

## Capture Integration

`10_capture` adds a platform boundary: it runs Linux target applications in a
Linux-based environment, with WSL 2 primary. Windows applications and native
Windows traffic capture are unsupported.

`00_preflight`, workspace scripts, and network-workspace documents reference
the development owner for shared platform and toolchain facts. Preflight keeps
ownership of actual readiness checks; the development document does not define
capture tool requirements or runtime configuration.

## Validation

The change is complete when every new link resolves, the toolchain names occur
only in the development owner, capture explicitly excludes Windows support, and
the architecture corpus has no trailing whitespace.
