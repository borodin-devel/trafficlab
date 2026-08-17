# Validation Study report: final-source r19 evidence

The accepted final-source study is
[`2026-08-17-research-fitness-r19`](evidence/2026-08-17-research-fitness-r19/).
Its immutable manifest retains 230 evidence files, nine training run trees,
nine fixed-seed same-reference fresh-simulation records, and three independent
held-out evaluations. The retained environment records source commit
`f5da0f2960bf501e9db5112fa6bfaee64b8c6b37`, source tree
`7d691bdc2d578897073d9ffaf262afe09ec2b000`, schema 2, immutable image
identities, and the locked dependency identity.

Historical Phase 7 material and earlier failed attempts are historical or
forensic evidence only. The r19 destination is the only accepted study bundle
in this repository.

## Evidence classes

The protocol has three workloads (`short`, `streaming`, and `bursty`), three
independent training captures per workload, selection seeds 17 and 29, and
final seed 97. Training selection, natural variation, fresh simulation, and
held-out evaluation are deliberately different claims.

| Workload | Training selection mean | Natural-variation symmetric mean | Fresh simulation, seed 97 | Independent held-out |
| --- | ---: | ---: | ---: | ---: |
| short | 0.738029 | 0.745447 | 0.720497 | 0.663603 |
| streaming | 0.812301 | 0.729264 | 0.806017 | 0.875836 |
| bursty | 0.753514 | 0.673335 | 0.752841 | 0.782398 |

All short and bursty training repetitions selected `markov_renewal`; streaming
selected `markov_renewal` twice and `poisson_empirical` once. The frozen
training-only rule selected short r3 (`markov_renewal`), streaming r2
(`poisson_empirical`), and bursty r3 (`markov_renewal`). These are observed
selections in three repeats, not a claim that any family is generally superior.

The fixed-seed fresh-simulation records reuse each training reference and are
not held-out evidence. The three held-out records use a new capture per
workload, a fixed retained training model, and seed 97 without refitting or
family reselection.

## Component interpretation

Every comparison executes four equally weighted components: frame-size KS,
inter-arrival-time KS, autocorrelation, and multiscale rate. Held-out component
scores are:

| Workload | Autocorrelation | Frame-size KS | IAT KS | Multiscale rate |
| --- | ---: | ---: | ---: | ---: |
| short | 0.985804 | 0.936118 | 0.725750 | 0.006738 |
| streaming | 0.876317 | 0.944138 | 0.858708 | 0.824182 |
| bursty | 0.929878 | 0.942974 | 0.921246 | 0.335494 |

The held-out multiscale-rate component is extremely weak for short and lower
for bursty, while their frame-size, IAT, and autocorrelation components are
higher. Streaming retains a higher multiscale-rate score. This metric
disagreement is retained as a result, not hidden by the arithmetic aggregate.

A retained one-factor aggregation check changes only weights, not traces,
components, diagnostics, or execution. It raises the selected aggregate by
0.050483 (short), 0.024854 (streaming), and 0.037633 (bursty). The result shows
weight sensitivity; it does not justify choosing weights after observing the
outcome.

No training run retained an invalid candidate. The recorded feasibility limits
are 25,000 packets, 40,000,000 output bytes, and 5 seconds per trial. An empty
invalid-candidate list is not evidence that all models are equally suitable.

## Variance, trace inspection, and limits

Selection-fitness sample variances are 0.000018 (short), 0.006962
(streaming), and 0.000841 (bursty). Mean retained training runtime is 9.494,
26.924, and 9.229 seconds respectively; runtime sample variances are 1.590,
2.993, and 0.223 seconds squared.

Public PCAPNG parsing found 210--2238 reference events and 113--2963 generated
events across the nine training runs. The three held-out references contain
222--2177 events and their generated traces contain 88--2767 events. These
are finite observations from one approved HTTPS object and three traffic
shapes. They do not establish behavior for unseen programs, endpoints, hosts,
network conditions, capture tools, time periods, traffic families, or model
classes beyond the three retained classical families. Three training repeats and
one held-out capture per workload are too small to establish external
generalization. The component metrics are descriptive diagnostics of these
traces, not a universal or calibrated fidelity measure.

## Reproduction and audit

`index.json` binds each retained path to owner and lineage. `manifest.json`
records the canonical path, size, SHA-256, owner, and lineage for each retained
file. Portable/realized configuration pairs are under `configs/`; the training,
fresh-simulation, and held-out records identify their exact input and output
content identities.

Audit from a no-hardlink clone checked out at the recorded source commit after
copying the accepted bundle into its matching evidence path. Docker and network
access are not required:

```bash
UV_OFFLINE=1 scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/2026-08-17-research-fitness-r19 \
  --repository .
```

The transient source candidate was audited before exclusive publication. It is
not an additional accepted result; the tracked
`evidence/2026-08-17-research-fitness-r19` destination is the immutable
accepted bundle.
