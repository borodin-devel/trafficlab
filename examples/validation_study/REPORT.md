# Validation Study report: production Scapy r2

The current accepted study is
[`2026-08-20-scapy-production-r2`](evidence/2026-08-20-scapy-production-r2/).
Its immutable manifest retains 231 evidence paths, excluding only the manifest
itself, across nine training run trees, nine fixed-seed same-reference fresh
simulations, and three independent held-out evaluations. The environment binds
source commit `ab10cb15e90a9e306f941ebacf10247821e458b3`, tree
`43ac13b8e510d447bc67b4ed559b7337f8f46a2d`, scientific schema 4,
CPython 3.12.3, Scapy 2.7.0 through the checked lock, and exact target/capture
images.

The first production-Scapy attempt, r1, failed the clean-tree prerequisite
because a checkout-local Hypothesis cache was present. Its ignored failure
record was preserved, its ID was not reused, and the cache was moved intact to
external scratch. r2 started from the same clean source with Hypothesis storage
outside the checkout and is the accepted successor. The older checked r6 and
r21 bundles remain byte-unchanged accepted predecessors.

## Evidence classes

The protocol has three workloads, three independent training captures per
workload, selection seeds 17 and 29, and final seed 97. Training selection,
natural variation, same-reference fresh simulation, and held-out evaluation
remain different claims.

| Workload | Training selection mean | Natural-variation symmetric mean | Fresh simulation, seed 97 | Independent held-out |
| --- | ---: | ---: | ---: | ---: |
| short | 0.730874 | 0.678735 | 0.753166 | 0.709721 |
| streaming | 0.880693 | 0.910030 | 0.880240 | 0.866157 |
| bursty | 0.744606 | 0.758155 | 0.667467 | 0.771568 |

All short and bursty training repetitions selected `markov_renewal`; all
streaming repetitions selected `poisson_empirical`. The frozen training-only
rule selected short r2, streaming r3, and bursty r3. These are observations from
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
| short | 0.854426 | 0.969318 | 0.798373 | 0.216765 |
| streaming | 0.959538 | 0.971604 | 0.813531 | 0.719953 |
| bursty | 0.936196 | 0.948653 | 0.931021 | 0.270401 |

The multiscale component remains materially lower for short and bursty than the
other three components. That disagreement is retained rather than hidden by the
aggregate. The controlled one-factor weight analysis changes only aggregation:
short `0.759553→0.804722`, streaming `0.888780→0.907827`, and bursty
`0.788289→0.824466`. It does not justify choosing weights after observing the
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
| short | 7.258183 | [7.041288, 7.405000] | 0.730874 | [0.711581, 0.747871] |
| streaming | 24.758951 | [24.310665, 25.014102] | 0.880693 | [0.870808, 0.892416] |
| bursty | 7.188188 | [7.035140, 7.394529] | 0.744606 | [0.711395, 0.765062] |

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
  examples/validation_study/evidence/2026-08-20-scapy-production-r2 \
  --repository .
```

The audit reconstructs every trace through the production Scapy boundary,
model, fixed-seed generation, comparison, natural-variation value, bootstrap
interval, report input, lineage edge, and manifest identity without Docker or
network access.
