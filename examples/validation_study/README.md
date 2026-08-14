# Validation Study

Validation Study compares Trafficlab's three classical traffic models on three real curl
traffic shapes. Run the entire protocol serially from a clean checkout with the
locked environment. Do not overlap Docker, Internet, primary-run,
reproduction, or broad verification commands.

## Endpoint and study identity

Set `TRAFFICLAB_INTERNET_URL` to an absolute, credential-free HTTPS URL with a
DNS hostname and no query or fragment. The URL and every redirect must support
byte ranges and identify one object whose total size is from 4 MiB through
16 MiB inclusive. Do not use a URL containing credentials, tokens, or other
secrets.

The protocol uses exactly this digest-pinned target:

```text
curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b
```

Study IDs must match `[a-z0-9][a-z0-9-]{0,31}`. For example:

```bash
export TRAFFICLAB_INTERNET_URL="https://operator-selected.example/object"
STUDY_ID=validation-study-20260813
```

The example URL above describes the required shape only; replace it with the
operator-selected conforming endpoint before running anything.

## Run the protocol

Start from the clean reviewed Task 6 commit and keep that commit unchanged for
the entire attempt:

```bash
TASK6_BASE="$(git rev-parse HEAD)"
test -z "$(git status --short)"
```

First validate the endpoint, images, Docker matrix, Internet smoke, and render
the three configurations plus canonical prerequisite record:

```bash
uv run --locked python scripts/run_validation_study.py \
  prerequisites --url "$TRAFFICLAB_INTERNET_URL" --study-id "$STUDY_ID"
```

Inspect those uncommitted prerequisite artifacts without changing them:

```bash
PREREQUISITE_STATUS="$(git status --porcelain=v1 --untracked-files=all)"
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python -c \
  'from pathlib import Path; import scripts.run_validation_study as study; root=Path.cwd(); '\
'path=Path("examples/validation_study/prerequisites.json"); content=path.read_bytes(); '\
'value=study.parse_prerequisite_results(content, repository_root=root); '\
'assert study.render_prerequisite_results(value)==content; study.validate_base_configs(root, value)'
test "$(git status --porcelain=v1 --untracked-files=all)" = "$PREREQUISITE_STATUS"
test "$(git rev-parse HEAD)" = "$TASK6_BASE"
```

Do not commit, stage, edit a tracked file, or otherwise change `HEAD` between
prerequisites and study. Run the nine balanced primary experiments and the
preselected reproduction from the same `TASK6_BASE`:

```bash
uv run --locked python scripts/run_validation_study.py \
  study --url "$TRAFFICLAB_INTERNET_URL" --study-id "$STUDY_ID" \
  --prerequisites examples/validation_study/prerequisites.json
```

The saved-run reproduction is automatically execution 10,
`10-streaming-r2-reproduction`. It copies only the saved effective
configuration and changes its run directory. It performs a fresh full
preflight, Internet capture, fit, generation, comparison, and cleanup; it is not
a byte-identical replay and does not reuse prior stage artifacts.

Only after prerequisites, all ten experiments, result/report construction, the
local read-only audit, and Roadmap evidence checks succeed may the operator
stage and commit the three configs, `prerequisites.json`, `results.json`,
`REPORT.md`, and `architecture/ROADMAP.md` together.

## Failure recovery

A prerequisite, primary, reproduction, or local evidence-audit failure
invalidates the attempt. Set `FAILED_ID` before changing `STUDY_ID`, retain
all three ignored roots, and hash every retained regular non-symlink file:

```bash
FAILED_ID="$STUDY_ID"
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python -c \
  'from hashlib import sha256; from pathlib import Path; import sys; '\
'import scripts.run_validation_study as study; study_id=study.validate_study_id(sys.argv[1]); '\
'roots=(Path("runs/validation_study")/study_id, '\
'Path("examples/validation_study/.study-work/evidence")/study_id, '\
'Path("examples/validation_study/.study-work/mount")/study_id); '\
'paths=sorted(p for root in roots if root.exists() for p in root.rglob("*") '\
'if p.is_file() and not p.is_symlink()); '\
'[print(f"{sha256(p.read_bytes()).hexdigest()}  {p.as_posix()}") for p in paths]' \
  "$FAILED_ID"
```

Then execute Task 7 Step 5 in
`docs/superpowers/plans/2026-08-13-validation-study.md` exactly. Its
copyable recovery blocks are authoritative; do not abbreviate their
symlink/type/occupancy checks:

- Classify only these six publication candidates:
  `examples/validation_study/prerequisites.json`,
  `examples/validation_study/results.json`, `examples/validation_study/REPORT.md`,
  `examples/validation_study/configs/short.toml`,
  `examples/validation_study/configs/streaming.toml`, and
  `examples/validation_study/configs/bursty.toml`. Reject regular or dangling source
  symlinks and every non-regular candidate before moving anything.
- If none is tracked, require `HEAD == TASK6_BASE`. Inspect the Roadmap diff,
  use `apply_patch` to restore only authorized Task 7 claims byte-for-byte to
  the Task 6 Roadmap blob, and require exact equality. Move only existing
  regular candidates, one by one, into the fresh ignored
  `examples/validation_study/.study-work/failed-publication/$FAILED_ID/` leaf using the
  plan's exact archive block. Never delete, glob, recursively move, overwrite,
  or move a run/evidence/mount root.
- If any candidate is tracked or committed, do not archive or overwrite it.
  Make a separately reviewed `apply_patch` corrective commit that withdraws
  only the existing stale configs/prerequisite/result/report artifacts and
  stale Roadmap claims while preserving Task 6 Docker evidence.
- Require clean status on the resulting reviewed base. Choose a new validated
  study ID and prove that its run, evidence, mount, and failed-publication
  roots are absent even as dangling symlinks before restarting prerequisites.

Never replace or selectively rerun a primary or reproduction inside an
existing study.

## Publication and local audit data

The checked publication paths are:

- `examples/validation_study/README.md`
- `examples/validation_study/configs/short.toml`
- `examples/validation_study/configs/streaming.toml`
- `examples/validation_study/configs/bursty.toml`
- `examples/validation_study/prerequisites.json`
- `examples/validation_study/results.json`
- `examples/validation_study/REPORT.md`

Raw run directories, transfer headers, JUnit XML, command output, and mount
scratch remain ignored under `runs/validation_study/$STUDY_ID/` and
`examples/validation_study/.study-work/`. No raw Internet or generated PCAPNG is checked
in. The checked JSON retains hashes that bind the publication to the ignored
audit evidence.

Retain ignored audit evidence by default. Only after accepting the report and
deciding that local audit evidence is no longer needed may the operator
manually remove these two exact study-ID audit trees:

```text
runs/validation_study/$STUDY_ID/
examples/validation_study/.study-work/evidence/$STUDY_ID/
```

The support script never performs that retention deletion. Do not use broad
cleanup commands.

## Verification commands

Run each command separately. Every pytest invocation must stay inside the
five-limit process-tree guard shown here.

Focused Validation Study tests:

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -vv -x -n 0 \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
```

Explicit Validation Study type check:

```bash
uv run --locked pyright scripts/run_validation_study.py \
  tests/unit/test_validation_study.py tests/integration/test_validation_study_pipeline.py
```

Fast non-external tests:

```bash
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not integration and not docker and not internet"
```

Branch-aware package coverage:

```bash
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -n 4 --dist worksteal --cov=trafficlab \
  --cov-branch --cov-report=term-missing \
  -m "not docker and not internet"
```

Dedicated Docker matrix:

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m docker
```

Opt-in Internet smoke:

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m internet \
  --internet-url "$TRAFFICLAB_INTERNET_URL"
```
