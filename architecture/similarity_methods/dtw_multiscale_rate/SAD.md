# Multi-Scale Rate DTW Software Architecture Document

## Context and Status

Goal is future time-warp-tolerant multi-scale rate comparison. Current
architecture defines no series features, local distance, warping window/step,
path normalization, unequal length policy, scale weights, score map, or
numerical/resource limit. It remains unsupported. Research must prevent
pathological warp, quadratic resource exhaustion, and arbitrary score scaling.
