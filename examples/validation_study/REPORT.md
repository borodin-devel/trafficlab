# Validation Study report: production Scapy r3

The current accepted study is
[`2026-08-20-scapy-production-r3`](evidence/2026-08-20-scapy-production-r3/).
Its immutable manifest retains 231 evidence paths, excluding only the manifest
itself, across nine training run trees, nine fixed-seed same-reference fresh
simulations, and three independent held-out evaluations. The environment binds
source commit `7ba2764dd5810cd061fc42bcbc46dfcfda2b6103`, tree
`80d3a7ab0666abd58ff9798274a9026ef2439dd7`, scientific schema 4,
CPython 3.12.3, Scapy 2.7.0 through the checked lock, and exact target/capture
images.

The first production-Scapy attempt, r1, failed the clean-tree prerequisite
because a checkout-local Hypothesis cache was present. Its ignored failure
record was preserved, its ID was not reused, and the cache was moved intact to
external scratch. r2 started from clean source with Hypothesis storage outside
the checkout and remains an immutable accepted predecessor. Final review then
required stricter reader, publication, and diagnostic boundaries, so r3 was
collected from the corrected source and is the accepted successor. The older
checked r6 and r21 bundles also remain byte-unchanged accepted predecessors.

## Evidence classes

The protocol has three workloads, three independent training captures per
workload, selection seeds 17 and 29, and final seed 97. Training selection,
natural variation, same-reference fresh simulation, and held-out evaluation
remain different claims.

| Workload | Training selection mean | Natural-variation symmetric mean | Fresh simulation, seed 97 | Independent held-out |
| --- | ---: | ---: | ---: | ---: |
| short | 0.739651 | 0.688785 | 0.740615 | 0.706114 |
| streaming | 0.887092 | 0.948433 | 0.888536 | 0.870293 |
| bursty | 0.765939 | 0.857618 | 0.757930 | 0.752445 |

All short and bursty training repetitions selected `markov_renewal`; all
streaming repetitions selected `poisson_empirical`. The frozen training-only
rule selected short r3, streaming r1, and bursty r3. These are observations from
three repeats, not evidence that either family is generally superior.

The fresh-simulation records reuse each training reference and are not held-out
evidence. Held-out records use a new capture, the fixed selected training model,
and seed 97 without refitting, family reselection, seed choice, or protocol
amendment.

## Component interpretation

Every comparison executes four equally weighted components. Held-out component
scores are:

| Workload | Autocorrelation | Frame-size KS | IAT KS | Multiscale rate |
| --- | ---: | ---: | ---: | ---: |
| short | 0.995260 | 0.981830 | 0.617282 | 0.230085 |
| streaming | 0.893911 | 0.972447 | 0.849927 | 0.764888 |
| bursty | 0.952784 | 0.991038 | 0.787906 | 0.278052 |

The multiscale component remains materially lower for short and bursty than the
other three components. That disagreement is retained rather than hidden by the
aggregate. The controlled one-factor weight analysis changes only aggregation:
short `0.752345→0.800417`, streaming `0.893827→0.911514`, and bursty
`0.777188→0.819667`. It does not justify choosing weights after observing the
result.

No training run retained an invalid candidate. Trial limits remain 25,000
packets, 40,000,000 bytes, and 5 seconds. An empty invalid-candidate list is not
evidence that all chromosomes or families are generally feasible.

## Bootstrap and finite-sample uncertainty

Training runtime and selection-fitness means retain independently recomputed
95% percentile-bootstrap intervals using 10,000 resamples and
`Generator(PCG64(20260819))`. Full initial generator state and metadata are in
`report_inputs.json`.

| Workload | Runtime mean | Runtime 95% interval | Fitness mean | Fitness 95% interval |
| --- | ---: | ---: | ---: | ---: |
| short | 8.172837 | [7.736646, 8.884956] | 0.739651 | [0.726606, 0.755222] |
| streaming | 27.975754 | [27.665757, 28.306091] | 0.887092 | [0.875326, 0.893137] |
| bursty | 8.065570 | [7.972743, 8.246730] | 0.765939 | [0.758999, 0.771565] |

These intervals describe three retained observations; they are not calibrated
hypothesis tests and do not overcome the small sample.

## Limits

The references are finite observations from one credential-free HTTPS object,
three traffic shapes, one host, and one time period. Three training captures and
one held-out capture per workload cannot establish external generalization.
The study does not establish behavior for unseen programs, endpoints, hosts,
networks, traffic families, or model classes. Similarity components are
descriptive diagnostics, not universal fidelity or causal measures.

## Reproduction and audit

The source candidate passed the standalone offline audit before exclusive
publication. A separate `git clone --no-local --no-hardlinks --no-checkout` was
detached at the recorded source commit; the accepted bundle was copied into the
matching relative path as regular files. It contained no Git object alternate,
symlink, or file with a hard-link count above one, matched the publisher bytes,
and passed:

```bash
UV_OFFLINE=1 scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/2026-08-20-scapy-production-r3 \
  --repository .
```

The audit reconstructs every trace through the production Scapy boundary,
model, fixed-seed generation, comparison, natural-variation value, bootstrap
interval, report input, lineage edge, and manifest identity without Docker or
network access.
