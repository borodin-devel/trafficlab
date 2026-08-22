from __future__ import annotations

from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
PACKAGE = REPOSITORY / "src" / "trafficlab"

EXPECTED_MODULES = {
    "common": {
        "compatibility.py",
        "config.py",
        "config_io.py",
        "errors.py",
        "scapy_io.py",
        "scientific_schema.py",
        "statistics.py",
        "trace.py",
    },
    "preflight": {"docker.py", "local.py", "probe.py", "stage.py", "types.py"},
    "capture": {
        "cleanup.py",
        "failures.py",
        "lifecycle.py",
        "lineage.py",
        "policy.py",
        "stage.py",
        "topology.py",
        "validation.py",
    },
    "fitting": {"stage.py"},
    "generation": {"stage.py"},
    "comparison": {"codec.py", "diagnostics.py", "metrics.py", "publication.py", "schema.py", "stage.py"},
}

EXPECTED_NESTED_PACKAGES = {
    "fitting/genetic": {
        "__init__.py",
        "coordinates.py",
        "evaluation.py",
        "operators.py",
        "population.py",
        "strategy.py",
        "types.py",
    },
    "generation/models": {
        "__init__.py",
        "common.py",
        "markov_renewal.py",
        "mmpp.py",
        "poisson.py",
        "registry.py",
    },
    "comparison/similarity": {
        "__init__.py",
        "autocorrelation.py",
        "common.py",
        "ks.py",
        "multiscale.py",
    },
    "capture/docker": {"__init__.py", "compose.py", "image.py", "process.py", "types.py"},
    "fitting/genetic/checkpoint": {
        "__init__.py",
        "codec.py",
        "compatibility.py",
        "history.py",
        "schema.py",
        "state.py",
    },
    "pipeline": {"__init__.py", "stage.py", "types.py", "validation.py"},
}

FORBIDDEN_ROOT_MODULES = {
    "capture.py",
    "capture_policy.py",
    "capture_validation.py",
    "cleanup.py",
    "comparison.py",
    "compatibility.py",
    "compose.py",
    "config.py",
    "config_io.py",
    "docker_cli.py",
    "errors.py",
    "fitting.py",
    "generation.py",
    "preflight.py",
    "scapy_io.py",
    "scientific_schema.py",
    "statistics.py",
    "trace.py",
    "run.py",
}

FORBIDDEN_ROOT_PACKAGES = {"genetic", "models", "similarity"}

EXPECTED_ARTIFACT_MODULES = {
    "__init__.py",
    "best_model.py",
    "capture.py",
    "generated.py",
    "io.py",
    "run_directory.py",
}


def _python_inventory(directory: Path) -> set[str]:
    if not directory.is_dir():
        return set()
    return {path.name for path in directory.iterdir() if path.is_file() and path.suffix == ".py"}


def test_production_modules_are_owned_by_pipeline_subsystems() -> None:
    for package_name, expected in EXPECTED_MODULES.items():
        package = PACKAGE / package_name
        assert package.is_dir(), f"missing subsystem package: {package.relative_to(REPOSITORY)}"
        assert _python_inventory(package) - {"__init__.py"} == expected

    for package_name, expected in EXPECTED_NESTED_PACKAGES.items():
        package = PACKAGE / package_name
        assert package.is_dir(), f"missing nested subsystem package: {package.relative_to(REPOSITORY)}"
        assert _python_inventory(package) == expected


def test_production_root_has_no_superseded_flat_modules_or_packages() -> None:
    present_modules = {name for name in FORBIDDEN_ROOT_MODULES if (PACKAGE / name).exists()}
    present_packages = {name for name in FORBIDDEN_ROOT_PACKAGES if (PACKAGE / name).exists()}

    assert present_modules == set()
    assert present_packages == set()


def test_artifact_persistence_is_owned_by_artifact_kind() -> None:
    artifacts = PACKAGE / "artifacts"

    assert artifacts.is_dir()
    assert _python_inventory(artifacts) == EXPECTED_ARTIFACT_MODULES
    assert not (PACKAGE / "artifacts.py").exists()
