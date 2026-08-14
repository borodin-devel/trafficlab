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

The canonical trace is an ordered sequence of packet timestamp `t_i`, direction
`d_i`, and captured frame length `l_i`. Direction has exactly two values:

```text
d_i = outbound  when Ethernet source MAC equals capture.json target_mac
d_i = inbound   otherwise
```

For a reference with `n >= 2` finite, nondecreasing timestamps, define
`W = t_n - t_1` and require finite `W > 0`. Normalize every reference timestamp
as `t'_i = t_i - t_1`, so the reference occupies the closed interval `[0, W]`.
Packets at both endpoints are included.

Before comparison, require a nonempty generated trace, shift it to its first
packet, and retain only events in `[0, W]`. The same W is passed explicitly to
fitting, generation, genetic evaluation, and every similarity method. Silence
outside the reference's first and last packets is outside MVP scope. These are
ordinary event sequences plus one scalar `W`, not a trace wrapper object.

The rule applies to valid Ethernet frames captured on the target's
non-promiscuous `eth0`. Parsing requires the accompanying `capture.json`; a
missing file, invalid MAC, unsupported link type, or malformed frame is an error.
The research core works on canonical values, not on file handles or Docker
objects. PCAPNG parsing and rendering are boundary functions.

Generated Ethernet frames use `capture.json`'s target MAC. Their peer MAC is
`02:00:00:00:00:01`, unless that equals the target MAC, in which case it is
`02:00:00:00:00:02`. Outbound frames use the target as source; inbound frames use
the target as destination. Rendering any other direction value is an error.

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
```

`EXPERIMENT` is a TOML file path. The bracketed option is accepted only by
`preflight`; every other command rejects it. These commands are the final public
surface, but the roadmap implements each one with its owning subsystem. Every
command calls an in-process stage function. `run` composes those same functions
and does not implement an alternate path.

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
same W. GA family order is the lexical order of enabled family names. Build
candidates from every enabled model family and evolve them under common
selection trial seeds and `generation.trial` limits. A generation count `G`
means evaluated generation zero followed by reproductions and evaluations
numbered `1` through `G`. Atomically publish `checkpoint.json` after each whole
evaluated generation, then repair or publish the derived `ga_history.csv` in
lexical family order and one overall row.

`genetic.resume = true` starts a new search when `checkpoint.json` is absent and
resumes only from a compatible checkpoint when it is present. With
`genetic.resume = false`, an existing checkpoint is an error. Final validation
uses exactly `run.final_seed`, which must not be a selection trial seed, and the
same `generation.trial` limits; it never reopens selection. A best-fitness
improvement `<= early_stopping_tolerance` stagnates, one `>` that tolerance
resets the count, and `early_stopping_generations = 0` disables early stopping.

### `generate`

Load `observation_window_seconds` from the winning fitted model and use a
distinct final seed and final reliability limits to simulate the complete
`[0, W]` interval and create `generated.pcapng`. Trial generations use different
seeds and may use different reliability budgets, but never a shorter observation
window and never serve as final artifacts.

The model completes simulation against the stored floating-point `W` before the
final trace is rendered. PCAPNG normally uses nearest-nanosecond timestamps; if
that representation would move a generated event above `W`, Trafficlab uses the
largest whole-nanosecond tick not above `W` for that event. This boundary-only
quantization retains every generated event, preserves nondecreasing order, and
does not shorten or repeat the stochastic simulation. Publication reparses the
bytes and explicitly requires every rendered timestamp to remain in `[0, W]`.

### `compare`

Normalize and crop the reference and final generated traces at the shared
boundary, then compare them over the same W with every enabled similarity method.
Write `observation_window_seconds`, all component scores, diagnostics, weights,
and the weighted aggregate to `similarity.json` and print a short summary.

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

An experiment may override each value independently.
Both probabilities must be finite and in `[0, 1]`. The normalized mutation scale
must be finite and in `(0, 1]`. There is no global operator setting, and an
individual gene cannot override its family's values.
The only family names are `poisson_empirical`, `markov_renewal`, and `mmpp`.
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

## Run directory

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

`experiment.toml` is the exact effective configuration snapshot. `capture.json`
contains exactly:

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
parsed or rendered. This is enough to reproduce a prototype run; there is no
separate lineage graph, launch record, manifest, or detached status file.

`best_model.json` and `similarity.json` contain the same finite positive
`observation_window_seconds` derived from the identified reference input. The
winning fitted model stores it so `generate` does not need to reopen the
reference capture. Every similarity method also returns it in diagnostics.

Structured results and checkpoints are written to a temporary sibling, flushed,
validated, and renamed into place. Capture writes and validates temporary
metadata and PCAPNG, renames `capture.json` first, then publishes
`reference.pcapng`. A crash may therefore leave metadata without a published
capture; stage reuse requires both valid files and reruns capture otherwise.
PCAPNG is accepted only after it is closed and successfully parsed. Existing
results are not silently replaced. `genetic.resume = true` may update
`checkpoint.json` only after compatibility validation; `false` rejects an
existing checkpoint. A different effective experiment starts a new run
directory rather than mixing experiments.

`checkpoint.json` stores every enabled family's resolved operator values. Resume
compares them with the effective configuration and rejects a mismatch before
creating another child.

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
