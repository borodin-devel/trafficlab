# PCAPNG I/O Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement PCAPNG I/O roadmap STEP 1.1 with deterministic supported
Ethernet records, streaming bounded parse/write shells, and preservation
filtering.

**Architecture:** Frozen record values and validation/filtering remain pure.
The shell consumes explicit binary streams, applies limits before allocations,
and emits only canonical little-endian PCAPNG Section Header, Interface
Description, and Enhanced Packet blocks. The maintained locked Scapy runtime
is used as an independent fixture/backend compatibility check, while Trafficlab
owns the narrow metadata-preserving writer Scapy does not expose.

**Tech Stack:** Python 3.12 standard library binary I/O and `struct`; locked
Scapy fixture validation; pytest, Pyright, Ruff.

---

### Task 1: Define supported immutable records and policies

**Files:**
- Create: `src/trafficlab/libs/pcap_io/errors.py`
- Create: `src/trafficlab/libs/pcap_io/values.py`
- Create: `src/trafficlab/libs/pcap_io/__init__.py`
- Test: `tests/libs/pcap_io/test_values.py`

- [ ] **Step 1: Write RED tests**

  Test frozen/slotted `InterfaceRecord`, `PacketRecord`, and `PcapPolicy`.
  Require Ethernet link type 1, positive timestamp resolution, finite timestamp,
  `0 <= captured_length <= original_length`, exact captured bytes, canonical
  interface identifiers, and positive byte/packet limits.

- [ ] **Step 2: Run RED test**

  Run: `uv run --locked pytest tests/libs/pcap_io/test_values.py -q`

  Expected: import failure for missing `trafficlab.libs.pcap_io`.

- [ ] **Step 3: Implement value contracts**

  Create typed errors and frozen values. Store timestamp as integer ticks with
  explicit positive decimal resolution and signed offset, so parser and writer
  never use floating point for stored PCAPNG metadata.

- [ ] **Step 4: Run GREEN test**

  Run: `uv run --locked pytest tests/libs/pcap_io/test_values.py -q`

  Expected: every contract construction/rejection test passes.

### Task 2: Parse supported blocks through bounded streams

**Files:**
- Create: `src/trafficlab/libs/pcap_io/codec.py`
- Test: `tests/libs/pcap_io/test_codec.py`

- [ ] **Step 1: Write RED fixture tests**

  Build binary fixtures with one and multiple Ethernet IDBs, decimal and binary
  timestamp resolutions, signed timestamp offsets, EPBs, and packet order.
  Test malformed magic, block-length mismatch, truncated body, unknown
  interface, invalid options, unsupported link type, oversized block/input,
  and captured/original length inconsistencies.

- [ ] **Step 2: Run RED test**

  Run: `uv run --locked pytest tests/libs/pcap_io/test_codec.py -q`

  Expected: missing parser import.

- [ ] **Step 3: Implement bounded parser**

  Read exactly one bounded block at a time, reject all malformed structure
  before yielding a record, preserve file order, and retain only IDB metadata
  needed by supported records. Reject unknown block types rather than silently
  repairing their semantics.

- [ ] **Step 4: Run GREEN test**

  Run: `uv run --locked pytest tests/libs/pcap_io/test_codec.py -q`

  Expected: valid fixtures decode exactly; each malformed fixture raises its
  documented typed error.

### Task 3: Preserve-filter and write canonical PCAPNG

**Files:**
- Modify: `src/trafficlab/libs/pcap_io/codec.py`
- Create: `src/trafficlab/libs/pcap_io/service.py`
- Test: `tests/libs/pcap_io/test_service.py`

- [ ] **Step 1: Write RED round-trip tests**

  Test predicate filtering preserves each retained `PacketRecord` exactly,
  output includes only referenced interfaces in canonical ID order, repeated
  writes are byte-identical, Scapy `RawPcapNgReader` accepts output, and an
  output limit/error leaves no claimed success.

- [ ] **Step 2: Run RED test**

  Run: `uv run --locked pytest tests/libs/pcap_io/test_service.py -q`

  Expected: missing filter/write public API.

- [ ] **Step 3: Implement pure filter and streaming writer**

  Do not sort or rewrite retained records. Emit one canonical little-endian
  SHB, deterministic IDBs with time resolution/offset options, and EPBs with
  original caplen/original length/ticks. Validate bytes, interfaces, and limits
  before every write.

- [ ] **Step 4: Run GREEN test**

  Run: `uv run --locked pytest tests/libs/pcap_io/test_service.py -q`

  Expected: round-trip and Scapy compatibility fixtures pass.

### Task 4: Verify and record roadmap evidence

**Files:**
- Modify: `architecture/libs/pcap_io/ROADMAP.md`

- [ ] **Step 1: Mark only PCAPNG I/O Stage 1, Step 1.1, and Substep 1.1.1 done**

  Add PCP-AC-001/002 evidence and exact full-quality command.

- [ ] **Step 2: Run full verification**

  Run: `PYTHONPATH=. UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv run --locked python tools/quality.py all`

  Expected: format, lint, Pyright, 100% coverage, docs, whitespace, and wheel
  build pass.

- [ ] **Step 3: Commit the one roadmap STEP**

  Stage only PCAPNG source, tests, plan, and PCAPNG roadmap. Commit:
  `feature(pcap-io): add supported PCapng core`.
