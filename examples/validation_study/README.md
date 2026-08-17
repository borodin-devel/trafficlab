# Validation Study

This directory contains the frozen local protocol and accepted evidence for the
replacement Phase 7 study. The protocol is serial and bounded. It has no default endpoint: the
operator supplies one credential-free HTTPS URL at invocation time, and the
exact URL, redirect result, headers, and external observation are retained in
the candidate bundle. Do not run the external commands below until the source
commit, endpoint, and prerequisite coordination have been explicitly approved.

## Accepted corrected study

[`evidence/2026-08-17-research-fitness-r18/`](evidence/2026-08-17-research-fitness-r18/)
is the accepted schema-2 study. Its [report](REPORT.md), `index.json`, and
`manifest.json` are the navigation and integrity roots. It contains:

- nine complete training trees, arranged as three workloads times three
  independent captures;
- nine fixed-final-seed fresh-simulation records and three genuine independent
  held-out records;
- portable/realized configuration pairs in `configs/`, named
  `training-<workload>-r<repeat>.portable.toml` and `.realized.toml`;
- retained prerequisite commands/results, transfer headers/observations,
  environment, report inputs, owner/lineage index, and path/size/SHA-256
  manifest.

The audited source candidate is a transient publisher work copy, not another
accepted study. Historical Phase 7 material and prior attempts remain
non-accepted consistency or forensic evidence.

## Frozen protocol

Use a new study ID matching `[a-z0-9][a-z0-9-]{0,31}` for every attempt. Before
starting, the source checkout must be clean and the capture image lock must name
the cold rebuilt local image ID. The prerequisite owner builds with
`--pull --no-cache --iidfile`; it records the exact image ID, locked
source/lock inputs, bounded Docker matrix, Internet smoke, command argv,
stdout, stderr, JUnit XML, test counts, transfer headers, and capability
observation.

```bash
export TRAFFICLAB_INTERNET_URL='https://operator-approved.example/object'
STUDY_ID='validation-study-YYYYMMDD-r1'
test -z "$(git status --porcelain=v1 --untracked-files=all)"

uv run --locked python scripts/run_validation_study.py \
  prerequisites --url "$TRAFFICLAB_INTERNET_URL" --study-id "$STUDY_ID"
```

The generated prerequisite record and base configurations are deliberately
ignored working evidence. They are copied, with identities, into the candidate
bundle; they are not a replacement for an accepted bundle. Do not edit any
source, lock, image-lock, prerequisite, or generated configuration after this
point.

Run collection exactly once for that ID:

```bash
uv run --locked python scripts/run_validation_study.py \
  collect --url "$TRAFFICLAB_INTERNET_URL" --study-id "$STUDY_ID" \
  --prerequisites examples/validation_study/prerequisites.json
```

Collection writes an immutable attempt marker before the first capture. It runs
nine training captures in the balanced order `short, streaming, bursty`, then
`streaming, bursty, short`, then `bursty, short, streaming`; each workload has
three independent references and evaluates all three enabled model families.
It freezes the training-only selected retained model before one new independent
held-out capture per workload. Held-out evaluation loads that fixed model,
generates with the predeclared final seed, and compares it without refitting,
family reselection, seed choice, or protocol amendment. Collection is serial,
uses study-scoped project/run paths, and preserves its evidence rather than
performing broad cleanup.

Any failure preserves the attempt marker and ignored evidence. It invalidates
the ID: do not replace a capture, retry a stage, alter a weight, or reuse that
study ID. Start again only with a new ID and a new prerequisite record.

## Candidate audit and exclusive publication

The candidate contains the complete strict nine-file training trees; portable
and realized configurations; nine `fresh_simulation` records against the
training references; three independent held-out capture/model/generated/
comparison/lineage bundles; canonical JSONL logs; prerequisite command and
output evidence; headers; observations; environment; report inputs; index; and
UTF-8 ordered manifest. The report separates four evidence classes:

- training fit and model selection;
- repeated-capture natural variation;
- same-reference fresh simulation using the fixed final seed;
- genuine held-out fixed-model evaluation.

It must list each mandatory component score and aggregate weight. A controlled
weight change may affect only aggregation/ranking, not component execution or
diagnostics. Invalid candidates are infeasible under their recorded genes,
settings, and limits, not evidence that a family fits poorly. State finite
sample, model, metric, and generalization limitations beside every conclusion.

Audit the candidate before publication. This command is offline and read-only:
it must not start Docker, open the network, fetch bytes, regenerate artifacts,
or mutate the candidate.

```bash
scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/.candidates/"$STUDY_ID" --repository .
```

Only a passing audit may publish through the exclusive owner:

```bash
uv run --locked python scripts/run_validation_study.py publish \
  --study-id "$STUDY_ID" \
  --candidate examples/validation_study/evidence/.candidates/"$STUDY_ID"
```

An occupied destination remains byte-for-byte unchanged. A collision or failed
audit never permits merge, overwrite, or selective rerun.

## Local verification

No ordinary test contacts the endpoint or Docker. The local collector test uses
in-process capture/run owners and deterministic PCAPNG bytes:

```bash
scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked pytest -q -n 0 \
  tests/unit/test_validation_study.py \
  tests/integration/test_validation_study_collection.py \
  tests/integration/test_validation_study_pipeline.py

uv run --locked ruff check scripts/run_validation_study.py \
  scripts/audit_validation_study.py tests/unit/test_validation_study.py \
  tests/integration/test_validation_study_collection.py
uv run --locked pyright scripts/run_validation_study.py \
  scripts/audit_validation_study.py tests/unit/test_validation_study.py \
  tests/integration/test_validation_study_collection.py
```
