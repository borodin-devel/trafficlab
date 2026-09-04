# Imported Reference Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `trafficlab import-run EXPERIMENT DUMP_DIRECTORY` so one exact supplied PCAP/PCAPNG plus `capture.json` can be normalized entirely in process and run through fit, generate, and compare without Docker, subprocesses, or repository scripts.

**Architecture:** Preserve the existing CLI, coordinator, capture-pair publisher, and nine-artifact run contract. Refactor the sole Scapy boundary into a compatibility-preserving package, add raw-frame PCAP/PCAPNG normalization with a disk spool and stable timestamp index, then add imported-source lineage/reuse as a new acquisition dependency for the existing coordinator.

**Tech Stack:** Python 3.12, Scapy 2.7, Pydantic 2, NumPy 2, pytest/Hypothesis, Ruff, strict Pyright, uv.

**Spec:** `docs/superpowers/specs/2026-09-04-import-run-design.md`

<!-- label_creation_ns: 1788526311315634885 -->

## Global Constraints

- Work only on `feature/import-run-v2` in `.worktrees/import-run-v2`, based on local `main` commit `c503f773e0fc0f15655e659c9c4af4884220cbcc`.
- Use `apply_patch` for hand-authored edits and `uv` for every Python dependency or command.
- Add no runtime dependency and no configuration or scientific-artifact schema version.
- `import-run` must never import or execute repository scripts, spawn subprocesses, invoke Wireshark executables, invoke a shell, or touch Docker.
- Keep Scapy behind the single `trafficlab.common.scapy_io` package boundary.
- Preserve exact captured Ethernet frame bytes, captured lengths, and wire lengths while emitting canonical one-interface PCAPNG Enhanced Packet Blocks.
- Accept classic PCAP and PCAPNG by decoded format rather than filename contents; reject every non-Ethernet packet.
- The supplied directory contains exactly one regular `.pcap`/`.pcapng` and exact `capture.json`, with no other entries or path overlap with `run.directory`.
- Keep supplied source bytes read-only, snapshot them stably, use one monotonic `capture.total_timeout_seconds` deadline, and clean only owned temporary files.
- Reuse only an exact source/output/configuration/normalization-version match; preserve and reject partial, corrupt, unowned, or different canonical capture artifacts.
- Compose config-only preflight and imported acquisition with the existing `run_experiment`; do not duplicate fit, generate, compare, final validation, failure logging, or checkpoint logic.
- A successful run contains exactly the existing nine artifacts and no import manifest or sidecar.
- Update owning architecture documents with implementation; make root `README.md` and `QUICK_START_RU.md` lead with the easy one-command workflow and retain manual stages as advanced use.
- Follow red/green TDD. Any function exposed by a failed test receives 100% executable line and branch coverage before its task closes.
- Use focused tests within tasks; run Ordinary, branch-aware Coverage, deterministic checks, and available real-program validation only at the final gate.
- Request independent specification and code-quality review after every task; fix all Critical and Important findings before continuing.
- Commit each reviewed task separately with a terse Conventional Commit message; do not amend prior commits and do not push or move tags unless explicitly requested.

## File and responsibility map

New production owners:

- `src/trafficlab/common/scapy_io/__init__.py` — compatibility-preserving public exports.
- `src/trafficlab/common/scapy_io/trace.py` — current canonical `TrafficTrace` PCAPNG read/write behavior.
- `src/trafficlab/common/scapy_io/raw.py` — raw PCAP/PCAPNG format detection, packet validation, spool ordering, and canonical raw-frame PCAPNG output.
- `src/trafficlab/pipeline/imported.py` — strict source discovery, snapshots, lineage/reuse, publication, and existing-coordinator composition.

Changed public and documentation owners:

- `src/trafficlab/cli.py` — `import-run` parser, lazy dispatch, errors, interruption, and summary.
- `architecture/SYSTEM.md`, `architecture/CAPTURE.md`, `architecture/TESTING.md` — stable normative contract.
- `README.md`, `QUICK_START_RU.md` — easiest copyable workflow first.

Checked deterministic evidence:

- `tests/fixtures/data/import_run/classic-pcap-source/` — exact classic PCAP plus supplied metadata for the installed-command gate.
- `tests/fixtures/data/import_run/noncanonical-pcapng-source/` — out-of-order multi-interface PCAPNG plus supplied metadata for the installed-command gate.
- `tests/fixtures/data/import_run/classic-nanosecond.pcap` and `expected.json` — timestamp-resolution edge input and independent expected packet facts.
- `tests/fixtures/data/manifest.json` — exact fixture identities.

---

### Task 1 [TASK-1-b2cef0ba]: Refactor the Scapy boundary into a package without behavior change

**Files:**
- Replace: `src/trafficlab/common/scapy_io.py`
- Create: `src/trafficlab/common/scapy_io/__init__.py`
- Create: `src/trafficlab/common/scapy_io/trace.py`
- Modify: `tests/unit/pipeline/test_source_layout.py`
- Modify: `tests/unit/common/test_scapy_io.py`

**Interfaces:**
- Produces: unchanged imports for `EncodedPcapng`, `PcapngPacket`, `encode_pcapng`, `read_pcapng`, `read_pcapng_bytes`, and `read_pcapng_packets`; empty package slot for later raw normalization.
- Consumes: the complete current `scapy_io.py` behavior at `c503f77`.

- [ ] **[STEP-1-d6b49f5f] Write the failing package-boundary and public-import tests**

Add a structural expectation that `trafficlab/common/scapy_io/` contains exactly `__init__.py` and `trace.py`, while `trafficlab/common/scapy_io.py` is absent. Extend the import test to assert every existing public object remains importable from `trafficlab.common.scapy_io` and keeps its public name.

```python
def test_scapy_io_is_one_owned_package_with_stable_public_exports() -> None:
    package = PACKAGE / "common" / "scapy_io"
    assert {path.name for path in package.glob("*.py")} == {"__init__.py", "trace.py"}
    assert not (PACKAGE / "common" / "scapy_io.py").exists()
    from trafficlab.common.scapy_io import encode_pcapng, read_pcapng

    assert encode_pcapng.__name__ == "encode_pcapng"
    assert read_pcapng.__name__ == "read_pcapng"
```

- [ ] **[STEP-2-4fbdcee5] Run the focused tests and preserve the expected structural RED**

Run:

```bash
uv run --locked pytest -q -n 0 \
  tests/unit/pipeline/test_source_layout.py \
  tests/unit/common/test_scapy_io.py
```

Expected: the new package inventory fails because the current owner is the flat `scapy_io.py` module; existing behavioral tests remain green.

- [ ] **[STEP-3-38bafe73] Move the existing implementation without semantic edits**

Mechanically move the complete flat module to `scapy_io/trace.py`. Create an explicit export surface:

```python
from trafficlab.common.scapy_io.trace import (
    EncodedPcapng,
    PcapngPacket,
    encode_pcapng,
    read_pcapng,
    read_pcapng_bytes,
    read_pcapng_packets,
)

__all__ = (
    "EncodedPcapng",
    "PcapngPacket",
    "encode_pcapng",
    "read_pcapng",
    "read_pcapng_bytes",
    "read_pcapng_packets",
)
```

Do not rename functions, change dynamic Scapy imports, or change PCAPNG semantics.

- [ ] **[STEP-4-a6de6bd0] Update structural ownership for the package**

Remove `scapy_io.py` from the flat `common` inventory and add:

```python
"common/scapy_io": {"__init__.py", "trace.py"}
```

to the nested-package inventory. Keep the 600-line cap on both files.

- [ ] **[STEP-5-d1d95b46] Run unchanged Scapy and downstream behavior green**

Run the full current Scapy owner plus capture, fit-input, generation-publication, and comparison selections that import it:

```bash
uv run --locked pytest -q -n 0 \
  tests/unit/common/test_scapy_io.py \
  tests/unit/capture/test_capture_validation.py \
  tests/unit/fitting/test_input.py \
  tests/integration/generation/test_generate_publication.py \
  tests/integration/comparison/test_comparison_pipeline.py
```

Expected: PASS with no fixture byte changes.

- [ ] **[STEP-6-48d00afc] Run targeted format, lint, typing, and layout checks**

```bash
uv run --locked ruff format --check src/trafficlab/common/scapy_io tests/unit/common/test_scapy_io.py tests/unit/pipeline/test_source_layout.py
uv run --locked ruff check src/trafficlab/common/scapy_io tests/unit/common/test_scapy_io.py tests/unit/pipeline/test_source_layout.py
uv run --locked pyright src/trafficlab/common/scapy_io tests/unit/common/test_scapy_io.py tests/unit/pipeline/test_source_layout.py
uv run --locked pytest -q -n 0 tests/unit/pipeline/test_source_layout.py
git diff --check
```

- [x] **[STEP-7-3fd73a6a] Request independent review and fix every blocking finding**

Review the exact Task 1 range for import compatibility, cycles, changed exception text, and accidental byte changes. Repeat only the affected focused gate after fixes.

- [ ] **[STEP-8-af6827fb] Commit the behavior-preserving package split**

```bash
git add src/trafficlab/common/scapy_io tests/unit/common/test_scapy_io.py tests/unit/pipeline/test_source_layout.py
git commit -m "refactor(io): split Scapy boundary"
```

### Task 2 [TASK-2-2bc7a100]: Normalize raw PCAP and PCAPNG frames entirely in process

**Files:**
- Create: `src/trafficlab/common/scapy_io/raw.py`
- Modify: `src/trafficlab/common/scapy_io/__init__.py`
- Create: `tests/unit/common/test_scapy_raw.py`
- Create: `tests/fixtures/data/import_run/classic-pcap-source/capture.json`
- Create: `tests/fixtures/data/import_run/classic-pcap-source/source.pcap`
- Create: `tests/fixtures/data/import_run/noncanonical-pcapng-source/capture.json`
- Create: `tests/fixtures/data/import_run/noncanonical-pcapng-source/source.pcapng`
- Create: `tests/fixtures/data/import_run/classic-nanosecond.pcap`
- Create: `tests/fixtures/data/import_run/expected.json`
- Modify: `tests/fixtures/data/manifest.json`
- Modify: `tests/unit/pipeline/test_source_layout.py`
- Modify: `architecture/SYSTEM.md`
- Modify: `architecture/TESTING.md`

**Interfaces:**
- Produces: `RawNormalizationResult`, `normalize_raw_capture(source, destination, *, deadline, clock) -> RawNormalizationResult` exported from `trafficlab.common.scapy_io`.
- Consumes: Scapy 2.7 raw readers/writer, the existing microsecond output convention, and same-filesystem temporary destinations supplied by callers.

- [x] **[STEP-9-d86f176d] Create independent deterministic raw-capture fixtures and expected facts**

Construct checked binary fixtures with literal Ethernet frames and timestamps. `expected.json` must independently record ordered frame hex, captured lengths, wire lengths, input ordinals, exact source timestamp fractions, canonical microsecond ticks, and whether reordering is expected. Include equal timestamp ties and two PCAPNG Ethernet interfaces. Update the fixture manifest only after manually checking these literals.

- [x] **[STEP-10-0fd927db] Write failing format, ordering, and preservation tests**

Define the desired public records in tests:

```python
@dataclass(frozen=True, slots=True)
class RawNormalizationResult:
    input_format: Literal["pcap", "pcapng"]
    packet_count: int
    observation_window_seconds: float
    reordered: bool


result = normalize_raw_capture(source, output, deadline=None)
assert result == RawNormalizationResult("pcapng", 4, 1.25, True)
assert read_raw_output(output) == expected_frames_and_lengths
```

Require byte-for-byte frame preservation, stable ties, one output Ethernet interface, Enhanced Packet Blocks, microsecond truncation toward the past, and format detection independent of suffix.

- [x] **[STEP-11-78de174d] Run normalization tests RED**

```bash
uv run --locked pytest -q -n 0 tests/unit/common/test_scapy_raw.py
```

Expected: collection or assertions fail because `RawNormalizationResult` and `normalize_raw_capture` do not exist.

- [x] **[STEP-12-e1d746f7] Implement exact raw-reader format detection and timestamp conversion**

Use magic bytes to select `RawPcapReader` or `RawPcapNgReader`; never use the suffix. Convert timestamps to exact `Fraction` values:

```python
pcap_timestamp = Fraction(metadata.sec * resolution + metadata.usec, resolution)
pcapng_ticks = (metadata.tshigh << 32) | metadata.tslow
pcapng_timestamp = Fraction(pcapng_ticks) * Fraction(str(metadata.tsresol))
microsecond_ticks = timestamp.numerator * 1_000_000 // timestamp.denominator
```

Require finite nonnegative effective timestamps, Ethernet link type 1, `caplen == len(frame) >= 14`, and `wirelen >= caplen > 0`. Wrap Scapy/OSError failures as actionable `TrafficlabError` without exposing a traceback.

- [x] **[STEP-13-96d07661] Implement the owned spool and compact stable ordering index**

Write each frame once to a temporary binary spool and retain only:

```python
@dataclass(frozen=True, slots=True)
class _RawPacketIndex:
    timestamp: Fraction
    ordinal: int
    offset: int
    captured_length: int
    wire_length: int
```

Sort by `(timestamp, ordinal)`. Check the absolute deadline before reading, after every packet, before sorting, after sorting, after every output packet, and after closing output. The spool is always caller-owned temporary state.

- [x] **[STEP-14-08b4e3c2] Implement canonical single-interface Enhanced Packet Block output**

Set writer link type 1, write the first header once, seek and read every exact spooled frame, and write `caplen`, `wirelen`, and the canonical microsecond timestamp. Fail if spool reads are short or if fewer than two packets remain. Compute the observation window from canonical output ticks and require it to be positive.

- [x] **[STEP-15-7fb3d354] Add malformed, non-Ethernet, length, timestamp, and deadline RED/GREEN cases**

Cover PCAP/PCAPNG truncation, unsupported magic, non-Ethernet readers, 13-byte frames, captured-length mismatch, `wirelen < caplen`, negative/overflow timestamp fields, one packet, equal-only timestamps, deadline before open, deadline during input, and deadline during output. Each expected error names the violated boundary and corrective action.

- [x] **[STEP-16-e6ba82ee] Prove failed-function line and branch coverage**

Run the raw owner with branch coverage and inspect its missing regions:

```bash
uv run --locked coverage run --branch --source=src/trafficlab/common/scapy_io/raw.py \
  -m pytest -q -n 0 tests/unit/common/test_scapy_raw.py
uv run --locked coverage report -m src/trafficlab/common/scapy_io/raw.py
```

Add direct cases until every function exposed by RED has 100% executable line and branch coverage.

- [x] **[STEP-17-91bdb278] Document stable raw normalization semantics**

Update `SYSTEM.md` with decoded-format selection, exact accepted variants, stable ordering, microsecond truncation, frame/length preservation, spool bounds, and rejection behavior. Update `TESTING.md` with the independent fixture oracle. Do not mention task state or dated results.

- [x] **[STEP-18-0102afe9] Run the raw-I/O focused gate and deterministic fixture audit**

```bash
uv run --locked ruff format --check src/trafficlab/common/scapy_io tests/unit/common tests/fixtures/data/import_run
uv run --locked ruff check src/trafficlab/common/scapy_io tests/unit/common/test_scapy_raw.py
uv run --locked pyright src/trafficlab/common/scapy_io tests/unit/common/test_scapy_raw.py
uv run --locked pytest -q -n 0 tests/unit/common/test_scapy_io.py tests/unit/common/test_scapy_raw.py
uv run --locked python scripts/check_fixture_layout.py --check
git diff --check
```

- [x] **[STEP-19-76059f7f] Request independent raw-normalization review and fix blockers**

Review exact timestamp arithmetic, Scapy metadata handling, frame preservation, multi-interface collapse, spool safety, deadline coverage, and fixture independence. Repeat the raw owner after any fix.

- [x] **[STEP-20-24f47418] Commit raw normalization and its normative contract**

```bash
git add src/trafficlab/common/scapy_io tests/unit/common/test_scapy_raw.py \
  tests/fixtures/data/import_run tests/fixtures/data/manifest.json \
  tests/unit/pipeline/test_source_layout.py architecture/SYSTEM.md architecture/TESTING.md
git commit -m "feat(io): normalize raw traffic captures"
```

### Task 3 [TASK-3-5cc09196]: Import, publish, and exactly reuse supplied reference pairs

**Files:**
- Create: `src/trafficlab/pipeline/imported.py`
- Create: `tests/unit/pipeline/test_imported.py`
- Modify: `tests/unit/pipeline/test_source_layout.py`
- Modify: `tests/unit/pipeline/test_test_layout.py`
- Modify: `architecture/SYSTEM.md`
- Modify: `architecture/CAPTURE.md`
- Modify: `architecture/TESTING.md`

**Interfaces:**
- Produces: `ImportSource`, `discover_import_source(directory)`, `import_reference(source, prepared) -> CaptureResult`, and `run_imported_experiment(experiment_path, dump_directory) -> RunResult`.
- Consumes: Task 2 `normalize_raw_capture`, stable content identities, config-only preflight, capture-pair publication, and `run_experiment` dependency injection.

- [x] **[STEP-21-53f3448e] Write failing exact-directory discovery tests**

Cover missing path, file instead of directory, directory symlink, no capture, two captures, wrong-case `capture.json`, extra file, nested directory, FIFO/symlink entries, relative/absolute aliases, and source/run overlap. The accepted table covers `.pcap`, `.PCAP`, `.pcapng`, and `.PCAPNG` with exactly two regular files.

- [x] **[STEP-22-cafa1534] Run discovery RED**

```bash
uv run --locked pytest -q -n 0 tests/unit/pipeline/test_imported.py -k discover
```

Expected: `trafficlab.pipeline.imported` or its public discovery interface is absent.

- [x] **[STEP-23-bdfd25a2] Implement strict discovery and overlap validation**

Use `stat(..., follow_symlinks=False)`, a sorted complete direct inventory, and resolved paths. Define:

```python
@dataclass(frozen=True, slots=True)
class ImportSource:
    directory: Path
    capture_path: Path
    metadata_path: Path
```

All stored paths are absolute direct children of `directory`. Error text says how many capture/metadata/unexpected entries were found.

- [x] **[STEP-24-2399b001] Write failing stable-snapshot and publication tests**

Use real small fixtures and injected file-operation boundaries only for forced failures. Require source identities before/copy/after, metadata parsing before normalization, same-filesystem temporary ownership, final pair validation, `CaptureResult(..., target_status=0, reused=False)`, source immutability, no residue, and one exact `reference_imported` record.

- [x] **[STEP-25-549e8097] Implement snapshot, normalization, and capture-pair publication**

Use one absolute deadline derived from `prepared.config.capture.total_timeout_seconds`. Snapshot both files, call Task 2 normalization on the capture snapshot, call `validate_capture_pair`, then delegate final publication to `publish_capture_pair`. The log record contains source/output content identities, source paths, `normalization_version="scapy-raw-v1"`, packet count, output path, stage `capture`, and `reused=false`.

- [x] **[STEP-26-cd97d12d] Write failing exact-reuse and mismatch tests**

Cover exact retry without calling normalization, changed source capture, changed metadata, replaced source path with equal bytes, changed effective config, changed normalization version, changed canonical output, missing log record, duplicate/contradictory record, metadata-only output, reference-only output, and malformed existing pair. Every non-exact case preserves all preexisting run bytes.

- [x] **[STEP-27-ba90e1bb] Implement nonmutating reuse validation**

Inspect existing path identities without the ordinary capture recovery deletion path. Reuse only a valid pair plus one authoritative matching `reference_imported` lineage. Append `reused=true` after revalidating current source identities and output identities. Do not normalize on exact reuse and never overwrite or remove a non-exact existing artifact.

- [x] **[STEP-28-2a2ffc2a] Compose config-only acquisition with the existing coordinator**

Build production dependencies without copying coordinator logic:

```python
def run_imported_experiment(experiment_path: Path, dump_directory: Path) -> RunResult:
    source = discover_import_source(dump_directory)

    def import_capture(_path: Path, prepared: PreparedExperiment) -> CaptureResult:
        return import_reference(source, prepared)

    dependencies = RunDependencies(
        preflight=_config_only_preflight,
        capture=import_capture,
        fit=fit_experiment,
        generate=generate_experiment,
        compare=compare_experiment,
    )
    return run_experiment(experiment_path, dependencies=dependencies)
```

Use a named nested function rather than an assigned lambda in production. Discovery occurs before preflight; all post-preflight errors use the existing coordinator failure path.

- [x] **[STEP-29-5d28ccbc] Add interruption, deadline, cleanup, and no-subprocess tests**

Raise at every snapshot/normalize/publish/log boundary and require owned temporary cleanup plus preservation of authoritative paths. Patch `subprocess.run`, Docker imports, and repository-script imports to raise if reached; a real import path must remain green.

- [x] **[STEP-30-3def4559] Prove imported-stage failed-function coverage**

```bash
uv run --locked pytest -q -n 0 tests/unit/pipeline/test_imported.py \
  --cov=trafficlab.pipeline.imported --cov-branch --cov-report=term-missing --cov-fail-under=100
```

If module-only 100% is impractical because trivial process entry code is absent, inspect function regions and require 100% for every function exposed by RED while keeping total project coverage policy unchanged.

- [x] **[STEP-31-4ae875cf] Update source/test ownership and normative acquisition docs**

Add `imported.py` to the pipeline module inventory and `test_imported.py` to the unit pipeline owner. Update `SYSTEM.md`, `CAPTURE.md`, and `TESTING.md` with discovery, snapshots, deadline, lineage, exact reuse, preservation, and coordinator composition. State explicitly that imported acquisition creates no Docker resource or subprocess.

- [x] **[STEP-32-d3e12475] Run the imported-stage focused gate**

```bash
uv run --locked ruff format --check src/trafficlab/pipeline/imported.py tests/unit/pipeline/test_imported.py
uv run --locked ruff check src/trafficlab/pipeline/imported.py tests/unit/pipeline/test_imported.py
uv run --locked pyright src/trafficlab/pipeline/imported.py tests/unit/pipeline/test_imported.py
uv run --locked pytest -q -n 0 \
  tests/unit/pipeline/test_imported.py \
  tests/unit/pipeline/test_source_layout.py \
  tests/unit/pipeline/test_test_layout.py
git diff --check
```

- [x] **[STEP-33-a4e614e3] Request independent import-stage review and fix blockers**

Review source immutability, identity races, reuse authority, failure preservation, deadline continuity, artifact ownership, and absence of hidden subprocess/Docker/script paths.

- [x] **[STEP-34-3a828140] Commit imported acquisition**

```bash
git add src/trafficlab/pipeline/imported.py tests/unit/pipeline/test_imported.py \
  tests/unit/pipeline/test_source_layout.py tests/unit/pipeline/test_test_layout.py \
  architecture/SYSTEM.md architecture/CAPTURE.md architecture/TESTING.md
git commit -m "feat(pipeline): import captured references"
```

### Task 4 [TASK-4-c2a40435]: Expose the CLI and easiest documented workflow

**Files:**
- Modify: `src/trafficlab/cli.py`
- Modify: `tests/unit/pipeline/test_cli.py`
- Create: `tests/integration/pipeline/test_import_run.py`
- Modify: `tests/unit/pipeline/test_test_layout.py`
- Modify: `README.md`
- Modify: `QUICK_START_RU.md`
- Modify: `architecture/SYSTEM.md`
- Modify: `architecture/TESTING.md`

**Interfaces:**
- Produces: `trafficlab import-run [-h] EXPERIMENT DUMP_DIRECTORY` and one copyable English/Russian workflow.
- Consumes: Task 3 `run_imported_experiment` and the existing `RunResult` summary fields.

- [x] **[STEP-35-4d85b1d6] Write failing parser, dispatch, output, error, and interruption tests**

Add `ImportRunExperiment = Callable[[Path, Path], RunResult]`, inject it through `main`, and test desired behavior before implementation:

```python
assert main(["import-run", "experiment.toml", "dumps/example"], import_run=fake) == 0
assert calls == [(Path("experiment.toml"), Path("dumps/example"))]
assert output.startswith("import-run: family=")
```

Require missing/extra positional rejection, `--config-only` rejection, exact `TrafficlabError` status/action, interruption 130, and lazy import of `trafficlab.pipeline.imported` only after command selection.

- [x] **[STEP-36-d48ce06c] Run CLI RED**

```bash
uv run --locked pytest -q -n 0 tests/unit/pipeline/test_cli.py -k import_run
```

Expected: parser or `main(import_run=...)` interface failure.

- [x] **[STEP-37-a38bd0fa] Implement the exact two-positional CLI surface**

Register `import-run`, lazily load `run_imported_experiment`, call one injected boundary, and share the existing summary formatting without broad CLI refactoring. Error prefixes and interruption guidance use `import-run` exactly.

- [x] **[STEP-38-3334cd6b] Write failing in-process complete-run integration**

Create a real temporary source directory from checked fixtures and a small deterministic config. Forbid `subprocess.run`, Docker adapter imports, and any `scripts` import. Invoke the public coordinator and require config-only preflight, normalized reference identities, real fit/generate/compare, `run_completed`, and exactly nine final files.

- [x] **[STEP-39-4433fdfd] Make complete import-run integration green and cover resume**

Use Task 3 production code without a second pipeline. Repeat the same command to prove exact reference reuse and compatible checkpoint/stage reuse. Change one source byte and require preserved-run rejection before another scientific artifact changes.

- [x] **[STEP-40-72aed39e] Update root README with the shortest English workflow**

Place this before the advanced standalone-stage walkthrough:

```bash
cp examples/configs/balanced.toml examples/configs/my-dump.toml
# Set a fresh run.directory in my-dump.toml.
uv run --locked trafficlab import-run \
  examples/configs/my-dump.toml dumps/my-dump
```

Explain the exact two-file input, automatic in-process format/order repair, supplied-MAC semantics, no Docker, retained run directory, retry behavior, and when to use advanced standalone stages.

- [x] **[STEP-41-05a9e308] Update QUICK_START_RU.md with the easiest Russian workflow**

Make the same one-command sequence the recommended first path in Russian. State plainly that no manual `preflight`, copying, `fit`, `generate`, or `compare` is needed, and that Trafficlab neither changes the source files nor re-infers the supplied MAC. Keep the manual sequence under an advanced/resume heading.

- [x] **[STEP-42-9ca0b2b2] Update CLI and integration architecture contracts**

Add the exact command surface and imported flow to `SYSTEM.md`; add CLI/in-process/no-Docker/nine-artifact cases to `TESTING.md`. Do not duplicate implementation prose or include progress state.

- [x] **[STEP-43-eba78224] Run CLI, in-process integration, docs, and structural gates**

```bash
uv run --locked ruff format --check src/trafficlab/cli.py tests/unit/pipeline/test_cli.py tests/integration/pipeline/test_import_run.py
uv run --locked ruff check src/trafficlab/cli.py tests/unit/pipeline/test_cli.py tests/integration/pipeline/test_import_run.py
uv run --locked pyright src/trafficlab/cli.py tests/unit/pipeline/test_cli.py tests/integration/pipeline/test_import_run.py
uv run --locked pytest -q -n 0 \
  tests/unit/pipeline/test_cli.py \
  tests/unit/pipeline/test_imported.py \
  tests/integration/pipeline/test_import_run.py \
  tests/unit/pipeline/test_source_layout.py \
  tests/unit/pipeline/test_test_layout.py
uv run --locked trafficlab import-run --help
git diff --check
```

- [x] **[STEP-44-75c31787] Request independent public-flow review and fix blockers**

Review command ergonomics, lazy imports, exact output/errors, Docker/subprocess exclusion, real-stage composition, resume, documentation ease, and English/Russian consistency.

- [x] **[STEP-45-02c42711] Commit the public imported-run workflow**

```bash
git add src/trafficlab/cli.py tests/unit/pipeline/test_cli.py \
  tests/integration/pipeline/test_import_run.py tests/unit/pipeline/test_test_layout.py \
  README.md QUICK_START_RU.md architecture/SYSTEM.md architecture/TESTING.md
git commit -m "feat(cli): run imported traffic captures"
```

### Task 5 [TASK-5-b5a334a3]: Run integrated acceptance and retain validation evidence

**Files:**
- Create: `docs/evidence/2026-09-04-import-run.md`
- Modify only if a gate proves ownership: files from Tasks 1–4

**Interfaces:**
- Produces: reproducible current-head validation evidence and a reviewed clean branch ready for integration.
- Consumes: complete imported acquisition, CLI, fixtures, architecture, and user documentation.

- [ ] **[STEP-46-9bcd76ab] Run locked sync and repository-wide static checks**

```bash
uv sync --locked --all-groups --all-extras
uv run --all-extras ruff format --check .
uv run --all-extras ruff check .
uv run --all-extras pyright
```

Stop on the first failure, return it to its smallest owner, and repeat only the failed static command after the focused correction.

- [ ] **[STEP-47-ecca463d] Run the authoritative Ordinary gate**

Copy the exact Ordinary command from `architecture/DEVELOPMENT.md`, including its `run_bounded.sh` memory/swap/wall limits, four workers, work stealing, marker expression, and duration report. Require every non-Docker/non-Internet test to pass.

- [ ] **[STEP-48-7c755bac] Run the authoritative branch-aware Coverage gate**

Copy the exact Coverage command from `architecture/DEVELOPMENT.md`. Require all selected tests to pass and combined `trafficlab` plus `trafficlab_dashboard` branch-aware coverage to remain at least 90%.

- [ ] **[STEP-49-0b726b3a] Run every deterministic fixture, schema, benchmark, and probe check**

Run the exact deterministic command list from the Release gate in `architecture/DEVELOPMENT.md`, including fixture generators, 13 public schemas, reduction, both benchmarks, immutable scientific-stack example, and both current/historical probes. Add the import-run fixture manifest/check command established by Task 2.

- [ ] **[STEP-50-6bb5e2df] Run the available bounded Docker and Internet gate once**

Use the exact External command from `architecture/DEVELOPMENT.md` with the repository's credential-free Wikimedia HTTPS URL. This verifies the feature did not regress the live-capture path; `import-run` itself must not create a Docker resource.

- [ ] **[STEP-51-1cb2d249] Run the installed command from the classic PCAP fixture**

Create a temporary configuration by loading the checked small fit configuration through `load_configuration_pair`, changing only `run.directory` to a fresh absolute temporary directory, and writing it with `render_effective_config`. Invoke under the focused bounded wrapper:

```bash
uv run --locked trafficlab import-run \
  /tmp/trafficlab-import-run-pcap/experiment.toml \
  tests/fixtures/data/import_run/classic-pcap-source
```

Require status 0, exact source hashes unchanged, no child process observed, exactly nine artifacts, one non-reused import lineage, and saved-model generation/comparison reproduction.

- [ ] **[STEP-52-85bb83f3] Run the installed command from the noncanonical PCAPNG fixture**

Repeat Step 51 with a different fresh temporary configuration/run directory and `tests/fixtures/data/import_run/noncanonical-pcapng-source`. Require stable timestamp correction, multi-interface collapse, exact frame/length preservation, no Docker resource, nine artifacts, and reproduction. Do not reuse the PCAP run or change scientific settings.

- [ ] **[STEP-53-f2bcb365] Write and commit reproducible validation evidence**

Create `docs/evidence/2026-09-04-import-run.md` with the exact commands, source/output identities, resource/status sidecars, normalized packet facts, no-external-process proof, nine-file inventories, reproduction result, and selected family/scores labeled as non-causal smoke observations.

```bash
git add docs/evidence/2026-09-04-import-run.md
git commit -m "test(evidence): validate imported reference runs"
```

- [ ] **[STEP-54-6cd826c6] Obtain final whole-branch review and leave a clean local branch**

Request one final independent review over `c503f77..HEAD`, including all task-review findings and the evidence document. Fix every Critical/Important finding, rerun its affected focused owner plus any invalidated final check, commit coherent fixes without amending, and verify:

```bash
git diff --check c503f77..HEAD
git status --short
```

The status must be empty. Do not push, merge, delete the worktree, or move `MVP_4` without explicit user direction.
