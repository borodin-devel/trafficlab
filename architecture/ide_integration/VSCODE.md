# Visual Studio Code Integration

## Purpose and Status

This document defines optional repository-owned Visual Studio Code workspace
support under `.vscode/`. It is a supporting integration description, not an
application, library, runtime component, or replacement for command-line
development. The repository does not yet contain `.vscode/`, the Python
implementation scaffold, or the stable command interface required to create
the workspace files safely.

VS Code support is complete only when it delegates to the same reviewed
commands used locally and in continuous integration. Editor behavior never
overrides repository configuration or changes Trafficlab runtime contracts.

## Authoritative Sources

- [Development environment](../DEVELOPMENT.md) owns the supported platform and
  toolchain.
- [Development infrastructure](../infrastructure/README.md) owns repository
  commands, dependency management, build, tests, static checks, and CI parity.
- [Infrastructure configuration](../infrastructure/CONFIGS.md) records which
  tooling decisions remain unresolved.
- [Implementation structure](../project/IMPLEMENTATION_STRUCTURE.md) owns the
  planned source and test layout.
- [Architecture governance](../README.md) owns naming, links, and revision
  rules.

If editor configuration conflicts with one of these owners, the editor
configuration changes. `.vscode` files do not become a second source of truth.

## Supported Execution Environment

The supported workspace is the repository root opened in Linux, preferably
through VS Code's WSL integration on WSL2. Native Windows interpreters, Windows
path translation, and native Windows capture are unsupported. A workspace must
use the Python 3.12 environment created by the repository's uv bootstrap; it
must not select a global interpreter or record an absolute user-specific path.

VS Code remains optional. A clean checkout must be installable, testable, and
buildable without the editor or any recommended extension.

## Repository-Owned Workspace Files

The initial supported set is intentionally limited:

| File | Responsibility | Availability condition |
| --- | --- | --- |
| `.vscode/extensions.json` | Recommend compatible editor capabilities. | May be added after extension identifiers are reviewed on the supported VS Code and WSL environment. |
| `.vscode/settings.json` | Select workspace-scoped language, test, formatting, and file-display behavior. | May be added after the uv environment and repository tool configuration exist. |
| `.vscode/tasks.json` | Expose thin adapters to repository-owned development commands. | Must wait for the stable command interface required by INF-IF-001. |
| `.vscode/launch.json` | Provide safe, unprivileged debugging of implemented application modules and fixtures. | Must wait for module entry points and representative non-privileged fixtures. |

User settings, keybindings, UI layout, recent files, extension state, machine
identifiers, and personal `.code-workspace` files are not repository-owned.
Additional `.vscode` files require an update to this document and a concrete
project-wide need.

## Extension Recommendations

`extensions.json` recommends capabilities, not mandatory developer products.
The reviewed set should cover:

- WSL workspace access;
- Python language and debugger support compatible with Python 3.12;
- pyright-compatible analysis without weakening repository type checks;
- ruff linting and formatting using repository configuration;
- TOML, JSON/JSONC, and Markdown editing.

Exact marketplace identifiers are selected and verified when the file is
implemented. Recommendations must be maintained, available for the supported
environment, and removable without breaking any repository command. The
workspace must not require an extension solely to generate committed files.

## Workspace Settings Contract

`settings.json` contains only portable workspace settings:

- use UTF-8, LF line endings, final newlines, and the repository's indentation
  rules;
- select the uv-created workspace interpreter without an absolute path;
- discover pytest tests from the repository-owned test layout and leave real
  host resources untouched;
- use ruff and pyright settings from repository configuration rather than
  duplicating rule lists, exclusions, or strictness in editor settings;
- enable format-on-save or save-time fixes only when they run the same formatter
  and semantics as the mandatory repository checks;
- hide generated or ignored output only in the editor view, never by changing
  artifact, lineage, or cleanup behavior.

The file must not contain environment secrets, usernames, home directories,
hostnames, absolute interpreters, local ports, capture devices, or settings
that suppress mandatory diagnostics. Personal overrides remain outside
version control.

## Task Contract

`tasks.json` is an imperative shell over the repository command interface. It
does not reimplement commands. Once those commands exist, the workspace may
expose stable `Trafficlab:` task labels for:

- environment bootstrap or dependency synchronization;
- the complete mandatory check suite;
- formatting verification, linting, and type checking;
- unit and integration tests;
- coverage;
- architecture-corpus validation;
- package build.

Tasks run from `${workspaceFolder}` and use process commands with explicit
argument arrays where supported. They must not use interpolated shell strings,
depend on globally installed Python tools, silently change command options, or
report success after a delegated command fails. The complete check task may
compose smaller tasks only in the order owned by infrastructure.

No committed task may run automatically when a folder opens. Tasks must not
request elevation, invoke `sudo`, alter namespaces or networking, access live
capture devices, or call the privileged operator scripts described by the
[script boundary](../scripts/README.md). Inputs may select only bounded,
non-secret fixture values.

## Debugging Contract

`launch.json` is limited to implemented, unprivileged Python module entry
points and deterministic fixtures. Each configuration must:

- use the selected workspace interpreter and module invocation instead of a
  source-file path;
- set `${workspaceFolder}` as its working directory;
- pass arguments as an array and use explicit fixture, configuration, seed,
  input, and output paths;
- keep diagnostic output inside documented ignored development locations;
- fail closed when a required fixture or configuration is absent;
- avoid embedded environment values, credentials, tokens, host paths, and
  operator-specific state.

Live capture, workspace setup or rollback, privilege elevation, and mutation of
real system resources are never editor launch configurations. Those boundaries
remain explicit manual operations or use fake adapters in automated debugging.

## Implementation and Maintenance Sequence

1. Infrastructure defines and validates the Python 3.12 uv environment and one
   repository command interface.
2. Add reviewed extension recommendations and portable workspace settings.
3. Add task adapters and compare every task result with its command-line and CI
   equivalent.
4. Add launch configurations only after application entry points and safe
   fixtures exist.
5. Parse every JSON/JSONC file, test a clean WSL2 checkout, validate paths, and
   scan for secrets and machine-specific values.

Changes to tool names, commands, source layout, test layout, or supported
platforms update the authoritative owner first and `.vscode` adapters in the
same change. If no stable repository command exists for an action, omit that
task rather than inventing an editor-only command.

## Acceptance Criteria

Repository-owned VS Code support is acceptable when:

- a clean WSL2 checkout opens at the repository root without machine-specific
  edits;
- the selected interpreter belongs to the reviewed uv environment;
- task exit codes and outputs match the corresponding local and CI commands;
- test discovery and debugging use only bounded fixtures and unprivileged
  adapters;
- no workspace file contains a secret, absolute user path, shell-injection
  surface, privilege request, or automatic folder-open task;
- VS Code and all extensions can be absent without reducing command-line
  functionality;
- workspace files parse successfully and all relative documentation links
  resolve.
