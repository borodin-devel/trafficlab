# Validation Study report: corrected r13 evidence

The accepted corrected-semantics study is
[`2026-08-17-research-fitness-r13`](evidence/2026-08-17-research-fitness-r13/).
Its immutable manifest retains 230 evidence files, nine training run trees,
nine fixed-seed fresh-simulation records, and three independent held-out
evaluations. The retained environment records source commit
`a93a9d598d831a8817dff7a597cb569dd3d80445`, source tree
`d521d0f8d00e7f7da9c8c68a87e5299fef88849c`, schema 2, immutable image
identities, and the locked dependency identity.

The historical Phase 7 material remains historical consistency evidence only;
it is not an accepted result. The r13 destination is the only accepted study
bundle in this repository.

## Evidence classes

The protocol has three workloads (`short`, `streaming`, and `bursty`), three
independent training captures per workload, selection seeds 17 and 29, and
final seed 97. Training selection, natural variation, fresh simulation, and
held-out evaluation are deliberately different claims.

| Workload | Training selection mean | Natural-variation symmetric mean | Fresh simulation, seed 97 | Independent held-out |
| --- | ---: | ---: | ---: | ---: |
| short | 0.723675 | 0.677203 | 0.711909 | 0.649095 |
| streaming | 0.871127 | 0.886072 | 0.864247 | 0.845498 |
| bursty | 0.764886 | 0.761388 | 0.744659 | 0.743594 |

Training selected `markov_renewal` twice and `poisson_empirical` once for
short; all three streaming and bursty selections were `markov_renewal`.
These are observed selections in three repeats, not a claim that one family is
generally superior.

The fixed-seed fresh-simulation records reuse each training reference and are
not held-out evidence. The three held-out records use a new capture per
workload, a fixed retained training model, and seed 97 without refitting or
family reselection.

## Component interpretation

Every comparison executes four equally weighted components: frame-size KS,
inter-arrival-time KS, autocorrelation, and multiscale rate. The held-out
multiscale-rate component is weak for short (0.098682) and bursty (0.124532),
while their held-out frame-size, IAT, and autocorrelation components are higher.
Streaming held-out multiscale rate is 0.630235. This disagreement is retained
as a metric result, not hidden by the arithmetic aggregate.

A retained one-factor aggregation check changes only weights, not traces,
components, diagnostics, or execution. It raises the selected aggregate by
0.040929 (short), 0.026141 (streaming), and 0.041212 (bursty). The result shows
weight sensitivity; it does not justify choosing weights after observing the
outcome.

No training run retained an invalid candidate. The recorded feasibility limits
are 25,000 packets, 40,000,000 output bytes, and 5 seconds per trial. An empty
invalid-candidate list is not evidence that all models are equally suitable.

## Variance, trace inspection, and limits

Selection-fitness sample variances are 0.000061 (short), 0.000127
(streaming), and 0.000093 (bursty). Mean retained training runtime is 7.835,
29.065, and 7.643 seconds respectively; runtime sample variances are 0.361,
5.355, and 0.039 seconds squared.

Public PCAPNG parsing found 213--3148 reference events and 156--3868 generated
events across the nine training runs. The three held-out references contain
219--2914 events and their generated traces contain 156--3896 events. These
are finite observations from one approved HTTPS object and three traffic
shapes. They do not establish behavior for unseen programs, endpoints, hosts,
network conditions, capture tools, time periods, traffic families, or model
classes beyond the three retained classical families. The component metrics are
diagnostics of these traces, not a universal fidelity measure.

## Reproduction and audit

`index.json` binds each retained path to owner and lineage. `manifest.json`
records the canonical path, size, SHA-256, owner, and lineage for each retained
file. Portable/realized configuration pairs are under `configs/`; the training,
fresh-simulation, and held-out records identify their exact input and output
content identities.

Audit from a compatible descendant checkout without Docker or Internet:

```bash
uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/2026-08-17-research-fitness-r13 \
  --repository .
```

The ignored `.candidates/2026-08-17-research-fitness-r13` tree is the
publication source work copy. It is intentionally not an additional accepted
result; the tracked `evidence/2026-08-17-research-fitness-r13` destination is
the immutable accepted bundle.
