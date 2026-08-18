# Trafficlab Phase 2 Trace and Similarity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse and render deterministic Ethernet PCAPNG traces, normalize one
shared observation window, evaluate the four documented similarity methods, and
publish reproducible `similarity.json` output through `trafficlab compare`.

**Architecture:** Plain immutable events are the research boundary. A small
standard-library PCAPNG codec handles Section Header, Interface Description,
and Enhanced Packet blocks without exposing file objects to the mathematical
core. Four focused similarity modules return bounded scores and complete
diagnostics; one direct comparison function aggregates them and owns the JSON
artifact. There is no plugin system, packet framework, NumPy/SciPy dependency,
subprocess, or Docker path.

**Tech Stack:** CPython 3.12, standard library (`dataclasses`, `json`, `math`,
`struct`, `hashlib`), Pydantic 2, argparse, pytest, pytest-cov, pytest-xdist,
Ruff, Pyright

## Global Constraints

- Use `src/trafficlab/`; public functions and models are strictly typed.
- Canonical events are finite, nondecreasing `(timestamp, direction,
  frame_length)` values. Directions are exactly `outbound` and `inbound`.
- A reference has at least two events and finite `W = t_n - t_1 > 0`.
  Normalize it to `[0, W]`, including both endpoints.
- Shift a nonempty generated trace to its first event and retain only events at
  or before `W`; validate the complete input before cropping.
- Parse one Ethernet interface in one PCAPNG section. Reject malformed block
  lengths, unsupported link types, packet-bearing block types without
  timestamps, invalid timestamp resolution, and decreasing/nonfinite
  timestamps. A captured Ethernet frame has at least 14 bytes; its canonical
  length is the captured length even when the original packet was longer.
- Render little-endian PCAPNG with one Ethernet interface and nanosecond
  timestamp resolution. Require generated frame length at least 14 bytes.
- `capture.json` contains only `interface = "eth0"` and one normalized nonzero
  unicast target MAC. Generated frames use the documented deterministic peer.
- All metric precondition failures are `TrafficlabError`; never turn an invalid
  input into score zero.
- Every component score, aggregate score, and discrepancy is finite in `[0, 1]`.
- `similarity.json` includes all diagnostics, the shared `W`, configured
  weights, and SHA-256 identities of the effective similarity settings,
  `capture.json`, reference PCAPNG, and generated PCAPNG. It is deterministic
  and atomically published without replacing an existing result.
- Keep lines at no more than 120 characters. Maintain at least 90%
  branch-aware non-Docker coverage.
- Write tests before implementation. Commit only after focused tests, Ruff,
  and strict Pyright pass.

---

### Task 1: Canonical events and strict capture metadata

**Files:**
- Create: `src/trafficlab/trace.py`
- Create: `tests/unit/test_trace.py`
- Create: `tests/unit/test_capture_metadata.py`

**Interfaces:**
- Produces: `Direction`, immutable `TraceEvent`, `CaptureMetadata`,
  `load_capture_metadata(path)`, `render_capture_metadata(metadata)`, and
  `deterministic_peer_mac(target_mac)`

- [ ] **Step 1: Write failing metadata and event tests**

Cover exact two-field JSON, unknown/missing fields, invalid UTF-8/JSON, literal
`eth0`, uppercase-to-lowercase MAC normalization, malformed MACs, all-zero MAC,
multicast MAC, and deterministic peer selection including the collision case.
Cover finite nonnegative timestamps, exact directions, and positive integer
frame lengths without silently coercing types.

- [ ] **Step 2: Confirm the focused red state**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_trace.py tests/unit/test_capture_metadata.py
```

Expected: collection fails because `trafficlab.trace` does not exist.

- [ ] **Step 3: Implement one strict boundary module**

Use a frozen, slotted `TraceEvent` dataclass and a frozen strict Pydantic
`CaptureMetadata`. Keep MAC values as canonical lowercase colon-separated
strings; convert to bytes only inside the PCAPNG codec. Translate expected
filesystem, decoding, JSON, and validation failures into actionable
`TrafficlabError` messages.

- [ ] **Step 4: Verify and commit**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_trace.py tests/unit/test_capture_metadata.py
uv run --locked ruff format --check src/trafficlab/trace.py tests/unit
uv run --locked ruff check src/trafficlab/trace.py tests/unit
uv run --locked pyright
```

Commit: `feat(trace): add canonical events and metadata`

---

### Task 2: Minimal reliable Ethernet PCAPNG codec

**Files:**
- Create: `src/trafficlab/pcapng.py`
- Create: `tests/unit/test_pcapng.py`
- Modify: `architecture/CAPTURE.md`

**Interfaces:**
- Consumes: `TraceEvent`, `CaptureMetadata`
- Produces: `parse_pcapng(path, metadata, *, deadline=None, clock=monotonic)
  -> tuple[TraceEvent, ...]`,
  `encode_pcapng(events, metadata) -> bytes`, and
  `write_pcapng(path, events, metadata) -> None`

- [ ] **Step 1: Write byte-level codec tests first**

Build tiny blocks in test helpers and cover:

- little- and big-endian Section Header byte-order magic;
- one Ethernet Interface Description Block, default microsecond resolution,
  decimal nanosecond `if_tsresol`, and binary `if_tsresol`;
- Enhanced Packet padding, repeated trailing block length, interface ID,
  captured/original lengths, and timestamp conversion;
- outbound target source, inbound peer source, and inbound broadcast source;
- invalid block lengths, truncated block/body/frame, multiple interfaces,
  unsupported link type, bad timestamp option, decreasing timestamps,
  captured length above original length, captured length above nonzero IDB
  SnapLen, and a valid packet truncated to the declared SnapLen;
- all-SPB, mixed EPB/SPB, and obsolete Packet Block input rejection so no
  packet-bearing block is silently omitted;
- a fake monotonic clock and two frames, with deadline checks before reading
  and after each accepted frame so expiry after frame one prevents frame two;
- renderer header addresses, deterministic peer, exact frame lengths,
  directions, nanosecond timestamps, stable bytes, and round trips.

- [ ] **Step 2: Confirm the focused red state**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_pcapng.py
```

- [ ] **Step 3: Implement only the required PCAPNG subset**

Stream Section Header (`0x0A0D0D0A`), Interface Description (`1`), and Enhanced
Packet (`6`) blocks from the file. Validate every block's leading/trailing
length and four-byte alignment. Skip well-formed non-packet blocks, but reject
Simple Packet (`3`) and obsolete Packet (`2`) blocks because omitting them
would silently lose traffic. Reject a second section or interface for the MVP.
Require Ethernet link type `1`, `14 <= captured_length <= original_length`,
and `captured_length <= SnapLen` when SnapLen is nonzero. Use captured length
as canonical `frame_length`. Amend `CAPTURE.md` to require the Phase 3 capture
command to emit Enhanced Packet Blocks.

Use the Interface Description `if_tsresol` option when present: high bit clear
means `10**-value`; high bit set means `2**-(value & 0x7f)`. Default to
microseconds. Accept an optional monotonic deadline and check it before reading
and immediately after every parsed frame. Rendering uses decimal exponent 9,
SnapLen `max(65535, max(frame_length))`, EtherType `0x0800`, and zero payload
bytes. Round finite nonnegative timestamps to the nearest integer nanosecond;
reject an empty sequence, 32-bit length/SnapLen overflow, and 64-bit timestamp
overflow.

- [ ] **Step 4: Verify and commit**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_pcapng.py
uv run --locked ruff format --check src/trafficlab/pcapng.py tests/unit/test_pcapng.py
uv run --locked ruff check src/trafficlab/pcapng.py tests/unit/test_pcapng.py
uv run --locked pyright
```

Commit: `feat(trace): add Ethernet PCAPNG codec`

---

### Task 3: Shared observation-window normalization

**Files:**
- Modify: `src/trafficlab/trace.py`
- Modify: `tests/unit/test_trace.py`

**Interfaces:**
- Produces: `normalize_reference(events) -> (normalized_events, W)` and
  `align_generated(events, W) -> normalized_events`

- [ ] **Step 1: Write the published hand examples and edge cases**

Require `[10, 11, 13] -> ([0, 1, 3], W=3)`. Retain generated events at `0` and
`W`, crop only values after `W`, preserve naturally early completion, and
reject empty/decreasing/nonfinite/generated-invalid traces. Reject a reference
with fewer than two events, zero/negative/nonfinite `W`, or invalid event data.

- [ ] **Step 2: Confirm red, implement direct tuple functions, and verify**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_trace.py
uv run --locked ruff format --check src/trafficlab/trace.py tests/unit/test_trace.py
uv run --locked ruff check src/trafficlab/trace.py tests/unit/test_trace.py
uv run --locked pyright
```

Do not introduce a trace-wrapper class. The functions receive ordinary event
sequences and return immutable tuples plus the explicit scalar `W`.

Commit: `feat(trace): normalize shared observation window`

---

### Task 4: Exact frame-size and IAT KS methods

**Files:**
- Create: `src/trafficlab/similarity/__init__.py`
- Create: `src/trafficlab/similarity/common.py`
- Create: `src/trafficlab/similarity/ks.py`
- Create: `tests/unit/similarity/test_ks.py`
- Modify: `architecture/similarity_methods/iat_ks.md`

**Interfaces:**
- Produces: immutable `SimilarityResult`, `exact_ecdf_distance(left, right)`,
  `frame_size_ks(reference, generated, W)`, and
  `iat_ks(reference, generated, W, diagnostic_quantile)`

- [ ] **Step 1: Write every published calculation and precondition**

Cover identical, disjoint singleton, `[1, 2]` versus `[1, 3] = 1/2`, ties,
order independence, empty/nonpositive/noninteger sizes, IATs `[0, 1, 3] ->
[1, 2]`, retained zero IATs, one-packet rejection, decreasing/nonfinite time,
invalid quantiles, and score/diagnostic ranges. Require the same supplied `W`
in both diagnostics.

- [ ] **Step 2: Pin the diagnostic quantile convention**

Clarify `iat_ks.md`: sort `n` samples and return the nearest-rank order
statistic at one-based rank `ceil(q*n)` for `0 < q < 1`. Add hand tests for
both exact and nonexact ranks. Define median diagnostics separately as the
middle value for odd `n` and the arithmetic mean of the two middle values for
even `n`; add an even-sample test.

- [ ] **Step 3: Implement one merged ECDF scan**

Sort both samples, consume all ties at each merged unique value, and compare
ECDFs only after tie consumption. Share that primitive between the two methods;
do not add a statistics dependency.

- [ ] **Step 4: Verify and commit**

```bash
uv run --locked pytest -q -n 0 tests/unit/similarity/test_ks.py
uv run --locked ruff format --check src/trafficlab/similarity tests/unit/similarity
uv run --locked ruff check src/trafficlab/similarity tests/unit/similarity
uv run --locked pyright
```

Commit: `feat(similarity): add exact KS methods`

---

### Task 5: Documented autocorrelation method

**Files:**
- Create: `src/trafficlab/similarity/autocorrelation.py`
- Create: `tests/unit/similarity/test_autocorrelation.py`

**Interfaces:**
- Produces: `sample_autocorrelation(values, lag)` and
  `autocorrelation_similarity(reference, generated, W, lags, lag_weights,
  iat_weight, size_weight)`

- [ ] **Step 1: Write formula-first tests**

Cover the documented whole-series-mean estimator, constant-series zero
convention, `[1, 2, 3]` lag-one zero, `[1, 2, 1]` lag-one `-2/3`, identical
series, one constant versus nonconstant, and synthetic `-1` versus `1`
discrepancy. Reject duplicate/nonpositive/too-large lags, nonfinite samples,
lag/weight length mismatch, and invalid normalized weights. Prove that two
packets with lag one fail because their IAT sample has length one. Prove that
reference and generated packet counts may differ when every lag is smaller
than all four sample lengths: reference/generated IAT and size. Assert every
per-lag, feature, and final diagnostic plus shared `W`.

- [ ] **Step 2: Confirm red and implement the equations literally**

Use direct loops and `math.fsum`. Clamp only roundoff within `1e-15` of the
documented ranges; a materially out-of-range value is an error.

- [ ] **Step 3: Verify and commit**

```bash
uv run --locked pytest -q -n 0 tests/unit/similarity/test_autocorrelation.py
uv run --locked ruff format --check src/trafficlab/similarity tests/unit/similarity
uv run --locked ruff check src/trafficlab/similarity tests/unit/similarity
uv run --locked pyright
```

Commit: `feat(similarity): add autocorrelation score`

---

### Task 6: Direction-separated multiscale-rate method

**Files:**
- Create: `src/trafficlab/similarity/multiscale.py`
- Create: `tests/unit/similarity/test_multiscale.py`
- Modify: `architecture/similarity_methods/multiscale_rate.md`

**Interfaces:**
- Produces: `normalized_l1(reference_cells, generated_cells)` and
  `multiscale_rate_similarity(reference, generated, W, widths,
  scale_weights, packet_weight, byte_weight, max_direction_bin_cells)`

- [ ] **Step 1: Write every hand calculation and boundary test**

Cover identical vectors, both-zero vectors, `[1, 0]` versus `[0, 1] = 1`,
`[1, 1]` versus `[1, 0] = 1/3`, multiple widths, packet and byte features,
outbound-before-inbound layout, trailing zero bins, a timestamp exactly at
`W`, direction reversal, and asymmetric directions. Reject empty traces,
events outside `[0, W]`, invalid directions/sizes/timestamps, invalid/duplicate
or too-large widths, invalid weights, nonpositive `W`, and
`2 * sum(ceil(W / h))` above the cell cap.
Add exact decimal regressions: `W=2.1, h=0.3` has seven bins, and an event at
`t=0.3, h=0.1` enters bin index three rather than two.

- [ ] **Step 2: Confirm red and implement direct per-scale binning**

Before `ceil` or `floor`, snap a finite quotient to its nearest integer only
when the difference is at most four ULPs of that quotient. Document this
numeric convention in `multiscale_rate.md`. For each width allocate exactly
`2*ceil(snapped(W/h))` cells and use
`min(floor(snapped(timestamp/h)), B-1)` so decimal boundaries and the closed
right endpoint enter the intended bins. Keep packet/byte and outbound/inbound
totals in diagnostics without building an abstraction for future metrics.

- [ ] **Step 3: Verify and commit**

```bash
uv run --locked pytest -q -n 0 tests/unit/similarity/test_multiscale.py
uv run --locked ruff format --check src/trafficlab/similarity tests/unit/similarity
uv run --locked ruff check src/trafficlab/similarity tests/unit/similarity
uv run --locked pyright
```

Commit: `feat(similarity): add multiscale rate score`

---

### Task 7: Weighted comparison and atomic similarity artifact

**Files:**
- Create: `src/trafficlab/comparison.py`
- Create: `tests/unit/test_comparison.py`
- Create: `tests/unit/test_similarity_artifact.py`
- Create: `tests/integration/test_comparison_pipeline.py`
- Modify: `src/trafficlab/artifacts.py`

**Interfaces:**
- Produces: `compare_traces(reference, generated, W, settings)`,
  `compare_experiment(experiment_path)`, and immutable `ComparisonResult`

- [ ] **Step 1: Write aggregation and artifact tests**

Use stub component results to prove all four configured weights, exact weighted
sum, retained component scores/diagnostics, shared `W`, finite `[0, 1]` output,
and error propagation. Unit-test deterministic sorted JSON, SHA-256 helpers,
typed round trips, and exclusive publication. In the marked integration test,
join the real experiment snapshot, metadata, PCAPNG, normalization, all metrics,
and publication. Prove source/snapshot equality, mismatch rejection, identities,
atomic temp-write/flush/fsync/validation, a pre-existing output, a publication
collision, and bounded cleanup/reporting on failure.

- [ ] **Step 2: Implement one direct orchestration function**

`compare_experiment` loads the caller's experiment only to locate the existing
run. It then requires and loads `run/experiment.toml`, rejects any difference
from the caller's effective configuration, and uses the authoritative snapshot
settings. It requires `capture.json`, `reference.pcapng`, and
`generated.pcapng` inside that run, evaluates all four methods over one `W`,
and publishes this shape:

```json
{
  "aggregate_score": 0.0,
  "input_sha256": {
    "capture_json": "...",
    "generated_pcapng": "...",
    "reference_pcapng": "...",
    "similarity_settings": "..."
  },
  "methods": {
    "autocorrelation": {"diagnostics": {}, "score": 0.0, "weight": 0.0},
    "frame_size_ks": {"diagnostics": {}, "score": 0.0, "weight": 0.0},
    "iat_ks": {"diagnostics": {}, "score": 0.0, "weight": 0.0},
    "multiscale_rate": {"diagnostics": {}, "score": 0.0, "weight": 0.0}
  },
  "observation_window_seconds": 0.0
}
```

Hash the effective similarity settings as sorted compact JSON, so the identity
does not depend on the checkout's absolute run path. Validate the temporary
JSON back into the same typed result, then atomically create the absent final
name with one same-filesystem `os.link` and unlink the temporary name. This is a
no-replace publication operation, not inode/security inspection. Test and
report a link collision without deleting either the existing result or an
unowned file. Reuse only a tiny private helper; do not add a repository or
artifact framework.

Add one small `append_run_log` helper in `artifacts.py`. After the run is
located, comparison appends deterministic JSON-lines detail for success,
evaluation/input failure, and publication failure. Flush and fsync each record.
If failure logging also fails, retain the comparison failure first and report
the logging problem as secondary. If success logging fails, report the logging
failure rather than claiming full stage success.

- [ ] **Step 3: Verify and commit**

```bash
uv run --locked pytest -q -n 0 \
  tests/unit/test_comparison.py tests/unit/test_similarity_artifact.py \
  tests/integration/test_comparison_pipeline.py
uv run --locked ruff format --check src/trafficlab \
  tests/unit/test_comparison.py tests/unit/test_similarity_artifact.py \
  tests/integration/test_comparison_pipeline.py
uv run --locked ruff check src/trafficlab \
  tests/unit/test_comparison.py tests/unit/test_similarity_artifact.py \
  tests/integration/test_comparison_pipeline.py
uv run --locked pyright
```

Commit: `feat(compare): aggregate similarity results`

---

### Task 8: Compare CLI, checked-in fixtures, and Phase 2 gate

**Files:**
- Modify: `src/trafficlab/cli.py`
- Create: `tests/integration/test_compare_cli.py`
- Create: `fixtures/examples/pipeline/capture.json`
- Create: `fixtures/examples/pipeline/reference.pcapng`
- Create: `fixtures/examples/pipeline/generated.pcapng`
- Create: `fixtures/examples/pipeline/similarity.json`
- Create: `scripts/generate_similarity_fixtures.py`
- Modify: `architecture/ROADMAP.md`

**Interfaces:**
- Produces: `trafficlab compare EXPERIMENT`

- [ ] **Step 1: Add failing production-boundary integration tests**

Round-trip a checked-in outbound/inbound fixture through metadata, PCAPNG, and
canonical values. Assert reference normalization, both endpoints, generated
cropping, and identical `W` in all four diagnostics. Reverse only directions
in a one-bin asymmetric fixture and require multiscale discrepancy `1` while
KS/IAT/ACF remain unchanged. In process, call the production CLI with internal
subprocess/Docker sentinels and compare its persisted `similarity.json` with the
direct Python API result. Separately launch the installed entry point and
validate its concise summary and exact errors; that boundary test may use a
subprocess to launch the CLI but must not claim the child launches none.

- [ ] **Step 2: Create deterministic example artifacts**

Check in a small Python fixture generator containing the hand-listed canonical
events. Its default mode writes the example metadata/PCAPNG/similarity files
through production functions. Its `--check` mode builds into a temporary run,
parses the exact canonical events back, invokes production comparison, and
byte-compares every result with the checked-in files. Tests consume rather than
regenerate fixtures; the full gate runs the generator's `--check` mode.

- [ ] **Step 3: Register only the compare command**

Add the `compare` parser and call `compare_experiment` in process. Keep plain
`preflight` deferred to Phase 3 and leave `capture`, `fit`, `generate`, and
`run` unregistered until their owning phases. Format errors as
`compare: <detail>; <corrective action>` and success as one aggregate score plus
the output path.

- [ ] **Step 4: Run the full Phase 2 quality gate**

```bash
uv sync --locked --all-groups
uv lock --check
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
uv run --locked pytest -q -n auto --dist worksteal \
  -m "not integration and not docker and not internet"
uv run --locked pytest -n auto --dist worksteal --cov=trafficlab \
  --cov-branch --cov-report=term-missing -m "not docker and not internet"
uv run --locked pytest -vv -x -n 0 \
  tests/unit/similarity/test_multiscale.py::test_reversed_one_bin_direction_has_maximum_discrepancy
uv run --locked pytest -vv -x -n 0 \
  tests/integration/test_compare_cli.py::test_in_process_compare_matches_api_without_internal_processes
uv run --locked pytest -vv -x -n 0 \
  tests/integration/test_compare_cli.py::test_installed_compare_publishes_expected_result
uv run --locked python scripts/generate_similarity_fixtures.py --check
```

- [ ] **Step 5: Close the Roadmap phase truthfully**

Mark Phase 2 deliverables/tests complete only after each has repository
evidence. Move `(Current)` to Phase 3 only after the full gate and independent
review pass. Commit the CLI/fixtures first and the reviewed Roadmap closure
separately.

Commit: `feat(compare): expose offline comparison CLI`

Final closure commit: `docs: complete roadmap phase 2`

---

## Final Phase Review

After all eight tasks are individually reviewed, review the complete Phase 2
range against this plan, `architecture/SYSTEM.md`, all four mathematical method
documents, `architecture/TESTING.md`, and the Phase 2 Roadmap. Fix every
Critical or Important finding in one bounded pass and perform one scoped
re-review. Run the complete locked gate again before starting Phase 3.

**Done when:** `trafficlab compare` returns reproducible component and
aggregate scores for checked-in fixture PCAPNG files, every published equation
has an independent hand-checked test, all Phase 2 checkboxes are truthful, the
non-Docker branch coverage is at least 90%, and the final independent review has
no Critical or Important finding.
