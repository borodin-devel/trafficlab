# Validation Study report: final-source r18 evidence

The accepted final-source study is
[`2026-08-17-research-fitness-r18`](evidence/2026-08-17-research-fitness-r18/).
Its immutable manifest retains 230 evidence files, nine training run trees,
nine fixed-seed same-reference fresh-simulation records, and three independent
held-out evaluations. The retained environment records source commit
`2f1537b0f0339bbf761a6c04a33035ee8fd26e8b`, source tree
`05baca066d50f6b2c5dfda60e60433bb4353d495`, schema 2, immutable image
identities, and the locked dependency identity.

Historical Phase 7 material and earlier failed attempts are historical or
forensic evidence only. The r18 destination is the only accepted study bundle
in this repository.

## Evidence classes

The protocol has three workloads (`short`, `streaming`, and `bursty`), three
independent training captures per workload, selection seeds 17 and 29, and
final seed 97. Training selection, natural variation, fresh simulation, and
held-out evaluation are deliberately different claims.

| Workload | Training selection mean | Natural-variation symmetric mean | Fresh simulation, seed 97 | Independent held-out |
| --- | ---: | ---: | ---: | ---: |
| short | 0.733597 | 0.714764 | 0.738244 | 0.718518 |
| streaming | 0.895940 | 0.951742 | 0.898700 | 0.864261 |
| bursty | 0.765113 | 0.791096 | 0.761779 | 0.771403 |

All short and bursty training repetitions selected `markov_renewal`; all
streaming repetitions selected `poisson_empirical`. The frozen training-only
rule selected short r3, streaming r2, and bursty r2. These are observed
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
| short | 0.928857 | 0.944730 | 0.850995 | 0.149489 |
| streaming | 0.909212 | 0.946738 | 0.814616 | 0.786476 |
| bursty | 0.956064 | 0.986342 | 0.914906 | 0.228302 |

The held-out multiscale-rate component is weak for short and bursty, while
their frame-size, IAT, and autocorrelation components are higher. Streaming
retains a higher multiscale-rate score. This metric disagreement is retained as
a result, not hidden by the arithmetic aggregate.

A retained one-factor aggregation check changes only weights, not traces,
components, diagnostics, or execution. It raises the selected aggregate by
0.046055 (short), 0.017411 (streaming), and 0.042242 (bursty). The result shows
weight sensitivity; it does not justify choosing weights after observing the
outcome.

No training run retained an invalid candidate. The recorded feasibility limits
are 25,000 packets, 40,000,000 output bytes, and 5 seconds per trial. An empty
invalid-candidate list is not evidence that all models are equally suitable.

## Variance, trace inspection, and limits

Selection-fitness sample variances are 0.000224 (short), 0.000036
(streaming), and 0.000475 (bursty). Mean retained training runtime is 9.130,
26.072, and 9.017 seconds respectively; runtime sample variances are 0.270,
0.010, and 0.005 seconds squared.

Public PCAPNG parsing found 214--2230 reference events and 193--2743 generated
events across the nine training runs. The three held-out references contain
232--2110 events and their generated traces contain 389--2778 events. These
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
UV_OFFLINE=1 uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/2026-08-17-research-fitness-r18 \
  --repository .
```

The transient source candidate was audited before exclusive publication. It is
not an additional accepted result; the tracked
`evidence/2026-08-17-research-fitness-r18` destination is the immutable
accepted bundle.
