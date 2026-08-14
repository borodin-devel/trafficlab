# MVP Validation Study Design

**Date:** 2026-08-13

## Status and authority

This design defines the approved Validation Study. The architecture in
`architecture/` remains authoritative. Validation Study evaluates the completed MVP; it
does not add another production command, model, metric, or orchestration path.

The study can be implemented and tested locally through the Class 1--4 autonomy
policy. At design approval, real Internet evidence was blocked until the
operator supplied the explicit credential-free HTTPS object URL required below.
That dependency was satisfied on 2026-08-14; the accepted study and canonical
evidence are recorded under `examples/validation_study/` and in the Roadmap.

## Decision

Use one real, immutable curl client image for all workloads:

```text
curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b
```

The study changes traffic shape through three direct curl argument vectors while
holding the client implementation, target image, model set, genetic settings,
seeds, and similarity weights fixed. This isolates the intended workload-shape
comparison, minimizes image pulls, and adds no Node.js or application runtime.

Run three fresh complete experiments per workload in a balanced order, for nine
successful primary runs:

| Execution order | Run ID | Workload | Repeat |
|---:|---|---|---:|
| 1 | `01-short-r1` | `short` | 1 |
| 2 | `02-streaming-r1` | `streaming` | 1 |
| 3 | `03-bursty-r1` | `bursty` | 1 |
| 4 | `04-streaming-r2` | `streaming` | 2 |
| 5 | `05-bursty-r2` | `bursty` | 2 |
| 6 | `06-short-r2` | `short` | 2 |
| 7 | `07-bursty-r3` | `bursty` | 3 |
| 8 | `08-short-r3` | `short` | 3 |
| 9 | `09-streaming-r3` | `streaming` | 3 |

This cyclic order balances first, middle, and last positions. Runs are serial
because they own Docker resources and because concurrent public transfers would
confound both runtime and natural-variation evidence.

## Approaches considered

### One client with three traffic profiles -- selected

One digest-pinned curl image sends a short range, a rate-limited streaming
range, and parallel ranges. It provides three reproducible shapes with the
smallest operational and interpretive surface.

### Three independent client programs -- rejected

Using curl, wget, and Python urllib would broaden program coverage, but it would
confound traffic shape with TLS stacks, defaults, image contents, and connection
reuse. It also adds image and certificate-management work without answering the
Validation Study question more clearly.

### Controlled local endpoint only -- rejected as study evidence

The checked Docker endpoint remains valuable regression evidence, but it is
deliberately deterministic. It cannot establish natural public-network
variation or replace the opt-in Internet smoke required by the Roadmap.

## Scope and non-goals

Validation Study will:

- run the existing five-stage `trafficlab run` pipeline on three real workloads;
- repeat each workload three times to describe natural capture variation;
- make all three existing model families compete in every experiment;
- record selection evidence, the selected winner's fresh held-out evidence,
  final published component scores, runtime, and run-to-run variance;
- inspect canonical traces and metric diagnostics to explain disagreements;
- publish concise configurations, results, commands, limitations, and a report;
- reproduce one saved completed run from its effective configuration and seeds.

Validation Study will not add a production CLI command, configuration section, model,
metric, plotting framework, protocol parser, database, manifest, workflow
engine, traffic replay, payload model, parallel evaluator, or security
subsystem. It will not treat ten Internet captures as deterministic fixtures or
make ordinary tests depend on the Internet.

No production `src/trafficlab` change is planned. If execution exposes a core
defect, that defect receives its owning architecture update and TDD fix; the
study harness must not carry a shadow implementation.

## Endpoint contract and prerequisite evidence

The operator supplies one credential-free absolute HTTPS URL with a DNS
hostname. User information, query text, and fragments are forbidden. The object
must be stable for the study, support byte ranges, and have a total size from
4 MiB through 16 MiB inclusive. Redirects are permitted only when every
resulting URL remains credential-free HTTPS under the same restrictions.

Before the study, `prerequisites` creates these ignored host paths for the
validated study ID:

```text
examples/validation_study/.study-work/
  mount/STUDY_ID/
  evidence/STUDY_ID/00-prerequisites/
```

The mount directory is owned by the invoking host user and has mode `0755`.
The script exclusively creates
`mount/STUDY_ID/.capability.headers` as an empty regular file, sets its mode to
`0666`, and rejects symlinks or a pre-existing unexpected entry. It then runs
this direct-argv capability command from the repository root:

```text
docker run --rm
--name trafficlab-validation-study-capability-STUDY_ID
--label org.trafficlab.validation-study.study=STUDY_ID
--cidfile EVIDENCE_ABS/capability.cid
--network bridge
--mount type=bind,src=MOUNT_ABS,dst=/trafficlab-study
curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b
--fail --silent --show-error --location --max-redirs 3
--proto =https --proto-redir =https --http1.1
--connect-timeout 15 --max-time 30
--range 0-0 --max-filesize 1
--dump-header /trafficlab-study/.capability.headers
--output /dev/null
--write-out status=%{response_code}\nsize=%{size_download}\nurl=%{url_effective}\nredirects=%{num_redirects}\n
--url URL
```

The command does not override the image's configured user, so it proves that
the exact pinned target user can truncate and write an existing file through
the same bind source and destination used by all ten experiments; the production
Compose target likewise has no user override. The injected
subprocess runner has a 45-second deadline around curl's 30-second deadline. On
timeout or abnormal exit it reads the exclusively created CID file, requires
that exact container to carry the expected study label, force-removes that ID,
verifies both the ID and exact name are absent, and retains the primary failure.
Before launch it requires both the name and CID-file path to be absent. Failure
to prove ownership forbids removal and reports the surviving resource. After a
normal exit it likewise verifies that `--rm` removed both the ID and name.

The host requires a changed, nonempty regular canary with the same inode, parses
every response block and redirect `Location`, and validates every resolved URL
against the endpoint contract. It parses the four exact write-out fields and
the final header block, requiring:

- status `206`;
- `Content-Range: bytes 0-0/TOTAL`;
- `Content-Length: 1`;
- `4 MiB <= TOTAL <= 16 MiB`;
- `size_download` exactly `1` and `num_redirects` an integer in `0..3`;
- a write-out final URL equal to the validated final response URL.

The exact canary bytes, stdout, and stderr are copied with mode `0600` to
`evidence/STUDY_ID/00-prerequisites/`, hashed into prerequisite evidence, and
the scratch canary is removed. The retained CID file contains the exact
container ID and has mode `0600`. This check establishes range support, the
bounded source object, and target-user mount writability. It is not Phase 3
Internet evidence and is excluded from measured run time.

Before any study run, the support script's `prerequisites` subcommand executes
the dedicated Phase 3 Docker matrix and then the opt-in Internet smoke against
the same explicit URL:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m docker \
  --junitxml "examples/validation_study/.study-work/evidence/$STUDY_ID/00-prerequisites/docker.xml"

scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m internet \
  --internet-url "$TRAFFICLAB_INTERNET_URL" \
  --junitxml "examples/validation_study/.study-work/evidence/$STUDY_ID/00-prerequisites/internet.xml"
```

Both selections must actually execute and pass. Collection, a skip, or an
expected unavailable-environment error is not evidence. The target and capture
images are pulled or built before timing, and their immutable repository digest
or local Docker image ID is recorded in prerequisite evidence.

`prerequisites` uses one small injected `subprocess.run` callable, not a generic
command framework. It passes argument arrays directly with `shell=False`, runs
the capability command and then the two exact guarded test commands above,
parses the JUnit counts, records command argv, start/completion UTC times and
status, and writes canonical `examples/validation_study/prerequisites.json`. It also:

- pulls and inspects the exact curl digest;
- builds `docker/capture/` with `docker build --pull=false --iidfile FILE`;
- reads the resulting exact `sha256:...` capture image ID without assigning or
  assuming a local tag;
- records the current Git commit and clean-tree state, Python and Trafficlab
  versions, Docker Engine and Compose versions, and both image IDs.

The order is URL/study/worktree validation, image pull/build/inspection,
capability canary, Docker matrix, Internet smoke, config rendering, then atomic
prerequisite JSON publication. A failure stops that sequence.

Only after all prerequisite operations pass, it renders the three complete
checked experiment configs from the exhaustive oracle, explicit URL, and exact
capture image ID. This avoids both an invented local tag and an unresolved image
reference. Before the URL exists, deterministic tests construct equivalent
temporary configs; the repository does not pretend that a runnable real-study
config already exists.

The capture image ID is then the literal `capture.image` in every realized
experiment. All nine primary runs and the fresh reproduction use the same ID.
The `study` subcommand refuses prerequisite evidence whose study ID, URL,
capability, Git commit, tool versions, target digest, capture image ID, command,
status, or test counts do not strictly match the current study inputs and
environment. It does not repeat the capability request.

## Workload profiles

Every target runs curl directly through the existing Compose command field. No
shell, wrapper, sleep process, or `docker exec` is introduced. Every transfer
uses HTTP/1.1 to make connection behavior easier to interpret, discards response
content, rejects HTTP errors, permits only HTTPS initial and redirect protocols,
permits at most three redirects, and has finite connection and transfer
deadlines.

Every realized target has one read-write bind from the absolute form of
`examples/validation_study/.study-work/mount/STUDY_ID/` to `/trafficlab-study`. Before
each serial run, the host removes only that profile's exact scratch names,
exclusively creates each as an empty regular file, and sets mode `0666` beneath
the host-owned mode-`0755` parent. Curl opens those existing files and writes
final response headers. The script requires every file to retain its inode and
become nonempty; a symlink, replacement, missing write, or malformed file fails
the run.

A transfer succeeds as study evidence only when status, `Content-Range`, and
`Content-Length` exactly match its requested range and the capability check's
same total object size. Missing, malformed, or duplicate status/range/length
evidence fails the run. `--max-filesize` independently prevents a
range-ignoring endpoint from silently downloading the whole object.

After validation, the host copies the exact bytes with mode `0600` to
`examples/validation_study/.study-work/evidence/STUDY_ID/RUN_ID/`, verifies the archive
hash, and removes the exact scratch files. This evidence directory is a sibling
of, never a child of, the production run at
`runs/validation_study/STUDY_ID/RUN_ID/`. Consequently every successful run directory
retains the production contract's exact nine entries.

The prerequisite-generated checked configurations expand these exact profile
definitions with the operator URL as `URL`.

### Short range

One 256 KiB range is capped at 4 MiB/s. This remains a short single request but
avoids a near-zero observation window on a fast endpoint.

```text
--fail --silent --show-error --location --max-redirs 3
--proto =https --proto-redir =https --http1.1
--connect-timeout 15 --max-time 30 --limit-rate 4M
--range 0-262143 --max-filesize 262144
--dump-header /trafficlab-study/short.headers
--output /dev/null --url URL
```

The final response must be `206` with exact header value
`Content-Range: bytes 0-262143/TOTAL` and `Content-Length: 262144`, where
`TOTAL` equals the capability check's object size.

### Fixed-rate streaming range

One 4 MiB range is capped at 256 KiB/s, producing an approximately 16-second
continuous transfer. Curl's rate option is an average ceiling; the report must
call this rate-limited traffic rather than claim exact wire pacing.

```text
--fail --silent --show-error --location --max-redirs 3
--proto =https --proto-redir =https --http1.1
--connect-timeout 15 --max-time 40 --limit-rate 256K
--range 0-4194303 --max-filesize 4194304
--dump-header /trafficlab-study/streaming.headers
--output /dev/null --url URL
```

The final response must be `206` with exact header value
`Content-Range: bytes 0-4194303/TOTAL` and `Content-Length: 4194304`, where
`TOTAL` equals the capability check's object size.

### Bursty parallel ranges

Eight 32 KiB ranges run with at most four concurrent HTTP/1.1 transfers. Curl
may schedule them in any order, while the concurrency limit remains four. The
ranges begin at byte offsets `0`, `524288`, `1048576`, `1572864`,
`2097152`, `2621440`, `3145728`, and `3670016`.

The argv begins with the following global options:

```text
--parallel --parallel-max 4 --fail-early
```

It then contains eight transfer groups separated by `--next`. Each group is:

```text
--fail --silent --show-error --location --max-redirs 3
--proto =https --proto-redir =https --http1.1
--connect-timeout 15 --max-time 30
--range START-(START+32767) --max-filesize 32768
--dump-header /trafficlab-study/bursty-INDEX.headers
--output /dev/null --url URL
```

The stored TOML contains the fully expanded direct argv. The support script
validates every token, range, header path, and URL before it creates a run. Each
of the eight final responses must be `206` with the exact requested
`Content-Range`, the capability check's same `TOTAL`, and
`Content-Length: 32768`.

## Common experiment policy

The following table is the exhaustive `ExperimentConfig` oracle. No omitted
default is allowed in a realized `experiment.toml`.

| Field | Locked value |
|---|---|
| `run.directory` | unique absolute primary or reproduction run path |
| `run.minimum_free_bytes` | `1048576` |
| `run.master_seed` | `73` |
| `run.final_seed` | `97` |
| `target.image` | exact curl digest named in this design |
| `target.argv` | exact fully expanded named workload profile |
| `target.environment` | empty object |
| `target.working_directory` | `/` |
| `target.mounts` | exactly one mount |
| `target.mounts[0].source` | absolute resolved `.study-work/mount/STUDY_ID`, identical in all ten runs |
| `target.mounts[0].target` | `/trafficlab-study` |
| `target.mounts[0].read_only` | `false` |
| `capture.image` | exact prerequisite `sha256:...` image ID, identical in every run |
| `capture.network_probe_url` | operator URL |
| `capture.readiness_timeout_seconds` | `10.0` |
| `capture.workload_timeout_seconds` | `35.0` short/bursty; `50.0` streaming |
| `capture.flush_timeout_seconds` | `5.0` |
| `capture.total_timeout_seconds` | `90.0` short/bursty; `120.0` streaming |
| `generation.trial.max_packets` | `25000` |
| `generation.trial.max_output_bytes` | `40000000` |
| `generation.trial.max_wall_seconds` | `5.0` |
| `generation.final.max_packets` | `50000` |
| `generation.final.max_output_bytes` | `80000000` |
| `generation.final.max_wall_seconds` | `10.0` |
| `genetic.population_size` | `6` |
| `genetic.generation_count` | `2` |
| `genetic.tournament_size` | `2` |
| `genetic.elite_count` | `1` |
| `genetic.trial_seeds` | `[17, 29]` |
| `genetic.duplicate_mutation_attempts` | `3` |
| `genetic.early_stopping_generations` | `0` |
| `genetic.early_stopping_tolerance` | `0.0` |
| `genetic.resume` | `true` |
| `models.enabled` | `["poisson_empirical", "markov_renewal", "mmpp"]` |
| `similarity.iat_diagnostic_quantile` | `0.95` |
| `similarity.acf_lags` | `[1]` |
| `similarity.acf_lag_weights` | `[1.0]` |
| `similarity.acf_iat_weight` | `0.5` |
| `similarity.acf_size_weight` | `0.5` |
| `similarity.multiscale_widths_seconds` | `[0.001, 0.01]` short/bursty; `[0.25, 1.0]` streaming |
| `similarity.multiscale_scale_weights` | `[0.5, 0.5]` |
| `similarity.multiscale_packet_weight` | `0.5` |
| `similarity.multiscale_byte_weight` | `0.5` |
| `similarity.max_direction_bin_cells` | `100000` |
| `similarity.method_weights.frame_size_ks` | `0.25` |
| `similarity.method_weights.iat_ks` | `0.25` |
| `similarity.method_weights.autocorrelation` | `0.25` |
| `similarity.method_weights.multiscale_rate` | `0.25` |

The exact family operator and gene-bound oracle is:

| Family and field | Locked value |
|---|---|
| `poisson_empirical.crossover_probability` | `0.9` |
| `poisson_empirical.mutation_probability` | `1.0` |
| `poisson_empirical.mutation_scale` | `0.1` |
| `poisson_empirical.c_lambda` | lower `0.25`, upper `4.0` |
| `markov_renewal.crossover_probability` | `0.9` |
| `markov_renewal.mutation_probability` | `0.2` |
| `markov_renewal.mutation_scale` | `0.1` |
| `markov_renewal.q1` | lower `0.1`, upper `0.4` |
| `markov_renewal.q2` | lower `0.6`, upper `0.9` |
| `markov_renewal.alpha` | lower `0.0`, upper `2.0` |
| `markov_renewal.r` | lower `1`, upper `8` |
| `markov_renewal.c_t` | lower `0.25`, upper `4.0` |
| `mmpp.crossover_probability` | `0.9` |
| `mmpp.mutation_probability` | `0.25` |
| `mmpp.mutation_scale` | `0.1` |
| `mmpp.q01` | lower `0.01`, upper `10.0` |
| `mmpp.q10` | lower `0.01`, upper `10.0` |
| `mmpp.lambda0` | lower `10.0`, upper `100.0` |
| `mmpp.lambda1` | lower `0.1`, upper `1000.0` |

The four method-weight fields are exactly `frame_size_ks`, `iat_ks`,
`autocorrelation`, and `multiscale_rate`. The shared scratch mount source is the
one absolute resolved `.study-work/mount/STUDY_ID` directory; it is identical in
all ten experiments. Only the operator URL, `run.directory`, the named profile
argv, profile capture timeouts, and the explicitly named profile multiscale
widths may differ. The prerequisite capture image ID is resolved once and then
locked, not varied.

The MMPP low-rate lower bound is `10.0` rather than the package-wide example
minimum. With the locked population and selection seeds, this keeps at least one
MMPP candidate evaluable in the observed sub-second short-transfer window while
preserving a full decade of low-regime rates. The original `0.01` study bound
made both deterministic MMPP candidates fail the enabled metric preconditions
before family comparison.

The total deadlines bound each capture lifecycle; the genetic and comparison
stages retain their existing independent guards.

Every width must still pass the ordinary post-capture validation against the
derived `W`. A workload that is too short or too sparse for the configured
metrics is a failed study run, not permission to drop a metric after seeing its
score. Any protocol revision follows a new complete nine-run study rather than
selective reruns.

The study compares repetitions within a workload. It does not treat aggregate
scores from different multiscale configurations as one homogeneous sample.

## Study-support boundary

Add one typed script, `scripts/run_validation_study.py`, and no supporting package
or framework. It has two narrow subcommands:

```text
prerequisites --url URL --study-id ID
study --url URL --study-id ID
      --prerequisites examples/validation_study/prerequisites.json
```

`prerequisites` owns only the exact preparation, config rendering, and evidence
operations defined above. `study` accepts the three resulting checked config
paths through fixed locations under `examples/validation_study/configs/`. Tests inject a
temporary repository root through the Python boundary rather than add public
path flags. Both subcommands require a study ID matching
`[a-z0-9][a-z0-9-]{0,31}`. Their operator commands are:

```bash
uv run --locked python scripts/run_validation_study.py \
  prerequisites --url "$TRAFFICLAB_INTERNET_URL" --study-id "$STUDY_ID"

uv run --locked python scripts/run_validation_study.py \
  study --url "$TRAFFICLAB_INTERNET_URL" --study-id "$STUDY_ID" \
  --prerequisites examples/validation_study/prerequisites.json
```

These support-script commands are intentionally unwrapped; only the exact child
commands identified in this design use `run_bounded.sh`. `study` performs:

1. validate the prerequisite study ID, URL, capability evidence, target image
   digest, base configs, exact workload argv, common settings, enabled families,
   and seed separation;
2. derive nine unique effective config files and absent run directories in the
   fixed balanced order;
3. prepare the exact header scratch files and call the production
   `run_experiment()` function once per fresh primary run;
4. measure total elapsed time with `time.perf_counter()` immediately around that
   call, after image preparation and URL capability validation;
5. archive and validate header evidence, returned values, and all persisted
   artifacts with existing strict parsers, then extract study records;
6. compare repeated references symmetrically and compute descriptive summaries;
7. derive a tenth config from saved `streaming` repeat 2 configuration by
   changing only `run.directory`, prepare its exact streaming header scratch,
   then run that fresh experiment through the installed CLI under the exact
   individual process-tree guard below and archive its header;
8. reconstruct and directly evaluate the tenth held-out result through the
   production evaluation boundary without modifying its run;
9. atomically render canonical, sorted, finite JSON to
   `examples/validation_study/results.json`.

The script imports existing configuration, trace, checkpoint, model, and
similarity functions. It does not duplicate those codecs. It may use small
injected callables for `run_experiment`, the monotonic timer, the exact
subprocess runner, and file reads so unit tests remain Docker- and
Internet-free.

Only these aggregate boundaries are immutable dataclasses:

```text
WorkloadSpec
StudyRunSpec
StudyRunRecord
ReproductionRecord
StudyResults
PrerequisiteResults
```

These types are private study boundaries, not production API. Schema names
below describe JSON shapes, not a requirement for one class per leaf. Small
typed constructor/validator helpers build leaf dictionaries and arrays. The
JSON codec strictly validates exact keys, scalar types, finite ranges, unique
workload and repeat identities, nine-run order, family coverage, seeds, hashes,
and derived statistics before publication. Do not create dozens of leaf record
classes or a general serialization framework.

## Evidence extraction and statistical policy

### Family competition

Load the terminal `checkpoint.json` with its compatibility context. For each of
the three families, select its highest-fitness valid terminal candidate, using
the established stable candidate-ID tie rule. Record:

- family and candidate ID;
- canonical genes;
- selection fitness;
- the two exact selection seeds;
- mean selection aggregate score;
- mean selection component score for each of the four methods.

All three family champions are selection evidence. They were optimized and
selected on `[17, 29]`; the report must not describe their scores as held-out
generalization results.

### Winner and fresh final evidence

Record the overall selected winner separately. The in-process `FitOutcome`
retains its one `final_trials` entry, which must use exactly seed `97`. Require
`97 not in [17, 29]`, require the `FitOutcome` winner family and genes to match
the published best model, and record its held-out aggregate and four component
scores.

The later `generated.pcapng` also uses seed `97` under `generation.final`, while
winner validation uses `generation.trial`. Record the final published aggregate
and component scores from `similarity.json` separately from the held-out trial.

After loading `best_model.json`, the support script regenerates the selected
winner in memory twice with seed `97`, the same stored `W`, and respectively the
trial and final limits. Both calls must complete naturally, and their raw
canonical event tuples must be exactly equal. Larger final guards cannot change
a sequence that already completed under trial guards. Re-evaluating the raw
trial-limit tuple must reproduce the `FitOutcome.final_trials` aggregate,
components, and diagnostics.

Quantize that one raw tuple with the production boundary, render and reparse it,
and require the reparsed tuple to equal `RunResult.generation.events`. Only this
documented timestamp quantization and PCAPNG reparse may explain a difference
between held-out raw scores and published `similarity.json` scores. A raw event
difference, mark difference, count difference, or unexplained score difference
is a product defect and aborts the study.

### Runtime

Runtime is total wall time for one cached-image call to `run_experiment()`,
including full Docker preflight, capture, fit, final generation, comparison, and
cleanup. URL capability checks, image pulls/builds, and report rendering are not
timed. A resumed or reused stage invalidates primary runtime evidence.

Every primary run requires an absent run directory and reports all reuse flags
as false. A failed attempt is preserved under the ignored raw-run root, recorded
as a failure, and excluded from the nine successful primary observations. The
entire balanced nine-run protocol then restarts under a new study identifier;
selective replacement would break order balance.

### Trace summaries

For each reference and generated trace, record:

- packet count and observation window;
- outbound/inbound packet counts and captured-byte totals;
- frame-length minimum, median, upper diagnostic quantile, and maximum;
- IAT count, zero-IAT count, median, upper diagnostic quantile, and maximum;
- the per-scale directional packet and byte totals already available from
  multiscale diagnostics.

Trace inspection uses these canonical summaries and the existing metric
diagnostics. No payload or application-protocol analysis is added.

For an ordered sample of size `n`, the median is its middle value for odd `n`
and the arithmetic mean of its two middle values for even `n`. The upper
diagnostic quantile uses the configured `q = 0.95` and the existing nearest-rank
definition at one-based rank `ceil(q * n)`. Empty IAT samples remain metric
precondition failures; the support script does not assign them null summaries.

### Natural capture variation

For each workload, compare every pair of its three references in both
directions. For `A -> B`, normalize `A`, align and crop `B` to `W_A`, and run the
existing four-method comparison with that workload's settings. Repeat as
`B -> A`; average the two aggregate scores and each pair of component scores.
This symmetric descriptive value avoids silently privileging either unequal
capture window.

Record all three unordered pairs, plus variance in reference packet count,
window, direction totals, and captured bytes. If either directional comparison
fails a metric precondition, the study fails with the exact precondition rather
than inventing a score.

### Descriptive statistics

For each workload, compute the arithmetic mean, minimum, maximum, range, sample
variance with denominator `n - 1`, and sample standard deviation for:

- complete-run runtime;
- each family's champion selection fitness and four mean selection components;
- selected-winner fitness;
- held-out aggregate and each held-out component;
- final published aggregate and each published component;
- reference packet count, window, and directional packet/byte totals.

Record winning-family counts separately. With only three repetitions, these are
descriptive pilot statistics. The report makes no confidence-interval,
hypothesis-test, or population-generalization claim.

## Result schema

The following contracts are the executable serialization oracle. Every object
requires exactly the listed keys and rejects unknown keys. No value is nullable.
JSON integers are exact non-boolean integers; JSON numbers are exact finite
floats unless a field is declared integer. SHA-256 values are 64 lowercase
hexadecimal characters. Timestamps are UTC RFC 3339 strings. Canonical output
uses UTF-8, lexically sorted object keys, compact separators, one trailing
newline, and `allow_nan = false`.

Arrays have the order stated below. Maps named as exact have neither additional
nor missing keys. Every bounded score is in `[0.0, 1.0]`.

Every filesystem path stored in either checked JSON file is a normalized,
repository-relative POSIX string: no leading slash, backslash, empty segment,
`.` segment, or `..` segment is allowed. Live validation resolves it against
the discovered repository root, requires the result to remain beneath that
root, and only then accesses it. Checked JSON never stores host-absolute paths.
For command arrays, the checked value is the exact canonical projection: a
standalone repository path or the `src` member of a Docker mount token remains
repository-relative. The live subprocess builder resolves only that operand to
an absolute host path immediately before execution and rejects any other token
difference.
This serialization rule does not change the production loader: realized
`ExperimentConfig` paths and the saved effective `experiment.toml` remain
absolute after `load_experiment()` resolves them.

### Prerequisite evidence schema

`examples/validation_study/prerequisites.json` has these exact root fields:

| Key | Type | Invariant |
|---|---|---|
| `schema_version` | integer | exactly `1` |
| `created_utc` | string | valid UTC RFC 3339 |
| `study_id` | string | exact validated study ID |
| `git_commit` | string | current 40-character lowercase commit |
| `git_tree_clean` | boolean | exactly `true` |
| `url` | string | exact validated operator URL |
| `tools` | `ToolRecord` | exact live versions |
| `images` | `ImageRecord` | exact inspected images |
| `capability` | `CapabilityRecord` | pinned-curl range and mount proof |
| `config_sha256` | exact profile hash map | rendered short, streaming, bursty configs |
| `commands` | array of `CommandRecord` | exactly Docker matrix, then Internet smoke |

`ProfileHashMap` has exactly the keys `short`, `streaming`, and `bursty`; each
value is the SHA-256 of that profile's canonical rendered base config. This map
is serialized in lexical key order, as are all JSON objects.

`ToolRecord` has exactly `python_version`, `trafficlab_version`,
`docker_engine_version`, `docker_compose_version`, and `platform`; all are
nonempty strings. Python must be `3.12.3` and Trafficlab must match the current
package.

`ImageRecord` has exactly:

| Key | Type | Invariant |
|---|---|---|
| `target_reference` | string | exact approved curl digest reference |
| `target_image_id` | string | inspected `sha256:...` ID |
| `target_repo_digests` | array of string | nonempty, sorted, includes approved digest |
| `target_config_user` | string | exact inspected image `Config.User`, possibly empty |
| `capture_image_id` | string | exact built `sha256:...` ID |
| `capture_dockerfile_sha256` | string | exact source hash |
| `capture_script_sha256` | string | exact source hash |

Each `CommandRecord` has exactly:

| Key | Type | Invariant |
|---|---|---|
| `kind` | string | `docker_matrix` or `internet_smoke` |
| `argv` | array of string | exact guarded argv from this design |
| `started_utc` | string | UTC RFC 3339 |
| `completed_utc` | string | not earlier than start |
| `exit_status` | integer | exactly `0` |
| `tests` | `TestCountRecord` | actual JUnit counts |
| `stdout_sha256` | string | hash of retained ignored output |
| `stderr_sha256` | string | hash of retained ignored output |
| `junit_sha256` | string | hash of parsed retained JUnit XML |

`TestCountRecord` has exactly integer fields `total`, `passed`, `failed`,
`errors`, and `skipped`. Require `total > 0`, `passed = total`, and the last
three fields equal zero. The command array order is `docker_matrix`, then
`internet_smoke`; duplicate kinds are invalid.

`CAPABILITY_HEADER_PATH` is exactly
`examples/validation_study/.study-work/evidence/STUDY_ID/00-prerequisites/capability.headers`.
`CapabilityRecord` has exactly:

| Key | Type | Invariant |
|---|---|---|
| `argv` | array of string | exact repository-relative projection of Docker/curl argv |
| `started_utc` | string | UTC RFC 3339 |
| `completed_utc` | string | not earlier than start |
| `exit_status` | integer | exactly `0` |
| `status` | integer | exactly `206` |
| `content_length` | integer | exactly `1` |
| `object_size_bytes` | integer | 4 MiB through 16 MiB inclusive |
| `redirect_count` | integer | in `0..3` and equals write-out evidence |
| `body_bytes_downloaded` | integer | exactly `1` |
| `content_range` | string | exactly `bytes 0-0/TOTAL` |
| `final_url` | string | exact validated write-out URL |
| `mount_source` | string | `examples/validation_study/.study-work/mount/STUDY_ID` |
| `canary_archive_path` | string | exactly `CAPABILITY_HEADER_PATH` |
| `canary_sha256` | string | hash of nonempty final header bytes |
| `container_id` | string | exact retained CID and inspected target ID |
| `stdout_sha256` | string | hash of retained exact write-out bytes |
| `stderr_sha256` | string | hash of retained exact stderr bytes |
| `used_image_default_user` | boolean | exactly `true`; no `--user` in argv |
| `mount_directory_mode` | integer | exactly `493`, decimal form of `0755` |
| `canary_file_mode` | integer | exactly `438`, decimal form of `0666` |
| `canary_archive_mode` | integer | exactly `384`, decimal form of `0600` |
| `container_cleanup_verified` | boolean | exactly `true` |

### Shared study result records

`RunKey` has exactly `workload` and `repeat`. Workload is one of `short`,
`streaming`, or `bursty`; repeat is an integer in `1..3`. Primary order is the
exact nine-entry table in this design.

`CandidateIdRecord` has exactly nonnegative integer fields `birth_generation`
and `birth_index`.

Every `genes` field is the family-specific canonical array from the production
registry, with exact scalar types and inclusive bounds from the experiment
oracle:

| Family | Array order and types | Additional invariant |
|---|---|---|
| `poisson_empirical` | `[c_lambda: float]` | none |
| `markov_renewal` | `[q1: float, q2: float, alpha: float, r: integer, c_t: float]` | `q1 < q2` |
| `mmpp` | `[q01: float, q10: float, lambda0: float, lambda1: float]` | `lambda0 < lambda1` |

`MethodScoreRecord` has exactly four float fields in published method order:
`autocorrelation`, `frame_size_ks`, `iat_ks`, and `multiscale_rate`.
`ScoreRecord` has exactly `aggregate` and `methods`; `aggregate` is a bounded
score and `methods` is a `MethodScoreRecord`.

`DescriptiveRecord` has exactly float fields `mean`, `minimum`, `maximum`,
`range`, `sample_variance`, and `sample_standard_deviation`, plus integer
`count`. Require `count = 3`, `range = maximum - minimum`, nonnegative variance
and standard deviation, and recomputation from the three source observations.

`ScoreSummaryRecord` has exactly `aggregate` and `methods`. `aggregate` is a
`DescriptiveRecord`; `methods` is an exact method-name map of
`DescriptiveRecord` values.

`DirectionValueRecord` has exactly nonnegative integer fields `outbound` and
`inbound`.

`SampleRecord` has exactly:

| Key | Type | Invariant |
|---|---|---|
| `count` | integer | positive |
| `minimum` | float | finite, nonnegative |
| `median` | float | exact definition in this design |
| `quantile_probability` | float | exactly `0.95` |
| `quantile` | float | exact nearest-rank value |
| `maximum` | float | require minimum <= median/quantile <= maximum |
| `zero_count` | integer | in `0..count`; zero for frame lengths |

`ScaleTotalRecord` has exactly float `width_seconds`, positive integer
`bins_per_direction`, and `DirectionValueRecord` fields `packet_totals` and
`byte_totals`. The width is finite and positive.

`TraceSummaryRecord` has exactly:

| Key | Type | Invariant |
|---|---|---|
| `packet_count` | integer | at least two and equals frame sample count |
| `observation_window_seconds` | float | finite and positive |
| `packet_totals` | `DirectionValueRecord` | directions sum to packet count |
| `byte_totals` | `DirectionValueRecord` | positive captured-byte totals |
| `frame_lengths` | `SampleRecord` | positive lengths, zero count zero |
| `iats` | `SampleRecord` | count equals packet count minus one |
| `scales` | array of `ScaleTotalRecord` | exact configured ascending widths |

`TransferResponseRecord` has exactly:

| Key | Type | Invariant |
|---|---|---|
| `transfer_index` | integer | zero-based contiguous profile index |
| `requested_start` | integer | exact nonnegative profile start |
| `requested_end` | integer | at least start and exact profile end |
| `status` | integer | exactly `206` |
| `content_length` | integer | exact inclusive requested range length |
| `content_range` | string | exact range and capability object total |
| `header_archive_path` | string | exact evidence directory plus profile filename |
| `header_sha256` | string | exact nonempty archive hash |
| `scratch_precreate_mode` | integer | exactly `438`, decimal `0666` |
| `archive_mode` | integer | exactly `384`, decimal `0600` |
| `inode_preserved` | boolean | exactly `true` from precreate through parse |

Short and streaming have one response; bursty has eight in transfer-index
order. Every archive path is unique and resolves beneath the record's
`transfer_evidence_directory`.

`FamilyChampionRecord` has exactly `family`, `candidate_id`, `genes`,
`selection_fitness`, `selection_seeds`, and `selection_score`. Genes are a
family-specific canonical array, seeds are exactly `[17, 29]`, fitness is a
bounded score, and `selection_score.aggregate` equals fitness. Champion arrays
are exactly lexical family order: `markov_renewal`, `mmpp`, then
`poisson_empirical`.

`WinnerRecord` has exactly `family`, `candidate_id`, `genes`, and
`selection_fitness`; it must exactly identify the checkpoint overall winner and
published best model. `HeldOutRecord` has exactly `seed`, `score`, and `source`;
seed is `97`, and source is `run_experiment_fit_outcome` for primary runs or
`post_cli_evaluate_final` for the reproduction. `PublishedRecord` has exactly
`seed` and `score`; seed is `97`.

`HeldOutRecord.score` is the exact aggregate/component projection of one
authoritative production `TrialResult`; it is never recomputed from a published
PCAPNG. The primary source is `FitOutcome.final_trials[0]`. The reproduction
source is the sole post-CLI `evaluate_final()` result. Its method diagnostics
participate in the equality checks below even though the compact checked record
stores only aggregate and component scores.

`RawSequenceRecord` has exactly:

| Key | Type | Invariant |
|---|---|---|
| `seed` | integer | exactly `97` |
| `observation_window_seconds` | float | exact stored `W` |
| `trial_event_count` | integer | positive |
| `final_event_count` | integer | equals trial count |
| `raw_events_equal` | boolean | exactly `true` |
| `held_out_score_reproduced` | boolean | exactly `true` |
| `reparsed_event_count` | integer | equals generated artifact count |
| `reparsed_matches_quantized` | boolean | exactly `true` |

`ReuseRecord` has exactly boolean `capture`, `best_model`, `generated`, and
`similarity`; all are false for every fresh primary and reproduction run.

`ArtifactHashRecord` has exactly these nine SHA-256 fields:
`experiment.toml`, `reference.pcapng`, `capture.json`, `checkpoint.json`,
`ga_history.csv`, `best_model.json`, `generated.pcapng`, `similarity.json`, and
`run.log`.

`StudyRunRecord` has exactly:

| Key | Type | Invariant |
|---|---|---|
| `execution_order` | integer | unique `1..9` |
| `run_id` | string | exact run ID from the balanced-order table |
| `key` | `RunKey` | exact balanced-order entry |
| `config_path` | string | exactly `runs/validation_study/STUDY_ID/realized-configs/RUN_ID.toml` |
| `run_directory` | string | exactly `runs/validation_study/STUDY_ID/RUN_ID` |
| `transfer_evidence_directory` | string | exactly `examples/validation_study/.study-work/evidence/STUDY_ID/RUN_ID` |
| `elapsed_seconds` | float | finite and positive |
| `reuse` | `ReuseRecord` | all false |
| `cleanup_verified` | boolean | exactly `true` |
| `transfer_responses` | array of `TransferResponseRecord` | exact profile responses |
| `artifact_sha256` | `ArtifactHashRecord` | hashes validated files |
| `reference` | `TraceSummaryRecord` | parsed reference summary |
| `generated` | `TraceSummaryRecord` | parsed generated summary |
| `family_champions` | array of `FamilyChampionRecord` | exactly three lexical families |
| `winner` | `WinnerRecord` | exact selected winner |
| `held_out` | `HeldOutRecord` | fresh raw final evidence |
| `published` | `PublishedRecord` | final PCAPNG evidence |
| `raw_sequence` | `RawSequenceRecord` | exact generation checks |

### Natural-variation and summary records

`PairComparisonRecord` has exactly `left_repeat`, `right_repeat`, `forward`,
`reverse`, and `symmetric`. Repeats form exactly `(1, 2)`, `(1, 3)`, and
`(2, 3)` in that order. Each score field is a `ScoreRecord`; every symmetric
value equals the arithmetic mean of its two directional values.

`NaturalVariationRecord` has exactly `workload`, `pairs`, and
`reference_descriptors`. `pairs` contains the three pair records.
`reference_descriptors` is an exact map with these `DescriptiveRecord` keys:

```text
packet_count
observation_window_seconds
outbound_packets
inbound_packets
outbound_bytes
inbound_bytes
```

`FamilySummaryRecord` has exactly `selection_fitness` and
`selection_components`. The former is a `DescriptiveRecord`; the latter is an
exact method-name map of `DescriptiveRecord` values.

`WorkloadSummaryRecord` has exactly:

| Key | Type | Invariant |
|---|---|---|
| `workload` | string | ordered short, streaming, bursty |
| `runtime` | `DescriptiveRecord` | three fresh primary runtimes |
| `family_champions` | exact family map | `FamilySummaryRecord` values |
| `winner_selection_fitness` | `DescriptiveRecord` | three selected winners |
| `held_out` | `ScoreSummaryRecord` | three fresh winner trials |
| `published` | `ScoreSummaryRecord` | three final PCAPNG comparisons |
| `reference_descriptors` | exact descriptor map | same keys as natural variation |
| `winner_counts` | exact family integer map | nonnegative and sums to three |

### Study result root and protocol records

`examples/validation_study/results.json` has these exact root fields:

| Key | Type | Invariant |
|---|---|---|
| `schema_version` | integer | exactly `1` |
| `environment` | `EnvironmentRecord` | exact live environment |
| `protocol` | `ProtocolRecord` | exact locked protocol |
| `runs` | array of `StudyRunRecord` | exactly nine in balanced order |
| `natural_variation` | array of `NaturalVariationRecord` | exactly short, streaming, bursty |
| `workload_summaries` | array of `WorkloadSummaryRecord` | exactly short, streaming, bursty |
| `reproduction` | `ReproductionRecord` | fresh tenth complete run |

`EnvironmentRecord` has exactly nonempty string fields `git_commit`,
`python_version`, `trafficlab_version`, `docker_engine_version`,
`docker_compose_version`, `platform`, `target_image_id`, `capture_image_id`, and
`study_date_utc`. The date is a UTC RFC 3339 timestamp. The commit, versions,
and image IDs must equal prerequisite evidence and the live environment.
Prerequisite evidence proves the tree was clean before evidence generation;
`study` then permits only the expected checked Validation Study config, prerequisite,
result, and report paths plus ignored raw evidence to differ.

The result `CapabilityRecord` is byte-for-byte the prerequisite capability
object defined above; `study` does not synthesize or refresh it.

`SeedRecord` has exactly integer `master` and `final`, plus integer array
`selection`; values are `73`, `97`, and `[17, 29]`, and final is absent from
selection.

`WorkloadDefinitionRecord` has exactly `name`, `argv`,
`workload_timeout_seconds`, `total_timeout_seconds`, and
`multiscale_widths_seconds`. Values exactly match the named profile oracle.
Definitions are ordered short, streaming, bursty.

`ProtocolRecord` has exactly:

| Key | Type | Invariant |
|---|---|---|
| `study_id` | string | matches `[a-z0-9][a-z0-9-]{0,31}` |
| `url` | string | exact operator URL |
| `capability` | `CapabilityRecord` | successful bounded check |
| `prerequisites_sha256` | string | exact canonical prerequisite file hash |
| `target_reference` | string | exact approved curl digest |
| `capture_image_id` | string | exact prerequisite image ID |
| `transfer_evidence_mount_source` | string | `examples/validation_study/.study-work/mount/STUDY_ID` |
| `base_config_sha256` | `ProfileHashMap` | exactly equals prerequisite config hashes |
| `primary_order` | array of `RunKey` | exact nine-entry balanced order |
| `seeds` | `SeedRecord` | locked seeds |
| `families` | array of string | exact lexical family order |
| `methods` | array of string | exact published method order |
| `workloads` | array of `WorkloadDefinitionRecord` | short, streaming, bursty |
| `runtime_boundary` | string | exactly `run_experiment_cached_images_full_lifecycle` |

That runtime token denotes the complete cached-image `run_experiment()` timing
boundary in the Runtime section; it excludes prerequisite work and report
rendering.

### Fresh reproduction record

`DeltaScoreRecord` has exactly float `aggregate` and an exact method-name float
map `methods`; each delta is in `[-1.0, 1.0]` and equals reproduction minus
source.

`ReproductionComparisonRecord` has exactly `winner_family_equal`,
`winner_genes_equal`, `winner_selection_fitness_delta`, `held_out_delta`,
`published_delta`, and `reference_similarity`. The first two are booleans, the
fitness delta is finite in `[-1.0, 1.0]`, the next two are `DeltaScoreRecord`,
and reference similarity is a symmetric `ScoreRecord` between source and new
captures. No equality outcome is required.

`ReproductionRecord` has exactly:

| Key | Type | Invariant |
|---|---|---|
| `source_key` | `RunKey` | streaming repeat 2 |
| `execution_order` | integer | exactly `10` |
| `run_id` | string | exactly `10-streaming-r2-reproduction` |
| `config_path` | string | exact `runs/validation_study/STUDY_ID/realized-configs/reproduction.toml` |
| `run_directory` | string | exactly `runs/validation_study/STUDY_ID/10-streaming-r2-reproduction` |
| `transfer_evidence_directory` | string | exact `examples/validation_study/.study-work/evidence/STUDY_ID/RUN_ID` |
| `command` | array of string | exactly `uv`, `run`, `--locked`, `trafficlab`, `run`, config path |
| `guard_command` | array of string | exact bounded wrapper plus `command` |
| `guard_exit_status` | integer | exactly `0` |
| `guard_stdout_sha256` | string | hash of retained ignored CLI stdout |
| `guard_stderr_sha256` | string | hash of retained ignored guard/CLI stderr |
| `elapsed_seconds` | float | finite and positive, not primary runtime sample |
| `changed_config_fields` | array of string | exactly `["run.directory"]` |
| `same_locked_config` | boolean | exactly `true` after structural comparison |
| `seeded_artifact_count` | integer | exactly `0` before execution |
| `cleanup_verified` | boolean | exactly `true` |
| `reuse` | `ReuseRecord` | all false |
| `transfer_responses` | array of `TransferResponseRecord` | one exact streaming response |
| `artifact_sha256` | `ArtifactHashRecord` | nine new artifact hashes |
| `reference` | `TraceSummaryRecord` | new real capture |
| `generated` | `TraceSummaryRecord` | new generated capture |
| `family_champions` | array of `FamilyChampionRecord` | three new champions |
| `winner` | `WinnerRecord` | new selected winner |
| `held_out` | `HeldOutRecord` | new fresh seed-97 evidence |
| `published` | `PublishedRecord` | new final comparison |
| `raw_sequence` | `RawSequenceRecord` | new exact generation checks |
| `comparison_to_source` | record | `ReproductionComparisonRecord` |

All derived values are recomputed during parse validation. The report may round
values for readability; both JSON files retain full serialized values.

For `ReproductionRecord`, `command` is exactly
`["uv", "run", "--locked", "trafficlab", "run", config_path]`, where its final
element equals that record's `config_path`. `guard_command` is exactly:

```text
["scripts/run_bounded.sh", "--memory-high", "2G", "--memory-max", "3G",
 "--swap-max", "512M", "--wall-time", "20m", "--kill-after", "10s", "--",
 ...command]
```

## Checked-in and ignored artifacts

Validation Study adds this checked tree:

```text
examples/validation_study/
  README.md
  REPORT.md
  prerequisites.json
  configs/
    short.toml
    streaming.toml
    bursty.toml
  results.json
```

`README.md` gives the endpoint contract, preparation, study, validation, and
reproduction commands. The three configs are complete valid experiments
rendered only after prerequisites pass, with the operator URL, exact
digest-pinned client, exact capture image ID, and expanded profile argv. No
reserved hostname, unresolved image tag, token, or sentinel value is committed as
real-study configuration. Results identify the exact base and realized
effective configuration hashes.

`REPORT.md` contains:

1. question, scope, environment, and protocol;
2. natural-variation table;
3. every family's champion fitness/component summaries, run-to-run variance,
   and winner counts;
4. held-out winner, final published score, and runtime tables;
5. trace inspection and major metric disagreements;
6. saved-run reproduction evidence;
7. limitations and one evidence-backed next-work decision.

Raw run directories live under ignored `runs/validation_study/STUDY_ID/`. Do not check in
any Internet PCAPNG, checkpoint, full run log, generated PCAPNG, response
header, JUnit XML, command output, or failed-attempt directory. The existing
`runs/` rule covers the run root; implementation adds the exact
`examples/validation_study/.study-work/` ignore and does not ignore the checked Validation Study
JSON, configs, README, or report.

Header, JUnit, and command evidence lives only under
`examples/validation_study/.study-work/evidence/STUDY_ID/`, outside every production run
directory. Relevant hashes remain in the checked JSON files and both ignored
study-ID trees remain available for audit by default. After accepting the
report, the operator may manually remove those two exact study-ID trees when
local audit evidence is no longer needed; the support script never performs
that retention deletion. This limits publication of observed network addresses
and server headers and avoids large nondeterministic fixtures. It is a
study-publication rule, not a new security subsystem.

## Saved-run reproduction

The protocol preselects `streaming` repeat 2 before any score is observed. After
all nine primary runs, copy its saved effective `experiment.toml`, change only
`run.directory` to the new absent
`runs/validation_study/STUDY_ID/10-streaming-r2-reproduction/` directory, render and
reload it, and require structural equality for every other exhaustive config
field. Write the caller config outside that absent run directory at
`runs/validation_study/STUDY_ID/realized-configs/reproduction.toml`. Do not copy or seed
`capture.json`, a PCAPNG, checkpoint, history, model, similarity, run log, or any
other stage artifact into the new directory.

Before publishing results, the support script invokes this command from the
unchanged checkout and locked CPython 3.12.3 environment:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked trafficlab run \
  runs/validation_study/STUDY_ID/realized-configs/reproduction.toml
```

The support script invokes that exact top-level argv with `shell=False`; the
inner CLI argv is exactly the suffix after `--`. The 20-minute bound covers the
streaming capture total of 120 seconds, at most 16 evaluated candidate slots at
two 5-second trial generations each, one 5-second held-out generation, one
10-second final generation, and more than 15 minutes of Docker, fitting,
comparison, serialization, and cleanup margin. The existing five guard flags
bound the complete CLI process tree with the established 2/3 GiB and 512 MiB
swap policy.

The documented `prerequisites` and `study` support subcommands are invoked
directly from the repository root, not inside `run_bounded.sh`. They launch the
individual guarded prerequisite commands and this individual guarded CLI
command themselves. Thus no `run_bounded.sh` call creates a nested systemd
scope. Primary in-process runs retain their production capture deadlines and
per-generation guards; the support script never claims a second whole-study
scope.

The installed CLI must execute a genuine fresh tenth full preflight, real
Internet capture, fit, generation, comparison, and cleanup. Every reuse field
is false and all nine ordinary artifacts are newly created and strictly
validated.

Require the same target profile, image IDs, mount, timeouts, models, bounds,
operators, similarity settings, master seed `73`, selection seeds `[17, 29]`,
and fresh final seed `97`. Because the installed CLI returns only an exit
status, its held-out evidence is reconstructed after success through production
read-only APIs:

1. strictly load the new effective `experiment.toml`, capture metadata,
   reference PCAPNG, terminal checkpoint, and best model;
2. normalize the new reference and call `make_strategy_context()` with the
   exact experiment, reference, and capture hashes;
3. call `validate_evaluation_context()` on that context's evaluation member to
   obtain `validated_context`,
   select the checkpoint's exact best candidate, and require its family and
   genes to equal the strict best model;
4. invoke the existing `evaluate_final(candidate, validated_context, 97)`
   directly and require exactly one returned `TrialResult`; that result,
   generated under the configured trial guards, is the reproduction held-out
   authority and projects to `HeldOutRecord`;
5. independently call the registered family with the stored best model, seed
   `97`, the same `W`, and first the trial then final limits; require both calls
   to complete and return exactly equal raw canonical event tuples;
6. compare the trial-limit raw tuple through `compare_traces()` and require its
   aggregate, four components, and diagnostics to equal the returned
   `TrialResult`;
7. quantize that raw tuple through the production boundary and require it to
   equal the parsed new `generated.pcapng`; set `reparsed_generated` to the
   result of the existing `align_generated(parsed_generated, W)` boundary.
   From the current strict bytes and loaded config, construct exactly:

   ```text
   input_sha256 = {
       "capture_json": sha256_bytes(capture_json_bytes),
       "reference_pcapng": sha256_bytes(reference_pcapng_bytes),
       "generated_pcapng": sha256_bytes(generated_pcapng_bytes),
       "similarity_settings": similarity_settings_sha256(config.similarity),
   }
   ```

   Then compute exactly
   `compare_traces(reference, reparsed_generated, W, config.similarity)` and
   call `.with_input_sha256(input_sha256)` on that result. Require this
   lineage-bound `ComparisonResult` to equal the strictly parsed persisted
   `similarity.json` `ComparisonResult` exactly. Independently require the first
   three mapping values to equal this reproduction record's `ArtifactHashRecord`
   entries for `capture.json`, `reference.pcapng`, and `generated.pcapng`, and
   require the settings value to equal a fresh
   `similarity_settings_sha256(config.similarity)` call. No unbound comparison
   or alternate lineage construction satisfies this check.

These calls do not publish an artifact, append `run.log`, mutate the checkpoint,
or re-enter a production stage. Record the new trace summaries, all-family
champions, winner, held-out scores, published scores, runtime, artifact hashes,
and cleanup evidence only after every equality holds.

Public-network capture is naturally variable, so the reproduction does not
require byte-identical PCAPNG, checkpoint, winner, model, history, similarity,
or score output. Compare the new reference symmetrically with its source, report
winner family/gene equality as observed booleans, and report selection,
held-out, and published score differences honestly. This is configuration-and-
seed reproduction of a complete experiment, not deterministic replay and not
terminal artifact reuse.

## Failure and cleanup policy

The support script stops at the first failed primary or reproduction run,
preserves the run directory and original failure, and publishes no partial
official `results.json`. A prerequisite failure publishes no valid
`prerequisites.json`. Capture remains the sole owner of bounded project cleanup.
The script performs no broad Docker cleanup and never deletes or overwrites a
run or archive directory.

Before a transfer, the script may remove and exclusively recreate only the
profile's exact scratch header names in its own mount directory. It also
exclusively creates that run ID's evidence directory and refuses to overwrite a
prior archive. On success it archives and hashes the headers before removing
those scratch names. On failure it best-effort copies any ordinary scratch file,
including empty or malformed bytes, into that run ID's evidence directory and
preserves the scratch and run directory. An archive error is reported as
secondary context without replacing the original run failure. Capability
failure similarly preserves its canary and evidence; only a capability
container whose exact ID and study label prove ownership may be force-removed,
and its absence must be verified.

Before exiting, it records the failed workload, repeat, execution position, raw
run path, and corrective action to standard error. A later complete study uses
a new study ID and absent run directories. Docker prerequisite tests must pass
their label-based residue checks, and every primary capture must return only
after the existing cleanup verifier reports no remaining project resources.

Malformed artifacts, missing families, wrong seeds, reused primary stages,
nonfinite scores, failed URL capability, inconsistent lineage, a noncanonical
result, or an invalid statistic is a study failure. Scientific metric
disagreement is evidence to explain, not an infrastructure failure.

## Testing

Unit tests for `scripts/run_validation_study.py` use temporary files and injected
boundaries. They cover:

- HTTPS URL, redirect, credential, range response, object-size, and payload
  validation;
- exact pinned-curl capability argv, default image user, shared mount, file
  modes, write-out parsing, 45-second runner timeout, and exact-name cleanup;
- exact prerequisite commands, JUnit counts, tool/image identities, canonical
  prerequisite JSON, generated base-config hashes, and current-environment
  compatibility;
- exact curl image digest and all three expanded argv profiles;
- HTTPS-only curl protocols, finite deadlines, redirect caps, maximum file
  sizes, exact final response headers, and rejection of silent full downloads;
- fixed balanced order, unique run directories, and rejection of existing
  output targets;
- exact run IDs, repository-relative checked paths, escape rejection, sibling
  evidence archives, exact file modes, inode/scratch preservation, and
  nine-entry successful run trees;
- every exhaustive `ExperimentConfig` value, allowed profile differences,
  all-family enablement, exact seeds, and final-seed disjointness;
- strict extraction of terminal family champions and stable-ID ties;
- distinction among selection, held-out winner, and published final scores;
- exact trial/final raw sequence equality, held-out score reproduction, and
  quantized/reparsed generated-artifact equality;
- trace summaries and both directions of symmetric natural-variation scoring;
- odd/even median and nearest-rank quantile definitions;
- mean, range, sample variance, sample standard deviation, and winner counts;
- every nested schema key, type, order, invariant, non-null rule, canonical
  rendering, finite value, hash, and atomic publication;
- missing, malformed, inconsistent, reused, and failed-run evidence;
- fresh tenth-run derivation, zero seeded artifacts, installed-CLI execution,
  exact nonnested 20-minute guard, direct `evaluate_final()` reconstruction,
  complete artifact validation, and honest source/reproduction differences.

An in-process integration test extracts records from the existing deterministic
fit/run fixtures without Docker. It proves all three family records, trial seeds,
the fresh final seed, component ordering, lineage hashes, and strict artifact
loading through production codecs.

External validation consists of the existing dedicated Phase 3 Docker suite,
the opt-in Internet smoke, the nine serial fresh study runs, and the fresh tenth
saved-configuration reproduction. The ordinary fast and branch-coverage gates
remain Docker- and Internet-free.

Because `scripts/` is outside the current project-wide Pyright include, the
Validation Study gate type-checks the script explicitly without broadening unrelated
scope:

```bash
uv run --locked pyright scripts/run_validation_study.py \
  tests/unit/test_validation_study.py \
  tests/integration/test_validation_study_pipeline.py
```

## Metric-disagreement interpretation

The report starts from observed traces and diagnostics rather than aggregate
rank alone. Important patterns include:

- high frame-size KS with low multiscale score: empirical frame marks match,
  while time-local directional packet or byte volume does not;
- high IAT KS with low ACF score: marginal timing matches, while selected serial
  dependence does not;
- high ACF with low multiscale score: selected linear lags match, while burst
  placement, silence, or direction-separated volume does not;
- strong held-out trial but weaker published comparison: inspect only timestamp
  quantization and PCAPNG reparse effects after the required raw-sequence
  equality; final guards or same-seed randomness are not valid explanations.

The report names concrete diagnostic fields and visible trace summaries for
every claimed disagreement. Validation Study may recommend a later Roadmap idea only
when the behavior is repeatable and the current models or metrics demonstrably
miss it.

## Completion and external dependency

Safe local work includes the support script, deterministic unit/in-process
tests, locked config-rendering logic, report structure, dedicated Docker matrix,
static gates, and review. Final checked configs require the real URL and
resolved capture image ID. None of this work substitutes for Internet evidence.

At design approval, the exact blocker was Class 5: only the operator could
authorize selection of a public credential-free HTTPS object. Trafficlab could
not invent a URL, assume an endpoint's stability, or publish fabricated results.
The operator later authorized web selection, and the accepted 2026-08-14 study
used a qualifying 10 MiB range-capable object. Capability validation, nine
primary runs, the fresh reproduction, analysis, canonical publication, and the
offline audit completed; the evidence-backed Roadmap boxes are checked.

Future reproductions retain the same endpoint contract and Class 1--4 handling
for capability validation, bounded execution, endpoint incompatibility
diagnostics, study restart, analysis, reporting, and evidence-backed Roadmap
updates without further routine approval.
