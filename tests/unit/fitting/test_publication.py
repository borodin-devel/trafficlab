"""Cohesive fitting behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import trafficlab.fitting.stage as fitting
from tests.support.fitting import (
    build_config,
    build_dependencies,
    build_inputs,
    build_outcome,
)
from trafficlab.common.errors import TrafficlabError
from trafficlab.common.scientific_schema import ScientificArtifactSchemaError
from trafficlab.fitting.stage import fit_experiment


@pytest.mark.parametrize("semantic", [True, False], ids=["schema", "publication"])
def test_fit_retains_the_owning_best_model_publisher_classification(
    valid_config_data: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, semantic: bool
) -> None:
    """A publisher's typed schema error must not be overwritten by the collision fallback."""
    experiment_path = tmp_path / "experiment.toml"
    run_directory = tmp_path / "run"
    run_directory.mkdir()
    config = build_config(valid_config_data, run_directory)
    inputs = build_inputs(config)

    def fail_publication(_path: Path, _content: bytes) -> object:
        if semantic:
            raise ScientificArtifactSchemaError(
                "best model schema is incompatible",
                corrective_action="refit under the current schema",
            )
        raise TrafficlabError("injected publication conflict", corrective_action="preserve the conflicting model")

    monkeypatch.setattr(fitting, "publish_best_model", fail_publication)

    with pytest.raises(TrafficlabError) as captured:
        fit_experiment(
            experiment_path,
            dependencies=build_dependencies(config, experiment_path, inputs, lambda _context: build_outcome(config)),
        )

    outcome = captured.value.failure_outcome
    assert outcome is not None
    assert outcome.kind == ("scientific_semantics_incompatible" if semantic else "publication_collision")
