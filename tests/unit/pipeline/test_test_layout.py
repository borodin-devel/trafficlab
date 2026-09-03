from __future__ import annotations

import ast
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

EXPECTED_DECOMPOSED_OWNER_TREES = {
    "unit/pipeline/artifacts": {
        "__init__.py",
        "test_best_model.py",
        "test_capture.py",
        "test_generated.py",
        "test_io.py",
        "test_run_directory.py",
    },
    "unit/capture": {
        "__init__.py",
        "test_capture_failure_context.py",
        "test_capture_policy.py",
        "test_capture_validation.py",
        "test_cleanup.py",
        "test_failures.py",
        "test_lifecycle.py",
        "test_lineage.py",
        "test_stage.py",
        "test_topology.py",
    },
    "unit/capture/docker": {"__init__.py", "test_compose.py", "test_image.py", "test_process.py"},
    "unit/comparison": {
        "__init__.py",
        "test_codec.py",
        "test_diagnostics.py",
        "test_metrics.py",
        "test_publication.py",
        "test_schema.py",
        "test_stage.py",
    },
    "unit/fitting/genetic/checkpoint": {
        "__init__.py",
        "test_codec.py",
        "test_compatibility.py",
        "test_history.py",
        "test_schema.py",
        "test_state.py",
    },
    "unit/generation/models/markov_renewal": {
        "__init__.py",
        "test_family.py",
        "test_generation.py",
        "test_model.py",
        "test_parameters.py",
        "test_sampling.py",
    },
    "unit/preflight": {"test_docker.py", "test_local.py", "test_probe.py", "test_stage.py"},
    "unit/validation/study_evidence": {"test_publication.py", "test_schema.py"},
    "unit/validation/study/audit": {
        "__init__.py",
        "_audit_support.py",
        "_boundary_support.py",
        "test_artifacts.py",
        "test_environment.py",
        "test_environment_boundaries.py",
        "test_lineage_boundaries.py",
        "test_manifest.py",
        "test_publication.py",
        "test_science.py",
        "test_worktree_boundaries.py",
    },
    "unit/validation/study/protocol": {
        "__init__.py",
        "_support.py",
        "test_reporting.py",
        "test_reproduction.py",
        "test_run_codec.py",
        "test_schema.py",
    },
    "unit/validation/study/orchestration": {
        "__init__.py",
        "_support.py",
        "test_cli.py",
        "test_collection.py",
        "test_reproduction.py",
        "test_study.py",
    },
    "unit/validation/study/prerequisites": {
        "__init__.py",
        "_support.py",
        "test_attempt.py",
        "test_cli.py",
        "test_recovery.py",
        "test_rotation.py",
    },
    "support/validation_study": {
        "__init__.py",
        "artifacts.py",
        "builders.py",
        "constants.py",
        "repository.py",
        "runners.py",
    },
    "scientific/fitting/probes/mmpp_likelihood": {
        "__init__.py",
        "evidence.py",
        "fit.py",
        "likelihood.py",
        "schema.py",
    },
    "scientific/fitting/probes/pymoo_optimizer": {
        "__init__.py",
        "adapter.py",
        "evidence.py",
        "policy.py",
        "schema.py",
    },
    "integration/generation": {
        "conftest.py",
        "test_generate_cli.py",
        "test_generate_failures.py",
        "test_generate_publication.py",
        "test_generate_reproduction.py",
        "test_model_pipeline.py",
    },
    "unit/fitting": {
        "__init__.py",
        "test_fit_fixture_generator.py",
        "test_input.py",
        "test_publication.py",
        "test_reuse.py",
        "test_stage.py",
    },
    "unit/pipeline/failure_matrix": {"test_boundaries.py", "test_oracle.py"},
}

EXPECTED_DECOMPOSED_ROOT_TESTS = {
    "unit/fitting/genetic": {
        "__init__.py",
        "test_coordinates.py",
        "test_crossover.py",
        "test_evaluation.py",
        "test_mutation.py",
        "test_population.py",
        "test_reproduction.py",
        "test_selection.py",
        "test_strategy.py",
        "test_types.py",
    },
    "unit/generation/models": {
        "test_acd.py",
        "test_common.py",
        "test_contract.py",
        "test_fixture_generator.py",
        "test_mmpp.py",
        "test_nhpp.py",
        "test_poisson.py",
        "test_registry.py",
    },
    "unit/pipeline": {
        "__init__.py",
        "test_artifact_schemas.py",
        "test_cli.py",
        "test_final_validation.py",
        "test_package.py",
        "test_source_layout.py",
        "test_stage.py",
        "test_stage_results.py",
        "test_test_layout.py",
    },
}


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


def test_every_prescribed_decomposition_owner_tree_is_permanent() -> None:
    """Each Task 1-10 split remains attached to its exact functional owners."""
    for relative, expected in EXPECTED_DECOMPOSED_OWNER_TREES.items():
        directory = TESTS / relative
        assert directory.is_dir(), f"missing test owner tree: {relative}"
        assert {path.name for path in directory.glob("*.py")} == expected

    for relative, expected in EXPECTED_DECOMPOSED_ROOT_TESTS.items():
        directory = TESTS / relative
        assert {path.name for path in directory.glob("*.py")} == expected


def test_unit_generated_artifact_owner_has_only_unit_local_setup() -> None:
    """A unit artifact owner must not borrow integration markers or conftest plugins."""
    path = TESTS / "unit" / "pipeline" / "artifacts" / "test_generated.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assigned_names = {
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
        if isinstance(target, ast.Name)
    }

    assert "pytestmark" not in assigned_names
    assert "pytest_plugins" not in assigned_names

    integration_path = TESTS / "integration" / "generation" / "test_generate_publication.py"
    integration_tree = ast.parse(integration_path.read_text(encoding="utf-8"))
    marker_values = {
        target.id: ast.unparse(node.value)
        for node in integration_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"pytestmark", "pytest_plugins"}
    }
    assert marker_values == {"pytestmark": "pytest.mark.integration"}


def test_diagnostics_owner_tests_import_the_diagnostics_owner_directly() -> None:
    """Diagnostic invariants need a direct owner test, not only schema-stage coverage."""
    path = TESTS / "unit" / "comparison" / "test_diagnostics.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module for node in tree.body if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names}

    assert "trafficlab.comparison.diagnostics" in imported_modules


def test_test_support_and_probe_modules_stay_within_the_cohesion_backstop() -> None:
    offenders = {
        path.relative_to(TESTS.parent).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in TESTS.rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > TEST_MODULE_LINE_LIMIT
    }

    assert offenders == {}
