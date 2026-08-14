# Phase 7 validation study

## Question, scope, environment, and protocol

This study asks whether Trafficlab's complete capture, fit, generate, and compare pipeline produces useful,
interpretable results for three different real HTTPS traffic shapes. It is a small validation study, not a
benchmark or a claim about all network programs.

The accepted study is `phase7-20260814-ovh-r3`, run from evidence commit
`976dcd6ba8bfb4df4894e79263fb8b75dc426ad0` on CPython 3.12.3, Trafficlab 0.1.0, Docker Engine 29.7.2, and Docker
Compose 5.4.0 under WSL2 Linux 6.6.87.2. The target image was
`sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b`; the capture image was
`sha256:db3e68a72c69697af92030da5713b3d5843dc0934835ec0c157a2d3456c309b4`.

The credential-free endpoint was `https://sbg.proof.ovh.net/files/10Mb.dat`, an OVHcloud network-test object.
Prerequisites proved HTTP 206, `Content-Range: bytes 0-0/10485760`, a one-byte body, no redirect, Docker 18/18,
Internet 1/1, and clean container removal. The prerequisite SHA-256 was
`5b8100f85aff99cba5de05e2c12d7b82ff71cc75824c4165acb7d6f44b50829f`; the result SHA-256 was
`5803a6c4c674182caaa0b450211ad4e19d8957d64d25904981a00790bb8ec655`.

The balanced order was short, streaming, bursty; streaming, bursty, short; bursty, short, streaming. The streaming
transfer was rate-limited to `256K`; it was not exactly paced. Every run used population 6, generation 2, master
seed 73, selection seeds 17 and 29, and fresh held-out seed 97. The ten fresh run IDs were:

- `01-short-r1`, `02-streaming-r1`, `03-bursty-r1`;
- `04-streaming-r2`, `05-bursty-r2`, `06-short-r2`;
- `07-bursty-r3`, `08-short-r3`, `09-streaming-r3`;
- `10-streaming-r2-reproduction`.

The invalidated pilot `phase7-20260814-ovh` observed `W = 0.7874600887298584` and stopped because both locked MMPP
candidates were invalid before family comparison. The Phase-7-only `lambda0` lower bound was amended from `0.01`
to `10.0`, preserving a decade of low-regime rates while making the family evaluable. No pilot score or winner was
accepted, analyzed, or used to choose that bound; only the family-wide precondition failure and observed window
were used. The pilot was excluded and retained locally. A later complete restart,
`phase7-20260814-ovh-r2`, was also excluded after a transient curl status 28 at primary 7. The accepted study reran
all nine primaries and the reproduction under one fresh ID.

## Natural variation

The symmetric reference-to-reference scores below compare each pair of repeats in both directions. Columns are
aggregate, autocorrelation, frame-size KS, IAT KS, and multiscale rate.

| Workload | Pair | Aggregate | ACF | Frame | IAT | Multiscale |
|---|---:|---:|---:|---:|---:|---:|
| short | 1–2 | 0.813587 | 0.972278 | 0.947257 | 0.929618 | 0.405195 |
| short | 1–3 | 0.581103 | 0.907512 | 0.740671 | 0.670281 | 0.005948 |
| short | 2–3 | 0.560406 | 0.890444 | 0.698065 | 0.648828 | 0.004287 |
| streaming | 1–2 | 0.882351 | 0.991047 | 0.920611 | 0.898299 | 0.719446 |
| streaming | 1–3 | 0.855619 | 0.946153 | 0.868615 | 0.866878 | 0.740831 |
| streaming | 2–3 | 0.860429 | 0.955535 | 0.941797 | 0.896926 | 0.647459 |
| bursty | 1–2 | 0.586506 | 0.861429 | 0.762721 | 0.709974 | 0.011899 |
| bursty | 1–3 | 0.570453 | 0.854292 | 0.671918 | 0.752430 | 0.003171 |
| bursty | 2–3 | 0.788144 | 0.986151 | 0.972464 | 0.946514 | 0.247448 |

Reference descriptors also varied. Short captures had 171–191 packets and windows of
0.47197985649108887–0.912630558013916 seconds. Streaming had 846–1048 packets and much steadier windows of
18.026829481124878–18.417349815368652 seconds. Bursty had 236–255 packets and windows of
0.448439359664917–0.9194221496582031 seconds. Streaming therefore supplies the strongest natural baseline;
short and bursty local-rate placement is intrinsically unstable in this three-repeat sample.

## Family champions

These are selection results, not held-out generalization. Each cell gives the three-run mean; `SD` is the sample
standard deviation of selection fitness. Component order is ACF, frame-size KS, IAT KS, multiscale rate.

| Workload | Family | Fitness | SD | Component means |
|---|---|---:|---:|---|
| short | Markov Renewal | 0.665354 | 0.014958 | 0.938513, 0.952130, 0.682963, 0.087811 |
| short | MMPP | 0.437543 | 0.029711 | 0.718104, 0.898913, 0.105960, 0.027195 |
| short | Poisson empirical | 0.531339 | 0.011874 | 0.813927, 0.983600, 0.214743, 0.113084 |
| streaming | Markov Renewal | 0.818089 | 0.008019 | 0.981416, 0.975349, 0.821260, 0.494329 |
| streaming | MMPP | 0.684458 | 0.011883 | 0.883197, 0.986018, 0.521140, 0.347477 |
| streaming | Poisson empirical | 0.765105 | 0.025520 | 0.941716, 0.980450, 0.455336, 0.682917 |
| bursty | Markov Renewal | 0.682348 | 0.017794 | 0.889816, 0.962049, 0.721503, 0.156022 |
| bursty | MMPP | 0.442025 | 0.027641 | 0.800904, 0.833256, 0.117286, 0.016656 |
| bursty | Poisson empirical | 0.588334 | 0.023329 | 0.830361, 0.981492, 0.372103, 0.169378 |

Markov Renewal won all three repeats of every workload: nine wins, versus zero for MMPP and Poisson empirical.
Its selection-fitness ranges were 0.029594 for short, 0.015164 for streaming, and 0.031464 for bursty. The winner
was therefore stable by family and comparatively stable by selection score, especially on streaming.

## Held-out, published, and runtime

Held-out values use fresh seed 97. Published scores reparse the stored PCAPNG; their tiny difference from held-out
values is the expected timestamp quantization boundary, not a different random sequence.

| Workload | Held-out mean ± SD | Published mean ± SD | Published component means | Runtime mean (range), s |
|---|---:|---:|---|---:|
| short | 0.649461 ± 0.013059 | 0.649461 ± 0.013059 | 0.933862, 0.923114, 0.665335, 0.075531 | 8.653 (1.025) |
| streaming | 0.813904 ± 0.007108 | 0.813904 ± 0.007108 | 0.982618, 0.977085, 0.820740, 0.475174 | 24.983 (1.702) |
| bursty | 0.677180 ± 0.025891 | 0.677180 ± 0.025891 | 0.890196, 0.964964, 0.726031, 0.127529 | 9.685 (1.186) |

| Run | Winner | Selection | Held-out | Published | Runtime, s |
|---|---|---:|---:|---:|---:|
| `01-short-r1` | Markov Renewal | 0.678889 | 0.663327 | 0.663327 | 9.118 |
| `02-streaming-r1` | Markov Renewal | 0.827178 | 0.816018 | 0.816018 | 25.319 |
| `03-bursty-r1` | Markov Renewal | 0.661815 | 0.654318 | 0.654318 | 9.103 |
| `04-streaming-r2` | Markov Renewal | 0.815075 | 0.805979 | 0.805979 | 25.666 |
| `05-bursty-r2` | Markov Renewal | 0.691949 | 0.671927 | 0.671927 | 9.664 |
| `06-short-r2` | Markov Renewal | 0.667878 | 0.637397 | 0.637397 | 8.749 |
| `07-bursty-r3` | Markov Renewal | 0.693279 | 0.705295 | 0.705295 | 10.289 |
| `08-short-r3` | Markov Renewal | 0.649295 | 0.647658 | 0.647658 | 8.093 |
| `09-streaming-r3` | Markov Renewal | 0.812014 | 0.819716 | 0.819716 | 23.964 |

Every primary reported false reuse for capture, best model, generation, and similarity; every cleanup was verified.

## Trace diagnostics

| Run | Reference W, s | Reference packets | Generated packets | Frame | IAT | ACF | Multiscale |
|---|---:|---:|---:|---:|---:|---:|---:|
| `01-short-r1` | 0.471980 | 189 | 429 | 0.927258 | 0.628505 | 0.936782 | 0.160763 |
| `02-streaming-r1` | 18.347886 | 1048 | 1492 | 0.963423 | 0.810667 | 0.980585 | 0.509396 |
| `03-bursty-r1` | 0.919422 | 255 | 1126 | 0.985665 | 0.699048 | 0.854704 | 0.077853 |
| `04-streaming-r2` | 18.417350 | 944 | 1740 | 0.986711 | 0.805177 | 0.988964 | 0.443064 |
| `05-bursty-r2` | 0.498732 | 249 | 613 | 0.959780 | 0.699136 | 0.902607 | 0.126185 |
| `06-short-r2` | 0.478037 | 171 | 317 | 0.965337 | 0.609605 | 0.930965 | 0.043679 |
| `07-bursty-r3` | 0.448439 | 236 | 389 | 0.949447 | 0.779908 | 0.913276 | 0.178550 |
| `08-short-r3` | 0.912631 | 191 | 279 | 0.876748 | 0.757895 | 0.933839 | 0.022152 |
| `09-streaming-r3` | 18.026829 | 846 | 1965 | 0.981122 | 0.846376 | 0.978304 | 0.473062 |

Frame-size agreement is consistently high, and ACF is also strong. IAT is moderate for short/bursty and strong for
streaming. Multiscale rate is the repeated weak component. The generated trace contains more packets than its
reference in every run, which explains why matching frame marks and selected lags can coexist with weak local
directional-volume agreement. Streaming is the clearest model gap: its natural multiscale scores are
0.647459–0.740831, while its generated scores are 0.443064–0.509396 even though the other three components remain
0.805177 or higher. Short and bursty also have weak generated multiscale scores, but their own natural multiscale
scores vary from near zero to 0.405195 and 0.247448 respectively, so those workloads do not isolate model error as
cleanly.

All nine raw checks prove trial events equal final events, seed 97 is retained, the observation window is exact,
the held-out score recomputes, and reparsed events equal the defined quantized sequence.

## Saved-run reproduction

`10-streaming-r2-reproduction` was a fresh full installed-CLI run from the saved `04-streaming-r2` configuration.
Only `run.directory` changed, no artifact was seeded, the exact 20-minute guard exited 0, all four reuse flags were
false, and cleanup succeeded. Its runtime was 25.503171746997396 seconds.

The reproduction again chose Markov Renewal. Its genes differed, as expected after a fresh capture, and selection
fitness increased by 0.07922758085838422. The fresh held-out aggregate was 0.8982635038655866 and the published
aggregate was 0.8982635039237588. Relative to source repeat 2, the held-out aggregate delta was
+0.09228442601729903; component deltas were ACF -0.03742625827528312, frame-size KS -0.009959983298520325, IAT KS
+0.09674703785675864, and multiscale rate +0.31977690778624124. The two fresh references had aggregate similarity
0.8405749723408512, confirming that this is reproducibility of the locked procedure rather than byte-identical
traffic.

Post-CLI reconstruction independently proved exact raw trial/final equality, 1406 events before and after
quantized PCAPNG reparse, the same `W = 14.687379598617554`, and exact recomputation of held-out and published
scores.

## Limitations and next work

This pilot has only three repeats per workload, one public endpoint, one host, small genetic settings, and one
held-out seed. Public-network behavior is visibly variable: the CacheFly prerequisite attempt
`phase7-20260814` was rejected after inconsistent capability behavior, and corrected attempt
`phase7-20260814-ovh-r2` was rejected after one of eight parallel ranges timed out. Both remain ignored and retained;
neither contributed a score or selected run to this report.

The invalidated MMPP pilot `phase7-20260814-ovh`, its `W = 0.7874600887298584`, all-invalid family diagnosis, and
the `lambda0` bound change from `0.01` to `10.0` are part of the audit trail. No failed-pilot score or winner was
used to choose the amendment. The accepted `phase7-20260814-ovh-r3` protocol started from fresh prerequisites and
reran all nine primaries plus the reproduction.

The results demonstrate useful frame-size, autocorrelation, and—especially for streaming—IAT fidelity, but a
specific local directional-rate gap remains. The one next-work decision is to improve the existing Markov Renewal
generator's local directional-volume dynamics and evaluate that change first on the stable streaming workload;
the evidence does not justify adding another model family or infrastructure layer.
