# Artifact I/O Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the complete Artifact I/O roadmap stage: immutable explicit
publication plans, canonical detached status, secure status consumption,
atomic no-replace single-file and package transactions, exact member/manifest
hashing, and diagnosable failure state.

**Architecture:** Pure frozen values and a canonical TOML codec form the
functional core. A Linux shell pins attempt/output paths, owns file handles,
uses bounded reads and writes, delegates component bytes/semantics through
callbacks, and publishes artifacts and status through
`renameat2(RENAME_NOREPLACE)`. Lineage supplies exact hashes and manifest-first
member verification; component contracts retain their schemas.

**Tech Stack:** Python 3.12, standard-library `ctypes`, `dataclasses`, `enum`,
`io`, `json`, `os`, `pathlib`, `secrets`, `stat`, `tomllib`; existing Lineage;
pytest, Ruff, Pyright, uv, setuptools.

## Global Constraints

- Follow
  `docs/superpowers/specs/2026-07-22-artifact-io-core-design.md` and Artifact
  I/O requirements `ART-FR-001` through `ART-FR-005`, `ART-IF-001` through
  `ART-IF-003`, `ART-NFR-001`/`002`, `ART-ERR-001`/`002`,
  `ART-SEC-001`/`002`, `ART-TST-001`, and `ART-AC-001` through `003`.
- Do not edit immutable architecture prose. Only the Artifact I/O and central
  `ROADMAP.md` files may change under `architecture/` in Task 6.
- Use only the standard library and committed Lineage API in production.
- Production targets Linux and Python `>=3.12,<3.13`.
- Public values are frozen, slotted dataclasses; public collections are tuples;
  public errors are typed and diagnostics deterministic and single-line.
- The library never chooses a destination, creates an attempt, overwrites,
  resumes, deletes, quarantines, or treats destination presence as success.
- Contract callbacks own bytes and semantics but never own publication handles,
  final rename, generic status, or generic membership/hash sequencing.
- Every changed behavior follows observed RED, minimal GREEN, focused and full
  verification, diff review, an independent review, and a focused commit.
- Run every repository command through the locked environment.

---

### Task 1: Immutable Plans and Canonical Status Codec

**Files:**

- Create: `src/trafficlab/libs/artifact_io/__init__.py`
- Create: `src/trafficlab/libs/artifact_io/errors.py`
- Create: `src/trafficlab/libs/artifact_io/values.py`
- Create: `src/trafficlab/libs/artifact_io/status.py`
- Create: `tests/libs/artifact_io/__init__.py`
- Create: `tests/libs/artifact_io/test_values.py`
- Create: `tests/libs/artifact_io/test_status.py`

**Interfaces:**

- Produces constants, `ArtifactKind`, `PublicationPlan`, `PackageMember`,
  `ArtifactStatus`, pure plan builders, status render/parse, and the complete
  typed error hierarchy.
- Consumes only public Lineage value types; no filesystem calls in Task 1.
- Later tasks extend `status.py`, add `filesystem.py`/`publication.py`, and
  extend facade exports without changing Task 1 contracts.

- [ ] **Step 1: Write failing plan/value tests**

Cover:

- file plan derivation of exact launch/status paths and empty members;
- package member iterable materialized once and sorted canonically;
- rejection of non-Path/wrong kind runtime values in direct constructors;
- empty/relative/noncanonical attempt and artifact paths;
- artifact equality with attempt, launch, or status;
- local member traversal, absolute form, dot/repeated/trailing separators,
  controls, duplicates, reserved `manifest.json`/`launch.toml`, and file-prefix
  collisions such as `a` plus `a/b`;
- direct plans requiring canonical member tuples and file/package category
  invariants;
- `PackageMember` path/callable validation and immutability.

Use real `Path` values under canonical absolute fixture roots; pure builders do
not require those paths to exist.

- [ ] **Step 2: Write failing status-codec tests**

Construct this exact golden value (using canonical test paths and digests) and
assert the complete byte string and terminal LF:

```toml
schema_version = 1
state = "published"
artifact_kind = "package"
artifact_path = "/absolute/attempt/artifact"
digest_path = "/absolute/attempt/artifact/manifest.json"
sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
launch_path = "/absolute/attempt/launch.toml"
launch_sha256 = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"
```

Add file-kind golden/round-trip and deterministic escaping cases. Reject:

- non-bytes, empty/oversized bytes, BOM, CR/CRLF, NUL, invalid UTF-8/TOML;
- unknown, missing, duplicate, nested, or reordered keys;
- Boolean/floating/string schema version, unsupported version/state/kind;
- wrong digest spelling/type and wrong runtime constructor fields;
- relative/noncanonical paths, package digest not direct manifest, and file
  digest path not equal to artifact path;
- TOML-equivalent but noncanonical whitespace, escaping, quoting, or key order.

Assert errors never repeat raw input bytes and remain one line.

- [ ] **Step 3: Run focused tests and observe RED**

Run:

```bash
uv run --locked python -m pytest -q \
  tests/libs/artifact_io/test_values.py \
  tests/libs/artifact_io/test_status.py
```

Expected: collection fails because `trafficlab.libs.artifact_io` is absent.

- [ ] **Step 4: Implement typed errors and pure values**

Create `ArtifactIoError` plus direct or narrowly grouped subclasses for:

```text
InvalidPublicationPlanError
InvalidArtifactStatusError
ArtifactStatusSecurityError
MissingArtifactStatusError
OrphanArtifactError
PublicationConflictError
ArtifactWriteError
ArtifactValidationError
AtomicPublicationError
UnsupportedAtomicPublicationError
```

Publication-related errors accept immutable retained/orphan path evidence
without changing normal `Exception.args` message behavior.

Implement constants and frozen/slotted values exactly as the design. Path
validation is lexical and pure. Reuse `FileIdentity(PathKind.LOCAL/EXTERNAL,
..., Sha256Digest("0" * 64))` only if that preserves the intended public error
types; otherwise implement small explicit POSIX validators without importing
Lineage private helpers.

`build_file_plan` and `build_package_plan` materialize once, reject before
sorting, and return canonical direct-constructor-valid plans. Prefix collisions
compare complete POSIX components, not string prefixes (`a` does not collide
with `ab`).

- [ ] **Step 5: Implement canonical status render and parse**

Use `tomllib` only for parsing. Render manually in fixed order. A small private
TOML basic-string encoder may use `json.dumps(..., ensure_ascii=True)` only
after tests prove every emitted escape is accepted by `tomllib` and round-trips
exactly. Reject non-flat mappings and require exact keys/types.

After constructing `ArtifactStatus`, compare rendered bytes to input to enforce
canonical representation. Validate detached hash domains with public Lineage
`HashDomain`/`HashRegion` so neither digest claims status bytes.

The facade exports only public Task 1 names.

- [ ] **Step 6: Verify, review, and commit Task 1**

Run:

```bash
uv run --locked python -m pytest -q \
  tests/libs/artifact_io/test_values.py \
  tests/libs/artifact_io/test_status.py
uv run --locked ruff format --check src tests tools
uv run --locked ruff check src tests tools
uv run --locked pyright src/trafficlab/libs/artifact_io tests/libs/artifact_io
uv run --locked python -m pytest -q
git diff --check
```

Request an independent spec/code-quality review of exact plan/status invariants,
canonical TOML, path/reserved/prefix handling, runtime types, and diagnostics.
Address findings test-first. Stage only Task 1 files, inspect the cached diff,
and commit:

```text
feature(artifact-io): add plans and status codec
```

### Task 2: Secure Detached Status Consumption

**Files:**

- Create: `src/trafficlab/libs/artifact_io/filesystem.py`
- Modify: `src/trafficlab/libs/artifact_io/status.py`
- Modify: `src/trafficlab/libs/artifact_io/__init__.py`
- Modify: `tests/libs/artifact_io/test_status.py`

**Interfaces:**

- Produces `validate_publication(plan, *, chunk_size=DEFAULT_CHUNK_SIZE)`.
- Privately produces pinned directory/status snapshot helpers reused by
  publication.
- Consumes Lineage public snapshot/validation functions after status schema and
  explicit path binding succeed.

- [ ] **Step 1: Write failing secure-envelope tests**

Create real attempt fixtures with mode `0700`, immutable `launch.toml`, and
file/package destinations. Write golden status bytes with mode `0600`.

Cover public success for file and package, including package copied launch.
Then cover:

- attempt relative/wrong mode/wrong owner/non-directory/symlink component;
- missing status with missing artifact versus orphan artifact;
- status symlink, hard link, directory, FIFO, wrong mode/owner/link count;
- status size at 16,384 and overflow at 16,385 with exact bounded read count;
- deterministic in-read mutation/replacement/disappearance;
- injected open/fstat/stat/read/close failures with cause preservation and no
  descriptor leak;
- status kind/path/launch binding mismatch against the explicit plan;
- missing/wrong-type/hash-mismatched artifact, manifest, launch, or copied
  package launch;
- single-line control-safe diagnostics for Unicode line separators and
  unencodable paths.

Tests may monkeypatch private I/O hooks only to make OS races/errors exact.
Correctness fixtures use real files and public behavior.

- [ ] **Step 2: Observe focused RED**

Run the new secure cases explicitly. Expected: import/attribute failure because
`validate_publication` and secure snapshot helpers do not exist.

- [ ] **Step 3: Implement pinned status snapshot**

In `filesystem.py`:

- validate chunk size and canonical absolute paths;
- open `/` and every attempt component descriptor-relative with
  `O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC`;
- require final attempt directory same effective UID and exact mode `0700`;
- open the status leaf relative to the pinned attempt with
  `O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC`;
- require regular/same-user/mode-`0600`/single-link metadata;
- read at most 16,385 bytes, record initial identity/content metadata, recheck
  descriptor plus directory binding and pinned chain, and close in reverse;
- convert OS failures to stable typed status/security errors with causes;
- expose narrow private hooks for every open/stat/read/close boundary.

Never load an oversized file beyond the first overflow byte. Never inspect file
content before the envelope passes.

- [ ] **Step 4: Implement explicit detached validation**

`validate_publication` first resolves missing/orphan state without following a
destination symlink, reads/parses exactly one status snapshot, and compares
every status path/kind to the plan before hashing declared targets.

Use public Lineage calls to validate launch and file/manifest hashes. For a
package additionally validate local `launch.toml` against
`status.launch_sha256`. Reject status/destination type mismatch and wrap target
validation failures as typed orphan/validation failures without losing causes.

Do not invoke any contract parser here; the orchestrator/consumer validates the
component contract after generic detached success.

- [ ] **Step 5: Verify, review, and commit Task 2**

Run focused status tests, all Artifact I/O tests, all Lineage tests, Ruff,
Pyright, and the full suite. Repeat mutation/replacement tests at least 20
times. Request independent security review of descriptor lifetime, no-follow
coverage, envelope-before-content ordering, bounded reads, metadata checks,
orphan classification, and causes. Address findings test-first and commit:

```text
feature(artifact-io): validate detached status
```

### Task 3: Atomic Single-File Publication

**Files:**

- Modify: `src/trafficlab/libs/artifact_io/filesystem.py`
- Create: `src/trafficlab/libs/artifact_io/publication.py`
- Modify: `src/trafficlab/libs/artifact_io/__init__.py`
- Create: `tests/libs/artifact_io/test_file_publication.py`

**Interfaces:**

- Produces `publish_file` with the exact design signature.
- Privately produces collision-resistant staging, library-owned writer handles,
  pinned-parent operations, and Linux atomic no-replace rename.
- Reuses Task 2's final status validator as the returned-success boundary.

- [ ] **Step 1: Write failing atomic primitive tests**

On the supported temporary filesystem, prove a private sibling file can be
renamed to an absent destination, and existing file/directory destinations are
not changed. Inject `EEXIST`, `EXDEV`, `ENOSYS`, `EINVAL`, and `EOPNOTSUPP` and
assert exact typed classification and retained source.

Observe RED because no no-replace primitive exists.

- [ ] **Step 2: Write failing successful file transaction test**

Use a real mode-`0700` attempt and immutable launch identity. Writer emits
known bytes and records that destination/status are absent during the callback.
Validator asserts it sees a closed, regular staging file and exact bytes.

Assert after success:

- the artifact appeared at the exact destination only after validation;
- status appeared last, is mode `0600`/single-link, and parses canonically;
- status digest equals a fresh Lineage snapshot of final bytes;
- launch path/digest match the immutable attempt record;
- `validate_publication(plan)` returns exactly the published status;
- the artifact contains no embedded/self digest assumption.

- [ ] **Step 3: Write failing pre-artifact failure matrix**

Inject and assert writer callback, underlying write, flush, close, component
validator, staging snapshot/hash, parent/staging revalidation, and artifact
rename failures. Every case must leave destination and status absent, retain
one diagnosable staging path, preserve the first relevant cause, and close all
owned descriptors. Existing destination/status and racing creation must survive
byte-for-byte and be classified without staging overwrite/resume.

- [ ] **Step 4: Implement Linux no-replace and staging shell**

Use `ctypes.CDLL(None, use_errno=True)` to bind `renameat2` with explicit
argument/restype declarations. Call it with pinned dirfds, encoded relative
names, and `RENAME_NOREPLACE = 1`. No fallback to `os.rename`, `os.replace`, a
visible copy, or check-then-rename is allowed.

Create sibling staging names from an injected private token hook with atomic
`O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC` mode `0600`. Give the callback an
unbuffered binary handle with library-owned descriptor lifetime. Check
staging-parent binding before rename.

- [ ] **Step 5: Implement file publication and status-last commit**

Follow the design's eight steps exactly. Validate the plan/callbacks/launch
before effects. Revalidate the launch after artifact rename. Construct status
only from fresh final identities. Write, close, envelope-validate, parse, and
path-bind a private status sibling before atomic no-replace status rename; then
call the same final public validator returned to consumers.

If artifact rename succeeded but any later operation fails, raise with
`orphan_path == plan.artifact_path`; never remove it.

- [ ] **Step 6: Verify, review, and commit Task 3**

Run file publication tests, the complete Artifact I/O suite, Lineage regressions,
Ruff, Pyright, and full tests. Repeat the no-overwrite race and status-last
ordering cases. Request independent security/spec review focused on real
atomicity, handle ownership/close paths, cause precedence, retained staging,
artifact-to-status orphan state, status prevalidation, and absence of cleanup.
Address findings test-first and commit:

```text
feature(artifact-io): publish single-file artifacts
```

### Task 4: Atomic Package Publication

**Files:**

- Modify: `src/trafficlab/libs/artifact_io/filesystem.py`
- Modify: `src/trafficlab/libs/artifact_io/publication.py`
- Modify: `src/trafficlab/libs/artifact_io/__init__.py`
- Create: `tests/libs/artifact_io/test_package_publication.py`

**Interfaces:**

- Produces `publish_package` with the exact design signature.
- Consumes public Lineage `snapshot_local_file`, `validate_local_file`,
  `validate_package_members`, domain values, and digests.
- Component callbacks own manifest JSON and package semantic validation.

- [ ] **Step 1: Write failing golden package transaction**

Create two component members, one nested, with input spec order reversed.
`build_manifest` receives canonical local `FileIdentity` values including copied
`launch.toml` but excluding `manifest.json`; serialize a small canonical JSON
fixture. `parse_manifest` validates that owned schema and returns identities.

Assert writers/validators run in path order, every handle is closed before its
validator, launch bytes/digest equal the live immutable record, manifest hashes
exactly all non-manifest members and never itself, package validator runs after
manifest-first generic validation, destination remains absent until one rename,
and detached status binds final manifest plus live launch.

Repeat permutations and require identical final file bytes, manifest bytes,
status bytes, callback order, and returned value (staging name excluded).

- [ ] **Step 2: Write failing membership/security matrix**

Cover:

- member specs missing, extra, duplicate, reserved, or inconsistent with plan;
- writer creates invalid bytes and member validator rejection;
- copied launch mutation/replacement/digest mismatch;
- manifest builder exception/non-bytes/oversize/self-entry/omission/extra member/
  wrong digest/wrong kind/duplicate path/traversal;
- delayed parser iterator exception and package validator exception;
- nested prefix/type conflict, missing/extra/symlink/hard-link/FIFO/directory
  content introduced by a callback or validator;
- member or manifest mutation after initial hash;
- artifact rename conflict and every post-rename status failure.

Pre-rename cases retain staging and no destination/status. Post-rename cases
retain an orphan package and no valid status. No failure deletes diagnostics.

- [ ] **Step 3: Implement secure nested member writing and launch copy**

Create planned subdirectories descriptor-relative in canonical order with mode
`0700`, no-follow, and exclusive structural checks. Open each planned file once
with mode `0600` and pass only its unbuffered handle. Recheck it as regular,
single-link, and bound to the planned entry after close/validation.

Copy `launch.toml` from the pinned attempt descriptor in bounded chunks while
computing the expected digest. Require stable source metadata and the exact
expected external identity before and after; validate the local copied member.

- [ ] **Step 4: Implement exact membership and manifest sequencing**

Walk staging without following symlinks and compare the exact regular-file set
to planned components plus launch (and later manifest). Directories are allowed
only when implied by planned paths. Sort every diagnostic selection.

Snapshot all non-manifest members through Lineage and invoke `build_manifest`
once. Enforce bytes and `max_manifest_bytes` before writing. Snapshot manifest,
call `validate_package_members` with the contract parser, and require its
canonical result equals the previously computed identities.

After the package validator, repeat membership, all member digests,
manifest-first validation, and launch checks. Then perform one directory
no-replace rename, revalidate published manifest/launch, and publish status last.

- [ ] **Step 5: Verify, review, and commit Task 4**

Run package tests, all Artifact I/O/Lineage tests, Ruff, Pyright, and full
tests. Repeat permutation, mutation, and artifact-to-status crash fixtures.
Request independent review focused on contract ownership, exact membership,
manifest self-exclusion, launch copy identity, callback ordering, nested path
security, no-overwrite directory atomicity, retained staging/orphans, and
determinism. Fix findings test-first and commit:

```text
feature(artifact-io): publish package artifacts
```

### Task 5: Acceptance, Failure-Injection, and Coverage Closure

**Files:**

- Modify only the Artifact I/O production/test files required by observed
  acceptance or coverage gaps.
- Do not change tool configuration, coverage thresholds, dependencies, or
  immutable architecture prose.

**Interfaces:**

- Consumes all committed public APIs and the aggregate quality gate.
- Produces complete ART requirement/acceptance traceability and exact 100%
  statement/branch coverage through meaningful behavior.

- [ ] **Step 1: Run exact aggregate gate and map every gap**

Run:

```bash
uv run --locked python tools/quality.py all
```

Map each failure or uncovered statement/branch to an external invariant,
failure boundary, cleanup path, or defensive condition. Do not add exclusions,
`pragma: no cover`, artificial local-state mutation, or mock-only assertions.

If a branch is genuinely unreachable, prove the invariant and correct the
production structure in a focused test-backed bugfix rather than manufacturing
coverage.

- [ ] **Step 2: Complete failure-point and acceptance matrix**

Use requirement-named tests/docstrings to trace `ART-AC-001`, `ART-AC-002`, and
`ART-AC-003`. Ensure every write, validation, hash, close, artifact publication,
status validation, and status publication boundary has a deterministic
injection. Add explicit self-hash, permission, orphan, crash-window,
same-filesystem, no-replace, bounded-copy/read, and reproducibility fixtures.

Run mutation/race cases repeatedly. Verify retained paths exist and are scoped,
but do not delete them within production behavior.

- [ ] **Step 3: Achieve exact aggregate GREEN**

Iterate focused tests, then require:

```bash
uv run --locked python tools/quality.py coverage
uv run --locked python tools/quality.py all
git diff --check
```

Coverage must report 100% statements and branches for the complete package.
Request a whole-implementation security/spec review from the pre-Artifact-I/O
base. Correct all Critical/Important findings test-first, rerun the full gate,
and commit each coherent correction with `bugfix`, or a tests-only expansion as:

```text
test(artifact-io): close boundary coverage
```

### Task 6: Public Documentation, Roadmap Evidence, and Integration

**Files:**

- Modify: `README.md`
- Modify: `architecture/libs/artifact_io/ROADMAP.md`
- Modify: `architecture/project/ROADMAP.md`
- Modify: `docs/superpowers/plans/2026-07-22-artifact-io-core.md`

**Interfaces:**

- Consumes the final reviewed API and exact current test/build evidence.
- Produces user-facing examples, a `[DONE]` Artifact I/O hierarchy, and central
  Stage 1 progress `[ 43%]` because Infrastructure, Lineage, and Artifact I/O
  are three of seven equal-weight foundations.

- [ ] **Step 1: Add public usage documentation**

Add a concise README section showing one single-file plan/publication/validation
flow and pointing package callers to component-owned manifest builder/parser
callbacks. Document no-overwrite, retained staging/orphan behavior, Linux
`RENAME_NOREPLACE`, status-last success, and the requirement to quarantine
failures explicitly before retry.

- [ ] **Step 2: Gather final focused and aggregate evidence**

Run focused Artifact I/O tests and exact aggregate `quality.py all`. Record
exact counts, coverage totals, all static/docs gates, architecture-file count,
and wheel SHA-256. Repeat the build and require byte-identical wheel hashes.

- [ ] **Step 3: Update mutable roadmaps and decision log**

Mark the Artifact I/O Stage/Step/Substep `[DONE]` with evidence for plans,
golden statuses, secure envelopes, file/package transactions, failure injection,
atomic no-replace, orphan/crash states, coverage, and reproducibility.

Change only central Stage 1/Step 1.1/Substep 1.1.1 markers/evidence from `[ 29%]`
to `[ 43%]`: Infrastructure, Lineage, and Artifact I/O are `[DONE]`; four
linked foundations remain `[PLAN]`; `300 / 7` rounds to `43%`.

Check completed plan steps and record every consequential implementation/review
decision. Do not edit root `ROADMAP.md`.

- [ ] **Step 4: Verify, review, and commit documentation**

Run exact aggregate `quality.py all`, `git diff --check`, and inspect status.
Request independent review of public examples, evidence arithmetic, mutable-only
architecture scope, and exact counts/hashes. Stage only the four Task 6 files
and commit:

```text
docs(roadmap): complete artifact io
```

- [ ] **Step 5: Clean-clone replay and safe integration**

Clone the committed implementation head to a fresh temporary directory without
an environment. Run locked sync and exact aggregate `quality.py all`; require
the same wheel hash. Obtain final whole-range approval.

Fast-forward clean `main`, run the locked aggregate gate on integrated main,
record truthful integration evidence, and commit a focused evidence refresh if
needed. Remove only the clean merged Artifact I/O worktree/branch and disposable
verification clones. Never touch unrelated worktrees.

## Decision Log

- **Major — unresolved Python API:** Architecture fixes the transaction and
  status protocol but intentionally defers signatures. Repository evidence
  favors immutable explicit plans, library-owned per-file handles, and
  component-owned manifest callbacks over staging-directory callbacks or a
  schema-aware generic library. The design is recorded in
  `docs/superpowers/specs/2026-07-22-artifact-io-core-design.md`.
- **Security boundary — atomic no-overwrite:** Plain `os.rename` can overwrite
  an existing file or empty directory. The design requires Linux
  `renameat2(RENAME_NOREPLACE)` through pinned dirfds and has no unsafe
  fallback.
- **Failure semantics — retained diagnostics:** Pre-rename failure retains
  private staging; post-artifact/pre-status failure retains an orphan. Neither
  condition is automatically deleted or resumed, matching operator-controlled
  quarantine ownership.
