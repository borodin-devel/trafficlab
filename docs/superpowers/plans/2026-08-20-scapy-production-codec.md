# [PLAN-1-17420096] Scapy Production Codec Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Trafficlab's custom PCAPNG codec with Scapy 2.7.0 as the sole production reader and writer, adopt explicit rewrite-over-compatibility and no-license-gating policies, and publish schema-v4 deterministic and real-program evidence.

**Architecture:** One typed `trafficlab.scapy_io` boundary contains every dynamic Scapy object and returns only Trafficlab-owned values. All installed callers and offline tooling migrate to the new APIs; the legacy codec, compatibility names, Scapy probe decision, and licensing artifacts are deleted. Scapy-emitted bytes are immediately reparsed, and that reparsed `TrafficTrace` owns downstream publication, comparison, and evidence.

**Tech Stack:** CPython 3.12.3, uv, Scapy 2.7.0, NumPy, SciPy, Pydantic 2, Hypothesis, pytest, Ruff, Pyright, Docker Compose CLI.

**Spec:** `docs/superpowers/specs/2026-08-20-scapy-production-codec-design.md`

## [SECTION-1-9ebf0a36] Global Constraints

- Scapy 2.7.0 is a required runtime dependency and the only production PCAPNG implementation.
- `src/trafficlab/pcapng.py` and every old API name are deleted; do not add aliases, wrappers, fallback imports, backend selectors, or legacy modes.
- Only `src/trafficlab/scapy_io.py` may import Scapy inside the installed package; strict typed protocols isolate Scapy's dynamic API.
- `TrafficTrace` remains the immutable columnar scientific representation; emitted PCAPNG bytes are reparsed before publication or comparison.
- Accept Scapy's container syntax, timestamp precision, byte layout, and measured performance. Preserve Trafficlab's one-interface Ethernet policy, target-MAC directions, finite ordered trace constraints, deadlines, resource bounds, and stable error categories.
- Keep the independent valid-subset oracle under `tests/support/`; installed code may not import or package it.
- `SCIENTIFIC_ARTIFACT_SCHEMA_VERSION` becomes 4; schema-v3 best models and checkpoints fail with refit/regenerate actions and are never migrated in place.
- Historical accepted r6 and r21 evidence remains byte-for-byte unchanged. A new source-bound schema-v4 validation study becomes current only after detached offline audit.
- `architecture/DEVELOPMENT.md` states that backward compatibility is not a cornerstone, coherent rewrites are preferred when they improve the named qualities, no project license is required, and licenses do not gate dependencies.
- MMPP and pymoo probe decisions remain unchanged. Scapy probe/license/adoption machinery is removed and replaced by ordinary production tests plus non-gating diagnostics.
- Follow RED, bounded RED verification, minimal GREEN, focused verification, refactor, independent review, and a coherent local commit for every task.
- Maintain at least 90% branch-aware package coverage; defect-exposed functions require 100% executable-line and branch coverage.
- Production stays one Python process with the existing two capture containers, Docker Compose CLI, classical models, and no security or Node.js application subsystem.

## [SECTION-2-dfb9660d] File Responsibility Map

| Responsibility | Owning files |
| --- | --- |
| Development/license/compatibility policy | `architecture/DEVELOPMENT.md`, `tests/unit/test_package.py`, `tests/unit/test_development_policy.py` |
| Runtime dependency | `pyproject.toml`, `uv.lock` |
| Typed Scapy production boundary | `src/trafficlab/scapy_io.py`, `tests/unit/test_scapy_io.py` |
| Independent valid-subset oracle | `tests/support/pcapng_oracle.py` |
| Capture inspection | `src/trafficlab/capture_validation.py`, `tests/unit/test_capture_validation.py` |
| Generated publication | `src/trafficlab/artifacts.py`, `src/trafficlab/generation.py`, related unit/integration tests |
| Installed pipeline migration | `src/trafficlab/{preflight,fitting,comparison,run}.py`, CLI and pipeline tests |
| Legacy codec removal | delete `src/trafficlab/pcapng.py`, replace `tests/unit/test_pcapng.py` |
| Tooling and probe retirement | validation/fixture scripts, `scripts/run_scientific_stack_probes.py`, delete Scapy probe/license files |
| Production Scapy diagnostic | `scripts/benchmark_scapy_production.py`, `examples/scientific_stack/scapy_production_benchmark.json`, focused tests |
| Schema v4 and fixtures | `src/trafficlab/scientific_schema.py`, generators, `examples/schemas/scientific-artifact-v4/`, `examples/data/`, `tests/fixtures/data/` |
| Stable architecture and evidence text | `architecture/{SYSTEM,CAPTURE,TESTING,DEVELOPMENT}.md`, model/similarity docs, `docs/SCIENTIFIC_STACK_ADOPTION_EVIDENCE.md` |
| Durable example | `scripts/check_scientific_stack_example.py`, `examples/scientific_stack/example_run*`, focused tests |
| Real validation study | `scripts/{run_validation_study,audit_validation_study}.py`, `examples/validation_study/`, validation tests |

---

### Task 1: [TASK-1-a54fcfcf] Adopt runtime Scapy and explicit development policies

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `architecture/DEVELOPMENT.md`
- Modify: `tests/unit/test_package.py`

**Interfaces:**
- Produces: installed runtime import `scapy==2.7.0`; stable `License policy` and `Evolution and compatibility policy` sections.
- Preserves: uv as the only dependency interface and the existing bounded gate commands.

- [x] **[STEP-1-708121d4] Step 1: Write runtime dependency tests first**

```python
def test_scapy_is_runtime_only() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text())
    assert "scapy==2.7.0" in project["project"]["dependencies"]
    assert "scapy==2.7.0" not in project["dependency-groups"]["dev"]
```

Human-facing policy prose is reviewed directly; do not add source-text assertions for `DEVELOPMENT.md` or the absence of a license file.

- [x] **[STEP-2-7fb4be7f] Step 2: Run bounded RED and record both failures**

Run:

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_package.py
```

Expected: dependency placement and installed metadata fail because Scapy is development-only.

- [x] **[STEP-3-5569667c] Step 3: Move Scapy with uv and write the two stable policies**

Run dependency commands rather than editing the lock:

```bash
uv remove --dev scapy
uv add 'scapy==2.7.0'
```

Remove the duplicate development entry if uv retains it. Add policy prose matching the approved spec: no project license requirement; dependency licenses and limitations are not reviewed or adoption gates; backward compatibility is secondary to coherent rewrites improving simplicity, precision, reproducibility, configurability, or reliability; schema bumps, deterministic rejection, complete caller migration, fixtures, documentation, and evidence remain mandatory.

- [x] **[STEP-4-43fac217] Step 4: Prove a production-only environment imports Scapy**

Run:

```bash
uv sync --locked --no-group dev
uv run --locked --no-group dev python -c \
  'import importlib.metadata, scapy; assert importlib.metadata.version("scapy") == "2.7.0"'
uv sync --locked --all-groups
```

Expected: both syncs succeed and the runtime-only import reports no missing dependency.

- [x] **[STEP-5-57937ca3] Step 5: Verify policy, package, lock, format, and strict types**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_package.py
uv run --locked ruff format --check tests/unit/test_package.py
uv run --locked ruff check tests/unit/test_package.py
uv run --locked pyright tests/unit/test_package.py
git diff --check
```

Expected: focused tests and static checks pass; `scapy==2.7.0` appears once in project metadata.

- [x] **[STEP-6-db92462b] Step 6: Commit the runtime policy foundation**

```bash
git add pyproject.toml uv.lock architecture/DEVELOPMENT.md \
  tests/unit/test_package.py docs/superpowers/plans/2026-08-20-scapy-production-codec.md
git commit -m "build: require scapy at runtime"
```

### Task 2: [TASK-2-9c72b2e4] Implement the typed Scapy reader and independent oracle

**Files:**
- Create: `src/trafficlab/scapy_io.py`
- Create: `tests/support/pcapng_oracle.py`
- Create: `tests/unit/test_scapy_io.py`
- Modify: `tests/property/strategies.py`
- Modify: `tests/property/test_parser_and_schema_properties.py`

**Interfaces:**
- Produces: `PcapngPacket`, `read_pcapng_packets()`, `read_pcapng_bytes()`, and `read_pcapng()`.
- Consumes: `TrafficTrace`, `TraceEvent`, `CaptureMetadata`, `DeadlineExceededError`, and `TrafficlabError`.

- [x] **[STEP-7-9062f10f] Step 1: Write reader contracts and scalar-oracle tests**

```python
def test_reader_returns_owned_trace_and_exact_frames(valid_capture: bytes, metadata: CaptureMetadata) -> None:
    packets = read_pcapng_packets(BytesIO(valid_capture), metadata, source=Path("case.pcapng"))
    trace = read_pcapng_bytes(valid_capture, metadata, source=Path("case.pcapng"))
    oracle = oracle_trace(valid_capture, metadata)
    assert trace == oracle
    assert tuple(packet.event for packet in packets) == trace.to_events()
    assert tuple(len(packet.ethernet_frame) for packet in packets) == tuple(trace.frame_lengths)
    assert not trace.timestamps.flags.writeable


def test_reader_deadline_wins_after_one_packet(valid_capture: bytes, metadata: CaptureMetadata) -> None:
    ticks = iter((0.0, 0.0, 0.0, 1.0))
    with pytest.raises(DeadlineExceededError):
        read_pcapng_bytes(
            valid_capture,
            metadata,
            source=Path("deadline.pcapng"),
            deadline=1.0,
            clock=ticks.__next__,
        )
```

Cover little and big endian inputs, decimal and binary timestamp resolutions, IPv4/IPv6/ARP Ethernet frames, target/peer/broadcast sources, options/padding accepted by Scapy, interface/link-type errors, missing timestamps, decreasing timestamps, empty captures, dynamic exceptions, and path I/O errors.

- [x] **[STEP-8-a6bad60f] Step 2: Run bounded reader RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_scapy_io.py tests/property/test_parser_and_schema_properties.py
```

Expected: import failure for `trafficlab.scapy_io` and missing oracle helper.

- [x] **[STEP-9-d82dd6f5] Step 3: Implement typed reader boundaries**

Use locally defined protocols for Scapy packets, readers, interfaces, timestamp values, and factories. Define:

```python
@dataclass(frozen=True, slots=True)
class PcapngPacket:
    event: TraceEvent
    ethernet_frame: bytes


def read_pcapng_packets(
    source_input: Path | BinaryIO,
    metadata: CaptureMetadata,
    *,
    source: Path,
    deadline: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> tuple[PcapngPacket, ...]:
    return _read_scapy_packets(
        source_input,
        metadata,
        source=source,
        deadline=deadline,
        clock=clock,
    )
```

`read_pcapng_bytes()` passes `BytesIO(content)`; `read_pcapng()` passes the path. Convert each accepted packet explicitly to bytes, timestamp, direction, and captured frame length. Validate the resulting `TrafficTrace`; translate Scapy, type, arithmetic, and I/O exceptions into stable Trafficlab errors without importing test code.

- [x] **[STEP-10-38c82e13] Step 4: Extract only the valid-subset test oracle**

Move the minimum `struct`-based Section Header, Interface Description, Enhanced Packet, timestamp-resolution, and Ethernet direction calculations needed by valid fixtures into `tests/support/pcapng_oracle.py`. It must expose only:

```python
def oracle_trace(content: bytes, metadata: CaptureMetadata) -> TrafficTrace:
    events = _parse_valid_enhanced_packets(content, metadata)
    return TrafficTrace.from_events(events)
```

The oracle must not import Scapy or `trafficlab.scapy_io`, implement writing, or define production error behavior.

- [x] **[STEP-11-542c5682] Step 5: Verify reader behavior and defect-function coverage**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_scapy_io.py \
  tests/property/test_parser_and_schema_properties.py
uv run --locked pytest -q -n 0 tests/unit/test_scapy_io.py \
  --cov=trafficlab.scapy_io --cov-branch --cov-report=term-missing
uv run --locked ruff format --check src/trafficlab/scapy_io.py \
  tests/support/pcapng_oracle.py tests/unit/test_scapy_io.py
uv run --locked ruff check src/trafficlab/scapy_io.py \
  tests/support/pcapng_oracle.py tests/unit/test_scapy_io.py
uv run --locked pyright src/trafficlab/scapy_io.py tests/support/pcapng_oracle.py \
  tests/unit/test_scapy_io.py
```

Expected: reader suite passes; every reader function exposed by a RED failure has 100% lines/branches.

- [x] **[STEP-12-9b7b4180] Step 6: Commit the production reader**

```bash
git add src/trafficlab/scapy_io.py tests/support/pcapng_oracle.py \
  tests/unit/test_scapy_io.py tests/property/strategies.py \
  tests/property/test_parser_and_schema_properties.py
git commit -m "feat: add production scapy reader"
```

### Task 3: [TASK-3-a9f7cb88] Implement deterministic Scapy writing and reparsed output

**Files:**
- Modify: `src/trafficlab/scapy_io.py`
- Modify: `src/trafficlab/artifacts.py`
- Modify: `src/trafficlab/generation.py`
- Modify: `tests/unit/test_scapy_io.py`
- Modify: `tests/unit/test_artifacts.py`
- Modify: `tests/integration/test_generate_cli.py`

**Interfaces:**
- Produces: frozen `EncodedPcapng(content: bytes, trace: TrafficTrace)` and `encode_pcapng()`.
- Changes: generated publication accepts Scapy bytes and the reparsed emitted trace; nanosecond-specific quantization helpers are removed.

- [x] **[STEP-13-3191eef7] Step 1: Write writer, determinism, and authority tests**

```python
def test_encode_returns_exact_bytes_and_reparsed_authoritative_trace(metadata: CaptureMetadata) -> None:
    original = TrafficTrace.from_events((TraceEvent(0.000000123, Direction.OUTBOUND, 64),))
    encoded = encode_pcapng(original, metadata, observation_window_seconds=1.0)
    assert encoded.content.startswith(b"\x0a\x0d\x0d\x0a")
    assert encoded.trace == read_pcapng_bytes(encoded.content, metadata, source=Path("generated.pcapng"))
    assert encoded.trace.timestamps[0] != original.timestamps[0]


def test_identical_locked_writes_are_byte_identical(metadata: CaptureMetadata) -> None:
    trace = TrafficTrace.from_events((TraceEvent(0.25, Direction.INBOUND, 64),))
    assert encode_pcapng(trace, metadata, observation_window_seconds=1.0).content == \
        encode_pcapng(trace, metadata, observation_window_seconds=1.0).content
```

Cover both directions, target/peer MAC roles, 14-byte minimum, uint32 limits, closed-window checks, nonfinite/decreasing inputs, writer I/O/type/dynamic errors, flush/close, and exact post-write reparse.

- [x] **[STEP-14-5fff5ec5] Step 2: Run bounded writer RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_scapy_io.py tests/unit/test_artifacts.py \
  tests/integration/test_generate_cli.py
```

Expected: `EncodedPcapng`/`encode_pcapng` are absent and nanosecond equality assumptions fail.

- [x] **[STEP-15-271ed263] Step 3: Implement deterministic Scapy encoding**

Define:

```python
@dataclass(frozen=True, slots=True)
class EncodedPcapng:
    content: bytes
    trace: TrafficTrace


def encode_pcapng(
    trace: TrafficTrace,
    metadata: CaptureMetadata,
    *,
    observation_window_seconds: float,
) -> EncodedPcapng:
    validated = _validate_encoding_input(trace, observation_window_seconds)
    content = _write_scapy_bytes(validated, metadata)
    reparsed = read_pcapng_bytes(content, metadata, source=Path("generated.pcapng"))
    return EncodedPcapng(content=content, trace=reparsed)
```

Use one owned temporary file, `PcapNgWriter`, explicit `Ether` frames, deterministic zero payloads, explicit `sec`, `caplen`, and `wirelen`, then close, read bytes, and call `read_pcapng_bytes()`. Reject empty output, out-of-window reparsed timestamps, changed directions/frame lengths, and nondeterministic ambient fields. Return only reparsed output.

- [x] **[STEP-16-0692d552] Step 4: Make generated publication consume reparsed authority**

Replace nanosecond `quantize_generated_trace()`/`quantize_generated_events()` ownership with `EncodedPcapng.trace`. `reproduce_generated_pcapng()` returns the raw model trace plus one `EncodedPcapng`; `publish_generated_pcapng()` validates exact content by reparsing through Scapy and compares against the encoded trace. Generation result, logs, and comparison consume that trace.

- [x] **[STEP-17-4d8975b9] Step 5: Verify writer, publication, CLI, and exact coverage**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_scapy_io.py \
  tests/unit/test_artifacts.py tests/unit/test_run.py \
  tests/integration/test_generate_cli.py
uv run --locked pytest -q -n 0 tests/unit/test_scapy_io.py tests/unit/test_artifacts.py \
  --cov=trafficlab.scapy_io --cov=trafficlab.artifacts --cov-branch --cov-report=term-missing
uv run --locked ruff format --check src/trafficlab/scapy_io.py \
  src/trafficlab/artifacts.py src/trafficlab/generation.py
uv run --locked ruff check src/trafficlab/scapy_io.py \
  src/trafficlab/artifacts.py src/trafficlab/generation.py
uv run --locked pyright src/trafficlab/scapy_io.py src/trafficlab/artifacts.py \
  src/trafficlab/generation.py
```

Expected: all focused paths pass, repeated bytes match, and RED-exposed functions have full line/branch coverage.

- [x] **[STEP-18-39c481a0] Step 6: Commit authoritative Scapy output**

```bash
git add src/trafficlab/scapy_io.py src/trafficlab/artifacts.py \
  src/trafficlab/generation.py tests/unit/test_scapy_io.py \
  tests/unit/test_artifacts.py tests/integration/test_generate_cli.py
git commit -m "feat: write generated traffic with scapy"
```

### Task 4: [TASK-4-406f729a] Migrate every installed caller and delete the legacy codec

**Files:**
- Delete: `src/trafficlab/pcapng.py`
- Delete: `tests/unit/test_pcapng.py`
- Modify: `src/trafficlab/{capture_validation,preflight,fitting,comparison,run}.py`
- Modify: `tests/docker/test_run_docker.py`
- Modify: `tests/integration/{test_capture_pipeline,test_compare_cli,test_generate_cli,test_genetic_fitting,test_model_pipeline,test_pipeline_equivalence}.py`
- Modify: `tests/property/{strategies,test_parser_and_schema_properties}.py`
- Modify: `tests/scientific/test_model_validation.py`
- Modify: `tests/unit/{test_artifacts,test_capture,test_capture_validation,test_docker_preflight,test_failure_outcome_public_matrix,test_fit_fixture_generator,test_fitting,test_run,test_similarity_artifact}.py`
- Modify: `tests/unit/validation_study/test_audit.py`
- Modify: `tests/unit/test_package.py`

**Interfaces:**
- Consumes: Task 2/3 `trafficlab.scapy_io` APIs.
- Produces: installed package with no old codec/import and one Scapy path for capture, fit, generate, compare, and run.

- [x] **[STEP-19-130480a1] Step 1: Write deletion and caller-migration tests**

```python
def test_legacy_pcapng_module_is_removed() -> None:
    assert importlib.util.find_spec("trafficlab.pcapng") is None


def test_only_scapy_io_imports_scapy() -> None:
    offenders = production_files_importing("scapy")
    assert offenders == {Path("src/trafficlab/scapy_io.py")}
```

Update capture inspection expectations to use `PcapngPacket`. Add an end-to-end offline `fit -> generate -> compare` assertion that reparses both reference and generated bytes through `trafficlab.scapy_io` and that no old name is monkeypatched.

- [x] **[STEP-20-3953aab9] Step 2: Run migration RED before deleting code**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 tests/unit/test_package.py \
  tests/unit/test_capture_validation.py tests/unit/test_fitting.py \
  tests/unit/test_comparison.py tests/unit/test_run.py \
  tests/integration/test_model_pipeline.py tests/integration/test_pipeline_equivalence.py
```

Expected: legacy-module removal assertion fails and callers still enter `trafficlab.pcapng`.

- [x] **[STEP-21-928a8a83] Step 3: Replace installed imports and packet inspection**

Replace each import from `trafficlab.pcapng` in `src/trafficlab` with the exact Task 2/3 API. Capture inspection consumes `PcapngPacket.ethernet_frame`; fitting/comparison/run use `read_pcapng_bytes()` or `read_pcapng()`; generation consumes `EncodedPcapng`. Preserve atomicity, identity checks, stage arbitration, and deadlines.

- [x] **[STEP-22-fdbb49b4] Step 4: Delete the old module and legacy-only tests**

Delete `src/trafficlab/pcapng.py` and `tests/unit/test_pcapng.py`. Move valid reader/writer cases into `tests/unit/test_scapy_io.py`; move only independent valid-subset calculations into the test oracle. Remove legacy exact bytes, padding rejection, nanosecond equality, old import names, and monkeypatch points.

Run the structural guard:

```bash
test -z "$(rg -l 'trafficlab\.pcapng|parse_pcapng|encode_pcapng_trace|write_pcapng' src tests \
  --glob '!tests/support/pcapng_oracle.py')"
```

- [x] **[STEP-23-04bbaa2e] Step 5: Verify the installed full workflow and package boundary**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_package.py \
  tests/unit/test_capture_validation.py tests/unit/test_fitting.py \
  tests/unit/test_comparison.py tests/unit/test_run.py \
  tests/integration/test_capture_pipeline.py tests/integration/test_model_pipeline.py \
  tests/integration/test_pipeline_equivalence.py
uv run --locked ruff format --check src tests
uv run --locked ruff check src tests
uv run --locked pyright
```

Expected: all selected workflows pass, no old module is importable, and only `scapy_io.py` imports Scapy.

- [x] **[STEP-24-92e8a103] Step 6: Commit the breaking codec replacement**

```bash
git add -A src/trafficlab tests
git commit -m "refactor!: replace pcapng codec with scapy"
```

Commit body:

```text
BREAKING CHANGE: trafficlab.pcapng and its parse/encode APIs are removed.
Use trafficlab.scapy_io and schema-v4 artifacts.
```

### Task 5: [TASK-5-fb061ee1] Migrate tooling and retire the Scapy probe/license workflow

**Files:**
- Modify: `scripts/{audit_validation_study,check_scientific_stack_example,generate_fit_fixtures,generate_model_fixtures,generate_similarity_fixtures,generate_validation_study_fixture,run_validation_study}.py`
- Modify: `scripts/run_scientific_stack_probes.py`
- Create: `scripts/benchmark_scapy_production.py`
- Create: `tests/unit/test_scapy_production_benchmark.py`
- Modify: `tests/unit/test_scientific_stack_probe_runner.py`
- Delete: `tests/scientific/probes/scapy_pcapng.py`
- Delete: `tests/scientific/probes/test_scapy_pcapng.py`
- Delete: `examples/scientific_stack/scapy_cases.json`
- Delete: `examples/scientific_stack/SCAPY_LICENSE_DECISION.md`
- Create: `examples/scientific_stack/scapy_production_benchmark.json`

**Interfaces:**
- Produces: MMPP/pymoo-only optional probe runner and non-gating production Scapy diagnostic.
- Preserves: offline audit independence, deterministic check modes, and recorded host-dependent raw samples without adoption/license fields.

- [x] **[STEP-25-bddc04ac] Step 1: Write tooling migration and retirement tests**

```python
def test_all_optional_probes_exclude_production_scapy() -> None:
    assert selected_probe_names("all") == ("mmpp", "pymoo")


def test_scapy_production_diagnostic_has_no_gate_or_license_fields() -> None:
    record = load_diagnostic()
    assert record["codec"] == "scapy-2.7.0"
    assert record["production"] is True
    assert not ({"license", "decision", "gates", "production_adoption"} & record.keys())
```

Add import-guard tests proving every script imports `trafficlab.scapy_io`, never the deleted module or test probe. Add tamper tests for raw samples, medians, input/trace identities, source/lock hashes, commands, and frame counts.

- [x] **[STEP-26-3e1c3644] Step 2: Run bounded tooling RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_scientific_stack_probe_runner.py \
  tests/unit/test_scapy_production_benchmark.py \
  tests/unit/validation_study/test_audit.py
```

Expected: Scapy remains in the optional runner, the production diagnostic is absent, and scripts import old APIs.

- [x] **[STEP-27-8f6bf1ab] Step 3: Migrate scripts and remove probe/license decisions**

Replace script imports with `trafficlab.scapy_io`. Restrict `ProbeName` and CLI choices to `mmpp`/`pymoo`; `--probe scapy` must be rejected by argparse. Delete Scapy probe implementation/tests, `scapy_cases.json`, and license decision. Remove license/adoption wording from active docs/tests rather than preserving deprecated fields.

- [x] **[STEP-28-9710ebaf] Step 4: Implement the non-gating production diagnostic**

`benchmark_scapy_production.py` generates fixed 100,000- and 1,000,000-frame inputs and records five post-warmup samples for `read_pcapng()` plus `encode_pcapng()`. Its canonical JSON records codec/runtime/source/lock identities, exact bounded commands, raw wall/RSS samples, input hashes, trace hashes, medians, and deterministic byte/trace agreement. `--check` validates retained arithmetic and identities but does not rerun host timing. It contains no threshold, pass/fail, adoption, or license field.

- [x] **[STEP-29-dd20b4a0] Step 5: Verify tooling, audit callers, and diagnostic bytes**

```bash
uv run --locked pytest -q -n 0 \
  tests/unit/test_scientific_stack_probe_runner.py \
  tests/unit/test_scapy_production_benchmark.py
uv run --locked python scripts/run_scientific_stack_probes.py --probe all --check
uv run --locked python scripts/benchmark_scapy_production.py --check
test -z "$(rg -l 'trafficlab\.pcapng|scientific\.probes\.scapy_pcapng|SCAPY_LICENSE_DECISION' scripts tests src \
  | rg -v '^tests/unit/test_package.py$')"
uv run --locked ruff format --check scripts tests
uv run --locked ruff check scripts tests
uv run --locked pyright scripts tests
```

Expected: optional probes and diagnostic check pass; deleted probe/license references are absent.

Ruling: full auditor and durable-example behavior runs in Tasks 6 and 7 after their schema-v4/generated-byte owners regenerate stale schema-v3 fixtures. Task 5 proves script imports and strict types now; if this dependency ordering is wrong, a tooling defect surfaces one task later, but no evidence can be accepted before those tests pass.

- [x] **[STEP-30-4c5bc556] Step 6: Commit tooling migration and probe retirement**

```bash
git add -A scripts tests examples/scientific_stack
git commit -m "refactor: retire scapy adoption probe"
```

### Task 6: [TASK-6-31d08e5c] Bump schema v4 and regenerate deterministic fixtures

**Files:**
- Modify: `src/trafficlab/scientific_schema.py`
- Modify: best-model/checkpoint/schema tests and generators
- Delete: `examples/schemas/scientific-artifact-v3/`
- Create: `examples/schemas/scientific-artifact-v4/`
- Regenerate: `examples/data/`, `tests/fixtures/data/`, fixture manifests
- Modify: fixture READMEs and exact-byte assertions

**Interfaces:**
- Produces: `SCIENTIFIC_ARTIFACT_SCHEMA_VERSION = 4` and deterministic Scapy-produced fixtures.
- Rejects: every schema-v3 best model/checkpoint/current-run reuse with exact refit instructions.

- [ ] **[STEP-31-166432a3] Step 1: Write schema-v4 and stale-artifact RED tests**

```python
def test_current_scientific_schema_is_four() -> None:
    assert SCIENTIFIC_ARTIFACT_SCHEMA_VERSION == 4


@pytest.mark.parametrize("version", [None, 2, 3, 5, True])
def test_schema_v3_and_other_noncurrent_artifacts_require_refit(version: object) -> None:
    with pytest.raises(ScientificArtifactSchemaError, match="incompatible") as caught:
        require_current_scientific_schema(version, artifact="checkpoint")
    assert caught.value.corrective_action == "refit under the current schema in a new run directory"
```

Assert all generated PCAPNG fixtures reparse through Scapy and repeated generator runs retain identical hashes.

- [ ] **[STEP-32-4caabb0a] Step 2: Run bounded schema/fixture RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 tests/unit/test_scientific_rng.py \
  tests/unit/genetic/test_checkpoint.py tests/unit/models/test_registry.py \
  tests/unit/test_artifact_schema_generator.py tests/unit/models/test_fixture_generator.py
```

Expected: version remains 3, schema-v3 artifacts are accepted, and checked bytes reflect the old codec.

- [ ] **[STEP-33-1fe65021] Step 3: Bump schema and update every strict literal**

Set the global constant to 4. Update `Literal[3]`, expected schema paths, scientific environment/report fixtures, checkpoint/best-model constructors, compatibility assertions, and corrective-action tests. Do not add a schema migration path or codec selector field.

- [ ] **[STEP-34-cd499ca2] Step 4: Regenerate all deterministic artifacts twice**

Run the owning generators:

```bash
uv run --locked python scripts/generate_similarity_fixtures.py
uv run --locked python scripts/generate_model_fixtures.py
uv run --locked python scripts/generate_fit_fixtures.py
uv run --locked python scripts/generate_validation_study_fixture.py
uv run --locked python scripts/generate_artifact_schemas.py
uv run --locked python scripts/check_fixture_layout.py --write-manifest
```

Record `sha256sum` for every generated file, rerun the same commands, and require the inventories and hashes to be identical. Remove only generator-owned v3 outputs; do not alter historical accepted r6/r21 bundles.

- [ ] **[STEP-35-5dfc21fe] Step 5: Verify schemas, fixtures, round trips, and history immutability**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_scientific_rng.py \
  tests/unit/genetic/test_checkpoint.py tests/unit/models/test_registry.py \
  tests/unit/models/test_fixture_generator.py tests/unit/test_fit_fixture_generator.py \
  tests/unit/test_artifact_schema_generator.py tests/integration/test_genetic_fitting.py
uv run --locked python scripts/generate_similarity_fixtures.py --check
uv run --locked python scripts/generate_model_fixtures.py --check
uv run --locked python scripts/generate_fit_fixtures.py --check
uv run --locked python scripts/generate_validation_study_fixture.py --check
uv run --locked python scripts/generate_artifact_schemas.py --check
uv run --locked python scripts/check_fixture_layout.py --check
git diff --exit-code HEAD -- examples/validation_study/evidence/2026-08-20-stack-adoption-r6 \
  examples/validation_study/evidence/2026-08-18-research-fitness-r21
```

Expected: focused tests/checks pass and historical bundles have no diff.

- [ ] **[STEP-36-a208a8c2] Step 6: Commit schema-v4 deterministic evidence**

```bash
git add -A src/trafficlab/scientific_schema.py examples/data examples/schemas \
  tests/fixtures tests/unit tests/integration scripts
git commit -m "feat: publish scapy schema v4 fixtures"
```

### Task 7: [TASK-7-7b8f8cfa] Rebuild the durable example and stable architecture

**Files:**
- Modify: `scripts/check_scientific_stack_example.py`
- Regenerate: `examples/scientific_stack/example_run.json`
- Regenerate: `examples/scientific_stack/example_run_artifacts/`
- Modify: `architecture/{SYSTEM,CAPTURE,TESTING,DEVELOPMENT}.md`
- Modify: `architecture/traffic_models/README.md`
- Modify: `architecture/similarity_methods/README.md`
- Modify: `docs/SCIENTIFIC_STACK_ADOPTION_EVIDENCE.md`
- Modify: `README.md`, `examples/data/README.md`, `examples/validation_study/README.md`
- Modify: `tests/unit/test_scientific_stack_example_run.py`

**Interfaces:**
- Produces: one clean schema-v4 Scapy durable example and authoritative architecture with no legacy/license claims.
- Preserves: exact checkpoint/refit/generation/comparison recomputation and clean project-scoped Docker teardown.

- [ ] **[STEP-37-3f0c548a] Step 1: Write durable-example and documentation RED tests**

```python
def test_example_is_schema_v4_and_regenerates_exact_scapy_bytes() -> None:
    evidence = load_example_evidence()
    assert json.loads(artifact_bytes("best_model.json"))["schema_version"] == 4
    assert derive_example_result() == evidence["result"]
    assert regenerate_generated_bytes() == artifact_bytes("generated.pcapng")


def test_active_docs_name_one_scapy_codec_and_no_license_gate() -> None:
    active = read_active_architecture_and_evidence()
    assert "trafficlab.scapy_io" in active
    assert "development-only Scapy" not in active
    assert "license compatibility" not in active.lower()
```

- [ ] **[STEP-38-c14d89ca] Step 2: Run bounded example/docs RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_scientific_stack_example_run.py tests/unit/test_package.py
uv run --locked python scripts/check_scientific_stack_example.py --check
```

Expected: retained example is schema 3/old bytes and active docs describe a rejected development-only probe.

- [ ] **[STEP-39-cf3d2126] Step 3: Update architecture and evidence claims**

Document the new Scapy API/data flow, accepted timestamp/container semantics, reparsed output authority, test-only oracle, schema-v4 incompatibility, diagnostic-only performance, no-license policy, and rewrite-over-compatibility policy. Remove active statements that the custom codec is production or Scapy is rejected. Preserve historical descriptions only when explicitly labeled as superseded evidence.

- [ ] **[STEP-40-ac598dde] Step 4: Freeze source, then run and retain a clean durable example**

Commit the final implementation and checker source before the run:

```bash
git add README.md architecture docs/SCIENTIFIC_STACK_ADOPTION_EVIDENCE.md \
  scripts/check_scientific_stack_example.py tests/unit/test_scientific_stack_example_run.py
git commit -m "feat: finalize production scapy source"
test -z "$(git status --porcelain=v1 --untracked-files=all)"
```

From that exact clean source, build/use the locked capture image, run the bounded `examples/scientific_stack/experiment.toml` workflow against the explicit Wikimedia HTTPS URL, retain the exact nine artifacts, and generate `example_run.json`. The checker must refit, encode through Scapy, byte-compare, reparse, recompute all four similarity methods, validate path relocation, and verify empty exact-label Docker inventories.

- [ ] **[STEP-41-e74e04b7] Step 5: Verify example, architecture, and source binding**

```bash
uv run --locked pytest -q -n 0 tests/unit/test_scientific_stack_example_run.py \
  tests/unit/test_package.py tests/integration/test_run_pipeline.py
uv run --locked python scripts/check_scientific_stack_example.py --check
test -z "$(rg -l 'trafficlab\.pcapng|SCAPY_LICENSE_DECISION' README.md architecture docs \
  examples --glob '!docs/superpowers/specs/2026-08-19-scientific-stack-adoption-design.md' \
  --glob '!docs/superpowers/plans/2026-08-19-scientific-stack-adoption.md' \
  --glob '!examples/validation_study/evidence/**')"
git diff --check
```

Expected: current docs/example use Scapy/schema v4; explicitly historical documents/bundles remain unchanged.

- [ ] **[STEP-42-4b7ec698] Step 6: Commit durable Scapy example and architecture**

```bash
git add README.md architecture docs/SCIENTIFIC_STACK_ADOPTION_EVIDENCE.md \
  examples/scientific_stack examples/data/README.md examples/validation_study/README.md \
  scripts/check_scientific_stack_example.py tests/unit/test_scientific_stack_example_run.py
git commit -m "docs: record production scapy workflow"
```

### Task 8: [TASK-8-995e6709] Prove in-process and Docker pipeline reliability

**Files:**
- Modify: Docker, integration, scientific, property, and unit tests affected by Scapy/schema v4
- Modify: `tests/support/docker.py`, `tests/support/validation_study.py` only where new artifacts require it
- Modify: `architecture/TESTING.md` when test contracts change

**Interfaces:**
- Consumes: sole Scapy production path and regenerated schema-v4 fixtures.
- Produces: complete offline and Docker workflow proof with exact cleanup and failure arbitration.

- [ ] **[STEP-43-0293b4f2] Step 1: Add full-workflow failure and reuse regressions**

Add tests proving capture success, malformed Scapy input, reader deadline, fit/generate/compare failures, interrupted runs, reuse, and full success preserve the existing authoritative failure matrix and cleanup behavior. The successful Docker run must assert all nine artifacts, schema 4, Scapy-reparsed generated trace, and no project-labeled containers/networks/volumes.

```python
assert result.trace == read_pcapng(result.generated_path, metadata)
assert best_model.schema_version == 4
assert docker_inventory(project_name) == {"containers": [], "networks": [], "volumes": []}
```

- [ ] **[STEP-44-a67e53a4] Step 2: Run focused in-process RED**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/integration/test_capture_pipeline.py tests/integration/test_run_pipeline.py \
  tests/integration/test_pipeline_equivalence.py tests/scientific/test_model_validation.py
```

Expected: old imports/schema/byte assumptions fail until every boundary uses Scapy output.

- [ ] **[STEP-45-85e63fac] Step 3: Repair only Scapy/schema-dependent expectations**

Update tests and support code to construct/read through `trafficlab.scapy_io`, use reparsed traces, and assert schema 4. Preserve model equations, PCG64 draw order, similarity tolerances, atomic publication, stage arbitration, and project-scoped cleanup unchanged. Do not relax a scientific tolerance merely because PCAPNG timestamp precision changed; compare the actual emitted trace.

- [ ] **[STEP-46-06aa7209] Step 4: Run focused Docker and Internet-capability tests**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m docker \
  tests/docker/test_capture_docker.py tests/docker/test_capture_failures.py \
  tests/docker/test_run_docker.py
```

Expected: every selected real Docker case passes and exact project-label inventories are empty afterward.

- [ ] **[STEP-47-5a2b4dad] Step 5: Verify Ordinary, targeted coverage, and static quality**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not docker and not internet" --durations=50
uv run --locked pytest -q -n 0 tests/unit/test_scapy_io.py \
  tests/integration/test_capture_pipeline.py tests/integration/test_run_pipeline.py \
  --cov=trafficlab.scapy_io --cov-branch --cov-report=term-missing
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
```

Expected: Ordinary and static gates pass; Scapy boundary defect functions have 100% lines/branches.

- [ ] **[STEP-48-1e21a2d4] Step 6: Commit complete pipeline migration**

```bash
git add tests architecture/TESTING.md
git commit -m "test: validate production scapy pipeline"
```

### Task 9: [TASK-9-04e17a3b] Generate and audit new real-program validation evidence

**Files:**
- Create: `examples/validation_study/evidence/2026-08-20-scapy-production-r1/`
- Modify: `examples/validation_study/{README,REPORT}.md`
- Modify: `docs/SCIENTIFIC_STACK_ADOPTION_EVIDENCE.md`
- Modify: study tests that identify the current accepted bundle

**Interfaces:**
- Produces: one accepted schema-v4 Scapy study bound to the final implementation source commit.
- Preserves: historical r6/r21 bytes and failed-attempt immutability.

- [ ] **[STEP-49-f9c2a123] Step 1: Write current-study and historical-immutability RED tests**

```python
def test_current_study_is_schema_v4_scapy_production() -> None:
    bundle = accepted_bundle("2026-08-20-scapy-production-r1")
    assert bundle.environment.scientific_schema == 4
    assert audit(bundle).accepted


def test_historical_r6_and_r21_are_unchanged_from_mvp3() -> None:
    assert git_diff("MVP_3", "HEAD", R6_PATH, R21_PATH) == b""
```

- [ ] **[STEP-50-f0e52748] Step 2: Run evidence RED before collection**

```bash
uv run --locked pytest -vv -x -n 0 tests/unit/test_study_evidence.py \
  tests/unit/validation_study/test_audit.py tests/integration/test_validation_study_pipeline.py
```

Expected: the new accepted path does not exist and current navigation still names r6.

- [ ] **[STEP-51-d599accd] Step 3: Freeze the implementation source and run prerequisites**

Commit every implementation/test/doc change except the new accepted bundle. Require a clean tree, then run:

```bash
export TRAFFICLAB_INTERNET_URL='https://upload.wikimedia.org/wikipedia/commons/5/5b/SPACE_ELECTRIC_ROCKET_TEST%2C_SERT_II_IN_TANK_5_%28GRC-1968-C-03031%29.jpg'
export HYPOTHESIS_STORAGE_DIRECTORY='/tmp/trafficlab-hypothesis-scapy-production-r1'
export STUDY_ID='2026-08-20-scapy-production-r1'
test -z "$(git status --porcelain=v1 --untracked-files=all)"
uv run --locked python scripts/run_validation_study.py prerequisites \
  --url "$TRAFFICLAB_INTERNET_URL" --study-id "$STUDY_ID"
```

Expected: Docker and Internet prerequisite records pass against the exact source/lock/schema/image identities. Any failure consumes r1; use r2 without reusing bytes.

- [ ] **[STEP-52-f0cb2407] Step 4: Collect, audit, and publish the complete candidate**

```bash
uv run --locked python scripts/run_validation_study.py collect \
  --url "$TRAFFICLAB_INTERNET_URL" --study-id "$STUDY_ID" \
  --prerequisites examples/validation_study/prerequisites.json
UV_OFFLINE=1 scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/.candidates/"$STUDY_ID" --repository .
UV_OFFLINE=1 uv run --locked --offline python scripts/run_validation_study.py publish \
  --study-id "$STUDY_ID" \
  --candidate examples/validation_study/evidence/.candidates/"$STUDY_ID"
```

Expected: nine training runs, nine fresh simulations, three held-out evaluations, bootstrap records, manifest, index, lifecycle, report inputs, and report publish once.

- [ ] **[STEP-53-66ca59b7] Step 5: Audit from a detached regular-copy clone and verify history**

Create a `git clone --no-local --no-hardlinks --no-checkout`, detach it at the bundle's recorded source commit, copy the accepted bundle with `cp -a --reflink=never`, prove no alternates/symlinks/link-count-above-one, then run the bounded offline audit. Also run the study test matrix and exact `git diff MVP_3 --` checks for r6/r21.

- [ ] **[STEP-54-aaf17844] Step 6: Commit accepted Scapy validation evidence**

```bash
git add examples/validation_study docs/SCIENTIFIC_STACK_ADOPTION_EVIDENCE.md \
  tests/unit/test_study_evidence.py tests/unit/validation_study \
  tests/integration/test_validation_study_pipeline.py
git commit -m "docs: publish scapy validation study"
```

### Task 10: [TASK-10-801e64d0] Run release gates, final review, and finish the branch

**Files:**
- Modify: this plan only to mark verified steps complete
- Modify: any file required by Critical/Important review findings through reviewed fix rounds

**Interfaces:**
- Consumes: every prior task and accepted bundle.
- Produces: clean reviewed branch with every checkbox accurate and all release gates retained locally.

- [ ] **[STEP-55-c1632cf1] Step 1: Run locked sync, format, lint, and strict types**

```bash
uv sync --locked --all-groups
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
```

Expected: 0 formatting changes, lint errors, type errors, warnings, or informations.

- [ ] **[STEP-56-91235279] Step 2: Run canonical Ordinary and Coverage gates separately**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not docker and not internet" --durations=50
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not docker and not internet" --cov=trafficlab --cov-branch \
  --cov-report=term-missing --cov-fail-under=90 --durations=50
```

Expected: both select every offline test exactly once; total branch coverage is at least 90%.

- [ ] **[STEP-57-65b98120] Step 3: Run every deterministic checker and detached audit**

```bash
uv run --locked python scripts/generate_similarity_fixtures.py --check
uv run --locked python scripts/generate_model_fixtures.py --check
uv run --locked python scripts/generate_fit_fixtures.py --check
uv run --locked python scripts/generate_validation_study_fixture.py --check
uv run --locked python scripts/generate_artifact_schemas.py --check
uv run --locked python scripts/measure_scientific_stack_reduction.py --check
uv run --locked python scripts/benchmark_scientific_stack.py --check
uv run --locked python scripts/benchmark_scapy_production.py --check
uv run --locked python scripts/check_scientific_stack_example.py --check
uv run --locked python scripts/run_scientific_stack_probes.py --probe all --check
uv run --locked python scripts/check_fixture_layout.py --check
```

Run the Task 9 detached regular-copy audit again. Expected: every checker and audit exits zero.

- [ ] **[STEP-58-daf4f6b9] Step 4: Run combined serial Docker and Internet release gate**

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m "docker or internet" \
  --internet-url 'https://upload.wikimedia.org/wikipedia/commons/5/5b/SPACE_ELECTRIC_ROCKET_TEST%2C_SERT_II_IN_TANK_5_%28GRC-1968-C-03031%29.jpg'
```

Expected: every selected test runs and passes; exact Compose-label container/network/volume inventories are empty afterward.

- [ ] **[STEP-59-6e2be932] Step 5: Obtain independent whole-branch review and resolve findings**

Generate a review package from merge base `c980e9f` to HEAD. Review against the approved spec, this plan, deferred findings, production-only dependency install, deleted API/module, Scapy reader/writer semantics, test-only oracle independence, schema v4, fixture determinism, real study, history immutability, and release evidence. Fix every Critical/Important finding through bounded TDD and one scoped re-review per round; record any Minor disposition.

- [ ] **[STEP-60-933ba4a9] Step 6: Record completion and invoke branch finishing**

Mark every checkbox only after its evidence exists. Commit the plan update:

```bash
git add docs/superpowers/plans/2026-08-20-scapy-production-codec.md
git commit -m "docs: record scapy production validation"
git status --porcelain --untracked-files=all
```

Expected: clean worktree, all local commits retained, no old codec/import/license gate, and final review has no Critical/Important findings. Then use `superpowers:finishing-a-development-branch`; do not merge, push, tag, publish externally, or delete the worktree without the user's integration choice.

## [SECTION-3-d8331707] Delivery

Implementation remains on `feature/scapy-production` until all ten tasks and final review pass. No task may preserve the legacy codec to make migration easier. Historical accepted evidence is immutable; only the new schema-v4 bundle becomes current. External integration is a separate user decision after the clean branch-finishing gate.
