# Back Up System Configuration Software Architecture Document

## Role

`scripts/backup_system_configuration.sh` is the conditional safeguard used
only when an approved setup plan must change durable system configuration.

## Authority

The setup plan classifies every explicitly named durable value as `public` or
`protected` before the script reads it. The script may need manual operator
authority to read a protected value, but it performs no system mutation.

## Effects

Before setup changes a public durable value, backup displays its original and
proposed values. For a protected value, neither value nor restoration command
arguments appear in console output, ordinary logs, `launch.toml`, or the
ordinary workspace manifest. A backup does not authorize a change by itself.

Protected originals and restoration data are atomically written below a
dedicated private workspace directory owned by the identity that performed the
protected read. Every directory component and the file are opened without
following symlinks. The directory mode is at most `0700`; each file mode is
exactly `0600`. Ownership and mode are verified before and after publication.
The ordinary manifest records only a normalized, traversal-free relative path
contained below that directory, the private-file SHA-256 digest, value names,
reader identity, and outcome.

## Invariants

The script rejects unspecified/unclassified values, ambiguous workspace
identities, absolute/traversing or symlinked private paths, permissive
ownership/mode, and an existing backup destination. It does not change system
configuration, make a workspace ready, or delete an existing backup. Rollback
verifies the private digest and uses equivalent read authority before
interpreting restoration data.

## Tests

Automated tests use temporary configuration fixtures, fake readers, and
sentinel protected values to verify scope, public displayed differences,
private file ownership/mode/digest, manifest output, no sentinel outside the
private file, and no system mutation.

## Architecture Qualities and Limits

Pure planning validates identity/value allowlists; reader subprocesses and
manifest output form the shell and use argument vectors. Work is bounded by the
explicit value list. Logs record names/outcomes only for protected entries. The
script is manually deployed as Bash and may require read authority only. Risks
are secret exposure, ambiguous values, permissive storage, and stale backup;
classification, private atomic storage, digest checks, and sentinel tests
address them.
