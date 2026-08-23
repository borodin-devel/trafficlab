# Examples

This tree contains runnable configurations, deterministic local fixtures,
generated public JSON Schemas, retained scientific-stack measurements, and the
real-program Validation Study. Paths use the repository's existing plural
`examples/` name.

## Directory map

| Directory | Contents |
| --- | --- |
| [`configs/`](configs/) | Copyable experiment configuration with safe, non-routable placeholder URLs. |
| [`data/`](data/) | Small deterministic capture, model, generation, comparison, and fitting fixtures. |
| [`schemas/`](schemas/) | Generated JSON Schema documents for public scientific artifacts. |
| [`scientific_stack/`](scientific_stack/) | Checked benchmarks, proof-of-concept decisions, and a retained real run. |
| [`validation_study/`](validation_study/) | Frozen protocol, accepted evidence, result summary, and scientific report. |

## What to edit

`configs/minimal.toml` is the starting point for a local experiment. Copy it to
an ignored local file and replace both placeholder URLs and the target image and
arguments before capture:

```bash
cp examples/configs/minimal.toml examples/configs/local.toml
uv run --locked trafficlab preflight examples/configs/local.toml
```

The JSON, PCAPNG, CSV, and retained evidence files are checked outputs. Do not
hand-edit them. Each subdirectory README names its owner command and validation
rules. In particular, accepted Validation Study bundles are immutable and their
manifests bind every retained byte.

## JSON conventions

The field dictionaries live beside the files:

- [`data/README.md`](data/README.md) covers capture metadata, fitted models,
  similarity output, fixture manifests, and the offline fitting checkpoint.
- [`schemas/README.md`](schemas/README.md) explains JSON Schema document fields;
  [`scientific-artifact-v4/README.md`](schemas/scientific-artifact-v4/README.md)
  catalogs the artifact fields represented by every public schema.
- [`scientific_stack/README.md`](scientific_stack/README.md) covers benchmark,
  probe, reduction, and retained-run evidence.
- [`validation_study/README.md`](validation_study/README.md) covers the standalone
  result summary, while [`evidence/README.md`](validation_study/evidence/README.md)
  covers accepted bundle records.

Common conventions are:

| Field or value | Meaning |
| --- | --- |
| `sha256` | Lowercase SHA-256 digest of the exact authoritative bytes. |
| `size` | Byte length paired with a SHA-256 identity. |
| `schema_version` | Version of the owning evidence-document format. |
| `scientific_artifact_schema` | Version of Trafficlab's shared public scientific-artifact contract. |
| `inbound` / `outbound` | Direction relative to the captured target container's MAC address. |
| `observation_window_seconds` | Reference-derived duration `W` used for fitting, generation, and comparison. |

All checked JSON is UTF-8 and canonicalized by its owning code. Unknown fields
are rejected by strict artifact models where a public schema exists.
