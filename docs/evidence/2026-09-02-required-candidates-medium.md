# Required-candidate development experiments

## Scope and provenance

These are bounded development checks for the Task 14 required-model candidate
profiles. They are not held-out evaluations, model-selection claims, or causal
evidence. Both profiles use the imported Moutai capture for fitting and final
comparison, so an aggregate score describes resemblance to this reference under
these settings only. No search deepening or retry/cherry-pick was performed
after either result became visible.

The source identities recorded by the checked-in derivation manifests are:

| object | path | SHA-256 | bytes |
| --- | --- | --- | ---: |
| source PCAPNG | `/home/bsa/projects/trafficlab/dumps/moutai-stock-price-response-success/trafficlab-ready-moutai-stock-price-response-success.pcapng` | `bbabc5529d910de0f3805939dc9dd1f1348070b61e47036cb44afd8e3004d2c3` | 2,854,456 |
| source metadata | `/home/bsa/projects/trafficlab/dumps/moutai-stock-price-response-success/capture.json` | `f1f1c3cd4dee6a8bb6e38e438e4e7898749a6801d93c21cff9940ded9b098209` | 63 |

The exact derived pairs were copied without modification:

| tier | packets | W (s) | derived `reference.pcapng` SHA-256 | copied run pair SHA-256 |
| --- | ---: | ---: | --- | --- |
| small | 256 | 19.671482801437378 | `e6d7e7509de1f6d9d49a704392ac84e7c6628d1e366c311cf14a396d1e34a77e` | identical |
| medium | 512 | 47.191195011138916 | `b96c39452d44a6e8534cbd0b33255667e5282c86c1e3cbd4040809505a6de51b` | identical |

Metadata SHA-256 is the same in both copied pairs. Wireshark tools recorded by
the manifests are Editcap/Reordercap 4.2.2.

The saved fit artifacts were produced by implementation commit
`fb1978e8d41ffe845dc2e988db2568b17e867332` (tree
`4e404e05de5058e2cf8593377fabacb1f68cd924`). The evidence document is retained
later at commit `9479284`; its tree is not used as the fixture generator
source.

The final derived artifact identities retained in each canonical run are:

| tier | effective config | checkpoint | history | best model | generated PCAPNG | similarity |
| --- | --- | --- | --- | --- | --- | --- |
| small | `6b1d9bbfc3ad7f396198a4243337d4194e2a7f55d6d1615a90a6ee0b368b2693` | `53353f725a361a217c3f0d971458d26ae2c10e2372b5481fe0c934e7c82b5244` | `7616f541edd37652f984a94be82f5646cca2f55141040f60a881586acbb470dd` | `1b8599d06dbd979641339314819d550459408f6a84e1d00569a357d1abf9aa53` | `b56cffa2c3dfa22cc472b702761fe065eb02bf2c750a6bf34399e4868972757d` | `32c0c4e91a6d34fd2de0ae09bd209526853f524fa504ca4706bd984f02a3ada8` |
| medium | `8289364af80014f4f69f6eadca8233548cec1a9ee925fa3f48ef951538f32ec9` | `b6a9d7c4c94c72117022267c5438378fd9b7b7b4984ea29f3485c8b562db9250` | `63f9420ffa4b592a454fb71324517049783b88392d584455441b382e8d6b28f0` | `1a45c7559b26ccc85a9e8bb93c38c7d42a4ba7ebad2ab291eb0384f5f5e2eea8` | `40bf9806ec244bc35282f5448f6388d7754eafa0a311eaa74ed448f4ce3c893a` | `7a32bcba9fbe029b608774e7d66d8b9c00e26651880770e0735b68cfa1214a77` |

## Commands and bounded resources

Fresh canonical run directories were used: `runs/required-candidates-small/`
and `runs/required-candidates-medium/`. The prior published runs were preserved
intact as `.work/required-candidates/{small,medium}-run-diagnostic-r1/`; the
canonical trees were rebuilt from their fit-complete artifacts without refit or
search. Existing `.work/required-candidates/*-run` preflight directories were
not touched. The configs staged from the example profiles differ only in
`run.directory`, pointing to those canonical siblings.

Standalone config-only preflight commands:

```text
UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv run --locked trafficlab preflight runs/required-candidates-small.toml --config-only
UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv run --locked trafficlab preflight runs/required-candidates-medium.toml --config-only
```

Each returned status 0. Generation and comparison used the available bounded
systemd controller and `/usr/bin/time -v`:

```text
/usr/bin/time -v -o .work/required-candidates/evidence/clean-r1/<tier>/<stage>.time.txt \
  scripts/run_bounded.sh --memory-high 2G --memory-max 3G --swap-max 512M \
  --wall-time 10m --kill-after 15s -- \
  uv run --locked trafficlab <stage> runs/required-candidates-<tier>.toml \
  >.work/required-candidates/evidence/clean-r1/<tier>/<stage>.stdout \
  2>.work/required-candidates/evidence/clean-r1/<tier>/<stage>.stderr
```

The exact controller limits were `memory-high=2G`, `memory-max=3G`,
`swap-max=512M`, `wall-time=10m`, and `kill-after=15s`; all four stage commands
returned status 0. Stage sidecars are outside the run directories because
strict final validation requires exactly the nine documented run entries.

| tier | stage | elapsed | user + system CPU | max RSS | status |
| --- | --- | ---: | ---: | ---: | ---: |
| small | fit (inherited) | 1.39 s | 10.65 + 0.13 s | 135,396 KiB | 0 |
| small | generate | 0.66 s | 5.90 + 0.10 s | 107,124 KiB | 0 |
| small | compare | 1.09 s | 8.00 + 0.18 s | 135,184 KiB | 0 |
| medium | fit (inherited) | 10.52 s | 22.11 + 0.11 s | 143,332 KiB | 0 |
| medium | generate | 0.68 s | 5.85 + 0.16 s | 107,160 KiB | 0 |
| medium | compare | 1.04 s | 6.60 + 0.19 s | 137,348 KiB | 0 |

## [STEP-118-4a99b800] Small outcome

Settings were the exact `examples/required_candidates/small.toml` profile:
population 8, one generation, trial seed `[17]`, final seed `20260904`, and
early stopping disabled. Seven family lanes received candidates. The final
checkpoint is generation 1 with `hard_limit`; the champion is
`markov_renewal`, selection fitness `0.8021017931804963`, and final generation
published 114 packets.

| family | candidates | valid | family champion fitness | outcome |
| --- | ---: | ---: | ---: | --- |
| acd | 1 | 0 | 0.000000000000 | invalid: similarity precondition |
| markov_packet_train | 1 | 0 | 0.000000000000 | invalid: `max_packets` |
| markov_renewal | 2 | 2 | 0.802101793180 | valid |
| mmpp | 1 | 0 | 0.000000000000 | invalid: `max_packets` |
| nhpp | 1 | 0 | 0.000000000000 | invalid: `max_packets` |
| packet_hmm | 1 | 0 | 0.000000000000 | invalid: `max_packets` |
| poisson_empirical | 1 | 1 | 0.657485023862 | valid |

Final aggregate comparison score was `0.7983006137453372`. The eight fitness
method scores were:

| method | score |
| --- | ---: |
| frame_size_ks | 0.830249451754 |
| iat_ks | 0.873572791949 |
| autocorrelation | 0.856793767657 |
| multiscale_rate | 0.025795450155 |
| cramer_von_mises | 0.992985602887 |
| anderson_darling | 0.990266230069 |
| jensen_shannon | 0.922568769564 |
| approximate_mmd | 0.894172845927 |

All three post-fit diagnostics published: `fano_allan` score
`0.789222644906`, `transition_matrix` score `0.953633094928`, and
`classical_c2st` score `0.978125000000`.

## [STEP-119-3d9e8896] Medium outcome

Settings were the exact `examples/required_candidates/medium.toml` profile:
population 12, three generations, trial seeds `[17, 29]`, final seed
`20260904`, and early stopping limit two with tolerance `0.0001`. Seven family
lanes received candidates. The final checkpoint is generation 3 with
`hard_limit`; the champion is `markov_renewal`, selection fitness
`0.8323151185647766`, and final generation published 446 packets.

| family | candidates | valid | family champion fitness | outcome |
| --- | ---: | ---: | ---: | --- |
| acd | 1 | 0 | 0.000000000000 | invalid: similarity precondition |
| markov_packet_train | 1 | 1 | 0.812984279693 | valid |
| markov_renewal | 6 | 6 | 0.832315118565 | valid |
| mmpp | 1 | 1 | 0.619738561323 | valid |
| nhpp | 1 | 0 | 0.000000000000 | invalid: `max_packets` |
| packet_hmm | 1 | 0 | 0.000000000000 | invalid: nonconverged fit diagnostics |
| poisson_empirical | 1 | 1 | 0.713398005082 | valid |

Final aggregate comparison score was `0.8341071669523366`. The eight fitness
method scores were:

| method | score |
| --- | ---: |
| frame_size_ks | 0.931141395740 |
| iat_ks | 0.926753006882 |
| autocorrelation | 0.872200872708 |
| multiscale_rate | 0.035413251440 |
| cramer_von_mises | 0.998228204276 |
| anderson_darling | 0.999526884972 |
| jensen_shannon | 0.963286958245 |
| approximate_mmd | 0.946306761356 |

All three post-fit diagnostics published: `fano_allan` score
`0.707209075514`, `transition_matrix` score `0.976342939830`, and
`classical_c2st` score `0.894736842105`.

## [STEP-120-6f6f63f1] Artifact and reproduction checks

The strict validator command was an in-process call to
`trafficlab.pipeline.validation.validate_final_artifacts` after loading the
saved `checkpoint.json`, `best_model.json`, and `similarity.json`. Its companion
reproducer called `trafficlab.generation.stage.reproduce_generated_pcapng` with
the saved final seed and model, then called
`trafficlab.comparison.metrics.compare_final_traces` with the saved comparison
settings. The exact bounded command used for each tier was:

```text
timeout --signal=TERM --kill-after=15s 10m env UV_CACHE_DIR=/tmp/trafficlab-uv-cache \
  uv run --locked python - runs/required-candidates-<tier>.toml
```

The Python stdin body loaded the persisted artifacts, asserted generated bytes
equal the saved-model/seed reproduction, asserted recomputed comparison equals
`similarity.json`, and called `validate_final_artifacts`; both commands returned
status 0. Results:

```text
strict_artifacts=pass .../runs/required-candidates-small
reproduction=pass generated_bytes_equal=true comparison_equal=true
fitness_methods=8 postfit_diagnostics=3

strict_artifacts=pass .../runs/required-candidates-medium
reproduction=pass generated_bytes_equal=true comparison_equal=true
fitness_methods=8 postfit_diagnostics=3
```

The final run trees contain exactly `experiment.toml`, `run.log`,
`capture.json`, `reference.pcapng`, `checkpoint.json`, `ga_history.csv`,
`best_model.json`, `generated.pcapng`, and `similarity.json`. Their key
artifact identities are retained in the run directories; config snapshots and
stage resource/output sidecars are retained under ignored `.work` evidence
paths.

One command-wrapper concern is recorded for auditability: an initial attempted
strict-validation wrapper called `fit` after the non-resumable checkpoint was
already present. The command correctly failed with `checkpoint already exists`
and did not alter fit artifacts; the subsequent validator loaded the saved
checkpoint/model directly and passed strict validation and both reproductions.
That diagnostic is preserved in `run.log` and does not constitute a search
retry or a scientific result.

## Source-bound fixture reproduction

The candidate fixture was regenerated and checked with:

```text
TREE=$(git rev-parse fb1978e^{tree})
UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv run --locked python scripts/generate_validation_study_fixture.py \
  --source-commit fb1978e8d41ffe845dc2e988db2568b17e867332 --source-tree "$TREE"
git clone --no-local --no-hardlinks --no-checkout "$PWD" .work/required-candidates/fixture-source-bound-r1
git -C .work/required-candidates/fixture-source-bound-r1 checkout --detach fb1978e8d41ffe845dc2e988db2568b17e867332
cp -a --reflink=never tests/fixtures/data/validation_study/candidate/. \
  .work/required-candidates/fixture-source-bound-r1/tests/fixtures/data/validation_study/candidate/
UV_CACHE_DIR=/tmp/trafficlab-uv-cache PYTHONPATH=.work/required-candidates/fixture-source-bound-r1/src uv run --offline --locked --active --no-project \
  python scripts/generate_validation_study_fixture.py --check \
  --source-commit fb1978e8d41ffe845dc2e988db2568b17e867332 --source-tree "$TREE"
```

The clean clone check returned status 0 with
`symlinks=0`, `hardlinks_above_one=0`, and no Git alternates. It reported
`validation-study fixture: checked-in paths and bytes match deterministic
production output`.

## Complete Medium gate

The inherited integrated gate remains authoritative; its exact command and
recorded outcome were:

```text
UV_CACHE_DIR=/tmp/trafficlab-uv-cache uv sync --locked --all-groups --all-extras
uv run --locked ruff format --check .
uv run --locked ruff check src scripts tests
uv run --locked pyright src scripts tests
uv run --locked pytest -q -m 'not docker and not internet' \
  tests/unit tests/scientific tests/integration/generation tests/integration/comparison tests/trafficlab_dashboard
```

The outcome was **4,436 passed in 426.74 seconds**; sync, format, Ruff, and
Pyright also passed as recorded in the Task 14 report. No fit/search rerun was
performed for this fix.

## Development-only and causal caveat

These experiments demonstrate that the seven required family registries can
receive bounded candidates and that the final schema publishes eight fitness
methods plus three post-fit diagnostics. They do not establish that
`markov_renewal` is globally best, that the imported workload generalizes, or
that any model family caused an observed traffic property. Candidate invalid
counts reflect these deliberately small generation/fit guards and should be
reported as development diagnostics, not silently treated as zero scores.
