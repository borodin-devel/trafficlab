"""Public registry for strict persisted-artifact Pydantic roots."""

from types import MappingProxyType

from pydantic import BaseModel

from trafficlab.comparison import PublishedComparisonResult
from trafficlab.errors import FailureOutcomeRecord
from trafficlab.genetic.checkpoint import CheckpointArtifact
from trafficlab.models.registry import BestModel
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
from trafficlab.trace import CaptureMetadata

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
