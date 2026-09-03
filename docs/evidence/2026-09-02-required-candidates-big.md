# Required-candidate Big completion evidence

## Scope and interpretation

This is the bounded Task 15 integrated-development check for the required-model
Big profile. It is not a held-out evaluation, a production model-selection
study, or causal evidence. The same imported reference is used for fitting and
final comparison, so the recorded scores describe resemblance to this capture
under this one predeclared configuration only. No generation or seed was added
after output inspection.

The final canonical run is retained at `runs/required-candidates-big/` and its
stage/resource sidecars at `.work/required-candidates/evidence/big/`. The first
fit attempt stopped before checkpoint publication on an exact floating-point
history invariant; its unchanged run and sidecars are retained separately at
`.work/required-candidates/big-run-diagnostic-round1/` and
`.work/required-candidates/evidence/big-diagnostic-round1/`.

## Environment and source identity

The final run was produced from source commit
`3e7096c8a7c4004db1b87fd5e795eba4732fce44` and tree
`b3676ab90ebb2c6726bf98810ff91f7ac0c3f477`. The locked environment used
CPython 3.12.3; `uv.lock` SHA-256 was
`b66ad35ff61e66b02cfd380eb0396916a7271b07421183b9253f489a2e1498f9`.
The host record was Linux `6.18.33.2-microsoft-standard-WSL2`, x86_64, Docker
Engine/client 29.7.2, and Docker Compose v5.5.0. Editcap and Reordercap both
reported Wireshark 4.2.2.

The copied full-capture pair remained regular files with link count one:

| object | source path | bytes | SHA-256 |
| --- | --- | ---: | --- |
| reference PCAPNG | `/home/bsa/projects/trafficlab/dumps/moutai-stock-price-response-success/trafficlab-ready-moutai-stock-price-response-success.pcapng` | 2,854,456 | `bbabc5529d910de0f3805939dc9dd1f1348070b61e47036cb44afd8e3004d2c3` |
| capture metadata | `/home/bsa/projects/trafficlab/dumps/moutai-stock-price-response-success/capture.json` | 63 | `f1f1c3cd4dee6a8bb6e38e438e4e7898749a6801d93c21cff9940ded9b098209` |

Strict capture inspection reported 3,649 packets, 2,064 outbound and 1,585
inbound, with normalized observation window `W=112.37044286727905` seconds.

## [STEP-122-816102dd] Big release gate

The prescribed release sequence completed with these accepted results:

| command/gate | accepted result |
| --- | --- |
| `uv sync --locked --all-groups --all-extras` | 56 packages resolved, 55 checked; status 0 |
| `ruff format --check .` | 566 files already formatted; status 0 |
| `ruff check .` | all checks passed; status 0 |
| strict `pyright` | 0 errors, 0 warnings, 0 informations; status 0 |
| bounded Ordinary | 4,598 passed in 225.47 s; status 0 |
| bounded branch Coverage | 4,598 passed in 634.37 s; 93.91%; status 0 |
| five fixture/schema generators with `--check` | similarity, models, fit/checkpoint, Validation Study, and 13 artifact-schema roots matched; status 0 |
| reduction and two retained benchmarks with `--check` | retained evidence verified; status 0 |
| source-bound scientific-stack example | verified against commit `292202368fa2ee7b4f2cccc5a68971feff243a3b`; status 0 |
| MMPP and pymoo scientific probes | both retained evidence files verified; status 0 |
| bounded Docker/Internet gate | 19 passed, 4,598 deselected in 424.78 s; status 0 |

This complete release sequence finished immediately before the fit-discovered
`3e7096c` checkpoint arithmetic correction. Per the gate policy, that isolated
correction reran its 254-test checkpoint owner, targeted 98% branch coverage,
strict targeted Ruff/Pyright, the fit fixture check, and the affected Big fit;
unrelated successful release commands were not repeated.

The external gate used the required credential-free Wikimedia HTTPS URL. Its
19 selected tests included the capture image identity, controlled capture and
failure/cleanup matrix, complete pipeline, injected post-preflight failures,
and real DNS/TLS bidirectional Internet capture. The final command left no
tracked Compose resource or active `trafficlab-test-guard-*.scope`.

Failed commands were stopped and returned to their smallest owning tier before
only the affected command was repeated. The accepted reruns include these
repairs:

- `f782837` lets the direct-execution guard apply canonical leading
  `NAME=value` arguments through `env` without shell evaluation.
- `8d3ee0b` refreshes three Validation Study outer-manifest hashes and makes the
  scalar ACF property oracle honor the specified exact-constant convention.
- `8348708` returns freed glibc heap pages at test-module teardown; this removed
  a reproducible 2 GiB `MemoryHigh` reclaim stall while preserving the exact
  release limits.
- A single process-guard signal-race failure was not reproducible in one
  focused plus ten repeated bounded probes; the subsequent complete Ordinary
  gate passed without changing that behavior.
- `084cc7e` makes the bounded Docker pipeline fixture contain exactly its three
  enabled family tables and gives its schema-5 final-only diagnostics windows
  valid for the controlled approximately 9 ms capture.

## [STEP-123-f666324b] Full-capture experiment

`runs/required-candidates-big.toml` is byte-equivalent to
`examples/required_candidates/big.toml` except for `run.directory`, which points
to the canonical retained directory. The executed profile retained population
21, generation hard limit 10, trial seeds `[17, 29, 43]`, early stopping after
three stagnant generations at tolerance `0.0001`, all seven model families,
eight equal weights of `0.125`, and the three configured post-fit diagnostics.

Each standalone stage ran through `scripts/run_bounded.sh` and `/usr/bin/time
-v`; output/error/time sidecars are outside the strict run directory:

| stage | memory high/max | swap max | wall limit | elapsed | user + system CPU | max RSS | status |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| config-only preflight | 2/3 GiB | 512 MiB | 10 min | 0.35 s | 3.47 + 0.10 s | 44,412 KiB | 0 |
| fit | 6/8 GiB | 1 GiB | 60 min | 3 min 12.14 s | 248.87 + 0.21 s | 190,876 KiB | 0 |
| generate | 2/3 GiB | 512 MiB | 10 min | 1.43 s | 6.43 + 0.20 s | 114,592 KiB | 0 |
| compare | 2/3 GiB | 512 MiB | 10 min | 2.04 s | 8.81 + 0.34 s | 155,636 KiB | 0 |
| strict reproducer | 2/3 GiB | 512 MiB | 10 min | 3.54 s | 11.23 + 0.34 s | 232,384 KiB | 0 |

The checkpoint stopped normally at generation 7 with `early_stop`, below the
generation-10 hard limit. Its final population contains 21 candidates and its
64 history rows form eight complete generation blocks. The selected candidate
identifier is `[4, 5]`; its family is `markov_renewal` and its three-seed
selection fitness is `0.8598385919637961`. Final generation used only the
predeclared seed `20260904` and published 3,505 packets.

The final-population family outcomes are diagnostic observations, not evidence
of a causal traffic mechanism or global model superiority:

| family | candidates | valid | final family champion fitness | bounded outcome |
| --- | ---: | ---: | ---: | --- |
| `acd` | 1 | 0 | 0 | generated too few events for configured similarity minima |
| `markov_packet_train` | 1 | 0 | 0 | `max_packets` guard |
| `markov_renewal` | 15 | 15 | 0.859838591964 | valid |
| `mmpp` | 1 | 1 | 0.573447199015 | valid |
| `nhpp` | 1 | 0 | 0 | `max_packets` guard |
| `packet_hmm` | 1 | 1 | 0.840013477911 | valid |
| `poisson_empirical` | 1 | 1 | 0.739457345359 | valid |

The first fit attempt returned status 2 after 26.87 s and 145,756 KiB maximum
RSS because a correctly rounded history mean crossed its exact valid-count
ceiling by one ULP. It published no checkpoint, history, best model, generated
trace, or comparison. Commit `3e7096c` added a red/green numerical regression
and canonical one-ULP downward adjustment at the history producer; all 254
checkpoint tests then passed, `summarize_generation` had full executable
line/branch coverage, targeted checkpoint coverage was 98%, and the fit fixture
remained byte-identical. The clean canonical run was recreated from the same
source pair and unchanged Big settings. This was a defect-correction rerun, not
search deepening or selective result retry.

## [STEP-124-6802a7e8] Artifact and arithmetic audit

The checked-in `scripts/check_required_candidate_run.py` loaded the terminal
checkpoint and best model, reproduced `generated.pcapng` byte for byte from the
saved model and final seed, recomputed the complete final comparison from saved
inputs/settings, and called strict final-artifact validation. It reported:

```text
strict_artifacts=pass run=.../runs/required-candidates-big
reproduction=pass generated_bytes_equal=true comparison_equal=true
packets=3649 generated_packets=3505 winner=markov_renewal aggregate=0.857822733861 fitness_methods=8 postfit_diagnostics=3
```

The explicit checkpoint and best-model schema field is 5. Checkpoint reference,
capture, and experiment identities exactly match the saved bytes. The final
comparison input identities match the reference, metadata, generated PCAPNG,
and similarity-settings objects. Independent `math.fsum(score * weight)`
recomputed the stored aggregate exactly as `0.857822733861172`, and the eight
weights sum exactly to `1.0`:

| fitness method | score | weight |
| --- | ---: | ---: |
| `frame_size_ks` | 0.982753291797 | 0.125 |
| `iat_ks` | 0.954503124249 | 0.125 |
| `autocorrelation` | 0.840419044468 | 0.125 |
| `multiscale_rate` | 0.121025653083 | 0.125 |
| `cramer_von_mises` | 0.999881277308 | 0.125 |
| `anderson_darling` | 0.999968665043 | 0.125 |
| `jensen_shannon` | 0.988514577042 | 0.125 |
| `approximate_mmd` | 0.975516237898 | 0.125 |

Exactly three unweighted, final-only diagnostics are present:
`fano_allan=0.8107592108311652`,
`transition_matrix=0.9761698708530007`, and
`classical_c2st=0.658567901234568`. They are absent from genetic trial method
tuples, which retain only the eight weighted methods.

The strict canonical directory contains exactly nine files with these final
SHA-256 identities:

| artifact | SHA-256 |
| --- | --- |
| `experiment.toml` | `d3b86f058de39be0d05f1a0acf701feb8a2917c5bbbac64c827d937bda3d9abe` |
| `run.log` | `7a91a1edcf0c6fb7ef08e1519f459a404d74a038daa27aa7ef2c68f3e8e1967c` |
| `capture.json` | `f1f1c3cd4dee6a8bb6e38e438e4e7898749a6801d93c21cff9940ded9b098209` |
| `reference.pcapng` | `bbabc5529d910de0f3805939dc9dd1f1348070b61e47036cb44afd8e3004d2c3` |
| `checkpoint.json` | `092f5321581b9c1fa14676ba8bc3bacf3b725de427dab088a76aa24a0f912024` |
| `ga_history.csv` | `5cdb1fec21d9da3b8b08d7254393a32a21948c7a5e953c363af06bf8b595010c` |
| `best_model.json` | `71685faa875631849260c3c9a0ede3c43e943c1f41e8b34194acb760eef02da4` |
| `generated.pcapng` | `a0c50bd72b9392914c07d84ba8707b0e295cfa4eeb7e1b835deaa805b3a95e40` |
| `similarity.json` | `adad14a4700559c7acd6621a842b42a47ccaf52f0ae33277b48fc670f2bf8fa8` |

Independent final specification/code-quality review is still pending. This
step and final push/closure remain open until the controller supplies a clean
review.
