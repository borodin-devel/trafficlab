# System Design

## Data flow

Trafficlab turns one experiment description into reproducible research outputs:

```text
Experiment TOML
  -> read-only preflight
  -> Docker reference capture
  -> canonical trace x_i = (t_i, d_i, l_i)
  -> heterogeneous genetic search over enabled model families
  -> best_model.json
  -> final generated trace and generated.pcapng
  -> component metrics and weighted similarity.json
```

The canonical scientific trace is an owned read-only `TrafficTrace` with three
one-dimensional equal-length NumPy columns: finite, nonnegative,
nondecreasing `float64` timestamps `t_i`; `uint8` directions `d_i` (`0` for
outbound and `1` for inbound); and strictly positive `uint32` captured frame
lengths `l_i`. Construction validates every column before narrowing integers,
copies into C-contiguous owned arrays, and marks each array non-writable.
Direction has exactly two values:

```text
d_i = outbound  when Ethernet source MAC equals capture.json target_mac
d_i = inbound   otherwise
```

For a reference with `n >= 2` finite, nondecreasing timestamps, define
`W = t_n - t_1` and require finite `W > 0`. Normalize every reference timestamp
as `t'_i = t_i - t_1`, so the reference occupies the closed interval `[0, W]`.
Packets at both endpoints are included.

`TrafficTrace.from_events()` and `TrafficTrace.to_events()` are the external
boundary for immutable `TraceEvent` records. Scapy PCAPNG reading and writing
may use those records only inside `trafficlab.common.scapy_io`. Normalization,
strategy setup, genetic evaluation, model repair/fit/generation, all eight
similarity methods, final comparison, and validation-study reconstruction retain
the exact `TrafficTrace` in memory. Generation results contain an immutable
`TrafficTrace`, including bounded diagnostic prefixes.

Before comparison, require a nonempty generated trace, shift it to its first
packet, and retain only events in `[0, W]`. The same W is passed explicitly to
fitting, generation, genetic evaluation, and every similarity method. Silence
outside the reference's first and last packets is outside MVP scope.

The rule applies to valid Ethernet frames captured on the target's
non-promiscuous `eth0`. Parsing requires the accompanying `capture.json`; a
missing file, invalid MAC, unsupported link type, or malformed frame is an error.
The research core works on canonical values, not on Scapy packets, file handles,
or Docker objects. `trafficlab.common.scapy_io` is the sole production PCAPNG boundary;
there is no selectable or fallback codec.

Generated Ethernet frames use `capture.json`'s target MAC. Their peer MAC is
`02:00:00:00:00:01`, unless that equals the target MAC, in which case it is
`02:00:00:00:00:02`. Outbound frames use the target as source; inbound frames use
the target as destination. Rendering any other direction value is an error.

### Raw capture normalization

Imported raw captures are decoded and encoded only inside
`trafficlab.common.scapy_io`. The decoder selects classic PCAP or PCAPNG from
the file magic, independently of the filename suffix. Classic PCAP accepts
version 2.4 in both byte orders and both microsecond and nanosecond timestamp
variants. PCAPNG accepts exactly one section in either byte order, multiple
interfaces within that section, Scapy-supported decimal or binary timestamp
resolutions, and packet blocks for which Scapy supplies a complete frame, wire
length, interface link type, and timestamp. Every packet must reference an
interface declared in that section and use Ethernet link type 1.

For classic PCAP, the timestamp is the exact fraction
`(seconds * resolution + subsecond_ticks) / resolution`. Scapy exposes PCAPNG
`tsresol` metadata as ticks per second, so its exact timestamp is
`((timestamp_high << 32) | timestamp_low) / tsresol`. Timestamp components must
be nonnegative, finite, within their encoded integer ranges, and representable
in canonical microsecond PCAPNG. Captured length must equal the exact returned
frame bytes and be at least 14; wire length must be no smaller than captured
length. Before Scapy decoding, Trafficlab validates every fixed block header
handled by its raw PCAPNG reader, packet interface references, packet data
bounds, and decoder-sensitive metadata lengths. Missing timestamps, repeated
sections, malformed or truncated containers and blocks, unsupported magic or
versions, non-Ethernet packets, and inconsistent lengths are errors.

Each validated frame is written exactly once to a temporary binary spool on the
destination filesystem. Memory retains only an index containing its exact
timestamp fraction, input ordinal, spool offset, captured length, and wire
length. Sorting by `(timestamp, input_ordinal)` makes timestamp ordering stable,
including exact ties, without retaining all frame bytes in memory. Temporary
spool state is removed on success, failure, deadline expiry, and interruption.

The normalized capture has one Ethernet interface and only Enhanced Packet
Blocks. It preserves each captured frame byte, captured length, and wire length.
Each exact timestamp is converted with
`floor(timestamp * 1_000_000)`; therefore an already-microsecond value is stable
and higher precision is truncated toward the past. The canonical output must
retain at least two packets and its last microsecond tick must exceed its first;
their difference divided by `1_000_000` is the observation window.

Raw normalization receives one absolute monotonic deadline. It checks that
deadline before input access, at structural block and decoded-packet boundaries,
on both sides of index sorting, after every encoded packet, and after output
closure. Source read failures and destination or spool failures become
actionable Trafficlab errors; no script, subprocess, alternate packet library,
or payload rewrite participates.

## One program

Trafficlab is one Python package with one CLI. Pipeline stages are ordinary
in-process functions. Only Docker/Compose commands and the captured target are
external processes.

```text
trafficlab preflight EXPERIMENT [--config-only]
trafficlab capture EXPERIMENT
trafficlab fit EXPERIMENT
trafficlab generate EXPERIMENT
trafficlab compare EXPERIMENT
trafficlab run EXPERIMENT
trafficlab import-run EXPERIMENT DUMP_DIRECTORY
```

`EXPERIMENT` is a TOML file path. `DUMP_DIRECTORY` is the supplied capture-pair
directory. The bracketed option is accepted only by `preflight`; every other
command rejects it. `import-run` accepts exactly its two positionals and `-h`.
These commands are the final public surface, and each is owned by its
corresponding subsystem. Every command calls an in-process stage function.
`run` and `import-run` compose the same scientific stages rather than alternate
implementations.

### `preflight`

Local checks, always:

- parse TOML and reject unknown fields;
- validate types, gene bounds, per-family genetic-operator settings, weights,
  argv, and timeout relationships;
- validate local paths, mount sources, output path, and minimum free space;
- produce the same effective configuration as the Python API.

Full checks, unless `--config-only`:

- check Docker Engine and Compose;
- inspect or pull required images;
- validate Docker-facing mount and image requirements;
- run the bounded Compose DNS and network probe.

`--config-only` succeeds without Docker when the local checks pass. It does not
call Docker or create probe resources. Plain `preflight` runs both scopes;
`capture` and `run` always invoke both scopes. Full preflight is read-only except
for Docker's normal image pull/cache when a required image is absent.

### `capture`

Start capture as the network namespace owner, wait for readiness, start the
target argument vector as its service command, then use one event arbiter for
target, capture, user interruption, stage-specific timeout, and total-run
timeout. An unexpected capture exit makes target kill the next orchestration
action. A natural target stop closes the workload window, after which Trafficlab
flushes capture only while it remains alive, validates `capture.json` and
`reference.pcapng`, and tears down the project.

### `fit`

Parse and normalize the reference once, derive `W`, and give every candidate the
same W. Family display order remains lexical. Search priority is one master-seed
permutation of sorted enabled families, derived by one temporary explicit PCG64
generator's `permutation(sorted_family_names_array)` call. It does not consume
the separate PCG64 search stream. The priority controls quota
remainders, initial family order, and exact cross-family ties; checkpoint state
retains it. Build candidates from every enabled model family and evolve them
under common selection trial seeds and `generation.trial` limits. A generation
count `G` means evaluated generation zero followed by reproductions and
evaluations numbered `1` through `G`. Atomically publish `checkpoint.json` after
each whole evaluated generation, then repair or publish the derived
`ga_history.csv` in lexical family order and one overall row.

`genetic.resume = true` starts a new search when `checkpoint.json` is absent and
resumes only from a compatible checkpoint when it is present. With
`genetic.resume = false`, an existing checkpoint is an error. Final validation
uses exactly `run.final_seed` as a fresh simulation seed on the same training
reference; it is not held-out data. It must not be a selection trial seed and
uses the same `generation.trial` limits without reopening selection. A
best-fitness improvement `<= early_stopping_tolerance` stagnates, one `>` that
tolerance resets the count, and `early_stopping_generations = 0` disables early
stopping.

### `generate`

Load `observation_window_seconds` from the winning fitted model and use a
distinct final seed and final reliability limits to simulate the complete
`[0, W]` interval and create `generated.pcapng`. Fitted models and checkpoints
carry the current global scientific artifact schema version for corrected model
semantics, including arrival-epoch initialization. Legacy MMPP semantics fail as
incompatible scientific semantics before reuse, resume, or generation. Trial
generations use different seeds and may use different reliability budgets, but
never a shorter observation window and never serve as final artifacts.

The model completes simulation against the stored floating-point `W` before the
final trace is rendered. Scapy emits microsecond PCAPNG timestamps. Trafficlab
passes exact decimal microseconds to Scapy, truncating higher-precision values
without moving them above `W`; values already on a microsecond remain stable
across repeated write/read cycles. Publication reparses the exact emitted bytes
through Scapy. That reparsed trace is authoritative for counts, comparison,
hashes, and evidence, and every timestamp must remain in `[0, W]`.

### `compare`

Normalize and crop the reference and final generated traces at the shared
boundary, then compare them over the same W with all eight mandatory similarity
methods. Every mandatory similarity method runs, validates its inputs, and
retains diagnostics. A zero weight changes only aggregate contribution; it never
disables execution, validation, diagnostic retention, or failure behavior. Write
the fixed eight-method result shape plus the three final-only records:
`observation_window_seconds`, all fitness component scores, diagnostics,
weights, the weighted aggregate, and exact `postfit_diagnostics` keys
`fano_allan`, `transition_matrix`, and `classical_c2st` to `similarity.json`,
then print a short summary.

Schema 5 fixes the fitness order as `autocorrelation`, `frame_size_ks`,
`iat_ks`, `multiscale_rate`, `cramer_von_mises`, `anderson_darling`,
`jensen_shannon`, and `approximate_mmd`. Genetic trials and checkpoints contain
exactly these methods. Final-only diagnostics belong to the final comparison
boundary, carry no genetic weights, and are not emulated by setting a fitness
method's weight to zero. `evaluate_fitness` remains the sole genetic entry;
`evaluate_postfit` is called only after final trace reconstruction. Schema-5
trial and checkpoint records reject post-fit fields.

### `run`

`run` executes exactly five in-process stages: `preflight -> capture -> fit ->
generate -> compare`. Full preflight runs once. Its `PreparedExperiment` enters
the capture core; capture may validate and reuse an existing pair before Docker
launch. Later stages call their ordinary public functions. The coordinator owns
no Docker cleanup: capture owns its project cleanup, including on failure.

After every stage returns, validate its result immediately before the next stage
or `run_completed` record. Stop at the first failure and preserve every earlier
complete output. If full preflight returns a prepared run, record a later
failure in `run_failed`. If full preflight raises, the coordinator MUST NOT
append `run_failed` or read, write, or assume `run.log`; it propagates the direct
structured error.
There is no new resume switch or manifest. `genetic.resume = true` permits only
a compatible checkpoint resume; all other reuse requires strict validation and
identity comparison.

### `import-run` and imported reference acquisition

`trafficlab import-run EXPERIMENT DUMP_DIRECTORY` invokes one public
coordinator boundary. Success prints the same family, selection fitness,
reference/generated packet counts, aggregate score, and run-directory summary
as `run`, prefixed `import-run:`. A structured error retains its exit status and
corrective action. User interruption returns 130 after owned temporary cleanup
and directs the user to inspect `run.log` before retrying.

The imported-reference workflow composes the existing coordinator as
`config-only preflight -> imported reference -> fit -> generate -> compare`.
It discovers the supplied directory before preflight. Before preflight may
create `run.directory`, it loads the configuration read-only and rejects a run
path that equals, contains, is contained by, or aliases the supplied directory.
The authoritative config-only preflight reloads the configuration and rejects
any concurrent change. Acquisition and every later failure therefore use the
ordinary coordinator failure path once a run has been prepared.

The supplied path must be a real directory rather than a symlink. Its complete
direct inventory contains exactly one regular, non-symlink file with a
case-insensitive `.pcap` or `.pcapng` suffix and one regular, non-symlink file
named exactly `capture.json`; all other files, nested directories, links, and
special entries are errors. Trafficlab resolves and retains the three paths but
never writes below the supplied directory.

Acquisition starts one monotonic absolute deadline derived from
`capture.total_timeout_seconds` before reading the prepared snapshot,
rediscovering the source, or inspecting canonical paths. It identifies both source files, copies them
to an owned same-filesystem temporary directory below the run, re-identifies
both the sources and snapshots, and reparses supplied metadata before raw
normalization. It normalizes only the capture snapshot in process, validates
the normalized snapshot with the metadata snapshot, then delegates canonical
publication to the existing capture-pair publisher. Source, metadata, lineage,
and output reads or hashes check the deadline within each bounded chunk loop;
the publisher does the same while copying each member. Owned temporary paths
are removed on success, failure, deadline expiry, and interruption, including
an interruption during publisher copy or exclusive linking.

Fresh publication appends exactly one authoritative `reference_imported`
record with `reused=false`. It binds the effective configuration,
normalization version, source paths, source content and file identities,
published metadata and reference content identities, packet count, and
canonical output path. A later retry appends `reused=true` without normalization
only when a valid complete pair, the current source/output identities, the
effective configuration, the normalization version, the sole authoritative
publication record, and every earlier reuse record agree exactly. A missing,
malformed, changed, duplicate, or contradictory input preserves every existing
run byte and fails; imported acquisition never invokes live-capture recovery.
Canonical names are recognized with non-following filesystem inspection, so a
dangling link or special entry cannot be mistaken for absence or opened for
hashing. Publication lineage remains bound to the publisher-owned file
identities across record construction and append, and the sole authority is
revalidated after append before success. It creates no Docker resource
and a clean import of the acquisition module does not load Docker adapters or
`subprocess`; execution invokes no subprocess, shell, Wireshark executable, or
repository script.

Capture reuse is exact: both files are absent, so capture starts; one absent or either
corrupt reruns capture; both valid with matching effective configuration and
capture identity are reused without Docker; valid files with a different
identity are rejected rather than overwritten. A successful capture removes
only stable stale `diagnostic-capture.json` and
`diagnostic-reference.pcapng`. Failed capture may retain those diagnostics, but
they are never reusable. A successful run has exactly the nine documented
names, and neither diagnostic identity, temporary file, or quarantine residue.

## Experiment configuration

The prototype has one current TOML shape. It contains:

- target image, argument vector, environment, working directory, and mounts;
- workload, capture-readiness, capture-flush, and total run timeouts;
- run directory, disk-space minimum, master seed, and final generation seed;
- enabled model families and family-specific gene bounds;
- population size, generation count, tournament size, elitism, trial seeds,
  duplicate-mutation attempts, early stopping (including its exact tolerance),
  and checkpoint behavior;
- per-enabled-family crossover probability, mutation probability applied
  independently to each gene, and normalized mutation scale;
- trial and final packet-count and output-size guards plus candidate wall-time
  limits;
- similarity lags, time-bin widths, feature weights, method weights, and maximum
  total direction-bin cell count.

Argument vectors are arrays rather than shell strings. Target argv is applied
directly as the Compose service command and is never evaluated as a shell
string. A mount declares host path, container path, and read-only/read-write
mode. Unknown settings are errors so misspellings do not silently change an
experiment. `W` is derived data, not a TOML setting. Multiscale widths and their
derived direction-bin cell count are validated against `W` after reference
parsing.

Read-only mounts are immutable workload inputs. A regular-file mount is bound by
its exact bytes; a directory mount is bound by a deterministic relative inventory
of directories and regular-file bytes. Links and nonregular entries are rejected.
Writable mounts are workload output/workspace locations: their configured source,
target, and mode remain authoritative, but expected workload writes are not treated
as input-identity changes.

For capture, the configured total-run deadline starts when the Compose project
is created and caps every later wait, parsing or validation step, and
unconditional cleanup. Readiness, workload, and flush timeouts are also enforced
within the remaining total-run budget. This defines the existing timeout; it
adds no setting.

PCAPNG parsing and validation receive the same monotonic deadline. They check it
before work and after every frame, aborting before another frame is accepted.
This cooperative check needs no worker thread or process.

Each enabled family has `crossover_probability`, `mutation_probability`, and
`mutation_scale` in its own family block. Defaults use `p_c = 0.9`,
`p_m = 1 / d_f`, and `sigma = 0.1`, where `d_f` is the fixed chromosome length:

| Family | `d_f` | `crossover_probability` | `mutation_probability` | `mutation_scale` |
|---|---:|---:|---:|---:|
| `poisson_empirical` | 1 | 0.9 | 1.0 | 0.1 |
| `markov_renewal` | 5 | 0.9 | 0.2 | 0.1 |
| `mmpp` | 4 | 0.9 | 0.25 | 0.1 |
| `nhpp` | 1 | 0.9 | 1.0 | 0.1 |
| `acd` | 1 | 0.9 | 1.0 | 0.1 |
| `markov_packet_train` | 1 | 0.9 | 1.0 | 0.1 |
| `packet_hmm` | 1 | 0.9 | 1.0 | 0.1 |

An experiment may override each value independently.
Both probabilities must be finite and in `[0, 1]`. The normalized mutation scale
must be finite and in `(0, 1]`. There is no global operator setting, and an
individual gene cannot override its family's values.
The registered family names are `poisson_empirical`, `markov_renewal`, `mmpp`,
`nhpp`, `acd`, `markov_packet_train`, and `packet_hmm`. Release-default examples
retain the original three families; newer families such as NHPP, ACD, Markov
packet trains, and packet HMMs require explicit enabled tables.
Unknown operator keys, unknown family names, and operator settings for a disabled
family are configuration errors.

Duplicate-mutation attempts are required and must be a nonnegative integer. A
value of zero disables retries after the first repaired child; duplicate
exhaustion is still recorded in that child's diagnostics.

Every gene has finite configured bounds `L < U`. A logarithmic coordinate also
requires `L > 0`. The integer bounds for Markov Renewal's `r` must be integers
with `L < U`, so the inclusive range contains at least two values. Local
preflight and the Python API must resolve and validate the same effective values,
and `experiment.toml` records those resolved values rather than omitted defaults.

### Portable and realized configuration

A portable configuration retains config-relative run and bind-mount source
paths, contains every explicit scientific and workload value, and is suitable
for transfer to another compatible clean environment.

A realized configuration applies defaults and resolves only `run.directory` and
declared bind-mount host sources to absolute paths. It retains every image, argv,
environment, URL, seed, bound, limit, operator, and similarity value without
substitution. Preflight rejects an unavailable or incompatible realization
before scientific artifact publication. Every accepted study run retains the
portable configuration, realized configuration, and their identities.

All eight mandatory similarity method settings are required. Their weights are
finite in `[0, 1]`, normalized under the existing numeric rule, and never
enable or disable method execution.

## Run directory

Standalone JSON documents produced by Trafficlab and its repository tooling use
sorted object keys, two-space indentation, UTF-8 encoding, finite JSON numbers,
and exactly one trailing LF. JSON Lines records remain sorted and compact so one
physical line is one record. Compact encodings used only as internal hash
preimages or subprocess messages are not standalone artifact documents and keep
their established byte contracts.

```text
experiment.toml
reference.pcapng
capture.json
checkpoint.json
ga_history.csv
best_model.json
generated.pcapng
similarity.json
run.log
```

`experiment.toml` is the realized configuration snapshot. Ordinary run
directories still have exactly these nine names. `capture.json` contains exactly:

```json
{
  "interface": "eth0",
  "target_mac": "02:42:ac:11:00:02"
}
```

On successful local preflight, `run.log` is atomically published as UTF-8 JSON
lines. Its deterministic initial records identify the `preflight` stage and the
paths for `effective_config_published` and `run_prepared`; they include no
wall-clock value. Later stages append their detailed diagnostic records there.

The interface value is the literal `eth0`. The target MAC is a normalized,
nonzero unicast six-octet address. Missing or unknown fields and invalid values
are errors. Model and similarity JSON contain the SHA-256 identities of inputs
they directly depend on, including `capture.json` wherever packet direction is
parsed or rendered. Ordinary runs need no production manifest, lineage graph,
launch record, or detached status file.

`best_model.json` and `similarity.json` contain the same finite positive
`observation_window_seconds` derived from the identified reference input. The
winning fitted model stores it so `generate` does not need to reopen the
reference capture. Every similarity method also returns it in diagnostics.

Structured results and checkpoints are written to a temporary sibling, flushed,
validated, and atomically published. Replaceable checkpoints use rename;
immutable stage results use exclusive links so an existing result is never
silently replaced. Each successful publication fsyncs its containing directory.
Capture publishes `capture.json` first, then `reference.pcapng`. A crash may
therefore leave metadata without a published capture; stage reuse requires both
valid files and reruns capture otherwise.
PCAPNG is accepted only after it is closed and successfully parsed. Existing
results are not silently replaced. `genetic.resume = true` may update
`checkpoint.json` only after compatibility validation; `false` rejects an
existing checkpoint. A different effective experiment starts a new run
directory rather than mixing experiments.

`checkpoint.json` stores every enabled family's resolved operator values. Resume
compares them with the effective configuration and rejects a mismatch before
creating another child.

`best_model.json` and `checkpoint.json` use the bumped global scientific
artifact schema version for corrected model semantics. A well-formed older
version is incompatible, not corrupt. Compatibility is checked before
generation, resume, or stage reuse.

### Published study evidence

An accepted evidence bundle is checked at
`examples/validation_study/evidence/<study-id>/`. It retains every report-cited
primary and reproduction strict nine-file run tree, portable configurations,
held-out inputs and results, protocol-used transfer headers and external
observations, prerequisite evidence, an environment record, and a canonical
path/size/SHA-256 inventory. Publication is exclusive and occurs only after the
candidate bundle passes the bounded offline audit. An existing different
accepted bundle is preserved. Hashes without retained bytes do not qualify.

Accepted reports require this retained evidence bundle in addition to the nine
ordinary run artifacts. Scores and winners are descriptive; they do not establish
likelihood, causal mechanism, universal superiority, or unseen-program
generalization.

## Research interfaces

Traffic model families implement:

```text
fit(reference, genes) -> fitted model
generate(fitted model, seed, observation_window, limits) -> canonical trace
serialize(fitted model) -> JSON-compatible value
```

Similarity methods implement:

```text
evaluate(reference, generated, observation_window, settings) -> score, diagnostics
```

Scores lie in `[0, 1]`, with `1` meaning identical under that method. The genetic
algorithm supplies the same W, trial seeds, reliability limits, and fitness
weights to every candidate so model families compete on behavior rather than
privileged evaluation conditions.

Fitted-model and checkpoint interfaces carry the current global scientific
artifact schema version. A well-formed older version is incompatible rather than
corrupt, and compatibility is checked before generation, resume, or stage reuse.

### Stage compatibility

<table>
  <thead>
    <tr><th>Context</th><th>Required equality</th><th>Permitted difference</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>portable transfer</td>
      <td>Every non-path scientific/workload value, image content identity,
      container mount target/mode, and mounted-input content identity</td>
      <td>Checkout, run-directory, and host mount-source absolute paths</td>
    </tr>
    <tr>
      <td>capture reuse</td>
      <td>Exact realized snapshot bytes plus capture identity and both capture files</td>
      <td>None</td>
    </tr>
    <tr>
      <td>fit resume</td>
      <td>Exact reference/capture identities, scientific artifact schema, CPython patch,
      families, genes/bounds/operators, seeds, trial limits, similarity settings</td>
      <td>None</td>
    </tr>
    <tr>
      <td>generate reuse</td>
      <td>Exact best-model/schema identity, final seed/limits, capture identity</td>
      <td>None</td>
    </tr>
    <tr>
      <td>compare reuse</td>
      <td>Exact capture/reference/generated/settings identities</td>
      <td>None</td>
    </tr>
    <tr>
      <td>offline reconstruction</td>
      <td>Exact retained source tree, <code>uv.lock</code>, CPython, scientific artifact schema,
      and artifact identities; Docker/Compose/kernel are recorded but not invoked</td>
      <td>None</td>
    </tr>
  </tbody>
</table>

Each mismatch names the first incompatible field and fails before reuse.

Initialization uses the candidate family's bounds and coordinate mapping.
Reproduction and duplicate retry use that family's effective operator values as
applicable. Different family values are an explicit, reproducible search policy;
Trafficlab does not claim that they give families equal search effort.

## Failure policy

Successful completion appends one JSON-line `run_completed` record to `run.log`
and prints one concise success summary for the whole run. After full preflight,
failure appends one `run_failed` record with the failed stage, primary error,
and preserved completed stages; secondary cleanup and diagnostic details remain
in the same log. A failure prints a structured stage error and no success
summary. Preflight failures print their direct structured error without a
guaranteed coordinator record.

Every expected stage failure has one canonical failure outcome with these fields:

```text
kind
stage
detail
affected_evidence
evidence_state = not_published | diagnostic_only | preserved | possibly_remaining
corrective_action
authority = primary | secondary
status (optional exact external/process status)
```

Its boundary classes are configuration/path; Docker/preflight; external
exit/timeout/interruption/malformed output; missing/changed/foreign/stale or
corrupt artifact; incompatible scientific semantics; metric/sample/numeric
infeasibility; generation guard/deadline; publication; cleanup; and combined failures.
Candidate-invalid diagnostics expose equivalent scientific fields. This outcome
extends existing exceptions and event arbitration without replacing either.

Expected research failures use direct messages: local preflight validation
failure, Docker full-preflight failure, target failure, timeout, malformed
capture, invalid observation window, insufficient samples, invalid candidate, or
output validation failure.
Both preflight failure kinds are direct stage errors. Configuration-only success
is never evidence that Docker is ready for `capture` or `run`. The CLI names the
failed stage and a corrective action, exits nonzero, and preflight owns its
direct diagnostics; prepared-run stages write full detail to `run.log`.

An interactive interruption of `trafficlab capture` exits with status 130 after
the capture stage performs its bounded cleanup.

An invalid candidate receives the documented worst fitness and a diagnostic. A
Docker, filesystem, parser, or evaluator failure aborts the run; infrastructure
failures are never disguised as poor science. Cleanup errors are reported after
the primary error without replacing it.

At each wait boundary, one event arbiter collects all visible events and applies
this fixed priority: user interruption, natural target stop, unexpected capture
stop, stage-specific timeout, then total-run timeout. It processes the selected
event before another action and never replaces an existing primary failure.

Trafficlab kills the complete target container on workload timeout, unexpected
capture failure, or interruption. It sends capture `SIGINT` and waits for bounded
flush only while capture remains alive. If capture already exited, Trafficlab
rejects its output without another signal or flush wait. A natural target exit
with nonzero status remains primary. When Trafficlab causes target kill, timeout,
capture failure, or interruption remains primary and the induced target status
is secondary. After natural target success, flush, validation, or total-run
timeout may be primary. Total-run timeout is primary only without a
higher-priority event or earlier primary failure.

Cleanup keeps the last known project inventory. With zero remaining budget it
launches no Docker command and records cleanup timeout. If a running cleanup
expires, Trafficlab terminates the local Compose CLI, makes no later Docker
query, and reports the inventory as possibly remaining. Cleanup failure is
primary only if the run otherwise succeeded. Every secondary detail is written
to `run.log`. No target process manager or PID protocol exists.

Reliability features are bounded execution, deterministic seeds, output
validation, atomic structured writes, idempotent Docker cleanup, and genetic
checkpoints. The system does not add permission checks, inode/symlink defenses,
custom filesystem syscalls, authorization, protected manifests, or host-network
rollback because it neither manages users nor changes host networking.
