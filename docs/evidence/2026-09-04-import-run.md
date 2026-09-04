# Imported-reference run validation evidence

## Scope and interpretation

This document records integrated development acceptance of `trafficlab import-run`
from the checked classic-PCAP and noncanonical-PCAPNG fixtures. The source for the
final offline gates and both installed-command runs was commit
`70c76beb0cb53492ad07147b3b109e5b5a3f2d0c`, tree
`25fdbfb043c2260b071eb651ee1cba25efdec131`. The locked environment used CPython
3.12.3 and `uv.lock` SHA-256
`b66ad35ff61e66b02cfd380eb0396916a7271b07421183b9253f489a2e1498f9`.

These are deterministic smoke runs against four-packet fixtures. Their selected
families, selection fitnesses, and final similarity scores are observations about
these fixtures and declared seeds only. They are not causal results, held-out
evidence, or claims that one model family is generally superior.

## Repository gates

The final current-head static commands were:

```bash
uv sync --locked --all-groups --all-extras
uv run --all-extras ruff format --check .
uv run --all-extras ruff check .
uv run --all-extras pyright
```

They respectively reported 56 resolved/55 checked packages, 581 formatted files,
no Ruff violations, and `0 errors, 0 warnings, 0 informations`.

The final current-head Ordinary command was copied from
`architecture/DEVELOPMENT.md`:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 10s -- \
  QT_QPA_PLATFORM=offscreen uv run --all-extras pytest -q -n 4 --dist worksteal \
  -m "not docker and not internet" --durations=50
```

Result: `4904 passed in 226.82s`.

The final current-head Coverage command was:

```bash
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  QT_QPA_PLATFORM=offscreen uv run --all-extras pytest -q -n 4 --dist worksteal \
  -m "not docker and not internet" \
  --cov=trafficlab --cov=trafficlab_dashboard --cov-branch --cov-report=term-missing \
  --cov-fail-under=90 --durations=50
```

Result: `4904 passed in 629.64s`; 16,823 statements, 5,018 branches,
627 missed statements, 449 partial branches, displayed total 95%, exact combined
coverage 94.88%. The imported owners
`trafficlab.common.scapy_io.raw`, `trafficlab.pipeline.imported`, and
`trafficlab.pipeline.imported_io` each reported 100%.

The deterministic Release checks were run separately in this order:

```bash
uv run --all-extras python scripts/generate_similarity_fixtures.py --check
uv run --all-extras python scripts/generate_model_fixtures.py --check
uv run --all-extras python scripts/generate_fit_fixtures.py --check
uv run --all-extras python scripts/generate_validation_study_fixture.py --check
uv run --all-extras python scripts/generate_artifact_schemas.py --check
uv run --all-extras python scripts/measure_scientific_stack_reduction.py --check
uv run --all-extras python scripts/benchmark_scientific_stack.py --check
uv run --all-extras python scripts/benchmark_scapy_production.py --check
uv run --all-extras python scripts/check_scientific_stack_example.py --check
uv run --all-extras python scripts/run_scientific_stack_probes.py --probe all --check
uv run --locked python scripts/check_fixture_layout.py --check
```

All passed. The schema generator verified 13 public schema roots. The immutable
scientific-stack example was checked against source
`292202368fa2ee7b4f2cccc5a68971feff243a3b`. The combined probe command verified
`mmpp_cases.json`, historical `pymoo_cases.json`, and current
`pymoo_schema5_cases.json`. The import fixture manifest SHA-256 is
`4faef4bfd9f3bf05ecd767a1d2cc4010ca0f4fc072f5b81826fc0405a37b3e8d`,
and its independent raw-packet facts file SHA-256 is
`e2a3dff2ab5215c7bd59319993cd8178728140bf3a6d5430692245ea698ab548`.

The retained study was audited under its source-binding rule. The repository was
cloned with no local object borrowing or hardlinks, detached at the bundle's
recorded source, and populated with regular evidence copies:

```bash
git clone --no-local --no-hardlinks --no-checkout . \
  /tmp/trafficlab-import-run-audit.un6RJk8l/repository
git -C /tmp/trafficlab-import-run-audit.un6RJk8l/repository \
  checkout --detach 7ba2764dd5810cd061fc42bcbc46dfcfda2b6103
mkdir -p /tmp/trafficlab-import-run-audit.un6RJk8l/repository/examples/validation_study/evidence/2026-08-20-scapy-production-r3
cp -a --reflink=never \
  examples/validation_study/evidence/2026-08-20-scapy-production-r3/. \
  /tmp/trafficlab-import-run-audit.un6RJk8l/repository/examples/validation_study/evidence/2026-08-20-scapy-production-r3/
scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --all-extras --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/2026-08-20-scapy-production-r3/ --repository .
```

The last command ran from the detached clone and accepted 231 retained files.
The clone had no object alternates, evidence symlinks, or evidence files with link
count above one, and was removed after the audit.

The one External gate used Docker Engine 29.7.2, Compose 5.5.0, and the repository's
credential-free Wikimedia HTTPS object:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 20m --kill-after 10s -- \
  uv run --all-extras pytest -vv -n 0 -m "docker or internet" \
  --internet-url https://upload.wikimedia.org/wikipedia/commons/5/5b/SPACE_ELECTRIC_ROCKET_TEST%2C_SERT_II_IN_TANK_5_%28GRC-1968-C-03031%29.jpg
```

Result: `19 passed, 4903 deselected in 449.76s`. It included controlled Docker
captures, the complete live-capture pipeline, failure cleanup, and the real HTTPS
DNS/TLS/bidirectional-traffic case. It ran at `62ce3b9`; the only later production
input change was the offline `small.toml` C2ST width correction used by the two
import runs, accompanied by one unit regression. The External command was not
repeated because the acceptance brief required it once.

## Installed-command setup and observation method

Both temporary configurations were derived from the realized checked profile
`examples/required_candidates/small.toml` (SHA-256
`2a5a2a2f522a06c79c944d43dce3c06d30d028a396db4aac0d6291f06ee687fb`)
through `load_configuration_pair`. Only `run.directory` was model-copied, the result
was written with `render_effective_config`, reloaded, and compared after restoring
the base run path. The PCAP and PCAPNG configurations were also compared after
substituting one another's run setting; every scientific setting was equal.

The reproducible creation pattern was run once with
`TRAFFICLAB_IMPORT_ROOT=/tmp/trafficlab-import-run-pcap` and once with
`TRAFFICLAB_IMPORT_ROOT=/tmp/trafficlab-import-run-pcapng`:

```bash
export TRAFFICLAB_IMPORT_ROOT=/tmp/trafficlab-import-run-pcap
mkdir "$TRAFFICLAB_IMPORT_ROOT"
uv run --locked python -c '
import os
from pathlib import Path
from trafficlab.common.config_io import load_configuration_pair, render_effective_config
source = Path("examples/required_candidates/small.toml")
root = Path(os.environ["TRAFFICLAB_IMPORT_ROOT"])
destination = root / "experiment.toml"
run = (root / "run").resolve()
base = load_configuration_pair(source).realized
configured = base.model_copy(update={"run": base.run.model_copy(update={"directory": run})})
destination.write_bytes(render_effective_config(configured))
checked = load_configuration_pair(destination).realized
assert checked == configured
assert checked.model_copy(update={"run": checked.run.model_copy(update={"directory": base.run.directory})}) == base
'
```

Each installed process ran under the documented Focused bounds and GNU `time`:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  /usr/bin/time -v -o "$TRAFFICLAB_IMPORT_ROOT/resource.txt" \
  uv run --locked trafficlab import-run "$TRAFFICLAB_IMPORT_ROOT/experiment.toml" "$SOURCE"
```

While each guard was active, an external 10 ms `/proc` polling loop selected the
process whose argument vector contained the installed `.venv/bin/trafficlab`
followed by `import-run`, recorded every target PID, and read
`/proc/PID/task/PID/children`. This observer was outside the guarded command.
The import process itself was observed in both runs; no direct child PID was ever
observed. Separately, complete Docker container/network/volume inventories were
sorted and compared before and after each run. Thus the observations do not infer
absence from a too-fast or missing target process.

## Classic PCAP run

The exact installed command was:

```bash
uv run --locked trafficlab import-run \
  /tmp/trafficlab-import-run-pcap/experiment.toml \
  tests/fixtures/data/import_run/classic-pcap-source
```

The rendered config was 3,879 bytes with SHA-256
`c5171ac797ff9043b64e7be0190b945a248428f2dce97d7c99d4683e36fde1f5`.
The source identities before and after were byte-for-byte equal:

| source | bytes | mode | links | SHA-256 |
| --- | ---: | ---: | ---: | --- |
| `capture.json` | 63 | 644 | 1 | `2573517a5b00a2cdde835ea2b16e6d537f8dbd90c9de843aa55b70f1a8944315` |
| `source.pcap` | 155 | 644 | 1 | `bbe915ef29375ca541bf5c057e213e8dd37a60581548451cb370128eae786d83` |

Status sidecar: start `2026-09-04T16:36:07.563048659Z`, end
`2026-09-04T16:36:08.869651775Z`, exit 0. GNU `time` reported 1.24 seconds,
8.43 user seconds, 0.31 system seconds, maximum RSS 138,136 KiB, zero swaps,
and exit 0. The observer recorded target PID 2715527 and no direct child.
The command printed:

```text
import-run: family=packet_hmm fitness=0.929162 reference_packets=4 generated_packets=6 aggregate_score=0.920875 output=/tmp/trafficlab-import-run-pcap/run
```

The status, resource, and process-observation sidecar SHA-256 values were,
respectively,
`852a3180a3a8311132ea8e0383d310a925d770827ba86508f0a9b9c3e9fce5bf`,
`66f3bab997cfc44a5658a02266811b8ba98568c310d51dcc72cc53baaab8228d`, and
`eec1f3c36451f6f72d76969f7d3240411a1ed8b418a7628210a68eb35f2f1e41`.
The stderr sidecar was empty. Pre/post Docker inventories both had SHA-256
`6ed8cd4cbab0d5e87057356b7e82079d1dfb816ab7e3437a3d8711763d2819df`
and contained only the three default networks.

Independent `struct` parsing verified one Ethernet interface, Enhanced Packet
Blocks only, stable equal-time ordering, and these exact canonical facts:

| input ordinal | canonical microseconds | captured | wire | frame hex |
| ---: | ---: | ---: | ---: | --- |
| 1 | 10,250,000 | 14 | 60 | `ffffffffffff0242ac1100020806` |
| 2 | 10,250,000 | 16 | 72 | `0242ac11000202000000000186dd6000` |
| 0 | 11,500,000 | 18 | 64 | `0011223344550242ac110002080045000000` |
| 3 | 11,500,000 | 19 | 80 | `33330000000102000000000186dddeadbeef00` |

The run log contained ten records, exactly one `reference_imported` record,
`reused=false`, normalization `scapy-raw-v1`, packet count 4, the two source
identities above, output metadata identity
`2573517a5b00a2cdde835ea2b16e6d537f8dbd90c9de843aa55b70f1a8944315`,
and normalized reference identity
`0c4ac3ae43aa65eb5fd52386bc906c3afc005fc640aa0f81579d649957917612`.
The last record was `run_completed`.

Exactly nine regular, single-link, mode-0600 artifacts were present:

| artifact | bytes | SHA-256 |
| --- | ---: | --- |
| `best_model.json` | 3,755 | `72f4c11d0a9e6dc34db38b96d81308cdb74ba9519109dd684addb4d7d66be9b1` |
| `capture.json` | 63 | `2573517a5b00a2cdde835ea2b16e6d537f8dbd90c9de843aa55b70f1a8944315` |
| `checkpoint.json` | 104,539 | `8eba3345f7570f10b7d1a1bdbb20850530f5f31a2b2d19fa2c3bcef6b7fd3c84` |
| `experiment.toml` | 3,879 | `c5171ac797ff9043b64e7be0190b945a248428f2dce97d7c99d4683e36fde1f5` |
| `ga_history.csv` | 978 | `68d25b85bdbea12047e4d7c6c54f5226df1f8b6586f390d2236aaea5a8e428af` |
| `generated.pcapng` | 348 | `9b30eb1a4859a245eb95950f3e610618856d9eb08078b13ac88435c0aa30d9eb` |
| `reference.pcapng` | 248 | `0c4ac3ae43aa65eb5fd52386bc906c3afc005fc640aa0f81579d649957917612` |
| `run.log` | 2,667 | `1fef76591518e7ace97ef20788f9aab3b1d15d50be399000355dbc97ddea3a03` |
| `similarity.json` | 180,963 | `5026154420302b574a4a2a0d8aa90eaacbdecd942e049c24860545247e584276` |

## Noncanonical PCAPNG run

The distinct fresh command was:

```bash
uv run --locked trafficlab import-run \
  /tmp/trafficlab-import-run-pcapng/experiment.toml \
  tests/fixtures/data/import_run/noncanonical-pcapng-source
```

The rendered config was 3,881 bytes with SHA-256
`180ebe0ee91a4b7a6d1133f125a71b80fe3203e4e6bbb3edb6cf89607bd0b83f`.
Source identities before and after were byte-for-byte equal:

| source | bytes | mode | links | SHA-256 |
| --- | ---: | ---: | ---: | --- |
| `capture.json` | 63 | 644 | 1 | `2573517a5b00a2cdde835ea2b16e6d537f8dbd90c9de843aa55b70f1a8944315` |
| `source.pcapng` | 292 | 644 | 1 | `9d8ccbd5a5304486b243de5992b92a7b620ca214964c8766efd63d56b50b2c71` |

Status sidecar: start `2026-09-04T16:38:00.178424277Z`, end
`2026-09-04T16:38:01.506133542Z`, exit 0. GNU `time` reported 1.24 seconds,
8.20 user seconds, 0.31 system seconds, maximum RSS 138,260 KiB, zero swaps,
and exit 0. The observer recorded target PID 2716717 and no direct child.
The command printed:

```text
import-run: family=packet_hmm fitness=0.924626 reference_packets=4 generated_packets=6 aggregate_score=0.918511 output=/tmp/trafficlab-import-run-pcapng/run
```

The status, resource, and process-observation sidecar SHA-256 values were,
respectively,
`c4802b6357649c0246885ff5ad6528d03afc9ae328d092d10d6d8a73fd292d1a`,
`b88408bb700ff79d51e79a033e0c73609f8be61a9f07fc846b4f47481f857ee1`, and
`47f79af3708e18ac142a355bd61088407a5eef0227c15b742b14db0ef2556ce4`.
The stderr sidecar was empty. The complete pre/post Docker inventories were again
identical at SHA-256
`6ed8cd4cbab0d5e87057356b7e82079d1dfb816ab7e3437a3d8711763d2819df`.

The independent parser found two Ethernet source interfaces and one Ethernet output
interface. Output contained one Section Header, one Interface Description, and four
Enhanced Packet Blocks. Exact higher-precision timestamps were stably ordered before
truncation toward the past; frames and lengths were unchanged:

| input ordinal | source timestamp | canonical microseconds | captured | wire | frame hex |
| ---: | --- | ---: | ---: | ---: | --- |
| 2 | `102500001/10000000` | 10,250,000 | 16 | 74 | `0242ac11000202000000000186dd6000` |
| 1 | `102500009/10000000` | 10,250,000 | 14 | 62 | `ffffffffffff0242ac1100020806` |
| 0 | `115000009/10000000` | 11,500,000 | 18 | 68 | `0011223344550242ac110002080045000000` |
| 3 | `115000009/10000000` | 11,500,000 | 19 | 82 | `33330000000102000000000186dddeadbeef00` |

The run log again had ten records, exactly one non-reused `scapy-raw-v1`
`reference_imported` lineage, and a terminal `run_completed`. Its normalized
reference identity was
`01ea912337120626e4176bccb5b778d57e06d407061df971095049cc31b084ef`;
the lineage source and output identities equal the independently hashed bytes above.

Exactly nine regular, single-link, mode-0600 artifacts were present:

| artifact | bytes | SHA-256 |
| --- | ---: | --- |
| `best_model.json` | 3,755 | `7921a4462745e14d5f570f2bd73964bbabb6db1311a8d89d40425675ca3d4b55` |
| `capture.json` | 63 | `2573517a5b00a2cdde835ea2b16e6d537f8dbd90c9de843aa55b70f1a8944315` |
| `checkpoint.json` | 106,444 | `12db75484c3906238acd66b773c3e63dc33471d7c6d8893e0d6663dbd3de3ad9` |
| `experiment.toml` | 3,881 | `180ebe0ee91a4b7a6d1133f125a71b80fe3203e4e6bbb3edb6cf89607bd0b83f` |
| `ga_history.csv` | 977 | `30423ba79f50f3a0122d22312c2d3933f90d65c8a733b438b6503b5e490a3771` |
| `generated.pcapng` | 348 | `e67f22c5cb607e1955ede0cca794f6e50e15ef3e2cca1d6c62da38ce3f449556` |
| `reference.pcapng` | 248 | `01ea912337120626e4176bccb5b778d57e06d407061df971095049cc31b084ef` |
| `run.log` | 2,701 | `d58a43ded94517b22e90c722595538e0b0c1fd2b31002305cbcf9e7a6173da07` |
| `similarity.json` | 181,494 | `87e567c14d1393e9e9e65cb2a12588ef3192afaf526b18c4fd4c020d9564fe7b` |

## Saved-output reproduction and arithmetic

Each saved run was audited independently under the Focused resource guard:

```bash
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python scripts/check_required_candidate_run.py \
  /tmp/trafficlab-import-run-pcap/run
scripts/run_bounded.sh \
  --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 5m --kill-after 10s -- \
  uv run --locked python scripts/check_required_candidate_run.py \
  /tmp/trafficlab-import-run-pcapng/run
```

The checker loaded each terminal checkpoint and saved model, regenerated
`generated.pcapng` byte for byte with the saved final seed, recomputed the complete
comparison from saved inputs/settings, and invoked strict nine-artifact validation:

```text
strict_artifacts=pass run=/tmp/trafficlab-import-run-pcap/run
reproduction=pass generated_bytes_equal=true comparison_equal=true
packets=4 generated_packets=6 winner=packet_hmm aggregate=0.920874569632 fitness_methods=8 postfit_diagnostics=3
strict_artifacts=pass run=/tmp/trafficlab-import-run-pcapng/run
reproduction=pass generated_bytes_equal=true comparison_equal=true
packets=4 generated_packets=6 winner=packet_hmm aggregate=0.918510698897 fitness_methods=8 postfit_diagnostics=3
```

An additional `math.fsum(method_score * configured_weight)` audit reproduced each
stored aggregate exactly, with the eight configured weights summing exactly to 1.0:

| run | generated SHA-256 | observed winner | observed selection fitness | stored/recomputed aggregate |
| --- | --- | --- | ---: | ---: |
| classic PCAP | `9b30eb1a4859a245eb95950f3e610618856d9eb08078b13ac88435c0aa30d9eb` | `packet_hmm` | 0.9291622037657498 | 0.9208745696318449 |
| noncanonical PCAPNG | `e67f22c5cb607e1955ede0cca794f6e50e15ef3e2cca1d6c62da38ce3f449556` | `packet_hmm` | 0.9246255412251192 | 0.9185106988974014 |

Again, these family and score values are non-causal smoke observations, not research
conclusions.

## Gate-owned corrections

Integrated acceptance exposed three contained owners. The checked implementation
plan had one Ruff-formatting defect; the capture deadline regression still expected
the later validation boundary after imported publication added an earlier copy
deadline; and the Scapy diagnostic source list still named the pre-refactor flat
module. Focused RED/GREEN checks corrected those owners in `bf888fc`, and the
source-bound Scapy timing diagnostic was freshly regenerated in `62ce3b9` without
changing its deterministic byte/trace identities or introducing a threshold.

The first classic-PCAP smoke attempt then proved that the checked small profile's
0.5-second C2ST windows provide only three windows over this fixture's 1.25-second
span, leaving a guarded fold without training data. Commit `70c76be` changed only
that development profile's C2ST width to the already-supported 0.25 seconds and
added a fixture-window feasibility regression. The failed temporary run was removed;
both retained evidence runs were recreated from fresh paths after the correction.

One Ordinary attempt also observed nested process-guard status 143 although the
systemd journal recorded an actual OOM kill and all controlled PIDs and the scope
were gone. The exact focused probe immediately passed with status 137, and both the
later Ordinary and Coverage selections passed the same process-guard test. No code
change was made for that non-reproducible environmental status observation.

Existing historical evidence documents and retained validation-study bundles were
not modified.
