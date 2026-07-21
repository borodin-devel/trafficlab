# Development and Delivery Infrastructure

## Purpose

Infrastructure owns supported platforms, dependency management, build, test,
coverage, static analysis, formatting, packaging, and continuous integration.

## Public Interfaces and Configuration

Developer interfaces are uv commands and repository configuration. Runtime
application configuration is outside this component and belongs to
[application configuration](../CONFIGURATION.md).

## Documents

- [SAD](SAD.md)
- [SRS](SRS.md)
- [Configuration](CONFIGS.md)
- [Roadmap](ROADMAP.md)
- [Development constraints](../DEVELOPMENT.md)
- [VS Code integration](../ide_integration/VSCODE.md)

## Execution Context and Dependencies

Development targets Python 3.12 on Linux, primarily Ubuntu under WSL2. Bash
scripts cover workspace operations. Infrastructure depends on no traffic model
or similarity method.
