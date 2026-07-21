# Hurst-Parameter Software Architecture Document

## Context and Status

Goal is future comparison of estimated Hurst exponent. Current architecture
does not select estimator or define input aggregation, valid scaling range,
stationarity assumptions, finite-sample bias, uncertainty, score mapping, or
failure criteria. It remains unsupported. Research must prevent an estimator
number from being treated as ground truth without diagnostics.
