# Imported Reference Run Design

**Status:** Approved in conversation on 2026-09-04

## Purpose

Add one public command that takes an existing traffic capture and its supplied
Trafficlab metadata through the complete fitting, generation, and comparison
workflow without Docker or a separate preparation command:

```text
trafficlab import-run EXPERIMENT DUMP_DIRECTORY
```

The command accepts classic PCAP and PCAPNG input, repairs supported structural
compatibility problems entirely in process, publishes the ordinary canonical
reference pair, and then delegates to the existing one-process pipeline.  It
does not invoke repository scripts, Wireshark executables, shell commands, or
Docker.

## Goals

- Make one supplied directory sufficient to start a complete imported-reference
  experiment.
- Preserve every captured Ethernet frame byte, captured length, and wire length.
- Normalize PCAP or PCAPNG to Trafficlab's canonical one-interface PCAPNG with
  nondecreasing microsecond timestamps and Enhanced Packet Blocks.
- Keep the supplied directory read-only and detect concurrent changes.
- Reuse the existing local preflight, capture-pair publication, fitting,
  generation, comparison, final validation, failure logging, and checkpoint
  semantics.
- Retain exactly the existing nine successful run artifacts.
- Keep Scapy as the sole production packet-I/O library and add no dependency.

## Non-goals

- Inferring or replacing `capture.json`.
- Correcting a wrong target MAC or claiming that supplied direction metadata is
  authoritative provenance.
- Supporting non-Ethernet link types, compressed captures, directories with
  arbitrary extra entries, packet payload rewriting, or application-protocol
  interpretation.
- Traffic replay, live capture, Docker fallback, shell fallback, or invoking
  `scripts/prepare_traffic_dumps.py`.
- Migrating or silently replacing an existing run created from another source.

## Public CLI contract

The root parser gains exactly one subcommand and no options:

```text
trafficlab import-run [-h] EXPERIMENT DUMP_DIRECTORY
```

`EXPERIMENT` is the existing TOML path. `DUMP_DIRECTORY` is a directory path.
Both paths are resolved by their owning loaders; the current working directory
has no other semantic role. Missing positionals, additional positionals, and
options other than `-h` fail through argparse with status 2.

On success the command prints the same family, selection fitness, reference
packet count, generated packet count, aggregate score, and run-directory fields
as `trafficlab run`, prefixed with `import-run:`. A `TrafficlabError` retains its
exact status and corrective action. `KeyboardInterrupt` returns 130 after owned
temporary cleanup and tells the user to inspect `run.log` before retrying.

The existing `run` command is unchanged and continues to mean full Docker
preflight followed by live capture. `import-run` is distinct so the acquisition
provenance cannot be confused.

## Supplied-directory contract

The supplied path must be a real directory, not a symlink. Its complete direct
inventory must contain exactly two regular, non-symlink files:

- one file whose case-insensitive suffix is `.pcap` or `.pcapng`;
- one file named exactly `capture.json`.

Nested directories, special files, a second capture, aliases, and unrelated
entries are errors. Trafficlab parses `capture.json` through the existing strict
metadata boundary before decoding the capture. It never infers a MAC, rewrites
metadata, or accepts a generated substitute.

The supplied directory and configured `run.directory` must not be identical,
nested in one another, or path aliases. Invalid source shape fails before the
run directory is created.

## Stable source snapshot

After source discovery and local config-only preflight, Trafficlab computes
stable content identities for both source files and copies them into an owned
temporary directory on the run filesystem. It computes the source identities
again after copying and requires each snapshot identity to equal both source
observations. A changed file, changed directory inventory, or replaced path
fails without publishing the canonical pair.

The input directory is never opened for writing. Temporary names are internal,
non-authoritative, same-filesystem paths and are removed on success, ordinary
failure, deadline expiry, and interruption.

## In-process normalization

All packet decoding and encoding remains inside
`trafficlab.common.scapy_io`. The module selects Scapy's raw reader by decoded
file format rather than trusting the filename suffix.

For every input packet it requires:

- Ethernet link type 1;
- a captured frame of at least 14 bytes;
- finite, nonnegative timestamp fields;
- positive captured and wire lengths with captured length equal to the bytes
  actually returned and wire length at least captured length.

Classic PCAP microsecond and nanosecond variants are accepted. PCAPNG may use
multiple interfaces and supported timestamp resolutions, but every packet must
be Ethernet. Obsolete, Simple, or otherwise noncanonical packet blocks may be
read when Scapy exposes complete packet bytes and timestamps; the output always
uses Enhanced Packet Blocks.

Frames are appended to an owned binary spool while a compact in-memory index
retains exact timestamp, original ordinal, spool offset, captured length, and
wire length. The index is stably sorted by timestamp then original ordinal.
This bounds frame-memory use independently of input byte size while retaining
deterministic equal-timestamp order.

Output timestamps use the existing Trafficlab microsecond convention. Values
already on a microsecond remain unchanged; higher precision is truncated
toward the past and never moved beyond the source time. The output epoch is not
normalized: only packet order and representable precision change. Scapy writes
one Ethernet interface and exact captured frame bytes, captured lengths, and
wire lengths.

The normalized output must contain at least two packets and have a finite
positive observation window after the existing reference normalization. The
complete normalized PCAPNG and supplied metadata are reparsed through
`validate_capture_pair` before publication.

## Deadline and resource behavior

The import acquisition stage uses `capture.total_timeout_seconds` as one
monotonic absolute deadline. Source identification, copying, raw decoding,
spooling, sorting, encoding, reparsing, and publication check the remaining
budget at their natural packet or artifact boundaries. The command adds no
configuration key.

Spool and normalized output bytes count against the existing free-space check.
Any write or directory-fsync error is actionable and preserves authoritative
artifacts already durably published. The implementation adds no worker,
subprocess, SQLite database, service, or persistent cache.

## Publication and reuse

The existing capture-pair publisher remains the only owner of canonical
`capture.json` and `reference.pcapng` publication. A successful import appends
one canonical `reference_imported` record containing:

- source capture and metadata content identities;
- normalized reference and published metadata identities;
- normalization format/version;
- source paths, packet count, canonical output path, and `reused=false`.

An exact retry first validates the existing pair and the retained import record.
It re-identifies the current supplied directory and reuses without
renormalization only when source identities, output identities, effective
configuration, and normalization version all match. It appends a corresponding
`reference_imported` record with `reused=true`.

If one canonical capture artifact is missing, either is malformed, the import
record is absent or contradictory, or any identity differs, Trafficlab
preserves the run and fails. It never guesses ownership, overwrites a different
pair, or falls back to live capture. The corrective action is to restore the
original source/run or select a fresh `run.directory`.

## Pipeline composition and failure behavior

`import-run` discovers the source before local preflight, then supplies two
different acquisition dependencies to the existing coordinator:

```text
config-only preflight -> imported reference -> fit -> generate -> compare
```

The ordinary coordinator continues to validate every returned stage result and
the final nine-file directory. Fit, generation, comparison, scientific
artifacts, seeds, limits, checkpoint resume, and aggregate semantics are
unchanged.

Failures before a run directory exists are direct `import-run` errors. Once
local preflight has published the initial run records, an acquisition or later
failure appends the existing structured `run_failed` record with the owning
stage. A success appends the ordinary `run_completed` record. No separate
manifest, alternate artifact schema, or success marker is introduced.

## Code ownership

- `src/trafficlab/common/scapy_io.py` owns raw PCAP/PCAPNG decoding, spool
  ordering, and canonical raw-frame PCAPNG writing. If the 600-line production
  cap would be exceeded, it becomes a compatibility-preserving
  `trafficlab.common.scapy_io` package with focused read/normalize/write owners;
  public imports do not change.
- `src/trafficlab/pipeline/imported.py` owns strict directory discovery, source
  snapshots, import lineage/reuse, and composition with `run_experiment`.
- `src/trafficlab/cli.py` owns parser dispatch and the human-facing summary.
- Existing capture artifact, preflight, fit, generate, compare, and final
  validation owners are reused rather than duplicated.

No production module imports from `scripts/`, and no subprocess boundary is
reachable from `import-run`.

## Verification

Unit and scientific tests use checked-in hand-built PCAP and PCAPNG fixtures
with independent expected raw frames, lengths, timestamps, and ordering. They
cover both classic byte orders and timestamp resolutions where Scapy supports
them, reversed timestamps, stable ties, multiple Ethernet interfaces,
noncanonical PCAPNG packet blocks, malformed/truncated inputs, non-Ethernet
links, short frames, bad lengths, bad timestamps, fewer than two packets, and
zero observation windows.

Source-bound tests cover missing/extra/aliased/symlinked entries, source changes
during every snapshot/normalization boundary, source/run overlap, deadline
expiry, free-space and write failures, temporary cleanup, incomplete existing
pairs, absent/contradictory lineage, exact reuse, and changed-source rejection.

CLI tests cover help, exact positional arguments, lazy in-package dispatch,
success output, structured errors, and interruption. In-process integration
uses real normalization plus real fit, generate, compare, final validation, and
checkpoint resume while forbidding Docker, shell, subprocess, and repository
script imports. A successful run has exactly nine artifacts.

Every behavior follows red/green TDD. Functions exposed by a failed test receive
100% executable line and branch coverage. Focused and package gates run before
the complete Ordinary and branch-aware Coverage gates. Available real-program
validation exercises both a PCAP and PCAPNG source without modifying the
checked input. Independent review must report no Critical or Important finding.

## Documentation

`architecture/SYSTEM.md` defines the public command, normalization, composition,
reuse, and failure contract. `architecture/CAPTURE.md` distinguishes imported
reference acquisition from Docker capture. `architecture/TESTING.md` owns its
verification matrix. The root README and Russian quick start show exact command
examples and explain that supplied `capture.json` direction metadata is not
re-inferred.

Architecture documents contain only stable behavior and verification
requirements, not implementation progress or dated results.

## Acceptance

The feature is accepted when one command can import either supported format
from the exact directory shape, normalize it entirely in process without
changing captured frame bytes, publish or exactly reuse the authoritative pair,
complete the ordinary scientific pipeline without Docker or subprocesses, and
produce a strictly validated nine-file run. All focused, Ordinary, Coverage,
deterministic fixture, and available real-program gates must pass, and the
reviewed feature branch must be clean.
