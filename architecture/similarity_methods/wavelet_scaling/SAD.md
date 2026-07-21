# Wavelet-Scaling Software Architecture Document

## Context and Status

Goal is future multi-scale burstiness comparison. Current architecture does not
select traffic series, discrete/continuous transform, wavelet/filter,
padding/boundary, levels, coefficient statistic, scale alignment/weights,
distance, score, or minimum input. Selection remains unsupported. Research must
address edge artifacts, non-dyadic lengths, zero scales, and complexity.
