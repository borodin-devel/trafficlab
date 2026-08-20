# Validation Study report: scientific-stack adoption r6

The accepted scientific-stack study is
[`2026-08-20-stack-adoption-r6`](evidence/2026-08-20-stack-adoption-r6/).
Its immutable manifest retains 231 evidence paths, excluding only the manifest
itself, across nine training run trees, nine fixed-seed same-reference fresh
simulations, and three independent held-out evaluations. The environment binds
source commit `bc7e0e99560d02091d363cb084dcb7e530ed5711`, tree
`9d4ad17c1621e532e2e3a40a086a329fb1e41690`, schema 3, CPython 3.12.3,
the locked dependency identity, and exact target/capture images.

Earlier Task 13 attempts are not this result. r1 failed the clean-tree check;
r2 completed collection but failed audit because Hypothesis created checkout
cache state; r3 passed its then-current audit but omitted required bootstrap
records and was moved intact to ignored preserved-nonfinal storage. The
producer, strict schema, and independent auditor were corrected before r4.
r5 then failed before Docker work because a checkout-local Hypothesis cache was
present; its ignored failure record remains recoverable and the ID was not
reused. r6 started from a clean source with Hypothesis storage outside the
checkout and is the accepted replacement.
The older checked r21 bundle remains unchanged as an accepted predecessor.

## Evidence classes

The protocol has three workloads, three independent training captures per
workload, selection seeds 17 and 29, and final seed 97. Training selection,
natural variation, same-reference fresh simulation, and held-out evaluation
remain different claims.

| Workload | Training selection mean | Natural-variation symmetric mean | Fresh simulation, seed 97 | Independent held-out |
| --- | ---: | ---: | ---: | ---: |
| short | 0.713227 | 0.712788 | 0.719584 | 0.686495 |
| streaming | 0.879773 | 0.934302 | 0.881480 | 0.883993 |
| bursty | 0.783881 | 0.773375 | 0.772643 | 0.764534 |

All short and bursty training repetitions selected `markov_renewal`; all
streaming repetitions selected `poisson_empirical`. The frozen training-only
rule selected short r2, streaming r1, and bursty r3. These are observations from
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
| short | 0.918047 | 0.971899 | 0.783028 | 0.073006 |
| streaming | 0.933778 | 0.990225 | 0.839748 | 0.772221 |
| bursty | 0.949853 | 0.987490 | 0.888799 | 0.231993 |

The multiscale component remains materially lower for short and bursty than the
other three components. That disagreement is retained rather than hidden by the
aggregate. The controlled one-factor weight analysis changes only aggregation:
short `0.751660→0.798004`, streaming `0.902308→0.918283`, and bursty
`0.792372→0.831034`. It does not justify choosing weights after observing the
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
| short | 7.513824 | [7.156744, 7.841511] | 0.713227 | [0.691966, 0.739639] |
| streaming | 25.853815 | [25.552728, 26.154076] | 0.879773 | [0.866471, 0.898722] |
| bursty | 7.565528 | [7.514585, 7.628014] | 0.783881 | [0.768268, 0.812627] |

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
  examples/validation_study/evidence/2026-08-20-stack-adoption-r6 \
  --repository .
```

The audit reconstructs every trace, model, fixed-seed generation, comparison,
natural-variation value, bootstrap interval, report input, lineage edge, and
manifest identity without Docker or network access.
