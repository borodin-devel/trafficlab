"""Public registry for strict persisted-artifact Pydantic roots."""

from types import MappingProxyType

from pydantic import BaseModel

from trafficlab.common.errors import FailureOutcomeRecord
from trafficlab.common.trace import CaptureMetadata
from trafficlab.comparison.stage import PublishedComparisonResult
from trafficlab.fitting.genetic.checkpoint import CheckpointArtifact
from trafficlab.generation.models.registry import BestModel
from trafficlab.study_evidence import (
    ValidationStudyEnvironment,
    ValidationStudyLifecycle,
    ValidationStudyLineage,
    ValidationStudyManifest,
    ValidationStudyPrerequisite,
    ValidationStudyProtocol,
    ValidationStudyReport,
    ValidationStudyReportInput,
)

PUBLIC_ARTIFACT_MODELS: MappingProxyType[str, type[BaseModel]] = MappingProxyType(
    {
        "best_model": BestModel,
        "capture_metadata": CaptureMetadata,
        "checkpoint": CheckpointArtifact,
        "comparison_result": PublishedComparisonResult,
        "failure_outcome": FailureOutcomeRecord,
        "study_environment": ValidationStudyEnvironment,
        "study_lifecycle": ValidationStudyLifecycle,
        "study_lineage": ValidationStudyLineage,
        "study_manifest": ValidationStudyManifest,
        "study_prerequisite": ValidationStudyPrerequisite,
        "study_protocol": ValidationStudyProtocol,
        "study_report": ValidationStudyReport,
        "study_report_input": ValidationStudyReportInput,
    }
)
