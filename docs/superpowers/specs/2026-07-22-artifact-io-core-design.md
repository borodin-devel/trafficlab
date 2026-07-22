# Artifact I/O Core Design

**Date:** 2026-07-22
**Status:** Approved by the repository's autonomous-delivery contract
**Owner:** Trafficlab maintainers

## Purpose

Implement the Artifact I/O roadmap stage as the transaction boundary used by
every artifact-producing application. The library validates an explicit
attempt/output plan, stages either one file or one package outside its absent
destination, delegates component-owned byte production and semantic validation
through callbacks, publishes the complete artifact with an atomic no-replace
operation, and only then publishes the closed detached
`artifact-status.toml` commit marker.

This design resolves the Python API and Linux implementation details that the
Artifact I/O architecture intentionally leaves open. It does not define any
application artifact schema, parse a component manifest beyond a supplied
contract callback, select an output path, create an application attempt, delete
failed state, or infer success from destination presence.

## Source Requirements

The design implements `ART-FR-001` through `ART-FR-005`, `ART-IF-001` through
`ART-IF-003`, `ART-NFR-001` and `ART-NFR-002`, `ART-ERR-001` and
`ART-ERR-002`, `ART-SEC-001` and `ART-SEC-002`, `ART-TST-001`, and acceptance
criteria `ART-AC-001` through `ART-AC-003`. It also supplies the atomic,
detached, non-self-referential artifact identity required by `SYS-IF-002`,
`SYS-NFR-002`, and `SYS-NFR-003`.

The owner documents fix these decisions:

- The attempt already exists as an absolute, normalized, same-user mode-`0700`
  directory and contains immutable `launch.toml`.
- The exact artifact destination is distinct from the attempt and absent until
  one atomic publication operation.
- Packages publish an absent directory; single-file artifacts publish an
  absent regular file.
- A package manifest hashes every non-manifest member, including its copied
  `launch.toml`, and never lists or hashes `manifest.json` itself.
- The generic status envelope is canonical UTF-8 TOML 1.0, no larger than
  16,384 bytes, mode `0600`, single-link, same-user, and atomically published at
  exactly `<attempt>/artifact-status.toml`.
- Status publication never overwrites. An artifact without valid status is an
  orphan and cannot be resumed or overwritten automatically.
- Contract owners retain their byte schemas, manifest fields, required members,
  and semantic validators.
- Failed staging and artifact-to-status crash state remain diagnosable; the
  library performs no automatic deletion or quarantine.

## Considered API Approaches

### Give callbacks a writable staging directory

This is maximally flexible, but it lets component callbacks create unplanned
files, symlinks, hard links, or unclosed handles. Artifact I/O could detect
some mistakes afterward, but it could not own every write and close boundary as
`ART-TST-001` requires.

### Make Artifact I/O understand every manifest schema

A schema-aware publisher could construct and validate complete component
manifests itself. That contradicts contract ownership and would couple this
foundation to every application format.

### Library-owned handles plus contract callbacks

The selected approach gives each file producer one library-owned unbuffered
binary handle for its declared file only. The library opens, closes, hashes,
and revalidates each file. Component callbacks produce bytes and validate
semantics; package callbacks serialize and parse their owned manifest. This
preserves schema independence while making publication, membership, close,
hash, and atomicity boundaries enforceable and injectable.

## Package Structure

```text
src/trafficlab/libs/artifact_io/
  __init__.py       public facade only
  errors.py         typed plan, status, validation, and publication failures
  values.py         immutable plans, members, status values, canonical paths
  status.py         canonical TOML codec and secure detached-status validation
  filesystem.py     pinned descriptors, secure writes, and no-replace rename
  publication.py    file and package transaction shells
tests/libs/artifact_io/
  __init__.py
  test_values.py
  test_status.py
  test_file_publication.py
  test_package_publication.py
```

Pure validation and serialization remain separate from filesystem effects.
The facade exports only documented values, errors, builders, codecs,
publication operations, and detached validation.

## Public Values and Plans

The public status constants are:

```python
CURRENT_ARTIFACT_STATUS_VERSION = 1
MAX_ARTIFACT_STATUS_BYTES = 16_384
ARTIFACT_STATUS_NAME = "artifact-status.toml"
LAUNCH_NAME = "launch.toml"
MANIFEST_NAME = "manifest.json"
```

`ArtifactKind` is a `StrEnum` with exactly `PACKAGE = "package"` and
`FILE = "file"`.

### PublicationPlan

`PublicationPlan` is a frozen, slotted value containing:

```text
attempt_dir: Path
artifact_path: Path
artifact_kind: ArtifactKind
member_paths: tuple[str, ...]
```

It derives `launch_path = attempt_dir / "launch.toml"` and
`status_path = attempt_dir / "artifact-status.toml"`.

Both filesystem paths must be absolute normalized POSIX paths. They may not
contain C0/DEL controls, repeated leading separators, dot segments, or trailing
separators. The artifact path must be distinct from the attempt, launch, and
status paths. The shell later proves the attempt and artifact parent are pinned
non-symlink directories and rejects inode aliasing that lexical validation
cannot observe.

File plans have no members. Package plans contain canonical, sorted component
member paths. Each member is a non-empty normalized POSIX relative path with no
absolute form, `.`/`..`, trailing separator, controls, or prefix collision with
another file. `manifest.json` and `launch.toml` are reserved and cannot be
supplied by a component. Artifact I/O adds `launch.toml` automatically and
constructs `manifest.json` only after every other member is closed, validated,
and hashed.

Builders are:

```python
build_file_plan(attempt_dir: Path, artifact_path: Path) -> PublicationPlan

build_package_plan(
    attempt_dir: Path,
    artifact_path: Path,
    *,
    members: Iterable[str],
) -> PublicationPlan
```

They materialize iterables once, reject duplicates and structural collisions,
sort explicitly, and return direct-constructor-valid plans.

### PackageMember

`PackageMember` is frozen and slotted:

```text
path: str
write: Callable[[BinaryIO], None]
validate: Callable[[Path], None]
```

Its path follows the same member rules and cannot be reserved. Both callbacks
must be callable. The transaction requires exactly one `PackageMember` for
every component member in the plan, independent of iterable order.

The writer receives one unbuffered binary handle for only its staging file.
Artifact I/O owns the descriptor and closes it after the callback returns or
raises. The validator runs only after close and receives the private staging
path for that member. The shell rechecks type, membership, and hashes after
callbacks so callback-side mutation cannot become success.

### ArtifactStatus

`ArtifactStatus` is frozen and slotted with fields in serialized order:

```text
schema_version: int
state: str
artifact_kind: ArtifactKind
artifact_path: str
digest_path: str
sha256: Sha256Digest
launch_path: str
launch_sha256: Sha256Digest
```

Only schema version `1` and state `"published"` are valid. All paths are
normalized absolute POSIX paths. For a package, `digest_path` is exactly the
direct `manifest.json` child of `artifact_path`; for a file, both paths are
identical. Both digests use Lineage's canonical `Sha256Digest`. Direct
construction rejects wrong runtime types and noncanonical relationships.

## Canonical Status Codec

`render_artifact_status(status) -> bytes` emits exactly the eight keys from the
SAD in their declared order, one per line, with LF termination. Integer and
fixed enum/state values have one spelling. Strings use a deterministic TOML
basic-string escape routine compatible with `tomllib`; output is UTF-8 without
BOM or CR and must not exceed 16,384 bytes.

`parse_artifact_status(data: bytes) -> ArtifactStatus` requires bytes, the size
bound, strict UTF-8, no BOM, NUL, or CR, TOML 1.0, one flat table, exactly the
eight known keys, exact runtime types, and every `ArtifactStatus` invariant.
It then requires `render_artifact_status(parsed) == data`. Successful status is
therefore canonical, not merely TOML-equivalent.

The codec owns syntax only. Filesystem envelope validation occurs before its
bytes reach the parser.

## Secure Status Snapshot and Consumer Validation

`validate_publication(plan, *, chunk_size=65_536) -> ArtifactStatus` is the
generic consumer boundary.

It opens the absolute attempt path one component at a time with directory,
close-on-exec, and no-follow flags. The final attempt must be a same-user
mode-`0700` directory. It opens `artifact-status.toml` relative to that pinned
descriptor with nonblocking, close-on-exec, and no-follow flags. The status
must be a same-user mode-`0600` regular file with link count one.

The reader records descriptor and directory-entry identity/content metadata,
reads at most `MAX_ARTIFACT_STATUS_BYTES + 1` bytes in bounded chunks, rechecks
the descriptor, entry binding, and pinned directory chain, then closes every
descriptor in reverse order. A symlink, hard link, wrong owner/mode/type,
oversize file, replacement, mutation, disappearance, read error, or close error
raises a typed failure with the original `OSError` as cause where applicable.

After parsing the exact snapshot, the validator requires status path, launch
path, artifact path, and kind to match the explicit plan. It validates the
declared launch digest through Lineage. For a file it validates the published
file digest. For a package it validates the direct manifest digest and the
copied package `launch.toml` digest. Component contract validation remains the
consumer's next explicit step.

If the artifact destination exists without valid status, the operation raises
`OrphanArtifactError`. A missing artifact and missing status raises
`MissingArtifactStatusError`. A producer treats any pre-existing destination
or status as a non-overwritable conflict; it never resumes either state.

## Atomic No-Replace Primitive

Python's `os.rename` may overwrite a file or an empty directory, so a
check-then-rename sequence cannot satisfy the no-overwrite rule. The Linux
shell therefore calls `renameat2(..., RENAME_NOREPLACE)` through `ctypes`, using
already pinned source/destination parent descriptors and relative entry names.

The call is one atomic publication operation and returns `EEXIST` on a racing
destination. `EXDEV`, `ENOSYS`, `EINVAL`, or `EOPNOTSUPP` becomes a typed
unsupported-atomic-publication failure; the library never falls back to an
overwriting or multi-step visible copy. Tests exercise the real primitive on
the supported temporary filesystem and inject every error outcome.

Private staging entries are collision-resistant siblings of their final
destination on the same filesystem. Package staging directories use mode
`0700`; file and status staging files use mode `0600`. The shell pins the
parent and staging identity through validation and rename.

## File Publication Transaction

The public interface is:

```python
publish_file(
    plan: PublicationPlan,
    launch: FileIdentity,
    write: Callable[[BinaryIO], None],
    validate: Callable[[Path], None],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> ArtifactStatus
```

The plan must be a file plan. `launch` must be an external Lineage identity for
exactly `<attempt>/launch.toml`. The shell performs this order:

1. Pin and validate the attempt and destination parent; reject existing status
   or destination before creating staging.
2. Freshly validate the launch identity.
3. Create a private mode-`0600` sibling staging file.
4. Invoke the writer with its unbuffered handle; flush and close under library
   control. Any callback, write, flush, or close failure stops publication.
5. Invoke the component validator on the closed staged file, then require a
   stable regular single-link snapshot and compute its Lineage digest.
6. Atomically rename the staging file to the absent destination with
   `RENAME_NOREPLACE` and revalidate the published digest.
7. Freshly revalidate `launch.toml`, construct the detached file status, write
   and close a private status sibling, parse and validate that staged status,
   then atomically rename it to the absent fixed status path.
8. Validate the final status snapshot and return its `ArtifactStatus`.

The artifact contains no self-digest. The status carries the final file and
live immutable launch digests in detached, non-overlapping hash domains.

## Package Publication Transaction

The public interface is:

```python
publish_package(
    plan: PublicationPlan,
    launch: FileIdentity,
    members: Iterable[PackageMember],
    build_manifest: Callable[[tuple[FileIdentity, ...]], bytes],
    parse_manifest: Callable[[bytes], Iterable[FileIdentity]],
    validate_package: Callable[[Path], None],
    *,
    max_manifest_bytes: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> ArtifactStatus
```

The plan must be a package plan. Member specs are materialized once and must
match the planned component membership exactly. The remaining order is:

1. Perform the same pinned attempt/parent, absence, and launch checks as file
   publication, then create a private mode-`0700` sibling staging directory.
2. Create required subdirectories without following links. Open each component
   member in canonical path order, invoke its writer, close it, invoke its
   validator, and require a regular single-link file.
3. Stream-copy the immutable live `launch.toml` into the root package member in
   bounded chunks, checking its expected digest and stable identity before and
   after the copy.
4. Reject missing, extra, symlinked, hard-linked, nonregular, or structurally
   conflicting staging content. Snapshot every non-manifest member through
   Lineage and pass the canonical local identities to `build_manifest`.
5. Require returned manifest bytes and the explicit size limit, write and close
   `manifest.json`, and snapshot its identity.
6. Call Lineage's manifest-first package validator with `parse_manifest`.
   Require parsed local identities to equal the exact canonical computed
   membership; this rejects self-listing, omitted, extra, or substituted hashes.
7. Invoke the component package validator, then repeat exact membership,
   regular-file, digest, manifest, and launch checks after it returns.
8. Atomically rename the complete directory to the absent destination,
   revalidate the published manifest and launch copy, and publish detached
   package status through the same closed no-replace status transaction.

Artifact I/O never interprets the component's JSON fields. The builder and
parser callbacks own schema, lineage fields, and canonical JSON; the generic
transaction owns the exact member identity set and sequencing.

## Failure State and Typed Errors

`ArtifactIoError` is the public base. Specific errors distinguish invalid
plans/members, invalid status syntax or envelope, missing status, orphan state,
publication conflicts, callback/write/close failures, validation/hash
failures, and unsupported/failed atomic publication.

Publication exceptions expose immutable `retained_paths` and `orphan_path`
attributes where applicable. Before artifact rename, failure leaves status and
destination absent and retains the private staging entry. After artifact rename
but before valid status publication, failure reports the immutable artifact as
an orphan and retains any status staging file. No exception path automatically
unlinks, overwrites, resumes, or quarantines state.

Stable public messages are deterministic, single-line, and never include
artifact or manifest bytes. Callback and OS details remain chained causes.

## Test Strategy

All behavior changes follow observed RED, minimal GREEN, focused regression,
complete gate, diff inspection, independent review, and a focused commit.

- Pure tests cover path normalization, attempt/destination aliasing, member
  traversal/reserved/prefix collisions, immutable canonical plans, runtime type
  invariants, and exact golden status bytes.
- Status parser fixtures cover every unknown/missing/duplicate/wrong-type key,
  version/state/kind/path/digest relation, BOM/CR/NUL/UTF-8/size issue, and
  noncanonical equivalent representation.
- Status filesystem fixtures cover mode, owner, hard link, symlink, FIFO,
  directory, size, mutation, replacement, disappearance, read/close errors,
  and bounded read counts.
- File publication tests prove absent-until-rename visibility, callback order,
  close-before-validation, no-overwrite races, launch/artifact digest binding,
  and status-last success.
- Package fixtures cover unordered/nested members, launch copying, canonical
  hash inputs, manifest self-exclusion, exact membership, parser sequencing,
  component validation, mutations, extras, symlinks, and deterministic output.
- Injection tests stop at every plan, staging-create, write, flush, close,
  member/package validation, hash, artifact rename, status write/validate, and
  status rename boundary. They distinguish retained staging from the required
  artifact-to-status orphan crash window.
- Real Linux integration tests exercise `RENAME_NOREPLACE` on same-filesystem
  temporary files and directories and prove existing destinations survive.
- Focused tests, full tests, 100% statement/branch coverage, Ruff, Pyright,
  whitespace, architecture validation, and reproducible wheel builds are
  required before `[DONE]` evidence.

## Compatibility and Limits

This is the first Artifact I/O runtime API, so it changes no existing Python
contract. It targets Linux and Python 3.12. Atomic publication is supported only
where `renameat2(RENAME_NOREPLACE)` succeeds on a same-filesystem sibling; there
is intentionally no unsafe fallback.

The library does not guarantee crash durability across power loss because the
architecture requires atomic visibility, not a filesystem-specific `fsync`
protocol. It does not delete staging or orphans, inspect logs, define component
manifest fields, validate a component contract without its callback, create
attempt directories, choose destinations, or support cross-user/remote
publication.
