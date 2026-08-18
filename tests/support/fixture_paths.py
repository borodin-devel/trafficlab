"""Canonical repository fixture paths shared by tests."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "fixtures"
PIPELINE_FIXTURE_ROOT = FIXTURE_ROOT / "examples" / "pipeline"
TEST_FIXTURE_ROOT = FIXTURE_ROOT / "tests"
DIAGNOSTIC_FIXTURE_ROOT = TEST_FIXTURE_ROOT / "diagnostics"
DOCKER_FIXTURE_ROOT = TEST_FIXTURE_ROOT / "docker"
PROCESS_GUARD_FIXTURE_ROOT = TEST_FIXTURE_ROOT / "process_guard"
VALIDATION_STUDY_FIXTURE_ROOT = TEST_FIXTURE_ROOT / "validation_study"
VALIDATION_STUDY_CANDIDATE = VALIDATION_STUDY_FIXTURE_ROOT / "candidate"
PRE_USER_AGENT_R6_FIXTURE = VALIDATION_STUDY_FIXTURE_ROOT / "pre-user-agent-r6"
