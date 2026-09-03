# Required-model development candidates

These profiles exercise all seven enabled classical traffic-model families
and all configured similarity methods against the imported Moutai reference.
They are bounded development checks only: no result from `small.toml`,
`medium.toml`, or `big.toml` may be presented as a best-model claim.

| Profile | Reference | Population | Generations | Trial seeds | Early stopping |
| --- | ---: | ---: | ---: | --- | ---: |
| small | first 256 packets | 8 | 1 | `[17]` | disabled (`0`) |
| medium | first 512 packets | 12 | 3 | `[17, 29]` | two generations, tolerance `0.0001` |
| big | full capture | 21 | 10 | `[17, 29, 43]` | three generations, tolerance `0.0001` |

All generation packet, byte, and wall-clock values are bounded guards. Guard
exhaustion invalidates the candidate instead of publishing a partial trace.
The configs use an unexecuted external-reference target and therefore support
only the standalone `preflight --config-only`, `fit`, `generate`, and
`compare` stages.

From the repository root:

```bash
UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv run --locked python scripts/derive_required_candidates_reference.py \
  --source /home/bsa/projects/trafficlab/dumps/moutai-stock-price-response-success/trafficlab-ready-moutai-stock-price-response-success.pcapng \
  --capture-json /home/bsa/projects/trafficlab/dumps/moutai-stock-price-response-success/capture.json \
  --packet-limit 256 --output .work/required-candidates/small
UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv run --locked python scripts/derive_required_candidates_reference.py \
  --source /home/bsa/projects/trafficlab/dumps/moutai-stock-price-response-success/trafficlab-ready-moutai-stock-price-response-success.pcapng \
  --capture-json /home/bsa/projects/trafficlab/dumps/moutai-stock-price-response-success/capture.json \
  --packet-limit 512 --output .work/required-candidates/medium
```

For `small` and `medium`, preflight the matching TOML, then copy that
candidate's `reference.pcapng` and `capture.json` into the configured
`.work/required-candidates/*-run` directory. For `big`, copy the full source
PCAPNG and its unchanged metadata into the big run directory. Run each stage
separately:

```bash
UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv run --locked trafficlab preflight examples/required_candidates/small.toml --config-only
UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv run --locked trafficlab fit examples/required_candidates/small.toml
UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv run --locked trafficlab generate examples/required_candidates/small.toml
UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv run --locked trafficlab compare examples/required_candidates/small.toml
```

Substitute `medium.toml` or `big.toml` for the other tiers. Keep the derived
directories local and ignored; `manifest.json` records source and metadata
SHA-256 identities, output identity, packet count, normalized `W`, and the
exact editcap/reordercap versions.
