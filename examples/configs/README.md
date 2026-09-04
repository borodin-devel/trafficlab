# Example configurations

`minimal.toml` is the complete small capture-oriented configuration.  The four
offline templates for prepared external references under `dumps/` enable all
seven classical model families and all eight mandatory fitness similarity
methods.  `default.toml` is intentionally equivalent to the recommended
`balanced.toml` search apart from its run directory.

| profile | population | generations | trial seeds | early-stop patience | maximum selection/trial simulations |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fast_routine.toml` | 14 | 10 | 1 | 3 | 154 |
| `balanced.toml` / `default.toml` | 28 | 50 | 3 | 12 | 4,284 |
| `maximum_quality.toml` | 35 | 100 | 5 | 25 | 17,675 per run |

The selection maximum is
`population × (generations + 1) × trial seeds`; early stopping can finish
earlier.  A successful `fit` performs one additional final-validation
simulation with `final_seed`, and the standalone `generate` stage performs
another simulation from the saved winner.  All profiles use an absolute
early-stopping tolerance of `0.0001` and identical model, similarity, post-fit,
and resource-limit settings, so their selection budgets are directly
comparable.

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

Choose a profile and give every experiment a fresh run directory.  The
balanced profile is the normal starting point:

```bash
cp examples/configs/balanced.toml examples/configs/claudecode-fit.toml
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

The target and capture sections in the offline profiles are deliberately
non-runnable placeholders: they record that the reference was imported rather
than captured by that experiment. Do not invoke `capture` or `run` with these
templates. The large generation guards cover every dump currently checked
locally, but they are reliability ceilings rather than expected consumption;
larger future captures need an explicit review. The inferred `capture.json` MAC
is structural metadata, not authoritative original-interface provenance, so
confirm packet directions before treating fitted direction-dependent
parameters as scientific evidence.

`fast_routine.toml` is for iteration, not a strong model-selection claim.
`balanced.toml` is the recommended default.  A maximum-quality study should run
`maximum_quality.toml` three times from fresh directories with independent
`master_seed` / `final_seed` pairs, for example `20260831` / `20260832`,
`20260911` / `20260912`, and `20260921` / `20260922`.  Trial seeds remain common
within each run so candidates face the same stochastic evaluations.  Compare
the three retained winners and convergence histories; no finite stochastic
search guarantees a global optimum.

This directory contains TOML rather than JSON. JSON output field dictionaries
are grouped with their artifacts under [`../data/`](../data/),
[`../scientific_stack/`](../scientific_stack/), and
[`../validation_study/`](../validation_study/).
