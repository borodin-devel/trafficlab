"""Primitive report values recomputed by the offline audit."""

import math
from collections.abc import Sequence
from statistics import fmean, variance

from scripts.validation_study.audit.common import Training, fail
from scripts.validation_study.common import PUBLISHED_METHOD_ORDER
from trafficlab.common.statistics import bootstrap_interval
from trafficlab.comparison.schema import ComparisonResult
from trafficlab.fitting.genetic.population import rank_candidates


def comparison_score(result: ComparisonResult) -> dict[str, object]:
    """Project one strict comparison onto the report score shape."""
    return {
        "aggregate": result.aggregate_score,
        "methods": {name: result.methods[name].score for name in PUBLISHED_METHOD_ORDER},
    }


def sample_summary(values: Sequence[float], *, name: str) -> dict[str, object]:
    """Recompute one fixed three-observation bootstrap summary."""
    if len(values) != 3 or any(not math.isfinite(value) or value < 0.0 for value in values):
        fail("artifact_corrupt", "report_inputs.json", f"{name} requires finite observations", "restore report inputs")
    return {
        "bootstrap": bootstrap_interval(values, seed=20260819).as_dict(),
        "mean": fmean(values),
        "sample_variance": variance(values),
    }


def winner_family(training: Training) -> str:
    """Return the stable ranked winner family for one retained training run."""
    candidate = rank_candidates(training.checkpoint.population, family_priority=training.checkpoint.family_priority)[0]
    return candidate.family
