# Development Environment

## Purpose

This document owns the shared platform and development-toolchain constraints
for Trafficlab.

## Platform

Trafficlab targets Linux-based operating systems. Windows Subsystem for Linux 2
is the primary development and capture environment. No native Windows
application or traffic-capture support is planned.

## Application Development

Application development uses Python 3.12, uv, setuptools, pytest, pytest-cov,
pyright, and ruff. PyArrow supports inspection Parquet contracts; Scapy supports
packet fixture and PCAPNG work where the selected PCAPNG backend permits it.
Automated tests run without elevation and do not alter the real system.

Exact dependency versions and tool configuration remain owned by
[development infrastructure](infrastructure/README.md).

## IDE Integration

Optional repository-owned VS Code workspace support follows the
[VS Code integration contract](ide_integration/VSCODE.md). Editor tasks and
debug configurations delegate to repository commands, remain unprivileged, and
do not replace command-line or CI validation.

## Script Development

Workspace and operator scripts use Bash. Their automated tests belong in
`tests/scripts/`, use no elevation, and do not mutate the real system.

## Reading

Follow the [architecture governance](README.md) before changing this document.
