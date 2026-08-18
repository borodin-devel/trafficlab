# Validation Study report: final-source r21 evidence

The accepted final-source study is
[`2026-08-18-research-fitness-r21`](evidence/2026-08-18-research-fitness-r21/).
Its immutable manifest retains 231 evidence paths, excluding only the manifest
itself, across nine training run trees, nine fixed-seed same-reference
fresh-simulation records, and three independent held-out evaluations. The
retained environment records source commit
`ca2522dcfae5b39a44355d9df5329744847b7136`, source tree
`4a743a7d8a48cd8ad34d8e96de48d53d4e7d789c`, schema-3 index provenance,
immutable image identities, and the locked dependency identity.

`2026-08-18-research-fitness-r20` is a consumed failed attempt, not a result: a
mandatory natural-variation comparison had insufficient aligned events and the
collector stopped before held-out/report/publication artifacts. Historical
real-program validation-study material and earlier failed attempts are historical
or forensic evidence only. The r21 destination is the accepted study bundle in
this repository with complete retained real-program validation evidence.

## Evidence classes

The protocol has three workloads (`short`, `streaming`, and `bursty`), three
independent training captures per workload, selection seeds 17 and 29, and final
seed 97. Training selection, natural variation, fresh simulation, and held-out
evaluation are deliberately different claims.

| Workload | Training selection mean | Natural-variation symmetric mean | Fresh simulation, seed 97 | Independent held-out |
| --- | ---: | ---: | ---: | ---: |
| short | 0.733800 | 0.633565 | 0.723225 | 0.712950 |
| streaming | 0.877848 | 0.887415 | 0.878295 | 0.889224 |
| bursty | 0.756479 | 0.787983 | 0.722909 | 0.737522 |

All short and bursty training repetitions selected `markov_renewal`; streaming
selected `poisson_empirical` twice and `markov_renewal` once. The frozen
training-only rule selected short r1 (`markov_renewal`), streaming r2
(`poisson_empirical`), and bursty r3 (`markov_renewal`). These are observed
selections in three repeats, not a claim that any family is generally superior.

The fixed-seed fresh-simulation records reuse each training reference and are
not held-out evidence. The three held-out records use a new capture per workload,
a fixed retained training model, and seed 97 without refitting or family
reselection.

## Component interpretation

Every comparison executes four equally weighted components: frame-size KS,
inter-arrival-time KS, autocorrelation, and multiscale rate. Held-out component
scores are:

| Workload | Autocorrelation | Frame-size KS | IAT KS | Multiscale rate |
| --- | ---: | ---: | ---: | ---: |
| short | 0.958889 | 0.970496 | 0.693169 | 0.229244 |
| streaming | 0.907000 | 0.959365 | 0.816189 | 0.874341 |
| bursty | 0.982553 | 0.928378 | 0.879921 | 0.159237 |

The multiscale-rate component is low for short and bursty in both fresh and
held-out comparisons: fresh values are 0.137112 and 0.108539, while held-out
values are 0.229244 and 0.159237. Streaming retains higher multiscale values
(0.788927 fresh and 0.874341 held-out). This metric disagreement is retained as
a result, not hidden by the arithmetic aggregate.

A retained one-factor aggregation check changes only weights, not traces,
components, diagnostics, or execution. It changes the selected aggregate from
0.756955 to 0.801070 (short), 0.894715 to 0.914756 (streaming), and 0.737200 to
0.782905 (bursty). The result shows weight sensitivity; it does not justify
choosing weights after observing the outcome.

No training run retained an invalid candidate. The recorded feasibility limits
are 25,000 packets, 40,000,000 output bytes, and 5 seconds per trial. An empty
invalid-candidate list is not evidence that all models are equally suitable.

## Variance, trace inspection, and limits

Selection-fitness sample variances are 0.000518 (short), 0.000406
(streaming), and 0.000332 (bursty). Mean retained training runtimes are 9.195,
26.081, and 8.645 seconds respectively; runtime sample variances are 0.613,
1.962, and 0.153 seconds squared.

The retained reference and generated PCAPNG paths parse under the same audit
and comparison settings used for the report. They are finite observations from
one approved HTTPS object and three traffic shapes. They do not establish
behavior for unseen programs, endpoints, hosts, network conditions, capture
tools, time periods, traffic families, or model classes beyond the three
retained classical families. Three training repeats and one held-out capture per
workload are too small to establish external generalization. The component
metrics are descriptive diagnostics of these traces, not a universal or
calibrated fidelity measure.

## Reproduction and audit

`index.json` binds each retained path to owner and lineage. `manifest.json`
records the canonical path, size, SHA-256, owner, and lineage for each retained
file. Portable/realized configuration pairs are under `configs/`; the training,
fresh-simulation, and held-out records identify their exact input and output
content identities. `lifecycle.json` binds the study ID, successful capture
cleanup records, distinct capture projects, and phase image cleanup before
publication.

Audit from a no-hardlink clone checked out at the recorded source commit after
copying the accepted bundle into its matching evidence path. Docker and network
access are not required:

```bash
UV_OFFLINE=1 scripts/run_bounded.sh \
  --memory-high 6G --memory-max 8G --swap-max 1G \
  --wall-time 20m --kill-after 10s -- \
  uv run --locked --offline python scripts/audit_validation_study.py \
  examples/validation_study/evidence/2026-08-18-research-fitness-r21 \
  --repository .
```

The transient source candidate was audited before exclusive publication. It is
not an additional accepted result; the tracked
`evidence/2026-08-18-research-fitness-r21` destination is the immutable
accepted bundle.
