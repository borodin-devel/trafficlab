"""Public registry for strict persisted-artifact Pydantic roots."""

from types import MappingProxyType

from pydantic import BaseModel

from trafficlab.comparison import ComparisonResult
from trafficlab.errors import FailureOutcomeRecord
from trafficlab.models.registry import BestModel

PUBLIC_ARTIFACT_MODELS: MappingProxyType[str, type[BaseModel]] = MappingProxyType(
    {
        "best_model": BestModel,
        "comparison_result": ComparisonResult,
        "failure_outcome": FailureOutcomeRecord,
    }
)
