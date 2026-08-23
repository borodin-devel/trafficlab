# Example configurations

`minimal.toml` is the complete copyable Trafficlab configuration. It enables all
three classical model families, all four mandatory similarity methods, bounded
trial and final generation, deterministic seeds, checkpoint settings, and the
two-container capture topology.

The checked file is intentionally safe: `example.invalid` cannot resolve to a
live endpoint. To run a capture, copy the file, then change the target image,
target argument vector, capture probe URL, and any local mount paths together.
Relative paths are resolved from the configuration file's directory.

```bash
cp examples/configs/minimal.toml examples/configs/local.toml
uv run --locked trafficlab preflight examples/configs/local.toml
uv run --locked trafficlab run examples/configs/local.toml
```

Every field is annotated inline in `minimal.toml`. The complete validation and
defaulting contract is described under "Experiment configuration" in
[`architecture/SYSTEM.md`](../../architecture/SYSTEM.md#experiment-configuration).
Unknown keys, disabled-family settings, invalid probability or bound ranges,
unsafe mount relationships, and resource limits that cannot satisfy the
configured workflow are errors.

This directory contains TOML rather than JSON. JSON output field dictionaries
are grouped with their artifacts under [`../data/`](../data/),
[`../scientific_stack/`](../scientific_stack/), and
[`../validation_study/`](../validation_study/).
