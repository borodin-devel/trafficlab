# Validation Study

This directory contains the frozen local protocol and accepted evidence for the
replacement real-program validation study. The protocol is serial and bounded. It has no default
endpoint: the operator supplies one credential-free HTTPS URL at invocation time,
and the exact URL, redirect result, headers, and external observation are retained
in the candidate bundle. Do not run the external commands below until the source
commit, endpoint, and prerequisite coordination have been explicitly approved.

## Accepted production-Scapy study

[`evidence/2026-08-20-scapy-production-r3/`](evidence/2026-08-20-scapy-production-r3/)
is the current accepted study. Its [report](REPORT.md), schema-4 `index.json`,
and schema-2 `manifest.json` are the navigation and integrity
roots. The manifest binds 231 retained evidence paths and excludes only itself.
The bundle contains:

- nine complete training trees, arranged as three workloads times three
  independent captures;
- nine fixed-final-seed fresh-simulation records and three genuine independent
  held-out records;
- portable/realized configuration pairs in `configs/`, named
  `training-<workload>-r<repeat>.portable.toml` and `.realized.toml`;
- retained prerequisite commands/results, transfer headers/observations,
  environment, lifecycle cleanup proof, report inputs, owner/lineage index, and
  path/size/SHA-256 manifest; and
- fixed-seed 95% percentile-bootstrap intervals with exact PCG64 state and
  metadata for every training runtime and selection-fitness mean.

The audited source candidate is a transient publisher work copy, not another
accepted study. The older checked
[`2026-08-20-scapy-production-r2`](evidence/2026-08-20-scapy-production-r2/),
[`2026-08-20-stack-adoption-r6`](evidence/2026-08-20-stack-adoption-r6/), and
[`2026-08-18-research-fitness-r21`](evidence/2026-08-18-research-fitness-r21/)
bundles remain byte-unchanged accepted predecessors. Historical material and
failed/nonfinal attempts remain ignored consistency or forensic evidence.

## Directory contents and `results.json` fields

[`evidence/`](evidence/) contains immutable accepted bundles and has its own
[record and JSON field dictionary](evidence/README.md). `REPORT.md` interprets
the current accepted production-Scapy study. `results.json` is an earlier
standalone retained study summary; it remains reproducibility evidence but is
not the navigation root for the accepted schema-4 bundle named above.

| `results.json` field | Description |
| --- | --- |
| `schema_version` | Standalone result-summary format version. |
| `environment` | Study date, source commit, Trafficlab/Python/platform/Docker versions, and capture/target image IDs. |
| `protocol` | Study ID, URL/capability record, workload definitions and execution order, model families, methods, seeds, configuration/prerequisite digests, images, and runtime boundary. |
| `runs` | Nine primary training-run records ordered by `execution_order`. |
| `reproduction` | Dedicated unchanged-configuration reproduction run and its comparison with the source run. |
| `natural_variation` | Per-workload repeated-reference comparison pairs and reference descriptors. |
| `workload_summaries` | Per-workload descriptive statistics for runtime, winners, family champions, published/fresh scores, and reference traffic. |

Each `runs[]` record contains its workload/repeat `key`, run ID/directory and
config path, elapsed time, cleanup result, artifact digests, transfer evidence,
reference/generated descriptors, family champions, winner, published score,
fresh-simulation score, raw-sequence reproduction checks, and explicit artifact
reuse flags. `reproduction` adds `source_key`, changed configuration fields,
guard command/status/digests, comparison deltas, and the same core run fields.

Traffic descriptors contain `packet_count`, inbound/outbound packet and byte
totals, `observation_window_seconds`, frame-length and IAT summaries, and
per-width scale totals. Each frame-length or IAT summary contains `count`,
`minimum`, `maximum`, `median`, `quantile`, `quantile_probability`, and
`zero_count`. A numeric summary under `workload_summaries` contains `count`,
`minimum`, `maximum`, `range`, `mean`, `sample_variance`, and
`sample_standard_deviation`. A score contains `aggregate` and all four named
method values. A transfer response contains requested byte bounds, HTTP
`status`, content range/length, transfer index, retained-header path/digest,
file modes, and inode-preservation proof.

## Frozen protocol

Use a new study ID matching `[a-z0-9][a-z0-9-]{0,31}` for every attempt. Before
starting, the source checkout must be clean and the capture image lock must name
the cold rebuilt local image ID. The prerequisite owner builds with
`--pull --no-cache --iidfile`; it records the exact image ID, locked source/lock
inputs, bounded Docker matrix, Internet smoke, command argv, stdout, stderr,
JUnit XML, test counts, transfer headers, and capability observation.

```bash
export TRAFFICLAB_INTERNET_URL='https://operator-approved.example/object'
export HYPOTHESIS_STORAGE_DIRECTORY='/absolute/scratch/outside/the/checkout'
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

`2026-08-18-research-fitness-r20` is consumed as a failed attempt: after all
nine primary runs, the short repeated-capture comparison `r2 <- r1` retained
one aligned event at r2's reference-derived observation window, making the
mandatory autocorrelation metric infeasible. Its ignored attempt and candidate
remain preserved; it created no accepted bundle, report, index, or manifest.

`2026-08-18-research-fitness-r21` is the accepted successor. Its predeclared
short single-request profile used `--range 0-1048575 --max-filesize 1048576` at
the unchanged `--limit-rate 4M`. This changed only the short transfer extent to
reduce the observed cross-direction one-event risk; the approved URL, timeouts,
metrics, observation-window derivation, seeds, model families, and resource
scope remained fixed. An infeasible metric would still have invalidated the
whole new ID.

For scientific-stack adoption, r1 failed the strict clean-tree prerequisite,
r2 failed final audit when Hypothesis created checkout-local constants, and r3
was preserved as nonfinal after bootstrap evidence was found absent. r4 was the
first bootstrap-complete accepted bundle and remains recoverable from Git
history. The final-review replacement attempt r5 failed before Docker work
because a checkout-local Hypothesis cache was present. r6 started from a new
clean source snapshot with Hypothesis storage outside the checkout and replaced
r4 in the current tree. No failed ID or capture was reused.

For production Scapy evidence, `2026-08-20-scapy-production-r1` failed the
strict clean-tree prerequisite because `.hypothesis/.gitignore` existed inside
the checkout. Its ignored failure record was preserved and the cache was moved
intact to external scratch. `2026-08-20-scapy-production-r2` used fresh external
Hypothesis storage, completed all collection phases, passed offline candidate
and detached regular-copy audits, and became an accepted predecessor. Final
review then required stricter reader, publication, and diagnostic boundaries;
`2026-08-20-scapy-production-r3` was collected from that corrected source,
passed both audits, and became current. No failed ID or capture was reused.

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
UV_OFFLINE=1 scripts/run_bounded.sh --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/.candidates/"$STUDY_ID" --repository .
```

Only a passing audit may publish through the exclusive owner:

```bash
UV_OFFLINE=1 uv run --locked --offline python scripts/run_validation_study.py publish \
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
  tests/unit/validation/study/ \
  tests/integration/validation/

uv run --locked ruff check scripts/run_validation_study.py \
  scripts/audit_validation_study.py scripts/validation_study tests/support/validation_study \
  tests/unit/validation/study/ \
  tests/integration/validation/
uv run --locked pyright scripts/run_validation_study.py \
  scripts/audit_validation_study.py scripts/validation_study tests/support/validation_study \
  tests/unit/validation/study/ \
  tests/integration/validation/
```
