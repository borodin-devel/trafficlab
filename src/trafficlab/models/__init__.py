"""Traffic-model contracts shared by the built-in model families."""

from trafficlab.models.common import (
    FamilyBounds,
    FittedModel,
    Gene,
    GenerationGuard,
    GenerationResult,
    Genes,
    IncompleteReason,
    MarkCount,
    MarkDistribution,
    ModelFamily,
    validate_fit_inputs,
    weighted_index,
)
from trafficlab.models.registry import (
    REGISTRY,
    BestModel,
    get_family,
    load_best_model,
    make_best_model,
    render_best_model,
    runtime_fitted_model,
)

__all__ = [
    "FamilyBounds",
    "BestModel",
    "FittedModel",
    "Gene",
    "GenerationGuard",
    "GenerationResult",
    "Genes",
    "IncompleteReason",
    "MarkCount",
    "MarkDistribution",
    "ModelFamily",
    "REGISTRY",
    "get_family",
    "load_best_model",
    "make_best_model",
    "render_best_model",
    "runtime_fitted_model",
    "validate_fit_inputs",
    "weighted_index",
]
