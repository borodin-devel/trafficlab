# Repository scripts

These scripts generate deterministic fixtures and evidence, verify checked
artifacts, contain test process trees, and run or audit the Validation Study.
They are repository tooling; the installed user interface is the `trafficlab`
command declared in `pyproject.toml`.

Run Python tools from the repository root through the locked uv environment:

```bash
uv run --locked python scripts/NAME.py --help
```

## Deterministic generators and checks

| Script | Purpose |
| --- | --- |
| `generate_similarity_fixtures.py` | Rebuilds or byte-checks the canonical capture, reference, fitted model, generated trace, and similarity result under `examples/data/`. |
| `generate_model_fixtures.py` | Rebuilds or byte-checks the model-generation pair under `examples/data/models/`. |
| `generate_fit_fixtures.py` | Runs the Docker-free heterogeneous fit and rebuilds or byte-checks its checkpoint-compatible fixture tree. |
| `generate_artifact_schemas.py` | Generates or checks the 13 public scientific-artifact v4 JSON Schemas. `--output` selects another directory. |
| `generate_validation_study_fixture.py` | Generates or checks deterministic Validation Study fixture evidence; optional source commit/tree arguments bind the fixture. |
| `check_fixture_layout.py` | Checks example and test fixture manifests plus repository layout; `--write-manifest` rewrites both manifests and `--check-manifest` checks only manifests. |
| `run_scientific_stack_probes.py` | Generates or checks MMPP and pymoo probe evidence; `--probe` selects `all`, `mmpp`, or `pymoo`. |
| `benchmark_scientific_stack.py` | Records or checks scalar-versus-NumPy scientific-kernel benchmark evidence. |
| `benchmark_scapy_production.py` | Records or checks non-gating production Scapy encode/read benchmark evidence. |
| `measure_scientific_stack_reduction.py` | Recomputes or checks source-reduction inventories from Git revisions. |
| `check_scientific_stack_example.py` | Strictly verifies a retained real run, or records a new explicitly supplied run directory. |
| `prepare_traffic_dumps.py` | Creates ordered, TrafficLab-validated PCAPNG copies of external PCAP/PCAPNG dumps without modifying their sources. |

Most generators write their owned checked paths when called without `--check`.
Use `--check` in normal verification so a stale artifact fails without mutating
the checkout. The exact release list is authoritative in
[`architecture/DEVELOPMENT.md`](../architecture/DEVELOPMENT.md#release-gate).

## External traffic-dump preparation

Prepare every PCAP and PCAPNG below `dumps/` recursively:

```bash
uv run --locked python scripts/prepare_traffic_dumps.py
```

Paths may also name individual captures or other directories, and `--prefix`
changes the default `trafficlab-ready-` output prefix:

```bash
uv run --locked python scripts/prepare_traffic_dumps.py \
  dumps/legacy.pcap dumps/another-directory \
  --prefix trafficlab-ready-
```

The script requires Wireshark's `editcap` and `reordercap` programs. It writes a
new sibling `<prefix><source-stem>.pcapng`, never changes the source, never
overwrites an existing destination, and excludes already-prefixed captures from
recursive discovery. Each candidate is converted to PCAPNG, timestamp-ordered,
and accepted only after TrafficLab's production parser confirms one Ethernet
interface, Enhanced Packet Blocks, at least two packets, and a positive
observation window. A prepared capture still needs an authoritative target MAC,
canonical `capture.json`, and external-capture lineage before it can serve as a
conformant experiment reference.

To publish a complete organized pair per source, pass `--organized-root`. The
script stages `<organized-root>/<source-stem>/` beside the destination root and
atomically publishes it only after the processed PCAPNG and canonical
`capture.json` validate together:

```bash
uv run --locked python scripts/prepare_traffic_dumps.py \
  dumps \
  --organized-root prepared-dumps
```

Each published directory contains exactly
`trafficlab-ready-<source-stem>.pcapng` and `capture.json`. The metadata uses
`interface="eth0"` plus a deterministic MAC inferred only from Ethernet source
and destination headers. That metadata is a structural compatibility proxy for
TrafficLab's parser boundary, not authoritative original-interface provenance or
scientific proof that the inferred inbound and outbound labels match the source
environment. Independently confirm the published direction labels before using
them as scientific evidence.

## Bounded command wrapper

`run_bounded.sh` runs a command inside a unique user-systemd scope and applies
memory, swap, wall-time, and termination-grace limits to the complete descendant
process tree:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  uv run --locked pytest -q -n 4 --dist worksteal \
  -m "not docker and not internet"
```

| Option | Description |
| --- | --- |
| `--memory-high` | Reclaim threshold in bytes or with a `K`, `M`, or `G` suffix. Must be below `--memory-max`. |
| `--memory-max` | Hard memory limit for the scope. |
| `--swap-max` | Hard swap limit for the scope. |
| `--wall-time` | Positive GNU `timeout` duration using `ms`, `s`, or `m`. |
| `--kill-after` | Grace period before a timed-out command receives `SIGKILL`. |
| `--unit` | Optional explicit systemd unit name used by tests; ordinary calls use a randomized project-scoped name. |
| `--` | Ends wrapper options; every remaining argument is executed directly without a shell. |

The wrapper preserves the child status, including timeout status 124. It returns
125 when containment setup, ownership, or final cleanup cannot be proved.

## Validation Study tools

| Entry point | Purpose |
| --- | --- |
| `run_validation_study.py prerequisites` | Cold-builds and records the required Docker and Internet prerequisite evidence. |
| `run_validation_study.py study` | Runs the older combined study workflow retained for compatibility with existing evidence tooling. |
| `run_validation_study.py collect` | Collects the frozen serial training, fresh-simulation, and held-out protocol into a candidate bundle. |
| `run_validation_study.py publish` | Audits and exclusively publishes a complete candidate to an unoccupied accepted destination. |
| `audit_validation_study.py BUNDLE` | Performs an offline, read-only audit of a retained or candidate bundle. |

The `validation_study/` Python package implements those entry points. Its
`prerequisites/`, `candidate/`, `results/`, `rotation/`, and `audit/` packages
separate collection, serialization, publication, recovery, and independent
verification ownership. They are internal modules, not additional CLIs.

External study commands require the coordination and clean-source rules in
[`examples/validation_study/README.md`](../examples/validation_study/README.md).
Never substitute an unapproved endpoint, reuse a failed study ID, selectively
rerun a failed phase, or overwrite an accepted evidence destination.
