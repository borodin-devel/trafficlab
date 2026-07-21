# Incremental Architecture Scaffold Design

## Goal

Extend the architecture corpus with the smallest structure needed to document
Trafficlab one application at a time. The corpus remains new, English-language
documentation; its previous draft is research material only and is not part of
the active reading path.

## Scope

The initial scaffold contains the existing governance README, one capture-app
owner document, and three empty extension roots:

```text
architecture/
├── README.md
├── apps/
│   └── 10_capture/
│       └── README.md
├── contracts/
│   └── .gitkeep
├── traffic_models/
│   └── .gitkeep
└── genetic_models/
    └── .gitkeep
```

No other application, contract, traffic model, genetic model, pipeline,
implementation, or project-management document is created in this change.

## Structure and Ownership

`architecture/README.md` remains the entry point and defines how the
architecture corpus is read and changed. It documents architecture governance,
not the Trafficlab product.

Each future application belongs in `apps/<number>_<name>/`. Its `README.md` is
the initial owner document for that application's stable boundary. Further
documents are added only when a concrete decision needs a distinct owner.

Each contract belongs in `contracts/<producer>_<consumer>_<name>/`. Its
`README.md` owns the contract, with schemas, examples, and contract tests added
there when needed. A contract is created only after both the producer and
consumer applications have been designed.

Each traffic or genetic model belongs in its own directory below the respective
model root. That directory owns the model's equations, assumptions, parameters,
and validation criteria. Applications reference a model rather than redefining
it.

## Capture Application Boundary

`apps/10_capture/README.md` establishes only the capture application's stable
boundary:

- it captures traffic produced by a requested application run;
- it accepts a validated application-execution request and capture settings;
- it publishes a verified capture artifact for a later consumer;
- it records enough execution metadata to establish artifact lineage;
- it does not publish incomplete or unverified output as a successful artifact;
- application failure and capture-app failure are distinct results; and
- command execution and capture privileges are security boundaries: commands
  are argument vectors, privileges are minimal, and cleanup is required.

The document deliberately does not select a capture backend, Linux/WSL
mechanism, privilege-helper design, file format, or contract fields. Those
choices belong to later, concrete decisions.

## Validation

The change is complete when:

- the required directories are represented in Git;
- the root README links to the capture-app owner document using a relative
  link, and its ordered reading path remains valid;
- the capture README links back to the root README using a relative link;
- no active document links to, cites, or treats the prior draft as
  authoritative;
- all local Markdown links resolve; and
- only the agreed scaffold and its design record are changed.
