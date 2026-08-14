# Trafficlab

Trafficlab is a single-process research prototype for capturing the network
traffic of a containerized program, fitting competing classical stochastic
models, generating a synthetic PCAPNG trace, and measuring how closely that
trace resembles the reference capture.

The project emphasizes reproducibility and interpretable results: strict
configuration, deterministic seeds, bounded execution, atomic artifacts,
resumable genetic fitting, direction-aware metrics, checked fixtures, and
project-scoped Docker cleanup. It is an MVP for one researcher, not a hosted
service or a general network-analysis platform.

## Purpose

Trafficlab answers a practical modelling question:

> Given one bounded Ethernet capture from a containerized workload, which of
> the implemented classical traffic models best reproduces its timing, frame
> sizes, directionality, serial dependence, and multiscale rate behavior?

An experiment follows this data flow:

```text
experiment.toml
  -> preflight
  -> capture
  -> fit
  -> generate
  -> compare
```

The `trafficlab run` command executes the complete flow in one Python process.
Each stage is also available separately so a researcher can inspect artifacts,
resume a compatible fit, or regenerate and compare without repeating unrelated
work.

The implemented MVP includes:

- non-promiscuous Ethernet capture from a target container's `eth0`;
- strict PCAPNG parsing and deterministic PCAPNG generation;
- Poisson empirical, Markov Renewal, and two-state MMPP traffic models;
- heterogeneous genetic search with deterministic population allocation,
  checkpoints, stable selection, crossover, mutation, and final validation;
- frame-size KS, inter-arrival-time KS, autocorrelation, and direction-aware
  multiscale-rate similarity;
- exact input hashes, canonical JSON, JSONL run logs, atomic publication, and
  bounded cleanup;
- unit, in-process integration, Docker, Internet, fixture, and real-study
  evidence.

The authoritative system overview is [System Design](architecture/SYSTEM.md).

## Requirements

### Supported environment

- **Operating system:** Linux, or WSL2 with systemd enabled. Capture and the
  documented process-tree test guard depend on Linux containers, cgroup v2,
  and a running systemd user manager.
- **Python:** Trafficlab supports CPython `>=3.12,<3.13`. Development,
  deterministic fixture generation, and strict checkpoint reproduction use
  the exact `3.12.3` version pinned in [.python-version](.python-version).
- **Python tooling:** [uv](https://docs.astral.sh/uv/) is the only supported
  dependency, environment, lock, and command interface.
- **Containers:** Docker Engine and Docker Compose v2 are required for full
  preflight, capture, `run`, Docker integration tests, and Internet validation.
  The current user must be able to run Docker directly; Trafficlab does not use
  `sudo` or manage Docker permissions.
- **Git:** required for normal development and for reproducing the checked
  study lineage.
- **Network access:** required to pull configured images and for workloads that
  access external endpoints. Ordinary unit and in-process integration tests do
  not use the public Internet.

Config-only preflight, model loading, fixture checks, and offline analytical
stages do not require a Docker daemon when all of their input artifacts already
exist.

### Resource guidance

The experiment configuration owns its disk-space minimum, timeouts, packet
limits, and output-byte limits. Plan enough storage for PCAPNG files,
checkpoints, histories, and generated runs.

The documented broad test guard uses an 8 GiB hard process-tree memory limit
and up to 1 GiB of swap. Docker and Internet test launchers use a 3 GiB local
process-tree limit plus the resources consumed by Docker itself. Smaller
focused tests use lower limits. See [Development Workflow](architecture/DEVELOPMENT.md)
for exact commands and containment behavior.

### Check the host

```bash
uv --version
docker info
docker compose version
systemctl --user is-system-running
```

`systemctl --user is-system-running` may report `running` or `degraded`, but the
user manager must accept transient scopes. The process-guard integration test
proves the required behavior on the current host.

## Installation

From the repository root:

```bash
uv python install 3.12.3
uv sync --locked --all-groups
uv lock --check
uv run --locked python --version
uv run --locked trafficlab --version
```

The Python command must report `Python 3.12.3` when regenerating deterministic
fixtures or resuming the checked genetic checkpoint. `uv sync --locked` uses
the committed [uv.lock](uv.lock) without silently upgrading dependencies.

Build the production capture image used by the example configuration:

```bash
docker build --tag trafficlab-capture:local docker/capture
```

The image definition is checked in at
[docker/capture/Dockerfile](docker/capture/Dockerfile). The target image is an
experiment input; full preflight inspects it locally and pulls it when needed.

## Configuration

Trafficlab uses one strict TOML experiment shape. Unknown keys, invalid bounds,
non-finite numbers, unsafe stage relationships, invalid mounts, and disabled or
unknown model settings are rejected rather than ignored.

Start with the checked example while preserving its relative paths:

```bash
cp examples/configs/minimal.toml examples/configs/local.toml
```

Edit `examples/configs/local.toml` before running it:

1. Set a new `run.directory` if `runs/minimal` already belongs to another
   experiment.
2. Replace both `example.invalid` URLs. The checked value is intentionally not
   a runnable Internet target.
3. Set `target.image` and `target.argv` to the container and direct argument
   vector being studied.
4. Review target environment, working directory, and mounts. Relative host
   mount sources resolve from the configuration file's directory.
5. Confirm the capture image tag matches the image built above.
6. Choose stage and total timeouts that cover the real workload and cleanup.
7. Review model bounds, genetic search size, trial/final reliability guards,
   and similarity settings.

The complete example is
[examples/configs/minimal.toml](examples/configs/minimal.toml). Configuration
semantics and invariants are defined in
[Experiment configuration](architecture/SYSTEM.md#experiment-configuration).

Target arguments are arrays and are passed directly as the Compose service
command. They are not evaluated by a shell.

## Quick start

The safest first command validates configuration, local paths, output capacity,
and disk space without contacting Docker:

```bash
uv run --locked trafficlab preflight \
  examples/configs/local.toml --config-only
```

Build the capture image, confirm Docker access, then run full preflight:

```bash
docker build --tag trafficlab-capture:local docker/capture
uv run --locked trafficlab preflight examples/configs/local.toml
```

Full preflight verifies Docker Engine, Compose v2, images, mounts, DNS,
capture readiness, and the configured network probe. Its temporary Compose
project is removed before the command returns.

Run the complete experiment:

```bash
uv run --locked trafficlab run examples/configs/local.toml
```

A successful run prints the winning family and fitness, reference and generated
packet counts, aggregate similarity score, and run directory. Detailed stage
events and failures are recorded in `run.log`.

Expected research failures return concise structured errors. Inspect `run.log`
and the preserved completed artifacts before correcting the experiment or
retrying. User interruption returns status `130` after bounded target stop,
capture flush where possible, and cleanup.

## Workflow stages

All commands take the experiment TOML path. The stages share the same validated
configuration and run directory.

### [Preflight](architecture/SYSTEM.md#preflight)

```bash
uv run --locked trafficlab preflight EXPERIMENT --config-only
uv run --locked trafficlab preflight EXPERIMENT
```

Config-only mode prepares the run locally without importing or contacting the
Docker boundary. Full mode adds image, Compose, mount, DNS, HTTP, interface, and
capture-tool checks. See also [Docker preflight](architecture/CAPTURE.md#preflight).

### [Capture](architecture/SYSTEM.md#capture)

```bash
uv run --locked trafficlab capture EXPERIMENT
```

Capture starts the capture service first, waits for `eth0` readiness, launches
the target directly in the shared network namespace, and closes the workload
window at target termination. On success it publishes `capture.json` followed
by `reference.pcapng`. Docker lifecycle and failure precedence are detailed in
[Capture lifecycle](architecture/CAPTURE.md#capture-lifecycle).

### [Fit](architecture/SYSTEM.md#fit)

```bash
uv run --locked trafficlab fit EXPERIMENT
```

Fit loads the validated reference pair, evaluates candidates from every enabled
family with common trial seeds and limits, checkpoints whole generations, and
publishes the independently validated winning `best_model.json`.
`genetic.resume = true` resumes only a compatible `checkpoint.json`.

### [Generate](architecture/SYSTEM.md#generate)

```bash
uv run --locked trafficlab generate EXPERIMENT
```

Generate loads the winning fitted model, uses the configured final seed and
final reliability limits, simulates the complete stored observation window,
and publishes a validated `generated.pcapng`.

### [Compare](architecture/SYSTEM.md#compare)

```bash
uv run --locked trafficlab compare EXPERIMENT
```

Compare aligns the reference and generated traces over the same observation
window, evaluates every configured similarity method, retains diagnostics and
weights, and publishes `similarity.json`.

### [Run](architecture/SYSTEM.md#run)

```bash
uv run --locked trafficlab run EXPERIMENT
```

Run orchestrates `preflight -> capture -> fit -> generate -> compare` in
process. It validates each stage before starting the next, stops at the first
failure, and preserves every earlier complete artifact. Capture remains the
only owner of Docker cleanup.

## Run artifacts

A complete run directory contains exactly these nine stable files:

| Artifact | Purpose |
|---|---|
| `experiment.toml` | Exact resolved configuration snapshot. |
| `capture.json` | Strict `eth0` and target-MAC metadata. |
| `reference.pcapng` | Validated reference Ethernet capture. |
| `checkpoint.json` | Complete resumable genetic-search state. |
| `ga_history.csv` | Derived generation and family history. |
| `best_model.json` | Canonical winning fitted model and input lineage. |
| `generated.pcapng` | Final validated synthetic trace. |
| `similarity.json` | Component scores, diagnostics, weights, aggregate, and hashes. |
| `run.log` | UTF-8 JSON Lines stage, lifecycle, diagnostic, and failure records. |

Artifacts are written through temporary siblings, flushed, validated, and
published without silently replacing a different result. Exact validated stage
outputs may be reused; corrupt, incomplete, mismatched, or race-replaced
artifacts are rejected or safely recovered according to their owning stage.

Failed capture may retain `diagnostic-capture.json` and
`diagnostic-reference.pcapng`. They are diagnostic only and never reusable as a
reference pair. A later successful run removes only those stable diagnostic
identities and leaves the exact nine-file result.

See [Run directory](architecture/SYSTEM.md#run-directory) for the canonical
artifact contract and lineage rules.

## Traffic models

Traffic models share one typed fit/generate/serialize contract and compete over
the same observation window, trial seeds, reliability limits, and similarity
weights. The registry order does not privilege a family during fitness
evaluation.

- [Poisson empirical](architecture/traffic_models/poisson_empirical.md) fits a
  global arrival rate, applies a rate-scale gene, samples exponential timing,
  and resamples joint empirical direction/frame-length marks.
- [Markov Renewal](architecture/traffic_models/markov_renewal.md) builds
  quantile-based mark states, fits smoothed conditional transitions and timing,
  and retains the documented global-IAT fallback.
- [Two-state MMPP](architecture/traffic_models/mmpp.md) repairs and fits a
  two-state Markov-modulated Poisson process, initializes from its stationary
  distribution, and simulates CTMC switches and arrivals.

The shared interfaces, reliability guards, chromosome meanings, and registry
rules are indexed in [Traffic Models](architecture/traffic_models/README.md).

## Similarity methods

Every method returns a score in `[0, 1]`, where `1` means identical under that
method. The configured method weights form a normalized weighted aggregate;
method-specific diagnostics remain available in `similarity.json`.

- [Frame-size KS](architecture/similarity_methods/frame_size_ks.md) compares
  merged empirical distributions of Ethernet frame lengths.
- [Inter-arrival-time KS](architecture/similarity_methods/iat_ks.md) compares
  merged empirical distributions of consecutive packet gaps.
- [Autocorrelation](architecture/similarity_methods/autocorrelation.md)
  compares selected timing and frame-size serial-dependence features.
- [Multiscale rate](architecture/similarity_methods/multiscale_rate.md)
  compares direction-separated packet and byte volumes across configured time
  scales.

Normalization, common preconditions, weighting, and diagnostic conventions are
defined in [Similarity Methods](architecture/similarity_methods/README.md).

## Genetic models

Trafficlab implements one classical
[basic generational genetic search](architecture/genetic_models/basic_generational.md).
It provides deterministic family quotas, common evaluation seeds, stable
tournament selection, global elites and family champions, family-aware
crossover, coordinate-aware Gaussian mutation, duplicate handling, early
stopping, strict checkpoints, and final held-out validation.

The genetic architecture index is
[Genetic Models](architecture/genetic_models/README.md).

## Testing

All pytest process trees must run through `scripts/run_bounded.sh`. The guard
uses a uniquely owned transient systemd user scope, enforces memory, swap, and
wall limits, terminates descendants on timeout or OOM, verifies the scope is
inactive, and preserves the guarded command's exit status.

Format, lint, and type checks:

```bash
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
```

Fast unit tests:

```bash
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not integration and not docker and not internet"
```

Branch-aware unit and in-process integration coverage:

```bash
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -n 4 --dist worksteal --cov=trafficlab \
  --cov-branch --cov-report=term-missing \
  -m "not docker and not internet"
```

Docker integration tests run serially:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m docker
```

The public-Internet smoke is opt-in and also serial. This endpoint was verified
during final MVP validation; replace it if it is no longer available:

```bash
export TRAFFICLAB_INTERNET_URL="https://cachefly.cachefly.net/10mb.test"
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -vv -n 0 -m internet \
  --internet-url "$TRAFFICLAB_INTERNET_URL"
```

Deterministic fixture checks:

```bash
uv run --locked python scripts/generate_phase2_fixtures.py --check
uv run --locked python scripts/generate_model_fixtures.py --check
uv run --locked python scripts/generate_fit_fixtures.py --check
```

The complete marker policy, named behavioral evidence, coverage rules, Docker
tracking, and Internet-test contract are in
[Testing Strategy](architecture/TESTING.md).

## Validation Study

The Validation Study used three real-Internet workload shapes, three fresh
primary repeats each, and one fresh saved-run reproduction. The checked
publication contains canonical prerequisite/config/result records but does not
track raw Internet PCAPNG files.

- [Study instructions and retained-evidence policy](examples/validation_study/README.md)
- [Study results and interpretation](examples/validation_study/REPORT.md)

The study found Markov Renewal strongest across the measured workloads while
also identifying repeatable local directional-volume limitations. The report
records the protocol amendment, failed attempts, natural variation, component
scores, trace diagnostics, reproducibility evidence, and the scoped next
research question.

## Limitations

Trafficlab deliberately remains a small classical-model research prototype. It
does not provide:

- traffic replay or packet injection;
- payload or application-protocol modelling;
- host-wide, promiscuous, loopback, or unrelated-container capture;
- distributed execution, worker services, queues, or a database;
- a multi-user API, web application, authentication, or security subsystem;
- neural, diffusion, optimal-transport, or wavelet models;
- a long-term public compatibility guarantee.

Docker daemon access and target-image trust are installation and experiment
concerns, not a Trafficlab-owned privilege or security framework. Raw captures
may contain sensitive traffic; the Validation Study publication intentionally keeps them
out of Git.

For the authoritative scope boundary, see
[Architecture scope boundaries](architecture/README.md#scope-boundaries).

## Project documentation

- [Architecture overview](architecture/README.md)
- [System design and CLI stages](architecture/SYSTEM.md)
- [Docker capture environment](architecture/CAPTURE.md)
- [Development workflow](architecture/DEVELOPMENT.md)
- [Testing strategy](architecture/TESTING.md)
- [MVP Roadmap and completion evidence](architecture/ROADMAP.md)
- [Research prototype fitness criteria](architecture/RESEARCH_FITNESS_CRITERIA.md)
- [Traffic-model index](architecture/traffic_models/README.md)
- [Similarity-method index](architecture/similarity_methods/README.md)
- [Genetic-model index](architecture/genetic_models/README.md)
- [Validation Study report](examples/validation_study/REPORT.md)

The files under `docs/superpowers/` preserve implementation designs and plans.
They are development history; current behavior is owned by `architecture/`, the
typed implementation, tests, examples, and checked result artifacts.
