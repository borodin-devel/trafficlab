# Lineage Core Design

**Date:** 2026-07-22
**Status:** Approved by the repository's autonomous-delivery contract
**Owner:** Trafficlab maintainers

## Purpose

Implement the first Lineage roadmap stage as the reusable foundation for
artifact I/O and every later contract. The library computes exact-byte SHA-256
identities, builds deterministic versioned provenance values, verifies local
and external file identities, enforces detached hash domains, and validates
lineage graphs without taking ownership of a contract's JSON or TOML schema.

This design resolves only the Python API intentionally deferred by the Lineage
architecture. It does not change the approved architecture, define a command,
publish artifacts, or select fields for a component-owned contract.

## Source Requirements

The design implements `LIN-FR-001` through `LIN-FR-005`, `LIN-IF-001`,
`LIN-NFR-001`, `LIN-ERR-001`, `LIN-SEC-001`, `LIN-TST-001`, and acceptance
criteria `LIN-AC-001` through `LIN-AC-003`. System requirements additionally
require deterministic ordering, non-self-referential SHA-256 domains, source
lineage, and detached validation status.

The following owner decisions constrain the implementation:

- SHA-256 covers exact bytes and is lowercase hexadecimal.
- File hashing uses bounded reads and detects identity changes during a read.
- Relative lineage paths cannot traverse or escape their assigned root.
- Package manifests hash every non-manifest member, never themselves; detached
  status carries the final manifest digest.
- A manifest digest is verified before its bytes are trusted to declare member
  lineage.
- Contract owners choose JSON or TOML, required fields, and serialization
  details.
- The functional core has no file, clock, process, network, random, terminal,
  or privilege side effects.

## Considered Approaches

### Generic dictionaries and utility functions

A dictionary-only interface would allow each contract to supply any shape. It
would also make version checks, duplicate detection, canonical ordering, hash
domains, and graph validation conventions rather than enforceable behavior.
That is too weak for a shared security and reproducibility boundary.

### Contract-specific serializers and validators

Lineage could own canonical JSON/TOML schemas for packages and status files.
This would make immediate consumers easy to implement, but it would duplicate
and contradict contract ownership. It would also couple this foundational
library to formats that the architecture explicitly leaves to component
owners.

### Typed core plus narrow filesystem shell

The selected approach uses immutable typed values and pure validators for
canonical provenance, hash domains, and graphs. A small Linux filesystem shell
performs pinned no-follow snapshots and feeds exact bytes or digests into that
core. A callback-based package operation verifies a manifest snapshot before
allowing a contract parser to obtain member declarations. This approach is
strict where the architecture is strict while remaining serialization-neutral.

## Package Structure

```text
src/trafficlab/libs/
  __init__.py
  lineage/
    __init__.py       public API only
    errors.py         typed deterministic failures
    values.py         immutable digests, identities, provenance, canonical form
    hashing.py        secure file snapshots and exact-byte SHA-256
    domains.py        full-file and delimited-payload hash-domain validation
    graph.py          missing-parent and cycle validation
    package.py        manifest-first package member validation
tests/libs/lineage/
  test_values.py
  test_hashing.py
  test_domains.py
  test_graph.py
  test_package.py
```

Each production module has one responsibility. `__init__.py` is a deliberate
facade so later contract code does not depend on private helpers.

## Public Value Model

All public values are frozen, slotted dataclasses. Constructors validate their
own invariants and never silently normalize invalid input.

### Digests and file identities

`Sha256Digest(value: str)` accepts exactly 64 lowercase hexadecimal characters.
It is ordered by `value` and string conversion returns the digest text.

`PathKind` has exactly `LOCAL` and `EXTERNAL` values. `FileIdentity` contains:

```text
kind: PathKind
path: str
sha256: Sha256Digest
```

A local path is a normalized non-empty POSIX relative path. An external path is
a normalized absolute POSIX path. Local identities are interpreted only with
an explicit root; external identities retain their explicit normalized path.

### Provenance values

`NamedIdentity(name: str, version: str)` records implementation and dependency
identity. `ConfigurationIdentity(name: str, identity: str)` records a
component-owned configuration identity without assuming it is a file digest.
`SeedIdentity(name: str, value: int)` records explicit integer seeds and rejects
booleans.

`ProvenanceRecord` contains these fields in this order:

```text
schema_version
paths
implementations
dependencies
seeds
configurations
parent_hashes
```

Callers construct it through this canonical builder:

```python
build_provenance(
    *,
    schema_version: int = 1,
    paths: Iterable[FileIdentity] = (),
    implementations: Iterable[NamedIdentity] = (),
    dependencies: Iterable[NamedIdentity] = (),
    seeds: Iterable[SeedIdentity] = (),
    configurations: Iterable[ConfigurationIdentity] = (),
    parent_hashes: Iterable[Sha256Digest] = (),
) -> ProvenanceRecord
```

Version 1 is the only supported lineage version. Empty categories are valid
because contract owners decide which fields they require. The builder rejects
duplicate path identities, duplicate names within a category, and duplicate
parent hashes. It returns tuples sorted by path kind/path, name, integer seed,
or digest as applicable.

`provenance_items(record)` returns an immutable tuple of key/value pairs in the
field order above. Nested records are likewise represented by ordered tuples of
pairs. This is the canonical serialization-neutral representation; a contract
owner converts it to its owned JSON or TOML shape and applies its own byte-level
canonical serializer.

## Hashing and Path Snapshots

The public hashing shell exposes:

```python
sha256_bytes(data: bytes) -> Sha256Digest
sha256_file(path: Path, *, chunk_size: int = 65_536) -> Sha256Digest
snapshot_local_file(root: Path, relative_path: str, *, chunk_size: int = 65_536) -> FileIdentity
snapshot_external_file(path: Path, *, chunk_size: int = 65_536) -> FileIdentity
validate_local_file(root: Path, expected: FileIdentity, *, chunk_size: int = 65_536) -> FileIdentity
validate_external_file(expected: FileIdentity, *, chunk_size: int = 65_536) -> FileIdentity
```

`sha256_file` and external operations require an absolute normalized path;
local operations require an absolute normalized root and a separately
validated relative path. This avoids hidden current-directory resolution.
Chunk size is positive and capped at 1 MiB so every caller retains a bounded
read guarantee.

The Linux shell opens `/`, then every directory component, then the final file
through descriptor-relative calls with close-on-exec and no-follow flags. The
leaf is also opened nonblocking so a FIFO or device cannot stall the type
check. It requires directories for ancestors and a regular file for the leaf.
The shell pins each descriptor, records stable identity metadata, hashes with
descriptor-based reads, compares file metadata before and after the read, and
rechecks every directory entry against its pinned descriptor before closing.
Replacement, symlinks, mutation, disappearance, or type changes therefore fail
instead of being silently followed.

Directory rechecks compare identity, type, ownership, mode, and link count but
not timestamps or size, so an unrelated entry change in a pinned directory
does not invalidate a stable file snapshot. The leaf additionally compares
size, modification time, and change time to detect content mutation.

The serialized `FileIdentity` intentionally excludes device, inode, owner,
mode, and timestamps because those values are used only to prove a stable read;
they are not reproducible artifact identity.

Validation takes a fresh stable snapshot and compares its exact-byte digest to
the expected identity. A one-byte change therefore raises `HashMismatchError`;
a change during the read raises `FileChangedError`.

## Hash Domains

`HashRegion(resource: str, region: str | None)` identifies either a whole
resource (`region is None`) or an explicitly delimited region. `HashDomain`
contains one carrier region and one or more covered regions.

`validate_hash_domain(domain)` rejects a claim when the carrier lies inside a
covered region:

- a whole-file domain covers every field in the same resource;
- a delimited domain covers the same named region in the same resource;
- distinct named regions in one resource do not overlap;
- resources with different identities do not overlap.

Thus a manifest cannot carry a digest of its complete bytes, and an artifact
cannot carry its own complete-file digest. A digest in detached status may
cover the manifest or single-file artifact. A field may carry the digest of a
distinct explicitly delimited payload in the same file.

Resource and region identifiers are non-empty, single-line strings. The caller
must identify the actual carrier and domain; a component contract remains
responsible for mapping its fields to these generic regions.

## Graph Validation

`LineageNode(digest, parent_hashes)` is immutable and canonicalizes its parents.
`validate_lineage_graph(nodes, *, external_roots=())` returns nodes sorted by
digest after proving:

- node digests are unique;
- external roots are unique and do not duplicate a supplied local node;
- every parent is another supplied node or an explicitly trusted external root;
- a node is not its own parent;
- the directed parent graph is acyclic.

Traversal and error selection use digest order, so equivalent unordered inputs
produce the same result or the same typed failure. External roots allow a
bounded local graph to refer to already validated upstream artifact identities
without pretending those artifacts are local nodes.

## Manifest-First Package Validation

`validate_package_members` has this interface:

```python
validate_package_members(
    root: Path,
    manifest: FileIdentity,
    parse_members: Callable[[bytes], Iterable[FileIdentity]],
    *,
    max_manifest_bytes: int,
    chunk_size: int = 65_536,
) -> tuple[FileIdentity, ...]
```

It requires a local manifest identity and performs these steps in order:

1. Read one bounded, stable manifest snapshot and verify its SHA-256 against
   the digest supplied from already validated detached status.
2. Only after that comparison succeeds, invoke the contract-owned parser with
   the exact verified manifest bytes.
3. Materialize and canonicalize the returned local member identities; reject
   duplicates, an empty declaration, and any declaration of the manifest
   itself.
4. Validate a manifest-carried member hash domain for each member.
5. Stream and verify members in canonical relative-path order.

The callback is never called for a bad detached manifest digest. The library
does not interpret JSON/TOML or decide which package members are required.

## Errors

`LineageError` is the public base exception. Specific subclasses distinguish:

- `InvalidDigestError`
- `UnsupportedLineageVersionError`
- `InvalidProvenanceError`
- `InvalidLineagePathError`
- `FileSnapshotError`
- `FileChangedError`
- `HashMismatchError`
- `InvalidHashDomainError`
- `MissingParentError`
- `LineageCycleError`
- `ManifestValidationError`

Messages are stable, deterministic, and single-line. Filesystem exceptions are
wrapped with the original exception as their cause. Errors may identify a
normalized path or digest but never include artifact bytes.

## Test Strategy

Tests use real public behavior and temporary directories. Production code is
written only after the relevant test has failed for the expected reason.

- Published SHA-256 vectors cover empty bytes and `abc`; multi-chunk file
  fixtures prove exact-byte behavior for every supported chunk boundary.
- Permutations of provenance categories and graph nodes prove deterministic
  canonical ordering without adding a property-test dependency.
- Path fixtures cover absolute/relative confusion, dot segments, traversal,
  root escape, symlink ancestors and leaves, non-regular leaves, replacement,
  and missing files.
- Mutation fixtures inject a deterministic change after an initial read and
  prove that no digest is returned for an unstable snapshot.
- Golden local and external snapshots pass; one-byte mutations and changed
  external sources raise typed failures.
- Hash-domain fixtures cover package members, detached manifest and file
  status, forbidden manifest/artifact self-hashes, and a valid delimited
  payload digest.
- Graph fixtures cover roots, external roots, missing parents, duplicates,
  self-cycles, and multi-node cycles.
- Package tests prove the parser is not called before manifest digest
  validation and that changed, missing, duplicate, or self-listed members fail.
- Focused tests, the complete suite, Ruff, Pyright, architecture validation,
  whitespace validation, coverage, and wheel build must pass before completion.

## Delivery Substages

1. Add typed errors, digest/identity values, and canonical provenance builders.
2. Add secure bounded hashing and local/external snapshots.
3. Add detached hash-domain and graph validation.
4. Add manifest-first package member validation.
5. Update public documentation and the Lineage and central roadmaps with exact
   verification evidence.

Each substage has an observed red test, a focused verification run, a broader
regression run, an independent diff review, and a focused commit.

## Compatibility and Limits

This is the first runtime Lineage API, so it changes no existing public Python
contract. The library supports Linux and Python 3.12 as the repository already
requires. It does not support symlinked path components, hidden current-
directory lookup, unbounded manifest reads, serialization discovery, artifact
publication, status parsing, logging, or network identities.

Later contract implementations may add adapters that parse their owned
formats into these values. Any future lineage schema version must add an
explicit parser/builder path; version 1 behavior will not silently reinterpret
new fields.
