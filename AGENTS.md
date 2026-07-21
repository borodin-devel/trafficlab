# Trafficlab agent guidance

## Authority and reading

`architecture/` is the source of truth. Start with `architecture/README.md` and
follow its order: system overview, scope, pipeline, the relevant stage, its
contracts, implementations and common rules, then `architecture/project/`.
Read the owner document for every fact you change; use relative links rather
than duplicating it. Original architecture documents are immutable.

Create an amendment only for an irreconcilable architectural conflict, at
`architecture/amendments/<number>_<short_name>.md`. It must name the conflicting
sources, describe the decision and rationale, define the scope and consequences,
record alternatives, compatibility/migration effects, and state its status and
owner/date. Do not use amendments to excuse missing implementation.

## Engineering

The stack is Python 3.12, uv, setuptools, pytest, PyArrow, and Scapy (see
`pyproject.toml`). Keep a functional core (deterministic, side-effect-free
logic) behind an imperative shell for files, subprocesses, networking and UI.
Honor file contracts: validate inputs and outputs, publish atomically, preserve
hashes/lineage, and make ordering, seeds and serialization deterministic.

Treat capture, namespaces, privileges, external commands and untrusted files as
security boundaries. Keep privilege minimal, use argument vectors rather than
shell strings, validate paths and data, and ensure cleanup and explicit
permission checks at boundary crossings.

## Autonomous delivery

Repeat: inspect evidence, plan, change, test, document, update. Keep roadmap
work in `ROADMAP.md` aligned with `architecture/project/ROADMAP.md`; every
stage, item and subitem must state its status, deliverable/task, completion
criteria and applicable test types. Comment non-obvious code, and document
public behavior, contracts, commands and limits where users need them.

Resolve low- and medium-severity questions from repository and architecture
evidence. Ask the programmer only about high-severity issues: security or
privilege boundaries, irreversible data changes, unsupported public contracts,
or conflicting product goals.
