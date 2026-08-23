from __future__ import annotations

import ast
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
PACKAGE = REPOSITORY / "src" / "trafficlab"

EXPECTED_MODULES = {
    "common": {
        "compatibility.py",
        "config.py",
        "config_io.py",
        "errors.py",
        "json.py",
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
        "fitted_model.py",
        "fitted_schema.py",
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
    "generation/models/markov_renewal": {
        "__init__.py",
        "family.py",
        "generation.py",
        "model.py",
        "parameters.py",
        "sampling.py",
    },
    "study_evidence": {"__init__.py", "protocol.py", "report.py", "publication.py"},
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
    "study_evidence.py",
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

PRODUCTION_MODULE_LINE_LIMIT = 600


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


def test_markov_renewal_is_a_owned_package_not_a_superseded_module() -> None:
    """A flat owner would re-couple fitting, sampling, and generation internals."""
    assert not (PACKAGE / "generation" / "models" / "markov_renewal.py").exists()


def test_artifact_persistence_is_owned_by_artifact_kind() -> None:
    artifacts = PACKAGE / "artifacts"

    assert artifacts.is_dir()
    assert _python_inventory(artifacts) == EXPECTED_ARTIFACT_MODULES
    assert not (PACKAGE / "artifacts.py").exists()


def test_capture_public_records_are_owned_only_by_the_stage() -> None:
    """The public capture boundary must not be defined below or imported upward from its operations."""
    capture = PACKAGE / "capture"
    stage_tree = ast.parse((capture / "stage.py").read_text(encoding="utf-8"))
    stage_classes = {node.name for node in stage_tree.body if isinstance(node, ast.ClassDef)}

    assert {"CaptureDocker", "CaptureResult"} <= stage_classes
    for owner in ("lineage.py", "lifecycle.py", "failures.py"):
        tree = ast.parse((capture / owner).read_text(encoding="utf-8"))
        classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        upward_imports = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == "trafficlab.capture.stage"
        }
        assert not ({"CaptureDocker", "CaptureResult"} & classes), owner
        assert upward_imports == set(), owner


def test_production_modules_stay_within_the_cohesion_backstop() -> None:
    offenders = {
        path.relative_to(REPOSITORY).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in PACKAGE.rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > PRODUCTION_MODULE_LINE_LIMIT
    }

    assert offenders == {}
