from __future__ import annotations

from pathlib import Path

TESTS = Path(__file__).resolve().parents[2]

ALLOWED_SUBSYSTEMS = {
    "unit": {
        "capture",
        "common",
        "comparison",
        "fitting",
        "generation",
        "pipeline",
        "preflight",
        "tooling",
        "validation",
    },
    "integration": {"capture", "comparison", "fitting", "generation", "pipeline", "preflight", "validation"},
    "docker": {"capture", "pipeline"},
    "internet": {"capture"},
    "property": {"common", "comparison", "fitting", "generation"},
    "scientific": {"fitting", "generation"},
}

TEST_MODULE_LINE_LIMIT = 1_000

EXPECTED_FITTING_OWNERS = {
    "__init__.py",
    "test_fit_fixture_generator.py",
    "test_input.py",
    "test_publication.py",
    "test_reuse.py",
    "test_stage.py",
}
EXPECTED_GENERATION_INTEGRATION_OWNERS = {
    "conftest.py",
    "test_generate_cli.py",
    "test_generate_failures.py",
    "test_generate_publication.py",
    "test_generate_reproduction.py",
    "test_model_pipeline.py",
}
EXPECTED_FAILURE_MATRIX_OWNERS = {"test_boundaries.py", "test_oracle.py"}


def test_behavior_tests_are_grouped_by_scope_then_subsystem() -> None:
    misplaced: dict[str, tuple[str, ...]] = {}

    for scope, allowed in ALLOWED_SUBSYSTEMS.items():
        scope_root = TESTS / scope
        invalid = tuple(
            sorted(
                path.relative_to(scope_root).as_posix()
                for path in scope_root.rglob("test_*.py")
                if path.relative_to(scope_root).parts[0] not in allowed
            )
        )
        if invalid:
            misplaced[scope] = invalid

    assert misplaced == {}


def test_remaining_large_suites_are_owned_by_behavior() -> None:
    fitting = TESTS / "unit" / "fitting"
    generation = TESTS / "integration" / "generation"
    failure_matrix = TESTS / "unit" / "pipeline" / "failure_matrix"

    assert {path.name for path in fitting.glob("*.py")} == EXPECTED_FITTING_OWNERS
    assert {path.name for path in generation.glob("*.py")} == EXPECTED_GENERATION_INTEGRATION_OWNERS
    assert {path.name for path in failure_matrix.glob("test_*.py")} == EXPECTED_FAILURE_MATRIX_OWNERS
    assert not (TESTS / "unit" / "pipeline" / "test_failure_outcome_public_matrix.py").exists()


def test_test_support_and_probe_modules_stay_within_the_cohesion_backstop() -> None:
    offenders = {
        path.relative_to(TESTS.parent).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in TESTS.rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > TEST_MODULE_LINE_LIMIT
    }

    assert offenders == {}
