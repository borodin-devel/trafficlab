# Example configurations

`minimal.toml` is the complete small capture-oriented configuration.
`default.toml` is the release-sized offline template for prepared external
references under `dumps/`. Both enable all three classical model families and
all four mandatory similarity methods.

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

## Prepared external references

Copy `default.toml` and give every experiment a fresh run directory:

```bash
cp examples/configs/default.toml examples/configs/claudecode-fit.toml
# Edit [run].directory in the copied file.
uv run --locked trafficlab preflight \
  examples/configs/claudecode-fit.toml --config-only
```

Then copy the selected prepared directory's two files into the configured run
directory, renaming the PCAPNG to the stage-owned name:

```bash
cp --reflink=auto dumps/claudecode/trafficlab-ready-claudecode.pcapng \
  runs/claudecode-fit/reference.pcapng
cp --reflink=auto dumps/claudecode/capture.json \
  runs/claudecode-fit/capture.json
uv run --locked trafficlab fit examples/configs/claudecode-fit.toml
uv run --locked trafficlab generate examples/configs/claudecode-fit.toml
uv run --locked trafficlab compare examples/configs/claudecode-fit.toml
```

The target and capture sections in `default.toml` are deliberately non-runnable
placeholders: they record that the reference was imported rather than captured
by that experiment. Do not invoke `capture` or `run` with this template. The
large generation guards cover every dump currently checked locally, but they
are reliability ceilings rather than expected consumption; larger future
captures need an explicit review. The inferred `capture.json` MAC is structural
metadata, not authoritative original-interface provenance, so confirm packet
directions before treating fitted direction-dependent parameters as scientific
evidence. The default search is intentionally deep—30 individuals, up to 100
generations, and five common trial seeds—and may run for hours or days. Resume
is enabled, and early stopping occurs only after 20 consecutive generations
without a best-fitness improvement greater than `0.0001`. No finite stochastic
search guarantees a global optimum; for stronger evidence, repeat the fit under
independent master seeds and compare retained winners.

This directory contains TOML rather than JSON. JSON output field dictionaries
are grouped with their artifacts under [`../data/`](../data/),
[`../scientific_stack/`](../scientific_stack/), and
[`../validation_study/`](../validation_study/).
