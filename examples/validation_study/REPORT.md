# Validation Study report: final-source r15 evidence

The accepted final-source study is
[`2026-08-17-research-fitness-r15`](evidence/2026-08-17-research-fitness-r15/).
Its immutable manifest retains 230 evidence files, nine training run trees,
nine fixed-seed same-reference fresh-simulation records, and three independent
held-out evaluations. The retained environment records source commit
`70f8605022613375a258759ce7600657ab99f91e`, source tree
`dfe08a8c8491e1332365d78a93cfdcd7ce5f404e`, schema 2, immutable image
identities, and the locked dependency identity.

The historical Phase 7 material and earlier failed attempts remain historical
or forensic evidence only; they are not accepted results. The r15 destination
is the only accepted study bundle in this repository.

## Evidence classes

The protocol has three workloads (`short`, `streaming`, and `bursty`), three
independent training captures per workload, selection seeds 17 and 29, and
final seed 97. Training selection, natural variation, fresh simulation, and
held-out evaluation are deliberately different claims.

| Workload | Training selection mean | Natural-variation symmetric mean | Fresh simulation, seed 97 | Independent held-out |
| --- | ---: | ---: | ---: | ---: |
| short | 0.741916 | 0.642156 | 0.728739 | 0.741511 |
| streaming | 0.878265 | 0.933526 | 0.878275 | 0.871539 |
| bursty | 0.757929 | 0.765033 | 0.765705 | 0.712900 |

Training selected `markov_renewal` in all nine repeats. The frozen
training-only rule selected short r1, streaming r3, and bursty r1. These are
observed selections in three repeats, not a claim that one family is generally
superior.

The fixed-seed fresh-simulation records reuse each training reference and are
not held-out evidence. The three held-out records use a new capture per
workload, a fixed retained training model, and seed 97 without refitting or
family reselection.

## Component interpretation

Every comparison executes four equally weighted components: frame-size KS,
inter-arrival-time KS, autocorrelation, and multiscale rate. The held-out
multiscale-rate component is weak for short (0.228159) and bursty (0.119806),
while their held-out frame-size, IAT, and autocorrelation components are higher.
Streaming held-out multiscale rate is 0.806537. This disagreement is retained
as a metric result, not hidden by the arithmetic aggregate.

A retained one-factor aggregation check changes only weights, not traces,
components, diagnostics, or execution. It raises the selected aggregate by
0.048187 (short), 0.021810 (streaming), and 0.043620 (bursty). The result shows
weight sensitivity; it does not justify choosing weights after observing the
outcome.

No training run retained an invalid candidate. The recorded feasibility limits
are 25,000 packets, 40,000,000 output bytes, and 5 seconds per trial. An empty
invalid-candidate list is not evidence that all models are equally suitable.

## Variance, trace inspection, and limits

Selection-fitness sample variances are 0.000218 (short), 0.000048
(streaming), and 0.000149 (bursty). Mean retained training runtime is 7.743,
29.102, and 7.898 seconds respectively; runtime sample variances are 0.227,
1.154, and 0.194 seconds squared.

Public PCAPNG parsing found 193--3068 reference events and 70--3796 generated
events across the nine training runs. The three held-out references contain
216--3227 events and their generated traces contain 402--3246 events. These
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

Audit from a compatible descendant checkout without Docker or Internet:

```bash
uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/2026-08-17-research-fitness-r15 \
  --repository .
```

The ignored `.candidates/2026-08-17-research-fitness-r15` tree is the
publication source work copy. It is intentionally not an additional accepted
result; the tracked `evidence/2026-08-17-research-fitness-r15` destination is
the immutable accepted bundle.
