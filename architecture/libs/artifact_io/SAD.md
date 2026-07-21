# Artifact I/O Software Architecture Document

## Context, Goals, and Boundaries

Applications need one publication mechanism that prevents partial success.
The library stages files privately, invokes producer-owned validators, computes
lineage, and renames the complete file or package atomically. It owns the
generic detached publication status but does not define application formats,
choose output paths, or delete prior artifacts.

An application attempt directory already exists and retains live
`launch.toml`, logs, child diagnostics, and references to any retained failed
staging. Its exact successful artifact destination is a distinct path that does
not exist before publication. Package producers publish an absent directory;
single-file producers publish an absent file.

## Structure and Data Flow

A pure plan validates attempt/destination separation, relative package names,
and expected membership. A filesystem shell creates a private same-filesystem
sibling, writes through injected callbacks, closes all handles, validates
content, computes hashes, and performs one atomic rename to the absent
destination. Publication never overwrites an existing artifact.

For a package, canonical `manifest.json` lists the schema, lineage, exact
membership, and SHA-256 of every non-manifest member. It neither lists nor
hashes itself. After manifest serialization, its SHA-256 is placed in the
external status envelope. A listed frozen `launch.toml` is an ordinary hashed
member.

For a single file, the artifact does not contain its own digest. The external
status envelope contains the SHA-256 of the final file and of the immutable
attempt `launch.toml` that accompanies it.

## Successful Status Envelope

`artifact-status.toml` is the commit marker for successful publication. Its
path is exactly `<attempt>/artifact-status.toml`, absent before commit. It is
canonical UTF-8 TOML 1.0 with LF endings, no unknown keys, and this key order:

```toml
schema_version = 1
state = "published"
artifact_kind = "package"
artifact_path = "/absolute/attempt/artifact"
digest_path = "/absolute/attempt/artifact/manifest.json"
sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
launch_path = "/absolute/attempt/launch.toml"
launch_sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
```

`artifact_kind` is exactly `package` or `file`. For `package`,
`artifact_path` names the published directory and `digest_path` names its
direct `manifest.json`. For `file`, both paths name the same regular file. All
paths are normalized absolute paths validated against the caller's assigned
attempt/output plan. Both digests are exactly 64 lowercase hexadecimal
characters, and `state` has no other successful value.

For a package, `launch_sha256` also equals the manifest digest for the frozen
package `launch.toml`. For a file, it binds the external immutable attempt
record. The status file is at most 16,384 bytes, a same-user mode-`0600`
regular single-link file, and never a symlink.

The producer atomically publishes status from a private same-filesystem sibling
only after artifact and launch validation. It never overwrites an existing
status. The status never hashes itself. A destination without a valid status is
an orphan, not success: consumers reject it, and producers neither overwrite
nor resume it automatically. Diagnosis or operator-controlled quarantine must
use a new path before retry.

## Errors, Logging, Performance, and Security

Any write, close, validation, hash, or publication failure leaves no successful
status and retains diagnosable temporary state, or its validated location,
according to caller policy.
Paths reject traversal, absolute package members, destination aliasing, and
managed output escape. Status consumers take one bounded no-follow snapshot,
compare identity metadata before and after reading, validate the closed schema,
and then hash the declared launch and digest targets. Hashing streams bounded
chunks; logging records summaries, never artifact bytes.

## Testing, Decisions, Risks, and Limits

Unit tests cover plans, hash domains, status schema, and path validation;
integration tests inject failures at every publication boundary, including the
artifact-to-status crash window. Atomicity is guaranteed only on a documented
supported filesystem and same-filesystem final operation. Abandoned staging or
orphan destinations remain diagnosable and require explicit quarantine; they
are never inferred as success.
