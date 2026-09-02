"""Traffic-model contracts shared by the built-in model families."""

from trafficlab.generation.models.acd import AcdFamily, AcdModel
from trafficlab.generation.models.common import (
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
from trafficlab.generation.models.fitted_model import (
    BestModel,
    load_best_model,
    make_best_model,
    render_best_model,
    runtime_fitted_model,
)
from trafficlab.generation.models.nhpp import NhppFamily, NhppModel
from trafficlab.generation.models.registry import (
    REGISTRY,
    get_family,
)

__all__ = [
    "AcdFamily",
    "AcdModel",
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
    "NhppFamily",
    "NhppModel",
    "REGISTRY",
    "get_family",
    "load_best_model",
    "make_best_model",
    "render_best_model",
    "runtime_fitted_model",
    "validate_fit_inputs",
    "weighted_index",
]
