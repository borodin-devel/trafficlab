# [DESIGN-1-60add1bf] Testing Infrastructure Design

## [SECTION-1-0fbc81d7] Goal

Make Trafficlab's test system faster to operate and easier to maintain without
weakening scientific, integration, cleanup, coverage, or retained-evidence
assurance. One document owns executable commands, pytest infrastructure has
focused ownership, and performance changes are accepted only after measured
equivalence to the current gates.

This is a test-infrastructure refactor. It does not change production behavior,
scientific tolerances, deterministic seeds, repetition counts, corruption
matrices, Docker cleanup expectations, or the validation-study audit contract.

## [SECTION-2-72f6d330] Gate model and document ownership

`architecture/TESTING.md` owns why each test class exists, its behavioral
obligations, marker semantics, and the evidence required to accept a run.
`architecture/DEVELOPMENT.md` owns the one canonical table of copyable bounded
commands. `README.md` links to that table and may show only the shortest focused
developer example. Historical plans remain records and are explicitly
non-normative.

The canonical gates are:

| Gate | Selection | Execution | Purpose |
| --- | --- | --- | --- |
| Focused | one node or last failures | serial | diagnosis and TDD |
| Fast | unit, not integration or external | four workers, work stealing | local feedback |
| Ordinary | all non-external tests | four workers, work stealing | offline regression and xdist safety |
| Coverage | all non-external tests | serial unless equivalence is proven | deterministic branch evidence |
| External | Docker or Internet tests | serial | attributable lifecycle and cleanup failures |
| Release | static gates plus every gate above | bounded commands | milestone evidence |

Every pytest process tree remains governed by `scripts/run_bounded.sh`. Tests
requiring Docker or the public Internet remain opt-in and fail clearly when
their explicitly selected capability is unavailable.

## [SECTION-3-72720bc2] Marker policy

The marker set remains intentionally small:

- `integration` means multiple Trafficlab modules cooperate without requiring
  an external service;
- `docker` means Docker Engine and Compose are required;
- `internet` means a configurable public endpoint is contacted.

No `slow`, `evidence`, phase, or implementation-detail marker is added. Runtime
is observed with pytest duration reporting, not encoded as a reason to omit a
correctness test. A test that has both external markers is collected once by the
combined external selection.

## [SECTION-4-b0808fdd] Helper ownership

`tests/conftest.py` retains pytest hook registration and thin fixture wiring.
Reusable implementation moves to focused test-support modules:

```text
tests/
  support/
    __init__.py
    config.py       # canonical valid experiment mapping
    docker.py       # external image lifecycle, resource tracking, adapters
    external.py     # URL, selection, seriality, bounded command helpers
    validation_study.py  # builders and fixture mechanics shared by study tests
  docker/
    support.py      # Docker-scenario config writers and capture-log readers
```

Support modules expose typed interfaces and receive direct unit coverage when
they contain decisions or error handling. Fixture functions may remain in
`conftest.py`, but their bodies delegate to the focused support modules. Docker
scenario helpers stay beside Docker tests and must not duplicate generic image
or cleanup machinery.

## [SECTION-5-a24234c3] Validation-study modularization

The validation-study unit suite is split by behavioral owner rather than by
arbitrary file size: protocol and codecs, orchestration and collection,
offline audit and publication, prerequisite rotation and durability, and audit
boundary matrices. Shared builders and repository-fixture mechanics move to
`tests/support/validation_study.py`; owner-specific helpers remain in the owner
module.

The split may intentionally change pytest file-qualified node IDs. Acceptance
therefore compares a manifest of test function names, parametrized case counts,
markers, and total collection, with an explicit old-to-new path mapping. Helper
extraction before the split must preserve exact node IDs. No test case is
deleted or narrowed merely to make the suite faster.

## [SECTION-6-470ffd72] Performance decision

The ordinary and coverage gates report the 50 slowest cases. Optimization
starts from that evidence. Four-worker coverage may replace serial coverage
only when repeated serial and parallel runs have all of the following:

- identical measured source-file sets;
- identical executed and missing line sets;
- identical executed and missing branch sets;
- the same pass/fail outcome and collection cardinality;
- identical checked-in deterministic output from every generator check.

If equivalence is not demonstrated, serial coverage remains normative. In that
case wall time is reduced by scheduling independent static, ordinary, coverage,
and external jobs concurrently on separate executors, not by running competing
resource-heavy commands inside one bounded local scope.

Repeated full repository copies, subprocesses, Docker builds, or cryptographic
inventories may be consolidated only after profiling and only when at least one
uncached, regular-copy, no-hardlink end-to-end case retains each boundary.

## [SECTION-7-3b3448ac] Compatibility and quality invariants

The refactor adds no runtime dependency and no Node.js application dependency.
It uses the existing Python 3.12, pytest, pytest-xdist, and pytest-cov toolchain.
Strict Pyright and Ruff cover all support modules.

Acceptance requires preservation of deterministic seeds, fixture bytes, file
modes, scientific formulas, repetition counts, validation mutations, cleanup
checks, source binding, no-hardlink reconstruction, and the 90% branch-aware
package coverage floor. A failed unit test that exposes a defective helper must
cover all executable lines and branches of that helper.

## [SECTION-8-043272d3] Verification and delivery

Each extraction follows RED, GREEN, and refactor: add or redirect a focused
helper contract test, confirm the intended import or behavior failure, perform
the smallest move, then run the focused owner suite. Collection and marker
manifests bracket modularization.

Final evidence includes locked sync, format, Ruff, strict Pyright, the bounded
parallel ordinary gate, bounded branch coverage at or above 90%, deterministic
generator checks, the combined serial external gate when capabilities are
available, and an independent review with no Critical or Important findings.
Changes are retained as coherent local commits. Remote pushes and tag changes
require separate user authorization.
