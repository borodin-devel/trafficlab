from dataclasses import dataclass
from typing import Literal

from tests.fixtures.paths import DIAGNOSTIC_FIXTURE_ROOT
from trafficlab.common.errors import FailureOutcome

FIXTURE_PATH = DIAGNOSTIC_FIXTURE_ROOT / "failure-outcomes.jsonl"

type Scenario = Literal[
    "config_invalid",
    "docker_unavailable",
    "compose_incompatible",
    "target_image_unavailable",
    "capture_image_incompatible",
    "dumpcap_unavailable",
    "dumpcap_incompatible",
    "mount_source_unavailable",
    "mount_target_incompatible",
    "mounted_input_unavailable",
    "mounted_input_incompatible",
    "prerequisite_unavailable",
    "prerequisite_incompatible",
    "target_exit_23",
    "capture_exit_42_active",
    "capture_exit_42_after_target_0",
    "workload_timeout",
    "flush_timeout_after_target_0",
    "validation_total_timeout",
    "user_interrupt",
    "malformed_capture",
    "best_model_missing",
    "reference_changed",
    "foreign_generated",
    "stale_capture_pair",
    "checkpoint_corrupt",
    "checkpoint_schema",
    "best_model_schema",
    "metric_infeasible",
    "packet_limit",
    "best_model_collision",
    "accepted_collision",
    "similarity_durability",
    "cleanup_timeout_after_success",
    "target_23_cleanup_timeout",
    "workload_timeout_target_137",
    "flush_and_total_timeout",
    "target_23_capture_42_total_timeout",
]

SCENARIOS: tuple[Scenario, ...] = (
    "config_invalid",
    "docker_unavailable",
    "compose_incompatible",
    "target_image_unavailable",
    "capture_image_incompatible",
    "dumpcap_unavailable",
    "dumpcap_incompatible",
    "mount_source_unavailable",
    "mount_target_incompatible",
    "mounted_input_unavailable",
    "mounted_input_incompatible",
    "prerequisite_unavailable",
    "prerequisite_incompatible",
    "target_exit_23",
    "capture_exit_42_active",
    "capture_exit_42_after_target_0",
    "workload_timeout",
    "flush_timeout_after_target_0",
    "validation_total_timeout",
    "user_interrupt",
    "malformed_capture",
    "best_model_missing",
    "reference_changed",
    "foreign_generated",
    "stale_capture_pair",
    "checkpoint_corrupt",
    "checkpoint_schema",
    "best_model_schema",
    "metric_infeasible",
    "packet_limit",
    "best_model_collision",
    "accepted_collision",
    "similarity_durability",
    "cleanup_timeout_after_success",
    "target_23_cleanup_timeout",
    "workload_timeout_target_137",
    "flush_and_total_timeout",
    "target_23_capture_42_total_timeout",
)


@dataclass(frozen=True, slots=True)
class BoundaryCase:
    """One primary outcome and all of its ordered fixture-defined secondaries."""

    scenario: Scenario
    outcomes: tuple[FailureOutcome, ...]

    @property
    def primary(self) -> FailureOutcome:
        return self.outcomes[0]

    @property
    def identifier(self) -> str:
        return f"primitive-boundary-{self.scenario}"


def fixture_outcomes() -> tuple[FailureOutcome, ...]:
    return tuple(
        FailureOutcome.from_json(line) for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines() if line
    )


def build_boundary_cases() -> tuple[BoundaryCase, ...]:
    grouped: list[tuple[FailureOutcome, ...]] = []
    active: list[FailureOutcome] = []
    for outcome in fixture_outcomes():
        if outcome.authority == "primary":
            if active:
                grouped.append(tuple(active))
            active = [outcome]
        else:
            if not active:
                raise AssertionError("a fixture secondary outcome must follow a primary outcome")
            active.append(outcome)
    if active:
        grouped.append(tuple(active))
    return tuple(
        BoundaryCase(scenario=scenario, outcomes=outcomes)
        for scenario, outcomes in zip(SCENARIOS, grouped, strict=True)
    )


PUBLIC_BOUNDARY_CASES = build_boundary_cases()

CAPTURE_FAILURE_SCENARIOS = frozenset(
    {
        "target_exit_23",
        "capture_exit_42_active",
        "capture_exit_42_after_target_0",
        "workload_timeout",
        "flush_timeout_after_target_0",
        "validation_total_timeout",
        "user_interrupt",
        "malformed_capture",
        "cleanup_timeout_after_success",
        "target_23_cleanup_timeout",
        "workload_timeout_target_137",
        "flush_and_total_timeout",
        "target_23_capture_42_total_timeout",
    }
)
