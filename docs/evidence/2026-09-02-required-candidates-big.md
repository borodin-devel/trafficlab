# Required-candidate Big completion evidence

## Scope and interpretation

This is the bounded integrated-development check for the required-model Big
profile after the final whole-branch review fixes. It is not held-out evidence,
a production model-selection study, a causal explanation, or evidence of global
model-family superiority. Fitting and final comparison use the same imported
reference, so all results describe resemblance to this one capture under the
declared settings only.

The canonical run is retained at `runs/required-candidates-big/`; stage and
resource sidecars are retained at `.work/required-candidates/evidence/big/`.
No generation, seed, population member, or search generation was added after
output inspection.

## Environment and source identity

The canonical run was produced from source commit
`38c5ecc44e4d61abd8a517f70af667700845ea89` and tree
`9ffea325c2d8fbf89570381f568de0eb2bc78843`. The locked environment used
CPython 3.12.3; `uv.lock` SHA-256 was
`b66ad35ff61e66b02cfd380eb0396916a7271b07421183b9253f489a2e1498f9`.
The host was Linux `6.18.33.2-microsoft-standard-WSL2`, x86_64. Editcap and
Reordercap both reported Wireshark 4.2.2.

The staged `runs/required-candidates-big.toml` is byte-equivalent to
`examples/required_candidates/big.toml` except for `run.directory`. Their
SHA-256 identities are respectively
`d2c3f48272bf540c8dab197a724c5c01696397f03980c7b0466331a67bfc490d`
and `c67349126860790443f1066bf1e263460a54d92f7f2b5228cb703b835055006b`.

The copied full-capture inputs were regular files with link count one:

| object | repository-relative source | bytes | SHA-256 |
| --- | --- | ---: | --- |
| reference PCAPNG | `dumps/moutai-stock-price-response-success/trafficlab-ready-moutai-stock-price-response-success.pcapng` | 2,854,456 | `bbabc5529d910de0f3805939dc9dd1f1348070b61e47036cb44afd8e3004d2c3` |
| capture metadata | `dumps/moutai-stock-price-response-success/capture.json` | 63 | `f1f1c3cd4dee6a8bb6e38e438e4e7898749a6801d93c21cff9940ded9b098209` |

Strict inspection reported 3,649 packets, comprising 2,064 outbound and 1,585
inbound packets, and normalized observation window
`W=112.37044286727905` seconds.

## Verification context

The prior complete Ordinary, Coverage, and Docker/Internet release commands
remain the reviewed pre-fix evidence recorded by the preceding run. They were
not repeated because the final brief required only affected owners. At the
current implementation, locked sync, repository format, Ruff, strict Pyright,
all affected deterministic generators/checkers, and the exact affected Medium
selection passed. Medium reported 4,534 passing tests in 454.71 seconds under
the prescribed 2/3 GiB, 512 MiB swap, ten-minute controller.

The deterministic checks covered similarity, model, fit/checkpoint, Validation
Study, and 13 public schema roots; exact historical and current pymoo probes
also passed. Function-region branch evidence reports 100% executable line and
branch coverage for every function exposed by a final-fix RED test.

## Prior-run and bounded-failure provenance

The prior canonical run at final-review base `c66e8e3` is preserved unchanged
at `.work/required-candidates/big-run-diagnostic-c66e8e3/`, with sidecars at
`.work/required-candidates/evidence/big-diagnostic-c66e8e3/`. Its checkpoint,
best model, generation, and comparison SHA-256 values remain respectively
`092f5321581b9c1fa14676ba8bc3bacf3b725de427dab088a76aa24a0f912024`,
`71685faa875631849260c3c9a0ede3c43e943c1f41e8b34194acb760eef02da4`,
`a0c50bd72b9392914c07d84ba8707b0e295cfa4eeb7e1b835deaa805b3a95e40`,
and `adad14a4700559c7acd6621a842b42a47ccaf52f0ae33277b48fc670f2bf8fa8`.
It is diagnostic provenance only and was not reused.

The first from-zero final-fix fit used the existing 3,649-packet trial guard.
It reached deterministic early stop at generation 9 with 17 valid candidates,
then exited 2 because the predeclared final seed produced 3,693 packets for the
selected Markov-renewal candidate and final validation normatively uses trial
limits. That intact failed run is retained at
`.work/required-candidates/big-run-diagnostic-trial-cap-3649/`, with sidecars
at `.work/required-candidates/evidence/big-diagnostic-trial-cap-3649/`. Its
source commit/tree were `1a68109d6dd111017fa3ea4980bb0e21372dbc74` /
`9a2a66ba12b71f51dddeef3416be3f7966915936`; fit elapsed 4:04.73, used
195,212 KiB maximum RSS, and published no best model, generated trace, or
comparison. Its checkpoint SHA-256 is
`4c0c39dddad19d74ff392bb1f8f6e8f974709d953e1a83990bd094ed81b2f3fb`.

The bounded failure returned to the profile owner. Commit `38c5ecc` changed
only Big `generation.trial.max_packets` from 3,649 to 10,000, matching its
existing final packet cap. The architecture does not freeze that packet bound;
the previous reference-count value was not a stochastic upper bound and was
incompatible with the required fresh-seed validation. Population 21,
generation cap 10, trial seeds `[17,29,43]`, master/final seeds, early stopping,
all wall guards, model/metric settings, and method weights remained unchanged.
This safety-limit correction can alter candidate validity and therefore
selection; the corrected run was recreated from zero rather than resumed, and
no candidate was manually reselected.

## Bounded canonical experiment

The corrected profile retained all seven families, all eight fitness methods
at weight `0.125`, and exactly three final-only diagnostics. Each standalone
stage ran through `scripts/run_bounded.sh` and `/usr/bin/time -v`:

| stage | memory high/max | swap max | wall limit | elapsed | user + system CPU | max RSS | status |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| config-only preflight | 2/3 GiB | 512 MiB | 10 min | 0.33 s | 3.36 + 0.04 s | 44,416 KiB | 0 |
| fit | 6/8 GiB | 1 GiB | 60 min | 54.26 s | 111.44 + 0.23 s | 200,804 KiB | 0 |
| generate | 2/3 GiB | 512 MiB | 10 min | 1.03 s | 6.67 + 0.17 s | 117,184 KiB | 0 |
| compare | 2/3 GiB | 512 MiB | 10 min | 1.61 s | 8.34 + 0.15 s | 159,960 KiB | 0 |
| strict reproducer | 2/3 GiB | 512 MiB | 10 min | 3.46 s | 10.52 + 0.29 s | 248,488 KiB | 0 |

The checkpoint stopped normally at generation 3 with `early_stop`, below the
generation-10 hard limit. Its final population contains 21 valid candidates
and its 32 history rows form four complete generation blocks. The selected
candidate identifier is `[0,14]`, family `markov_packet_train`, gene `[6]`,
with three-seed selection fitness `0.8591359237826298`. Final generation used
only seed `20260904` and published 4,707 packets.

Final-population family outcomes are diagnostic observations, not mechanism or
superiority claims:

| family | candidates | valid | final family champion fitness |
| --- | ---: | ---: | ---: |
| `acd` | 1 | 1 | 0.725935188971 |
| `markov_packet_train` | 15 | 15 | 0.859135923783 |
| `markov_renewal` | 1 | 1 | 0.845870375522 |
| `mmpp` | 1 | 1 | 0.744593894695 |
| `nhpp` | 1 | 1 | 0.764307163423 |
| `packet_hmm` | 1 | 1 | 0.839854606160 |
| `poisson_empirical` | 1 | 1 | 0.737958641248 |

## Strict artifact and arithmetic audit

The checked-in read-only reproducer loaded the terminal checkpoint and best
model, regenerated `generated.pcapng` byte for byte, recomputed the complete
comparison from saved inputs/settings, and invoked strict final-artifact
validation. It reported:

```text
strict_artifacts=pass run=.../runs/required-candidates-big
reproduction=pass generated_bytes_equal=true comparison_equal=true
packets=3649 generated_packets=4707 winner=markov_packet_train aggregate=0.864008131594 fitness_methods=8 postfit_diagnostics=3
```

Checkpoint and best-model scientific schema fields are 5. Checkpoint input and
settings identities match the saved bytes. The comparison input identities
match capture metadata, reference, generated PCAPNG, and the 1,301-byte
similarity settings identity
`fe9676961b86333e4c863a9bf2f4498375cf5136fec99cf149cd4b0642c495a7`.
Independent `math.fsum(score * weight)` exactly reproduces aggregate
`0.8640081315935364`; the eight weights sum exactly to `1.0`:

| fitness method | score | weight |
| --- | ---: | ---: |
| `frame_size_ks` | 0.979439670006 | 0.125 |
| `iat_ks` | 0.974326471060 | 0.125 |
| `autocorrelation` | 0.824389927712 | 0.125 |
| `multiscale_rate` | 0.171697866766 | 0.125 |
| `cramer_von_mises` | 0.985939205836 | 0.125 |
| `anderson_darling` | 0.999611318246 | 0.125 |
| `jensen_shannon` | 0.991615169664 | 0.125 |
| `approximate_mmd` | 0.985045423458 | 0.125 |

The CvM stratum discrepancies are global
`0.00005259182559858103`, uplink/outbound `0.014940241933418254`, and
downlink/inbound `0.04119775107012375`. The AD values are respectively
`0.000042001349755368154`, `0.0006288126748622162`, and
`0.000841911641008025`. Both methods retain configured stratum weights
`(global=0.5,uplink=0.25,downlink=0.25)` while artifacts preserve canonical
`outbound`/`inbound` keys.

Exactly three unweighted final-only diagnostics are present:
`fano_allan=0.944914442699881`,
`transition_matrix=0.9418577132531387`, and
`classical_c2st=0.8979358024691358`. They are absent from genetic trial method
tuples, which contain only the eight weighted methods.

The canonical run contains exactly nine regular files, all link count one:

| artifact | bytes | SHA-256 |
| --- | ---: | --- |
| `experiment.toml` | 3,969 | `d58906460481c085fe352ecd575ac55cc7365ac5de604916d8f2827871f4c62c` |
| `run.log` | 1,699 | `a3ab1f1c261fe338c52264882eacf09f1128b9ed876bac9d5775ec0d0644894b` |
| `capture.json` | 63 | `f1f1c3cd4dee6a8bb6e38e438e4e7898749a6801d93c21cff9940ded9b098209` |
| `reference.pcapng` | 2,854,456 | `bbabc5529d910de0f3805939dc9dd1f1348070b61e47036cb44afd8e3004d2c3` |
| `checkpoint.json` | 5,481,898 | `320e6dfc3369721b2dbe99ca784424bf0ecf34c5044c4a846ce6cb82881fb87f` |
| `ga_history.csv` | 2,227 | `72cbf9539cf003e99ae37074d0fe7c77bdf5721457a18d90fe0de616b86ad655` |
| `best_model.json` | 194,339 | `e8c501e30cd85317b60d66522594e54280d80154180cfc7899aabd62ecdd7c81` |
| `generated.pcapng` | 3,589,048 | `a6649cf6e8776d4329784bc0274d5965415e182f18ff344cc8e4f3271890cf9a` |
| `similarity.json` | 984,467 | `abe7ccecaf561b34cc3a7627c08dbb3e8d71298792c0481f980417ed8d24ac47` |

Independent final scoped specification/code-quality re-review is pending.
Plan Steps 124 and 125 remain open, and this evidence makes no clean-review or
push claim.
