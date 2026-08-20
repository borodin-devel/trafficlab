# [SPEC-1-c85b7e39] Scapy Production Codec Design

**Status:** Approved in conversation on 2026-08-20

**Goal:** Make Scapy 2.7.0 the sole production PCAPNG reader and writer, remove
the custom production codec and its compatibility surface, and align the
project's development policy with deliberate large-scale replacement over
backward-compatibility layers.

## [SECTION-1-93b51025] Decisions and motivation

Trafficlab is a personal, non-public research prototype. The user explicitly
does not require a project license and does not want dependency licenses or
their limitations evaluated as adoption criteria. Scapy must therefore move
from a rejected development-only probe to a required, full production
dependency without a licensing gate.

Scapy will be the only production PCAPNG implementation. Existing
`trafficlab.pcapng` functions will not be preserved as wrappers, aliases, or a
fallback. Every caller will migrate to new APIs in `trafficlab.scapy_io`, and
the old module will be deleted. A small independent parser may remain only
under `tests/` as an oracle; installed code must not import or package it.

The migration intentionally accepts Scapy's container parsing, emitted byte
layout, timestamp precision, and performance characteristics. It does not
claim byte compatibility with the custom codec or compatibility with schema-v3
scientific artifacts.

## [SECTION-2-574c4f12] Development and compatibility policy

`architecture/DEVELOPMENT.md` will gain two stable policy sections.

The license policy states that Trafficlab does not require a project license
and does not inspect, compare, approve, reject, or gate dependencies according
to their licenses or license limitations. License metadata is not a scientific
or release artifact. No licensing checklist, compatibility decision, legal
review, or license-specific adoption blocker belongs in the project workflow.

The evolution policy states that backward compatibility is not a cornerstone
of this prototype. A coherent replacement or deletion of a large subsystem is
preferred over adapters, shims, selectable backends, parallel execution paths,
or compatibility layers when the replacement materially improves simplicity,
precision, reproducibility, configurability, or reliability. Simplicity means
fewer concepts and authoritative paths, not merely a small diff.

Compatibility breaks must remain explicit. The owning schema is bumped, stale
artifacts fail deterministically, every internal caller migrates in the same
change, fixtures and examples are regenerated, and the stable architecture is
updated. Simplification never permits silently weakening scientific formulas,
validation, bounded execution, deterministic failures, or reproducible
evidence.

## [SECTION-3-8d73b242] Production architecture

`src/trafficlab/scapy_io.py` owns all production PCAPNG work and is the only
production module allowed to import Scapy. Dynamic Scapy objects remain behind
small local typed protocols so the rest of the package consumes only
`TrafficTrace`, `CaptureMetadata`, bytes, paths, and Trafficlab errors.

The production flow becomes:

```text
capture PCAPNG bytes/path
  -> trafficlab.scapy_io decode
  -> TrafficTrace
  -> normalize / fit / generate / compare
  -> trafficlab.scapy_io encode
  -> emitted PCAPNG bytes
  -> trafficlab.scapy_io decode
  -> authoritative emitted TrafficTrace
  -> publication / comparison / identities
```

Capture remains owned by Docker Compose and the capture image. Scapy operates
in the one host Python process after bytes are available and before scientific
processing. This adds no service, process boundary, backend selector, security
subsystem, or orchestration path.

## [SECTION-4-aac00759] Public API and types

The new module exposes these typed concepts; exact argument ordering may be
refined by the implementation plan, but their responsibilities are fixed:

```python
@dataclass(frozen=True, slots=True)
class EncodedPcapng:
    content: bytes
    trace: TrafficTrace  # reparsed emitted bytes

def read_pcapng_bytes(
    content: bytes,
    metadata: CaptureMetadata,
    *,
    source: Path,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> TrafficTrace: ...

def read_pcapng(
    path: Path,
    metadata: CaptureMetadata,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> TrafficTrace: ...

def encode_pcapng(
    trace: TrafficTrace,
    metadata: CaptureMetadata,
    *,
    observation_window_seconds: float,
) -> EncodedPcapng: ...
```

The byte reader and path reader are distinct because artifact validation and
auditing already own bytes, while capture and CLI boundaries own paths.
Encoding returns both exact bytes and the trace obtained by reparsing those
bytes. No caller may assume the pre-encoding trace is identical after Scapy's
timestamp conversion.

`src/trafficlab/pcapng.py` and every old parse/encode name are removed. Import
failures are intentional and covered so compatibility aliases cannot return
later.

## [SECTION-5-584d52d0] Reading and scientific validation

Scapy defines PCAPNG structural acceptance. Trafficlab will not reproduce the
custom parser's block, padding, option, or error-message rules around Scapy.
The reader still enforces the project-specific facts required by the
scientific workflow:

- exactly one observed Ethernet interface and a supported Ethernet link type;
- explicit packet timestamps convertible to finite nonnegative seconds;
- nondecreasing timestamps;
- frame lengths representable by `uint32` and valid for Ethernet processing;
- target-MAC source classification for outbound versus inbound direction;
- observation-window filtering at the owning caller; and
- deadline checks before I/O, after reader construction, for every packet, and
  after conversion before returning the trace.

Scapy warnings are not scientific evidence and must not be used as pass/fail
signals. Unsupported or invalid data is translated into stable Trafficlab
error categories and corrective actions. The new contract does not preserve
the previous parser's exact message wording or rejection precedence.

## [SECTION-6-6e65e053] Writing and authoritative output

The writer validates its input `TrafficTrace`, closed observation window,
direction values, and frame lengths before entering Scapy. It constructs
deterministic Ethernet frames using the target and deterministic peer MACs,
explicit timestamps, declared captured/wire lengths, and deterministic zero
payload bytes. It uses Scapy's PCAPNG writer without calling the old encoder.

Scapy's emitted bytes and timestamp precision are authoritative. The writer
must close and flush the destination, read the exact bytes, and decode them
through the production Scapy reader. Publication receives `EncodedPcapng` only
after the reparse succeeds. Packet counts, generated identities, comparison
inputs, similarity scores, example evidence, and validation-study evidence use
the reparsed `EncodedPcapng.trace` rather than the pre-write trace.

Two locked executions from the same trace and environment must emit identical
bytes. If Scapy cannot satisfy deterministic writing under the locked version,
the implementation must add only the smallest explicit Scapy-boundary controls
needed to remove ambient time or ordering; it must not restore the old codec.

## [SECTION-7-7277db1c] Caller migration and deletion

All production, script, fixture, and test callers migrate in one change. This
includes capture validation, normalization inputs, fitting, generation,
comparison, CLI commands, artifact reconstruction, deterministic fixture
generators, durable example checking, validation-study collection, and the
offline auditor.

The old production codec is deleted after caller migration. Unused constants,
block builders, compatibility wrappers, and legacy byte-specific helpers are
deleted with it. Tests that exist only to preserve the removed container syntax
or exact legacy bytes are replaced rather than carried forward.

The independent oracle lives at `tests/support/pcapng_oracle.py` or an equally
narrow test-only location. It parses only the valid subset needed to calculate
timestamp, direction, and frame-length expectations without importing Scapy or
production decoding code. It is not a second complete codec and does not
provide malformed-input policy or writing.

## [SECTION-8-f3c028be] Dependency and schema migration

`scapy==2.7.0` moves from the development group into project runtime
dependencies through `uv`; it appears exactly once in `pyproject.toml` and the
lock is regenerated by uv. The installed production package must import and
execute the Scapy boundary without the development group.

`SCIENTIFIC_ARTIFACT_SCHEMA_VERSION` becomes 4. Schema-v3 best models,
checkpoints, examples, and current-run reusable scientific artifacts are
rejected with an explicit refit/regenerate instruction. This prevents a result
produced with the custom codec from being silently resumed or republished with
different PCAPNG semantics.

Public JSON schema roots remain the same logical artifact models unless a
field is required to identify the production codec. Prefer source, lock,
schema, and exact-byte identities already owned by the artifacts over adding a
redundant configurable backend field; there is only one codec.

## [SECTION-9-929cf46c] Probe retirement and evidence

Scapy is no longer an optional candidate. Remove its license-decision document,
license evidence, compatibility gate, adoption decision, and the
`scapy_cases.json` probe artifact. The shared optional-probe runner retains only
the MMPP and pymoo probes and their existing decisions.

Useful Scapy functional cases move into normal production tests. Bounded
100,000- and 1,000,000-frame measurements remain diagnostic rather than an
adoption gate. A new production-oriented machine-readable benchmark may retain
raw samples, source/lock identity, exact commands, medians, and peak RSS, but it
must contain no license or adoption decision. The already observed performance
regression is explicitly accepted by this design.

`docs/SCIENTIFIC_STACK_ADOPTION_EVIDENCE.md` becomes a historical adoption
record that explains the earlier rejection was superseded by an explicit user
decision. It must not continue to describe Scapy as development-only after the
runtime migration.

## [SECTION-10-344557ed] Deterministic fixtures and examples

All generated model, fitting, checkpoint, similarity, PCAPNG, manifest, and
durable-example fixtures affected by schema v4 or Scapy output are regenerated
through their owning generators. Two clean locked generations must produce
identical arrays, JSON/CSV/TOML, PCAPNG bytes, manifests, and hashes.

The durable example is rerun from a clean implementation source commit. Its
checker independently parses Scapy-produced reference/generated captures,
validates checkpoint compatibility, refits the winner, regenerates exact
Scapy PCAPNG bytes, reparses them, recomputes all four similarity methods, and
checks every identity and run-log claim.

Historical accepted r6 and r21 evidence remains byte-for-byte unchanged and
continues to describe its historical source. Current schema-v4 readers reject
those scientific roots where appropriate rather than rewriting history.

## [SECTION-11-6bbaa2b4] Real-program validation

A new accepted validation-study bundle is generated from the final Scapy
production source commit. It includes successful Docker and explicit
credential-free HTTPS prerequisites, nine training runs, fresh-seed
simulations, held-out comparisons, recorded bootstrap intervals, full manifests
and lineage, and an exclusive publisher audit.

The final offline audit runs from a no-local, no-hardlink detached clone at the
recorded implementation source commit. The accepted bundle is copied as
regular files, contains no symlink or hard-link shortcut, and must reconstruct
all Scapy-decoded traces, fitted models, generated bytes, comparison scores,
bootstrap records, report arithmetic, and source/environment identities without
Docker or network access.

## [SECTION-12-a5ac0669] Verification and acceptance

Acceptance requires:

- Scapy is a runtime dependency and no production custom codec remains;
- every old PCAPNG API import fails and every internal caller uses
  `trafficlab.scapy_io`;
- direct production tests cover valid little-/big-endian captures, timestamp
  resolutions, Ethernet IPv4/IPv6/ARP frames, direction classification,
  interface/link-type rejection, deadlines, writing, reparsing, determinism,
  and error normalization;
- the test-only oracle independently agrees on timestamps within Scapy's
  emitted resolution and exactly on directions and frame lengths;
- schema-v3 reuse is rejected and schema-v4 fixture generations are identical
  twice;
- no license file, license gate, license artifact, or license-based dependency
  decision remains in the active workflow;
- Ruff formatting/lint, strict Pyright, Ordinary, branch Coverage at or above
  90%, every deterministic generator/checker, the production Scapy diagnostic,
  detached accepted-study audit, and combined serial Docker/Internet gate pass;
  and
- an independent final review reports no Critical or Important findings.

## [SECTION-13-2c13fa84] Exclusions

This change does not add a codec selector, legacy backend, fallback parser,
compatibility shim, packet replay, live Scapy sniffing, payload or
application-protocol modeling, distributed execution, service boundary,
security subsystem, Node.js application dependency, project licensing
workflow, or legal analysis.

MMPP and pymoo production decisions do not change. Docker Compose remains the
only capture orchestrator. Trafficlab remains one Python process outside the
two established capture containers.
