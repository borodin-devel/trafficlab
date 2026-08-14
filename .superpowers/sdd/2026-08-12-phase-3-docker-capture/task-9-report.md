# Task 9 — Real Docker fixtures, Internet smoke, and Phase 3 evidence

## Result

- Added checked-in deterministic endpoint, client, and final-stage `scratch`
  shell-free workload images. The endpoint provides controlled TCP/UDP replies,
  one inbound broadcast, and a concurrent unrelated unicast exchange.
- Added a checked-in test-only Compose overlay. Production rendering is
  unchanged and remains exactly `{capture, target}`. Tests merge the endpoint,
  unrelated-noise, and deliberate-orphan services only at the test boundary.
- Added 12 serial real-Docker cases covering full preflight, capture readiness
  before target start, TCP/UDP/address/count/direction/broadcast inspection,
  unrelated-traffic exclusion, direct shell-free launch, natural zero and exact
  nonzero status, normal/timed-out children, early capture exit, ignored
  `SIGINT`, malformed output, readiness failure, and interruption.
- Added a serial opt-in Internet smoke requiring `--internet-url` with a
  credential-free HTTPS URL. It requires DNS/TLS client success, target zero,
  parseable bidirectional TCP capture, and labelled-resource teardown.
- Every resource-owning test records unique project names. Teardown inventories
  project-labelled containers, networks, and volumes after the tested lifecycle;
  failures print remaining names and perform bounded best-effort removal. An
  intentionally removed overlay service exercises Compose orphan removal.
- Explicit external selection rejects xdist workers and fails actionably rather
  than skipping when Docker Engine, Compose v2, or image building is unavailable.
  Default and deterministic scopes deselect external tests without contacting
  Docker or the Internet.

## Cross-module resolution

This was a Class 3 interface decision resolved from `CAPTURE.md` and the Task 9
brief. The production renderer and Docker adapter were not generalized to know
about a third service or multiple Compose files. Test setup merges the checked-in
overlay into temporary test documents, and a test-only `DockerCompose` subclass
starts the controlled endpoint before delegating capture creation. Preflight
probes and captures therefore share their own unique project/network with the
endpoint while production remains a two-service document.

## TDD evidence

- Baseline: guarded scope `trafficlab-task9-baseline-15534.scope`; `31 passed`
  for the preceding Task 8 unit/installed-entry paths, scope inactive afterward.
- RED 1: guarded scope `trafficlab-task9-red-options-18245.scope`; 15 expected
  failures because external-selection, URL, actionable-command, cleanup-message,
  and fixture-root contracts did not exist.
- GREEN 1: guarded scope `trafficlab-task9-green-options-8645.scope`; `15 passed`.
- RED 2: guarded scope `trafficlab-task9-red-selection-5770.scope`; two expected
  failures for parenthesized negation and absolute Docker test paths.
- GREEN 2: guarded scope `trafficlab-task9-green-selection-21353.scope`; `17 passed`.
- RED 3: guarded scope `trafficlab-task9-red-serial-26217.scope`; three expected
  failures because explicit external sessions did not yet enforce `-n 0`.
- GREEN 3: guarded scope `trafficlab-task9-green-serial-15794.scope`; `20 passed`.
- Final focused: guarded scope `trafficlab-task9-focused-review-29064.scope`;
  `20 passed`, inactive afterward, with no pytest descendant.
- One early `--collect-only` command was accidentally launched without the
  required systemd resource guard. It is invalid and is not cited as evidence.
  Guarded replacement collection was then performed.

Every pytest invocation used after that correction ran within the prescribed
collected user systemd scope. Pre/post exact executable checks found no
overlapping or surviving `pytest`/`py.test` process.

## External-test contract evidence

- Guarded Docker collection `trafficlab-task9-collect-review-32642.scope`:
  12 Docker nodes collected.
- The same guarded collection selected the one Internet node, for 13 external
  nodes total, and left the scope inactive with no pytest descendant.
- Guarded controlled unavailable check
  `trafficlab-task9-docker-unavailable-32250.scope`: a narrowly selected Docker
  node failed setup nonzero with `Docker CLI was not found` and `install Docker
  Engine with Compose v2`; the wrapper asserted both diagnostics and returned
  success. The scope was inactive and left no pytest descendant.
- Fixture Python files compile under locked CPython, the overlay parses as JSON,
  and a bounded direct renderer check confirms production services are exactly
  `capture` and `target`.

The unavailable-contract check proves only failure behavior. It is not Docker
capture, image-build, cleanup, or Internet evidence.

## Local gates

- `uv sync --locked --all-groups` — pass; 20 packages resolved and 19 checked.
- `uv lock --check` — pass with no lock change.
- `uv run --locked ruff format --check .` — 101 files already formatted.
- `uv run --locked ruff check .` — pass.
- `uv run --locked pyright` — `0 errors, 0 warnings, 0 informations`.
- Guarded fast scope `trafficlab-task9-fast-15256.scope`, exactly four workers
  with work stealing and marker `not integration and not docker and not
  internet` — `979 passed in 1.56s`; scope inactive, no descendants.
- Guarded coverage scope `trafficlab-task9-coverage-13054.scope`, exactly four
  workers with work stealing and marker `not docker and not internet` — `1018
  passed in 3.03s`; 96.69% branch-aware package coverage; scope inactive, no
  descendants.
- Before each broad gate the host showed approximately 14 GiB available of 15
  GiB and zero of 4 GiB swap in use. Suites did not overlap.
- `git diff --check` — pass.

## Environmental limitation

Class 4: this host has no `docker` executable. Per the approved autonomous
policy, fixture implementation, deterministic local checks, and the actionable
failure contract were completed without asking or stopping. No Docker image was
built, no real Compose project was created, and no Internet smoke was run. The
required serial commands remain for a capable host:

```bash
uv run --locked pytest -vv -x -n 0 -m docker
uv run --locked pytest -vv -x -n 0 -m internet --internet-url URL
```

Those commands must run under the documented process-tree guard. Their results
must not be inferred from collection or static checks.

## Roadmap state

- Phase 3 remains `(Current)`.
- The implemented production deliverables, event/deadline unit evidence, and
  exact production-service/test-overlay contract are checked.
- Every checkbox requiring real Docker lifecycle, resource-removal, shell-free
  container, or public-Internet execution remains unchecked.
- The Phase 3 Done-when condition remains unmet because controlled Docker and
  opt-in Internet capture evidence do not exist on this host.
- Phase 4 is not marked current, and dependency order is unchanged.

## Files

- `tests/conftest.py`
- `tests/__init__.py`
- `tests/unit/test_external_test_control.py`
- `tests/docker/__init__.py`
- `tests/docker/support.py`
- `tests/docker/compose.endpoint.json`
- `tests/docker/images/endpoint/Dockerfile`
- `tests/docker/images/endpoint/server.py`
- `tests/docker/images/client/Dockerfile`
- `tests/docker/images/client/client.py`
- `tests/docker/images/no_shell/Dockerfile`
- `tests/docker/test_capture_docker.py`
- `tests/docker/test_capture_failures.py`
- `tests/internet/test_capture_internet.py`
- `architecture/DEVELOPMENT.md`
- `architecture/ROADMAP.md`
- `.superpowers/sdd/2026-08-12-phase-3-docker-capture/progress.md`
- `.superpowers/sdd/2026-08-12-phase-3-docker-capture/task-9-report.md`

## Self-review

- Re-read Task 9, `CAPTURE.md`, `TESTING.md`, `DEVELOPMENT.md`, `SYSTEM.md`, and
  every Phase 3 Roadmap item against the diff.
- Confirmed no production source, runtime dependency, NodeJS application file,
  `sudo`, `shell=True`, skip, xfail, PID file, Compose `exec`, or target wrapper
  was added.
- Confirmed expected packet/address/count values are independent literals and
  tests assert parsed Trafficlab inspection results rather than mock behavior.
- Confirmed normal and failure projects share unconditional labelled-resource
  inventory and teardown diagnostics.
- Fixed during self-review: the HTTPS client image now installs CA certificates;
  controlled endpoint startup waits for health and the unrelated exchange;
  an intentional removed service exercises `--remove-orphans`; real failure
  tests assert target kill, capture signal/kill counts, and induced-status
  precedence rather than only matching error text.
- No Critical or Important task-local finding remains in static/local evidence.
  Real Docker execution may still reveal image/runtime defects and is explicitly
  pending rather than claimed clean.

## Commit

`c3de6c6 test(capture): verify Docker capture lifecycle`

## Review fix round 1/5

Verified all five findings against the implementation and corrected them without
changing production code:

- The deterministic UDP client now accepts the inbound broadcast while waiting
  for any expected acknowledgement, retains that observation across requests,
  and still requires the broadcast before returning.
- Docker lifecycle ordering now reads the real `capture_ready` and
  `capture_published` run-log events.
- Labelled-resource inventory catches errors independently for containers,
  networks, and volumes. All known resources still receive bounded removal
  attempts, and inventory, leak, nonzero-removal, and removal-exception
  diagnostics are aggregated. A call-phase exception is retained in the pytest
  item stash; if cleanup also fails, teardown reports both in a
  `BaseExceptionGroup` instead of replacing the body failure.
- The 300-second background-child case now measures bounded completion and,
  before the ordinary tracker teardown, inventories the run-log project and
  asserts that no labelled project container remains.
- An explicit negative external marker is authoritative over a selected test
  path. The guarded final command included `tests/docker/test_capture_docker.py`
  with `-m 'not docker'` and deselected all four Docker nodes without contacting
  Docker.

TDD and verification evidence:

- RED: collected scope `trafficlab-task9-fix1-red2-8437.scope` — `7 failed, 20
  passed`; the failures were the UDP interleaving/broadcast requirement,
  publication-schema lookup, cleanup aggregation/body preservation, and two
  path-plus-negation cases. Scope ended `inactive/dead` with no pytest
  descendant.
- GREEN: collected scope `trafficlab-task9-fix1-green-15878.scope` — `27 passed
  in 0.18s`; scope ended `inactive/dead` with no pytest descendant.
- Final guarded selection after the teardown hook was wired:
  `trafficlab-task9-fix1-hook-5244.scope` — `27 passed, 4 deselected in 0.06s`
  using `-n 0` and `-m 'not docker'`; scope ended `inactive/dead` with no pytest
  descendant.
- Focused Ruff format/check passed; strict Pyright reported `0 errors, 0
  warnings, 0 informations`; `git diff --check` passed.

Docker remains a truthful Class 4 environmental limitation: the executable is
absent, so no Docker image, container lifecycle, or Internet result was run or
claimed in this fix round. Phase 3 remains `(Current)` and every external
evidence/Done-when checkbox remains unchecked.

Fix commit: `5b883da fix(capture): harden Docker test lifecycle`
