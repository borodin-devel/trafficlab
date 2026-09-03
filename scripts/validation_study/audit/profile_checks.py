"""Frozen profile and capture-lineage checks for the offline audit."""

from collections.abc import Mapping

from scripts.validation_study.audit.common import artifact_identity, fail, frozen_workload_profiles
from scripts.validation_study.audit.environment import config_semantics
from scripts.validation_study.common import MODEL_FAMILIES
from trafficlab.common.config import ExperimentConfig


def capture_lineage(content: bytes, environment: Mapping[str, object]) -> dict[str, object]:
    """Rebuild the exact capture lineage fields retained by the study."""
    return {
        "capture_identity": artifact_identity(content),
        "capture_image_id": environment["capture_image_id"],
        "capture_image_reference": environment["capture_image_reference"],
        "capture_tool_version": environment["capture_tool_version"],
        "target_image_id": environment["target_image_id"],
        "target_image_reference": environment["target_image_reference"],
    }


def require_config_images(config: ExperimentConfig, environment: Mapping[str, object], *, affected: str) -> None:
    """Require retained target and capture images to match prerequisite identities."""
    if (
        config.target.image != environment["target_image_reference"]
        or config.capture.image != environment["capture_image_reference"]
    ):
        fail(
            "artifact_foreign",
            affected,
            "configuration image references do not match the frozen prerequisite environment",
            "restore image-lock-bound configuration evidence",
        )


def require_config_workload_argv(config: ExperimentConfig, *, workload: str, url: str, affected: str) -> None:
    """Require the exact frozen workload arguments for a retained config."""
    expected = frozen_workload_profiles(url)[workload].argv
    if config.target.argv != expected:
        fail(
            "artifact_foreign",
            affected,
            "configuration target argv does not match the frozen workload profile",
            "restore the exact frozen curl workload configuration",
        )


def require_frozen_profile(config: ExperimentConfig, frozen: ExperimentConfig, *, affected: str) -> None:
    """Require one retained config to equal its independently reconstructed profile."""
    if tuple(config.models.enabled) != MODEL_FAMILIES or config_semantics(config) != config_semantics(frozen):
        fail(
            "artifact_foreign",
            affected,
            "configuration does not match the frozen source-owned study profile",
            "restore the exact frozen workload configuration",
        )
