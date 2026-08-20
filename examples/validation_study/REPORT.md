# Validation Study report: scientific-stack adoption r4

The accepted scientific-stack study is
[`2026-08-20-stack-adoption-r4`](evidence/2026-08-20-stack-adoption-r4/).
Its immutable manifest retains 231 evidence paths, excluding only the manifest
itself, across nine training run trees, nine fixed-seed same-reference fresh
simulations, and three independent held-out evaluations. The environment binds
source commit `a71d74b7b2dc27cea0a9eb00c375e510a0d7acbf`, tree
`0e11527885623448fb03604dbf9f6bb2b4634dfd`, schema 3, CPython 3.12.3,
the locked dependency identity, and exact target/capture images.

Earlier Task 13 attempts are not this result. r1 failed the clean-tree check;
r2 completed collection but failed audit because Hypothesis created checkout
cache state; r3 passed its then-current audit but omitted required bootstrap
records and was moved intact to ignored preserved-nonfinal storage. The
producer, strict schema, and independent auditor were corrected before r4.
The older checked r21 bundle remains unchanged as an accepted predecessor.

## Evidence classes

The protocol has three workloads, three independent training captures per
workload, selection seeds 17 and 29, and final seed 97. Training selection,
natural variation, same-reference fresh simulation, and held-out evaluation
remain different claims.

| Workload | Training selection mean | Natural-variation symmetric mean | Fresh simulation, seed 97 | Independent held-out |
| --- | ---: | ---: | ---: | ---: |
| short | 0.743596 | 0.756048 | 0.736582 | 0.752091 |
| streaming | 0.888097 | 0.953496 | 0.890632 | 0.897818 |
| bursty | 0.772237 | 0.735295 | 0.760013 | 0.751923 |

All short and bursty training repetitions selected `markov_renewal`; all
streaming repetitions selected `poisson_empirical`. The frozen training-only
rule selected short r3, streaming r3, and bursty r1. These are observations from
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
| short | 0.906767 | 0.974611 | 0.922261 | 0.204726 |
| streaming | 0.946967 | 0.962923 | 0.870919 | 0.810463 |
| bursty | 0.922875 | 0.965295 | 0.913718 | 0.205803 |

The multiscale component remains materially lower for short and bursty than the
other three components. That disagreement is retained rather than hidden by the
aggregate. The controlled one-factor weight analysis changes only aggregation:
short `0.780367→0.822881`, streaming `0.896630→0.913858`, and bursty
`0.778054→0.818179`. It does not justify choosing weights after observing the
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
| short | 7.723709 | [7.309674, 8.362012] | 0.743596 | [0.719923, 0.759720] |
| streaming | 26.488855 | [26.306931, 26.730307] | 0.888097 | [0.877200, 0.894601] |
| bursty | 7.408110 | [7.223545, 7.593625] | 0.772237 | [0.749484, 0.799640] |

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
matching relative path as regular files. It contained no symlink or file with a
hard-link count above one, matched the publisher bytes, and passed:

```bash
UV_OFFLINE=1 scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/2026-08-20-stack-adoption-r4 \
  --repository .
```

The audit reconstructs every trace, model, fixed-seed generation, comparison,
natural-variation value, bootstrap interval, report input, lineage edge, and
manifest identity without Docker or network access.
