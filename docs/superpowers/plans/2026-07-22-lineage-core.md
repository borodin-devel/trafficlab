# Lineage Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the complete Lineage roadmap stage: deterministic provenance,
exact-byte SHA-256 snapshots, detached hash domains, graph validation, and
manifest-first package verification.

**Architecture:** Immutable typed values and pure validators form the
functional core. A narrow Linux shell opens normalized absolute paths one
no-follow component at a time, hashes stable descriptor snapshots with bounded
reads, and returns reproducible identities without filesystem metadata.
Contract-owned parsers receive manifest bytes only after detached digest
verification.

**Tech Stack:** Python 3.12, standard-library `dataclasses`, `enum`, `hashlib`,
`os`, `pathlib`, pytest, Ruff, Pyright, uv, setuptools.

## Global Constraints

- Follow
  `docs/superpowers/specs/2026-07-22-lineage-core-design.md` and Lineage
  requirements `LIN-FR-001` through `LIN-FR-005`, `LIN-IF-001`,
  `LIN-NFR-001`, `LIN-ERR-001`, `LIN-SEC-001`, and `LIN-TST-001`.
- Do not edit immutable architecture prose. Only the Lineage and central
  `ROADMAP.md` files may change under `architecture/`.
- Use only the Python standard library in production Lineage code.
- Production targets Linux and Python `>=3.12,<3.13`.
- Public values are frozen, slotted dataclasses; public collections are
  immutable tuples; errors are typed and single-line.
- Exact-byte hashing uses lowercase SHA-256, reads no chunk larger than 1 MiB,
  follows no symlink component, and rejects unstable identity metadata.
- JSON/TOML parsing, required contract fields, status parsing, artifact
  publication, logging, and network sources remain outside Lineage.
- Every behavior change follows observed RED, minimal GREEN, refactor, focused
  verification, broader regression, diff review, and a focused commit.
- Run every repository command through the locked environment.

---

### Task 1: Typed Values and Canonical Provenance

**Files:**

- Create: `src/trafficlab/libs/__init__.py`
- Create: `src/trafficlab/libs/lineage/__init__.py`
- Create: `src/trafficlab/libs/lineage/errors.py`
- Create: `src/trafficlab/libs/lineage/values.py`
- Create: `tests/libs/lineage/test_values.py`

**Interfaces:**

- Produces: `Sha256Digest`, `PathKind`, `FileIdentity`, `NamedIdentity`,
  `ConfigurationIdentity`, `SeedIdentity`, `ProvenanceRecord`,
  `build_provenance`, `provenance_items`, and the complete public error
  hierarchy.
- Consumes: no runtime project code.
- Later tasks import path validators privately from `values.py` and all public
  types from the Lineage facade.

- [x] **Step 1: Write failing value and provenance tests**

Create `tests/libs/lineage/test_values.py` with focused tests equivalent to the
following complete behavior matrix:

```python
from itertools import permutations

import pytest

from trafficlab.libs.lineage import (
    ConfigurationIdentity,
    FileIdentity,
    InvalidDigestError,
    InvalidLineagePathError,
    InvalidProvenanceError,
    NamedIdentity,
    PathKind,
    SeedIdentity,
    Sha256Digest,
    UnsupportedLineageVersionError,
    build_provenance,
    provenance_items,
)

ZERO = "0" * 64
ONE = "1" * 64


@pytest.mark.unit
def test_sha256_digest_accepts_only_lowercase_hex() -> None:
    assert str(Sha256Digest(ZERO)) == ZERO
    for invalid in ("", "0" * 63, "0" * 65, "A" * 64, "g" * 64):
        with pytest.raises(InvalidDigestError):
            Sha256Digest(invalid)


@pytest.mark.unit
def test_file_identity_requires_canonical_path_for_kind() -> None:
    digest = Sha256Digest(ZERO)
    assert FileIdentity(PathKind.LOCAL, "nested/member.bin", digest).path == (
        "nested/member.bin"
    )
    assert FileIdentity(PathKind.EXTERNAL, "/data/source.pcapng", digest).path == (
        "/data/source.pcapng"
    )
    for kind, path in (
        (PathKind.LOCAL, "../escape"),
        (PathKind.LOCAL, "/absolute"),
        (PathKind.LOCAL, "member/"),
        (PathKind.EXTERNAL, "relative"),
        (PathKind.EXTERNAL, "/tmp/../escape"),
        (PathKind.EXTERNAL, "//ambiguous"),
    ):
        with pytest.raises(InvalidLineagePathError):
            FileIdentity(kind, path, digest)


@pytest.mark.unit
def test_build_provenance_is_permutation_invariant() -> None:
    paths = (
        FileIdentity(PathKind.LOCAL, "z.bin", Sha256Digest(ONE)),
        FileIdentity(PathKind.EXTERNAL, "/a.bin", Sha256Digest(ZERO)),
    )
    identities = (NamedIdentity("z", "2"), NamedIdentity("a", "1"))
    seeds = (SeedIdentity("z", 2), SeedIdentity("a", 1))
    configs = (
        ConfigurationIdentity("z", "cfg-z"),
        ConfigurationIdentity("a", "cfg-a"),
    )
    expected = build_provenance(
        paths=paths,
        implementations=identities,
        dependencies=identities,
        seeds=seeds,
        configurations=configs,
        parent_hashes=(Sha256Digest(ONE), Sha256Digest(ZERO)),
    )
    for path_order in permutations(paths):
        assert build_provenance(
            paths=path_order,
            implementations=reversed(identities),
            dependencies=reversed(identities),
            seeds=reversed(seeds),
            configurations=reversed(configs),
            parent_hashes=(Sha256Digest(ZERO), Sha256Digest(ONE)),
        ) == expected


@pytest.mark.unit
def test_provenance_items_have_fixed_serialization_neutral_order() -> None:
    record = build_provenance(
        paths=(FileIdentity(PathKind.LOCAL, "member", Sha256Digest(ZERO)),),
        implementations=(NamedIdentity("trafficlab", "0.1.0"),),
        seeds=(SeedIdentity("generator", 7),),
    )
    assert tuple(name for name, _ in provenance_items(record)) == (
        "schema_version",
        "paths",
        "implementations",
        "dependencies",
        "seeds",
        "configurations",
        "parent_hashes",
    )


@pytest.mark.unit
def test_provenance_rejects_invalid_version_and_duplicates() -> None:
    with pytest.raises(UnsupportedLineageVersionError):
        build_provenance(schema_version=2)
    duplicate = NamedIdentity("same", "1")
    with pytest.raises(InvalidProvenanceError):
        build_provenance(implementations=(duplicate, duplicate))
    with pytest.raises(InvalidProvenanceError):
        SeedIdentity("boolean", True)  # type: ignore[arg-type]
```

Add separate parametrized cases for empty/control-bearing names, versions,
configuration identities, and paths; duplicate local paths with different
digests; duplicate parent hashes; and immutability of returned tuples.

- [x] **Step 2: Run the test and observe RED**

Run:

```bash
uv run --locked python -m pytest -q tests/libs/lineage/test_values.py
```

Expected: collection fails because `trafficlab.libs.lineage` does not exist.
This is the correct missing-feature failure; no production Lineage package may
exist before this run.

- [x] **Step 3: Implement typed errors and canonical values**

Create `errors.py` with `LineageError(Exception)` and these direct subclasses:

```python
class LineageError(Exception):
    """Base class for deterministic lineage failures."""


class InvalidDigestError(LineageError):
    """A SHA-256 value is not canonical."""


class UnsupportedLineageVersionError(LineageError):
    """A lineage representation version is unsupported."""


class InvalidProvenanceError(LineageError):
    """Typed provenance values are invalid or ambiguous."""


class InvalidLineagePathError(LineageError):
    """A local or external lineage path is not canonical."""


class FileSnapshotError(LineageError):
    """A stable regular-file snapshot could not be opened."""


class FileChangedError(LineageError):
    """A path or file identity changed during its snapshot."""


class HashMismatchError(LineageError):
    """Exact file bytes do not match a declared SHA-256 value."""


class InvalidHashDomainError(LineageError):
    """A digest carrier overlaps its own hash domain."""


class MissingParentError(LineageError):
    """A lineage node refers to an unavailable parent."""


class LineageCycleError(LineageError):
    """A lineage graph contains a directed cycle."""


class ManifestValidationError(LineageError):
    """A package manifest cannot safely declare members."""
```

Implement `values.py` with these exact declarations and rules:

```python
CURRENT_LINEAGE_VERSION = 1

@dataclass(frozen=True, slots=True, order=True)
class Sha256Digest:
    value: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.value) is None:
            raise InvalidDigestError("sha256 must be 64 lowercase hexadecimal characters")

    def __str__(self) -> str:
        return self.value


class PathKind(StrEnum):
    LOCAL = "local"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class FileIdentity:
    kind: PathKind
    path: str
    sha256: Sha256Digest


@dataclass(frozen=True, slots=True, order=True)
class NamedIdentity:
    name: str
    version: str


@dataclass(frozen=True, slots=True, order=True)
class ConfigurationIdentity:
    name: str
    identity: str


@dataclass(frozen=True, slots=True, order=True)
class SeedIdentity:
    name: str
    value: int


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    schema_version: int
    paths: tuple[FileIdentity, ...]
    implementations: tuple[NamedIdentity, ...]
    dependencies: tuple[NamedIdentity, ...]
    seeds: tuple[SeedIdentity, ...]
    configurations: tuple[ConfigurationIdentity, ...]
    parent_hashes: tuple[Sha256Digest, ...]
```

Complete each `__post_init__` with deterministic validation. `_validate_text`
accepts only non-empty strings without C0 controls or DEL. Local paths must
equal `PurePosixPath(path).as_posix()`, be relative, contain no `.`/`..`
component, and have no trailing slash. External paths must begin with exactly
one `/`, equal `posixpath.normpath(path)`, and contain no controls. Reject an
unknown runtime `kind` rather than treating it as external.

Implement `build_provenance` with the signature in the design. Materialize
iterables once, reject duplicates before sorting, sort with explicit keys, and
return `ProvenanceRecord`. Implement `provenance_items` as the fixed field order
from the test, with nested tuple-of-pairs values such as:

```python
("paths", tuple(
    (("kind", item.kind.value), ("path", item.path), ("sha256", str(item.sha256)))
    for item in record.paths
))
```

Export only public names from `lineage/__init__.py`. Keep `libs/__init__.py`
limited to a package docstring.

- [x] **Step 4: Verify GREEN and static correctness**

Run:

```bash
uv run --locked python -m pytest -q tests/libs/lineage/test_values.py
uv run --locked ruff format --check src/trafficlab/libs tests/libs/lineage
uv run --locked ruff check src/trafficlab/libs tests/libs/lineage
uv run --locked pyright src/trafficlab/libs tests/libs/lineage
```

Expected: all value tests pass; Ruff and Pyright report no findings.

- [x] **Step 5: Review and commit Task 1**

Run the complete existing suite, inspect `git diff --check`, request an
independent spec-compliance and quality review, stage only the five Task 1
files, inspect `git diff --cached`, then commit:

```bash
git commit -m "feature(lineage): add canonical provenance values"
```

### Task 2: Stable Exact-Byte File Snapshots

**Files:**

- Create: `src/trafficlab/libs/lineage/hashing.py`
- Modify: `src/trafficlab/libs/lineage/__init__.py`
- Create: `tests/libs/lineage/test_hashing.py`

**Interfaces:**

- Consumes: `Sha256Digest`, `FileIdentity`, `PathKind`, private canonical path
  validators, and snapshot-related errors from Task 1.
- Produces: `DEFAULT_CHUNK_SIZE`, `MAX_CHUNK_SIZE`, `sha256_bytes`,
  `sha256_file`, `snapshot_local_file`, `snapshot_external_file`,
  `validate_local_file`, and `validate_external_file`.
- Produces privately for Task 4:
  `_read_verified_local_bytes(root, expected, max_bytes, chunk_size) -> bytes`.

- [x] **Step 1: Write failing hashing and path-boundary tests**

Create `test_hashing.py` with these public behaviors:

```python
@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
        (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    ],
)
def test_sha256_bytes_matches_published_vectors(payload: bytes, expected: str) -> None:
    assert str(sha256_bytes(payload)) == expected


@pytest.mark.unit
@pytest.mark.parametrize("chunk_size", [1, 2, 3, 64, 65_536, 1_048_576])
def test_sha256_file_is_chunk_boundary_invariant(tmp_path: Path, chunk_size: int) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"abc" * 100_003)
    assert sha256_file(source, chunk_size=chunk_size) == sha256_bytes(source.read_bytes())


@pytest.mark.unit
def test_local_snapshot_and_validation_detect_one_byte_mutation(tmp_path: Path) -> None:
    member = tmp_path / "nested" / "member.bin"
    member.parent.mkdir()
    member.write_bytes(b"before")
    identity = snapshot_local_file(tmp_path, "nested/member.bin")
    assert validate_local_file(tmp_path, identity) == identity
    member.write_bytes(b"beFore")
    with pytest.raises(HashMismatchError):
        validate_local_file(tmp_path, identity)


@pytest.mark.unit
def test_external_snapshot_records_normalized_absolute_path(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    identity = snapshot_external_file(source)
    assert identity == FileIdentity(PathKind.EXTERNAL, str(source), sha256_bytes(b"source"))


@pytest.mark.unit
def test_snapshot_rejects_symlink_ancestor_and_leaf(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "member").write_bytes(b"x")
    (tmp_path / "alias").symlink_to(actual, target_is_directory=True)
    (tmp_path / "leaf").symlink_to(actual / "member")
    with pytest.raises(FileSnapshotError):
        snapshot_local_file(tmp_path, "alias/member")
    with pytest.raises(FileSnapshotError):
        snapshot_local_file(tmp_path, "leaf")
```

Add parametrized tests for chunk sizes `0`, `-1`, and `1_048_577`; relative and
non-normalized external paths; traversal and non-normalized local paths;
missing files; directory/FIFO leaves; root symlinks; validation with the wrong
`PathKind`; and an external file changed after its snapshot.

For deterministic in-read mutation, monkeypatch private `_read_chunk`. On its
first call, read one chunk, replace or rewrite the target, then return that
chunk. Assert `FileChangedError`, not a mixed digest.

- [x] **Step 2: Run the hashing tests and observe RED**

Run:

```bash
uv run --locked python -m pytest -q tests/libs/lineage/test_hashing.py
```

Expected: collection fails because the hashing exports do not exist.

- [x] **Step 3: Implement stable descriptor snapshots**

In `hashing.py`, define:

```python
DEFAULT_CHUNK_SIZE = 65_536
MAX_CHUNK_SIZE = 1_048_576

@dataclass(frozen=True, slots=True)
class _PinnedEntry:
    parent_fd: int
    name: str
    fd: int
    initial: os.stat_result


def _read_chunk(fd: int, size: int) -> bytes:
    return os.read(fd, size)
```

Implement `_validate_chunk_size`, `_open_component`, `_identity_fingerprint`,
`_content_fingerprint`, `_assert_entry_unchanged`, and one private
`_read_stable_absolute` operation. The operation must:

1. validate a normalized absolute lexical path;
2. open `/` and every ancestor with
   `O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC`;
3. open the leaf with
   `O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC` and require
   `stat.S_ISREG`, so an untrusted FIFO or device cannot block the check;
4. record identity `(st_dev, st_ino, st_mode, st_nlink, st_uid, st_gid)` for
   every descriptor and additionally `(st_size, st_mtime_ns, st_ctime_ns)` for
   the leaf before reading;
5. hash repeated `_read_chunk(fd, chunk_size)` results and optionally retain no
   more than an explicit `max_bytes`;
6. compare the complete leaf fingerprint after reading;
7. compare every pinned descriptor's identity fingerprint with a fresh
   no-follow `os.stat` of its parent entry, without comparing directory size or
   timestamps changed by unrelated directory entries; and
8. close every descriptor in reverse order in `finally`.

Initial open/type failures raise `FileSnapshotError` from the `OSError`.
Post-open disappearance, replacement, or fingerprint changes raise
`FileChangedError`. Error messages use the already validated normalized path
and contain no artifact bytes.

Build the six public functions directly on that operation. For local paths,
validate the absolute root and relative identity separately, then combine them
lexically; never call `Path.resolve()`. `validate_*` takes a fresh snapshot and
raises `HashMismatchError` when the kind/path is valid but the digest differs.
`_read_verified_local_bytes` uses the same one-pass snapshot, raises
`ManifestValidationError` when `max_bytes <= 0` or the bound is exceeded, and
compares the digest before returning bytes.

- [x] **Step 4: Verify focused RED/GREEN guarantees**

Run:

```bash
uv run --locked python -m pytest -q tests/libs/lineage/test_values.py tests/libs/lineage/test_hashing.py
uv run --locked ruff format --check src/trafficlab/libs tests/libs/lineage
uv run --locked ruff check src/trafficlab/libs tests/libs/lineage
uv run --locked pyright src/trafficlab/libs tests/libs/lineage
```

Expected: all focused tests and static checks pass. Repeat mutation tests at
least 20 times to prove they are injection-deterministic rather than timing
dependent.

- [x] **Step 5: Review and commit Task 2**

Run the full suite, review descriptor cleanup and race handling independently,
stage only Task 2 files, inspect the cached diff, then commit:

```bash
git commit -m "feature(lineage): add stable file snapshots"
```

### Task 3: Detached Hash Domains and Lineage Graphs

**Files:**

- Create: `src/trafficlab/libs/lineage/domains.py`
- Create: `src/trafficlab/libs/lineage/graph.py`
- Modify: `src/trafficlab/libs/lineage/__init__.py`
- Create: `tests/libs/lineage/test_domains.py`
- Create: `tests/libs/lineage/test_graph.py`

**Interfaces:**

- Consumes: Task 1 digest/value validators and typed errors.
- Produces: `HashRegion`, `HashDomain`, `validate_hash_domain`, `LineageNode`,
  and `validate_lineage_graph`.
- Task 4 uses hash-domain validation for every manifest-declared member.

- [x] **Step 1: Write failing detached-domain tests**

Cover these exact cases:

```python
@pytest.mark.unit
def test_whole_file_self_hash_is_rejected() -> None:
    domain = HashDomain(
        carrier=HashRegion("manifest.json", "sha256"),
        covered=(HashRegion("manifest.json"),),
    )
    with pytest.raises(InvalidHashDomainError):
        validate_hash_domain(domain)


@pytest.mark.unit
def test_detached_status_domains_are_valid() -> None:
    manifest = validate_hash_domain(HashDomain(
        carrier=HashRegion("artifact-status.toml", "sha256"),
        covered=(HashRegion("manifest.json"),),
    ))
    artifact = validate_hash_domain(HashDomain(
        carrier=HashRegion("artifact-status.toml", "sha256"),
        covered=(HashRegion("artifact.pcapng"),),
    ))
    assert manifest.covered == (HashRegion("manifest.json"),)
    assert artifact.covered == (HashRegion("artifact.pcapng"),)


@pytest.mark.unit
def test_distinct_delimited_payload_may_share_resource() -> None:
    assert validate_hash_domain(HashDomain(
        carrier=HashRegion("record.json", "payload_sha256"),
        covered=(HashRegion("record.json", "payload"),),
    ))
```

Add empty/control identifiers, empty/duplicate covered regions, exact-region
self-reference, permutation ordering, and manifest-member domain cases.

- [x] **Step 2: Write failing deterministic graph tests**

Use short helpers that construct real 64-character digests. Cover one root,
one external root, unordered acyclic nodes, duplicate nodes, duplicate parents,
an external root that duplicates a local node, missing parents, self-cycles,
and multi-node cycles. Assert permutations return the same sorted tuple or the
same exception type/message.

- [x] **Step 3: Run both new files and observe RED**

Run:

```bash
uv run --locked python -m pytest -q tests/libs/lineage/test_domains.py tests/libs/lineage/test_graph.py
```

Expected: imports fail because domain and graph exports are absent.

- [x] **Step 4: Implement the pure domain core**

Use these declarations:

```python
@dataclass(frozen=True, slots=True)
class HashRegion:
    resource: str
    region: str | None = None


@dataclass(frozen=True, slots=True)
class HashDomain:
    carrier: HashRegion
    covered: tuple[HashRegion, ...]
```

Both identifier fields use Task 1's printable single-line validator.
`validate_hash_domain` rejects an empty or duplicate covered tuple, sorts by
`(resource, region is not None, region or "")`, and rejects overlap when the
carrier and covered resource match and either side denotes the whole resource
or the named regions match. Return a canonical `HashDomain`.

- [x] **Step 5: Implement deterministic graph validation**

Use:

```python
@dataclass(frozen=True, slots=True)
class LineageNode:
    digest: Sha256Digest
    parent_hashes: tuple[Sha256Digest, ...] = ()


def validate_lineage_graph(
    nodes: Iterable[LineageNode],
    *,
    external_roots: Iterable[Sha256Digest] = (),
) -> tuple[LineageNode, ...]:
    ordered = tuple(sorted(nodes, key=lambda node: node.digest.value))
    by_digest: dict[Sha256Digest, LineageNode] = {}
    for node in ordered:
        if node.digest in by_digest:
            raise InvalidProvenanceError(f"duplicate lineage node: {node.digest}")
        by_digest[node.digest] = node

    roots = tuple(sorted(external_roots))
    if len(set(roots)) != len(roots):
        raise InvalidProvenanceError("duplicate external lineage root")
    root_set = frozenset(roots)
    if root_set.intersection(by_digest):
        raise InvalidProvenanceError("external lineage root duplicates a local node")

    for node in ordered:
        for parent in node.parent_hashes:
            if parent == node.digest:
                raise LineageCycleError(f"lineage cycle at: {node.digest}")
            if parent not in by_digest and parent not in root_set:
                raise MissingParentError(f"missing lineage parent: {parent}")

    color: dict[Sha256Digest, int] = {}
    for start in by_digest:
        if color.get(start, 0) != 0:
            continue
        stack: list[tuple[Sha256Digest, bool]] = [(start, False)]
        while stack:
            digest, exiting = stack.pop()
            if exiting:
                color[digest] = 2
                continue
            state = color.get(digest, 0)
            if state == 2:
                continue
            if state == 1:
                raise LineageCycleError(f"lineage cycle at: {digest}")
            color[digest] = 1
            stack.append((digest, True))
            parents = by_digest[digest].parent_hashes
            for parent in reversed(parents):
                if parent in root_set:
                    continue
                if color.get(parent, 0) == 1:
                    raise LineageCycleError(f"lineage cycle at: {parent}")
                if color.get(parent, 0) == 0:
                    stack.append((parent, False))
    return ordered
```

`LineageNode` rejects duplicate parents and stores them in digest order.
Validation materializes iterables once, rejects duplicate node/root digests,
checks parents in sorted node/parent order, and raises `MissingParentError` for
the first unknown parent. A color-map depth-first traversal visits nodes and
parents in digest order, skips external roots, and raises `LineageCycleError`
at the first back edge. Never use set iteration to select an error.

- [x] **Step 6: Verify, review, and commit Task 3**

Run all Lineage tests plus Ruff and Pyright, then the complete test suite.
Request an independent review of domain overlap and deterministic graph error
selection. Stage the five Task 3 files, inspect the cached diff, and commit:

```bash
git commit -m "feature(lineage): validate domains and graphs"
```

### Task 4: Manifest-First Package Validation

**Files:**

- Create: `src/trafficlab/libs/lineage/package.py`
- Modify: `src/trafficlab/libs/lineage/__init__.py`
- Create: `tests/libs/lineage/test_package.py`

**Interfaces:**

- Consumes: Task 2's private verified-byte operation and local validator, Task
  3 hash domains, and Task 1 values/errors.
- Produces: public `validate_package_members` with the exact callback signature
  in the design.

- [x] **Step 1: Write failing manifest-sequencing tests**

Create fixtures that write `manifest.json` and two members under `tmp_path`.
The parser should accept the exact verified manifest bytes and return local
`FileIdentity` values. Cover this primary security assertion:

```python
@pytest.mark.unit
def test_bad_manifest_digest_prevents_parser_call(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b"member declarations")
    called = False

    def parser(_: bytes) -> tuple[FileIdentity, ...]:
        nonlocal called
        called = True
        return ()

    bad = FileIdentity(PathKind.LOCAL, "manifest.json", Sha256Digest("0" * 64))
    with pytest.raises(HashMismatchError):
        validate_package_members(
            tmp_path,
            bad,
            parser,
            max_manifest_bytes=1024,
        )
    assert called is False
```

Add a golden two-member result, manifest-size boundary, zero/negative bound,
wrong manifest kind, parser exception, generator exception, empty members,
duplicate member paths with equal/different digests, external members,
self-listed manifest, missing member, changed member, and canonical validation
order. Use monkeypatch around `validate_local_file` only for observing order;
all correctness cases use real files.

- [x] **Step 2: Run the package tests and observe RED**

Run:

```bash
uv run --locked python -m pytest -q tests/libs/lineage/test_package.py
```

Expected: import fails because `validate_package_members` is absent.

- [x] **Step 3: Implement manifest-first validation**

Implement this exact flow in `package.py`:

```python
def validate_package_members(
    root: Path,
    manifest: FileIdentity,
    parse_members: Callable[[bytes], Iterable[FileIdentity]],
    *,
    max_manifest_bytes: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[FileIdentity, ...]:
    if manifest.kind is not PathKind.LOCAL:
        raise ManifestValidationError("package manifest must use a local path")
    verified = _read_verified_local_bytes(
        root, manifest, max_bytes=max_manifest_bytes, chunk_size=chunk_size
    )
    try:
        members = tuple(parse_members(verified))
    except Exception as exc:
        raise ManifestValidationError("manifest parser rejected verified bytes") from exc
    if not members:
        raise ManifestValidationError("manifest must declare at least one member")
    # Validate local kinds, unique paths, and exclusion of manifest.path.
    ordered = tuple(sorted(members, key=lambda item: item.path))
    for member in ordered:
        validate_hash_domain(HashDomain(
            carrier=HashRegion(manifest.path, f"member:{member.path}:sha256"),
            covered=(HashRegion(member.path),),
        ))
        validate_local_file(root, member, chunk_size=chunk_size)
    return ordered
```

Do not catch `BaseException`. Materialize the parser's iterable inside the
`try` so delayed parse failures are wrapped. Validate all declarations before
hashing any member. Reject duplicate paths independently of digest. Preserve
the underlying exception as `__cause__` without placing parser text or bytes in
the stable public message.

- [x] **Step 4: Verify all Lineage acceptance criteria**

Run:

```bash
uv run --locked python -m pytest -q tests/libs/lineage
uv run --locked python -m pytest -q
uv run --locked ruff format --check src tests tools
uv run --locked ruff check src tests tools
uv run --locked pyright
```

Expected: all tests and static checks pass. Confirm tests explicitly trace
`LIN-AC-001`, `LIN-AC-002`, and `LIN-AC-003` in names or docstrings.

- [x] **Step 5: Review and commit Task 4**

Request an independent security/spec review focused on detached-manifest
sequencing, callback boundaries, member validation order, and exception
handling. Address findings test-first. Stage only the three Task 4 files,
inspect the cached diff, and commit:

```bash
git commit -m "feature(lineage): verify package member lineage"
```

### Task 5: Public Documentation, Roadmap Evidence, and Stage Verification

**Files:**

- Modify: `README.md`
- Modify: `architecture/libs/lineage/ROADMAP.md`
- Modify: `architecture/project/ROADMAP.md`
- Modify: `docs/superpowers/plans/2026-07-22-lineage-core.md`

**Interfaces:**

- Consumes: committed public API and final current test/build evidence.
- Produces: user-facing usage, `[DONE]` Lineage hierarchy, and central Stage 1
  progress of `[ 29%]` because two of seven equal-weight foundations are done.
- Changes no immutable architecture prose.

- [x] **Step 1: Add public usage documentation**

Add a concise `Lineage library` section to `README.md` showing imports and one
local snapshot/provenance example. State that paths are explicit and no-follow,
contract owners retain serialization, and package callers validate detached
status before passing the expected manifest identity.

- [x] **Step 2: Run the focused and aggregate gates for evidence**

Run:

```bash
uv run --locked python -m pytest -q tests/libs/lineage
uv run --locked python tools/quality.py all
```

Record the exact focused/full test counts and current wheel SHA-256. Repeat the
wheel build and require the same hash before claiming deterministic packaging.

- [x] **Step 3: Update mutable roadmaps**

In `architecture/libs/lineage/ROADMAP.md`, set Stage, Step, and Substep to
`[DONE]` and add evidence covering API files, known vectors, permutation/path/
mutation/domain/detached/graph/package tests, static gates, full-suite count,
coverage, and wheel reproducibility.

In `architecture/project/ROADMAP.md`, change the Stage 1, Step 1.1, and Substep
1.1.1 markers and evidence from `[ 14%]` to `[ 29%]`. State that Infrastructure
and Lineage are `[DONE]`, five component roadmaps remain `[PLAN]`, and the
equal-weight mean rounds from `200 / 7` to `29%`.

Update this plan's decision log with any major implementation issue and its
test-backed resolution. Do not change root `ROADMAP.md`; it is already a link
to the authoritative central roadmap.

- [x] **Step 4: Re-run validation after documentation changes**

Run:

```bash
uv run --locked python tools/quality.py all
git diff --check
git status --short
```

Expected: all aggregate gates pass and only the four Task 5 files are modified.

- [x] **Step 5: Review and commit Task 5**

Request an independent whole-stage review from the pre-Lineage base through
the current tree. Correct every actionable finding test-first, rerun the locked
aggregate gate, stage only reviewed documentation/roadmap files, inspect the
cached diff, and commit:

```bash
git commit -m "docs(roadmap): complete lineage core"
```

- [x] **Step 6: Reproducibility replay and integration**

Clone the committed implementation head into a fresh temporary directory with
no environment. Run:

```bash
uv sync --locked --all-groups
uv run --locked python tools/quality.py all
```

Compare the clean-clone wheel SHA-256 to the worktree build, record exact
evidence in the mutable Lineage roadmap if it differs from Step 2, and commit a
focused evidence refresh if necessary. Obtain final whole-range approval, then
use the finishing-a-development-branch skill's safe local fast-forward path.
Run the locked aggregate gate on integrated `main` before removing only the
clean Lineage worktree and fully merged local branch.

## Decision Log

- **Major — unresolved Python API:** Architecture intentionally deferred exact
  signatures to this stage. Repository evidence favored immutable typed values
  plus a narrow snapshot shell over generic dictionaries or contract-owned
  serialization in this library. The approved design is recorded in
  `docs/superpowers/specs/2026-07-22-lineage-core-design.md`.
- **Security boundary — package validation:** The parser callback is invoked
  only with bytes from a stable manifest snapshot whose digest already matches
  the detached expected identity. This makes LIN-FR-005 an enforced call order,
  not caller documentation.
- **Major — pytest module namespace:** The required Lineage
  `tests/libs/lineage/test_package.py` basename collided with Infrastructure's
  existing `test_package.py` under pytest prepend mode. A local
  `tests/libs/lineage/__init__.py` package marker isolates the mandated module
  name without a global pytest configuration change or an existing-file rename.
- **Security boundary — stable snapshots:** Boundary tests exposed filesystem
  encoding and Unicode line-separator diagnostics plus mutation, flag, and
  cleanup edge cases. The snapshot shell now wraps exact causes, emits
  deterministic single-line errors, preserves no-follow/nonblocking flags, and
  closes pinned descriptors under every tested failure.
- **Major — aggregate coverage closure:** The first aggregate gate exposed
  untested public and boundary paths. Meaningful vector, validation, and error
  tests now cover them with 100% statement and branch coverage, without an
  exclusion or threshold change.
- **Major — graph traversal invariant:** Exhaustive and stack/color invariant
  evidence proved that a popped-entry `state == 1` branch was unreachable.
  Reviewed commit `308c119` removed only that redundant check while retaining
  parent-edge back-edge detection and deterministic cycle diagnostics.
- **Security boundary — final whole-range review:** Test-first regressions for
  the two review findings now ensure retained manifest verification observes at
  most `max_bytes + 1` bytes before overflow while preserving stability and
  cleanup, and `HashRegion` rejects every remaining Unicode line break without
  broadening path semantics.
