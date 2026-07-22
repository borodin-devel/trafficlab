# Amendment 0001: Artifact I/O Namespace Stability

- **Status:** Approved
- **Owner:** Artifact I/O architecture owner
- **Decision date:** 2026-07-22

## Conflicting Sources

The [Artifact I/O SAD](../libs/artifact_io/SAD.md) requires publication
failures to retain diagnosable temporary state or its validated location. The
[Artifact I/O README](../libs/artifact_io/README.md) describes typed failures
with retained diagnostics, while [ART-ERR-001 and
ART-TST-001](../libs/artifact_io/SRS.md) require safe failure and injectable
boundaries.

The approved [Artifact I/O core
design](../../docs/superpowers/specs/2026-07-22-artifact-io-core-design.md)
and [implementation
plan](../../docs/superpowers/plans/2026-07-22-artifact-io-core.md) make that
diagnostic state concrete as immutable `retained_paths` and `orphan_path`
values. They require a pre-rename failure to retain private staging and a
post-rename failure to identify the orphan artifact.

Those requirements conflict when an in-process callback or concurrent actor
with the same effective user ID renames, unlinks, or rebinds the assigned
staging entry, attempt directory, output parent, or one of their ancestors. An
open directory descriptor pins the filesystem object but does not pin any
pathname. The object may remain accessible through the descriptor while no
stable absolute `Path` names it. Another path check only creates a later race
window, so the existing Path-only error API cannot guarantee an extant current
location against that actor.

## Decision

Artifact I/O publication has a namespace-stability precondition. From the
first publication boundary until `publish_file` or `publish_package` returns or
raises, callbacks and concurrent actors running with the publisher's effective
user ID shall not rename, unlink, or rebind:

- every library-assigned artifact, package-root, and status staging entry;
- the attempt directory or any component that binds its absolute path; or
- the artifact destination parent or any component that binds its absolute
  path; and
- after Artifact I/O atomically publishes them, the artifact destination entry
  and `<attempt>/artifact-status.toml` entry through the end of the call.

This precondition does not trust staged bytes or package contents. Writers and
validators may produce their owned bytes, and package callbacks may create only
the entries authorized by their callback contract. Artifact I/O must still
recheck file type, links, membership, content hashes, entry binding, and pinned
directory chains after callbacks and immediately around publication. Competing
creation of the artifact or status leaf remains in scope and must be handled by
atomic no-replace publication. The leaf-entry precondition begins only after
Artifact I/O wins that atomic publication; it does not exclude destination
races before publication.

Within the namespace-stability precondition, `retained_paths` and
`orphan_path` identify the exact assigned absolute locations and the normal
failure-state guarantees remain unchanged. If an actor violates the
precondition, Artifact I/O must not treat a detected binding change as success,
but Path evidence is only the immutable last-assigned logical location and is
not guaranteed to resolve to the moved or unlinked object.

Callbacks are therefore data and semantic extension points, not an in-process
sandbox for hostile namespace control. A caller that does not trust callback
code to honor this precondition must isolate that code in a separate process or
namespace and mediate its output before invoking Artifact I/O.

## Rationale

The decision preserves the explicit plan and Path-based failure API while
stating the capability boundary the operating system actually provides. It
keeps byte mutation, unexpected members, hard links, symlinks, replacement,
and destination races testable within stable assigned parents. It also avoids
claiming that descriptor pinning freezes the global namespace.

The library continues to use pinned descriptors and before/after identity
checks because they reject observable changes and prevent path traversal or
publication through an unvalidated descriptor. These checks are defense in
depth; they are not a substitute for the namespace-stability precondition.

## Scope and Consequences

This amendment applies to Artifact I/O producer transactions, their writer and
validator callbacks, manifest callbacks, and same-user concurrent actors. It
does not weaken detached-status parsing, status filesystem-envelope checks,
hash verification, atomic no-overwrite, status-last success, or the consumer's
requirement to reject observed mutation and replacement.

Implementations and tests shall:

- guarantee extant retained/orphan paths for ordinary failures and callbacks
  that honor namespace stability;
- preserve typed failure and refuse success when a binding change is detected;
- keep immediate parent/source revalidation around atomic publication; and
- avoid tests or documentation that promise recoverable Path evidence after an
  out-of-contract rename or unlink of the assigned roots.

Operator-controlled quarantine, movement, or deletion begins only after the
publication call finishes. Package publication inherits the same precondition.

## Alternatives Considered

1. **Descriptor- or capability-backed failure evidence.** This could identify
   an unlinked object, but it would replace the approved Path-only public API,
   introduce descriptor lifetime and privilege responsibilities, and require a
   broader consumer redesign. It is deferred rather than introduced implicitly.
2. **Resolve `/proc/self/fd` after failure.** The resolved name can race again,
   may be absent or marked deleted, depends on procfs, and becomes unusable when
   descriptors close. It does not provide the promised stable evidence.
3. **Add another pathname check or cooperative lock.** A same-user actor can
   move the entry after the check, and POSIX advisory locks do not prevent
   namespace operations.
4. **Rollback or quarantine automatically.** A second rename has the same race
   and failure modes and conflicts with the architecture's no-delete,
   no-resume, operator-owned quarantine rule.
5. **Treat arbitrary in-process callbacks as hostile.** Python callback code
   can mutate process and filesystem state beyond what this library can
   sandbox. Process or namespace isolation is the appropriate boundary when
   that threat model is required.

## Compatibility and Migration

There is no status-schema, artifact-format, or Python signature change.
Existing callers whose callbacks and same-user actors leave assigned staging
entries and ancestors in place, and leave published artifact and status
entries in place until the call ends, are compatible without modification.
Callback implementations that move or unlink those bindings during publication
must stop doing so or be isolated outside the publisher process. Callers and
operators must likewise coordinate concurrent actors with the publisher's
effective user ID so those bindings remain stable through return or raise.

Public documentation and acceptance evidence must mention the precondition
when describing retained staging and orphan paths. A future capability-backed
evidence API would require a separate amendment and explicit migration plan.
